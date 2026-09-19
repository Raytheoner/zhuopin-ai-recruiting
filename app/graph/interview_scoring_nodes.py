"""post 评分 L4 编排层（voice-structured-interview U5 tasks 6.1/6.2/6.3/6.4/
6.5/6.6/6.7）。

compute_align/compute_score 只读查库组装输入，不写库（工程铁律 2，与
app/graph/interview_prep_nodes.py 的 compute_prep 同一先例）。effect_* 节点
独占写库，全部用 @idempotent_effect 装饰。

不建真实编译的 LangGraph StateGraph——post 段是批处理触发（没有 Web 请求/
响应之间的人工确认），本来就不需要 interrupt()/Command(resume=...)；节点
序列用 run_post_scoring() 这个普通函数串联，判例见
app/graph/interview_prep_nodes.py 顶部 docstring（2026-08-26 定「行为等价」）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.agents.interview_scoring import ScoreCardDraft, score
from app.parsing.spans import TextSpan, locate_quote
from app.schemas.interview_ai_input import ScoreInput, ScoreInputTurn


@dataclass(frozen=True)
class AlignedTurn:
    turn_id: str
    seq: int
    question_id: str
    question_text: str
    answer_text: str
    answer_mode: str
    asr_confidence: float | None
    audio_start_ms: int | None
    audio_end_ms: int | None
    low_confidence: bool


def _load_thresholds(conn: sqlite3.Connection, session_id: str) -> tuple[float, float]:
    """读岗位级评分阈值配置（低置信度阈值、低分阈值）。没有对应 job_prep_config
    行的岗位回落到默认值 (0.6, 2.0)——与 app/graph/interview_prep_nodes.py::
    load_prep_config 同一"新表可选、既有岗位零改动"手法。"""
    row = conn.execute(
        "SELECT jpc.low_confidence_threshold, jpc.low_score_threshold "
        "FROM interview_session s "
        "JOIN application a ON a.id = s.application_id "
        "LEFT JOIN job_prep_config jpc ON jpc.job_id = a.job_id "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    low_confidence_threshold, low_score_threshold = row
    return (
        low_confidence_threshold if low_confidence_threshold is not None else 0.6,
        low_score_threshold if low_score_threshold is not None else 2.0,
    )


def compute_align(conn: sqlite3.Connection, *, session_id: str) -> list[AlignedTurn]:
    """L4 compute_* 节点：把该场次的全部 turn 整理为可定位证据单元（interview-
    scorecard spec「转写按 turn 对齐」）。只读，不写库。"""
    low_confidence_threshold, _ = _load_thresholds(conn, session_id)
    rows = conn.execute(
        "SELECT id, seq, question_id, question_text, answer_text, answer_mode, "
        "asr_confidence, audio_start_ms, audio_end_ms FROM interview_turn "
        "WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()

    aligned: list[AlignedTurn] = []
    for (turn_id, seq, question_id, question_text, answer_text, answer_mode,
         asr_confidence, audio_start_ms, audio_end_ms) in rows:
        low_confidence = asr_confidence is not None and asr_confidence < low_confidence_threshold
        aligned.append(
            AlignedTurn(
                turn_id=turn_id, seq=seq, question_id=question_id, question_text=question_text,
                answer_text=answer_text or "", answer_mode=answer_mode, asr_confidence=asr_confidence,
                audio_start_ms=audio_start_ms, audio_end_ms=audio_end_ms, low_confidence=low_confidence,
            )
        )
    return aligned


def _load_session_context(conn: sqlite3.Connection, session_id: str) -> tuple[str, str, str]:
    """返回 (application_id, job_id, prep_snapshot 的 id)。"""
    row = conn.execute(
        "SELECT s.application_id, a.job_id, s.prep_snapshot_version "
        "FROM interview_session s JOIN application a ON a.id = s.application_id "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    application_id, job_id, snapshot_version = row

    snap_row = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, snapshot_version),
    ).fetchone()
    if snap_row is None:
        raise ValueError(
            f"prep_snapshot 不存在: application_id={application_id!r} version={snapshot_version!r}"
        )
    return application_id, job_id, snap_row[0]


def _load_rubric_dimensions(conn: sqlite3.Connection, snapshot_id: str) -> list[str]:
    """按 seq 顺序取该快照的去重维度列表。⛔ 不用 SELECT DISTINCT ... ORDER BY
    seq——DISTINCT 折叠重复维度后 ORDER BY 引用的 seq 取哪一行未定义，改用
    Python 去重保序。"""
    rows = conn.execute(
        "SELECT dimension FROM prep_question WHERE snapshot_id = ? ORDER BY seq",
        (snapshot_id,),
    ).fetchall()
    dimensions: list[str] = []
    for (dimension,) in rows:
        if dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


def compute_score(
    conn: sqlite3.Connection, *, session_id: str, aligned_turns: list[AlignedTurn], gateway
) -> ScoreCardDraft:
    """L4 compute_* 节点：只读查库组装 ScoreInput，调 L3 Agent 评分（interview-
    scorecard spec「逐维评分带 turn 回指」）。"""
    application_id, job_id, snapshot_id = _load_session_context(conn, session_id)
    rubric_dimensions = _load_rubric_dimensions(conn, snapshot_id)

    score_input = ScoreInput(
        rubric_dimensions=rubric_dimensions,
        turns=[
            ScoreInputTurn(
                turn_id=t.turn_id, seq=t.seq, question_text=t.question_text, answer_text=t.answer_text
            )
            for t in aligned_turns
        ],
    )
    return score(
        gateway,
        score_input,
        audit_context={
            "thread_id": f"{session_id}:post",
            "node": "compute_score",
            "application_id": application_id,
            "job_id": job_id,
            "rubric_version": snapshot_id,
            "rubric_snapshot": {"dimensions": rubric_dimensions},
        },
    )


class ScoringEvidenceUnusable(Exception):
    """某个维度的证据反查失败，或指向的 turn 不属于本场次——interview-scorecard
    spec Scenario「模型未给出证据」：该次评分整体判不可用，不写入任何评分项。"""


@dataclass(frozen=True)
class CorrectedCriterionScore:
    dimension: str
    score: float
    turn_id: str
    start: int
    end: int
    quote: str


def correct_evidence(
    draft: ScoreCardDraft, aligned_turns: list[AlignedTurn]
) -> list[CorrectedCriterionScore]:
    """用 quote 在 turn 原文里反查校正偏移（interview-scorecard spec「回指
    校正」）：模型给的 start/end 不采信，一律以反查结果为准。任一维度反查失败
    或指向的 turn 不属于本场次，整次评分判不可用（抛异常，⛔ 不做部分写入）。
    """
    turns_by_id = {t.turn_id: t for t in aligned_turns}
    corrected: list[CorrectedCriterionScore] = []

    for dim in draft.dimensions:
        turn = turns_by_id.get(dim.turn_id)
        if turn is None:
            raise ScoringEvidenceUnusable(
                f"维度 {dim.dimension!r} 的证据指向不属于本场次的 turn: {dim.turn_id!r}"
            )
        span = TextSpan(span_id=0, start=0, end=len(turn.answer_text), text=turn.answer_text)
        located = locate_quote(span, dim.quote)
        if located is None:
            raise ScoringEvidenceUnusable(
                f"维度 {dim.dimension!r} 的证据摘录在 turn {dim.turn_id!r} 原文中反查失败: {dim.quote!r}"
            )
        start, end = located
        corrected.append(
            CorrectedCriterionScore(
                dimension=dim.dimension, score=dim.score, turn_id=dim.turn_id,
                start=start, end=end, quote=dim.quote,
            )
        )
    return corrected
