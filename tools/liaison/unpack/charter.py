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


def compute_prompt(
    *,
    letter_number: str,
    msgid: str,
    signal_relpath: str,
    checkpoint_iso: str,
    charter_text: str,
) -> str:
    """拼出起活 prompt：事件驱动前言在前，章程全文逐字在后。

    前言携带本次触发的信件编号、消息标识、信号文件路径、检查点时刻，以及
    「走完一轮再探一次信号，仍有则再走一轮，直到无信号」这条循环规则——
    这条规则只属于前言，不得混进 ``charter_text``（spec 要求章程正文不可被
    前言改写，也不可反过来把只在前言里的规则塞进章程）。
    """

    preamble = (
        "# 拆件会话起活\n\n"
        f"- 信件编号：{letter_number}\n"
        f"- 消息标识（msgid）：{msgid}\n"
        f"- 信号文件：{signal_relpath}\n"
        f"- 检查点时刻：{checkpoint_iso}\n\n"
        "走完一轮拆件后，再探测一次信号（`python -m tools.liaison unpack-signal "
        "--probe`）；仍有信号则再走一轮，直到输出 `[NO-SIGNAL]` 为止。这条规则"
        "只在本前言里，不在下面的章程正文里。\n\n"
        "---\n\n"
    )
    return preamble + charter_text
