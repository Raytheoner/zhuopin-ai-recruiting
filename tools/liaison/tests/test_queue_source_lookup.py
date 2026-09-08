"""5.5 / spec「从队列条目找回材料」：条目 → 来源消息 → 全部附件。"""

import json

import pytest

from tools.liaison.archive import InboundAttachment, archive_message
from tools.liaison.attachments import StoredAttachment
from tools.liaison.queue import enqueue_task, load_task_source
from tools.liaison.storage import db as liaison_db

RECEIVED_AT = "2026-09-09T10:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _inbound(conn, archive_root, *, msgid, content, payload=None, filename=None):
    attachment = (
        InboundAttachment(filename=filename, payload=payload) if payload is not None else None
    )
    archive_message(
        conn,
        thread_id="u_zhang",
        msgid=msgid,
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        msgtype="file" if payload is not None else "text",
        content=content,
        attachment=attachment,
        archive_root=archive_root,
    )
    enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid=msgid,
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary=content,
    )
    return conn.execute("SELECT id FROM liaison_task WHERE msgid = ?", (msgid,)).fetchone()[0]


def test_load_task_source_returns_the_message_and_its_attachments(conn, tmp_path):
    archive_root = tmp_path / "archive"
    payload = b"\x50\x4b\x03\x04binary-not-utf8\xff\xfe"
    task_id = _inbound(
        conn, archive_root, msgid="msg-1", content="报价单", payload=payload, filename="报价.xlsx"
    )

    source = load_task_source(conn, task_id=task_id)

    assert source is not None
    assert source.task_id == task_id
    assert source.msgid == "msg-1"
    assert source.thread_id == "u_zhang"
    assert source.sender_userid == "u_zhang"
    assert source.received_at == RECEIVED_AT
    assert source.send_status == "pending"
    assert source.pushed_at is None
    assert source.msgtype == "file"
    assert source.content == "报价单"
    assert len(source.attachments) == 1
    assert isinstance(source.attachments[0], StoredAttachment)
    # `StoredAttachment.filename` 存的是落盘后的**归一化文件名**（含 `msgid__`
    # 前缀），不是通道递进来的原始文件名——这是第 4 章 `store_attachment` 早已
    # 确立并有测试覆盖的语义（见 test_archive_effect.py 对应断言），本 Task
    # 只读取不改写，⛔ 不在这里重新定义它的含义。
    assert source.attachments[0].filename == "msg-1__报价.xlsx"
    assert source.attachments[0].byte_length == len(payload)


def test_the_attachment_can_actually_be_read_back_byte_for_byte(conn, tmp_path):
    """spec：据此 SHALL 能取回该条目对应的原始消息与附件。

    "能定位到"不等于"真的取得回来"——所以这条把字节读出来比一遍。
    ⛔ 全程 rb，不做任何 UTF-8 解码（design D4 的「二进制判误」生产 bug）。
    """
    archive_root = tmp_path / "archive"
    payload = b"\x89PNG\r\n\x1a\n\xff\xd8not-text"
    task_id = _inbound(
        conn, archive_root, msgid="msg-1", content="图纸", payload=payload, filename="图纸.png"
    )

    source = load_task_source(conn, task_id=task_id)
    paths = source.attachment_paths(archive_root=archive_root)

    assert len(paths) == 1
    assert paths[0].exists()
    assert paths[0].read_bytes() == payload


def test_a_message_without_attachments_yields_an_empty_tuple(conn, tmp_path):
    """⛔ 空附件返回空元组，不是 None——调用方少一个分支。"""
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content="纯文本")
    source = load_task_source(conn, task_id=task_id)
    assert source.attachments == ()
    assert source.attachment_paths(archive_root=tmp_path / "archive") == ()


def test_load_task_source_returns_none_for_an_unknown_task(conn):
    assert load_task_source(conn, task_id=999) is None


def test_content_with_pipes_and_newlines_comes_back_verbatim(conn, tmp_path):
    """5.6 的存储侧：任意内容原样存、原样取回，⛔ 不许被任何"归一化"改掉。"""
    raw = "第一行|第二列\n第二行\t制表\x00空字节"
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content=raw)
    assert load_task_source(conn, task_id=task_id).content == raw


def test_a_corrupt_attachments_json_does_not_crash_the_lookup(conn, tmp_path):
    """台账里的 JSON 坏了，回指要退化成"消息取得到、附件取不到"，⛔ 不是整个查询炸掉。

    材料的完整性核对是 `consistency.verify_ledger_against_archive` 的职责；
    本函数只负责取回，遇到坏 JSON 返回空附件即可。
    """
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content="纯文本")
    conn.execute(
        "UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?", ("{坏的", "msg-1")
    )
    source = load_task_source(conn, task_id=task_id)
    assert source is not None
    assert source.content == "纯文本"
    assert source.attachments == ()


def test_all_attachments_come_back_not_just_the_first(conn, tmp_path):
    """一条消息带多份附件时，全部都要能取回，⛔ 不许只回第一个。

    aibot 协议每条消息目前只投一个媒体项（`archive_message` docstring 明令
    「一条消息最多一个附件」），所以这里直接摆一份两元素的 `attachments_json`
    ——这是在测 `load_task_source` 对台账数组的解析是不是"取全部"，不是在
    测协议能不能一次带两份附件（那不是本 Task、也不是 `archive.py` 的职责）。
    """
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content="两份材料")
    entries = [
        {
            "filename": "msg-1__报价.xlsx",
            "relative_path": "u_zhang/20260909/msg-1__报价.xlsx",
            "byte_length": 10,
            "sha256": "a" * 64,
        },
        {
            "filename": "msg-1__附图.png",
            "relative_path": "u_zhang/20260909/msg-1__附图.png",
            "byte_length": 20,
            "sha256": "b" * 64,
        },
    ]
    conn.execute(
        "UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?",
        (json.dumps(entries, ensure_ascii=False), "msg-1"),
    )

    source = load_task_source(conn, task_id=task_id)

    assert len(source.attachments) == 2
    assert {item.filename for item in source.attachments} == {"msg-1__报价.xlsx", "msg-1__附图.png"}
    paths = source.attachment_paths(archive_root=tmp_path / "archive")
    assert len(paths) == 2
    assert paths[0] != paths[1]


def test_same_filename_different_content_are_kept_as_distinct_attachments(conn, tmp_path):
    """同名不同内容的附件各自独立回指，⛔ 不因文件名相同被去重或互相覆盖。"""
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content="重名材料")
    entries = [
        {
            "filename": "报价.xlsx",
            "relative_path": "u_zhang/20260909/msg-1__报价.xlsx",
            "byte_length": 10,
            "sha256": "a" * 64,
        },
        {
            "filename": "报价.xlsx",
            "relative_path": "u_zhang/20260910/msg-2__报价.xlsx",
            "byte_length": 20,
            "sha256": "c" * 64,
        },
    ]
    conn.execute(
        "UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?",
        (json.dumps(entries, ensure_ascii=False), "msg-1"),
    )

    source = load_task_source(conn, task_id=task_id)

    assert len(source.attachments) == 2
    assert source.attachments[0].filename == source.attachments[1].filename == "报价.xlsx"
    assert source.attachments[0].sha256 != source.attachments[1].sha256
    paths = source.attachment_paths(archive_root=tmp_path / "archive")
    assert len(paths) == 2
    assert paths[0] != paths[1]
