"""群通知**回推**的接线门面（0930K / tasks 8.8）。

## 一、为什么要有这一层

`webhook.send_group_notify` 是通道门面（算 plan → 发 → 记），但它要求调用方先凑齐
`GroupWebhookSender` / 令牌桶 / 时钟 / 告警出口。凑齐的过程里藏着一条**无症状**的
失败方向：地址没配时 `build_group_webhook_sender` 抛 `GroupWebhookMissingError`，
调用方顺手一个 `except` 吞掉，就等于把那次拒发伪装成了成功——群里没消息，台账里
也没行，没有任何东西能事后指认"这一次本来该发"。

**0930J 的现场就是这个形状的另一半**：`send_group_notify` 在生产代码里零调用点，
于是 `liaison_group_notify` 恒 0 行，而 8.6 的第 3 条验收面永远拿不到样本。
⇒ 本层把两件事一起做死：把调用点接出来，并把"地址缺失"这条分支接成**拒发**。

## 二、它不是第二个通道实现

外发仍然是 `GroupWebhookSender.send_markdown`，节流与退避仍然是
`ratelimit.TokenBucket` + `webhook.effect_deliver_with_backoff`，台账仍然是
`store.effect_send_group_notify`（`effect_send_group_notify` effect 节点 ＋ 幂等键
`{thread_id}:effect_send_group_notify:{摘要}`）。地址缺失那条分支生成的也是一个
`MODE_REJECT` 的 `NotifyPlan`，走的是**同一个** `effect_send_group_notify`——所以
两种拒发（超限无法降级／地址缺失）在台账里是同一个终态、同一个幂等键形状、
同一处告警出口。⛔ 不需要、也不许另造一套幂等或第二张表。

## 三、纪律

- ⛔ 本模块不读时钟（`sleep` 与令牌桶由构造参数注入），不 `import time`。
- ⛔ 本模块不自己发 HTTP：传输层一律经 `transport` 注入。
- ⛔ 本目录禁止写 `with X:`（第 2 章事务扫描器），也 ⛔ 不许对正文做切片。
- ⛔ 本模块**没有**"发给谁"这个参数：收件对象由 webhook 地址决定，而地址只能来自
  `HR_LIAISON_GROUP_WEBHOOK`（内部值守群）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping

from tools.liaison.alerts import AlertSink
from tools.liaison.errors import GroupWebhookMissingError
from tools.liaison.notify.guard import (
    GROUP_WEBHOOK_CHANNEL,
    NotifyPlan,
    compute_missing_webhook_plan,
)
from tools.liaison.notify.ratelimit import TokenBucket
from tools.liaison.notify.store import NotifyRecord, effect_send_group_notify
from tools.liaison.notify.transport import Transport
from tools.liaison.notify.webhook import (
    build_group_webhook_sender,
    send_group_notify as send_group_notify_via_channel,
)

#: 回推文案。**只有三段**：会话、消息、任务摘要。
#:
#: ⛔ 正文本身不进这里（它可能是简历材料）：`summary` 由 `queue.compute_task_summary`
#: 机械截断产出（默认 ≤120 字符），与队列视图里显示的是**同一个字符串**；本条消息
#: 因此永远落在 `MODE_DIRECT`，附件承载那条路走不到——"完整正文见附件"这件事在
#: 回推里不会发生。
#: ⛔ 里面没有、也永远不许有收件对象字段或凭据：`msgid`/`chatid` 都是不透明标识。
TASK_RELAY_TEMPLATE = (
    "【值守通道·新任务已登记】\n"
    "来源会话：{thread_id}\n"
    "来源消息：{msgid}\n"
    "摘要：{summary}"
)


def compute_task_relay_text(*, thread_id: str, msgid: str, summary: str) -> str:
    """回推文案。**纯函数**（工程铁律 2 的形状）。⛔ 不读时钟、不记日志、不碰库。"""
    return TASK_RELAY_TEMPLATE.format(thread_id=thread_id, msgid=msgid, summary=summary)


def refuse_delivery(_plan: NotifyPlan) -> NotifyRecord:
    """`MODE_REJECT` 的 plan ⛔ 不该走到投递器：`effect_send_group_notify` 会提前短路。

    真走到了这里就**炸**——静默返回一个"发送完成"的记录，会在台账里留下一行假的
    `sent`，而那正是本目录所有注释都在防的那类谎。同一条纪律见
    `webhook.make_group_webhook_delivery` 对 `MODE_REJECT` 的处理。
    """
    raise AssertionError(
        "拒发分支不应该产生投递调用：MODE_REJECT 的 plan 由 effect_send_group_notify 提前短路"
    )


class GroupNotifyRelay:
    """把一条**已经入队**的任务摘要回推进值守群的唯一接线门面。

    调用方（`tools/liaison/__main__.handle_message_frame`）只回答三个问题：
    哪条会话（`thread_id`）、发什么（`text`）、告警走哪儿（`alert_sink`）。
    地址从哪来、桶怎么扣、退避怎么等、失败了怎么记——全在本类里面，⛔ 不外泄给调用方，
    否则第二个调用方一出现就会各自发明一套（那正是零调用点那半个缺口复活的方式）。
    """

    def __init__(
        self,
        *,
        bucket: TokenBucket,
        sleep: Callable[[float], None],
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._bucket = bucket
        self._sleep = sleep
        self._transport = transport
        self._env = env

    def send_group_notify(
        self,
        conn: sqlite3.Connection,
        *,
        thread_id: str,
        text: str,
        alert_sink: AlertSink,
    ) -> str | None:
        """回推一条并落**恰好一行**台账。返回终局状态；幂等命中返回 `None`。

        ⚠️ 地址**每条消息现读一次**（`load_group_webhook` 读环境）：配置改了不必重启
        值守进程，而"这台机器还没配地址"这件事也因此是**按次**判定、按次记账的。
        """
        try:
            sender = build_group_webhook_sender(env=self._env, transport=self._transport)
        except GroupWebhookMissingError as exc:
            return self._record_refusal(
                conn, thread_id=thread_id, text=text, alert_sink=alert_sink, exc=exc
            )
        return send_group_notify_via_channel(
            conn,
            text=text,
            sender=sender,
            bucket=self._bucket,
            alert_sink=alert_sink,
            sleep=self._sleep,
            thread_id=thread_id,
        )

    def _record_refusal(
        self,
        conn: sqlite3.Connection,
        *,
        thread_id: str,
        text: str,
        alert_sink: AlertSink,
        exc: GroupWebhookMissingError,
    ) -> str | None:
        """地址缺失 ⇒ 落一行 `rejected` ＋ 一条告警。**不上抛**（见类 docstring）。

        `exc.missing_names` 只含变量名，⛔ 不含取值——告警进的是台账与日志，
        那两处都不该出现 webhook 地址（它本身就是凭据，见 `followup.py` 同一条纪律）。
        """
        plan = compute_missing_webhook_plan(
            text, missing_name="、".join(exc.missing_names) or GROUP_WEBHOOK_CHANNEL.name
        )
        return effect_send_group_notify(
            conn,
            thread_id=thread_id,
            business_key=plan.digest,
            plan=plan,
            deliver=refuse_delivery,
            alert_sink=alert_sink,
            channel=GROUP_WEBHOOK_CHANNEL.name,
        )
