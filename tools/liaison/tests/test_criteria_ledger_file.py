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


def test_ledger_rows_state_evidence_invariant_holds():
    """真实台账会随时间流转（`待专员`→`已回复`→`已签认`/`已作废`），行级断言
    不能锁死某一行此刻恰好处在哪一态——那会在下一次真实流转后必然变红。
    改断言与流转进度无关的不变式：状态必须落在四态之一；`已签认` 的行必须
    带非空 evidence（防止手滑预填假 evidence 或签认时漏填）；`待专员` 的行
    evidence 必须为空（防止起始态就带出处）。"""
    text = LEDGER_PATH.read_text(encoding="utf-8")
    valid_states = {"待专员", "已回复", "已签认", "已作废"}
    data_rows = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 6:
            continue
        first_cell = cells[1].strip()
        if not (first_cell.startswith("`") and first_cell.endswith("`")):
            continue  # 表头行与分隔行（`---`）都过不了「反引号包裹的 ID」这道
        data_rows.append(cells)

    assert data_rows, "台账表格里没有任何数据行"
    for cells in data_rows:
        state = cells[4].strip()
        evidence = cells[5].strip()
        assert state in valid_states, f"未知状态 {state!r}：{cells}"
        if state == "已签认":
            assert evidence != "", f"已签认的行必须带 evidence：{cells}"
        elif state == "待专员":
            assert evidence == "", f"待专员的行不该有 evidence：{cells}"
