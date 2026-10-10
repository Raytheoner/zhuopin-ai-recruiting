"""视图硬约束：当日安排/HR 排期页⛔不显示评分/排名/硬门槛/联系方式；子路径前缀正确。

Task 6 范围（面试官周视图页 + 当日安排页 + `/api/interviewers/me/schedule`）先落
「当日安排 JSON 字段级无敏感信息」与「两个面试官页面在任意挂载前缀下可渲染」两条；
HR 排期页（`/api/applications/{id}/schedule`，Task 7 交付）相关用例在 Task 10 补齐。
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.llm.gateway import LLMGateway
from app.storage.auth_session import create_session
from app.storage.db import get_connection
from app.storage.hr_account import upsert_account
from app.web.server import create_app


ROOT_PATH = "/hr/recruit-agent"
_FORBIDDEN_KEYS = {"phone", "email", "contact", "score", "rank", "hard_gate", "hard_requirement"}


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
    return client, conn


def _seed(conn):
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
    conn.execute(
        "INSERT INTO interviewer_availability (id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES ('av1', ?, '2999-01-01 00:00', '2999-01-01 23:00', NULL, 'iv-1', 0)",
        (interviewer_id,),
    )
    slot_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode, status, created_by) "
        "VALUES (?, 'app1', 1, datetime('now', '+1 hour'), datetime('now', '+2 hour'), "
        "'onsite', 'scheduled', 'hr-1')",
        (slot_id,),
    )
    conn.execute(
        "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) VALUES (?, ?)",
        (slot_id, interviewer_id),
    )
    conn.commit()
    return {"hr_id": hr_id, "iv_account": iv_account, "interviewer_id": interviewer_id}


def _login(client, conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def _assert_no_forbidden_keys(obj):
    if isinstance(obj, dict):
        for key in obj:
            assert key not in _FORBIDDEN_KEYS, f"视图泄漏敏感字段 {key}"
            _assert_no_forbidden_keys(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_forbidden_keys(item)


def test_day_schedule_json_has_no_sensitive_fields(tmp_path):
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    _login(client, conn, seed["iv_account"])
    resp = client.get(f"{ROOT_PATH}/api/interviewers/me/schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slots"][0]["candidate_name"] == "张三"
    assert body["slots"][0]["job_title"] == "嵌入式工程师"
    _assert_no_forbidden_keys(body)


def test_pages_render_under_root_path_prefix(tmp_path):
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    _login(client, conn, seed["iv_account"])
    for page in ("interviewers/me/availability", "interviewers/me/schedule"):
        resp = client.get(f"{ROOT_PATH}/{page}")
        assert resp.status_code == 200
        assert f'<base href="{ROOT_PATH}/">' in resp.text
    # 2026-10-11 修正（Spec review）：HR 排期页一并纳入子路径渲染核验。
    resp = client.get(f"{ROOT_PATH}/applications/app1/schedule")
    assert resp.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in resp.text


def test_application_schedule_page_extracts_application_id_from_middle_segment():
    """2026-10-11 修正（Spec review 实测 blocker）：⛔ 不能取 URL 末段
    （那是字面量 "schedule"），否则排期页首个数据请求恒 404。"""
    from pathlib import Path

    html = Path("app/web/static/application_schedule.html").read_text(encoding="utf-8")
    assert "match(/\\/applications\\/([^/]+)\\/schedule\\/?$/)" in html
    assert 'split("/").filter(Boolean).pop()' not in html
    assert "if (!r.ok)" in html  # 非 401 失败也有就地提示（F4）


def test_schedule_post_returns_404_for_unknown_application(tmp_path):
    """2026-10-11 修正（Spec review）：POST 排期路线上 SlotNotFoundError 是
    ValueError 子类，曾因 except 顺序被宽分支吃掉、404 退化成 422——钉死语义。

    ⚠️ 非空转（2026-10-11 实测补钉）：本文件 `_make()` 带 `root_path=ROOT_PATH`，
    请求路径必须带该前缀，否则命中的是 Starlette 的「路由未匹配」404
    （`{"detail":"Not Found"}`），本用例就退化成恒真的空转——既测不到 except 顺序，
    也测不到路由本身。故：① URL 带 ROOT_PATH 前缀；② 断言 detail 是 store 层
    抛出的「不存在」文案，与路由未匹配的 "Not Found" 区分开。
    """
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    _login(client, conn, seed["hr_id"])
    resp = client.post(
        f"{ROOT_PATH}/api/applications/nope/schedule",
        json={
            "request_id": "req-x",
            "interviewer_ids": [seed["interviewer_id"]],
            "round": 1,
            "start_at": "2999-01-02 00:00",
            "end_at": "2999-01-02 01:00",
            "mode": "onsite",
            "location_or_link": None,
        },
    )
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]
