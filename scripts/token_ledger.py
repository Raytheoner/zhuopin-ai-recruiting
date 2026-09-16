#!/usr/bin/env python3
"""Token 账本（Phase 0 基线 + 每个 Phase 验收的唯一量尺）。

读本机 Claude Code 会话日志（~/.claude/projects/<HumanResource 及其 worktree>/*.jsonl，
含 subagents/ 子目录），按会话汇总 token 与折算成本，产出 Markdown 报告 + JSON 快照。

为什么自己算、不抄报告：外部报告的数字无法复核（口径不明：是否按 message.id 去重、
<synthetic> 是否计入、价格表是否正确）。本脚本口径写死在代码里，任何一轮都能重跑对比。

口径：
  - 一次 API 调用 = 一个 assistant message.id；同一 id 在 JSONL 里会按内容块重复多行，只计一次
  - 上下文大小 = input + cache_creation + cache_read（该次调用送进模型的总量）
  - <synthetic> 是本地合成消息，不是 API 调用，不计成本（单列条数）
  - 价格（$/MTok，input/cache_write_5m/cache_read/output）取 platform.claude.com 2026-09-16 页面：
      Opus 5 = 5/6.25/0.50/25   Sonnet 5 = 2/2.50/0.20/10   Haiku 4.5 = 1/1.25/0.10/5
    这是 API 等价成本，用来比较相对大小；订阅额度的实际折算规则官方未公开

用法：
  python3 scripts/token_ledger.py --since 2026-09-01 --until 2026-09-15 --out docs/token治理/baseline-0901-0915
  python3 scripts/token_ledger.py --since 2026-09-17 --compare docs/token治理/baseline-0901-0915.json --out docs/token治理/phase1-check
只读，不改任何日志。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

PRICES = {  # input, cache_write, cache_read, output  ($/MTok)
    "opus": (5.0, 6.25, 0.50, 25.0),
    "sonnet": (2.0, 2.50, 0.20, 10.0),
    "haiku": (1.0, 1.25, 0.10, 5.0),
}
BIG = 10_000  # 字节：「大回显」阈值
CST = timezone(timedelta(hours=8))


def family(model: str) -> str:
    m = (model or "").lower()
    for k in PRICES:
        if k in m:
            return k
    return "synthetic" if "synthetic" in m else "other"


def cost(fam, u):
    p = PRICES.get(fam)
    if not p:
        return 0.0
    return (u["input"] * p[0] + u["cache_create"] * p[1] + u["cache_read"] * p[2] + u["output"] * p[3]) / 1e6


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(CST)
    except Exception:
        return None


def result_len(content):
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        n = 0
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                n += len(str(c.get("text", "")).encode("utf-8"))
        return n
    return 0


def cmd_key(name, inp):
    if name == "Bash":
        c = str(inp.get("command", "")).strip()
        c = re.sub(r"^(cd\s+\S+\s*(&&|;)\s*)+", "", c)
        w = c.split()
        if not w:
            return "Bash:?"
        head = os.path.basename(w[0])
        if head in ("git", "python", "python3", "uv", "npx", "npm") and len(w) > 1:
            sub = w[1] if not w[1].startswith("-") else (w[2] if len(w) > 2 else "")
            if head.startswith("python") and sub == "-m" and len(w) > 2:
                sub = w[2]
            if "pytest" in c:
                return "Bash:pytest"
            return f"Bash:{head} {sub}"
        if "pytest" in head:
            return "Bash:pytest"
        if head in ("cat", "head", "tail", "less") and len(w) > 1:
            return f"Bash:{head} {os.path.basename(w[-1])}"
        return f"Bash:{head}"
    if name == "Read":
        return "Read"
    return name


def new_session():
    return {
        "title": "", "file": "", "is_sub": False, "parent": "", "start": None, "end": None,
        "calls": 0, "synthetic": 0, "models": Counter(),
        "u": defaultdict(lambda: {"input": 0, "cache_create": 0, "cache_read": 0, "output": 0}),
        "ctx": [], "tools": Counter(), "big": Counter(), "big_bytes": Counter(), "read_files": Counter(),
        "agent_spawns": 0,
    }


def scan_file(path, since, until):
    s = new_session()
    s["file"] = path
    s["is_sub"] = "/subagents/" in path
    s["parent"] = os.path.basename(os.path.dirname(os.path.dirname(path))) if s["is_sub"] else ""
    seen = set()
    tool_names = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t in ("custom-title",) and o.get("customTitle"):
                s["title"] = o["customTitle"]
                continue
            if t == "summary" and o.get("summary") and not s["title"]:
                s["title"] = "〔摘要〕" + str(o["summary"])[:40]
                continue
            ts = parse_ts(o.get("timestamp", "")) if o.get("timestamp") else None
            if ts:
                if since and ts < since or until and ts >= until:
                    continue
                s["start"] = ts if not s["start"] or ts < s["start"] else s["start"]
                s["end"] = ts if not s["end"] or ts > s["end"] else s["end"]
            msg = o.get("message") or {}
            if t == "assistant":
                content = msg.get("content") or []
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            if c.get("id") in tool_names:
                                continue
                            name = c.get("name", "?")
                            inp = c.get("input") or {}
                            tool_names[c.get("id")] = (name, cmd_key(name, inp), inp.get("file_path", ""))
                            s["tools"][name] += 1
                            if name in ("Agent", "Task"):
                                s["agent_spawns"] += 1
                            if name == "Read" and inp.get("file_path"):
                                s["read_files"][inp["file_path"]] += 1
                mid = msg.get("id") or o.get("uuid")
                if mid in seen:
                    continue
                seen.add(mid)
                model = msg.get("model", "")
                fam = family(model)
                if fam == "synthetic":
                    s["synthetic"] += 1
                    continue
                us = msg.get("usage") or {}
                if not us:
                    continue
                u = {"input": us.get("input_tokens", 0) or 0,
                     "cache_create": us.get("cache_creation_input_tokens", 0) or 0,
                     "cache_read": us.get("cache_read_input_tokens", 0) or 0,
                     "output": us.get("output_tokens", 0) or 0}
                s["calls"] += 1
                s["models"][fam] += 1
                for k, v in u.items():
                    s["u"][fam][k] += v
                s["ctx"].append(u["input"] + u["cache_create"] + u["cache_read"])
            elif t == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            n = result_len(c.get("content"))
                            name, key, fp = tool_names.get(c.get("tool_use_id"), ("?", "?", ""))
                            if n >= BIG:
                                k = key if key != "Read" else f"Read:{os.path.basename(fp)}"
                                s["big"][k] += 1
                                s["big_bytes"][k] += n
    return s


def summarize(sessions):
    tot = defaultdict(lambda: {"input": 0, "cache_create": 0, "cache_read": 0, "output": 0, "cost": 0.0, "calls": 0})
    band = Counter()  # cache_read 按单次上下文分段
    rows = []
    first_main, first_sub = [], []
    big, big_bytes, tools, reads = Counter(), Counter(), Counter(), Counter()
    by_kind = defaultdict(lambda: {"sessions": 0, "cost": 0.0, "calls": 0})
    for s in sessions:
        if s["calls"] == 0:
            continue
        c = 0.0
        for fam, u in s["u"].items():
            fc = cost(fam, u)
            c += fc
            for k in u:
                tot[fam][k] += u[k]
            tot[fam]["cost"] += fc
            tot[fam]["calls"] += s["models"][fam]
        for x in s["ctx"]:
            b = "<50k" if x < 50_000 else "50–150k" if x < 150_000 else "150–250k" if x < 250_000 else "≥250k"
            band[b] += 1
        (first_sub if s["is_sub"] else first_main).append(s["ctx"][0])
        big.update(s["big"]); big_bytes.update(s["big_bytes"]); tools.update(s["tools"]); reads.update(s["read_files"])
        title = s["title"] or os.path.basename(s["file"])[:8]
        kind = ("子代理" if s["is_sub"] else "看护" if "看护" in title else
                "泳道/opener" if title.startswith("[Mac]") else "交互/其他")
        by_kind[kind]["sessions"] += 1; by_kind[kind]["cost"] += c; by_kind[kind]["calls"] += s["calls"]
        rows.append({
            "title": title, "kind": kind, "file": os.path.basename(s["file"]),
            "date": s["start"].strftime("%m-%d") if s["start"] else "?",
            "model": "/".join(f"{k}:{v}" for k, v in s["models"].most_common()),
            "calls": s["calls"], "peak_ctx": max(s["ctx"]), "first_ctx": s["ctx"][0],
            "cache_read": sum(u["cache_read"] for u in s["u"].values()),
            "output": sum(u["output"] for u in s["u"].values()),
            "cost": round(c, 2), "agents": s["agent_spawns"], "big_outputs": sum(s["big"].values()),
        })
    rows.sort(key=lambda r: -r["cost"])
    med = lambda a: int(statistics.median(a)) if a else 0
    return {
        "by_model": {k: dict(v) for k, v in tot.items()},
        "total_cost": round(sum(v["cost"] for v in tot.values()), 4),
        "total_calls": sum(v["calls"] for v in tot.values()),
        "sessions": len(rows),
        "ctx_band_calls": dict(band),
        "first_ctx_median": {"main": med(first_main), "subagent": med(first_sub)},
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "big_outputs": [(k, big[k], big_bytes[k]) for k, _ in big_bytes.most_common(15)],
        "tools": tools.most_common(12),
        "most_read": reads.most_common(10),
        "synthetic_msgs": sum(s["synthetic"] for s in sessions),
        "top": rows[:25],
    }


def fmt(n):
    return f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.0f}k" if n >= 1e3 else str(n)


def render(sm, args, base=None):
    L = [f"# Token 账本 · {args.since or '起'} → {args.until or '今'}", "",
         f"会话 {sm['sessions']}（含子代理）｜ API 调用 {sm['total_calls']} ｜ API 等价成本 **${sm['total_cost']}**"
         f" ｜ <synthetic> 本地消息 {sm['synthetic_msgs']} 条（不计费）", ""]
    if base:
        d = sm["total_cost"] - base["total_cost"]
        L += [f"> 对比基线：成本 ${base['total_cost']} → ${sm['total_cost']}（{d:+.2f}）；"
              f"主会话首轮上下文中位数 {fmt(base['first_ctx_median']['main'])} → {fmt(sm['first_ctx_median']['main'])}", ""]
    L += ["## 按模型", "", "| 模型 | 调用 | cache_read | cache_write | output | 成本$ | 占比 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for k, v in sorted(sm["by_model"].items(), key=lambda x: -x[1]["cost"]):
        L.append(f"| {k} | {v['calls']} | {fmt(v['cache_read'])} | {fmt(v['cache_create'])} | {fmt(v['output'])} | {v['cost']:.0f} | {v['cost']/max(sm['total_cost'],1e-9):.0%} |")
    L += ["", "## 按会话类型", "", "| 类型 | 会话 | 调用 | 成本$ |", "|---|---:|---:|---:|"]
    for k, v in sorted(sm["by_kind"].items(), key=lambda x: -x[1]["cost"]):
        L.append(f"| {k} | {v['sessions']} | {v['calls']} | {v['cost']:.0f} |")
    L += ["", "## 单次调用上下文分段（滚雪球程度）", ""]
    tc = max(sum(sm["ctx_band_calls"].values()), 1)
    for b in ("<50k", "50–150k", "150–250k", "≥250k"):
        L.append(f"- {b}：{sm['ctx_band_calls'].get(b,0)} 次（{sm['ctx_band_calls'].get(b,0)/tc:.0%}）")
    L += ["", f"首轮上下文中位数：主会话 {fmt(sm['first_ctx_median']['main'])} ｜ 子代理 {fmt(sm['first_ctx_median']['subagent'])}", "",
          "## 大回显（单次 ≥10KB）来源 Top 15", "", "| 来源 | 次数 | 合计 |", "|---|---:|---:|"]
    for k, n, b in sm["big_outputs"]:
        L.append(f"| `{k}` | {n} | {b/1e6:.2f}MB |")
    L += ["", "## 被 Read 最多的文件", ""]
    for f, n in sm["most_read"]:
        L.append(f"- {n}× `{f}`")
    L += ["", "## 成本 Top 25 会话", "", "| 日期 | 标题 | 类型 | 模型(调用) | 调用 | 首轮 | 峰值 | cache_read | 子代理 | 大回显 | $ |",
          "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sm["top"]:
        L.append(f"| {r['date']} | {r['title'][:36]} | {r['kind']} | {r['model']} | {r['calls']} | {fmt(r['first_ctx'])} | {fmt(r['peak_ctx'])} | {fmt(r['cache_read'])} | {r['agents']} | {r['big_outputs']} | {r['cost']:.1f} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--match", default="-Users-paulshao-Projects-HumanResource", help="项目目录名前缀（含 worktree 派生目录）")
    ap.add_argument("--since"); ap.add_argument("--until")
    ap.add_argument("--out", required=True, help="输出路径前缀，生成 .md 与 .json")
    ap.add_argument("--compare", help="基线 JSON")
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since).replace(tzinfo=CST) if a.since else None
    until = datetime.fromisoformat(a.until).replace(tzinfo=CST) + timedelta(days=1) if a.until else None
    files = []
    for d in glob.glob(os.path.join(a.root, a.match + "*")):
        files += glob.glob(os.path.join(d, "*.jsonl")) + glob.glob(os.path.join(d, "*", "subagents", "*.jsonl"))
    if not files:
        sys.exit(f"找不到日志：{a.root}/{a.match}*")
    sessions = [scan_file(f, since, until) for f in files]
    sm = summarize(sessions)
    base = json.load(open(a.compare, encoding="utf-8")) if a.compare else None
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out + ".json", "w", encoding="utf-8") as fh:
        json.dump(sm, fh, ensure_ascii=False, indent=1, default=str)
    with open(a.out + ".md", "w", encoding="utf-8") as fh:
        fh.write(render(sm, a, base))
    print(f"OK {len(files)} 个日志文件 → {a.out}.md / .json ｜ 成本 ${sm['total_cost']} ｜ 会话 {sm['sessions']}")


if __name__ == "__main__":
    main()
