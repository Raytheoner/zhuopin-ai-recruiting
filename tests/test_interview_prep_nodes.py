"""app/graph/interview_prep_nodes.py：L4 编排层（compute_prep 只读组装输入，
effect_* 节点写库）。LLM 用 tests/test_interview_prep_agent.py 同款脚本化
假客户端，数据库用真实 SQLite（tmp_path）。"""

import json
import logging

import pytest

from app.graph.interview_prep_nodes import (
    ProfileNotApprovedError,
    PrepSnapshotNotFrozenError,
    compute_prep,
    effect_delete_prep_question,
    effect_edit_prep_question,
    effect_freeze_prep,
    effect_persist_prep_draft,
    effect_regenerate_prep_question,
    expire_outdated_snapshots,
    next_prep_version,
    verify_prep_frozen,
)
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema


class ScriptedClient:
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


class RecordingHook:
    def __init__(self, conn):
        self._conn = conn
        self._seq = 0

    def record(self, **kwargs):
        self._seq += 1
        run_id = f"run-{self._seq}"
        self._conn.execute(
            "INSERT INTO analysis_run (id, application_id, configured_model, "
            "prompt_version, temperature, input_hash, raw_response, created_at) "
            "VALUES (?, ?, 'deepseek-chat', ?, 0, 'hash', ?, datetime('now'))",
            (run_id, kwargs["audit_context"].get("application_id") if kwargs.get("audit_context") else None,
             kwargs["prompt_version"], kwargs["raw_response"]),
        )
        self._conn.commit()
        return run_id


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_job_and_approved_profile(conn, *, job_id="job-1", version=1, profile=None):
    conn.execute("INSERT INTO job (id, title) VALUES (?, '嵌入式软件工程师')", (job_id,))
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES (?, ?, ?, 'approved', ?)",
        (f"{job_id}-v{version}", job_id, version, json.dumps(profile or {
            "core_skills": [{"name": "AUTOSAR CP", "required": True}],
            "soft_skill_keywords": ["沟通能力"],
        }, ensure_ascii=False)),
    )
    conn.commit()


def _seed_application(conn, *, application_id="app-1", job_id="job-1"):
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')",
        (application_id, job_id),
    )
    conn.commit()


def test_compute_prep_rejects_when_profile_not_approved(conn):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', '嵌入式软件工程师')")
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([]), audit_hook=RecordingHook(conn),
    )

    with pytest.raises(ProfileNotApprovedError):
        compute_prep(conn, application_id="app-1", gateway=gateway)


def _question_body(dimension="AUTOSAR CP", difficulty="easy"):
    return json.dumps(
        {
            "questions": [
                {
                    "dimension": dimension,
                    "difficulty": difficulty,
                    "text": "讲讲你做过的 AUTOSAR 项目",
                    "rubric": "能说清分层架构者得分",
                    "follow_ups": ["具体是哪个 OEM 项目？"],
                    "rationale": "画像要求 AUTOSAR CP 经验",
                }
            ]
        },
        ensure_ascii=False,
    )


def test_compute_prep_and_persist_draft_end_to_end(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )

    draft, profile_version, resume_run_id = compute_prep(
        conn, application_id="app-1", gateway=gateway
    )
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id,
        application_id="app-1", version=version, profile_version=profile_version,
        resume_run_id=resume_run_id, draft=draft,
    )

    row = conn.execute(
        "SELECT status, gen_run_id FROM prep_snapshot WHERE application_id='app-1' AND version=?",
        (version,),
    ).fetchone()
    assert row[0] == "draft"
    assert row[1] == draft.run_id
    questions = conn.execute(
        "SELECT seq, dimension FROM prep_question WHERE snapshot_id = "
        "(SELECT id FROM prep_snapshot WHERE application_id='app-1' AND version=?) ORDER BY seq",
        (version,),
    ).fetchall()
    assert questions == [(1, "AUTOSAR CP")]


