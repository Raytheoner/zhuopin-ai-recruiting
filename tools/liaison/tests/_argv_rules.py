"""测试共用：把 `dispatch.HEADLESS_ARGV_FIXED_PART` 切成 allow / deny 两组规则。

TD-48（0917O）之后 argv 里同时有 `--allowedTools` 与 `--disallowedTools`，扫描
「放行了什么」的测试必须只看 allow 段——deny 段里出现 `git push` 是**期望的**，
混在一起扫会把 deny 当成放行。⛔ 只给测试用，不是生产代码。
"""

from __future__ import annotations

import re

from tools.liaison.unpack.dispatch import HEADLESS_ARGV_FIXED_PART

#: `Bash(<命令前缀>:*)` 形式的规则，取冒号前的命令前缀本体（如 `git add docs/x/`）。
BASH_RULE = re.compile(r"Bash\(([^:]+):\*\)")


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


def allowed_bash_prefixes() -> list[str]:
    allowed, _ = split_tool_rules()
    return [m.group(1) for tok in allowed if (m := BASH_RULE.match(tok)) is not None]
