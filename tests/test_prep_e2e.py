"""prep 出题的完整链路 e2e（voice-structured-interview U2 tasks 3.8）：
合成画像 + M2 合成样本评分 → 生成 → 确认页改一题 → 冻结 → 开场校验通过。

用真实 RecorderAuditHook（不是默认 NoopAuditHook）：prep_snapshot.gen_run_id
是 NOT NULL REFERENCES analysis_run(id)，NoopAuditHook 的占位 id 不对应真实
行，会撞外键失败——这正是本文件要验证的"AI 评分必须持久化"链路本身。
"""
import json
import sqlite3

from fastapi.testclient import TestClient

from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.interview_prep_nodes import PrepSnapshotNotFrozenError, verify_prep_frozen
from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import PrepInput
from app.storage.auth_session import create_session
from app.storage.db import get_connection
from app.storage.hr_account import upsert_account
from app.web.server import create_app
from tests.test_web_api import ScriptedOpenAIClient


def _make_app_with_real_audit(tmp_path, responses):
    """镜像 app/main.py::_gateway_factory() 的生产装配：RecorderAuditHook 用
    专属连接、真实写 analysis_run（不是 NoopAuditHook）。"""
    db_path = str(tmp_path / "web.db")
    audit_conn = get_connection(db_path)
    recorder = AuditRecorder(SqliteSink(audit_conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
    hook = RecorderAuditHook(recorder, audit_conn)
    scripted_client = ScriptedOpenAIClient(responses)

    def gateway_factory():
        return LLMGateway(
            api_key="k", base_url="https://example.com", model="deepseek-chat-241226",
            supports_json_schema=False, client=scripted_client, audit_hook=hook,
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory)
    return TestClient(app), db_path


def _setup_auth(db_path: str, client: TestClient) -> None:
    """`/api/applications/...` 端点在 Task 6 起挂了登录中间件（本文件写作
    依据的计划片段早于该改动，字面代码未带登录态，跑会撞 401）——与
    `tests/test_prep_endpoints.py::_setup_auth` 同一精确前例：造一个 HR 账号 +
    session，把 token 塞进 `hr_session` cookie。"""
    conn = sqlite3.connect(db_path)
    account_id = upsert_account(conn, username="tester", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    conn.close()
    client.cookies.set("hr_session", token)


def _seed_synthetic_resume_score(db_path: str, *, application_id: str, job_id: str):
    """M2 合成样本评分：手工造一条精排 analysis_run + criterion_score +
    resume_text_span，模拟"简历评分已完成"的前置状态（M2 candidate-ranking
    尚未实现，本单元不等它落地）。"""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "raw_text, uploaded_by) VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', "
        "'做过三年 AUTOSAR CP 分层开发', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')",
        (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
        "VALUES ('resume-1', 0, 0, 13, '做过三年 AUTOSAR CP 分层开发')"
    )
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "prompt_version, temperature, input_hash, raw_response) VALUES "
        "('rank-run-1', ?, ?, 'deepseek-chat', 'rank-v1', 0, 'hash', '{}')",
        (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
        "VALUES ('score-1', 'rank-run-1', 'AUTOSAR CP', 0.9, "
        "'{\"span_id\": 0, \"start\": 0, \"end\": 13}')"
    )
    conn.commit()
    conn.close()


def _question_response(dimension="CAN 驱动开发", difficulty="easy"):
    return json.dumps(
        {
            "questions": [
                {
                    "dimension": dimension, "difficulty": difficulty,
                    "text": "讲讲你做过的 AUTOSAR 项目", "rubric": "能说清分层架构者得分",
                    "follow_ups": ["具体是哪个 OEM 项目？"], "rationale": "画像要求 AUTOSAR CP 经验",
                }
            ]
        },
        ensure_ascii=False,
    )


def test_prep_full_lifecycle_generate_edit_freeze_verify(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    client, db_path = _make_app_with_real_audit(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE, _question_response()]
    )
    _setup_auth(db_path, client)
    job_id = client.post("/api/jobs", json={"message": "要个做 ECU 底层的"}).json()["job_id"]
    confirm = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})
    assert confirm.status_code == 200, confirm.text

    _seed_synthetic_resume_score(db_path, application_id="app-1", job_id=job_id)

    generated = client.post("/api/applications/app-1/prep/generate")
    assert generated.status_code == 200, generated.text
    body = generated.json()
    version = body["version"]
    assert body["status"] == "draft"
    assert len(body["questions"]) == 1

    edited = client.patch(
        f"/api/applications/app-1/prep/{version}/questions/1",
        json={"text": "改过的题面", "rubric": "改过的 rubric"},
    )
    assert edited.status_code == 200
    assert edited.json()["questions"][0]["origin"] == "ai_edited"

    conn = sqlite3.connect(db_path)
    with_open_conn = get_connection(db_path)
    try:
        verify_prep_frozen(with_open_conn, application_id="app-1", version=version)
        assert False, "未冻结时 verify_prep_frozen 应该抛异常"
    except PrepSnapshotNotFrozenError:
        pass

    frozen = client.post(f"/api/applications/app-1/prep/{version}/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"

    verify_prep_frozen(with_open_conn, application_id="app-1", version=version)  # 不抛

    # gen_run_id 是真实的 analysis_run.id（不是 NoopAuditHook 占位符）
    snapshot_row = conn.execute(
        "SELECT gen_run_id, resume_run_id FROM prep_snapshot WHERE application_id='app-1'"
    ).fetchone()
    assert conn.execute(
        "SELECT COUNT(*) FROM analysis_run WHERE id = ?", (snapshot_row[0],)
    ).fetchone()[0] == 1
    assert snapshot_row[1] == "rank-run-1"
    conn.close()


def test_prep_input_structurally_excludes_identity_fields():
    """spec「MUST NOT 包含候选人姓名、联系方式等身份字段」+ 合规红线「绝不用
    历史录用结果做监督信号」：PrepInput 的字段白名单是结构性的，反射断言
    没有 name/phone/候选人身份/历史录用结果相关字段（复用
    app/schemas/interview_ai_input.py 模块 docstring 里已声明的既有约束
    ——extra="forbid" 让传错键在构造对象那一刻直接失败）。"""
    forbidden_keys = {"name", "candidate_name", "phone", "phone_number", "hired", "offer_accepted"}
    assert forbidden_keys.isdisjoint(set(PrepInput.model_fields.keys()))

    try:
        PrepInput(
            profile={}, rubric_dimensions=["x"], resume_scores=[],
            candidate_name="张三",  # type: ignore[call-arg]
        )
        assert False, "extra='forbid' 应该拒绝未登记字段"
    except Exception as exc:
        assert "candidate_name" in str(exc) or "extra" in str(exc).lower()
