"""`unpack-signal`／`unpack-dispatch` 两条 CLI 子命令。

spec「信号只由 CLI 清除且只清检查点之前」＋「该子命令 MUST NOT 打开值守数据库」：
本文件专门守「两条子命令的模块 ⛔ 不 import tools.liaison.storage.db」这条
（AST 静态扫描，⛔ 不是"跑起来观察行为"——观察不到"没 import 什么"）。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_cli(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.liaison", *args],
        cwd=cwd, capture_output=True, text=True,
    )


def test_unpack_signal_probe_no_signal(tmp_path, monkeypatch):
    signal_path = tmp_path / "unpack-signal.json"
    monkeypatch.setenv("HR_LIAISON_SIGNAL_PATH", str(signal_path))
    result = _run_cli("unpack-signal", "--probe")
    assert result.returncode == 1
    assert "[NO-SIGNAL]" in result.stdout


def test_unpack_signal_probe_has_signal(tmp_path, monkeypatch):
    signal_path = tmp_path / "unpack-signal.json"
    signal_path.write_text(
        json.dumps({"pending": [{"msgid": "m1", "letter_number": "x", "archived_relpath": "y", "at": "z"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HR_LIAISON_SIGNAL_PATH", str(signal_path))
    result = _run_cli("unpack-signal", "--probe")
    assert result.returncode == 0
    assert "[SIGNAL]" in result.stdout


def test_unpack_signal_clear_before(tmp_path, monkeypatch):
    signal_path = tmp_path / "unpack-signal.json"
    signal_path.write_text(
        json.dumps({"pending": [
            {"msgid": "m1", "letter_number": "x", "archived_relpath": "y", "at": "2026-09-10T14:00:00.000000+08:00"},
            {"msgid": "m2", "letter_number": "x", "archived_relpath": "y", "at": "2026-09-10T15:00:00.000000+08:00"},
        ]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HR_LIAISON_SIGNAL_PATH", str(signal_path))
    result = _run_cli("unpack-signal", "--clear", "--before", "2026-09-10T14:30:00.000000+08:00")
    assert result.returncode == 0
    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m2"]


def test_unpack_dispatch_dry_run_prints_argv_without_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("HR_LIAISON_CLAUDE_BIN", "/usr/local/bin/claude")
    result = _run_cli("unpack-dispatch", "--dry-run")
    assert result.returncode == 0
    assert "/usr/local/bin/claude" in result.stdout
    assert "-p" in result.stdout
    assert "--dangerously-skip-permissions" not in result.stdout


def _module_source_files() -> list[Path]:
    return [
        REPO_ROOT / "tools" / "liaison" / "unpack" / "signal.py",
        REPO_ROOT / "tools" / "liaison" / "unpack" / "dispatch.py",
        REPO_ROOT / "tools" / "liaison" / "unpack" / "unpack_cli.py",
    ]


def test_unpack_subcommand_modules_do_not_import_storage_db():
    for path in _module_source_files():
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "storage.db" not in node.module and node.module != "db", (
                    f"{path} 违规 import 了 storage.db：{node.module}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "storage.db" not in alias.name, (
                        f"{path} 违规 import 了 storage.db：{alias.name}"
                    )


# ---- TD-48（0917O）：`unpack-dispatch --force` 也带真章程 ----------------------
"""`0917K`/`0917N` 实测：`--force` 走占位 prompt，子会话没有章程红线，却照常加载
仓库根 CLAUDE.md，据此自发改写并提交 `docs/session接力.md`。Shao Peishen
2026-09-17 答 `1a`：`--force` 与真实起活走同一份 `charter.compute_prompt`。
以下用例进程内调 `unpack_dispatch_main`，用替身接住 `dispatch_headless_unpack`，
⛔ 不起真实子进程。"""

from tools.liaison.unpack import charter as _charter
from tools.liaison.unpack import unpack_cli as _unpack_cli


def test_force_的prompt以真章程全文结尾(tmp_path, monkeypatch):
    received: dict = {}

    def fake_dispatch(**kwargs):
        received.update(kwargs)
        from tools.liaison.unpack.dispatch import DispatchOutcome
        return DispatchOutcome(status="started", pid=1, log_path="x.log")

    monkeypatch.setattr(_unpack_cli, "dispatch_headless_unpack", fake_dispatch)
    monkeypatch.setattr(_unpack_cli, "DEFAULT_LOCK_PATH", tmp_path / "lock.json")
    monkeypatch.setattr(_unpack_cli, "DEFAULT_LOG_DIR", tmp_path / "logs")

    assert _unpack_cli.unpack_dispatch_main(["--force"]) == 0

    charter_text = _charter.read_charter(_unpack_cli.REPO_ROOT)
    assert received["charter_text"] == charter_text
    assert received["prompt"].endswith(charter_text), "--force 的 prompt 必须以章程全文逐字结尾"
    preamble = received["prompt"][: -len(charter_text)]
    assert "（验收起活·无真实回件）" in preamble
    assert "FORCE-" in preamble
    assert "验收占位" not in received["prompt"]


def test_force_缺章程时不起进程且退出码非零(tmp_path, monkeypatch, capsys):
    calls: list = []
    monkeypatch.setattr(_unpack_cli, "dispatch_headless_unpack", lambda **kw: calls.append(kw))
    monkeypatch.setattr(_unpack_cli, "DEFAULT_LOCK_PATH", tmp_path / "lock.json")
    monkeypatch.setattr(_unpack_cli, "DEFAULT_LOG_DIR", tmp_path / "logs")
    # 把仓库根指到一个没有章程正本的空目录
    monkeypatch.setattr(_unpack_cli, "REPO_ROOT", tmp_path / "empty-repo")

    rc = _unpack_cli.unpack_dispatch_main(["--force"])

    assert rc != 0
    assert calls == [], "缺章程 ⛔ 不得起进程"
    out = capsys.readouterr()
    assert "章程" in (out.out + out.err)
