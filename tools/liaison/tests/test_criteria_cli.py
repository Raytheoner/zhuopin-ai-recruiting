"""4.2·`criteria` 子命令的 CLI 层：参数校验、退出码、原子写。

⛔ 全程用 tmp_path 造台账文件，不碰仓库里的真实 `docs/跟进信/口径点台账.md`
——测试要能在任何机器、任何顺序下重复跑，不能依赖也不能污染那份真实台账。
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

from tools.liaison.unpack import criteria

TODAY = datetime.date(2026, 9, 16)

LEDGER_TEXT = """# 口径点台账

## 台账

| 口径点ID | 来源信 | 描述 | 状态 | evidence | 更新 |
|---|---|---|---|---|---|
| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |
"""


@pytest.fixture()
def ledger(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "口径点台账.md"
    path.write_text(LEDGER_TEXT, encoding="utf-8")
    return path


def test_add_appends_new_row_and_returns_ok(ledger, capsys):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2", "--desc", "新口径点"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_OK
    text = ledger.read_text(encoding="utf-8")
    assert "| `HR-G-02` | 人事部#2 | 新口径点 | 待专员 |  | 2026-09-16 |" in text
    assert "HR-G-02" in capsys.readouterr().out


def test_add_without_desc_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_transition_with_evidence_updates_row(ledger):
    code = criteria.criteria_main(
        [
            "--id", "HR-G-01",
            "--to", "已签认",
            "--evidence", "docs/跟进信/回件/人事部#1-2026-09-16.md#a",
        ],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_OK
    text = ledger.read_text(encoding="utf-8")
    assert "已签认" in text
    assert "docs/跟进信/回件/人事部#1-2026-09-16.md#a" in text


def test_transition_to_已签认_without_evidence_exits_3_and_file_unchanged(ledger):
    before = ledger.read_bytes()
    code = criteria.criteria_main(
        ["--id", "HR-G-01", "--to", "已签认"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_MISSING_EVIDENCE
    assert ledger.read_bytes() == before


def test_transition_unknown_id_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--id", "HR-G-99", "--to", "已回复"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_add_and_id_together_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2", "--desc", "x", "--id", "HR-G-01", "--to", "已回复"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_neither_add_nor_id_exits_bad_args(ledger):
    code = criteria.criteria_main([], ledger_path=ledger, today=TODAY)
    assert code == criteria.EXIT_BAD_ARGS


def test_missing_ledger_file_exits_bad_args(tmp_path):
    missing = tmp_path / "不存在.md"
    code = criteria.criteria_main(
        ["--id", "HR-G-01", "--to", "已回复"], ledger_path=missing, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS
