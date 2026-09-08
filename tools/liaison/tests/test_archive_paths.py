"""归档路径的两个纯函数：文件名路径安全（4.2/4.9）与路径拼装（4.1）。

⛔ 这两个函数里不许出现任何 I/O、时钟读取、环境变量读取——它们是
`compute_*`（工程铁律 2 的形状），本文件末尾有 AST 断言把这条钉死。
"""

from __future__ import annotations

import ast
import inspect

import pytest

from tools.liaison.archive import (
    DEFAULT_MAX_FILENAME_BYTES,
    FALLBACK_FILENAME,
    compute_safe_filename,
)


# ─────────────────────────────────────────────────────────────────────────
# 4.2 / spec「文件名归一化只处理路径安全」
# ─────────────────────────────────────────────────────────────────────────


def test_chinese_filename_is_preserved_verbatim():
    """spec Scenario「中文文件名」：原样保留，⛔ 不转拼音、⛔ 不替换、⛔ 不拒收。

    这是生产 bug「二进制判误」的同源思路在文件名侧的翻版——"看着像乱码就处理掉"
    对中文文件名是纯粹的破坏。本条一旦变红，⛔ 不许改断言去迁就实现。
    """
    assert compute_safe_filename("岗位要求确认反馈表v3.xlsx") == "岗位要求确认反馈表v3.xlsx"


def test_non_ascii_of_other_scripts_is_preserved_too():
    """不是"给中文开个特例"，是根本不看字符属于哪个书写系统。"""
    for name in ("履歴書.pdf", "Lebenslauf–2026.docx", "résumé.txt", "emoji✅表.xlsx"):
        assert compute_safe_filename(name) == name


def test_path_separators_are_removed():
    """spec Scenario「文件名含路径分隔符」：分隔符被移除，文件落在预期目录内。"""
    assert compute_safe_filename("a/b/c.xlsx") == "abc.xlsx"
    assert compute_safe_filename("a\\b\\c.xlsx") == "abc.xlsx"


def test_traversal_attempt_cannot_escape_the_directory():
    """`../../etc/passwd` 被压成**一个**普通名字，不含任何分隔符。

    判据是"结果里没有分隔符"而不是"结果等于某个具体串"——后者会把断言
    绑死在归一化的实现细节上。
    """
    result = compute_safe_filename("../../etc/passwd")
    assert "/" not in result and "\\" not in result
    assert result not in (".", "..", "")


def test_control_characters_are_removed():
    """控制字符（含 NUL）会截断 C 层的路径字符串，必须清掉。"""
    assert compute_safe_filename("re\x00port\x1f.pdf") == "report.pdf"
    assert compute_safe_filename("line\nbreak.txt") == "linebreak.txt"


def test_leading_and_trailing_whitespace_is_stripped():
    assert compute_safe_filename("  报告.docx  ") == "报告.docx"
    assert compute_safe_filename("\t报告.docx\n") == "报告.docx"


def test_names_that_reduce_to_nothing_fall_back_to_a_placeholder():
    """全被清空、或只剩点号的名字，折成占位名。

    ⛔ 不允许返回空串或 "." / ".."——它们拼进路径会变成"目录本身"，
    最终 `os.replace` 会去覆盖一个目录，报的是 IsADirectoryError 这类
    看不出根因的错。
    """
    for hostile in ("", "   ", "///", "\x00", ".", "..", "  ..  "):
        assert compute_safe_filename(hostile) == FALLBACK_FILENAME


def test_non_string_input_falls_back_instead_of_raising():
    """协议给的字段可能是 None。判定路径不抛异常，折成占位名。"""
    assert compute_safe_filename(None) == FALLBACK_FILENAME
    assert compute_safe_filename(12345) == FALLBACK_FILENAME


# ─────────────────────────────────────────────────────────────────────────
# 4.9 超长截断
# ─────────────────────────────────────────────────────────────────────────


def test_overlong_name_is_truncated_and_keeps_the_extension():
    """spec Scenario「文件名超长」：截断且扩展名保留。"""
    name = "报" * 500 + ".xlsx"
    result = compute_safe_filename(name)
    assert result.endswith(".xlsx")
    assert len(result.encode("utf-8")) <= DEFAULT_MAX_FILENAME_BYTES


def test_truncation_counts_bytes_not_characters():
    """文件系统的 255 上限是**字节**。一个中文字 3 字节——按字符数算会溢出。

    ⛔ 不许把这条改成 `len(result) <= max_bytes`：那在纯 ASCII 下恰好也绿，
    是最典型的"测试写得比实现还宽"。
    """
    result = compute_safe_filename("字" * 200 + ".pdf", max_bytes=64)
    assert len(result.encode("utf-8")) <= 64
    assert result.endswith(".pdf")


def test_truncation_never_produces_broken_utf8():
    """按字节切会切在多字节字符中间，必须丢掉那个残缺字符而不是留下坏字节。"""
    result = compute_safe_filename("字" * 100 + ".pdf", max_bytes=10)
    # 能无损往返编解码 ⇒ 没有残缺序列
    assert result.encode("utf-8").decode("utf-8") == result


def test_extension_longer_than_the_budget_still_yields_a_bounded_name():
    """扩展名本身就超预算的病态输入：保不住扩展名，但**绝不能溢出**。"""
    result = compute_safe_filename("a." + "x" * 300, max_bytes=32)
    assert len(result.encode("utf-8")) <= 32
    assert result != ""


def test_dotfile_is_treated_as_a_whole_name_not_as_an_extension():
    """`.gitignore` 的"扩展名"是整个名字。⛔ 不许把它切成空 stem + 长后缀。"""
    result = compute_safe_filename(".gitignore", max_bytes=200)
    assert result == ".gitignore"


def test_name_within_budget_is_returned_unchanged():
    """没超预算就一个字符都不动——截断只在真的超了才发生。"""
    assert compute_safe_filename("正常文件.xlsx", max_bytes=200) == "正常文件.xlsx"


# ─────────────────────────────────────────────────────────────────────────
# 铁律 2：`compute_*` 是纯函数
# ─────────────────────────────────────────────────────────────────────────

_FORBIDDEN_IN_PURE_FUNCTIONS = {
    "open",
    "now",
    "today",
    "getenv",
    "urlopen",
    "print",
}


def test_compute_safe_filename_is_pure():
    """AST 判据：函数体里不许出现 I/O、时钟、环境变量、网络调用。

    ⛔ 变红时不许"加个 noqa"——纯函数是铁律 2 的形状，不是风格偏好。
    """
    tree = ast.parse(inspect.getsource(compute_safe_filename))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    leaked = called & _FORBIDDEN_IN_PURE_FUNCTIONS
    assert not leaked, f"compute_safe_filename 不再是纯函数，出现了 {sorted(leaked)}"
