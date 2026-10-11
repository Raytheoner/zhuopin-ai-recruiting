"""effect_record_approval / effect_revise_offer 的状态机、逐级与幂等（tasks 3.2/3.3）。"""
from __future__ import annotations

import sqlite3
import threading

import pytest

import app.graph.offer_nodes as offer_nodes
from app.graph.offer_nodes import effect_record_approval, effect_revise_offer
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer import (
    ApprovalAlreadyRecordedError,
    ApprovalChainNotConfiguredError,
    ApprovalOutOfOrderError,
    NotAnApproverError,
    OfferNotEditableError,
    OfferNotPendingApprovalError,
)
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn, *, chain=True, stage="interview"):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, status, uploaded_by) "
        "VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1',?)", (stage,)
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()
    if chain:
        put_approval_chain(conn, job_id="j1", chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ], updated_by="hr1")


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-nodes-approval.db"))
    init_schema(c)
    _seed(c)
    yield c
    c.close()


def _offer(conn, *, status="pending_approval", round_no=1):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES ('o1','app1','j1','研发部','2026-11-01','李四',?,?, 'hr1')", (status, round_no)
    )
    conn.commit()
    return "o1"


def _approve(conn, *, level, approver, decision="approved", comment=None, round_no=1):
    return effect_record_approval(
        conn, thread_id="o1", business_key=f"{round_no}:{level}:{approver}:{decision}",
        offer_id="o1", level=level, approver=approver, decision=decision, comment=comment,
    )


def _status(conn):
    return conn.execute("SELECT status, approval_round FROM offer WHERE id = 'o1'").fetchone()


def test_two_level_approval_ends_approved(conn):
    _offer(conn)
    assert _approve(conn, level=1, approver="mgr")["status"] == "pending_approval"
    assert _status(conn) == ("pending_approval", 1)
    assert _approve(conn, level=2, approver="gm")["status"] == "approved"
    assert _status(conn) == ("approved", 1)
    rows = conn.execute("SELECT level, approver, decision, at FROM offer_approval WHERE offer_id='o1' ORDER BY level").fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [(1, "mgr", "approved"), (2, "gm", "approved")]
    assert all(r[3] for r in rows)


def test_first_level_return_blocks_next_level(conn):
    _offer(conn)
    result = _approve(conn, level=1, approver="mgr", decision="returned", comment="入职日期待与候选人确认")
    assert result["status"] == "needs_revision"
    assert _status(conn) == ("needs_revision", 1)
    with pytest.raises(OfferNotPendingApprovalError):
        _approve(conn, level=2, approver="gm")
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 1
    assert conn.execute("SELECT comment FROM offer_approval WHERE level=1").fetchone()[0] == "入职日期待与候选人确认"


def test_return_requires_comment(conn):
    _offer(conn)
    with pytest.raises(ValueError, match="退回必须填意见"):
        _approve(conn, level=1, approver="mgr", decision="returned", comment="  ")


def test_non_approver_is_rejected(conn):
    _offer(conn)
    with pytest.raises(NotAnApproverError):
        _approve(conn, level=1, approver="hr1")
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0


def test_skipping_a_level_is_rejected(conn):
    _offer(conn)
    with pytest.raises(ApprovalOutOfOrderError, match="第 1 级尚未通过"):
        _approve(conn, level=2, approver="gm")
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0


def test_unconfigured_chain_fails_closed(tmp_path):
    c = get_connection(str(tmp_path / "no-chain.db"))
    init_schema(c)
    _seed(c, chain=False)
    _offer(c)
    with pytest.raises(ApprovalChainNotConfiguredError, match="尚未配置审批人"):
        _approve(c, level=1, approver="mgr")
    assert c.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0
    c.close()


def test_approval_rejected_when_not_pending(conn):
    _offer(conn, status="approved")
    with pytest.raises(OfferNotPendingApprovalError):
        _approve(conn, level=1, approver="mgr")


def test_replay_same_decision_is_noop(conn):
    _offer(conn)
    assert _approve(conn, level=1, approver="mgr") is not None
    assert _approve(conn, level=1, approver="mgr") is None
    assert _approve(conn, level=2, approver="gm") is not None
    assert _approve(conn, level=2, approver="gm") is None
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 2
    assert _status(conn) == ("approved", 1)
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE node_name='effect_record_approval' AND thread_id='o1'").fetchone()[0] == 2


