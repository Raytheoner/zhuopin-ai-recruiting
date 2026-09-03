"""
候选人外发消息的具体形状。**这是唯一能喂进候选人门禁的形状**——
`contracts.OutboundGateMessage` 只是说明书（刻意不 runtime_checkable），
本模块给出那份说明书的一个具体实现。

⚠️ 两个默认值是 spec 的直接落地，⛔ 不要为了"让流程跑通"改掉它们：
`specs/outbound-approval-gate` 的「门禁覆盖范围」逐字写着拒信与邀约
「这两类 MUST **一律**判为高风险」。所以 `requires_confirmation` 默认 `True`、
`severity` 默认最高级——候选人信件**天生**需要人签字，这不是保守配置，是红线。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from app.channels.base import OutboundMessage
from app.outbound.contracts import MAX_SEVERITY


@dataclass(frozen=True)
class CandidateOutboundMessage:
    """一封待外发的候选人信件。字段名与 `GATE_FIELDS` 逐一对应。"""

    message_type: str
    recipient: str
    body: str
    severity: str = MAX_SEVERITY
    requires_confirmation: bool = True
    confirmed_by: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """
        草稿内容的稳定指纹。**⛔ 不含 `confirmed_by`。**

        它同时是三样东西的来源：`pending_approval.content_hash`、
        `(thread_id, content_hash)` 唯一索引、以及两个 effect 的幂等键。
        把签名算进去的话，`approve()` 带签名重走门禁时同一封信会变成"另一封"
        ——找不回队列里的原记录，重新被拦时还会插出第二行，5.2 的死锁防线当场
        失效。签名是**对这封信的处置**，不是这封信的内容。

        `sort_keys=True` 保证同一份内容渲染结果稳定（与
        `app/graph/nodes.py:message_business_key` 同一做法）。
        """
        material = {
            "message_type": self.message_type,
            "recipient": self.recipient,
            "body": self.body,
            "severity": self.severity,
            "requires_confirmation": self.requires_confirmation,
            "payload": self.payload,
        }
        blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def with_confirmation(self, confirmed_by: str) -> CandidateOutboundMessage:
        """带上确认人标识的同一封信。`content_hash()` 不变，见其 docstring。"""
        return replace(self, confirmed_by=confirmed_by)

    def to_outbound_message(self) -> OutboundMessage:
        """交给 `Channel.deliver` 的形状。⛔ 不改 `Channel` Protocol（tasks 5.5）。"""
        return OutboundMessage(
            type=self.message_type,
            payload={**self.payload, "body": self.body, "recipient": self.recipient},
        )
