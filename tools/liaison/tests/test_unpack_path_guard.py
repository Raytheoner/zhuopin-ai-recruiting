"""TD-48（0917O）：拆件会话的编辑与 `git add` 在**权限层**按章程 §三 路径收窄。

背景：`acceptEdits` 对文件编辑不按路径限制，此前红线 ②④⑥⑧ 只在提示词层守，
`0917K`/`0917N` 三次实测子会话都自发改写并提交了 `docs/session接力.md`。
2026-09-17 一次性仓实验（写法 乙：`acceptEdits` ＋ 路径限定 allow ＋ 显式
`--disallowedTools` deny）证实 a(改 tools/x.py)/c(git add -A)/e(git commit -a)
被拒、b/d/f 放行；写法 甲（只收窄 allow、不加 deny）下 a 仍被 acceptEdits 放行。

TD-50（0917Q）：首次真实回件（`20260917T014544528242Z.log`）会话 `git add` 三个章程
路径被判「This command requires approval」。一次性仓实测定因：`Bash(X:*)` 是**词边界**
前缀，`Bash(git add docs/跟进信/回件/:*)` 命中不了 `git add docs/跟进信/回件/a.md`
（目录规则从未生效），引号写法 `git add "…"` 与 `-- …` 也各是不同前缀。修法＝目录
用 `Bash(git add <dir>*)` 通配、文件用 `Bash(git add <file>:*)`，各加 `"…"`／`-- `
两种写法变体；`Write(...)` 规则被 CLI 判「not matched by file permission checks」，
整段删掉（`Edit(path)` 规则覆盖全部编辑工具，含 Write）。

⚠️ 唯一真源仍是 `dispatch.HEADLESS_ARGV_FIXED_PART`；§三 的路径清单从章程正本
（SKILL.md）**解析**出来，⛔ 本文件不另抄一份路径字面量。
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.liaison.tests._argv_rules import bash_command_verdict, split_tool_rules as _split_flags
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


def test_章程三节列出的每个路径都在Edit与git_add放行里() -> None:
    allowed, _ = _split_flags()
    edit_patterns = [m.group(2) for tok in allowed if (m := _RULE.match(tok)) and m.group(1) == "Edit"]
    for path in _charter_section_three_paths():
        assert any(_rule_covers(p, path) for p in edit_patterns), f"章程 §三 路径 `{path}` 没有对应的 Edit 放行：{edit_patterns}"
        sample = f"{path}a.md" if path.endswith("/") else path
        assert bash_command_verdict(f"git add {sample}") == "allowed", (
            f"章程 §三 路径 `{path}` 的 `git add {sample}` 没被放行：{allowed}"
        )


def test_TD50_argv里不再有Write规则() -> None:
    """CLI 告警原文：`Permission allow rule (--allowed-tools): Write(docs/跟进信/回件/**)
    is not matched by file permission checks — only Edit(path) rules are. Use
    Edit(docs/跟进信/回件/**) instead (Edit rules cover all file-editing tools).`
    deny 段同样告警。Write 规则既不生效又占 argv，整段删。"""
    assert not [tok for tok in HEADLESS_ARGV_FIXED_PART if tok.startswith("Write(")], HEADLESS_ARGV_FIXED_PART
    assert "Write" not in HEADLESS_ARGV_FIXED_PART


def test_TD50_目录路径不用词边界前缀写法() -> None:
    """`Bash(git add <dir>/:*)` 永远命中不了 `git add <dir>/<文件>`（`:*` 要求前缀后
    紧跟空格或结束）——这正是 TD-50 根因，⛔ 不许再出现。"""
    allowed, _ = _split_flags()
    for path in _charter_section_three_paths():
        if path.endswith("/"):
            assert f"Bash(git add {path}:*)" not in allowed, f"目录 `{path}` 仍用词边界前缀写法"


def test_TD50_章程路径的git_add四种写法都放行() -> None:
    """一次性仓 g1–g4 ＋ h 轮实测：裸路径／双引号／两个路径／`--` 四种写法都要放行，
    目录下的文件也要放行。"""
    paths = _charter_section_three_paths()
    samples = [f"{p}人事部#1-20260917.md" if p.endswith("/") else p for p in paths]
    for sample in samples:
        for cmd in (
            f"git add {sample}",
            f'git add "{sample}"',
            f"git add -- {sample}",
            f'git add -- "{sample}"',
        ):
            assert bash_command_verdict(cmd) == "allowed", cmd
    # 多路径：首个路径决定命中（h2/h4 实测），任一章程路径打头都放行
    for first in samples:
        cmd = "git add " + " ".join([first, *(s for s in samples if s != first)])
        assert bash_command_verdict(cmd) == "allowed", cmd
    quoted = "git add " + " ".join(f'"{s}"' for s in samples)
    assert bash_command_verdict(quoted) == "allowed", quoted


def test_TD50_越界git_add仍被拒() -> None:
    for cmd in (
        "git add -A", "git add .", "git add tools/x.py", "git add docs/openers/x.md",
        "git add docs/tech-debt.md", "git add CLAUDE.md", "git add .env", "git add data/x",
        'git add "tools/x.py"', "git add -- tools/x.py",
        # 尾随越界：章程路径打头、红线目录跟在后面（并行泳道的未提交改动会被卷走）
        "git add docs/session接力.md tools/x.py",
        'git add docs/session接力.md "tools/x.py"',
        "git add docs/跟进信/回件/a.md docs/openers/x.md",
        "git add docs/跟进信/回件/a.md .claude/settings.json",
    ):
        assert bash_command_verdict(cmd) != "allowed", cmd


def test_红线目录都在disallowedTools里() -> None:
    """写法 乙：红线 ②④⑤⑧ 涉及的目录与文件（含 opener 目录与 data/）对 Edit
    显式 deny（Edit 规则覆盖 Write，TD-50 起不再单列 Write），git 的整体性动作也 deny；
    TD-50 起 `git add` 尾随红线目录也 deny（通配 `Bash(git add *<dir>/*)`）。"""
    _, disallowed = _split_flags()
    for root in (
        "tools/**", "app/**", "scripts/**", "tests/**", "openspec/**", ".claude/**",
        "CLAUDE.md", "data/**", ".env*", "docs/openers/**",
    ):
        assert f"Edit({root})" in disallowed, f"缺 deny：Edit({root})"
    for cmd in ("git add -A", "git add .", "git commit -a", "git stash", "git push"):
        assert f"Bash({cmd}:*)" in disallowed, f"缺 deny：Bash({cmd}:*)"


def test_send_followup与git_push仍不在放行里() -> None:
    allowed, _ = _split_flags()
    joined = " ".join(allowed)
    assert "send-followup" not in joined
    assert "git push" not in joined
    assert "--dangerously-skip-permissions" not in HEADLESS_ARGV_FIXED_PART
