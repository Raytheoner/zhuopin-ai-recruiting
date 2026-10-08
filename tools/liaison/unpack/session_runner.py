"""拆件会话 wrapper（1001I）：起引擎、原样转发 stdin、等退出、给本人一条私信。

`dispatch_headless_unpack` 起的**不再是引擎二进制本身**，而是本模块：

    <python> -m tools.liaison.unpack.session_runner \
        --msgid <M> --log-path <L> -- <引擎 argv 逐字>

本模块只做三件事，按顺序：

1. `Popen(<引擎 argv>)` 并**继承** stdin/stdout/stderr——prompt 由 dispatch 写进
   本进程的 stdin 后关掉，原样（同一个 fd，不经过本模块的读写）到达引擎；引擎的
   输出照旧落进 dispatch 已经开好的日志文件。⛔ 本模块不缓冲、不改写、不读 prompt。
2. `wait()` 拿退出码，量一次耗时。
3. 往本人通知发件箱写一行（`dedupe_key = "unpack-session-exit:{msgid}"`），正文只含
   msgid／退出码／日志路径／耗时／时刻；写库失败只记日志、不上抛。

⛔ **只做"等待＋通知"**：不读会话输出内容、不判定拆件结论（结论仍按章程落 `docs/`）。
⛔ **不是拆件会话本身**：本模块跑在值守侧（它与值守服务同一个仓库根、同一条通道，
自己开一条库连接、用完就关），拆件会话的权限一个字节都不加——起活失败／会话结束
两条通知都由值守侧进程写，正是这条纪律。

由来（1001I）：09-24 那次起活死于「订阅被禁」，**没有任何人被告知**，信号挂在
`unpack-signal.json` 里直到 10-08 才被人工发现。会话结束通知就是堵这个洞的——
谁起、起没起来、退了没有、退在几号，一律先经值守通道私信本人，零轮询。
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.liaison import owner_notify
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_enqueue_owner_notify

logger = logging.getLogger("tools.liaison.unpack.session_runner")

# tools/liaison/unpack/session_runner.py → parents[0]=unpack, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 会话结束本人通知的幂等键前缀（1001I）。完整键＝`{前缀}:{msgid}`。
#: ⛔ 只在这里写死一次——别处（含测试）一律 import 这个常量。
SESSION_EXIT_DEDUPE_PREFIX = "unpack-session-exit"

#: 连引擎进程都没能创建时，通知正文里记的哨兵"退出码"——此刻没有真实退出码可取。
#: ⛔ 不是 POSIX 退出码，只为让正文那一行不空着；真实情况写在「备注」里。
SPAWN_FAILED_EXIT_CODE = -1

#: 进程创建失败时本进程返回给外层的退出码（非零即异常）。
SPAWN_FAILED_RC = 1


def build_session_exit_notice(
    *,
    msgid: str,
    exit_code: int,
    log_path: str | Path,
    elapsed_seconds: float,
    now: datetime,
    note: str | None = None,
) -> tuple[str, str]:
    """会话结束 ⇒ `(dedupe_key, 正文)`。**纯函数**，⛔ 不读环境、不写库。

    正文**只由这几个入参拼出**：msgid、退出码、日志路径、耗时、时刻（＋可选备注）。
    ⛔ 不含任何归档件内容或候选人个人信息。
    """
    dedupe_key = f"{SESSION_EXIT_DEDUPE_PREFIX}:{msgid}"
    lines = [
        "【HR·拆件会话结束】",
        f"- 消息标识（msgid）：{msgid}",
        f"- 退出码：{exit_code}",
        f"- 耗时：{elapsed_seconds:.1f} 秒",
        f"- 日志路径：{log_path}",
        f"- 时刻：{now.isoformat()}",
    ]
    if note:
        lines.append(f"- 备注：{note}")
    return dedupe_key, "\n".join(lines)


def enqueue_owner_notify(
    *,
    dedupe_key: str,
    body: str,
    db_path: str | Path | None = None,
) -> bool:
    """往本人通知发件箱写一行（只入队、不发送——发送是值守线程按既有通道的事）。

    ⛔ **任何失败只记日志、不上抛**：一条发不出去的提示不配有让起活链路变形的
    破坏力（与起活审计同一基调）。返回是否真的新入队了一条（幂等命中 ⇒ False）。

    ⛔ 收件人不是参数、也不在这里解析——`owner_notify` 的消费者按名单里
    `OWNER_NAME` 那一条发，本函数只负责把行放进发件箱。
    """
    try:
        conn = liaison_db.get_connection(db_path)
        try:
            liaison_db.init_schema(conn)
            result = effect_enqueue_owner_notify(
                conn,
                thread_id=owner_notify.OWNER_NOTIFY_THREAD_ID,
                business_key=dedupe_key,
                body=body,
            )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 —— 见 docstring
        logger.error(
            "本人通知入队失败（dedupe_key=%s），只记日志、不上抛", dedupe_key, exc_info=True
        )
        return False
    return result is not None


def run_session(
    engine_argv: Sequence[str],
    *,
    msgid: str,
    log_path: str | Path,
    popen: Callable[..., Any] = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] | None = None,
    cwd: Path = REPO_ROOT,
    db_path: str | Path | None = None,
) -> int:
    """起引擎 → 等退出 → 通知。返回**引擎的退出码**（进程没起得来时返回
    `SPAWN_FAILED_RC`）。⛔ 永不因为通知写不进去而改变返回值/上抛。

    `popen`/`monotonic`/`now`/`db_path` 是测试注入缝（生产走默认值）。stdin 与
    stdout/stderr **全部继承**：prompt 原样到达引擎，引擎输出原样落进日志文件。
    """
    started = monotonic()
    note: str | None = None
    try:
        process = popen(list(engine_argv), cwd=str(cwd))
    except Exception as exc:  # noqa: BLE001 —— 引擎起不来也是一种"会话结束"，照常通知
        exit_code = SPAWN_FAILED_EXIT_CODE
        rc = SPAWN_FAILED_RC
        note = f"引擎进程未能创建：{type(exc).__name__}"
        logger.error("会话 wrapper 起不了引擎进程：%s", exc, exc_info=True)
    else:
        exit_code = int(process.wait())
        rc = exit_code

    elapsed_seconds = monotonic() - started
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    dedupe_key, body = build_session_exit_notice(
        msgid=msgid,
        exit_code=exit_code,
        log_path=log_path,
        elapsed_seconds=elapsed_seconds,
        now=moment,
        note=note,
    )
    try:
        # `enqueue_owner_notify` 自己已经吞掉一切异常；这里再包一层是**双保险**——
        # 通知链路的任何变化都不该让本进程的退出码跟着变（它是引擎退出码的唯一出处）。
        enqueue_owner_notify(dedupe_key=dedupe_key, body=body, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 见注释
        logger.error("本人通知入队意外失败（dedupe_key=%s）", dedupe_key, exc_info=True)
    return rc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison.unpack.session_runner",
        description=(
            "拆件会话 wrapper：起引擎、转发 stdin、等退出，再给本人一条会话结束私信。"
        ),
    )
    parser.add_argument("--msgid", required=True, help="本次触发的消息标识（通知的幂等键分量）")
    parser.add_argument("--log-path", required=True, help="dispatch 开好的本次日志文件路径")
    # `--` 之后的引擎 argv 原样透传，⛔ 本模块不解释、不重排、不补默认值。
    parser.add_argument("engine_argv", nargs=argparse.REMAINDER)
    return parser


def main(
    argv: Sequence[str],
    *,
    popen: Callable[..., Any] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], datetime] | None = None,
    cwd: Path = REPO_ROOT,
    db_path: str | Path | None = None,
) -> int:
    """`python -m tools.liaison.unpack.session_runner …` 的实现。"""
    args = _build_parser().parse_args(list(argv))
    engine_argv = list(args.engine_argv)
    if engine_argv and engine_argv[0] == "--":
        # argparse 的 REMAINDER 会把 `--` 自己也收进来（3.9 与 3.14 实测都是），剔掉它。
        engine_argv = engine_argv[1:]
    if not engine_argv:
        print("⛔ 没给引擎 argv（`--` 之后要逐字带上引擎命令行）", file=sys.stderr)
        return SPAWN_FAILED_RC
    kwargs: dict[str, Any] = {"cwd": cwd, "db_path": db_path}
    if popen is not None:
        kwargs["popen"] = popen
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if now is not None:
        kwargs["now"] = now
    return run_session(engine_argv, msgid=args.msgid, log_path=args.log_path, **kwargs)


if __name__ == "__main__":  # pragma: no cover —— 真实入口，测试直接调 main()/run_session()
    raise SystemExit(main(sys.argv[1:]))
