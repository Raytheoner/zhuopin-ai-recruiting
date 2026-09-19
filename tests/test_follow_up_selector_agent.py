"""app/agents/follow_up_selector.py 的纯函数测试：不接触数据库，LLM 用脚本化
假客户端。"""
import json

import pytest

from app.agents.follow_up_selector import FOLLOW_UP_PROMPT_VERSION, select
from app.llm.gateway import LLMGateway, SchemaExtractionFailed
from app.schemas.interview_ai_input import FollowUpInput


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


def _gateway(bodies):
    return LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient(bodies),
    )


def _follow_up_input():
    return FollowUpInput(
        question_text="讲讲你的 AUTOSAR 项目经验",
        follow_ups=["具体分层设计是怎么做的", "遇到过哪些踩坑经历"],
        transcript="做过三年，用的是分层架构",
    )


def test_in_range_follow_up_index_is_selected():
    body = json.dumps({"decision": "follow_up", "follow_up_index": 1}, ensure_ascii=False)
    choice = select(_gateway([body]), _follow_up_input())
    assert choice.is_follow_up is True
    assert choice.follow_up_index == 1
    assert choice.out_of_range is False


def test_next_question_decision_is_not_a_follow_up():
    body = json.dumps({"decision": "next_question"}, ensure_ascii=False)
    choice = select(_gateway([body]), _follow_up_input())
    assert choice.is_follow_up is False
    assert choice.follow_up_index is None
    assert choice.out_of_range is False


def test_out_of_range_index_is_normalized_to_next_question():
    body = json.dumps({"decision": "follow_up", "follow_up_index": 99}, ensure_ascii=False)
    choice = select(_gateway([body]), _follow_up_input())
    assert choice.is_follow_up is False
    assert choice.out_of_range is True


def test_prompt_version_constant_is_recorded():
    assert FOLLOW_UP_PROMPT_VERSION == "interview-follow-up-v1"


def test_schema_extraction_failure_propagates_to_caller():
    # 两次都返回非法 JSON，网关重试耗尽后抛 SchemaExtractionFailed——不在
    # select() 内部吞掉，由调用方（voice_host/worker.py，Task 11）决定兜底。
    with pytest.raises(SchemaExtractionFailed):
        select(_gateway(["not json", "still not json", "nope"]), _follow_up_input())
