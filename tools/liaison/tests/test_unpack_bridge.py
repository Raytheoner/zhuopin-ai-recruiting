"""P0·回件桥＋第九态。四态判定、原子写、审计 effect、`run_bridge` 编排。"""

from __future__ import annotations

from datetime import datetime

import pytest

from tools.liaison import session
from tools.liaison.storage import db as liaison_db
from tools.liaison.unpack.bridge import (
    ALREADY_PUSHED_PREFIX,
    NINTH_STATE_MARKER,
    BridgeDecision,
    compute_bridge_decision,
    compute_ninth_state_cell,
)

T0 = datetime(2026, 9, 10, 14, 3, 0, tzinfo=session.CHINA_TZ)


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


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


def _archive_outcome(msgid: str):
    from tools.liaison.archive import ArchiveOutcome

    return ArchiveOutcome(msgid=msgid, newly_archived=True, attachments=())


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


def test_resolve_reply_archive_relpath_reads_the_ledger_on_replay(tmp_path, conn):
    """0917W：重投时 `outcome.attachments` 不权威（`ArchiveOutcome` docstring），
    桥改读台账 `attachments_json` 记的那份材料，⛔ 不落 `正文.txt`。"""
    from tools.liaison.archive import ArchiveOutcome, InboundAttachment, archive_message
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    first = archive_message(
        conn,
        thread_id="ShaoPeiShen",
        msgid="msg-4",
        sender_userid="ShaoPeiShen",
        received_at="2026-09-17T10:00:00+08:00",
        msgtype="file",
        attachment=InboundAttachment(filename="批改表.xlsx", payload=b"\x00\x01bytes"),
        archive_root=tmp_path / "archive",
    )
    assert first.newly_archived and len(first.attachments) == 1

    replay = ArchiveOutcome(msgid="msg-4", newly_archived=False, attachments=())
    relpath = resolve_reply_archive_relpath(
        replay,
        thread_id="ShaoPeiShen",
        msgid="msg-4",
        received_at="2026-09-17T10:00:00+08:00",
        content="",
        archive_root=tmp_path / "archive",
        conn=conn,
    )
    assert relpath == "data/liaison/archive/ShaoPeiShen/20260917/msg-4__批改表.xlsx"
    assert sorted(p.name for p in (tmp_path / "archive").rglob("*") if p.is_file()) == ["msg-4__批改表.xlsx"]


def test_resolve_reply_archive_relpath_replay_of_a_text_message_still_snapshots(tmp_path, conn):
    """重投但台账里本来就没附件（纯文本）⇒ 仍走正文快照，与首投同一路径（幂等）。"""
    from tools.liaison.archive import ArchiveOutcome, archive_message
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    archive_message(
        conn,
        thread_id="ShaoPeiShen",
        msgid="msg-5",
        sender_userid="ShaoPeiShen",
        received_at="2026-09-17T10:00:00+08:00",
        msgtype="text",
        content="纯文本",
        archive_root=tmp_path / "archive",
    )
    replay = ArchiveOutcome(msgid="msg-5", newly_archived=False, attachments=())
    relpath = resolve_reply_archive_relpath(
        replay,
        thread_id="ShaoPeiShen",
        msgid="msg-5",
        received_at="2026-09-17T10:00:00+08:00",
        content="纯文本",
        archive_root=tmp_path / "archive",
        conn=conn,
    )
    assert relpath == "data/liaison/archive/ShaoPeiShen/20260917/msg-5__正文.txt"


