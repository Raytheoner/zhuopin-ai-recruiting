#!/usr/bin/env python3
"""`.claude/handoff` 近期协议摘要（`[Mac]0919Q`），只读、不改任何 handoff 文件。

为什么：2026-09-19 巡检时用不带过滤的 `find` 扫 `.claude/handoff`——commit/launch 各几百个历史件、
根目录上百个 `lanes-*` 批次目录与几十个 `.log`——命中输出触发工具 63KB 硬截断，复现了 Win 端
复盘钉住的同一类开场 token 浪费。本工具默认只出「最近 N 条 ＋ 一行状态」，历史批次目录与
`.log` 只出汇总行；要看全量必须显式 `--full --confirm-large-output` 两个参数同时给。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDOFF_REL = ".claude/handoff"
SUBDIRS = ("commit", "launch", "events")
_STATUS_SUFFIXES = {".done": "done", ".rejected": "rejected", ".deferred": "deferred", ".request": "待处理"}


def _status_of(name: str) -> str:
    for suf, label in _STATUS_SUFFIXES.items():
        if name.endswith(suf):
            return label
    return "-"


def _mtime_str(p: Path) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))


def _sorted_by_mtime(entries: list[Path]) -> list[Path]:
    return sorted(entries, key=lambda p: p.stat().st_mtime, reverse=True)


def _render_subdir(dir_path: Path, n: int, full: bool) -> list[str]:
    if not dir_path.is_dir():
        return [f"### {dir_path.name}/：不存在"]
    entries = _sorted_by_mtime(list(dir_path.iterdir()))
    total = len(entries)
    shown = entries if full else entries[:n]
    lines = [f"### {dir_path.name}/（共 {total} 条{'' if full else f'，只显示最近 {len(shown)} 条'}）"]
    lines.extend(f"- {e.name}\t{_status_of(e.name)}" for e in shown)
    return lines


def _render_root(handoff: Path, full: bool) -> list[str]:
    if not handoff.is_dir():
        return ["### 根目录：不存在"]
    top = list(handoff.iterdir())
    lane_dirs = [p for p in top if p.is_dir() and p.name.startswith("lanes-")]
    logs = [p for p in top if p.is_file() and p.suffix == ".log"]
    if full:
        lines = [f"### 根目录 lanes-* 批次目录（{len(lane_dirs)} 个，全量）"]
        lines.extend(f"- {p.name}\t{_mtime_str(p)}" for p in _sorted_by_mtime(lane_dirs))
        lines.append(f"### 根目录 .log 文件（{len(logs)} 个，全量）")
        lines.extend(f"- {p.name}\t{_mtime_str(p)}" for p in _sorted_by_mtime(logs))
        return lines
    lines = [f"### 根目录历史批次汇总：lanes-* 目录 {len(lane_dirs)} 个，.log 文件 {len(logs)} 个"]
    latest = max(lane_dirs + logs, key=lambda p: p.stat().st_mtime, default=None)
    if latest is not None:
        lines.append(f"最新一条：{latest.name}（mtime={_mtime_str(latest)}）")
    return lines


def render(handoff: Path, n: int = 10, full: bool = False) -> str:
    lines: list[str] = []
    for name in SUBDIRS:
        lines.extend(_render_subdir(handoff / name, n, full))
    lines.extend(_render_root(handoff, full))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=ROOT, help="仓库根，默认当前仓库")
    ap.add_argument("-n", "--recent", type=int, default=10, help="commit/launch/events 各显示最近几条，默认 10")
    ap.add_argument("--recent-only", action="store_true", default=True, help="默认行为，显式写出以便脚本调用自文档化")
    ap.add_argument("--full", action="store_true", help="全量输出；必须同时传 --confirm-large-output")
    ap.add_argument("--confirm-large-output", action="store_true", help="配合 --full 使用的显式确认")
    args = ap.parse_args(argv)
    if args.full and not args.confirm_large_output:
        print(
            "⛔ --full 需要同时传 --confirm-large-output（避免无确认的全量 dump 撑爆输出），已拒绝执行。",
            file=sys.stderr,
        )
        return 2
    handoff = args.repo.resolve() / HANDOFF_REL
    print(render(handoff, n=args.recent, full=args.full))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
