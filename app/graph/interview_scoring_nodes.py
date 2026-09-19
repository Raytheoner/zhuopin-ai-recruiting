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
