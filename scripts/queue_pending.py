#!/usr/bin/env python3
"""定夺队列「只出待答行」查询（`[Mac]0919Q`）。

为什么：`docs/roadmap/定夺队列.md`「一、待答」全表常年混着已答／作废／远期行，
2026-09-19 巡检时被整段读入（49 行里真正待答只有个位数），复现了 Win 端复盘钉住的同一类
开场 token 浪费。本模块只复用 `dispatcher_answers.queue_rows`／`_status` 做过滤，
不改两者的既有语义，只加一层「只出待答行 ＋ 显式回显隐藏行数」的薄壳。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts import dispatcher_answers as answers
from scripts import gates

TRUNCATE_AT = 60


def _truncate(text: str, limit: int = TRUNCATE_AT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def pending_rows(text: str) -> list[dict[str, str]]:
    """`queue_rows` 里 `_status == 待答` 的行；解析不出状态的行按 `_status` 既有语义归入待答，不吞。"""
    return [r for r in answers.queue_rows(text) if answers._status(r) == "待答"]


def format_row(row: dict[str, str]) -> str:
    cells = [
        row.get("编号", ""),
        row.get("场景", ""),
        row.get("阻塞类型", ""),
        _truncate(row.get("问题", "")),
        row.get("推荐", ""),
    ]
    return "｜".join(cells)


def render(text: str) -> str:
    rows = answers.queue_rows(text)
    pending = pending_rows(text)
    header = f"共 {len(rows)} 行，其中待答 {len(pending)} 行（隐藏 {len(rows) - len(pending)} 行已答/作废/远期）"
    lines = [header]
    lines.extend(format_row(r) for r in pending)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=gates.ROOT, help="仓库根，默认当前仓库")
    ap.add_argument("--queue", type=Path, default=None, help="队列文件路径，默认 <repo>/docs/roadmap/定夺队列.md")
    args = ap.parse_args(argv)
    queue = args.queue or (args.repo.resolve() / gates.QUEUE_REL)
    text = queue.read_text(encoding="utf-8") if queue.is_file() else ""
    print(render(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
