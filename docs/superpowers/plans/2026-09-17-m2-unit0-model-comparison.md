# M2 U0 模型对比定型 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一条可重复运行的模型对比流程：对同一批脱敏／合成简历样本，按候选境内 LLM 分别跑「六字段抽取 → rubric 精排」，输出字段准确率、Spearman、Top-K 召回、span 可回溯率、P50/P95 延迟、token 与成本；并实测本地 CPU BGE-M3 召回耗时与 PaddleOCR 扫描件可用性，结论落 `docs/m2-model-comparison.md` 供 Shao Peishen 定型（tasks.md §1 U0，1.1–1.7）。

**Architecture:** 三层。① 可复用地基（U2/U4/U6 直接消费，⛔ 不在 U0 之外重写）：`app/schemas/resume_fields.py` 六字段 schema、`app/schemas/rank_result.py` 精排输出 schema、`app/parsing/spans.py` 分片与 quote 反查、`app/parsing/extract_text.py` 文件→文本、`app/eval/metrics.py` D9 四项指标、`app/eval/recall.py` numpy 余弦召回。② 对比脚本 `scripts/compare_models_m2.py`（复用 `app/llm/gateway.py::LLMGateway`，留痕走 `JsonlAuditHook` 落 JSONL）、`scripts/bench_bge_m3.py`、`scripts/smoke_m2_deps.py`、`scripts/gen_pilot_samples.py`（合成样本）。③ 文档 `docs/m2-model-comparison.md`。所有 LLM/embedding/OCR 依赖**懒加载＋可注入**，单测全部离线（假 client、假 OCR、假 embedder），⛔ 单测不碰网络与 2 GB 模型。

**Tech Stack:** Python 3.14、pydantic 2.13、pytest 8.3、`openai` SDK（经 `LLMGateway`）、numpy、pypdf、python-docx、reportlab、Pillow、PyMuPDF、PaddleOCR、FlagEmbedding（BGE-M3，本地 CPU）。

**Spec:** `openspec/changes/m2-resume-parse-and-rank/specs/candidate-ranking/spec.md`「模型对比定型的输入输出契约」「逐维评分带证据回指」；`specs/resume-parsing/spec.md`「首期字段抽取」「原文分片与字段回指」「扫描件与不可读文件」；`specs/eval-set-and-metrics/spec.md`「四项验收指标可一键计算」「评测集样本来源与访问控制」；`design.md` D4／D5／D7／D9／D14；WBS：`tasks.md` §1（1.1–1.7）。

## Global Constraints

- 铁律 2：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。本单元：`app/parsing/spans.py`、`app/eval/metrics.py`、`app/eval/recall.py`、`app/schemas/*` 全部纯函数——⛔ 不读文件、不读时钟、不写库；文件 IO 只在 `app/parsing/extract_text.py` 与 `scripts/*`。
- 铁律 3：所有 AI 评分必须持久化：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。本单元没有 `analysis_run` 写入方（U2/U4 才接 `RecorderAuditHook`），对比脚本用 `JsonlAuditHook` 把 `AuditHook.record` 的**每个参数**原样落 `data/eval/m2-pilot/runs/<model>/<ts>.jsonl`，rubric 快照与 prompt 版本随行写入；⛔ 不用 `NoopAuditHook` 跑对比。
- 铁律 4：每条 `criterion_score` 必须有 `evidence_ref`（回指简历原文 offset）。`evidence_ref` 为空不允许写入。本单元：`RankResult` 每维 `evidence` 必填；反查偏移失败 ⇒ 该次评分整体判不可用、不计分（spec「模型未给出证据位置」）。
- 铁律 5：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。供应商不提供带版本号快照时，必须从 API 响应里取回实际的 `model` 字段并持久化——配置里写的名字不算数，响应返回的才算。本单元：对比表「模型标识」列只写 `LLMCallMeta.response_model`；`system_fingerprint` 一并落表。
- 合规红线：AI 只做排序推荐，不做自动淘汰。本单元只产出指标与文档，⛔ 不写任何 `application` / `rejection_record`。
- 合规红线：模型全部走境内，简历数据不出境。候选模型只取 `scripts/compare_models.py::PROVIDER_CANDIDATES` 同源的境内供应商；BGE-M3 在本地 CPU 推理（design D7，2026-09-17 裁决），⛔ 不调境外 embedding API。
- 合规红线：绝不用历史录用结果做监督信号。合成样本的「设计排序」与将来的人工排序只用于离线指标，⛔ 不进任何 prompt 自动优化。
- 合规红线（评测集）：样本 MUST 只来自脱敏、历史离职或合成样本，MUST NOT 含真实在招简历；`data/eval/` 已在 `.gitignore`，⛔ 不提交任何样本文件。
- 部署约束 4：目标服务器是 Windows，没有 Docker。新依赖必须在 Windows venv 上 pip 可装（Task 4 冒烟脚本就是为此）。
- 排序维度只能取 `app/audit/criteria.py::CRITERION_KEY_WHITELIST`（`skill_match / experience_depth / project_relevance / domain_knowledge / education_fit / language_proficiency / role_seniority_fit`），白名单外的 key ⇒ `RankResult` 校验失败。

---

## 文件结构

| 文件 | 职责 | 后续消费方 |
|---|---|---|
| `app/schemas/resume_fields.py` | 六字段 schema（`{value, confidence, spans[]}`），`not_mentioned` 语义 | U2 3.5 |
| `app/schemas/rank_result.py` | 精排输出 schema，每维 `evidence` 必填、key 白名单 | U4 5.4 |
| `app/parsing/__init__.py` `app/parsing/spans.py` | 按行分片（带 offset）、quote 反查偏移、prompt 渲染 | U2 3.4/3.6 |
| `app/parsing/extract_text.py` | PDF 文本直抽／Word／扫描件 OCR（懒加载、可注入）、不可读判定 | U2 3.4 |
| `app/eval/__init__.py` `app/eval/metrics.py` | D9 四项指标与归一化、门槛常量 | U6 7.3 |
| `app/eval/recall.py` | numpy 余弦 top-K 与 recall@K | U4 5.2 |
| `scripts/smoke_m2_deps.py` | 依赖可装性／体积探针（Mac 与 `.51` Windows 各跑一次） | 1.1 |
| `scripts/gen_pilot_samples.py` | 20 份合成样本（txt/docx/pdf/扫描 pdf）＋ `truth.json` | 1.2 |
| `scripts/compare_models_m2.py` | 抽取＋精排对比、JSONL 留痕、指标、成本、markdown 表 | 1.3/1.4 |
| `scripts/bench_bge_m3.py` | 本地 CPU BGE-M3 单份耗时与 recall@K | 1.5 |
| `requirements-m2-u0.txt` | U0 新依赖锁定（独立文件，⛔ 不并入 `requirements.txt`，定型后由 U2/U4 挑选并入） | 1.1 |
| `docs/m2-model-comparison.md` | 环境／对比／决策三节 | 1.6/1.7 |
| `docs/eval/m2-pilot-README.md` | 样本目录规范、来源类别、留存期、禁止训练用途 | 7.5 |

## 与 tasks.md §1 的对应

| tasks | Task |
|---|---|
| 1.1 Windows 冒烟 | Task 4（脚本）＋ Task 8（⏸ 留步：需在 `.51` 同款 Windows 上实跑） |
| 1.2 样本 ≥20 | Task 5（合成样本；真实脱敏样本到位后同目录替换，见「待裁决」#2） |
| 1.3 对比脚本 | Task 6、Task 7 |
| 1.4 ≥3 家境内 LLM 实跑 | Task 8（有 key 的当场跑；无 key 的登记留步，见「待裁决」#3） |
| 1.5 embedding 召回实测 | Task 7（脚本）＋ Task 8（实跑） |
| 1.6 决策节 | Task 8 |
| 1.7 🔴 定型确认 | Shao Peishen 签认，本计划不做 |

## 建议拆段点（lane-dispatch「长 run-build 拆段」，Task = 8 ≥ 5）

- **第 1 条：Task 1–3**（纯函数地基，无新依赖，只需现有 venv）
- **第 2 条：Task 4–6**（引入 `requirements-m2-u0.txt` 的轻依赖：pypdf／python-docx／reportlab／Pillow／numpy；PaddleOCR／FlagEmbedding 只探针不强装）
- **第 3 条：Task 7–8**（CLI、bench、文档骨架、能跑的实跑 ＋ final review ＋ 合回 main ＋ 回勾 1.1–1.6 中已闭环的项）

拆段交接只靠分支与 commit hash（`docs/openers` 块内「前置」写上一条的 hash）。

---

### Task 1: 六字段 schema 与精排输出 schema

**Files:**
- Create: `app/schemas/resume_fields.py`
- Create: `app/schemas/rank_result.py`
- Test: `tests/test_resume_fields_schema.py`
- Test: `tests/test_rank_result_schema.py`

**Interfaces:**
- Consumes: `app/audit/criteria.py::CRITERION_KEY_WHITELIST`
- Produces:
  - `FIELD_NAMES: tuple[str, ...]`（六字段 key，顺序固定）
  - `SpanRef(span_id: int, quote: str, start: int | None, end: int | None)`
  - `TextField / NumberField / ListField / EducationField`，共同字段 `not_mentioned: bool`、`confidence: float ∈ [0,1]`、`spans: list[SpanRef]`
  - `ResumeFields(name: TextField, years_of_experience: NumberField, skills: ListField, companies: ListField, education: EducationField, expected_city: TextField)`，方法 `plain() -> dict[str, object]`（未提及 → `None`）
  - `CriterionEvidence(span_id, quote, start, end)`、`CriterionScoreOut(key, score ∈ [0,5], rationale, evidence)`、`RankResult(scores: list[CriterionScoreOut])`，方法 `total_score() -> float`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_resume_fields_schema.py
import pytest
from pydantic import ValidationError

from app.schemas.resume_fields import (
    FIELD_NAMES,
    EducationField,
    EducationValue,
    ListField,
    NumberField,
    ResumeFields,
    SpanRef,
    TextField,
)


def _full() -> ResumeFields:
    return ResumeFields(
        name=TextField(value="张明远", confidence=0.95, spans=[SpanRef(span_id=1, quote="张明远")]),
        years_of_experience=NumberField(value=5, confidence=0.8, spans=[SpanRef(span_id=3, quote="工作年限：5 年")]),
        skills=ListField(value=["C", "AUTOSAR"], confidence=0.9, spans=[SpanRef(span_id=9, quote="C、AUTOSAR")]),
        companies=ListField(value=["无锡华芯车控科技有限公司"], confidence=0.9, spans=[SpanRef(span_id=6, quote="无锡华芯车控科技有限公司")]),
        education=EducationField(value=EducationValue(degree="本科", school="江南大学"), confidence=0.9, spans=[SpanRef(span_id=4, quote="江南大学｜本科")]),
        expected_city=TextField(not_mentioned=True, confidence=1.0),
    )


def test_field_names_fixed_order():
    assert FIELD_NAMES == ("name", "years_of_experience", "skills", "companies", "education", "expected_city")


def test_plain_maps_not_mentioned_to_none_and_unwraps_values():
    plain = _full().plain()
    assert plain["expected_city"] is None
    assert plain["name"] == "张明远"
    assert plain["years_of_experience"] == 5
    assert plain["skills"] == ["C", "AUTOSAR"]
    assert plain["education"] == {"degree": "本科", "school": "江南大学"}


def test_not_mentioned_with_value_is_rejected():
    with pytest.raises(ValidationError, match="not_mentioned"):
        TextField(value="无锡", not_mentioned=True, confidence=0.5)


def test_empty_value_without_not_mentioned_is_rejected():
    with pytest.raises(ValidationError, match="not_mentioned"):
        TextField(confidence=0.5)
    with pytest.raises(ValidationError, match="not_mentioned"):
        ListField(value=[], confidence=0.5)


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        TextField(value="x", confidence=1.5)


def test_wrong_type_fails_whole_object():
    with pytest.raises(ValidationError):
        ResumeFields.model_validate(
            {
                **_full().model_dump(),
                "years_of_experience": {"value": "五年", "confidence": 0.5, "spans": []},
            }
        )


def test_json_schema_has_six_top_level_fields():
    props = ResumeFields.model_json_schema()["properties"]
    assert set(props) == set(FIELD_NAMES)
```

```python
# tests/test_rank_result_schema.py
import pytest
from pydantic import ValidationError

from app.schemas.rank_result import CriterionEvidence, CriterionScoreOut, RankResult


def _score(key: str, score: float = 3.0) -> CriterionScoreOut:
    return CriterionScoreOut(
        key=key,
        score=score,
        rationale="有 AUTOSAR 量产经验",
        evidence=CriterionEvidence(span_id=6, quote="负责 AUTOSAR CP 平台"),
    )


def test_total_score_is_mean_of_dimensions():
    result = RankResult(scores=[_score("skill_match", 4), _score("experience_depth", 2)])
    assert result.total_score() == 3.0


def test_key_outside_whitelist_rejected():
    with pytest.raises(ValidationError, match="白名单"):
        RankResult(scores=[_score("facial_expression")])


def test_duplicate_key_rejected():
    with pytest.raises(ValidationError, match="重复"):
        RankResult(scores=[_score("skill_match"), _score("skill_match")])


