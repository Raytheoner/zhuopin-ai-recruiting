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
import logging
import time
from collections.abc import Callable
from datetime import datetime

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

#: 本模块必须订阅的全部事件。**清单即契约**：`_prepare_client` 逐条接线，
#: `tests/test_session_client_reconnect.py::test_every_event_in_the_contract_is_actually_subscribed`
#: 断言"列了就必须真的接上"。⛔ 加常量不接线 = 该类事件永远到不了本模块，
#: 与 TD-39 同形、同样无症状。
SUBSCRIBED_EVENTS = (EVENT_CONNECTED, EVENT_DISCONNECTED, EVENT_ERROR)

#: 存活戳停更多久算"loop 还活着但连接已死"（N-0 兜底的判据）。
#:
#: ⚠️ **取值依据是实测，⛔ 不许拍脑袋改小**：2026-09-09 实测 SDK 的判死时延是
#: **55.75 秒**（心跳 30 秒 × 2 次未回 pong）。阈值必须**明显大于**它，否则会把
#: 正常的判死过程本身误判成假死，看门狗于是主动拆掉一条正在自愈的连接——
#: 一个"修复"反过来制造断线。180 秒 ≈ 3.2×，且让 SDK 自己的重连（退避封顶 30 秒）
#: 有好几轮完整的机会先跑，兜底层才接手。
#:
#: ⚠️ **已知且刻意接受的行为**：真实长断网（比如网线拔了一小时）期间存活戳同样
#: 一直停在断线时刻（`tick()` 只在 `connected` 状态下刷新），于是看门狗每 ~180 秒
#: 就拆一次连接、由外层 `run_forever` 造一个全新的连接对象重来。
#: ⛔ 这不是 bug，⛔ 也不要为它加"断网期间不看门"的例外：
#: ① 一小时约 17 次重建，远达不到企微限流的量级（TD-38 那次是 44 秒 1399 次心跳）；
#: ② 本条修的正是"SDK 自己的重连链会被打断"，长断网恰恰是它最可能卡死的时候，
#:    定期从头重建是**保护**不是浪费；
#: ③ 要加例外就得先分辨"真断网"与"假死"，而这两者从进程外部看**完全一样**——
#:    分辨得了的话，一开始就不需要看门狗了。
STALE_LIVENESS_SECONDS = 180.0

#: 看门狗两次检查之间等多久。比 `__main__.TICK_INTERVAL_SECONDS`（15 秒）不快，
#: ⛔ 不许设成 0——0 就是满速自旋（与 `compute_backoff_delay` 永不返回 0 同理）。
WATCHDOG_POLL_SECONDS = 15.0

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


def build_ws_options(credentials, *, heartbeat_interval: int = DEFAULT_HEARTBEAT_MS):
    """构造 SDK 的连接参数。

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
            f"{list(SUBSCRIBED_EVENTS)} 这几个连接事件。"
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
            logger.error(
                "存活戳已陈旧，但没有可停的事件循环把手（本次连接从未触发过任何"
                "SDK 事件）。⛔ 本轮兜底重建没能执行，连接可能仍卡在假死状态。"
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


def _prepare_client(client_factory, on_connected, on_disconnected, loop_stopper):
    """造一个连接对象、核表面、接事件。返回**尚未 run** 的那个对象。

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

    `loop_stopper` 是 N-0 兜底的把手（见 `LoopStopper`）。不传就自己造一个——
    调用方不接看门狗时，行为与从前完全一致。
    """
    if loop_stopper is None:
        loop_stopper = LoopStopper()
    prepared = _prepare_client(client_factory, on_connected, on_disconnected, loop_stopper)

    def connect_once() -> None:
        nonlocal prepared
        client = prepared
        prepared = None
        if client is None:
            client = _prepare_client(
                client_factory, on_connected, on_disconnected, loop_stopper
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
    if not stamp_at:
        return False
    try:
        stamped = datetime.fromisoformat(stamp_at)
    except (TypeError, ValueError):
        return False
    if stamped.tzinfo is None:
        # naive 时间与带时区的 `now` 相减会抛 TypeError。⛔ 不猜它是哪个时区。
        return False
    age_seconds = (now - stamped).total_seconds()
    if age_seconds < 0:
        return False
    return age_seconds > threshold_seconds


def check_liveness_once(
    *,
    read_stamp_at: Callable[[], str | None],
    clock: Callable[[], datetime],
    request_rebuild: Callable[[], bool],
    threshold_seconds: float = STALE_LIVENESS_SECONDS,
) -> bool:
    """看一眼存活戳；陈旧就请求重建。返回"本轮是否触发了重建请求"。

    ⚠️ 时间与读戳都由调用方注入：几分钟的场景要在单测里毫秒跑完，
    ⛔ 不许用真实 `sleep` 等时间过去。
    """
    stamp_at = read_stamp_at()
    if not compute_liveness_is_stale(stamp_at, clock(), threshold_seconds=threshold_seconds):
        return False
    logger.warning(
        "存活戳已 %.0f 秒以上没有刷新（末次 %s），判定为「事件循环还活着但连接已死」，"
        "主动停掉事件循环让外层重连接手",
        threshold_seconds,
        stamp_at,
    )
    if not request_rebuild():
        # ⛔ 不许静默：兜底层没能兜住，必须留下一个能被看见的症状。
        logger.error(
            "兜底重建请求未能执行——连接可能仍卡在假死状态，且外层 run_forever 接不上手"
        )
    return True


def run_liveness_watchdog(
    *,
    read_stamp_at: Callable[[], str | None],
    clock: Callable[[], datetime],
    request_rebuild: Callable[[], bool],
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = WATCHDOG_POLL_SECONDS,
    threshold_seconds: float = STALE_LIVENESS_SECONDS,
) -> None:
    """看门狗主体，跑在自己的线程上（主线程被 `client.run()` 占着）。

    🔴 **看门狗自己 ⛔ 不许成为新的静默故障源。** 本轮检查抛异常时：记 ERROR
    并继续下一轮，⛔ 不许写成 `except Exception: pass`——那样看门狗死了没人知道，
    兜底层等于不存在，而这正是 TD-39 那一类无症状故障的形状。
    """
    while not should_stop():
        try:
            check_liveness_once(
                read_stamp_at=read_stamp_at,
                clock=clock,
                request_rebuild=request_rebuild,
                threshold_seconds=threshold_seconds,
            )
        except Exception:  # noqa: BLE001 —— 看门狗 ⛔ 不许被任何一轮的失败打死
            logger.error("存活戳看门狗本轮检查失败，本轮跳过、继续守着", exc_info=True)
        sleep(poll_seconds)


def build_client(credentials):
    """按凭据造一个 SDK 连接对象。

    ⛔ `import aibot` 同样写在函数体里（根 venv 不装 SDK）。装不上时抛的是
    `ImportError`，调用方据此给一个**专用退出码**——SDK 缺失是配置问题，
    ⛔ 不许被当成"网络不好"进重试循环，那会让一个永远不会自愈的故障
    看起来像是在等待恢复。
    """
    import aibot

    return aibot.WSClient(build_ws_options(credentials))
