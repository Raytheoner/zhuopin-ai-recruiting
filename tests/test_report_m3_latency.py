"""scripts/report_m3_latency.py：延迟统计与 docs/m3-voice-probe.md 幂等
写入（voice-structured-interview U4 tasks 5.10）。"""
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.report_m3_latency import collect_latencies, summarize, upsert_batch_section
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_turn(conn, *, session_id, seq, latency: dict):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', 't') ON CONFLICT(id) DO NOTHING")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三') ON CONFLICT(id) DO NOTHING")
    conn.execute(
        "INSERT OR IGNORE INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', 'job-1', 'synthetic', 'a.pdf', 'hash', 'tester')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        f"VALUES ('app-{session_id}', 'cand-1', 'job-1', 'resume-1', 'initial')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO analysis_run (id, configured_model, prompt_version, temperature, input_hash, raw_response) "
        f"VALUES ('run-{session_id}', 'test-model', 'v1', 0.0, 'hash', 'response')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        f"VALUES ('snap-{session_id}', 'app-{session_id}', 1, 1, 'run-{session_id}', 'frozen')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin) "
        f"VALUES ('q1', 'snap-{session_id}', 1, 'test', 'easy', 'test question', '{{}}', '[]', 'rationale', 'ai')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO interview_session (id, application_id, prep_snapshot_version, "
        f"retention_until, retention_policy_version, sample_class, status) "
        f"VALUES ('{session_id}', 'app-{session_id}', 1, '2099-01-01', 'v1', 'internal_sim', 'completed')"
    )
    conn.execute(
        "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, answer_text, "
        "answer_mode, latency_json) VALUES (?, ?, ?, 'q1', 't', 'a', 'voice', ?)",
        (f"{session_id}-{seq}", session_id, seq, json.dumps(latency, ensure_ascii=False)),
    )
    conn.commit()


def test_collect_latencies_gathers_values_per_segment(conn):
    _seed_turn(conn, session_id="s1", seq=1, latency={"endpoint_detection_ms": 300, "end_to_end_ms": 700})
    _seed_turn(conn, session_id="s1", seq=2, latency={"endpoint_detection_ms": 320, "end_to_end_ms": 750})
    values = collect_latencies(conn, session_ids=["s1"])
    assert values["endpoint_detection_ms"] == [300.0, 320.0]
    assert values["end_to_end_ms"] == [700.0, 750.0]
    assert values["asr_ms"] == []


def test_summarize_computes_median_and_p95():
    values = {"end_to_end_ms": [100.0, 200.0, 300.0, 400.0, 500.0]}
    summary = summarize(values)
    assert summary["end_to_end_ms"]["median"] == 300.0
    assert summary["end_to_end_ms"]["n"] == 5
    assert summary["end_to_end_ms"]["p95"] >= 400.0


def test_summarize_handles_empty_series():
    summary = summarize({"asr_ms": []})
    assert summary["asr_ms"] == {"median": 0.0, "p95": 0.0, "n": 0}


def test_upsert_batch_section_is_idempotent_and_overwrites_same_label(tmp_path):
    md_path = tmp_path / "m3-voice-probe.md"
    summary_v1 = {"end_to_end_ms": {"median": 700.0, "p95": 750.0, "n": 10}}
    summary_v2 = {"end_to_end_ms": {"median": 650.0, "p95": 720.0, "n": 12}}

    upsert_batch_section(md_path, "batch-1", summary_v1)
    upsert_batch_section(md_path, "batch-1", summary_v2)

    text = md_path.read_text(encoding="utf-8")
    assert text.count("batch-1") >= 1
    assert "650.0" in text
    assert "700.0" not in text  # 旧数据被覆盖，不是追加


def test_upsert_batch_section_keeps_different_labels_separate(tmp_path):
    md_path = tmp_path / "m3-voice-probe.md"
    upsert_batch_section(md_path, "batch-1", {"end_to_end_ms": {"median": 700.0, "p95": 750.0, "n": 10}})
    upsert_batch_section(md_path, "batch-2", {"end_to_end_ms": {"median": 600.0, "p95": 650.0, "n": 8}})
    text = md_path.read_text(encoding="utf-8")
    assert "batch-1" in text
    assert "batch-2" in text
