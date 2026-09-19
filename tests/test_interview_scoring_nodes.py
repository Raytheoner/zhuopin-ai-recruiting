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


from app.graph.interview_scoring_nodes import (
    TalkingPoint,
    compute_acoustic_ref,
    derive_talking_points,
    effect_write_acoustic_refs,
)


def test_derive_talking_points_flags_low_score_dimension():
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=1.5, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    aligned = [_aligned_turn(turn_id="t1")]

    tips = derive_talking_points(corrected, aligned, low_score_threshold=2.0)

    assert len(tips) == 1
    assert tips[0].dimension == "AUTOSAR CP"
    assert tips[0].turn_id == "t1"
    assert "得分偏低" in tips[0].tip_text


def test_derive_talking_points_ignores_dimension_above_threshold():
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    aligned = [_aligned_turn(turn_id="t1")]

    tips = derive_talking_points(corrected, aligned, low_score_threshold=2.0)

    assert tips == []


def test_derive_talking_points_flags_low_confidence_voice_turn():
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    low_conf_turn = AlignedTurn(
        turn_id="t2", seq=2, question_id="q2", question_text="沟通方式",
        answer_text="转写内容", answer_mode="voice", asr_confidence=0.3,
        audio_start_ms=0, audio_end_ms=5000, low_confidence=True,
    )
    aligned = [_aligned_turn(turn_id="t1"), low_conf_turn]

    tips = derive_talking_points(corrected, aligned, low_score_threshold=2.0)

    assert any(t.turn_id == "t2" and "置信度低" in t.tip_text for t in tips)


def test_derive_talking_points_dedupes_same_dimension_and_turn():
    low_score_low_conf_turn = AlignedTurn(
        turn_id="t1", seq=1, question_id="q1", question_text="讲讲你的项目",
        answer_text="做过三年", answer_mode="voice", asr_confidence=0.3,
        audio_start_ms=0, audio_end_ms=5000, low_confidence=True,
    )
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=1.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]

    tips = derive_talking_points(corrected, [low_score_low_conf_turn], low_score_threshold=2.0)

    assert len(tips) == 1  # 同一 (dimension, turn_id) 只保留一条，低分文案优先
    assert "得分偏低" in tips[0].tip_text


def test_compute_acoustic_ref_returns_none_for_text_answer():
    assert compute_acoustic_ref(answer_text="文本作答", audio_start_ms=None, audio_end_ms=None) is None


def test_compute_acoustic_ref_computes_speech_rate_and_pause_ratio():
    ref_json = compute_acoustic_ref(answer_text="做过三年 AUTOSAR CP 分层开发", audio_start_ms=0, audio_end_ms=10000)
    assert ref_json is not None
    ref = json.loads(ref_json)
    assert "speech_rate_cpm" in ref
    assert "pause_ratio" in ref
    assert ref["pause_ratio"] == ref["silence_ratio"]
    assert 0.0 <= ref["pause_ratio"] <= 1.0
    assert "note" in ref


def test_effect_write_acoustic_refs_writes_only_voice_turns_with_audio(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
                 answer_text="语音作答内容", audio_start_ms=0, audio_end_ms=8000)
    _insert_turn(conn, turn_id="t2", session_id="sess-1", seq=2, answer_mode="text", answer_text="文本作答")

    aligned = compute_align(conn, session_id="sess-1")
    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )

    row1 = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't1'").fetchone()
    row2 = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't2'").fetchone()
    assert row1[0] is not None
    assert row2[0] is None


def test_effect_write_acoustic_refs_is_idempotent(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
                 answer_text="语音作答内容", audio_start_ms=0, audio_end_ms=8000)
    aligned = compute_align(conn, session_id="sess-1")

    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )
    log_count_1 = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]

    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )
    log_count_2 = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]

    assert log_count_1 == log_count_2 == 1  # 第二次调用命中幂等键，effect_log 不再增加


def test_effect_write_acoustic_refs_is_idempotent_with_multiple_turns(conn):
    """幂等性测试覆盖批量场景（N > 1 voice turns）。验证工程铁律：一个 effect_*
    节点的 effect_log 条数恒等于其业务表行数按 thread（本例：1 effect_log row
    覆盖 2 interview_turn 写）。"""
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
                 answer_text="第一个语音回答", audio_start_ms=0, audio_end_ms=5000)
    _insert_turn(conn, turn_id="t2", session_id="sess-1", seq=2, answer_mode="voice",
                 answer_text="第二个语音回答", audio_start_ms=5000, audio_end_ms=12000)
    aligned = compute_align(conn, session_id="sess-1")

    # 第一次调用：N=2 的 voice turn，应生成 1 条 effect_log 并写入 2 条 acoustic_ref
    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )
    log_count_1 = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]
    ref1_first = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't1'").fetchone()[0]
    ref2_first = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't2'").fetchone()[0]

    assert log_count_1 == 1  # 一个 effect_log 条目
    assert ref1_first is not None  # 两个 turn 都写入了 acoustic_ref
    assert ref2_first is not None

    # 第二次调用：命中幂等键，effect_log 不增加，业务行也不变
    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )
    log_count_2 = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]
    ref1_second = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't1'").fetchone()[0]
    ref2_second = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't2'").fetchone()[0]

    assert log_count_2 == 1  # effect_log 不增加（幂等性）
    assert ref1_second == ref1_first  # 业务行内容不变
    assert ref2_second == ref2_first


