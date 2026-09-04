import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.graph.nodes import DECISION_REVISION_REQUESTED, MAX_REVISIONS
from app.llm.gateway import LLMGateway
from app.web.server import create_app


class _NeverCalledCompletions:
    """三个视图端点全是只读 GET。任何一次模型调用都是 bug，当场戳穿。"""

    def create(self, **kwargs):
        raise AssertionError("只读视图端点不得触发任何 LLM 调用")


class _NeverCalledChat:
    def __init__(self):
        self.completions = _NeverCalledCompletions()


class _NeverCalledClient:
    def __init__(self):
        self.chat = _NeverCalledChat()


def _make_app(tmp_path, root_path: str = ""):
    """建 app 并额外开一条**独立连接**直接写测试数据。

    直接写库而不是走 POST /api/jobs 跑真实链路：三个端点都是只读的，用真实
    链路造数据要脚本化好几轮 LLM 响应，而那些响应内容与本单元要断言的东西
    毫无关系——测试会变成在测别人的代码。库文件同一份，WAL 模式下两条连接
    并存是既有做法（app/storage/db.py 的 get_connection 已开 WAL）。
    """
    db_path = str(tmp_path / "views.db")

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=_NeverCalledClient(),
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path=root_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return TestClient(app), conn


def _seed_job(conn, job_id, *, status="drafting", created_at="2026-09-01 10:00:00"):
    conn.execute(
        "INSERT INTO job (id, title, status, created_at) VALUES (?, '待确定', ?, ?)",
        (job_id, status, created_at),
    )
    conn.commit()


def _seed_version(
    conn,
    job_id,
    version,
    profile,
    *,
    status="drafting",
    created_at="2026-09-01 10:01:00",
    derived=(),
    asked=(),
    written=(),
    ungrounded=(),
    model="deepseek-chat-241226",
    latency=1500.0,
    productive=1,
):
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, "
        "unspecified_fields, derived_unspecified_fields, created_at, is_productive, "
        "turn_started_at, llm_latency_ms, ungrounded_fields, written_fields, "
        "llm_response_model, asked_questions) "
        "VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-v{version}",
            job_id,
            version,
            status,
            json.dumps(profile, ensure_ascii=False),
            json.dumps(list(derived), ensure_ascii=False),
            created_at,
            productive,
            "2026-09-01 10:00:55",
            latency,
            json.dumps(list(ungrounded), ensure_ascii=False),
            json.dumps(list(written), ensure_ascii=False),
            model,
            json.dumps(list(asked), ensure_ascii=False),
        ),
    )
    conn.commit()


def _seed_review(
    conn, job_id, version, decision_type, *, reviewer="unknown:web-session",
    feedback=None, decided_at="2026-09-01 11:00:00",
):
    conn.execute(
        "INSERT INTO human_review (id, job_id, profile_version, decision_type, reviewer, "
        "feedback, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-{version}-{decision_type}",
            job_id,
            version,
            decision_type,
            reviewer,
            feedback,
            decided_at,
        ),
    )
    conn.commit()


def _seed_outbox(conn, job_id, message_type):
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, ?, '{}')",
        (job_id, message_type),
    )
    conn.commit()


# ── 8.1 岗位列表 ─────────────────────────────────────────────────────────────


def test_list_jobs_returns_empty_list_when_there_is_nothing(tmp_path):
    client, _ = _make_app(tmp_path)

    resp = client.get("/api/jobs")

    assert resp.status_code == 200
    assert resp.json() == {"jobs": []}


def test_list_jobs_uses_profile_job_title_not_the_placeholder(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "嵌入式软件工程师"})

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["title"] == "嵌入式软件工程师"
    assert job["title"] != "待确定"


