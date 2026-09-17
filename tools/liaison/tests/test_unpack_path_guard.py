"""TD-48（0917O）：拆件会话的编辑与 `git add` 在**权限层**按章程 §三 路径收窄。

背景：`acceptEdits` 对文件编辑不按路径限制，此前红线 ②④⑥⑧ 只在提示词层守，
`0917K`/`0917N` 三次实测子会话都自发改写并提交了 `docs/session接力.md`。
2026-09-17 一次性仓实验（写法 乙：`acceptEdits` ＋ 路径限定 allow ＋ 显式
`--disallowedTools` deny）证实 a(改 tools/x.py)/c(git add -A)/e(git commit -a)
被拒、b/d/f 放行；写法 甲（只收窄 allow、不加 deny）下 a 仍被 acceptEdits 放行。

⚠️ 唯一真源仍是 `dispatch.HEADLESS_ARGV_FIXED_PART`；§三 的路径清单从章程正本
（SKILL.md）**解析**出来，⛔ 本文件不另抄一份路径字面量。
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.liaison.tests._argv_rules import split_tool_rules as _split_flags
from tools.liaison.unpack import charter
from tools.liaison.unpack.dispatch import HEADLESS_ARGV_FIXED_PART

REPO_ROOT = Path(__file__).resolve().parents[3]

_RULE = re.compile(r"^(Read|Edit|Write|Glob|Grep|Bash)(?:\((.*)\))?$")


def _charter_section_three_paths() -> list[str]:
    """从章程正本 §三 「只 `git add` 本轮明确写入的路径（…）」里解析出路径清单。
    尾部的 `…` 表示目录下任意文件，解析时去掉。"""
    text = charter.read_charter(REPO_ROOT)
    start = text.index("## §三")
    end = text.index("## §四", start)
    section = text[start:end]
    line = next(l for l in section.splitlines() if "只 `git add` 本轮明确写入的路径" in l)
    # 括号可能跨行——把从该行起到「）」为止的文本一起拿来解析
    tail = section[section.index(line):]
    tail = tail[: tail.index("）") + 1]
    paths = [p.rstrip("…") for p in re.findall(r"`(docs/[^`]+)`", tail)]
    assert len(paths) >= 4, f"章程 §三 路径清单解析异常：{paths}"
    return paths


def _rule_covers(rule_pattern: str, path: str) -> bool:
    """`Edit(<pattern>)` 的 pattern 是否覆盖章程路径：目录路径要求 pattern 是
    `<dir>**`；文件路径要求逐字相等。"""
    if path.endswith("/"):
        return rule_pattern == path + "**"
    return rule_pattern == path


def test_argv_不再放行裸的Edit_Write_git_add_git_commit() -> None:
    allowed, _ = _split_flags()
    for bare in ("Edit", "Write", "Bash(git add:*)", "Bash(git commit:*)"):
        assert bare not in allowed, f"`{bare}` 未按路径收窄，仍是裸放行：{allowed}"


def test_章程三节列出的每个路径都在Edit_Write与git_add放行里() -> None:
    allowed, _ = _split_flags()
    edit_patterns = [m.group(2) for tok in allowed if (m := _RULE.match(tok)) and m.group(1) == "Edit"]
    write_patterns = [m.group(2) for tok in allowed if (m := _RULE.match(tok)) and m.group(1) == "Write"]
    add_prefixes = [
        m.group(2)[len("git add ") : -len(":*")]
        for tok in allowed
        if (m := _RULE.match(tok)) and m.group(1) == "Bash" and (m.group(2) or "").startswith("git add ")
    ]
    for path in _charter_section_three_paths():
        assert any(_rule_covers(p, path) for p in edit_patterns), f"章程 §三 路径 `{path}` 没有对应的 Edit 放行：{edit_patterns}"
        assert any(_rule_covers(p, path) for p in write_patterns), f"章程 §三 路径 `{path}` 没有对应的 Write 放行：{write_patterns}"
        assert path in add_prefixes, f"章程 §三 路径 `{path}` 没有对应的 `git add` 前缀放行：{add_prefixes}"


def test_红线目录都在disallowedTools里() -> None:
    """写法 乙：红线 ②④⑤⑧ 涉及的目录与文件（含 opener 目录与 data/）对 Edit 与
    Write 都显式 deny，git 的四个整体性动作也 deny。"""
    _, disallowed = _split_flags()
    for root in (
        "tools/**", "app/**", "scripts/**", "tests/**", "openspec/**", ".claude/**",
        "CLAUDE.md", "data/**", ".env*", "docs/openers/**",
    ):
        assert f"Edit({root})" in disallowed, f"缺 deny：Edit({root})"
        assert f"Write({root})" in disallowed, f"缺 deny：Write({root})"
    for cmd in ("git add -A", "git add .", "git commit -a", "git stash", "git push"):
        assert f"Bash({cmd}:*)" in disallowed, f"缺 deny：Bash({cmd}:*)"


def test_send_followup与git_push仍不在放行里() -> None:
    allowed, _ = _split_flags()
    joined = " ".join(allowed)
    assert "send-followup" not in joined
    assert "git push" not in joined
    assert "--dangerously-skip-permissions" not in HEADLESS_ARGV_FIXED_PART
