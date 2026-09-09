"""启动期凭据校验（WBS 1.4）：缺失／空串／纯空白一律拒绝启动并指明缺失项。

对应 spec liaison-channel-session「凭据缺失时拒绝启动」的两个场景：
- 凭据未填写 → 启动失败并指明缺失项、进程不驻留
- 凭据为空白字符串 → 视为缺失，启动失败

⛔ 本文件不测 SDK、不测建连——凭据校验必须在任何 SDK import 之前发生，
这本身就是被测的不变式之一（test_entrypoint_does_not_import_sdk_at_module_level）。
"""

import ast
import functools
import os
import pathlib
import subprocess
import sys
import time

import pytest

from tools.liaison.__main__ import (
    EXIT_MISSING_CREDENTIALS,
    EXIT_SDK_UNAVAILABLE,
    SELF_CHECK_ARG,
)
from tools.liaison.config import (
    BOT_ID_ENV,
    BOT_SECRET_ENV,
    LiaisonCredentials,
    load_credentials,
)
from tools.liaison.errors import MissingCredentialsError
from tools.liaison.tests import netguard_support

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIAISON_DIR = REPO_ROOT / "tools" / "liaison"

# 入口在校验凭据之前**不得**触碰这些模块——根 venv 里它们根本不存在，
# 一旦在模块层 import，缺凭据的退出路径就会先炸在 ModuleNotFoundError 上。
FORBIDDEN_MODULE_LEVEL_IMPORTS = ("aibot", "wecom_aibot_python_sdk", "websockets")

# 校验必须发生在 SDK import 之前的那几个模块。
STARTUP_PATH_MODULES = ("errors.py", "config.py", "__main__.py")