import uuid

from app.audit.evidence_ref import parse_evidence_ref
from app.graph.interview_scoring_nodes import (
    effect_mark_scoring_failed,
    effect_persist_scorecard,
    run_post_scoring,
)


def test_effect_persist_scorecard_writes_criterion_score_and_scorecard(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="做过三年 AUTOSAR CP")
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "prompt_version, temperature, input_hash, raw_response) VALUES "
        "('score-run-1', 'app-1', 'job-1', 'deepseek-chat', 'interview-score-v1', 0, 'h', '{}')"
    )
    conn.commit()
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    tips = [TalkingPoint(dimension="AUTOSAR CP", turn_id="t1", tip_text="建议追问")]

    effect_persist_scorecard(
        conn, thread_id="sess-1:post", business_key="score-run-1", session_id="sess-1",
        corrected_scores=corrected, overall_summary="整体表现良好", talking_points=tips,
        analysis_run_id="score-run-1",
    )

    cs_row = conn.execute(
        "SELECT criterion_key, score, evidence_ref FROM criterion_score WHERE analysis_run_id = 'score-run-1'"
    ).fetchone()
    assert cs_row[0] == "AUTOSAR CP"
    ref = parse_evidence_ref(cs_row[2])
    assert ref.id == "t1"

    sc_row = conn.execute(
        "SELECT summary FROM interview_scorecard WHERE session_id = 'sess-1'"
    ).fetchone()
    assert sc_row[0] == "整体表现良好"

    tip_row = conn.execute(
        "SELECT tip_text FROM interview_scorecard_tip"
    ).fetchone()
    assert tip_row[0] == "建议追问"

    session_row = conn.execute(
        "SELECT post_scoring_status, post_scored_at FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()
    assert session_row[0] == "scored"
    assert session_row[1] is not None


def test_effect_persist_scorecard_rejects_dangling_evidence_ref(conn):
    _seed_job_application_session(conn)
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "prompt_version, temperature, input_hash, raw_response) VALUES "
        "('score-run-2', 'app-1', 'job-1', 'deepseek-chat', 'interview-score-v1', 0, 'h', '{}')"
    )
    conn.commit()
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="no-such-turn",
                                          start=0, end=5, quote="做过三年")]

    with pytest.raises(ValueError, match="interview_turn 不存在"):
        effect_persist_scorecard(
            conn, thread_id="sess-1:post", business_key="score-run-2", session_id="sess-1",
            corrected_scores=corrected, overall_summary="s", talking_points=[],
            analysis_run_id="score-run-2",
        )
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM interview_scorecard").fetchone()[0] == 0


def test_effect_mark_scoring_failed_sets_failed_retry_status(conn):
    _seed_job_application_session(conn)

    effect_mark_scoring_failed(
        conn, thread_id="sess-1:post", business_key=str(uuid.uuid4()),
        session_id="sess-1", reason="维度证据反查失败",
    )

    row = conn.execute("SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'").fetchone()
    assert row[0] == "failed_retry"


def test_run_post_scoring_succeeds_and_marks_scored(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="做过三年 AUTOSAR CP 分层开发")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False,
        client=_ScriptedClient([_score_body(quote="做过三年 AUTOSAR CP 分层开发")]),
        audit_hook=_RecordingHook(conn),
    )

    result = run_post_scoring(conn, session_id="sess-1", gateway=gateway)

    assert result == "scored"
    status = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()[0]
    assert status == "scored"
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 1


def test_run_post_scoring_marks_failed_retry_when_evidence_unusable(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="完全不相关的内容")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False,
        client=_ScriptedClient([_score_body(quote="AUTOSAR CP 分层开发")]),  # quote 在 turn 原文里反查不到
        audit_hook=_RecordingHook(conn),
    )

    result = run_post_scoring(conn, session_id="sess-1", gateway=gateway)

    assert result == "failed_retry"
    status = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()[0]
    assert status == "failed_retry"
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 0


def test_run_post_scoring_is_idempotent_and_does_not_rescore_after_success(conn):
    """run_post_scoring 自身必须是幂等的，不能只依赖批处理脚本的 WHERE 过滤：
    interview_scorecard 有 UNIQUE(session_id)，对已 scored 的场次重复调用若
    不做短路会撞唯一键报错，必须在函数入口检查状态并直接返回 "scored"，
    ⛔ 不产出第二条 criterion_score。"""
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="做过三年 AUTOSAR CP 分层开发")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False,
        client=_ScriptedClient([_score_body(quote="做过三年 AUTOSAR CP 分层开发")]),
        audit_hook=_RecordingHook(conn),
    )

    first = run_post_scoring(conn, session_id="sess-1", gateway=gateway)
    assert first == "scored"

    # 第二次调用不应该再消费 gateway 的脚本化响应（只喂了一条）——如果实现
    # 没有在入口短路，这里会因为 ScriptedClient 的响应列表耗尽而报 IndexError，
    # 而不是命中 UNIQUE 约束，两种失败都足以说明幂等短路没生效。
    second = run_post_scoring(conn, session_id="sess-1", gateway=gateway)
    assert second == "scored"
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 1
