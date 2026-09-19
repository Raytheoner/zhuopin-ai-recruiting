"""定夺队列「只出待答行」查询（`[Mac]0919Q`）。

复用 `dispatcher_answers.queue_rows`／`_status`，只加一层过滤＋显式回显隐藏行数。
钉住三条：① 全部已答时待答=0 但表头如实报总数 ② 混合待答/已答只出待答行 ③ 格式异常行不被吞（当待答保留）。
"""
from __future__ import annotations

from scripts import queue_pending as QP
from tests.test_dispatcher_answers import queue_md, row


def test_mixed_pending_and_answered_only_shows_pending():
    text = queue_md(
        [
            row("Q-01", "M2", "决策", "id1", "待答", "", question="待答问题"),
            row("Q-02", "M2", "决策", "id2", "已答", "a：定", question="已答问题"),
            row("Q-03", "M2", "决策", "id3", "作废", "作废：不做", question="作废问题"),
        ]
    )
    pending = QP.pending_rows(text)
    assert [r["编号"] for r in pending] == ["Q-01"]

    out = QP.render(text)
    assert "共 3 行，其中待答 1 行（隐藏 2 行已答/作废/远期）" in out
    assert "Q-01" in out
    assert "Q-02" not in out
    assert "Q-03" not in out


def test_all_answered_reports_zero_pending_but_true_total():
    text = queue_md(
        [
            row("Q-01", "M2", "决策", "id1", "已答", "a：定"),
            row("Q-02", "M2", "决策", "id2", "已答", "b：定"),
        ]
    )
    pending = QP.pending_rows(text)
    assert pending == []

    out = QP.render(text)
    lines = out.splitlines()
    assert lines[0] == "共 2 行，其中待答 0 行（隐藏 2 行已答/作废/远期）"
    assert len(lines) == 1  # 表头之外没有数据行


def test_malformed_row_is_kept_not_dropped():
    """状态列解析不出（列数异常）的行按待答保留，⛔ 不吞。"""
    malformed = "| Q-09 | M2 | 决策 | 列数异常只有五列的问题行 | 无推荐 |"
    text = queue_md(
        [
            row("Q-01", "M2", "决策", "id1", "已答", "a：定"),
            malformed,
        ]
    )
    pending = QP.pending_rows(text)
    ids = [r["编号"] for r in pending]
    assert "Q-09" in ids
    assert "Q-01" not in ids

    out = QP.render(text)
    assert "共 2 行，其中待答 1 行（隐藏 1 行已答/作废/远期）" in out
    assert "Q-09" in out


def test_truncate_adds_ellipsis_only_when_over_limit():
    short = "短问题"
    assert QP._truncate(short) == short
    long_text = "问" * 80
    truncated = QP._truncate(long_text)
    assert truncated.endswith("…")
    assert len(truncated) == QP.TRUNCATE_AT + 1
