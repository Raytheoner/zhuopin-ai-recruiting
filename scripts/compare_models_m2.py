"""
M2 U0 模型对比（tasks 1.3／1.4；spec「模型对比定型的输入输出契约」）。

沿用 scripts/compare_models.py 的方法：同一候选池、缺 key 即跳过、`max_retries=0` 只看首次。
不同点：留痕走 JsonlAuditHook（铁律 3，⛔ 不用 NoopAuditHook），指标走 app/eval/metrics（D9），
模型标识只认响应侧 `LLMCallMeta.response_model`（铁律 5）。

用法（Task 7 加 CLI）：python -m scripts.compare_models_m2 --samples data/eval/m2-pilot --out docs/m2-model-comparison-run.md
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.eval.metrics import FieldAccuracy, field_accuracy, spearman, span_traceability, top_k_recall
from app.llm.gateway import LLMCallMeta, LLMGateway
from app.parsing.spans import TextSpan, render_for_prompt, resolve_span_ref, split_into_spans
from app.schemas.rank_result import RankResult
from app.schemas.resume_fields import FIELD_LABELS, FIELD_NAMES, ResumeFields

PARSE_PROMPT_VERSION = "parse-u0-v1"
RANK_PROMPT_VERSION = "rank-u0-v1"

PARSE_SYSTEM_PROMPT = (
    "你是招聘助手，从简历原文分片中抽取六个字段：" + "、".join(FIELD_LABELS[f] for f in FIELD_NAMES) + "。\n"
    "规则：\n"
    "1. 每个字段给 value、confidence（0–1 的把握程度）、spans（来源分片）。spans 里每项写 span_id（分片编号 [#N] 的 N）"
    "和 quote（从该分片**逐字**摘录的原文，不要改写、不要跨分片拼接）。\n"
    "2. 简历里没写的字段，value 留空并把 not_mentioned 设为 true，⛔ 不要猜、不要编造。\n"
    "3. years_of_experience 用数字（年）；简历没直接写年限时可按工作经历起止年份估算，但 confidence 要相应降低。\n"
    "4. skills 是技能名列表；companies 是公司全称列表（按简历原文）；education 是最高学历 degree 与院校 school。\n"
    "5. 只输出符合 Schema 的 JSON。"
)

RANK_SYSTEM_PROMPT = (
    "你是招聘助手，按给定岗位画像与评分维度，对一份简历逐维打分（0–5 分，5 = 完全匹配）。\n"
    "规则：\n"
    "1. 每个维度必须给 evidence：span_id（分片编号 [#N] 的 N）与 quote（从该分片**逐字**摘录的原文）。"
    "找不到证据的维度打 0 分并引用最相关的一句原文作为 evidence，⛔ 不要留空。\n"
    "2. 维度 key 只能用题目给出的 key，一个不多一个不少。\n"
    "3. 只做匹配度评估，不做录用建议。只输出符合 Schema 的 JSON。"
)


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    api_key_env: str
    base_url: str
    model: str
    supports_json_schema: bool
    # 元／百万 token。跑对比当天从供应商定价页抄并在 docs/m2-model-comparison.md 记日期；None ⇒ 表中"未填价格"
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None


# 与 scripts/compare_models.py::PROVIDER_CANDIDATES 同源（境内供应商白名单）。
# deepseek 两项 2026-08-09 实测存在；doubao / qwen 模型名仍是占位猜测，接线前必须去控制台核实。
CANDIDATES: list[ModelCandidate] = [
    ModelCandidate("deepseek-pro", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-v4-pro", False),
    ModelCandidate("deepseek-flash", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-v4-flash", False),
    ModelCandidate("doubao", "ARK_API_KEY", "https://ark.cn-beijing.volces.com/api/v3", "doubao-seed-2-1-turbo-241215", True),
    ModelCandidate("qwen", "DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.7-plus-241226", False),
]


class JsonlAuditHook:
    """AuditHook 的 JSONL 实现：每次尝试一行，参数原样落盘（铁律 3），并累计 token 与指纹。"""

    def __init__(self, path: Path, *, rubric_snapshot: dict | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rubric = rubric_snapshot
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.fingerprints: set[str] = set()

    def record(self, **kwargs: Any) -> None:
        self.calls += 1
        usage = kwargs.get("token_usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        fingerprint = kwargs.get("system_fingerprint")
        if fingerprint:
            self.fingerprints.add(str(fingerprint))
        row = {"at": datetime.now(timezone.utc).isoformat(), **kwargs}
        if self._rubric is not None:
            row["rubric_snapshot"] = self._rubric
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


@dataclass(frozen=True)
class Sample:
    sample_id: str
    text: str
    truth_fields: dict[str, Any]
    human_rank: int


def load_samples(sample_dir: Path) -> tuple[list[Sample], dict[str, Any]]:
    sample_dir = Path(sample_dir)
    truth = json.loads((sample_dir / "truth.json").read_text(encoding="utf-8"))
    if truth.get("sample_class") == "live":
        raise ValueError("评测样本类别为 live，拒绝（spec：评测集 MUST NOT 含真实在招简历）")
    samples = [
        Sample(
            sample_id=row["sample_id"],
            text=(sample_dir / row["files"]["txt"]).read_text(encoding="utf-8"),
            truth_fields=row["fields"],
            human_rank=int(row["human_rank"]),
        )
        for row in truth["samples"]
    ]
    return samples, truth["rubric"]


def build_parse_user_prompt(spans: list[TextSpan]) -> str:
    return "以下是简历原文分片，[#N] 是分片编号：\n" + render_for_prompt(spans)


def build_rank_user_prompt(spans: list[TextSpan], rubric: dict[str, Any]) -> str:
    dims = "\n".join(f"- {c['key']}：{c['description']}" for c in rubric["criteria"])
    return (
        f"岗位画像：\n{rubric['profile_text']}\n\n评分维度（key：说明）：\n{dims}\n\n"
        "简历原文分片，[#N] 是分片编号：\n" + render_for_prompt(spans)
    )


def ground_fields(fields: ResumeFields, spans: list[TextSpan]) -> ResumeFields:
    """反查每个 span 的偏移；非未提及字段若无任何可定位 span ⇒ confidence 归 0（design D5）。"""
    data = fields.model_dump()
    for name in FIELD_NAMES:
        fld = data[name]
        located = 0
        for ref in fld["spans"]:
            loc = resolve_span_ref(spans, ref["span_id"], ref["quote"])
            if loc is None:
                ref["start"], ref["end"] = None, None
            else:
                ref["start"], ref["end"] = loc
                located += 1
        if not fld["not_mentioned"] and located == 0:
            fld["confidence"] = 0.0
    return ResumeFields.model_validate(data)


def ground_rank(result: RankResult, spans: list[TextSpan]) -> tuple[RankResult | None, list[str]]:
    data = result.model_dump()
    missing: list[str] = []
    for s in data["scores"]:
        loc = resolve_span_ref(spans, s["evidence"]["span_id"], s["evidence"]["quote"])
        if loc is None:
            missing.append(s["key"])
        else:
            s["evidence"]["start"], s["evidence"]["end"] = loc
    if missing:
        return None, missing
    return RankResult.model_validate(data), []


def run_parse(gateway: LLMGateway, spans: list[TextSpan]) -> tuple[ResumeFields, LLMCallMeta]:
    fields, meta = gateway.extract_structured_with_meta(
        system_prompt=PARSE_SYSTEM_PROMPT,
        user_prompt=build_parse_user_prompt(spans),
        schema=ResumeFields,
        prompt_version=PARSE_PROMPT_VERSION,
    )
    return ground_fields(fields, spans), meta


def run_rank(
    gateway: LLMGateway, spans: list[TextSpan], rubric: dict[str, Any]
) -> tuple[RankResult | None, LLMCallMeta, list[str], RankResult]:
    raw, meta = gateway.extract_structured_with_meta(
        system_prompt=RANK_SYSTEM_PROMPT,
        user_prompt=build_rank_user_prompt(spans, rubric),
        schema=RankResult,
        prompt_version=RANK_PROMPT_VERSION,
    )
    grounded, missing = ground_rank(raw, spans)
    return grounded, meta, missing, raw


@dataclass
class SampleOutcome:
    sample_id: str
    parse_ok: bool = False
    parse_latency_ms: float = 0.0
    predicted: dict[str, Any] | None = None
    parse_error: str | None = None
    rank_ok: bool = False
    rank_latency_ms: float = 0.0
    total_score: float | None = None
    traceability: float | None = None  # 该样本所有返回维度里证据可定位的比例（含被判不可用的那次）
    rank_error: str | None = None
    response_models: set[str] = field(default_factory=set)


def evaluate_sample(gateway: LLMGateway, sample: Sample, rubric: dict[str, Any]) -> SampleOutcome:
    outcome = SampleOutcome(sample_id=sample.sample_id)
    spans = split_into_spans(sample.text)
    try:
        fields, meta = run_parse(gateway, spans)
    except Exception as exc:  # noqa: BLE001
        # 网关对不可切换的 4xx 原样抛出（gateway.py），单份样本的失败不能拖垮整个候选的对比，按样本记错继续。
        outcome.parse_error = f"{sample.sample_id}: parse 失败 {type(exc).__name__}: {exc}"
        return outcome
    outcome.parse_ok = True
    outcome.parse_latency_ms = meta.latency_ms
    outcome.predicted = fields.plain()
    if meta.response_model:
        outcome.response_models.add(meta.response_model)
    try:
        grounded, meta, missing, raw = run_rank(gateway, spans, rubric)
    except Exception as exc:  # noqa: BLE001
        # 网关对不可切换的 4xx 原样抛出（gateway.py），单份样本的失败不能拖垮整个候选的对比，按样本记错继续。
        outcome.rank_error = f"{sample.sample_id}: rank 失败 {type(exc).__name__}: {exc}"
        return outcome
    outcome.rank_latency_ms = meta.latency_ms
    if meta.response_model:
        outcome.response_models.add(meta.response_model)
    refs = []
    for s in raw.scores:
        loc = resolve_span_ref(spans, s.evidence.span_id, s.evidence.quote)
        refs.append((s.evidence.span_id, loc[0] if loc else None, loc[1] if loc else None))
    outcome.traceability = span_traceability(refs, spans)
    if grounded is None:
        outcome.rank_error = f"{sample.sample_id}: 证据不可定位，整次评分不可用: {missing}"
        return outcome
    outcome.rank_ok = True
    outcome.total_score = grounded.total_score()
    return outcome


@dataclass
class ModelReport:
    name: str
    config_model: str
    n_samples: int = 0
    skipped: bool = False
    skip_reason: str | None = None
    response_models: list[str] = field(default_factory=list)
    fingerprints: list[str] = field(default_factory=list)
    parse_ok: int = 0
    rank_ok: int = 0
    field_acc: FieldAccuracy = field(default_factory=lambda: FieldAccuracy({f: 0.0 for f in FIELD_NAMES}, 0.0, 0))
    spearman: float | None = None
    top_k_recall: float | None = None
    span_traceability: float = 0.0
    parse_p50_ms: float = 0.0
    parse_p95_ms: float = 0.0
    rank_p50_ms: float = 0.0
    rank_p95_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_yuan: float | None = None
    audit_path: str | None = None
    errors: list[str] = field(default_factory=list)


def percentile(values: list[float], p: float) -> float:
    """最近秩法：P50/P95 取排序后第 ⌈p%·n⌉ 个；空列表 ⇒ 0.0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(p / 100 * len(ordered)) - 1))
    return ordered[idx]


