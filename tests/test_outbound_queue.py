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


def test_list_pending_message_type_filter_returns_only_the_matching_type(conn):
    """
    `queue.list_pending(conn, *, message_type=...)` 的过滤分支此前零覆盖。
    两种登记消息类型各入队一条，按类型过滤应只拿到对应那条；不传
    message_type 时两条都要在（阴性对照，防止过滤条件被写反成"总是过滤"）。
    """
    rejection_id = queue.enqueue(
        conn,
        thread_id="job-7",
        message=_msg(message_type="rejection_letter"),
        blocked_reason="等待人工确认",
    )
    invitation_id = queue.enqueue(
        conn,
        thread_id="job-7",
        message=_msg(message_type="interview_invitation", body=AI_BODY + "（面试邀约）"),
        blocked_reason="等待人工确认",
    )
    conn.commit()

    rejection_only = queue.list_pending(conn, message_type="rejection_letter")
    assert [row["id"] for row in rejection_only] == [rejection_id]

    invitation_only = queue.list_pending(conn, message_type="interview_invitation")
    assert [row["id"] for row in invitation_only] == [invitation_id]

    unfiltered_ids = {row["id"] for row in queue.list_pending(conn)}
    assert unfiltered_ids == {rejection_id, invitation_id}


# ── 放行与死锁防线（tasks 5.2 / design D5）──────────────────────────────


def _approve(conn, approval_id, *, switch, delivered, confirmed_by="张三"):
    from app.outbound import queue as q

    return q.approve(
        conn,
        approval_id,
        confirmed_by=confirmed_by,
        outbound_enabled=lambda: switch,
        deliver=delivered.append,
    )


def test_approving_with_the_switch_on_delivers_and_marks_approved(conn):
    """spec「人工放行」：草稿携带确认人标识重新走门禁，两道闸都通过时被外发。"""
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    delivered = []

    decision = _approve(conn, approval_id, switch=True, delivered=delivered)
    conn.commit()

    assert decision.allowed is True
    assert [m.recipient for m in delivered] == ["cand-9@example.com"]
    assert delivered[0].confirmed_by == "张三"  # 投递出去的是带签名的那份
    row = queue.get(conn, approval_id)
    assert row["status"] == "approved"
    assert row["confirmed_by"] == "张三"


def test_the_row_is_already_approved_at_the_moment_deliver_runs(conn):
    """
    ⭐ 顺序断言（review 发现 1）。`mark_resolved` 的 `UPDATE ... WHERE
    status = 'pending'` 本身就是一次比较后交换（CAS）——必须先拿到行的所有权
    （UPDATE 成功）才能 `deliver`，不能反过来。用一个会在被调用的当下回读
    数据库状态的 `deliver` 探针来钉住顺序：如果实现被"优化"回"先 deliver
    后 mark_resolved"，这里看到的会是 "pending" 而不是 "approved"，测试变红。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    statuses_seen_by_deliver = []

    def spy_deliver(message):
        statuses_seen_by_deliver.append(queue.get(conn, approval_id)["status"])

    queue.approve(
        conn,
        approval_id,
        confirmed_by="张三",
        outbound_enabled=lambda: True,
        deliver=spy_deliver,
    )
    conn.commit()

    assert statuses_seen_by_deliver == ["approved"]


def test_losing_the_resolve_race_does_not_deliver_a_second_time(conn, monkeypatch):
    """
    ⭐ 并发丢失竞态（review 发现 1，Important）。两个 `approve()` 几乎同时
    读到同一行 pending、都过了门禁，但 `mark_resolved` 的 CAS 只有一个能真正
    命中——抢输的那一方 ⛔ 不能 `deliver`，否则候选人会收到两封信而 DB 里
    干干净净只有一条 `approved`，查无重复的痕迹。

    单线程测试里没法真的并发，所以用 monkeypatch 固定 `approve()` 内部读到的
    `get()` 快照仍是 pending，而数据库里这一行**已经**被真实地结清（模拟另一个
    并发调用抢先完成）——`mark_resolved` 的 UPDATE 因此在真实表上找不到匹配行，
    返回 False，触发的正是抢输分支。
    """
    from app.outbound import queue as q

    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    stale_snapshot = queue.get(conn, approval_id)
    # 真实地让这一行被"另一个并发的放行"抢先结清
    assert queue.mark_resolved(
        conn, approval_id, status=queue.STATUS_APPROVED, confirmed_by="李四"
    )
    monkeypatch.setattr(q, "get", lambda c, a: stale_snapshot)

    delivered = []
    with pytest.raises(queue.ApprovalNotPending):
        _approve(conn, approval_id, switch=True, delivered=delivered)

    assert delivered == []


def test_top_severity_is_cleared_by_the_signature_not_terminal(conn):
    """
    ⭐ D-6 取 (b) 的下游判据。默认草稿是 severity=high + requires_confirmation=True
    （spec：候选人信件一律高风险），上一条能放行出去，就证明这两条确实是**由人
    清关**而不是终局拦截。若 U4 的门禁被改回 (a)，上一条会红，本条给出可读的理由。
    """
    assert _msg().severity == "high"
    assert _msg().requires_confirmation is True


def test_approving_with_the_switch_off_does_not_deliver_and_does_not_requeue(conn):
    """
    ⭐ 死锁防线（design D5，平台侧踩过）。spec「放行时总开关关闭」：
    消息仍不外发、状态保持 pending、可在开关开启后再次放行。

    ⚠️ `len(list_pending) == 1` 这条**不能**单独当成"没有重复入队"的证明——
    review 发现 2：`enqueue()` 是 `ON CONFLICT(thread_id, content_hash) DO
    NOTHING`，同一内容重入队是静默 no-op，行数照样是 1。真正证明"approve
    的放行复发路径里根本没有调用 enqueue"的是
    `test_the_approve_path_contains_no_enqueue_call`（AST 结构守护）与
    `test_the_switch_off_path_never_calls_enqueue`（行为级 spy，堵住 AST
    扫描认不出的间接调用）两条测试。这里的行数与状态断言只负责它们各自
    字面能证明的事——"没多一行"、"状态没被改成别的"——不越界代言。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    delivered = []

    decision = _approve(conn, approval_id, switch=False, delivered=delivered)
    conn.commit()

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert delivered == []
    assert len(queue.list_pending(conn)) == 1  # 队列行数没变
    assert queue.get(conn, approval_id)["status"] == "pending"  # 状态没动


