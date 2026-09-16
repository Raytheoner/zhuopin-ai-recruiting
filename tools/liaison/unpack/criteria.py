"""4.2·口径点台账的纯函数与常量。

**为什么不 import `storage.db`**：spec `liaison-criteria-ledger` 明写
"转态命令不打开值守数据库"——`criteria` 是人手工敲的一次性命令，与值守线程
毫无关系；⛔ 让它碰一下库连接就给了"某天有人图省事在这里插一句 db 查询"
的口子，而那条口子会在值守线程独占连接的假设上开出一个洞（工程铁律 1）。

**为什么状态转换是纯函数**：`已签认` 缺 evidence 必须"退出码 3 且文件逐字节
不变"——纯函数在抛异常之前不产出任何新文本，调用方（CLI 层）因此天然满足
这条要求，不需要额外写"回滚"逻辑。
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

# tools/liaison/unpack/criteria.py → parents[0]=unpack, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 台账真身。⚠️ 这个相对路径字符串与 Task 1 建的文件必须逐字一致。
LEDGER_PATH = REPO_ROOT / "docs" / "跟进信" / "口径点台账.md"

EXIT_OK = 0
EXIT_BAD_ARGS = 2
#: spec 明写的退出码——`已签认` 缺 evidence。⚠️ 与 `__main__.py` 的启动期码、
#: `followup.py` 的发信码都刻意不共用一套：三个命令的失败面完全不同。
EXIT_MISSING_EVIDENCE = 3

#: `criteria --to` 允许的目标态。⛔ 不含"待专员"——那只是新增的初始态，
#: 不该是转态目标（转回"待专员"没有业务含义）。
ALLOWED_TRANSITIONS = ("已回复", "已签认", "已作废")

_ID_PATTERN = re.compile(r"HR-G-(\d+)")


class MissingEvidenceError(Exception):
    """转 `已签认` 但未给非空 `--evidence`。⚠️ 抛出时台账文本尚未被改写。"""


def _table_row_cells(line: str) -> list[str] | None:
    """把一行台账表格行拆成 `|` 分隔的单元格；非表格行/分隔行返回 `None`。

    分隔行（`|---|---|...`）判据：整行去掉所有 `|` 之后只剩 `-` 和空格。
    """
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    bare = stripped.replace("|", "").strip()
    if bare and set(bare) <= {"-"}:
        return None
    return line.split("|")


def compute_new_criterion(
    text: str, *, from_letter: str, desc: str, today: datetime.date
) -> tuple[str, str]:
    """纯函数：在台账末尾追加一条新口径点，初始态 `待专员`、evidence 为空。

    返回 `(新文本, 新分配的 ID)`。ID 取现存 `HR-G-NN` 里的最大编号 +1，
    ⛔ 不回收已作废口径点的编号——台账是审计记录，编号一旦分配就不作废重用。
    """
    max_seen = 0
    for match in _ID_PATTERN.finditer(text):
        max_seen = max(max_seen, int(match.group(1)))
    new_id = f"HR-G-{max_seen + 1:02d}"
    new_row = f"| `{new_id}` | {from_letter} | {desc} | 待专员 |  | {today.isoformat()} |\n"
    base = text if text.endswith("\n") else text + "\n"
    return base + new_row, new_id


def compute_criteria_transition(
    text: str, *, id: str, to: str, evidence: str | None, today: datetime.date
) -> tuple[str, bool]:
    """纯函数：把台账里 `口径点ID` 为 `id` 的那一行转态为 `to`。

    `to == "已签认"` 且 `evidence` 为空或全空白 ⇒ 抛 `MissingEvidenceError`，
    ⛔ 这一支在抛异常之前不构造、不返回任何新文本——调用方据此保证
    "台账逐字节不变"，不需要额外写回滚逻辑。

    `id` 在台账里找不到 ⇒ 抛 `LookupError(id)`。

    返回 `(新文本, changed)`；`changed` 恒为 `True`（找不到行已经抛异常，
    找到了就一定重写）。保留这个返回值是为了跟 `followup.py::
    compute_backfilled_ledger` 的调用形状一致，⛔ 不要因为它恒真就删掉——
    删掉会让两个模块的调用点长得不一样，读者要多记一种形状。
    """
    if to == "已签认" and not (evidence and evidence.strip()):
        raise MissingEvidenceError(id)

    marker = f"`{id}`"
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        cells = _table_row_cells(line)
        if cells is None:
            continue
        if cells[1].strip() != marker:
            continue
        cells[4] = f" {to} "
        if evidence and evidence.strip():
            cells[5] = f" {evidence.strip()} "
        cells[6] = f" {today.isoformat()} "
        lines[index] = "|".join(cells)
        return "".join(lines), True
    raise LookupError(id)