def test_run_bridge_marks_and_records_all_side_effects(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    archive_root = tmp_path / "archive"
    signal_calls = []
    dispatch_calls = []

    outcome_result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-1",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="下周要两个嵌入式",
        outcome=_archive_outcome("msg-1"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=archive_root,
        now=T0,
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append((path, item)) or True,
        dispatch=lambda: dispatch_calls.append(True),
    )

    assert outcome_result == "marked"
    new_text = ledger_path.read_text(encoding="utf-8")
    assert "📨 回件已到，待拆件" in new_text
    audit_rows = conn.execute(
        "SELECT kind FROM liaison_unpack_audit WHERE msgid = 'msg-1'"
    ).fetchall()
    assert [row[0] for row in audit_rows] == ["bridge_marked"]
    assert len(signal_calls) == 1
    assert len(dispatch_calls) == 1


def test_run_bridge_signal_at_field_uses_format_instant_not_bare_isoformat(tmp_path, conn):
    """I7：`clear_signal_before`（signal.py:76-79）按字符串字典序比较 `at`/
    `checkpoint`，要求两者都是 `session.format_instant` 的定宽产出（固定
    `+08:00`、微秒定宽）。`T0` 的微秒恰好是 0——`datetime.isoformat()` 在这种
    情况下会把微秒整段省略（`timespec="auto"`），产出
    `"...T14:03:00+08:00"` 而不是 `"...T14:03:00.000000+08:00"`，两者按字符串
    比较不等价，会让恰好落在检查点那一刻的信号项被误清/误留。"""
    from tools.liaison.unpack.bridge import run_bridge

    assert T0.microsecond == 0, "本用例的判据依赖 T0 的微秒恰好是 0"

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER + _row("人事部#2", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    signal_calls = []

    run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-i7",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="下周要两个嵌入式",
        outcome=_archive_outcome("msg-i7"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append(item) or True,
        dispatch=lambda: None,
    )

    assert len(signal_calls) == 1
    at_value = signal_calls[0]["at"]
    assert at_value == session.format_instant(T0)
    assert at_value == "2026-09-10T14:03:00.000000+08:00"
    assert at_value != T0.isoformat(), (
        "回归判据：裸 isoformat() 在微秒为 0 时会省略微秒段，与 format_instant 的"
        "定宽产出不再逐字相等——正是 I7 要修的那个 bug"
    )


def test_run_bridge_never_raises_when_the_ledger_file_is_missing(tmp_path, conn):
    """design D10：桥的任何失败都只落 bridge_failed 审计，⛔ 不上抛
    ——归档与入队已经提交，本函数是"归档之后"的独立环节。"""
    from tools.liaison.unpack.bridge import run_bridge

    result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-missing",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        outcome=_archive_outcome("msg-missing"),
        route_admitted=True,
        ledger_path=tmp_path / "不存在的台账.md",
        archive_root=tmp_path / "archive",
        now=T0,
    )
    assert result == "bridge_failed"
    kinds = [
        row[0]
        for row in conn.execute(
            "SELECT kind FROM liaison_unpack_audit WHERE msgid = 'msg-missing'"
        ).fetchall()
    ]
    assert kinds == ["bridge_failed"]


def test_run_bridge_no_inflight_writes_no_signal_and_no_ledger_change(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    original = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 无需回复`")
    ledger_path.write_text(original, encoding="utf-8")
    signal_calls = []

    result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-2",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="新的岗位需求",
        outcome=_archive_outcome("msg-2"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append(item) or True,
    )
    assert result == "skipped_no_inflight"
    assert ledger_path.read_text(encoding="utf-8") == original
    assert signal_calls == []


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


def test_run_bridge_serial_violation_alerts_and_writes_no_signal(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER
        + _row("人事部#2", "汤丽萍", "`✅ 已推送 2026-09-08`")
        + _row("人事部#3", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    sink = RecordingSink()
    signal_calls = []

    result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-3",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="回复",
        outcome=_archive_outcome("msg-3"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append(item) or True,
        alert_sink=sink,
    )
    assert result == "refused_serial_violation"
    assert signal_calls == []
    assert sink.texts and "人事部#2" in sink.texts[0] and "人事部#3" in sink.texts[0]


def test_run_bridge_serial_violation_replay_does_not_resend_the_alert(tmp_path, conn):
    """回归 + 幂等：同一 msgid 的 refused_serial_violation 第一次真告警一次，
    重放（企微重投／ack 前崩溃重试）第二次起 ⛔ 不得再发——`effect_emit_alert`
    自身不是 `effect_*`（不落业务表），幂等性借 `effect_unpack_audit` 是否
    真插入新行来判定：命中 `effect_log` 短路 ⇒ 不是"第一次看到这个事件" ⇒
    不告警。台账在这条分支上从不改写，所以两次调用的判定输入完全相同，
    `compute_bridge_decision` 两次都会重新判成 refused_serial_violation
    ——这正是本用例要覆盖的重放场景（而不是"判定结果变了所以巧合没告警"）。
    """
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER
        + _row("人事部#2", "汤丽萍", "`✅ 已推送 2026-09-08`")
        + _row("人事部#3", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    original_ledger_text = ledger_path.read_text(encoding="utf-8")
    sink = RecordingSink()
    kwargs = dict(
        thread_id="TangLiPing",
        msgid="msg-3-replay",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="回复",
        outcome=_archive_outcome("msg-3-replay"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
        alert_sink=sink,
    )

    first = run_bridge(conn, **kwargs)
    assert first == "refused_serial_violation"
    assert len(sink.texts) == 1  # (a) 首次出现照常告警一次
    assert "人事部#2" in sink.texts[0] and "人事部#3" in sink.texts[0]

    # 台账在这条分支上确实不变——重放场景的判定输入与第一次逐字节相同。
    assert ledger_path.read_text(encoding="utf-8") == original_ledger_text

    second = run_bridge(conn, **kwargs)
    assert second == "refused_serial_violation"
    assert len(sink.texts) == 1  # (b) 重放 ⛔ 不再告警，仍是同一条

    # 审计行本身的幂等性不变：同一 msgid + kind 只此一行。
    audit_rows = conn.execute(
        "SELECT COUNT(*) FROM liaison_unpack_audit "
        "WHERE msgid = 'msg-3-replay' AND kind = 'bridge_refused_serial_violation'"
    ).fetchone()[0]
    assert audit_rows == 1


def test_run_bridge_same_msgid_replayed_does_not_duplicate_anything(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    kwargs = dict(
        thread_id="TangLiPing",
        msgid="msg-dup",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        outcome=_archive_outcome("msg-dup"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
    )
    first = run_bridge(conn, **kwargs)
    text_after_first = ledger_path.read_text(encoding="utf-8")
    second = run_bridge(conn, **kwargs)

    assert first == "marked"
    assert second == "skipped_already_marked"
    assert ledger_path.read_text(encoding="utf-8") == text_after_first
    marked_rows = conn.execute(
        "SELECT COUNT(*) FROM liaison_unpack_audit "
        "WHERE msgid = 'msg-dup' AND kind = 'bridge_marked'"
    ).fetchone()[0]
    assert marked_rows == 1


def test_run_bridge_already_marked_row_still_signals_and_dispatches_for_a_new_msgid(
    tmp_path, conn
):
    """spec「第九态期间又来一条新消息」：台账不变，但信号追加与起活仍要发生
    ——`skipped_already_marked` 不在 `_NO_FURTHER_ACTION_OUTCOMES` 里。"""
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    base_kwargs = dict(
        thread_id="TangLiPing",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
    )
    run_bridge(conn, msgid="msg-first", outcome=_archive_outcome("msg-first"), **base_kwargs)
    text_after_first = ledger_path.read_text(encoding="utf-8")

    signal_calls = []
    dispatch_calls = []
    result = run_bridge(
        conn,
        msgid="msg-second",
        outcome=_archive_outcome("msg-second"),
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append(item) or True,
        dispatch=lambda: dispatch_calls.append(True),
        **base_kwargs,
    )
    assert result == "skipped_already_marked"
    assert ledger_path.read_text(encoding="utf-8") == text_after_first
    assert len(signal_calls) == 1 and signal_calls[0]["msgid"] == "msg-second"
    assert len(dispatch_calls) == 1


def test_run_bridge_not_admitted_does_nothing(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    original = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`")
    ledger_path.write_text(original, encoding="utf-8")

    result = run_bridge(
        conn,
        thread_id="someone-else",
        msgid="msg-outsider",
        sender_userid="someone-else",
        sender_name=None,
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        outcome=_archive_outcome("msg-outsider"),
        route_admitted=False,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
    )
    assert result == "not_admitted"
    assert ledger_path.read_text(encoding="utf-8") == original
    assert conn.execute("SELECT COUNT(*) FROM liaison_unpack_audit").fetchone()[0] == 0
