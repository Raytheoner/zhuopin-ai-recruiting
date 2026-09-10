"""HR 值守通道服务的入口：`python -m tools.liaison`。

**三条线程，各管各的**（第 7 章 + TD-39）：
- **值守线程**独占 sqlite 连接与 `LiaisonSession`——库与状态机只有它一个人碰。
  它的循环是 `events.get(timeout=心跳间隔)`：有事件就处理，超时就盖存活戳。
- **主线程**只跑 `run_forever(connect)`。SDK 回调唯一做的事是把
  `(事件名, 当时的时间)` 放进队列，⛔ 回调里不碰库、不碰文件——sqlite 连接
  默认只能在创建它的线程里用，在断线回调里碰库会在最不该出错的那一刻抛异常。
- **看门狗线程**（2026-09-09 加，TD-39·N-0）只读 `liveness.json`，
  停更超阈值就停掉 SDK 的事件循环。⛔ 它不碰库、不碰状态机。
  ⚠️ 2026-09-10（TD-42）起它还看 `last_event_at`（SDK 多久没送来东西），且判定假死后
  **总能**终止进程（ERROR ＋ 告警 ＋ 台账之后 `os._exit`，launchd `KeepAlive` 拉起）——
  「拿到事件循环把手」不再是前提。它写的只有自己的台账 `watchdog.json`。
  *为什么需要第三条*：主线程被 `client.run()` 里的 `loop.run_forever()` 占死，
  接收 task 一旦被异常打掉，那个 loop 照样挂着 ⇒ `run()` 永不返回 ⇒ 外层
  `run_forever` 一次都不会触发。没有一条独立线程，就没人能把它叫醒。

**入站消息的去处**（2026-09-10，tasks 8.5bis）：SDK 的 `message` 回调与连接事件回调
同一纪律——**只把帧放进队列**，归档／入队／回复全部由**值守线程**做（工程铁律 1：
幂等记录与业务写必须与业务连接同属一个 `BEGIN`，而那条连接归值守线程独占）。
判据见 tests/test_inbound_wiring.py 与 tests/test_main_wiring.py::
test_this_chapter_wires_message_handling。⛔ 不许把 `handle_inbound_message` 挪进回调。

⛔ 模块层只 import 标准库与本服务自己的模块。任何 SDK import 都必须在凭据校验
**之后**、且写在函数体里——理由见 config.py 的模块 docstring。
"""

from __future__ import annotations

import logging
import os
import queue
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools.liaison import alerts, frames, inbound, session, session_client
from tools.liaison import logsetup
from tools.liaison.archive import DEFAULT_ARCHIVE_ROOT
from tools.liaison.config import load_credentials
from tools.liaison.errors import MissingCredentialsError
from tools.liaison.storage import db as liaison_db

logger = logging.getLogger(__name__)

#: 缺凭据的退出码。选 2 而不是 1：1 太容易和"脚本里随便哪一步炸了"混在一起，
#: 2 让 launchd / 人工排障能一眼分辨出"这是配置没配好，不是程序崩了"。
EXIT_MISSING_CREDENTIALS = 2
#: SDK 装不上（配置问题，⛔ 不进重试循环）。
EXIT_SDK_UNAVAILABLE = 3
#: SDK 的方法／事件表面与接线假设对不上（⛔ 不许硬着头皮跑）。
EXIT_SDK_SURFACE_UNVERIFIED = 4

#: 启动期自检子命令：把「读 .env → 校验凭据 → 造连接对象 → 核 SDK 表面 → 接事件」
#: 整条启动路径**原样**跑一遍，然后在**建立任何网络连接之前**退出。
#:
#: 🔴 它 ⛔ **不是**"跳过校验的开关"——恰恰相反，它把校验跑完才退出；它跳过的是
#: `run_forever(connect)`，也就是唯一会碰网络的那一步。加它的理由是 TD-36：
#: `test_liaison_credentials.py` 的两条用例用 `sys.executable` 起真实子进程、喂
#: 假凭据（`bot-1`/`sec-1`）。TD-19 之前它们停在 SDK 表面校验（exit 4）纯属运气；
#: TD-19 落地后那道拦阻消失，同样两条用例会带着假凭据**真的去连企微**——而且
#: ⛔ 没有任何报错会告诉你。
#:
#: ⛔ **不许写进 launchd plist 的 ProgramArguments**：那会让服务每次被拉起都
#: 立刻 exit 0，launchd 认为"跑完了"，值守通道从此根本不存在，且 ⛔ 无任何症状。
#: 守护断言：tests/test_launchd_plist.py::test_plist_never_runs_the_self_check_mode。
SELF_CHECK_ARG = "--self-check"