def test_list_jobs_shows_chinese_stage_label_never_english_status(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_outbox(conn, "j1", "confirmation_prompt")

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["stage_label"] == "等你确认"
    assert "drafting" not in job["stage_label"]


def test_list_jobs_sorts_most_recently_active_first(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "old", created_at="2026-09-01 08:00:00")
    _seed_version(conn, "old", 1, {"job_title": "旧"}, created_at="2026-09-01 08:01:00")
    _seed_job(conn, "new", created_at="2026-09-01 09:00:00")
    _seed_version(conn, "new", 1, {"job_title": "新"}, created_at="2026-09-03 15:00:00")

    ids = [job["job_id"] for job in client.get("/api/jobs").json()["jobs"]]

    assert ids == ["new", "old"]


def test_list_jobs_includes_a_job_with_no_profile_yet(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "empty")

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["job_id"] == "empty"
    assert job["latest_version"] is None
    assert job["stage_label"] == "刚发起，还没有画像"


def test_list_jobs_reports_revision_count_and_jd_state(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 2, {"job_title": "A", "_jd_text": "岗位职责…"}, status="approved")
    _seed_review(conn, "j1", 1, DECISION_REVISION_REQUESTED)

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["revision_count"] == 1
    assert job["jd"] == {"generated": True, "needs_manual": False, "human_written": False}
    assert job["stage_label"] == "已确认 · JD 已生成"


def test_list_jobs_flags_needs_manual_with_a_readable_reason(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["needs_manual"] is True
    assert len(job["needs_manual_reasons"]) == 1
    assert "歧视" in job["needs_manual_reasons"][0]["label"]


def test_list_jobs_does_not_flag_a_healthy_job(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["needs_manual"] is False
    assert job["needs_manual_reasons"] == []


def test_list_jobs_never_leaks_jd_text_into_the_list_payload(tmp_path):
    """列表是概览，JD 正文有专门的展示位（GET /api/jobs/{id}/jd）。
    把正文塞进列表会让每一行都背着一大段文案，也多一个必须自己保证 AI 标识
    不被裁掉的地方（Global Constraints 第 5 条）。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "A", "_jd_text": "这是一整段 JD 正文"}, status="approved")

    body = client.get("/api/jobs").text

    assert "这是一整段 JD 正文" not in body


def test_list_jobs_is_mounted_under_the_configured_root_path(tmp_path):
    """部署约束 1：挂到任意子路径下都要能工作，不带前缀的路径必须 404。"""
    client, conn = _make_app(tmp_path, root_path="/hr/recruit-agent")
    _seed_job(conn, "j1")

    assert client.get("/hr/recruit-agent/api/jobs").status_code == 200
    assert client.get("/api/jobs").status_code == 404


def test_list_jobs_does_not_write_anything(tmp_path):
    """只读端点的机器判据：调用前后 job / job_profile / human_review /
    effect_log / outbox 的行数逐表恒等。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_outbox(conn, "j1", "question")

    tables = ("job", "job_profile", "human_review", "effect_log", "outbox")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    client.get("/api/jobs")
    client.get("/api/jobs")

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after


# ── 8.2 画像详情（版本历史 + 生成快照）─────────────────────────────────────


def test_profile_detail_404_for_unknown_job(tmp_path):
    client, _ = _make_app(tmp_path)

    assert client.get("/api/jobs/nope/profile").status_code == 404


def test_profile_detail_returns_every_version_in_order(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "嵌入式工程师"}, created_at="2026-09-01 10:01:00")
    _seed_version(
        conn, "j1", 2, {"job_title": "嵌入式软件工程师"},
        status="approved", created_at="2026-09-01 10:20:00",
    )

    data = client.get("/api/jobs/j1/profile").json()

    assert data["latest_version"] == 2
    assert [v["version"] for v in data["versions"]] == [1, 2]
    assert data["title"] == "嵌入式软件工程师"
    assert data["versions"][1]["status_label"] == "已确认"


def test_profile_detail_version_carries_a_generation_snapshot(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(
        conn, "j1", 1, {"job_title": "A"},
        model="deepseek-chat-241226", latency=2100.0,
        written=["job_title"], ungrounded=["mcu_family"],
        asked=[{"question_id": "q1"}, {"question_id": "q2"}, {"question_id": "q3"}],
    )

    version = client.get("/api/jobs/j1/profile").json()["versions"][0]

    snapshot = version["snapshot"]
    # 工程铁律 5：模型标识必须是 API 响应实际返回的那个，不是配置里写的。
    assert snapshot["llm_response_model"] == "deepseek-chat-241226"
    assert snapshot["llm_latency_ms"] == 2100.0
    assert snapshot["written_fields"] == ["job_title"]
    assert snapshot["ungrounded_fields"] == ["mcu_family"]
    assert version["asked_question_count"] == 3


def test_profile_detail_shows_gaps_in_chinese_only(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"}, derived=["experience_years"])

    version = client.get("/api/jobs/j1/profile").json()["versions"][0]

    assert version["unspecified_fields"] == ["experience_years"]
    assert version["unspecified_field_labels"]
    assert "experience_years" not in version["unspecified_field_labels"][0]


def test_profile_detail_does_not_render_jd_text(tmp_path):
    """Global Constraints 第 5 条：详情页只给 JD 状态徽标，不给正文。
    多一个渲染正文的地方就多一个会漏掉 AI 生成标识的地方。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1,
        {"job_title": "A", "_jd_text": "这是一整段 JD 正文", "_jd_needs_manual": False},
        status="approved",
    )

    body = client.get("/api/jobs/j1/profile").text

    assert "这是一整段 JD 正文" not in body
    assert client.get("/api/jobs/j1/profile").json()["versions"][0]["jd"]["generated"] is True


def test_profile_detail_lists_human_decisions_in_chinese(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_review(conn, "j1", 1, DECISION_REVISION_REQUESTED,
                 feedback="人数改成 3 个", decided_at="2026-09-01 11:00:00")
    _seed_review(conn, "j1", 2, "approved", decided_at="2026-09-01 12:00:00")

    decisions = client.get("/api/jobs/j1/profile").json()["decisions"]

    assert [d["decision_label"] for d in decisions] == ["要求修改", "确认"]
    assert decisions[0]["feedback"] == "人数改成 3 个"
    assert decisions[0]["reviewer"] == "unknown:web-session"


def test_profile_detail_states_the_snapshot_boundary_honestly(tmp_path):
    """analysis_run.job_id 恒为 NULL（没有调用点传 audit_context），按岗位查不出来。
    ⛔ 不许静默留白：留白会让人以为"这就是全部留痕"。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    note = client.get("/api/jobs/j1/profile").json()["snapshot_note"]

    assert "analysis_run" in note
    # ⛔ 指向**既有的** TD-1（它的「怎么还」第 ① 步就是接 audit_context），
    # 不新开一条 TD——同一个事实两个真源，两边迟早写得不一样而没有症状。
    assert "TD-1" in note


def test_profile_detail_for_a_job_without_versions(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "empty")

    data = client.get("/api/jobs/empty/profile").json()

    assert data["versions"] == []
    assert data["decisions"] == []
    assert data["latest_version"] is None
    assert data["stage_label"] == "刚发起，还没有画像"


def test_profile_detail_is_mounted_under_the_configured_root_path(tmp_path):
    client, conn = _make_app(tmp_path, root_path="/hr/recruit-agent")
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    assert client.get("/hr/recruit-agent/api/jobs/j1/profile").status_code == 200
    assert client.get("/api/jobs/j1/profile").status_code == 404


def test_profile_detail_does_not_shadow_the_existing_single_job_endpoint(tmp_path):
    """回归：新增 /api/jobs/{id}/profile 之后，既有的 GET /api/jobs/{id} 必须照旧。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_outbox(conn, "j1", "question")

    existing = client.get("/api/jobs/j1").json()

    assert existing["job_id"] == "j1"
    assert existing["status"] == "drafting"
    assert existing["message"]["type"] == "question"


def test_profile_detail_does_not_write_anything(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    tables = ("job", "job_profile", "human_review", "effect_log", "outbox")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    client.get("/api/jobs/j1/profile")
    client.get("/api/jobs/j1/profile")

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after


def test_profile_detail_version_count_equals_job_profile_row_count_after_revisions(tmp_path):
    """控制器追加：本交付单元被考核的不变式——详情页返回的版本数必须等于
    job_profile 表里这个岗位实际的行数，一条不多一条不少；且每一版都要带
    完整的六键快照，不能只有第一版有、后面几版被漏填。

    ⚠️ 局限：这里用 _seed_version / _seed_review 手工造一段"改过 3 次"的历史，
    不是让真实的 revise() 端到端跑一遍——真跑 revise() 要真的模型调用，而本
    文件的 _NeverCalledClient 就是设计成不许任何 LLM 调用发生。这条测试验证
    的是"查询与展示层对已落库的修改历史诚实"，不是"revise() 本身产出正确"。
    """
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "A"}, created_at="2026-09-01 10:01:00")
    _seed_review(conn, "j1", 1, DECISION_REVISION_REQUESTED,
                 feedback="人数改成 3 个", decided_at="2026-09-01 10:05:00")
    _seed_version(conn, "j1", 2, {"job_title": "A", "headcount": 3}, created_at="2026-09-01 10:10:00")
    _seed_review(conn, "j1", 2, DECISION_REVISION_REQUESTED,
                 feedback="学历改成本科", decided_at="2026-09-01 10:15:00")
    _seed_version(
        conn, "j1", 3, {"job_title": "A", "headcount": 3, "education_requirement": "本科"},
        status="approved", created_at="2026-09-01 10:20:00",
    )

    data = client.get("/api/jobs/j1/profile").json()

    expected_count = conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id = ?", ("j1",)
    ).fetchone()[0]
    assert len(data["versions"]) == expected_count

    snapshot_keys = {
        "llm_response_model", "llm_latency_ms", "turn_started_at",
        "completed_at", "ungrounded_fields", "written_fields",
    }
    for version in data["versions"]:
        assert snapshot_keys <= version["snapshot"].keys()
