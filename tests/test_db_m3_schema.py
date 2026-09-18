"""M3 U1 面试域数据模型：新库建表齐全、老库升级既有表不变、全部 CHECK 反证。

本文件随 tasks.md 2.1–2.8 逐任务追加：
  ① 新库 fresh init_schema() 后逐表齐全 —— 各任务各自建表时先写
  ② 老库（复制 .51 demo.db 结构）升级后既有表一行不改 —— Task 8 统一补
  ③ 全部新增 CHECK 的反证（直接 INSERT，绕过应用层）—— 各表在各自任务里先写
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "m3.db"))
    init_schema(c)
    return c


def _seed_job_candidate_resume_application(
    conn, job_id="j1", candidate_id="c1", resume_id="r1", application_id="app1"
):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'approved')", (job_id,)
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (candidate_id,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'sha-1', 'hr-1')",
        (resume_id, job_id),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (application_id, candidate_id, job_id, resume_id),
    )
    conn.commit()


def _seed_analysis_run(conn, run_id="run1", prompt_version="interview-prep-v1"):
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES (?, 'deepseek-chat', ?, 0.0, 'hash-1', '{}')",
        (run_id, prompt_version),
    )
    conn.commit()


# ── prep_snapshot / prep_question（tasks 2.1）────────────────────────────


def test_prep_snapshot_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "prep_snapshot")
    assert _columns(conn, "prep_snapshot") == {
        "id", "application_id", "version", "profile_version", "resume_run_id",
        "gen_run_id", "confirmed_by", "confirmed_at", "status", "created_at",
    }


def test_prep_snapshot_unique_on_application_and_version(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
            "VALUES ('snap-2', 'app1', 1, 1, 'run1')"
        )


def test_prep_snapshot_allows_multiple_versions_per_application(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn, run_id="run1")
    _seed_analysis_run(conn, run_id="run2")
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-2', 'app1', 2, 1, 'run2')"
    )
    conn.commit()


def test_prep_snapshot_resume_run_id_is_nullable(conn):
    """spec「简历评分尚未完成」场景：prep 只按画像生成通用题目，无简历 run 可关联。"""
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT resume_run_id FROM prep_snapshot WHERE id='snap-1'"
    ).fetchone()
    assert row[0] is None


def test_prep_snapshot_status_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
            "VALUES ('snap-bad', 'app1', 1, 1, 'run1', 'published')"
        )


@pytest.mark.parametrize("status", ["draft", "frozen", "expired"])
def test_prep_snapshot_status_check_accepts_three_values(conn, status):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES (?, 'app1', 1, 1, 'run1', ?)",
        (f"snap-{status}", status),
    )
    conn.commit()


def test_prep_snapshot_defaults_to_draft(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT status FROM prep_snapshot WHERE id='snap-1'"
    ).fetchone()[0] == "draft"


def test_prep_question_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "prep_question")
    assert _columns(conn, "prep_question") == {
        "id", "snapshot_id", "seq", "dimension", "difficulty", "text",
        "rubric_json", "follow_ups_json", "rationale", "origin", "ai_text", "created_at",
    }


def _seed_prep_snapshot(conn, snapshot_id="snap-1", application_id="app1"):
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES (?, ?, 1, 1, 'run1')",
        (snapshot_id, application_id),
    )
    conn.commit()


def test_prep_question_unique_on_snapshot_and_seq(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale) "
        "VALUES ('q-1', 'snap-1', 1, 'diag_stack', 'medium', '问题', '{}', '[]', '依据画像')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_question "
            "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale) "
            "VALUES ('q-2', 'snap-1', 1, 'toolchain', 'easy', '另一题', '{}', '[]', '依据画像')"
        )


def test_prep_question_origin_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_question "
            "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin) "
            "VALUES ('q-bad', 'snap-1', 1, 'diag_stack', 'medium', '问题', '{}', '[]', '依据画像', 'human_written')"
        )


@pytest.mark.parametrize("origin", ["ai", "ai_edited"])
def test_prep_question_origin_check_accepts_two_values(conn, origin):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin) "
        "VALUES (?, 'snap-1', 1, 'diag_stack', 'medium', '问题', '{}', '[]', '依据画像', ?)",
        (f"q-{origin}", origin),
    )
    conn.commit()


def test_prep_question_ai_text_preserves_original_after_edit(conn):
    """spec「业务经理修改题面」：人工改过的题标 ai_edited，原 AI 文本仍可追溯。"""
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, "
        "origin, ai_text) "
        "VALUES ('q-1', 'snap-1', 1, 'diag_stack', 'medium', '人工改写后的题面', '{}', '[]', '依据画像', "
        "'ai_edited', 'AI 原始生成的题面')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT text, ai_text FROM prep_question WHERE id='q-1'"
    ).fetchone()
    assert row[0] == "人工改写后的题面"
    assert row[1] == "AI 原始生成的题面"