def test_evidence_is_required_and_quote_non_empty():
    with pytest.raises(ValidationError):
        CriterionScoreOut(key="skill_match", score=3, rationale="", evidence=None)
    with pytest.raises(ValidationError):
        CriterionEvidence(span_id=1, quote="")


def test_empty_scores_rejected():
    with pytest.raises(ValidationError):
        RankResult(scores=[])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_resume_fields_schema.py tests/test_rank_result_schema.py -q`
Expected: `ModuleNotFoundError: No module named 'app.schemas.resume_fields'`

- [ ] **Step 3: 实现**

```python
# app/schemas/resume_fields.py
"""
M2 首期六字段的抽取结果 schema（design D4／D5）。

每个字段是 {value, confidence, spans[]} 三元组；缺失 MUST 显式 `not_mentioned=True`，
⛔ 不允许用空值冒充"未提及"（spec「首期字段抽取」：缺失字段 MUST 显式记为"未提及"，MUST NOT 编造）。
`spans` 里的 start/end 由 app/parsing/spans.py::resolve_span_ref 反查填充，模型只给 span_id + quote。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

FIELD_NAMES: tuple[str, ...] = (
    "name",
    "years_of_experience",
    "skills",
    "companies",
    "education",
    "expected_city",
)

FIELD_LABELS: dict[str, str] = {
    "name": "姓名",
    "years_of_experience": "工作年限",
    "skills": "技能列表",
    "companies": "公司经历",
    "education": "教育（最高学历与院校）",
    "expected_city": "期望城市",
}


class SpanRef(BaseModel):
    span_id: int = Field(ge=1, description="原文分片编号（prompt 里的 [#N]）")
    quote: str = Field(min_length=1, description="从该分片逐字摘录的原文")
    start: int | None = Field(default=None, description="全文偏移，反查后填充")
    end: int | None = Field(default=None, description="全文偏移（不含），反查后填充")


class _FieldBase(BaseModel):
    not_mentioned: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    spans: list[SpanRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _value_matches_flag(self):
        value = getattr(self, "value")
        empty = value is None or value == []
        if self.not_mentioned and not empty:
            raise ValueError("not_mentioned=True 时 value 必须为空")
        if not self.not_mentioned and empty:
            raise ValueError("value 为空时必须显式 not_mentioned=True，不得留空冒充")
        return self


class TextField(_FieldBase):
    value: str | None = None


class NumberField(_FieldBase):
    value: float | None = None


class ListField(_FieldBase):
    value: list[str] = Field(default_factory=list)


class EducationValue(BaseModel):
    degree: str | None = Field(default=None, description="最高学历，如 本科／硕士")
    school: str | None = Field(default=None, description="院校")


class EducationField(_FieldBase):
    value: EducationValue | None = None


class ResumeFields(BaseModel):
    name: TextField
    years_of_experience: NumberField
    skills: ListField
    companies: ListField
    education: EducationField
    expected_city: TextField

    def plain(self) -> dict[str, object]:
        """六字段裸值（未提及 → None），给 app/eval/metrics.py 用。"""
        out: dict[str, object] = {}
        for name in FIELD_NAMES:
            fld = getattr(self, name)
            if fld.not_mentioned:
                out[name] = None
            elif isinstance(fld, EducationField):
                out[name] = fld.value.model_dump()
            else:
                out[name] = fld.value
        return out
```

```python
# app/schemas/rank_result.py
"""
精排（rubric 逐维打分）的 LLM 输出 schema（design D7、spec「逐维评分带证据回指」）。

evidence 在 schema 层强制而不是事后校验——事后校验只能丢结果，schema 强制能让模型
第一次就给出位置。start/end 由 spans.resolve_span_ref 反查填充；反查失败 ⇒ 整次评分不可用。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.audit.criteria import CRITERION_KEY_WHITELIST


class CriterionEvidence(BaseModel):
    span_id: int = Field(ge=1)
    quote: str = Field(min_length=1)
    start: int | None = None
    end: int | None = None


class CriterionScoreOut(BaseModel):
    key: str
    score: float = Field(ge=0.0, le=5.0)
    rationale: str = ""
    evidence: CriterionEvidence


class RankResult(BaseModel):
    scores: list[CriterionScoreOut] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_whitelisted_and_unique(self):
        keys = [s.key for s in self.scores]
        bad = [k for k in keys if k not in CRITERION_KEY_WHITELIST]
        if bad:
            raise ValueError(f"评分维度不在白名单: {bad}；已登记: {sorted(CRITERION_KEY_WHITELIST)}")
        if len(set(keys)) != len(keys):
            raise ValueError(f"评分维度重复: {keys}")
        return self

    def total_score(self) -> float:
        return sum(s.score for s in self.scores) / len(self.scores)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_resume_fields_schema.py tests/test_rank_result_schema.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add app/schemas/resume_fields.py app/schemas/rank_result.py tests/test_resume_fields_schema.py tests/test_rank_result_schema.py
