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
