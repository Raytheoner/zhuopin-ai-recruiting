"""把 Task 4 的 `dispatch_headless_unpack` 接成 P0 `bridge.run_bridge` 的
`dispatch=` 注入点，并把结果转成三态审计（design D11、tasks.md 2.7）。

⚠️ **跨计划接口假设**（详见计划文件顶部「已知的跨计划接口假设」）：
1. `bridge.run_bridge(..., dispatch=bridge_dispatch)`——若 P0 合入后的真实调用
   约定与 `bridge_dispatch` 的签名不同，改这一个文件即可，其余 P1 模块不受影响。
2. `effects.effect_unpack_audit` 的签名。

章程正文与起活 prompt 由 `unpack/charter.py`（P2，design D13）唯一负责——本模块
只负责把 `charter.read_charter` / `charter.compute_prompt` 需要的各个字段
（`repo_root`、信号文件相对路径、检查点时刻、信件编号）拼出来。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from tools.liaison import session
from tools.liaison.storage import effects
from tools.liaison.unpack import charter, unpack_cli
from tools.liaison.unpack.dispatch import DispatchOutcome, dispatch_headless_unpack
from tools.liaison.unpack.signal import find_pending

logger = logging.getLogger("tools.liaison.unpack.dispatch_wiring")

#: 起活尝试的结果 → 审计 `kind`（design D11 的九个取值里，本模块只产出这三个）。
_OUTCOME_STATUS_TO_AUDIT_KIND = {
    "started": "dispatch_started",
    "skipped_busy": "dispatch_skipped_busy",
    "failed": "dispatch_failed",
}

#: I3（2026-09-16 修）：`tools/liaison/unpack/dispatch_wiring.py` →
#: parents[0]=unpack, [1]=liaison, [2]=tools, [3]=仓库根——与 `dispatch.py` 的
#: `REPO_ROOT` 同一口径、同一深度。⛔ **必须 REPO_ROOT 锚定，不能是裸相对路径**：
#: 部署约束是 Windows 计划任务（SYSTEM 账户 + AtStartup），守护进程的 cwd 不保证
#: 是仓库根，裸 `Path("data/liaison")` 会静默落到别处且没有任何报错。
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 信号/锁/日志的默认落位（design D12）。⛔ 单点常量——`__main__.py`
#: 的 CLI 子命令（Task 6）与本模块共用同一份，防止两处路径字面量漂移。
DEFAULT_SIGNAL_ROOT = REPO_ROOT / "data" / "liaison"
DEFAULT_LOG_DIR = DEFAULT_SIGNAL_ROOT / "logs" / "unpack-headless"
DEFAULT_LOCK_PATH = DEFAULT_SIGNAL_ROOT / "unpack-session.lock"


def _resolve_letter_number_for_prompt(
    signal_path: Path, *, letter_number: str | None, msgid: str
) -> str:
    """`letter_number` 入参为 `None`（P0 零参 `dispatch()` 契约拿不到，见
    `__main__.py` 的绑定处）时，从信号文件里按 `msgid` 补查——`bridge.
    _emit_signal_and_dispatch` 在调 `dispatch()` 之前已经 `append_signal` 落了
    这一项。查不到（信号文件缺失/损坏/确实没有这条）⇒ 写占位「（未匹配）」，
    ⛔ 不抛异常、⛔ 不阻断起活。
    """
    if letter_number:
        return letter_number
    entry = find_pending(signal_path, msgid)
    resolved = entry.get("letter_number") if entry else None
    return resolved or "（未匹配）"


def _resolve_signal_relpath(signal_path: Path, repo_root: Path) -> str:
    try:
        return str(signal_path.relative_to(repo_root))
    except ValueError:
        return str(signal_path)


def _prior_launch_already_recorded(conn, *, thread_id: str, msgid: str) -> bool:
    """I2（2026-09-16 修）：redelivery 守卫，探测「这条消息是不是已经真的起过一次
    拆件会话」——⛔ 不能靠 `effect_unpack_audit` 自身的幂等短路，那个短路发生在
    `dispatch_headless_unpack`（会花钱、会起进程）**之后**，挡得住"审计重复写"，
    挡不住"再起一个真实进程"（企微对同一 msgid 的重投，或值守服务重启后重放）。

    探测键与 `idempotent_effect` 装饰器（`app/storage/idempotency.py`）拼的幂等键
    同一口径：`f"{thread_id}:{node_name}:{business_key}"`，这里
    `node_name="effect_unpack_audit"`、`business_key=f"{msgid}:dispatch_started"`
    （`effects.py::effect_unpack_audit` docstring 逐字给出的拼法）。

    只探测 `dispatch_started` 这一种 kind——`skipped_busy`/`dispatch_failed` 都
    没有真的花钱起活，允许下一次重试；只有已经真launch过一次才需要拦下来。
    """
    effect_key = f"{thread_id}:effect_unpack_audit:{msgid}:dispatch_started"
    row = conn.execute(
        "SELECT 1 FROM effect_log WHERE effect_key = ?", (effect_key,)
    ).fetchone()
    return row is not None


def bridge_dispatch(
    conn,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    letter_number: str | None,
    now: datetime,
    repo_root: Path = REPO_ROOT,
    _clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> DispatchOutcome:
    """P0 `bridge.run_bridge` 的 `dispatch=` 注入点。**结果三态一律落一条审计**，
    审计写失败本身吞掉只记日志（spec 明写），⛔ 不让审计失败掩盖起活本身的结果。

    `_clock`（95b4d15 记录的检查点边界坑修复）：前言里「检查点时刻」⛔ 不能沿用
    `now`——`now` 与触发本轮的这一项信号自身的 `at` 同源（`bridge.py::
    _emit_signal_and_dispatch` 用同一个 `now` 落 `at`），即便格式对齐，
    `signal.clear_signal_before` 的 `at >= checkpoint` 保留规则也会把这一项原地
    锁死、永远清不掉。本函数运行在 `append_signal` 已经落盘之后（`bridge.py` 先
    落信号后调 `dispatch()`），所以取一次此刻的真实时钟就足以保证严格晚于
    `at`。生产默认真实时钟，测试注入固定值以保证可复现。
    """
    if _prior_launch_already_recorded(conn, thread_id=thread_id, msgid=msgid):
        logger.warning(
            "msgid=%s 已经起过一次拆件会话（dispatch_started 审计已存在），"
            "本次判定为重投/重放，跳过再起一个真实进程",
            msgid,
        )
        return DispatchOutcome(status="started", reason="idempotent_replay_skipped_relaunch")

    try:
        charter_text = charter.read_charter(repo_root)
    except charter.CharterMissing:
        charter_text = None

    if charter_text is None:
        prompt = ""
    else:
        signal_path = unpack_cli._resolve_signal_path()
        prompt = charter.compute_prompt(
            letter_number=_resolve_letter_number_for_prompt(
                signal_path, letter_number=letter_number, msgid=msgid
            ),
            msgid=msgid,
            signal_relpath=_resolve_signal_relpath(signal_path, repo_root),
            checkpoint_iso=session.format_instant(_clock()),
            charter_text=charter_text,
        )
    outcome = dispatch_headless_unpack(
        charter_text=charter_text,
        prompt=prompt,
        log_dir=DEFAULT_LOG_DIR,
        lock_path=DEFAULT_LOCK_PATH,
        env=os.environ,
        now=now,
    )

    kind = _OUTCOME_STATUS_TO_AUDIT_KIND[outcome.status]
    detail = outcome.reason or outcome.status
    try:
        effects.effect_unpack_audit(
            conn,
            thread_id=thread_id,
            business_key=f"{msgid}:{kind}",
            sender_userid=sender_userid,
            letter_number=letter_number,
            kind=kind,
            detail=detail,
        )
    except Exception:
        logger.error(
            "起活审计写入失败（msgid=%s kind=%s），起活本身的结果不受影响",
            msgid, kind, exc_info=True,
        )

    return outcome
