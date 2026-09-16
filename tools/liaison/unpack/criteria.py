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

import argparse
import datetime
import os
import re
import sys
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison criteria",
        description=(
            "口径点台账：新增一条口径点，或把已有口径点转态。"
            "⛔ 无按时间/超期的批量参数——已签认必须人工给 evidence。"
        ),
    )
    parser.add_argument("--add", action="store_true", help="新增一条口径点")
    parser.add_argument(
        "--from", dest="from_letter", metavar="信编号", help="--add 时必填，如 人事部#1"
    )
    parser.add_argument("--desc", help="--add 时必填，口径点描述")
    parser.add_argument("--id", metavar="HR-G-NN", help="转态时必填，目标口径点 ID")
    parser.add_argument(
        "--to", choices=list(ALLOWED_TRANSITIONS), help="转态时必填，目标状态"
    )
    parser.add_argument("--evidence", help="转 已签认 时必填；其它转态可选")
    return parser


def _write_ledger_atomic(path: Path, text: str) -> None:
    """原子写：临时文件 + `os.replace`。

    ⛔ 不用 `with open(...)`：本仓库 `session.py` / `session_client.py` /
    `logsetup.py` / `queue_view.py` 已一致选择"文件读写只用
    `Path.write_text()`/`read_text()` + `os.replace()`，全不写 `with`"，
    本模块跟随同一约定，不再另开一种写法。
    """
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def criteria_main(
    argv: list[str],
    *,
    ledger_path: Path | None = None,
    today: datetime.date | None = None,
) -> int:
    """子命令入口。`ledger_path`/`today` 是测试注入缝，⛔ 不是配置项。"""
    args = build_parser().parse_args(argv)
    today = today or datetime.date.today()
    path = ledger_path if ledger_path is not None else LEDGER_PATH

    if args.add and args.id:
        print("--add 与 --id 不能同时给：新增和转态是两件事", file=sys.stderr)
        return EXIT_BAD_ARGS
    if not args.add and not args.id:
        print("需要 --add 或 --id 之一", file=sys.stderr)
        return EXIT_BAD_ARGS
    if not path.is_file():
        print(f"找不到台账 {path}", file=sys.stderr)
        return EXIT_BAD_ARGS

    text = path.read_text(encoding="utf-8")

    if args.add:
        if not args.from_letter or not args.desc:
            print("--add 需要同时给 --from 与 --desc", file=sys.stderr)
            return EXIT_BAD_ARGS
        new_text, new_id = compute_new_criterion(
            text, from_letter=args.from_letter, desc=args.desc, today=today
        )
        _write_ledger_atomic(path, new_text)
        print(f"已新增 {new_id}｜{path}")
        return EXIT_OK

    if not args.to:
        print("--id 需要同时给 --to", file=sys.stderr)
        return EXIT_BAD_ARGS
    try:
        new_text, _changed = compute_criteria_transition(
            text, id=args.id, to=args.to, evidence=args.evidence, today=today
        )
    except MissingEvidenceError:
        print(
            f"转 已签认 缺 --evidence ⇒ 拒绝，台账不变：{args.id}", file=sys.stderr
        )
        return EXIT_MISSING_EVIDENCE
    except LookupError:
        print(f"台账里没有 {args.id} 这一行", file=sys.stderr)
        return EXIT_BAD_ARGS
    _write_ledger_atomic(path, new_text)
    print(f"{args.id} → {args.to}｜{path}")
    return EXIT_OK