def test_duplicate_row_hits_unique_index_and_maps_to_domain_error(conn):
    _offer(conn)
    conn.execute("INSERT INTO offer_approval (id, offer_id, round, level, approver, decision, comment) VALUES ('other','o1',1,1,'mgr','returned','先退回')")
    conn.commit()
    with pytest.raises(ApprovalAlreadyRecordedError, match="已记录"):
        _approve(conn, level=1, approver="mgr", decision="returned", comment="重复")


def test_revise_bumps_round_and_restarts_from_level_one(conn):
    _offer(conn)
    _approve(conn, level=1, approver="mgr", decision="returned", comment="改入职日期")
    assert effect_revise_offer(conn, thread_id="o1", business_key="req-2", offer_id="o1", department="研发部", start_date="2026-12-01", report_to="李四", note="入职日期已与候选人确认", updated_by="hr1") == 2
    assert _status(conn) == ("pending_approval", 2)
    assert conn.execute("SELECT COUNT(*) FROM offer_approval WHERE round=1").fetchone()[0] == 1
    with pytest.raises(ApprovalOutOfOrderError):
        _approve(conn, level=2, approver="gm", round_no=2)
    _approve(conn, level=1, approver="mgr", round_no=2)
    _approve(conn, level=2, approver="gm", round_no=2)
    assert _status(conn) == ("approved", 2)
    assert conn.execute("SELECT COUNT(*) FROM offer_approval WHERE offer_id='o1'").fetchone()[0] == 3


def test_revise_replay_is_noop(conn):
    _offer(conn)
    _approve(conn, level=1, approver="mgr", decision="returned", comment="改")
    kwargs = dict(thread_id="o1", business_key="req-2", offer_id="o1", department="研发部", start_date="2026-12-01", report_to="李四", note=None, updated_by="hr1")
    assert effect_revise_offer(conn, **kwargs) == 2
    assert effect_revise_offer(conn, **kwargs) is None
    assert _status(conn) == ("pending_approval", 2)


def test_old_round_approval_replay_after_revision_is_noop(conn):
    """第 1 轮成功退回键在第 2 轮已待审批时重放，整组事实保持不变。"""
    _offer(conn)
    returned_key = "1:1:mgr:returned"
    assert effect_record_approval(
        conn, thread_id="o1", business_key=returned_key, offer_id="o1",
        level=1, approver="mgr", decision="returned", comment="第 1 轮需修改",
    ) is not None
    assert effect_revise_offer(
        conn, thread_id="o1", business_key="req-round-2", offer_id="o1",
        department="研发部", start_date="2026-12-01", report_to="李四",
        note="第 2 轮修改完成", updated_by="hr1",
    ) == 2

    def snapshot():
        return (
            conn.execute("SELECT * FROM offer WHERE id='o1'").fetchall(),
            conn.execute("SELECT * FROM offer_approval WHERE offer_id='o1' ORDER BY round, level").fetchall(),
            conn.execute("SELECT * FROM application_stage_history WHERE application_id='app1' ORDER BY id").fetchall(),
            conn.execute("SELECT * FROM effect_log WHERE thread_id='o1' ORDER BY effect_key").fetchall(),
        )

    before = snapshot()
    assert effect_record_approval(
        conn, thread_id="o1", business_key=returned_key, offer_id="o1",
        level=1, approver="mgr", decision="returned", comment="重放不应覆盖",
    ) is None
    assert snapshot() == before
    assert _status(conn) == ("pending_approval", 2)


@pytest.mark.parametrize("comment", [None, "", "   ", " \t\n"])
def test_cached_returned_approval_still_requires_non_empty_comment(conn, comment):
    """请求字段校验必须发生在缓存短路前，不能用成功键绕过退回意见要求。"""
    _offer(conn)
    key = "1:1:mgr:returned"
    assert effect_record_approval(
        conn, thread_id="o1", business_key=key, offer_id="o1",
        level=1, approver="mgr", decision="returned", comment="需修改入职日期",
    ) is not None

    def snapshot():
        return (
            conn.execute("SELECT * FROM offer WHERE id='o1'").fetchall(),
            conn.execute(
                "SELECT * FROM offer_approval WHERE offer_id='o1' ORDER BY round, level"
            ).fetchall(),
            conn.execute(
                "SELECT * FROM application_stage_history WHERE application_id='app1' ORDER BY id"
            ).fetchall(),
            conn.execute(
                "SELECT * FROM effect_log WHERE thread_id='o1' ORDER BY effect_key"
            ).fetchall(),
        )

    before = snapshot()
    with pytest.raises(ValueError, match="退回必须填意见"):
        effect_record_approval(
            conn, thread_id="o1", business_key=key, offer_id="o1",
            level=1, approver="mgr", decision="returned", comment=comment,
        )
    assert snapshot() == before


