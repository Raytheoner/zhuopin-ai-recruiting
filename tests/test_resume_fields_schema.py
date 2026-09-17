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


def test_empty_string_requires_not_mentioned():
    with pytest.raises(ValidationError, match="not_mentioned"):
        TextField(value="", confidence=0.9)


def test_whitespace_only_string_requires_not_mentioned():
    with pytest.raises(ValidationError, match="not_mentioned"):
        TextField(value="   ", confidence=0.9)


def test_all_none_education_value_requires_not_mentioned():
    with pytest.raises(ValidationError, match="not_mentioned"):
        EducationField(value=EducationValue(), confidence=0.9)


def test_zero_is_valid_value():
    """0 is a real value, not empty, so it should pass without not_mentioned."""
    field = NumberField(value=0, confidence=0.9)
    assert field.value == 0
    assert not field.not_mentioned
