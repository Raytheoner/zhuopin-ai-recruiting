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


def compute_screen(
    conn: sqlite3.Connection, *, resume_id: str, job_id: str, profile_version: int
) -> list[RuleVerdict]:
    """L4 读编排：组装 screen() 的三个入参并调用它（工程铁律 2，⛔ 本函数
    只读不写）。"""
    resume_row = conn.execute(
        "SELECT parsed_json FROM resume WHERE id = ?", (resume_id,)
    ).fetchone()
    if resume_row is None or resume_row[0] is None:
        raise ValueError(f"resume_id={resume_id} 尚未解析，无法判定硬门槛")
    fields = ResumeFields.model_validate_json(resume_row[0])

    pending_rows = conn.execute(
        "SELECT field FROM field_review_queue WHERE resume_id = ? AND status = 'pending'",
        (resume_id,),
    ).fetchall()
    review_queue = frozenset(row[0] for row in pending_rows)

    rules = load_active_rules(conn, job_id=job_id, profile_version=profile_version)
    return screen(fields, rules, review_queue)


@idempotent_effect("effect_persist_flags")
def effect_persist_flags(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    profile_version: int,
    verdicts: list[RuleVerdict],
) -> int:
    """唯一的写入点（tasks 4.3）。幂等键
    {application_id}:effect_persist_flags:{profile_version}:{parse_version}
    由调用方（screen_and_persist）拼好传入 business_key。

    ⛔ 不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交
    （工程铁律 1）。重判（校对完成／画像升版）产生新一组 flags：不同
    business_key 天然对应不同的 effect_key，旧组的行永远不会被本函数
    删除或覆盖。
    """
    for verdict in verdicts:
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict, reason, evidence_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), application_id, profile_version,
                verdict.rule_ref, verdict.verdict, verdict.reason, verdict.evidence_ref,
            ),
        )
    return len(verdicts)


def screen_and_persist(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    resume_id: str,
    job_id: str,
    profile_version: int,
    parse_version: str,
) -> int | None:
    """三个触发点（初次解析后／字段校对完成后／画像升版后）共用的唯一入口。
    返回 effect_persist_flags 的返回值（写入的标记数，幂等命中时为 None）。
    """
    verdicts = compute_screen(
        conn, resume_id=resume_id, job_id=job_id, profile_version=profile_version
    )
    business_key = f"{profile_version}:{parse_version}"
    return effect_persist_flags(
        conn,
        thread_id=application_id,
        business_key=business_key,
        application_id=application_id,
        profile_version=profile_version,
        verdicts=verdicts,
    )
