import json
from dataclasses import FrozenInstanceError, fields

import pytest

from app.audit.events import (
    AI_ANALYSIS,
    BACKFILL,
    EVENT_TYPES,
    OUTBOUND_BLOCKED,
    CriterionScore,
    DecisionEvent,
)

# to_dict() 的键集合。这份清单是"留痕里允许出现什么"的白名单——加字段必须先
# 改这里，于是加字段这个动作本身会被 review 看见。
EXPECTED_KEYS = {
    "id",
    "event_type",
    "thread_id",
    "created_at",
    "application_id",
    "job_id",
    "configured_model",
    "response_model",
    "system_fingerprint",
    "prompt_version",
    "temperature",
    "input_hash",
    "rubric_version",
    "rubric_snapshot",
    "raw_response",
    "token_usage",
    "latency_ms",
    "scores",
    "message_type",
    "recipient",
    "content_hash",
    "blocked_reason",
    "confirmed_by",
    "evidence",
    "backfill_of",
    "error",
}


def _analysis_event(**overrides) -> DecisionEvent:
    payload = {
        "id": "thread-1:effect_record_analysis:sha256:abc",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "application_id": "app-1",
        "job_id": "job-1",
        "configured_model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_abc",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:abc",
        "rubric_version": "ecu-embedded-v2",
        "rubric_snapshot": {"criteria": [{"key": "autosar", "weight": 0.4}]},
        "raw_response": '{"scores": []}',
        "token_usage": {"total_tokens": 128},
        "latency_ms": 812.5,
        "scores": (
            CriterionScore(criterion_key="autosar", score=3.0, evidence_ref="resume-1#120-180"),
        ),
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


@pytest.mark.parametrize("bad_type", ["", "unknown_kind"])
def test_decision_event_rejects_unregistered_event_type(bad_type):
    """
    未登记的事件类型立刻抛，不留给下游 sink 去猜。fail-loud 与门禁的
    fail-closed 同一口径：未知就是错，不是默认值。
    """
    with pytest.raises(ValueError, match="未登记的事件类型"):
        _analysis_event(event_type=bad_type)


def test_all_registered_event_types_construct():
    for event_type in EVENT_TYPES:
        assert _analysis_event(event_type=event_type).event_type == event_type


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_to_dict_drops_empty_error_field(empty):
    """tasks 2.1：to_dict() 剔除空 error 字段。"""
    payload = _analysis_event(error=empty).to_dict()

    assert "error" not in payload


def test_to_dict_keeps_non_empty_error():
    payload = _analysis_event(error="镜像 append 失败").to_dict()

    assert payload["error"] == "镜像 append 失败"


def test_to_dict_keeps_fields_that_are_none():
    """
    ⚠️ 只有 error 被剔除，其余 None 一律保留。spec「供应商不返回部署指纹」要求
    该字段"记为空值、留痕照常写入"——把 None 一并剔掉，镜像里就分不清"这次调用
    没拿到指纹"和"这个版本的代码还没有指纹这个概念"。
    """
    payload = _analysis_event(system_fingerprint=None).to_dict()

    assert "system_fingerprint" in payload
    assert payload["system_fingerprint"] is None


def test_to_dict_keys_are_exactly_the_whitelist():
    assert set(_analysis_event(error="x").to_dict()) == EXPECTED_KEYS


@pytest.mark.parametrize("token", ["resume", "cv_text", "input_text", "raw_input", "plaintext"])
def test_no_field_name_smells_like_resume_plaintext(token):
    """
    spec「AI 调用的可复现留痕」：系统 MUST NOT 在留痕记录中存储简历原文。
    将来有人为"方便排查"加一个 input_text 字段，这条立刻变红。
    raw_response 是铁律 3 明令要存的模型响应，不在此列。
    """
    names = {field.name for field in fields(DecisionEvent)}
    assert not any(token in name for name in names), f"字段名疑似承载原文: {token}"


def test_scores_serialise_as_list_of_dicts():
    payload = _analysis_event().to_dict()

    assert payload["scores"] == [
        {"id": None, "criterion_key": "autosar", "score": 3.0, "evidence_ref": "resume-1#120-180"}
    ]


def test_scores_are_normalised_to_a_tuple():
    """传 list 也要变成 tuple——frozen dataclass 里挂一个可变列表是个陷阱。"""
    event = _analysis_event(scores=[CriterionScore("autosar", 3.0, "resume-1#1-2")])

    assert isinstance(event.scores, tuple)


def test_event_is_frozen():
    with pytest.raises(FrozenInstanceError):
        _analysis_event().id = "tampered"


def test_to_dict_is_json_serialisable_with_chinese():
    payload = _analysis_event(blocked_reason="外发总开关关闭\n第二行").to_dict()

    text = json.dumps(payload, ensure_ascii=False)
    assert "外发总开关关闭" in text
    assert json.loads(text)["blocked_reason"] == "外发总开关关闭\n第二行"


def test_events_module_carries_no_training_use_marker():
    """
    spec「留痕数据的用途限制」：该限制 SHALL 在数据结构层面以显式标注体现。
    U1 已在 analysis_run 的表注释里写过一遍；读代码的人和读 schema 的人不是
    同一批，模块层要再写一遍。
    """
    import app.audit.events as module

    doc = module.__doc__ or ""
    assert "禁止用作" in doc
    assert "训练" in doc
    assert "偏见" in doc  # 理由必须在场，不能只有一句禁令


def test_backfill_event_points_at_the_missing_record():
    event = DecisionEvent(
        id="backfill:run-1", event_type=BACKFILL, backfill_of="run-1", error="镜像缺行"
    )

    assert event.to_dict()["backfill_of"] == "run-1"


def test_outbound_event_carries_gate_evidence():
    event = DecisionEvent(
        id="thread-1:effect_record_outbound_audit:hash-1:False",
        event_type=OUTBOUND_BLOCKED,
        message_type="rejection_letter",
        blocked_reason="缺少 requires_confirmation",
        evidence={"requires_confirmation": None, "severity": ""},
    )

    assert event.to_dict()["evidence"] == {"requires_confirmation": None, "severity": ""}


def test_no_platform_vocabulary_leaked_into_the_event():
    """
    design「参考边界」：不拷贝参考文件。平台侧 AuditEvent 的四个字段一个都不许
    出现——字段名相同就是"照着抄了一遍"最直接的证据。
    """
    names = {field.name for field in fields(DecisionEvent)}
    assert names.isdisjoint({"scenario", "automation_level", "oem_context", "override_reason"})
