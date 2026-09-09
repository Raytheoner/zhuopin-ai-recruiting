"""群通知外发（tasks.md 第 6 章 / spec `liaison-group-notify`）。

⛔ 本包只做**内部值守群**的外发。不存在、也永远不许出现以候选人为收件对象的
参数或调用路径（6.7，由 tests/test_notify_boundaries.py 的 AST 断言守着）。
"""

from tools.liaison.notify.guard import (
    AIBOT_CHANNEL,
    GROUP_WEBHOOK_CHANNEL,
    NotifyPlan,
    compute_length_guard,
    compute_notify_digest,
    compute_notify_plan,
)
from tools.liaison.notify.ratelimit import TokenBucket, make_group_webhook_bucket
from tools.liaison.notify.store import (
    STATE_PENDING_RESEND,
    STATE_REJECTED,
    STATE_SENT,
    select_pending_resends,
)
from tools.liaison.notify.webhook import (
    GroupWebhookSender,
    build_group_webhook_sender,
    send_group_notify,
)

__all__ = [
    "AIBOT_CHANNEL",
    "GROUP_WEBHOOK_CHANNEL",
    "GroupWebhookSender",
    "NotifyPlan",
    "STATE_PENDING_RESEND",
    "STATE_REJECTED",
    "STATE_SENT",
    "TokenBucket",
    "build_group_webhook_sender",
    "compute_length_guard",
    "compute_notify_digest",
    "compute_notify_plan",
    "make_group_webhook_bucket",
    "select_pending_resends",
    "send_group_notify",
]
