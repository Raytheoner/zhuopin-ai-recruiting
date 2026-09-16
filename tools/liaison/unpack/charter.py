"""拆件章程正本的单点定位与读取（design D13）。

章程正本只有一份，落在 `.claude/skills/liaison-unpack/SKILL.md`——CLAUDE.md
「规则真源在 `.claude/skills/`」，且人也能手动 `/liaison-unpack` 走同一流程。
本模块只做「找到它、读出它」，⛔ 不持有章程正文的任何副本（正文本身在
SKILL.md 里，见 Task 2）。
"""

from __future__ import annotations

from pathlib import Path


class CharterMissing(Exception):
    """章程正本文件缺失或不可读。"""


#: 章程正本相对仓库根的唯一路径。⛔ 这是本仓库里唯一一处定义这条路径的地方——
#: `dispatch.py`（P1）与全部测试都必须 import 这个常量，不得自己拼字符串。
CHARTER_RELATIVE_PATH = ".claude/skills/liaison-unpack/SKILL.md"


def read_charter(repo_root: Path) -> str:
    """读出章程正本全文。`repo_root` 缺该文件 ⇒ `CharterMissing`。"""

    path = Path(repo_root) / CHARTER_RELATIVE_PATH
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CharterMissing(f"章程正本缺失：{path}") from exc
