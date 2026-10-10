"""U2 e2e（tasks 2.9）：夹具流转到 interview → 登记时段 → 安排 → 冲突拒绝 → 改期 → 完成；
全程子路径前缀；history 条数守恒。"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.llm.gateway import LLMGateway
from app.storage.auth_session import create_session
from app.storage.db import get_connection
from app.storage.hr_account import upsert_account
from app.web.server import create_app


ROOT_PATH = "/hr/recruit-agent"


def _make(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app(
        db_path=db_path,
        gateway_factory=lambda: LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        ),
        root_path=ROOT_PATH,
        resume_storage_dir=str(tmp_path / "resumes"),
    )
    client = TestClient(app)
    conn = get_connection(db_path)
    hr_id = upsert_account(conn, username="hr-1", password="pw123456")
    iv_account = upsert_account(conn, username="iv-1", password="pw123456")
    conn.execute("UPDATE hr_account SET role='interviewer' WHERE username='iv-1'")
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
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.commit()
    return client, conn, {"hr_id": hr_id, "iv_account": iv_account, "interviewer_id": interviewer_id}


def _login(client, conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def test_full_scheduling_flow_under_root_path(tmp_path):
    client, conn, ids = _make(tmp_path)

    # 1. HR 排期页可访问，base href 正确
    _login(client, conn, ids["hr_id"])
    page = client.get(f"{ROOT_PATH}/applications/app1/schedule")
    assert page.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in page.text

    # 2. 面试官未登记时段 → 安排被拒（冲突：无可用时段）
    rejected = client.post(f"{ROOT_PATH}/api/applications/app1/schedule", json={
        "request_id": "req-1", "interviewer_ids": [ids["interviewer_id"]],
        "round": 1, "start_at": "2020-01-01 10:00", "end_at": "2020-01-01 11:00",
        "mode": "onsite",
    })
    assert rejected.status_code == 409
    assert "interviewer_no_availability" in rejected.json()["detail"]["conflicts"]

    # 3. 面试官登记时段
    _login(client, conn, ids["iv_account"])
    reg = client.post(f"{ROOT_PATH}/api/interviewers/me/availability", json={
        "start_at": "2020-01-01 00:00", "end_at": "2020-01-01 23:00",
    })
    assert reg.status_code == 201

    # 4. HR 安排
    _login(client, conn, ids["hr_id"])
    scheduled = client.post(f"{ROOT_PATH}/api/applications/app1/schedule", json={
        "request_id": "req-1", "interviewer_ids": [ids["interviewer_id"]],
        "round": 1, "start_at": "2020-01-01 10:00", "end_at": "2020-01-01 11:00",
        "mode": "onsite",
    })
    assert scheduled.status_code == 201
    assert scheduled.json()["slot_id"] == "req-1"

    # 5. 改期
    rescheduled = client.post(f"{ROOT_PATH}/api/interview-slots/req-1/reschedule", json={
        "request_id": "rr-1", "start_at": "2020-01-01 12:00", "end_at": "2020-01-01 13:00",
    })
    assert rescheduled.status_code == 200
    assert rescheduled.json()["status"] == "rescheduled"

    # 6. 该场面试官标记完成
    _login(client, conn, ids["iv_account"])
    completed = client.post(f"{ROOT_PATH}/api/interview-slots/req-1/complete", json={
        "target_status": "completed",
    })
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    # 7. history 条数守恒：安排/改期/完成 = 3 条，且 effect_log 恒等
    history = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1' AND action IS NOT NULL"
    ).fetchone()[0]
    effect = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id='app1' AND node_name IN "
        "('effect_schedule_slot','effect_reschedule_slot','effect_cancel_slot','effect_complete_slot')"
    ).fetchone()[0]
    slots = conn.execute(
        "SELECT COUNT(*) FROM interview_slot WHERE application_id='app1'"
    ).fetchone()[0]
    assert history == effect == 3
    assert slots == 1