def _partial_drop_question_body():
    """2 道题，1 道维度在白名单内（AUTOSAR CP）、1 道越界（越界维度）——
    generate() 应当丢弃越界那道、保留另一道，dropped_count == 1 且不触发
    PrepGenerationFailed（该异常只在全部越界时抛出）。"""
    return json.dumps(
        {
            "questions": [
                {
                    "dimension": "AUTOSAR CP",
                    "difficulty": "easy",
                    "text": "讲讲你做过的 AUTOSAR 项目",
                    "rubric": "能说清分层架构者得分",
                    "follow_ups": ["具体是哪个 OEM 项目？"],
                    "rationale": "画像要求 AUTOSAR CP 经验",
                },
                {
                    "dimension": "越界维度",
                    "difficulty": "easy",
                    "text": "一道维度越界的题",
                    "rubric": "不应保留",
                    "follow_ups": ["追问"],
                    "rationale": "越界",
                },
            ]
        },
        ensure_ascii=False,
    )


def test_compute_prep_logs_warning_on_partial_drop(conn, caplog):
    """fix 2 回归：spec Scenario「维度越界」要求部分丢弃"可观测"。此前
    generate() 算出 dropped_count 后没有任何地方记录，本用例断言 compute_prep
    在部分丢弃、生成仍成功时打一条带丢弃数的 WARNING 日志。"""
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_partial_drop_question_body()]),
        audit_hook=RecordingHook(conn),
    )

    with caplog.at_level(logging.WARNING, logger="app.graph.interview_prep_nodes"):
        draft, _, _ = compute_prep(conn, application_id="app-1", gateway=gateway)

    assert draft.dropped_count == 1
    assert len(draft.questions) == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("1" in r.getMessage() and "丢弃" in r.getMessage() for r in warnings)


def test_effect_persist_prep_draft_is_idempotent(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    kwargs = dict(
        thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )

    effect_persist_prep_draft(conn, **kwargs)
    effect_persist_prep_draft(conn, **kwargs)  # 重放

    count = conn.execute(
        "SELECT COUNT(*) FROM prep_snapshot WHERE application_id='app-1'"
    ).fetchone()[0]
    assert count == 1


def test_freeze_then_verify_prep_frozen_passes(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )

    with pytest.raises(PrepSnapshotNotFrozenError):
        verify_prep_frozen(conn, application_id="app-1", version=version)

    effect_freeze_prep(
        conn, thread_id="app-1", business_key=str(version),
        application_id="app-1", version=version, confirmed_by="hr-1",
    )
    verify_prep_frozen(conn, application_id="app-1", version=version)  # 不抛


def test_expire_outdated_snapshots_marks_old_versions(conn):
    _seed_job_and_approved_profile(conn, version=1)
    _seed_application(conn)
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-0', 'm', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', 'app-1', 1, 1, 'run-0', 'frozen')"
    )
    conn.commit()

    expire_outdated_snapshots(conn, application_id="app-1", current_profile_version=2)
    conn.commit()

    status = conn.execute("SELECT status FROM prep_snapshot WHERE id='snap-1'").fetchone()[0]
    assert status == "expired"


def test_edit_then_regenerate_question_updates_origin(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )
    snapshot_id = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id='app-1' AND version=?", (version,)
    ).fetchone()[0]

    effect_edit_prep_question(
        conn, thread_id="app-1", business_key=f"{version}:1:edit1",
        snapshot_id=snapshot_id, seq=1, text="改过的题面", rubric="改过的 rubric",
    )
    row = conn.execute(
        "SELECT text, origin, ai_text FROM prep_question WHERE snapshot_id=? AND seq=1",
        (snapshot_id,),
    ).fetchone()
    assert row == ("改过的题面", "ai_edited", "讲讲你做过的 AUTOSAR 项目")

    from app.agents.interview_prep import PrepQuestionDraft

    replacement = PrepQuestionDraft(
        dimension="AUTOSAR CP", difficulty="medium", text="新题面",
        rubric="新 rubric", follow_ups=["新追问"], rationale="重生成",
    )
    effect_regenerate_prep_question(
        conn, thread_id="app-1", business_key=f"{version}:1:regen1",
        snapshot_id=snapshot_id, seq=1, question=replacement,
    )
    row = conn.execute(
        "SELECT text, origin, ai_text FROM prep_question WHERE snapshot_id=? AND seq=1",
        (snapshot_id,),
    ).fetchone()
    assert row == ("新题面", "ai", None)


def test_delete_question_removes_row(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )
    snapshot_id = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id='app-1' AND version=?", (version,)
    ).fetchone()[0]

    effect_delete_prep_question(
        conn, thread_id="app-1", business_key=f"{version}:1:delete", snapshot_id=snapshot_id, seq=1
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM prep_question WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()[0]
    assert count == 0
