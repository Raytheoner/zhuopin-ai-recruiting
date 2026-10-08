"""`tools/liaison/unpack/dispatch.py` 的并发守卫与起活。

🔴 本文件里**真实 `subprocess.Popen`／真实 `os.kill` 一律不调用**——`popen`/
`is_alive` 全部用 fake 注入。真实 `os.kill(pid, 0)` 只在 `compute_is_alive`
自身的单测里对**本进程自己的 pid**（`os.getpid()`）调一次，用来验证"确实活着"
这一条正向路径，不构造/依赖任何外部进程。
"""

from __future__ import annotations

import os

import pytest

from tools.liaison.unpack.dispatch import compute_is_alive, compute_is_busy


def test_compute_is_alive_true_for_self_pid():
    assert compute_is_alive(os.getpid()) is True


def test_compute_is_alive_false_for_process_lookup_error():
    def _raiser(pid, sig):
        raise ProcessLookupError()

    assert compute_is_alive(-1, _kill=_raiser) is False


def test_compute_is_alive_false_for_permission_error():
    def _raiser(pid, sig):
        raise PermissionError()

    assert compute_is_alive(1, _kill=_raiser) is False


def test_compute_is_alive_false_for_any_other_exception():
    def _raiser(pid, sig):
        raise OSError("boom")

    assert compute_is_alive(1, _kill=_raiser) is False


def test_compute_is_busy_true_when_lock_valid_and_alive():
    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, lambda pid: True) is True


def test_compute_is_busy_false_when_alive_returns_false():
    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, lambda pid: False) is False


def test_compute_is_busy_false_when_lock_missing():
    assert compute_is_busy(None, lambda pid: True) is False


def test_compute_is_busy_false_when_lock_corrupted_json():
    assert compute_is_busy("{not json", lambda pid: True) is False


def test_compute_is_busy_false_when_lock_missing_pid_field():
    assert compute_is_busy('{"started_at": "x"}', lambda pid: True) is False


def test_compute_is_busy_false_when_is_alive_raises():
    def _raiser(pid):
        raise RuntimeError("判活查询本身抛异常")

    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, _raiser) is False


from pathlib import Path

from tools.liaison.unpack.dispatch import (
    AGENT_ENGINE_ENV,
    BUDGET_ENV,
    CLAUDE_BIN_ENV,
    CODEX_BIN_ENV,
    UNPACK_GUARD_ENV,
    DEFAULT_BUDGET_USD,
    build_headless_argv,
    build_headless_argv_codex,
    resolve_claude_bin,
    resolve_codex_bin,
)


def test_resolve_claude_bin_prefers_env_override():
    assert resolve_claude_bin({CLAUDE_BIN_ENV: "/opt/claude/bin/claude"}) == "/opt/claude/bin/claude"


def test_resolve_claude_bin_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.shutil.which", lambda name: "/usr/local/bin/claude"
    )
    assert resolve_claude_bin({}) == "/usr/local/bin/claude"


def test_resolve_claude_bin_falls_back_to_known_install_path(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.liaison.unpack.dispatch.shutil.which", lambda name: None)
    fake_home = tmp_path / "home"
    fake_local_bin = fake_home / ".local" / "bin"
    fake_local_bin.mkdir(parents=True)
    (fake_local_bin / "claude").write_text("", encoding="utf-8")
    monkeypatch.setattr("tools.liaison.unpack.dispatch.Path.home", lambda: fake_home)
    assert resolve_claude_bin({}) == str(fake_local_bin / "claude")


def test_resolve_claude_bin_none_when_all_three_fail(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.liaison.unpack.dispatch.shutil.which", lambda name: None)
    monkeypatch.setattr("tools.liaison.unpack.dispatch.Path.home", lambda: tmp_path / "empty-home")
    assert resolve_claude_bin({}) is None


def test_resolve_codex_bin_prefers_env_override():
    assert resolve_codex_bin({CODEX_BIN_ENV: "/opt/codex/bin/codex"}) == "/opt/codex/bin/codex"


def test_resolve_codex_bin_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.shutil.which", lambda name: "/usr/local/bin/codex"
    )
    assert resolve_codex_bin({}) == "/usr/local/bin/codex"


def test_resolve_codex_bin_falls_back_to_bundled_app_path(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.liaison.unpack.dispatch.shutil.which", lambda name: None)
    monkeypatch.setattr("tools.liaison.unpack.dispatch.Path.home", lambda: tmp_path / "no-home")
    fallback = tmp_path / "fake-app" / "codex"
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.CODEX_BIN_FALLBACKS",
        (str(fallback),),
    )
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fallback.write_text("", encoding="utf-8")
    assert resolve_codex_bin({}) == str(fallback)


def test_build_headless_argv_codex_shape():
    argv = build_headless_argv_codex("/usr/local/bin/codex", {})
    assert argv == [
        "/usr/local/bin/codex",
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "-c",
        "approval_policy=never",
        "-c",
        "forced_login_method=chatgpt",
        "--dangerously-bypass-hook-trust",
        "-",
    ]


def test_build_headless_argv_codex_honors_login_override():
    argv = build_headless_argv_codex("/x/codex", {"HR_CODEX_FORCED_LOGIN": "api"})
    assert "forced_login_method=api" in argv


def test_build_headless_argv_shape():
    from tools.liaison.tests._argv_rules import split_tool_rules

    argv = build_headless_argv("/usr/local/bin/claude", "5")
    assert argv[0] == "/usr/local/bin/claude"
    assert "-p" in argv
    assert "--output-format" in argv and "text" in argv
    assert "--permission-mode" in argv and "acceptEdits" in argv
    assert "--max-budget-usd" in argv and "5" in argv
    assert "--dangerously-skip-permissions" not in argv
    allowed, disallowed = split_tool_rules()
    joined = " ".join(allowed)
    assert "send-followup" not in joined
    # TD-48（0917O）：`git push` 从"不出现"升级为显式 deny——只看 allow 段没有它、
    # deny 段有它，⛔ 不能再对整条 argv 做 `not in`。
    assert "git push" not in joined
    assert "Bash(git push:*)" in disallowed
    for required in (
        "Read", "Glob", "Grep",
        # TD-48/TD-50：Edit（覆盖 Write）/git add 按章程 §三 路径收窄（清单与规则写法见 test_unpack_path_guard.py），
        # 这里只守形状：`git commit` 只放行 `-m` 形式。
        "Bash(git commit -m:*)", "Bash(git status:*)",
        "Bash(git diff:*)", "Bash(git log:*)",
        # I4：必须是本仓库唯一 canonical 的调法（venv 解释器 + PYTHONPATH=.），
        # ⛔ 不是裸 "python -m tools.liaison unpack-signal:*"——那条匹配不上拆件
        # 会话实际会敲的命令，会让它在唯一需要的自我轮询命令上被拒绝。
        "Bash(PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal:*)",
        "Bash(PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison criteria:*)",
    ):
        assert required in allowed


def test_default_budget_env_name_and_value():
    assert BUDGET_ENV == "HR_LIAISON_UNPACK_BUDGET_USD"
    assert DEFAULT_BUDGET_USD == "5"


# 追加到 tools/liaison/tests/test_unpack_dispatch.py 末尾
"""🔴 本节起，测试文件文首那条纪律再强调一次：以下用例的 `popen` 参数一律是
fake（`_FakeProcess`/`_raising_popen`），⛔ 没有一条调用真实 subprocess.Popen。
"""

import io
import json
from datetime import datetime, timezone

from tools.liaison.unpack.dispatch import DispatchOutcome, dispatch_headless_unpack


class _FakeStdin(io.BytesIO):
    def close(self):
        self.closed_with = self.getvalue()
        super().close()


class _FakeProcess:
    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.stdin = _FakeStdin()


def _fake_popen_factory(process: "_FakeProcess"):
    calls = []

    def _popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    _popen.calls = calls
    return _popen


NOW = datetime(2026, 9, 10, 14, 3, 0, tzinfo=timezone.utc)


def test_dispatch_charter_missing_is_failed_without_touching_anything(tmp_path):
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text=None,
        prompt="prompt",
        msgid="MSG-CHARTER",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome == DispatchOutcome(status="failed", reason="charter_missing", pid=None, log_path=None)
    assert popen.calls == []
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "lock.json").exists()


