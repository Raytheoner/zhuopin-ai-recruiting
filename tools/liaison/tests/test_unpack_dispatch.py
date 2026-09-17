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
    BUDGET_ENV,
    CLAUDE_BIN_ENV,
    DEFAULT_BUDGET_USD,
    build_headless_argv,
    resolve_claude_bin,
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


def test_build_headless_argv_shape():
    argv = build_headless_argv("/usr/local/bin/claude", "5")
    assert argv[0] == "/usr/local/bin/claude"
    assert "-p" in argv
    assert "--output-format" in argv and "text" in argv
    assert "--permission-mode" in argv and "acceptEdits" in argv
    assert "--max-budget-usd" in argv and "5" in argv
    assert "--dangerously-skip-permissions" not in argv
    joined = " ".join(argv)
    assert "send-followup" not in joined
    assert "git push" not in joined
    for required in (
        "Read", "Edit", "Write", "Glob", "Grep",
        "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)",
        "Bash(git diff:*)", "Bash(git log:*)",
        # I4：必须是本仓库唯一 canonical 的调法（venv 解释器 + PYTHONPATH=.），
        # ⛔ 不是裸 "python -m tools.liaison unpack-signal:*"——那条匹配不上拆件
        # 会话实际会敲的命令，会让它在唯一需要的自我轮询命令上被拒绝。
        "Bash(PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal:*)",
        "Bash(PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison criteria:*)",
    ):
        assert required in argv


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
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
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
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
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
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
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
    assert argv[0] == "/usr/local/bin/claude"
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
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
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
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
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
        "HR_LIAISON_BOT_SECRET": "top-secret",
        "HR_LIAISON_GROUP_WEBHOOK": "https://example.invalid/webhook",
        "SOME_UNRELATED_VAR": "x",
    }

    dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
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
    }


def test_filter_child_env_omits_keys_absent_from_the_source_env():
    """白名单键在源环境里缺失时 ⛔ 不编造——结果字典里干脆没有那个键。"""
    from tools.liaison.unpack.dispatch import _filter_child_env

    assert _filter_child_env({"PATH": "/usr/bin"}) == {"PATH": "/usr/bin"}
    assert _filter_child_env({}) == {}


def test_filter_child_env_never_leaks_hr_liaison_secrets_regardless_of_allowlist_growth():
    """凭据边界回归闸（TD-47）：无论白名单以后为登录态再补多少非秘密系统变量，
    任意 `HR_LIAISON_*`（除 `HR_LIAISON_CLAUDE_BIN` 这个二进制路径覆盖键外）
    都不得进入子进程环境。"""
    from tools.liaison.unpack.dispatch import _filter_child_env

    source_env = {
        "PATH": "/usr/bin",
        "USER": "paulshao",
        "HR_LIAISON_CLAUDE_BIN": "/usr/local/bin/claude",
        "HR_LIAISON_BOT_SECRET": "top-secret",
        "HR_LIAISON_GROUP_WEBHOOK": "https://example.invalid/webhook",
        "HR_LIAISON_FUTURE_UNKNOWN_SECRET": "should-never-leak",
    }

    result = _filter_child_env(source_env)

    leaked_secrets = {
        key
        for key in result
        if key.startswith("HR_LIAISON_") and key != "HR_LIAISON_CLAUDE_BIN"
    }
    assert leaked_secrets == set()