@pytest.mark.parametrize(
    ("decision", "approver", "message"),
    [
        ("unknown", "mgr", "decision 只能是 approved/returned"),
        ("approved", "  ", "approver 不能为空"),
    ],
)
def test_cached_approval_still_validates_decision_and_approver(
    conn, decision, approver, message
):
    _offer(conn)
    key = "1:1:mgr:approved"
    assert effect_record_approval(
        conn, thread_id="o1", business_key=key, offer_id="o1",
        level=1, approver="mgr", decision="approved",
    ) is not None
    before = (
        conn.execute("SELECT * FROM offer WHERE id='o1'").fetchall(),
        conn.execute("SELECT * FROM offer_approval WHERE offer_id='o1'").fetchall(),
        conn.execute("SELECT * FROM application_stage_history WHERE application_id='app1'").fetchall(),
        conn.execute("SELECT * FROM effect_log WHERE thread_id='o1'").fetchall(),
    )

    with pytest.raises(ValueError, match=message):
        effect_record_approval(
            conn, thread_id="o1", business_key=key, offer_id="o1",
            level=1, approver=approver, decision=decision,
        )

    after = (
        conn.execute("SELECT * FROM offer WHERE id='o1'").fetchall(),
        conn.execute("SELECT * FROM offer_approval WHERE offer_id='o1'").fetchall(),
        conn.execute("SELECT * FROM application_stage_history WHERE application_id='app1'").fetchall(),
        conn.execute("SELECT * FROM effect_log WHERE thread_id='o1'").fetchall(),
    )
    assert after == before


@pytest.mark.parametrize(
    ("parameter", "overrides"),
    [
        ("level", {"level": 2}),
        ("approver", {"approver": "gm"}),
        ("decision", {"decision": "returned", "comment": "合法退回意见"}),
    ],
)
@pytest.mark.parametrize("cached", [False, True], ids=["fresh", "cached"])
def test_approval_key_rejects_parameter_mismatch_without_changing_facts(
    conn, parameter, overrides, cached
):
    """同 thread/key 只换实际参数时，fresh 和缓存命中前都必须拒绝。"""
    _offer(conn)
    key = "1:1:mgr:approved"
    if cached:
        assert effect_record_approval(
            conn, thread_id="o1", business_key=key, offer_id="o1",
            level=1, approver="mgr", decision="approved",
        ) is not None

    def snapshot():
        return (
            conn.execute("SELECT * FROM offer WHERE id='o1'").fetchall(),
            conn.execute(
                "SELECT * FROM offer_approval WHERE offer_id='o1' ORDER BY round, level"
            ).fetchall(),
            conn.execute(
                "SELECT * FROM application_stage_history WHERE application_id='app1' ORDER BY id"
            ).fetchall(),
            conn.execute(
                "SELECT * FROM effect_log WHERE thread_id='o1' ORDER BY effect_key"
            ).fetchall(),
        )

    before = snapshot()
    kwargs = {"level": 1, "approver": "mgr", "decision": "approved"}
    kwargs.update(overrides)
    with pytest.raises(ValueError, match="business_key 必须匹配"):
        effect_record_approval(
            conn, thread_id="o1", business_key=key, offer_id="o1", **kwargs
        )
    assert snapshot() == before
    if not cached:
        assert conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE thread_id='o1' "
            "AND node_name='effect_record_approval'"
        ).fetchone()[0] == 0
    assert parameter in {"level", "approver", "decision"}


