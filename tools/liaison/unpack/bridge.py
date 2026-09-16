"""P0·回件桥＋第九态（design.md D1/D2/D3/D9/D10）。

**本模块分两层，⛔ 不许混在一起**（与 archive.py 同一纪律）：
- `compute_*` 是纯函数（工程铁律 2）：不读文件、不读时钟、不记日志。
- `run_bridge` 是编排点，在值守线程里被 `__main__.py::handle_message_frame`
  调用，做台账写、effect 写、信号/起活注入——全部包在 `try/except` 里，
  任何失败都不上抛（design D10）。
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from dataclasses import dataclass
from datetime import datetime

#: 第九态的语义标记：回件已到、待人工/会话拆件、仍在途、串行闸仍锁。
#: ⛔ 这段文字本身就是被 spec Scenario 逐字断言的契约，改动前先读
#: spec.md「第九态改写只动命中行且原状态原样接后」。
NINTH_STATE_MARKER = "📨 回件已到，待拆件"

#: 台账既有约定：`✅ 已推送` 起头的状态视为在途（followup.py::_ALREADY_SENT
#: 是同一枚举值的另一份拷贝，两边各自独立维护——那边管"是否已推送完成"，
#: 这边管"是否处于回件桥意义上的在途"，语义不同，⛔ 不合并成一个常量）。
ALREADY_PUSHED_PREFIX = "✅ 已推送"


def compute_ninth_state_cell(
    original_cell_text: str, *, archived_relpath: str, now_cst: datetime
) -> str:
    """把"发送状态"列的原文改写成第九态文案（design D9 逐字模板）。

    纯函数：不读时钟（`now_cst` 由调用方传入，工程铁律 2）、不读文件。

    ⚠️ `original_cell_text` 必须是**该单元格的完整原文**（调用方从 markdown
    表格行的 `split("|")` 结果里原样取出、不做任何清洗），因为 spec 要求
    "原状态列的完整原文"逐字接在分隔符之后——本函数不对它做 strip 之外的
    任何改写，`.strip()` 只是去掉表格单元格惯用的首尾空白（followup.py
    写单元格时也是 `f" {value} "` 这种前后各一个空格的padding，⛔ 不去掉
    这一层会让新文案两侧多出不对称的空白）。
    """
    time_text = now_cst.strftime("%Y-%m-%d %H:%M") + " CST"
    return (
        f"{NINTH_STATE_MARKER} {time_text}"
        f"（值守服务自动标记，入信归档 `{archived_relpath}`；"
        f"仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一）"
        f" ━━━ 原状态 ━━━ {original_cell_text.strip()}"
    )


OUTCOME_MARKED = "marked"
OUTCOME_SKIPPED_NO_INFLIGHT = "skipped_no_inflight"
OUTCOME_REFUSED_SERIAL_VIOLATION = "refused_serial_violation"
OUTCOME_SKIPPED_ALREADY_MARKED = "skipped_already_marked"


@dataclass(frozen=True)
class BridgeDecision:
    """`compute_bridge_decision` 的判定结果。纯数据。"""

    outcome: str
    new_ledger_text: str | None
    matched_letter_numbers: tuple[str, ...]


def _strip_outer_backtick(cell: str) -> str:
    """去掉状态列惯用的单层反引号包裹，只为**判定**用，⛔ 不用于改写输出
    （改写时 `compute_ninth_state_cell` 保留完整原文，见该函数 docstring）。
    """
    text = cell.strip()
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        return text[1:-1].strip()
    return text


def _is_inflight_status(status_cell: str) -> bool:
    text = _strip_outer_backtick(status_cell)
    return text.startswith(ALREADY_PUSHED_PREFIX) or NINTH_STATE_MARKER in text


def _is_already_marked_status(status_cell: str) -> bool:
    return NINTH_STATE_MARKER in _strip_outer_backtick(status_cell)


def _extract_letter_number(number_cell: str) -> str | None:
    text = number_cell.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return text[1:-1].strip()
    return text or None


def compute_bridge_decision(
    ledger_text: str,
    *,
    sender_name: str,
    archived_relpath: str,
    now_cst: datetime,
) -> BridgeDecision:
    """按 spec「名单内入站按串行原则定位在途信」判出四态之一。

    纯函数：不读文件、不读时钟（`now_cst` 由调用方传入）、不记日志（工程铁律 2）。

    判定顺序（spec 逐字）：
    1. 按 `sender_name` 匹配"收信人"列，筛出"发送状态"以 `✅ 已推送` 起头
       或已含第九态标记的行（在途行）。
    2. 0 行 ⇒ `skipped_no_inflight`。
    3. ≥2 行 ⇒ `refused_serial_violation`，`matched_letter_numbers` 列出全部命中编号。
    4. 恰 1 行且已是第九态 ⇒ `skipped_already_marked`（幂等短路，同一消息重投
       或第九态期间又来一条新消息都会落进这一支——两者在"台账层面"看起来
       完全一样，`run_bridge` 用审计表的幂等键区分"是否要重复起活"）。
    5. 恰 1 行且未是第九态 ⇒ `marked`，改写该行"发送状态"列为第九态文案，
       台账其它行逐字节不变。
    """
    lines = ledger_text.splitlines(keepends=True)
    inflight_indices: list[int] = []
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 6:
            continue
        recipient = cells[3].strip()
        if recipient != sender_name:
            continue
        if _is_inflight_status(cells[-2]):
            inflight_indices.append(index)

    if not inflight_indices:
        return BridgeDecision(
            outcome=OUTCOME_SKIPPED_NO_INFLIGHT, new_ledger_text=None, matched_letter_numbers=()
        )

    if len(inflight_indices) > 1:
        numbers = tuple(
            number
            for index in inflight_indices
            if (number := _extract_letter_number(lines[index].split("|")[1])) is not None
        )
        return BridgeDecision(
            outcome=OUTCOME_REFUSED_SERIAL_VIOLATION,
            new_ledger_text=None,
            matched_letter_numbers=numbers,
        )

    hit_index = inflight_indices[0]
    hit_cells = lines[hit_index].split("|")
    letter_number = _extract_letter_number(hit_cells[1])
    matched = (letter_number,) if letter_number is not None else ()

    if _is_already_marked_status(hit_cells[-2]):
        return BridgeDecision(
            outcome=OUTCOME_SKIPPED_ALREADY_MARKED,
            new_ledger_text=None,
            matched_letter_numbers=matched,
        )

    hit_cells[-2] = (
        f" {compute_ninth_state_cell(hit_cells[-2], archived_relpath=archived_relpath, now_cst=now_cst)} "
    )
    lines[hit_index] = "|".join(hit_cells)
    return BridgeDecision(
        outcome=OUTCOME_MARKED,
        new_ledger_text="".join(lines),
        matched_letter_numbers=matched,
    )


def write_ledger_atomic(path: pathlib.Path, text: str) -> None:
    """临时文件 ＋ `os.replace` 写台账（design D10 逐字）。⛔ 不用 `with`
    （第 2 章事务扫描器会把 `with <名字>:` 判为隐式提交违规——虽然本函数不碰
    数据库，但扫描器按语法结构扫，不区分"这段是不是真的在碰事务"）。

    写失败（目录不存在、权限不足、磁盘满）原样向上抛，⛔ 不在这里吞——
    `run_bridge` 接住它转 `bridge_failed` 审计，见 design D10「台账写失败 ⇒
    不落信号、不起活」。
    """
    handle_fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    temp_path = pathlib.Path(temp_name)
    handle = os.fdopen(handle_fd, "w", encoding="utf-8")
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(temp_path, path)
