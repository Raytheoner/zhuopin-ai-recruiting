"""SDK 接线：外层建连退避 + WSClientOptions 构造 + 事件订阅。

**边界**（design D8，⛔ 不许越）：
- SDK 负责心跳（30s、2 次无响判死）与**连上之后**的重连（指数退避封顶 30s）。
  ⛔ 本模块不实现、不模拟、不替换。
- 本模块只负责 SDK 管不到的那层：**建连尝试本身失败**时的外层重试。SDK 的内置
  重连在"从来没连上过"的时候还没生效，没有这一层，进程会在第一次建连失败时
  直接退出。

⛔ 不许写 `with`（见 session.py 模块 docstring 第 1 条）。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import pathlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from tools.liaison import alerts as liaison_alerts

logger = logging.getLogger(__name__)

#: 外层退避：1s 起、翻倍、封顶 30s。**单位是秒**，这两个常量只喂给本模块自己的
#: `sleep`，⛔ 不传给 SDK。
#: ⚠️ 2026-09-09（TD-38）更正：原注释称"封顶值与 SDK 内置退避的封顶取同一个数、
#: 两层节奏一致"——**那是在两种单位下比的，结论不成立、已作废**。SDK 的
#: `reconnect_interval` 单位是毫秒（`aibot/types.py:43`，默认 1000 = 1 秒），
#: 且本模块**根本没传**该参数，所以 SDK 那层走的是它自己的默认退避，与这里的
#: 30.0 秒没有任何对应关系。⛔ 不要为了"让两层一致"顺手加传参——那会改变重连行为。
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0

#: 一次连接活过这么久，就认为"连得挺好、只是偶尔断了"，退避从头开始。
#: ⛔ 不重置的话，连了三天的服务第一次断线也要等满 30 秒才重连。
HEALTHY_SESSION_SECONDS = 60.0

#: 超过这个尝试次数一律直接返回封顶值，⛔ 不去算 2 ** attempt 那个大整数。
_SATURATION_ATTEMPT = 64

#: findings 2026-09-08 实测：`max_reconnect_attempts` 默认 **10**，`-1` 才是无限。
#: spec 要求"自动重试直至成功"，⛔ 不许用默认值。
UNLIMITED_RECONNECT_ATTEMPTS = -1

#: 心跳间隔，**单位是毫秒**——30_000 就是 30 秒。SDK 契约见 `aibot/types.py:49`
#: （`heartbeat_interval: int = 30000`，docstring 明写「心跳间隔（毫秒）」；
#: `aibot/ws.py:299` 实际 `asyncio.sleep(interval / 1000)`）。
#: ⛔ **不许改成 30**：2026-09-09 首次真实建连（TD-38）传的就是 30，被解读成 30 毫秒 ⇒
#: 心跳频率 ×1000（44 秒内 1399 次）⇒ 企微 45009 限流 ⇒ 连接 44 秒即死、服务保持不了在线。
#: ⛔ 常量名必须带 `_MS`：旧名叫 `_SECONDS` 而值要按毫秒填，正是本条的成因。
DEFAULT_HEARTBEAT_MS = 30_000

#: ⚠️ 事件名与必需方法名以 Step 5 的探针实测为准（见 docs/findings/
#: 2026-09-09-aibot-wsclient-表面实测.md）。探针没跑成时这里保持默认值，
#: `make_sdk_connect` 会在启动时**当场报错**，⛔ 不会静默错接线。
EVENT_CONNECTED = "connected"
EVENT_DISCONNECTED = "disconnected"

#: SDK 在认证帧得到服务器应答之后才 emit（`aibot/client.py:64-66`，`ws.py:257-260`）。
#: ⚠️ `connected` 在**认证之前**就 emit（`ws.py:140`，socket 一通就发）——它证明不了
#: 服务器在应答。TD-42 的十小时里 `state=connected` 正是这样来的。本事件是 SDK 唯一
#: 能证明「对端真的回话了」的事件，接进 `on_activity`（存活戳 `last_event_at`）。
EVENT_AUTHENTICATED = "authenticated"

#: 🔴 **`error` 事件必须有监听器，这不是可选的日志改良**（TD-39 根因）。
#:
#: SDK 把 `on_error` 接成 `self.emit("error", error)`（`aibot/client.py:76`），而
#: pyee 对**没有监听器**的 `error` 事件直接 `raise` 载荷本身
#: （`pyee/base.py:178-184` `_emit_handle_potential_error`）。于是
#: `aibot/ws.py:152` 的 `self.on_error(e)` 当场炸掉，**下一行**的
#: `await self._schedule_reconnect()`（153 行）永远走不到；异常再冒出
#: `_receive_loop()`（`ws.py:208`）把那个 task 杀死——而它是唯一还会调
#: `_schedule_reconnect` 的地方。结果是断线后**只重连一次**，之后进程仍活着、
#: 日志一片正常、`liveness.json` 永远停在 `disconnected`、中断窗口永不闭合、
#: 恢复告警永不发出。⛔ 没有任何一处会报错。
#: 实证：2026-09-09 断线实测，见 `docs/findings/2026-09-09-断线重连实测.md`。
EVENT_ERROR = "error"

#: 🔴 **入站消息事件**（2026-09-10 接线）。名字与载荷形状**已实证**，证据是钉死版本
#: `wecom-aibot-python-sdk==1.0.2` 的源码：`aibot/message_handler.py`
#: `_handle_message_callback` 逐字 `emitter.emit("message", frame)`，载荷是整个 `WsFrame`。
#:
#: ⛔ **不许改成订阅 `message.text` / `.image` / `.mixed` / `.voice` / `.file` 那五个细分
#: 事件**：同一段源码里，msgtype 不在 `MessageType` 枚举里时 SDK 只
#: `logger.debug("Received unhandled message type: ...")`、**一个细分事件都不 emit**。
#: 按细分事件接线 ⇒ 新增/未知类型的消息静默消失，与 TD-39 同形、同样无症状。
#:
#: ⚠️ `emit("message", frame)` 在 `_handle_message_callback` 里，而 `handle_frame` 会先把
#: `cmd == aibot_event_callback` 的帧岔到 `_handle_event_callback`（emit 的是 `"event"`）。
#: ⇒ 本事件只承接**消息**回调，⛔ 不承接 `enter_chat` 之类的事件回调。
EVENT_MESSAGE = "message"

#: 本模块必须订阅的全部事件。**清单即契约**：`_prepare_client` 逐条接线，
#: `tests/test_session_client_reconnect.py::test_every_event_in_the_contract_is_actually_subscribed`
#: 断言"列了就必须真的接上"。⛔ 加常量不接线 = 该类事件永远到不了本模块，
#: 与 TD-39 同形、同样无症状。
SUBSCRIBED_EVENTS = (
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_AUTHENTICATED,
    EVENT_MESSAGE,
)

#: 存活戳停更多久算"loop 还活着但连接已死"（N-0 兜底的判据）。
#:
#: ⚠️ **取值依据是实测，⛔ 不许拍脑袋改小**：2026-09-09 实测 SDK 的判死时延是
#: **55.75 秒**（心跳 30 秒 × 2 次未回 pong）。阈值必须**明显大于**它，否则会把
#: 正常的判死过程本身误判成假死，看门狗于是主动拆掉一条正在自愈的连接——
#: 一个"修复"反过来制造断线。180 秒 ≈ 3.2×，且让 SDK 自己的重连（退避封顶 30 秒）
#: 有好几轮完整的机会先跑，兜底层才接手。
#:
#: ⚠️ **已知且刻意接受的行为**：真实长断网（比如网线拔了一小时）期间存活戳同样
#: 一直停在断线时刻（`tick()` 只在 `connected` 状态下刷新），于是看门狗会把它当假死：
#: 180 秒后拆一次连接让外层 `run_forever` 造一个全新的连接对象重来，再过一个宽限期
#: （`TERMINATE_GRACE_SECONDS`）仍没连上就终止进程交给 launchd 拉起（2026-09-10 TD-42
#: 起；此前是每 15 秒拆一次，见 `LivenessWatchdog.check_once`）。
#: ⛔ 这不是 bug，⛔ 也不要为它加"断网期间不看门"的例外：
#: ① 一小时约 10 次重启（连续计数满 3 次后还会翻倍拉长），远达不到企微限流的量级
#:    （TD-38 那次是 44 秒 1399 次心跳）；
#: ② 本条修的正是"SDK 自己的重连链会被打断"，长断网恰恰是它最可能卡死的时候，
#:    定期从头重建是**保护**不是浪费；
#: ③ 要加例外就得先分辨"真断网"与"假死"，而这两者从进程外部看**完全一样**——
#:    分辨得了的话，一开始就不需要看门狗了。
STALE_LIVENESS_SECONDS = 180.0

#: 看门狗两次检查之间等多久。比 `__main__.TICK_INTERVAL_SECONDS`（15 秒）不快，
#: ⛔ 不许设成 0——0 就是满速自旋（与 `compute_backoff_delay` 永不返回 0 同理）。
WATCHDOG_POLL_SECONDS = 15.0

#: TD-42 · 第二条判据：`connected` 状态下 SDK 多久没送来任何东西就算假死。
#:
#: `stamp_at` 只证明**值守线程**活着（2026-09-10 实测：它推进了十小时，一条都没收到）。
#: 「东西」= 连接事件、`authenticated`、以及 SDK 日志里的入站证据（心跳回包 30 秒
#: 一次，见 `SDK_ACTIVITY_LOG_MARKERS`）。健康的连接因此**每 30 秒**至少推进一次
#: `last_event_at`，与有没有人发消息无关——这不是「无消息判断线」（7.1）。
#:
#: 取值 600 秒 = **20 个心跳周期**、SDK 判死时延（~56 秒）的 10 倍以上、存活戳阈值
#: 的 3.3 倍。⛔ 不许改小到接近 180：活动的观察通道是日志文案（无保证的契约），
#: 留够余量，一次回包晚到 ⛔ 不许拆掉一条健康连接。
STALE_SDK_ACTIVITY_SECONDS = 600.0

#: 看门狗终止进程时的退出码。launchd `KeepAlive=true` 对退出码不作区分（照样拉起），
#: 这个数只给人与 `launchctl print` 看：5 = 「看门狗判定假死、主动终止」，与
#: `__main__` 的 2/3/4（缺凭据／SDK 缺失／表面不符）区分开。
WATCHDOG_EXIT_CODE = 5

#: 请求停 loop（TD-39 路径）之后，再给多久让重建生效；到点仍假死 ⇒ 把手无效，终止进程。
#: 取存活戳阈值同一个数：重建一条连接只要几秒，180 秒还没恢复就不是「慢」。
TERMINATE_GRACE_SECONDS = 180.0

#: ⛔ 不许无限自杀循环。连续「起来就假死」到这个次数起，判死之后**等一个翻倍的
#: 宽限期**再终止（`compute_terminate_grace`），封顶 `TERMINATE_GRACE_CAP_SECONDS`。
#: 循环 ⛔ 不停——停了就是把假死留在原地（正是 TD-42 要消灭的），只是频率衰减、
#: 且每一次都带计数落日志与告警，让「系统性故障」被看出来。
CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF = 3
TERMINATE_GRACE_CAP_SECONDS = 3600.0

#: 一个进程活过这么久才被终止，就**不是**「起来就假死」，连续计数从 1 重数。
SHORT_LIFE_SECONDS = 1800.0

#: 台账里上一次终止早于这么久，就当没有上一次（不算连续）。
LEDGER_MAX_AGE_SECONDS = 24 * 3600.0

#: SDK 日志里**证明收到了对端数据**的文案（`aibot/ws.py`，按 2026-09-10 装的 1.0.2）：
#:   272 行 `Received heartbeat ack`      —— 心跳回包（30 秒一次，健康连接的主要证据）
#:   257 行 `Authentication successful`   —— 认证应答
#:   222 行 `Received push message: …`    —— 消息推送
#:   229 行 `Received event callback: …`  —— 事件推送
#: ⛔ 只收**入站**证据：`Heartbeat sent`、`Reconnecting in` 是本地计时器，证明不了对端。
#: SDK 没有「收到心跳回包」这种事件，只有这几行日志；`WSClientOptions.logger` 是公开
#: 注入点（`aibot/types.py:59`），`SdkLogObserver` 从那里看。文案是无保证的契约，
#: 所以 `verify_sdk_activity_markers` 在启动时核对它们仍在 SDK 源码里，不在就拒绝启动。
SDK_ACTIVITY_LOG_MARKERS = (
    "Received heartbeat ack",
    "Authentication successful",
    "Received push message",
    "Received event callback",
)

#: 本模块真正依赖的两个方法。⚠️ 2026-09-09（TD-19）把 `connect` 从这份清单里
#: **移走、换成 `run`**：实测 `WSClient.connect` 是 `async def` 且建连后立即返回，
#: 不满足 `run_forever`「阻塞到断开为止」的契约；本模块从此不再调用它。
#: ⛔ 清单里只留**真正会被调用**的方法——要求一个用不到的方法，会让"表面校验"
#: 与"真实依赖"错位：校验绿灯，而真正被调的那个方法从没被核过。
REQUIRED_CLIENT_ATTRS = ("on", "run")


class SdkSurfaceUnverifiedError(RuntimeError):
    """SDK 的方法／事件表面与本模块的假设对不上。

    ⛔ 这个错误不许被吞成一条警告：接不上事件 ⇒ 断线事件永远到不了
    LiaisonSession ⇒ 中断窗口一条都不会有 ⇒ "没有告警"被当成"一切正常"。
    """


def compute_backoff_delay(
    attempt: int,
    *,
    base_seconds: float = BASE_BACKOFF_SECONDS,
    cap_seconds: float = MAX_BACKOFF_SECONDS,
) -> float:
    """第 `attempt` 次失败后该等多久。纯函数（铁律 2）。`attempt` 从 1 起。

    ⛔ 永不返回 0：0 就是紧密循环，spec 的「服务不退出、不占满 CPU」是两条要求，
    退出与自旋一样糟。
    """
    if attempt < 1:
        raise ValueError(f"attempt 从 1 起，收到 {attempt}")
    if attempt >= _SATURATION_ATTEMPT:
        return cap_seconds
    return min(base_seconds * (2 ** (attempt - 1)), cap_seconds)


def run_forever(
    connect: Callable[[], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_attempt_failed: Callable[[int, float, BaseException | None], None] | None = None,
) -> None:
    """反复建连，**⛔ 永不主动返回**（spec「网络恢复后自动重连……无需人工重启」）。

    `connect` 是一个"跑到断开为止"的阻塞调用。它抛异常（连不上）或正常返回
    （连上后又断了）都按同一件事处理：等一段退避，再来。
    ⛔ 两种情况都必须 sleep——正常返回那条路径不 sleep 就是满速自旋。

    ⛔ 刻意不提供"最多重试 N 次"的参数：那个参数一旦存在，迟早有人在生产配置里
    设成有限值，服务于是会在某次长时间断网后**安静地退出**。测试要停下这个循环，
    用一个会抛异常的 `sleep`。
    """
    attempt = 0
    while True:
        started = monotonic()
        outcome: BaseException | None = None
        try:
            connect()
        except Exception as exc:  # noqa: BLE001 —— 任何建连失败都只是"再试一次"
            outcome = exc
        # ⛔ 捕获 Exception 而不是 BaseException：KeyboardInterrupt 与 SystemExit
        # 必须能穿过这个循环把服务停下来。
        if monotonic() - started >= HEALTHY_SESSION_SECONDS:
            attempt = 0
        attempt += 1
        delay = compute_backoff_delay(attempt)
        logger.warning(
            "值守通道连接结束（第 %s 次尝试），%.1f 秒后重连：%r", attempt, delay, outcome
        )
        if on_attempt_failed is not None:
            on_attempt_failed(attempt, delay, outcome)
        sleep(delay)


def build_ws_options(
    credentials,
    *,
    heartbeat_interval: int = DEFAULT_HEARTBEAT_MS,
    sdk_logger=None,
):
    """构造 SDK 的连接参数。

    `sdk_logger`（TD-42）：给 SDK 用的日志对象，通常是 `SdkLogObserver`。None ⇒ SDK
    用它自己的 `DefaultLogger`，行为与从前完全一致。

    ⚠️ `heartbeat_interval` 的**单位是毫秒**（SDK 契约，见 `DEFAULT_HEARTBEAT_MS` 处的
    说明）。显式传值时⛔ 不要传秒——传 30 会变成 30 毫秒（TD-38）。

    ⛔ `import aibot` 写在函数体里：根 venv 不装 SDK（design D10 的依赖隔离），
    模块层 import 会让全量 pytest 在 collect 阶段就整个红掉。
    """
    import aibot

    return aibot.WSClientOptions(
        bot_id=credentials.bot_id,
        secret=credentials.bot_secret,
        heartbeat_interval=heartbeat_interval,
        max_reconnect_attempts=UNLIMITED_RECONNECT_ATTEMPTS,
        logger=sdk_logger,
    )


def verify_client_surface(client) -> None:
    """核对 SDK 连接对象的表面，对不上就 raise。⛔ 不许降级成 warning。

    🔴 **这是整条链上唯一的护栏，⛔ 不许删、不许加"跳过校验"的开关或环境变量。**
    它防的正是「服务起得来、日志一片正常、但从来没真正建连／断线事件永远到不了
    `LiaisonSession`」这一类**静默**故障——中断窗口一条都不会有，"没有告警"被当成
    "一切正常"。

    校验四项，每一项对应一种真实存在过的静默失败形态：

    1. `REQUIRED_CLIENT_ATTRS` 都在——SDK 换版本改名时当场炸；
    2. `run` 可调用；
    3. `run` **不是**协程函数——协程 `run()` 同步调用只返回一个协程对象、不执行
       任何网络动作，`run_forever` 会把它当成"连上后立刻断开"，从此满速退避重连；
    4. `run` 能以**零参数**调用——将来若变成 `run(self, forever)`，`run()` 会抛
       `TypeError`，而 `run_forever` 把任何 `Exception` 都当"这次没连上"吞掉重试：
       一个永远不会自愈的接口变更会伪装成"网络一直不好"。
    """
    missing = [attr for attr in REQUIRED_CLIENT_ATTRS if not hasattr(client, attr)]
    if missing:
        raise SdkSurfaceUnverifiedError(
            f"SDK 连接对象缺少本模块依赖的方法 {missing}（期望 {list(REQUIRED_CLIENT_ATTRS)}）。"
            "请按 tools/liaison/scripts/probe_ws_surface.py 的实测输出更新 "
            "REQUIRED_CLIENT_ATTRS / EVENT_CONNECTED / EVENT_DISCONNECTED，"
            "并把原始输出落进 docs/findings/。⛔ 不要绕过本检查。"
        )
    if not callable(client.on):
        raise SdkSurfaceUnverifiedError(
            "SDK 连接对象的 on 不可调用，订阅不了 "
            f"{list(SUBSCRIBED_EVENTS)} 这几个事件。"
        )
    # ⚠️ 校验的是**清单本身**，⛔ 不是"接了几个就算几个"。`error` 在 2026-09-09
    # 之前不在清单里，那个缺口正是 TD-39 能发生的原因之一：漏订阅不报错，
    # 只是 pyee 改走 raise 分支，把整条重连链一并打掉。
    if EVENT_ERROR not in SUBSCRIBED_EVENTS:
        raise SdkSurfaceUnverifiedError(
            f"{EVENT_ERROR!r} 必须在 SUBSCRIBED_EVENTS 里。缺了它，pyee 对没有"
            "监听器的 error 事件会直接 raise，aibot/ws.py:152 当场炸掉，"
            "153 行的 _schedule_reconnect() 永远走不到（TD-39）。⛔ 不要绕过本检查。"
        )
    # ⚠️ 与上面那条同构（2026-09-10 加）。`message` 漏订阅**不会报错**，现象只是
    # `liaison_message` 恒为 0 —— 而那与「连接假死」「对方没发」从外部完全无法区分，
    # TD-42 排查耗掉的十小时正是这个歧义。⇒ 宁可拒绝启动，也 ⛔ 不许"连着但不收消息"。
    if EVENT_MESSAGE not in SUBSCRIBED_EVENTS:
        raise SdkSurfaceUnverifiedError(
            f"{EVENT_MESSAGE!r} 必须在 SUBSCRIBED_EVENTS 里。缺了它，值守通道会"
            "连得好好的但一条消息都收不到，且 ⛔ 没有任何报错——"
            "`liaison_message` 恒为 0 与「连接假死」从外部无法区分（TD-42）。"
            "⛔ 不要绕过本检查。"
        )
    run = client.run
    if not callable(run):
        raise SdkSurfaceUnverifiedError("SDK 连接对象的 run 不可调用，没有可阻塞的入口。")
    if inspect.iscoroutinefunction(run):
        raise SdkSurfaceUnverifiedError(
            "SDK 的 run 是协程函数（async def），不满足 run_forever 期望的"
            "「阻塞到断开为止」同步调用契约：同步调用它只会返回一个协程对象，"
            "不执行任何网络操作，run_forever 会把它当成「连上后立刻断开」而满速退避重连。"
            "请按 docs/findings/2026-09-09-aibot-wsclient-表面实测.md 重新核对表面，"
            "⛔ 不要绕过本检查。"
        )
    try:
        signature = inspect.signature(run)
    except (TypeError, ValueError):
        # 取不到签名（C 实现、奇异的可调用对象）⇒ 这一项无从判断，放行。
        # ⛔ 不因"看不清"就拒绝启动：上面三项已经守住了主要失败形态。
        return
    try:
        signature.bind()
    except TypeError as exc:
        raise SdkSurfaceUnverifiedError(
            f"SDK 的 run 不能以零参数调用（签名 {signature}）。run_forever 只会调 run()，"
            "参数对不上会抛 TypeError，而 run_forever 把任何 Exception 都当成「这次没连上」"
            "吞掉重试——一个永不自愈的接口变更会伪装成「网络一直不好」。⛔ 不要绕过本检查。"
        ) from exc


class LoopStopper:
    """跨线程把 `client.run()` 逼返回的把手（N-0 兜底用）。

    🔴 **为什么需要它**：`make_sdk_connect` 的注释曾断言「`client.run()` 几乎不会
    返回，外层 `run_forever` 是兜底那一层」。TD-39 的故障下这条假设**不成立**——
    SDK 的 `run()` 是 `loop.run_until_complete(connect())` 后接 `loop.run_forever()`
    （`aibot/client.py:345-362`）。接收 task 死掉之后 `loop.run_forever()` 照样挂着
    ⇒ `run()` **仍然不返回** ⇒ 外层 `run_forever` 永远等不到那次返回，
    **兜底一次都不会触发**。两层重连被同一个异常一并打掉。
    ⇒ 必须有人从**另一条线程**把那个 loop 停下来，`run()` 才会返回、外层才接得上。

    `loop.call_soon_threadsafe` 是 asyncio 唯一有文档保证的跨线程入口，
    ⛔ 不许改成直接调 `loop.stop()`（那不是线程安全的）。

    ⛔ **本类不写 `with`**（连 `threading.Lock` 都不用）：`tools/liaison/` 下非测试
    代码里任何 `with <名字|属性|调用>:` 都会被
    `test_liaison_effects.py::test_no_second_transaction_manager_in_source` 判违规。
    这里也确实不需要锁——持有的只是一个引用，赋值与读取在 GIL 下都是原子的。
    """

    def __init__(self) -> None:
        self._loop = None

    def remember(self, loop) -> None:
        """记住一个 loop。⛔ 只该在该 loop **自己**的线程里被调用。"""
        self._loop = loop

    def capture(self) -> bool:
        """在事件回调里抓当前正在跑的 loop。回调由 SDK 在 loop 内触发，抓得到。

        抓不到（没有正在跑的 loop）返回 False，⛔ 不抛——事件回调抛异常正是
        本条要修的那个 bug 的形状。
        """
        try:
            self.remember(asyncio.get_running_loop())
        except RuntimeError:
            return False
        return True

    def forget(self) -> None:
        """丢掉上一次的把手。**每次新建连接前必须调**。

        ⛔ 留着的后果：看门狗去停一个早就 `loop.close()` 掉的 loop（`run()` 的
        `finally` 会关它），`request_stop` 报成功，而真正卡住的那个 loop 没人动——
        兜底层看起来在工作，实际什么都没做。
        """
        self._loop = None

    def request_stop(self) -> bool:
        """请求停掉 loop，让 `client.run()` 返回。成功返回 True。

        ⛔ 失败一律返回 False 并留日志，**不许静默假装停成功了**——调用方要靠
        这个返回值决定是否升级成 ERROR。看门狗自己变成静默故障源，就是把
        TD-39 原样复制了一份。
        """
        loop = self._loop
        if loop is None:
            # WARNING 而不是 ERROR（2026-09-10 TD-42 起）：没有把手不再是死路——
            # 调用方（`LivenessWatchdog`）拿到 False 就改走终止进程的路径，ERROR 由那里落。
            logger.warning(
                "没有可停的事件循环把手（本次连接从未触发过任何 SDK 事件、也没打过"
                "一行 SDK 日志）。本轮停 loop 的兜底没法执行，交给终止进程的路径"
            )
            return False
        try:
            if loop.is_closed():
                logger.warning("事件循环已关闭，无需停它——`client.run()` 应当已经返回")
                return False
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            logger.warning("停事件循环失败（它可能刚刚已经关掉）", exc_info=True)
            return False
        return True


def _describe_error(error) -> str:
    """把 `error` 渲染成一行可打印的文本。**⛔ 本函数永不抛异常。**

    ⚠️ 载荷的 `__repr__` 自己抛异常时（第三方库里并不罕见——半初始化的对象、
    `__repr__` 里再去读一个已关闭的 socket），`logger.warning(..., %r, error)`
    会在格式化那一步炸。而从 `error` 监听器里逃出去的异常会被 pyee 再 emit 一次
    `error`（`pyee/asyncio.py:78-81`）⇒ 无限递归。
    """
    try:
        return repr(error)
    except Exception:  # noqa: BLE001 —— 渲染失败 ⛔ 不许升级成故障
        try:
            return f"<{type(error).__name__} 的 repr() 自己抛了异常，无法呈现>"
        except Exception:  # noqa: BLE001 —— 连 type().__name__ 都取不到就认了
            return "<无法呈现的错误载荷>"


def _handle_sdk_error(loop_stopper: "LoopStopper", error) -> None:
    """`error` 事件的监听器。**记日志，⛔ 不重新抛出。**

    存在的意义只有一个：让 pyee 走「有监听器 ⇒ 分发、不 raise」的那条分支，
    好让 `aibot/ws.py:153` 的 `await self._schedule_reconnect()` 能正常执行。
    ⛔ 不许在这里 `raise`、也 ⛔ 不许把异常放回 SDK——那就等于没修。

    ⛔ 同时**不许吞掉当没事发生**：至少 WARNING，且写清"已交给 SDK 重连"，
    否则一条真实的连接错误会连一行日志都不留。日志走 `logging.getLogger(__name__)`
    ⇒ 沿 `tools.liaison` 包 logger 的 handler 链走 `logsetup` 的脱敏过滤器
    （异常文本里可能带 URL/凭据片段），⛔ 不许裸 `print`。
    """
    loop_stopper.capture()
    try:
        logger.warning(
            "值守通道连接报错，已交给 SDK 重连（⛔ 不回抛，回抛会打断重连链）：%s",
            _describe_error(error),
        )
    except Exception:  # noqa: BLE001 —— 见下面这段注释，这不是"异常就 pass"
        # ⛔ 最后一道闸：**任何**从本函数逃出去的异常都会被 pyee 拿去
        # `self.emit("error", exc)`（`pyee/asyncio.py:78-81`）⇒ 又进本函数 ⇒
        # **无限递归**。修好了 TD-39 却在这里挂死，比原来的 bug 更难查。
        # 这一句刻意**不做任何插值**（不碰 error、不碰 %），所以它自己炸不了。
        logger.warning("值守通道连接报错（错误详情无法呈现），已交给 SDK 重连")


def _make_message_handler(loop_stopper, on_message, on_activity):
    """`message` 事件的处理函数。**⛔ 绝不许把异常放出去。**

    pyee 的 `AsyncIOEventEmitter` 会把处理函数抛出的异常拿去 `self.emit("error", exc)`
    （`pyee/asyncio.py`），于是一条畸形报文就能顺着 error 事件把整条重连链搅进来——
    这与 TD-39 是同一条路径。本函数因此把**所有**异常吞在自己肚子里，只记 ERROR。

    ⚠️ 吞异常的代价是"这条消息丢了"，所以日志必须**说清楚丢了什么**（thread 级别的
    键，⛔ 不打正文、不打 userid 取值）。⛔ 不要改成 `pass`。
    """
    def handler(*args, **kwargs):
        loop_stopper.capture()
        # ⚠️ 顺序刻意：**先**记活动戳再处理。处理失败也不该让看门狗把一条
        # 明明在收消息的连接判成假死——那会把"报文解析不了"放大成"连接被拆"。
        if on_activity is not None:
            try:
                on_activity()
            except Exception:  # noqa: BLE001 —— 见 docstring，异常放出去会打断重连链
                logger.exception("入站消息的活动戳回调抛异常（已吞下，⛔ 不回抛）")
        if on_message is None:
            # ⛔ 不是可有可无：没有去处 ＝ 这条消息被丢弃。接线漏传时必须有症状。
            logger.error(
                "收到入站消息，但 `on_message` 没有接线 ⇒ 这条消息已被丢弃。"
                "⛔ 这不是正常状态：`liaison_message` 会恒为 0，且与"
                "「连接假死」「根本没订阅」从外部完全无法区分（TD-42 的十小时）。"
            )
            return
        try:
            on_message(args[0] if args else None)
        except Exception:  # noqa: BLE001 —— 同上
            logger.exception(
                "入站消息回调抛异常（已吞下，⛔ 不回抛，回抛会经 pyee 的 error 事件"
                "打断重连链）。⚠️ 这条消息大概率没有落库，请核 channel.py 的字段名表。"
            )

    return handler


def _prepare_client(
    client_factory,
    on_connected,
    on_disconnected,
    loop_stopper,
    on_activity=None,
    on_message=None,
):
    """造一个连接对象、核表面、接事件。返回**尚未 run** 的那个对象。

    `on_activity`（TD-42）：`authenticated` 事件的去处——SDK 唯一能证明对端在应答
    的事件。不传就只抓 loop 把手、不通知任何人。

    `on_message`（2026-09-10）：入站消息帧的去处，签名是 `(frame) -> None`。
    ⛔ **它必须只做"把帧放进队列"这一件事**：SDK 回调跑在它自己的事件循环线程上，
    在这里碰库会踩 sqlite 的线程归属，异常再被 pyee 转成 `error` 事件把重连链打断
    （TD-39 的形状）。不传就只更新存活戳、**消息被丢弃**——所以 `__main__` 那条路
    ⛔ 不许不传。

    ⚠️ 入站消息**同时也是**"对端在应答"的铁证，所以它也喂 `on_activity`：不喂的话，
    一个只收消息、久未心跳的连接会被看门狗判成假死拆掉（TD-42 的判据是
    `last_event_at`）。

    先核表面再接线：对不上就 raise，⛔ 不许"能接的先接上、接不上的算了"。

    接线**逐条走 `SUBSCRIBED_EVENTS`**，⛔ 不许手写三行 `client.on(...)`：清单与
    接线一旦是两处真源，加了常量忘了接线就不会有任何症状（TD-39 的缺口正是这个）。

    每个回调都顺手 `loop_stopper.capture()`：SDK 的事件都在它自己的事件循环里
    触发，这是**唯一**能从外部拿到那个 loop 的时机（`run()` 内部
    `asyncio.new_event_loop()` 之后不对外暴露）。
    """
    client = client_factory()
    verify_client_surface(client)

    def wrap(callback):
        def handler(*args, **kwargs):
            loop_stopper.capture()
            callback()
        return handler

    handlers = {
        EVENT_CONNECTED: wrap(on_connected),
        EVENT_DISCONNECTED: wrap(on_disconnected),
        EVENT_ERROR: lambda *args, **kwargs: _handle_sdk_error(
            loop_stopper, args[0] if args else None
        ),
        EVENT_AUTHENTICATED: wrap(on_activity if on_activity is not None else lambda: None),
        EVENT_MESSAGE: _make_message_handler(loop_stopper, on_message, on_activity),
    }
    # ⛔ 断言清单与实现一一对应：漏一个就当场炸，⛔ 不许静默少接一个事件。
    missing = [event for event in SUBSCRIBED_EVENTS if event not in handlers]
    if missing:
        raise SdkSurfaceUnverifiedError(
            f"SUBSCRIBED_EVENTS 里的 {missing} 没有对应的处理函数——"
            "列了却没接线的事件永远到不了本模块，且没有任何症状。"
        )
    for event in SUBSCRIBED_EVENTS:
        client.on(event, handlers[event])
    return client


def make_sdk_connect(
    client_factory: Callable[[], object],
    *,
    on_connected: Callable[[], None],
    on_disconnected: Callable[[], None],
    loop_stopper: "LoopStopper | None" = None,
    on_activity: Callable[[], None] | None = None,
    on_message: Callable[[object], None] | None = None,
) -> Callable[[], None]:
    """返回一个交给 `run_forever` 用的、真正阻塞到断开为止的调用。

    `on_activity`（TD-42）／`on_message`（2026-09-10 入站消息接线）：见 `_prepare_client`。
    ⚠️ 两者都要**每一轮重连都带上**——`connect_once` 里重新造对象那条分支漏传任何一个，
    服务会在第一次重连之后变成"连着但不收消息"，且 ⛔ 没有任何症状。

    ⚠️ **参数是一个"每次造一个全新连接对象"的工厂，⛔ 不是一个连接对象**——
    这不是风格选择，是 SDK 的一个闩锁逼出来的（实测 `aibot==1.0.2`
    `client.py::connect`）：

        async def connect(self):
            if self._started:
                self._logger.warn("Client already connected")
                return self          # ⛔ 什么都不做就回来了
            self._started = True
            await self._ws_manager.connect()

    `_started` 一旦置位就只有 `disconnect()` 会清掉它。于是**同一个对象第二次
    `run()`** 会：connect 立刻返回 → `loop.run_forever()` 挂在一个空转的事件循环上
    → 进程活着、日志正常、**永远不再连上**，⛔ 没有任何症状。这正是本模块存在的
    理由要消灭的那一类故障，所以每一次建连尝试都必须拿一个全新的对象。

    表面校验在**返回之前**就跑掉（拿第一个对象核），⛔ 不许挪进返回的闭包里：
    `run_forever` 会把任何 `Exception`（`SdkSurfaceUnverifiedError` 是 `RuntimeError`）
    当成"这次没连上"吞掉重试，护栏一旦挪进去就等于被拆掉。

    ⚠️ **线程归属**：`client.run()` 自己 `new_event_loop()`。它必须跑在**主线程**
    （第 7 章接线：值守线程独占库、主线程跑连接），⛔ 不许挪进子线程，也 ⛔ 不许
    在一个已经有事件循环在跑的线程里调它。

    `loop_stopper` 是 N-0 兜底的把手（见 `LoopStopper`）。不传就自己造一个——
    调用方不接看门狗时，行为与从前完全一致。
    """
    if loop_stopper is None:
        loop_stopper = LoopStopper()
    prepared = _prepare_client(
        client_factory, on_connected, on_disconnected, loop_stopper, on_activity, on_message
    )

    def connect_once() -> None:
        nonlocal prepared
        client = prepared
        prepared = None
        if client is None:
            client = _prepare_client(
                client_factory,
                on_connected,
                on_disconnected,
                loop_stopper,
                on_activity,
                on_message,
            )
        # ⛔ 必须在 run() **之前**丢掉上一轮的 loop 把手：`run()` 的 finally 会
        # `loop.close()`，留着旧把手会让看门狗去停一个已经关掉的 loop 并报成功。
        loop_stopper.forget()
        # ⚠️ `client.run()` 在真实 SDK 上几乎不会返回：建连失败由 SDK 内部
        # `_schedule_reconnect()` 接手（`max_reconnect_attempts=-1` ⇒ 无限重试），
        # `loop.run_forever()` 就一直挂着。外层 `run_forever` 因此是**兜底**那一层，
        # ⛔ 不是主重连路径——主重连是 SDK 的（design D8）。
        #
        # 🔴 **但"兜底"只有在 `run()` 真的会返回时才成立**（TD-39·N-0）：接收 task
        # 被异常打死之后 `loop.run_forever()` 照样挂着，`run()` 于是永不返回，外层
        # 这一层**一次都不会触发**。`run_liveness_watchdog` 就是补这个洞的——它在
        # 另一条线程上盯 `liveness.json` 的 `stamp_at`，停更超阈值就用
        # `loop_stopper` 把这个 loop 停掉，`run()` 才返回、外层才接得上。
        client.run()

    return connect_once


# ── N-0 兜底：存活戳看门狗 ──────────────────────────────────────────────────
#
# 2026-09-10（TD-42）重写了判据与处置，⛔ 但 TD-39 的路径（停 loop 让外层重建）原样保留：
#
#   判据（`compute_liveness_verdict`，纯函数）：
#     stale_stamp     存活戳停更 > 180 秒（值守线程死了／断线定格）——TD-39 原判据
#     stale_activity  `connected` 但 SDK > 600 秒没送来任何东西——TD-42 新判据
#     unknown_format  旧格式或字段坏了 ⇒ 需要关注，⛔ 不当健康、也 ⛔ 不据此拆连接
#     ok
#   处置（`LivenessWatchdog.check_once`）：
#     假死 ⇒ ① 有把手先 `request_rebuild()`（TD-39 路径）；② 没把手／把手无效（宽限期
#     满仍假死）⇒ ERROR ＋ 告警 ＋ 记台账 ⇒ **终止进程**，launchd `KeepAlive` 拉起。
#     ⛔ 「能拿到把手」不再是前提——那正是 TD-42 失效的地方。


VERDICT_OK = "ok"
VERDICT_STALE_STAMP = "stale_stamp"
VERDICT_STALE_ACTIVITY = "stale_activity"
VERDICT_UNKNOWN_FORMAT = "unknown_format"

#: 存活戳里 `last_event_at` 的键名。⚠️ 与 `session.LIVENESS_EVENT_KEY` 必须一致；
#: 这里另写一份字面量是为了让本模块不 import `session`（它拖着 sqlite 与幂等层），
#: `tests/test_session_client_td42.py` 直接用 `session.LIVENESS_EVENT_KEY` 造存活戳，
#: 两处漂移会当场红。
LIVENESS_EVENT_KEY = "last_event_at"


@dataclass(frozen=True)
class LivenessVerdict:
    """一次判定的结果。`reference_at` = 判定所依据的那个时刻（给日志与告警用）。"""

    kind: str
    detail: str
    reference_at: str | None = None

    @property
    def is_stale(self) -> bool:
        return self.kind in (VERDICT_STALE_STAMP, VERDICT_STALE_ACTIVITY)

    @property
    def needs_attention(self) -> bool:
        return self.kind != VERDICT_OK


def _parse_instant(text) -> datetime | None:
    """ISO8601（带时区）→ datetime；任何看不清的情况返回 None，⛔ 不猜。"""
    if not text or not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        # naive 时间与带时区的 `now` 相减会抛 TypeError。⛔ 不猜它是哪个时区。
        return None
    return moment


def compute_liveness_is_stale(
    stamp_at: str | None,
    now: datetime,
    *,
    threshold_seconds: float = STALE_LIVENESS_SECONDS,
) -> bool:
    """存活戳是不是已经陈旧到该判"假死"了。**纯函数**（铁律 2）：不读文件、不打日志。

    `stamp_at` 是 `liveness.json` 里的 ISO8601 字符串（`session.format_instant` 的
    输出，带 +08:00）。

    **三种"看不清"一律返回 False，⛔ 不猜**：
    - 读不到／空（进程刚起、值守线程还没盖第一个戳）——判 True 会让**每一次启动**
      都先炸一轮兜底重建；
    - 解析不了（文件被写坏）——存活戳只是诊断信息，⛔ 不许因为温度计坏了就拆连接；
    - 戳比"现在"还新（机器时钟被往回调过）——负数的"停更时长"没有意义。
    """
    stamped = _parse_instant(stamp_at)
    if stamped is None:
        return False
    age_seconds = (now - stamped).total_seconds()
    if age_seconds < 0:
        return False
    return age_seconds > threshold_seconds


def compute_liveness_verdict(
    payload: dict | None,
    now: datetime,
    *,
    started_at: datetime,
    threshold_seconds: float = STALE_LIVENESS_SECONDS,
    activity_threshold_seconds: float = STALE_SDK_ACTIVITY_SECONDS,
) -> LivenessVerdict:
    """看门狗的判据。**纯函数**（铁律 2）：不读文件、不打日志。

    `started_at` = 本进程看门狗起来的时刻。🔴 早于它的存活戳是**上一次运行的残留**
    （`0909AJ` 附带发现的冷启动误报），⛔ 不算本进程的证据，改以 `started_at` 计时。
    改法 1 把「假死 ⇒ 终止进程」接上之后，这条误报会变成每次冷启动都自杀的死循环，
    所以它从「刻意不修」升级成必须修。反过来，启动后值守线程一直没盖过戳，同样以
    `started_at` 计时、超阈值照判——⛔ 不许因为「没有本进程的戳」就永远不判。

    判定顺序（先重后轻）：
    1. 存活戳停更 > `threshold_seconds` ⇒ `stale_stamp`（TD-39 原判据，⛔ 不变）；
    2. 旧格式（缺 `last_event_at` 键）或字段坏了 ⇒ `unknown_format`：需要关注，
       ⛔ 不当健康，也 ⛔ 不据此拆连接（温度计坏了不等于病人死了）；
    3. `connected` 且 SDK > `activity_threshold_seconds` 没送来任何东西 ⇒ `stale_activity`
       （TD-42）。⛔ 只在 `connected` 下判：断线期间存活戳定格是第 7 章契约，由第 1 条管；
    4. 其余 ⇒ `ok`。
    """
    own_stamp: datetime | None = None
    if isinstance(payload, dict):
        stamped = _parse_instant(payload.get("stamp_at"))
        if stamped is not None and stamped >= started_at:
            own_stamp = stamped
    reference = own_stamp if own_stamp is not None else started_at
    age = (now - reference).total_seconds()
    if age > threshold_seconds:
        return LivenessVerdict(
            VERDICT_STALE_STAMP,
            f"存活戳已 {age:.0f} 秒没有刷新（阈值 {threshold_seconds:.0f} 秒）",
            reference.isoformat(timespec="microseconds"),
        )
    if own_stamp is None:
        # 还没有本进程的戳（刚起、或残留）：以启动时刻计时，阈值内一律 ok。
        return LivenessVerdict(
            VERDICT_OK,
            "本进程尚未盖戳，以启动时刻计时",
            reference.isoformat(timespec="microseconds"),
        )
    if LIVENESS_EVENT_KEY not in payload:
        return LivenessVerdict(
            VERDICT_UNKNOWN_FORMAT,
            f"存活戳是旧格式（无 {LIVENESS_EVENT_KEY}），无法判断 SDK 是否在收东西，需要关注",
            own_stamp.isoformat(timespec="microseconds"),
        )
    if payload.get("state") != "connected":
        return LivenessVerdict(VERDICT_OK, f"状态 {payload.get('state')!r}，由存活戳判据管")
    raw_event_at = payload.get(LIVENESS_EVENT_KEY)
    if raw_event_at is None:
        # connected 却从未有事件——on_connected 本身就会写事件时刻，这只可能是别的写法
        # 造出来的；以 `since` 计时，仍然要判。
        event_at = _parse_instant(payload.get("since"))
    else:
        event_at = _parse_instant(raw_event_at)
    if event_at is None:
        return LivenessVerdict(
            VERDICT_UNKNOWN_FORMAT,
            f"存活戳的 {LIVENESS_EVENT_KEY} 无法解析（{raw_event_at!r}），需要关注",
            own_stamp.isoformat(timespec="microseconds"),
        )
    quiet_for = (now - event_at).total_seconds()
    if quiet_for > activity_threshold_seconds:
        return LivenessVerdict(
            VERDICT_STALE_ACTIVITY,
            f"state=connected 且存活戳在推进，但 SDK 已 {quiet_for:.0f} 秒没送来任何东西"
            f"（阈值 {activity_threshold_seconds:.0f} 秒）——连着但收不到",
            event_at.isoformat(timespec="microseconds"),
        )
    return LivenessVerdict(VERDICT_OK, "存活戳与 SDK 活动都新鲜")


def compute_terminate_grace(previous_consecutive: int) -> float:
    """这一次终止之前要等的宽限期（秒）。纯函数。

    `previous_consecutive` = 台账里已记的连续次数，这一次将是第 `previous_consecutive + 1` 次。
    前 N-1 次 0 秒（立刻）；第 N 次起 `TERMINATE_GRACE_SECONDS` 翻倍，封顶
    `TERMINATE_GRACE_CAP_SECONDS`。见 `CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF`。
    """
    this_one = max(previous_consecutive, 0) + 1
    if this_one < CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF:
        return 0.0
    exponent = min(this_one - CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF, _SATURATION_ATTEMPT)
    return min(TERMINATE_GRACE_SECONDS * (2 ** exponent), TERMINATE_GRACE_CAP_SECONDS)


def read_termination_ledger(path: pathlib.Path | None, now: datetime) -> dict | None:
    """读看门狗台账（上一次终止的记录）。读不到／坏了／太旧一律 None，⛔ 不猜。"""
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("看门狗台账读取失败，按「没有上一次」处理：%s", path, exc_info=True)
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("consecutive"), int):
        return None
    last_at = _parse_instant(payload.get("last_terminated_at"))
    if last_at is None or (now - last_at).total_seconds() > LEDGER_MAX_AGE_SECONDS:
        return None
    return payload


def write_termination_ledger(path: pathlib.Path | None, payload: dict) -> None:
    """覆写台账。⛔ 不用 `with open(...)`（见 session.py 模块 docstring 第 1 条）。"""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def terminate_process(exit_code: int = WATCHDOG_EXIT_CODE) -> None:
    """真的把进程结束掉。⛔ 只能在日志与告警都落下之后调。

    用 `os._exit` 而不是抛 `SystemExit`：本函数跑在**看门狗线程**上，
    `SystemExit` 只会结束这一条线程，主线程的 `loop.run_forever()` 照样挂着——
    那就是把 TD-42 原样复制一份。`os._exit` 不跑 `finally`、不 flush，所以先把
    标准流与本包 logger 链上的 handler 全部 flush 一遍（launchd 下 stderr 是文件、
    块缓冲，SDK 的最后几行否则会丢）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 —— 临终 flush 失败不许挡住退出
            pass
    current = logger
    while current is not None:
        for handler in list(current.handlers):
            try:
                handler.flush()
            except Exception:  # noqa: BLE001
                pass
        current = current.parent
    os._exit(exit_code)


