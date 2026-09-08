"""5.4 / 5.8：Markdown 只是只读导出物，且是**单向的**。

本文件最重要的两条是 `test_queue_view_module_never_reads_anything`（静态，钉住单向性）
与 `test_hand_edits_to_the_export_never_reach_the_database`（行为，钉住"没有第二份真身"）。
"""

import ast
import pathlib

import pytest

from tools.liaison import queue_view
from tools.liaison.queue import enqueue_task, mark_task_pushed
from tools.liaison.queue_view import (
    escape_cell,
    export_queue_markdown,
    render_queue_markdown,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message

RECEIVED_AT = "2026-09-09T10:00:00+08:00"
GENERATED_AT = "2026-09-09T12:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _enqueue(conn, *, msgid, content, thread_id="u_zhang"):
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        msgtype="text",
        content=content,
    )
    enqueue_task(
        conn,
        thread_id=thread_id,
        msgid=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        summary=content,
    )


# ── 转义（纯函数） ────────────────────────────────────────────────────

def test_escape_cell_escapes_the_pipe():
    """spec：内容中的竖线以转义形式呈现。"""
    assert escape_cell("A|B") == r"A\|B"


def test_escape_cell_escapes_the_backslash_before_the_pipe_without_double_escaping():
    """⛔ 不许用链式 .replace 实现——那会把竖线规则插入的反斜杠再转义一遍。

    `str.translate` 是单遍替换，插入的字符不会被后续规则再看一次。
    """
    assert escape_cell(r"A\B") == r"A\\B"
    assert escape_cell(r"A\|B") == r"A\\\|B"


def test_escape_cell_turns_newlines_into_visible_two_char_sequences():
    """换行 ⇒ 字面 `\\n`。

    ⛔ 刻意不用 `<br>`：那是往一份只读文本里注入 HTML，
    而这个文件唯一的读者是 Shao Peishen 本人的编辑器。
    """
    assert escape_cell("第一行\n第二行") == r"第一行\n第二行"
    assert escape_cell("回车\r\n换行") == r"回车\r\n换行"
    assert escape_cell("制表\t符") == r"制表\t符"


def test_escape_cell_escapes_other_control_characters():
    assert escape_cell("空\x00字节") == r"空\x00字节"
    assert escape_cell("转义\x1b序列") == r"转义\x1b序列"
    assert escape_cell("删除\x7f符") == r"删除\x7f符"


def test_escape_cell_truncates_and_marks_it():
    long_text = "甲" * 500
    cell = escape_cell(long_text, max_chars=200)
    assert len(cell) == 200
    assert cell.endswith("…")


def test_escape_cell_keeps_cjk_and_emoji_intact():
    """⛔ 不许因为"看起来不是 ASCII"就转义。只有不可打印字符才转。"""
    assert escape_cell("报价单 📎 已发") == "报价单 📎 已发"


# ── 渲染（纯函数） ────────────────────────────────────────────────────

def test_render_keeps_the_table_shape_when_content_has_pipes_and_newlines(conn):
    """5.6 的渲染侧：任意内容 ⛔ 不许改变表格的列数或行数。"""
    _enqueue(conn, msgid="msg-1", content="a|b|c\nd|e")
    _enqueue(conn, msgid="msg-2", content="普通内容")

    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    table_rows = [line for line in text.splitlines() if line.startswith("|")]

    # 表头 + 分隔行 + 2 条数据 = 4 行，⛔ 一行都不许多
    assert len(table_rows) == 4
    column_counts = {row.count("|") - row.count(r"\|") for row in table_rows}
    assert len(column_counts) == 1, f"列数不一致，字段错位了：{column_counts}"


def test_render_shows_the_emoji_labels_but_the_database_stores_the_enum(conn):
    """design D11：存的是枚举值，emoji 只出现在渲染层。"""
    _enqueue(conn, msgid="msg-1", content="待处理")
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "🆕 待发" in text
    assert conn.execute("SELECT send_status FROM liaison_task").fetchone()[0] == "pending"