def compute_cost(candidate: ModelCandidate, prompt_tokens: int, completion_tokens: int) -> float | None:
    if candidate.price_in_per_mtok is None or candidate.price_out_per_mtok is None:
        return None
    return round(prompt_tokens / 1e6 * candidate.price_in_per_mtok + completion_tokens / 1e6 * candidate.price_out_per_mtok, 4)


def default_gateway_factory(candidate: ModelCandidate, hook: JsonlAuditHook) -> LLMGateway | None:
    api_key = os.environ.get(candidate.api_key_env, "")
    if not api_key:
        return None
    return LLMGateway(
        api_key=api_key,
        base_url=candidate.base_url,
        model=candidate.model,
        supports_json_schema=candidate.supports_json_schema,
        max_retries=0,  # 对比只看首次是否达标，不吃重试红利（与 M1 同口径）
        audit_hook=hook,
    )


GatewayFactory = Callable[[ModelCandidate, JsonlAuditHook], LLMGateway | None]


def evaluate_model(
    candidate: ModelCandidate,
    samples: list[Sample],
    rubric: dict[str, Any],
    *,
    gateway_factory: GatewayFactory,
    audit_dir: Path,
    top_k: int = 10,
) -> ModelReport:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    hook = JsonlAuditHook(Path(audit_dir) / candidate.name / f"{stamp}.jsonl", rubric_snapshot=rubric)
    report = ModelReport(name=candidate.name, config_model=candidate.model, n_samples=len(samples), audit_path=str(hook.path))
    gateway = gateway_factory(candidate, hook)
    if gateway is None:
        report.skipped = True
        report.skip_reason = f"跳过：环境变量 {candidate.api_key_env} 未设置"
        return report

    outcomes = [evaluate_sample(gateway, s, rubric) for s in samples]
    report.parse_ok = sum(o.parse_ok for o in outcomes)
    report.rank_ok = sum(o.rank_ok for o in outcomes)
    report.errors = [e for o in outcomes for e in (o.parse_error, o.rank_error) if e]
    report.response_models = sorted({m for o in outcomes for m in o.response_models})
    report.fingerprints = sorted(hook.fingerprints)

    predictions = {o.sample_id: o.predicted for o in outcomes if o.parse_ok and o.predicted is not None}
    truths = {s.sample_id: s.truth_fields for s in samples}
    report.field_acc = field_accuracy(predictions, truths)

    scored = sorted((o for o in outcomes if o.rank_ok), key=lambda o: (-(o.total_score or 0.0), o.sample_id))
    system_order = [o.sample_id for o in scored]
    system_rank = {sid: i + 1 for i, sid in enumerate(system_order)}
    human_order = [s.sample_id for s in sorted(samples, key=lambda s: s.human_rank)]
    human_rank = {s.sample_id: s.human_rank for s in samples}
    report.spearman = spearman(system_rank, human_rank)
    report.top_k_recall = top_k_recall(system_order, human_order, k=top_k) if scored else None
    traces = [o.traceability for o in outcomes if o.traceability is not None]
    report.span_traceability = sum(traces) / len(traces) if traces else 0.0

    parse_lat = [o.parse_latency_ms for o in outcomes if o.parse_ok]
    rank_lat = [o.rank_latency_ms for o in outcomes if o.rank_latency_ms > 0]
    report.parse_p50_ms, report.parse_p95_ms = percentile(parse_lat, 50), percentile(parse_lat, 95)
    report.rank_p50_ms, report.rank_p95_ms = percentile(rank_lat, 50), percentile(rank_lat, 95)
    report.prompt_tokens, report.completion_tokens = hook.prompt_tokens, hook.completion_tokens
    report.cost_yuan = compute_cost(candidate, hook.prompt_tokens, hook.completion_tokens)
    return report


