"""拆件受限会话的 Codex PreToolUse 路径级 deny hook（0930A）。

把 Claude 侧「Edit(path) 白名单／deny」补成 Codex 原生 PreToolUse hook。本脚本读
stdin 的 PreToolUse 事件 JSON，只在 `HR_LIAISON_UNPACK=1` 时拦截；未置位时静默放行
（exit 0、无输出），⛔ 不影响任何正常会话。

拦截契约（opener 0930A §三 钉死）：
- deny：把拦截 JSON 打到 stdout、exit 0（Codex 手册：stdout JSON 的
  `hookSpecificOutput.permissionDecision == "deny"` 即拦截；plain text 被忽略）。
- allow：stdout 无输出、exit 0。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: 仓库根 = 本文件（scripts/hooks/）上两级。用文件自身定位而非 cwd——手册建议
#: repo 内 hook 用 git root 稳定定位（Codex 可能从仓库子目录起活）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: 白名单唯一真源（⛔ 不抄第二份）：章程 §三 允许拆件会话写入并 `git add` 的路径。
from tools.liaison.unpack.dispatch import CHARTER_WRITABLE_PATHS  # noqa: E402

#: 守卫开关环境变量名（与 `dispatch.UNPACK_GUARD_ENV` 同值，⛔ 只在 dispatch 写死一次）。
GUARD_ENV = "HR_LIAISON_UNPACK"

#: 编辑类工具名。Codex 手册：文件编辑统一以 `apply_patch` 上报（matcher 别名可用
#: apply_patch/Edit/Write）；`Edit`/`Write` 若以独立工具名出现（兼容旧版/抽象事件），
#: 参数键是 `tool_input.file_path`。
_EDIT_TOOLS = frozenset({"apply_patch", "Edit", "Write"})

#: apply_patch patch 文本携带目标路径的行头（Add/Update/Delete/Move 都要看）。
_PATCH_PATH_LINE = re.compile(
    r"^\*\*\*\s*(?:Add|Update|Delete)\s+File:\s*(\S.*?)\s*$"
    r"|^\*\*\*\s*Move\s+to:\s*(\S.*?)\s*$"
)

#: 红线整体性 git 动作（与 `dispatch.HEADLESS_DENY_BASH_PREFIXES` 同一份语义）。
_DENY_BASH_SUBSTRINGS = (
    "git add -A",
    "git add .",
    "git commit -a",
    "git stash",
    "git push",
)


def _writable_path(path: str, cwd: str) -> bool:
    """目标路径是否落在章程 §三 白名单内。

    绝对路径先相对化到 `cwd`（拆件会话 cwd＝仓库根），不在 `cwd` 下 ⇒ 越界 deny。
    归一化后仍以 `../` 开头的（越过仓库根）一律 deny。目录项（以 `/` 结尾）按前缀
    匹配：`docs/跟进信/回件/x.md` 命中、`docs/跟进信/回件.md` 不命中。
    """
    p = path.strip()
    if not p:
        return False
    raw = Path(p)
    if raw.is_absolute():
        try:
            rel = raw.relative_to(Path(cwd))
        except ValueError:
            return False
    else:
        rel = raw
    norm = os.path.normpath(str(rel))
    if norm == ".." or norm.startswith(".." + os.sep):
        return False
    if norm in ("", "."):
        return False
    for allowed in CHARTER_WRITABLE_PATHS:
        if allowed.endswith("/"):
            if norm.startswith(allowed) or norm == allowed.rstrip("/"):
                return True
        elif norm == allowed:
            return True
    return False


def _apply_patch_paths(command: str) -> list[str]:
    """从 apply_patch 的 patch 文本里解析出目标路径（Add/Update/Delete/Move）。"""
    paths: list[str] = []
    for line in command.splitlines():
        m = _PATCH_PATH_LINE.match(line.strip())
        if m:
            paths.append((m.group(1) or m.group(2)).strip())
    return paths


def _deny(reason: str) -> None:
    """把拦截 JSON 打到 stdout（exit 0 由调用方控制）。"""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    if os.environ.get(GUARD_ENV) != "1":
        return 0

    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        event = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = event.get("cwd") or str(_REPO_ROOT)

    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        for bad in _DENY_BASH_SUBSTRINGS:
            if bad in command:
                _deny(
                    "拆件会话禁止整体性 git 动作（章程红线）：命中「"
                    + bad
                    + "」；命令："
                    + command
                )
                return 0
        return 0

    if tool_name in _EDIT_TOOLS:
        if tool_name == "apply_patch":
            patch_text = tool_input.get("command") or ""
            paths = _apply_patch_paths(patch_text)
            bad = [p for p in paths if not _writable_path(p, cwd)]
            if bad:
                _deny(
                    "拆件会话只能写章程 §三 白名单路径（真源 "
                    "tools.liaison.unpack.dispatch.CHARTER_WRITABLE_PATHS）；越界目标："
                    + ", ".join(bad)
                )
            return 0
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        if file_path and not _writable_path(file_path, cwd):
            _deny(
                "拆件会话只能写章程 §三 白名单路径（真源 "
                "tools.liaison.unpack.dispatch.CHARTER_WRITABLE_PATHS）；越界目标："
                + file_path
            )
        return 0

    # Read / Glob / Grep / MCP 等其它工具不拦截。
    return 0


if __name__ == "__main__":
    sys.exit(main())