def test_approval_key_with_colon_in_approver_supports_fresh_and_replay(conn):
    approver = "team:lead"
    upsert_account(conn, username=approver, password="s3cret!")
    put_approval_chain(conn, job_id="j1", chain=[
        {"level": 1, "approver_account_ids": [approver]},
        {"level": 2, "approver_account_ids": ["gm"]},
    ], updated_by="hr1")
    _offer(conn)
    key = f"1:1:{approver}:approved"
    kwargs = dict(
        thread_id="o1", business_key=key, offer_id="o1", level=1,
        approver=approver, decision="approved",
    )

    assert effect_record_approval(conn, **kwargs)["approver"] == approver
    before = (
        conn.execute("SELECT * FROM offer WHERE id='o1'").fetchall(),
        conn.execute("SELECT * FROM offer_approval WHERE offer_id='o1'").fetchall(),
        conn.execute("SELECT * FROM effect_log WHERE thread_id='o1'").fetchall(),
    )
    assert effect_record_approval(conn, **kwargs) is None
    after = (
        conn.execute("SELECT * FROM offer WHERE id='o1'").fetchall(),
        conn.execute("SELECT * FROM offer_approval WHERE offer_id='o1'").fetchall(),
        conn.execute("SELECT * FROM effect_log WHERE thread_id='o1'").fetchall(),
    )
    assert after == before


@pytest.mark.parametrize("effect_name", ["approval", "revise"])
@pytest.mark.parametrize("cached", [False, True])
def test_offer_effect_rejects_swapped_business_object_before_new_or_cached_key(
    conn, effect_name, cached
):
    """同一 thread_id/business_key 换实际 offer_id，缓存未命中也要绑定拒绝。"""
    _offer(conn, status="pending_approval")
    # 建立另一个真实 Offer，确保验证发生在读取或命中缓存之前。
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2','李四')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, status, uploaded_by) "
        "VALUES ('r2','j1','synthetic','b.pdf','sha2','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app2','c2','j1','r2','interview')"
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES ('o2','app2','j1','研发部','2026-11-01','王五',?,1,'hr1')",
        ("needs_revision" if effect_name == "revise" else "pending_approval",),
    )
    conn.commit()

    if cached and effect_name == "approval":
        assert _approve(conn, level=1, approver="mgr") is not None
        business_key = "1:1:mgr:approved"
    elif effect_name == "revise":
        assert _approve(conn, level=1, approver="mgr", decision="returned", comment="改") is not None
        revise = dict(
            thread_id="o1", business_key="req-swap", offer_id="o1",
            department="研发部", start_date="2026-12-01", report_to="李四",
            note=None, updated_by="hr1",
        )
        if cached:
            assert effect_revise_offer(conn, **revise) == 2
            # 恢复 o2 为同类可编辑状态，使得仅靠业务状态无法解释拒绝。
            conn.execute("UPDATE offer SET status='needs_revision' WHERE id='o2'")
            conn.commit()
        business_key = "req-swap"
    else:
        business_key = "1:1:mgr:approved"

    before = (
        conn.execute("SELECT * FROM offer ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM offer_approval ORDER BY offer_id, round, level").fetchall(),
        conn.execute("SELECT * FROM application_stage_history ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall(),
    )
    with pytest.raises(ValueError, match="thread_id must match offer_id"):
        if effect_name == "approval":
            effect_record_approval(
                conn, thread_id="o1", business_key=business_key, offer_id="o2",
                level=1, approver="mgr", decision="approved",
            )
        else:
            effect_revise_offer(
                conn, thread_id="o1", business_key=business_key, offer_id="o2",
                department="研发部", start_date="2026-12-15", report_to="李四",
                note="不应写", updated_by="hr1",
            )
    after = (
        conn.execute("SELECT * FROM offer ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM offer_approval ORDER BY offer_id, round, level").fetchall(),
        conn.execute("SELECT * FROM application_stage_history ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall(),
    )
    assert after == before


def test_sequential_second_revision_request_is_rejected_without_any_changes(conn):
    """req-B follows successful req-A; the new pending round must reject revision."""
    _offer(conn)
    _approve(conn, level=1, approver="mgr", decision="returned", comment="需调整")

    req_a = dict(
        thread_id="o1", business_key="req-A", offer_id="o1",
        department="研发部", start_date="2026-12-01", report_to="李四",
        note="req-A 修改", updated_by="hr1",
    )
    assert effect_revise_offer(conn, **req_a) == 2

    def snapshot():
        return {
            "offer": conn.execute(
                "SELECT application_id, job_id, department, start_date, report_to, note, "
                "status, approval_round, created_by, updated_by FROM offer WHERE id='o1'"
            ).fetchall(),
            "approvals": conn.execute(
                "SELECT id, offer_id, round, level, approver, decision, comment, at "
                "FROM offer_approval WHERE offer_id='o1' ORDER BY round, level"
            ).fetchall(),
            "history": conn.execute(
                "SELECT * FROM application_stage_history WHERE application_id='app1' ORDER BY id"
            ).fetchall(),
            "effects": conn.execute(
                "SELECT effect_key, thread_id, node_name, business_key, applied_at "
                "FROM effect_log ORDER BY effect_key"
            ).fetchall(),
        }

    before = snapshot()
    req_b = {**req_a, "business_key": "req-B", "start_date": "2027-01-01", "note": "req-B"}
    with pytest.raises(OfferNotEditableError):
        effect_revise_offer(conn, **req_b)
    assert snapshot() == before
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id='o1' AND node_name='effect_revise_offer'"
    ).fetchone()[0] == 1


def test_revise_requires_needs_revision(conn):
    _offer(conn)
    with pytest.raises(OfferNotEditableError, match="只有「待修改」可以修改"):
        effect_revise_offer(conn, thread_id="o1", business_key="req-2", offer_id="o1", department="研发部", start_date="2026-12-01", report_to="李四", note=None, updated_by="hr1")


def test_offer_table_still_has_no_salary_column(conn):
    columns = [row[1] for row in conn.execute("PRAGMA table_info(offer)")]
    forbidden = ("salary", "pay", "compensation", "bonus", "薪")
    assert not [c for c in columns if any(k in c.lower() or k in c for k in forbidden)]


@pytest.mark.parametrize("effect_name", ["approval", "revise"])
@pytest.mark.parametrize("cached", [False, True])
def test_offer_effect_rejects_thread_mismatch_before_new_or_cached_key(conn, effect_name, cached):
    _offer(conn, status="needs_revision" if effect_name == "revise" else "pending_approval")
    if cached:
        if effect_name == "revise":
            conn.execute("UPDATE offer SET status='pending_approval', approval_round=2 WHERE id='o1'")
            conn.commit()
        if effect_name == "approval":
            _approve(conn, level=1, approver="mgr")
            key = "1:1:mgr:approved"
        else:
            conn.execute("UPDATE offer SET status='pending_approval', approval_round=1 WHERE id='o1'")
            conn.commit()
            _approve(conn, level=1, approver="mgr", decision="returned", comment="改")
            effect_revise_offer(
                conn, thread_id="o1", business_key="req-1", offer_id="o1",
                department="研发部", start_date="2026-12-01", report_to="李四",
                note=None, updated_by="hr1",
            )
            key = "req-1"
    else:
        key = "1:1:mgr:approved" if effect_name == "approval" else "req-1"
    before_facts = conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0]
    before_logs = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]
    with pytest.raises(ValueError, match="thread_id must match offer_id"):
        if effect_name == "approval":
            effect_record_approval(
                conn, thread_id="wrong", business_key=key, offer_id="o1",
                level=1, approver="mgr", decision="approved",
            )
        else:
            effect_revise_offer(
                conn, thread_id="wrong", business_key=key, offer_id="o1",
                department="研发部", start_date="2026-12-01", report_to="李四",
                note=None, updated_by="hr1",
            )
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == before_facts
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == before_logs