def test_the_switch_off_path_never_calls_enqueue(conn, monkeypatch):
    """
    ⭐ 补齐 AST 结构守护的盲区（review 发现 2）。
    `test_the_approve_path_contains_no_enqueue_call` 只扫描 `approve` 函数体里
    **字面写成** `enqueue(...)` 的调用节点——经一层小助手、一个别名 import，
    或 `getattr(queue_module, "enqueue")(...)` 转一手，就能绕过纯文本 AST 匹配、
    同时原样复活死锁。

    这里换成行为级 spy：把 `app.outbound.queue.enqueue` 本体替换掉，不管
    `approve()` 内部用什么姿势去调用它——只要真的调用了模块里那个 `enqueue`
    对象，这里就会看见。AST 测试与这条测试合起来才是"没有重复入队"的完整证明，
    单独一条都不够（同上一条测试的说明）。
    """
    from app.outbound import queue as q

    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    enqueue_calls = []
    monkeypatch.setattr(q, "enqueue", lambda *a, **k: enqueue_calls.append((a, k)))

    delivered = []
    decision = _approve(conn, approval_id, switch=False, delivered=delivered)
    conn.commit()

    assert decision.allowed is False
    assert enqueue_calls == []


def test_a_draft_blocked_by_the_switch_can_be_approved_again_later(conn):
    """spec 同一 Scenario 的后半句：可在总开关开启后再次放行。"""
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    delivered = []

    _approve(conn, approval_id, switch=False, delivered=delivered)
    decision = _approve(conn, approval_id, switch=True, delivered=delivered)
    conn.commit()

    assert decision.allowed is True
    assert len(delivered) == 1
    assert queue.get(conn, approval_id)["status"] == "approved"


def test_a_malformed_draft_is_not_delivered_even_with_a_signature(conn):
    """
    spec「确认人不能放行一条畸形消息」：风险等级读不出但带了确认人标识 →
    仍拦截，原因是「风险等级未知」而非「等待人工确认」。

    ⭐ 这条是 D-6 口径 B 的**边界**：签名清关"已知的高风险"，⛔ 清不了"畸形"。
    没有它，(b) 就退化成"签个字什么都能发"。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(severity="不认识的等级"),
        blocked_reason="风险等级缺失或未登记",
    )
    delivered = []

    decision = _approve(conn, approval_id, switch=True, delivered=delivered)
    conn.commit()

    assert decision.allowed is False
    assert decision.reason == "风险等级缺失或未登记"
    assert delivered == []
    assert queue.get(conn, approval_id)["status"] == "pending"


def test_approving_an_unknown_or_already_resolved_id_raises(conn):
    """
    ⛔ 不静默返回。放行一条不存在或已处置的草稿是调用方的错，静默吞掉会让
    "我明明点了放行"和"它真的发出去了"这两件事再也对不上。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    _approve(conn, approval_id, switch=True, delivered=[])
    conn.commit()

    with pytest.raises(queue.ApprovalNotPending):
        _approve(conn, approval_id, switch=True, delivered=[])  # 已经 approved
    with pytest.raises(queue.ApprovalNotPending):
        _approve(conn, "job-7:不存在", switch=True, delivered=[])


def test_the_approve_path_contains_no_enqueue_call():
    """
    ⭐ 死锁防线的机械判据。上面那条行为测试只能证明"当前实现没有重复入队"；
    这条证明"approve 的函数体里**根本没有**入队这个动作"，将来有人为了
    "保险"补一个 upsert 会立刻变红。

    带阳性对照——0 命中同时兼容"约束守住了"和"检查根本没跑"两种解释。
    """
    import ast
    from pathlib import Path

    def enqueue_calls_in(source: str, func_name: str) -> list[str]:
        hits = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != func_name:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
                    if name in {"enqueue", "INSERT"}:
                        hits.append(name)
        return hits

    source = (Path(__file__).resolve().parents[1] / "app" / "outbound" / "queue.py").read_text(
        encoding="utf-8"
    )
    assert enqueue_calls_in(source, "approve") == []
    # 阳性对照
    offending = "def approve(conn, i):\n    enqueue(conn, thread_id='t', message=m, blocked_reason='r')\n"
    assert enqueue_calls_in(offending, "approve") == ["enqueue"]
