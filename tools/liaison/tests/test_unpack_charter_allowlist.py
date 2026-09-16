"""章程用命与 D4 `--allowedTools` 白名单双向核对（design D4，spec 要求「拆件
会话以受限权限启动」不得因措辞不对齐而卡死等不到的权限确认）。

⚠️ **唯一真源是 `dispatch.HEADLESS_ARGV_FIXED_PART`**——本文件不重抄一份白名单
字面量，只扫描该常量里 `Bash(<前缀>:*)` 形状的条目。
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.liaison.unpack import charter
from tools.liaison.unpack.dispatch import HEADLESS_ARGV_FIXED_PART

REPO_ROOT = Path(__file__).resolve().parents[3]

#: `HEADLESS_ARGV_FIXED_PART` 里 `Bash(<命令前缀>:*)` 形式的白名单项，取冒号前的
#: 命令前缀本体（如 `git add`、`PYTHONPATH=. tools/liaison/.venv/bin/python -m
#: tools.liaison unpack-signal`）。
_BASH_PATTERN = re.compile(r"Bash\(([^:]+):\*\)")


def _bash_command_prefixes() -> list[str]:
    return [
        match.group(1)
        for tok in HEADLESS_ARGV_FIXED_PART
        if (match := _BASH_PATTERN.match(tok)) is not None
    ]


#: ⚠️ 与 `HEADLESS_ARGV_FIXED_PART` 的 I4 修复对齐：`unpack-signal` 子命令的
#: 唯一放行前缀是带 `PYTHONPATH=. tools/liaison/.venv/bin/python` 的完整调法
#: （裸 `python -m tools.liaison unpack-signal` 匹配不上，会话会卡在权限确认
#: 上），因此章程正文与 `compute_prompt` 的前言也都必须逐字用这个完整调法
#: （见本文件对 SKILL.md 与 charter.py 前言的同步改动）。
_REQUIRED_USES = [
    "PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal",
    "PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison criteria",
    "git add",
    "git commit",
    "git status",
    "git diff",
    "git log",
]


def test_章程使用的命令都在白名单里放行() -> None:
    text = charter.read_charter(REPO_ROOT)
    prefixes = _bash_command_prefixes()
    for use in _REQUIRED_USES:
        assert any(use.startswith(p) or p.startswith(use) for p in prefixes), (
            f"章程要求执行的命令 `{use}` 未被 HEADLESS_ARGV_FIXED_PART 放行，会话会卡住而不报错：{use}"
        )
        assert use in text, f"章程正文里找不到命令 `{use}` 的用途说明（须逐字出现，措辞需与放行前缀对齐）"


def test_compute_prompt输出里命令也在白名单里放行() -> None:
    # 实际无头会话收到的是 compute_prompt() 的拼接结果（前言 + 章程正文），
    # 不是单独的章程正文——前言里也有一条会被会话真正执行的命令
    # （`unpack-signal --probe` 的循环探测），必须同样对齐白名单前缀，
    # 否则前言与章程各自修过一处、漏了另一处也测不出来。
    charter_text = charter.read_charter(REPO_ROOT)
    prompt = charter.compute_prompt(
        letter_number="第 001 号",
        msgid="<test@example.com>",
        signal_relpath="var/liaison/signal.json",
        checkpoint_iso="2026-09-10T00:00:00+08:00",
        charter_text=charter_text,
    )
    for use in _REQUIRED_USES:
        assert use in prompt, (
            f"compute_prompt 输出里找不到命令 `{use}` 的用途说明（须逐字出现，措辞需与放行前缀对齐）"
        )

    # ⚠️ 仅查整段 prompt 子串不够：章程正文（SKILL.md）已经是修好的新写法，
    # 会把 unpack-signal 的正确调法一起带进 prompt，即使前言自己单独回退成旧的
    # 裸调用（`python -m tools.liaison unpack-signal --probe`），上面逐条子串
    # 检查也会因为章程正文那份而假阳性通过，测不出前言自己的回归——这正是
    # charter.py:55 曾经出现过的那个 bug（章程正文已修、前言忘了同步）。
    # 因此额外把前言单独切出来（compute_prompt 是 preamble + charter_text 的
    # 逐字拼接，用 charter_text 的长度从尾部切掉即可还原前言），命令必须直接
    # 出现在前言里，不能靠章程正文兜底。
    assert prompt.endswith(charter_text), (
        "compute_prompt 未把 charter_text 原样逐字拼在末尾，无法切出前言部分核对"
    )
    preamble = prompt[: len(prompt) - len(charter_text)]
    unpack_signal_use = next(use for use in _REQUIRED_USES if "unpack-signal" in use)
    assert unpack_signal_use in preamble, (
        f"compute_prompt 的前言部分找不到 `{unpack_signal_use}`——前言里的探测命令写法"
        "回退成了旧的裸调用形式，即便章程正文仍是新写法，也会让无头会话在权限确认"
        "上静默卡住"
    )


def test_白名单放行的每条命令章程里都有用途() -> None:
    text = charter.read_charter(REPO_ROOT)
    for prefix in _bash_command_prefixes():
        # 命令前缀里挑最具辨识度的最后一段关键词做子串核对（如 "unpack-signal"、"criteria"、"git add"）
        keyword = prefix.split(" ")[-1] if "tools.liaison" in prefix else prefix
        assert keyword in text, f"白名单放行的命令 `{prefix}` 在章程里找不到用途说明（关键词 `{keyword}` 缺失）"
