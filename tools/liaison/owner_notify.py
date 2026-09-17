"""本人通知发件箱（0917Y）：泳道批次收敛后经值守通道**私信 Shao Peishen 本人**一条摘要。

裁决：Shao Peishen 2026-09-17 答 1a——⛔ 不进群、⛔ 不发给任何其他人。本模块把这条
裁决做成结构：

1. **收件人不是参数。** `drain_owner_notify_outbox` / CLI 都没有"发给谁"这个入口；
   收件人由 `compute_owner_userid` 从名单（`config/whitelist.yaml`）里按
   `name == OWNER_NAME` 解析，恰好一条命中才发，0 条或多条 ⇒ fail-closed（不发、告警）。
   ⛔ 不硬编码 userid——名单是唯一真源，改名单不改 .py。
2. **发送只在值守线程、只经此刻正在 `run()` 的那个 SDK 连接对象。** 外部进程（CLI、
   run-lanes.sh）只往 `owner_notify_outbox` 写行；`SdkSendPort` 把 `client.send_message`
   协程用 `asyncio.run_coroutine_threadsafe` 投到主线程的事件循环上等回执。
   ⛔ 不在值守线程另起 loop、⛔ 不自建第二条连接。
3. **发送是 `effect_*` 幂等节点，键＝dedupe_key**（`effect_send_owner_notify`），
   与群通知同一顺序：**先发送、后落 `sent_at`**——记录说发了就一定发过；代价是
   "发出去但落库前进程被杀 ⇒ 下次可能重发一次"，重复看得见、丢失看不见。
4. 失败 `attempts + 1` 并记 `last_error`（`effect_record_owner_notify_failure`，
   键＝`{dedupe_key}:attempt:{n}`）；达到 `MAX_ATTEMPTS` ⇒ 记一次告警、停发（不再被
   `select_pending_owner_notify` 选中）。**连接未就绪不算一次失败**——否则一次 45 秒的
   断线就把三次机会烧光。

SDK 表面依据（`wecom-aibot-python-sdk==1.0.2`，主工作区 `tools/liaison/.venv` 只读核过）：
`aibot/client.py:286` `async def send_message(self, chatid, body) -> WsFrame`，docstring 逐字
「chatid: 会话 ID，**单聊填用户的 userid**，群聊填对应群聊的 chatid」；README 的 Markdown 示例
`{'msgtype': 'markdown', 'markdown': {'content': …}}`；回执 `errcode != 0` 由 `ws.py:511-521`
置成 `RuntimeError`，超时／断连同样以异常返回——`future.result()` 一处兜住。
单聊 chatid = userid 与 `frames.THREAD_ID_PATHS_BY_CHATTYPE["single"]`（入站单聊帧的
thread_id 取 `body.from.userid`，2026-09-16 真实帧实证）同口径。

⛔ 本模块不许出现 `conn.commit()` / `conn.rollback()` / `with X:`（第 2 章事务扫描器）。
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.storage.idempotency import idempotent_effect
from tools.liaison.alerts import AlertSink, effect_emit_alert
from tools.liaison.lane_digest import compute_lane_digest
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_enqueue_owner_notify
from tools.liaison.whitelist import load_whitelist_names

logger = logging.getLogger(__name__)

#: 名单里本人的 `name`。**只认这一个名字、恰好一条**——这是"只发给本人"在配置层的锚点。
#: ⛔ 不是 userid：userid 从名单里查，改名单不改代码。
OWNER_NAME = "邵培申"

#: 发件箱不挂在任何入站会话上，取固定哨兵值——与群通知 `__liaison_group_notify__`、
#: 连接窗口 `__liaison_connection__` 同一手法。恒等式按它分组。
OWNER_NOTIFY_THREAD_ID = "__liaison_owner_notify__"

#: 达到这个次数就停发、记告警。⛔ 不是"无限重试"：本人通知是提示不是交易，
#: 发不出去的正确处置是让人看日志，不是每 15 秒撞一次企微限流。
MAX_ATTEMPTS = 3

#: 等 SDK 回执的上限。SDK 自己对 reply ack 有超时（`ws.py` `_pending_acks`），这里
#: 取一个明显更长的数只防"永远不回"，⛔ 不许不带超时地 `future.result()`。
SEND_TIMEOUT_SECONDS = 30.0

#: 本模块依赖的 SDK 方法名。发送时核（不在启动期 `REQUIRED_CLIENT_ATTRS` 里加：
#: 那份清单只放"接不上就不该启动"的方法，本人通知发不出不该拦住整条值守通道）。
SDK_SEND_METHOD = "send_message"

EXIT_OK = 0
EXIT_BAD_ARGS = 2


class OwnerNotifySendError(RuntimeError):
    """一次发送没有拿到成功回执：连接未就绪、SDK 表面不符、回执 errcode≠0、超时。"""


class OwnerNotifyStateError(RuntimeError):
    """发件箱行状态与预期不符（要标已发的行不存在或已发）。"""


class SendPort(Protocol):
    """值守线程侧的发送口形状。生产用 `SdkSendPort`，单测用替身。"""

    def ready(self) -> bool: ...

    def send_markdown(self, chatid: str, content: str) -> None: ...


# ── 纯函数 ────────────────────────────────────────────────────────────────


def compute_owner_userid(names: Mapping[str, str]) -> str | None:
    """名单 userid→name 映射 ⇒ 本人的 userid。**恰好一条**命中才返回，否则 None。

    ⛔ 不猜：0 条是"名单里没有本人"（出厂态或被误删），>1 条是"两个 userid 都叫这个名"
    ——两种都发不得，交给调用方 fail-closed。纯函数：不读文件、不记日志。
    """
    matches = [userid for userid, name in names.items() if name == OWNER_NAME]
    if len(matches) != 1:
        return None
    return matches[0]


def compute_markdown_body(content: str) -> dict:
    """SDK `send_message` 的 body（README 示例逐字形状）。"""
    return {"msgtype": "markdown", "markdown": {"content": content}}


def _now_text() -> str:
    """与 SQLite `datetime('now')` 同口径：UTC、秒级、无时区后缀（同 notify/store）。"""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── effect 节点 ───────────────────────────────────────────────────────────


@idempotent_effect("effect_send_owner_notify")
def effect_send_owner_notify(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    chatid: str,
    body: str,
    send_port: SendPort,
) -> str:
    """发一条并标 `sent_at`。`business_key` 即 `dedupe_key`。**先发送、后落行**。

    发送抛错 ⇒ 装饰器回滚并原样上抛 ⇒ **不留幂等记录**，下一轮还能再试；
    调用方负责随后记一次失败（`effect_record_owner_notify_failure`）。
    ⛔ 不登记进 `EFFECT_NODE_TO_TABLE`：它是 UPDATE，不产生新行。
    """
    send_port.send_markdown(chatid, body)
    cursor = conn.execute(
        "UPDATE owner_notify_outbox SET sent_at = ? WHERE dedupe_key = ? AND sent_at IS NULL",
        (_now_text(), business_key),
    )
    if cursor.rowcount != 1:
        raise OwnerNotifyStateError(f"要标已发的发件箱行不存在或已发：dedupe_key={business_key}")
    return business_key


@idempotent_effect("effect_record_owner_notify_failure")
def effect_record_owner_notify_failure(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    error: str,
) -> str:
    """记一次发送失败：`attempts + 1`、`last_error`。`business_key` 必须是
    `f"{dedupe_key}:attempt:{第几次}"`——同一行的每次失败各占一个幂等键。
    ⛔ 不登记进 `EFFECT_NODE_TO_TABLE`：它是 UPDATE。
    """
    dedupe_key = business_key.rsplit(":attempt:", 1)[0]
    cursor = conn.execute(
        "UPDATE owner_notify_outbox SET attempts = attempts + 1, last_error = ? "
        "WHERE dedupe_key = ? AND sent_at IS NULL",
        (error[:500], dedupe_key),
    )
    if cursor.rowcount != 1:
        raise OwnerNotifyStateError(f"要记失败的发件箱行不存在或已发：dedupe_key={dedupe_key}")
    return business_key


def select_pending_owner_notify(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """未发、且还有机会的行：(dedupe_key, body, attempts)，按入队顺序。"""
    return [
        (row[0], row[1], row[2])
        for row in conn.execute(
            "SELECT dedupe_key, body, attempts FROM owner_notify_outbox "
            "WHERE sent_at IS NULL AND attempts < ? ORDER BY id",
            (MAX_ATTEMPTS,),
        ).fetchall()
    ]


# ── 消费者 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DrainReport:
    sent: int = 0
    failed: int = 0
    parked: int = 0
    skipped_not_ready: bool = False
    owner_unresolved: bool = False


def drain_owner_notify_outbox(
    conn: sqlite3.Connection,
    *,
    send_port: SendPort,
    whitelist_path: Path | None,
    alert_sink: AlertSink,
    alert_owner_unresolved: bool = True,
) -> DrainReport:
    """取未发的行 ⇒ 发给名单里的本人 ⇒ 标 `sent_at`；失败计次、满次停发。

    **没有收件人参数。** 名单每次重读（与 `whitelist.load_whitelist` 同一纪律：进程内
    不缓存）。顺序刻意：先查有没有待发再解析本人——发件箱空时不读名单、不告警。
    """
    pending = select_pending_owner_notify(conn)
    if not pending:
        return DrainReport()
    if not send_port.ready():
        return DrainReport(skipped_not_ready=True)

    owner = compute_owner_userid(load_whitelist_names(whitelist_path))
    if owner is None:
        if alert_owner_unresolved:
            effect_emit_alert(
                alert_sink,
                f"本人通知发不出：准入名单里 name={OWNER_NAME!r} 不是恰好一条，"
                f"fail-closed 不发（待发 {len(pending)} 条原地留着）。请核 config/whitelist.yaml",
            )
        return DrainReport(owner_unresolved=True)

    sent = failed = parked = 0
    for dedupe_key, body, attempts in pending:
        try:
            outcome = effect_send_owner_notify(
                conn,
                thread_id=OWNER_NOTIFY_THREAD_ID,
                business_key=dedupe_key,
                chatid=owner,
                body=body,
                send_port=send_port,
            )
        except Exception as exc:  # noqa: BLE001 —— 任何一次发送失败都只记账，不打死值守线程
            failed += 1
            attempt_no = attempts + 1
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "本人通知发送失败（第 %d/%d 次）：dedupe_key=%s %s",
                attempt_no, MAX_ATTEMPTS, dedupe_key, error,
            )
            effect_record_owner_notify_failure(
                conn,
                thread_id=OWNER_NOTIFY_THREAD_ID,
                business_key=f"{dedupe_key}:attempt:{attempt_no}",
                error=error,
            )
            if attempt_no >= MAX_ATTEMPTS:
                parked += 1
                effect_emit_alert(
                    alert_sink,
                    f"本人通知已停发：dedupe_key={dedupe_key} 连续 {attempt_no} 次失败，"
                    f"最后一次：{error}。正文仍在 owner_notify_outbox，需人工查",
                )
            continue
        if outcome is None:
            # 幂等命中却仍在待发集合里：effect_log 与 sent_at 同一事务提交，正常到不了这里；
            # 到了就是库被人手改过。⛔ 不重发、⛔ 不假装已发，只记一行让人看。
            logger.error(
                "本人通知幂等键已存在但行未标已发（库被手改？）：dedupe_key=%s", dedupe_key
            )
            continue
        sent += 1
        logger.info("本人通知已发：dedupe_key=%s", dedupe_key)
    return DrainReport(sent=sent, failed=failed, parked=parked)


class OwnerNotifyConsumer:
    """值守线程每个空闲 tick 调一次的消费者。唯一的状态是"名单里没本人这条告警
    记过没"——避免每 15 秒刷一条同样的告警；名单一修好就自动复位。"""

    def __init__(self, *, send_port: SendPort, whitelist_path: Path | None = None) -> None:
        self._send_port = send_port
        self._whitelist_path = whitelist_path
        self._owner_unresolved_alerted = False

    def drain(self, conn: sqlite3.Connection, *, alert_sink: AlertSink) -> DrainReport:
        report = drain_owner_notify_outbox(
            conn,
            send_port=self._send_port,
            whitelist_path=self._whitelist_path,
            alert_sink=alert_sink,
            alert_owner_unresolved=not self._owner_unresolved_alerted,
        )
        self._owner_unresolved_alerted = report.owner_unresolved
        return report


