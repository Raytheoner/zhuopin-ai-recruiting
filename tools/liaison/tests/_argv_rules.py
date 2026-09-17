"""测试共用：把 `dispatch.HEADLESS_ARGV_FIXED_PART` 切成 allow / deny 两组规则，
并按 Claude Code CLI 实测的 `Bash(...)` 规则语义做匹配。

TD-48（0917O）之后 argv 里同时有 `--allowedTools` 与 `--disallowedTools`，扫描
「放行了什么」的测试必须只看 allow 段——deny 段里出现 `git push` 是**期望的**，
混在一起扫会把 deny 当成放行。⛔ 只给测试用，不是生产代码。

TD-50（0917Q，2026-09-17 一次性仓实测，claude 2.1.263）的 `Bash` 规则语义：
- `Bash(X:*)` 是**词边界**前缀：命中 `X` 本身或 `X ` ＋任意后续；`X` 后面紧跟非空格
  字符不算（`Bash(git add notes/:*)` 命中不了 `git add notes/x.md`——TD-50 根因）。
- `Bash(<含 * 的模式>)` 是通配：`*` 匹配任意字符串，**含空格**
  （`Bash(git add docs/跟进信/回件/*)` 同时命中 `git add docs/跟进信/回件/a.md`
  与 `git add docs/跟进信/回件/a.md docs/session接力.md`）。
- 引号不做归一化：`git add "docs/x.md"` 与 `git add docs/x.md` 是两条不同命令。
"""

from __future__ import annotations

import re

from tools.liaison.unpack.dispatch import HEADLESS_ARGV_FIXED_PART

_BASH_RULE = re.compile(r"^Bash\((.*)\)$")


def split_tool_rules() -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    disallowed: list[str] = []
    bucket: list[str] | None = None
    for tok in HEADLESS_ARGV_FIXED_PART:
        if tok == "--allowedTools":
            bucket = allowed
            continue
        if tok == "--disallowedTools":
            bucket = disallowed
            continue
        if tok.startswith("--"):
            bucket = None
            continue
        if bucket is not None:
            bucket.append(tok)
    return allowed, disallowed


def bash_rule_body(rule: str) -> str | None:
    """`Bash(<body>)` ⇒ `<body>`；不是 Bash 路径规则 ⇒ None（裸 `Bash` 也算 None，
    本项目 argv 里不该出现裸 `Bash`）。"""
    m = _BASH_RULE.match(rule)
    return m.group(1) if m else None


def bash_rule_prefix(rule: str) -> str | None:
    """规则的命令前缀本体：`Bash(git add docs/x.md:*)` ⇒ `git add docs/x.md`，
    `Bash(git add docs/x/*)` ⇒ `git add docs/x/`。"""
    body = bash_rule_body(rule)
    if body is None:
        return None
    if body.endswith(":*"):
        return body[:-2]
    if body.endswith("*"):
        return body[:-1]
    return body


def bash_rule_matches(rule: str, command: str) -> bool:
    """按上面文档里实测的语义判断 `rule` 是否命中 `command`。"""
    body = bash_rule_body(rule)
    if body is None:
        return False
    if body.endswith(":*"):
        prefix = body[:-2]
        return command == prefix or command.startswith(prefix + " ")
    if "*" in body:
        pattern = ".*".join(re.escape(part) for part in body.split("*"))
        return re.fullmatch(pattern, command, flags=re.DOTALL) is not None
    return command == body


def allowed_bash_prefixes() -> list[str]:
    allowed, _ = split_tool_rules()
    return [p for tok in allowed if (p := bash_rule_prefix(tok)) is not None]


def bash_command_verdict(command: str) -> str:
    """模拟 CLI 对一条 Bash 命令的裁决：deny 优先 ⇒ "denied"；否则有 allow 命中 ⇒
    "allowed"；都没有 ⇒ "needs_approval"（无头会话里等价于被拒，原文
    「This command requires approval」）。"""
    allowed, disallowed = split_tool_rules()
    if any(bash_rule_matches(r, command) for r in disallowed):
        return "denied"
    if any(bash_rule_matches(r, command) for r in allowed):
        return "allowed"
    return "needs_approval"
