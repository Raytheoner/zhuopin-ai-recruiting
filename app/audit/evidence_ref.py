"""criterion_score.evidence_ref 的 JSON 回指约定。

两种编码并存：
  ① resume span 回指（M2 精排，U4 tasks 5.4/5.5）：{"span_id","start","end"}
  ② interview turn 回指（M3 评分，U5 tasks 6.2/6.6）：
     {"type":"interview_turn","id","start","end","quote"}

列本身仍是自由文本（M1 intake 场景可以继续写 "resume-1#120-180" 这类自由
格式字符串）；两种 JSON 编码都是**可选**的程序化约定，`parse_evidence_ref`
按 `type` 键是否等于 "interview_turn" 分派——没有 `type` 键（① 的旧编码）
走原有分支，**旧调用方零改动、旧测试零改动**。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceRef:
    span_id: int
    start: int
    end: int


@dataclass(frozen=True)
class InterviewTurnEvidenceRef:
    id: str
    start: int
    end: int
    quote: str


def format_evidence_ref(span_id: int, start: int, end: int) -> str:
    return json.dumps(
        {"span_id": span_id, "start": start, "end": end}, ensure_ascii=False
    )


def format_interview_turn_evidence_ref(turn_id: str, start: int, end: int, quote: str) -> str:
    return json.dumps(
        {"type": "interview_turn", "id": turn_id, "start": start, "end": end, "quote": quote},
        ensure_ascii=False,
    )


def parse_evidence_ref(raw: str) -> EvidenceRef | InterviewTurnEvidenceRef:
    """⛔ 不做静默兜底：非 JSON 输入直接抛 json.JSONDecodeError，缺键直接抛
    KeyError。调用方在写入前自己保证格式，读取历史 M1 自由文本行的调用方要
    先判断是否是本约定的 JSON，不能指望本函数替它兜底。"""
    data = json.loads(raw)
    if data.get("type") == "interview_turn":
        return InterviewTurnEvidenceRef(
            id=data["id"], start=data["start"], end=data["end"], quote=data["quote"]
        )
    return EvidenceRef(span_id=data["span_id"], start=data["start"], end=data["end"])


def validate_interview_turn_evidence(conn: sqlite3.Connection, ref: InterviewTurnEvidenceRef) -> None:
    """校验 interview_turn 证据回指：turn 必须存在，偏移必须落在该 turn
    answer_text 的合法范围内（0 <= start < end <= len(answer_text)）。

    校验失败抛 ValueError 而不是返回 bool——调用方（U5 compute_score/
    compute_align）要的是"这次评分整体判不可用"的硬失败，不是一个可能被
    忽略的布尔值（interview-scorecard spec「模型未给出证据」场景：证据
    校验失败时该次评分整体不写入）。
    """
    row = conn.execute(
        "SELECT answer_text FROM interview_turn WHERE id = ?", (ref.id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_turn 不存在: {ref.id!r}")
    answer_text = row[0] or ""
    if not (0 <= ref.start < ref.end <= len(answer_text)):
        raise ValueError(
            f"证据偏移越界: start={ref.start}, end={ref.end}, "
            f"answer_text 长度={len(answer_text)}"
        )
