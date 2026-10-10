"""interview-scheduling U1 的 schema 回归测试（tasks 1.6）。

覆盖三类判据：
- 新库建表齐全（8 张面试域表 + stage.interview 预置行 + hr_account.role 列）
- 复制 M2 结构的老库升级后既有表/既有行不改、既有三行 stage 不变
- 全部 CHECK 反证（非法 status/mode/kind/source/enabled 被拒）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.storage.db import get_connection, init_schema


_INTERVIEW_TABLES = (
    "interviewer",
    "interviewer_availability",
    "interview_slot",
    "interview_slot_interviewer",
    "interview_invitation_draft",
    "invitation_template",
    "candidate_contact",
    "candidate_contact_access_log",
)

_LEGACY_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "zp51_demo_db_schema_pre_m3.sql"
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn, name):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _fresh_conn(tmp_path):
    c = get_connection(str(tmp_path / "fresh.db"))
    init_schema(c)
    return c


def _legacy_db(tmp_path):
    c = get_connection(str(tmp_path / "legacy.db"))
    c.executescript(_LEGACY_FIXTURE_PATH.read_text(encoding="utf-8"))
    # 快照只含 DDL，stage 三条种子行需手工补（与 test_db_m3_schema.py 同一理由）。
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected')")
    c.execute("INSERT INTO job (id, title, status) VALUES ('old-job', '底层软件工程师', 'approved')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('old-cand', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('old-resume', 'old-job', 'synthetic', 'a.pdf', 'sha-old', 'hr-1')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('old-app', 'old-cand', 'old-job', 'old-resume', 'initial')"
    )
    c.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('old-acct', 'tangliping', 'h', 's')"
    )
    c.commit()
    return c


# ── 新库 ─────────────────────────────────────────────────────────────────


def test_fresh_db_has_all_interview_tables(tmp_path):
    c = _fresh_conn(tmp_path)
    for table in _INTERVIEW_TABLES:
        assert _table_exists(c, table), f"{table} 应该在 init_schema 后出现"


def test_stage_preloads_interview_row(tmp_path):
    """本单元的判据是「`interview` 预置行在、原有三行不动」。

    ⚠️ 计划文本此处写的是「恰好四行」（`initial/screening/rejected/interview`），
    但 main 在排期包之后又落了 offer-generation U1——同一张 stage 表再补
    `offer`/`hired`（见 `tests/test_db_m2_schema.py::test_stage_table_preloads_six_rows`）。
    新库现在是六行，逐字照抄计划会恒红。故本断言只钉本单元新引入的那一行，
    ⛔ 不把后续单元的合法加行变成这里的回归。
    """
    c = _fresh_conn(tmp_path)
    rows = dict(c.execute("SELECT id, stage_type FROM stage").fetchall())
    assert rows["interview"] == "interview"
    # M2 三行是「既有行」，本单元只追加不修改。
    assert {"initial": "initial", "screening": "screening", "rejected": "rejected"}.items() <= rows.items()


def test_stage_check_rejects_unknown_type(tmp_path):
    """非法 stage_type 被 CHECK 拒（反证）。

    ⚠️ 计划文本此处拿 `'offer'` 当非法值，但 `offer` 已是 offer-generation U1
    合法化的 stage_type；照抄会在 `stage` 上撞**主键**（种子行 id 就是 'offer'）
    而"恰好"抛出同一种 IntegrityError——红灯变绿灯，但证的不是 CHECK。
    故改用任何单元都不认的取值。
    """
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO stage (id, name, stage_type) "
            "VALUES ('bogus', '不存在的阶段', 'bogus')"
        )


def test_hr_account_has_role_column_with_hr_default(tmp_path):
    c = _fresh_conn(tmp_path)
    assert "role" in _columns(c, "hr_account")
    c.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('a1', 'alice', 'h', 's')"
    )
    c.commit()
    assert c.execute("SELECT role FROM hr_account WHERE username='alice'").fetchone()[0] == "hr"


# ── 老库升级 ─────────────────────────────────────────────────────────────


def test_legacy_db_gains_all_interview_tables_after_init_schema(tmp_path):
    c = _legacy_db(tmp_path)
    init_schema(c)
    for table in _INTERVIEW_TABLES:
        assert _table_exists(c, table)


def test_legacy_db_existing_rows_are_untouched(tmp_path):
    """既有 job/candidate/resume/application 行内容不变；stage 原三行保留并新增
    interview 行；hr_account 既有列一个不少、新增 role 列且老行默认 'hr'。

    ⚠️ 两处相对计划文本的修正（main 在排期包之后又落了 Offer 包与入职包）：
    ① `before_stage_rows <= stage_rows` 对 list 是**前缀**比较（计划笔误），
       意图是「子集」——改用 set；
    ② `hr_account` 除 role 外还有 onboarding-flow U1 落的 `department`，
       故断言「既有列 ⊆ 升级后列 ∧ role 在 ∧ 老行 role='hr'」，
       ⛔ 不断言「只多 role」。
    """
    c = _legacy_db(tmp_path)
    before_job = c.execute("SELECT id, title, status FROM job WHERE id='old-job'").fetchone()
    before_candidate = c.execute("SELECT id, name FROM candidate WHERE id='old-cand'").fetchone()
    before_application = c.execute(
        "SELECT id, current_stage_id FROM application WHERE id='old-app'"
    ).fetchone()
    before_stage_rows = sorted(c.execute("SELECT id, stage_type FROM stage").fetchall())
    before_hr_cols = _columns(c, "hr_account")

    init_schema(c)

    assert before_job == c.execute(
        "SELECT id, title, status FROM job WHERE id='old-job'"
    ).fetchone()
    assert before_candidate == c.execute(
        "SELECT id, name FROM candidate WHERE id='old-cand'"
    ).fetchone()
    assert before_application == c.execute(
        "SELECT id, current_stage_id FROM application WHERE id='old-app'"
    ).fetchone()

    stage_rows = sorted(c.execute("SELECT id, stage_type FROM stage").fetchall())
    assert set(before_stage_rows) <= set(stage_rows)
    assert ("interview", "interview") in stage_rows

    after_hr_cols = _columns(c, "hr_account")
    assert before_hr_cols <= after_hr_cols
    assert "role" in after_hr_cols
    assert c.execute("SELECT role FROM hr_account WHERE id='old-acct'").fetchone()[0] == "hr"


# ── CHECK 反证 ───────────────────────────────────────────────────────────


def _insert_slot(c, *, status="scheduled", mode="onsite", kind="human"):
    c.execute("INSERT INTO job (id, title) VALUES ('j', '岗位')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c', '候选人')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r', 'j', 'synthetic', 'a.pdf', 'sha', 'hr')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a', 'c', 'j', 'r', 'interview')"
    )
    c.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, status, kind) "
        "VALUES ('s', 'a', 1, '2026-10-09 14:00', '2026-10-09 15:00', ?, ?, ?)",
        (mode, status, kind),
    )


@pytest.mark.parametrize(
    "status", ["scheduled", "rescheduled", "cancelled", "completed", "no_show"]
)
def test_interview_slot_accepts_valid_status(tmp_path, status):
    c = _fresh_conn(tmp_path)
    _insert_slot(c, status=status)
    c.commit()


def test_interview_slot_rejects_invalid_status(tmp_path):
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(c, status="bogus")


def test_interview_slot_rejects_invalid_mode(tmp_path):
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(c, mode="bogus")


def test_interview_slot_rejects_invalid_kind(tmp_path):
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(c, kind="ai_live")


def test_candidate_contact_rejects_invalid_source(tmp_path):
    c = _fresh_conn(tmp_path)
    c.execute("INSERT INTO job (id, title) VALUES ('j', '岗位')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c', '候选人')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r', 'j', 'synthetic', 'a.pdf', 'sha', 'hr')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a', 'c', 'j', 'r', 'interview')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO candidate_contact (application_id, registered_by, source) "
            "VALUES ('a', 'hr-1', 'bogus')"
        )


def test_interviewer_rejects_invalid_enabled(tmp_path):
    c = _fresh_conn(tmp_path)
    c.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('a1', 'alice', 'h', 's')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO interviewer (id, account_id, name, enabled) "
            "VALUES ('i1', 'a1', '张三', 2)"
        )


def test_interview_invitation_draft_version_is_unique_per_slot(tmp_path):
    c = _fresh_conn(tmp_path)
    c.execute("INSERT INTO job (id, title) VALUES ('j', '岗位')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c', '候选人')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r', 'j', 'synthetic', 'a.pdf', 'sha', 'hr')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a', 'c', 'j', 'r', 'interview')"
    )
    c.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s', 'a', 1, '2026-10-09 14:00', '2026-10-09 15:00', 'onsite')"
    )
    c.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "VALUES ('d1', 's', 1, 'v1', '正文', 1)"
    )
    c.commit()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO interview_invitation_draft "
            "(id, slot_id, version, template_version, body, ai_generated) "
            "VALUES ('d2', 's', 1, 'v1', '另一版', 1)"
        )
