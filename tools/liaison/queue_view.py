"""队列的 Markdown 导出视图。**只读、单向、可随时重建。**

真身是 `liaison.db` 的 `liaison_task` 表（design D11）。本模块把它渲染成一份
给人看的 Markdown，⛔ **反过来一个字都不读**：人在导出文件里改了什么，下一次
渲染原样覆盖，数据库一个字都不变。

⛔ **本模块不许出现任何读取动作**（`open(` / `read_text` / `read_bytes` /
`iterdir`）。`tests/test_queue_view.py::test_queue_view_module_never_reads_anything`
用 AST 把这条钉死。加一个"检测到手改就提示/回填"的分支，等于把导出文件变成
第二份真身——那正是 design D11 单向性要消灭的东西。

⛔ **本模块不许写 `with` 语句**（含 `with open(...)`、`contextlib.suppress`）：
第 2 章的 `test_no_second_transaction_manager_in_source` 把 `tools/liaison/` 非测试
代码里任何 `with X:` 判为隐式提交事务违规，它认形状不认语义。
文件写一律 `pathlib.Path.write_text()` + `os.replace()`。
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

from tools.liaison.queue import list_tasks
from tools.liaison.storage.db import REPO_ROOT

#: 默认导出落点。`data/` 已被 .gitignore 覆盖，导出物不会误入版本管理。
DEFAULT_QUEUE_VIEW_PATH = REPO_ROOT / "data" / "liaison" / "queue.md"

#: 枚举值 → 展示文案。**只在这里出现 emoji**（design D11：存的是枚举值，
#: emoji 只允许出现在渲染层）。⛔ 不要把这份映射倒过来用去解析文件——
#: 那就是从导出物读状态，单向性当场破掉。
STATUS_LABELS = {
    "pending": "🆕 待发",
    "deferred": "⏸ 暂缓",
    "pushed": "✅ 已推送",
}

#: 单元格的字符上限。超长内容在视图里截断，**真身不截断**——
#: 要看全文去 `liaison_message.content`，视图只是视图。
DEFAULT_CELL_MAX_CHARS = 200

#: 单遍替换表。**必须用 `str.translate` 而不是链式 `.replace`**：
#: 链式替换里，竖线规则插入的反斜杠会被反斜杠规则再转义一遍（或反过来漏转），
#: 取决于谁先跑——两种顺序都错，只是错法不同。`translate` 一遍扫完，
#: 插入的字符不会被后续规则再看一次。
_CELL_TRANSLATION = str.maketrans(
    {
        "\\": r"\\",
        "|": r"\|",
        "\n": r"\n",
        "\r": r"\r",
        "\t": r"\t",
    }
)

_TABLE_HEADER = "| ID | 状态 | 发送人 | 会话 | 接收时间 | 推送时间 | msgid | 摘要 |"
_TABLE_DIVIDER = "|---:|---|---|---|---|---|---|---|"


def escape_cell(text, *, max_chars: int = DEFAULT_CELL_MAX_CHARS) -> str:
    """纯函数：把任意字符串压成一个安全的表格单元格。

    竖线转义成 `\\|`、换行/回车/制表符转义成可见的两字符序列、其余不可打印字符
    转义成 `\\xNN`。超长按字符截断并以 `…` 标记。

    ⛔ 只在这里转义。存储层原样存——参考服务的"竖线归一化"是给
    "拿 Markdown 当数据库"那套形态打的补丁，在本形态下只会静默改掉用户发来的字。
    """
    escaped = ("" if text is None else str(text)).translate(_CELL_TRANSLATION)
    escaped = "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in escaped)
    if len(escaped) > max_chars:
        escaped = escaped[: max_chars - 1] + "…"
    return escaped


def render_queue_markdown(conn: sqlite3.Connection, *, generated_at: str) -> str:
    """纯渲染：读库、返回字符串。⛔ 不碰文件系统、⛔ 不读时钟。

    `generated_at` 由调用方传入而不是在这里 `datetime.now()`——渲染读时钟会让
    "同一份数据渲染出同一份文本"不再成立，测试只能靠 monkeypatch 才写得动。
    """
    lines = [
        "# 值守任务队列（只读导出）",
        "",
        f"> 生成于 {generated_at}。本文件由 `render_queue_markdown` 从 `data/liaison.db` "
        "渲染而来，是一份**只读导出**。",
        "> ⛔ 在这里改任何东西**不会回写**数据库，下一次渲染会原样覆盖。要改状态请走 "
        "`queue.defer_task` / `queue.mark_task_pushed`。",
        "",
        _TABLE_HEADER,
        _TABLE_DIVIDER,
    ]
    for row in list_tasks(conn):
        lines.append(
            "| {id} | {status} | {sender} | {thread} | {received} | {pushed} | {msgid} | {summary} |".format(
                id=row["id"],
                # 未知取值不该出现（表上有 CHECK），但真出现了要看得见，
                # ⛔ 不要静默显示成空白——那会让一个约束破裂的行看起来正常。
                status=STATUS_LABELS.get(row["send_status"], f"⚠️ 未知({row['send_status']})"),
                sender=escape_cell(row["sender_userid"]),
                thread=escape_cell(row["thread_id"]),
                received=escape_cell(row["received_at"]),
                pushed=escape_cell(row["pushed_at"]) if row["pushed_at"] else "—",
                msgid=escape_cell(row["msgid"]),
                summary=escape_cell(row["summary"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def export_queue_markdown(
    conn: sqlite3.Connection,
    *,
    out_path: pathlib.Path | str = DEFAULT_QUEUE_VIEW_PATH,
    generated_at: str,
) -> pathlib.Path:
    """把渲染结果原子地覆盖写到 `out_path`。**本模块唯一的副作用。**

    先写同目录临时文件再 `os.replace()`：中途崩溃时读者要么看到上一版完整内容、
    要么看到新版完整内容，⛔ 不会看到半截表格——半截表格看起来就像"队列少了几条"。

    ⛔ 不用 `with open(...)`（本模块 docstring 的第三条）。
    """
    destination = pathlib.Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = render_queue_markdown(conn, generated_at=generated_at)
    staging = destination.with_name(destination.name + ".tmp")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, destination)
    return destination
