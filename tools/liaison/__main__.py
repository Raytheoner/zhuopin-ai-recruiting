"""HR 值守通道服务的入口：`python -m tools.liaison`。

**两条线程，各管各的**（第 7 章）：
- **值守线程**独占 sqlite 连接与 `LiaisonSession`——库与状态机只有它一个人碰。
  它的循环是 `events.get(timeout=心跳间隔)`：有事件就处理，超时就盖存活戳。
- **主线程**只跑 `run_forever(connect)`。SDK 回调唯一做的事是把
  `(事件名, 当时的时间)` 放进队列，⛔ 回调里不碰库、不碰文件——sqlite 连接
  默认只能在创建它的线程里用，在断线回调里碰库会在最不该出错的那一刻抛异常。

⛔ 消息处理（归档=第 4 章、入队=第 5 章、群通知=第 6 章）不在本文件里。
tests/test_main_wiring.py::test_this_chapter_wires_no_message_handling 守着这条。

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
from datetime import datetime
from pathlib import Path

from tools.liaison import alerts, session, session_client
from tools.liaison import logsetup
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

#: tools/liaison/__main__.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 测试专用逃生口：让用例指一个不存在的 .env，免得开发机上真实的 .env 把
#: "凭据缺失"这条用例喂绿。⛔ 不写进 .env.example——那会把它暗示成生产用法。
DOTENV_PATH_ENV = "HR_LIAISON_DOTENV_PATH"

#: 存活戳的刷新间隔。取 15s——SDK 心跳是 30s、2 次无响判死（design D8），
#: 比它快一档，外部读存活戳时不至于把"正常心跳间隙"看成"停更"。
TICK_INTERVAL_SECONDS = 15.0

EVENT_CONNECTED = session_client.EVENT_CONNECTED
EVENT_DISCONNECTED = session_client.EVENT_DISCONNECTED


def resolve_dotenv_path() -> Path:
    override = os.environ.get(DOTENV_PATH_ENV)
    return Path(override) if override else REPO_ROOT / ".env"


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
    else:
        logger.error("收到未知的连接事件 %r（时间 %s），已忽略", name, moment)


def run_session_worker(
    svc: session.LiaisonSession,
    events: "queue.Queue",
    stop_event,
    *,
    tick_interval: float = TICK_INTERVAL_SECONDS,
    clock=now,
) -> None:
    """值守线程主体：处理事件，没事件就盖存活戳。

    ⚠️ 事件自带时间戳（回调那一刻取的），这里 ⛔ 不许用 `clock()` 覆盖它——
    否则中断窗口的起点会比真实断线时间晚最多一个心跳间隔。
    """
    while not stop_event.is_set():
        try:
            name, moment = events.get(timeout=tick_interval)
        except queue.Empty:
            svc.tick(clock())
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
) -> int:
    """⚠️ 三个关键字参数是**接线缝**，只给测试注入 fake 用，⛔ 不是配置项——
    ⛔ 不要给它们加环境变量开关。"""
    logsetup.setup_logging()
    load_dotenv_into_environ(resolve_dotenv_path())

    try:
        credentials = load_credentials()
    except MissingCredentialsError as exc:
        # 只打变量名，⛔ 不打取值。进程立刻退，⛔ 不进任何等待/重试循环。
        print(str(exc), file=sys.stderr)
        return EXIT_MISSING_CREDENTIALS

    try:
        client = client_builder(credentials)
    except ImportError as exc:
        print(
            f"HR 值守通道拒绝启动：aibot SDK 不可用（{exc}）。"
            "请在 tools/liaison/.venv 里装 tools/liaison/requirements.txt。"
            "⛔ 不要把它加进根 requirements.txt（design D10）。",
            file=sys.stderr,
        )
        return EXIT_SDK_UNAVAILABLE

    events: queue.Queue = queue.Queue()

    try:
        connect = session_client.make_sdk_connect(
            client,
            on_connected=lambda: events.put((EVENT_CONNECTED, now())),
            on_disconnected=lambda: events.put((EVENT_DISCONNECTED, now())),
        )
    except session_client.SdkSurfaceUnverifiedError as exc:
        print(f"HR 值守通道拒绝启动：{exc}", file=sys.stderr)
        return EXIT_SDK_SURFACE_UNVERIFIED

    stop_event = threading.Event()
    worker = threading.Thread(
        target=_session_thread_main,
        args=(events, stop_event, session_builder),
        name="liaison-session",
        daemon=True,
    )
    worker.start()

    try:
        runner(connect)
    except KeyboardInterrupt:
        stop_event.set()
        logger.warning("收到中断信号，值守通道停止接收")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
