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


import json

from app.graph.interview_scoring_nodes import compute_score
from app.llm.gateway import LLMGateway


class _ScriptedClient:
    def __init__(self, bodies):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class _RecordingHook:
    def __init__(self, conn):
        self._conn = conn
        self._seq = 0

    def record(self, **kwargs):
        self._seq += 1
        run_id = f"run-{self._seq}"
        audit_context = kwargs.get("audit_context") or {}
        self._conn.execute(
            "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
            "prompt_version, temperature, input_hash, raw_response, created_at) "
            "VALUES (?, ?, ?, 'deepseek-chat', ?, 0, 'hash', ?, datetime('now'))",
            (run_id, audit_context.get("application_id"), audit_context.get("job_id"),
             kwargs["prompt_version"], kwargs["raw_response"]),
        )
        self._conn.commit()
        return run_id


def _score_body(dimension="AUTOSAR CP", turn_id="t1", quote="第一题回答"):
    return json.dumps(
        {
            "dimensions": [
                {"dimension": dimension, "score": 4.0, "rationale": "回答扎实",
                 "evidence": {"turn_id": turn_id, "quote": quote}},
            ],
            "overall_summary": "整体表现良好",
        },
        ensure_ascii=False,
    )


def test_compute_score_builds_score_input_from_frozen_snapshot_dimensions(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="第一题回答")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=_ScriptedClient([_score_body()]), audit_hook=_RecordingHook(conn),
    )

    aligned = compute_align(conn, session_id="sess-1")
    draft = compute_score(conn, session_id="sess-1", aligned_turns=aligned, gateway=gateway)

    assert draft.dimensions[0].dimension == "AUTOSAR CP"
    assert draft.dimensions[0].turn_id == "t1"
    run_row = conn.execute(
        "SELECT application_id, job_id, prompt_version FROM analysis_run WHERE id = ?", (draft.run_id,)
    ).fetchone()
    assert run_row == ("app-1", "job-1", "interview-score-v1")


def test_compute_score_raises_on_unknown_session(conn):
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=_ScriptedClient([]), audit_hook=_RecordingHook(conn),
    )
    with pytest.raises(ValueError, match="interview_session 不存在"):
        compute_score(conn, session_id="no-such-session", aligned_turns=[], gateway=gateway)


from app.agents.interview_scoring import ScoreCardDraft, ScoredDimensionDraft
from app.graph.interview_scoring_nodes import (
    AlignedTurn,
    CorrectedCriterionScore,
    ScoringEvidenceUnusable,
    correct_evidence,
)


def _aligned_turn(turn_id="t1", seq=1, answer_text="做过三年 AUTOSAR CP 分层开发"):
    return AlignedTurn(
        turn_id=turn_id, seq=seq, question_id="q1", question_text="讲讲你的项目",
        answer_text=answer_text, answer_mode="text", asr_confidence=None,
        audio_start_ms=None, audio_end_ms=None, low_confidence=False,
    )


def _draft(dimension="AUTOSAR CP", score=4.0, turn_id="t1", quote="AUTOSAR CP 分层开发"):
    return ScoreCardDraft(
        dimensions=[ScoredDimensionDraft(dimension=dimension, score=score, rationale="r",
                                          turn_id=turn_id, quote=quote)],
        overall_summary="s", dropped_count=0, run_id="run-1", response_model="m",
    )


def test_correct_evidence_locates_exact_offset():
    aligned = [_aligned_turn()]
    corrected = correct_evidence(_draft(), aligned)

    assert len(corrected) == 1
    result = corrected[0]
    assert isinstance(result, CorrectedCriterionScore)
    assert result.dimension == "AUTOSAR CP"
    assert result.quote == "AUTOSAR CP 分层开发"
    answer_text = aligned[0].answer_text
    assert answer_text[result.start:result.end] == "AUTOSAR CP 分层开发"


def test_correct_evidence_raises_when_quote_not_found_in_turn_text():
    aligned = [_aligned_turn(answer_text="完全不相关的回答内容")]
    with pytest.raises(ScoringEvidenceUnusable, match="反查失败"):
        correct_evidence(_draft(), aligned)


def test_correct_evidence_raises_when_turn_id_not_in_session():
    aligned = [_aligned_turn(turn_id="t1")]
    draft = _draft(turn_id="t-not-in-session")
    with pytest.raises(ScoringEvidenceUnusable, match="不属于本场次"):
        correct_evidence(draft, aligned)
