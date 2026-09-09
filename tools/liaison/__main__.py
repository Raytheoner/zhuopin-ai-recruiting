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
    self_check: bool = False,
) -> int:
    """⚠️ 三个接线缝关键字参数只给测试注入 fake 用，⛔ 不是配置项——
    ⛔ 不要给它们加环境变量开关。

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

    try:
        # ⚠️ 传的是**工厂**不是对象：SDK 的 `_started` 闩锁让同一个连接对象没法
        # 重连第二次（详见 session_client.make_sdk_connect 的 docstring）。
        connect = session_client.make_sdk_connect(
            lambda: client_builder(credentials),
            on_connected=lambda: events.put((EVENT_CONNECTED, now())),
            on_disconnected=lambda: events.put((EVENT_DISCONNECTED, now())),
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

    try:
        runner(connect)
    except KeyboardInterrupt:
        stop_event.set()
        logger.warning("收到中断信号，值守通道停止接收")
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
