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

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass

from app.agents.interview_scoring import ScoreCardDraft, score
from app.audit.evidence_ref import (
    InterviewTurnEvidenceRef,
    format_interview_turn_evidence_ref,
    validate_interview_turn_evidence,
)
from app.parsing.spans import TextSpan, locate_quote
from app.schemas.interview_ai_input import ScoreInput, ScoreInputTurn
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class TalkingPoint:
    dimension: str
    turn_id: str
    tip_text: str


def derive_talking_points(
    corrected_scores: list[CorrectedCriterionScore],
    aligned_turns: list[AlignedTurn],
    *,
    low_score_threshold: float,
) -> list[TalkingPoint]:
    """规则派生要点提示（interview-scorecard spec「ScoreCard 与要点提示只作
    参考」），不再调 LLM。两类来源：① 低分维度 ② 低置信度语音 turn；同一
    (dimension, turn_id) 只保留一条，低分文案优先（低分维度先写入 dict，
    低置信度检查时 `if key not in seen` 短路，不覆盖）。"""
    seen: dict[tuple[str, str], TalkingPoint] = {}

    for cs in corrected_scores:
        if cs.score < low_score_threshold:
            key = (cs.dimension, cs.turn_id)
            seen[key] = TalkingPoint(
                dimension=cs.dimension, turn_id=cs.turn_id,
                tip_text=(
                    f"建议终面追问：候选人在【{cs.dimension}】维度得分偏低（{cs.score}），"
                    "可结合本轮回答当面深挖"
                ),
            )

    dimension_by_turn: dict[str, str] = {cs.turn_id: cs.dimension for cs in corrected_scores}
    for turn in aligned_turns:
        if turn.answer_mode == "voice" and turn.low_confidence:
            dimension = dimension_by_turn.get(turn.turn_id, f"第 {turn.seq} 题")
            key = (dimension, turn.turn_id)
            if key not in seen:
                seen[key] = TalkingPoint(
                    dimension=dimension, turn_id=turn.turn_id,
                    tip_text="该 turn 转写置信度低，建议面试官当面复核候选人在此题的实际回答",
                )

    return list(seen.values())


# 普通话正常语速的经验值（字/秒），用于估算"预期朗读时长"——非实测校准，
# 是"没有逐词时间戳时的近似基线"，不是精确测量（design 决策 4）。
BASELINE_CHARS_PER_SECOND = 4.5


def compute_acoustic_ref(
    *, answer_text: str, audio_start_ms: int | None, audio_end_ms: int | None
) -> str | None:
    """turn 级近似估算语速/停顿/静默（interview-scorecard spec「声学信号只
    展示不计分」）。⚠️ 这是启发式近似，不是逐词 VAD——interview_turn 只有
    turn 级起止毫秒，没有逐词时间戳，无法做真正的停顿检测。文本作答 turn
    （音频起止任一为空）恒返回 None。"""
    if audio_start_ms is None or audio_end_ms is None:
        return None
    duration_ms = audio_end_ms - audio_start_ms
    if duration_ms <= 0:
        return None

    char_count = len(answer_text or "")
    speech_rate_cpm = char_count / (duration_ms / 60000)
    expected_speaking_ms = (char_count / BASELINE_CHARS_PER_SECOND) * 1000
    pause_ratio = max(0.0, min(1.0, (duration_ms - expected_speaking_ms) / duration_ms))

    return json.dumps(
        {
            "speech_rate_cpm": round(speech_rate_cpm, 1),
            "pause_ratio": round(pause_ratio, 3),
            "silence_ratio": round(pause_ratio, 3),
            "note": "turn 级近似估算，非逐词 VAD；停顿与静默取同一近似值",
        },
        ensure_ascii=False,
    )


@idempotent_effect("effect_write_acoustic_refs")
def effect_write_acoustic_refs(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    session_id: str,
    aligned_turns: list[AlignedTurn],
) -> None:
    """effect_* 节点：把每个 turn 的声学参考写入 interview_turn.acoustic_ref
    （只读展示字段）。独立于评分成败——即便评分失败待重试，面试官也应该能看到
    已完成场次的声学参考，故本节点不依赖 compute_score 的结果。"""
    for turn in aligned_turns:
        acoustic_ref = compute_acoustic_ref(
            answer_text=turn.answer_text,
            audio_start_ms=turn.audio_start_ms,
            audio_end_ms=turn.audio_end_ms,
        )
        if acoustic_ref is not None:
            conn.execute(
                "UPDATE interview_turn SET acoustic_ref = ? WHERE id = ?",
                (acoustic_ref, turn.turn_id),
            )


