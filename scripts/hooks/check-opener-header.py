#!/usr/bin/env python3
"""Claude Code `Stop` hook —— 拦截缺少 `[Mac]MMDDX-<主题短名>` 标题行的指令代码块。

为什么需要机器校验：强制格式这条规则本来就写在 CLAUDE.md 里、每会话自动加载，
但 2026-08-26 实测仍然整整一个会话（八份指令）全部漏掉——失效方式是自我豁免
「我这只是会话中途让他去跑条命令，不算 Opener」。规则文本防不住这种漏法，
因为漏的人当时并不觉得自己在违规。所以加一道机器判据。

判据（保守，宁可漏报不误报）——两条，各自独立报错：
  ① 代码块有「指令特征」（含【设置】，或同时含 界面：与 Session：）却无 `[Mac]MMDDX-` 标题行
  ② 代码块的【设置】写着「执行环境: CC」却没有 set_session_title 那一行

判据②的由来（2026-08-27，企业AI转型侧正本 §〇.0 补充三）：
  🔴 **标题行不会自动变成 session 名。** 不显式调 set_session_title，Claude Code 就用
  Haiku 读首条消息现写一句摘要当标题 —— 与首行是什么无关。
  那边把这条规则定下的**当天就被违反 17 次**（OP-0826-D/E/F/G/H/J/K/L/P/Q/R/S/T/U/V、
  0827-A/B/C 全都漏了那一行），根因不是没载体——规则就写在起草者当天亲手编辑过两次的
  文件里、位置在正上方 40 行。⇒ 典型的「读到规则 ≠ 执行规则」，只能上机器判据。
  那边的处置建议原文就是「给 opener 集加一条 lint」，本 hook 即该 lint 在本仓库的实现。

  ⚠️ 三条被实测推翻的假说，别再提出来（每条都是"给一个看起来合理的自动机制去解释
  一个根本没有自动机制的现象"）：
    · 「长度超限会吃掉编号」—— 32 字符的标题实测完整保留，未截断
    · 「Desktop 有时保留编号有时丢」—— 那几条带号的是人手工补的
    · 「摘要模型会碰巧保留编号 token」—— 同上，也是手工补的。
      🔴 Shao Peishen 2026-08-27 原话：「已有编号基本都是我手工后加的，几乎很少概率实现」。
      正本里那边的原话是「U/V 不是靠运气，是我无法分别 session，及时手工加的」。
      ⇒ **自动带上编号的概率是零，不是"有时"。** 唯一的实现路径就是显式调这个工具。
  ⚠️ Cowork 侧没有这个工具（实测只有 list_sessions/read_transcript/Task*），
  它的 session 名取自**开场词首行**，格式是「语义在前、编号在后」，与 CC 侧相反。
  故判据②只对写着「执行环境: CC」的块生效。

2026-08-27 格式改版（对齐 Win 端）：`【OP-0826-A】CC · 任务名` → `[Mac]0827A-任务名`。
去掉的两截都是零信息量：`OP-` 前缀谁都有、`CC ·` 在 Code tab 下条条都是。
省下的字符让侧边栏能多显示几个字的主题——session 名的唯一用途就是一眼认出是哪条。
`-` 是 id 与主题的唯一分隔符（id 内不含 `-`）：主题以 ASCII 开头时（如 `audit U1计划`）
空格分隔会与 id 粘连读岔，这是弃用空格的原因。
`[Mac]` 标的是**执行机**：Win 笔记本与 Mac 两台都在跑，启用 Remote Control
（从 claude.ai / App 控制 Claude Code）后，那一侧要靠它区分这条 session 在哪台机器上。
Win 端对应 `[Win]`。
旧格式仍放行（历史 opener 不追改），见 LEGACY_HEADER。

行为：
  exit 0  正常放行
  exit 2  stderr 反馈给 Claude，它会自行补上头两行重发
  任何异常一律 exit 0（fail open）——hook 自己坏掉不该挡住干活

配置见 .claude/settings.json 的 hooks.Stop。
"""

from __future__ import annotations

import json
import re
import sys

# 现行格式：[Mac]0827A-<主题短名> ／ 模板占位 [Mac]MMDDX-<主题短名>
OPENER_HEADER = re.compile(r"^\[Mac\][0-9A-Za-z]+-\S", re.MULTILINE)
# 历史格式，仍放行（不追改）：[Mac] 0820-10 <主题> ／ 【OP-0826-A】...
LEGACY_HEADER = re.compile(
    r"^\[Mac\]\s+[0-9A-Za-z]+-[0-9A-Za-z]+\s+\S|【OP-[0-9A-Za-z]+-[0-9A-Za-z]+】",
    re.MULTILINE,
)
FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

