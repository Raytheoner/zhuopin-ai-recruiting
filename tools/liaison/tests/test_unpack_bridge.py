"""P0·回件桥＋第九态。四态判定、原子写、审计 effect、`run_bridge` 编排。"""

from __future__ import annotations

from datetime import datetime

import pytest

from tools.liaison import session
from tools.liaison.unpack.bridge import (
    ALREADY_PUSHED_PREFIX,
    NINTH_STATE_MARKER,
    BridgeDecision,
    compute_bridge_decision,
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


LEDGER_HEADER = (
    "| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |\n"
    "|---|---|---|---|---|---|\n"
)


def _row(number: str, recipient: str, status: str) -> str:
    return f"| `{number}` | 2026-09-09 | {recipient} | 事项 | 无 | {status} |\n"


def test_no_inflight_row_leaves_the_ledger_untouched():
    """D1：发送人在台账里没有任何在途信 ⇒ skipped_no_inflight，台账逐字节不变。"""
    ledger = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 无需回复`")
    decision = compute_bridge_decision(
        ledger, sender_name="汤丽萍", archived_relpath="x/y.txt", now_cst=T0
    )
    assert decision.outcome == "skipped_no_inflight"
    assert decision.new_ledger_text is None
    assert decision.matched_letter_numbers == ()


def test_sender_not_in_ledger_at_all_is_also_no_inflight():
    ledger = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`")
    decision = compute_bridge_decision(
        ledger, sender_name="邵培申", archived_relpath="x/y.txt", now_cst=T0
    )
    assert decision.outcome == "skipped_no_inflight"


def test_two_inflight_rows_refuse_and_list_both_numbers():
    """D2：同一收信人 ≥2 行在途 ⇒ 拒标，涉及编号可枚举，台账不变。"""
    ledger = (
        LEDGER_HEADER
        + _row("人事部#2", "汤丽萍", "`✅ 已推送 2026-09-08`")
        + _row("人事部#3", "汤丽萍", "`✅ 已推送 2026-09-09`")
    )
    decision = compute_bridge_decision(
        ledger, sender_name="汤丽萍", archived_relpath="x/y.txt", now_cst=T0
    )
    assert decision.outcome == "refused_serial_violation"
    assert decision.new_ledger_text is None
    assert set(decision.matched_letter_numbers) == {"人事部#2", "人事部#3"}


def test_exactly_one_inflight_row_gets_marked_and_other_rows_are_untouched():
    other_row = _row("人事部#9", "邵培申", "`✅ 已推送 2026-09-01`")
    ledger = LEDGER_HEADER + other_row + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`")
    decision = compute_bridge_decision(
        ledger,
        sender_name="汤丽萍",
        archived_relpath="data/liaison/archive/x/20260910/y__正文.txt",
        now_cst=T0,
    )
    assert decision.outcome == "marked"
    assert decision.matched_letter_numbers == ("人事部#1",)
    new_lines = decision.new_ledger_text.splitlines(keepends=True)
    old_lines = ledger.splitlines(keepends=True)
    # 表头与其它行逐字节不变。
    assert new_lines[0] == old_lines[0]
    assert new_lines[1] == old_lines[1]
    assert new_lines[2] == old_lines[2]  # 邵培申那一行
    assert new_lines[3] == old_lines[3].replace(
        "`✅ 已推送 2026-09-09`",
        "📨 回件已到，待拆件 2026-09-10 14:03 CST"
        "（值守服务自动标记，入信归档 "
        "`data/liaison/archive/x/20260910/y__正文.txt`；"
        "仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一） "
        "━━━ 原状态 ━━━ `✅ 已推送 2026-09-09`",
    )


def test_already_ninth_state_row_is_skipped_and_ledger_unchanged():
    ninth_state_cell = (
        "`📨 回件已到，待拆件 2026-09-10 14:03 CST"
        "（值守服务自动标记，入信归档 `data/liaison/archive/x/20260910/y__正文.txt`；"
        "仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一） "
        "━━━ 原状态 ━━━ ✅ 已推送 2026-09-09`"
    )
    ledger = LEDGER_HEADER + _row("人事部#1", "汤丽萍", ninth_state_cell)
    decision = compute_bridge_decision(
        ledger, sender_name="汤丽萍", archived_relpath="whatever/new.txt", now_cst=T0
    )
    assert decision.outcome == "skipped_already_marked"
    assert decision.new_ledger_text is None
    assert decision.matched_letter_numbers == ("人事部#1",)


