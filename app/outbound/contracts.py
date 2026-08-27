"""门禁的消息契约与登记表。

本模块**只有常量与类型**，零判定逻辑、零 import 副作用模块——判定在
`gate.py`，副作用在 U5。
"""

from __future__ import annotations

from typing import Protocol


class OutboundGateMessage(Protocol):
    """门禁判定所需字段的说明书。

    ⚠️ **这不是运行时校验，而且刻意没有加 `@runtime_checkable`。**
    fail-closed 的前提正是"来的东西可能什么属性都没有"：那样的消息必须
    走完判定、被判拦截、带着证据进留痕，而不是在门口被一个 isinstance
    挡回去（挡回去就没有拦截留痕，误拦就只能等业务方投诉）。
    `gate.py` 逐字段试读，读不到即未知，未知即拦截。
    """

    message_type: str
    requires_confirmation: bool
    severity: str
    recipient: str
    body: str
    confirmed_by: str | None


# 已登记的候选人外发消息类型。⛔ 往这个集合里加取值 = 多开一个候选人
# 外发口子，属 CLAUDE.md 决策代理表的**不可代**项（"候选人对外通道的
# 开关：拒信/邀约对外发送"），必须由 Shao Peishen 本人拍板。
# 不在本集合中的类型一律拦截——未知类型即拦截（spec「fail-closed 判定语义」）。
REGISTERED_MESSAGE_TYPES: frozenset[str] = frozenset(
    {"rejection_letter", "interview_invitation"}
)

# 风险等级词表，**从低到高有序**。不在表内的取值一律视为"未知"→ 拦截；
# 表内最高级 MAX_SEVERITY 同样拦截。于是实际能过闸的只有 low / medium。
KNOWN_SEVERITIES: tuple[str, ...] = ("low", "medium", "high")
MAX_SEVERITY: str = KNOWN_SEVERITIES[-1]

# 门禁会去读的六个属性名，顺序固定（证据字典的键序由它决定，便于 U6 对账
# 时逐字比对）。⛔ 门禁不读这六个之外的任何属性——尤其不读
# `requires_confirmation` 的任何同义别名，"消息自称"只有这一个入口。
GATE_FIELDS: tuple[str, ...] = (
    "message_type",
    "requires_confirmation",
    "severity",
    "recipient",
    "body",
    "confirmed_by",
)
