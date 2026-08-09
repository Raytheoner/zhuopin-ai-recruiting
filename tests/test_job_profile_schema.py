import pytest
from pydantic import ValidationError

from app.schemas.job_profile import (
    AutosarLayer,
    FunctionalSafetyLevel,
    JobProfile,
    JobStatus,
    SkillItem,
    SopProject,
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
