"""台账 ↔ 材料的一致性核对（spec「台账中存在的消息其材料必然可取回」）。

**只核对一个方向**：台账说有的，磁盘上是不是真的有、且字节没变。
⛔ 反方向（磁盘上有、台账没有）**不是**不一致——那正是 design D3 允许的、
可由重跑收敛的中间态。把它报成问题，会让崩溃恢复期的每次核对刷出一堆假
警报，真问题就淹在里面了。

⛔ **本模块不删任何东西。** 留存期清理是第 8 章。本模块是清理的**前置**
（清理前后各跑一次，用来证明清理没有把台账和材料弄成两张皮），不是清理本身。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from tools.liaison.archive import DEFAULT_ARCHIVE_ROOT
from tools.liaison.attachments import verify_archived_file


@dataclass(frozen=True)
class LedgerInconsistency:
    """一条"台账说有、实际对不上"的记录。

    `relative_path` 在"连 JSON 都解析不了"的情况下是空串——那时候根本
    读不出路径。⛔ 不要因此把它做成 `str | None`：调用方多一个分支，
    换来的信息量是零。
    """

    msgid: str
    relative_path: str
    problem: str


def verify_ledger_against_archive(
    conn, *, archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT
) -> list[LedgerInconsistency]:
    """遍历消息台账里每条带附件的记录，核对材料可读且摘要匹配。

    返回不一致清单；空列表 = 一致。**遇到坏记录继续往下跑**，⛔ 不抛异常、
    ⛔ 不提前返回——spec 要求的是"遍历每条记录"，跑一半停下等于没核对。
    """
    problems: list[LedgerInconsistency] = []

    for msgid, attachments_json in conn.execute(
        "SELECT msgid, attachments_json FROM liaison_message ORDER BY msgid"
    ):
        try:
            entries = json.loads(attachments_json)
        except (TypeError, ValueError):
            problems.append(
                LedgerInconsistency(msgid, "", "attachments_json 不是合法 JSON，无法核对")
            )
            continue

        if not isinstance(entries, list):
            problems.append(
                LedgerInconsistency(msgid, "", "attachments_json 不是数组，无法核对")
            )
            continue

        for entry in entries:
            problems.extend(_verify_entry(msgid, entry, archive_root))

    return problems


def _verify_entry(msgid: str, entry, archive_root: pathlib.Path) -> list[LedgerInconsistency]:
    if not isinstance(entry, dict):
        return [LedgerInconsistency(msgid, "", "attachments_json 条目不是对象")]

    relative_path = entry.get("relative_path")
    byte_length = entry.get("byte_length")
    sha256 = entry.get("sha256")
    if not isinstance(relative_path, str) or not isinstance(byte_length, int) or not isinstance(sha256, str):
        return [
            LedgerInconsistency(
                msgid,
                relative_path if isinstance(relative_path, str) else "",
                "attachments_json 条目缺 relative_path / byte_length / sha256",
            )
        ]

    if not verify_archived_file(
        archive_root / relative_path, byte_length=byte_length, sha256=sha256
    ):
        return [
            LedgerInconsistency(
                msgid, relative_path, "材料缺失或字节长度/SHA-256 与台账不符"
            )
        ]

    return []
