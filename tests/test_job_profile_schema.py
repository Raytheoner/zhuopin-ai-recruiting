import pytest
from pydantic import ValidationError

from app.schemas.job_profile import (
    FIELD_LABELS,
    SYSTEM_MANAGED_FIELDS,
    AutosarLayer,
    FunctionalSafetyLevel,
    JobProfile,
    JobStatus,
    SkillItem,
    SopProject,
    field_label,
    field_labels,
)


def test_minimal_valid_profile():
    profile = JobProfile(
        job_title="嵌入式软件工程师",
        department="研发部",
        headcount=1,
        education_requirement="本科及以上",
        experience_years="3-5年",
        core_skills=[SkillItem(name="AUTOSAR CP", required=True)],
        soft_skill_keywords=["沟通能力强"],
        autosar_experience=[AutosarLayer.CP],
        functional_safety=FunctionalSafetyLevel.ASIL_B,
        mcu_family=["英飞凌 Aurix TC3xx"],
        diag_stack=["UDS", "CAN-FD"],
        sop_projects=[
            SopProject(vehicle_model="X1", role="核心开发", is_mass_production=True)
        ],
        toolchain=["CANoe", "Vector"],
    )
    assert profile.headcount == 1
    assert profile.autosar_experience == [AutosarLayer.CP]


def test_headcount_must_be_positive():
    with pytest.raises(ValidationError):
        JobProfile(
            job_title="x",
            department="x",
            headcount=0,
            education_requirement="x",
            experience_years="x",
        )


def test_defaults_are_empty_not_none():
    profile = JobProfile(
        job_title="x",
        department="x",
        headcount=1,
        education_requirement="x",
        experience_years="x",
    )
    assert profile.core_skills == []
    assert profile.autosar_experience == []
    assert profile.unspecified_fields == []


def test_job_status_enum_values():
    assert {s.value for s in JobStatus} == {
        "drafting",
        "needs_manual",
        "approved",
        "abandoned",
    }


# --- 字段中文名映射（tasks 6.4，design.md 决策 7） ---------------------------


def test_every_profile_field_has_a_chinese_label():
    """
    design.md 决策 7 的机械保障：加字段忘了补中文名，这条当场失败。
    没有这条测试，漏改的表现是业务经理在警示块里看到一个英文 snake_case——
    而那正是本章要修的故障现象本身。
    """
    business_fields = set(JobProfile.model_fields) - set(SYSTEM_MANAGED_FIELDS)

    missing = sorted(business_fields - set(FIELD_LABELS))
    assert not missing, f"这些字段没有中文名：{missing}"

    extra = sorted(set(FIELD_LABELS) - business_fields)
    assert not extra, f"中文名映射里有字段表中不存在的键（字段被删了没跟）：{extra}"


def test_labels_are_chinese_and_never_leak_the_english_identifier():
    """spec：界面上不出现内部英文字段标识。中文名本身不许原样带英文标识。"""
    for name, label in FIELD_LABELS.items():
        assert label.strip(), f"{name} 的中文名是空的"
        assert name not in label, f"{name} 的中文名里混进了英文标识：{label}"


def test_field_label_never_returns_english_for_unknown_name():
    """降级也不许泄漏英文：未知字段名返回中性文案，不返回原名。"""
    assert field_label("toolchain") == FIELD_LABELS["toolchain"]
    assert "some_hallucinated_field" not in field_label("some_hallucinated_field")


def test_field_labels_preserves_order():
    """下游把英文列表与中文列表按下标配对，顺序错位会张冠李戴。"""
    assert field_labels(["toolchain", "headcount"]) == [
        FIELD_LABELS["toolchain"],
        FIELD_LABELS["headcount"],
    ]