class LivenessWatchdog:
    """看门狗的状态与处置（一个进程一只）。`run_liveness_watchdog` 驱动它。

    ⛔ 不碰库、不碰状态机、不碰 `liveness.json`（只读）。写的只有自己的台账。
    """

    def __init__(
        self,
        *,
        read_liveness: Callable[[], dict | None],
        clock: Callable[[], datetime],
        request_rebuild: Callable[[], bool],
        terminate: Callable[[int], None] = terminate_process,
        alert_sink: "liaison_alerts.AlertSink | None" = None,
        ledger_path: pathlib.Path | None = None,
        threshold_seconds: float = STALE_LIVENESS_SECONDS,
        activity_threshold_seconds: float = STALE_SDK_ACTIVITY_SECONDS,
    ) -> None:
        self._read_liveness = read_liveness
        self._clock = clock
        self._request_rebuild = request_rebuild
        self._terminate = terminate
        self._alert_sink = (
            alert_sink if alert_sink is not None else liaison_alerts.LoggingAlertSink()
        )
        self._ledger_path = ledger_path
        self._threshold_seconds = threshold_seconds
        self._activity_threshold_seconds = activity_threshold_seconds
        self.started_at = clock()
        previous = read_termination_ledger(ledger_path, self.started_at)
        self._previous_consecutive = previous["consecutive"] if previous else 0
        #: 本次「假死」连续段的起点；None = 当前不在假死里。
        self._stale_since: datetime | None = None
        #: 本段里是否成功请求过停 loop（有把手）。
        self._stop_requested = False
        #: 本段里「按退避还要等多久才终止」是否已经记过（⛔ 不每 15 秒刷一行 ERROR）。
        self._backoff_logged = False
        self._attention_logged_at: datetime | None = None
        if self._previous_consecutive:
            logger.warning(
                "看门狗台账：上一次运行已被看门狗终止（连续第 %s 次，%s）。"
                "本次判定假死后将等 %.0f 秒宽限期再终止",
                self._previous_consecutive,
                previous.get("last_terminated_at"),
                compute_terminate_grace(self._previous_consecutive),
            )

    # ── 一轮 ──
    def check_once(self) -> LivenessVerdict:
        now = self._clock()
        verdict = compute_liveness_verdict(
            self._read_liveness(),
            now,
            started_at=self.started_at,
            threshold_seconds=self._threshold_seconds,
            activity_threshold_seconds=self._activity_threshold_seconds,
        )
        if verdict.kind == VERDICT_UNKNOWN_FORMAT:
            self._stale_since = None
            self._log_attention(verdict, now)
            return verdict
        if not verdict.is_stale:
            self._stale_since = None
            self._stop_requested = False
            return verdict
        if self._stale_since is None:
            self._stale_since = now
            self._stop_requested = False
            self._backoff_logged = False
            logger.warning(
                "判定连接假死：%s（依据时刻 %s）。先请求停掉事件循环让外层重连接手",
                verdict.detail,
                verdict.reference_at,
            )
        # TD-39 路径：**一段假死只请求一次**重建。有把手它就能救回来，救回来了
        # 存活戳会变新、这一段就结束；没救回来（宽限期满仍假死）⇒ 终止进程。
        # ⚠️ 2026-09-10 之前是「每一轮陈旧都请求一次」：长断网期间每 15 秒就把刚造出来
        # 的新连接对象再停一次，SDK 自己的退避永远走不到第二档。改成一次之后，
        # 宽限期内由 SDK 的重连链（TD-39 已验）接手，看门狗只在它也没救回来时才出手。
        if not self._stop_requested:
            if self._request_rebuild():
                self._stop_requested = True
            else:
                # WARNING 而不是 ERROR：真正的 ERROR 由紧接着的终止路径落，一次假死一条。
                logger.warning(
                    "兜底重建请求未能执行——没有可停的事件循环把手，改走终止进程的路径"
                )
        self._maybe_terminate(verdict, now)
        return verdict

    def _log_attention(self, verdict: LivenessVerdict, now: datetime) -> None:
        """未知格式：留一个可见的关注信号，但按阈值限频，⛔ 不每 15 秒刷一行。"""
        last = self._attention_logged_at
        if last is not None and (now - last).total_seconds() < self._threshold_seconds:
            return
        self._attention_logged_at = now
        logger.warning("存活戳需要关注：%s", verdict.detail)

    def _maybe_terminate(self, verdict: LivenessVerdict, now: datetime) -> None:
        assert self._stale_since is not None
        required = compute_terminate_grace(self._previous_consecutive)
        if self._stop_requested:
            # 有把手：给 TD-39 的路径一个完整的宽限期，⛔ 不许上来就自杀。
            required = max(required, TERMINATE_GRACE_SECONDS)
        waited = (now - self._stale_since).total_seconds()
        if waited < required:
            if not self._stop_requested and not self._backoff_logged:
                self._backoff_logged = True
                logger.error(
                    "假死已持续 %.0f 秒，按连续假死退避还要等 %.0f 秒才终止进程（连续第 %s 次）",
                    waited,
                    required - waited,
                    self._previous_consecutive + 1,
                )
            return
        self._terminate_now(verdict, now)

    def _terminate_now(self, verdict: LivenessVerdict, now: datetime) -> None:
        lifetime = (now - self.started_at).total_seconds()
        consecutive = (
            self._previous_consecutive + 1 if lifetime < SHORT_LIFE_SECONDS else 1
        )
        # ⛔ 顺序钉死：先 ERROR、再告警、再台账，最后才终止——静默退出只是把「假死」
        # 换成另一种无症状。
        logger.error(
            "看门狗终止进程（退出码 %s，交给 launchd KeepAlive 拉起）：%s；依据时刻 %s；"
            "本进程已运行 %.0f 秒；连续第 %s 次。%s",
            WATCHDOG_EXIT_CODE,
            verdict.detail,
            verdict.reference_at,
            lifetime,
            consecutive,
            (
                "🔴 连续「起来就假死」，可能是系统性故障（凭据／网络／SDK 契约），请人工介入。"
                if consecutive >= CONSECUTIVE_TERMINATIONS_BEFORE_BACKOFF
                else ""
            ),
        )
        liaison_alerts.effect_emit_alert(
            self._alert_sink, compute_stall_alert_text(verdict, now, consecutive=consecutive)
        )
        try:
            write_termination_ledger(
                self._ledger_path,
                {
                    "consecutive": consecutive,
                    "last_terminated_at": now.isoformat(timespec="microseconds"),
                    "reason": verdict.kind,
                    "detail": verdict.detail,
                    "reference_at": verdict.reference_at,
                    "process_lifetime_seconds": round(lifetime),
                },
            )
        except OSError:
            # 台账只是计数；磁盘出问题 ⛔ 不许挡住终止——否则假死进程因为写不了
            # 一个 json 而永远活着。
            logger.error("看门狗台账写入失败，本次终止不计数", exc_info=True)
        self._previous_consecutive = consecutive
        self._stale_since = None
        self._stop_requested = False
        self._terminate(WATCHDOG_EXIT_CODE)


