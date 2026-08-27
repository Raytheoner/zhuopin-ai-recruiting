#!/usr/bin/env python3
"""Claude Code `Stop` hook —— 拦截缺少 `[Mac]MMDDX-<主题短名>` 标题行的指令代码块。

为什么需要机器校验：强制格式这条规则本来就写在 CLAUDE.md 里、每会话自动加载，
但 2026-08-26 实测仍然整整一个会话（八份指令）全部漏掉——失效方式是自我豁免
「我这只是会话中途让他去跑条命令，不算 Opener」。规则文本防不住这种漏法，
因为漏的人当时并不觉得自己在违规。所以加一道机器判据。

判据（保守，宁可漏报不误报）：
  代码块里出现「指令特征」（含【设置】，或同时含 界面：与 Session：）
  但整块找不到 `[Mac]MMDDX-` 标题行 → 报错

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


def offending_blocks(text: str) -> list[str]:
    bad = []
    for body in FENCED_BLOCK.findall(text):
        looks_like_instruction = bool(HAS_SETTING_LINE.search(body)) or bool(
            HAS_LEGACY_FOUR_LINE.search(body)
        )
        if not looks_like_instruction:
            continue
        if OPENER_HEADER.search(body) or LEGACY_HEADER.search(body):
            continue
        bad.append(body.strip().splitlines()[0] if body.strip() else "(空块)")
    return bad


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

    bad = offending_blocks(text)
    if not bad:
        return 0

    print(
        "⛔ 指令代码块缺少 `[Mac]MMDDX-<主题短名>` 标题行 + 【设置】行"
        "（CLAUDE.md「给 Paul 任务指令时的强制格式」）。\n"
        f"命中 {len(bad)} 块，首行分别是：\n"
        + "\n".join(f"  - {b[:60]}" for b in bad)
        + "\n\n"
        "「这只是中途一条命令、不算 Opener」不是豁免。判据是：这段会不会被复制到\n"
        "另一个界面去？会，就必须带头两行，且两行都写在代码块内部。\n\n"
        "请整块重发（不要只补一个片段），格式：\n"
        "  [Mac]MMDDX-<主题短名>\n"
        "  【设置】执行环境: <CC/Cowork> ｜ Session: <新开/利旧+理由> ｜ 分支: <main/worktree 分支> "
        "｜ worktree: <❌ 不勾/☑ 勾选>（<理由>）｜ 工作区: <绝对路径>\n\n"
        "MMDD 取中国时间当天（TZ=Asia/Shanghai date +%m%d）。\n"
        "主题短名要短——这一行就是侧边栏的 session 名，唯一用途是让他一眼认出是哪条；\n"
        "⛔ 不要写 `OP-` 前缀，也不要写 `CC ·`（Code tab 下条条都是 CC，零分辨力）。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