def test_dispatch_skipped_busy_when_lock_alive(tmp_path, monkeypatch):
    lock_path = tmp_path / "lock.json"
    lock_path.write_text('{"pid": 99999, "started_at": "x", "log": "y"}', encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: True
    )
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-BUSY",
        log_dir=tmp_path / "logs",
        lock_path=lock_path,
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "skipped_busy"
    assert popen.calls == []


def test_dispatch_log_dir_unwritable_is_failed(tmp_path, monkeypatch):
    # 把 log_dir 的父目录做成一个文件，mkdir(parents=True) 必炸 NotADirectoryError（OSError 子类）。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-LOGDIR",
        log_dir=blocker / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "log_file_failed"
    assert popen.calls == []


def test_dispatch_binary_not_found_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: None
    )
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-NOBIN",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={AGENT_ENGINE_ENV: "claude"},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "binary_not_found"
    assert popen.calls == []


def test_dispatch_popen_raises_is_process_create_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )

    def _raising_popen(argv, **kwargs):
        raise FileNotFoundError("二进制没了")

    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-POPEN-RAISES",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={AGENT_ENGINE_ENV: "claude"},
        now=NOW,
        popen=_raising_popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "process_create_failed"
    assert not (tmp_path / "lock.json").exists()


def test_dispatch_started_writes_lock_and_stdin_and_does_not_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )
    process = _FakeProcess(pid=4242)
    popen = _fake_popen_factory(process)
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="给拆件会话的完整 prompt",
        msgid="MSG-STARTED",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={AGENT_ENGINE_ENV: "claude"},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "started"
    assert outcome.pid == 4242
    # 非阻塞：fake process 没有 wait() 方法，能走到这里就证明本函数没调它。
    assert not hasattr(process, "wait_called")
    # stdin 写了 prompt 并关闭。
    assert process.stdin.closed_with == "给拆件会话的完整 prompt".encode("utf-8")
    assert process.stdin.closed is True
    # 锁文件已写。
    lock_payload = json.loads((tmp_path / "lock.json").read_text(encoding="utf-8"))
    assert lock_payload["pid"] == 4242
    # popen 的 argv/cwd/stdin/stdout/stderr 形状正确。
    argv, kwargs = popen.calls[0]
    # 1001I：起的是 wrapper（`python -m …session_runner -- …`），引擎 argv 逐字跟在 `--` 后。
    from tools.liaison.unpack.dispatch import SESSION_RUNNER_MODULE, resolve_python_bin

    assert argv[0] == resolve_python_bin()
    assert argv[1:3] == ["-m", SESSION_RUNNER_MODULE]
    assert argv[argv.index("--") + 1 :] == build_headless_argv("/usr/local/bin/claude", "5")
    assert kwargs["stdin"] is not None
    assert kwargs["cwd"] == str(_repo_root_for_test())


def _repo_root_for_test():
    from tools.liaison.unpack.dispatch import REPO_ROOT

    return REPO_ROOT


