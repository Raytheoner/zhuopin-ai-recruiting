#!/usr/bin/env python3
"""`codex exec --json` 的 JSONL 收敛器（2026-09-29，Codex 迁移）。

把一条无头泳道的 stdout（JSONL 事件流）收敛成两样东西：

  ① 7 列 TSV：`cost in out cache_read cache_write turns peak`——列序与 run-lanes.sh
     原 claude `--output-format json` 解析完全一致，下游 results.tsv／usage.tsv 零改动。
     `cost` 没有真实值来源，恒 `-`（⛔ 不编造美元数）；`turns`＝`turn.completed` 事件数；
     `peak`＝各轮 `input+cached+write` 之和的最大值，近似单轮上下文峰值——口径与
     claude 的 `usage.iterations` 不同（codex 不提供该结构），只记账与 150k 提示用，
     ⛔ 不当作硬闸。

  ② 最终文本：按序拼接 `item.completed` 里 `type=agent_message` 的 `text`，追加进
     `<log>`，供哨兵 grep（`OPENER_DONE`／`OPENER_PARTIAL`）与 BUDGET-HIT 判据复用原逻辑。

  防退兜底（与 claude 分支同精神）：整份输入一行合法 JSON 都没有 ⇒ 视为非 JSON 输出，
  原文整段当文本落 `<log>`（字节等价），TSV 全 `-`，哨兵判据零影响。

用法：
  python3 scripts/codex_jsonl_summary.py <rawjson> <log>            # run-lanes：落文本 + 打印 TSV
  python3 scripts/codex_jsonl_summary.py --usage-only --skip N <文件>  # dispatcher：只打印 TSV
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_lines(text: str) -> tuple[list[dict], list[str], bool]:
    """逐行解析。返回 (事件列表, 行文本, 是否出现过至少一行合法 JSON)。"""
    events: list[dict] = []
    lines: list[str] = []
    saw_json = False
    for raw in text.splitlines():
        line = raw.strip()
        lines.append(raw)
        if not line:
            continue
        try:
            events.append(json.loads(line))
            saw_json = True
        except ValueError:
            continue
    return events, lines, saw_json


def summarize(text: str) -> tuple[list[str], str, bool]:
    """收敛成 (7 个 TSV 字段, 最终文本, 是否出现过合法 JSON)。"""
    events, lines, saw_json = parse_lines(text)
    tin = tout = cread = cwrite = turns = 0
    peak = 0
    parts: list[str] = []
    for ev in events:
        t = ev.get("type")
        if t == "item.completed":
            item = ev.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                txt = item.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        elif t == "turn.completed":
            turns += 1
            u = ev.get("usage")
            if not isinstance(u, dict):
                continue
            i = int(u.get("input_tokens") or 0)
            o = int(u.get("output_tokens") or 0)
            cr = int(u.get("cached_input_tokens") or 0)
            cw = int(u.get("cache_write_input_tokens") or 0)
            tin += i
            tout += o
            cread += cr
            cwrite += cw
            peak = max(peak, i + cr + cw)

    if parts:
        final = "\n".join(parts)
        if not final.endswith("\n"):
            final += "\n"
    else:
        # 无 agent_message 文本：合法 JSON 却拿不到文本 ⇒ 留空；整份都不是 JSON ⇒
        # 原文当文本（假桩/异常中断兜底，与 claude 分支的旧行为字节等价）。
        final = "" if saw_json else "\n".join(lines)

    if not saw_json:
        fields = ["-", "-", "-", "-", "-", "-", "-"]
    else:
        fields = ["-", str(tin), str(tout), str(cread), str(cwrite), str(turns), str(peak)]
    return fields, final, saw_json


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--usage-only", action="store_true", help="只打印 TSV，不把最终文本追加进日志")
    ap.add_argument("--skip", type=int, default=0, help="跳过文件开头 N 行（dispatcher 的日志与 stderr 混写场景）")
    ap.add_argument("path")
    ap.add_argument("log", nargs="?")
    a = ap.parse_args(argv)

    try:
        raw = Path(a.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    if a.skip:
        raw = "\n".join(raw.splitlines()[a.skip:])

    fields, final, _saw = summarize(raw)
    if final and not a.usage_only and a.log:
        try:
            with open(a.log, "a", encoding="utf-8") as lf:
                lf.write(final)
        except OSError:
            pass
    print("\t".join(fields))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
