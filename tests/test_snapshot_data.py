"""scripts/snapshot_data.py（Q-14 每日快照）的单测。

覆盖 opener 四条判据：
1. 另一连接持写事务时备份仍成功且可读（模拟 .51 上服务占着 demo.db 的场景）
2. 保留期清理只清 daily-* 目录，⛔ 不碰发版前的手工快照目录
3. 目标目录不可写 ⇒ 非零退出
4. 不触碰源库文件（db / -wal / -shm 的 mtime 与大小不变）
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from scripts import snapshot_data


def _make_data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    conn = sqlite3.connect(data / "demo.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t(v) VALUES ('committed')")
    conn.commit()
    conn.close()
    (data / "audit").mkdir()
    (data / "audit" / "decisions.jsonl").write_text('{"k": 1}\n', encoding="utf-8")
    (data / "candidate_outbound.switch").write_text("off\n", encoding="utf-8")
    return data


def _run(data: Path, root: Path, *extra: str) -> int:
    return snapshot_data.main(["--data-dir", str(data), "--backup-root", str(root), *extra])


def test_backup_succeeds_while_another_connection_holds_write_txn(tmp_path: Path) -> None:
    data = _make_data_dir(tmp_path)
    root = tmp_path / "backups"
    # 模拟运行中的服务：持一个未提交的写事务，并且 -shm/-wal 被它占着
    writer = sqlite3.connect(data / "demo.db", isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t(v) VALUES ('uncommitted')")
    try:
        rc = _run(data, root)
    finally:
        writer.rollback()
        writer.close()
    assert rc == 0

    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    assert len(dirs) == 1 and dirs[0].name.startswith("daily-")
    snap = dirs[0]
    copy = snap / "demo.db"
    assert copy.is_file()
    c = sqlite3.connect(copy)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [r[0] for r in c.execute("SELECT v FROM t ORDER BY id")] == ["committed"]
    c.close()
    # 旁路文件一并带走
    assert (snap / "audit" / "decisions.jsonl").read_text(encoding="utf-8") == '{"k": 1}\n'
    assert (snap / "candidate_outbound.switch").read_text(encoding="utf-8") == "off\n"
    assert (snap / "manifest.json").is_file()


def test_retention_keeps_newest_n_and_never_touches_manual_snapshots(tmp_path: Path) -> None:
    data = _make_data_dir(tmp_path)
    root = tmp_path / "backups"
    root.mkdir()
    old = [f"daily-202609{d:02d}-0300" for d in range(1, 16)]  # 15 份旧快照
    for name in old:
        (root / name).mkdir()
        (root / name / "demo.db").write_bytes(b"x")
    manual = root / "20260903-1003"  # 发版前手工快照，不是 daily-*，必须留
    manual.mkdir()
    (root / "daily-notes.txt").write_text("not a dir", encoding="utf-8")

    assert _run(data, root, "--keep", "3") == 0

    remaining = sorted(p.name for p in root.iterdir())
    daily = [n for n in remaining if n.startswith("daily-2")]
    assert len(daily) == 3
    assert daily[:2] == old[-2:]  # 最旧的都清掉，留最新 2 份旧的 + 本次新建的 1 份
    assert manual.is_dir()
    assert (root / "daily-notes.txt").is_file()


def test_retention_zero_keep_rejected(tmp_path: Path) -> None:
    data = _make_data_dir(tmp_path)
    assert _run(data, tmp_path / "backups", "--keep", "0") != 0


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="chmod 只读目录在 Windows/root 下不生效")
def test_unwritable_backup_root_exits_nonzero(tmp_path: Path) -> None:
    data = _make_data_dir(tmp_path)
    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o500)
    try:
        assert _run(data, root) != 0
    finally:
        root.chmod(0o700)
    assert list(root.iterdir()) == []


def test_missing_data_dir_or_no_db_exits_nonzero(tmp_path: Path) -> None:
    assert _run(tmp_path / "nope", tmp_path / "backups") != 0
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _run(empty, tmp_path / "backups") != 0


def test_source_files_untouched(tmp_path: Path) -> None:
    data = _make_data_dir(tmp_path)
    # 让 -wal / -shm 真实存在（服务在线时就是这个状态）
    holder = sqlite3.connect(data / "demo.db")
    holder.execute("INSERT INTO t(v) VALUES ('more')")
    holder.commit()
    names = ["demo.db", "demo.db-wal", "demo.db-shm"]
    for n in names:
        assert (data / n).exists(), n
    before = {n: (os.stat(data / n).st_mtime_ns, os.stat(data / n).st_size) for n in names}
    try:
        assert _run(data, tmp_path / "backups") == 0
        # 必须在 holder 关闭前取样：最后一个连接关闭会 checkpoint 并删 -wal，那是 sqlite 自己的事，不是快照脚本碰的
        after = {n: (os.stat(data / n).st_mtime_ns, os.stat(data / n).st_size) for n in names}
    finally:
        holder.close()
    assert before == after


def test_second_run_same_minute_does_not_clobber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _make_data_dir(tmp_path)
    root = tmp_path / "backups"
    monkeypatch.setattr(snapshot_data, "_stamp", lambda: "20260917-0300")
    assert _run(data, root) == 0
    assert _run(data, root) == 0
    names = sorted(p.name for p in root.iterdir())
    assert names == ["daily-20260917-0300", "daily-20260917-0300-2"]
    for n in names:
        assert (root / n / "demo.db").is_file()


def test_prune_failure_keeps_completed_snapshot_but_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _make_data_dir(tmp_path)
    root = tmp_path / "backups"

    def boom(_root: Path, _keep: int) -> list[Path]:
        raise OSError("旧目录被占用，删不掉")

    monkeypatch.setattr(snapshot_data, "prune", boom)
    assert _run(data, root) != 0
    dirs = [p for p in root.iterdir() if p.is_dir()]
    assert len(dirs) == 1 and (dirs[0] / "demo.db").is_file()


def test_integrity_failure_removes_partial_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _make_data_dir(tmp_path)
    root = tmp_path / "backups"

    def bad_backup(_src: Path, _dst: Path) -> int:
        raise snapshot_data.SnapshotError("demo.db integrity_check='corrupt'")

    monkeypatch.setattr(snapshot_data, "backup_db", bad_backup)
    assert _run(data, root) != 0
    assert [p for p in root.iterdir() if p.is_dir()] == []
