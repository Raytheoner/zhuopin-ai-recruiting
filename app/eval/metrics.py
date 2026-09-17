"""
D9 四项验收指标的算法口径（design D9，spec「四项验收指标可一键计算」）。全部纯函数。

口径锁死在这里：U0 对比脚本与 U6 `scripts/eval_m2.py report` 共用，⛔ 不各写各的。
"""
from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from app.parsing.spans import TextSpan
from app.schemas.resume_fields import FIELD_NAMES

THRESHOLDS: dict[str, float] = {
    "field_accuracy": 0.90,
    "spearman": 0.70,
    "top_k_recall": 0.85,
    "span_traceability": 1.0,
}
MIN_SAMPLES_FOR_RANK_METRICS = 10

_COMPANY_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "公司")


def normalize_text(value: object) -> str:
    """NFKC 统一全半角 + 去掉全部空白 + 小写（英文技能名大小写不算错）。"""
    if value is None:
        return ""
    return "".join(unicodedata.normalize("NFKC", str(value)).split()).lower()


def normalize_company(value: object) -> str:
    text = normalize_text(value)
    for suffix in _COMPANY_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def field_correct(field: str, predicted: object, truth: object) -> bool:
    if predicted is None or truth is None:
        return predicted is None and truth is None
    if field == "years_of_experience":
        return round(float(predicted)) == round(float(truth))
    if field == "skills":
        if isinstance(predicted, str) or isinstance(truth, str):
            raise TypeError(f"skills 期望 list[str]，收到 {type(predicted).__name__ if isinstance(predicted, str) else type(truth).__name__}")
        return jaccard({normalize_text(x) for x in predicted}, {normalize_text(x) for x in truth}) >= 0.8
    if field == "companies":
        if isinstance(predicted, str) or isinstance(truth, str):
            raise TypeError(f"companies 期望 list[str]，收到 {type(predicted).__name__ if isinstance(predicted, str) else type(truth).__name__}")
        return {normalize_company(x) for x in predicted} == {normalize_company(x) for x in truth}
    if field == "education":
        p, t = dict(predicted), dict(truth)
        return normalize_text(p.get("degree")) == normalize_text(t.get("degree")) and normalize_text(
            p.get("school")
        ) == normalize_text(t.get("school"))
    return normalize_text(predicted) == normalize_text(truth)


@dataclass(frozen=True)
class FieldAccuracy:
    per_field: dict[str, float]
    overall: float
    n: int


def field_accuracy(predictions: dict[str, dict], truths: dict[str, dict]) -> FieldAccuracy:
    ids = [sid for sid in truths if sid in predictions]
    if not ids:
        return FieldAccuracy({f: 0.0 for f in FIELD_NAMES}, 0.0, 0)
    per_field = {
        f: sum(field_correct(f, predictions[sid].get(f), truths[sid].get(f)) for sid in ids) / len(ids)
        for f in FIELD_NAMES
    }
    return FieldAccuracy(per_field, sum(per_field.values()) / len(FIELD_NAMES), len(ids))


def spearman(
    system_rank: dict[str, int], human_rank: dict[str, int], *, min_samples: int = MIN_SAMPLES_FOR_RANK_METRICS
) -> float | None:
    ids = sorted(set(system_rank) & set(human_rank))
    n = len(ids)
    if n < min_samples:
        return None
    d2 = sum((system_rank[i] - human_rank[i]) ** 2 for i in ids)
    return 1.0 - 6.0 * d2 / (n * (n * n - 1))


def top_k_recall(system_order: list[str], human_order: list[str], *, k: int = 10) -> float:
    if k < 1:
        raise ValueError(f"k 必须 ≥ 1，收到 {k}")
    n = len(human_order)
    if n == 0:
        return 0.0
    kk = k if n >= k else math.ceil(n / 2)
    human_top = set(human_order[:kk])
    system_top = set(system_order[:kk])
    return len(human_top & system_top) / len(human_top)


def span_traceability(refs: Iterable[tuple[int, int | None, int | None]], spans: list[TextSpan]) -> float:
    refs = list(refs)
    if not refs:
        return 0.0
    by_id = {s.span_id: s for s in spans}
    ok = 0
    for span_id, start, end in refs:
        span = by_id.get(span_id)
        if span is not None and start is not None and end is not None and span.start <= start < end <= span.end:
            ok += 1
    return ok / len(refs)