# ── SDK 发送口 ────────────────────────────────────────────────────────────


class SdkSendPort:
    """把 `client.send_message(chatid, body)` 协程投到主线程的事件循环上并等回执。

    两个把手都来自 `__main__.main()`：`ClientHolder`（`make_sdk_connect` 每轮 `run()`
    前后 remember/forget）与 `LoopStopper`（SDK 回调里 capture 的 loop）。缺任一个 ⇒
    `ready()` 为 False ⇒ 消费者本轮不动（不计失败）。
    """

    def __init__(
        self,
        client_holder,
        loop_stopper,
        *,
        timeout: float = SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._client_holder = client_holder
        self._loop_stopper = loop_stopper
        self._timeout = timeout

    def _handles(self):
        client = self._client_holder.current()
        loop = self._loop_stopper.current_loop()
        if client is None or loop is None or loop.is_closed():
            return None, None
        return client, loop

    def ready(self) -> bool:
        client, loop = self._handles()
        return client is not None and loop is not None

    def send_markdown(self, chatid: str, content: str) -> None:
        client, loop = self._handles()
        if client is None or loop is None:
            raise OwnerNotifySendError("SDK 连接未就绪（没有正在 run() 的连接对象或事件循环）")
        send = getattr(client, SDK_SEND_METHOD, None)
        if not callable(send):
            raise OwnerNotifySendError(
                f"SDK 连接对象没有 {SDK_SEND_METHOD}()，本人通知 fail-closed 不发。"
                "请按 aibot 当前版本重新核对表面，⛔ 不要绕过本检查"
            )
        awaitable = send(chatid, compute_markdown_body(content))
        if not inspect.isawaitable(awaitable):
            raise OwnerNotifySendError(
                f"SDK 的 {SDK_SEND_METHOD}() 不是协程（返回 {type(awaitable).__name__}），"
                "拿不到回执，fail-closed 不当作已发"
            )
        future = asyncio.run_coroutine_threadsafe(awaitable, loop)
        try:
            future.result(timeout=self._timeout)
        except TimeoutError:
            future.cancel()
            raise OwnerNotifySendError(f"等 SDK 回执超时（{self._timeout} 秒）") from None
        except Exception as exc:
            raise OwnerNotifySendError(f"{type(exc).__name__}: {exc}") from None


# ── 入队 CLI ──────────────────────────────────────────────────────────────

_USAGE = (
    "python -m tools.liaison owner-notify --dedupe-key K "
    "(--body-file F | --lane-logdir DIR)"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison owner-notify",
        description="把一条摘要放进本人通知发件箱（只写库，由值守服务私信本人）。⛔ 没有收件人参数。",
    )
    parser.add_argument("--dedupe-key", required=True, help="幂等键；同键再入队即忽略")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--body-file", help="正文文件（Markdown），原样入队")
    source.add_argument(
        "--lane-logdir",
        help="run-lanes.sh 的 LOGDIR；正文由 results.tsv + summary.txt 生成（compute_lane_digest）",
    )
    return parser


def _read_body(args) -> str | None:
    """取正文；取不到／为空 ⇒ None（调用方报 EXIT_BAD_ARGS）。"""
    if args.body_file is not None:
        path = Path(args.body_file)
        if not path.is_file():
            print(f"正文文件不存在：{path}", file=sys.stderr)
            return None
        body = path.read_text(encoding="utf-8")
    else:
        logdir = Path(args.lane_logdir)
        results = logdir / "results.tsv"
        if not results.is_file():
            print(f"日志目录里没有 results.tsv：{logdir}", file=sys.stderr)
            return None
        # 2026-09-17 Shao Peishen：泳道批次「全部 OK」不私信，只在有 PARTIAL/FAIL 等异常时提醒。
        rows = [l.split("\t") for l in results.read_text(encoding="utf-8").splitlines() if l.strip()]
        if rows and all(len(r) > 2 and r[2].strip() == "OK" for r in rows):
            print("批次全部 OK，按口径不私信", file=sys.stderr)
            return None
        summary = logdir / "summary.txt"
        body = compute_lane_digest(
            results.read_text(encoding="utf-8"),
            summary.read_text(encoding="utf-8") if summary.is_file() else "",
            batch_label=logdir.name,
        )
    if not body.strip():
        print("正文为空，不入队", file=sys.stderr)
        return None
    return body


def owner_notify_main(argv: Sequence[str]) -> int:
    """`python -m tools.liaison owner-notify` 的实现。纯写库：⛔ 不建连接、不发消息。

    `argv` 没有默认值（与 `retention.cleanup_main` 同一理由）。库路径取
    `liaison_db.DEFAULT_DB_PATH` 的属性访问，单测 monkeypatch 到 tmp_path。
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else EXIT_BAD_ARGS
    body = _read_body(args)
    if body is None:
        return EXIT_BAD_ARGS

    conn = liaison_db.get_connection()
    try:
        liaison_db.init_schema(conn)
        result = effect_enqueue_owner_notify(
            conn,
            thread_id=OWNER_NOTIFY_THREAD_ID,
            business_key=args.dedupe_key,
            body=body,
        )
    finally:
        conn.close()
    if result is None:
        print(f"已存在（幂等忽略）：dedupe_key={args.dedupe_key}")
    else:
        print(f"已入队：dedupe_key={args.dedupe_key}，{len(body)} 字；值守服务连着时私信本人")
    return EXIT_OK