@functools.cache
def _sdk_available_to_subprocess() -> bool:
    """跑测试的这个解释器装没装 aibot？

    ⚠️ 这是**环境事实，⛔ 不是契约**——design D10 决定了 SDK 只进
    `tools/liaison/.venv`，全量 pytest 走的根 venv 里必然没有。前一版用例踩过的坑
    正是把它写死进断言：`tools/liaison/.venv` 一建起来、pytest 从那里跑，同一句断言
    就转红，而被测行为一个字都没变。

    所以这里**测出来**再决定期望值，⛔ 不假设。判据本身（"启动期自检不会因为凭据
    缺失退出、也不会因为 SDK 表面对不上退出"）在两个 venv 里是同一条。
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import aibot"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    return probe.returncode == 0


def _expected_self_check_code() -> int:
    """自检该以哪个码结束：装了 SDK ⇒ 0（整条走通）；没装 ⇒ 3（缺依赖，与凭据无关）。"""
    return 0 if _sdk_available_to_subprocess() else EXIT_SDK_UNAVAILABLE


def _subprocess_env(tmp_path: pathlib.Path, env_overrides: dict[str, str]) -> dict[str, str]:
    """子进程环境：清掉真实凭据、指一个受控的 .env、**装上网络闸门**。

    ⚠️ 必须指一个**不存在**（或用例自己写的）.env：开发机上仓库根真有一个 .env，
    不隔离的话"凭据缺失"这条用例会在他机器上变绿、在 CI 上变红。

    ⚠️ `netguard_support.subprocess_env` ⛔ 不能省（TD-36）：进程内的闸门罩不到
    子进程，而带着假凭据真的去连企微的正是这里起的子进程。
    """
    env = os.environ.copy()
    env.pop(BOT_ID_ENV, None)
    env.pop(BOT_SECRET_ENV, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    netguard_support.subprocess_env(env)
    env["HR_LIAISON_DOTENV_PATH"] = str(tmp_path / "absent.env")
    env.update(env_overrides)
    return env


def _run_entrypoint(
    env_overrides: dict[str, str], tmp_path: pathlib.Path, *, args: tuple[str, ...] = ()
):
    """在子进程里跑 `python -m tools.liaison`，返回 CompletedProcess 与耗时。"""
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "tools.liaison", *args],
        cwd=str(REPO_ROOT),
        env=_subprocess_env(tmp_path, env_overrides),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, time.monotonic() - started


# ── 纯函数层：三种缺失形态 ──────────────────────────────────────────────

def test_both_credentials_absent():
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env={})
    assert set(excinfo.value.missing_names) == {BOT_ID_ENV, BOT_SECRET_ENV}
    assert BOT_ID_ENV in str(excinfo.value)
    assert BOT_SECRET_ENV in str(excinfo.value)


def test_empty_string_counts_as_missing():
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env={BOT_ID_ENV: "", BOT_SECRET_ENV: ""})
    assert set(excinfo.value.missing_names) == {BOT_ID_ENV, BOT_SECRET_ENV}


def test_whitespace_only_counts_as_missing():
    """spec 场景「凭据为空白字符串」：只含空格 / 制表符 / 换行一律算缺失。"""
    blank = {BOT_ID_ENV: "   ", BOT_SECRET_ENV: "\t\n "}
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env=blank)
    assert set(excinfo.value.missing_names) == {BOT_ID_ENV, BOT_SECRET_ENV}


def test_error_names_only_the_actually_missing_item():
    """"指明缺失项"要指得准：配了一半时，⛔ 不许把配好的那项也报成缺失。"""
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env={BOT_ID_ENV: "bot-present", BOT_SECRET_ENV: "  "})
    assert excinfo.value.missing_names == (BOT_SECRET_ENV,)
    assert BOT_ID_ENV not in str(excinfo.value)


def test_valid_credentials_are_returned_stripped():
    creds = load_credentials(env={BOT_ID_ENV: " bot-1 ", BOT_SECRET_ENV: " sec-1 "})
    assert creds == LiaisonCredentials(bot_id="bot-1", bot_secret="sec-1")


def test_repr_does_not_leak_the_secret():
    """repr 会出现在日志、traceback、pytest 断言里。secret 不能跟着一起走。"""
    creds = load_credentials(env={BOT_ID_ENV: "bot-1", BOT_SECRET_ENV: "super-sensitive"})
    rendered = f"{creds!r} {creds}"
    assert "super-sensitive" not in rendered
    assert "bot-1" in rendered  # bot_id 不是秘密，排障要看得见


# ── 进程层：拒绝启动且不驻留 ────────────────────────────────────────────

def test_entrypoint_exits_nonzero_and_names_missing_items(tmp_path):
    proc, elapsed = _run_entrypoint({}, tmp_path)
    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert BOT_ID_ENV in proc.stderr
    assert BOT_SECRET_ENV in proc.stderr
    # "进程不驻留"：拿不到凭据就该立刻退，⛔ 不允许进入任何等待/重试循环。
    assert elapsed < 15, f"入口在缺凭据时驻留了 {elapsed:.1f}s"


def test_entrypoint_succeeds_when_credentials_present(tmp_path):
    """凭据校验通过后，入口不得再因为"凭据"这件事拒绝启动。

    ⚠️ **2026-09-09 二次修订（TD-36，与 TD-19 同一个 commit）**：本用例改跑
    `--self-check`。⛔ 这不是"为了让测试好过"而放宽——恰恰相反，判据变**严**了：

    - 旧写法断言的是 `returncode != EXIT_MISSING_CREDENTIALS`，而进程实际停在哪
      靠的是**下游某一关碰巧拦住了它**（装了 aibot 就停在表面校验 exit 4，没装就停在
      exit 3）。TD-19 落地后那两道拦阻都没了，同样一条用例会带着假凭据 `bot-1`/`sec-1`
      **真的去连企微 WebSocket**，而且 ⛔ 不会有任何报错告诉你。
    - 新写法把整条启动路径（读 .env → 校验凭据 → 造连接对象 → 核 SDK 表面 → 接事件）
      原样跑完，在建连前退出，因此可以直接断言 **exit 0**：⛔ 不再依赖"下游哪一关会红"
      这个环境事实，也 ⛔ 不再需要网络。

    `--self-check` ⛔ 不跳过任何一项校验（见 `__main__.SELF_CHECK_ARG` 的说明）；
    真实建连是 8.6 由 Shao Peishen 亲自跑，⛔ 不在本套件里。
    """
    proc, _ = _run_entrypoint(
        {BOT_ID_ENV: "bot-1", BOT_SECRET_ENV: "sec-1"}, tmp_path, args=(SELF_CHECK_ARG,)
    )
    assert proc.returncode != EXIT_MISSING_CREDENTIALS, (
        f"凭据齐备时不该被判定为缺失：stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.returncode == _expected_self_check_code(), (
        "启动期自检应当整条走通（装了 SDK ⇒ 0；没装 ⇒ 3，与凭据无关）："
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "sec-1" not in proc.stdout + proc.stderr, "凭据取值不得出现在任何输出里"


def test_entrypoint_reads_dotenv_when_process_env_is_absent(tmp_path):
    """凭据"只从进程环境读"——.env 的作用是**填进**进程环境，不是第二个真源。

    ⚠️ 同上一条（TD-36）：改跑 `--self-check`，判据收紧为 exit 0，且 ⛔ 不触网。
    """
    dotenv = tmp_path / "from-file.env"
    dotenv.write_text(
        f"# 注释行应被跳过\n{BOT_ID_ENV}=bot-from-file\n{BOT_SECRET_ENV}=sec-from-file\n",
        encoding="utf-8",
    )
    env = _subprocess_env(tmp_path, {"HR_LIAISON_DOTENV_PATH": str(dotenv)})
    proc = subprocess.run(
        [sys.executable, "-m", "tools.liaison", SELF_CHECK_ARG],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != EXIT_MISSING_CREDENTIALS, f"stderr={proc.stderr!r}"
    assert proc.returncode == _expected_self_check_code(), (
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    # 上一条有泄漏断言而本条原先没有——补齐。凭据来自 .env 这条路径同样不得回显取值。
    assert "sec-from-file" not in proc.stdout + proc.stderr, (
        "凭据取值不得出现在任何输出里"
    )


def test_the_self_check_never_reaches_the_network(tmp_path):
    """TD-36 的判据本身也要有岗：闸门装上了，且被测路径确实没撞上它。

    三件事一起断言，⛔ 缺一条都不成立：
    ① 子进程里闸门对**裸 socket** 生效；
    ② 子进程里闸门对**真实建连栈**（`websockets`，SDK 用的就是它）也生效——
       ⚠️ 这一条 ⛔ 不能省：2026-09-09 实测过一次真实的假绿灯——本机
       `HTTPS_PROXY=http://127.0.0.1:<port>`，`websockets` 走
       `urllib.request.getproxies()`（macOS 上连**系统代理设置**一起读，清环境变量
       没用），建连打到回环、被闸门放行、再由代理转发出去。只验 ① 的话闸门会一直
       报绿，而流量照常出去；
    ③ 同样这个环境下跑 `--self-check`，退出码 0 且 stderr 里 ⛔ 没有闸门的报错名，
       即被测路径**根本没走到建连**。

    ⚠️ 探针打的是 `example.com` 而不是企微：闸门若哪天回归失效，这条用例会真的把
    包发出去——那就让它发给一个无关的公共域名，⛔ 不要发给企微生产端点。

    ⚠️ 根 venv 里 skip 是**预期行为**（design D10：SDK 与 `websockets` 只进
    `tools/liaison/.venv`）——② 需要 `websockets` 才验得了。⛔ 不要为了让它在根 venv
    也能跑而把 SDK 加进根 requirements.txt。
    """
    if not _sdk_available_to_subprocess():
        pytest.skip("SDK/websockets 只装在 tools/liaison/.venv，根 venv skip 是预期（design D10）")
    env = _subprocess_env(tmp_path, {BOT_ID_ENV: "bot-1", BOT_SECRET_ENV: "sec-1"})

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; socket.getaddrinfo('example.com', 443)",
        ],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode != 0, "闸门没生效——子进程居然解析出了外部域名"
    assert "NetworkAccessInTestError" in probe.stderr, probe.stderr

    ws_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio, websockets\n"
            "async def m():\n"
            "    await websockets.connect('wss://example.com/ws')\n"
            "asyncio.run(m())\n",
        ],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert "NetworkAccessInTestError" in ws_probe.stderr, (
        "闸门拦不住 websockets（SDK 真正用的那条路）——很可能又被代理绕过去了。"
        f"stderr={ws_probe.stderr!r}"
    )

    proc = subprocess.run(
        [sys.executable, "-m", "tools.liaison", SELF_CHECK_ARG],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "NetworkAccessInTestError" not in proc.stderr, (
        "启动期自检撞上了网络闸门——它 ⛔ 不该走到建连那一步"
    )


def test_dotenv_strips_export_prefix(tmp_path):
    """`export KEY=value` 是常见的 shell 书写习惯，必须解析出真正的键名。

    回归目标：曾经 `key, _, value = line.partition("=")` 不剥离 `export ` 前缀，
    导致真实键名从未被设置，而是设了一个叫 "export HR_LIAISON_BOT_ID" 的假键——
    诊断信息会误导成"变量缺失"，即便 .env 里明明写了。与扫描器
    test_liaison_no_secrets_in_vcs.py 的 _ASSIGNMENT 正则（同样接受
    `(?:export[ \t]+)?`）保持口径一致。
    """
    dotenv = tmp_path / "export-style.env"
    dotenv.write_text(f"export {BOT_ID_ENV}=bot-exported\n", encoding="utf-8")
    from tools.liaison.__main__ import load_dotenv_into_environ

    env_backup = dict(os.environ)
    try:
        os.environ.pop(BOT_ID_ENV, None)
        load_dotenv_into_environ(dotenv)
        assert os.environ[BOT_ID_ENV] == "bot-exported"
        assert not any(name.startswith("export") for name in os.environ), (
            "不应残留一个以 'export' 开头的假键名"
        )
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


def test_default_dotenv_path_is_the_liaison_dir_not_the_repo_root():
    """🔴 TD-40：值守通道的 `.env` 默认在 `tools/liaison/`，⛔ 不在仓库根。

    ⛔ 这条 ⛔ 不是整洁癖。三个 `HR_LIAISON_*` 键放在根 `.env` 里会让
    `app/config.py` 的 `Settings`（pydantic-settings，默认 `extra="forbid"`）
    每次实例化都抛 `ValidationError` ⇒ **Web 服务起不来**，且报错把 `bot_secret`
    明文打进输出。默认路径一旦被改回仓库根，那三个键就会被"合法地"请回去。

    ⚠️ 同时钉死「⛔ 没有根 .env 兜底」：兜底会让"键还留在根 .env 里"这个坏状态
    继续静默存在——而那正是本条要消灭的东西。
    """
    from tools.liaison import __main__ as liaison_main

    assert liaison_main.DEFAULT_DOTENV_PATH == LIAISON_DIR / ".env"
    assert liaison_main.DEFAULT_DOTENV_PATH.parent != REPO_ROOT, (
        "默认 .env 又回到仓库根了——Settings 会因 extra='forbid' 整个炸掉"
    )

    env_backup = dict(os.environ)
    try:
        os.environ.pop("HR_LIAISON_DOTENV_PATH", None)
        assert liaison_main.resolve_dotenv_path() == LIAISON_DIR / ".env"
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


def test_process_env_wins_over_dotenv(tmp_path):
    """进程环境优先于 .env（与 pydantic-settings 在 app/config.py 里的口径一致）。"""
    dotenv = tmp_path / "loser.env"
    dotenv.write_text(f"{BOT_ID_ENV}=from-file\n{BOT_SECRET_ENV}=from-file\n", encoding="utf-8")
    from tools.liaison.__main__ import load_dotenv_into_environ

    env_backup = dict(os.environ)
    try:
        os.environ[BOT_ID_ENV] = "from-process"
        os.environ[BOT_SECRET_ENV] = "from-process"
        load_dotenv_into_environ(dotenv)
        assert os.environ[BOT_ID_ENV] == "from-process"
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


# ── 结构不变式：校验先于 SDK ────────────────────────────────────────────

def test_entrypoint_does_not_import_sdk_at_module_level():
    """启动路径上的模块**只能用标准库**。

    ⚠️ 这不是洁癖：跑全量 pytest 的根 venv 里没有 aibot／websockets（design D10 的
    依赖隔离）。启动路径一旦在模块层 import 它们，"缺凭据 → 退出码 2"就会先炸在
    ModuleNotFoundError 上——fail-closed 变成 fail-confusing，而且只在根 venv 里现形。
    """
    for filename in STARTUP_PATH_MODULES:
        path = LIAISON_DIR / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in tree.body:  # 只看模块层，函数体内的延迟 import 不受此限
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for name in imported:
            root = name.split(".")[0]
            assert root not in FORBIDDEN_MODULE_LEVEL_IMPORTS, (
                f"{filename} 在模块层 import 了 {name}；"
                f"凭据校验必须先于任何 SDK import 发生"
            )
