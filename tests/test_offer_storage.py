"""app/storage/offer.py 的判定真源（U3 tasks 3.1/3.2/3.3/3.6）。"""
from __future__ import annotations

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer import (
    OfferNotCreatableError,
    OfferNotFoundError,
    OfferNotExportableError,
    approvers_for_level,
    assert_offer_creatable,
    assert_offer_exportable,
    current_approval_level,
    get_offer,
    list_pending_offers_for,
    stage_rank,
    total_approval_levels,
)
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn, *, stage="interview", status="active"):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, "
        "status) VALUES ('app1','c1','j1','r1',?,?)",
        (stage, status),
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-storage.db"))
    init_schema(c)
    _seed(c)
    return c


def _offer(conn, *, status="pending_approval", round_no=1, offer_id="o1"):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, approval_round, created_by) "
        "VALUES (?, 'app1', 'j1', '研发部', '2026-11-01', '李四', ?, ?, 'hr1')",
        (offer_id, status, round_no),
    )
    conn.commit()
    return offer_id


def _two_level_chain(conn, job_id="j1"):
    put_approval_chain(
        conn,
        job_id=job_id,
        chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ],
        updated_by="hr1",
    )


# ── 阶段位次 ────────────────────────────────────────────────────


def test_stage_rank_order_and_unknown():
    assert stage_rank("initial") < stage_rank("screening") < stage_rank("interview")
    assert stage_rank("interview") < stage_rank("offer") < stage_rank("hired")
    assert stage_rank("rejected") == -1
    assert stage_rank("no-such-stage") == -1


# ── 发起前置（tasks 3.1） ───────────────────────────────────────


def test_creatable_at_interview(conn):
    assert_offer_creatable(conn, application_id="app1")  # 不抛 = 过


def test_creatable_rejects_early_stage(conn):
    conn.execute("UPDATE application SET current_stage_id='screening' WHERE id='app1'")
    with pytest.raises(OfferNotCreatableError, match="阶段"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_terminated_status(conn):
    conn.execute("UPDATE application SET status='withdrawn' WHERE id='app1'")
    with pytest.raises(OfferNotCreatableError, match="已终止"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_rejection_record(conn):
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rej1','app1','human_decision','mgr')"
    )
    with pytest.raises(OfferNotCreatableError, match="淘汰记录"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_existing_offer(conn):
    _offer(conn)
    with pytest.raises(OfferNotCreatableError, match="已有 Offer"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_unknown_application(conn):
    with pytest.raises(OfferNotFoundError):
        assert_offer_creatable(conn, application_id="no-such-app")


# ── 审批级数与当前级（tasks 3.2/3.3） ───────────────────────────


def test_default_chain_is_one_level_with_no_approvers(conn):
    assert total_approval_levels(conn, "j1") == 1
    assert approvers_for_level(conn, "j1", 1) == []


def test_configured_chain_levels_and_approvers(conn):
    _two_level_chain(conn)
    assert total_approval_levels(conn, "j1") == 2
    assert approvers_for_level(conn, "j1", 1) == ["mgr"]
    assert approvers_for_level(conn, "j1", 2) == ["gm"]
    assert approvers_for_level(conn, "j1", 3) == []


def test_current_level_starts_at_one_then_advances(conn):
    offer_id = _offer(conn)
    assert current_approval_level(conn, offer_id) == 1
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a1', ?, 1, 1, 'mgr', 'approved')",
        (offer_id,),
    )
    conn.commit()
    assert current_approval_level(conn, offer_id) == 2


def test_current_level_resets_on_new_round(conn):
    offer_id = _offer(conn, round_no=2)
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a0', ?, 1, 1, 'mgr', 'approved')",
        (offer_id,),
    )
    conn.commit()
    # 第 2 轮没有任何通过记录 ⇒ 回到第 1 级（链从第一级重走）。
    assert current_approval_level(conn, offer_id) == 1


# ── 本人待审列表（tasks 3.4） ───────────────────────────────────


def test_pending_list_only_returns_offers_where_approver_stands(conn):
    _two_level_chain(conn)
    offer_id = _offer(conn)
    assert [item["id"] for item in list_pending_offers_for(conn, "mgr")] == [offer_id]
    # 总经理在第 1 级尚未通过时看不到这条（逐级，不是"在链里就能审"）。
    assert list_pending_offers_for(conn, "gm") == []
    assert list_pending_offers_for(conn, "hr1") == []


def test_pending_list_advances_to_next_level(conn):
    _two_level_chain(conn)
    offer_id = _offer(conn)
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a1', ?, 1, 1, 'mgr', 'approved')",
        (offer_id,),
    )
    conn.commit()
    assert list_pending_offers_for(conn, "mgr") == []
    assert [item["current_level"] for item in list_pending_offers_for(conn, "gm")] == [2]


def test_pending_list_excludes_non_pending_offers(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["mgr"]}],
        updated_by="hr1",
    )
    _offer(conn, status="approved")
    assert list_pending_offers_for(conn, "mgr") == []


def test_pending_list_has_no_ai_decision_fields(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["mgr"]}],
        updated_by="hr1",
    )
    _offer(conn)
    item = list_pending_offers_for(conn, "mgr")[0]
    forbidden = ("score", "ranking", "recommend", "总分", "排名", "评分", "建议")
    flat = " ".join(str(key) for key in item) + " " + " ".join(
        str(value) for value in item.values()
    )
    for word in forbidden:
        assert word not in flat


# ── 导出门槛（tasks 3.6） ───────────────────────────────────────


@pytest.mark.parametrize(
    "status", ["approved", "exported", "accepted", "declined", "negotiating"]
)
def test_exportable_statuses_pass(conn, status):
    _offer(conn, status=status)
    assert_offer_exportable(conn, "app1")  # 不抛 = 过


@pytest.mark.parametrize("status", ["pending_approval", "needs_revision"])
def test_pending_and_needs_revision_are_rejected(conn, status):
    _offer(conn, status=status)
    with pytest.raises(OfferNotExportableError, match="审批通过后才可导出"):
        assert_offer_exportable(conn, "app1")


def test_exportable_rejects_missing_offer(conn):
    with pytest.raises(OfferNotFoundError):
        assert_offer_exportable(conn, "app1")


def test_get_offer_carries_labels_and_joins(conn):
    offer = get_offer(conn, _offer(conn))
    assert offer["job_title"] == "嵌入式工程师"
    assert offer["candidate_name"] == "张三"
    assert offer["status_label"] == "待审批"
