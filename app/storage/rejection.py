"""拒绝记录写入的唯一路径（hard-requirement-screening spec「淘汰只由人
确认并可申诉」，合规红线「AI 只做排序推荐，不做自动淘汰」，design D6）。

⛔ 本模块外的任何代码不得直接 INSERT rejection_record——这是应用层的
唯一路径；数据库 CHECK 约束（rejection_record.reason_type）是第二道防线，
两道防线都要有测试覆盖。
"""
from __future__ import annotations

import logging
import sqlite3
import uuid

logger = logging.getLogger(__name__)

_VALID_REASON_TYPES = frozenset({"hard_rule", "human_decision"})


class InvalidRejectionReason(ValueError):
    """理由类型不合法，或 hard_rule 缺 rule_ref，或决策人为空。⛔ 不静默
    降级、不猜默认值——这条异常必须让调用方（未来的 U5 单条/批量确认
    接口）整体失败。"""


def write_rejection(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    reason_type: str,
    decided_by: str,
    rule_ref: str | None = None,
    human_readable: str | None = None,
    batch_id: str | None = None,
) -> str:
    """写一条拒绝记录并把投递流转到"已淘汰"阶段（同一事务）。返回
    rejection_record.id。

    ⛔ reason_type 不接受 'ai_score'——合规红线的应用层第一道防线。
    """
    if reason_type not in _VALID_REASON_TYPES:
        raise InvalidRejectionReason(
            f"reason_type={reason_type!r} 不合法，只能是 hard_rule 或 human_decision"
            "（合规红线：AI 只做排序推荐，不做自动淘汰）"
        )
    if reason_type == "hard_rule" and not rule_ref:
        raise InvalidRejectionReason("reason_type='hard_rule' 必须带 rule_ref")
    if not decided_by or not decided_by.strip():
        raise InvalidRejectionReason("decided_by 不能为空")

    application_row = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    if application_row is None:
        raise ValueError(f"application_id={application_id} 不存在")
    from_stage_id = application_row[0]

    rejection_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO rejection_record "
            "(id, application_id, reason_type, rule_ref, human_readable, decided_by, batch_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rejection_id, application_id, reason_type, rule_ref, human_readable, decided_by, batch_id),
        )
        conn.execute(
            "UPDATE application SET status = 'rejected', current_stage_id = 'rejected' WHERE id = ?",
            (application_id,),
        )
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, actor) "
            "VALUES (?, ?, ?, 'rejected', 'human', ?)",
            (str(uuid.uuid4()), application_id, from_stage_id, decided_by),
        )
    except Exception:
        # conn 是全应用共享的单连接：写到一半失败，前面几条语句仍挂在这个
        # 隐式打开的事务里，不回滚的话会被之后任何一次*不相关*的
        # conn.commit() 悄悄落盘——拒绝记录有了、流转事实表却没有，
        # 精确审计链出现窟窿（这正是本子系统存在的目的）。
        #
        # 回滚自己也可能失败（同 idempotency.py 的理由：例如事务已被另一个
        # 持有者结束）。这个次生异常⛔不得替换掉原始异常（调用方要看到的是
        # 写入失败的真实原因，不是"rollback 炸了"），但也不能静默——回滚失败
        # 意味着上面那半截写入仍挂在未提交事务里，记 ERROR 留下痕迹。
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.error(
                "rollback failed while cleaning up after write_rejection "
                "raised for application_id=%s; the partial write was NOT "
                "undone and may be silently committed by a later, unrelated "
                "effect",
                application_id,
                exc_info=rollback_exc,
            )
        raise
    conn.commit()
    return rejection_id
