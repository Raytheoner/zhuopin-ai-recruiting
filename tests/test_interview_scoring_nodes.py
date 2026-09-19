"""app/graph/interview_scoring_nodes.py：L4 编排层。数据库用真实 SQLite
（tmp_path），LLM 用脚本化假客户端（本文件的 compute_align 测试不涉及 LLM）。"""
import pytest

from app.graph.interview_scoring_nodes import AlignedTurn, compute_align
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_job_application_session(conn, *, job_id="job-1", application_id="app-1", session_id="sess-1"):
    conn.execute("INSERT INTO job (id, title) VALUES (?, '嵌入式软件工程师')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')", (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')", (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-prep', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', ?, 1, 1, 'run-prep', 'frozen')", (application_id,),
    )
    conn.execute(
        "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
        "rubric_json, follow_ups_json, rationale) VALUES "
        "('q1', 'snap-1', 1, 'AUTOSAR CP', 'easy', '讲讲你的项目', '{}', '[]', 'r')"
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES (?, ?, 1, '2099-01-01', 'v1', 'internal_sim', 'completed')",
        (session_id, application_id),
    )
    conn.commit()


def _insert_turn(conn, *, turn_id, session_id, seq, answer_mode="text", answer_text="回答内容",
                  asr_confidence=None, audio_start_ms=None, audio_end_ms=None):
    conn.execute(
        "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, "
        "answer_text, answer_mode, audio_start_ms, audio_end_ms, asr_confidence) "
        "VALUES (?, ?, ?, 'q1', '讲讲你的项目', ?, ?, ?, ?, ?)",
        (turn_id, session_id, seq, answer_text, answer_mode, audio_start_ms, audio_end_ms, asr_confidence),
    )
    conn.commit()


def test_compute_align_returns_turns_ordered_by_seq(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t2", session_id="sess-1", seq=2, answer_text="第二题回答")
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="第一题回答")

    aligned = compute_align(conn, session_id="sess-1")

    assert [t.turn_id for t in aligned] == ["t1", "t2"]
    assert aligned[0].answer_text == "第一题回答"
    assert all(isinstance(t, AlignedTurn) for t in aligned)


def test_compute_align_marks_low_confidence_voice_turn_using_default_threshold(conn):
    _seed_job_application_session(conn)
    _insert_turn(
        conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
        answer_text="低置信度回答", asr_confidence=0.5, audio_start_ms=0, audio_end_ms=5000,
    )
    _insert_turn(
        conn, turn_id="t2", session_id="sess-1", seq=2, answer_mode="voice",
        answer_text="高置信度回答", asr_confidence=0.9, audio_start_ms=0, audio_end_ms=5000,
    )

    aligned = compute_align(conn, session_id="sess-1")

    by_id = {t.turn_id: t for t in aligned}
    assert by_id["t1"].low_confidence is True
    assert by_id["t2"].low_confidence is False


def test_compute_align_uses_job_level_confidence_threshold_override(conn):
    _seed_job_application_session(conn)
    conn.execute(
        "UPDATE job_prep_config SET low_confidence_threshold = 0.95 WHERE job_id = 'job-1'"
    )
    if conn.execute("SELECT changes()").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO job_prep_config (job_id, low_confidence_threshold) VALUES ('job-1', 0.95)"
        )
    conn.commit()
    _insert_turn(
        conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
        answer_text="回答", asr_confidence=0.8, audio_start_ms=0, audio_end_ms=5000,
    )

    aligned = compute_align(conn, session_id="sess-1")

    assert aligned[0].low_confidence is True  # 0.8 < 岗位级覆盖值 0.95


def test_compute_align_text_turn_has_no_low_confidence_flag(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="text", answer_text="文本作答")

    aligned = compute_align(conn, session_id="sess-1")

    assert aligned[0].low_confidence is False  # asr_confidence 为 None，文本作答 turn 恒不标记
    assert aligned[0].audio_start_ms is None and aligned[0].audio_end_ms is None
