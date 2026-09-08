"""5.4 / 5.8：Markdown 只是只读导出物，且是**单向的**。

本文件最重要的两条是 `test_queue_view_module_never_reads_anything`（静态，钉住单向性）
与 `test_hand_edits_to_the_export_never_reach_the_database`（行为，钉住"没有第二份真身"）。
"""

import ast
import pathlib
import re

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
    """fix round 2：不可打印字符改成定长 `\\uXXXX`（BMP 内固定 4 位十六进制），
    不再用变长的 `\\xNN`——变长会让截断切出语义错误的半截（见下方专项用例）。
    """
    assert escape_cell("空\x00字节") == r"空\u0000字节"
    assert escape_cell("转义\x1b序列") == r"转义\u001b序列"
    assert escape_cell("删除\x7f符") == r"删除\u007f符"


def test_escape_cell_escapes_codepoints_above_0xff_with_a_fixed_width_form():
    """核心缺陷（fix round 2）：旧实现用 `f"\\\\x{{ord(ch):02x}}"`，`02x` 只保证
    **最小**宽度 2，码点 > 0xFF 时产出**变长**十六进制（如 U+2028 → `\\x2028`，
    6 字符而不是 4）。而截断用的 token 化正则只吃固定 2 位 `\\x[0-9a-fA-F]{2}`，
    于是把 `\\x2028` 拆成 `\\x20` + `2` + `8` 三个 token——`\\x20` 恰好是一个语法
    完整但语义错误的转义（真正的 U+0020 是空格，但这里其实是 U+2028 的前半截）。

    修复：BMP 内的不可打印字符一律 `\\uXXXX`（定长 4 位），超出 BMP 的一律
    `\\UXXXXXXXX`（定长 8 位）——与 Python 自身 `unicode_escape` 的惯例一致，
    定长意味着 token 化正则可以精确匹配、不会再产生变长导致的歧义。
    """
    for ch in (chr(0x2028), chr(0x200B), chr(0xFEFF)):
        escaped = escape_cell(ch)
        assert escaped == f"\\u{ord(ch):04x}", (
            f"U+{ord(ch):04X} 转义结果不是定长 \\uXXXX：{escaped!r}"
        )
        assert len(escaped) == 6, f"\\uXXXX 必须恒定 6 字符：{escaped!r}"

    # 超出 BMP（> 0xFFFF）：用 8 位 \U 形式，与 \u 形式靠大小写区分，彼此不会混淆。
    astral = chr(0xE0001)  # LANGUAGE TAG，Cf 类别，非打印
    escaped_astral = escape_cell(astral)
    assert escaped_astral == f"\\U{ord(astral):08x}"
    assert len(escaped_astral) == 10


def test_escape_cell_truncation_never_splits_a_wide_codepoint_escape():
    """截断恰好落在 `\\uXXXX`（宽字符转义）中间时，必须整体丢弃，⛔ 不许留半截。

    旧实现下这个用例会产出 `AAAAAAA\\x20…`（切出了看起来完整、实际是
    U+2028 前半截的 `\\x20`）——静默语义失真，比"肉眼可见的半截"更危险。
    """
    text = "A" * 7 + chr(0x2028) + "Z"
    full = escape_cell(text, max_chars=200)
    assert full == "AAAAAAA\\u2028Z"

    # ` ` 转义后是 6 字符：\ u 2 0 2 8。逐个 max_chars 扫过整个序列的
    # 中间位置，确保输出里不会出现"半个 \\uXXXX"这种不完整片段。
    for max_chars in range(8, 15):
        cell = escape_cell(text, max_chars=max_chars)
        assert cell.endswith("…") or cell == full
        stripped = cell[:-1] if cell.endswith("…") else cell
        # 不许出现游离的 `\`、`\u`，或位数不足 4 位的残缺 `\uXXX`/`\uXX`/`\uX`
        assert not re.search(r"\\u[0-9a-fA-F]{0,3}$", stripped), (
            f"max_chars={max_chars} 切出了半个 \\uXXXX：{cell!r}"
        )


def test_escape_cell_is_unambiguous_for_distinct_inputs():
    """无歧义性：即使完全不截断，转义结果也必须能唯一还原原始字符串——
    任意两个不同的原始字符串，转义后 ⛔ 不许得到同一个结果。

    第一组：真实的 U+2028 字符 vs 用户自己打的六个字面 ASCII 字符 `\\u2028`。
    后者含一个真实反斜杠，会被 `_CELL_TRANSLATION` 先翻倍成两个反斜杠，
    天然与"转义生成的单个反斜杠"区分开，这是本方案无歧义性的结构性保证。

    第二组：对应旧缺陷的经典构造——`\\x2028`（旧变长方案对 U+2028 的转义结果）
    这一串字符本身有歧义，既可读作"一个 4 位转义"也可读作"2 位转义 + 字面
    `28`"。新方案下二者不会再合流，因为 `\\uXXXX` 恒定 4 位，`\\x` 前缀也
    根本不再是合法转义形状。
    """
    real_char = escape_cell(chr(0x2028))
    user_typed_literal = escape_cell("\\u2028")  # 用户真的打了这 6 个字符
    assert real_char != user_typed_literal
    assert real_char == "\\u2028"
    assert user_typed_literal == "\\\\u2028"  # 真实反斜杠被翻倍


