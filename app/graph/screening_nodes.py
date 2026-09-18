"""硬门槛判定的编排层（design D13：compute_screen → effect_persist_flags）。

⛔ compute_screen 只读、不写（工程铁律 2：L4 编排层的 compute_* 节点可以
查库做输入组装，但写只能在 effect_* 节点里）。
"""
from __future__ import annotations

import logging
import sqlite3
import uuid

from app.agents.hard_requirement import HardRequirement, SubjectiveRequirementError, is_subjective
from app.agents.hard_requirement_screening import RuleVerdict, screen
from app.schemas.resume_fields import ResumeFields
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


def load_active_rules(
    conn: sqlite3.Connection, *, job_id: str, profile_version: int
) -> list[HardRequirement]:
    """按 (job_id, profile_version) 读取 hard_requirement 规则集
    （hard-requirement-screening spec「规则版本随画像冻结版本」）。

    ⛔ 第二道防线：M1 的 assert_no_subjective_requirements() 已经在规则
    落库前拦过一次主观描述（app/graph/nodes.py::_record_hard_requirements），
    这里对 blocking=1 且命中 is_subjective() 的规则再拦一次并拒绝加载
    （candidate-ranking spec「主观描述不入硬门槛」、合规红线）。命中就让
    SubjectiveRequirementError 穿透——这是数据完整性问题，⛔ 不静默跳过。
    """
    rows = conn.execute(
        "SELECT field, operator, value, blocking, human_readable FROM hard_requirement "
        "WHERE job_id = ? AND profile_version = ? ORDER BY field, operator, value",
        (job_id, profile_version),
    ).fetchall()
    rules = [
        HardRequirement(
            field=row[0], operator=row[1], value=row[2],
            blocking=bool(row[3]), human_readable=row[4],
        )
        for row in rows
    ]
    for rule in rules:
        if rule.blocking and (is_subjective(rule.value) or is_subjective(rule.human_readable)):
            raise SubjectiveRequirementError(
                f"规则 job_id={job_id} profile_version={profile_version} "
                f"field={rule.field!r} 是 blocking 且命中主观描述，拒绝加载"
                "（合规红线：主观描述不得进入硬门槛规则，只能作为软技能关键词）"
            )
    return rules


def latest_approved_profile_version(conn: sqlite3.Connection, job_id: str) -> int | None:
    """当前岗位冻结（已确认）的最新画像版本号，没有任何已确认版本时返回
    None——调用方据此判断"这个岗位还不能做硬门槛判定"。"""
    row = conn.execute(
        "SELECT MAX(version) FROM job_profile WHERE job_id = ? AND status = 'approved'",
        (job_id,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None
