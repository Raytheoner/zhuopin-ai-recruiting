"""onboarding-flow U3 终态确认 UI：三个 HR 端点 + 清单页终态卡片。"""
from pathlib import Path

import pytest

from app.graph.onboarding_nodes import effect_instantiate_checklist
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account

STATIC = Path("app/web/static")
PAST_START_DATE = "2020-01-01"


def _login(client, conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute(
        "UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id)
    )
    conn.commit()
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))
    return account_id


def _seed_hired(conn, application_id="app-1", start_date=PAST_START_DATE):
    conn.execute(
        "INSERT INTO job (id, title, department, status) "
        "VALUES ('j1', '嵌入式工程师', '研发部', 'approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', 'hired')",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', '研发部', ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, start_date),
    )
    conn.commit()
    effect_instantiate_checklist(
        conn, thread_id=application_id, business_key="instantiate", created_by="hr-user"
    )


def _finish_all_required(conn, application_id="app-1"):
    conn.execute(
        "UPDATE onboarding_item SET status='done' WHERE required=1 AND checklist_id="
        "(SELECT id FROM onboarding_checklist WHERE application_id=?)",
        (application_id,),
    )
    conn.commit()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/applications/app-1/onboarding/terminal", None),
        (
            "post",
            "/api/applications/app-1/onboarding/confirm-hired",
            {"actual_start_date": PAST_START_DATE, "note": None},
        ),
        ("post", "/api/applications/app-1/onboarding/abandon", {"reason": "候选人自愿退出"}),
    ],
)
def test_terminal_endpoints_require_hr(make_test_client, method, path, body):
    client, conn = make_test_client()
    _seed_hired(conn)
    call = getattr(client, method)
    payload = {"json": body} if body is not None else {}
    tables = (
        "application", "offer", "application_stage_history", "onboarding_checklist",
        "onboarding_item", "data_disposition_queue", "effect_log",
    )
    before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    assert call(path, **payload).status_code == 401
    assert {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables} == before
    _login(client, conn, "iv", "interviewer")
    assert call(path, **payload).status_code == 403
    assert {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables} == before


def test_terminal_state_reports_pending_confirmation(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    body = client.get("/api/applications/app-1/onboarding/terminal").json()
    assert body["checklist_status"] == "open"
    assert body["terminal_outcome"] is None
    assert body["pending_confirmation"] is True
    assert body["can_confirm_hired"] is True
    assert body["can_abandon"] is True


def test_confirm_hired_endpoint_writes_terminal_state(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")
    resp = client.post(
        "/api/applications/app-1/onboarding/confirm-hired",
        json={"actual_start_date": PAST_START_DATE, "note": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["terminal_outcome"] == "onboarded"
    assert body["application_status"] == "hired"
    assert body["checklist_status"] == "closed"
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6


def test_confirm_hired_endpoint_is_idempotent(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")
    payload = {"actual_start_date": PAST_START_DATE, "note": None}
    first = client.post("/api/applications/app-1/onboarding/confirm-hired", json=payload)
    second = client.post("/api/applications/app-1/onboarding/confirm-hired", json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 6


def test_confirm_hired_endpoint_rejects_before_start_date(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn, start_date="2099-01-01")
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")
    resp = client.post(
        "/api/applications/app-1/onboarding/confirm-hired",
        json={"actual_start_date": "2099-01-01", "note": None},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0


def test_confirm_hired_endpoint_requires_note_when_items_pending(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    assert client.post(
        "/api/applications/app-1/onboarding/confirm-hired",
        json={"actual_start_date": PAST_START_DATE, "note": None},
    ).status_code == 422
    assert client.post(
        "/api/applications/app-1/onboarding/confirm-hired", json={"note": "体检报告后补"}
    ).status_code == 200


def test_abandon_endpoint_writes_refused_and_no_rejection_record(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    resp = client.post(
        "/api/applications/app-1/onboarding/abandon", json={"reason": "接受其他公司"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["application_status"] == "refused"
    assert body["offer_status"] == "abandoned"
    assert body["terminal_outcome"] == "abandoned"
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == 0


def test_abandon_endpoint_requires_reason(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    resp = client.post("/api/applications/app-1/onboarding/abandon", json={"reason": "  "})
    assert resp.status_code == 422
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


def test_abandon_endpoint_missing_reason_is_input_validation_422(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")

    before = {
        table: conn.execute(f"SELECT * FROM {table}").fetchall()
        for table in ("application", "offer", "application_stage_history", "onboarding_checklist", "data_disposition_queue", "effect_log")
    }
    resp = client.post("/api/applications/app-1/onboarding/abandon", json={})

    assert resp.status_code == 422
    assert {
        table: conn.execute(f"SELECT * FROM {table}").fetchall()
        for table in before
    } == before


def test_abandon_endpoint_rejected_after_confirm(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")
    client.post(
        "/api/applications/app-1/onboarding/confirm-hired",
        json={"actual_start_date": PAST_START_DATE, "note": None},
    )
    resp = client.post("/api/applications/app-1/onboarding/abandon", json={"reason": "反悔"})
    assert resp.status_code == 422
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


def test_terminal_block_is_present_in_checklist_page():
    html = (STATIC / "onboarding_checklist.html").read_text(encoding="utf-8")
    assert 'id="terminal-card"' in html
    assert 'id="confirm-hired-btn"' in html
    assert 'id="abandon-btn"' in html
    assert 'id="actual-start-date"' in html
    assert "onboarding/confirm-hired" in html
    assert "onboarding/abandon" in html
    terminal_block = html.split('id="terminal-card"', 1)[1]
    assert 'type="file"' not in terminal_block
    assert "multipart/form-data" not in terminal_block
