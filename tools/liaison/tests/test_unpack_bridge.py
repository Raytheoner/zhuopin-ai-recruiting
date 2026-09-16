"""P0·回件桥＋第九态。四态判定、原子写、审计 effect、`run_bridge` 编排。"""

from __future__ import annotations

from datetime import datetime

import pytest

from tools.liaison import session
from tools.liaison.unpack.bridge import (
    ALREADY_PUSHED_PREFIX,
    NINTH_STATE_MARKER,
    compute_ninth_state_cell,
)

T0 = datetime(2026, 9, 10, 14, 3, 0, tzinfo=session.CHINA_TZ)


def test_ninth_state_cell_matches_the_literal_design_template():
    """design.md D9 的逐字模板：标记＋时刻＋归档路径＋分隔符＋原状态原文。"""
    result = compute_ninth_state_cell(
        "`✅ 已推送 2026-09-09`",
        archived_relpath="data/liaison/archive/ShaoPeiShen/20260910/msg__正文.txt",
        now_cst=T0,
    )
    assert result == (
        "📨 回件已到，待拆件 2026-09-10 14:03 CST"
        "（值守服务自动标记，入信归档 "
        "`data/liaison/archive/ShaoPeiShen/20260910/msg__正文.txt`；"
        "仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一） "
        "━━━ 原状态 ━━━ `✅ 已推送 2026-09-09`"
    )


def test_ninth_state_cell_preserves_original_text_verbatim_including_backticks():
    """「原状态列的完整原文」＝原样保留，⛔ 不剥离台账既有的反引号包裹。"""
    result = compute_ninth_state_cell(
        "`✅ 已推送 2026-09-09`", archived_relpath="x/y.txt", now_cst=T0
    )
    assert result.endswith("━━━ 原状态 ━━━ `✅ 已推送 2026-09-09`")


def test_ninth_state_cell_strips_only_the_table_cell_padding_whitespace():
    """调用方从 `split(\"|\")` 拿到的单元格通常带一层 padding 空格，
    只去掉这一层，不去掉原文内部的任何字符。"""
    result = compute_ninth_state_cell(
        "  ✅ 已推送 2026-09-09  ", archived_relpath="x/y.txt", now_cst=T0
    )
    assert result.endswith("━━━ 原状态 ━━━ ✅ 已推送 2026-09-09")


def test_ninth_state_marker_constant_matches_the_spec_literal_text():
    assert NINTH_STATE_MARKER == "📨 回件已到，待拆件"


def test_already_pushed_prefix_constant_matches_the_ledger_convention():
    assert ALREADY_PUSHED_PREFIX == "✅ 已推送"
