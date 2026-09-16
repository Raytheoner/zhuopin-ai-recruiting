#!/usr/bin/env python3
"""长会话「拆段」收益推演（Token 治理 Phase 7 前置，[Mac]0916J）。

问题：把一个长会话拆成 k 段、每段全新上下文（Workflow 的 agent() 或手工分件），能省多少？
方法（只用真实日志里每次调用的上下文序列，不假设增长形状）：
  原会话输入量 = Σ ctx_i
  拆 k 段（按调用数均分）：第 j 段内每次调用的上下文 = ctx_i − (ctx_{段首} − ctx_0) + HANDOFF
    即「扣掉前面各段累积的增长、从底座重新起步、再加一份交接摘要」
  节省比例 = 1 − Σ拆后 / Σ原
价格按该会话主模型的 cache_read 单价折算（输入绝大部分是 cache read；输出量拆不拆不变，不计）。
这是**上限估计**：假设各段互不需要对方读过的内容。需要沿用前文线索的调试线，实际收益更低甚至为负——
所以报告里每个会话必须人工再标「可切分／不可切分」，不可切分的不计入试点候选。

用法：python3 scripts/split_projection.py --since 2026-09-01 --until 2026-09-15 --top 10 --out docs/token治理/P7-拆段推演.md
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_ledger as tl  # noqa: E402

HANDOFF = 3000


def project(ctx, k):
    n = len(ctx)
    if n < k * 5:
        return None
    base = ctx[0]
    total = 0
    for j in range(k):
        s, e = j * n // k, (j + 1) * n // k
        drop = ctx[s] - base if j else 0
        total += sum(max(base, c - drop) + (HANDOFF if j else 0) for c in ctx[s:e])
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--match", default="-Users-paulshao-Projects-HumanResource")
    ap.add_argument("--since"); ap.add_argument("--until")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since).replace(tzinfo=tl.CST) if a.since else None
    until = datetime.fromisoformat(a.until).replace(tzinfo=tl.CST) + timedelta(days=1) if a.until else None
    files = []
    for d in glob.glob(os.path.join(a.root, a.match + "*")):
        files += glob.glob(os.path.join(d, "*.jsonl"))          # 只看主会话，子代理本来就是独立上下文
    rows = []
    for f in files:
        s = tl.scan_file(f, since, until)
        if s["calls"] < 20:
            continue
        fam = s["models"].most_common(1)[0][0]
        price = tl.PRICES.get(fam, (0, 0, 0, 0))[2]
        orig = sum(s["ctx"])
        cost = sum(tl.cost(fm, u) for fm, u in s["u"].items())
        r = {"title": s["title"] or os.path.basename(f)[:8], "fam": fam, "calls": s["calls"], "first": s["ctx"][0],
             "peak": max(s["ctx"]), "cost": cost, "orig": orig}
        for k in (2, 3, 4):
            p = project(s["ctx"], k)
            r[k] = None if p is None else (1 - p / orig, (orig - p) * price / 1e6)
        rows.append(r)
    rows.sort(key=lambda r: -r["cost"])
    top = rows[: a.top]
    L = [f"# P7 拆段推演（{a.since} → {a.until}，主会话成本 Top {a.top}）", "",
         f"> 生成：`scripts/split_projection.py`。方法与「上限估计」口径见脚本 docstring；交接摘要按每段 {HANDOFF} token 计。", "",
         "| # | 标题 | 模型 | 调用 | 首轮 | 峰值 | 成本$ | 拆2 省 | 拆3 省 | 拆4 省 | 可切分？ |", "|---:|---|---|---:|---:|---:|---:|---|---|---|---|"]
    fmt = lambda v: "—" if v is None else f"{v[0]:.0%} / ${v[1]:.1f}"
    for i, r in enumerate(top, 1):
        L.append(f"| {i} | {r['title'][:34]} | {r['fam']} | {r['calls']} | {tl.fmt(r['first'])} | {tl.fmt(r['peak'])} | {r['cost']:.1f} | {fmt(r[2])} | {fmt(r[3])} | {fmt(r[4])} | 待标 |")
    tot = sum(r["cost"] for r in top)
    s3 = sum((r[3] or (0, 0))[1] for r in top)
    L += ["", f"Top {a.top} 合计成本 ${tot:.1f}；若全部拆 3 段，cache read 上限可省 ${s3:.1f}（{s3 / max(tot, 1e-9):.0%}）。「可切分」列由人工标注后，只按可切分行重算。"]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"OK {len(rows)} 个 ≥20 调用的主会话 → {a.out}")


if __name__ == "__main__":
    main()
