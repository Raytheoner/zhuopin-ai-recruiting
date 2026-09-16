"""并发守卫 ＋ 非阻塞起活（design D4/D12，spec「并发守卫按 pid 判活」「拆件会话
以受限权限启动」「起活失败只审计不上抛」）。

⛔ 本模块 ⛔ 不 import `tools.liaison.storage.db`（spec「子命令不碰库」，Task 6
的 AST 测试守着整条 `unpack-dispatch` 子命令的 import 面，含本模块）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger("tools.liaison.unpack.dispatch")


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
