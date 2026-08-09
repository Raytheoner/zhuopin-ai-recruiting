import json
from dataclasses import dataclass

from app.agents.jd_agent import (
    contains_discriminatory_language,
    generate_jd,
)
from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list
    usage: object = None


class FakeChatCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeChatCompletions(responses)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


def make_gateway(responses):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses),
    )


def make_profile():
    return JobProfile(
        job_title="嵌入式软件工程师",
        department="研发部",
        headcount=1,
        education_requirement="本科及以上",
        experience_years="3-5年",
    )


def test_detects_gender_keyword():
    assert "性别" in contains_discriminatory_language("仅限男性应聘")


def test_no_false_positive_on_clean_text():
    assert contains_discriminatory_language("负责嵌入式软件开发与调试") == []


def test_generate_jd_injects_ai_label_and_returns_clean_text():
    gateway = make_gateway([json.dumps({"body": "负责嵌入式软件开发与调试"})])

    result = generate_jd(gateway, make_profile())

    assert "AI 生成" in result.text
    assert "负责嵌入式软件开发与调试" in result.text
    assert result.needs_manual is False
    assert result.blocked_categories == []


def test_regenerates_once_on_discriminatory_hit_then_succeeds():
    gateway = make_gateway(
        [
            json.dumps({"body": "仅限男性应聘"}),
            json.dumps({"body": "负责嵌入式软件开发与调试"}),
        ]
    )

    result = generate_jd(gateway, make_profile())

    assert result.needs_manual is False
    assert "仅限男性" not in result.text
    assert len(gateway._client.chat.completions.calls) == 2  # type: ignore[attr-defined]


def test_needs_manual_after_two_consecutive_hits():
    gateway = make_gateway(
        [
            json.dumps({"body": "仅限男性应聘"}),
            json.dumps({"body": "限男性，35岁以下"}),
        ]
    )

    result = generate_jd(gateway, make_profile(), max_retries=2)

    assert result.needs_manual is True
    assert "性别" in result.blocked_categories
