"""疑似重复判定（channel-resume-intake U2 tasks 2.3）。

纯函数：只做姓名规范化比较，⛔ 不写库、不调模型。姓名规范化＝NFKC 统一全半角 +
去掉全部空白（与 app/agents/field_grounding.py::normalize_for_grounding 同口径）。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def normalize_name(text: str | None) -> str:
    if not text:
        return ""
    return "".join(unicodedata.normalize("NFKC", str(text)).split())


@dataclass(frozen=True)
class CandidateRef:
    id: str
    name: str


def detect_suspected_duplicates(
    candidate: CandidateRef,
    same_job_candidates: list[CandidateRef],
) -> list[CandidateRef]:
    """同岗位 + 姓名规范化后相同 ⇒ 疑似重复。只返回其它候选人（不含 candidate 自己）。
    纯函数，⛔ 无任何模型调用。"""
    target = normalize_name(candidate.name)
    return [
        c for c in same_job_candidates
        if c.id != candidate.id and normalize_name(c.name) == target
    ]
