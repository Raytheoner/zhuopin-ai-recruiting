"""追问选择 L3 Agent（design D17，U4 tasks 5.4）。

无副作用纯函数：只调用 LLM 网关做一次结构化输出，不写库、不访问简历/身份
字段（输入类型 `FollowUpInput` 结构上不含这些字段，U1 已交付，见
app/schemas/interview_ai_input.py）。追问选择只在预埋集合内选：模型返回
`follow_up_index` 越界（不在 `0..len(follow_ups)-1`）按"进入下一题"处理并
标记 `out_of_range`——语音主机上的 `voice_host/worker.py`（经
sync-to-voice-host.sh 拷贝的这份文件）据此调用。本文件不知道自己跑在哪台
机器上，不 import 任何 livekit/funasr/cosyvoice，也不 import app.storage。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.llm.gateway import LLMGateway
from app.schemas.follow_up_result import FollowUpChoiceOut
from app.schemas.interview_ai_input import FollowUpInput

FOLLOW_UP_PROMPT_VERSION = "interview-follow-up-v1"

_SYSTEM_PROMPT_TEMPLATE = (
    "你是资深技术面试官助手。当前题目：{question_text}\n"
    "预埋追问集合（编号从 0 开始）：\n{follow_ups}\n"
    "候选人本轮转写：{transcript}\n"
    "判断候选人的回答是否遗漏了 rubric 关键点、需要追问。如果需要，从预埋"
    "追问集合中选出编号最贴切的一条（decision=follow_up，给出"
    "follow_up_index）；如果回答已经充分，或没有一条预埋追问贴切，返回"
    "decision=next_question。⛔ 不得输出预埋集合之外的新问题文本。输出"
    "JSON，字段：decision、follow_up_index（decision=next_question 时可省略）。"
)


@dataclass(frozen=True)
class FollowUpChoice:
    is_follow_up: bool
    follow_up_index: int | None
    out_of_range: bool
    run_id: str
    response_model: str | None


def _build_system_prompt(follow_up_input: FollowUpInput) -> str:
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(follow_up_input.follow_ups))
    return _SYSTEM_PROMPT_TEMPLATE.format(
        question_text=follow_up_input.question_text,
        follow_ups=numbered,
        transcript=follow_up_input.transcript,
    )


def select(
    gateway: LLMGateway, follow_up_input: FollowUpInput, *, audit_context: dict | None = None,
) -> FollowUpChoice:
    """越界／`next_question` 都归一为"进入下一题"（spec Scenario「模型输出不
    在集合内」）；只有 `decision=follow_up` 且 index 落在
    `0..len(follow_ups)-1` 内才是真正的追问。⛔ 不重试——追问选择的默认安全
    回退本来就是"下一题"，重试对时延预算（design D18 <800ms 端到端）不划算。
    `SchemaExtractionFailed`/`LLMProviderUnavailable` 不在这里捕获，原样上抛，
    由调用方决定兜底（本计划「设计决策 11」，见 Task 11）。"""
    system_prompt = _build_system_prompt(follow_up_input)
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=system_prompt,
        user_prompt=follow_up_input.model_dump_json(),
        schema=FollowUpChoiceOut,
        prompt_version=FOLLOW_UP_PROMPT_VERSION,
        audit_context=audit_context,
    )

    if parsed.decision != "follow_up":
        return FollowUpChoice(
            is_follow_up=False, follow_up_index=None, out_of_range=False,
            run_id=meta.run_id, response_model=meta.response_model,
        )

    index = parsed.follow_up_index
    if index is None or not (0 <= index < len(follow_up_input.follow_ups)):
        return FollowUpChoice(
            is_follow_up=False, follow_up_index=None, out_of_range=True,
            run_id=meta.run_id, response_model=meta.response_model,
        )

    return FollowUpChoice(
        is_follow_up=True, follow_up_index=index, out_of_range=False,
        run_id=meta.run_id, response_model=meta.response_model,
    )
