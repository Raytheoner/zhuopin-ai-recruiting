"""M3 U1 面试域数据模型：新库建表齐全、老库升级既有表不变、全部 CHECK 反证。

本文件随 tasks.md 2.1–2.8 逐任务追加：
  ① 新库 fresh init_schema() 后逐表齐全 —— 各任务各自建表时先写
  ② 老库（复制 .51 demo.db 结构）升级后既有表一行不改 —— Task 8 统一补
  ③ 全部新增 CHECK 的反证（直接 INSERT，绕过应用层）—— 各表在各自任务里先写
"""
import sqlite3
from pathlib import Path

import pytest

from app.storage.db import get_connection, init_schema, apply_column_migrations


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


# ── interview_session（tasks 2.2）───────────────────────────────────────


def _seed_interview_session(
    conn, session_id="sess-1", application_id="app1",
    retention_until="2026-12-01 00:00:00", sample_class="internal_sim",
):
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, retention_until, "
        "retention_policy_version, sample_class) "
        "VALUES (?, ?, 1, ?, 'v1', ?)",
        (session_id, application_id, retention_until, sample_class),
    )
    conn.commit()


def test_interview_session_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_session")
    assert _columns(conn, "interview_session") == {
        "id", "application_id", "prep_snapshot_version", "invite_token_hash",
        "invite_expires_at", "resume_token_hash", "phone_verified_at",
        "phone_attempts", "recording_uri", "retention_until",
        "retention_policy_version", "sample_class", "status", "created_at",
        "phone_code_hash", "phone_code_expires_at",
    }


def test_interview_session_retention_until_cannot_be_null(conn):
    """recording-retention spec「留存期限在场次建立时固定」：retention_until
    MUST 在建立时非空写入，不允许留空等以后补。"""
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-bad', 'app1', 1, NULL, 'v1', 'internal_sim')"
        )


def test_interview_session_retention_policy_version_cannot_be_null(conn):
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-bad', 'app1', 1, '2026-12-01 00:00:00', NULL, 'internal_sim')"
        )


def test_interview_session_sample_class_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-bad', 'app1', 1, '2026-12-01 00:00:00', 'v1', 'staged')"
        )


@pytest.mark.parametrize("sample_class", ["internal_sim", "live"])
def test_interview_session_sample_class_check_accepts_two_values(conn, sample_class):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn, session_id=f"sess-{sample_class}", sample_class=sample_class)


def test_interview_session_status_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class, status) "
            "VALUES ('sess-bad', 'app1', 1, '2026-12-01 00:00:00', 'v1', 'internal_sim', 'archived')"
        )


@pytest.mark.parametrize(
    "status", ["pending", "in_progress", "completed", "interrupted", "abandoned", "locked"]
)
def test_interview_session_status_check_accepts_six_values(conn, status):
    _seed_job_candidate_resume_application(conn)
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, retention_until, "
        "retention_policy_version, sample_class, status) "
        "VALUES (?, 'app1', 1, '2026-12-01 00:00:00', 'v1', 'internal_sim', ?)",
        (f"sess-{status}", status),
    )
    conn.commit()


def test_interview_session_status_defaults_to_pending(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    assert conn.execute(
        "SELECT status FROM interview_session WHERE id='sess-1'"
    ).fetchone()[0] == "pending"


def test_interview_session_invite_token_hash_is_unique(conn):
    _seed_job_candidate_resume_application(conn, application_id="app1")
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j2', '供应链总监', 'approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r2', 'j2', 'synthetic', 'b.pdf', 'sha-2', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app2', 'c2', 'j2', 'r2', 'initial')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, invite_token_hash, retention_until, "
        "retention_policy_version, sample_class) "
        "VALUES ('sess-1', 'app1', 1, 'hash-same', '2026-12-01 00:00:00', 'v1', 'internal_sim')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, invite_token_hash, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-2', 'app2', 1, 'hash-same', '2026-12-01 00:00:00', 'v1', 'internal_sim')"
        )