def compute_stall_alert_text(
    verdict: LivenessVerdict, now: datetime, *, consecutive: int
) -> str:
    """假死告警文本。纯函数。⛔ 不许出现 `alerts.FORBIDDEN_ALERT_CLAIMS` 里的任何说法。"""
    reference = verdict.reference_at or "未知"
    return (
        "【HR 值守通道·连接假死】"
        f"{verdict.detail}（依据时刻 {reference}）。"
        f"看门狗已于 {now.strftime('%Y-%m-%d %H:%M:%S')} 终止进程，等待 launchd 拉起重连"
        f"（连续第 {consecutive} 次）。"
        f"自依据时刻起{liaison_alerts.ALERT_RESEND_SENTENCE}。"
    )


def run_liveness_watchdog(
    *,
    read_liveness: Callable[[], dict | None],
    clock: Callable[[], datetime],
    request_rebuild: Callable[[], bool],
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = WATCHDOG_POLL_SECONDS,
    threshold_seconds: float = STALE_LIVENESS_SECONDS,
    activity_threshold_seconds: float = STALE_SDK_ACTIVITY_SECONDS,
    terminate: Callable[[int], None] = terminate_process,
    alert_sink: "liaison_alerts.AlertSink | None" = None,
    ledger_path: pathlib.Path | None = None,
) -> None:
    """看门狗主体，跑在自己的线程上（主线程被 `client.run()` 占着）。

    `read_liveness` 返回整份存活戳（dict）或 None——⚠️ 2026-09-10（TD-42）从只读
    `stamp_at` 改成读整份，判据要看 `state` 与 `last_event_at`。

    🔴 **看门狗自己 ⛔ 不许成为新的静默故障源。** 本轮检查抛异常时：记 ERROR
    并继续下一轮，⛔ 不许写成 `except Exception: pass`——那样看门狗死了没人知道，
    兜底层等于不存在，而这正是 TD-39 那一类无症状故障的形状。
    """
    dog = LivenessWatchdog(
        read_liveness=read_liveness,
        clock=clock,
        request_rebuild=request_rebuild,
        terminate=terminate,
        alert_sink=alert_sink,
        ledger_path=ledger_path,
        threshold_seconds=threshold_seconds,
        activity_threshold_seconds=activity_threshold_seconds,
    )
    while not should_stop():
        try:
            dog.check_once()
        except Exception:  # noqa: BLE001 —— 看门狗 ⛔ 不许被任何一轮的失败打死
            logger.error("存活戳看门狗本轮检查失败，本轮跳过、继续守着", exc_info=True)
        sleep(poll_seconds)


