"""U3 tasks 3.2：来源改正追加 source_corrected 事实，⛔ 不改写初始记录。"""
from __future__ import annotations

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _client_and_resume(make_test_client, *, with_application: bool, source="boss"):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice', ?)",
        (source,),
    )
    if with_application:
        conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES ('app-1', 'c1', 'j1', 'r1', 'screening')"
        )
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, source) "
            "VALUES ('h-init', 'app-1', NULL, 'initial', 'agent', ?)",
            (source,),
        )
    conn.commit()
    return client, conn


def _facts(conn):
    return conn.execute(
        "SELECT actor_type, actor, action, source, detail_json FROM application_stage_history "
        "WHERE action = 'source_corrected' ORDER BY rowid"
    ).fetchall()


def test_correction_appends_a_fact_and_keeps_the_initial_record(make_test_client):
    client, conn = _client_and_resume(make_test_client, with_application=True, source="boss")

    resp = client.post("/api/resumes/r1/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert resp.json() == {"resume_id": "r1", "source": "referral", "already_corrected": False}

    # 初始记录一字未动
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, action, source "
        "FROM application_stage_history WHERE id = 'h-init'"
    ).fetchone()
    assert row == (None, "initial", "agent", None, "boss")

    # 新事实：human / 改正人 / source_corrected / 新来源 / 原-新值详情
    facts = _facts(conn)
    assert len(facts) == 1
    actor_type, actor, action, source, detail_json = facts[0]
    assert (actor_type, actor, action, source) == (
        "human", "alice", "source_corrected", "referral",
    )
    assert '"from_source": "boss"' in detail_json
    assert '"to_source": "referral"' in detail_json

    # 阶段没有被这次改正改动：事实的 to_stage_id = 该投递当前阶段
    assert conn.execute(
        "SELECT to_stage_id FROM application_stage_history WHERE action = 'source_corrected'"
    ).fetchone()[0] == "screening"
    assert conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app-1'"
    ).fetchone()[0] == "screening"


def test_same_value_correction_appends_no_second_fact(make_test_client):
    client, conn = _client_and_resume(make_test_client, with_application=True)
    client.post("/api/resumes/r1/source", json={"source": "referral"})
    resp = client.post("/api/resumes/r1/source", json={"source": "referral"})
    assert resp.json()["already_corrected"] is True
    assert len(_facts(conn)) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM source_correction_log WHERE resume_id = 'r1'"
    ).fetchone()[0] == 1


def test_second_correction_appends_a_second_fact(make_test_client):
    """幂等的是"同值"，不是"只能改一次"：前后两次不同值的改正是两条事实。"""
    client, conn = _client_and_resume(make_test_client, with_application=True)
    client.post("/api/resumes/r1/source", json={"source": "referral"})
    client.post("/api/resumes/r1/source", json={"source": "51job"})
    facts = _facts(conn)
    assert [f[3] for f in facts] == ["referral", "51job"]


def test_correction_without_application_skips_the_fact(make_test_client):
    """解析不出/解析失败的简历没有投递：改正照常生效，但没有事实可追加。"""
    client, conn = _client_and_resume(make_test_client, with_application=False)
    resp = client.post("/api/resumes/r1/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT source FROM resume WHERE id = 'r1'").fetchone()[0] == "referral"
    assert conn.execute(
        "SELECT COUNT(*) FROM source_correction_log WHERE resume_id = 'r1'"
    ).fetchone()[0] == 1


def test_invalid_source_is_still_rejected_before_any_write(make_test_client):
    """回归：非法取值 422，且不留下任何事实/留痕。"""
    client, conn = _client_and_resume(make_test_client, with_application=True)
    assert client.post("/api/resumes/r1/source", json={"source": "unknown"}).status_code == 422
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action = 'source_corrected'"
    ).fetchone()[0] == 0