def test_interview_session_allows_multiple_null_invite_token_hash(conn):
    """令牌尚未签发时可空；SQLite 的 UNIQUE 把多个 NULL 视为互不相等
    （与 candidate.phone_hash 同一手法）。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn, session_id="sess-1")
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, retention_until, "
        "retention_policy_version, sample_class) "
        "VALUES ('sess-2', 'app1', 1, '2026-12-01 00:00:00', 'v1', 'internal_sim')"
    )
    conn.commit()


# ── interview_consent / identity_check（tasks 2.3）──────────────────────


def test_interview_consent_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_consent")
    assert _columns(conn, "interview_consent") == {
        "session_id", "kind", "result", "consent_version", "at",
    }


def test_interview_consent_primary_key_is_session_and_kind(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
        "VALUES ('sess-1', 'ai_interview', 'accepted', 'v1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
            "VALUES ('sess-1', 'ai_interview', 'declined', 'v1')"
        )


def test_interview_consent_allows_two_independent_kinds_per_session(conn):
    """spec「AI 面试与身份核验各自单独同意」：两项各自独立留痕。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
        "VALUES ('sess-1', 'ai_interview', 'accepted', 'v1')"
    )
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
        "VALUES ('sess-1', 'identity_check', 'accepted', 'v1')"
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM interview_consent WHERE session_id='sess-1'"
    ).fetchone()[0]
    assert count == 2


def test_interview_consent_kind_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
            "VALUES ('sess-1', 'video_interview', 'accepted', 'v1')"
        )


def test_interview_consent_result_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
            "VALUES ('sess-1', 'ai_interview', 'maybe', 'v1')"
        )


def test_identity_check_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "identity_check")
    assert _columns(conn, "identity_check") == {"session_id", "result", "checked_at"}


def test_identity_check_result_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', 'pending')"
        )


@pytest.mark.parametrize("result", ["pass", "fail", "skipped"])
def test_identity_check_result_check_accepts_three_values(conn, result):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', ?)", (result,)
    )
    conn.commit()