@idempotent_effect("effect_persist_scorecard")
def effect_persist_scorecard(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    session_id: str,
    corrected_scores: list[CorrectedCriterionScore],
    overall_summary: str,
    talking_points: list[TalkingPoint],
    analysis_run_id: str,
) -> None:
    """effect_* 节点：写 analysis_run(已由 gateway 落库)＋criterion_score＋
    interview_scorecard＋interview_scorecard_tip，同事务、独占、幂等（interview-
    scorecard spec「逐维评分带 turn 回指」；design D4/D20）。business_key 传
    analysis_run_id（字面幂等键 `{session_id}:effect_persist_scorecard:
    {analysis_run_id}`）。

    悬空/越界的证据回指在这里被 validate_interview_turn_evidence 拒绝（抛
    ValueError）——@idempotent_effect 装饰器会回滚本次调用已经写入的行，
    ⛔ 不会有部分写入残留。
    """
    for cs in corrected_scores:
        ref = InterviewTurnEvidenceRef(id=cs.turn_id, start=cs.start, end=cs.end, quote=cs.quote)
        validate_interview_turn_evidence(conn, ref)
        conn.execute(
            "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), analysis_run_id, cs.dimension, cs.score,
                format_interview_turn_evidence_ref(cs.turn_id, cs.start, cs.end, cs.quote),
            ),
        )

    scorecard_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interview_scorecard (id, session_id, analysis_run_id, summary) VALUES (?, ?, ?, ?)",
        (scorecard_id, session_id, analysis_run_id, overall_summary),
    )
    for seq, tip in enumerate(talking_points, start=1):
        conn.execute(
            "INSERT INTO interview_scorecard_tip (id, scorecard_id, dimension, turn_id, tip_text, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), scorecard_id, tip.dimension, tip.turn_id, tip.tip_text, seq),
        )

    conn.execute(
        "UPDATE interview_session SET post_scoring_status = 'scored', "
        "post_scored_at = datetime('now') WHERE id = ?",
        (session_id,),
    )


@idempotent_effect("effect_mark_scoring_failed")
def effect_mark_scoring_failed(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, reason: str
) -> None:
    """effect_* 节点：把场次标注「评分失败待重试」并记日志，可观测（interview-
    scorecard spec Scenario「模型未给出证据」）。business_key 用调用方传入的
    一次性 uuid——每次失败尝试都要能独立落一条 effect_log（语义类比
    app/graph/invite_nodes.py::effect_log_invite_access_denied 这类可重复
    事件，不是"只能发生一次"的语义）。"""
    logger.warning("post 评分失败，场次 %s 标记 failed_retry：%s", session_id, reason)
    conn.execute(
        "UPDATE interview_session SET post_scoring_status = 'failed_retry' WHERE id = ?",
        (session_id,),
    )


def run_post_scoring(conn: sqlite3.Connection, *, session_id: str, gateway) -> str:
    """post 子图编排（design D20，普通函数链，不建真实 StateGraph——见设计
    决策 7）：compute_align → effect_write_acoustic_refs → compute_score →
    correct_evidence → effect_persist_scorecard（成功）/ effect_mark_scoring_
    failed（证据不可用或全维度越界丢弃）。返回 "scored" 或 "failed_retry"，
    供批处理脚本记日志。

    入口先查 post_scoring_status——本函数自身必须是幂等的，不能只依赖批处理
    脚本的 WHERE 过滤（scripts/run_interview_post_scoring.py::_due_sessions）：
    interview_scorecard 有 UNIQUE(session_id)，已 scored 的场次若不在这里
    短路，重复调用会在 effect_persist_scorecard 里撞唯一键报错。
    """
    from app.agents.interview_scoring import ScoringGenerationFailed  # 避免模块顶层循环 import

    status_row = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    if status_row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    if status_row[0] == "scored":
        return "scored"

    aligned_turns = compute_align(conn, session_id=session_id)
    effect_write_acoustic_refs(
        conn, thread_id=f"{session_id}:post", business_key=session_id,
        session_id=session_id, aligned_turns=aligned_turns,
    )

    try:
        draft = compute_score(conn, session_id=session_id, aligned_turns=aligned_turns, gateway=gateway)
        corrected_scores = correct_evidence(draft, aligned_turns)
    except (ScoringEvidenceUnusable, ScoringGenerationFailed) as exc:
        effect_mark_scoring_failed(
            conn, thread_id=f"{session_id}:post", business_key=str(uuid.uuid4()),
            session_id=session_id, reason=str(exc),
        )
        return "failed_retry"

    _, low_score_threshold = _load_thresholds(conn, session_id)
    talking_points = derive_talking_points(corrected_scores, aligned_turns, low_score_threshold=low_score_threshold)

    effect_persist_scorecard(
        conn, thread_id=f"{session_id}:post", business_key=draft.run_id,
        session_id=session_id, corrected_scores=corrected_scores, overall_summary=draft.overall_summary,
        talking_points=talking_points, analysis_run_id=draft.run_id,
    )
    return "scored"
