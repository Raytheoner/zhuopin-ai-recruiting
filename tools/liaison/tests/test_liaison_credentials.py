"""启动期凭据校验（WBS 1.4）：缺失／空串／纯空白一律拒绝启动并指明缺失项。

对应 spec liaison-channel-session「凭据缺失时拒绝启动」的两个场景：
- 凭据未填写 → 启动失败并指明缺失项、进程不驻留
- 凭据为空白字符串 → 视为缺失，启动失败

⛔ 本文件不测 SDK、不测建连——凭据校验必须在任何 SDK import 之前发生，
这本身就是被测的不变式之一（test_entrypoint_does_not_import_sdk_at_module_level）。
"""

import ast
import os
import pathlib
import subprocess
import sys
import time

import pytest

from tools.liaison.__main__ import EXIT_MISSING_CREDENTIALS, EXIT_SDK_UNAVAILABLE
from tools.liaison.config import (
    BOT_ID_ENV,
    BOT_SECRET_ENV,
    LiaisonCredentials,
    load_credentials,
)
from tools.liaison.errors import MissingCredentialsError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIAISON_DIR = REPO_ROOT / "tools" / "liaison"

# 入口在校验凭据之前**不得**触碰这些模块——根 venv 里它们根本不存在，
# 一旦在模块层 import，缺凭据的退出路径就会先炸在 ModuleNotFoundError 上。
FORBIDDEN_MODULE_LEVEL_IMPORTS = ("aibot", "wecom_aibot_python_sdk", "websockets")

# 校验必须发生在 SDK import 之前的那几个模块。
STARTUP_PATH_MODULES = ("errors.py", "config.py", "__main__.py")


def _run_entrypoint(env_overrides: dict[str, str], tmp_path: pathlib.Path):
    """在子进程里跑 `python -m tools.liaison`，返回 CompletedProcess 与耗时。

    ⚠️ 必须指一个**不存在**的 .env：开发机上仓库根真有一个 .env，不隔离的话
    "凭据缺失"这条用例会在他机器上变绿、在 CI 上变红。
    """
    env = os.environ.copy()
    env.pop(BOT_ID_ENV, None)
    env.pop(BOT_SECRET_ENV, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HR_LIAISON_DOTENV_PATH"] = str(tmp_path / "absent.env")
    env.update(env_overrides)

    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "tools.liaison"],
        cwd=str(REPO_ROOT),
        env=env,
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

    ⚠️ **第 7 章更新**：Task 6 接线后，凭据校验通过只是入口的第一关，紧接着会
    尝试 `import aibot` 构造 SDK 连接对象。本用例用 `sys.executable` 起子进程，
    也就是**根 venv**——design D10 的依赖隔离决定了 aibot 只装在
    `tools/liaison/.venv`，根 venv 里必然装不上。因此这里的"成功"标准从
    "returncode == 0"改写为"没有因为凭据缺失退出（EXIT_MISSING_CREDENTIALS），
    而是恰好在下一关——SDK 不可用（EXIT_SDK_UNAVAILABLE）——停下"，这正是本
    进程在根 venv 里能达到的最远、也是诚实的位置；凭据取值仍然不得出现在任何
    输出里。
    """
    proc, _ = _run_entrypoint(
        {BOT_ID_ENV: "bot-1", BOT_SECRET_ENV: "sec-1"}, tmp_path
    )
    assert proc.returncode != EXIT_MISSING_CREDENTIALS, (
        f"凭据齐备时不该被判定为缺失：stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.returncode == EXIT_SDK_UNAVAILABLE, (
        f"根 venv 不装 aibot（design D10），预期止步于 SDK 不可用："
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "sec-1" not in proc.stdout + proc.stderr, "凭据取值不得出现在任何输出里"


def test_entrypoint_reads_dotenv_when_process_env_is_absent(tmp_path):
    """凭据"只从进程环境读"——.env 的作用是**填进**进程环境，不是第二个真源。

    ⚠️ 同上一条：根 venv 没装 aibot（design D10），.env 填进的凭据一旦通过校验，
    下一关必然止步于 EXIT_SDK_UNAVAILABLE。本用例只关心"没有因为凭据缺失退出"，
    这才是"从 .env 读到了凭据"这件事本身要验的东西。
    """
    dotenv = tmp_path / "from-file.env"
    dotenv.write_text(
        f"# 注释行应被跳过\n{BOT_ID_ENV}=bot-from-file\n{BOT_SECRET_ENV}=sec-from-file\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop(BOT_ID_ENV, None)
    env.pop(BOT_SECRET_ENV, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HR_LIAISON_DOTENV_PATH"] = str(dotenv)
    proc = subprocess.run(
        [sys.executable, "-m", "tools.liaison"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != EXIT_MISSING_CREDENTIALS, f"stderr={proc.stderr!r}"
    assert proc.returncode == EXIT_SDK_UNAVAILABLE, f"stderr={proc.stderr!r}"


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
