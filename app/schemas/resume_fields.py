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
