#!/usr/bin/env python3
"""长 run-build opener 拆分闸（Token 治理 Phase 7·A 路，2026-09-16）。

依据 `docs/token治理/P7-拆段推演.md`：P0 基线里可切分＋可无头的 A 类全是 run-build 建造 opener
（0909B/0908D/0908O/0908Q/0904E，每条 91–125 次调用、plan 5–6 个 Task），拆 3 段省量上限 $19.6。
主会话在逐个派发子代理时上下文不断累积；把一份 plan 拆成同泳道串行的几条，每条新开会话、从进度台账续跑，
就能切断这段累积。

判据（只看编排文件「待执行区」里带 `> 泳道：` 的块）：
  块正文（含它引用的 docs/openers/*.md 正文文件）提到 run-build / subagent-driven-development，
  且引用的 plan 文件 `### Task ` 数 ≥ 5，
  且块里没有声明 Task 范围（如「Task 1–3」「Task 4-6」）也没有「拆分豁免：<理由>」
  ⇒ 报出来，退出码 1。
用法（lane-dispatch ④ dry-run 之后跑）：python3 scripts/opener_split_check.py [--plan <编排文件>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THRESHOLD = 5
BUILD = re.compile(r"(Skill\(|调用?\s*`?|按\s*`?|执行\s*`?)(superpowers:)?(run-build|subagent-driven-development)")
PLAN_ONLY = re.compile(r"Skill\(spec-to-plan\)|`?spec-to-plan`?\s*skill\s*走|调\s*`?spec-to-plan")
PLAN = re.compile(r"docs/superpowers/plans/[\w\-.]+\.md")
OPENER_REF = re.compile(r"docs/openers/[^\s`，。）)]+\.md")
RANGE = re.compile(r"Task\s*\d+\s*[–\-~～至到]\s*\d+|Task\s*\d+\s*起续跑")
EXEMPT = re.compile(r"拆分豁免[:：]\s*\S")


def blocks(plan_text):
    part = plan_text.split("## 待执行区", 1)[-1]
    for m in re.finditer(r"^>\s*泳道：[^\n]*\n(?:>[^\n]*\n|\s*\n)*```\n(.*?)\n```", part, re.M | re.S):
        yield m.group(1)


def check(root, plan_path):
    bad = []
    for body in blocks(plan_path.read_text(encoding="utf-8")):
        oid = (re.match(r"\[Mac\](\w+)-", body) or [None, "?"])[1]
        full = body
        for ref in OPENER_REF.findall(body):
            p = root / ref
            if p.is_file():
                full += "\n" + p.read_text(encoding="utf-8")
        if not BUILD.search(full) or PLAN_ONLY.search(full) or RANGE.search(body) or EXEMPT.search(full):
            continue
        for plan in sorted(set(PLAN.findall(full))):
            p = root / plan
            if not p.is_file():
                continue
            n = len(re.findall(r"^### Task ", p.read_text(encoding="utf-8"), re.M))
            if n >= THRESHOLD:
                k = -(-n // 3)
                bad.append(f"{oid}：{plan} 有 {n} 个 Task ⇒ 拆成同泳道串行 {k} 条（每条 ≤3 个 Task，块内写明「Task a–b」），"
                           f"或在块内写「拆分豁免：<理由>」")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--plan", default=None)
    a = ap.parse_args()
    root = Path(a.root)
    plan = Path(a.plan) if a.plan else root / "docs/openers/OP-0820-全量编排.md"
    bad = check(root, plan)
    if bad:
        print("✗ 长 run-build opener 未拆分（Token 治理 P7·A 路）：\n  " + "\n  ".join(bad))
        return 1
    print("✓ 待执行区无需拆分的长 run-build opener")
    return 0


if __name__ == "__main__":
    sys.exit(main())
