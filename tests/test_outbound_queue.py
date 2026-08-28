"""
待审批队列。spec「被拦截草稿的持久化待审批队列」：
- 记录 MUST 含消息内容、类型、收件对象、拦截原因、入队时刻、当前状态
- 状态至少区分「待审批」「已放行」「已放弃」
- 同一草稿重复被拦截 MUST NOT 产生重复队列记录
- 已放行或已放弃的草稿 MUST NOT 再被当作待审批项返回
"""

import pytest

from app.outbound.messages import CandidateOutboundMessage
from app.outbound import queue
from app.storage.db import get_connection, init_schema

AI_BODY = "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "q.db"))
    init_schema(c)
    return c


def _msg(**over):
    payload = {
        "message_type": "rejection_letter",
        "severity": "high",
        "recipient": "cand-9@example.com",
        "body": AI_BODY,
    }
    payload.update(over)
    return CandidateOutboundMessage(**payload)


def test_enqueue_persists_everything_spec_requires(conn):
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    conn.commit()

    row = queue.get(conn, approval_id)
    assert row["thread_id"] == "job-7"
    assert row["message_type"] == "rejection_letter"
    assert row["recipient"] == "cand-9@example.com"
    assert row["blocked_reason"] == "等待人工确认"
    assert row["status"] == "pending"
    assert row["enqueued_at"]  # 入队时刻
    assert row["resolved_at"] is None
    # 消息内容整份可还原——放行时要拿它重走门禁
    assert row["payload"]["body"] == AI_BODY


def test_the_same_draft_blocked_twice_produces_one_row(conn):
    """spec「同一草稿重复被拦截」：队列中该草稿仍只有一条记录。"""
    first = queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    second = queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    conn.commit()

    assert first == second  # 同一条，id 确定性
    assert len(queue.list_pending(conn)) == 1


def test_same_content_in_different_threads_are_two_rows(conn):
    """
    U1 偏离登记 2 的下游判据：唯一索引是 (thread_id, content_hash) 两列。
    两个岗位给同一个候选人发同样的拒信是正常业务，⛔ 不能互相顶掉。
    """
    queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    queue.enqueue(conn, thread_id="job-8", message=_msg(), blocked_reason="等待人工确认")
    conn.commit()

    assert len(queue.list_pending(conn)) == 2


def test_resolved_drafts_leave_the_pending_list(conn):
    """spec「检索待审批项」：已放行与已放弃的草稿不在结果中。"""
    approved = queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    abandoned = queue.enqueue(
        conn, thread_id="job-8", message=_msg(), blocked_reason="等待人工确认"
    )
    still_pending = queue.enqueue(
        conn, thread_id="job-9", message=_msg(), blocked_reason="等待人工确认"
    )
    queue.mark_resolved(conn, approved, status="approved", confirmed_by="张三")
    queue.mark_resolved(conn, abandoned, status="abandoned", confirmed_by="李四")
    conn.commit()

    ids = [row["id"] for row in queue.list_pending(conn)]
    assert ids == [still_pending]


def test_resolving_records_who_and_when_and_does_not_delete(conn):
    """
    tasks 5.1 逐字：放行不 DELETE 而是改状态并记 confirmed_by 与 resolved_at。
    删掉就没有"谁在什么时候放的"这条审计事实了。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    queue.mark_resolved(conn, approval_id, status="approved", confirmed_by="张三")
    conn.commit()

    row = queue.get(conn, approval_id)
    assert row is not None  # 行还在
    assert row["status"] == "approved"
    assert row["confirmed_by"] == "张三"
    assert row["resolved_at"]


@pytest.mark.parametrize("bad_status", ["done", "sent", "PENDING", ""])
def test_an_unregistered_status_is_rejected_by_the_database(conn, bad_status):
    """
    U1 的 CHECK (status IN ('pending','approved','abandoned')) 是这条的强制点。
    ⛔ 应用层不再写第二份判定——两处判定就会出现"一处放行一处拒绝"的分叉。
    这里断言的是**数据库**拒绝，绕过应用层直接 UPDATE 同样被拒。
    """
    import sqlite3

    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE pending_approval SET status = ? WHERE id = ?", (bad_status, approval_id)
        )


def test_enqueue_does_not_commit(conn):
    """
    工程铁律 1：入队会被包进 effect_enqueue_pending_approval，写入必须与装饰器的
    effect_log 记录落在同一个事务里、由装饰器提交一次。自己 commit 会把两者拆开。
    """
    queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    conn.rollback()

    assert queue.list_pending(conn) == []


def test_content_hash_ignores_the_confirmation_signature(conn):
    """
    ⭐ 放行路径的地基。`approve()` 会带上 confirmed_by 重走门禁——如果
    content_hash 把 confirmed_by 算进去，那条草稿在幂等键与唯一索引眼里就变成了
    **另一条草稿**：既找不回队列里的原记录，重新被拦时还会插出第二行。

    ⛔ 这条不是"顺手多测一个"，它是 5.2 死锁防线成立的前提。
    """
    plain = _msg()
    signed = plain.with_confirmation("张三")

    assert signed.confirmed_by == "张三"
    assert signed.content_hash() == plain.content_hash()


def test_content_hash_changes_when_the_body_changes():
    """阴性对照：别把 content_hash 写成常量，那样上一条恒真而去重全错。"""
    assert _msg().content_hash() != _msg(body=AI_BODY + "（改了一个字）").content_hash()
