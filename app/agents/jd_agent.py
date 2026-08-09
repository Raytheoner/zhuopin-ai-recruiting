from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pydantic import BaseModel

from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile

AI_LABEL_TEMPLATE = (
    "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {generated_at}。"
)

DISCRIMINATORY_PATTERNS: dict[str, list[str]] = {
    "性别": ["仅限男性", "仅限女性", "限男性", "限女性", "男性优先", "女性优先"],
    "年龄": ["35岁以下", "30岁以下", "限35周岁", "年轻化团队"],
    "婚育": ["已婚已育", "未婚未育", "限已婚"],
    "地域": ["仅限本地户口", "限本地生源"],
    "民族": ["仅限汉族"],
    "健康状况": ["无乙肝", "限健康人士"],
}

JD_SYSTEM_PROMPT = (
    "你是招聘文案助手。基于给定的岗位画像 JSON 生成招聘文案正文（不含 AI 标识，"
    "标识由系统另行拼接），包含岗位职责、任职要求（必备/加分分列）、简短团队介绍。"
    "文案中出现的技术要求必须能追溯到画像字段，不得凭空新增。"
    "禁止出现任何性别/年龄/婚育/地域/民族/健康状况相关的限制性表述。"
    "输出 JSON，字段：body(string)。"
)


class _JDBodySchema(BaseModel):
    body: str


@dataclass
class JDGenerationResult:
    text: str
    needs_manual: bool
    blocked_categories: list[str]


def contains_discriminatory_language(text: str) -> list[str]:
    hits = []
    for category, keywords in DISCRIMINATORY_PATTERNS.items():
        if any(keyword in text for keyword in keywords):
            hits.append(category)
    return hits


def _compose_with_label(body: str, generated_at: str) -> str:
    label = AI_LABEL_TEMPLATE.format(generated_at=generated_at)
    return f"{body}\n\n{label}"


def generate_jd(
    gateway: LLMGateway, profile: JobProfile, *, max_retries: int = 2
) -> JDGenerationResult:
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    last_body = ""
    last_hits: list[str] = []

    # max_retries 是总生成尝试次数（不是"首次+N次重试"），对齐 spec「连续 N 次仍
    # 出现则转人工处理」的字面语义——默认 2 次，与 job-description spec 的
    # 「拦截歧视性表述」Scenario 保持一致（不同于 LLMGateway.max_retries 的
    # "首次+N次重试"约定，两者是不同函数，各自的语义以各自测试为准）。
    for _ in range(max_retries):
        parsed = gateway.extract_structured(
            system_prompt=JD_SYSTEM_PROMPT,
            user_prompt=profile.model_dump_json(),
            schema=_JDBodySchema,
            prompt_version="jd-v1",
        )
        last_body = parsed.body
        last_hits = contains_discriminatory_language(parsed.body)

        if not last_hits:
            return JDGenerationResult(
                text=_compose_with_label(parsed.body, generated_at),
                needs_manual=False,
                blocked_categories=[],
            )

    return JDGenerationResult(
        text=_compose_with_label(last_body, generated_at),
        needs_manual=True,
        blocked_categories=last_hits,
    )
