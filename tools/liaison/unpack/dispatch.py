"""并发守卫 ＋ 非阻塞起活（design D4/D12，spec「并发守卫按 pid 判活」「拆件会话
以受限权限启动」「起活失败只审计不上抛」）。

⛔ 本模块 ⛔ 不 import `tools.liaison.storage.db`（spec「子命令不碰库」，Task 6
的 AST 测试守着整条 `unpack-dispatch` 子命令的 import 面，含本模块）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger("tools.liaison.unpack.dispatch")

#: 环境变量名。⛔ 只在这里写死一次——`.env.example`（Task 7）与
#: `build_headless_argv` 的调用方都从这里 import，⛔ 不许各处重写字面量。
CLAUDE_BIN_ENV = "HR_LIAISON_CLAUDE_BIN"
BUDGET_ENV = "HR_LIAISON_UNPACK_BUDGET_USD"

#: 预算默认值（design D16，Shao Peishen 2026-09-10 答 2a）。取字符串——它只会被
#:拼进 argv，⛔ 不参与任何数值运算，存成 `str` 免得调用方还要 `str(int(...))`。
DEFAULT_BUDGET_USD = "5"


def compute_is_alive(pid: int, *, _kill: Callable[[int, int], None] = os.kill) -> bool:
    """`pid` 对应的进程是否仍在运行。**查询失败一律按不存活处理**（spec 明写）。

    `_kill` 是测试注入缝（⛔ 不是配置项）：`os.kill(pid, 0)` 不发信号只探测，
    `ProcessLookupError`（进程不存在）/`PermissionError`（进程存在但探测不到，
    如属于别的用户）/**任何其它异常**统一判「不存活」——design D12 逐字要求这三者
    同一处置，⛔ 不许把 `PermissionError` 特殊化成"存活但探测不到"。
    """
    try:
        _kill(pid, 0)
    except Exception:
        return False
    return True


def compute_is_busy(lock_text: str | None, is_alive: Callable[[int], bool]) -> bool:
    """锁文本 + 判活函数 → 是否忙。四种形状全部落在「不忙」这一侧，除了
    「锁存在、pid 合法、`is_alive` 返回 True」这一种。

    `is_alive` 是调用方注入的判活函数（生产传 `compute_is_alive`，单测传 fake）。
    ⚠️ 本函数**自己也**包一层 `try/except`——不因为不信任 `compute_is_alive`
    （它已经不会抛），而是「查询失败归不存活」是 spec 对"判忙"这整条判据的要求，
    不该只在 `compute_is_alive` 一处兜底，未来换一个判活实现时这条防线不能丢。
    """
    if not lock_text:
        return False
    try:
        payload: Any = json.loads(lock_text)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        return bool(is_alive(pid))
    except Exception:
        return False


def resolve_claude_bin(env: Mapping[str, str]) -> str | None:
    """`claude` 二进制路径解析（design D12 三级顺序）。均找不到 ⇒ `None`，
    调用方（Task 4）据此转 `failed(reason=binary_not_found)`。

    ⛔ 不在这里抛异常——"找不到"是一个**正常、被 spec 预期到**的结果，抛异常
    会强迫调用方用 `try/except` 来处理一个其实只是"返回值是 None"的情形。
    """
    override = env.get(CLAUDE_BIN_ENV)
    if override and override.strip():
        return override.strip()
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.is_file():
        return str(fallback)
    return None


#: 受限权限 argv 模板（design D4）。⛔ **唯一真源**——白名单是否放行某条命令、
#: 预算参数是否存在，全部由 `subagent-driven-development` 的 reviewer 对着*这个
#: 常量*核对，⛔ 不许在别处再拼一份 argv 字面量。
HEADLESS_ARGV_FIXED_PART: tuple[str, ...] = (
    "-p",
    "--output-format", "text",
    "--permission-mode", "acceptEdits",
    "--allowedTools",
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)",
    "Bash(git diff:*)", "Bash(git log:*)",
    "Bash(python -m tools.liaison unpack-signal:*)",
    "Bash(python -m tools.liaison criteria:*)",
)


def build_headless_argv(claude_bin: str, budget: str) -> list[str]:
    """拼出完整 argv（含二进制路径与预算值）。**纯函数**，⛔ 不读环境、不起进程。

    白名单里 ⛔ **不出现** `send-followup`（合规红线：对外通道不可代）、
    ⛔ 不出现 `git push`（design D7），且权限模式固定 `acceptEdits`——
    ⛔ 绝不使用跳过全部确认的模式（合规红线 + design D4）。
    """
    return [claude_bin, *HEADLESS_ARGV_FIXED_PART, "--max-budget-usd", budget]