def test_write_ledger_atomic_replaces_file_content(tmp_path):
    from tools.liaison.unpack.bridge import write_ledger_atomic

    target = tmp_path / "README-跟进信清单.md"
    target.write_text("旧内容", encoding="utf-8")
    write_ledger_atomic(target, "新内容")
    assert target.read_text(encoding="utf-8") == "新内容"


def test_write_ledger_atomic_leaves_no_temp_file_behind(tmp_path):
    from tools.liaison.unpack.bridge import write_ledger_atomic

    target = tmp_path / "README-跟进信清单.md"
    write_ledger_atomic(target, "内容")
    assert list(tmp_path.iterdir()) == [target]


def test_write_ledger_atomic_propagates_failure_to_the_caller(tmp_path):
    from tools.liaison.unpack.bridge import write_ledger_atomic

    missing_parent = tmp_path / "no-such-dir" / "README-跟进信清单.md"
    with pytest.raises(OSError):
        write_ledger_atomic(missing_parent, "内容")


def test_resolve_reply_archive_relpath_uses_the_attachment_when_present(tmp_path):
    from tools.liaison.archive import ArchiveOutcome
    from tools.liaison.attachments import StoredAttachment
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    outcome = ArchiveOutcome(
        msgid="msg-1",
        newly_archived=True,
        attachments=(
            StoredAttachment(
                filename="简历.pdf",
                relative_path="ShaoPeiShen/20260910/msg-1__简历.pdf",
                byte_length=10,
                sha256="deadbeef",
            ),
        ),
    )
    relpath = resolve_reply_archive_relpath(
        outcome,
        thread_id="ShaoPeiShen",
        msgid="msg-1",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文不重要",
        archive_root=tmp_path,
    )
    assert relpath == "data/liaison/archive/ShaoPeiShen/20260910/msg-1__简历.pdf"
    assert list(tmp_path.rglob("*")) == []  # 没有额外落盘


def test_resolve_reply_archive_relpath_snapshots_content_when_no_attachment(tmp_path):
    from tools.liaison.archive import ArchiveOutcome
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    outcome = ArchiveOutcome(msgid="msg-2", newly_archived=True, attachments=())
    relpath = resolve_reply_archive_relpath(
        outcome,
        thread_id="ShaoPeiShen",
        msgid="msg-2",
        received_at="2026-09-16T13:55:09+08:00",
        content="下周要两个嵌入式",
        archive_root=tmp_path,
    )
    assert relpath == "data/liaison/archive/ShaoPeiShen/20260916/msg-2__正文.txt"
    stored_file = tmp_path / "ShaoPeiShen" / "20260916" / "msg-2__正文.txt"
    assert stored_file.read_bytes() == "下周要两个嵌入式".encode("utf-8")


def test_resolve_reply_archive_relpath_is_idempotent_on_repeated_calls(tmp_path):
    from tools.liaison.archive import ArchiveOutcome
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    outcome = ArchiveOutcome(msgid="msg-3", newly_archived=True, attachments=())
    kwargs = dict(
        thread_id="ShaoPeiShen",
        msgid="msg-3",
        received_at="2026-09-16T13:55:09+08:00",
        content="重复调用同一条消息",
        archive_root=tmp_path,
    )
    first = resolve_reply_archive_relpath(outcome, **kwargs)
    second = resolve_reply_archive_relpath(outcome, **kwargs)
    assert first == second
