import json
from dataclasses import dataclass

import pytest

from app.agents.jd_agent import (
    AI_LABEL_PREFIX,
    AI_LABEL_TEMPLATE,
    UNKNOWN_GENERATED_AT,
    contains_discriminatory_language,
    enforce_ai_label,
    extract_label_generated_at,
    generate_jd,
    strip_ai_label,
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


# ── AI 标识的保护（tasks 7.5 纯函数层）──────────────────────────────────
#
# 合规红线：AI 生成的 JD 须带标识（《AI 生成合成内容标识办法》）。
# 下面这几个函数是那条红线在代码里的落点，⛔ 常规编辑不得删标识。

_TS = "2026-09-04T02:00:00+00:00"
_LABELLED = f"岗位职责：负责 ECU 底层软件开发。\n\n{AI_LABEL_TEMPLATE.format(generated_at=_TS)}"


def test_prefix_is_the_actual_head_of_the_template():
    """前缀与模板必须同源。写死一个和模板对不上的前缀，strip 会一行都剥不掉，
    而"标识删不掉"这个保护就悄悄失效了——不报错、不失败。"""
    assert AI_LABEL_TEMPLATE.startswith(AI_LABEL_PREFIX)


def test_strip_removes_the_label_line_only():
    assert strip_ai_label(_LABELLED) == "岗位职责：负责 ECU 底层软件开发。"


def test_strip_removes_every_label_line_even_if_duplicated():
    """反复编辑保存过的老文本里可能积了不止一行标识，必须全剥干净，
    否则重贴之后会越堆越多。"""
    label = AI_LABEL_TEMPLATE.format(generated_at=_TS)
    text = f"正文\n\n{label}\n{label}"
    assert strip_ai_label(text) == "正文"


def test_strip_tolerates_leading_whitespace_before_the_label():
    label = AI_LABEL_TEMPLATE.format(generated_at=_TS)
    assert strip_ai_label(f"正文\n\n   {label}") == "正文"


def test_strip_keeps_text_that_merely_mentions_ai():
    """只有以标识前缀开头的**整行**才是标识。正文里提到"AI"不能被吃掉。"""
    text = "岗位职责：开发 AI 相关的嵌入式模块。"
    assert strip_ai_label(text) == text


@pytest.mark.compliance
def test_strip_keeps_a_line_that_contains_the_prefix_but_is_not_a_label_line():
    """判据必须是"整行以前缀开头"，⛔ 不是"行内含前缀"。这条对抗样本真正嵌有
    `AI_LABEL_PREFIX` 这个子串（不是裸字"AI"），但不在行首——如果判据被放宽成
    "in"，这一整行会被当成标识行吃掉，而它其实只是正文里提到了这个说法。"""
    text = "参考同类岗位【AI 生成】风格撰写"
    assert AI_LABEL_PREFIX in text
    assert not text.startswith(AI_LABEL_PREFIX)
    assert strip_ai_label(text) == text


def test_extract_reads_back_the_generation_time():
    assert extract_label_generated_at(_LABELLED) == _TS


def test_extract_returns_none_when_there_is_no_label():
    assert extract_label_generated_at("岗位职责：负责 ECU 底层软件开发。") is None


def test_extract_round_trips_whatever_compose_produced():
    """回读的正则由模板拆出来，不是手抄的。这条用例守的是"模板改了、正则没跟着改"
    ——那会让回读静默失效，编辑一次就把真实生成时间换成"未知"。"""
    composed = f"正文\n\n{AI_LABEL_TEMPLATE.format(generated_at=_TS)}"
    assert extract_label_generated_at(composed) == _TS


def test_enforce_reattaches_the_label_after_a_user_deleted_it():
    """7.5 的核心：不管用户提交上来的文本里有没有标识，服务端一律重新贴。
    ⛔ 不是"检查他删没删"，是"重新贴"。"""
    edited = "岗位职责：负责 ECU 底层软件开发（HR 改过一版）。"
    result = enforce_ai_label(edited, generated_at=_TS)
    assert result.startswith(edited)
    assert result.endswith(AI_LABEL_TEMPLATE.format(generated_at=_TS))


def test_enforce_does_not_stack_labels():
    once = enforce_ai_label(_LABELLED, generated_at=_TS)
    twice = enforce_ai_label(once, generated_at=_TS)
    assert once == twice
    assert once.count(AI_LABEL_PREFIX) == 1


@pytest.mark.compliance
def test_enforce_reattaches_rather_than_checking_and_skipping():
    """7.5 的核心不变式：enforce_ai_label 是"无条件剥离再重贴"，不是"文本里
    已有标识就原样返回"。用**不同**的 generated_at 对一段已带旧标识的文本调用，
    结果必须换成新时间戳、旧标识那一行必须消失——check-and-return 式的实现会
    原样返回旧标识，这条会当场变红。"""
    new_ts = "2026-09-05T09:00:00+00:00"
    result = enforce_ai_label(_LABELLED, generated_at=new_ts)
    assert new_ts in result
    assert _TS not in result
    assert result.count(AI_LABEL_PREFIX) == 1


def test_enforce_on_empty_body_yields_the_label_alone():
    """正文被清空也必须留下标识，⛔ 不留下两个空行加一行标识那种脏输出。"""
    assert enforce_ai_label("", generated_at=_TS) == AI_LABEL_TEMPLATE.format(
        generated_at=_TS
    )


def test_unknown_generated_at_is_honest_not_a_fake_timestamp():
    """回读不出生成时间时用一个明说"没留存"的占位串。⛔ 不许拿"现在"冒充
    生成时间——一个错的时间比一个诚实的空缺更难被发现，也更难被审计接受。"""
    assert "未知" in UNKNOWN_GENERATED_AT
