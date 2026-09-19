"""U5 post 评分完整链路 e2e（voice-structured-interview tasks 6.8）：用 U3
签发的内部模拟场次以文本作答走完 10 题 → post 评分 → 每维有 turn 回指且可
定位 → rejection_record 无新增、application 阶段不变。

⚠️ 候选人文本作答提交端点是 U4 第 5 章 5.7/5.8 范围，晚于本单元交付。本文件
直接用 sqlite3 INSERT interview_turn 行并 UPDATE interview_session.status=
'completed'，是绕过尚未交付端点的**测试专用 fixture**，不代表生产可以这样
写 completed（见 docs/superpowers/plans/2026-09-19-u5-post-scoring.md「设计
决策 6」）。

用真实 RecorderAuditHook（不是脚本化假 hook）：criterion_score.analysis_run_id
是 NOT NULL REFERENCES analysis_run(id)，本测试要验证的正是"AI 评分必须
持久化"这条链路本身，镜像 tests/test_prep_e2e.py 的装配方式。
"""
import json

from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.interview_scoring_nodes import run_post_scoring
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from tests.test_web_api import ScriptedOpenAIClient

RUBRIC_DIMENSIONS = ["AUTOSAR CP", "沟通表达"]

QUESTIONS = [
    ("AUTOSAR CP", "讲讲你做过的 AUTOSAR 项目"),
    ("沟通表达", "怎么跟团队同步进度"),
] * 5  # 10 题，两个维度交替


def _make_conn_with_real_audit(tmp_path):
    db_path = str(tmp_path / "post_scoring.db")
    conn = get_connection(db_path)
    init_schema(conn)
    audit_conn = get_connection(db_path)
    recorder = AuditRecorder(SqliteSink(audit_conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
    hook = RecorderAuditHook(recorder, audit_conn)
    return conn, hook


def _seed_frozen_snapshot_with_ten_questions(conn, *, application_id="app-1", job_id="job-1"):
    conn.execute("INSERT INTO job (id, title) VALUES (?, 'ECU 工程师')", (job_id,))
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
    for seq, (dimension, text) in enumerate(QUESTIONS, start=1):
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
            "rubric_json, follow_ups_json, rationale) VALUES (?, 'snap-1', ?, ?, 'easy', ?, '{}', '[]', 'r')",
            (f"q{seq}", seq, dimension, text),
        )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES ('sess-1', ?, 1, '2099-01-01', 'v1', 'internal_sim', 'in_progress')",
        (application_id,),
    )
    conn.commit()
    return application_id


def _complete_session_with_text_answers(conn, *, session_id="sess-1"):
    """测试专用 fixture：直接写 10 条文本作答 turn 并把场次标记 completed，
    绕过尚未交付的候选人提交端点（见文件顶部说明）。"""
    for seq in range(1, 11):
        answer_text = f"针对第 {seq} 题，我做过三年 AUTOSAR CP 分层开发经验，也擅长跨团队同步进度"
        conn.execute(
            "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, "
            "answer_text, answer_mode) VALUES (?, ?, ?, ?, ?, ?, 'text')",
            (f"turn-{seq}", session_id, seq, f"q{seq}", QUESTIONS[seq - 1][1], answer_text),
        )
    conn.execute("UPDATE interview_session SET status = 'completed' WHERE id = ?", (session_id,))
    conn.commit()


def _score_response_body():
    dimensions = []
    for seq in range(1, 11):
        dimension, _ = QUESTIONS[seq - 1]
        dimensions.append({
            "dimension": dimension, "score": 4.0, "rationale": "回答扎实",
            "evidence": {"turn_id": f"turn-{seq}",
                         "quote": f"针对第 {seq} 题，我做过三年 AUTOSAR CP 分层开发经验，也擅长跨团队同步进度"},
        })
    # 每个维度只需要一条评分项（interview-scorecard spec「一个场次的评分」：
    # 每个 rubric 维度各有一条评分项），只取每个维度第一次出现的那条。
    seen = set()
    deduped = []
    for d in dimensions:
        if d["dimension"] not in seen:
            seen.add(d["dimension"])
            deduped.append(d)
    return json.dumps({"dimensions": deduped, "overall_summary": "整体表现良好，AUTOSAR 与沟通均达标"},
                       ensure_ascii=False)


def test_post_scoring_e2e_with_text_answer_session(tmp_path):
    conn, audit_hook = _make_conn_with_real_audit(tmp_path)
    application_id = _seed_frozen_snapshot_with_ten_questions(conn)
    _complete_session_with_text_answers(conn)

    scripted_client = ScriptedOpenAIClient([_score_response_body()])
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat-241226",
        supports_json_schema=False, client=scripted_client, audit_hook=audit_hook,
    )

    rejection_count_before = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    stage_before = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]

    result = run_post_scoring(conn, session_id="sess-1", gateway=gateway)

    assert result == "scored"

    for dimension in RUBRIC_DIMENSIONS:
        row = conn.execute(
            "SELECT cs.evidence_ref FROM criterion_score cs "
            "JOIN analysis_run ar ON ar.id = cs.analysis_run_id "
            "WHERE ar.prompt_version = 'interview-score-v1' AND cs.criterion_key = ?",
            (dimension,),
        ).fetchone()
        assert row is not None, f"维度 {dimension} 没有评分项"
        ref = json.loads(row[0])
        assert ref["type"] == "interview_turn"
        turn_row = conn.execute(
            "SELECT answer_text FROM interview_turn WHERE id = ?", (ref["id"],)
        ).fetchone()
        assert turn_row is not None
        assert turn_row[0][ref["start"]:ref["end"]] == ref["quote"]

    scorecard_row = conn.execute(
        "SELECT summary FROM interview_scorecard WHERE session_id = 'sess-1'"
    ).fetchone()
    assert scorecard_row is not None
    assert scorecard_row[0]

    rejection_count_after = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    stage_after = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    assert rejection_count_after == rejection_count_before == 0
    assert stage_after == stage_before

    session_status = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()[0]
    assert session_status == "scored"
