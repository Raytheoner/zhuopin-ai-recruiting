"""群通知外发（tasks.md 第 6 章 / spec `liaison-group-notify`）。

⛔ 本包只做**内部值守群**的外发。不存在、也永远不许出现以候选人为收件对象的
参数或调用路径（6.7，由 tests/test_notify_boundaries.py 的 AST 断言守着）。

**再导出面的口径（TD-29 ①）**：一个概念的**全部成员一起导出**，⛔ 不许只导一半。
原先 `STATE_*` 导出而 `MODE_*` 不导出、`transport` 一个名字都没导出（调用方从包根
拿不到 `WebhookTransportError`，于是只能去 import 子模块，包根这层边界就形同虚设）。
⚠️ `AIBOT_CHANNEL` 今天在生产代码里零消费者，仍然导出——它与 `GROUP_WEBHOOK_CHANNEL`
是同一个概念的两个成员，只导一个正是本条债要消灭的那种不对称。
"""

from tools.liaison.notify.guard import (
    AIBOT_CHANNEL,
    GROUP_WEBHOOK_CHANNEL,
    MODE_DEGRADED,
    MODE_DIRECT,
    MODE_REJECT,
    NotifyPlan,
    compute_length_guard,
    compute_notify_digest,
    compute_notify_plan,
)
from tools.liaison.notify.ratelimit import (
    TokenBucket,
    get_group_webhook_bucket,
    make_group_webhook_bucket,
    reset_group_webhook_bucket,
)
from tools.liaison.notify.store import (
    GROUP_NOTIFY_THREAD_ID,
    STATE_PENDING_RESEND,
    STATE_REJECTED,
    STATE_SENT,
    NotifyRecord,
    effect_send_group_notify,
    select_pending_resends,
)
from tools.liaison.notify.transport import (
    WEBHOOK_TIMEOUT_SECONDS,
    Transport,
    UrllibTransport,
    WebhookResponse,
    WebhookTransportError,
)
from tools.liaison.notify.webhook import (
    GroupWebhookSender,
    build_group_webhook_sender,
    send_group_notify,
)

__all__ = [
    "AIBOT_CHANNEL",
    "GROUP_NOTIFY_THREAD_ID",
    "GROUP_WEBHOOK_CHANNEL",
    "GroupWebhookSender",
    "MODE_DEGRADED",
    "MODE_DIRECT",
    "MODE_REJECT",
    "NotifyPlan",
    "NotifyRecord",
    "STATE_PENDING_RESEND",
    "STATE_REJECTED",
    "STATE_SENT",
    "Transport",
    "TokenBucket",
    "UrllibTransport",
    "WEBHOOK_TIMEOUT_SECONDS",
    "WebhookResponse",
    "WebhookTransportError",
    "build_group_webhook_sender",
    "compute_length_guard",
    "compute_notify_digest",
    "compute_notify_plan",
    "effect_send_group_notify",
    "get_group_webhook_bucket",
    "make_group_webhook_bucket",
    "reset_group_webhook_bucket",
    "select_pending_resends",
    "send_group_notify",
]
