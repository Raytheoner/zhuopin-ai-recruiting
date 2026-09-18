"""criterion_score.evidence_ref 的 JSON 回指约定：{"span_id", "start", "end"}。

列本身仍是自由文本（M1 intake 场景可以继续写 "resume-1#120-180" 这类自由
格式字符串），这里新增的是 M2 精排（U4 tasks 5.4/5.5）落库时使用的一种
**可选**编码方式——评分的 evidence 天然就是 span_id + 偏移量，JSON 编码
让它可以被程序化解析，而不必像自由文本那样正则拆解。
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceRef:
    span_id: int
    start: int
    end: int


def format_evidence_ref(span_id: int, start: int, end: int) -> str:
    return json.dumps(
        {"span_id": span_id, "start": start, "end": end}, ensure_ascii=False
    )


def parse_evidence_ref(raw: str) -> EvidenceRef:
    """⛔ 不做静默兜底：非 JSON 输入直接抛 json.JSONDecodeError，缺键直接抛
    KeyError。调用方（U4）在写入前自己保证格式，读取历史 M1 自由文本行的
    调用方要先判断是否是本约定的 JSON，不能指望本函数替它兜底。"""
    data = json.loads(raw)
    return EvidenceRef(span_id=data["span_id"], start=data["start"], end=data["end"])
