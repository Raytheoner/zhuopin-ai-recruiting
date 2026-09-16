#!/usr/bin/env python3
"""滚动大文件机械归档（Token 治理 Phase 3，[Mac]0916D）。

为什么：`docs/session接力.md`（118 KB）与 `docs/openers/OP-0820-全量编排.md`（379 KB）只增不减，
新会话开场、出号查台账、改文档时整段读进上下文，之后每一轮都要为它付 cache read。
归档＝把「已结束」的段落**原样搬走**，不改写、不删除；grep 仍能在归档文件里查到全部历史（号池查重不受影响）。

搬什么（全部机械判定，不靠理解正文）：
  1. OP-0820：`## ` 段落里不含待执行标注 `> 泳道：`、且标题不含「并发协议」「待执行区」的 → docs/openers/归档/OP-0820-历史批次.md
     首次运行（`--init-ledger`）先把「🔢 号池台账」整节拆到 docs/openers/号池台账.md
  2. 号池台账：日期列月份 ≠ 当月（北京时间）的行 → docs/openers/归档/号池台账-归档.md
  3. session接力：标题以 `### ~~` 开头（已划掉）的小节，以及标题含「【已闭环】」的 `##`/`###` 段 → docs/archive/session接力-归档.md

守恒校验（不过即不写盘）：搬运前所有涉及文件的行（含已有归档文件）＝ 搬运后所有行 − 本工具新增的行；
本工具新增的每一行都带「〔归档工具〕」字样，便于人工识别。

用法：
  python3 scripts/archive_docs.py                 # 只打印计划（默认 dry-run）
  python3 scripts/archive_docs.py --apply         # 写盘
  python3 scripts/archive_docs.py --init-ledger --apply   # 一次性：拆出号池台账（已拆过则跳过）
  python3 scripts/archive_docs.py --scope plan|relay --apply   # 只处理一侧
  python3 scripts/archive_docs.py --verify-tags   # 核对 session接力.md 相对 HEAD 只加了「【已闭环】」标记
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

MARK = "〔归档工具〕"
TAG = "【已闭环】"
CST = timezone(timedelta(hours=8))

ROOT = Path(__file__).resolve().parent.parent
REL = {
    "plan": "docs/openers/OP-0820-全量编排.md",
    "ledger": "docs/openers/号池台账.md",
    "plan_arch": "docs/openers/归档/OP-0820-历史批次.md",
    "ledger_arch": "docs/openers/归档/号池台账-归档.md",
    "relay": "docs/session接力.md",
    "relay_arch": "docs/archive/session接力-归档.md",
    "td": "docs/tech-debt.md",
    "td_arch": "docs/archive/tech-debt-已还.md",
}
PENDING = re.compile(r"^>\s*泳道：")
KEEP_PLAN = ("并发协议", "待执行区")
ROW = re.compile(r"^\|\s*(\d{2})-(\d{2})\s*\|")


def sections(lines, prefix):
    """按标题前缀切段：返回 [(start, end)]，end 不含；第一个标题之前的前言不算段。"""
    idx = [i for i, l in enumerate(lines) if l.startswith(prefix) and not l.startswith(prefix + "#")]
    return [(s, idx[k + 1] if k + 1 < len(idx) else len(lines)) for k, s in enumerate(idx)]


def read(files, key):
    p = files[key]
    return p.read_text(encoding="utf-8").split("\n") if p.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--init-ledger", action="store_true")
    ap.add_argument("--verify-tags", action="store_true")
    ap.add_argument("--scope", choices=["all", "plan", "relay", "techdebt"], default="all",
                    help="plan＝编排文件＋号池台账；relay＝接力文件")
    ap.add_argument("--today", help="测试用，YYYY-MM-DD")
    a = ap.parse_args()
    root = Path(a.root)
    files = {k: root / v for k, v in REL.items()}
    today = datetime.fromisoformat(a.today).replace(tzinfo=CST) if a.today else datetime.now(CST)
    stamp = today.strftime("%Y-%m-%d")

    if a.verify_tags:
        head = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{REL['relay']}"],
                              capture_output=True, text=True, check=True).stdout.split("\n")
        head = [l.replace(TAG, "") for l in head]
        work = [l.replace(TAG, "") for l in read(files, "relay")]
        if head != work:
            bad = next(i for i, (x, y) in enumerate(zip(head + [""], work + [""])) if x != y)
            sys.exit(f"✗ session接力.md 相对 HEAD 不止加了「{TAG}」：第 {bad+1} 行附近\n  HEAD: {head[bad][:80] if bad < len(head) else '<EOF>'}\n  现在: {work[bad][:80] if bad < len(work) else '<EOF>'}")
        n = sum(l.count(TAG) for l in read(files, "relay"))
        print(f"✓ session接力.md 相对 HEAD 只加了 {n} 处「{TAG}」")
        return

    before = {k: read(files, k) for k in REL}
    after = {k: (list(v) if v is not None else None) for k, v in before.items()}
    added = Counter()
    report = []

    def append_block(key, title, block):
        cur = after[key]
        if cur is None:
            head = [f"# {Path(REL[key]).stem}", "", f"> {MARK} 只追加、不回改；由 scripts/archive_docs.py 从原文件原样搬入。", ""]
            added.update(head)
            cur = head
        hdr = ["", f"<!-- {MARK} {stamp} 搬入：{title} -->", ""]
        added.update(hdr)
        after[key] = cur + hdr + block

    # 0. 一次性拆出号池台账
    plan = after["plan"]
    do_plan, do_relay = a.scope in ("all", "plan"), a.scope in ("all", "relay")
    do_td = a.scope in ("all", "techdebt")
    if a.init_ledger and do_plan:
        if after["ledger"] is not None:
            report.append("号池台账.md 已存在，--init-ledger 跳过")
        else:
            sec = next(((s, e) for s, e in sections(plan, "## ") if "号池台账" in plan[s]), None)
            if not sec:
                sys.exit("✗ OP-0820 里找不到「号池台账」节")
            s, e = sec
            body = plan[s:e]
            head = ["# 号池台账", "", f"> {MARK} {stamp} 从 OP-0820-全量编排.md 顶部整节拆出（Token 治理 Phase 3）。出号、登记只动本文件。", ""]
            pointer = ["## 🔢 号池台账 → 已迁至 `docs/openers/号池台账.md`", "",
                       f"> {MARK} {stamp}：出号前查、给出后登记，一律改在该文件；历史月份的行在 `docs/openers/归档/号池台账-归档.md`。", ""]
            added.update(head); added.update(pointer)
            after["ledger"] = head + body
            plan = plan[:s] + pointer + plan[e:]
            report.append(f"号池台账整节 {len(body)} 行 → 号池台账.md")

    # 1. OP-0820 历史段
    moved, keep = [], []
    cursor = 0
    out = []
    for s, e in (sections(plan, "## ") if do_plan else []):
        out += plan[cursor:s]; cursor = e
        seg = plan[s:e]; h = seg[0]
        if any(k in h for k in KEEP_PLAN) or "号池台账" in h or any(PENDING.match(l) for l in seg):
            out += seg; keep.append(h[:40])
        else:
            moved.append((h, seg))
    out += plan[cursor:]
    if moved:
        if not any("待执行区" in l for l in out if l.startswith("## ")):
            zone = ["## 待执行区", "",
                    f"> {MARK} {stamp}：`lane-dispatch` 新写的无头 opener 放在本节之后（`> 泳道：<名>` ＋ 代码块）。",
                    f"> {MARK} 跑完被 `mark_done` 摘标注的批次，下次 `python3 scripts/archive_docs.py --apply` 会整段搬去归档。", ""]
            added.update(zone); out += zone
        for h, seg in moved:
            append_block("plan_arch", h.lstrip("# ")[:60], seg)
        report.append(f"OP-0820：搬走 {len(moved)} 段，保留 {len(keep)} 段 {keep}")
    after["plan"] = out

    # 2. 号池台账按月轮转
    if after["ledger"] is not None and do_plan:
        led, old = [], {}
        for l in after["ledger"]:
            m = ROW.match(l)
            if m and int(m.group(1)) != today.month:
                y = today.year - (1 if int(m.group(1)) > today.month else 0)
                old.setdefault(f"{y}-{m.group(1)}", []).append(l)
            else:
                led.append(l)
        for ym, rows in sorted(old.items()):
            tbl = ["| 日期 | 号 | 主题 | 去向 |", "|---|---|---|---|"]
            added.update(tbl)
            append_block("ledger_arch", f"{ym} 月登记行", tbl + rows)
        if old:
            report.append(f"号池台账：{sum(len(v) for v in old.values())} 行历史月份 → 归档")
        after["ledger"] = led

    # 3. session接力
    relay = after["relay"]
    drop = set()
    for prefix in (("## ", "### ") if do_relay else ()):
        for s, e in sections(relay, prefix):
            h = relay[s]
            if TAG in h or (prefix == "### " and h.startswith("### ~~")):
                if prefix == "## ":
                    e = next((i for i in range(s + 1, len(relay)) if relay[i].startswith("## ")), len(relay))
                else:  # ### 段止于下一个 ### 或 ##
                    e = next((i for i in range(s + 1, len(relay)) if relay[i].startswith("## ") or relay[i].startswith("### ")), len(relay))
                if not any(i in drop for i in range(s, e)):
                    drop.update(range(s, e))
                    append_block("relay_arch", h.lstrip("# ")[:60], relay[s:e])
    if drop:
        report.append(f"session接力：搬走 {len(drop)} 行")
    after["relay"] = [l for i, l in enumerate(relay) if i not in drop]

    # 4. tech-debt：标题形如 `## ~~TD-N~~ …`（已还）的整段 → 归档（0916K）
    if do_td and after["td"] is not None:
        td, keep_td, moved_td = after["td"], [], 0
        cur = 0
        for s_, e_ in sections(td, "## "):
            keep_td += td[cur:s_]; cur = e_
            if td[s_].startswith("## ~~"):
                append_block("td_arch", td[s_].lstrip("# ")[:60], td[s_:e_]); moved_td += 1
            else:
                keep_td += td[s_:e_]
        keep_td += td[cur:]
        if moved_td:
            report.append(f"tech-debt：搬走 {moved_td} 条已还")
        after["td"] = keep_td

    # 守恒校验
    b = Counter(l for v in before.values() if v for l in v)
    c = Counter(l for v in after.values() if v for l in v)
    if c - added != b or b - (c - added):
        diff = list(((c - added) - b).items())[:3] + list((b - (c - added)).items())[:3]
        sys.exit(f"✗ 守恒校验失败，未写盘。样例：{diff}")

    def kb(v): return sum(len(l.encode("utf-8")) + 1 for l in v) / 1024 if v else 0
    for k in REL:
        if before[k] != after[k]:
            report.append(f"  {REL[k]}：{kb(before[k]):.0f} KB → {kb(after[k]):.0f} KB")
    print("\n".join(report) if report else "无可归档内容")
    print("✓ 守恒校验通过")
    if a.apply:
        for k in REL:
            if after[k] is not None and before[k] != after[k]:
                files[k].parent.mkdir(parents=True, exist_ok=True)
                files[k].write_text("\n".join(after[k]), encoding="utf-8")
        print("已写盘")
    else:
        print("（dry-run，未写盘；加 --apply 执行）")


if __name__ == "__main__":
    main()