# ── TD-42：从 SDK 日志里看入站证据 ─────────────────────────────────────────


def verify_sdk_activity_markers(ws_source: str) -> None:
    """核对 `SDK_ACTIVITY_LOG_MARKERS` 每一条都还在 SDK 的 `ws.py` 源码里，不在就 raise。

    ⛔ 不许降级成 warning：文案一变，`last_event_at` 就永远不再推进 ⇒ 健康连接每
    600 秒被判假死一次 ⇒ 十分钟一杀。这与 `verify_client_surface` 是同一种护栏。
    """
    missing = [marker for marker in SDK_ACTIVITY_LOG_MARKERS if marker not in ws_source]
    if missing:
        raise SdkSurfaceUnverifiedError(
            f"SDK 的 ws.py 源码里找不到活动文案 {missing}（期望 {list(SDK_ACTIVITY_LOG_MARKERS)}）。"
            "SDK 大概换了版本或改了日志文案；请按 aibot/ws.py 实际文案更新 "
            "SDK_ACTIVITY_LOG_MARKERS，并把原始行号落进 docs/findings/。⛔ 不要绕过本检查。"
        )


class SdkLogObserver:
    """接在 `WSClientOptions.logger` 上的观察者：每一行原样转给 `delegate`，
    见到入站证据（`SDK_ACTIVITY_LOG_MARKERS`）就调 `on_activity`。

    - `on_any_log`：任何一行都调（SDK 的日志都在它自己的事件循环里打，是抓 loop 把手
      的又一个时机，`__main__` 接的是 `LoopStopper.capture`）。
    - 🔴 两个回调抛出的异常一律吞掉并记日志：本对象的方法从 `ws.py::_handle_frame`
      里被调，异常冒出去会打死接收 task——TD-39 同形。
    - `delegate` 为 None 时自己按 SDK `DefaultLogger` 的格式打到 stderr，SDK 日志去向不变。
    - ⛔ 不写 `with`（见 session.py 模块 docstring 第 1 条）。
    """

    def __init__(
        self,
        *,
        on_activity: Callable[[], None],
        on_any_log: Callable[[], object] | None = None,
        delegate=None,
    ) -> None:
        self._on_activity = on_activity
        self._on_any_log = on_any_log
        self.delegate = delegate

    def _observe(self, message) -> None:
        if self._on_any_log is not None:
            try:
                self._on_any_log()
            except Exception:  # noqa: BLE001 —— 见类 docstring
                logger.warning("SDK 日志观察者的 on_any_log 抛出异常，已吞掉", exc_info=True)
        if isinstance(message, str) and message.startswith(SDK_ACTIVITY_LOG_MARKERS):
            try:
                self._on_activity()
            except Exception:  # noqa: BLE001 —— 见类 docstring
                logger.warning("SDK 日志观察者的 on_activity 抛出异常，已吞掉", exc_info=True)

    def _forward(self, level: str, message, args) -> None:
        delegate = self.delegate
        if delegate is None:
            print(f"[{level.upper()}] {message}", *args, file=sys.stderr)
            return
        try:
            getattr(delegate, level)(message, *args)
        except Exception:  # noqa: BLE001 —— 原 logger 炸了 ⛔ 不许连累 SDK 的接收循环
            logger.warning("SDK 原 logger 抛出异常，本行 SDK 日志已丢弃", exc_info=True)

    def debug(self, message, *args) -> None:
        self._observe(message)
        self._forward("debug", message, args)

    def info(self, message, *args) -> None:
        self._observe(message)
        self._forward("info", message, args)

    def warn(self, message, *args) -> None:
        self._observe(message)
        self._forward("warn", message, args)

    def error(self, message, *args) -> None:
        self._observe(message)
        self._forward("error", message, args)


def build_client(credentials, *, sdk_logger: "SdkLogObserver | None" = None):
    """按凭据造一个 SDK 连接对象。

    ⛔ `import aibot` 同样写在函数体里（根 venv 不装 SDK）。装不上时抛的是
    `ImportError`，调用方据此给一个**专用退出码**——SDK 缺失是配置问题，
    ⛔ 不许被当成"网络不好"进重试循环，那会让一个永远不会自愈的故障
    看起来像是在等待恢复。

    `sdk_logger`（TD-42）：传了就把它接进 SDK，并先核对活动文案仍在 SDK 源码里
    （对不上 ⇒ `SdkSurfaceUnverifiedError`，`__main__` 以退出码 4 拒绝启动）。
    观察者没有 delegate 时补上 SDK 自己的 `DefaultLogger`，SDK 日志去向不变。
    """
    import aibot
    import aibot.ws

    if sdk_logger is not None:
        verify_sdk_activity_markers(inspect.getsource(aibot.ws))
        if sdk_logger.delegate is None:
            sdk_logger.delegate = aibot.DefaultLogger()
    return aibot.WSClient(build_ws_options(credentials, sdk_logger=sdk_logger))