def test_identity_check_session_id_is_unique_primary_key(conn):
    """一期一个场次只产生一行核验结果（D13：一律 skipped，保留给活体/证件比对）。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', 'skipped')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', 'pass')"
        )


# ── m3-compliance-assertions spec「身份核验与评分隔离」的源码级反证 ───────
#
# identity_check 表结构上不允许出现图像列或任何可用于评分的列。这不是数据库
# CHECK 能表达的约束（CHECK 只能限制值域，不能限制"未来会不会加这一列"），
# 所以判据下沉到源码：直接扫描 sqlite_master.sql 的建表原文与
# PRAGMA table_info 的列名，任何一处出现 image/photo/face 子串就判违规。
# 真正把这条接入 CI 断言是 U7 tasks 8.1 的职责，这里只是 U1 自己的结构守护。


def test_identity_check_has_no_image_or_scoring_columns(conn):
    banned_substrings = ("image", "photo", "face", "video", "biometric")
    columns = _columns(conn, "identity_check")
    for column in columns:
        lowered = column.lower()
        assert not any(bad in lowered for bad in banned_substrings), (
            f"identity_check.{column} 命中禁止列名模式，疑似引入图像/生物特征列"
        )
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='identity_check'"
    ).fetchone()[0].lower()
    for bad in banned_substrings:
        assert bad not in ddl, f"identity_check 建表原文里出现了禁止词 {bad!r}"


# ── interview_turn（tasks 2.4）───────────────────────────────────────────


def _seed_prep_question(conn, question_id="q-1", snapshot_id="snap-1", seq=1):
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale) "
        "VALUES (?, ?, ?, 'diag_stack', 'medium', '问题文本', '{}', '[]', '依据画像')",
        (question_id, snapshot_id, seq),
    )
    conn.commit()


def _seed_turn_chain(conn):
    """搭好 interview_turn 需要的完整前置链：job/candidate/resume/application
    → prep_snapshot/prep_question → interview_session。"""
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    _seed_prep_question(conn)
    _seed_interview_session(conn)


def test_interview_turn_table_exists_with_expected_columns(conn):
    _seed_turn_chain(conn)
    assert _table_exists(conn, "interview_turn")
    assert _columns(conn, "interview_turn") == {
        "id", "session_id", "seq", "question_id", "question_text", "answer_text",
        "answer_mode", "audio_start_ms", "audio_end_ms", "latency_json",
        "follow_up_of", "interrupted_at_ms", "asr_confidence", "acoustic_ref", "created_at",
    }


def test_interview_turn_unique_on_session_and_seq(conn):
    _seed_turn_chain(conn)
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_mode) "
        "VALUES ('turn-1', 'sess-1', 1, 'q-1', '问题文本', 'voice')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode) "
            "VALUES ('turn-2', 'sess-1', 1, 'q-1', '问题文本', 'text')"
        )


def test_interview_turn_answer_mode_check_rejects_unknown_value(conn):
    _seed_turn_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode) "
            "VALUES ('turn-bad', 'sess-1', 1, 'q-1', '问题文本', 'video')"
        )


def test_interview_turn_text_mode_rejects_audio_offsets(conn):
    """live-voice-interview-session spec「文本作答降级」：文本作答的 turn
    MUST NOT 有音频起止。"""
    _seed_turn_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode, audio_start_ms) "
            "VALUES ('turn-bad', 'sess-1', 1, 'q-1', '问题文本', 'text', 100)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode, audio_end_ms) "
            "VALUES ('turn-bad2', 'sess-1', 1, 'q-1', '问题文本', 'text', 3000)"
        )


def test_interview_turn_voice_mode_allows_audio_offsets(conn):
    _seed_turn_chain(conn)
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_text, answer_mode, "
        "audio_start_ms, audio_end_ms) "
        "VALUES ('turn-1', 'sess-1', 1, 'q-1', '问题文本', '回答文本', 'voice', 0, 3000)"
    )
    conn.commit()


def test_interview_turn_follow_up_of_points_to_another_turn(conn):
    """spec「选择预埋追问」：turn 记录 follow_up_of 指向被追问的 turn。"""
    _seed_turn_chain(conn)
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_mode) "
        "VALUES ('turn-1', 'sess-1', 1, 'q-1', '问题文本', 'voice')"
    )
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_mode, follow_up_of) "
        "VALUES ('turn-2', 'sess-1', 2, 'q-1', '追问文本', 'voice', 'turn-1')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT follow_up_of FROM interview_turn WHERE id='turn-2'"
    ).fetchone()
    assert row[0] == "turn-1"


# ── interview_recording_deletion / interview_access_log（tasks 2.5）─────


def test_interview_recording_deletion_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_recording_deletion")
    assert _columns(conn, "interview_recording_deletion") == {
        "session_id", "deleted_at", "scope", "reason", "actor",
    }


def test_interview_recording_deletion_session_id_is_unique(conn):
    """interview-recording-retention spec「重复扫描」：已删除的场次再次被
    扫描到不产生新的删除留痕。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
        "VALUES ('sess-1', 'recording+transcript', 'expired', 'system')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
            "VALUES ('sess-1', 'recording+transcript', 'expired', 'system')"
        )


def test_interview_recording_deletion_reason_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
            "VALUES ('sess-1', 'recording', 'user_request', 'system')"
        )


@pytest.mark.parametrize("reason", ["expired", "withdrawn", "terminated"])
def test_interview_recording_deletion_reason_check_accepts_three_values(conn, reason):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
        "VALUES ('sess-1', 'recording', ?, 'hr-1')",
        (reason,),
    )
    conn.commit()


