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


def test_章程使用的命令都在白名单里放行() -> None:
    text = charter.read_charter(REPO_ROOT)
    prefixes = _bash_command_prefixes()
    # ⚠️ 与 `HEADLESS_ARGV_FIXED_PART` 的 I4 修复对齐：`unpack-signal` 子命令的
    # 唯一放行前缀是带 `PYTHONPATH=. tools/liaison/.venv/bin/python` 的完整调法
    # （裸 `python -m tools.liaison unpack-signal` 匹配不上，会话会卡在权限确认
    # 上），因此章程正文也必须逐字用这个完整调法（见本文件对 SKILL.md 的同步改动）。
    required_uses = [
        "PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal",
        "python -m tools.liaison criteria",
        "git add",
        "git commit",
        "git status",
        "git diff",
        "git log",
    ]
    for use in required_uses:
        assert any(use.startswith(p) or p.startswith(use) for p in prefixes), (
            f"章程要求执行的命令 `{use}` 未被 HEADLESS_ARGV_FIXED_PART 放行，会话会卡住而不报错：{use}"
        )
        assert use in text, f"章程正文里找不到命令 `{use}` 的用途说明（须逐字出现，措辞需与放行前缀对齐）"


def test_白名单放行的每条命令章程里都有用途() -> None:
    text = charter.read_charter(REPO_ROOT)
    for prefix in _bash_command_prefixes():
        # 命令前缀里挑最具辨识度的最后一段关键词做子串核对（如 "unpack-signal"、"criteria"、"git add"）
        keyword = prefix.split(" ")[-1] if "tools.liaison" in prefix else prefix
        assert keyword in text, f"白名单放行的命令 `{prefix}` 在章程里找不到用途说明（关键词 `{keyword}` 缺失）"