git commit -m "feat(m2-u0): 六字段抽取 schema 与精排输出 schema（not_mentioned 显式、evidence 必填、维度白名单）"
```

---

### Task 2: 原文分片与 quote 反查偏移

**Files:**
- Create: `app/parsing/__init__.py`（空文件）
- Create: `app/parsing/spans.py`
- Test: `tests/test_parsing_spans.py`

**Interfaces:**
- Produces:
  - `TextSpan(span_id: int, start: int, end: int, text: str)`（frozen dataclass；不变式 `full_text[start:end] == text`）
  - `split_into_spans(text: str) -> list[TextSpan]`（按行，跳过空白行，`span_id` 从 1 起）
  - `locate_quote(span: TextSpan, quote: str) -> tuple[int, int] | None`（全文偏移；先精确 find，再 NFKC＋去空白归一化后反查并映射回原偏移）
  - `resolve_span_ref(spans: list[TextSpan], span_id: int, quote: str) -> tuple[int, int] | None`
  - `render_for_prompt(spans: list[TextSpan]) -> str`（每行 `[#N] 文本`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_parsing_spans.py
from app.parsing.spans import TextSpan, locate_quote, render_for_prompt, resolve_span_ref, split_into_spans

SAMPLE = "张明远\n\n  期望工作城市：无锡  \n技能\nC、AUTOSAR CP、CAN/LIN\n"


def test_split_skips_blank_lines_and_offsets_index_original_text():
    spans = split_into_spans(SAMPLE)
    assert [s.span_id for s in spans] == [1, 2, 3, 4]
    for s in spans:
        assert SAMPLE[s.start:s.end] == s.text
    assert spans[1].text == "期望工作城市：无锡"


def test_locate_exact_quote_returns_absolute_offsets():
    spans = split_into_spans(SAMPLE)
    loc = locate_quote(spans[1], "无锡")
    assert loc is not None
    assert SAMPLE[loc[0]:loc[1]] == "无锡"


def test_locate_tolerates_fullwidth_and_whitespace_differences():
    spans = split_into_spans(SAMPLE)
    loc = locate_quote(spans[3], "AUTOSAR  ＣＰ")  # 多空格 + 全角
    assert loc is not None
    assert SAMPLE[loc[0]:loc[1]] == "AUTOSAR CP"


def test_locate_returns_none_when_quote_absent_or_blank():
    spans = split_into_spans(SAMPLE)
    assert locate_quote(spans[0], "李四") is None
    assert locate_quote(spans[0], "   ") is None


def test_resolve_span_ref_unknown_id_is_none():
    spans = split_into_spans(SAMPLE)
    assert resolve_span_ref(spans, 99, "无锡") is None
    assert resolve_span_ref(spans, 2, "无锡") == locate_quote(spans[1], "无锡")


def test_render_for_prompt_numbers_each_span():
    rendered = render_for_prompt(split_into_spans("a\nb"))
    assert rendered == "[#1] a\n[#2] b"


def test_textspan_is_frozen():
    s = TextSpan(span_id=1, start=0, end=1, text="a")
    try:
        s.text = "b"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("TextSpan 必须不可变")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_parsing_spans.py -q`
Expected: `ModuleNotFoundError: No module named 'app.parsing'`

- [ ] **Step 3: 实现**

```python
# app/parsing/spans.py
"""
简历原文分片与字段回指（spec「原文分片与字段回指」，design D5／D7「quote 反查校正偏移」）。

纯函数：不读文件、不读时钟。分片粒度 = 非空行。行级分片让 quote 反查的搜索空间小、
偏移可核对（字段校对页按 offset 直接切字符串高亮，design D8）。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class TextSpan:
    span_id: int
    start: int
    end: int
    text: str


def split_into_spans(text: str) -> list[TextSpan]:
    spans: list[TextSpan] = []
    pos = 0
    span_id = 0
    for line in text.split("\n"):
        line_start = pos
        pos = line_start + len(line) + 1  # +1 是被 split 吃掉的 "\n"
        stripped = line.strip()
        if not stripped:
            continue
        lead = len(line) - len(line.lstrip())
        start = line_start + lead
        end = start + len(stripped)
        span_id += 1
        spans.append(TextSpan(span_id=span_id, start=start, end=end, text=stripped))
    return spans


def _index_map(text: str) -> tuple[str, list[int]]:
    """NFKC 逐字归一化并去空白，返回 (归一化串, 每个归一化字符对应的原始下标)。"""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace():
                continue
            chars.append(c)
            index.append(i)
    return "".join(chars), index


def locate_quote(span: TextSpan, quote: str) -> tuple[int, int] | None:
    q = quote.strip()
    if not q:
        return None
    i = span.text.find(q)
    if i >= 0:
        return span.start + i, span.start + i + len(q)
    norm_text, index = _index_map(span.text)
    norm_q, _ = _index_map(q)
    if not norm_q:
        return None
    j = norm_text.find(norm_q)
    if j < 0:
        return None
    first = index[j]
    last = index[j + len(norm_q) - 1]
    return span.start + first, span.start + last + 1


def resolve_span_ref(spans: list[TextSpan], span_id: int, quote: str) -> tuple[int, int] | None:
    for span in spans:
        if span.span_id == span_id:
            return locate_quote(span, quote)
    return None


def render_for_prompt(spans: list[TextSpan]) -> str:
    return "\n".join(f"[#{s.span_id}] {s.text}" for s in spans)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_parsing_spans.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add app/parsing/__init__.py app/parsing/spans.py tests/test_parsing_spans.py
git commit -m "feat(m2-u0): 原文行级分片与 quote 反查偏移（NFKC＋去空白容错）"
```

---

### Task 3: D9 四项指标与 numpy 余弦召回

**Files:**
- Create: `app/eval/__init__.py`（空文件）
- Create: `app/eval/metrics.py`
- Create: `app/eval/recall.py`
- Modify: `requirements.txt`（追加 `numpy`，U4 生产路径也要）
- Test: `tests/test_eval_metrics.py`
- Test: `tests/test_eval_recall.py`

**Interfaces:**
- Consumes: `FIELD_NAMES`（Task 1）、`TextSpan`（Task 2）
- Produces:
  - `THRESHOLDS = {"field_accuracy": 0.90, "spearman": 0.70, "top_k_recall": 0.85, "span_traceability": 1.0}`、`MIN_SAMPLES_FOR_RANK_METRICS = 10`
  - `normalize_text(v) -> str`、`normalize_company(v) -> str`、`jaccard(a: set, b: set) -> float`
  - `field_correct(field: str, predicted, truth) -> bool`
  - `FieldAccuracy(per_field: dict[str, float], overall: float, n: int)`；`field_accuracy(predictions: dict[str, dict], truths: dict[str, dict]) -> FieldAccuracy`
  - `spearman(system_rank: dict[str, int], human_rank: dict[str, int], *, min_samples=10) -> float | None`
  - `top_k_recall(system_order: list[str], human_order: list[str], *, k=10) -> float`
  - `span_traceability(refs: Iterable[tuple[int, int | None, int | None]], spans: list[TextSpan]) -> float`
  - `recall.cosine_top_k(query: np.ndarray, docs: np.ndarray, ids: list[str], k: int) -> list[tuple[str, float]]`
  - `recall.recall_at_k(recalled_ids: list[str], human_order: list[str], *, k_truth=10) -> float`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_eval_metrics.py
import math

from app.eval.metrics import (
    MIN_SAMPLES_FOR_RANK_METRICS,
    THRESHOLDS,
    field_accuracy,
    field_correct,
    normalize_company,
    spearman,
    span_traceability,
    top_k_recall,
)
from app.parsing.spans import split_into_spans


def test_thresholds_match_design_d9():
    assert THRESHOLDS == {"field_accuracy": 0.90, "spearman": 0.70, "top_k_recall": 0.85, "span_traceability": 1.0}
    assert MIN_SAMPLES_FOR_RANK_METRICS == 10


def test_company_suffix_and_fullwidth_do_not_count_as_error():
    assert normalize_company("上海某某科技有限公司") == normalize_company("上海某某科技")
    assert field_correct("companies", ["上海某某科技有限公司"], ["上海 某某科技"])


def test_years_rounded_before_compare():
    assert field_correct("years_of_experience", 4.6, 5)
    assert not field_correct("years_of_experience", 3, 5)


def test_skills_jaccard_threshold_0_8():
    truth = ["C", "AUTOSAR", "CAN", "LIN", "UDS"]
    assert field_correct("skills", ["c", "AUTOSAR", "CAN", "LIN", "UDS"], truth)  # 5/5
    assert field_correct("skills", ["C", "AUTOSAR", "CAN", "LIN"], truth)  # 4/5 = 0.8
    assert not field_correct("skills", ["C", "AUTOSAR", "CAN"], truth)  # 3/5


def test_education_compares_degree_and_school():
    assert field_correct("education", {"degree": "本科", "school": "江南 大学"}, {"degree": "本科", "school": "江南大学"})
    assert not field_correct("education", {"degree": "硕士", "school": "江南大学"}, {"degree": "本科", "school": "江南大学"})


def test_not_mentioned_counts_correct_only_when_both_none():
    assert field_correct("expected_city", None, None)
    assert not field_correct("expected_city", "无锡", None)
    assert not field_correct("expected_city", None, "无锡")


def test_field_accuracy_overall_is_mean_of_six_fields():
    truths = {"S1": {"name": "张三", "years_of_experience": 5, "skills": ["C"], "companies": ["A公司"], "education": {"degree": "本科", "school": "X"}, "expected_city": None}}
    preds = {"S1": {**truths["S1"], "name": "李四"}}
    acc = field_accuracy(preds, truths)
    assert acc.n == 1
    assert acc.per_field["name"] == 0.0
    assert acc.per_field["skills"] == 1.0
    assert math.isclose(acc.overall, 5 / 6)


def test_field_accuracy_ignores_samples_without_prediction():
    truths = {"S1": {"name": "a"}, "S2": {"name": "b"}}
    acc = field_accuracy({"S1": {"name": "a"}}, truths)
    assert acc.n == 1


def test_spearman_perfect_and_reversed():
    ids = [f"S{i}" for i in range(10)]
    human = {s: i + 1 for i, s in enumerate(ids)}
    assert spearman(human, human) == 1.0
    reversed_rank = {s: 10 - i for i, s in enumerate(ids)}
    assert math.isclose(spearman(reversed_rank, human), -1.0)


def test_spearman_none_below_min_samples():
    ids = [f"S{i}" for i in range(9)]
    human = {s: i + 1 for i, s in enumerate(ids)}
    assert spearman(human, human) is None


def test_top_k_recall_uses_half_n_when_small():
    human = ["a", "b", "c", "d", "e", "f"]  # n=6 ⇒ k=3
    assert top_k_recall(["a", "b", "x", "c"], human) == 2 / 3
    assert top_k_recall(["a", "b", "c"], human) == 1.0


def test_top_k_recall_default_k_10():
    human = [f"h{i}" for i in range(20)]
    system = [f"h{i}" for i in range(5)] + [f"z{i}" for i in range(15)]
    assert top_k_recall(system, human) == 0.5


def test_span_traceability_requires_existing_span_and_in_bounds():
    spans = split_into_spans("abc\ndef")
    assert span_traceability([(1, 0, 3), (2, 4, 7)], spans) == 1.0
    assert span_traceability([(1, 0, 3), (2, 4, 9)], spans) == 0.5  # 越界
    assert span_traceability([(1, None, None)], spans) == 0.0  # 未反查到
    assert span_traceability([(9, 0, 1)], spans) == 0.0  # 分片不存在
    assert span_traceability([], spans) == 0.0
```

```python
# tests/test_eval_recall.py
import numpy as np

from app.eval.recall import cosine_top_k, recall_at_k


def test_cosine_top_k_orders_by_similarity():
    query = np.array([1.0, 0.0])
    docs = np.array([[0.0, 1.0], [1.0, 0.0], [0.7, 0.7]])
    top = cosine_top_k(query, docs, ["a", "b", "c"], k=2)
    assert [t[0] for t in top] == ["b", "c"]
    assert abs(top[0][1] - 1.0) < 1e-9


def test_cosine_top_k_handles_k_larger_than_docs_and_zero_vectors():
    query = np.array([1.0, 0.0])
    docs = np.array([[0.0, 0.0], [1.0, 0.0]])
    top = cosine_top_k(query, docs, ["zero", "b"], k=5)
    assert [t[0] for t in top] == ["b", "zero"]
    assert top[1][1] == 0.0


def test_recall_at_k_against_human_top():
    human = [f"h{i}" for i in range(12)]
    recalled = ["h0", "h1", "h2", "h3", "h4", "z"]
    assert recall_at_k(recalled, human) == 0.5  # 人工前 10 中 5 个被召回
    assert recall_at_k([], human) == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/pip install "numpy==2.5.3" && venv/bin/python -m pytest tests/test_eval_metrics.py tests/test_eval_recall.py -q`
Expected: `ModuleNotFoundError: No module named 'app.eval'`
（`numpy==2.5.3` 2026-09-17 实测在 cp314 的 macOS arm64 与 win_amd64 都有 wheel。）

- [ ] **Step 3: 实现**

```python
# app/eval/metrics.py
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
        return jaccard({normalize_text(x) for x in predicted}, {normalize_text(x) for x in truth}) >= 0.8
    if field == "companies":
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
```

```python
# app/eval/recall.py
"""
召回段的进程内向量比对（design D7「向量存储」：SQLite BLOB + numpy 全量 cosine，⛔ 不引入 pgvector/FAISS）。
纯函数；embedding 的产生在调用方。
"""
from __future__ import annotations

import numpy as np


def cosine_top_k(query: np.ndarray, docs: np.ndarray, ids: list[str], k: int) -> list[tuple[str, float]]:
    if len(ids) == 0:
        return []
    q = np.asarray(query, dtype=np.float64)
    d = np.asarray(docs, dtype=np.float64)
    qn = np.linalg.norm(q)
    dn = np.linalg.norm(d, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = np.where((dn > 0) & (qn > 0), d @ q / (dn * qn), 0.0)
    order = np.argsort(-sims, kind="stable")[: max(k, 0)]
    return [(ids[i], float(sims[i])) for i in order]


def recall_at_k(recalled_ids: list[str], human_order: list[str], *, k_truth: int = 10) -> float:
    truth = set(human_order[:k_truth])
    if not truth:
        return 0.0
    return len(truth & set(recalled_ids)) / len(truth)
```

`requirements.txt` 末尾追加一行：

```
numpy==2.5.3
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_eval_metrics.py tests/test_eval_recall.py -q`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add app/eval/__init__.py app/eval/metrics.py app/eval/recall.py tests/test_eval_metrics.py tests/test_eval_recall.py requirements.txt
git commit -m "feat(m2-u0): D9 四项指标口径与 numpy 余弦召回（U6/U4 共用）"
```

---

### Task 4: 文件→文本（PDF 直抽／Word／扫描件 OCR）＋ U0 依赖锁定 ＋ 可装性探针

**Files:**
- Create: `app/parsing/extract_text.py`
- Create: `requirements-m2-u0.txt`
- Create: `scripts/smoke_m2_deps.py`
- Test: `tests/test_extract_text.py`
- Test: `tests/test_smoke_m2_deps.py`

**Interfaces:**
- Produces:
  - `MIN_EFFECTIVE_CHARS = 50`；`UnsupportedFileType(ValueError)`；`OcrUnavailable(RuntimeError)`
  - `ExtractedText(text: str, kind: Literal["pdf_text","pdf_scan","docx"], effective_chars: int, readable: bool)`
  - `effective_char_count(text: str) -> int`
  - `extract_docx(path: Path) -> str`、`extract_pdf_text(path: Path) -> str`、`ocr_pdf(path: Path, *, lang="ch") -> str`（懒加载 PaddleOCR＋PyMuPDF，缺包抛 `OcrUnavailable`）
  - `extract_text(path: Path, *, ocr: Callable[[Path], str] | None = None, min_effective_chars: int = MIN_EFFECTIVE_CHARS) -> ExtractedText`
  - `smoke_m2_deps.probe(dist_name: str, module_name: str) -> dict`、`probe_all(deps) -> dict`、`render_markdown(report) -> str`、`DEPS: list[tuple[str, str]]`

- [ ] **Step 1: 锁定依赖并校正版本**

写 `requirements-m2-u0.txt`：

```
# M2 U0 对比与冒烟用的新依赖（tasks 1.1）。⛔ 不并入 requirements.txt——定型后由 U2/U4 只把
# 真正进生产的挑进去。版本号 2026-09-17 已实测：下面六项在 cp314 的 macosx_arm64 与 win_amd64
# 都有 wheel（pip download --only-binary=:all: --python-version 3.14 --platform win_amd64 逐项 OK）。
# ---- 轻依赖（单测需要）----
numpy==2.5.3
pypdf==6.19.0
python-docx==1.2.0
reportlab==5.0.1
pillow==12.3.0
pymupdf==1.28.2
# ---- 重依赖（不随上面一起装；单测懒加载、缺包即 skip）----
# PaddleOCR（design D14）。⚠️ 2026-09-17 实测：paddlepaddle 最新 3.4.0 只有 cp313 wheel，
# cp314 在 PyPI 与 https://www.paddlepaddle.org.cn/packages/stable/cpu/ 都没有——项目锁 Python 3.14，
# 项目 venv 里装不上。处置见计划「待裁决」#6；未裁决前按 D14 退路（扫描件进「不可读」队列＋技术债）。
# paddlepaddle==3.4.0   # cp313 only（实测）
paddleocr==3.7.0        # 纯 py wheel 可装，但没有 paddlepaddle 跑不起来
# BGE-M3 本地 CPU（design D7）。FlagEmbedding 拉 torch（cp314 win_amd64 有 2.14.0、mac 有 2.11.0）与 ~2.2 GB 模型权重。
FlagEmbedding==1.4.2
```

Run（版本 2026-09-17 已校正，本步只需复核并装轻依赖）:
```bash
for p in numpy==2.5.3 pypdf==6.19.0 python-docx==1.2.0 reportlab==5.0.1 pillow==12.3.0 pymupdf==1.28.2; do
  venv/bin/pip download --only-binary=:all: --python-version 3.14 --platform win_amd64 --no-deps "$p" -d /tmp/wheels-u0 -q && echo "OK  $p" || echo "NO  $p  ← 换最近有 cp314 win_amd64 wheel 的版本并改文件";
done
venv/bin/pip install numpy==2.5.3 pypdf==6.19.0 python-docx==1.2.0 reportlab==5.0.1 pillow==12.3.0 pymupdf==1.28.2
```
Expected: 六行全 `OK`；重依赖三项**不在本步装**。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_extract_text.py
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from app.parsing.extract_text import (
    MIN_EFFECTIVE_CHARS,
    OcrUnavailable,
    UnsupportedFileType,
    effective_char_count,
    extract_text,
)

LONG_ASCII = "Resume of Test Person. Skills: C, AUTOSAR CP, CAN, LIN, UDS, Bootloader, MCAL, ISO 26262 functional safety."


def _text_pdf(path: Path, text: str) -> Path:
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, text)
    c.save()
    return path


def _scan_pdf(path: Path) -> Path:
    Image.new("RGB", (600, 200), "white").save(str(path), "PDF")
    return path


def _docx(path: Path, lines: list[str]) -> Path:
    import docx

    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "技能"
    table.rows[0].cells[1].text = "C、AUTOSAR"
    document.save(str(path))
    return path


def test_effective_char_count_ignores_whitespace():
    assert effective_char_count(" a b\n c ") == 3
    assert MIN_EFFECTIVE_CHARS == 50


def test_text_pdf_is_extracted_directly_without_ocr(tmp_path):
    def boom(_):
        raise AssertionError("文本型 PDF 不该走 OCR")

    out = extract_text(_text_pdf(tmp_path / "a.pdf", LONG_ASCII), ocr=boom)
    assert out.kind == "pdf_text"
    assert out.readable is True
    assert "AUTOSAR" in out.text


def test_scan_pdf_routes_to_injected_ocr(tmp_path):
    out = extract_text(_scan_pdf(tmp_path / "s.pdf"), ocr=lambda _: LONG_ASCII)
    assert out.kind == "pdf_scan"
    assert out.readable is True
    assert out.text == LONG_ASCII


def test_scan_pdf_with_empty_ocr_is_unreadable(tmp_path):
    out = extract_text(_scan_pdf(tmp_path / "s.pdf"), ocr=lambda _: "  \n ")
    assert out.kind == "pdf_scan"
    assert out.readable is False
    assert out.effective_chars == 0


def test_ocr_unavailable_propagates(tmp_path):
    def missing(_):
        raise OcrUnavailable("paddleocr 未安装")

    with pytest.raises(OcrUnavailable):
        extract_text(_scan_pdf(tmp_path / "s.pdf"), ocr=missing)


def test_docx_paragraphs_and_tables_are_joined(tmp_path):
    lines = ["张明远", "期望工作城市：无锡"] + ["填充行" * 10] * 3
    out = extract_text(_docx(tmp_path / "r.docx", lines))
    assert out.kind == "docx"
    assert out.readable is True
    assert "期望工作城市：无锡" in out.text
    assert "技能\tC、AUTOSAR" in out.text


def test_unsupported_suffix_rejected(tmp_path):
    p = tmp_path / "r.txt"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(UnsupportedFileType):
        extract_text(p)


def test_default_ocr_raises_unavailable_when_paddle_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"paddleocr", "pymupdf", "fitz"}:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(OcrUnavailable):
        extract_text(_scan_pdf(tmp_path / "s.pdf"))
```

```python
# tests/test_smoke_m2_deps.py
from scripts.smoke_m2_deps import DEPS, probe, probe_all, render_markdown


def test_probe_installed_package_reports_version_and_size():
    result = probe("pytest", "pytest")
    assert result["ok"] is True
    assert result["version"]
    assert result["size_mb"] is None or result["size_mb"] >= 0


def test_probe_missing_package_reports_error_without_raising():
    result = probe("no-such-dist-0917af", "no_such_module_0917af")
    assert result["ok"] is False
    assert "no_such_module_0917af" in result["error"]


def test_deps_cover_design_choices():
    dists = {d for d, _ in DEPS}
    assert {"paddleocr", "paddlepaddle", "FlagEmbedding", "pypdf", "python-docx", "numpy"} <= dists


def test_render_markdown_has_one_row_per_probe():
    report = probe_all([("pytest", "pytest"), ("no-such-dist-0917af", "no_such_module_0917af")])
    md = render_markdown(report)
    assert "| pytest |" in md
    assert "| no-such-dist-0917af |" in md
    assert report["python"] and report["platform"]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_extract_text.py tests/test_smoke_m2_deps.py -q`
Expected: `ModuleNotFoundError: No module named 'app.parsing.extract_text'`

- [ ] **Step 4: 实现**

```python
# app/parsing/extract_text.py
"""
简历文件 → 文本（spec「扫描件与不可读文件」，design D14）。

文本型 PDF 直抽（pypdf）、Word 走 python-docx、扫描件走 PaddleOCR（懒加载；缺包抛
OcrUnavailable，由调用方决定进"不可读"队列还是报错）。OCR 可注入，单测不碰 Paddle。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MIN_EFFECTIVE_CHARS = 50
SUPPORTED_SUFFIXES = (".pdf", ".docx")


class UnsupportedFileType(ValueError):
    """只收 pdf/docx（design D1 类型白名单）。"""


class OcrUnavailable(RuntimeError):
    """PaddleOCR / PyMuPDF 未安装或初始化失败。"""


@dataclass(frozen=True)
class ExtractedText:
    text: str
    kind: Literal["pdf_text", "pdf_scan", "docx"]
    effective_chars: int
    readable: bool


def effective_char_count(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def extract_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _paddle_lines(result: object) -> list[str]:
    """兼容 PaddleOCR 2.x（[[box, (text, score)], ...]）与 3.x（含 rec_texts 的结果对象）两种返回形态。"""
    lines: list[str] = []
    for page in result or []:
        rec_texts = None
        if isinstance(page, dict):
            rec_texts = page.get("rec_texts")
        elif hasattr(page, "get"):
            try:
                rec_texts = page.get("rec_texts")
            except Exception:
                rec_texts = None
        if rec_texts is not None:
            lines.extend(str(t) for t in rec_texts)
            continue
        for item in page or []:
            try:
                lines.append(str(item[1][0]))
            except (TypeError, IndexError, KeyError):
                continue
    return lines


def ocr_pdf(path: Path, *, lang: str = "ch") -> str:
    try:
        import numpy as np
        from paddleocr import PaddleOCR

        try:
            import pymupdf as fitz  # 1.24+ 的正式模块名；`import fitz` 已标 deprecated
        except ImportError:
            import fitz
    except ImportError as exc:
        raise OcrUnavailable(f"PaddleOCR/PyMuPDF 未安装: {exc}") from exc
    try:
        engine = PaddleOCR(lang=lang)
    except Exception as exc:  # 模型下载失败、DLL 缺失等，都属"OCR 不可用"
        raise OcrUnavailable(f"PaddleOCR 初始化失败: {exc!r}") from exc
    lines: list[str] = []
    with fitz.open(str(path)) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                image = image[:, :, :3]
            result = engine.ocr(image)
            lines.extend(_paddle_lines(result))
    return "\n".join(lines)


def extract_text(
    path: Path,
    *,
    ocr: Callable[[Path], str] | None = None,
    min_effective_chars: int = MIN_EFFECTIVE_CHARS,
) -> ExtractedText:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        text = extract_docx(path)
        kind: Literal["pdf_text", "pdf_scan", "docx"] = "docx"
    elif suffix == ".pdf":
        text = extract_pdf_text(path)
        kind = "pdf_text"
        if effective_char_count(text) < min_effective_chars:
            kind = "pdf_scan"
            text = (ocr or ocr_pdf)(path)
    else:
        raise UnsupportedFileType(f"只支持 {SUPPORTED_SUFFIXES}，收到 {path.name}")
    n = effective_char_count(text)
    return ExtractedText(text=text, kind=kind, effective_chars=n, readable=n >= min_effective_chars)
```

```python
# scripts/smoke_m2_deps.py
"""
U0 依赖可装性／体积探针（tasks 1.1）。在 Mac 与 `.51` 同款 Windows venv 各跑一次，
把输出贴进 docs/m2-model-comparison.md「环境」节。只 import、不下载模型、不调用。

用法：python -m scripts.smoke_m2_deps --json data/eval/m2-pilot/smoke-<平台>.json
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import json
import platform
import sys
import time
from pathlib import Path

DEPS: list[tuple[str, str]] = [
    ("numpy", "numpy"),
    ("pypdf", "pypdf"),
    ("python-docx", "docx"),
    ("reportlab", "reportlab"),
    ("pillow", "PIL"),
    ("pymupdf", "pymupdf"),
    ("paddlepaddle", "paddle"),
    ("paddleocr", "paddleocr"),
    ("torch", "torch"),
    ("FlagEmbedding", "FlagEmbedding"),
]


def _dist_size_mb(dist_name: str) -> float | None:
    try:
        files = md.files(dist_name)
    except md.PackageNotFoundError:
        return None
    if not files:
        return None
    total = 0
    for f in files:
        try:
            total += f.locate().stat().st_size
        except OSError:
            continue
    return round(total / 1e6, 1)


def _version(dist_name: str, module: object) -> str | None:
    try:
        return md.version(dist_name)
    except md.PackageNotFoundError:
        return getattr(module, "__version__", None)


def probe(dist_name: str, module_name: str) -> dict:
    started = time.monotonic()
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # ImportError、DLL 加载错、初始化异常都要记下来
        return {"dist": dist_name, "module": module_name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "dist": dist_name,
        "module": module_name,
        "ok": True,
        "version": _version(dist_name, module),
        "size_mb": _dist_size_mb(dist_name),
        "import_ms": round((time.monotonic() - started) * 1000),
    }


def probe_all(deps: list[tuple[str, str]]) -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "results": [probe(d, m) for d, m in deps],
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"平台：`{report['platform']}` ｜ Python {report['python']}",
        "",
        "| 包 | 可导入 | 版本 | 体积 MB | import 耗时 ms | 错误 |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        if r["ok"]:
            lines.append(f"| {r['dist']} | ✅ | {r['version']} | {r['size_mb']} | {r['import_ms']} | |")
        else:
            lines.append(f"| {r['dist']} | ❌ | | | | {r['error']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="U0 依赖探针")
    parser.add_argument("--json", type=Path, default=None, help="同时把结果写成 JSON")
    args = parser.parse_args(argv)
    report = probe_all(DEPS)
    print(render_markdown(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_extract_text.py tests/test_smoke_m2_deps.py -q`
Expected: `12 passed`

- [ ] **Step 6: Commit**

```bash
git add app/parsing/extract_text.py requirements-m2-u0.txt scripts/smoke_m2_deps.py tests/test_extract_text.py tests/test_smoke_m2_deps.py
git commit -m "feat(m2-u0): 文件→文本（pypdf/python-docx/PaddleOCR 懒加载可注入）＋ U0 依赖锁定与可装性探针"
```

---

### Task 5: 合成样本生成器（tasks 1.2 的替身）

**Files:**
- Create: `scripts/gen_pilot_samples.py`
- Test: `tests/test_gen_pilot_samples.py`

**Interfaces:**
- Consumes: `FIELD_NAMES`（Task 1）
- Produces:
  - `SEED = 20260917`、`RUBRIC: dict`（底层软件工程师 5 维，key 全在白名单）
  - `Profile` dataclass；`build_profiles(n: int = 20, seed: int = SEED) -> list[Profile]`（确定性）
  - `render_text(profile: Profile) -> str`；`truth_fields(profile) -> dict`（六字段真值，未提及 → `None`）
  - `find_cjk_font() -> Path | None`
  - `write_all(out_dir: Path, profiles: list[Profile], *, font: Path | None) -> dict`（写 txt/docx/pdf/_scan.pdf 与 `truth.json`，返回 truth 字典）
  - `truth.json` 结构：`{"generated_at", "seed", "sample_class": "synthetic", "rubric": RUBRIC, "samples": [{"sample_id", "files": {"txt","docx","pdf","scan"|null}, "fields": {...六字段}, "human_rank": int, "designed_fit": float}]}`

**为什么是合成**：仓库与 `data/` 里没有任何脱敏简历库存（2026-09-17 实查），无人值守下拿不到真人样本；合成样本无个人信息，能验证管线、schema 守字段率、span 可定位率与延迟成本，**不能替代 D9 验收**（见「待裁决」#2）。真实脱敏样本到位后用同一目录布局与 `truth.json` 结构替换，脚本零改动。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gen_pilot_samples.py
import json
import re

from app.audit.criteria import CRITERION_KEY_WHITELIST
from app.schemas.resume_fields import FIELD_NAMES
from scripts.gen_pilot_samples import RUBRIC, SEED, build_profiles, render_text, truth_fields, write_all


def test_build_profiles_is_deterministic_and_unique():
    a = build_profiles(20, seed=SEED)
    b = build_profiles(20, seed=SEED)
    assert [p.sample_id for p in a] == [f"S{i:02d}" for i in range(1, 21)]
    assert [truth_fields(p) for p in a] == [truth_fields(p) for p in b]
    assert len({p.name for p in a}) == 20


def test_human_rank_is_permutation_and_follows_designed_fit():
    profiles = build_profiles(20)
    ranks = sorted(p.human_rank for p in profiles)
    assert ranks == list(range(1, 21))
    ordered = sorted(profiles, key=lambda p: p.human_rank)
    fits = [p.fit for p in ordered]
    assert fits == sorted(fits, reverse=True)


def test_rubric_keys_in_whitelist():
    assert {c["key"] for c in RUBRIC["criteria"]} <= CRITERION_KEY_WHITELIST
    assert RUBRIC["profile_text"]


def test_render_text_contains_truth_and_no_phone_like_digits():
    for p in build_profiles(20):
        text = render_text(p)
        truth = truth_fields(p)
        assert truth["name"] in text
        for company in truth["companies"]:
            assert company in text
        assert re.search(r"\d{11}", text) is None
        assert "@" not in text
        if truth["expected_city"] is None:
            assert "期望工作城市" not in text
        else:
            assert f"期望工作城市：{truth['expected_city']}" in text


def test_truth_fields_has_six_keys_and_some_not_mentioned_cities():
    profiles = build_profiles(20)
    for p in profiles:
        assert set(truth_fields(p)) == set(FIELD_NAMES)
    assert any(truth_fields(p)["expected_city"] is None for p in profiles)
    assert any(p.explicit_years for p in profiles) and any(not p.explicit_years for p in profiles)


def test_write_all_produces_files_and_truth_json(tmp_path):
    profiles = build_profiles(3)
    truth = write_all(tmp_path, profiles, font=None)  # font=None ⇒ 不产扫描件
    data = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    assert data == truth
    assert data["sample_class"] == "synthetic"
    assert len(data["samples"]) == 3
    for row in data["samples"]:
        assert (tmp_path / row["files"]["txt"]).exists()
        assert (tmp_path / row["files"]["docx"]).exists()
        assert (tmp_path / row["files"]["pdf"]).exists()
        assert row["files"]["scan"] is None
        assert row["human_rank"] in {1, 2, 3}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_gen_pilot_samples.py -q`
Expected: `ModuleNotFoundError: No module named 'scripts.gen_pilot_samples'`

- [ ] **Step 3: 实现**

```python
# scripts/gen_pilot_samples.py
"""
合成简历样本生成器（tasks 1.2 的替身；真实脱敏样本到位后同目录替换，见计划「待裁决」#2）。

样本全部虚构：姓名／公司／院校来自本文件的固定池，不对应任何真人；⛔ 不含手机号／邮箱／身份证。
输出到 data/eval/m2-pilot/（已在 .gitignore）：<id>.txt / .docx / .pdf / _scan.pdf ＋ truth.json。
「人工排序」在合成样本上 = 设计的匹配度排序（designed_fit），只用于离线指标，⛔ 不进任何 prompt 优化。

用法：python -m scripts.gen_pilot_samples --out data/eval/m2-pilot --n 20
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SEED = 20260917

SURNAMES = ["赵", "钱", "孙", "李", "周", "吴", "郑", "王", "冯", "陈", "褚", "卫"]
GIVEN_NAMES = ["明远", "子墨", "思远", "若曦", "浩然", "雨桐", "嘉豪", "欣怡", "俊杰", "语嫣", "宇航", "梓涵"]
CITIES = ["无锡", "苏州", "上海", "南京", "常州"]
SCHOOLS = [
    ("江南大学", "本科"),
    ("南京理工大学", "硕士"),
    ("东南大学", "本科"),
    ("合肥工业大学", "本科"),
    ("哈尔滨工业大学", "硕士"),
    ("苏州大学", "本科"),
    ("常州信息职业技术学院", "大专"),
]
MAJORS = ["车辆工程", "电子信息工程", "自动化", "计算机科学与技术", "测控技术与仪器"]
COMPANIES = [
    "无锡华芯车控科技有限公司",
    "苏州澜盾汽车电子有限公司",
    "上海泓驰智能装备股份有限公司",
    "南京擎川电子有限公司",
    "常州锐驰微电子有限公司",
    "合肥启程新能源科技有限公司",
    "杭州云栈软件有限公司",
]
TITLES_CORE = ["嵌入式软件工程师", "底层软件工程师", "BSP 工程师"]
TITLES_OTHER = ["测试工程师", "硬件工程师", "应用软件工程师", "Java 开发工程师"]
CORE_SKILLS = ["C", "AUTOSAR CP", "CAN", "LIN", "UDS", "MCAL", "Bootloader", "英飞凌 TC3xx", "NXP S32K", "ISO 26262"]
OTHER_SKILLS = ["Python", "Java", "Linux", "Qt", "MATLAB/Simulink", "PLC", "SQL", "前端开发", "Android", "Docker"]
DUTIES = [
    "负责 ECU 底层驱动开发与 MCAL 配置，参与两个量产项目的 SOP。",
    "负责 CAN/LIN 通信协议栈移植与 UDS 诊断服务实现。",
    "负责 Bootloader 开发与刷写流程验证。",
    "负责整车控制器应用层功能开发与台架测试。",
    "负责产线测试工装软件开发与维护。",
    "负责后台服务开发与数据库设计。",
    "负责嵌入式 Linux 板级支持包移植。",
]
SELF_EVAL = [
    "熟悉汽车电子开发流程，具备较强的问题定位能力。",
    "工作认真负责，能独立承担模块开发。",
    "有良好的团队协作与文档习惯。",
]

RUBRIC: dict = {
    "job_title": "底层软件工程师",
    "criteria": [
        {"key": "skill_match", "description": "C／AUTOSAR CP／MCAL／Bootloader／CAN-LIN-UDS 等底层技能覆盖度"},
        {"key": "experience_depth", "description": "嵌入式底层开发年限与深度"},
        {"key": "project_relevance", "description": "汽车电子量产项目经历与本岗相关性"},
        {"key": "domain_knowledge", "description": "汽车 ECU、功能安全等领域知识"},
        {"key": "education_fit", "description": "本科及以上、车辆／电子／自动化相关专业"},
    ],
    "profile_text": (
        "岗位：底层软件工程师（汽车 ECU）。要求：本科及以上，车辆／电子／自动化相关专业；"
        "3 年以上嵌入式 C 开发；熟悉 AUTOSAR CP、MCAL、Bootloader；熟悉 CAN/LIN/UDS；"
        "有量产项目经历者优先；了解 ISO 26262。工作地点无锡。"
    ),
}


@dataclass(frozen=True)
class Job:
    company: str
    start_year: int
    end_year: int
    title: str
    duty: str


@dataclass(frozen=True)
class Profile:
    sample_id: str
    name: str
    city: str | None
    school: str
    degree: str
    major: str
    grad_year: int
    jobs: tuple[Job, ...]
    skills: tuple[str, ...]
    years: int
    explicit_years: bool
    self_eval: str
    fit: float
    human_rank: int


def _designed_fit(core_n: int, years: int, degree: str, jobs: tuple[Job, ...]) -> float:
    fit = core_n * 1.0 + min(years, 8) * 0.3
    if degree in ("本科", "硕士"):
        fit += 0.5
    if any(j.title in TITLES_CORE for j in jobs):
        fit += 0.4
    return round(fit, 2)


def build_profiles(n: int = 20, seed: int = SEED) -> list[Profile]:
    rng = random.Random(seed)
    names = rng.sample([s + g for s in SURNAMES for g in GIVEN_NAMES], n)
    drafts = []
    for i in range(n):
        core_n = rng.randint(0, 6)
        skills = rng.sample(CORE_SKILLS, core_n) + rng.sample(OTHER_SKILLS, rng.randint(1, 4))
        rng.shuffle(skills)
        n_jobs = rng.randint(1, 3)
        companies = rng.sample(COMPANIES, n_jobs)
        year = 2026
        jobs: list[Job] = []
        for company in companies:
            duration = rng.randint(1, 5)
            title = rng.choice(TITLES_CORE if rng.random() < 0.6 else TITLES_OTHER)
            jobs.append(Job(company, year - duration, year, title, rng.choice(DUTIES)))
            year -= duration
        years = 2026 - jobs[-1].start_year
        school, degree = rng.choice(SCHOOLS)
        city = rng.choice(CITIES) if rng.random() > 0.25 else None
        explicit_years = rng.random() < 0.5
        drafts.append(
            dict(
                sample_id=f"S{i + 1:02d}",
                name=names[i],
                city=city,
                school=school,
                degree=degree,
                major=rng.choice(MAJORS),
                grad_year=jobs[-1].start_year,
                jobs=tuple(jobs),
                skills=tuple(skills),
                years=years,
                explicit_years=explicit_years,
                self_eval=rng.choice(SELF_EVAL),
                fit=_designed_fit(core_n, years, degree, tuple(jobs)),
            )
        )
    order = sorted(range(n), key=lambda k: (-drafts[k]["fit"], drafts[k]["sample_id"]))
    rank_of = {k: r + 1 for r, k in enumerate(order)}
    return [Profile(**drafts[k], human_rank=rank_of[k]) for k in range(n)]


def render_text(p: Profile) -> str:
    lines = [p.name, ""]
    if p.city is not None:
        lines.append(f"期望工作城市：{p.city}")
    if p.explicit_years:
        lines.append(f"工作年限：{p.years} 年")
    lines += ["", "教育背景", f"{p.school}｜{p.degree}｜{p.major}｜{p.grad_year - 4}—{p.grad_year}", "", "工作经历"]
    for j in p.jobs:
        lines.append(f"{j.company}（{j.start_year}年—{j.end_year}年）｜{j.title}")
        lines.append(f"· {j.duty}")
    lines += ["", "技能", "、".join(p.skills), "", "自我评价", p.self_eval, ""]
    return "\n".join(lines)


def truth_fields(p: Profile) -> dict:
    return {
        "name": p.name,
        "years_of_experience": p.years,
        "skills": list(p.skills),
        "companies": [j.company for j in p.jobs],
        "education": {"degree": p.degree, "school": p.school},
        "expected_city": p.city,
    }


_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_cjk_font() -> Path | None:
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _write_docx(text: str, path: Path) -> None:
    import docx

    document = docx.Document()
    for line in text.split("\n"):
        document.add_paragraph(line)
    document.save(str(path))


def _write_pdf(text: str, path: Path) -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path))
    y = 800
    for line in text.split("\n"):
        if y < 60:
            c.showPage()
            y = 800
        c.setFont("STSong-Light", 11)
        c.drawString(50, y, line)
        y -= 16
    c.save()


