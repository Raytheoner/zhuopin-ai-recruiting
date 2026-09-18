"""M2 U1 数据模型：新库建表齐全、老库升级既有表不变、全部 CHECK 反证。

本文件三段结构（随后续任务继续追加）：
  ① 新库 fresh init_schema() 后逐表齐全 —— 本任务先写 candidate/resume/resume_text_span 三张
  ② 老库（复制 .51 demo.db 结构）升级后既有表一行不改 —— Task 7 统一补
  ③ 全部新增 CHECK 的反证（直接 INSERT，绕过应用层）—— 各表在各自任务里先写，Task 7 汇总检查覆盖面
"""
import json
import sqlite3
from pathlib import Path

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
    c = get_connection(str(tmp_path / "m2.db"))
    init_schema(c)
    return c


# ── candidate / resume / resume_text_span（tasks 2.1）───────────────────


def test_candidate_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "candidate")
    assert _columns(conn, "candidate") == {"id", "name", "phone_hash", "created_at"}


def test_candidate_has_no_status_column(conn):
    """CLAUDE.md 数据模型要点：状态属于投递不属于候选人。"""
    assert "status" not in _columns(conn, "candidate")
    assert "current_stage_id" not in _columns(conn, "candidate")


def test_candidate_unique_on_name_and_phone_hash(conn):
    conn.execute(
        "INSERT INTO candidate (id, name, phone_hash) VALUES ('c-1', '张三', 'hash-abc')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate (id, name, phone_hash) VALUES ('c-2', '张三', 'hash-abc')"
        )


def test_candidate_allows_multiple_rows_with_null_phone_hash(conn):
    """解析没能拿到手机号时 phone_hash 可空；SQLite 的 UNIQUE 把多个 NULL 视为互不相等，
    这正是我们想要的行为——没有手机号就不该被强行合并成同一人。"""
    conn.execute("INSERT INTO candidate (id, name, phone_hash) VALUES ('c-3', '李四', NULL)")
    conn.execute("INSERT INTO candidate (id, name, phone_hash) VALUES ('c-4', '李四', NULL)")
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM candidate WHERE name='李四'").fetchone()[0]
    assert rows == 2


def test_resume_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume")
    assert _columns(conn, "resume") == {
        "id", "job_id", "sample_class", "file_name", "content_sha256",
        "status", "parsed_json", "parse_confidence", "parser_version",
        # M2 U2 task 3：抽取出的全文，resume_text_span 的偏移量相对这份原文。
        "raw_text",
        "uploaded_by", "uploaded_at",
    }


@pytest.mark.parametrize("bad_class", ["Live", "real", "", "LIVE "])
def test_resume_sample_class_check_rejects_invalid_values(conn, bad_class):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES ('r-bad', 'j1', ?, 'a.pdf', 'sha-x', 'hr-1')",
            (bad_class,),
        )


@pytest.mark.parametrize("good_class", ["synthetic", "anonymized", "departed", "live"])
def test_resume_sample_class_check_accepts_all_four_values(conn, good_class):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.commit()
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, 'j1', ?, 'a.pdf', ?, 'hr-1')",
        (f"r-{good_class}", good_class, f"sha-{good_class}"),
    )
    conn.commit()


def test_resume_dedup_unique_index_on_job_and_content_hash(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-1', 'j1', 'synthetic', 'a.pdf', 'sha-same', 'hr-1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES ('r-2', 'j1', 'synthetic', 'b.pdf', 'sha-same', 'hr-1')"
        )


def test_resume_same_content_hash_allowed_across_different_jobs(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j2', '供应链总监', 'approved')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-1', 'j1', 'synthetic', 'a.pdf', 'sha-same', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-2', 'j2', 'synthetic', 'a.pdf', 'sha-same', 'hr-1')"
    )
    conn.commit()


def test_resume_status_check_accepts_three_values(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.commit()
    for status in ("pending", "parsed", "unreadable"):
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, status, uploaded_by) "
            "VALUES (?, 'j1', 'synthetic', 'a.pdf', ?, ?, 'hr-1')",
            (f"r-{status}", f"sha-{status}", status),
        )
    conn.commit()


def test_resume_text_span_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume_text_span")
    assert _columns(conn, "resume_text_span") == {"resume_id", "span_id", "start", "end", "text"}


def test_resume_text_span_primary_key_is_resume_and_span(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
        "VALUES ('r-1', 1, 0, 5, '张三简历')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
            "VALUES ('r-1', 1, 10, 15, '重复的 span_id')"
        )