#: tools/liaison/__main__.py → parents[0]=liaison, [1]=tools, [2]=仓库根
LIAISON_DIR = Path(__file__).resolve().parents[0]
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 🔴 值守通道的 .env 在 **tools/liaison/**，⛔ 不是仓库根（2026-09-09 迁，TD-40）。
#:
#: 迁的理由不是整洁：`app/config.py` 的 `Settings` 是 pydantic-settings 的
#: `BaseSettings`，默认 `extra="forbid"`。三个 `HR_LIAISON_*` 键放在**根** `.env` 里，
#: `Settings()` 每次实例化都抛 `ValidationError: Extra inputs are not permitted`
#: ⇒ **整个 Web 服务起不来**，而且报错会把 `bot_secret` 明文打进输出。
#: 实测：带根 `.env` 的 checkout 上根 venv 全量 23 failed，没有 `.env` 的 worktree 0 failed。
#:
#: ⚠️ 与 design D10 同构：依赖在 `tools/liaison/requirements.txt`，配置就在
#: `tools/liaison/.env`，两套各归各位。⛔ **不做"根 .env 兜底"**——兜底会让
#: "键还留在根 .env 里"这个坏状态继续静默存在，而它正是本条要消灭的东西。
DEFAULT_DOTENV_PATH = LIAISON_DIR / ".env"

#: 测试专用逃生口：让用例指一个不存在的 .env，免得开发机上真实的 .env 把
#: "凭据缺失"这条用例喂绿。⛔ 不写进 .env.example——那会把它暗示成生产用法。
DOTENV_PATH_ENV = "HR_LIAISON_DOTENV_PATH"

#: 存活戳的刷新间隔。取 15s——SDK 心跳是 30s、2 次无响判死（design D8），
#: 比它快一档，外部读存活戳时不至于把"正常心跳间隙"看成"停更"。
TICK_INTERVAL_SECONDS = 15.0

EVENT_CONNECTED = session_client.EVENT_CONNECTED
EVENT_DISCONNECTED = session_client.EVENT_DISCONNECTED
#: 队列里的第三种事件（TD-42）：SDK 刚刚真的把东西送到了本进程（`authenticated`、
#: 心跳回包、推送）。值守线程收到它只更新存活戳的 `last_event_at`，⛔ 不改状态。
EVENT_SDK_ACTIVITY = "sdk_activity"

#: 队列里的第四种事件（8.5bis）：一条入站消息。**只有这一种事件带第三个元素**
#: （帧本身）——连接事件仍是二元组，⛔ 不许为了"整齐"把它们也改成三元组，那会
#: 让 test_main_wiring 里一批既有用例的入队形状跟着改，改动面远大于收益。
EVENT_MESSAGE = session_client.EVENT_MESSAGE

#: 看门狗台账（连续终止计数）。与存活戳同目录、同一个真源推导，⛔ 不另写路径字面量。
DEFAULT_WATCHDOG_LEDGER_PATH = session.DEFAULT_LIVENESS_PATH.with_name("watchdog.json")


def resolve_dotenv_path() -> Path:
    override = os.environ.get(DOTENV_PATH_ENV)
    return Path(override) if override else DEFAULT_DOTENV_PATH


def load_dotenv_into_environ(path: Path) -> None:
    """把 .env 的键值填进 os.environ，**已存在的环境变量不覆盖**。

    优先级口径与 app/config.py 用的 pydantic-settings 一致：进程环境 > .env 文件。
    ⛔ 不引入 python-dotenv：本服务依赖清单独立，标准库能解决的不加依赖，
    每多一个依赖就多一份"这东西会不会跟着被推到 .51"的疑问。

    文件不存在是正常情况（凭据也可以直接从进程环境给），静默返回。
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # 允许 `export KEY=value` 这种 shell 习惯写法：与本文件的扫描器
        # test_liaison_no_secrets_in_vcs.py 的 _ASSIGNMENT 正则口径保持一致，
        # 否则真实的 export 行会被当成变量名叫 "export XXX" 的键，诊断信息误导人。
        key = re.sub(r"^export[ \t]+", "", key)
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def now() -> datetime:
    """当前时间，带 +08:00。⛔ 唯一允许调 `datetime.now()` 的地方——
    状态机里一律由调用方传时间，好让两小时的场景能在单测里毫秒跑完。"""
    return datetime.now(session.CHINA_TZ)


def read_liveness_payload(
    path: Path = session.DEFAULT_LIVENESS_PATH,
) -> dict | None:
    """读整份存活戳（看门狗的唯一输入）。读不到一律 None。

    ⚠️ 2026-09-10（TD-42）从只读 `stamp_at` 改成读整份：判据要看 `state` 与
    `last_event_at`。

    ⚠️ **默认值必须直接引用 `session.DEFAULT_LIVENESS_PATH`**，⛔ 不许在本文件
    另写一份路径字面量：看门狗读的必须是值守线程**真的在写**的那一份。两处真源
    一旦漂移，看门狗要么盯着一个永远不更新的文件（每 3 分钟拆一次健康连接），
    要么盯着一个不存在的文件（永远不触发）——两种都没有任何症状。
    """
    return session.read_liveness_stamp(path)


def build_session() -> session.LiaisonSession:
    """在**调用它的那条线程里**建连接与状态机。⛔ 不许在别处建好再传进来。"""
    conn = liaison_db.get_connection()
    liaison_db.init_schema(conn)
    return session.LiaisonSession(conn, alerts.LoggingAlertSink())


def apply_connection_event(svc: session.LiaisonSession, name: str, moment: datetime) -> None:
    """把一个连接事件喂给状态机。未知事件只记 ERROR，⛔ 不许打死值守线程。"""
    if name == EVENT_CONNECTED:
        svc.on_connected(moment)
    elif name == EVENT_DISCONNECTED:
        svc.on_disconnected(moment)
    elif name == EVENT_SDK_ACTIVITY:
        svc.on_sdk_activity(moment)
    else:
        logger.error("收到未知的连接事件 %r（时间 %s），已忽略", name, moment)


@dataclass(frozen=True)
class InboundPorts:
    """入站处理要用到的三个外部落点。**默认值就是生产用的那一份。**

    ⚠️ 它存在的理由只有一个：让用例把归档根与名单指到 `tmp_path`，⛔ 不是配置项，
    ⛔ 不许给它加环境变量开关（口径与 `main()` 那四个接线缝关键字参数一致）。

    `reply`：礼貌回复的外发端口。**现在是 `None`**——本条（8.5bis）⛔ 不发任何真实
    消息；`handle_inbound_message` 对 `None` 有明确处置（留一条 WARNING，⛔ 不静默）。
    """

    archive_root: Path = DEFAULT_ARCHIVE_ROOT
    whitelist_path: Path | None = None
    reply: Callable[[str, str], object] | None = None


def handle_message_frame(
    svc: session.LiaisonSession,
    moment: datetime,
    frame,
    ports: InboundPorts,
) -> bool:
    """值守线程侧的入站处理：帧 → 参数 → 归档／入队／（名单外）回复。

    **跑在值守线程上**，用的是 `svc.conn`——那条独占连接，工程铁律 1 要求的
    "同一连接、同一 `BEGIN`"由 `handle_inbound_message` 内部的 `effect_*` 保证。
    ⛔ 不许从 SDK 回调线程调本函数。

    两类异常都**不许打死值守线程**（它死了 = 存活戳停更 = 看门狗把整个进程重启，
    一条畸形消息⛔ 不配有这种破坏力），但两类的性质不同、日志也分开：

    1. 映射对不上（`InboundFrameUnverifiedError`，现状必走这一支）——**fail-closed，
       本帧不落库**，并把帧的**键结构**（⛔ 无取值）打进日志，供 AT-1b 填路径表；
    2. 落库本身失败——材料没落定，ERROR 记清 `msgid`。⛔ 不吞、⛔ 不假装成功。

    返回是否真的落库了（用例据此断言，⛔ 不靠日志文本判断）。
    """
    try:
        fields = frames.compute_inbound_frame(frame)
    except session_client.SdkSurfaceUnverifiedError as exc:
        logger.error(
            "入站帧未落库（帧字段映射未经真实帧确认，fail-closed）：%s｜"
            "帧结构（只有键名与类型，⛔ 无取值）：%s",
            exc,
            frames.describe_frame_shape(frame),
        )
        return False

    try:
        inbound.handle_inbound_message(
            svc.conn,
            thread_id=fields.thread_id,
            msgid=fields.msgid,
            sender_userid=fields.sender_userid,
            received_at=session.format_instant(moment),
            msgtype=fields.msgtype,
            content=fields.content,
            # ⛔ 附件恒为 None：帧里的附件**句柄**要再走一次 SDK 下载才变成字节，
            # 那是一次网络调用，且句柄落在哪个键同样未经真实帧确认。与映射一起
            # 由 AT-1b 收口（已登记 TD）。⛔ 不许在这里猜一个 URL 去下载。
            attachment=None,
            archive_root=ports.archive_root,
            whitelist_path=ports.whitelist_path,
            reply=ports.reply,
        )
    except Exception:  # noqa: BLE001 —— 见 docstring：⛔ 不许打死值守线程
        logger.error(
            "入站消息处理失败，本条未落库。thread_id=%s msgid=%s",
            fields.thread_id,
            fields.msgid,
            exc_info=True,
        )
        return False
    return True


def run_session_worker(
    svc: session.LiaisonSession,
    events: "queue.Queue",
    stop_event,
    *,
    tick_interval: float = TICK_INTERVAL_SECONDS,
    clock=now,
    ports: InboundPorts | None = None,
) -> None:
    """值守线程主体：处理事件，没事件就盖存活戳。

    ⚠️ 事件自带时间戳（回调那一刻取的），这里 ⛔ 不许用 `clock()` 覆盖它——
    否则中断窗口的起点会比真实断线时间晚最多一个心跳间隔。
    """
    if ports is None:
        ports = InboundPorts()
    while not stop_event.is_set():
        try:
            name, moment, *payload = events.get(timeout=tick_interval)
        except queue.Empty:
            svc.tick(clock())
            continue
        if name == EVENT_MESSAGE:
            # ⚠️ 先记一次"SDK 真的送来了东西"（TD-42 的第二条判据看的就是它）：
            # 一条真实入站消息是对端在应答的最强证据，⛔ 不许让它只算"消息"不算
            # "活动"——否则一条消息流量正常、却因 authenticated/日志沉默被看门狗
            # 判成假死，把一条健康连接拆掉。
            svc.on_sdk_activity(moment)
            handle_message_frame(svc, moment, payload[0] if payload else None, ports)
            continue
        apply_connection_event(svc, name, moment)


def _session_thread_main(events, stop_event, session_builder) -> None:
    svc = session_builder()
    svc.start(now())
    run_session_worker(svc, events, stop_event)


def main(
    *,
    session_builder=build_session,
    client_builder=session_client.build_client,
    runner=session_client.run_forever,
    watchdog=session_client.run_liveness_watchdog,
    self_check: bool = False,
) -> int:
    """⚠️ 四个接线缝关键字参数（`session_builder` / `client_builder` / `runner` /
    `watchdog`）只给测试注入 fake 用，⛔ 不是配置项——⛔ 不要给它们加环境变量开关。

    `self_check=True` 见 `SELF_CHECK_ARG` 的说明：跑完整条启动路径（含 SDK 表面
    校验），**在建立任何网络连接之前**返回 0。⛔ 它不跳过任何一项校验。
    """
    logsetup.setup_logging()
    load_dotenv_into_environ(resolve_dotenv_path())

    try:
        credentials = load_credentials()
    except MissingCredentialsError as exc:
        # 只打变量名，⛔ 不打取值。进程立刻退，⛔ 不进任何等待/重试循环。
        print(str(exc), file=sys.stderr)
        # ⚠️ 把「刚才去哪儿找的」一并打出来：2026-09-09 把 .env 从仓库根迁到
        # tools/liaison/（TD-40）之后，"我明明配了啊"最可能的原因就是文件还在旧位置。
        # ⛔ 不打文件内容、不打取值，只打路径。
        print(f"（已从 {resolve_dotenv_path()} 读取；⛔ 仓库根的 .env 不再被本服务读）",
              file=sys.stderr)
        return EXIT_MISSING_CREDENTIALS

    events: queue.Queue = queue.Queue()

    # 🔴 **必须在这里造、并且传进去**（TD-39·N-0）：`make_sdk_connect` 不传就自己造
    # 一个，那个外面拿不到，看门狗于是停不了任何东西——兜底层看起来在跑、实际
    # 什么都没做，且 ⛔ 没有任何症状。
    loop_stopper = session_client.LoopStopper()

    # TD-42：SDK 活动的两个来源都汇到同一个回调——`authenticated` 事件（经
    # `make_sdk_connect`）与 SDK 日志里的入站证据（经 `SdkLogObserver`，接在
    # `WSClientOptions.logger` 上）。回调只做一件事：把 `(事件名, 当时的时间)` 放进
    # 队列，与连接事件回调同一纪律——⛔ 不碰库、不碰文件。
    # `on_any_log=loop_stopper.capture`：SDK 的每一行日志都在它的事件循环里打，
    # 是抓 loop 把手的又一个时机（连接建立前 SDK 就会打 `Connecting to WebSocket`）。
    def on_sdk_activity() -> None:
        events.put((EVENT_SDK_ACTIVITY, now()))

    # 8.5bis ③：入站消息回调**只做这一件事**。
    # 🔴 ⛔ 这个函数体里**永远只许有 `events.put`**：它跑在 SDK 的事件循环线程上，
    # 碰库会跨线程用值守线程那条 sqlite 连接（工程铁律 1 的事务归属当场破掉），
    # 碰文件会在回调里做阻塞 IO 把心跳拖住。判据：tests/test_inbound_wiring.py::
    # test_the_sdk_message_callback_body_only_puts_the_frame_on_the_queue（AST 扫本函数体）。
    def on_message(frame) -> None:
        events.put((EVENT_MESSAGE, now(), frame))

    sdk_logger = session_client.SdkLogObserver(
        on_activity=on_sdk_activity, on_any_log=loop_stopper.capture
    )

    try:
        # ⚠️ 传的是**工厂**不是对象：SDK 的 `_started` 闩锁让同一个连接对象没法
        # 重连第二次（详见 session_client.make_sdk_connect 的 docstring）。
        connect = session_client.make_sdk_connect(
            lambda: client_builder(credentials, sdk_logger=sdk_logger),
            on_connected=lambda: events.put((EVENT_CONNECTED, now())),
            on_disconnected=lambda: events.put((EVENT_DISCONNECTED, now())),
            loop_stopper=loop_stopper,
            on_activity=on_sdk_activity,
            on_message=on_message,
        )
    except ImportError as exc:
        print(
            f"HR 值守通道拒绝启动：aibot SDK 不可用（{exc}）。"
            "请在 tools/liaison/.venv 里装 tools/liaison/requirements.txt。"
            "⛔ 不要把它加进根 requirements.txt（design D10）。",
            file=sys.stderr,
        )
        return EXIT_SDK_UNAVAILABLE
    except session_client.SdkSurfaceUnverifiedError as exc:
        print(f"HR 值守通道拒绝启动：{exc}", file=sys.stderr)
        return EXIT_SDK_SURFACE_UNVERIFIED

    if self_check:
        # ⛔ 到此为止：⛔ 不起值守线程、⛔ 不调 runner、⛔ 不碰网络。
        print(
            "HR 值守通道启动期自检通过：凭据齐备、SDK 表面符合契约、连接事件已接线。"
            "⛔ 本次未建立任何连接（--self-check）。",
            file=sys.stderr,
        )
        return 0

    stop_event = threading.Event()
    worker = threading.Thread(
        target=_session_thread_main,
        args=(events, stop_event, session_builder),
        name="liaison-session",
        daemon=True,
    )
    worker.start()

    # ── 第三条线程：存活戳看门狗（TD-39·N-0 兜底 ＋ TD-42 终止路径）──────────
    # 主线程被 `client.run()` 占着（它内部 `loop.run_forever()`），值守线程独占库，
    # 所以这一层只能自己起一条线程。它只读 `liveness.json`：陈旧就先用 `loop_stopper`
    # 把 SDK 的事件循环停掉（`client.run()` 返回、外层 `run_forever` 接手）；没有把手
    # 或停了仍假死 ⇒ ERROR ＋ 告警 ＋ 台账 ⇒ 终止进程，launchd `KeepAlive` 拉起。
    #
    # ⚠️ `sleep` 用 `stop_event.wait`：停服时看门狗立刻醒来退出，⛔ 不许让它在
    # `time.sleep` 里再挂满一个轮询周期。
    # ⚠️ 告警走 `alerts.LoggingAlertSink`（与值守线程同一种出口）；⛔ 不许给看门狗
    # 一条库连接去写窗口——它不碰库。
    threading.Thread(
        target=watchdog,
        kwargs={
            "read_liveness": read_liveness_payload,
            "clock": now,
            "request_rebuild": loop_stopper.request_stop,
            "should_stop": stop_event.is_set,
            "sleep": stop_event.wait,
            "alert_sink": alerts.LoggingAlertSink(),
            "ledger_path": DEFAULT_WATCHDOG_LEDGER_PATH,
        },
        name="liaison-watchdog",
        daemon=True,
    ).start()

    try:
        runner(connect)
    except KeyboardInterrupt:
        logger.warning("收到中断信号，值守通道停止接收")
    finally:
        # ⛔ 必须放 finally：`runner` 无论怎么结束，两条后台线程都得收到停的信号。
        # 只在 KeyboardInterrupt 分支里 set，`runner` 正常返回时看门狗会一直空转。
        stop_event.set()
    return 0


# ── 第 8 章·留存期清理子命令（tasks 8.1–8.2）─────────────────────────────
# ⛔ 本段是**纯插入**：下面那两行既有入口一字节未动（日志泳道同时在 main()
# 函数体首行接线，两头各占一边，避免 merge 冲突）。
#
# 三个判据的顺序是刻意的：
# ① `__name__ == "__main__"` 放最前 ⇒ 被 import 时（测试、工具）这段完全惰性，
#    ⛔ 不许改成模块级裸判断；
# ② 它排在既有入口之前 ⇒ `raise SystemExit(main())` 不会先跑掉；
# ③ 清理**不需要企微凭据**（它不建连接），所以这条分支必须短路在
#    `main()` 的 `load_credentials()` 之前——否则一台还没配 BOT_ID 的机器
#    永远清理不了自己的过期数据。
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "cleanup":
    from tools.liaison.retention import cleanup_main

    raise SystemExit(cleanup_main(sys.argv[2:]))

# ── 8.7·跟进信群发子命令 ──────────────────────────────────────────────────
# ⛔ 又一段**纯插入**：上面的 cleanup 分支与下面的既有入口都一字节未动。
#
# 判据顺序与 cleanup 那段同理，第三条尤其要紧：**发跟进信不需要企微 SDK 凭据**
# （它不建长连接，只往群 webhook 发一次 HTTP），所以这条分支必须短路在
# `main()` 的 `load_credentials()` 之前——排到后面去，一台还没配 BOT_ID 的机器
# 就永远发不了跟进信，而报错说的是"缺 BOT_ID"，与真实原因毫无关系。
#
# 🔴 默认 dry-run，真发要显式 `--send`——理由见 followup.py 的模块 docstring。
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "send-followup":
    from tools.liaison.followup import send_followup_main

    # ⚠️ 群 webhook 在 `tools/liaison/.env` 里（真实值只落 .env，⛔ 不入版本管理）。
    # 守护进程那条路靠 `main()` 里的这一句读它；本分支短路在 `main()` 之前，
    # 所以必须自己读一次——否则他在配置完全正确的机器上跑也会得到"缺凭据"，
    # 而那个报错指向的原因是错的。
    load_dotenv_into_environ(resolve_dotenv_path())
    raise SystemExit(send_followup_main(sys.argv[2:]))

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == SELF_CHECK_ARG:
    raise SystemExit(main(self_check=True))

if __name__ == "__main__":
    raise SystemExit(main())