def test_approval_key_must_describe_current_round_and_replays_old_round(conn):
    _offer(conn)
    with pytest.raises(ValueError, match="business_key 必须匹配"):
        effect_record_approval(
            conn, thread_id="o1", business_key="9:1:mgr:approved", offer_id="o1",
            level=1, approver="mgr", decision="approved",
        )
    assert _approve(conn, level=1, approver="mgr")
    assert _approve(conn, level=1, approver="mgr") is None


def test_fresh_old_round_approval_key_is_rejected_after_revision(conn):
    _offer(conn, round_no=2)
    with pytest.raises(ValueError, match="business_key 必须匹配"):
        effect_record_approval(
            conn, thread_id="o1", business_key="1:1:mgr:approved", offer_id="o1",
            level=1, approver="mgr", decision="approved",
        )
    assert _status(conn) == ("pending_approval", 2)
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_record_approval'"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("blank_key", ["", "   "])
def test_revise_rejects_blank_request_key(conn, blank_key):
    _offer(conn)
    _approve(conn, level=1, approver="mgr", decision="returned", comment="改")
    with pytest.raises(ValueError, match="business_key 不能为空"):
        effect_revise_offer(
            conn, thread_id="o1", business_key=blank_key, offer_id="o1",
            department="研发部", start_date="2026-12-01", report_to="李四",
            note=None, updated_by="hr1",
        )
    assert _status(conn) == ("needs_revision", 1)
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE node_name='effect_revise_offer'").fetchone()[0] == 0