# ── application / stage / application_stage_history（tasks 2.2）─────────


def _seed_job_candidate_resume(conn, job_id="j1", candidate_id="c1", resume_id="r1"):
    conn.execute("INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'approved')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (candidate_id,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'sha-1', 'hr-1')",
        (resume_id, job_id),
    )
    conn.commit()


def test_stage_table_preloads_three_rows(conn):
    rows = dict(conn.execute("SELECT id, stage_type FROM stage").fetchall())
    assert rows == {"initial": "initial", "screening": "screening", "rejected": "rejected"}


def test_stage_type_check_rejects_unknown_type(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage (id, name, stage_type) VALUES ('offer', '发 offer', 'offer')"
        )


def test_application_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "application")
    assert _columns(conn, "application") == {
        "id", "candidate_id", "job_id", "resume_id", "current_stage_id",
        "status", "kanban_state", "created_at",
    }


def test_application_resume_id_is_unique(conn):
    """一条 resume 只能挂一条 application——1:1。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES ('app-2', 'c1', 'j1', 'r1', 'initial')"
        )


def test_application_status_check(conn):
    _seed_job_candidate_resume(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
            "VALUES ('app-bad', 'c1', 'j1', 'r1', 'initial', 'unknown_status')"
        )


def test_application_kanban_state_defaults_null_and_accepts_pending_reject(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT kanban_state FROM application WHERE id='app-1'"
    ).fetchone()[0] is None

    conn.execute("UPDATE application SET kanban_state='pending_reject' WHERE id='app-1'")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE application SET kanban_state='bogus' WHERE id='app-1'")


def test_application_stage_history_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "application_stage_history")
    assert _columns(conn, "application_stage_history") == {
        "id", "application_id", "from_stage_id", "to_stage_id",
        "actor_type", "actor", "occurred_at",
    }


def test_application_stage_history_actor_type_check(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type) "
            "VALUES ('h-1', 'app-1', 'screening', 'system')"
        )


def test_status_lives_on_application_not_candidate(conn):
    """CLAUDE.md 数据模型要点：不要合并 candidate 和 application，状态挂在投递上。"""
    assert "status" not in _columns(conn, "candidate")
    assert "status" in _columns(conn, "application")


# ── rejection_record（tasks 2.3）─────────────────────────────────────────


def test_rejection_record_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "rejection_record")
    assert _columns(conn, "rejection_record") == {
        "id", "application_id", "reason_type", "rule_ref", "human_readable",
        "decided_by", "batch_id", "appeal_status", "decided_at",
    }


def test_rejection_record_reason_type_rejects_ai_score(conn):
    """合规红线机器判据：直接 INSERT reason_type='ai_score' 必须被 CHECK 拒绝。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
            "VALUES ('rej-1', 'app-1', 'ai_score', 'hr-1')"
        )


@pytest.mark.parametrize("reason_type", ["hard_rule", "human_decision"])
def test_rejection_record_reason_type_accepts_legal_values(conn, reason_type):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES (?, 'app-1', ?, 'hr-1')",
        (f"rej-{reason_type}", reason_type),
    )
    conn.commit()


def test_rejection_record_decided_by_cannot_be_blank(conn):
    """淘汰必须有人工确认节点并留痕——决策人为空的留痕等于没留痕
    （与 human_review.reviewer 同一 CHECK 手法）。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
            "VALUES ('rej-blank', 'app-1', 'hard_rule', '   ')"
        )


def test_rejection_record_appeal_status_defaults_none_and_check(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rej-1', 'app-1', 'hard_rule', 'hr-1')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT appeal_status FROM rejection_record WHERE id='rej-1'"
    ).fetchone()[0] == "none"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE rejection_record SET appeal_status='approved' WHERE id='rej-1'"
        )


# ── resume_access_log / field_review_queue / screening_flag（tasks 2.4）─


def test_resume_access_log_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume_access_log")
    assert _columns(conn, "resume_access_log") == {
        "id", "accessor", "resume_id", "access_type", "accessed_at",
    }


def test_resume_access_log_has_no_content_columns(conn):
    """spec「简历访问留痕」：留痕记录 MUST NOT 包含简历内容本身。"""
    cols = _columns(conn, "resume_access_log")
    assert not ({"text", "content", "parsed_json"} & cols)


def test_resume_access_log_accessor_cannot_be_blank(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
            "VALUES ('log-1', '  ', 'r-x', 'raw_text')"
        )


@pytest.mark.parametrize("access_type", ["raw_text", "spans", "parsed_result", "download"])
def test_resume_access_log_access_type_accepts_four_values(conn, access_type):
    conn.execute(
        "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
        "VALUES (?, 'hr-1', 'r-x', ?)",
        (f"log-{access_type}", access_type),
    )
    conn.commit()


def test_resume_access_log_access_type_rejects_unknown_value(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
            "VALUES ('log-bad', 'hr-1', 'r-x', 'preview')"
        )


def test_field_review_queue_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "field_review_queue")
    assert _columns(conn, "field_review_queue") == {
        "id", "resume_id", "field", "machine_value", "confidence",
        "status", "reviewed_by", "reviewed_at", "human_value", "created_at",
    }


def test_field_review_queue_status_check(conn):
    _seed_job_candidate_resume(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO field_review_queue (id, resume_id, field, status) "
            "VALUES ('q-1', 'r1', 'years_of_experience', 'closed')"
        )


def test_field_review_queue_rejects_second_pending_row_for_same_field(conn):
    """结构性幂等guard：同一简历同一字段不该同时有两条待校对行。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO field_review_queue (id, resume_id, field) VALUES ('q-1', 'r1', 'years_of_experience')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO field_review_queue (id, resume_id, field) VALUES ('q-2', 'r1', 'years_of_experience')"
        )


