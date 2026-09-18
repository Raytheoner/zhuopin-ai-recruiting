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