def _write_scan_pdf(text: str, path: Path, font: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image_font = ImageFont.truetype(str(font), 22)
    lines = text.split("\n")
    image = Image.new("RGB", (1240, max(1754, 40 + 30 * len(lines))), "white")
    draw = ImageDraw.Draw(image)
    y = 40
    for line in lines:
        draw.text((60, y), line, fill="black", font=image_font)
        y += 30
    image.save(str(path), "PDF", resolution=150)


def write_all(out_dir: Path, profiles: list[Profile], *, font: Path | None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in profiles:
        text = render_text(p)
        (out_dir / f"{p.sample_id}.txt").write_text(text, encoding="utf-8")
        _write_docx(text, out_dir / f"{p.sample_id}.docx")
        _write_pdf(text, out_dir / f"{p.sample_id}.pdf")
        scan_name = None
        if font is not None:
            scan_name = f"{p.sample_id}_scan.pdf"
            _write_scan_pdf(text, out_dir / scan_name, font)
        rows.append(
            {
                "sample_id": p.sample_id,
                "files": {"txt": f"{p.sample_id}.txt", "docx": f"{p.sample_id}.docx", "pdf": f"{p.sample_id}.pdf", "scan": scan_name},
                "fields": truth_fields(p),
                "human_rank": p.human_rank,
                "designed_fit": p.fit,
            }
        )
    truth = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "sample_class": "synthetic",
        "rubric": RUBRIC,
        "samples": rows,
    }
    (out_dir / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成合成简历样本")
    parser.add_argument("--out", type=Path, default=Path("data/eval/m2-pilot"))
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    font = find_cjk_font()
    truth = write_all(args.out, build_profiles(args.n, seed=args.seed), font=font)
    print(f"写入 {len(truth['samples'])} 份样本到 {args.out}；扫描件：{'已生成' if font else '未生成（未找到中文字体）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_gen_pilot_samples.py -q`
Expected: `6 passed`

- [ ] **Step 5: 实跑一次生成器并确认不入库**

Run: `venv/bin/python -m scripts.gen_pilot_samples --out data/eval/m2-pilot && ls data/eval/m2-pilot | head && git status --porcelain data/ | wc -l`
Expected: 打印 `写入 20 份样本…`；目录里有 `S01.txt S01.docx S01.pdf`（Mac 上还有 `S01_scan.pdf`）；`git status` 计数为 `0`（`data/` 已 ignore）

- [ ] **Step 6: Commit**

```bash
git add scripts/gen_pilot_samples.py tests/test_gen_pilot_samples.py
git commit -m "feat(m2-u0): 合成简历样本生成器（20 份、四种文件形态、truth.json 含设计排序）"
```

---

### Task 6: 对比脚本核心——抽取＋精排、JSONL 留痕、按模型汇总指标

**Files:**
- Create: `scripts/compare_models_m2.py`
- Test: `tests/test_compare_models_m2.py`

**Interfaces:**
- Consumes: `LLMGateway.extract_structured_with_meta`、`LLMCallMeta`（`app/llm/gateway.py`）；Task 1–3 全部产出；`truth.json` 结构（Task 5）
- Produces:
  - `PARSE_PROMPT_VERSION = "parse-u0-v1"`、`RANK_PROMPT_VERSION = "rank-u0-v1"`、`PARSE_SYSTEM_PROMPT`、`RANK_SYSTEM_PROMPT`
  - `ModelCandidate(name, api_key_env, base_url, model, supports_json_schema, price_in_per_mtok=None, price_out_per_mtok=None)`；`CANDIDATES: list[ModelCandidate]`
  - `JsonlAuditHook(path: Path, *, rubric_snapshot: dict | None)`：实现 `AuditHook.record(**kwargs)`；属性 `calls / prompt_tokens / completion_tokens / fingerprints`
  - `Sample(sample_id, text, truth_fields, human_rank)`；`load_samples(sample_dir: Path) -> tuple[list[Sample], dict]`
  - `build_parse_user_prompt(spans) -> str`、`build_rank_user_prompt(spans, rubric) -> str`
  - `ground_fields(fields: ResumeFields, spans) -> ResumeFields`、`ground_rank(result: RankResult, spans) -> tuple[RankResult | None, list[str]]`
  - `run_parse(gateway, spans) -> tuple[ResumeFields, LLMCallMeta]`、`run_rank(gateway, spans, rubric) -> tuple[RankResult | None, LLMCallMeta, list[str], RankResult]`（依次：反查后的结果或 None、调用元数据、不可定位的维度 key、模型原始输出）
  - `SampleOutcome`、`evaluate_sample(gateway, sample, rubric) -> SampleOutcome`
  - `ModelReport`（字段见实现）；`evaluate_model(candidate, samples, rubric, *, gateway_factory, audit_dir, top_k=10) -> ModelReport`
  - `default_gateway_factory(candidate, hook) -> LLMGateway | None`（key 缺 ⇒ `None`）；`compute_cost(candidate, prompt_tokens, completion_tokens) -> float | None`；`percentile(values, p) -> float`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compare_models_m2.py
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.llm.gateway import LLMGateway
from scripts.compare_models_m2 import (
    PARSE_PROMPT_VERSION,
    RANK_PROMPT_VERSION,
    JsonlAuditHook,
    ModelCandidate,
    Sample,
    compute_cost,
    evaluate_model,
    load_samples,
    percentile,
)

# ---- 假 OpenAI client（与 tests/test_llm_gateway.py 同构，独立定义以免跨测试文件 import）----


@dataclass
class _Msg:
    content: str


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Usage:
    prompt_tokens: int = 100
    completion_tokens: int = 40


@dataclass
class _Resp:
    choices: list
    model: str
    usage: _Usage = field(default_factory=_Usage)
    system_fingerprint: str | None = "fp_test_0917"


class _Completions:
    def __init__(self, responses: list[str], model: str):
        self._responses = list(responses)
        self._model = model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(choices=[_Choice(_Msg(self._responses.pop(0)))], model=self._model)


class _Chat:
    def __init__(self, responses, model):
        self.completions = _Completions(responses, model)


class FakeClient:
    def __init__(self, responses: list[str], model: str = "fake-model-2026-01"):
        self.chat = _Chat(responses, model)


TEXT_A = "张明远\n期望工作城市：无锡\n工作年限：5 年\n教育背景\n江南大学｜本科｜车辆工程\n工作经历\n无锡华芯车控科技有限公司（2021年—2026年）｜底层软件工程师\n技能\nC、AUTOSAR CP、CAN\n"
TEXT_B = "李子墨\n教育背景\n苏州大学｜大专｜自动化\n工作经历\n杭州云栈软件有限公司（2024年—2026年）｜Java 开发工程师\n技能\nJava、SQL\n"
TEXT_C = "王思远\n期望工作城市：苏州\n教育背景\n东南大学｜硕士｜电子信息工程\n工作经历\n南京擎川电子有限公司（2019年—2026年）｜嵌入式软件工程师\n技能\nC、MCAL、UDS、Bootloader\n"

RUBRIC = {
    "job_title": "底层软件工程师",
    "criteria": [{"key": "skill_match", "description": "底层技能"}, {"key": "experience_depth", "description": "年限"}],
    "profile_text": "底层软件工程师，要求 C/AUTOSAR。",
}


def _samples():
    return [
        Sample("SA", TEXT_A, {"name": "张明远", "years_of_experience": 5, "skills": ["C", "AUTOSAR CP", "CAN"], "companies": ["无锡华芯车控科技有限公司"], "education": {"degree": "本科", "school": "江南大学"}, "expected_city": "无锡"}, 2),
        Sample("SB", TEXT_B, {"name": "李子墨", "years_of_experience": 2, "skills": ["Java", "SQL"], "companies": ["杭州云栈软件有限公司"], "education": {"degree": "大专", "school": "苏州大学"}, "expected_city": None}, 3),
        Sample("SC", TEXT_C, {"name": "王思远", "years_of_experience": 7, "skills": ["C", "MCAL", "UDS", "Bootloader"], "companies": ["南京擎川电子有限公司"], "education": {"degree": "硕士", "school": "东南大学"}, "expected_city": "苏州"}, 1),
    ]


def _parse_json(name_q, name, years, years_q, skills, skills_q, companies, comp_q, degree, school, edu_q, city, city_q):
    def f(value, quote, sid, not_mentioned=False):
        if not_mentioned:
            return {"not_mentioned": True, "confidence": 1.0, "spans": [], "value": None}
        spans = [{"span_id": sid, "quote": quote}] if quote else []  # quote 为空 ⇒ 模型给了值但没给来源
        return {"not_mentioned": False, "confidence": 0.9, "spans": spans, "value": value}

    return json.dumps(
        {
            "name": f(name, name_q, 1),
            "years_of_experience": f(years, years_q, 3),
            "skills": f(skills, skills_q, 9),
            "companies": f(companies, comp_q, 7),
            "education": f({"degree": degree, "school": school}, edu_q, 5),
            "expected_city": f(city, city_q, 2) if city else f(None, "", 0, not_mentioned=True),
        },
        ensure_ascii=False,
    )


def _rank_json(skill_quote, skill_sid, exp_quote, exp_sid, s1=4.0, s2=3.0):
    return json.dumps(
        {
            "scores": [
                {"key": "skill_match", "score": s1, "rationale": "r", "evidence": {"span_id": skill_sid, "quote": skill_quote}},
                {"key": "experience_depth", "score": s2, "rationale": "r", "evidence": {"span_id": exp_sid, "quote": exp_quote}},
            ]
        },
        ensure_ascii=False,
    )


def _candidate(**overrides):
    base = dict(name="fake", api_key_env="FAKE_KEY_0917", base_url="https://example.invalid/v1", model="fake-model-2026-01", supports_json_schema=False)
    return ModelCandidate(**{**base, **overrides})


def _factory_with(responses: list[str]):
    def factory(candidate, hook):
        return LLMGateway(
            api_key="k",
            base_url=candidate.base_url,
            model=candidate.model,
            supports_json_schema=False,
            max_retries=0,
            audit_hook=hook,
            client=FakeClient(responses, model="fake-model-2026-01"),
        )

    return factory


def test_evaluate_model_end_to_end_with_fake_client(tmp_path):
    # 顺序：每份样本先 parse 再 rank
    responses = [
        _parse_json("张明远", "张明远", 5, "工作年限：5 年", ["C", "AUTOSAR CP", "CAN"], "C、AUTOSAR CP、CAN", ["无锡华芯车控科技有限公司"], "无锡华芯车控科技有限公司", "本科", "江南大学", "江南大学｜本科", "无锡", "无锡"),
        _rank_json("C、AUTOSAR CP、CAN", 9, "2021年—2026年", 7, 4.0, 3.0),
        # SB：年限未在文中出现 ⇒ 模型给了值但无 span ⇒ 置信度归 0；rank 的证据引错分片 ⇒ 整次不可用
        _parse_json("李子墨", "李子墨", 2, "", ["Java", "SQL"], "Java、SQL", ["杭州云栈软件有限公司"], "杭州云栈软件有限公司", "大专", "苏州大学", "苏州大学｜大专", None, ""),
        _rank_json("Java、SQL", 7, "不存在的引文", 3, 1.0, 1.0),
        _parse_json("王思远", "王思远", 7, "", ["C", "MCAL", "UDS", "Bootloader"], "C、MCAL、UDS、Bootloader", ["南京擎川电子有限公司"], "南京擎川电子有限公司", "硕士", "东南大学", "东南大学｜硕士", "苏州", "苏州"),
        _rank_json("C、MCAL、UDS、Bootloader", 8, "2019年—2026年", 6, 5.0, 4.0),
    ]
    report = evaluate_model(_candidate(price_in_per_mtok=2.0, price_out_per_mtok=8.0), _samples(), RUBRIC, gateway_factory=_factory_with(responses), audit_dir=tmp_path, top_k=10)

    assert report.skipped is False
    assert report.n_samples == 3
    assert report.parse_ok == 3
    assert report.rank_ok == 2  # SB 证据不可定位 ⇒ 不计分
    assert report.response_models == ["fake-model-2026-01"]
    assert report.fingerprints == ["fp_test_0917"]
    assert report.field_acc.per_field["name"] == 1.0
    assert report.field_acc.per_field["expected_city"] == 1.0
    assert report.spearman is None  # n=2 < 10 ⇒ 不出结论
    assert report.top_k_recall == 1.0  # 人工前 ⌈3/2⌉=2 = {SC, SA}，系统前 2 = {SC, SA}
    assert 0 < report.span_traceability < 1.0  # SB 的一条证据不可定位
    assert report.prompt_tokens == 600 and report.completion_tokens == 240
    assert report.cost_yuan == round(600 / 1e6 * 2.0 + 240 / 1e6 * 8.0, 4)
    assert report.parse_p50_ms >= 0 and report.rank_p95_ms >= 0

    # 留痕：每次调用一行，含 rubric 快照、prompt 版本、原始响应、temperature、input_hash
    jsonl = list(tmp_path.glob("fake/*.jsonl"))
    assert len(jsonl) == 1
    rows = [json.loads(line) for line in jsonl[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6
    assert {r["prompt_version"] for r in rows} == {PARSE_PROMPT_VERSION, RANK_PROMPT_VERSION}
    assert all(r["temperature"] == 0 and r["input_hash"] and r["raw_response"] for r in rows)
    assert all(r["rubric_snapshot"] == RUBRIC for r in rows)
    assert all(r["response_model"] == "fake-model-2026-01" for r in rows)


def test_schema_failure_counts_as_parse_error_and_skips_rank(tmp_path):
    responses = ["{not json", _rank_json("C", 9, "2021年—2026年", 7)]  # 第二条不会被消费
    report = evaluate_model(_candidate(), _samples()[:1], RUBRIC, gateway_factory=_factory_with(responses), audit_dir=tmp_path)
    assert report.parse_ok == 0
    assert report.rank_ok == 0
    assert report.errors and "SA" in report.errors[0]
    assert report.field_acc.n == 0


def test_missing_api_key_yields_skipped_report(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_KEY_0917", raising=False)
    from scripts.compare_models_m2 import default_gateway_factory

    report = evaluate_model(_candidate(), _samples(), RUBRIC, gateway_factory=default_gateway_factory, audit_dir=tmp_path)
    assert report.skipped is True
    assert "FAKE_KEY_0917" in report.skip_reason


def test_load_samples_rejects_live_class(tmp_path):
    (tmp_path / "truth.json").write_text(json.dumps({"sample_class": "live", "rubric": RUBRIC, "samples": []}), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="live"):
        load_samples(tmp_path)


def test_load_samples_reads_txt_and_rubric(tmp_path):
    (tmp_path / "S01.txt").write_text(TEXT_A, encoding="utf-8")
    truth = {"sample_class": "synthetic", "rubric": RUBRIC, "samples": [{"sample_id": "S01", "files": {"txt": "S01.txt"}, "fields": {"name": "张明远"}, "human_rank": 1}]}
    (tmp_path / "truth.json").write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    samples, rubric = load_samples(tmp_path)
    assert samples[0].sample_id == "S01" and samples[0].text == TEXT_A and samples[0].human_rank == 1
    assert rubric == RUBRIC


def test_compute_cost_none_when_price_missing():
    assert compute_cost(_candidate(), 1000, 1000) is None
    assert compute_cost(_candidate(price_in_per_mtok=1.0, price_out_per_mtok=2.0), 1_000_000, 500_000) == 2.0


def test_percentile_nearest_rank():
    assert percentile([], 50) == 0.0
    assert percentile([10.0], 95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0


def test_jsonl_audit_hook_accumulates_tokens_and_fingerprints(tmp_path):
    hook = JsonlAuditHook(tmp_path / "x" / "a.jsonl", rubric_snapshot=None)
    hook.record(model="m", response_model="m2", system_fingerprint="fp1", prompt_version="v", temperature=0, input_hash="h", raw_response="{}", token_usage={"prompt_tokens": 3, "completion_tokens": 2}, latency_ms=1.0, attempt=1, audit_context=None)
    hook.record(model="m", response_model="m2", system_fingerprint=None, prompt_version="v", temperature=0, input_hash="h", raw_response=None, token_usage={}, latency_ms=1.0, attempt=2, audit_context=None)
    assert hook.calls == 2 and hook.prompt_tokens == 3 and hook.completion_tokens == 2
    assert hook.fingerprints == {"fp1"}
    assert len((tmp_path / "x" / "a.jsonl").read_text(encoding="utf-8").splitlines()) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_compare_models_m2.py -q`
Expected: `ModuleNotFoundError: No module named 'scripts.compare_models_m2'`

- [ ] **Step 3: 实现**

```python
# scripts/compare_models_m2.py
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
from app.llm.gateway import LLMCallMeta, LLMGateway, LLMProviderUnavailable, SchemaExtractionFailed
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
    except (SchemaExtractionFailed, LLMProviderUnavailable) as exc:
        outcome.parse_error = f"{sample.sample_id}: parse 失败 {type(exc).__name__}: {exc}"
        return outcome
    outcome.parse_ok = True
    outcome.parse_latency_ms = meta.latency_ms
    outcome.predicted = fields.plain()
    if meta.response_model:
        outcome.response_models.add(meta.response_model)
    try:
        grounded, meta, missing, raw = run_rank(gateway, spans, rubric)
    except (SchemaExtractionFailed, LLMProviderUnavailable) as exc:
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_compare_models_m2.py -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/compare_models_m2.py tests/test_compare_models_m2.py
git commit -m "feat(m2-u0): 模型对比核心——抽取＋精排、JSONL 留痕、按模型汇总 D9 指标与成本"
```

---

### Task 7: 对比报告渲染、CLI、扫描件 OCR 核对、BGE-M3 本地 CPU 召回 bench

**Files:**
- Modify: `scripts/compare_models_m2.py`（追加 `render_markdown` / `ocr_check` / `main`）
- Create: `scripts/bench_bge_m3.py`
- Test: `tests/test_compare_models_m2.py`（追加）
- Test: `tests/test_bench_bge_m3.py`

**Interfaces:**
- Consumes: Task 6 全部；`app/parsing/extract_text.extract_text`（Task 4）；`app/eval/recall`（Task 3）
- Produces:
  - `compare_models_m2.render_markdown(reports: list[ModelReport], *, n_samples: int, generated_at: str) -> str`
  - `compare_models_m2.ocr_check(sample_dir: Path, *, ocr: Callable[[Path], str] | None = None) -> list[dict]`（每个有扫描件的样本一行：`sample_id / readable / effective_chars / similarity`；OCR 不可用 ⇒ 单行 `{"error": ...}`）
  - `compare_models_m2.main(argv) -> int`：`--samples --models --out --json --audit-dir --top-k --ocr-check`
  - `bench_bge_m3.MODEL_ID = "BAAI/bge-m3"`、`load_embedder(backend: str) -> Callable[[list[str]], np.ndarray]`、`bench(embed, samples, rubric_text, *, top_k=10) -> dict`、`render_markdown(result: dict) -> str`、`main(argv) -> int`

- [ ] **Step 1: 写失败测试（追加到 `tests/test_compare_models_m2.py` 末尾，以及新建 `tests/test_bench_bge_m3.py`）**

```python
# tests/test_compare_models_m2.py（追加）
from app.eval.metrics import FieldAccuracy
from scripts.compare_models_m2 import ModelReport, main, ocr_check, render_markdown


def test_render_markdown_lists_response_model_and_skipped_rows():
    ok = ModelReport(
        name="deepseek-pro", config_model="deepseek-v4-pro", n_samples=20, response_models=["deepseek-v4-pro"], fingerprints=["fp_x"],
        parse_ok=20, rank_ok=19, field_acc=FieldAccuracy({f: 0.9 for f in ("name", "years_of_experience", "skills", "companies", "education", "expected_city")}, 0.9, 20),
        spearman=0.72, top_k_recall=0.9, span_traceability=0.98, parse_p50_ms=1200, parse_p95_ms=2500, rank_p50_ms=3000, rank_p95_ms=6000,
        prompt_tokens=100000, completion_tokens=20000, cost_yuan=0.36,
    )
    skipped = ModelReport(name="qwen", config_model="qwen3.7-plus-241226", n_samples=20, skipped=True, skip_reason="跳过：环境变量 DASHSCOPE_API_KEY 未设置")
    md = render_markdown([ok, skipped], n_samples=20, generated_at="2026-09-17T00:00:00Z")
    assert "| deepseek-pro | deepseek-v4-pro | fp_x |" in md
    assert "90.0%" in md and "0.72" in md and "0.36" in md
    assert "| qwen |" in md and "DASHSCOPE_API_KEY" in md
    assert "≥90%" in md and "≥0.70" in md and "≥85%" in md and "100%" in md
    assert "| 候选 | 姓名 | 工作年限 |" in md


def test_ocr_check_uses_injected_ocr_and_reports_similarity(tmp_path):
    from PIL import Image

    (tmp_path / "S01.txt").write_text(TEXT_A, encoding="utf-8")
    Image.new("RGB", (300, 100), "white").save(str(tmp_path / "S01_scan.pdf"), "PDF")
    truth = {"sample_class": "synthetic", "rubric": RUBRIC, "samples": [{"sample_id": "S01", "files": {"txt": "S01.txt", "scan": "S01_scan.pdf"}, "fields": {}, "human_rank": 1}, {"sample_id": "S02", "files": {"txt": "S01.txt", "scan": None}, "fields": {}, "human_rank": 2}]}
    (tmp_path / "truth.json").write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    rows = ocr_check(tmp_path, ocr=lambda _: TEXT_A.replace("无锡", "无钖"))
    assert len(rows) == 1
    assert rows[0]["sample_id"] == "S01" and rows[0]["readable"] is True
    assert 0.9 < rows[0]["similarity"] < 1.0


def test_ocr_check_reports_unavailable_without_raising(tmp_path):
    from PIL import Image

    from app.parsing.extract_text import OcrUnavailable

    (tmp_path / "S01.txt").write_text(TEXT_A, encoding="utf-8")
    Image.new("RGB", (300, 100), "white").save(str(tmp_path / "S01_scan.pdf"), "PDF")
    truth = {"sample_class": "synthetic", "rubric": RUBRIC, "samples": [{"sample_id": "S01", "files": {"txt": "S01.txt", "scan": "S01_scan.pdf"}, "fields": {}, "human_rank": 1}]}
    (tmp_path / "truth.json").write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")

    def missing(_):
        raise OcrUnavailable("paddle 未装")

    rows = ocr_check(tmp_path, ocr=missing)
    assert rows == [{"sample_id": "S01", "error": "OcrUnavailable: paddle 未装"}]


def test_main_writes_markdown_and_json_with_all_candidates_skipped(tmp_path, monkeypatch):
    for env in ("DEEPSEEK_API_KEY", "ARK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    (tmp_path / "S01.txt").write_text(TEXT_A, encoding="utf-8")
    truth = {"sample_class": "synthetic", "rubric": RUBRIC, "samples": [{"sample_id": "S01", "files": {"txt": "S01.txt"}, "fields": {"name": "张明远"}, "human_rank": 1}]}
    (tmp_path / "truth.json").write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    out_md, out_json = tmp_path / "r.md", tmp_path / "r.json"
    code = main(["--samples", str(tmp_path), "--out", str(out_md), "--json", str(out_json), "--audit-dir", str(tmp_path / "runs")])
    assert code == 0
    md = out_md.read_text(encoding="utf-8")
    assert "deepseek-pro" in md and "DEEPSEEK_API_KEY" in md
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert {r["name"] for r in data["reports"]} == {"deepseek-pro", "deepseek-flash", "doubao", "qwen"}
    assert all(r["skipped"] for r in data["reports"])
```

```python
# tests/test_bench_bge_m3.py
import numpy as np

from scripts.bench_bge_m3 import MODEL_ID, bench, render_markdown
from scripts.compare_models_m2 import Sample

KEYS = ("C", "AUTOSAR", "MCAL", "Java")


def fake_embed(texts: list[str]) -> np.ndarray:
    return np.array([[float(t.count(k)) for k in KEYS] for t in texts])


def _samples():
    return [
        Sample("S1", "C AUTOSAR MCAL", {}, 1),
        Sample("S2", "C MCAL", {}, 2),
        Sample("S3", "Java Java", {}, 3),
        Sample("S4", "AUTOSAR", {}, 4),
    ]


def test_bench_reports_dim_timing_and_recall():
    result = bench(fake_embed, _samples(), "C AUTOSAR MCAL", top_k=2)
    assert MODEL_ID == "BAAI/bge-m3"
    assert result["n"] == 4 and result["dim"] == 4 and result["top_k"] == 2
    assert result["per_doc_ms"] >= 0 and result["query_ms"] >= 0
    assert [r[0] for r in result["recalled"]] == ["S1", "S2"]
    assert result["recall_at_k"] == 1.0  # k_truth=top_k=2 ⇒ 人工前 2 = {S1,S2}，全部被召回


def test_render_markdown_mentions_backend_and_recall():
    result = bench(fake_embed, _samples(), "C AUTOSAR MCAL", top_k=2)
    md = render_markdown({**result, "backend": "fake"})
    assert "fake" in md and "recall@2" in md
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_compare_models_m2.py tests/test_bench_bge_m3.py -q`
Expected: `ImportError: cannot import name 'render_markdown' from 'scripts.compare_models_m2'`

- [ ] **Step 3: 实现（追加到 `scripts/compare_models_m2.py` 末尾）**

```python
# scripts/compare_models_m2.py（追加）
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
```

```python
# scripts/bench_bge_m3.py
"""
BGE-M3 本地 CPU 召回实测（tasks 1.5；design D7 已裁决本地 CPU，⛔ 不测托管 API）。

模型权重约 2.2 GB，首次运行会下载；境内机器先 `export HF_ENDPOINT=https://hf-mirror.com`。
单测只测 bench() 的纯计算部分（注入假 embedder），⛔ 不加载模型。

用法：python -m scripts.bench_bge_m3 --samples data/eval/m2-pilot --backend flag --top-k 10 --json data/eval/m2-pilot/bench-bge-m3.json
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.eval.recall import cosine_top_k, recall_at_k
from scripts.compare_models_m2 import Sample, load_samples

MODEL_ID = "BAAI/bge-m3"


def load_embedder(backend: str) -> Callable[[list[str]], np.ndarray]:
    if backend == "flag":
        from FlagEmbedding import BGEM3FlagModel

        try:
            model = BGEM3FlagModel(MODEL_ID, use_fp16=False, devices=["cpu"])
        except TypeError:  # FlagEmbedding < 1.3 的参数名是 device
            model = BGEM3FlagModel(MODEL_ID, use_fp16=False, device="cpu")

        def embed(texts: list[str]) -> np.ndarray:
            return np.asarray(model.encode(texts, batch_size=4, max_length=4096)["dense_vecs"], dtype=np.float32)

        return embed
    if backend == "st":
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL_ID, device="cpu")
        return lambda texts: np.asarray(model.encode(texts, normalize_embeddings=True, batch_size=4), dtype=np.float32)
    raise ValueError(f"未知 backend: {backend}（可选 flag / st）")


def bench(embed: Callable[[list[str]], np.ndarray], samples: list[Sample], rubric_text: str, *, top_k: int = 10) -> dict:
    started = time.perf_counter()
    query = embed([rubric_text])[0]
    query_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    docs = embed([s.text for s in samples])
    docs_ms = (time.perf_counter() - started) * 1000
    ids = [s.sample_id for s in samples]
    recalled = cosine_top_k(query, docs, ids, top_k)
    human_order = [s.sample_id for s in sorted(samples, key=lambda s: s.human_rank)]
    return {
        "model": MODEL_ID,
        "platform": platform.platform(),
        "n": len(samples),
        "dim": int(docs.shape[1]) if len(samples) else 0,
        "query_ms": round(query_ms, 1),
        "docs_total_ms": round(docs_ms, 1),
        "per_doc_ms": round(docs_ms / len(samples), 1) if samples else 0.0,
        "top_k": top_k,
        "recall_at_k": recall_at_k([sid for sid, _ in recalled], human_order, k_truth=top_k),
        "recalled": [(sid, round(score, 4)) for sid, score in recalled],
    }


def render_markdown(result: dict) -> str:
    return "\n".join(
        [
            f"模型：`{result['model']}`（backend={result.get('backend', '—')}）｜ 平台：`{result['platform']}`",
            f"样本 {result['n']} 份 ｜ 维度 {result['dim']} ｜ 画像向量 {result['query_ms']} ms ｜ 单份简历 {result['per_doc_ms']} ms（合计 {result['docs_total_ms']} ms）",
            f"recall@{result['top_k']}（以人工排序前 {result['top_k']} 为真值）＝ {result['recall_at_k'] * 100:.1f}%",
            "",
            "| 名次 | 样本 | cosine |",
            "|---|---|---|",
        ]
        + [f"| {i + 1} | {sid} | {score} |" for i, (sid, score) in enumerate(result["recalled"])]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BGE-M3 本地 CPU 召回 bench")
    parser.add_argument("--samples", type=Path, default=Path("data/eval/m2-pilot"))
    parser.add_argument("--backend", choices=["flag", "st"], default="flag")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    samples, rubric = load_samples(args.samples)
    result = {**bench(load_embedder(args.backend), samples, rubric["profile_text"], top_k=args.top_k), "backend": args.backend}
    print(render_markdown(result))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_compare_models_m2.py tests/test_bench_bge_m3.py -q`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/compare_models_m2.py scripts/bench_bge_m3.py tests/test_compare_models_m2.py tests/test_bench_bge_m3.py
git commit -m "feat(m2-u0): 对比报告渲染＋CLI＋扫描件 OCR 核对＋BGE-M3 本地 CPU 召回 bench"
```

---

### Task 8: 文档骨架、能跑的实跑、留步登记

**Files:**
- Create: `docs/m2-model-comparison.md`
- Create: `docs/eval/m2-pilot-README.md`
- Modify: `openspec/changes/m2-resume-parse-and-rank/tasks.md`（只回勾本计划已闭环的项，规则见 Step 6）

**Interfaces:**
- Consumes: Task 4–7 的四个脚本

- [ ] **Step 1: 写文档骨架**

`docs/m2-model-comparison.md`：

```markdown
# M2 模型对比实测（U0）

> 状态：**进行中**（2026-09-17 建骨架）。定型须 Shao Peishen 签认（tasks 1.7）：签认后在文末「已确认」填日期。
> 方法沿用 `docs/m1-model-comparison.md`：模型标识只认响应侧 `model` 字段，`system_fingerprint` 是漂移的唯一可信信号，`temperature=0`，只看首次不吃重试红利。
> 裁决前提（2026-09-17 Shao Peishen）：BGE-M3 在 `.51` 本地 CPU（design D7）；扫描件用 PaddleOCR（D14）；置信度阈值 0.7 起步由本文实测定终值（D5）。

## 如何跑

```bash
set -a; [ -f .env ] && source .env; set +a          # DEEPSEEK_API_KEY / ARK_API_KEY / DASHSCOPE_API_KEY
venv/bin/python -m scripts.gen_pilot_samples --out data/eval/m2-pilot          # 合成样本（真实脱敏样本到位后替换同目录）
venv/bin/python -m scripts.smoke_m2_deps --json data/eval/m2-pilot/smoke-$(uname).json
venv/bin/python -m scripts.compare_models_m2 --samples data/eval/m2-pilot --out data/eval/m2-pilot/compare-run.md --json data/eval/m2-pilot/compare-run.json
venv/bin/python -m scripts.compare_models_m2 --ocr-check                       # 扫描件 OCR 核对（需 PaddleOCR）
HF_ENDPOINT=https://hf-mirror.com venv/bin/python -m scripts.bench_bge_m3 --json data/eval/m2-pilot/bench-bge-m3.json
```

## 环境（tasks 1.1）

### Mac（开发机）
（贴 `scripts.smoke_m2_deps` 输出）

### `.51` 同款 Windows
⏸ 留步：需在 `.51` 同款 Windows venv 上实跑 `python -m scripts.smoke_m2_deps`，本节由该次运行填写。重依赖三项（paddlepaddle／paddleocr／FlagEmbedding）在 Python 3.14 + win_amd64 是否有 wheel 是第一问；paddle 装不上 ⇒ 走 D14 退路并登记技术债。

## 样本

- 来源类别：`synthetic`（`scripts/gen_pilot_samples.py`，seed 20260917，20 份，无任何真人信息）。⚠️ 合成样本上的指标只证明管线、schema 守字段率、evidence 可定位率与延迟成本；**D9 验收数字必须在真实脱敏样本上重算**（计划「待裁决」#2）。
- 文件形态：txt／docx／文本型 pdf（reportlab STSong-Light）／扫描 pdf（Pillow 渲染，Mac 有中文字体时生成）。

## 候选模型

| 候选 | 配置侧模型 | json_schema | 定价（元/百万 token，入/出，抄录日期） | key 状态 |
|---|---|---|---|---|
| deepseek-pro | deepseek-v4-pro | ✗（M1 实测） | | |
| deepseek-flash | deepseek-v4-flash | ✗ | | |
| doubao | doubao-seed-2-1-turbo-241215（未核实） | ？ | | |
| qwen | qwen3.7-plus-241226（未核实） | ？ | | |

## 对比结果（tasks 1.3／1.4）

（贴 `data/eval/m2-pilot/compare-run.md`）

## BGE-M3 本地 CPU 召回（tasks 1.5）

（贴 `scripts.bench_bge_m3` 输出）

## 扫描件 OCR（D14）

（贴 `--ocr-check` 输出）

## 决策（tasks 1.6；每项必须有上面的数据支撑）

| 项 | 结论 | 数据支撑 |
|---|---|---|
| 抽取模型 | 待数据 | |
| 精排模型 | 待数据 | |
| embedding 方案 | `.51` 本地 CPU BGE-M3（已裁决），装包体积／单份耗时： | 见「BGE-M3」节 |
| 置信度阈值起步值（Q3） | 0.7（推荐起步），终值： | 见对比表 evidence 可定位率与逐字段准确率 |
| 扫描件路径 | PaddleOCR（已裁决），可装性／相似度： | 见「扫描件 OCR」节 |

## 待裁决与留步

（由 run-build 收工时同步计划「待裁决」节的现状）

## 已确认

（Shao Peishen 签认后填：已确认 YYYY-MM-DD）
```

`docs/eval/m2-pilot-README.md`：

```markdown
# `data/eval/m2-pilot/` 目录规范（M2 U0 对比样本）

- **位置**：`data/eval/m2-pilot/`，整个 `data/` 已在 `.gitignore`，⛔ 任何样本、留痕 JSONL、对比输出都不进版本库。
- **来源类别**（`truth.json.sample_class`）：`synthetic`（合成，无真人信息）／`anonymized`（脱敏）／`departed`（历史离职）。`live` 一律拒绝（`load_samples` 抛错；spec「评测集样本来源与访问控制」）。
- **访问控制**：目录只在开发机与 `.51` 存在；`.51` 上由 Windows ACL 限制到运行账户与 HR 管理员（发版时随 U7 落实）。
- **留存期**：合成样本无限期；脱敏／离职样本与回件归档件 **90 天**（2026-09-17 裁决，以合规验收 #1 留存策略为准）。
- **禁止训练用途**：`truth.json` 的人工排序与字段真值只用于离线指标，⛔ 不进任何训练／微调／prompt 自动优化（U6 7.5 有机器断言）。
- **结构**：`<id>.txt`（真值文本）、`<id>.docx`、`<id>.pdf`、`<id>_scan.pdf`（可选）、`truth.json`（见 `scripts/gen_pilot_samples.py` 文档串）、`runs/<候选>/<ts>.jsonl`（每次 LLM 调用一行，铁律 3）、`compare-run.md/json`、`bench-bge-m3.json`、`smoke-<平台>.json`。
- **替换为真实脱敏样本**：保持同一布局与 `truth.json` 字段，`human_rank` 改为汤丽萍判例批改表的名次，脚本零改动。
```

- [ ] **Step 2: 全量测试与本机实跑（能跑的都跑）**

Run:
```bash
venv/bin/python -m pytest -q 2>&1 | tail -3
venv/bin/python -m scripts.gen_pilot_samples --out data/eval/m2-pilot
venv/bin/python -m scripts.smoke_m2_deps --json data/eval/m2-pilot/smoke-Darwin.json
```
Expected: 全量 `passed`、无 `failed`；生成器打印 `写入 20 份样本`；探针表打印（重依赖三项此时预期 ❌，属正常）。把探针表贴进 `docs/m2-model-comparison.md`「Mac」节。

- [ ] **Step 3: LLM 对比实跑（有 key 才跑，无 key 登记）**

Run:
```bash
set -a; [ -f .env ] && source .env; set +a
venv/bin/python -m scripts.compare_models_m2 --samples data/eval/m2-pilot --out data/eval/m2-pilot/compare-run.md --json data/eval/m2-pilot/compare-run.json 2>&1 | tail -30
```
Expected: 打印对比表；有 key 的候选有数字，无 key 的候选一行 `跳过：环境变量 … 未设置`。把表贴进「对比结果」节；在「候选模型」表填 key 状态与定价抄录日期（无定价 ⇒ 写"未抄录"）。**全部候选都跳过** ⇒ 「对比结果」节写 `⏸ 留步：本机无任何供应商 key`，收工 PARTIAL。

- [ ] **Step 4: 重依赖冒烟 ＋ bench ＋ OCR（限时，装不上就登记）**

Run:
```bash
timeout 1500 venv/bin/pip install "FlagEmbedding==1.4.2" 2>&1 | tail -3
timeout 900 venv/bin/pip install "paddlepaddle==3.4.0" "paddleocr==3.7.0" 2>&1 | tail -3   # 预期 NO（cp314 无 wheel，2026-09-17 实测）——如实记进「扫描件 OCR」节，走 D14 退路，⛔ 不改 requires-python
venv/bin/python -m scripts.smoke_m2_deps --json data/eval/m2-pilot/smoke-Darwin.json
HF_ENDPOINT=https://hf-mirror.com timeout 2400 venv/bin/python -m scripts.bench_bge_m3 --json data/eval/m2-pilot/bench-bge-m3.json 2>&1 | tail -20
timeout 1200 venv/bin/python -m scripts.compare_models_m2 --ocr-check 2>&1 | tail -30
```
Expected: 三条各自要么打印结果（贴进对应节），要么以非零退出／❌ 结束——后者在对应节写 `⏸ 留步：<pip 或运行的最后一行错误>`，⛔ 不判整件失败、⛔ 不改依赖选型（换引擎须重新裁决）。

- [ ] **Step 5: 填「决策」节能填的行，登记待裁决**

有数据的行填结论＋数据支撑；无数据的行保持"待数据"并在「待裁决与留步」节逐条列：① `.51` Windows 冒烟未跑；② 真实脱敏样本未到位、合成样本上的数字不作验收；③ 缺 key 的候选（写清哪几个环境变量）；④ 重依赖装不上的项；⑤ 1.7 定型确认待 Shao Peishen。

- [ ] **Step 6: 回勾 tasks.md（只勾已闭环的）**

规则：1.3 勾（脚本＋测试齐）；1.2 勾并在行尾加「（合成替身，真实脱敏样本到位后重跑，见 U0 计划待裁决 #2）」；1.1／1.4／1.5 只有对应实跑真的出了数据才勾，否则不勾、在行尾加 `⏸ 留步：<原因>`；1.6 只有「决策」节每行都有数据支撑才勾；1.7 ⛔ 不动。`tasks.md` 首行进度计数按实际勾数更新。

- [ ] **Step 7: Commit**

```bash
git add docs/m2-model-comparison.md docs/eval/m2-pilot-README.md openspec/changes/m2-resume-parse-and-rank/tasks.md
git commit -m "docs(m2-u0): 模型对比文档骨架＋样本目录规范＋本机实跑结果与留步登记"
```

---

## 待裁决（无人值守下不自行拍板；均已登记，不阻塞 Task 1–7）

1. **1.7 定型确认**——Shao Peishen 签认 `docs/m2-model-comparison.md`「决策」节。本计划只产数据，⛔ 不替他定。
2. **真实脱敏样本 ≥20 份（含 ≥3 扫描件、≥3 Word）**：仓库与 `data/` 无库存（2026-09-17 实查），U0 先用合成样本跑通管线；D9 验收数字须在脱敏／离职样本上重算。样本来源与脱敏方式属「真实简历数据处理范围」相关判断 ⇒ 由 Shao Peishen 指定来源（候选：历史离职候选人简历经脱敏；或汤丽萍批改表首批 20 份），本计划无默认。
3. **第 3／4 家境内 LLM 的 API key**（火山方舟 `ARK_API_KEY`、阿里百炼 `DASHSCOPE_API_KEY`）：M1 时未注册（`docs/m1-model-comparison.md`），属「预算与外部采购」不可代项。无 key 时 tasks 1.4「≥3 家」只能跑 DeepSeek 两款，对比表如实标跳过。
4. **`.51` Windows 冒烟（tasks 1.1）**：需在 `.51` 同款 Windows 上跑 `scripts.smoke_m2_deps`，无头 Mac 会话做不到 ⇒ 留步；结果决定 PaddleOCR 是否走 D14 退路。
5. **Q3 置信度阈值终值**：按裁决由 U0 数据定，属可代实施参数；数据不足时保持 0.7 起步并在文档标"待真实样本复算"。
6. **PaddleOCR 在 Python 3.14 装不上（2026-09-17 实测）**：`paddlepaddle` 最新 3.4.0 只发 cp313 wheel，cp314 在 PyPI 与 Paddle 官方索引均无；项目 `requires-python >=3.14,<3.15`、`.51` 也是 3.14。两个结果不同的处置：(a) **按 D14 退路**——一期扫描件进「不可读」人工队列＋登记技术债，等 Paddle 发 cp314 wheel 再接（零新依赖、零架构变化，推荐）；(b) 在 `.51` 另建 Python 3.13 sidecar venv 只跑 OCR，主进程用 subprocess 调（多一套解释器与计划任务，发版白名单要改，属技术方案审查、代理人未设 ⇒ 挂起等本人）。未答前本计划 Task 4/8 按 (a) 执行；⛔ 不换 MinerU（D14 明令）。

## 端到端提取验证记录（2026-09-17，已实测执行）

按 skill 步骤 6：把本计划全部 `python` 代码块按首行 `# 路径` 原样提取到 `/tmp/u0-verify`（rsync 仓库副本，排除 `.git/venv/data`），建独立 venv 装 `requirements.txt`＋六项轻依赖（实际拿到 numpy 2.5.3／pypdf 6.19.0／python-docx 1.2.0／reportlab 5.0.1／pillow 12.3.0／pymupdf 1.28.2，Python 3.14.6），然后：

- U0 十个测试文件：**67 passed**（首轮即绿，无转录修复）
- 全量 `pytest`（含 `tools/liaison/tests`）：1650 passed，**1 failed = `tools/liaison/tests/test_criteria_ledger_file.py::test_signed_off_row_has_no_evidence_yet`**——主仓库 main 上同样红，是 liaison 泳道既有问题，与本计划无关、本计划不碰
- `scripts.gen_pilot_samples`：20 份 × 4 形态生成成功（Mac 找到中文字体，扫描件已产）；`extract_text` 对合成 `S01.pdf`（reportlab STSong-Light）直抽 261 有效字符、中文完整可读；`S01.docx` 同
- `scripts.smoke_m2_deps`：六项轻依赖 ✅；paddlepaddle／paddleocr／torch ❌（未装，预期）
- `scripts.compare_models_m2` 无 key：四候选全部「跳过：环境变量 … 未设置」，markdown/json 正常落盘
- wheel 可得性（`pip download --only-binary=:all: --python-version 3.14`）：轻依赖六项 win_amd64 全 OK；`torch` win 2.14.0／mac 2.11.0 OK；`FlagEmbedding 1.4.2`、`paddleocr 3.7.0`（纯 py）OK；**`paddlepaddle` cp314 无 wheel**（PyPI 与官方 cpu 索引均无，cp313 有 3.4.0）⇒ 待裁决 #6
- 修正回写：依赖版本号改为实测值；`ocr_pdf` 改 `import pymupdf as fitz` 优先（`fitz` 已 deprecated）；`DEPS` 的 pymupdf 探针模块名同步
- **二次验证**：回写后清空副本再原样提取一遍，U0 十个测试文件 **67 passed**，`smoke_m2_deps` 的 pymupdf 行 ✅——Edit 未引入转录误差

## 自查清单（交付前）

- [x] 任务标题全部为三级 `### Task N:`（`grep -c '^### Task ' <本文件>` = 8）
- [x] Global Constraints 段与 CLAUDE.md 逐字一致（铁律 2/3/4/5 ＋ 合规红线 ＋ 部署约束 4 ＋ 白名单）
- [x] spec Requirement → Task：「模型对比定型的输入输出契约」→ Task 6/7/8；「逐维评分带证据回指」（schema 与 evidence 强制部分）→ Task 1/6；「首期字段抽取」「原文分片与字段回指」→ Task 1/2/6；「扫描件与不可读文件」→ Task 4；「四项验收指标可一键计算」（算法口径）→ Task 3；「评测集样本来源与访问控制」（live 拒收、目录规范）→ Task 6/8。其余 Requirement（两段式匹配的召回集配置、排序只做推荐、主观描述不入硬门槛、判例批改表导入、字段校对队列、解析留痕落库）属 U2–U6，不在 U0 范围。
- [x] 每个 Task 有确切路径、完整代码、确切命令与预期输出；无 TBD/TODO 占位
- [x] 类型名／签名跨 Task 一致：`TextSpan`／`split_into_spans`／`resolve_span_ref`（Task 2 → 3/6）、`ResumeFields.plain()`（Task 1 → 6）、`FieldAccuracy`（Task 3 → 6/7）、`Sample`／`load_samples`（Task 6 → 7 bench）、`extract_text`／`OcrUnavailable`（Task 4 → 7）
- [x] 副作用：本单元无 `effect_*` 节点、无库写；唯一持久化是 `JsonlAuditHook`（追加写、每次调用一行、按候选＋时间戳分文件，重跑不覆盖）
- [x] AI 评分证据：`RankResult.evidence` 必填（Task 1 测试）、反查失败整次不可用（Task 6 测试 `rank_ok == 2`）