def test_interview_recording_deletion_actor_cannot_be_blank(conn):
    """删除动作必须有执行者——空 actor 等于没留痕（与 human_review.reviewer
    同一 CHECK 手法）。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
            "VALUES ('sess-1', 'recording', 'expired', '   ')"
        )


def test_interview_access_log_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_access_log")
    assert _columns(conn, "interview_access_log") == {
        "id", "accessor", "session_id", "access_type", "at",
    }


def test_interview_access_log_has_no_content_columns(conn):
    """interview-recording-retention spec「访问留痕」：留痕 MUST 不含录音或
    转写内容。"""
    cols = _columns(conn, "interview_access_log")
    assert not ({"text", "content", "transcript", "audio"} & cols)


def test_interview_access_log_accessor_cannot_be_blank(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_access_log (id, accessor, session_id, access_type) "
            "VALUES ('log-1', '  ', 'sess-x', 'recording_playback')"
        )


@pytest.mark.parametrize(
    "access_type", ["recording_playback", "transcript_view", "scorecard_view", "export"]
)
def test_interview_access_log_access_type_accepts_four_values(conn, access_type):
    conn.execute(
        "INSERT INTO interview_access_log (id, accessor, session_id, access_type) "
        "VALUES (?, 'interviewer-1', 'sess-x', ?)",
        (f"log-{access_type}", access_type),
    )
    conn.commit()


def test_interview_access_log_access_type_rejects_unknown_value(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_access_log (id, accessor, session_id, access_type) "
            "VALUES ('log-bad', 'interviewer-1', 'sess-x', 'preview')"
        )


# ── job_prep_config（tasks 2.6）────────────────────────────────────────────


def test_job_prep_config_defaults_when_no_row(conn):
    """没有 job_prep_config 行的岗位，读取端回落到默认值——那是
    app/graph/interview_prep_nodes.py::load_prep_config()（Task 4）的事。
    本测试只确认表本身**存在一行**时的列默认值是 'easy_to_hard'/10。"""
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', '测试岗位')")
    conn.execute("INSERT INTO job_prep_config (job_id) VALUES ('job-1')")
    row = conn.execute(
        "SELECT prep_curve, prep_question_count FROM job_prep_config WHERE job_id='job-1'"
    ).fetchone()
    assert row == ("easy_to_hard", 10)


def test_job_prep_config_rejects_invalid_curve(conn):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-2', '测试岗位')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_prep_config (job_id, prep_curve) VALUES ('job-2', 'bogus')"
        )


# ── 老库升级：M3 U1 落地前的 .51 现网库真实形态 ───────────────────────────
#
# 基线不是从 SCHEMA 裁剪，而是刻意固定成 Task 1 生成的历史快照——它代表
# "M3 U1 上线前，.51 上的库长什么样"，不随 SCHEMA 一起演进（与
# tests/test_db_migration.py 顶部注释同一理由：派生的话测试会随 SCHEMA 一起
# 演进，永远测不出"老库升级不了"这个真正要防的故障）。

_LEGACY_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zp51_demo_db_schema_pre_m3.sql"

_M3_NEW_TABLES = (
    "prep_snapshot", "prep_question", "interview_consent",
    "identity_check", "interview_turn", "interview_recording_deletion",
    "interview_access_log", "interview_invite_event",
)


def _legacy_pre_m3_db(tmp_path):
    c = get_connection(str(tmp_path / "legacy_pre_m3.db"))
    ddl = _LEGACY_FIXTURE_PATH.read_text(encoding="utf-8")
    c.executescript(ddl)
    # sqlite_master.sql 只存 DDL，不存 SCHEMA 常量里紧跟 stage 建表之后的
    # `INSERT OR IGNORE INTO stage ...` 三行种子数据——这三行必须在这里手工
    # 补上，否则下面插入 application 行会因为 current_stage_id 的外键指向
    # 一张空的 stage 表而失败（本计划写作时已实测踩到这个 FOREIGN KEY
    # constraint failed，在此补齐修正）。
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected')")
    # 挑几张跨 M1/M2 都在用的老表插入历史数据，验证升级后行数与内容不变。
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
    c.commit()
    return c


def _legacy_sqlite_master_sql(conn: sqlite3.Connection, known_names: set[str]) -> dict[str, str]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {name: sql for name, sql in rows if name in known_names}


def test_legacy_pre_m3_db_gains_all_new_tables_after_init_schema(tmp_path):
    conn = _legacy_pre_m3_db(tmp_path)

    init_schema(conn)

    for table in _M3_NEW_TABLES:
        assert _table_exists(conn, table), f"{table} 应该在 init_schema 后出现"


def test_legacy_pre_m3_db_existing_tables_and_rows_are_untouched(tmp_path):
    """M3 U1 的字面判据：老库升级后既有表一行不改。既比列集合，也比
    sqlite_master.sql 原文（CHECK/DEFAULT/REFERENCES 措辞是否被悄悄改写），
    还比几张关键表的行数。"""
    conn = _legacy_pre_m3_db(tmp_path)
    known_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    before_sql = _legacy_sqlite_master_sql(conn, known_names)
    before_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("job", "candidate", "resume", "application")
    }

    init_schema(conn)

    after_sql = _legacy_sqlite_master_sql(conn, known_names)
    after_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("job", "candidate", "resume", "application")
    }

    assert after_sql == before_sql
    assert after_counts == before_counts


def test_legacy_pre_m3_db_init_schema_is_idempotent(tmp_path):
    """重跑三次不报错——UNIQUE INDEX 与 CHECK 都必须带 IF NOT EXISTS 的
    幂等性（M3 新加的 8 张表同样要满足）。"""
    conn = _legacy_pre_m3_db(tmp_path)

    init_schema(conn)
    init_schema(conn)
    init_schema(conn)

    for table in _M3_NEW_TABLES:
        assert _table_exists(conn, table)


def test_m3_new_tables_never_enter_the_add_column_path():
    """本单元的第二条硬约束：8 张全新表一个都不许进 _ADDED_COLUMNS——加列
    路径只服务"老库缺列"，把新表塞进去会让 apply_column_migrations 对着一张
    不存在的表执行 ALTER TABLE。"""
    from app.storage.db import _ADDED_COLUMNS

    tables_touched = {table for table, _column, _ddl in _ADDED_COLUMNS}
    assert not (set(_M3_NEW_TABLES) & tables_touched)


def test_fresh_and_legacy_upgraded_schemas_have_identical_m3_tables(tmp_path):
    """新库直接 init_schema() 与老库升级后，M3 新表的列集合必须完全一致——
    两条路径不能产生两种不同形状的表。"""
    fresh = get_connection(str(tmp_path / "fresh.db"))
    init_schema(fresh)

    legacy = _legacy_pre_m3_db(tmp_path)
    init_schema(legacy)

    for table in _M3_NEW_TABLES:
        assert _columns(fresh, table) == _columns(legacy, table), table


def test_interview_invite_event_table_exists(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(interview_invite_event)")}
    assert cols == {"id", "session_id", "event_type", "detail", "at"}


def test_interview_invite_event_type_check_rejects_unknown_value(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    # 创建必要的前置数据以通过FK约束
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'ECU 工程师', 'open')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class) "
        "VALUES ('s1', 'a1', 1, '2027-01-01T00:00:00+00:00', 'v1-90d', 'internal_sim')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_invite_event (id, session_id, event_type) "
            "VALUES ('e1', 's1', 'not_a_real_event_type')"
        )


def test_job_prep_config_invite_expiry_days_default(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'ECU 工程师', 'open')")
    conn.execute("INSERT INTO job_prep_config (job_id) VALUES ('j1')")
    row = conn.execute(
        "SELECT invite_expiry_days FROM job_prep_config WHERE job_id = 'j1'"
    ).fetchone()
    assert row[0] == 7


def test_interview_session_phone_code_columns_exist(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(interview_session)")}
    assert {"phone_code_hash", "phone_code_expires_at"} <= cols


def test_legacy_db_gains_new_columns_via_migration(tmp_path):
    """老库（U1/U2 已建表，没有本单元新列）跑 apply_column_migrations 后补齐，
    既有行一列不丢（工程铁律「老库升级后既有表一行不改」的延伸：只加列不改值）。"""
    db_path = str(tmp_path / "legacy.db")
    conn = get_connection(db_path)
    init_schema(conn)
    # 模拟老库：手工删掉本单元要加的三列，重建成 U2 时代的形状
    conn.execute("ALTER TABLE job_prep_config RENAME TO job_prep_config_old")
    conn.execute(
        "CREATE TABLE job_prep_config (job_id TEXT PRIMARY KEY, "
        "prep_curve TEXT NOT NULL DEFAULT 'easy_to_hard', "
        "prep_question_count INTEGER NOT NULL DEFAULT 10)"
    )
    conn.execute(
        "INSERT INTO job_prep_config (job_id, prep_curve, prep_question_count) "
        "SELECT job_id, prep_curve, prep_question_count FROM job_prep_config_old"
    )
    conn.execute("DROP TABLE job_prep_config_old")
    conn.commit()
    added = apply_column_migrations(conn)
    assert "job_prep_config.invite_expiry_days" in added
    cols = {row[1] for row in conn.execute("PRAGMA table_info(job_prep_config)")}
    assert "invite_expiry_days" in cols
