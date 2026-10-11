from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class OutboundMessage:
    type: str  # "question" | "confirmation_prompt" | "jd_result" | "needs_manual"
    #          | "offer_approval_todo"（offer-generation U3：Offer 审批待办，内部通知）
    payload: dict


class Channel(Protocol):
    def deliver(self, thread_id: str, message: OutboundMessage) -> None: ...

    def latest(self, thread_id: str) -> OutboundMessage | None: ...