def test_dispatch_never_raises_even_on_unexpected_stdin_error(tmp_path, monkeypatch):
    """第四类失败——「其它未预期异常」：stdin.write 抛异常时不上抛、返回 failed。"""
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )

    class _BrokenStdinProcess:
        pid = 5555

        class _stdin:
            @staticmethod
            def write(data):
                raise BrokenPipeError("对端已关闭")

            @staticmethod
            def close():
                pass

        stdin = _stdin()

    popen = _fake_popen_factory(_BrokenStdinProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-BROKEN-STDIN",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={AGENT_ENGINE_ENV: "claude"},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "unexpected_error"


def test_dispatch_kills_the_child_process_when_lock_write_fails(tmp_path, monkeypatch):
    """I5：进程已经起来了，但收尾（写锁）失败——⛔ 不许留下一个已经在跑、却没有
    锁文件记录它存在的孤儿会话：下一次 `compute_is_busy` 读不到锁会判「不忙」，
    在同一份工作区上再起一个会话。修复要求这个失败分支必须 kill 掉已起的子进程。
    """
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )

    class _KillableProcess(_FakeProcess):
        def __init__(self, pid: int = 7777):
            super().__init__(pid=pid)
            self.killed = False

        def kill(self):
            self.killed = True

    process = _KillableProcess()
    popen = _fake_popen_factory(process)

    def _raising_write_lock_atomic(*args, **kwargs):
        raise OSError("磁盘满")

    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch._write_lock_atomic", _raising_write_lock_atomic
    )

    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-LOCK-WRITE-FAILS",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={AGENT_ENGINE_ENV: "claude"},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "unexpected_error"
    assert process.killed is True, "写锁失败后必须 kill 掉已经起来的子进程"


def test_dispatch_filters_child_env_to_the_allowlist(tmp_path, monkeypatch):
    """I6：子进程持有 Write + Bash(git commit:*)，⛔ 不能把父进程整份环境
    （含 HR_LIAISON_BOT_SECRET / HR_LIAISON_GROUP_WEBHOOK）透传下去——只放行
    子进程真正需要的键。TD-47：`USER` 是 macOS 上 `claude` 读取 Keychain
    登录态所需的非秘密系统变量（实验记录见 docs/tech-debt.md TD-47），
    同样只放行不编造。"""
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )
    process = _FakeProcess(pid=8888)
    popen = _fake_popen_factory(process)
    parent_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/x",
        "PYTHONPATH": ".",
        "USER": "paulshao",
        "HR_LIAISON_CLAUDE_BIN": "/usr/local/bin/claude",
        AGENT_ENGINE_ENV: "claude",
        "HR_LIAISON_BOT_SECRET": "top-secret",
        "HR_LIAISON_GROUP_WEBHOOK": "https://example.invalid/webhook",
        "SOME_UNRELATED_VAR": "x",
    }

    dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-ENV",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env=parent_env,
        now=NOW,
        popen=popen,
    )

    _, kwargs = popen.calls[0]
    child_env = kwargs["env"]
    assert "HR_LIAISON_BOT_SECRET" not in child_env
    assert "HR_LIAISON_GROUP_WEBHOOK" not in child_env
    assert "SOME_UNRELATED_VAR" not in child_env
    assert child_env == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/x",
        "PYTHONPATH": ".",
        "USER": "paulshao",
        "HR_LIAISON_CLAUDE_BIN": "/usr/local/bin/claude",
        AGENT_ENGINE_ENV: "claude",
        UNPACK_GUARD_ENV: "1",
    }


def test_filter_child_env_omits_keys_absent_from_the_source_env():
    """白名单键在源环境里缺失时 ⛔ 不编造——结果字典里干脆没有那个键。"""
    from tools.liaison.unpack.dispatch import _filter_child_env

    assert _filter_child_env({"PATH": "/usr/bin"}) == {"PATH": "/usr/bin"}
    assert _filter_child_env({}) == {}


def test_filter_child_env_never_leaks_hr_liaison_secrets_regardless_of_allowlist_growth():
    """凭据边界回归闸（TD-47）：无论白名单以后为登录态再补多少非秘密系统变量，
    任意 `HR_LIAISON_*`（除 `HR_LIAISON_CLAUDE_BIN` 与 `HR_LIAISON_UNPACK` 这两个
    非秘密键外）都不得进入子进程环境。"""
    from tools.liaison.unpack.dispatch import _filter_child_env

    source_env = {
        "PATH": "/usr/bin",
        "USER": "paulshao",
        "HR_LIAISON_CLAUDE_BIN": "/usr/local/bin/claude",
        UNPACK_GUARD_ENV: "1",
        "HR_LIAISON_BOT_SECRET": "top-secret",
        "HR_LIAISON_GROUP_WEBHOOK": "https://example.invalid/webhook",
        "HR_LIAISON_FUTURE_UNKNOWN_SECRET": "should-never-leak",
    }

    result = _filter_child_env(source_env)

    leaked_secrets = {
        key
        for key in result
        if key.startswith("HR_LIAISON_")
        and key not in ("HR_LIAISON_CLAUDE_BIN", UNPACK_GUARD_ENV)
    }
    assert leaked_secrets == set()
    assert result.get(UNPACK_GUARD_ENV) == "1"