def test_escape_cell_keeps_cjk_and_emoji_intact():
    """⛔ 不许因为"看起来不是 ASCII"就转义。只有不可打印字符才转。"""
    assert escape_cell("报价单 📎 已发") == "报价单 📎 已发"


def test_escape_cell_truncates_and_marks_it():
    long_text = "甲" * 500
    cell = escape_cell(long_text, max_chars=200)
    assert len(cell) == 200
    assert cell.endswith("…")


def test_escape_cell_truncation_never_splits_an_escape_sequence():
    """Minor（fix round 1）：截断发生在**已转义之后**的字符串上，按字符数硬切
    可能恰好切在一个多字符转义序列中间（如 `\\` 和 `n` 之间），留下一个孤立
    反斜杠紧贴省略号——看起来像半个转义、观感是瑕疵。

    构造：7 个 `A` + 一个真实换行符（转义后变成两字符 `\\n`）+ 1 个 `Z`，
    转义后共 10 字符：`AAAAAAA\nZ`（其中 `\n` 是两个字符：反斜杠、字母 n）。
    `max_chars=9` 时朴素的字符切片会切在反斜杠之后、`n` 之前，
    留下 `AAAAAAA` 加一个孤立反斜杠再加 `…`。转义序列必须整体保留或整体丢弃，不许切一半。
    """
    text = "A" * 7 + "\n" + "Z"
    cell = escape_cell(text, max_chars=9)
    assert cell.endswith("…")
    assert not cell.endswith("\\…"), f"转义序列被从中间切断：{cell!r}"
    # 序列要么整体保留（结果里出现完整的两字符 \n），要么整体丢弃——
    # 这里预算不够容纳 \n 这个 token，所以应该整体丢弃，只剩 7 个 A。
    assert cell == "AAAAAAA…"


def test_escape_cell_truncation_never_splits_a_control_char_hex_escape():
    """同一条 Minor 的第二个场景：fix round 2 后 `\\x00` 变成 6 字符的 `\\u0000`
    （定长 `\\uXXXX`），同样不许被切一半。"""
    text = "A" * 7 + "\x00" + "Z"
    cell = escape_cell(text, max_chars=9)
    assert cell.endswith("…")
    stripped = cell[:-1]
    # 不许出现游离的 `\`、`\u` 或位数不足 4 位的残缺 `\uXXX`/`\uXX`/`\uX`
    assert not stripped.endswith("\\")
    assert not stripped.endswith("\\u")
    assert not re.search(r"\\u[0-9a-fA-F]{0,3}$", stripped)
    assert cell == "AAAAAAA…"


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


def test_render_escapes_an_illegal_send_status_value_in_the_fallback_branch(conn):
    """终审 Minor-5：`⚠️ 未知(...)` 兜底分支存在的全部理由就是"CHECK 约束已经
    破了"——也就是最不该假设 `send_status` 干净的时候。修复前这里直接把
    DB 原值拼进单元格，若那时 `send_status` 恰好含 `|` 或换行，这一行的列数
    就会错位。

    用 `PRAGMA ignore_check_constraints = ON` 绕开表上的三态 CHECK，直接塞一个
    含竖线的非法取值进去，断言渲染出来的表格每一行列数仍然一致。
    """
    _enqueue(conn, msgid="msg-1", content="待处理")
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute(
        "UPDATE liaison_task SET send_status = ? WHERE msgid = ?",
        ("broken|status\nwith-newline", "msg-1"),
    )
    conn.commit()

    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "⚠️ 未知(broken\\|status\\nwith-newline)" in text

    table_rows = [line for line in text.splitlines() if line.startswith("|")]
    unescaped_pipe_counts = {row.count("|") - row.count(r"\|") for row in table_rows}
    assert len(unescaped_pipe_counts) == 1, f"列数不一致：{unescaped_pipe_counts}"


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


def test_export_removes_the_temp_file_when_writing_it_fails(conn, tmp_path, monkeypatch):
    """Important（fix round 1）：写临时文件中途失败（磁盘满/权限错误等），
    `queue.md.tmp` ⛔ 不许堆积在目标目录里。原异常必须原样向上抛，
    ⛔ 不许被清理逻辑吞掉或替换成别的异常。

    用 monkeypatch 模拟写盘失败，⛔ 不真的把磁盘写满。只拦截以 `.tmp`
    结尾的那次 `Path.write_text`，其余调用（含测试自身用到的）走原实现。
    """
    _enqueue(conn, msgid="msg-1", content="报价单已发")
    out = tmp_path / "queue.md"

    original_write_text = pathlib.Path.write_text

    def _boom(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            # 模拟"写盘写到一半失败"：真实的磁盘满/断电是文件已经落地、
            # 内容不完整时抛异常，⛔ 不是异常在文件创建之前就拦下——
            # 后者测不出清理逻辑,因为根本没有文件可清理。
            original_write_text(self, "partial", encoding="utf-8")
            raise OSError("simulated disk full")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", _boom)

    with pytest.raises(OSError, match="simulated disk full"):
        export_queue_markdown(conn, out_path=out, generated_at=GENERATED_AT)

    assert not out.exists(), "写失败不该留下目标文件"
    leftover_tmp = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover_tmp == [], f"临时文件残留：{leftover_tmp}"


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
