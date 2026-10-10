"""四节点 + 排期路由契约（tasks 2.3/2.4/2.5）：阶段门槛、幂等重跑、改期重过冲突、
取消保留记录、开始前标完成被拒、HR/面试官授权。"""
from __future__ import annotations

import uuid

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _account(conn, username: str, role: str) -> str:
    account_id = upsert_account(conn, username=username, password="pw123456")
    conn.execute("UPDATE hr_account SET role = ? WHERE username = ?", (role, username))
    conn.commit()
    return account_id


def _seed(conn, *, stage: str = "interview"):
    """HR 账号 + 面试官名单 + job/candidate/resume/application。"""
    hr_id = _account(conn, "hr-1", "hr")
    iv_account = _account(conn, "iv-1", "interviewer")
    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, '汤丽萍', '人事部', '[]', 1)",
        (interviewer_id, iv_account),
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', ?)",
        (stage,),
    )
    conn.commit()
    return {"hr_id": hr_id, "iv_account": iv_account, "interviewer_id": interviewer_id}


def _login(client, conn, account_id: str) -> None:
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def _avail(conn, interviewer_id: str) -> None:
    # 可用时段窗口取到 3000 年：test_complete_before_start_rejected 用 2999-01-01
    # 的未来场次，窗口若只到 2020 该场次会被冲突检查判为 interviewer_no_availability
    # 而根本没建成（complete 退化成 404 而非 422）。窗口必须覆盖本文件全部用例。
    conn.execute(
        "INSERT INTO interviewer_availability (id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES (?, ?, '2020-01-01 00:00', '3000-01-01 00:00', NULL, 'iv-1', 0)",
        (str(uuid.uuid4()), interviewer_id),
    )
    conn.commit()


def _schedule(client, *, request_id="req-1", interviewer_ids=None, start="2020-01-01 10:00",
              end="2020-01-01 11:00", round_=1):
    return client.post("/api/applications/app1/schedule", json={
        "request_id": request_id,
        "interviewer_ids": interviewer_ids,
        "round": round_, "start_at": start, "end_at": end, "mode": "onsite",
    })


def test_schedule_requires_interview_stage(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn, stage="screening")
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 422


def test_hr_can_schedule(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "scheduled"
    assert body["interviewer_ids"] == [seed["interviewer_id"]]


def test_schedule_conflict_lists_all_reasons(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _login(client, conn, seed["hr_id"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 409
    assert "interviewer_no_availability" in resp.json()["detail"]["conflicts"]


def test_schedule_rerun_creates_no_second_slot(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    first = _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    second = _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["slot_id"] == second.json()["slot_id"] == "req-1"
    count = conn.execute("SELECT COUNT(*) FROM interview_slot WHERE application_id='app1'").fetchone()[0]
    assert count == 1


def test_duplicate_round_rejected(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    assert _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]]).status_code == 201
    resp = _schedule(client, request_id="req-2", interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 422


def test_reschedule_updates_time_and_history(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    resp = client.post("/api/interview-slots/req-1/reschedule", json={
        "request_id": "rr-1", "start_at": "2020-01-01 12:00", "end_at": "2020-01-01 13:00",
    })
    assert resp.status_code == 200
    assert resp.json()["start_at"] == "2020-01-01 12:00"
    assert resp.json()["status"] == "rescheduled"
    # occurred_at 是秒级（datetime('now')），安排与改期同秒落库时会并列；
    # 再按 rowid 兜底，保证断言的是插入顺序（scheduled → rescheduled）而不是
    # 随机 uuid 的排序。
    rows = conn.execute(
        "SELECT action FROM application_stage_history WHERE application_id='app1' "
        "ORDER BY occurred_at, rowid"
    ).fetchall()
    assert [r[0] for r in rows] == ["scheduled", "rescheduled"]


def test_cancel_keeps_record(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    resp = client.post("/api/interview-slots/req-1/cancel", json={"cancel_reason": "候选人临时有事"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert conn.execute("SELECT COUNT(*) FROM interview_slot WHERE id='req-1'").fetchone()[0] == 1


def test_complete_before_start_rejected(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-future", interviewer_ids=[seed["interviewer_id"]],
              start="2999-01-01 10:00", end="2999-01-01 11:00")
    resp = client.post("/api/interview-slots/req-future/complete", json={"target_status": "completed"})
    assert resp.status_code == 422


def test_interviewer_can_complete_own_slot(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    _login(client, conn, seed["iv_account"])
    resp = client.post("/api/interview-slots/req-1/complete", json={"target_status": "completed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_non_hr_cannot_schedule(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["iv_account"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 403
