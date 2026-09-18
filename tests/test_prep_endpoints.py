"""prep 出题确认页的 6 个端点（voice-structured-interview U2 tasks 3.6/3.7）。"""

import json
import sqlite3

import pytest

from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.llm.gateway import LLMGateway
from app.storage.auth_session import create_session
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from tests.test_web_api import ScriptedOpenAIClient
from fastapi.testclient import TestClient
from app.web.server import create_app


def _make_app_with_real_audit(tmp_path, responses):
    """Set up test client with real audit hook (needed for analysis_run FK constraints)."""
    db_path = str(tmp_path / "web.db")
    conn = get_connection(db_path)
    init_schema(conn)

    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    hook = RecorderAuditHook(recorder, conn)

    scripted_client = ScriptedOpenAIClient(responses)
    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=scripted_client,
            audit_hook=hook,
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path="")
    client = TestClient(app)
    return client, scripted_client


def _question_response(dimension="CAN 驱动开发", difficulty="easy"):
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


def _setup_auth(tmp_path, client):
    """Set up authentication for /api/applications endpoints."""
    db_path = str(tmp_path / "web.db")
    conn = sqlite3.connect(db_path)
    account_id = upsert_account(conn, username="tester", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    conn.close()
    client.cookies.set("hr_session", token)


def test_generate_returns_409_when_profile_not_approved(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE

    # COMPLETE_PROFILE_RESPONSE 驱动第一轮问答直接产出完整画像草案，但本用例
    # 故意**不**调 /confirm——job_profile 只有 drafting 版本，没有任何
    # approved 版本，这正是本用例要触发的前置条件。
    client, _ = _make_app_with_real_audit(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    _setup_auth(tmp_path, client)

    job_id = client.post(
        "/api/jobs", json={"message": "要个做 ECU 底层的"}
    ).json()["job_id"]

    # 手工插一条投递，画像还没确认（job_profile 只有 drafting 版本）
    db_path = str(tmp_path / "web.db")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'cand-1', ?, 'resume-1', 'initial')",
        (job_id,),
    )
    conn.commit()
    conn.close()

    resp = client.post("/api/applications/app-1/prep/generate")
    assert resp.status_code == 409


def _seed_application(tmp_path, job_id, application_id="app-1"):
    db_path = str(tmp_path / "web.db")
    conn = sqlite3.connect(db_path)
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
    conn.close()


def _confirmed_job_id(tmp_path, client):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE

    job_id = client.post("/api/jobs", json={"message": "要个做 ECU 底层的"}).json()["job_id"]
    resp = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})
    assert resp.status_code == 200, resp.text
    return job_id


