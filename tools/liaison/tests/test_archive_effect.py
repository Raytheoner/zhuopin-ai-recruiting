"""归档编排（4.5）：先落材料、后写台账，重复投递不产生第二份（4.6/4.8）。

本文件的核心不是"能写进去"，是**顺序**与**中间态方向**：
"文件已落、DB 行未写"可收敛；"DB 行已写、文件缺失"是不可恢复的谎。
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from tools.liaison.archive import (
    ArchiveOutcome,
    InboundAttachment,
    archive_message,
)
from tools.liaison.attachments import AttachmentIntegrityError
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:30:00+08:00"


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


def _archive(conn, root, *, msgid="m1", thread_id="tanglp", filename=None, payload=None, content="收到"):
    attachment = (
        InboundAttachment(filename=filename, payload=payload) if payload is not None else None
    )
    return archive_message(
        conn,
        thread_id=thread_id,
        msgid=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        msgtype="file" if attachment else "text",
        content=content,
        attachment=attachment,
        archive_root=root,
    )


def _rows(conn):
    return conn.execute(
        "SELECT msgid, thread_id, attachments_json FROM liaison_message ORDER BY msgid"
    ).fetchall()


# ─────────────────────────────────────────────────────────────────────────
# 4.5 顺序：材料先落、台账后写
# ─────────────────────────────────────────────────────────────────────────


def test_text_message_without_attachment_is_archived(conn, root):
    outcome = _archive(conn, root, content="明天面试改到下午三点")

    assert isinstance(outcome, ArchiveOutcome)
    assert outcome.newly_archived is True
    assert outcome.attachments == ()
    assert _rows(conn) == [("m1", "tanglp", "[]")]
    assert_effect_log_identity(conn)


def test_attachment_lands_on_disk_and_is_recorded_in_the_ledger(conn, root):
    payload = b"\x50\x4b\x03\x04binary-ish\x00\xff"
    outcome = _archive(conn, root, filename="岗位要求确认反馈表v3.xlsx", payload=payload)

    stored = outcome.attachments[0]
    assert (root / stored.relative_path).read_bytes() == payload

    recorded = json.loads(_rows(conn)[0][2])
    assert recorded == [stored.as_dict()]
    assert recorded[0]["filename"] == "m1__岗位要求确认反馈表v3.xlsx"
    assert_effect_log_identity(conn)


def test_attachments_json_keeps_chinese_readable(conn, root):
    """`ensure_ascii=False`：库里存的是可读的中文，不是 `\\uXXXX` 转义。

    这不是审美——排障时要靠 `sqlite3 data/liaison.db 'select ...'` 直接看，
    一串转义码会让"这条到底是哪个文件"变成一次额外的解码。
    """
    _archive(conn, root, filename="反馈表.xlsx", payload=b"x")
    assert "反馈表" in _rows(conn)[0][2]
    assert_effect_log_identity(conn)


def test_store_is_called_before_the_ledger_write(conn, root, monkeypatch):
    """运行期判据：调用顺序必须是 落盘 → 写台账。"""
    calls = []
    import tools.liaison.archive as archive_module

    real_store = archive_module.store_attachment
    real_effect = archive_module.effect_archive_message
    monkeypatch.setattr(
        archive_module, "store_attachment",
        lambda *a, **k: (calls.append("store"), real_store(*a, **k))[1],
    )
    monkeypatch.setattr(
        archive_module, "effect_archive_message",
        lambda *a, **k: (calls.append("ledger"), real_effect(*a, **k))[1],
    )

    _archive(conn, root, filename="a.bin", payload=b"x")

    assert calls == ["store", "ledger"], f"D3 的顺序被反了：{calls}"
    assert_effect_log_identity(conn)


def test_source_order_puts_store_attachment_before_the_effect_call():
    """静态判据：源码里 `store_attachment` 的调用行号必须小于 `effect_archive_message` 的。

    运行期那条测试可以被一个"先算后写"的重构绕过去（比如把落盘挪进 effect
    的参数求值里）。这条盯的是源码形态，⛔ 两条都要，不许二选一。
    """
    tree = ast.parse(inspect.getsource(archive_message))
    lines = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in ("store_attachment", "effect_archive_message"):
                lines.setdefault(name, node.lineno)
    assert "store_attachment" in lines and "effect_archive_message" in lines, lines
    assert lines["store_attachment"] < lines["effect_archive_message"], (
        f"⛔ D3 顺序在源码里被反了：{lines}"
    )


def test_ledger_row_is_never_written_when_the_attachment_fails_to_land(conn, root, monkeypatch):
    """spec「MUST NOT 出现'台账已记但材料缺失'」的直接断言。

    落盘炸了 ⇒ 台账一行都不能有。⛔ 不许"先记上等下次补文件"——
    那句台账就是一个不可恢复的谎。
    """
    import tools.liaison.archive as archive_module

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(archive_module, "store_attachment", boom)

    with pytest.raises(OSError):
        _archive(conn, root, filename="a.bin", payload=b"x")

    assert _rows(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_conflicting_bytes_at_the_same_path_abort_before_the_ledger_write(conn, root):
    """同一 msgid 带来两份不同字节 ⇒ 抛，且台账保持原状（既有那一行不变）。"""
    _archive(conn, root, filename="a.bin", payload=b"first")
    assert len(_rows(conn)) == 1

    with pytest.raises(AttachmentIntegrityError):
        _archive(conn, root, msgid="m1", filename="a.bin", payload=b"second")

    assert len(_rows(conn)) == 1
    assert_effect_log_identity(conn)


def test_archive_message_never_commits_by_itself():
    """⛔ 本模块不许出现 `conn.commit()` / `conn.rollback()`。

    提交由 `idempotent_effect` 独占——那是"业务写与幂等记录同一个 BEGIN"
    成立的结构前提（`docs/findings/2026-08-13-sqlite-事务归属冲突.md`）。
    第 2 章已有一条仓库级的 AST 扫描器守这条；这里再钉一次本模块的源码，
    让违规在本章的测试里就红，不必等跑到 test_liaison_effects.py。
    """
    import pathlib

    import tools.liaison.archive as archive_module

    source = pathlib.Path(archive_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback")
    ]
    assert offenders == [], f"archive.py 里出现了 commit/rollback，行号 {offenders}"


# ─────────────────────────────────────────────────────────────────────────
# 4.6 同人同天多条消息互不覆盖（生产 bug「归档覆盖」）
# ─────────────────────────────────────────────────────────────────────────


def test_three_same_named_attachments_on_the_same_day_all_survive(conn, root):
    """spec Scenario「同人同天多条消息」：3 份内容各自完整，没有一份被覆盖。"""
    payloads = {f"m{n}": bytes([n]) * 1024 for n in (1, 2, 3)}
    outcomes = {
        msgid: _archive(conn, root, msgid=msgid, filename="反馈表.xlsx", payload=payload)
        for msgid, payload in payloads.items()
    }

    for msgid, payload in payloads.items():
        stored = outcomes[msgid].attachments[0]
        assert (root / stored.relative_path).read_bytes() == payload

    assert len({o.attachments[0].relative_path for o in outcomes.values()}) == 3
    assert len(_rows(conn)) == 3
    assert_effect_log_identity(conn)


def test_same_name_different_content_can_both_be_retrieved(conn, root):
    """spec Scenario「同人同天同名文件内容不同」：两份都能分别取回。"""
    first = _archive(conn, root, msgid="m1", filename="表.xlsx", payload=b"AAAA")
    second = _archive(conn, root, msgid="m2", filename="表.xlsx", payload=b"BBBB")

    assert (root / first.attachments[0].relative_path).read_bytes() == b"AAAA"
    assert (root / second.attachments[0].relative_path).read_bytes() == b"BBBB"
    assert_effect_log_identity(conn)


def test_two_senders_do_not_share_a_directory(conn, root):
    a = _archive(conn, root, thread_id="tanglp", msgid="m1", filename="表.xlsx", payload=b"A")
    b = _archive(conn, root, thread_id="shaops", msgid="m2", filename="表.xlsx", payload=b"B")
    assert a.attachments[0].relative_path.split("/")[0] == "tanglp"
    assert b.attachments[0].relative_path.split("/")[0] == "shaops"
    assert_effect_log_identity(conn)


# ─────────────────────────────────────────────────────────────────────────
# 4.8 重复投递 / 崩溃后重跑
# ─────────────────────────────────────────────────────────────────────────


def test_delivering_the_same_message_twice_yields_one_archive_and_one_row(conn, root):
    """spec Scenario「同一消息被投递两次」。"""
    payload = b"once"
    first = _archive(conn, root, msgid="m1", filename="a.bin", payload=payload)
    second = _archive(conn, root, msgid="m1", filename="a.bin", payload=payload)

    assert first.newly_archived is True
    assert second.newly_archived is False, "第二次必须是幂等命中"
    assert len(_rows(conn)) == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1
    assert second.attachments[0] == first.attachments[0]
    assert_effect_log_identity(conn)


def test_replaying_a_whole_batch_adds_nothing(conn, root):
    """spec Scenario「重连后重投历史消息」：归档与队列均无新增，恒等仍成立。"""
    batch = [("tanglp", "m1"), ("tanglp", "m2"), ("wrkSHat_chat1", "m3")]
    for thread_id, msgid in batch:
        _archive(conn, root, thread_id=thread_id, msgid=msgid, filename="a.bin", payload=msgid.encode())

    files_before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    for thread_id, msgid in batch:
        outcome = _archive(conn, root, thread_id=thread_id, msgid=msgid, filename="a.bin", payload=msgid.encode())
        assert outcome.newly_archived is False

    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == files_before
    assert len(_rows(conn)) == 3
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_crash_between_landing_the_file_and_writing_the_ledger_converges(conn, root):
    """spec Scenario「处理中途被强制终止」。

    模拟：材料已落盘，台账行还没写，进程被 kill。重启后重新处理同一条消息 ⇒
    台账与材料一致，且**没有**第二份材料。
    """
    import tools.liaison.archive as archive_module

    def die(*args, **kwargs):
        raise KeyboardInterrupt("kill -9 的替身")

    monkeypatched = pytest.MonkeyPatch()
    monkeypatched.setattr(archive_module, "effect_archive_message", die)
    with pytest.raises(KeyboardInterrupt):
        _archive(conn, root, msgid="m1", filename="a.bin", payload=b"payload")
    monkeypatched.undo()

    # 中间态：材料在、台账没有——这是**允许**的方向
    landed = sorted(p for p in root.rglob("*") if p.is_file())
    assert len(landed) == 1
    assert _rows(conn) == []

    # 重跑：收敛
    outcome = _archive(conn, root, msgid="m1", filename="a.bin", payload=b"payload")
    assert outcome.newly_archived is True
    assert len(_rows(conn)) == 1
    assert sorted(p for p in root.rglob("*") if p.is_file()) == landed, "⛔ 不许落出第二份材料"
    assert_effect_log_identity(conn)
