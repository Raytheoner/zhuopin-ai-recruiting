"""信号文件：拆件会话「有没有活要干」的旗子（design D8：不是数据，是旗子）。

⛔ 本模块不 import `tools.liaison.storage.db`——spec「子命令不碰库」要求
`unpack-signal` 子命令能在不碰值守数据库的情况下跑，模块层的 import 边界是
最结实的保证方式（AST 扫描守不住"函数体内 late import"，但堵住模块层 import
足以覆盖本模块自己的直接调用面，Task 6 的 AST 测试另行扫整个子命令模块）。

追加/清除都走「写临时文件 + `os.replace`」原子替换（复用 `session.py::
effect_write_liveness_stamp` 的手法）：外部读者要么看到替换前完整的一份，
要么看到替换后完整的一份，⛔ 不会看到半截 JSON。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _read_pending(path: Path) -> tuple[list[dict], bool]:
    """读 `pending` 数组。返回 `(数组, 文件是否存在但读不出合法内容)`。

    第二个返回值就是"损坏"判据：文件不存在 ⇒ `(每, False)`（正常的"还没有信号"）；
    文件存在但不是合法 JSON、或没有 `pending` 数组 ⇒ `([], True)`（损坏，调用方据此
    决定是否要在追加后打「已替换」审计）。
    """
    if not path.is_file():
        return [], False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], True
    if not isinstance(payload, dict) or not isinstance(payload.get("pending"), list):
        return [], True
    return payload["pending"], False


def _write_pending_atomic(path: Path, pending: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps({"pending": pending}, ensure_ascii=False), encoding="utf-8"
    )
    # 原子替换：追加期间进程被杀，外部读到的仍是替换前完整的一份。
    os.replace(tmp_path, path)


def append_signal(path: Path, item: dict) -> bool:
    """追加一项，按 `msgid` 去重（同 msgid 的旧项被新项覆盖，取最新时刻）。

    返回是否发生了"文件缺失/损坏 ⇒ 以只含本次一项的新文件替换"（spec 要求
    调用方据此留一条 `signal_file_replaced` 审计记录，本函数不碰库，只报状态）。
    """
    pending, was_corrupted = _read_pending(path)
    if was_corrupted:
        pending = []
    deduped = [entry for entry in pending if entry.get("msgid") != item["msgid"]]
    deduped.append(item)
    _write_pending_atomic(path, deduped)
    return was_corrupted


def probe_signal(path: Path) -> bool:
    """有没有待处理信号。空文件、`pending: []`、文件不存在都算「没有」。"""
    pending, _ = _read_pending(path)
    return bool(pending)


def clear_signal_before(path: Path, checkpoint: str) -> None:
    """只清 `at < checkpoint` 的项，保留 `at >= checkpoint` 的项。

    文件不存在 ⇒ 什么都不用清，直接返回（⛔ 不创建文件——清除操作不该凭空
    造出一个空信号文件，那会让下一次 `probe_signal` 的"没有信号"判断多一层
    不必要的文件系统访问）。

    ⚠️ `checkpoint`/`at` 必须是同一口径的 ISO8601 字符串（`session.format_instant`
    产出、固定 `+08:00` 偏移、微秒定宽）才能安全按字符串比较；调用方负责这一点，
    本函数不做时区归一化——归一化需要解析时间，那会把这个本该是纯字符串操作
    的函数拖进 `datetime` 解析的错误处理泥潭，超出"只清检查点之前"这条职责。
    """
    pending, was_corrupted = _read_pending(path)
    if was_corrupted or not pending:
        return
    kept = [entry for entry in pending if entry.get("at", "") >= checkpoint]
    if len(kept) == len(pending):
        return
    _write_pending_atomic(path, kept)
