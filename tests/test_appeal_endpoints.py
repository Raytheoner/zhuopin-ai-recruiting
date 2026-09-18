"""申诉接口（task-9-brief.md）：POST /api/applications/{id}/appeal
注册申诉（none→requested，幂等），POST /api/rejections/{id}/appeal/transition
驱动后续流转。两个路由都在 PROTECTED_PATH_PREFIXES（/api/applications、
/api/rejections）覆盖范围内，用 make_test_client + upsert_account +
create_session 建立已登录 client——与 tests/test_screening_trigger_on_upload.py
的既有鉴权测试模式一致（⛔ 不用 task-9-brief.md 草稿里假设的
create_app(gateway=..., channel=...) / POST /api/login / create_account，
这些符号在当前代码库不存在，create_app 的真实签名见
app/web/server.py::create_app 与 tests/conftest.py::make_test_client）。

review 定级 Critical 之后补的一条（2026-09-18）：/api/rejections 曾经不在
PROTECTED_PATH_PREFIXES 里，未登录调用 transition 端点会静默用
UNKNOWN_REVIEWER 占位符落 actor，绕开"人工确认并留痕"的合规要求。修复后
补 test_transition_endpoint_unauthenticated_returns_401 锁住这个契约，
沿用 tests/test_resume_pages_unauthenticated.py 同款"未登录打受保护前缀
必 401"的写法（不需要真实存在的 rejection_id——AuthMiddleware 在路由函数
执行前就拦下）。同时把 test_transition_endpoint_records_acting_operator
从"只确认路由不冲突"补强为真正断言 appeal_event 里落的 actor 是登录用户
本人，不是占位符。
"""
from __future__ import annotations

import pytest

from app.middleware.auth import UNKNOWN_REVIEWER
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.rejection import write_rejection

ACTING_REVIEWER_USERNAME = "hr2"


@pytest.fixture
def client_and_rejection(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username=ACTING_REVIEWER_USERNAME, password="testpass123")
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

    return client, conn, rejection_id


def test_register_appeal_transitions_none_to_requested(client_and_rejection):
    client, _conn, rejection_id = client_and_rejection
    resp = client.post("/api/applications/a1/appeal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["appeal_status"] == "requested"
    assert body["already_applied"] is False
    assert body["rejection_id"] == rejection_id


def test_register_appeal_is_idempotent_on_repeat(client_and_rejection):
    client, _conn, _rejection_id = client_and_rejection
    client.post("/api/applications/a1/appeal")
    resp = client.post("/api/applications/a1/appeal")
    assert resp.status_code == 200
    assert resp.json()["already_applied"] is True


def test_register_appeal_404_when_no_rejection_record(client_and_rejection):
    client, _conn, _rejection_id = client_and_rejection
    resp = client.post("/api/applications/no-such-app/appeal")
    assert resp.status_code == 404


def test_transition_endpoint_advances_state(client_and_rejection):
    client, _conn, rejection_id = client_and_rejection
    client.post("/api/applications/a1/appeal")
    resp = client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    assert resp.status_code == 200
    assert resp.json()["appeal_status"] == "under_review"


def test_transition_endpoint_illegal_transition_returns_409(client_and_rejection):
    client, _conn, rejection_id = client_and_rejection
    resp = client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    assert resp.status_code == 409


def test_transition_endpoint_404_for_unknown_rejection(client_and_rejection):
    client, _conn, _rejection_id = client_and_rejection
    resp = client.post(
        "/api/rejections/no-such-id/appeal/transition",
        json={"to_status": "requested"},
    )
    assert resp.status_code == 404


def test_transition_endpoint_unauthenticated_returns_401(make_test_client):
    """Critical finding 修复的回归锁：/api/rejections 现在必须在
    PROTECTED_PATH_PREFIXES 里，未带 session cookie 的调用要在进路由函数、
    落任何 actor 之前就被 AuthMiddleware 挡住——不依赖 rejection_id 是否
    真实存在，中间件按路径前缀判断，不查库。"""
    client, _conn = make_test_client()
    resp = client.post(
        "/api/rejections/no-such-id/appeal/transition",
        json={"to_status": "requested"},
    )
    assert resp.status_code == 401


def test_transition_endpoint_records_acting_operator(client_and_rejection):
    client, conn, rejection_id = client_and_rejection
    client.post("/api/applications/a1/appeal")
    client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "overturned"},
    )

    rows = conn.execute(
        "SELECT from_status, to_status, actor FROM appeal_event "
        "WHERE rejection_record_id = ? ORDER BY occurred_at",
        (rejection_id,),
    ).fetchall()
    assert rows == [
        ("none", "requested", ACTING_REVIEWER_USERNAME),
        ("requested", "under_review", ACTING_REVIEWER_USERNAME),
        ("under_review", "overturned", ACTING_REVIEWER_USERNAME),
    ]
    # 合规红线的落地判据不是"字段有值"，是"不是占位符"——顺带锁死这条。
    assert all(row[2] != UNKNOWN_REVIEWER for row in rows)

    resp = client.get("/api/applications/a1/screening-flags")
    assert resp.status_code == 200  # 只读接口存在即可（Task 5 已加），此处顺带确认路由未冲突
