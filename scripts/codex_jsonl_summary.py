#!/usr/bin/env python3
"""`codex exec --json` 的 JSONL 收敛器（2026-09-29，Codex 迁移）。

把一条无头泳道的 stdout（JSONL 事件流）收敛成两样东西：

  ① 7 列 TSV：`cost in out cache_read cache_write turns peak`——列序与 run-lanes.sh
    原 claude `--output-format json` 解析完全一致，下游 results.tsv／usage.tsv 零改动。
     `cost` 没有真实值来源，恒 `-`（⛔ 不编造美元数）；`turns`＝`turn.completed` 事件数；
     `in`／`out`／`cache_read`／`cache_write` 均为**累计口径**——`usage` 是 session 累计值，
     故多事件时取**最后一条累计快照**（⛔ 不逐条相加：把累计量再累加一遍即重复计数）。
     单事件（codex 的常态）下四列即该事件的值，与迁移期旧行为逐字一致。

  ⚠️ `peak` 口径（2026-09-30 `0930I` 修正）：**codex 的 JSONL 不提供单轮上下文峰值**——
     `codex exec --json` 全程只在收尾发一次 `turn.completed`，其 `usage` 是**整场累计值**
     （0930F 实证：`input_tokens=6454677`、`cached_input_tokens=6394496`、`cache_write=0`、
     `turns=1`）。故本列＝「**最大单轮累计用量**」：把每条 `turn.completed` 的 `usage` 当累计值，
     与上一条作差得该轮增量（首条事件的增量＝其自身），
     `peak = max(增量 input_tokens + 增量 cache_write_input_tokens)`。
     ⛔ **不再叠加 `cache_read`**（`cached_input_tokens` 是 `input_tokens` 内的命中量，重复计入
     会把 0930F 这种单事件样本从 6,454,677 虚增到 12,849,173）。⛔ 本列**不得与 claude 的
     `usage.iterations` 口径并排比较**（claude 的 iterations 提供真实单轮快照，codex 没有），
     ⛔ **不得单独作为 150k 转场判据**（codex 泳道的转场判据只看 `CTX-RELAY` 哨兵）。
     `usage` 缺 `input_tokens`／`cache_write_input_tokens` 等字段（老版本 codex）按 0 处理，
     ⛔ 不让整份解析因此退化成 `-`。

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
    prev_in = prev_cw = 0
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
            # 五个用量列仍是**累计口径**：`usage` 是 session 累计值 ⇒ 多事件时取**最后一条累计快照**，
            # ⛔ 不逐条相加（把累计量再累加一遍＝重复计数，与本次要修的 cache_read 失真同一类错误）。
            # 单事件（codex 常态）下与迁移期旧行为逐字一致：`turns=1`，四列即该事件的值。
            tin = i
            tout = o
            cread = cr
            cwrite = cw
            # codex 的 usage 是累计值（0930F 实证：全程只发一次 turn.completed），
            # 故 peak 取「逐轮增量 input + 增量 cache_write」的最大值（首轮增量＝其自身），
            # ⛔ 不叠加 cache_read（已含在 input_tokens 内，重复计入即本次失真的一半）。
            delta_in = i - prev_in
            delta_cw = cw - prev_cw
            peak = max(peak, delta_in + delta_cw)
            prev_in = i
            prev_cw = cw

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
