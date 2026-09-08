"""中断告警：文本怎么写、往哪送。

**本章只定接口 + 日志实现**（opener 约束 3）。真实的群 webhook 外发是第 6 章
（`liaison-group-notify`，带令牌桶限流与长度守卫）——⛔ 本模块不许出现任何
网络调用，`test_alerts_module_has_no_outbound_channel` 守着这条。

⛔ 不许写 `with`（见 session.py 模块 docstring 第 1 条：第 2 章的事务扫描器
会把任何 `with <名字|属性|调用>:` 判为隐式提交违规）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

logger = logging.getLogger(__name__)

#: 7.4 逐字要求的那句话。⛔ 改这句之前先读 spec：它是被 Scenario 直接断言的文本。
ALERT_RESEND_SENTENCE = "该时段消息可能未收到、请重发"

#: ⛔ 告警文本里绝不许出现的说法。
#:
#: 幂等防的是同一条消息被处理两次，**补不回从未到达的消息**（design D3 逐字）。
#: 一旦告警里写了"已由幂等机制保障"，收信人就不会重发——一个真实的缺口被一句
#: 让人安心的话盖住，而这正是本章存在的理由。
FORBIDDEN_ALERT_CLAIMS = (
    "幂等",
    "重复投递",
    "已覆盖",
    "已被覆盖",
    "无需重发",
    "不会丢",
    "不会遗漏",
    "已自动补收",
)


class AlertSink(Protocol):
    """告警的出口。**本章只定这个形状**，真实群通知在第 6 章接同一个形状。

    约定：送不出去就 `raise`。⛔ 不许在实现里自己吞掉异常然后假装送到了——
    调用方 `effect_emit_outage_alert` 需要靠异常判定"这条要留到下次重发"。
    """

    def send(self, text: str) -> None: ...


class LoggingAlertSink:
    """本章唯一实现：写本地运行日志。

    ⛔ 不是"临时占位"。第 6 章接上真实群通知之后，这个实现仍然有用武之地：
    单机灰度、测试、以及真实通道自己也挂掉时的兜底。
    """

    def __init__(self, target_logger: logging.Logger | None = None) -> None:
        self._logger = target_logger if target_logger is not None else logger

    def send(self, text: str) -> None:
        self._logger.warning("%s", text)


def compute_outage_duration_text(seconds: int) -> str:
    """把秒数说成人话。纯函数（铁律 2）。"""
    if seconds < 0:
        raise ValueError(f"中断时长不可能是负数：{seconds}")
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} 小时")
    if hours or minutes:
        parts.append(f"{minutes} 分")
    parts.append(f"{secs} 秒")
    return " ".join(parts)


def compute_outage_alert_text(started_at: str, recovered_at: str) -> str:
    """拼一条中断告警。纯函数：不读库、不写日志、不发送（铁律 2）。

    入参是 `session.format_instant` 产出的 ISO8601 字面量；展示时截到秒——
    微秒是键的精度需求，不是给人看的。
    """
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(recovered_at)
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        raise ValueError(
            f"恢复时间早于中断时间，参数可能传反了：started_at={started_at} "
            f"recovered_at={recovered_at}"
        )
    return (
        "【HR 值守通道·连接中断】"
        f"{start.strftime('%Y-%m-%d %H:%M:%S')} 至 {end.strftime('%Y-%m-%d %H:%M:%S')}"
        f"（持续 {compute_outage_duration_text(seconds)}）连接中断，期间无法接收消息。"
        f"{ALERT_RESEND_SENTENCE}。"
    )


def effect_emit_outage_alert(sink: AlertSink, text: str) -> bool:
    """把告警送出去。**⛔ 永不抛异常**，返回是否送成功。

    7.5 逐字：告警通道失败只记本地日志、⛔ 不中止接收。"不中止"在这一层的
    可执行形式就是这个 try/except——把异常抛给接收循环，一次告警失败就能打断
    消息接收，而这两件事毫无关系。

    返回 False ⇒ 调用方 ⛔ 不许标记 `alerted_at` ⇒ 下次启动会重新扫到并补发。
    ⛔ 捕获的是 `Exception` 而不是 `BaseException`：`KeyboardInterrupt` 与
    `SystemExit` 必须能停下服务。
    """
    try:
        sink.send(text)
    except Exception:
        logger.error(
            "中断告警发送失败，本次 ⛔ 不标记已告警，下次启动会重发；"
            "⛔ 不因此中止消息接收。原文：%s",
            text,
            exc_info=True,
        )
        return False
    return True
