"""spec「台账中存在的消息其材料必然可取回」的可反复执行版本。

这个核对器只回答一个方向的问题：**台账说有的，磁盘上是不是真的有且没变。**
⛔ 反方向（磁盘上有、台账没有）不是不一致——那正是 design D3 允许的中间态。
"""

from __future__ import annotations

import json

import pytest

from tools.liaison.archive import InboundAttachment, archive_message
from tools.liaison.consistency import (
    LedgerInconsistency,
    verify_ledger_against_archive,
)
from tools.liaison.storage import db as liaison_db

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


def _archive(conn, root, msgid, payload=None, filename="表.xlsx"):
    return archive_message(
        conn,
        thread_id="tanglp",
        msgid=msgid,
        sender_userid="tanglp",
        received_at=RECEIVED_AT,
        msgtype="file" if payload is not None else "text",
        content="",
        attachment=InboundAttachment(filename=filename, payload=payload) if payload is not None else None,
        archive_root=root,
    )


def test_a_healthy_archive_reports_no_inconsistency(conn, root):
    """spec Scenario「台账与材料的一致性可核对」的正路。"""
    for n in range(5):
        _archive(conn, root, f"m{n}", payload=f"payload-{n}".encode())

    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_empty_ledger_is_consistent(conn, root):
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_text_only_messages_are_skipped(conn, root):
    """没有附件的消息不参与核对——`attachments_json` 是 `[]`，没有材料可查。"""
    _archive(conn, root, "m1")
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_a_missing_file_behind_the_ledger_is_reported(conn, root):
    """"台账已记但材料缺失"——这正是 spec 禁止出现的那种状态，核对器必须看得见。"""
    outcome = _archive(conn, root, "m1", payload=b"payload")
    (root / outcome.attachments[0].relative_path).unlink()

    problems = verify_ledger_against_archive(conn, archive_root=root)

    assert len(problems) == 1
    assert isinstance(problems[0], LedgerInconsistency)
    assert problems[0].msgid == "m1"
    assert problems[0].relative_path == outcome.attachments[0].relative_path


def test_a_corrupted_file_is_reported(conn, root):
    """长度相同但内容变了 ⇒ SHA-256 抓得到。"""
    outcome = _archive(conn, root, "m1", payload=b"AAAA")
    (root / outcome.attachments[0].relative_path).write_bytes(b"BBBB")

    problems = verify_ledger_against_archive(conn, archive_root=root)
    assert [p.msgid for p in problems] == ["m1"]


def test_a_truncated_file_is_reported(conn, root):
    outcome = _archive(conn, root, "m1", payload=b"AAAABBBB")
    (root / outcome.attachments[0].relative_path).write_bytes(b"AAAA")

    assert [p.msgid for p in verify_ledger_against_archive(conn, archive_root=root)] == ["m1"]


def test_extra_files_on_disk_are_not_inconsistencies(conn, root):
    """磁盘上有、台账没有 ⇒ design D3 明确允许的中间态，⛔ 不许报成不一致。

    报了会让崩溃恢复期的每一次核对都刷出一堆假警报，真问题就淹了。
    """
    _archive(conn, root, "m1", payload=b"payload")
    stray = root / "tanglp" / "20260909" / "m9__孤儿.bin"
    stray.write_bytes(b"orphan")

    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_malformed_attachments_json_is_reported_not_raised(conn, root):
    """台账里存了坏 JSON ⇒ 报一条不一致，⛔ 不抛——核对器要能跑完整张表。"""
    _archive(conn, root, "m1", payload=b"payload")
    conn.execute("UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?", ("{坏", "m1"))
    conn.commit()

    problems = verify_ledger_against_archive(conn, archive_root=root)
    assert len(problems) == 1
    assert "attachments_json" in problems[0].problem


def test_all_rows_are_visited_even_after_the_first_failure(conn, root):
    """遇到第一条坏的不能停——spec 要求的是"遍历每条记录"。"""
    outcomes = {m: _archive(conn, root, m, payload=m.encode()) for m in ("m1", "m2", "m3")}
    for msgid in ("m1", "m3"):
        (root / outcomes[msgid].attachments[0].relative_path).unlink()

    assert sorted(p.msgid for p in verify_ledger_against_archive(conn, archive_root=root)) == ["m1", "m3"]


def test_checker_reads_bytes_never_text(conn, root):
    """核对走的是 `verify_archived_file`（`rb` + SHA-256），二进制样本必须过。"""
    payload = bytes(range(256)) * 8
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")

    _archive(conn, root, "m1", payload=payload)
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_ledger_records_enough_to_re_verify_without_the_original(conn, root):
    """台账里存的元数据必须自足：路径 + 字节长度 + SHA-256，三样齐了才核对得了。"""
    _archive(conn, root, "m1", payload=b"payload")
    recorded = json.loads(
        conn.execute("SELECT attachments_json FROM liaison_message WHERE msgid = 'm1'").fetchone()[0]
    )
    assert set(recorded[0]) == {"filename", "relative_path", "byte_length", "sha256"}