def test_generate_edit_freeze_flow(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    # Need multiple responses: job creation + confirm (JD) + prep generation
    # Plus potential retries or additional calls
    client, _ = _make_app_with_real_audit(
        tmp_path, [
            COMPLETE_PROFILE_RESPONSE,
            JD_RESPONSE,
            _question_response(),
            JD_RESPONSE,  # In case confirm is called again or JD generation retried
            _question_response(),  # In case prep generation retried
        ]
    )
    _setup_auth(tmp_path, client)
    job_id = _confirmed_job_id(tmp_path, client)
    _seed_application(tmp_path, job_id)

    generated = client.post("/api/applications/app-1/prep/generate")
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["status"] == "draft"
    assert len(body["questions"]) == 1
    version = body["version"]

    edited = client.patch(
        f"/api/applications/app-1/prep/{version}/questions/1",
        json={"text": "改过的题面", "rubric": "改过的 rubric"},
    )
    assert edited.status_code == 200
    assert edited.json()["questions"][0]["origin"] == "ai_edited"
    assert edited.json()["questions"][0]["ai_text"] == "讲讲你做过的 AUTOSAR 项目"

    frozen = client.post(f"/api/applications/app-1/prep/{version}/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"
    assert frozen.json()["confirmed_by"]

    # 冻结后不能再改
    blocked = client.patch(
        f"/api/applications/app-1/prep/{version}/questions/1",
        json={"text": "再改一次", "rubric": "r"},
    )
    assert blocked.status_code == 409


def test_generate_twice_returns_existing_version_not_404(tmp_path):
    """fix 1a 回归：重复调用 generate（相同画像、相同简历评分输入）在
    temperature=0 下会得到同一个 draft.run_id，effect_persist_prep_draft
    被 idempotent_effect 短路、不会真的插入第二行。此前的 bug 是端点信任
    调用前预算好的 version 号去查payload，第二次查的 version 从未真正落库，
    404。现在端点改成按 gen_run_id 反查实际落库的 version。"""
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    client, _ = _make_app_with_real_audit(
        tmp_path, [
            COMPLETE_PROFILE_RESPONSE,
            JD_RESPONSE,
            _question_response(),
            _question_response(),
        ]
    )
    _setup_auth(tmp_path, client)
    job_id = _confirmed_job_id(tmp_path, client)
    _seed_application(tmp_path, job_id)

    first = client.post("/api/applications/app-1/prep/generate")
    assert first.status_code == 200, first.text
    first_version = first.json()["version"]

    second = client.post("/api/applications/app-1/prep/generate")
    assert second.status_code == 200, second.text
    assert second.json()["version"] == first_version


def test_delete_question_via_endpoint(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    client, _ = _make_app_with_real_audit(
        tmp_path, [
            COMPLETE_PROFILE_RESPONSE,
            JD_RESPONSE,
            _question_response(),
            JD_RESPONSE,
            _question_response(),
        ]
    )
    _setup_auth(tmp_path, client)
    job_id = _confirmed_job_id(tmp_path, client)
    _seed_application(tmp_path, job_id)
    version = client.post("/api/applications/app-1/prep/generate").json()["version"]

    deleted = client.delete(f"/api/applications/app-1/prep/{version}/questions/1")
    assert deleted.status_code == 200
    assert deleted.json()["questions"] == []


def test_regenerate_question_via_endpoint(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    replacement = _question_response(dimension="CAN 驱动开发", difficulty="hard")
    replacement = json.dumps(
        {"question": json.loads(replacement)["questions"][0]}, ensure_ascii=False
    )
    client, _ = _make_app_with_real_audit(
        tmp_path,
        [
            COMPLETE_PROFILE_RESPONSE,
            JD_RESPONSE,
            _question_response(),
            replacement,
            JD_RESPONSE,
            _question_response(),
            replacement,
        ],
    )
    _setup_auth(tmp_path, client)
    job_id = _confirmed_job_id(tmp_path, client)
    _seed_application(tmp_path, job_id)
    version = client.post("/api/applications/app-1/prep/generate").json()["version"]

    regenerated = client.post(
        f"/api/applications/app-1/prep/{version}/questions/1/regenerate"
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["questions"][0]["difficulty"] == "hard"
    assert regenerated.json()["questions"][0]["origin"] == "ai"


def test_regenerate_twice_applies_both_times(tmp_path):
    """fix 1b 回归：重生成用 f"{version}:{seq}:regen:{run_id}" 做 business_key
    时，两次点击若命中相同 input_hash（同一维度/难度、temperature=0）会得到
    相同的确定性 run_id，第二次点击被 idempotent_effect 短路成静默无操作——
    真调用了 API、DB 却没变。business_key 改用 uuid4 后，两次点击必须都
    真的落库，用两个不同的脚本化替换题目断言第二次点击生效、且不是第一次
    点击内容的重复。"""
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    first_replacement = _question_response(dimension="CAN 驱动开发", difficulty="medium")
    first_replacement = json.dumps(
        {"question": json.loads(first_replacement)["questions"][0]}, ensure_ascii=False
    )
    second_replacement = _question_response(dimension="CAN 驱动开发", difficulty="hard")
    second_replacement = json.dumps(
        {"question": json.loads(second_replacement)["questions"][0]}, ensure_ascii=False
    )
    client, _ = _make_app_with_real_audit(
        tmp_path,
        [
            COMPLETE_PROFILE_RESPONSE,
            JD_RESPONSE,
            _question_response(),
            first_replacement,
            second_replacement,
        ],
    )
    _setup_auth(tmp_path, client)
    job_id = _confirmed_job_id(tmp_path, client)
    _seed_application(tmp_path, job_id)
    version = client.post("/api/applications/app-1/prep/generate").json()["version"]

    first_click = client.post(
        f"/api/applications/app-1/prep/{version}/questions/1/regenerate"
    )
    assert first_click.status_code == 200, first_click.text
    assert first_click.json()["questions"][0]["difficulty"] == "medium"

    second_click = client.post(
        f"/api/applications/app-1/prep/{version}/questions/1/regenerate"
    )
    assert second_click.status_code == 200, second_click.text
    assert second_click.json()["questions"][0]["difficulty"] == "hard"
