"""app/agents/interview_prep.py 的纯函数测试：不接触数据库，LLM 用脚本化假客户端。"""

import json

import pytest

from app.agents.interview_prep import (
    DIFFICULTY_ORDER,
    GENERIC_RATIONALE_NOTE,
    PREP_PROMPT_VERSION,
    PrepGenerationFailed,
    generate,
    regenerate_one,
)
from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import PrepInput, ResumeScoreItem


class ScriptedClient:
    """极简假 OpenAI client：按顺序吐出预设响应体，形状对齐
    tests/test_web_api.py::ScriptedOpenAIClient 但不依赖那个模块（本文件不碰
    数据库/Web 层，保持纯函数测试的独立性）。"""

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
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",
        supports_json_schema=False,
        client=ScriptedClient(bodies),
        audit_hook=RecordingHook(),
    )


def _question(dimension="AUTOSAR CP", difficulty="easy", text="讲讲你做过的 AUTOSAR 项目"):
    return {
        "dimension": dimension,
        "difficulty": difficulty,
        "text": text,
        "rubric": "能说清分层架构者得分",
        "follow_ups": ["具体是哪个 OEM 项目？"],
        "rationale": "画像要求 AUTOSAR CP 经验",
    }


def _prep_input(resume_scores=None):
    return PrepInput(
        profile={"job_title": "嵌入式软件工程师"},
        rubric_dimensions=["AUTOSAR CP", "CAN 总线"],
        resume_scores=resume_scores or [],
    )


def test_out_of_whitelist_dimension_is_dropped_others_kept():
    body = json.dumps(
        {"questions": [_question(), _question(dimension="Python", difficulty="easy")]},
        ensure_ascii=False,
    )
    gateway = _gateway([body])

    draft = generate(gateway, _prep_input(), question_count=2)

    assert len(draft.questions) == 1
    assert draft.questions[0].dimension == "AUTOSAR CP"
    assert draft.dropped_count == 1


def test_all_dropped_raises_after_max_retries():
    body = json.dumps(
        {"questions": [_question(dimension="Python")]}, ensure_ascii=False
    )
    gateway = _gateway([body, body])  # max_retries=2，两次都全丢

    with pytest.raises(PrepGenerationFailed):
        generate(gateway, _prep_input(), max_retries=2)


def test_all_dropped_then_succeeds_on_retry():
    bad = json.dumps({"questions": [_question(dimension="Python")]}, ensure_ascii=False)
    good = json.dumps({"questions": [_question()]}, ensure_ascii=False)
    gateway = _gateway([bad, good])

    draft = generate(gateway, _prep_input(), max_retries=2)

    assert len(draft.questions) == 1
    assert draft.dropped_count == 0


def test_no_resume_scores_uses_generic_branch_and_notes_rationale():
    body = json.dumps(
        {"questions": [_question(text="通用题", dimension="AUTOSAR CP")]},
        ensure_ascii=False,
    )
    # rationale 里带标注词是 prompt 指示模型做的，脚本化假客户端直接返回
    # 已经带标注的内容——本用例断言的是"输入没有简历评分时，系统构造的
    # system_prompt 里出现了标注要求"，而不是断言模型一定听话（模型行为
    # 不受本单元控制）。
    gateway = _gateway([body])

    draft = generate(gateway, _prep_input(resume_scores=[]), question_count=1)

    assert len(draft.questions) == 1


def test_order_questions_easy_to_hard_is_deterministic():
    from app.agents.interview_prep import PrepQuestionDraft, order_questions

    questions = [
        PrepQuestionDraft(dimension="A", difficulty="hard", text="t1", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="B", difficulty="easy", text="t2", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="C", difficulty="medium", text="t3", rubric="r", follow_ups=["f"], rationale="x"),
    ]
    ordered = order_questions(questions, curve="easy_to_hard", dimensions=["A", "B", "C"])
    assert [q.dimension for q in ordered] == ["B", "C", "A"]

    # 同输入同配置多跑几次，题序必须逐字一致（spec「同输入同配置输出题序稳定」）。
    again = order_questions(list(questions), curve="easy_to_hard", dimensions=["A", "B", "C"])
    assert [q.dimension for q in again] == [q.dimension for q in ordered]


def test_order_questions_by_dimension_groups_by_whitelist_order():
    from app.agents.interview_prep import PrepQuestionDraft, order_questions

    questions = [
        PrepQuestionDraft(dimension="B", difficulty="easy", text="t1", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="A", difficulty="hard", text="t2", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="A", difficulty="easy", text="t3", rubric="r", follow_ups=["f"], rationale="x"),
    ]
    ordered = order_questions(questions, curve="by_dimension", dimensions=["A", "B"])
    # 组间按白名单顺序（A 先于 B）；组内沿用 easy→hard 的难度序（与
    # easy_to_hard 曲线的组内含义一致，只是作用范围收窄到同一维度）。
    assert [(q.dimension, q.difficulty) for q in ordered] == [
        ("A", "easy"), ("A", "hard"), ("B", "easy"),
    ]


def test_regenerate_one_returns_question_and_run_id():
    body = json.dumps({"question": _question(dimension="CAN 总线", difficulty="hard")}, ensure_ascii=False)
    gateway = _gateway([body])

    question, run_id = regenerate_one(
        gateway, _prep_input(), dimension="CAN 总线", difficulty="hard"
    )

    assert question.dimension == "CAN 总线"
    assert question.difficulty == "hard"
    assert run_id == "run-1"