def test_dispatch_codex_engine_starts_with_codex_argv_and_prompt(tmp_path, monkeypatch):
    """codex 引擎（默认）：引擎 argv 走 `exec --json --sandbox workspace-write`，prompt 仍从 stdin 写入；
    0930A：argv 含 `--dangerously-bypass-hook-trust`，子进程环境含 `HR_LIAISON_UNPACK=1`。
    1001I：`popen` 的目标是 wrapper，引擎 argv 逐字跟在 `--` 之后（wrapper 负责
    spawn 引擎 + 会话结束通知）。"""
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_codex_bin", lambda env: "/usr/local/bin/codex"
    )
    process = _FakeProcess(pid=5151)
    popen = _fake_popen_factory(process)
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="给拆件会话的完整 prompt",
        msgid="MSG-CODEX",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "started"
    assert outcome.pid == 5151
    argv, kwargs = popen.calls[0]
    from tools.liaison.unpack.dispatch import (
        SESSION_RUNNER_MODULE,
        _utc_log_stamp,
        resolve_python_bin,
    )

    assert argv[0] == resolve_python_bin()
    assert argv[1:3] == ["-m", SESSION_RUNNER_MODULE]
    # wrapper 自己的旗标先走，引擎 argv 一律在 `--` 之后逐字透传。
    head = argv[: argv.index("--")]
    assert head[3:5] == ["--msgid", "MSG-CODEX"]
    assert head[5:7] == ["--log-path", str(tmp_path / "logs" / f"{_utc_log_stamp(NOW)}.log")]
    engine_argv = argv[argv.index("--") + 1 :]
    assert engine_argv[0] == "/usr/local/bin/codex"
    assert engine_argv[1:4] == ["exec", "--json", "--sandbox"]
    assert "workspace-write" in engine_argv and "approval_policy=never" in engine_argv
    assert "--dangerously-bypass-hook-trust" in engine_argv
    assert engine_argv[-1] == "-"
    assert kwargs["env"][UNPACK_GUARD_ENV] == "1"
    assert process.stdin.closed_with == "给拆件会话的完整 prompt".encode("utf-8")
    assert kwargs["cwd"] == str(_repo_root_for_test())


# ─────────────────────────────────────────────────────────────────────────
# 1001I（2026-10-08 Shao Peishen 答 1a）：拆件链路的结果可见性不能只挂在
# `unpack-signal.json` 上——起活失败与会话结束各给本人一条私信，零轮询。
#
# 本节钉两半：① dispatch 四类失败各入队一行（同 msgid 幂等、busy 不通知、
# 正文无候选人信息）；② 会话 wrapper 等引擎退出、按退出码入队。
# ⚠️ 库路径由 `conftest.py` 的 TD-49 夹具顶到 tmp_path（⛔ 不连真库）。
# ─────────────────────────────────────────────────────────────────────────

from tools.liaison.storage import db as liaison_db  # noqa: E402
from tools.liaison.unpack import session_runner  # noqa: E402
from tools.liaison.unpack.dispatch import (  # noqa: E402
    DISPATCH_FAILURE_DEDUPE_PREFIX,
    build_dispatch_failure_notice,
    dispatch_headless_unpack as _dispatch,
)