@pytest.mark.parametrize("effect_name", ["approval", "revise"])
def test_offer_effect_same_key_serializes_across_connections(tmp_path, monkeypatch, effect_name):
    """实际 Offer effect 持有写锁覆盖业务读取，竞争重放只落一次事实。"""
    db_path = str(tmp_path / f"{effect_name}-race.db")
    setup = get_connection(db_path)
    init_schema(setup)
    _seed(setup)
    _offer(setup)
    setup.close()
    if effect_name == "revise":
        returned = get_connection(db_path)
        _approve(returned, level=1, approver="mgr", decision="returned", comment="需修改")
        returned.close()

    first = get_connection(db_path)
    second = get_connection(db_path)
    second.execute("PRAGMA busy_timeout=0")
    first_read = threading.Event()
    release_first = threading.Event()
    first_result = []
    first_errors = []
    original_get_offer = offer_nodes.get_offer

    def pause_after_first_read(connection, offer_id):
        result = original_get_offer(connection, offer_id)
        if threading.current_thread().name == "offer-first":
            first_read.set()
            assert release_first.wait(5)
        return result

    monkeypatch.setattr(offer_nodes, "get_offer", pause_after_first_read)

    def first_call():
        try:
            if effect_name == "approval":
                first_result.append(_approve(first, level=1, approver="mgr"))
            else:
                first_result.append(effect_revise_offer(
                    first, thread_id="o1", business_key="same-request", offer_id="o1",
                    department="研发部", start_date="2026-12-01", report_to="李四",
                    note="修改后", updated_by="hr1",
                ))
        except BaseException as exc:  # surfaced in the test thread below
            first_errors.append(exc)

    worker = threading.Thread(target=first_call, name="offer-first")
    worker.start()
    try:
        assert first_read.wait(5), "首请求未进入业务读取"
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            if effect_name == "approval":
                _approve(second, level=1, approver="mgr")
            else:
                effect_revise_offer(
                    second, thread_id="o1", business_key="same-request", offer_id="o1",
                    department="研发部", start_date="2026-12-01", report_to="李四",
                    note="修改后", updated_by="hr1",
                )
    finally:
        release_first.set()
        worker.join(5)
        second.close()

    assert not worker.is_alive()
    assert not first_errors
    assert len(first_result) == 1 and first_result[0] is not None
    if effect_name == "approval":
        assert _status(first) == ("pending_approval", 1)
        assert first.execute("SELECT round, level, approver, decision FROM offer_approval WHERE offer_id='o1'").fetchall() == [(1, 1, "mgr", "approved")]
        assert _approve(first, level=1, approver="mgr") is None
        node = "effect_record_approval"
        assert first.execute("SELECT COUNT(*) FROM offer_approval WHERE offer_id='o1'").fetchone()[0] == 1
    else:
        assert _status(first) == ("pending_approval", 2)
        assert first.execute("SELECT round, level, approver, decision, comment FROM offer_approval WHERE offer_id='o1'").fetchall() == [(1, 1, "mgr", "returned", "需修改")]
        replay = effect_revise_offer(
            first, thread_id="o1", business_key="same-request", offer_id="o1",
            department="研发部", start_date="2026-12-01", report_to="李四",
            note="修改后", updated_by="hr1",
        )
        assert replay is None
        node = "effect_revise_offer"
    assert first.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='o1' AND node_name=?", (node,)).fetchone()[0] == 1
    assert not first.in_transaction
    first.close()


def test_different_business_keys_reach_domain_preconditions_without_writes(conn):
    _offer(conn)
    with pytest.raises(ApprovalOutOfOrderError):
        _approve(conn, level=2, approver="gm")
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE node_name='effect_record_approval'").fetchone()[0] == 0

    with pytest.raises(OfferNotEditableError):
        effect_revise_offer(
            conn, thread_id="o1", business_key="different-request", offer_id="o1",
            department="研发部", start_date="2026-12-01", report_to="李四",
            note=None, updated_by="hr1",
        )
    assert _status(conn) == ("pending_approval", 1)
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE node_name='effect_revise_offer'").fetchone()[0] == 0