# 判据②：CC 的 opener 必须带 set_session_title
IS_CC_BLOCK = re.compile(r"【设置】[^\n]*执行环境\s*[:：]\s*CC")
HAS_SET_TITLE = re.compile(r"set_session_title")

# 指令特征：命中任一即认为这块是"要他去别处执行的东西"
HAS_SETTING_LINE = re.compile(r"【设置】")
HAS_LEGACY_FOUR_LINE = re.compile(r"界面\s*[:：].*\n(?:.*\n)?\s*Session\s*[:：]", re.MULTILINE)


def last_assistant_text(transcript_path: str) -> str:
    """取末条 assistant 消息的纯文本。转录是 JSONL，逐行解析，坏行跳过。"""
    chunks: list[str] = []
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text"
            ]
            if parts:
                chunks = parts  # 只保留最后一条 assistant 的文本
    return "\n".join(chunks)


def offending_blocks(text: str) -> tuple[list[str], list[str]]:
    """返回 (缺标题行的块, CC 块里缺 set_session_title 的块)。"""
    no_header, no_title_call = [], []
    for body in FENCED_BLOCK.findall(text):
        looks_like_instruction = bool(HAS_SETTING_LINE.search(body)) or bool(
            HAS_LEGACY_FOUR_LINE.search(body)
        )
        if not looks_like_instruction:
            continue
        first = body.strip().splitlines()[0] if body.strip() else "(空块)"
        if not (OPENER_HEADER.search(body) or LEGACY_HEADER.search(body)):
            no_header.append(first)
        # 判据②：只判 CC 块；Cowork 没有这个工具
        if IS_CC_BLOCK.search(body) and not HAS_SET_TITLE.search(body):
            no_title_call.append(first)
    return no_header, no_title_call


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    # 已经因为本 hook 停过一次，不再拦，避免无限循环
    if payload.get("stop_hook_active"):
        return 0

    transcript = payload.get("transcript_path")
    if not transcript:
        return 0

    try:
        text = last_assistant_text(transcript)
    except Exception:
        # fail open：转录读不动（不存在／权限／编码坏）一律放行，hook 自己坏掉不该挡住干活
        return 0

    no_header, no_title_call = offending_blocks(text)
    if not no_header and not no_title_call:
        return 0

    msgs = []

    if no_header:
        msgs.append(
            "⛔ 指令代码块缺少 `[Mac]MMDDX-<主题短名>` 标题行"
            "（CLAUDE.md「给 Paul 任务指令时的强制格式」）。\n"
            f"命中 {len(no_header)} 块，首行分别是：\n"
            + "\n".join(f"  - {b[:60]}" for b in no_header)
            + "\n\n"
            "「这只是中途一条命令、不算 Opener」不是豁免。判据是：这段会不会被复制到\n"
            "另一个界面去？会，就必须带头两行，且两行都写在代码块内部。\n"
            "  [Mac]MMDDX-<主题短名>\n"
            "  【设置】执行环境: <CC/Cowork> ｜ Session: … ｜ 分支: … ｜ worktree: … ｜ 工作区: …\n"
            "MMDD 取中国时间当天（TZ=Asia/Shanghai date +%m%d）。"
        )

    if no_title_call:
        msgs.append(
            "⛔ CC 的 opener 缺少 set_session_title 那一行。\n"
            f"命中 {len(no_title_call)} 块，首行分别是：\n"
            + "\n".join(f"  - {b[:60]}" for b in no_title_call)
            + "\n\n"
            "🔴 标题行**不会**自动变成 session 名——不显式设置，Claude Code 就用 Haiku\n"
            "读首条消息现写一句摘要当标题，与首行是什么无关。侧边栏因此丢编号，\n"
            "而编号是跨会话对账时定位「这是哪件任务」的唯一抓手。\n\n"
            "在【设置】行下面补这一行（照抄，session_id 传字面量 self）：\n"
            "  开工第一件事：调 mcp__ccd_session_mgmt__set_session_title\n"
            "  （session_id 传字面量 \"self\"），标题：[Mac]MMDDX-<主题短名>\n\n"
            "⚠️ 已在跑的 session 可补救，对它说一句同样的话即可，不必重开。\n"
            "⚠️ 这条只对 CC 生效；Cowork 侧没有该工具，它的名字取自开场词首行。"
        )

    print("\n\n———\n\n".join(msgs), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
