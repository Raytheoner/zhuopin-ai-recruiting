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


_LIST_FIELD_DELIMITER = "、"  # 与 app/web/static/resume_review.html::fieldDisplayValue
# 的展示/回填约定同源（Array.isArray(v) ? v.join("、") ...）——不是本次新造的口径。


def _overlay_reviewed_fields(
    conn: sqlite3.Connection, *, resume_id: str, fields: ResumeFields
) -> ResumeFields:
    """把 field_review_queue 里已校对（status='reviewed'）的人工修正值覆盖到
    fields 对应字段上（tasks 4.4：字段校对完成后重判必须读到修正值，而不是
    过期的机器抽取值）。

    ⛔ screen() 保持纯函数、不接触 DB（工程铁律 2）——覆盖逻辑全部留在
    compute_screen 这一层的读编排里，本函数只是它的私有子步骤。

    human_value 是 review_field 路由收的纯字符串（非 JSON，见
    app/web/server.py::FieldReviewRequest.human_value），按字段类型转换：
    NumberField 转 float；ListField（skills/companies）按「、」拆分；
    EducationField 只覆盖 degree；其余（TextField）原样赋值。覆盖后的字段
    视为人工已确认：not_mentioned=False、confidence=1.0；spans 保留该字段
    原有的机器抽取分片，不臆造新偏移量——fail 判定的 evidence_ref 因此仍
    指向原始简历原文，这是已知的可接受限制，不在本次修复范围内解决。
    """
    # ORDER BY reviewed_at ASC：同一字段可能有多条 reviewed 行（重解析后再次
    # 判低置信度、又被再次校对），循环按升序遍历、后写的覆盖先写的，保证
    # 最终落在 data[field_name] 里的是"最近一次人工修正"，不受插入顺序/主键
    # 顺序摆布（原查询没有 ORDER BY 时结果顺序未定义）。
    reviewed_rows = conn.execute(
        "SELECT field, human_value FROM field_review_queue "
        "WHERE resume_id = ? AND status = 'reviewed' "
        "ORDER BY reviewed_at ASC",
        (resume_id,),
    ).fetchall()
    if not reviewed_rows:
        return fields

    data = fields.model_dump()
    for field_name, human_value in reviewed_rows:
        if field_name not in data:
            continue
        spans = data[field_name].get("spans", [])
        if field_name == "years_of_experience":
            value: object = float(human_value)
        elif field_name in ("skills", "companies"):
            value = [
                item.strip()
                for item in human_value.split(_LIST_FIELD_DELIMITER)
                if item.strip()
            ]
        elif field_name == "education":
            value = {"degree": human_value, "school": None}
        else:
            value = human_value
        data[field_name] = {
            "not_mentioned": False,
            "confidence": 1.0,
            "spans": spans,
            "value": value,
        }
    return ResumeFields.model_validate(data)


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
    fields = _overlay_reviewed_fields(conn, resume_id=resume_id, fields=fields)

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
    {application_id}:effect_persist_flags:{profile_version}:{parse_version}:{review_generation}
    由调用方（screen_and_persist）拼好传入 business_key。

    ⛔ 不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交
    （工程铁律 1）。重判（校对完成／画像升版）产生新一组 flags：不同
    business_key 天然对应不同的 effect_key，旧组的行永远不会被本函数
    删除或覆盖。

    review_generation（截至调用时 field_review_queue 里 status='reviewed'
    的行数）是必要的第三段：profile_version 与 parse_version 在"上传时判定"
    与"字段校对完成后重判"两次调用之间完全相同（HR 改正一个字段不会改变
    画像版本或简历解析版本），没有它两次调用的 business_key 会撞在一起，
    第二次被当成幂等命中静默 no-op（2026-09-18 final review 发现的
    Critical 问题）。
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
    # review_generation：该简历当前已校对（reviewed）的字段数，随每次
    # queue_reapplication_screening 触发（字段 pending→reviewed）恰好 +1，
    # 上传时判定恰好为 0——用它区分"上传时"与"校对后重判"两次调用，否则
    # profile_version:parse_version 在两次调用间完全相同，见 Fix 1。
    review_generation = conn.execute(
        "SELECT COUNT(*) FROM field_review_queue WHERE resume_id = ? AND status = 'reviewed'",
        (resume_id,),
    ).fetchone()[0]
    business_key = f"{profile_version}:{parse_version}:{review_generation}"
    return effect_persist_flags(
        conn,
        thread_id=application_id,
        business_key=business_key,
        application_id=application_id,
        profile_version=profile_version,
        verdicts=verdicts,
    )
