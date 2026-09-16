#!/usr/bin/env python3
"""Claude Code `UserPromptSubmit` hook —— 长会话「换任务先换 session」提醒（Token 治理 Phase 6，0916I）。

依据：P0 账本 65% 的调用发生在 50–150k 上下文、18% 在 150k 以上；成本 Top 25 几乎全是一个 session 连做多件事
（0916D 跑完本职后又接着派发 0916E/F、落档裁决、出 0910A——每一轮都在重读前面全部历史）。
会话里「接着做下一件不相干的事」只有人能判断，但人看不到上下文有多大 ⇒ 把数字递给模型，由它在**换任务时**提醒。

行为：读 transcript 里最后一次 API 调用的上下文（input＋cache_creation＋cache_read）。
  ≥ 150k 且本会话在该档位还没提醒过 → 往本轮注入一段提示（stdout，exit 0，不阻断）
  档位：150k、250k 各提醒一次（记在 /tmp/cc-context-guard-<session_id>）
  任何异常 → 静默放行。无头 `claude -p` 首条消息时还没有 transcript，不触发。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

BANDS = (250_000, 150_000)
TAIL_BYTES = 4 * 1024 * 1024


def last_context(path):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "assistant" or o.get("isSidechain"):
            continue
        u = (o.get("message") or {}).get("usage") or {}
        if u:
            return (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    return None


def main():
    try:
        data = json.load(sys.stdin)
        path = data.get("transcript_path")
        if not path or not os.path.isfile(path):
            return 0
        ctx = last_context(path)
        if not ctx:
            return 0
        band = next((b for b in BANDS if ctx >= b), None)
        if band is None:
            return 0
        sid = "".join(ch for ch in str(data.get("session_id") or "unknown") if ch.isalnum() or ch in "-_")[:80]
        mark = os.path.join(os.environ.get("CC_CONTEXT_GUARD_DIR") or tempfile.gettempdir(), f"cc-context-guard-{sid}")
        done = set(open(mark).read().split()) if os.path.exists(mark) else set()
        if str(band) in done:
            return 0
        with open(mark, "a") as fh:
            fh.write(f"{band}\n")
        print(
            f"【Token 护栏·自动注入，非用户原话】本会话上下文已约 {ctx // 1000}k，之后每一轮都要整段重读。"
            "判断用户这条消息：若是**本会话主任务之外的新任务**（另派 opener、另一件事的落档/裁决/排查），"
            "先用一句话建议他新开 session，并给出可直接粘贴的接续要点（≤5 行，或指向已落档的文件），⛔ 不要在本会话里直接展开；"
            "若是同一任务的延续，照常继续，⛔ 不必提这条。"
        )
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
