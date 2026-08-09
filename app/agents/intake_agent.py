from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.gateway import LLMGateway
from pydantic import BaseModel

MAX_ROUNDS = 5
MAX_QUESTIONS_PER_ROUND = 3

SYSTEM_PROMPT = (
    "你是招聘助手。判断用户消息是否是用人需求；如果是，基于 ECU 行业知识"
    "生成至多 3 个追问问题，并把能确定的字段整理进 profile_patch（只放本轮新确定的字段，"
    "不要重复历史已有字段）。如果不是用人需求，questions 里放一句引导语，"
    "is_job_related=false，profile_patch 为空对象。"
    "输出 JSON，字段：is_job_related(bool), questions(string[]), profile_patch(object), "
    "unspecified_fields(string[], 可选)。"
)


class _IntakeTurnSchema(BaseModel):
    is_job_related: bool
    questions: list[str] = []
    profile_patch: dict = {}
    unspecified_fields: list[str] = []


@dataclass
class IntakeTurnResult:
    is_job_related: bool
    questions: list[str]
    profile_patch: dict
    is_complete: bool
    unspecified_fields: list[str] = field(default_factory=list)


def run_intake_turn(
    gateway: LLMGateway, *, history: list[dict], round_count: int
) -> IntakeTurnResult:
    user_prompt = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history)

    parsed = gateway.extract_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=_IntakeTurnSchema,
        prompt_version="intake-v1",
    )

    if not parsed.is_job_related:
        return IntakeTurnResult(
            is_job_related=False,
            questions=parsed.questions or ["没听懂是不是用人需求，可以试试：'要招一个做XX的工程师'"],
            profile_patch={},
            is_complete=False,
        )

    at_round_limit = round_count >= MAX_ROUNDS
    questions = [] if at_round_limit else parsed.questions[:MAX_QUESTIONS_PER_ROUND]

    return IntakeTurnResult(
        is_job_related=True,
        questions=questions,
        profile_patch=parsed.profile_patch,
        is_complete=at_round_limit or not questions,
        unspecified_fields=parsed.unspecified_fields if at_round_limit else [],
    )
