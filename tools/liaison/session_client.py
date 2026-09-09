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

import inspect
import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: 外层退避：1s 起、翻倍、封顶 30s。封顶值与 SDK 内置退避的封顶取同一个数，
#: 让两层的节奏是一致的（design D8 记的 SDK 封顶就是 30s）。
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
DEFAULT_HEARTBEAT_SECONDS = 30

#: ⚠️ 事件名与必需方法名以 Step 5 的探针实测为准（见 docs/findings/
#: 2026-09-09-aibot-wsclient-表面实测.md）。探针没跑成时这里保持默认值，
#: `make_sdk_connect` 会在启动时**当场报错**，⛔ 不会静默错接线。
EVENT_CONNECTED = "connected"
EVENT_DISCONNECTED = "disconnected"

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


def build_ws_options(credentials, *, heartbeat_interval: int = DEFAULT_HEARTBEAT_SECONDS):
    """构造 SDK 的连接参数。

    ⛔ `import aibot` 写在函数体里：根 venv 不装 SDK（design D10 的依赖隔离），
    模块层 import 会让全量 pytest 在 collect 阶段就整个红掉。
    """
    import aibot

    return aibot.WSClientOptions(
        bot_id=credentials.bot_id,
        secret=credentials.bot_secret,
        heartbeat_interval=heartbeat_interval,
        max_reconnect_attempts=UNLIMITED_RECONNECT_ATTEMPTS,
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
            f"{EVENT_CONNECTED!r}/{EVENT_DISCONNECTED!r} 两个连接事件。"
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


def _prepare_client(client_factory, on_connected, on_disconnected):
    """造一个连接对象、核表面、接事件。返回**尚未 run** 的那个对象。

    先核表面再接线：对不上就 raise，⛔ 不许"能接的先接上、接不上的算了"。
    """
    client = client_factory()
    verify_client_surface(client)
    client.on(EVENT_CONNECTED, lambda *args, **kwargs: on_connected())
    client.on(EVENT_DISCONNECTED, lambda *args, **kwargs: on_disconnected())
    return client


def make_sdk_connect(
    client_factory: Callable[[], object],
    *,
    on_connected: Callable[[], None],
    on_disconnected: Callable[[], None],
) -> Callable[[], None]:
    """返回一个交给 `run_forever` 用的、真正阻塞到断开为止的调用。

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
    """
    prepared = _prepare_client(client_factory, on_connected, on_disconnected)

    def connect_once() -> None:
        nonlocal prepared
        client = prepared
        prepared = None
        if client is None:
            client = _prepare_client(client_factory, on_connected, on_disconnected)
        # ⚠️ `client.run()` 在真实 SDK 上几乎不会返回：建连失败由 SDK 内部
        # `_schedule_reconnect()` 接手（`max_reconnect_attempts=-1` ⇒ 无限重试），
        # `loop.run_forever()` 就一直挂着。外层 `run_forever` 因此是**兜底**那一层，
        # ⛔ 不是主重连路径——主重连是 SDK 的（design D8）。
        client.run()

    return connect_once


def build_client(credentials):
    """按凭据造一个 SDK 连接对象。

    ⛔ `import aibot` 同样写在函数体里（根 venv 不装 SDK）。装不上时抛的是
    `ImportError`，调用方据此给一个**专用退出码**——SDK 缺失是配置问题，
    ⛔ 不许被当成"网络不好"进重试循环，那会让一个永远不会自愈的故障
    看起来像是在等待恢复。
    """
    import aibot

    return aibot.WSClient(build_ws_options(credentials))
