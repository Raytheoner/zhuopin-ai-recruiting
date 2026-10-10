"""邀约文案 L3 Agent（interview-scheduling U3 tasks 3.2）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库是
app/graph/invitation_nodes.py 的 effect_* 节点的事——本模块 ⛔ 不 import
app.storage / app.graph（tests/test_invitation_drafter.py 有 AST 静态断言守着）。

AI 生成标识与 M1 JD **同串**：复用 app/agents/jd_agent.enforce_ai_label
（AI_LABEL_TEMPLATE 是唯一真源）。门禁 app/outbound/gate.py 的 AI_LABEL_PREFIX 判定、
邀约页回读、以及"编辑不去标"三者因此读同一串，不会出现"文案有标识但门禁读不出来"的
错配（offer-generation U2 的 letter_drafter 同一做法）。

temperature 恒 0（LLMGateway.TEMPERATURE，铁律 5）；prompt_version='invite-v1'；
模型标识取 **API 响应**的 model 字段（LLMCallMeta.response_model，铁律 5）。
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.agents.jd_agent import enforce_ai_label
from app.llm.gateway import LLMGateway

INVITATION_PROMPT_VERSION = "invite-v1"

# 「联系人」缺省串（占位）。tasks 0.4 的人事部#3 回件（现用邀约话术样例）到后随模板 v1
# 内容一起替换；在那之前 ⛔ 不编造具体人名或电话。
DEFAULT_CONTACT_HINT = "卓品智能人力资源部"

_INVITATION_SYSTEM_PROMPT = (
    "你是招聘邀约文案助手。基于给定的邀约模板与场次事实，生成一份可直接发给候选人的"
    "面试邀约文案。\n"
    "规则：\n"
    "1. 模板里的占位符（{candidate_name}/{job_title}/{round}/{start_at}/{end_at}/"
    "{mode}/{location_or_link}/{interviewer_names}/{contact}）MUST 用给定字段填充，"
    "保持模板的段落结构与礼貌语气。\n"
    "2. MUST NOT 新增 facts 里没有的事实：不得编造地址、会议链接、薪资待遇、"
    "面试官的职级或任何未给出的安排；字段为空时保持留空。\n"
    "3. MUST NOT 输出任何评分、排名、硬门槛命中、淘汰理由，或『建议录用／建议淘汰』"
    "类文本——邀约文案是给候选人本人看的。\n"
    "4. 措辞礼貌简洁，不使用内部术语与流程缩写。\n"
    "输出 JSON，字段：body(string)。"
)


class InvitationSlotFacts(BaseModel):
    """生成邀约所需的场次事实。⛔ 字段里不得出现评分／排名／淘汰理由类键——
    这是「邀约文案不含评分信息」在生成输入侧的第一道防线（另两道是模板白名单与
    effect_* 只写文案表）。"""

    candidate_name: str
    job_title: str
    round: int
    start_at: str
    end_at: str
    mode: str
    location_or_link: str | None = None
    interviewer_names: list[str] = Field(default_factory=list)


class _InvitationBodySchema(BaseModel):
    body: str


@dataclass(frozen=True)
class InvitationDraft:
    body: str  # 已带 AI 生成标识
    run_id: str
    response_model: str | None
    prompt_version: str


def compute_invitation_draft(
    gateway: LLMGateway,
    *,
    facts: InvitationSlotFacts,
    template_body: str,
    template_version: str,
    contact_hint: str | None = None,
    audit_context: dict | None = None,
) -> InvitationDraft:
    """纯函数：按模板 + 场次事实生成一版邀约文案并带 AI 生成标识。

    ⚠️ user_prompt 末尾的「请求标识」nonce 是刻意的：temperature=0 下同一场次连续
    两次生成的 prompt 逐字相同，RecorderAuditHook 的确定性 id
    （{thread_id}:{node}:{input_hash}:{attempt}）会撞主键——第二次的 analysis_run 被
    SqliteSink 短路、run_id 与第一次相同，effect_persist_draft 随之被幂等短路 ⇒ 版本
    不递增，直接违反 spec「同一场次重复生成 MUST 产生新版本而不覆盖旧版本」。把 nonce
    写进实际发给模型的字节里，input_hash 每次不同、run_id 随之不同。
    （同款先例与实证：app/agents/letter_drafter.py::compute_letter_draft。）
    """
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    nonce = uuid.uuid4().hex
    contact = (contact_hint or "").strip() or DEFAULT_CONTACT_HINT
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=_INVITATION_SYSTEM_PROMPT,
        user_prompt=(
            f"模板版本={template_version}\n模板正文：\n{template_body}\n"
            f"场次事实：{facts.model_dump_json()}\n联系人：{contact}\n"
            f"请求标识：{nonce}"
        ),
        schema=_InvitationBodySchema,
        prompt_version=INVITATION_PROMPT_VERSION,
        audit_context=audit_context,
    )
    return InvitationDraft(
        body=enforce_ai_label(parsed.body, generated_at=generated_at),
        run_id=meta.run_id,
        response_model=meta.response_model,
        prompt_version=INVITATION_PROMPT_VERSION,
    )