# ---- Task 7: render_markdown / ocr_check / main ----

import argparse
import difflib
from dataclasses import asdict

from app.eval.metrics import THRESHOLDS
from app.parsing.extract_text import OcrUnavailable, extract_text


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def render_markdown(reports: list[ModelReport], *, n_samples: int, generated_at: str) -> str:
    lines = [
        f"生成时间：{generated_at} ｜ 样本数：{n_samples} ｜ prompt 版本：{PARSE_PROMPT_VERSION} / {RANK_PROMPT_VERSION} ｜ temperature=0",
        "",
        "| 候选 | 模型标识（响应侧） | fingerprint | 解析成功 | 字段准确率 | Spearman | Top-K 召回 | evidence 可定位率 | 精排成功 | 解析 P50/P95 ms | 精排 P50/P95 ms | tokens 入/出 | 成本（元） | 备注 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        f"| 门槛（D9） | | | | ≥{THRESHOLDS['field_accuracy'] * 100:.0f}% | ≥{THRESHOLDS['spearman']:.2f} | ≥{THRESHOLDS['top_k_recall'] * 100:.0f}% | {THRESHOLDS['span_traceability'] * 100:.0f}% | | | | | | |",
    ]
    for r in reports:
        if r.skipped:
            lines.append(f"| {r.name} | （未跑） | | | | | | | | | | | | {r.skip_reason} |")
            continue
        note = f"{len(r.errors)} 条错误，见 {r.audit_path}" if r.errors else ""
        lines.append(
            f"| {r.name} | {', '.join(r.response_models) or '—'} | {', '.join(r.fingerprints) or '—'} | {r.parse_ok}/{r.n_samples} | "
            f"{_pct(r.field_acc.overall)} | {_num(r.spearman)} | {_pct(r.top_k_recall)} | {_pct(r.span_traceability)} | {r.rank_ok}/{r.n_samples} | "
            f"{r.parse_p50_ms:.0f}/{r.parse_p95_ms:.0f} | {r.rank_p50_ms:.0f}/{r.rank_p95_ms:.0f} | {r.prompt_tokens}/{r.completion_tokens} | "
            f"{_num(r.cost_yuan, 4) if r.cost_yuan is not None else '未填价格'} | {note} |"
        )
    lines += ["", "逐字段准确率：", "", "| 候选 | " + " | ".join(FIELD_LABELS[f] for f in FIELD_NAMES) + " |", "|---|" + "---|" * len(FIELD_NAMES)]
    for r in reports:
        if r.skipped:
            continue
        lines.append(f"| {r.name} | " + " | ".join(_pct(r.field_acc.per_field[f]) for f in FIELD_NAMES) + " |")
    lines += ["", "注：Spearman 为 — 表示可计算样本 < 10（D9：不出结论）；成本按 ModelCandidate 里抄录的定价计算，未填即 —。"]
    return "\n".join(lines)


