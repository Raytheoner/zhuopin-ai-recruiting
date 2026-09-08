"""5.1 / 5.2：入队接线与「缺来源即拒绝」。

⛔ 本文件不测渲染、不测状态流转——那是 test_queue_view.py 与 test_queue_status.py。
"""

import sqlite3

import pytest

from tools.liaison.queue import (
    MissingSourceMessage,
    compute_task_summary,
    enqueue_task,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _archive(conn, *, thread_id, msgid, content="材料收到"):
    """造一条父台账行。队列条目的外键指向它，⛔ 没有它入队必被拒。"""
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        msgtype="text",
        content=content,
    )


def test_enqueue_writes_one_row_carrying_the_source_handles(conn):
    """spec：每条队列条目 SHALL 携带发送人标识、来源会话标识、来源消息唯一标识、接收时间。"""
    _archive(conn, thread_id="u_zhang", msgid="msg-1")

    assert enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary="报价单已发",
    ) is True

    rows = conn.execute(
        "SELECT msgid, thread_id, sender_userid, received_at, summary, send_status, pushed_at "
        "FROM liaison_task"
    ).fetchall()
    assert rows == [("msg-1", "u_zhang", "u_zhang", RECEIVED_AT, "报价单已发", "pending", None)]
    assert_effect_log_identity(conn)


def test_enqueue_is_idempotent_on_redelivery(conn):
    """SDK 重连后重投同一 msgid：⛔ 不许产生第二条待办。"""
    _archive(conn, thread_id="u_zhang", msgid="msg-1")
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is True
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is False

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert_effect_log_identity(conn)


def test_enqueue_rejects_when_the_source_message_does_not_exist(conn):
    """5.2 / spec「条目缺少来源信息不允许写入」：外键是这条要求的真身。"""
    with pytest.raises(MissingSourceMessage):
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid="msg-does-not-exist",
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    # 铁律 1 的方向：业务写失败 ⇒ 幂等记录也必须不存在，否则重投时会被判"已执行"而永不重试。
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_enqueue_task'"
    ).fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_enqueue_rejects_a_null_msgid(conn):
    """`msgid TEXT NOT NULL`：连来源标识都没有的条目，⛔ 一行都不许落。"""
    with pytest.raises(MissingSourceMessage):
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid=None,
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_same_msgid_from_another_thread_is_treated_as_an_idempotent_hit(conn):
    """`msgid TEXT NOT NULL UNIQUE`：一条消息最多一条队列条目。

    幂等装饰器按 `{thread_id}:...:{msgid}` 去重，thread_id 换了就命不中；
    但 UNIQUE 会拦住。这是 schema 注释写的"两道防线"里的第二道——
    ⛔ 它必须表现为幂等命中（返回 False），不是抛给调用方的崩溃。
    """
    _archive(conn, thread_id="u_zhang", msgid="msg-1")
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is True

    assert enqueue_task(
        conn, thread_id="chat_group", msgid="msg-1", sender_userid="u_li", received_at=RECEIVED_AT
    ) is False

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert_effect_log_identity(conn)


def test_summary_is_a_mechanical_truncation_never_a_generated_one():
    """⛔ 摘要只能是机械截断。本服务不调用任何 LLM（design D7 / 合规红线）。"""
    assert compute_task_summary("报价单已发", msgtype="text") == "报价单已发"

    long_text = "甲" * 500
    truncated = compute_task_summary(long_text, msgtype="text", max_chars=120)
    assert len(truncated) == 120
    assert truncated.endswith("…")
    assert truncated[:119] == "甲" * 119


def test_summary_keeps_the_raw_characters_including_pipes_and_newlines():
    """⛔ 入队前不许做任何"竖线归一化"。转义只发生在渲染层（判断 1）。"""
    raw = "第一行|第二列\n第二行"
    assert compute_task_summary(raw, msgtype="text") == raw


def test_summary_for_a_message_without_text_names_the_type():
    """图片/文件消息没有正文。摘要写成`[file]`比空字符串可用——但仍然不是生成的内容。"""
    assert compute_task_summary("", msgtype="file") == "[file]"
    assert compute_task_summary("   ", msgtype="image") == "[image]"


def test_queue_module_never_commits_by_itself():
    """提交由 idempotent_effect 独占（第 2 章的单一事务管理者约束）。"""
    import ast
    import pathlib

    from tools.liaison import queue as queue_module

    tree = ast.parse(pathlib.Path(queue_module.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback", "executescript")
    ]
    assert offenders == [], f"queue.py 里出现了事务边界调用，行号 {offenders}"
