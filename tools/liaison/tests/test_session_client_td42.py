"""TD-42 · 连接假死：看门狗有判据无把手，`liveness=connected` 会骗人（`[Mac]0909AS`）。

2026-09-09 23:27 → 09-10 09:38 实测：进程活着、`state=connected`、`stamp_at` 推进了
**十小时**，实际一条都没收到。两层缺口，本文件各钉一段：

1. **有判据无把手**：看门狗判定假死后要「拿事件循环把手去停」，而「本次连接从未
   触发过任何 SDK 事件」时根本没有把手 ⇒ 停不下来。⇒ 判定假死后必须**总能**让进程
   终止，由 launchd `KeepAlive` 拉起。⛔ 把手不是前提。
2. **判据会骗人**：`stamp_at` 只证明值守线程活着。⇒ 存活戳多带 `last_event_at`
   （SDK 最近一次真的送到东西的时刻），看门狗与人都改看它。

全部离线：注入时钟、注入 `terminate` 替身、⛔ 不建真连、⛔ 不发网络。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import pytest

from tools.liaison import alerts, session, session_client
from tools.liaison.session import CHINA_TZ

T0 = datetime(2026, 9, 10, 9, 0, 0, tzinfo=CHINA_TZ)


def iso(moment: datetime) -> str:
    return session.format_instant(moment)


def payload(
    *,
    state: str = session.STATE_CONNECTED,
    stamp_at: datetime = T0,
    since: datetime | None = None,
    last_event_at: datetime | None | str = "omit",
) -> dict:
    """造一份存活戳。`last_event_at="omit"` ⇒ 旧格式（键缺席）。"""
    data = {
        "state": state,
        "stamp_at": iso(stamp_at),
        "since": iso(since if since is not None else stamp_at),
    }
    if last_event_at != "omit":
        data[session.LIVENESS_EVENT_KEY] = (
            iso(last_event_at) if isinstance(last_event_at, datetime) else last_event_at
        )
    return data


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class Harness:
    """把 `LivenessWatchdog` 的全部注入点收进一个对象，用例只改它关心的那几个。

    看门狗在 `started` 时刻起（默认比 `now` 早 10 分钟：一个已经跑了一阵的进程），
    之后每一轮 `run()` 都是**同一只**狗在 `self.now` 时刻检查一次——连续段、宽限期、
    连续计数都跨轮保持，与真实进程里的一只狗一样。
    """

    def __init__(
        self,
        tmp_path,
        *,
        now: datetime = T0,
        started: datetime | None = None,
        handle: bool = True,
    ) -> None:
        self.now = now
        self.started = started if started is not None else now - timedelta(minutes=10)
        self.liveness: dict | None = None
        self.handle = handle
        self.rebuilds: list[datetime] = []
        self.terminations: list[int] = []
        self.sink = RecordingSink()
        self.ledger_path = tmp_path / "watchdog.json"
        self._dog: session_client.LivenessWatchdog | None = None

    # ── 注入点 ──
    def read_liveness(self):
        return self.liveness

    def clock(self):
        # 第一次调用发生在看门狗构造时（取 started_at），之后都是「现在」。
        if self._dog is None:
            return self.started
        return self.now

    def request_rebuild(self) -> bool:
        self.rebuilds.append(self.now)
        return self.handle

    def terminate(self, exit_code: int) -> None:
        self.terminations.append(exit_code)

    def run(self, rounds: int = 1) -> session_client.LivenessVerdict:
        """同一只狗检查 `rounds` 轮，返回最后一轮的判定。"""
        if self._dog is None:
            self._dog = session_client.LivenessWatchdog(
                read_liveness=self.read_liveness,
                clock=self.clock,
                request_rebuild=self.request_rebuild,
                terminate=self.terminate,
                alert_sink=self.sink,
                ledger_path=self.ledger_path,
            )
        verdict = None
        for _ in range(rounds):
            verdict = self._dog.check_once()
        return verdict

    def ledger(self) -> dict | None:
        if not self.ledger_path.is_file():
            return None
        return json.loads(self.ledger_path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────
# 一、判据（纯函数）：`compute_liveness_verdict`
# ─────────────────────────────────────────────────────────────────────────


def test_fresh_stamp_with_fresh_activity_is_ok():
    verdict = session_client.compute_liveness_verdict(
        payload(stamp_at=T0 - timedelta(seconds=10), last_event_at=T0 - timedelta(seconds=25)),
        T0,
        started_at=T0 - timedelta(hours=1),
    )
    assert verdict.kind == session_client.VERDICT_OK


def test_connected_with_advancing_stamp_but_no_sdk_activity_for_ten_minutes_is_stale():
    """🔴 TD-42 的反例本身：`state=connected` ＋ `stamp_at` 在推进 ⛔ 不足以证明在收消息。"""
    verdict = session_client.compute_liveness_verdict(
        payload(
            stamp_at=T0 - timedelta(seconds=5),
            since=T0 - timedelta(hours=10),
            last_event_at=T0 - timedelta(seconds=session_client.STALE_SDK_ACTIVITY_SECONDS + 1),
        ),
        T0,
        started_at=T0 - timedelta(hours=10),
    )
    assert verdict.kind == session_client.VERDICT_STALE_ACTIVITY
    assert verdict.reference_at == iso(
        T0 - timedelta(seconds=session_client.STALE_SDK_ACTIVITY_SECONDS + 1)
    )


def test_activity_just_inside_the_threshold_is_still_ok():
    """证伪的另一半：阈值内 ⛔ 不许触发——心跳回包 30 秒一次，阈值内的安静是正常的。"""
    verdict = session_client.compute_liveness_verdict(
        payload(
            stamp_at=T0,
            last_event_at=T0 - timedelta(seconds=session_client.STALE_SDK_ACTIVITY_SECONDS - 1),
        ),
        T0,
        started_at=T0 - timedelta(hours=1),
    )
    assert verdict.kind == session_client.VERDICT_OK


def test_activity_threshold_is_well_above_the_heartbeat_period_and_the_stamp_threshold():
    """心跳 30 秒一次（`DEFAULT_HEARTBEAT_MS`），SDK 自己判死要 ~56 秒，存活戳阈值 180 秒。
    活动阈值必须留出足够多个心跳周期，⛔ 否则一次回包晚到就拆掉一条健康连接。"""
    assert session_client.STALE_SDK_ACTIVITY_SECONDS >= 10 * (session_client.DEFAULT_HEARTBEAT_MS / 1000)
    assert session_client.STALE_SDK_ACTIVITY_SECONDS > session_client.STALE_LIVENESS_SECONDS


def test_old_format_stamp_is_unknown_not_healthy():
    """兼容：旧格式（无 `last_event_at`）读到时按「未知」处理并当作**需要关注**，⛔ 不许当成健康。"""
    verdict = session_client.compute_liveness_verdict(
        payload(stamp_at=T0 - timedelta(seconds=5)),  # 键缺席
        T0,
        started_at=T0 - timedelta(hours=1),
    )
    assert verdict.kind == session_client.VERDICT_UNKNOWN_FORMAT
    assert verdict.kind != session_client.VERDICT_OK
    assert verdict.needs_attention is True


def test_stale_stamp_is_still_stale_regardless_of_activity():
    """改法 2 是**增加**判据，⛔ 不是替换：存活戳停更（值守线程死了／断线定格）照旧算假死。"""
    verdict = session_client.compute_liveness_verdict(
        payload(
            state=session.STATE_DISCONNECTED,
            stamp_at=T0 - timedelta(seconds=session_client.STALE_LIVENESS_SECONDS + 1),
            last_event_at=T0 - timedelta(seconds=1),
        ),
        T0,
        started_at=T0 - timedelta(hours=1),
    )
    assert verdict.kind == session_client.VERDICT_STALE_STAMP


def test_activity_is_only_judged_while_connected():
    """断线期间存活戳定格是第 7 章契约，那时 `last_event_at` 旧不旧没有意义——
    由存活戳判据管；⛔ 不许对一条已知断开的连接再报一次「活动陈旧」。"""
    verdict = session_client.compute_liveness_verdict(
        payload(
            state=session.STATE_DISCONNECTED,
            stamp_at=T0 - timedelta(seconds=30),
            last_event_at=T0 - timedelta(hours=1),
        ),
        T0,
        started_at=T0 - timedelta(hours=2),
    )
    assert verdict.kind == session_client.VERDICT_OK


def test_a_stamp_left_by_a_previous_run_is_not_this_process_s_evidence():
    """🔴 冷启动残留（`0909AJ` 附带发现）：进程刚起，读到的是**上一次运行**的存活戳。

    改法 1 把「假死 ⇒ 终止进程」接上之后，这条误报会变成**每次冷启动都自杀**的无限
    循环，所以它从「刻意不修」升级成**必须修**：早于本进程启动时刻的戳 ⛔ 不算数，
    以启动时刻为基准重新计时。
    """
    started_at = T0 - timedelta(seconds=5)
    verdict = session_client.compute_liveness_verdict(
        payload(stamp_at=T0 - timedelta(hours=10), last_event_at=T0 - timedelta(hours=10)),
        T0,
        started_at=started_at,
    )
    assert verdict.kind == session_client.VERDICT_OK
    assert verdict.reference_at == iso(started_at)


def test_never_stamping_after_start_is_eventually_stale():
    """反过来：启动后值守线程**一直没盖过戳**也是假死（比如库初始化挂住），
    以启动时刻计时，超阈值照样判。⛔ 不许因为「没有本进程的戳」就永远不判。"""
    started_at = T0 - timedelta(seconds=session_client.STALE_LIVENESS_SECONDS + 1)
    for stale_payload in (None, payload(stamp_at=T0 - timedelta(hours=10))):
        verdict = session_client.compute_liveness_verdict(stale_payload, T0, started_at=started_at)
        assert verdict.kind == session_client.VERDICT_STALE_STAMP


def test_unparsable_activity_is_treated_as_attention_not_as_stale():
    """字段写坏了 ⇒ 温度计坏了，⛔ 不据此拆连接；但也 ⛔ 不当成健康。"""
    verdict = session_client.compute_liveness_verdict(
        payload(stamp_at=T0, last_event_at="不是时间"),
        T0,
        started_at=T0 - timedelta(hours=1),
    )
    assert verdict.kind == session_client.VERDICT_UNKNOWN_FORMAT


# ─────────────────────────────────────────────────────────────────────────
# 二、把手：假死 ⇒ 总能终止进程
# ─────────────────────────────────────────────────────────────────────────


def test_stale_without_any_sdk_event_terminates_the_process_with_error_and_alert(tmp_path, caplog):
    """🔴 **TD-42 的钉子**：存活戳陈旧 ＋ 本次连接从未触发过任何 SDK 事件（没有把手）
    ⇒ 进程必须被终止，且 ERROR 日志与中断告警各一条。

    修复前：`request_stop()` 返回 False、记两条 ERROR，然后**什么都不做**——
    进程活着、`liveness` 不变、下一轮再记两条 ERROR，十小时。
    """
    h = Harness(tmp_path, handle=False)
    h.liveness = payload(
        state=session.STATE_STARTING,
        stamp_at=T0 - timedelta(seconds=session_client.STALE_LIVENESS_SECONDS + 30),
        last_event_at=None,
    )
    with caplog.at_level(logging.INFO):
        h.run()

    assert h.terminations == [session_client.WATCHDOG_EXIT_CODE], (
        "没有把手时进程必须被终止（交给 launchd KeepAlive 拉起），⛔ 不许停在「本轮兜底没能执行」"
    )
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "终止" in r.getMessage()]
    assert len(errors) == 1, f"终止前必须恰好落一条 ERROR：{[r.getMessage() for r in errors]}"
    assert len(h.sink.texts) == 1, "终止前必须恰好发一条中断告警（沿用 alerts 现有出口）"
    assert alerts.ALERT_RESEND_SENTENCE in h.sink.texts[0]
    assert not any(claim in h.sink.texts[0] for claim in alerts.FORBIDDEN_ALERT_CLAIMS)


def test_connected_but_silent_for_ten_minutes_gets_rebuilt_and_terminated_if_that_does_not_help(
    tmp_path,
):
    """改法 1 与 2 合起来的形态（十小时那一次）：`connected`、戳在推进、SDK 十分钟没送来
    任何东西。有把手就先停 loop 让外层重建（TD-39 的路径，⛔ 不动）；停了之后仍然
    假死满一个宽限期 ⇒ 终止进程。"""
    h = Harness(tmp_path, handle=True)
    quiet_since = T0 - timedelta(seconds=session_client.STALE_SDK_ACTIVITY_SECONDS + 1)
    h.liveness = payload(stamp_at=T0, since=T0 - timedelta(hours=10), last_event_at=quiet_since)

    h.run()  # 第一轮：判定假死，请求重建
    assert len(h.rebuilds) == 1
    assert h.terminations == [], "有把手时先给 TD-39 的路径一个机会，⛔ 不许上来就自杀"

    # 宽限期内：戳还在推进（值守线程活着）、SDK 依旧沉默 ⇒ 继续等
    h.now = T0 + timedelta(seconds=session_client.TERMINATE_GRACE_SECONDS - 1)
    h.liveness = payload(stamp_at=h.now, since=T0 - timedelta(hours=10), last_event_at=quiet_since)
    h.run()
    assert h.terminations == []

    # 宽限期满仍假死 ⇒ 把手无效，终止
    h.now = T0 + timedelta(seconds=session_client.TERMINATE_GRACE_SECONDS + 1)
    h.liveness = payload(stamp_at=h.now, since=T0 - timedelta(hours=10), last_event_at=quiet_since)
    h.run()
    assert h.terminations == [session_client.WATCHDOG_EXIT_CODE]


def test_recovery_within_the_grace_period_cancels_the_pending_termination(tmp_path):
    """重建真的救回来了（SDK 事件又来了）⇒ ⛔ 不许再终止。"""
    h = Harness(tmp_path, handle=True)
    h.liveness = payload(
        stamp_at=T0, last_event_at=T0 - timedelta(seconds=session_client.STALE_SDK_ACTIVITY_SECONDS + 1)
    )
    h.run()
    assert len(h.rebuilds) == 1

    h.now = T0 + timedelta(seconds=30)
    h.liveness = payload(stamp_at=h.now, last_event_at=h.now)  # 重建后的 connected
    h.run()
    h.now = T0 + timedelta(seconds=session_client.TERMINATE_GRACE_SECONDS + 60)
    h.liveness = payload(stamp_at=h.now, last_event_at=h.now - timedelta(seconds=20))
    h.run()
    assert h.terminations == []


def test_unknown_format_is_logged_as_attention_but_never_terminates(tmp_path, caplog):
    h = Harness(tmp_path)
    h.liveness = payload(stamp_at=T0 - timedelta(seconds=5))  # 旧格式
    with caplog.at_level(logging.WARNING, logger=session_client.__name__):
        h.run(2)
    assert h.terminations == []
    assert h.rebuilds == []
    assert any("需要关注" in r.getMessage() for r in caplog.records), "未知格式必须留下可见的关注信号"


def test_termination_uses_the_real_terminator_by_default():
    """默认的 `terminate` 必须真的能结束进程：它是 `os._exit`，⛔ 不是 `sys.exit`——
    后者在看门狗线程里只会结束那条线程，主线程的 `loop.run_forever()` 照样挂着。"""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(session_client.terminate_process))
    calls = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "os._exit" in calls
    assert "sys.exit" not in calls
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]


# ─────────────────────────────────────────────────────────────────────────
# 三、连续假死计数：⛔ 不许无限自杀循环
# ─────────────────────────────────────────────────────────────────────────


def _stale_payload(now: datetime) -> dict:
    return payload(
        state=session.STATE_STARTING,
        stamp_at=now - timedelta(seconds=session_client.STALE_LIVENESS_SECONDS + 30),
        last_event_at=None,
    )


def test_each_termination_is_counted_in_the_ledger_with_the_process_lifetime(tmp_path):
    h = Harness(tmp_path, handle=False)
    h.liveness = _stale_payload(T0)
    h.run()
    ledger = h.ledger()
    assert ledger is not None
    assert ledger["consecutive"] == 1
    assert ledger["last_terminated_at"] == iso(T0)
    assert ledger["reason"] == session_client.VERDICT_STALE_STAMP


def test_a_short_lived_process_terminated_again_increments_the_count(tmp_path):
    """「起来就假死」：上一次被看门狗终止、这次活了不到 `SHORT_LIFE_SECONDS` 又被终止 ⇒ 连续。"""
    h = Harness(tmp_path, handle=False)
    h.liveness = _stale_payload(T0)
    h.run()
    assert h.ledger()["consecutive"] == 1
    for k in range(2, session_client.CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF):
        # 下一个进程：活了 STALE+30 秒就又假死（远短于 SHORT_LIFE）
        started = T0 + timedelta(minutes=5 * k)
        now = started + timedelta(seconds=session_client.STALE_LIVENESS_SECONDS + 30)
        h2 = Harness(tmp_path, now=now, started=started, handle=False)
        h2.liveness = None  # 值守线程一直没盖过戳
        h2.run()
        assert h2.terminations == [session_client.WATCHDOG_EXIT_CODE]
        assert h2.ledger()["consecutive"] == k


def test_a_long_lived_process_resets_the_count(tmp_path):
    h = Harness(
        tmp_path,
        started=T0 - timedelta(seconds=session_client.SHORT_LIFE_SECONDS + 1),
        handle=False,
    )
    h.ledger_path.write_text(
        json.dumps({"consecutive": 7, "last_terminated_at": iso(T0 - timedelta(hours=3)), "reason": "x"}),
        encoding="utf-8",
    )
    h.liveness = _stale_payload(T0)
    # 上一次连续 7 次 ⇒ 这一次要等宽限期（封顶 1 小时）；本进程已活 SHORT_LIFE+1 秒，
    # 直接把「现在」推到宽限期之后。
    h.run()
    assert h.terminations == []
    h.now = T0 + timedelta(seconds=session_client.TERMINATE_GRACE_CAP_SECONDS + 1)
    h.liveness = _stale_payload(h.now)
    h.run()
    assert h.terminations == [session_client.WATCHDOG_EXIT_CODE]
    assert h.ledger()["consecutive"] == 1, "活满 SHORT_LIFE 再被终止 ⇒ 不是「起来就假死」，从 1 重数"


def test_from_the_nth_consecutive_termination_on_the_grace_doubles_and_is_capped():
    """处置方式：前 N-1 次立刻终止；从第 N 次起判死后**等一个翻倍的宽限期**再终止，
    封顶 1 小时。循环不停（停了就是把假死留在原地），但频率衰减，且每次都带计数告警。"""
    n = session_client.CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF
    assert session_client.compute_terminate_grace(0) == 0
    assert session_client.compute_terminate_grace(n - 2) == 0
    assert session_client.compute_terminate_grace(n - 1) == session_client.TERMINATE_GRACE_SECONDS
    assert session_client.compute_terminate_grace(n) == 2 * session_client.TERMINATE_GRACE_SECONDS
    assert session_client.compute_terminate_grace(n + 20) == session_client.TERMINATE_GRACE_CAP_SECONDS


def test_the_nth_consecutive_termination_waits_for_the_backoff_grace_and_says_so(tmp_path, caplog):
    n = session_client.CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF
    h = Harness(tmp_path, handle=False)
    h.ledger_path.write_text(
        json.dumps({"consecutive": n - 1, "last_terminated_at": iso(T0 - timedelta(minutes=4)), "reason": "x"}),
        encoding="utf-8",
    )
    h.liveness = _stale_payload(T0)
    with caplog.at_level(logging.ERROR, logger=session_client.__name__):
        h.run()
    assert h.terminations == [], "第 N 次起不许立刻终止，要等宽限期"

    h.now = T0 + timedelta(seconds=session_client.compute_terminate_grace(n - 1) + 1)
    h.liveness = _stale_payload(h.now)
    with caplog.at_level(logging.ERROR, logger=session_client.__name__):
        h.run()
    assert h.terminations == [session_client.WATCHDOG_EXIT_CODE]
    assert h.ledger()["consecutive"] == n
    assert any(f"连续第 {n} 次" in r.getMessage() for r in caplog.records)
    assert any(f"连续第 {n} 次" in text for text in h.sink.texts)


def test_a_ledger_older_than_a_day_does_not_count_as_consecutive(tmp_path):
    h = Harness(tmp_path, handle=False)
    h.ledger_path.write_text(
        json.dumps({"consecutive": 9, "last_terminated_at": iso(T0 - timedelta(days=2)), "reason": "x"}),
        encoding="utf-8",
    )
    h.liveness = _stale_payload(T0)
    h.run()
    assert h.terminations == [session_client.WATCHDOG_EXIT_CODE]
    assert h.ledger()["consecutive"] == 1


def test_a_corrupt_ledger_is_ignored_and_rewritten(tmp_path):
    h = Harness(tmp_path, handle=False)
    h.ledger_path.write_text("{不是 json", encoding="utf-8")
    h.liveness = _stale_payload(T0)
    h.run()
    assert h.terminations == [session_client.WATCHDOG_EXIT_CODE]
    assert h.ledger()["consecutive"] == 1


# ─────────────────────────────────────────────────────────────────────────
# 四、活动来源：SDK 事件 ＋ SDK 日志里的入站证据
# ─────────────────────────────────────────────────────────────────────────


class _Delegate:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def debug(self, message, *args):
        self.lines.append(("debug", message))

    def info(self, message, *args):
        self.lines.append(("info", message))

    def warn(self, message, *args):
        self.lines.append(("warn", message))

    def error(self, message, *args):
        self.lines.append(("error", message))


def test_sdk_log_observer_reports_inbound_evidence_and_forwards_every_line():
    """SDK 没有「收到心跳回包」事件，只有一行 DEBUG 日志（`aibot/ws.py:272`）。
    `WSClientOptions.logger` 是公开注入点：包一层，见到入站证据就报活动，
    每一行原样转给原来的 logger，⛔ 不改 SDK 日志去向。"""
    seen: list[int] = []
    delegate = _Delegate()
    observer = session_client.SdkLogObserver(on_activity=lambda: seen.append(1), delegate=delegate)
    observer.debug("Heartbeat sent")
    observer.debug("Received heartbeat ack")
    observer.info("Authentication successful")
    observer.info("Reconnecting in 1000ms (attempt 1)...")
    observer.debug('Received push message: {"msgid": "x"}')
    observer.warn("No heartbeat ack received for 2 consecutive pings, connection considered dead")
    assert len(seen) == 3, "只有入站证据算活动：心跳回包、认证成功、推送——出站与内部计时 ⛔ 不算"
    assert [line for _, line in delegate.lines] == [
        "Heartbeat sent",
        "Received heartbeat ack",
        "Authentication successful",
        "Reconnecting in 1000ms (attempt 1)...",
        'Received push message: {"msgid": "x"}',
        "No heartbeat ack received for 2 consecutive pings, connection considered dead",
    ]


def test_sdk_log_observer_never_lets_a_callback_exception_reach_the_sdk(caplog):
    """回调抛出会从 `_handle_frame` 冒出去打死接收 task——TD-39 同形。⛔ 一律吞掉并记日志。"""

    def boom():
        raise RuntimeError("队列炸了")

    observer = session_client.SdkLogObserver(on_activity=boom, on_any_log=boom, delegate=_Delegate())
    with caplog.at_level(logging.WARNING, logger=session_client.__name__):
        observer.debug("Received heartbeat ack")
    assert caplog.records, "吞掉也要留症状"


def test_sdk_log_observer_calls_on_any_log_for_every_line():
    """任何一行 SDK 日志都是在它的事件循环里打的——这是抓 loop 把手的又一个时机。"""
    hits: list[int] = []
    observer = session_client.SdkLogObserver(
        on_activity=lambda: None, on_any_log=lambda: hits.append(1), delegate=_Delegate()
    )
    observer.info("Connecting to WebSocket: wss://x...")
    observer.error("Failed to create WebSocket connection: boom")
    assert hits == [1, 1]


def test_activity_markers_are_verified_against_the_installed_sdk_source():
    """日志文案是一个没有保证的契约。SDK 一改文案，活动就永远不再推进 ⇒ 十分钟一杀。
    所以按本模块既有手法（`verify_client_surface`）：启动时核对文案还在，不在就**拒绝启动**，
    ⛔ 不许静默降级。"""
    good = "\n".join(f'    self._logger.debug("{m} ...")' for m in session_client.SDK_ACTIVITY_LOG_MARKERS)
    session_client.verify_sdk_activity_markers(good)  # 不抛
    with pytest.raises(session_client.SdkSurfaceUnverifiedError):
        session_client.verify_sdk_activity_markers(good.replace("Received heartbeat ack", "pong ok"))


def test_activity_markers_match_the_real_sdk_when_it_is_installed():
    pytest.importorskip("aibot", reason="aibot SDK 只装在 tools/liaison/.venv，根 venv skip 是预期")
    import inspect

    import aibot.ws

    session_client.verify_sdk_activity_markers(inspect.getsource(aibot.ws))


def test_authenticated_event_is_in_the_subscription_contract_and_feeds_activity():
    """`authenticated` 是 SDK 唯一能证明「服务器真的应答了」的事件（`connected` 在认证之前）。"""
    assert session_client.EVENT_AUTHENTICATED in session_client.SUBSCRIBED_EVENTS

    class FakeClient:
        def __init__(self) -> None:
            self.handlers: dict[str, list] = {}

        def on(self, event, handler):
            self.handlers.setdefault(event, []).append(handler)

        def run(self) -> None:  # pragma: no cover —— 本用例不 run
            raise AssertionError("不该 run")

    client = FakeClient()
    activity: list[int] = []
    session_client.make_sdk_connect(
        lambda: client,
        on_connected=lambda: None,
        on_disconnected=lambda: None,
        on_activity=lambda: activity.append(1),
    )
    for handler in client.handlers[session_client.EVENT_AUTHENTICATED]:
        handler()
    assert activity == [1]


def test_run_liveness_watchdog_threads_terminate_and_ledger_through_to_the_dog(tmp_path):
    """`run_liveness_watchdog` 是 `__main__` 接的那一层：它必须把 `terminate` / `alert_sink` /
    `ledger_path` 原样交给狗，且 `should_stop` 一给真值就退出。"""
    started = T0 - timedelta(minutes=10)
    ticks = iter([started, T0, T0])
    rounds = iter([False, True])
    terminations: list[int] = []
    sink = RecordingSink()
    session_client.run_liveness_watchdog(
        read_liveness=lambda: _stale_payload(T0),
        clock=lambda: next(ticks),
        request_rebuild=lambda: False,
        should_stop=lambda: next(rounds),
        sleep=lambda _: None,
        terminate=terminations.append,
        alert_sink=sink,
        ledger_path=tmp_path / "watchdog.json",
    )
    assert terminations == [session_client.WATCHDOG_EXIT_CODE]
    assert len(sink.texts) == 1
    assert (tmp_path / "watchdog.json").is_file()


# ─────────────────────────────────────────────────────────────────────────
# 五、端到端（走 `main()` 的三线程接线）：假死 ＋ 从未触发任何 SDK 事件 ⇒ 进程被终止
# ─────────────────────────────────────────────────────────────────────────
#
# 被跑到的都是产品代码：`main()` 的接线、真的 `make_sdk_connect` / `LoopStopper` /
# `run_liveness_watchdog` / `run_forever` / `LiaisonSession`。注入的只有：假 SDK（连上
# 之后**一个事件都不 emit**、一行 SDK 日志都不打——TD-42 的原始形态，也是「没有把手」
# 的唯一造法）、假时钟、看门狗的 `terminate`（记录 ＋ 把假 SDK 的 loop 停掉好让
# `main()` 退出；⛔ 真 `os._exit` 会杀掉 pytest）、存活戳与台账指到 tmp。


def test_e2e_stale_without_any_sdk_event_terminates_the_process(tmp_path, monkeypatch, caplog):
    import asyncio
    import threading
    import time as real_time

    from tools.liaison import __main__ as liaison_main
    from tools.liaison.storage import db as liaison_db

    base = T0
    clock_now = [base]
    stamp_path = tmp_path / "liveness.json"
    ledger_path = tmp_path / "watchdog.json"

    monkeypatch.setenv(liaison_main.DOTENV_PATH_ENV, str(tmp_path / "nope.env"))
    monkeypatch.setenv("HR_LIAISON_BOT_ID", "fake-bot")
    monkeypatch.setenv("HR_LIAISON_BOT_SECRET", "fake-secret")
    monkeypatch.setattr(liaison_main, "now", lambda: clock_now[0])

    stamped_once = threading.Event()
    terminations: list[int] = []
    loops: list[asyncio.AbstractEventLoop] = []
    safety_net_fired = [False]

    class MuteSdkClient:
        """`run()` 复刻 `aibot/client.py:345-362`，但连上之后**什么都不 emit**。"""

        def on(self, event, handler):
            pass

        def run(self) -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loops.append(loop)
            try:
                # 等值守线程盖下本进程的第一个戳（state=starting）再把时钟推过阈值，
                # 否则戳会带着推进后的时间落下来、永远不陈旧。
                stamped_once.wait(5.0)
                clock_now[0] = base + timedelta(seconds=session_client.STALE_LIVENESS_SECONDS + 60)

                def _safety_net() -> None:
                    safety_net_fired[0] = True
                    loop.stop()

                loop.call_later(5.0, _safety_net)
                loop.run_forever()
            finally:
                loop.close()
                asyncio.set_event_loop(None)

    class StampingSession(session.LiaisonSession):
        def start(self, now):
            super().start(now)
            stamped_once.set()

    def session_builder():
        conn = liaison_db.get_connection(tmp_path / "liaison.db")
        liaison_db.init_schema(conn)
        return StampingSession(conn, RecordingSink(), liveness_path=stamp_path)

    def terminate(exit_code: int) -> None:
        terminations.append(exit_code)
        # 等价于进程结束：把假 SDK 的 loop 停掉，`run()` 返回 ⇒ `run_forever` 的 sleep 抛出 ⇒ main 退出
        for loop in loops:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass

    def watchdog(**kwargs):
        kwargs["read_liveness"] = lambda: liaison_main.read_liveness_payload(stamp_path)
        kwargs["poll_seconds"] = 0.01
        kwargs["terminate"] = terminate
        kwargs["ledger_path"] = ledger_path
        session_client.run_liveness_watchdog(**kwargs)

    def stop_after_first_return(_delay: float) -> None:
        raise KeyboardInterrupt

    with caplog.at_level(logging.INFO):
        exit_code = liaison_main.main(
            session_builder=session_builder,
            client_builder=lambda _c, **_: MuteSdkClient(),
            runner=lambda connect: session_client.run_forever(connect, sleep=stop_after_first_return),
            watchdog=watchdog,
        )

    assert exit_code == 0
    assert safety_net_fired[0] is False, "loop 是被本用例的安全网停的，看门狗的终止路径没有生效"
    assert terminations == [session_client.WATCHDOG_EXIT_CODE], (
        "存活戳陈旧 ＋ 从未触发过任何 SDK 事件 ⇒ 没有把手 ⇒ 必须发出终止请求"
    )
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "终止进程" in r.getMessage()]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    alert_lines = [
        r for r in caplog.records
        if r.name == alerts.__name__ and alerts.ALERT_RESEND_SENTENCE in r.getMessage()
    ]
    assert len(alert_lines) == 1, "中断告警必须恰好一条（走 alerts.LoggingAlertSink）"
    assert json.loads(ledger_path.read_text(encoding="utf-8"))["consecutive"] == 1
    # 收尾：给守护线程一点时间退出，⛔ 不让它们带着 tmp 路径活到下一条用例
    real_time.sleep(0.05)


def test_an_unwritable_ledger_does_not_block_termination(tmp_path, monkeypatch):
    """台账只是计数；写不了 ⛔ 不许挡住终止——否则假死进程因为写不了一个 json 而永远活着。"""
    h = Harness(tmp_path, handle=False)
    h.liveness = _stale_payload(T0)
    monkeypatch.setattr(
        session_client, "write_termination_ledger",
        lambda path, payload: (_ for _ in ()).throw(OSError("磁盘满了")),
    )
    h.run()
    assert h.terminations == [session_client.WATCHDOG_EXIT_CODE]


def test_backoff_wait_is_logged_once_per_stall_not_every_poll(tmp_path, caplog):
    n = session_client.CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF
    h = Harness(tmp_path, handle=False)
    h.ledger_path.write_text(
        json.dumps({"consecutive": n + 2, "last_terminated_at": iso(T0 - timedelta(minutes=4)), "reason": "x"}),
        encoding="utf-8",
    )
    h.liveness = _stale_payload(T0)
    with caplog.at_level(logging.ERROR, logger=session_client.__name__):
        for step in range(5):
            h.now = T0 + timedelta(seconds=15 * step)
            h.liveness = _stale_payload(h.now)
            h.run()
    waits = [r for r in caplog.records if "还要等" in r.getMessage()]
    assert len(waits) == 1, "退避等待只记一次，⛔ 不每 15 秒刷一行 ERROR"
    assert h.terminations == []