def test_screening_flag_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "screening_flag")
    assert _columns(conn, "screening_flag") == {
        "id", "application_id", "profile_version", "rule_ref",
        "verdict", "reason", "evidence_ref", "created_at",
    }


def test_screening_flag_verdict_check(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict) "
            "VALUES ('sf-1', 'app-1', 1, 'edu-gte-bachelor', 'maybe')"
        )


def test_screening_flag_fail_requires_evidence_ref(conn):
    """工程铁律 4：每条 fail 标记必须带 evidence_ref，为空不允许写入。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict, evidence_ref) "
            "VALUES ('sf-1', 'app-1', 1, 'edu-gte-bachelor', 'fail', NULL)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict, evidence_ref) "
            "VALUES ('sf-2', 'app-1', 1, 'edu-gte-bachelor', 'fail', '   ')"
        )


def test_screening_flag_pass_and_skipped_allow_null_evidence(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO screening_flag "
        "(id, application_id, profile_version, rule_ref, verdict) "
        "VALUES ('sf-pass', 'app-1', 1, 'edu-gte-bachelor', 'pass')"
    )
    conn.execute(
        "INSERT INTO screening_flag "
        "(id, application_id, profile_version, rule_ref, verdict) "
        "VALUES ('sf-skip', 'app-1', 1, 'years-gte-3', 'skipped')"
    )
    conn.commit()


# ── resume_embedding / eval_* / hr_account（tasks 2.5）───────────────────


def test_resume_embedding_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume_embedding")
    assert _columns(conn, "resume_embedding") == {
        "resume_id", "model", "dim", "vector", "created_at",
    }


def test_resume_embedding_primary_key_is_resume_and_model(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
        "VALUES ('r1', 'bge-m3', 1024, X'0102')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
            "VALUES ('r1', 'bge-m3', 1024, X'0304')"
        )


def test_resume_embedding_allows_multiple_models_per_resume(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
        "VALUES ('r1', 'bge-m3', 1024, X'0102')"
    )
    conn.execute(
        "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
        "VALUES ('r1', 'bge-m3-v2', 1024, X'0304')"
    )
    conn.commit()


def test_eval_import_batch_table_exists_and_has_training_ban_comment(conn):
    assert _table_exists(conn, "eval_import_batch")
    assert _columns(conn, "eval_import_batch") == {
        "id", "job_id", "source_archive_path", "imported_by", "imported_at", "row_count",
    }
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='eval_import_batch'"
    ).fetchone()[0]
    assert "训练" in sql


def test_eval_sample_table_exists_and_has_training_ban_comment(conn):
    assert _table_exists(conn, "eval_sample")
    assert _columns(conn, "eval_sample") == {
        "id", "job_id", "sample_ref", "import_batch_id", "created_at",
    }
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='eval_sample'"
    ).fetchone()[0]
    assert "训练" in sql


def test_eval_annotation_table_exists_and_has_training_ban_comment(conn):
    assert _table_exists(conn, "eval_annotation")
    assert _columns(conn, "eval_annotation") == {
        "id", "eval_sample_id", "field_values_json", "human_rank",
        "annotated_by", "annotated_at", "import_batch_id", "created_at",
    }
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='eval_annotation'"
    ).fetchone()[0]
    assert "训练" in sql


def _seed_eval_batch_and_sample(conn, batch_id="b1", sample_id="s1"):
    conn.execute(
        "INSERT INTO eval_import_batch (id, job_id, imported_by, row_count) "
        "VALUES (?, 'j1', 'hr-1', 1)",
        (batch_id,),
    )
    conn.execute(
        "INSERT INTO eval_sample (id, job_id, sample_ref, import_batch_id) "
        "VALUES (?, 'j1', 'sample-001', ?)",
        (sample_id, batch_id),
    )
    conn.commit()


def test_eval_annotation_same_batch_reimport_is_rejected_by_unique_index(conn):
    """同一批次重复导入不产生重复标注——唯一索引在 (import_batch_id, eval_sample_id)。"""
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    _seed_eval_batch_and_sample(conn)
    conn.execute(
        "INSERT INTO eval_annotation "
        "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
        "VALUES ('ann-1', 's1', '{}', '汤丽萍', '2026-09-17 10:00:00', 'b1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO eval_annotation "
            "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
            "VALUES ('ann-2', 's1', '{}', '汤丽萍', '2026-09-17 10:05:00', 'b1')"
        )


def test_eval_annotation_keeps_history_across_different_batches(conn):
    """不同批次可以对同一样本再标注一次——保留历史，不覆盖。"""
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    _seed_eval_batch_and_sample(conn, batch_id="b1", sample_id="s1")
    conn.execute(
        "INSERT INTO eval_import_batch (id, job_id, imported_by, row_count) VALUES ('b2', 'j1', 'hr-1', 1)"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO eval_annotation "
        "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
        "VALUES ('ann-1', 's1', '{}', '汤丽萍', '2026-09-17 10:00:00', 'b1')"
    )
    conn.execute(
        "INSERT INTO eval_annotation "
        "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
        "VALUES ('ann-2', 's1', '{}', '汤丽萍', '2026-09-20 10:00:00', 'b2')"
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM eval_annotation WHERE eval_sample_id='s1'"
    ).fetchone()[0]
    assert count == 2


def test_hr_account_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "hr_account")
    assert _columns(conn, "hr_account") == {
        "id", "username", "password_hash", "password_salt", "created_at",
    }


def test_hr_account_username_is_unique(conn):
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('acc-1', 'tangliping', 'h1', 's1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO hr_account (id, username, password_hash, password_salt) "
            "VALUES ('acc-2', 'tangliping', 'h2', 's2')"
        )


# ── 老库升级：.51 现网 demo.db 在 U1 上线前的真实形态 ─────────────────────
#
# 基线不是从 SCHEMA 裁剪，而是刻意固定成一份历史快照——它代表"U1 上线前，
# .51 上的库长什么样"，不随 SCHEMA 一起演进（与 tests/test_db_migration.py
# 顶部注释同一理由：派生的话测试会随 SCHEMA 一起演进，永远测不出"老库升级
# 不了"这个真正要防的故障）。快照文件 2026-09-18 取自生产 .51 的
# demo.db（sqlite_master.sql，只含 DDL、不含任何数据行），见
# tests/fixtures/zp51_demo_db_schema_pre_u1.sql。

_LEGACY_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zp51_demo_db_schema_pre_u1.sql"

_LEGACY_TABLES = (
    "job", "job_profile", "conversation", "effect_log", "outbox",
    "checkpoints", "writes",
    "analysis_run", "criterion_score", "pending_approval", "human_review",
    "hard_requirement",
)

_M2_U1_NEW_TABLES = (
    "candidate", "resume", "resume_text_span", "application", "stage",
    "application_stage_history", "rejection_record", "resume_access_log",
    "field_review_queue", "screening_flag", "resume_embedding",
    "eval_import_batch", "eval_sample", "eval_annotation", "hr_account",
)


def _legacy_db(tmp_path):
    c = get_connection(str(tmp_path / "legacy_pre_u1.db"))
    ddl = _LEGACY_FIXTURE_PATH.read_text(encoding="utf-8")
    # sqlite_sequence 是 SQLite 内建表（AUTOINCREMENT 列——本快照里是
    # outbox.id——建表时自动创建），手工 CREATE 会报 "object name reserved
    # for internal use"，加载快照前把这条语句整行摘掉即可，其余原样
    # executescript（该调用本身就支持一次跑多条以 ; 分隔的语句，含内嵌注释）。
    ddl = ddl.replace("CREATE TABLE sqlite_sequence(name,seq);\n", "")
    c.executescript(ddl)
    c.execute("INSERT INTO job (id, title, status) VALUES ('old-job', '采购工程师', 'approved')")
    c.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('old-job-v1', 'old-job', 1, 'approved', ?)",
        (json.dumps({"job_title": "采购工程师"}, ensure_ascii=False),),
    )
    c.execute(
        "INSERT INTO human_review (id, job_id, profile_version, decision_type, reviewer) "
        "VALUES ('hr-1', 'old-job', 1, 'approved', 'someone')"
    )
    c.commit()
    return c


def _legacy_sqlite_master_sql(conn: sqlite3.Connection, known_names: set[str]) -> dict[str, str]:
    """既有表 + 既有索引的建表/建索引原文，按名字过滤到升级前就存在的对象。

    用于比对 init_schema 前后一字不改——列集合相同不代表 DDL 原文相同（比如
    CHECK 约束、DEFAULT、REFERENCES 措辞都不体现在 PRAGMA table_info 里）。
    """
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {name: sql for name, sql in rows if name in known_names}


def test_legacy_pre_u1_db_gains_all_new_tables_after_init_schema(tmp_path):
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    for table in _M2_U1_NEW_TABLES:
        assert _table_exists(conn, table), f"{table} 应该在 init_schema 后出现"


def test_legacy_pre_u1_db_existing_tables_and_rows_are_untouched(tmp_path):
    """
    "M2 U1 不得动老表"的字面判据。job 表是本判据的刻意例外：M2 U2 task 3
    （design D5）经 _ADDED_COLUMNS 给 job 合法新增 parse_confidence_threshold，
    这是被设计文档认可的加列机制，不是本测试要拦的"未声明改动"。job 单独
    验证放在 tests/test_db_migration.py::test_job_columns_are_pinned（钉住加列
    后的新列集合）与 tests/test_db_m2_u2_schema.py（专门验证这次迁移本身），
    这里只把 job 从"列集合/行数/DDL 原文逐字不变"的老表未触碰判据里摘出去，
    其余老表的保护力度不变。
    """
    untouched_tables = tuple(t for t in _LEGACY_TABLES if t != "job")
    conn = _legacy_db(tmp_path)
    before_columns = {t: _columns(conn, t) for t in untouched_tables}
    before_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in untouched_tables
    }
    known_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if row[0] != "job"
    }
    before_sql = _legacy_sqlite_master_sql(conn, known_names)

    init_schema(conn)

    after_columns = {t: _columns(conn, t) for t in untouched_tables}
    after_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in untouched_tables
    }
    after_sql = _legacy_sqlite_master_sql(conn, known_names)

    assert after_columns == before_columns
    assert after_counts == before_counts
    # "既有表一行不改"的字面判据：升级前就存在的每张表、每个索引，其
    # sqlite_master.sql 原文必须逐字相同——列集合相同不足以覆盖 CHECK/
    # DEFAULT/REFERENCES 措辞被悄悄改写的情况。
    assert after_sql == before_sql


def test_added_columns_tuple_still_only_touches_job_profile():
    """reviewer 机械判据：本单元 diff 不得往 _ADDED_COLUMNS 里塞新表——
    新表一律走 CREATE TABLE IF NOT EXISTS。

    M2 U2 task 3（design D5）往 _ADDED_COLUMNS 加了 job 的
    parse_confidence_threshold——job 是老表（SCHEMA 里一直有 CREATE TABLE IF
    NOT EXISTS job），不是新表，不违反这条护栏的本意，预期表集合相应放宽。

    final review 后再放宽到含 resume：resume 是 M2 U1 建的表，U1 合并之后建的
    任何库里它都已经存在，U2 给它加的 raw_text 属于"老表缺列"，必须登记进
    _ADDED_COLUMNS 才补得上（漏登记的话老库上每次上传都 500）。护栏本意不变：
    进这个集合的表必须在 SCHEMA 里已有 CREATE TABLE IF NOT EXISTS。
    """
    from app.storage.db import _ADDED_COLUMNS

    tables_in_added_columns = {row[0] for row in _ADDED_COLUMNS}
    assert tables_in_added_columns == {"job_profile", "job", "resume"}


def test_fresh_and_legacy_upgraded_schemas_have_identical_m2_u1_tables(tmp_path):
    """新库直接 init_schema() 与老库升级后，M2 U1 新表的列集合必须完全一致——
    两条路径不能产生两种不同形状的表。"""
    fresh = get_connection(str(tmp_path / "fresh.db"))
    init_schema(fresh)

    legacy = _legacy_db(tmp_path)
    init_schema(legacy)

    for table in _M2_U1_NEW_TABLES:
        assert _columns(fresh, table) == _columns(legacy, table), table
