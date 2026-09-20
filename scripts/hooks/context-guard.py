#!/usr/bin/env python3
"""Claude Code context-guard hook —— 一份代码、两个挂点，按 `hook_event_name` 分流。

  · `UserPromptSubmit`（0916I）：有人值守会话的「换任务先换 session」提醒。行为见 main_user_prompt_submit()。
  · `PostToolUse`（0920H）：无头泳道的上下文硬闸。只在 HR_HEADLESS_LANE 非空时生效，越线注入
    「做完手上这个里程碑并提交，然后打 `OPENER_PARTIAL: 上下文转场 | 续棒: …`」，续棒由 run-lanes.sh 自动排。
    有人值守会话在这个挂点**零输出**——⛔ 不许每次工具调用后都被注入。

──── UserPromptSubmit：长会话「换任务先换 session」提醒（Token 治理 Phase 6，0916I）────

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


def _mark_path(data):
    sid = "".join(ch for ch in str(data.get("session_id") or "unknown") if ch.isalnum() or ch in "-_")[:80]
    return os.path.join(os.environ.get("CC_CONTEXT_GUARD_DIR") or tempfile.gettempdir(), f"cc-context-guard-{sid}")


def _claim_once(mark, band):
    """本 session 在该档位是否还没注入过；没注入过则登记并返回 True。"""
    done = set(open(mark).read().split()) if os.path.exists(mark) else set()
    if str(band) in done:
        return False
    with open(mark, "a") as fh:
        fh.write(f"{band}\n")
    return True


def main_post_tool_use(data):
    """无头泳道上下文硬闸（0920H）。

    为什么挂这里：无头泳道一辈子只有开场那一条 user prompt（彼时上下文≈0），UserPromptSubmit 永不触发；
    姐妹项目（Win 端）七条泳道峰值 151k–244k 全部越线、无一条按规则收尾，根因是守卫没有消费者。
    这里注入的哨兵格式与 run-lanes.sh 的 CTX-RELAY 判据是一对——改一处必须改另一处。
    阈值 HR_LANE_CONTEXT_LIMIT（默认 150000）；同一 session 只注入一次（去重档位 `relay`）。
    PostToolUse 的纯 stdout 不进模型，注入必须走 hookSpecificOutput.additionalContext；exit 0 不阻断。
    """
    if not os.environ.get("HR_HEADLESS_LANE"):
        return 0
    path = data.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 0
    ctx = last_context(path)
    if not ctx:
        return 0
    try:
        limit = int(os.environ.get("HR_LANE_CONTEXT_LIMIT") or 150_000)
    except ValueError:
        limit = 150_000
    if ctx < limit:
        return 0
    if not _claim_once(_mark_path(data), "relay"):
        return 0
    text = (
        f"【上下文硬闸·自动注入，非用户原话】本会话上下文已约 {ctx // 1000}k，已越过转场线。"
        "请**把手上这一个里程碑做完并提交**（该提交的提交、该 push 的 push），然后顶格输出一行：\n"
        "`OPENER_PARTIAL: 上下文转场 | 续棒: 分支=<分支名或-> worktree=<路径或-> 上一条commit=<hash或-> "
        "已完成=<一句话> 待续=<一句话>`\n"
        "⛔ 不要开始新的里程碑、⛔ 不要缩小或放弃已开始的这一个、⛔ 不要在报告里省略「待续」。"
        "续棒由 `run-lanes.sh` 自动排，你**不需要**也**不许**自己再起 session。"
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}},
                     ensure_ascii=False))
    return 0


def main_user_prompt_submit(data):
    """有人值守提醒（0916I）。⛔ 行为一字不改：含 HR_HEADLESS_LANE 豁免、150k/250k 两档、每档只提醒一次。"""
    try:
        if os.environ.get("HR_HEADLESS_LANE"):
            return 0          # 无头泳道一律不提醒（0916K：Win 端 #584/#585 泳道被 150k 提醒叫停、半途收尾）
        path = data.get("transcript_path")
        if not path or not os.path.isfile(path):
            return 0
        ctx = last_context(path)
        if not ctx:
            return 0
        band = next((b for b in BANDS if ctx >= b), None)
        if band is None:
            return 0
        if not _claim_once(_mark_path(data), band):
            return 0
        print(
            f"【Token 护栏·自动注入，非用户原话】本会话上下文已约 {ctx // 1000}k，之后每一轮都要整段重读。"
            "判断用户这条消息：若是**本会话主任务之外的新任务**（另派 opener、另一件事的落档/裁决/排查），"
            "先用一句话建议他新开 session，并给出可直接粘贴的接续要点（≤5 行，或指向已落档的文件），⛔ 不要在本会话里直接展开；"
            "若是同一任务的延续，照常继续做完，⛔ 不必提这条、⛔ 不许因为上下文大而提前收尾或缩小任务范围。"
        )
    except Exception:
        return 0
    return 0


def main():
    try:
        data = json.load(sys.stdin)
        if (data.get("hook_event_name") or "UserPromptSubmit") == "PostToolUse":
            return main_post_tool_use(data)
        return main_user_prompt_submit(data)
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
