"""4.1·口径点台账文件本体：版本管理内的 markdown 表格。

⛔ 本文件只断言"文件长什么样"，不解析、不改写——解析与改写是 Task 2/3 的事。
"""

from __future__ import annotations

import pathlib

# tools/liaison/tests/test_x.py → parents[0]=tests, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LEDGER_PATH = REPO_ROOT / "docs" / "跟进信" / "口径点台账.md"


def test_ledger_file_exists_with_header_and_first_row():
    assert LEDGER_PATH.is_file(), f"台账文件不存在：{LEDGER_PATH}"
    text = LEDGER_PATH.read_text(encoding="utf-8")
    assert "口径点ID | 来源信 | 描述 | 状态 | evidence | 更新" in text
    assert "`HR-G-01`" in text
    assert "人事部#1" in text
    assert "待专员" in text


def test_four_state_semantics_are_documented():
    text = LEDGER_PATH.read_text(encoding="utf-8")
    for state in ("待专员", "已回复", "已签认", "已作废"):
        assert f"`{state}`" in text, f"四态语义表缺 {state}"
    assert "放久了就算签认" in text


def test_signed_off_row_has_no_evidence_yet():
    """HR-G-01 起始态是 `待专员`，evidence 列应为空——防止有人手滑预填了假 evidence。"""
    text = LEDGER_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "`HR-G-01`" in line and line.strip().startswith("|"):
            cells = line.split("|")
            assert cells[4].strip() == "待专员"
            assert cells[5].strip() == ""
            return
    raise AssertionError("台账表格里没找到 HR-G-01 那一行")
