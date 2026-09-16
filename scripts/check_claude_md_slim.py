#!/usr/bin/env python3
"""CLAUDE.md 瘦身守恒校验（Token 治理 Phase 5，[Mac]0916H）。

CLAUDE.md 每个会话、每个子代理都会整份加载（P5 实测项目层约 25k token/首轮，CLAUDE.md 是其中最大一块）。
瘦身只许做一件事：把「由来/实证/反例」类叙述**原样**搬到 `docs/CLAUDE-md-由来.md`，规则一条不删。
机器判据（相对 git 基准版本，默认 HEAD）：
  ① 从 CLAUDE.md 删掉的每一个非空行，必须原样出现在 docs/CLAUDE-md-由来.md（搬走≠删掉）
  ② 基准版里含规则标记（⛔ 🔴 必须 不许 禁止 一律 不得 硬规则）的每一行，必须仍原样留在 CLAUDE.md
  ③ CLAUDE.md 里新增的行只允许是指针行（含「CLAUDE-md-由来」字样）或空行
用法：python3 scripts/check_claude_md_slim.py [--base HEAD]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULE = re.compile(r"⛔|🔴|必须|不许|禁止|一律|不得|硬规则")
POINTER = "CLAUDE-md-由来"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    root = Path(a.root)
    base = subprocess.run(["git", "-C", str(root), "show", f"{a.base}:CLAUDE.md"],
                          capture_output=True, text=True, check=True).stdout.split("\n")
    now = (root / "CLAUDE.md").read_text(encoding="utf-8").split("\n")
    arch_p = root / "docs" / "CLAUDE-md-由来.md"
    arch = Counter(arch_p.read_text(encoding="utf-8").split("\n")) if arch_p.exists() else Counter()
    b, n = Counter(l for l in base if l.strip()), Counter(l for l in now if l.strip())
    errs = []
    removed = b - n
    missing = [l for l, c in removed.items() if arch[l] < c]
    if missing:
        errs.append(f"① 删掉却没搬进 docs/CLAUDE-md-由来.md 的行 {len(missing)} 条，例：{missing[0][:80]}")
    lost_rules = [l for l in b if RULE.search(l) and n[l] == 0]
    if lost_rules:
        errs.append(f"② 规则行被移出 CLAUDE.md {len(lost_rules)} 条，例：{lost_rules[0][:80]}")
    added = [l for l in (n - b) if POINTER not in l]
    if added:
        errs.append(f"③ CLAUDE.md 新增了非指针行 {len(added)} 条，例：{added[0][:80]}")
    kb = lambda ls: len("\n".join(ls).encode("utf-8")) / 1024
    print(f"CLAUDE.md {kb(base):.1f} KB → {kb(now):.1f} KB ｜ 搬走 {sum(removed.values())} 行")
    if errs:
        print("✗ " + "\n✗ ".join(errs)); sys.exit(1)
    print("✓ 守恒：搬走的都在由来文件里，规则行一条未动，无新增正文")


if __name__ == "__main__":
    main()