def test_render_shows_the_push_timestamp(conn):
    _enqueue(conn, msgid="msg-1", content="已发出")
    mark_task_pushed(
        conn, thread_id="u_zhang", msgid="msg-1", pushed_at="2026-09-09T11:30:00+08:00"
    )
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "✅ 已推送" in text
    assert "2026-09-09T11:30:00+08:00" in text


def test_render_carries_the_read_only_banner(conn):
    """人打开这个文件，第一眼就该知道改它没用。"""
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "只读导出" in text
    assert "不会回写" in text
    assert GENERATED_AT in text


def test_render_on_an_empty_queue_still_produces_a_table(conn):
    """⛔ 空队列不许渲染成空文件——那和"渲染失败"长得一模一样。"""
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "只读导出" in text
    assert text.count("\n|") >= 2  # 表头 + 分隔行仍在


# ── 单向性 ────────────────────────────────────────────────────────────

def test_hand_edits_to_the_export_never_reach_the_database(conn, tmp_path):
    """5.8 / spec 逐字场景：有人在导出文件里把状态改成「已推送」。"""
    _enqueue(conn, msgid="msg-1", content="报价单已发")
    out = tmp_path / "queue.md"
    export_queue_markdown(conn, out_path=out, generated_at=GENERATED_AT)

    tampered = out.read_text(encoding="utf-8").replace("🆕 待发", "✅ 已推送")
    tampered += "\n| 999 | ✅ 已推送 | 伪造 | 伪造 | 伪造 | 伪造 | 伪造 |\n"
    out.write_text(tampered, encoding="utf-8")

    # 真身一个字都没变
    assert conn.execute("SELECT send_status, pushed_at FROM liaison_task").fetchall() == [
        ("pending", None)
    ]
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1

    # 下一次渲染原样覆盖手改内容
    export_queue_markdown(conn, out_path=out, generated_at="2026-09-09T13:00:00+08:00")
    after = out.read_text(encoding="utf-8")
    assert "🆕 待发" in after
    assert "伪造" not in after
    assert "✅ 已推送" not in after


def test_export_leaves_no_temporary_file_behind(conn, tmp_path):
    _enqueue(conn, msgid="msg-1", content="报价单已发")
    out = tmp_path / "queue.md"
    export_queue_markdown(conn, out_path=out, generated_at=GENERATED_AT)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["liaison.db", "liaison.db-shm",
                                                          "liaison.db-wal", "queue.md"]


def test_queue_view_module_never_reads_anything():
    """🔴 单向性的静态判据：本模块**一次都不许**出现读取动作。

    行为测试只能证明"这一次没回写"；这条证明"源码里根本没有回写的路径"。
    ⛔ 不要为了加一个"检测手改并提示"的功能去放宽它——那一加，导出文件
    就变成了第二份真身，而"避免两份真身"正是 design D11 单向性的全部含义。
    """
    source = pathlib.Path(queue_view.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_attrs = {"read_text", "read_bytes", "readline", "readlines", "read", "iterdir"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_attrs:
                offenders.append(f"L{node.lineno} .{node.func.attr}()")
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                offenders.append(f"L{node.lineno} open()")
    assert offenders == [], f"queue_view.py 出现了读取动作，单向性破了：{offenders}"


def test_queue_view_module_has_no_with_statement():
    """第 2 章的事务扫描器把任何 `with X:` 判为隐式提交违规（本计划判断 3）。

    这条在本模块自己的测试里先拦一道，免得改动在一个毫不相干的测试里变红、
    让人以为是事务出了问题。
    """
    tree = ast.parse(pathlib.Path(queue_view.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno for node in ast.walk(tree) if isinstance(node, (ast.With, ast.AsyncWith))
    ]
    assert offenders == [], (
        f"queue_view.py 出现 with 语句，行号 {offenders}；"
        "改用 Path.write_text() + os.replace()"
    )
