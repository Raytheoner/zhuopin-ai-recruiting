"""
待审批队列：被门禁拦下的候选人草稿的持久化落点。

⛔ **不复用 `outbox`**（design D5）：outbox 的语义是"已决定要投递的消息"，
本表的语义相反（"尚未获批、可能永远不发"）。合表就要求每个读 outbox 的地方
都加状态过滤，漏一处 = 未审批的拒信被发出去。

⛔ **本模块不自行 `commit`**：写入会被包进 `effect_enqueue_pending_approval`，
必须与装饰器追加的 `effect_log` 行落在同一个事务里（工程铁律 1）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any, Callable

from app.outbound.gate import compute_outbound_gate
from app.outbound.messages import CandidateOutboundMessage

if TYPE_CHECKING:
    from app.audit.recorder import AuditRecorder
    from app.outbound.gate import GateDecision

# 队列状态。⛔ 应用层**不**再写一份取值校验：U1 已把它做成数据库 CHECK
# （app/storage/db.py:159-160），两处判定就会出现"一处放行一处拒绝"的分叉。
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_ABANDONED = "abandoned"


def approval_id(thread_id: str, content_hash: str) -> str:
    """
    确定性 id，与 `(thread_id, content_hash)` 唯一索引同粒度（U1 偏离登记 2）。
    确定性让"同一草稿重复被拦截"天然收敛到同一行，而不是靠先查后插。
    """
    return f"{thread_id}:{content_hash}"


def enqueue(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    message: CandidateOutboundMessage,
    blocked_reason: str,
) -> str:
    """
    入队一条被拦下的草稿，返回 approval id。重复入队是 no-op（spec「同一草稿
    重复被拦截」），靠 `ON CONFLICT DO NOTHING` 而不是先查后插——先查后插在
    并发下有窗口，唯一索引没有。

    ⚠️ `confirmed_by` 一栏**入队时恒为 NULL**：带着签名的草稿走的是放行复发
    路径，那条路 ⛔ 不入队（design D5 的死锁防线，见 Task 2）。

    ⚠️ **冲突目标必须显式写成 `(thread_id, content_hash)`，⛔ 不要"简化"成无目标
    的 `ON CONFLICT DO NOTHING`。** 后者会把**任何**约束冲突都吞掉——包括 U1 那条
    `status IN (...)` 的 CHECK——于是一条畸形记录会被静默丢弃而调用方以为入队了。
    2026-08-28 实测：`id` 是确定性主键，重复入队时主键与唯一索引**同时**命中，
    SQLite 按显式目标 DO NOTHING、主键冲突不会漏出来，行数稳定为 1。
    """
    content_hash = message.content_hash()
    row_id = approval_id(thread_id, content_hash)
    conn.execute(
        "INSERT INTO pending_approval "
        "(id, thread_id, message_type, recipient, payload_json, blocked_reason, "
        " content_hash, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(thread_id, content_hash) DO NOTHING",
        (
            row_id,
            thread_id,
            message.message_type,
            message.recipient,
            json.dumps(
                {
                    "message_type": message.message_type,
                    "recipient": message.recipient,
                    "body": message.body,
                    "severity": message.severity,
                    "requires_confirmation": message.requires_confirmation,
                    "payload": message.payload,
                },
                ensure_ascii=False,
            ),
            blocked_reason,
            content_hash,
            STATUS_PENDING,
        ),
    )
    return row_id


def _row_to_dict(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """⚠️ 刻意不设 `conn.row_factory`：conn 是全应用共享的一条连接
    （`app/storage/db.py:get_connection`），换掉它会让所有按下标取值的既有代码
    静默改变行为（与 `app/audit/sinks.py:_rows_as_dicts` 同一理由）。"""
    columns = [d[0] for d in cursor.description]
    rows = []
    for raw in cursor.fetchall():
        row = dict(zip(columns, raw))
        row["payload"] = json.loads(row.pop("payload_json"))
        rows.append(row)
    return rows


def get(conn: sqlite3.Connection, approval_id_: str) -> dict[str, Any] | None:
    rows = _row_to_dict(
        conn.execute("SELECT * FROM pending_approval WHERE id = ?", (approval_id_,))
    )
    return rows[0] if rows else None


def list_pending(
    conn: sqlite3.Connection, *, message_type: str | None = None
) -> list[dict[str, Any]]:
    """spec「检索待审批项」：只返回 pending，已放行与已放弃不在结果中。"""
    sql = "SELECT * FROM pending_approval WHERE status = ?"
    params: list[Any] = [STATUS_PENDING]
    if message_type is not None:
        sql += " AND message_type = ?"
        params.append(message_type)
    return _row_to_dict(conn.execute(sql + " ORDER BY enqueued_at, id", params))


def mark_resolved(
    conn: sqlite3.Connection,
    approval_id_: str,
    *,
    status: str,
    confirmed_by: str | None,
) -> bool:
    """
    改状态并记下是谁、什么时候。⛔ **不 DELETE**（tasks 5.1）——删掉就没有
    "谁在什么时候放的"这条审计事实了，而这正是本变更包要建的东西。

    ⚠️ `confirmed_by` 现阶段**不可信**：鉴权是空壳（`AuthContext.user_id` 恒为
    `None`），值只能由调用方传入（design D7）。SSO 落地后同一字段变可信，
    结构不改。
    """
    cursor = conn.execute(
        "UPDATE pending_approval SET status = ?, confirmed_by = ?, "
        "resolved_at = datetime('now') WHERE id = ? AND status = ?",
        (status, confirmed_by, approval_id_, STATUS_PENDING),
    )
    return cursor.rowcount == 1


def to_message(row: dict[str, Any]) -> CandidateOutboundMessage:
    """把队列行还原成可重走门禁的消息。放行路径（Task 2）用它。"""
    payload = row["payload"]
    return CandidateOutboundMessage(
        message_type=payload["message_type"],
        recipient=payload["recipient"],
        body=payload["body"],
        severity=payload["severity"],
        requires_confirmation=payload["requires_confirmation"],
        payload=payload.get("payload", {}),
    )


class ApprovalNotPending(LookupError):
    """要放行的 approval 不存在，或已经不是 pending。"""


def approve(
    conn: sqlite3.Connection,
    approval_id_: str,
    *,
    confirmed_by: str,
    outbound_enabled: Callable[[], bool],
    deliver: Callable[[CandidateOutboundMessage], None],
    recorder: "AuditRecorder",
) -> "GateDecision":
    """
    人工放行：把草稿取回来、带上确认人标识**重新走门禁**，两道闸都过才投递。

    ⛔ **放行复发被拦时不重复入队**（design D5 的死锁防线，平台侧踩过）：
    它已经在队列里，重入会撞自己的唯一索引，把"暂时发不出去"变成 IntegrityError。
    判据是「是否携带 `confirmed_by`」——本函数走的永远是携带的那一支，所以这里
    **一行入队代码都没有**，这就是防线本身。⛔ 不要"顺手补一个 upsert 保险"。

    被拦时状态保持 `pending`：总开关开启后可以再次放行（spec 逐字）。

    ⛔ 本函数自身不 `commit`。⚠️ 但被拦分支调用的 `record_outbound_decision()`
    内部会走 `effect_record_outbound_audit`，而 `idempotent_effect` 装饰器在函数体
    成功后 `conn.commit()`（`app/storage/idempotency.py:75`）——这与
    `deliver_candidate_message` 的既有行为同构，不是新增的事务管理者。
    这条路径上此前只做过读（`get()` / `compute_outbound_gate()`），`mark_resolved`
    的 CAS 在早返回之后根本没执行，没有半截事务会被这次 commit 意外提交。

    ⚠️ **先 `mark_resolved`（CAS），确认拿到行的所有权后才 `deliver`**（review
    发现 1）：`mark_resolved` 的 `UPDATE ... WHERE status = 'pending'` 本身就是
    一次比较后交换——两个并发的 `approve()` 都能读到 `pending`、都能过门禁，
    但只有一个的 UPDATE 真正命中一行。若顺序是先 `deliver` 后 `mark_resolved`，
    两边都会各投递一次，DB 里却只留下一条 `approved`——重复来信查无痕迹。
    把顺序换过来后，抢输的一方直接判定为"这条已经被别人处置了"（复用
    `ApprovalNotPending`，与"未知/已处置 id"是同一件事只是晚一步被观察到），
    ⛔ 不投递。**代价是方向性的**：如果进程在 UPDATE 与 `deliver` 之间崩溃，
    这一行会停在 `approved` 但信没发出去——这比"发两次却查不出"更安全，
    因为一封没发出的信人工可以补，一封发重的信收不回来。⛔ 不要把这个顺序
    "优化"回去。
    """
    row = get(conn, approval_id_)
    if row is None or row["status"] != STATUS_PENDING:
        raise ApprovalNotPending(
            f"approval {approval_id_!r} 不存在或已不是 pending"
            f"（当前 {None if row is None else row['status']!r}）"
        )

    signed = to_message(row).with_confirmation(confirmed_by)
    decision = compute_outbound_gate(signed, outbound_enabled)
    if not decision.allowed:
        # TD-9：这里以前是光秃秃的 `return decision`，于是「人工点了放行、却被
        # 总开关拦下」这一次尝试**一条痕都不产生**——审计分辨不出"从未尝试放行"
        # 与"尝试过但被拦下"，而 6.5 的拦截统计会让"一直发不出去的那批信"
        # 系统性缺席。
        #
        # ⚠️ **函数体内延迟 import，⛔ 不许提到模块顶部**（design.md 决策 2）：
        # `app/graph/nodes.py` 顶部已有 `from app.outbound import queue`，方向不能
        # 反过来；模块顶部再 import delivery 会构成 queue → delivery → nodes → queue
        # 的模块级循环导入。函数体内的 import 在两个模块都完成初始化之后才执行。
        from app.outbound.delivery import record_outbound_decision

        record_outbound_decision(
            conn,
            thread_id=row["thread_id"],
            message=signed,
            decision=decision,
            recorder=recorder,
        )
        return decision

    claimed = mark_resolved(conn, approval_id_, status=STATUS_APPROVED, confirmed_by=confirmed_by)
    if not claimed:
        # 抢输了这次 CAS：另一个并发的 approve() 已经先一步把这一行结清。
        # 不投递——deliver() 只属于赢得所有权的那一方。
        raise ApprovalNotPending(
            f"approval {approval_id_!r} 在放行过程中被并发处置，已失去 pending 所有权"
        )

    deliver(signed)
    return decision