def _outbox_rows() -> list[tuple[str, str]]:
    """读（夹具已顶到 tmp 的）发件箱：[(dedupe_key, body)]，按 id。"""
    conn = liaison_db.get_connection()
    try:
        liaison_db.init_schema(conn)
        return conn.execute(
            "SELECT dedupe_key, body FROM owner_notify_outbox ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _failing_call(reason: str, tmp_path, monkeypatch, msgid: str) -> DispatchOutcome:
    """按 reason 造出对应的那类失败并真调一次 dispatch（⛔ 不真实起进程）。

    同一份 kwargs 复用给五类失败、只按 reason 改一处——这样「四类失败都能走到
    通知」这条断言才是对机制说的，不是对五个手抄的调用点说的。
    """
    monkeypatch.setattr("tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False)
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )
    kwargs: dict = dict(
        charter_text="章程全文",
        prompt="prompt",
        msgid=msgid,
        letter_number="人事部#7",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={AGENT_ENGINE_ENV: "claude"},
        now=NOW,
        popen=_fake_popen_factory(_FakeProcess()),
    )
    if reason == "charter_missing":
        kwargs["charter_text"] = None
    elif reason == "log_file_failed":
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        kwargs["log_dir"] = blocker / "logs"
    elif reason == "binary_not_found":
        monkeypatch.setattr("tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: None)
    elif reason == "process_create_failed":

        def _raising_popen(argv, **kw):
            raise FileNotFoundError("二进制没了")

        kwargs["popen"] = _raising_popen
    elif reason == "unexpected_error":

        def _raising_write_lock_atomic(*args, **kw):
            raise OSError("磁盘满")

        monkeypatch.setattr(
            "tools.liaison.unpack.dispatch._write_lock_atomic", _raising_write_lock_atomic
        )

        class _KillableProcess(_FakeProcess):
            def kill(self):
                pass

        kwargs["popen"] = _fake_popen_factory(_KillableProcess())
    else:  # pragma: no cover —— 参数化里不会出现别的取值
        raise AssertionError(reason)
    return _dispatch(**kwargs)


@pytest.mark.parametrize(
    "reason",
    [
        "charter_missing",
        "log_file_failed",
        "binary_not_found",
        "process_create_failed",
        "unexpected_error",
    ],
)
def test_dispatch_四类失败各入队一行且同msgid幂等(reason, tmp_path, monkeypatch):
    msgid = f"MSG-{reason}"
    first = _failing_call(reason, tmp_path, monkeypatch, msgid)
    second = _failing_call(reason, tmp_path, monkeypatch, msgid)

    assert first.status == "failed" and first.reason == reason
    assert second.status == "failed"
    rows = _outbox_rows()
    assert [key for key, _ in rows] == [f"{DISPATCH_FAILURE_DEDUPE_PREFIX}:{msgid}"], rows
    (_, body), = rows
    assert msgid in body and reason in body and "人事部#7" in body
    assert "候选人" not in body


def test_dispatch_skipped_busy_不通知(tmp_path, monkeypatch):
    """busy 不通知（避免噪音）：锁里 pid 活着 ⇒ 只落审计，发件箱一行为空。"""
    lock_path = tmp_path / "lock.json"
    lock_path.write_text('{"pid": 99999, "started_at": "x", "log": "y"}', encoding="utf-8")
    monkeypatch.setattr("tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: True)

    outcome = _dispatch(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-BUSY-1001I",
        log_dir=tmp_path / "logs",
        lock_path=lock_path,
        env={AGENT_ENGINE_ENV: "claude"},
        now=NOW,
        popen=_fake_popen_factory(_FakeProcess()),
    )

    assert outcome.status == "skipped_busy"
    assert _outbox_rows() == []


def test_dispatch_通知写库失败只记日志不上抛(tmp_path, monkeypatch):
    """与起活审计同基调：通知写不进去⛔ 不许改变起活结果、⛔ 不许上抛。"""

    def _boom(**kwargs):
        raise RuntimeError("磁盘满")

    monkeypatch.setattr(session_runner, "enqueue_owner_notify", _boom)
    outcome = _dispatch(
        charter_text=None,
        prompt="",
        msgid="MSG-NOTIFY-FAILS",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=_fake_popen_factory(_FakeProcess()),
    )
    assert outcome == DispatchOutcome(
        status="failed", reason="charter_missing", pid=None, log_path=None
    )


@pytest.mark.parametrize(
    "reason",
    [
        "charter_missing",
        "log_file_failed",
        "binary_not_found",
        "process_create_failed",
        "unexpected_error",
    ],
)
def test_起活失败通知正文只由入参拼出(reason):
    """正文形状逐字钉死——它只含信件编号/msgid/原因/日志/时刻，⛔ 不回指归档件、
    更不含任何候选人个人信息（候选人信息不进任何对外文案）。"""
    outcome = DispatchOutcome(status="failed", reason=reason, log_path="/x/1.log")
    dedupe_key, body = build_dispatch_failure_notice(
        outcome=outcome, msgid="MSG1", letter_number="人事部#7", now=NOW
    )
    assert dedupe_key == f"{DISPATCH_FAILURE_DEDUPE_PREFIX}:MSG1"
    assert body == "\n".join(
        (
            "【HR·拆件会话起活失败】",
            "- 信件编号：人事部#7",
            "- 消息标识（msgid）：MSG1",
            f"- 失败原因：{reason}",
            "- 日志路径：/x/1.log",
            f"- 时刻：{NOW.isoformat()}",
        )
    )


def test_起活失败通知对非失败结果为空并且占位齐全():
    """`started`／`skipped_busy` ⇒ `None`（⛔ 不发）；信件编号/日志缺失 ⇒ 占位。"""
    assert (
        build_dispatch_failure_notice(
            outcome=DispatchOutcome(status="started", pid=1, log_path="x"),
            msgid="m",
            letter_number="人事部#1",
            now=NOW,
        )
        is None
    )
    assert (
        build_dispatch_failure_notice(
            outcome=DispatchOutcome(status="skipped_busy"),
            msgid="m",
            letter_number=None,
            now=NOW,
        )
        is None
    )
    _, body = build_dispatch_failure_notice(
        outcome=DispatchOutcome(status="failed", reason="log_file_failed"),
        msgid="m",
        letter_number=None,
        now=NOW,
    )
    assert "- 信件编号：（未匹配）" in body
    assert "- 日志路径：（未生成）" in body


# ── 会话 wrapper（1001I：会话结束给本人一条私信）───────────────────────────


class _ExitProcess:
    """只回答 `wait()` 的替身进程——wrapper 不读它的任何输出（⛔ 不判定结论）。"""

    def __init__(self, rc: int) -> None:
        self._rc = rc

    def wait(self) -> int:
        return self._rc


def _stepping_monotonic(values):
    it = iter(values)
    return lambda: next(it)


def test_wrapper_按引擎退出码落库且同msgid幂等(tmp_path):
    calls: list[tuple[list[str], dict]] = []

    def _popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return _ExitProcess(3)

    engine_argv = ["/usr/local/bin/codex", "exec", "--json", "-"]
    rc = session_runner.run_session(
        engine_argv,
        msgid="MSG-EXIT",
        log_path="/x/1.log",
        popen=_popen,
        monotonic=_stepping_monotonic([100.0, 112.5]),
        now=lambda: NOW,
    )
    assert rc == 3
    # 引擎 argv 逐字透传，stdin/stdout/stderr 全部继承（⛔ 不重定向、不缓冲）。
    assert calls == [(engine_argv, {"cwd": str(session_runner.REPO_ROOT)})]

    # 同一 msgid 再来一次（重投/重放）⇒ 仍然只有一行。
    session_runner.run_session(
        engine_argv,
        msgid="MSG-EXIT",
        log_path="/x/1.log",
        popen=_popen,
        monotonic=_stepping_monotonic([1.0, 2.0]),
        now=lambda: NOW,
    )
    rows = _outbox_rows()
    assert [key for key, _ in rows] == [
        f"{session_runner.SESSION_EXIT_DEDUPE_PREFIX}:MSG-EXIT"
    ]
    (_, body), = rows
    assert "MSG-EXIT" in body
    assert "退出码：3" in body
    assert "耗时：12.5 秒" in body
    assert "/x/1.log" in body
    assert "候选人" not in body


def test_wrapper_引擎起不来也落一行并返回非零(tmp_path):
    def _raising_popen(argv, **kwargs):
        raise FileNotFoundError("引擎没了")

    rc = session_runner.run_session(
        ["/nope/codex"],
        msgid="MSG-SPAWN-FAIL",
        log_path="/x/2.log",
        popen=_raising_popen,
        now=lambda: NOW,
    )
    assert rc == session_runner.SPAWN_FAILED_RC
    (key, body), = _outbox_rows()
    assert key == f"{session_runner.SESSION_EXIT_DEDUPE_PREFIX}:MSG-SPAWN-FAIL"
    assert f"退出码：{session_runner.SPAWN_FAILED_EXIT_CODE}" in body
    assert "备注：引擎进程未能创建：FileNotFoundError" in body


def test_wrapper_入队失败不改变退出码(tmp_path, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("磁盘满")

    monkeypatch.setattr(session_runner, "enqueue_owner_notify", _boom)
    rc = session_runner.run_session(
        ["/usr/local/bin/codex"],
        msgid="MSG-NOTIFY-BOOM",
        log_path="/x/3.log",
        popen=lambda argv, **kwargs: _ExitProcess(0),
        now=lambda: NOW,
    )
    assert rc == 0


def test_wrapper_main_把引擎argv逐字转给引擎并剔掉分隔符(tmp_path):
    seen: dict = {}

    def _popen(argv, **kwargs):
        seen["argv"] = argv
        return _ExitProcess(0)

    rc = session_runner.main(
        [
            "--msgid", "MSG-MAIN",
            "--log-path", "/x/4.log",
            "--",
            "/usr/local/bin/codex", "exec", "--json", "-",
        ],
        popen=_popen,
        now=lambda: NOW,
    )
    assert rc == 0
    assert seen["argv"] == ["/usr/local/bin/codex", "exec", "--json", "-"]
    assert _outbox_rows()[0][0] == f"{session_runner.SESSION_EXIT_DEDUPE_PREFIX}:MSG-MAIN"


def test_wrapper_main_缺引擎argv时不起进程(capsys):
    assert session_runner.main(["--msgid", "m", "--log-path", "L"]) == session_runner.SPAWN_FAILED_RC
    assert "引擎 argv" in capsys.readouterr().err


def test_会话结束通知正文形状():
    key, body = session_runner.build_session_exit_notice(
        msgid="MSG1", exit_code=1, log_path="/x/1.log", elapsed_seconds=12.34, now=NOW
    )
    assert key == f"{session_runner.SESSION_EXIT_DEDUPE_PREFIX}:MSG1"
    assert body == "\n".join(
        (
            "【HR·拆件会话结束】",
            "- 消息标识（msgid）：MSG1",
            "- 退出码：1",
            "- 耗时：12.3 秒",
            "- 日志路径：/x/1.log",
            f"- 时刻：{NOW.isoformat()}",
        )
    )


# ─────────────────────────────────────────────────────────────────────────
# 1001L（2026-10-08 Shao Peishen 答 2a）：拆件会话的收口搬到值守侧。
# 10-08 人事部#3 的 codex 首跑把结论全写完却卡在收口（沙箱对 .git 只读），
# 文档已写、无法提交、信号清不掉 ⇒ 修法＝wrapper 在引擎退出后**代提交**章程
# §三 白名单路径并按 §一.5 清信号（照 0930D「执行器代提交」先例）。
# 🔴 本节所有 git / CLI 调用一律注入替身（_FakeRunner），⛔ 一条都不真跑 git——
# 真实 git add/commit 会在跑测试的工作区上留下真实提交。
# ─────────────────────────────────────────────────────────────────────────

import sys  # noqa: E402

from tools.liaison.unpack.dispatch import (  # noqa: E402
    CHARTER_WRITABLE_PATHS,
    NO_LETTER_NUMBER_PLACEHOLDER,
)
from tools.liaison.unpack.session_runner import (  # noqa: E402
    NO_CHECKPOINT_NOTE,
    NONZERO_EXIT_NOTE,
    build_unpack_commit_message,
    clear_unpack_signal,
    collect_changes_and_clear_signal,
)

CHECKPOINT = "2026-10-08T12:35:00.000000+08:00"


class _Completed:
    """subprocess.CompletedProcess 的最小替身：只留调用方真会看的三样。"""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeRunner:
    """按 argv 形状返回预置结果的替身，并逐条记录调用（⛔ 不真跑 git / CLI）。"""

    def __init__(
        self,
        *,
        status_out: str = "",
        tracked_out: str = "",
        status_rc: int = 0,
        add_rc: int = 0,
        commit_rc: int = 0,
        clear_rc: int = 0,
    ) -> None:
        self._status_out = status_out
        self._tracked_out = tracked_out
        self._status_rc = status_rc
        self._add_rc = add_rc
        self._commit_rc = commit_rc
        self._clear_rc = clear_rc
        self.calls: list[tuple[list[str], Path, dict | None]] = []

    def __call__(self, argv, *, cwd, env=None):
        self.calls.append((list(argv), cwd, env))
        if argv[:3] == ["git", "status", "--porcelain"]:
            return _Completed(self._status_rc, self._status_out)
        if argv[:2] == ["git", "ls-files"]:
            return _Completed(0, self._tracked_out)
        if argv[:2] == ["git", "add"]:
            return _Completed(self._add_rc)
        if argv[:2] == ["git", "commit"]:
            return _Completed(self._commit_rc)
        if argv[1:4] == ["-m", "tools.liaison", "unpack-signal"]:
            return _Completed(self._clear_rc)
        raise AssertionError(f"替身收到未预期的命令：{argv}")

    @property
    def argvs(self) -> list[list[str]]:
        return [argv for argv, _, _ in self.calls]

    def argvs_of(self, *head: str) -> list[list[str]]:
        return [argv for argv in self.argvs if argv[: len(head)] == list(head)]


def test_代提交_无改动时不commit但清信号(tmp_path):
    """`git status` 空 ⇒ 视为已落档，直接进第 3 步清信号（⛔ 不做空提交）。"""
    runner = _FakeRunner(status_out="")

    note = collect_changes_and_clear_signal(
        exit_code=0,
        checkpoint=CHECKPOINT,
        letter_number="人事部#3",
        msgid="MSG-NOCHANGE",
        cwd=tmp_path,
        run_command=runner,
    )

    assert runner.argvs[0] == [
        "git", "status", "--porcelain", "--untracked-files=all", "--", *CHARTER_WRITABLE_PATHS
    ]
    assert runner.argvs_of("git", "ls-files") == [], "无改动时不必再探可暂存路径"
    assert runner.argvs_of("git", "add") == []
    assert runner.argvs_of("git", "commit") == []
    assert runner.argvs[-1][:5] == [
        sys.executable,
        "-m",
        "tools.liaison",
        "unpack-signal",
        "--clear",
    ]
    assert runner.argvs[-1][5:] == ["--before", CHECKPOINT]
    assert "无改动" in note
    assert note.endswith(f"信号已清（before={CHECKPOINT}）")
    # 清信号必须在仓库根、`PYTHONPATH=.` 下跑（否则 import 不到 tools.liaison）。
    _, clear_cwd, clear_env = runner.calls[-1]
    assert clear_cwd == tmp_path
    assert clear_env["PYTHONPATH"] == "."


def test_代提交_有改动则先add并commit再清信号(tmp_path):
    """有改动 ⇒ `git add -- <可暂存的白名单路径>` → `git commit -m …` → 清信号。

    白名单里的四条并非都存在（`docs/跟进信/回件/` 首次回件前是空的、
    `docs/跟进信/口径点台账.md` 未转态前不存在），而 `git add` 对匹配不到的
    pathspec 是致命错误（exit 128、什么都不暂存）⇒ 必须先按「工作区存在 or 索引已跟踪」
    收窄，本用例把这条也钉住。
    """
    status_out = " M docs/session接力.md\n?? docs/跟进信/回件/人事部#3-2026-10-08.md\n"
    runner = _FakeRunner(status_out=status_out, tracked_out="docs/session接力.md\n")
    # 新落档的文件在盘上真的存在；台账/README 两条不存在也不该进 add 列表。
    new_file = tmp_path / "docs" / "跟进信" / "回件" / "人事部#3-2026-10-08.md"
    new_file.parent.mkdir(parents=True)
    new_file.write_text("回灌结论\n", encoding="utf-8")

    note = collect_changes_and_clear_signal(
        exit_code=0,
        checkpoint=CHECKPOINT,
        letter_number="人事部#3",
        msgid="abcdef12-3456-7890",
        cwd=tmp_path,
        run_command=runner,
    )

    assert runner.argvs[1] == ["git", "ls-files", "--", *CHARTER_WRITABLE_PATHS]
    assert runner.argvs[2] == [
        "git",
        "add",
        "--",
        "docs/跟进信/回件/",
        "docs/session接力.md",
    ]
    commit_argv = runner.argvs[3]
    assert commit_argv[:3] == ["git", "commit", "-m"]
    assert commit_argv[3] == "chore(unpack): 人事部#3 回灌落档（abcdef12）"
    # 顺序：先提交、后清信号（章程 §一.5）。
    assert runner.argvs[4][3:5] == ["unpack-signal", "--clear"]
    assert "代提交 2 文件" in note
    assert note.endswith(f"信号已清（before={CHECKPOINT}）")


def test_代提交_白名单路径都不存在时不add且不清信号(tmp_path):
    """白名单四条都既不在工作区也不在索引里 ⇒ 无可暂存内容，⛔ 不拿空 add 去撞
    `git add` 的 128，也⛔不清信号（fail-closed）。"""
    runner = _FakeRunner(status_out=" M docs/session接力.md\n", tracked_out="")

    note = collect_changes_and_clear_signal(
        exit_code=0,
        checkpoint=CHECKPOINT,
        letter_number="人事部#3",
        msgid="MSG-EMPTY-ADD",
        cwd=tmp_path,
        run_command=runner,
    )

    assert runner.argvs_of("git", "add") == []
    assert runner.argvs_of("git", "commit") == []
    assert runner.argvs_of(sys.executable, "-m") == []
    assert note == "代提交失败（白名单路径无可暂存内容），未代提交、未清信号"


def test_代提交_commit失败则不清信号且通知带失败备注(tmp_path):
    runner = _FakeRunner(
        status_out=" M docs/session接力.md\n",
        tracked_out="docs/session接力.md\n",
        commit_rc=1,
    )

    rc = session_runner.run_session(
        ["/usr/local/bin/codex"],
        msgid="MSG-COMMIT-FAIL",
        log_path="/x/9.log",
        popen=lambda argv, **kwargs: _ExitProcess(0),
        monotonic=_stepping_monotonic([0.0, 1.0]),
        now=lambda: NOW,
        checkpoint=CHECKPOINT,
        letter_number="人事部#3",
        run_command=runner,
    )

    assert rc == 0
    assert runner.argvs_of("git", "commit") != []
    assert runner.argvs_of(sys.executable, "-m") == [], "commit 失败后 ⛔ 不许清信号"
    (_, body), = _outbox_rows()
    assert "备注：代提交失败（git commit 退出码 1），未代提交、未清信号" in body


def test_代提交_非零退出不动git不清信号(tmp_path):
    """引擎中途死掉时白名单路径可能只写了一半——半份结论 ⛔ 不提交，信号也不清。"""
    runner = _FakeRunner()

    rc = session_runner.run_session(
        ["/usr/local/bin/codex"],
        msgid="MSG-NONZERO-EXIT",
        log_path="/x/8.log",
        popen=lambda argv, **kwargs: _ExitProcess(3),
        monotonic=_stepping_monotonic([0.0, 2.0]),
        now=lambda: NOW,
        checkpoint=CHECKPOINT,
        letter_number="人事部#3",
        run_command=runner,
    )

    assert rc == 3
    assert runner.calls == []
    (_, body), = _outbox_rows()
    assert f"备注：{NONZERO_EXIT_NOTE}" in body
    assert "未代提交、未清信号" in body


def test_代提交_没拿到检查点时整条留步(tmp_path):
    """没检查点就无从判断「清到哪儿为止」——代提交也一并跳过，⛔ 不留半吊子态。"""
    runner = _FakeRunner(status_out=" M docs/session接力.md\n")

    note = collect_changes_and_clear_signal(
        exit_code=0,
        checkpoint="",
        letter_number="人事部#3",
        msgid="MSG-NOCHECKPOINT",
        cwd=tmp_path,
        run_command=runner,
    )

    assert runner.calls == []
    assert note == NO_CHECKPOINT_NOTE


def test_代提交_只带白名单路径且白名单外路径进不了任何命令(tmp_path):
    """并发协议的机器判据：`git status`／`git add` 只提这四条白名单路径——
    `git status` 输出里的白名单外改动（别人的泳道）⛔ 一个字都不进 argv。"""
    out_of_scope = "tools/liaison/unpack/dispatch.py"
    runner = _FakeRunner(
        status_out=f" M {out_of_scope}\n M docs/session接力.md\n",
        tracked_out="docs/session接力.md\n",
    )

    collect_changes_and_clear_signal(
        exit_code=0,
        checkpoint=CHECKPOINT,
        letter_number="人事部#3",
        msgid="MSG-SCOPE",
        cwd=tmp_path,
        run_command=runner,
    )

    for argv in runner.argvs:
        if argv[:2] == ["git", "add"]:
            assert argv[:3] == ["git", "add", "--"]
            assert set(argv[3:]) <= set(CHARTER_WRITABLE_PATHS)
            assert argv[3:] == ["docs/session接力.md"], "白名单外的改动 ⛔ 不许进 add"
    joined = " ".join(token for argv in runner.argvs for token in argv)
    assert out_of_scope not in joined
    assert "git add -A" not in joined and "git add ." not in joined


def test_代提交_commit_message缺信件编号用占位():
    assert build_unpack_commit_message(letter_number=None, msgid="0123456789") == (
        f"chore(unpack): {NO_LETTER_NUMBER_PLACEHOLDER} 回灌落档（01234567）"
    )
    assert build_unpack_commit_message(letter_number="人事部#7", msgid="short") == (
        "chore(unpack): 人事部#7 回灌落档（short）"
    )


def test_清信号命令失败只留备注不上抛(tmp_path):
    """清信号失败 ⇒ 备注写明原因（信号原样保留给下一轮），⛔ 不上抛、⛔ 不改返回值。"""
    runner = _FakeRunner(status_out="", clear_rc=1)

    note = clear_unpack_signal(checkpoint=CHECKPOINT, cwd=tmp_path, run_command=runner)

    assert note == "清信号失败（退出码 1），信号保留待下一轮"


def test_wrapper_main_把检查点与信件编号带进收口(tmp_path):
    """argv 解析这一段：`--checkpoint`／`--letter-number` 要真的走到收口（否则
    生产里 wrapper 拿到空检查点 ⇒ 整条留步，本次修复等于没生效）。"""
    runner = _FakeRunner(
        status_out=" M docs/session接力.md\n", tracked_out="docs/session接力.md\n"
    )

    rc = session_runner.main(
        [
            "--msgid", "M-1001L",
            "--log-path", "/x/7.log",
            "--checkpoint", CHECKPOINT,
            "--letter-number", "人事部#3",
            "--",
            "/usr/local/bin/codex", "exec", "--json", "-",
        ],
        popen=lambda argv, **kwargs: _ExitProcess(0),
        now=lambda: NOW,
        run_command=runner,
    )

    assert rc == 0
    assert runner.argvs_of("git", "commit")[0][3] == (
        "chore(unpack): 人事部#3 回灌落档（M-1001L）"
    )
    assert runner.argvs[-1][5:] == ["--before", CHECKPOINT]


def test_dispatch_把检查点与信件编号透传给wrapper(tmp_path, monkeypatch):
    """1001L 交付物 1：`dispatch_headless_unpack` 收的两个字段原样进 wrapper argv
    （位置在 `--` 之前，引擎 argv 仍逐字透传在 `--` 之后）。缺省时传空串——
    wrapper 侧据此整条留步，⛔ 不在这里替调用方编造检查点。"""
    monkeypatch.setattr("tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False)
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_codex_bin", lambda env: "/usr/local/bin/codex"
    )
    popen = _fake_popen_factory(_FakeProcess())

    dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-CP",
        letter_number="人事部#3",
        checkpoint=CHECKPOINT,
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    argv, _ = popen.calls[0]
    head = argv[: argv.index("--")]
    assert head[head.index("--checkpoint") + 1] == CHECKPOINT
    assert head[head.index("--letter-number") + 1] == "人事部#3"

    # 缺省（未传）⇒ 空串，而不是某个编造的时刻。
    popen = _fake_popen_factory(_FakeProcess())
    dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        msgid="MSG-CP-EMPTY",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    argv, _ = popen.calls[0]
    head = argv[: argv.index("--")]
    assert head[head.index("--checkpoint") + 1] == ""
    assert head[head.index("--letter-number") + 1] == ""
