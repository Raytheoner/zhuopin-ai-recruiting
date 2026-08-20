from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# 由系统填写、不接受模型或用户直接作答的字段。追问的 field 落在这里面时
# 与"字段名不存在"同等处理（app/agents/intake_question.derive_question_id）。
# 放在 schema 模块而不是 intake_agent：intake_question 也要用，从 intake_agent
# 导入会形成 intake_agent → intake_question → intake_agent 的循环。
SYSTEM_MANAGED_FIELDS: frozenset[str] = frozenset({"unspecified_fields"})


class JobStatus(str, Enum):
    DRAFTING = "drafting"
    NEEDS_MANUAL = "needs_manual"
    APPROVED = "approved"
    ABANDONED = "abandoned"


class AutosarLayer(str, Enum):
    CP = "CP"
    AP = "AP"
    NONE = "无"


class FunctionalSafetyLevel(str, Enum):
    ASIL_A = "ASIL-A"
    ASIL_B = "ASIL-B"
    ASIL_C = "ASIL-C"
    ASIL_D = "ASIL-D"
    NONE = "无"
    CERTIFIED_ENGINEER = "FuSa工程师认证"


class SkillItem(BaseModel):
    name: str
    required: bool  # True = 必会, False = 加分


class SopProject(BaseModel):
    vehicle_model: str
    sop_date: str | None = None
    role: str
    is_mass_production: bool


class JobProfile(BaseModel):
    # 通用字段
    job_title: str
    department: str
    headcount: int = Field(ge=1)
    education_requirement: str
    experience_years: str  # 保留字符串以容纳"3-5年"这类区间表达
    core_skills: list[SkillItem] = Field(default_factory=list)
    project_experience_requirement: str | None = None
    soft_skill_keywords: list[str] = Field(default_factory=list)

    # ECU 行业特化字段
    autosar_experience: list[AutosarLayer] = Field(default_factory=list)
    functional_safety: FunctionalSafetyLevel = FunctionalSafetyLevel.NONE
    mcu_family: list[str] = Field(default_factory=list)
    diag_stack: list[str] = Field(default_factory=list)
    sop_projects: list[SopProject] = Field(default_factory=list)
    toolchain: list[str] = Field(default_factory=list)

    # 追问超限降级时标记哪些字段是"未指定"填充的
    unspecified_fields: list[str] = Field(default_factory=list)
