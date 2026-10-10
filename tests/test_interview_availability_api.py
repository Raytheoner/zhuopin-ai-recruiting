"""时段登记接口契约（tasks 2.2）：只写本人、重叠拒绝、被占用不可撤、HR on_behalf 留痕。"""
from __future__ import annotations

import uuid

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _account(conn, username: str, role: str = "interviewer") -> str:
    account_id = upsert_account(conn, username=username, password="pw123456")
    conn.execute("UPDATE hr_account SET role = ? WHERE username = ?", (role, username))
    conn.commit()
    return account_id


def _roster(conn, account_id: str, name: str = "汤丽萍") -> str:
    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, ?, '人事部', '[]', 1)",
        (interviewer_id, account_id, name),
    )
    conn.commit()
    return interviewer_id


def _login(client, conn, account_id: str) -> None:
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def test_interviewer_registers_own_availability(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-1")
    _roster(conn, account_id)
    _login(client, conn, account_id)

    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["registered_by"] == "iv-1"
    assert body["on_behalf"] is False


def test_overlap_rejected(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-1")
    _roster(conn, account_id)
    _login(client, conn, account_id)
    client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
    })
    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 07:00", "end_at": "2026-10-14 09:00",
    })
    assert resp.status_code == 422
    assert "重叠" in resp.json()["detail"]


def test_duplicate_window_returns_existing_row(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-1")
    _roster(conn, account_id)
    _login(client, conn, account_id)
    payload = {"start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00"}
    first = client.post("/api/interviewers/me/availability", json=payload)
    second = client.post("/api/interviewers/me/availability", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_interviewer_cannot_read_others(make_test_client):
    client, conn = make_test_client()
    a_id = _account(conn, "iv-a")
    _account(conn, "iv-b")
    _roster(conn, a_id, name="A")
    _login(client, conn, a_id)

    resp = client.get("/api/interviewers/me/availability?interviewer_id=some-other-id")
    assert resp.status_code == 403


def test_non_roster_account_cannot_register(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-nobody")
    _login(client, conn, account_id)
    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
    })
    assert resp.status_code == 403


def test_hr_on_behalf_register_is_marked(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1", role="hr")
    target_id = _account(conn, "iv-1")
    target_roster = _roster(conn, target_id)
    _login(client, conn, hr_id)

    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
        "on_behalf": True, "interviewer_id": target_roster,
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["registered_by"] == "hr-1"
    assert body["on_behalf"] is True


def test_hr_register_without_on_behalf_rejected(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1", role="hr")
    target_id = _account(conn, "iv-1")
    target_roster = _roster(conn, target_id)
    _login(client, conn, hr_id)
    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
        "interviewer_id": target_roster,
    })
    assert resp.status_code == 422
