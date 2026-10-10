"""Offer/拒信共用的文书草稿 L3 Agent（offer-generation U2 tasks 2.2）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库是
app/graph/letter_nodes.py 的 effect_* 节点的事，facts/模板组装是同文件
compute_* 的事——本模块 ⛔ 不 import app.storage / app.graph（模块内 grep 无
storage 写入，tests/test_letter_drafter.py 有静态断言守着）。

AI 生成标识与 M1 JD 同串：复用 app/agents/jd_agent.enforce_ai_label（其
AI_LABEL_TEMPLATE 是唯一真源）。这样 U4 门禁 gate.py 的 AI_LABEL_PREFIX in body
判定与 docx 页眉回读都用同一串，不会出现「拒信有标识但门禁读不出来」的错配。
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from app.agents.jd_agent import enforce_ai_label
from app.llm.gateway import LLMGateway

LETTER_PROMPT_VERSION_TMPL = "letter-{kind}-v1"

_LETTER_SYSTEM_PROMPT = (
    "你是招聘文书助手。基于给定的文书模板与投递事实生成一份完整的 Offer/拒信正文。\n"
    "规则：\n"
    "1. 模板里的占位符（{candidate_name}/{job_title}/{department}/{start_date}/"
    "{report_to}）MUST 用 facts 里的对应字段填充，保持模板的段落结构、措辞与礼貌语气。\n"
    "2. MUST NOT 新增 facts 里没有的事实，尤其 MUST NOT 编造或估算任何薪资/薪酬/待遇数字"
    "——薪资位置保持留空，供 HR 手工填写。\n"
    "3. MUST NOT 输出评分、排名、硬门槛命中或任何「建议录用/建议淘汰」类文本。\n"
    "输出 JSON，字段：body(string)。"
)


class LetterFacts(BaseModel):
    """生成文书所需的投递事实。⛔ schema 层不得出现薪资类键——这是「薪资不入库」
    在生成输入侧的第二道防线（第一道是 offer 表无薪资列，已由 U1 落地）。"""

    candidate_name: str
    job_title: str
    department: str | None = None
    start_date: str | None = None
    report_to: str | None = None


class _LetterBodySchema(BaseModel):
    body: str


@dataclass(frozen=True)
class LetterDraft:
    kind: str
    body: str  # 已带 AI 生成标识
    run_id: str
    response_model: str | None
    prompt_version: str


def _prompt_version(kind: str) -> str:
    if kind not in ("offer", "rejection"):
        raise ValueError(f"kind 只能是 offer/rejection，收到: {kind!r}")
    return LETTER_PROMPT_VERSION_TMPL.format(kind=kind)


def compute_letter_draft(
    gateway: LLMGateway,
    *,
    kind: str,
    template_body: str,
    template_version: int,
    facts: LetterFacts,
    audit_context: dict | None = None,
) -> LetterDraft:
    """纯函数：按模板 + 投递事实生成一版文书草稿并带 AI 生成标识。

    ⚠️ user_prompt 末尾的「请求标识」nonce 是刻意的：temperature=0 下同一投递
    连续两次生成的 prompt 逐字相同，RecorderAuditHook 的确定性 id
    （{thread_id}:{node}:{input_hash}:{attempt}）会撞主键——第二次的 analysis_run
    被 SqliteSink 短路、run_id 与第一次相同，effect_persist_letter 随之被幂等
    短路 ⇒ 版本不递增，直接违反 spec「同一投递重复生成 MUST 产生新版本」。把
    nonce 写进实际发给模型的字节里，input_hash 每次不同、run_id 随之不同。"""
    prompt_version = _prompt_version(kind)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    nonce = uuid.uuid4().hex
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=_LETTER_SYSTEM_PROMPT,
        user_prompt=(
            f"kind={kind}\n模板版本={template_version}\n模板正文：\n{template_body}\n"
            f"投递事实：{facts.model_dump_json()}\n请求标识：{nonce}"
        ),
        schema=_LetterBodySchema,
        prompt_version=prompt_version,
        audit_context=audit_context,
    )
    body = enforce_ai_label(parsed.body, generated_at=generated_at)
    return LetterDraft(
        kind=kind,
        body=body,
        run_id=meta.run_id,
        response_model=meta.response_model,
        prompt_version=prompt_version,
    )
