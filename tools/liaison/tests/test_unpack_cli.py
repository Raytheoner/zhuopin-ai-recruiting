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
