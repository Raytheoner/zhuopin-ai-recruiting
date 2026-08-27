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


# 字段 → 给业务经理看的中文名（design.md 决策 7）。
#
# ⛔ 不要把这份表搬到前端。它必须和上面的字段定义待在同一个文件里：JobProfile
# 加字段时，前端不会跟着改，用户就会在缺口警示里看到一个英文 snake_case——那正是
# 本章要修的故障现象。放在这里，漏改会被
# tests/test_job_profile_schema.py::test_every_profile_field_has_a_chinese_label
# 当场抓到。
#
# **加字段时必须同时加一行。** 这不是文档义务，是会让测试变红的硬约束。
FIELD_LABELS: dict[str, str] = {
    "job_title": "岗位名称",
    "department": "所属部门",
    "headcount": "招聘人数",
    "education_requirement": "学历要求",
    "experience_years": "工作年限",
    "core_skills": "核心技能",
    "project_experience_requirement": "项目经验要求",
    "soft_skill_keywords": "软技能关键词",
    "autosar_experience": "AUTOSAR 经验",
    "functional_safety": "功能安全等级",
    "mcu_family": "MCU 平台",
    "diag_stack": "诊断与总线协议栈",
    "sop_projects": "量产项目经历",
    "toolchain": "开发工具链",
}

_UNKNOWN_FIELD_LABEL = "未命名字段"


def field_label(name: str) -> str:
    """字段名 → 中文名。

    未知字段名返回中性文案而**不是**原样返回英文标识：spec 明确要求"界面上不出现
    内部英文字段标识"，降级路径也不例外。上面那条完整性测试保证这个降级在真实
    字段上不可能发生——留着它是为了不让一次映射缺失变成 payload 组装时的 KeyError
    （那会在业务经理点确认的那一刻炸成 500）。
    """
    return FIELD_LABELS.get(name, _UNKNOWN_FIELD_LABEL)


def field_labels(names) -> list[str]:
    """按原顺序批量转中文名。下游按下标与英文列表配对，顺序必须保持。"""
    return [field_label(name) for name in names]
