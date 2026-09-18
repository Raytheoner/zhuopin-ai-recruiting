"""prep 出题 L3 Agent（voice-structured-interview U2 tasks 3.1/3.2/3.3）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库
是 app/graph/interview_prep_nodes.py 的 effect_* 节点的事，画像/rubric/简历
评分的组装是同文件 compute_prep 的事——本模块不 import app.storage。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import PrepInput

PREP_PROMPT_VERSION = "interview-prep-v1"

# 难度曲线的显式偏序，⛔ 不依赖字符串默认排序（"easy" < "hard" < "medium"
# 按字典序排列是错的）。interview-prep-question-engine spec「难度曲线」。
DIFFICULTY_ORDER: tuple[str, ...] = ("easy", "medium", "hard")

# spec Scenario「简历评分尚未完成」：走通用题分支时，rationale 必须标注这句话
# （或语义等价的可断言字符串），供测试与将来审计读。
GENERIC_RATIONALE_NOTE = "无简历弱点输入"

_PREP_SYSTEM_PROMPT_TEMPLATE = (
    "你是资深技术面试官。基于给定的岗位画像、rubric 维度白名单与该候选人的简历"
    "弱点，出 {question_count} 道结构化面试题，覆盖尽量多的 rubric 维度。"
    "每道题的 dimension 字段 MUST 是 rubric 维度白名单中的一个原样值，MUST NOT "
    "发明白名单外的维度。每道题 MUST 带：dimension（维度）、difficulty"
    "（easy/medium/hard 之一）、text（题面）、rubric（该题的评分标准，分档"
    "描述）、follow_ups（至少 1 条预埋追问，字符串数组）、rationale（为什么问"
    "这题，MUST 指向画像维度或具体简历弱点）。"
    "rubric 维度白名单：{dimensions}。"
    "{resume_note}"
    "输出 JSON，字段：questions(array)。"
)


class _PrepQuestionSchema(BaseModel):
    dimension: str
    difficulty: str
    text: str
    rubric: str
    follow_ups: list[str] = Field(min_length=1)
    rationale: str


class _PrepDraftSchema(BaseModel):
    questions: list[_PrepQuestionSchema]


class _SinglePrepQuestionSchema(BaseModel):
    question: _PrepQuestionSchema


@dataclass(frozen=True)
class PrepQuestionDraft:
    dimension: str
    difficulty: str
    text: str
    rubric: str
    follow_ups: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True)
class PrepDraft:
    questions: list[PrepQuestionDraft]
    dropped_count: int
    run_id: str
    response_model: str | None


class PrepGenerationFailed(Exception):
    """模型返回的题目全部越界丢弃后，本次生成判失败（spec Scenario「维度越界」
    「全部被丢弃则本次生成判失败、可重试」）。"""


def _build_system_prompt(prep_input: PrepInput, *, question_count: int) -> str:
    resume_note = (
        ""
        if prep_input.resume_scores
        else f"该候选人暂无简历评分结果，请只按画像出通用题，并在每题 rationale "
        f"中标注「{GENERIC_RATIONALE_NOTE}」。"
    )
    return _PREP_SYSTEM_PROMPT_TEMPLATE.format(
        question_count=question_count,
        dimensions="、".join(prep_input.rubric_dimensions),
        resume_note=resume_note,
    )


def _to_draft(item: _PrepQuestionSchema) -> PrepQuestionDraft:
    return PrepQuestionDraft(
        dimension=item.dimension,
        difficulty=item.difficulty,
        text=item.text,
        rubric=item.rubric,
        follow_ups=list(item.follow_ups),
        rationale=item.rationale,
    )


def _filter_whitelisted(
    items: list[_PrepQuestionSchema], *, dimensions: list[str]
) -> tuple[list[PrepQuestionDraft], int]:
    allowed = set(dimensions)
    kept: list[PrepQuestionDraft] = []
    dropped = 0
    for item in items:
        if item.dimension in allowed:
            kept.append(_to_draft(item))
        else:
            dropped += 1
    return kept, dropped


def _difficulty_rank(difficulty: str) -> int:
    # 白名单外的难度值排到最后，⛔ 不抛异常——模型偶尔吐出白名单外的难度值不
    # 应该让整次生成失败（那不是维度越界，spec 没有把它列为拒绝条件）。
    try:
        return DIFFICULTY_ORDER.index(difficulty)
    except ValueError:
        return len(DIFFICULTY_ORDER)


def order_questions(
    questions: list[PrepQuestionDraft], *, curve: str, dimensions: list[str]
) -> list[PrepQuestionDraft]:
    """按难度曲线策略排序，确定性、可重复（interview-prep-question-engine
    spec「难度曲线」：同输入同配置输出题序稳定）。"""
    if curve == "by_dimension":
        dimension_rank = {name: i for i, name in enumerate(dimensions)}
        return sorted(
            questions,
            key=lambda q: (dimension_rank.get(q.dimension, len(dimensions)), _difficulty_rank(q.difficulty)),
        )
    # 默认 easy_to_hard：⛔ 不用 Python list.sort 的默认稳定性掩盖字符串比较——
    # 显式用 _difficulty_rank 作 key，保证结果与字符串默认排序无关。
    return sorted(questions, key=lambda q: _difficulty_rank(q.difficulty))


def generate(
    gateway: LLMGateway,
    prep_input: PrepInput,
    *,
    curve: str = "easy_to_hard",
    question_count: int = 10,
    max_retries: int = 2,
    audit_context: dict | None = None,
) -> PrepDraft:
    """L3 Agent：纯函数，只调 LLM 网关。

    重试判据不是"命中歧视词"（那是 jd_agent 的判据），是"本轮题目全部越界
    丢弃"（spec Scenario「维度越界」：单题越界丢弃+计数、其余保留；全丢才
    判失败可重试）。max_retries 是总生成尝试次数（不是"首次+N次重试"），
    与 jd_agent.generate_jd 的既有约定一致。
    """
    system_prompt = _build_system_prompt(prep_input, question_count=question_count)
    last_dropped = 0
    last_run_id = ""
    last_response_model: str | None = None

    for _ in range(max_retries):
        parsed, meta = gateway.extract_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=prep_input.model_dump_json(),
            schema=_PrepDraftSchema,
            prompt_version=PREP_PROMPT_VERSION,
            audit_context=audit_context,
        )
        kept, dropped = _filter_whitelisted(
            parsed.questions, dimensions=prep_input.rubric_dimensions
        )
        last_dropped = dropped
        last_run_id = meta.run_id
        last_response_model = meta.response_model

        if kept:
            ordered = order_questions(
                kept, curve=curve, dimensions=prep_input.rubric_dimensions
            )
            return PrepDraft(
                questions=ordered,
                dropped_count=dropped,
                run_id=meta.run_id,
                response_model=meta.response_model,
            )

    raise PrepGenerationFailed(
        f"{max_retries} 次尝试后题目全部越界丢弃（最近一次丢弃 {last_dropped} "
        f"道，run_id={last_run_id}，response_model={last_response_model}）"
    )


def regenerate_one(
    gateway: LLMGateway,
    prep_input: PrepInput,
    *,
    dimension: str,
    difficulty: str,
    audit_context: dict | None = None,
) -> tuple[PrepQuestionDraft, str]:
    """确认页"重生成单题"：只针对指定维度/难度再出一道题，不重跑整套（换掉
    这一道，而不是重新生成一遍全套再挑一道——后者会让业务经理已经改过的其余
    题目跟着漂移语气）。返回 (题目, 本次调用的 run_id)。"""
    system_prompt = (
        f"你是资深技术面试官。针对岗位画像的「{dimension}」维度、难度"
        f"「{difficulty}」，只出 1 道结构化面试题（不要输出其他维度或其他难度）。"
        "题目 MUST 带：dimension（固定为给定维度）、difficulty（固定为给定"
        "难度）、text（题面）、rubric（评分标准）、follow_ups（至少 1 条预埋"
        "追问）、rationale（为什么问这题）。"
        f"该岗位 rubric 维度白名单：{'、'.join(prep_input.rubric_dimensions)}。"
        "输出 JSON，字段：question(object)。"
    )
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=system_prompt,
        user_prompt=prep_input.model_dump_json(),
        schema=_SinglePrepQuestionSchema,
        prompt_version=PREP_PROMPT_VERSION,
        audit_context=audit_context,
    )
    return _to_draft(parsed.question), meta.run_id
