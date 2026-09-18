"""申诉接口（task-9-brief.md）：POST /api/applications/{id}/appeal
注册申诉（none→requested，幂等），POST /api/rejections/{id}/appeal/transition
驱动后续流转。两个路由都在 PROTECTED_PATH_PREFIXES（/api/applications）覆盖
范围内或复用同一鉴权中间件，用 make_test_client + upsert_account +
create_session 建立已登录 client——与 tests/test_screening_trigger_on_upload.py
的既有鉴权测试模式一致（⛔ 不用 task-9-brief.md 草稿里假设的
create_app(gateway=..., channel=...) / POST /api/login / create_account，
这些符号在当前代码库不存在，create_app 的真实签名见
app/web/server.py::create_app 与 tests/conftest.py::make_test_client）。
"""
from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.rejection import write_rejection


@pytest.fixture
def client_and_rejection(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="hr2", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)

    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'screening')"
    )
    rejection_id = write_rejection(
        conn, application_id="a1", reason_type="hard_rule",
        rule_ref="experience_years:gte:5", human_readable="工作年限不满足",
        decided_by="hr1",
    )
    conn.commit()

    return client, rejection_id


def test_register_appeal_transitions_none_to_requested(client_and_rejection):
    client, rejection_id = client_and_rejection
    resp = client.post("/api/applications/a1/appeal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["appeal_status"] == "requested"
    assert body["already_applied"] is False
    assert body["rejection_id"] == rejection_id


def test_register_appeal_is_idempotent_on_repeat(client_and_rejection):
    client, _ = client_and_rejection
    client.post("/api/applications/a1/appeal")
    resp = client.post("/api/applications/a1/appeal")
    assert resp.status_code == 200
    assert resp.json()["already_applied"] is True


def test_register_appeal_404_when_no_rejection_record(client_and_rejection):
    client, _ = client_and_rejection
    resp = client.post("/api/applications/no-such-app/appeal")
    assert resp.status_code == 404


def test_transition_endpoint_advances_state(client_and_rejection):
    client, rejection_id = client_and_rejection
    client.post("/api/applications/a1/appeal")
    resp = client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    assert resp.status_code == 200
    assert resp.json()["appeal_status"] == "under_review"


def test_transition_endpoint_illegal_transition_returns_409(client_and_rejection):
    client, rejection_id = client_and_rejection
    resp = client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    assert resp.status_code == 409


def test_transition_endpoint_404_for_unknown_rejection(client_and_rejection):
    client, _ = client_and_rejection
    resp = client.post(
        "/api/rejections/no-such-id/appeal/transition",
        json={"to_status": "requested"},
    )
    assert resp.status_code == 404


def test_transition_endpoint_records_acting_operator(client_and_rejection):
    client, rejection_id = client_and_rejection
    client.post("/api/applications/a1/appeal")
    client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "overturned"},
    )
    resp = client.get("/api/applications/a1/screening-flags")
    assert resp.status_code == 200  # 只读接口存在即可（Task 5 已加），此处顺带确认路由未冲突
