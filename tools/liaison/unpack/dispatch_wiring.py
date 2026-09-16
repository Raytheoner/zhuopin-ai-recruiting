"""把 Task 4 的 `dispatch_headless_unpack` 接成 P0 `bridge.run_bridge` 的
`dispatch=` 注入点，并把结果转成三态审计（design D11、tasks.md 2.7）。

⚠️ **跨计划接口假设**（详见计划文件顶部「已知的跨计划接口假设」）：
1. `bridge.run_bridge(..., dispatch=bridge_dispatch)`——若 P0 合入后的真实调用
   约定与 `bridge_dispatch` 的签名不同，改这一个文件即可，其余 P1 模块不受影响。
2. `effects.effect_unpack_audit` 的签名。

# P2-TODO（`liaison-unpack-charter` 落地后由该变更包的执行者做）：
本文件目前自己拼「前言 + 章程全文」这个最简 prompt、自己解析章程相对路径，
是因为 P2 的 `unpack/charter.py`（`charter.read_charter` / `charter.compute_prompt`）
此刻还不存在。P2 落地后：
  - 删除本文件里的 `_read_charter_text` 与 `_build_minimal_prompt`；
  - 改成 `from tools.liaison.unpack import charter` 并调
    `charter.read_charter(repo_root)` / `charter.compute_prompt(...)`；
  - `charter_relpath`/`charter_root` 两个参数可能随之被
    `charter.CHARTER_RELATIVE_PATH` 取代，按 P2 的 design D13 实际落地情况调整。
本文件当前的实现是**完整可运行**的最简版本，不是留空占位。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from tools.liaison.storage import effects
from tools.liaison.unpack.dispatch import DispatchOutcome, dispatch_headless_unpack

logger = logging.getLogger("tools.liaison.unpack.dispatch_wiring")

#: 起活尝试的结果 → 审计 `kind`（design D11 的九个取值里，本模块只产出这三个）。
_OUTCOME_STATUS_TO_AUDIT_KIND = {
    "started": "dispatch_started",
    "skipped_busy": "dispatch_skipped_busy",
    "failed": "dispatch_failed",
}

#: 信号/锁/日志的默认落位（design D12）。⛔ 单点常量——`__main__.py`
#: 的 CLI 子命令（Task 6）与本模块共用同一份，防止两处路径字面量漂移。
DEFAULT_SIGNAL_ROOT = Path("data/liaison")
DEFAULT_LOG_DIR = DEFAULT_SIGNAL_ROOT / "logs" / "unpack-headless"
DEFAULT_LOCK_PATH = DEFAULT_SIGNAL_ROOT / "unpack-session.lock"


def _read_charter_text(charter_root: Path, charter_relpath: str) -> str | None:
    """# P2-TODO：本函数整体会被 `charter.read_charter` 取代，见模块 docstring。"""
    path = charter_root / charter_relpath
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _build_minimal_prompt(*, letter_number: str, msgid: str, charter_text: str) -> str:
    """# P2-TODO：本函数会被 `charter.compute_prompt` 取代，见模块 docstring。

    最简前言（编号/msgid）＋ 章程全文——满足 spec「章程全文逐字结尾、前有非空
    前言」的最低要求，⛔ 不含 D13 要求的完整字段集（检查点/信号相对路径等），
    那些字段需要与 P2 `unpack-signal --probe` 的循环规则一起设计，属 P2 范围。
    """
    preamble = f"你在处理信件 {letter_number}（msgid={msgid}）的拆件流程。以下是完整章程：\n"
    return preamble + charter_text


def bridge_dispatch(
    conn,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    letter_number: str,
    charter_relpath: str,
    charter_root: Path,
    now: datetime,
) -> DispatchOutcome:
    """P0 `bridge.run_bridge` 的 `dispatch=` 注入点。**结果三态一律落一条审计**，
    审计写失败本身吞掉只记日志（spec 明写），⛔ 不让审计失败掩盖起活本身的结果。
    """
    charter_text = _read_charter_text(charter_root, charter_relpath)
    prompt = (
        _build_minimal_prompt(letter_number=letter_number, msgid=msgid, charter_text=charter_text)
        if charter_text is not None
        else ""
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
