"""第 7 章·中断告警（7.4 / 7.5 / 7.8）。

spec liaison-channel-session「中断窗口必须显式告警且说明可能漏消息」：
内容 MUST 包含该窗口的起止时间，并 MUST 明确说明该窗口内对方发送的消息可能未被
接收、需要重发；MUST NOT 把该缺口描述为已被重复投递保护（幂等）机制覆盖。
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

from tools.liaison import alerts

ALERTS_SOURCE = pathlib.Path(alerts.__file__)

STARTED = "2026-09-09T10:00:00.000000+08:00"
RECOVERED = "2026-09-09T10:03:12.000000+08:00"


def test_alert_text_contains_both_ends_of_the_window():
    """7.4 逐字：告警文本包含中断起止时间。"""
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    assert "2026-09-09 10:00:00" in text
    assert "2026-09-09 10:03:12" in text


def test_alert_text_contains_the_resend_request():
    """7.4 逐字：含"该时段消息可能未收到、请重发"的明确表述。"""
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    assert alerts.ALERT_RESEND_SENTENCE in text
    assert alerts.ALERT_RESEND_SENTENCE == "该时段消息可能未收到、请重发"


def test_alert_text_never_claims_the_gap_is_covered_by_idempotency():
    """⛔ 措辞不得声称缺口已被幂等机制覆盖（design D3 逐字）。

    幂等防的是同一条消息被处理两次，**不能补回从未到达的消息**。写一句
    "已由幂等机制保障不丢"是把一个真实缺口说成不存在——收信人因此不会重发。
    """
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    for claim in alerts.FORBIDDEN_ALERT_CLAIMS:
        assert claim not in text, f"告警文本出现了禁语：{claim}"
    assert alerts.FORBIDDEN_ALERT_CLAIMS, "禁语清单不许是空的"


def test_alert_text_carries_the_duration():
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    assert "3 分 12 秒" in text


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 秒"),
        (7, "7 秒"),
        (60, "1 分 0 秒"),
        (192, "3 分 12 秒"),
        (3600, "1 小时 0 分 0 秒"),
        (7325, "2 小时 2 分 5 秒"),
    ],
)
def test_duration_text_covers_the_boundaries(seconds, expected):
    assert alerts.compute_outage_duration_text(seconds) == expected


def test_alert_text_rejects_a_window_that_ends_before_it_starts():
    """恢复时间早于起始时间是真 bug（时钟被改／参数传反），⛔ 不许拼出一条负数告警。"""
    with pytest.raises(ValueError):
        alerts.compute_outage_alert_text(RECOVERED, STARTED)


def test_alert_text_carries_no_personal_information():
    """合规：中断窗口只需要时间。⛔ 告警里不许出现消息正文、姓名、手机号、userid。"""
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    for token in ("userid", "@", "手机", "姓名", "http"):
        assert token not in text


def test_logging_sink_writes_the_alert_to_the_local_log(caplog):
    sink = alerts.LoggingAlertSink()
    with caplog.at_level(logging.WARNING):
        sink.send("【HR 值守通道·连接中断】测试")
    assert "【HR 值守通道·连接中断】测试" in caplog.text


def test_emit_returns_true_when_the_sink_accepts():
    sent = []

    class OkSink:
        def send(self, text: str) -> None:
            sent.append(text)

    assert alerts.effect_emit_outage_alert(OkSink(), "hello") is True
    assert sent == ["hello"]


def test_emit_returns_false_and_only_logs_when_the_sink_fails(caplog):
    """7.5 逐字：告警通道失败只记本地日志、⛔ 不中止接收。

    "不中止"在这一层的可执行形式 = **本函数不把异常抛出去**。抛出去，调用它的
    那条接收循环就会被一次告警失败打断——而告警失败与"能不能继续收消息"毫无关系。
    """

    class BoomSink:
        def send(self, text: str) -> None:
            raise RuntimeError("webhook 502")

    with caplog.at_level(logging.ERROR):
        assert alerts.effect_emit_outage_alert(BoomSink(), "hello") is False
    assert "告警" in caplog.text
    assert "webhook 502" in caplog.text


def test_emit_lets_keyboard_interrupt_through():
    """⛔ 不许吞 BaseException：Ctrl-C 与 SystemExit 必须能停下服务。"""

    class InterruptingSink:
        def send(self, text: str) -> None:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        alerts.effect_emit_outage_alert(InterruptingSink(), "hello")


def test_alerts_module_has_no_outbound_channel():
    """opener 约束 3：告警发送通道本章只定接口 + 日志实现，⛔ 不在本章实现外发。

    真实群 webhook 是第 6 章。这条断言让"顺手把 urllib 接上"当场变红。
    """
    tree = ast.parse(ALERTS_SOURCE.read_text(encoding="utf-8"), filename=str(ALERTS_SOURCE))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"urllib", "http", "requests", "httpx", "socket", "aiohttp", "smtplib"}
    assert not (imported & forbidden), f"alerts.py 引入了外发通道：{sorted(imported & forbidden)}"


def test_alerts_module_never_uses_a_with_statement():
    """⛔ tools/liaison 非测试代码里不许出现 with（见 session.py 模块 docstring 第 1 条）。"""
    tree = ast.parse(ALERTS_SOURCE.read_text(encoding="utf-8"), filename=str(ALERTS_SOURCE))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))]