def ocr_check(sample_dir: Path, *, ocr: Callable[[Path], str] | None = None) -> list[dict]:
    """对每个有扫描件的样本跑 extract_text（走 OCR），与 txt 真值算 difflib 相似度（1.1／D14 实测用）。"""
    sample_dir = Path(sample_dir)
    truth = json.loads((sample_dir / "truth.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    for row in truth["samples"]:
        scan = row["files"].get("scan")
        if not scan:
            continue
        expected = (sample_dir / row["files"]["txt"]).read_text(encoding="utf-8")
        try:
            got = extract_text(sample_dir / scan, ocr=ocr)
        except OcrUnavailable as exc:
            rows.append({"sample_id": row["sample_id"], "error": f"OcrUnavailable: {exc}"})
            continue
        similarity = difflib.SequenceMatcher(None, "".join(expected.split()), "".join(got.text.split())).ratio()
        rows.append({"sample_id": row["sample_id"], "kind": got.kind, "readable": got.readable, "effective_chars": got.effective_chars, "similarity": round(similarity, 4)})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M2 U0 模型对比")
    parser.add_argument("--samples", type=Path, default=Path("data/eval/m2-pilot"))
    parser.add_argument("--models", default=",".join(c.name for c in CANDIDATES), help="逗号分隔的候选名")
    parser.add_argument("--out", type=Path, default=Path("data/eval/m2-pilot/compare-run.md"))
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--audit-dir", type=Path, default=Path("data/eval/m2-pilot/runs"))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--ocr-check", action="store_true", help="只跑扫描件 OCR 核对，不调 LLM")
    args = parser.parse_args(argv)

    if args.ocr_check:
        rows = ocr_check(args.samples)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    samples, rubric = load_samples(args.samples)
    wanted = {name.strip() for name in args.models.split(",") if name.strip()}
    unknown = wanted - {c.name for c in CANDIDATES}
    if unknown:
        parser.error(f"未知候选: {sorted(unknown)}；可选: {[c.name for c in CANDIDATES]}")
    reports = [
        evaluate_model(c, samples, rubric, gateway_factory=default_gateway_factory, audit_dir=args.audit_dir, top_k=args.top_k)
        for c in CANDIDATES
        if c.name in wanted
    ]
    generated_at = datetime.now(timezone.utc).isoformat()
    md = render_markdown(reports, n_samples=len(samples), generated_at=generated_at)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"generated_at": generated_at, "n_samples": len(samples), "reports": [asdict(r) for r in reports]}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
