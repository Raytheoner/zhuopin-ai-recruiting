"""1001H：校对页"待校对"状态的接线（后端只读键 `field_review_status`）。

背景见 `docs/findings/2026-10-07-1001G-UI审计与升级.md` §七 R-1：
`app/web/static/resume_review.html` 的 `.pending` 黄底与「待校对」徽标逻辑早已
写好，但页面的唯一数据源 `GET /api/resumes/{resume_id}/parsed` 只回
`parsed_json`，不带 `field_review_queue` 的校对状态，前端只能把
`review_pending` 写死 `false`——真实数据下黄底/徽标永远不显示。

三条用例分别锁住这次接线的三面：
1. 端点按字段返回**最近一条**队列行的 status（同一字段的历史 reviewed 行不得
   盖过更新的 pending 行，反之亦然）；
2. 完全没有队列行时返回空 map，且 `parsed_json` 与库中逐字一致（存储内容与
   响应兼容性不动，⛔ 不往 parsed_json 里塞东西）；
3. 页面真的读了 `field_review_status`（字面量断言，防接线被回退）。
"""
from __future__ import annotations

import json

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account

PARSED = {
    "name": {"value": "张三", "confidence": 0.42, "not_mentioned": False},
    "expected_city": {"value": "无锡", "confidence": 0.55, "not_mentioned": False},
}


@pytest.fixture
def review_client(make_test_client):
    """已登录的客户端 + 一个岗位；用例自己往 resume / field_review_queue 里造行。"""
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def _insert_resume(conn, resume_id: str, parsed_json: str | None = None) -> None:
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, parsed_json, uploaded_by) "
        "VALUES (?, 'j1', 'synthetic', 'a.docx', ?, 'parsed', ?, 'alice')",
        (resume_id, f"hash-{resume_id}", parsed_json or json.dumps(PARSED, ensure_ascii=False)),
    )


def _insert_review_row(conn, row_id, resume_id, field, status, created_at) -> None:
    conn.execute(
        "INSERT INTO field_review_queue (id, resume_id, field, machine_value, confidence, "
        "status, reviewed_by, reviewed_at, human_value, created_at) "
        "VALUES (?, ?, ?, '旧机器值', 0.3, ?, ?, ?, '人工值', ?)",
        (
            row_id, resume_id, field, status,
            "alice" if status == "reviewed" else None,
            created_at if status == "reviewed" else None,
            created_at,
        ),
    )


def test_parsed_returns_latest_review_status_per_field(review_client):
    client, conn = review_client
    _insert_resume(conn, "r1")
    # expected_city：先 reviewed、后 pending（重解析后再次判低置信度）⇒ 最新的
    # pending 胜出，页码才应该亮黄底。
    _insert_review_row(conn, "q1", "r1", "expected_city", "reviewed", "2026-01-01 00:00:00")
    _insert_review_row(conn, "q2", "r1", "expected_city", "pending", "2026-01-02 00:00:00")
    # name：先 pending、后 reviewed（已人工确认）⇒ 最新的 reviewed 胜出，不再待校对。
    _insert_review_row(conn, "q3", "r1", "name", "pending", "2026-01-01 00:00:00")
    _insert_review_row(conn, "q4", "r1", "name", "reviewed", "2026-01-02 00:00:00")
    conn.commit()

    resp = client.get("/api/resumes/r1/parsed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["field_review_status"] == {"expected_city": "pending", "name": "reviewed"}
    # 没有队列行的字段（本用例里 parsed_json 只有 name/expected_city）不出现在 map 里
    assert set(body["field_review_status"]) == {"name", "expected_city"}
    # parsed_json 原样返回：校对状态是旁路键，⛔ 不被塞进 parsed_json
    assert body["parsed_json"] == PARSED
    assert "field_review_status" not in body["parsed_json"]


def test_no_review_rows_yields_empty_map_and_verbatim_parsed_json(review_client):
    client, conn = review_client
    _insert_resume(conn, "r2")
    conn.commit()

    resp = client.get("/api/resumes/r2/parsed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["field_review_status"] == {}
    stored = conn.execute(
        "SELECT parsed_json FROM resume WHERE id = 'r2'"
    ).fetchone()[0]
    assert body["parsed_json"] == json.loads(stored)


def test_review_page_reads_field_review_status(review_client):
    """防接线被回退：页面里必须出现 field_review_status 这个旁路键。"""
    client, _conn = review_client
    resp = client.get("/resumes/r1/review")
    assert resp.status_code == 200
    assert "field_review_status" in resp.text
