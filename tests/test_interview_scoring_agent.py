"""app/agents/interview_scoring.py 的纯函数测试：不接触数据库，LLM 用脚本化
假客户端（形状与 tests/test_interview_prep_agent.py 一致，本文件不依赖它，
保持独立）。"""
import json

import pytest

from app.agents.interview_scoring import (
    SCORE_PROMPT_VERSION,
    ScoreCardDraft,
    ScoringGenerationFailed,
    score,
)
from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import ScoreInput, ScoreInputTurn


class ScriptedClient:
    def __init__(self, bodies: list[str]):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 10

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class RecordingHook:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return f"run-{len(self.calls)}"


def _gateway(bodies):
    return LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient(bodies), audit_hook=RecordingHook(),
    )


def _score_input():
    return ScoreInput(
        rubric_dimensions=["AUTOSAR CP", "沟通表达"],
        turns=[
            ScoreInputTurn(turn_id="t1", seq=1, question_text="讲讲你的 AUTOSAR 项目", answer_text="做过三年分层开发"),
            ScoreInputTurn(turn_id="t2", seq=2, question_text="怎么跟团队协作", answer_text="每周同步进度"),
        ],
    )


def _body(dimension="AUTOSAR CP", turn_id="t1", quote="做过三年分层开发"):
    return json.dumps(
        {
            "dimensions": [
                {
                    "dimension": dimension, "score": 4.0, "rationale": "回答扎实",
                    "evidence": {"turn_id": turn_id, "quote": quote},
                },
                {
                    "dimension": "沟通表达", "score": 4.0, "rationale": "回答扎实",
                    "evidence": {"turn_id": "t2", "quote": "每周同步进度"},
                },
            ],
            "overall_summary": "整体表现良好",
        },
        ensure_ascii=False,
    )


def test_score_returns_draft_with_run_id_and_response_model():
    gateway = _gateway([_body()])
    draft = score(gateway, _score_input())
    assert isinstance(draft, ScoreCardDraft)
    assert draft.dimensions[0].dimension == "AUTOSAR CP"
    assert draft.dimensions[0].turn_id == "t1"
    assert draft.dimensions[0].quote == "做过三年分层开发"
    assert draft.overall_summary == "整体表现良好"
    assert draft.run_id == "run-1"
    assert draft.response_model == "deepseek-chat-241226"
    assert draft.dropped_count == 0


def _single_dimension_score_input():
    return ScoreInput(
        rubric_dimensions=["AUTOSAR CP"],
        turns=[
            ScoreInputTurn(turn_id="t1", seq=1, question_text="讲讲你的 AUTOSAR 项目", answer_text="做过三年分层开发"),
            ScoreInputTurn(turn_id="t2", seq=2, question_text="怎么跟团队协作", answer_text="每周同步进度"),
        ],
    )


def test_score_drops_dimension_outside_whitelist_but_keeps_valid_ones():
    body = json.dumps(
        {
            "dimensions": [
                {"dimension": "AUTOSAR CP", "score": 4.0, "rationale": "r",
                 "evidence": {"turn_id": "t1", "quote": "做过三年分层开发"}},
                {"dimension": "薪资期望", "score": 3.0, "rationale": "r",
                 "evidence": {"turn_id": "t2", "quote": "每周同步进度"}},
            ],
            "overall_summary": "s",
        },
        ensure_ascii=False,
    )
    gateway = _gateway([body])
    draft = score(gateway, _single_dimension_score_input())
    assert [d.dimension for d in draft.dimensions] == ["AUTOSAR CP"]
    assert draft.dropped_count == 1


def test_score_raises_after_max_retries_when_all_dimensions_out_of_whitelist():
    bad_body = json.dumps(
        {
            "dimensions": [{"dimension": "薪资期望", "score": 3.0, "rationale": "r",
                             "evidence": {"turn_id": "t1", "quote": "x"}}],
            "overall_summary": "s",
        },
        ensure_ascii=False,
    )
    gateway = _gateway([bad_body, bad_body])
    with pytest.raises(ScoringGenerationFailed):
        score(gateway, _score_input(), max_retries=2)


def test_score_retries_and_succeeds_when_later_attempt_covers_all_dimensions():
    # 模拟模型第一次只评了 rubric 两个维度中的一个（非空、非全部越界丢弃）。
    partial_body = json.dumps(
        {
            "dimensions": [
                {"dimension": "AUTOSAR CP", "score": 4.0, "rationale": "r",
                 "evidence": {"turn_id": "t1", "quote": "做过三年分层开发"}},
            ],
            "overall_summary": "s",
        },
        ensure_ascii=False,
    )
    full_body = _body()
    gateway = _gateway([partial_body, full_body])
    draft = score(gateway, _score_input(), max_retries=2)
    assert isinstance(draft, ScoreCardDraft)
    assert {d.dimension for d in draft.dimensions} == {"AUTOSAR CP", "沟通表达"}


def test_score_raises_when_every_attempt_stays_incomplete():
    partial_body = json.dumps(
        {
            "dimensions": [
                {"dimension": "AUTOSAR CP", "score": 4.0, "rationale": "r",
                 "evidence": {"turn_id": "t1", "quote": "做过三年分层开发"}},
            ],
            "overall_summary": "s",
        },
        ensure_ascii=False,
    )
    gateway = _gateway([partial_body, partial_body])
    with pytest.raises(ScoringGenerationFailed):
        score(gateway, _score_input(), max_retries=2)


def test_score_uses_score_prompt_version_constant():
    hook = RecordingHook()
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_body()]), audit_hook=hook,
    )
    score(gateway, _score_input())
    assert hook.calls[0]["prompt_version"] == SCORE_PROMPT_VERSION == "interview-score-v1"