@pytest.mark.parametrize("effect_name", ["approval", "revise"])
def test_offer_effect_rejects_caller_transaction_without_rolling_it_back(conn, effect_name):
    if effect_name == "revise":
        _offer(conn)
        _approve(conn, level=1, approver="mgr", decision="returned", comment="需修改")
    else:
        _offer(conn)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('caller-sentinel', '调用方事务')")
    with pytest.raises(RuntimeError, match="active transaction"):
        if effect_name == "approval":
            _approve(conn, level=1, approver="mgr")
        else:
            effect_revise_offer(
                conn, thread_id="o1", business_key="caller-transaction", offer_id="o1",
                department="研发部", start_date="2026-12-01", report_to="李四",
                note=None, updated_by="hr1",
            )
    assert conn.in_transaction
    conn.commit()
    assert conn.execute("SELECT name FROM candidate WHERE id='caller-sentinel'").fetchone()[0] == "调用方事务"
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE node_name=?", (
        "effect_record_approval" if effect_name == "approval" else "effect_revise_offer",
    )).fetchone()[0] == 0


@pytest.mark.parametrize("effect_name", ["approval", "revise"])
def test_begin_immediate_precedes_offer_business_reads_without_trace_callback(tmp_path, effect_name):
    class RecordingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            self.executed_sql.append(sql.strip().upper())
            return super().execute(sql, *args, **kwargs)

    db_path = str(tmp_path / f"{effect_name}-order.db")
    conn = sqlite3.connect(db_path, check_same_thread=False, factory=RecordingConnection)
    conn.executed_sql = []
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    _seed(conn)
    _offer(conn, status="needs_revision" if effect_name == "revise" else "pending_approval")
    conn.executed_sql.clear()

    if effect_name == "approval":
        _approve(conn, level=1, approver="mgr")
    else:
        effect_revise_offer(
            conn, thread_id="o1", business_key="ordered", offer_id="o1",
            department="研发部", start_date="2026-12-01", report_to="李四",
            note=None, updated_by="hr1",
        )
    begin = conn.executed_sql.index("BEGIN IMMEDIATE")
    business_reads = [
        i for i, sql in enumerate(conn.executed_sql)
        if sql.startswith("SELECT O.ID") or sql.startswith("SELECT ROUND") or sql.startswith("SELECT LEVEL")
    ]
    assert business_reads and begin < business_reads[0]
    assert not conn.in_transaction
    conn.close()


@pytest.mark.parametrize("failure_point", ["approval_status", "revise_update"])
def test_midwrite_failure_rolls_back_business_rows_and_effect_log(conn, failure_point):
    _offer(conn)
    if failure_point == "approval_status":
        conn.execute("CREATE TRIGGER fail_approval_status BEFORE UPDATE OF status ON offer BEGIN SELECT RAISE(ABORT, 'status failure'); END")
        conn.commit()
        call = lambda: _approve(conn, level=1, approver="mgr")
    else:
        _approve(conn, level=1, approver="mgr", decision="returned", comment="改")
        conn.execute("CREATE TRIGGER fail_revise_update BEFORE UPDATE OF approval_round ON offer BEGIN SELECT RAISE(ABORT, 'revise failure'); END")
        conn.commit()
        call = lambda: effect_revise_offer(conn, thread_id="o1", business_key="req-fail", offer_id="o1", department="研发部", start_date="2026-12-01", report_to="李四", note=None, updated_by="hr1")
    with pytest.raises(sqlite3.IntegrityError):
        call()
    conn.execute("INSERT INTO candidate (id, name) VALUES ('unrelated-failure', '无关')")
    conn.commit()
    assert conn.execute("SELECT status, approval_round FROM offer WHERE id='o1'").fetchone() == (("pending_approval", 1) if failure_point == "approval_status" else ("needs_revision", 1))
    assert conn.execute("SELECT COUNT(*) FROM offer_approval WHERE offer_id='o1'").fetchone()[0] == (0 if failure_point == "approval_status" else 1)
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='o1' AND node_name='effect_record_approval'").fetchone()[0] == (0 if failure_point == "approval_status" else 1)
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='o1' AND node_name='effect_revise_offer'").fetchone()[0] == 0


