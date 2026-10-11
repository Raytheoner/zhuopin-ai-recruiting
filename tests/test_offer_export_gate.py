"""导出门槛：offer.status 白名单在导出卡点上生效（U3 tasks 3.6）。"""
from __future__ import annotations

import uuid

import pytest

from app.graph.letter_nodes import record_letter_access
from app.storage.auth_session import create_session
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer import OfferNotExportableError


def _seed(conn, *, status="pending_approval", kind="offer"):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','offer')"
    )
    if kind == "offer":
        conn.execute(
            "INSERT INTO offer (id, application_id, job_id, department, start_date, "
            "report_to, status, approval_round, created_by) "
            "VALUES ('o1','app1','j1','研发部','2026-11-01','李四',?,1,'hr1')",
            (status,),
        )
    conn.execute(
        "INSERT INTO candidate_letter (id, application_id, kind, version, "
        "template_version, body, ai_generated, created_by) "
        "VALUES ('l1','app1',?,1,1,'【AI 生成】正文',1,'hr1')",
        (kind,),
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-export-gate.db"))
    init_schema(c)
    return c


def _export(conn, letter_id="l1"):
    record_letter_access(
        conn,
        accessor="hr1",
        application_id="app1",
        letter_id=letter_id,
        access_type="export",
    )


def _log_count(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id = 'l1'"
    ).fetchone()[0]


@pytest.mark.parametrize("status", ["pending_approval", "needs_revision"])
def test_export_blocked_while_not_approved(conn, status):
    _seed(conn, status=status)
    with pytest.raises(OfferNotExportableError, match="审批通过后才可导出"):
        _export(conn)
    # 门槛先于留痕：⛔ 拦截时一行留痕都不写（没导出就谈不上"访问过文书"）。
    assert _log_count(conn) == 0


@pytest.mark.parametrize(
    "status", ["approved", "exported", "accepted", "declined", "negotiating"]
)
def test_export_allowed_when_approved(conn, status):
    _seed(conn, status=status)
    _export(conn)
    assert _log_count(conn) == 1
    assert conn.execute(
        "SELECT access_type FROM letter_access_log WHERE letter_id = 'l1'"
    ).fetchone()[0] == "export"


def test_view_is_not_gated(conn):
    _seed(conn, status="pending_approval")
    record_letter_access(
        conn,
        accessor="hr1",
        application_id="app1",
        letter_id="l1",
        access_type="view",
    )
    assert _log_count(conn) == 1


def test_rejection_letter_export_is_not_gated(conn):
    """拒信没有 offer 记录，spec 的门槛只针对 Offer 记录 ⇒ 不受影响。"""
    _seed(conn, kind="rejection")
    _export(conn)
    assert _log_count(conn) == 1


def test_export_unknown_letter_raises_not_found(conn):
    _seed(conn, status="approved")
    with pytest.raises(Exception, match="文书不存在"):
        _export(conn, letter_id=f"missing-{uuid.uuid4()}")


def test_pending_offer_export_endpoint_returns_409_without_side_effects(make_test_client):
    """HTTP 门槛：未审批 Offer 导出返回 409，响应前后相关业务事实不变。"""
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="hr1", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    _seed(conn, status="pending_approval")

    def snapshot():
        export_facts = conn.execute(
            "SELECT id, accessor, application_id, letter_id, access_type, at "
            "FROM letter_access_log WHERE letter_id = 'l1' AND access_type = 'export' "
            "ORDER BY id"
        ).fetchall()
        letter_sent_status = conn.execute(
            "SELECT sent_status FROM candidate_letter WHERE id = 'l1'"
        ).fetchone()
        offer_status = conn.execute(
            "SELECT status FROM offer WHERE application_id = 'app1'"
        ).fetchone()
        return export_facts, letter_sent_status, offer_status

    before = snapshot()
    response = client.get("/api/letters/l1/export.docx")
    after = snapshot()

    assert response.status_code == 409
    assert "审批通过后才可导出" in response.json()["detail"]
    assert after == before
