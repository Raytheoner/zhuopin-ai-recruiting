"""P0·回件桥＋第九态（design.md D1/D2/D3/D9/D10）。

**本模块分两层，⛔ 不许混在一起**（与 archive.py 同一纪律）：
- `compute_*` 是纯函数（工程铁律 2）：不读文件、不读时钟、不记日志。
- `run_bridge` 是编排点，在值守线程里被 `__main__.py::handle_message_frame`
  调用，做台账写、effect 写、信号/起活注入——全部包在 `try/except` 里，
  任何失败都不上抛（design D10）。
"""

from __future__ import annotations

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
