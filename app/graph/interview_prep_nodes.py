"""prep 出题 L4 编排层（voice-structured-interview U2 tasks 3.4/3.5/3.6/3.7）。

compute_prep 只读查库组装 L3 Agent 的输入（工程铁律 2：compute_* 节点可以
查库做输入组装，写只能在 effect_* 节点里，与 app/graph/screening_nodes.py
的 compute_screen 同一先例）。

"业务经理确认后冻结"不使用 LangGraph 真实 interrupt()/Command(resume=...)：
本项目在 2026-08-26 已有明确判例（openspec/changes/m1-job-profile-intake/
tasks.md 第 175-176/514-535 行，Shao Peishen 判定「行为等价」）——Web 通道下
"挂起等人确认"由「HTTP 请求/响应 + 状态落 SQLite（status='draft'）+ 独立的
/freeze 端点直接调用 effect_* 函数」达成，不需要真的建一个已编译的
StateGraph。effect_freeze_prep 与 app/graph/nodes.py::effect_confirm_profile
是同一种形态：被 Web 路由处理函数直接调用的普通 Python 函数。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.agents.interview_prep import PrepDraft, PrepQuestionDraft, generate
from app.audit.evidence_ref import EvidenceRef, parse_evidence_ref
from app.graph.screening_nodes import latest_approved_profile_version
from app.schemas.interview_ai_input import PrepInput, ResumeScoreItem
from app.schemas.job_profile import derive_rubric_dimensions
from app.storage.idempotency import idempotent_effect


class ProfileNotApprovedError(Exception):
    """该投递所属岗位还没有已确认（approved）的画像版本，无法生成 prep 题目。"""


class PrepSnapshotNotFrozenError(Exception):
    """开场入口遇到未冻结（非 frozen）的 prep 快照（spec「未确认即开场」场景）。"""


def load_application_job(conn: sqlite3.Connection, application_id: str) -> str:
    row = conn.execute(
        "SELECT job_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"application 不存在: {application_id!r}")
    return row[0]


def _load_profile(conn: sqlite3.Connection, *, job_id: str, version: int) -> dict:
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
        (job_id, version),
    ).fetchone()
    return json.loads(row[0])


def _load_resume_scores(
    conn: sqlite3.Connection, application_id: str
) -> tuple[list[ResumeScoreItem], str | None]:
    """取该投递最近一次简历精排（prompt_version 前缀 'rank-'）的逐维评分与
    证据摘录。没有任何精排 run 时返回 ([], None)（spec Scenario「简历评分
    尚未完成」）。

    证据摘录取 evidence_ref 指向的整个 resume_text_span.text（不在此基础上
    再按 start/end 二次切片）——span 本身就是"原文分片"（resume-parsing
    spec），用整段作摘录足够可读，也避免臆造 M2 精排尚未实现时未曾验证过的
    子串偏移约定。
    """
    run_row = conn.execute(
        "SELECT id FROM analysis_run WHERE application_id = ? AND prompt_version LIKE 'rank-%' "
        "ORDER BY created_at DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    if run_row is None:
        return [], None
    run_id = run_row[0]

    resume_row = conn.execute(
        "SELECT resume_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    resume_id = resume_row[0]

    rows = conn.execute(
        "SELECT criterion_key, score, evidence_ref FROM criterion_score "
        "WHERE analysis_run_id = ?",
        (run_id,),
    ).fetchall()

    items: list[ResumeScoreItem] = []
    for criterion_key, score, evidence_ref_raw in rows:
        ref = parse_evidence_ref(evidence_ref_raw)
        assert isinstance(ref, EvidenceRef)  # 简历评分只产出 span 回指，不产出 interview_turn 回指
        span_row = conn.execute(
            "SELECT text FROM resume_text_span WHERE resume_id = ? AND span_id = ?",
            (resume_id, ref.span_id),
        ).fetchone()
        excerpt = span_row[0] if span_row else ""
        items.append(
            ResumeScoreItem(criterion_key=criterion_key, score=score, evidence_excerpt=excerpt)
        )
    return items, run_id


def load_prep_config(conn: sqlite3.Connection, job_id: str) -> tuple[str, int]:
    """读岗位级 prep 配置（Task 2 的 job_prep_config 表）；没有对应行的岗位
    （包括全部既有岗位）回落到默认值 ('easy_to_hard', 10)——这正是"新表可选、
    既有岗位零改动也能正常工作"的手段，⛔ 不要求业务经理为每个岗位先建一行
    配置才能生成 prep 题目。"""
    row = conn.execute(
        "SELECT prep_curve, prep_question_count FROM job_prep_config WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return "easy_to_hard", 10
    return row


def next_prep_version(conn: sqlite3.Connection, application_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(version) FROM prep_snapshot WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    return (row[0] or 0) + 1


def expire_outdated_snapshots(
    conn: sqlite3.Connection, *, application_id: str, current_profile_version: int
) -> None:
    """画像升到新版本时，该投递已有的非过期快照里 profile_version 落后的置为
    expired（spec「画像升版后重新生成」）。⛔ 不在这里 commit——调用方
    （effect_persist_prep_draft）与其余写入同一个事务提交。"""
    conn.execute(
        "UPDATE prep_snapshot SET status = 'expired' "
        "WHERE application_id = ? AND profile_version < ? AND status != 'expired'",
        (application_id, current_profile_version),
    )


def compute_prep(
    conn: sqlite3.Connection, *, application_id: str, gateway
) -> tuple[PrepDraft, int, str | None]:
    """L4 compute_* 节点：只读查库组装 PrepInput，调 L3 Agent 生成题目。
    返回 (草稿, 生成时绑定的画像版本, 简历评分 run 标识或 None)。"""
    job_id = load_application_job(conn, application_id)
    profile_version = latest_approved_profile_version(conn, job_id)
    if profile_version is None:
        raise ProfileNotApprovedError(f"岗位 {job_id!r} 还没有已确认的画像版本")

    profile = _load_profile(conn, job_id=job_id, version=profile_version)
    rubric_dimensions = derive_rubric_dimensions(profile)
    resume_scores, resume_run_id = _load_resume_scores(conn, application_id)

    curve, question_count = load_prep_config(conn, job_id)

    prep_input = PrepInput(
        profile=profile, rubric_dimensions=rubric_dimensions, resume_scores=resume_scores
    )
    draft = generate(
        gateway,
        prep_input,
        curve=curve,
        question_count=question_count,
        audit_context={
            "thread_id": f"{application_id}:prep",
            "node": "compute_prep",
            "application_id": application_id,
            "job_id": job_id,
        },
    )
    return draft, profile_version, resume_run_id


@idempotent_effect("effect_persist_prep_draft")
def effect_persist_prep_draft(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    version: int,
    profile_version: int,
    resume_run_id: str | None,
    draft: PrepDraft,
) -> None:
    """effect_* 节点：把 L3 Agent 的草稿落成 prep_snapshot(status=draft) +
    prep_question，独占、幂等。business_key 由调用方传 gen_run_id（同一次
    真实 LLM 调用只落一次快照）。

    先按当前画像版本把该投递的旧快照标 expired（spec「画像升版后重新生成」），
    再插入新草稿——两步在同一个事务里，由 idempotent_effect 装饰器统一提交。
    """
    expire_outdated_snapshots(
        conn, application_id=application_id, current_profile_version=profile_version
    )
    snapshot_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, "
        "resume_run_id, gen_run_id, status) VALUES (?, ?, ?, ?, ?, ?, 'draft')",
        (snapshot_id, application_id, version, profile_version, resume_run_id, draft.run_id),
    )
    for seq, question in enumerate(draft.questions, start=1):
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, "
            "text, rubric_json, follow_ups_json, rationale, origin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai')",
            (
                str(uuid.uuid4()), snapshot_id, seq, question.dimension, question.difficulty,
                question.text, question.rubric, json.dumps(question.follow_ups, ensure_ascii=False),
                question.rationale,
            ),
        )


@idempotent_effect("effect_freeze_prep")
def effect_freeze_prep(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    version: int,
    confirmed_by: str,
) -> None:
    """effect_* 节点：draft → frozen，记确认人与时刻，独占、幂等
    （spec「业务经理确认」：生成一份带版本号的冻结快照，记录确认人与时刻；
    该快照后续不可修改）。"""
    conn.execute(
        "UPDATE prep_snapshot SET status = 'frozen', confirmed_by = ?, "
        "confirmed_at = datetime('now') WHERE application_id = ? AND version = ? "
        "AND status = 'draft'",
        (confirmed_by, application_id, version),
    )


@idempotent_effect("effect_edit_prep_question")
def effect_edit_prep_question(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    snapshot_id: str,
    seq: int,
    text: str,
    rubric: str,
) -> None:
    """effect_* 节点：业务经理改题面/rubric，origin 转 ai_edited，原 AI 文本
    保留在 ai_text（spec「业务经理修改题面」）。只在 draft 状态下可改——
    调用方（Web 端点）在调用前校验快照状态，本节点信任调用方已校验。"""
    row = conn.execute(
        "SELECT text, origin, ai_text FROM prep_question WHERE snapshot_id = ? AND seq = ?",
        (snapshot_id, seq),
    ).fetchone()
    original_text, origin, existing_ai_text = row
    # 第二次及以后编辑：ai_text 已经保留过第一版 AI 原文，不能用"这一次编辑前
    # 的文本"去覆盖它，否则第一版原文会丢失、只剩上一次编辑后的版本。
    ai_text_to_keep = existing_ai_text if origin == "ai_edited" else original_text
    conn.execute(
        "UPDATE prep_question SET text = ?, rubric_json = ?, origin = 'ai_edited', "
        "ai_text = ? WHERE snapshot_id = ? AND seq = ?",
        (text, rubric, ai_text_to_keep, snapshot_id, seq),
    )


@idempotent_effect("effect_delete_prep_question")
def effect_delete_prep_question(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, snapshot_id: str, seq: int
) -> None:
    """effect_* 节点：删除草稿里的一道题（只在 draft 状态下调用，调用方校验）。"""
    conn.execute(
        "DELETE FROM prep_question WHERE snapshot_id = ? AND seq = ?", (snapshot_id, seq)
    )


@idempotent_effect("effect_regenerate_prep_question")
def effect_regenerate_prep_question(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    snapshot_id: str,
    seq: int,
    question: PrepQuestionDraft,
) -> None:
    """effect_* 节点：把重生成的题目原样覆盖到该 seq 位置，origin 重置为 'ai'
    （这是一次全新的 AI 生成，不是人工编辑）。"""
    conn.execute(
        "UPDATE prep_question SET dimension = ?, difficulty = ?, text = ?, rubric_json = ?, "
        "follow_ups_json = ?, rationale = ?, origin = 'ai', ai_text = NULL "
        "WHERE snapshot_id = ? AND seq = ?",
        (
            question.dimension, question.difficulty, question.text, question.rubric,
            json.dumps(question.follow_ups, ensure_ascii=False), question.rationale,
            snapshot_id, seq,
        ),
    )


def verify_prep_frozen(conn: sqlite3.Connection, *, application_id: str, version: int) -> None:
    """开场前置校验（spec「未确认即开场」：请求被拒绝，场次不建立，拒绝原因
    可查）。本单元只实现这个可调用的校验函数——真正的"开场"端点属于 U3/U4，
    届时直接调用本函数。"""
    row = conn.execute(
        "SELECT status FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, version),
    ).fetchone()
    if row is None:
        raise PrepSnapshotNotFrozenError(
            f"投递 {application_id!r} 没有版本 {version} 的 prep 快照"
        )
    if row[0] != "frozen":
        raise PrepSnapshotNotFrozenError(
            f"投递 {application_id!r} 的 prep 快照 v{version} 状态是 {row[0]!r}，非 frozen"
        )
