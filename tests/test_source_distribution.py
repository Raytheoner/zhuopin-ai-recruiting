"""U3 tasks 3.3：只读来源分布 + spec「不做漏斗报表」的反证。"""
from __future__ import annotations

import sqlite3

from app.storage.auth_session import create_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account
from app.storage.source import source_distribution


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO job (id, title) VALUES ('j2', '采购工程师')")
    return c


def _application(conn, *, app_id, resume_id, job_id="j1", source=None):
    if conn.execute("SELECT 1 FROM candidate WHERE id = 'c1'").fetchone() is None:
        conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES (?, ?, 'synthetic', ?, ?, 'alice', ?)",
        (resume_id, job_id, resume_id + ".pdf", "h-" + resume_id, source),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'c1', ?, ?, 'initial')",
        (app_id, job_id, resume_id),
    )
    if source is not None:
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, source) "
            "VALUES (?, ?, NULL, 'initial', 'agent', ?)",
            ("h-init-" + app_id, app_id, source),
        )
    conn.commit()


def test_counts_by_source_from_the_fact_table():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source="boss")
    _application(conn, app_id="app-2", resume_id="r2", source="boss")
    _application(conn, app_id="app-3", resume_id="r3", source="referral")
    assert source_distribution(conn, "j1") == {"boss": 2, "referral": 1}


def test_correction_moves_the_count_to_the_new_source():
    """来源改正追加的新事实覆盖初始值 ⇒ 分布跟着搬，而不是两边各记一票。"""
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source="boss")
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, source) "
        "VALUES ('h-corr', 'app-1', NULL, 'initial', 'human', 'alice', 'source_corrected', 'referral')"
    )
    conn.commit()
    assert source_distribution(conn, "j1") == {"referral": 1}


def test_application_without_a_source_fact_counts_as_unknown():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source=None)
    assert source_distribution(conn, "j1") == {"unknown": 1}


def test_distribution_is_scoped_to_the_job():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", job_id="j1", source="boss")
    _application(conn, app_id="app-2", resume_id="r2", job_id="j2", source="liepin")
    assert source_distribution(conn, "j1") == {"boss": 1}
    assert source_distribution(conn, "j2") == {"liepin": 1}
    assert source_distribution(conn, "nope") == {}


def test_distribution_is_read_only():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source="boss")
    before = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0]
    source_distribution(conn, "j1")
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == before


def test_there_is_no_funnel_report_page(make_test_client):
    """spec「只记字段不做漏斗报表」的机器判据：报表页不存在。⛔ 不要为了"让
    source_distribution 有调用方"顺手加一个页面或接口——那条 Requirement 会当场
    失去守护，而它正是本单元唯一的 Non-Goal。"""
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()

    assert client.get("/reports/source-distribution?job_id=j1").status_code == 404
    assert client.get("/jobs/j1/source-distribution").status_code == 404
    assert client.get("/jobs/j1/channel-funnel").status_code == 404
