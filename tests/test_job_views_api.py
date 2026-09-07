import ast
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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


_WRITE_GUARD_TABLES = ("job", "job_profile", "human_review", "effect_log", "outbox")


def _snapshot_tables(conn, tables=_WRITE_GUARD_TABLES):
    """只读端点的机器判据：内容快照而不是行数快照（final review I-4）。

    `SELECT COUNT(*)` 前后相等挡得住 INSERT/DELETE，但**挡不住 UPDATE**——
    UPDATE 不改变行数。这里改成对每张表取 `SELECT * ORDER BY rowid` 的全量
    元组，请求前后逐字比对：任何一个字段被悄悄改写，快照就会不相等。
    """
    return {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        for table in tables
    }


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


def test_list_jobs_created_at_label_is_shanghai_time_not_raw_utc(tmp_path):
    """final review I-1：`created_at` 落库时是 SQLite `datetime('now')`
    （UTC、无时区后缀）。直接把它上屏，无锡的人会看到早 8 小时的时间。
    `created_at_label` / `updated_at_label` 必须是转换后的东八区文案，⛔ 不
    写死一个魔数字符串——期望值由裸 UTC 值现算，通用换算才是这条测试要
    钉住的事实。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", created_at="2026-09-01 10:00:00")
    _seed_version(conn, "j1", 1, {"job_title": "A"}, created_at="2026-09-01 10:05:00")

    job = client.get("/api/jobs").json()["jobs"][0]

    def shanghai(utc: str) -> str:
        return (
            datetime.strptime(utc, "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
        ).strftime("%Y-%m-%d %H:%M:%S")

    assert job["created_at_label"] == shanghai("2026-09-01 10:00:00")
    assert job["updated_at_label"] == shanghai("2026-09-01 10:05:00")
    # 裸值原样保留给逻辑用（"裸值给逻辑、label 给显示"）。
    assert job["created_at"] == "2026-09-01 10:00:00"
    assert job["updated_at"] == "2026-09-01 10:05:00"


def test_profile_detail_created_at_label_is_shanghai_time(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", created_at="2026-09-01 10:00:00")
    _seed_version(conn, "j1", 1, {"job_title": "A"}, created_at="2026-09-01 10:00:30")

    data = client.get("/api/jobs/j1/profile").json()

    expected = (
        datetime.strptime("2026-09-01 10:00:00", "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
    ).strftime("%Y-%m-%d %H:%M:%S")
    assert data["created_at_label"] == expected
    assert data["versions"][0]["created_at_label"] == (
        datetime.strptime("2026-09-01 10:00:30", "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
    ).strftime("%Y-%m-%d %H:%M:%S")


def test_profile_detail_decision_reviewer_label_is_chinese_for_the_unknown_reviewer(tmp_path):
    """M1：鉴权是空壳，今天每条留痕的 reviewer 恒为 UNKNOWN_REVIEWER
    （"unknown:web-session"）。直接展示裸值是一串没有意义的英文。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_review(conn, "j1", 1, "approved")

    decision = client.get("/api/jobs/j1/profile").json()["decisions"][0]

    assert decision["reviewer"] == "unknown:web-session"
    assert decision["reviewer_label"] == "未登录（演示环境）"
    assert decision["decided_at_label"]


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
    effect_log / outbox 的**内容**逐表恒等（I-4：行数快照挡不住 UPDATE，
    UPDATE 不改变行数）。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_outbox(conn, "j1", "question")

    before = _snapshot_tables(conn)

    client.get("/api/jobs")
    client.get("/api/jobs")

    after = _snapshot_tables(conn)
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
    """内容快照而非行数快照（I-4）：UPDATE 不改变行数，只有逐字比对才挡得住。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    before = _snapshot_tables(conn)

    client.get("/api/jobs/j1/profile")
    client.get("/api/jobs/j1/profile")

    after = _snapshot_tables(conn)
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


# ── 8.4 转人工队列 ───────────────────────────────────────────────────────────


def test_queue_is_empty_when_nothing_needs_a_human(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    data = client.get("/api/queues/needs-manual").json()

    assert data == {"jobs": [], "total": 0}


def test_queue_picks_up_jd_discrimination_flag(tmp_path):
    """今天唯一真实存在的写入方：app/graph/nodes.py 的
    effect_generate_and_persist_jd 在 JD 连续 2 次触发歧视性表述检测后落库。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    data = client.get("/api/queues/needs-manual").json()

    assert data["total"] == 1
    assert data["jobs"][0]["job_id"] == "j1"
    assert [r["code"] for r in data["jobs"][0]["needs_manual_reasons"]] == ["jd_discrimination"]


def test_queue_picks_up_revision_limit(tmp_path):
    """spec「修改次数上限」要求"提示转人工"。修复前那句提示只活在一次 409 响应里，
    页面一关就没了——队列把它变成一条查得到的事实。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    for version in range(1, MAX_REVISIONS + 1):
        _seed_review(
            conn, "j1", version, DECISION_REVISION_REQUESTED,
            decided_at=f"2026-09-01 11:0{version}:00",
        )

    data = client.get("/api/queues/needs-manual").json()

    assert data["total"] == 1
    assert [r["code"] for r in data["jobs"][0]["needs_manual_reasons"]] == ["revision_limit"]
    assert str(MAX_REVISIONS) in data["jobs"][0]["needs_manual_reasons"][0]["label"]


def test_queue_excludes_approved_jobs_that_only_hit_the_revision_limit(tmp_path):
    """final review I-2：approved 岗位一旦撞过修改上限，`revise()` 已经对它
    直接 409、`revision_counts` 也不会再变——这是一件**已经做完的事**，不是
    还需要人工介入的事。修复前这类岗位会永久钉在队列里、没有任何路径能清掉
    （队列只读、revise() 对 approved 409、本单元不许写库）。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "A"}, status="approved")
    for version in range(1, MAX_REVISIONS + 1):
        _seed_review(
            conn, "j1", version, DECISION_REVISION_REQUESTED,
            decided_at=f"2026-09-01 11:0{version}:00",
        )

    data = client.get("/api/queues/needs-manual").json()

    assert data == {"jobs": [], "total": 0}


def test_queue_still_includes_approved_jobs_flagged_by_jd_discrimination(tmp_path):
    """反证，防止上一条修法写过头：⛔ 不能整体过滤 approved 岗位——
    `_jd_needs_manual` 恰恰只出现在 approved 岗位上，那是今天队列里唯一
    真实存在的写入方，整体过滤会得到一个恒空队列（Global Constraint 8
    明令要防的无症状故障）。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    data = client.get("/api/queues/needs-manual").json()

    assert data["total"] == 1
    assert [r["code"] for r in data["jobs"][0]["needs_manual_reasons"]] == ["jd_discrimination"]


def test_queue_picks_up_job_status_column_when_someone_finally_writes_it(tmp_path):
    """WBS 2.5 落地当天这一条自动生效。⛔ 不许因为"现在无人写入"就省掉——
    只认一个恒为空的状态列，队列会永远是空的，而"空队列"和"没人需要处理"
    在界面上长得一模一样。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="needs_manual")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    data = client.get("/api/queues/needs-manual").json()

    assert data["total"] == 1
    assert [r["code"] for r in data["jobs"][0]["needs_manual_reasons"]] == ["job_status"]


def test_queue_excludes_abandoned_jobs(tmp_path):
    """放弃是终态、不再流转。把它摆进 HR 的待办里只会制造清不掉的积压。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="abandoned")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="abandoned",
    )

    assert client.get("/api/queues/needs-manual").json() == {"jobs": [], "total": 0}


def test_queue_orders_oldest_first(tmp_path):
    """队列按"等得最久的排前面"，与列表页的"最近有动静的排前面"刻意相反：
    列表回答"最近发生了什么"，队列回答"该先办哪一个"。"""
    client, conn = _make_app(tmp_path)
    for job_id, at in (("recent", "2026-09-03 15:00:00"), ("stale", "2026-09-01 08:00:00")):
        _seed_job(conn, job_id, status="approved")
        _seed_version(
            conn, job_id, 1, {"job_title": job_id, "_jd_text": "…", "_jd_needs_manual": True},
            status="approved", created_at=at,
        )

    ids = [job["job_id"] for job in client.get("/api/queues/needs-manual").json()["jobs"]]

    assert ids == ["stale", "recent"]


def test_queue_row_has_the_same_shape_as_a_list_row(tmp_path):
    """两个页面渲染同一个卡片组件。形状分叉了没有测试会自己发现。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    list_row = client.get("/api/jobs").json()["jobs"][0]
    queue_row = client.get("/api/queues/needs-manual").json()["jobs"][0]

    assert set(list_row.keys()) == set(queue_row.keys())
    assert list_row == queue_row


def test_queue_is_mounted_under_the_configured_root_path(tmp_path):
    client, conn = _make_app(tmp_path, root_path="/hr/recruit-agent")
    _seed_job(conn, "j1")

    assert client.get("/hr/recruit-agent/api/queues/needs-manual").status_code == 200
    assert client.get("/api/queues/needs-manual").status_code == 404


def test_queue_exposes_no_write_verbs(tmp_path):
    """合规红线「AI 只做排序推荐，不做自动淘汰」在本单元的落点：队列只展示、
    不处置。⛔ 不做批量确认/批量放弃/批量重生成（M2 的事）。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")

    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/api/queues/needs-manual").status_code == 405


def test_queue_does_not_write_anything(tmp_path):
    """内容快照而非行数快照（I-4）：UPDATE 不改变行数，只有逐字比对才挡得住。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    before = _snapshot_tables(conn)

    client.get("/api/queues/needs-manual")
    client.get("/api/queues/needs-manual")

    after = _snapshot_tables(conn)
    assert before == after


# ── final review I-4：server.py 里三个 handler + 两个 helper 的 AST 写守卫 ──
#
# app/storage/job_queries.py 已经有等价的 AST 守卫
# （test_module_contains_no_write_statements），app/web/server.py 里
# list_jobs / get_job_profile / needs_manual_queue 三个 handler 及其
# _job_row_payload / _job_rows_with_context 两个 helper 此前没有——而这三个
# handler 的函数体正落在 server.py 里，是最可能被将来某个人顺手加一句
# UPDATE 的地方（行数快照挡不住 UPDATE，见上面几条 _does_not_write_anything
# 测试的加固说明）。

_READ_ONLY_HANDLER_NAMES = (
    "list_jobs",
    "get_job_profile",
    "needs_manual_queue",
    "_job_row_payload",
    "_job_rows_with_context",
)


def _non_docstring_string_literals_in_function(tree: ast.AST, name: str) -> list[str] | None:
    """给定函数名，取出它函数体内所有**非 docstring**的字符串字面量。

    与 tests/test_job_queries.py::_non_docstring_literals 同一条纪律：
    ⛔ 不扫函数自己的说明性注释/docstring（这几个 handler 的 docstring 里
    大量出现"⛔ 不许有 INSERT/UPDATE/DELETE"这类说明文字，扫全文会被自己
    的说明判违例）。返回 None 表示按这个名字找不到函数——调用方必须把它当
    失败处理，不能悄悄跳过。
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            docstring_id = None
            if ast.get_docstring(node, clean=False) is not None:
                docstring_id = id(node.body[0].value)
            return [
                sub.value
                for sub in ast.walk(node)
                if isinstance(sub, ast.Constant)
                and isinstance(sub.value, str)
                and id(sub) != docstring_id
            ]
    return None


def test_view_endpoint_handlers_and_helpers_contain_no_write_statements():
    """8.1/8.2/8.4 三个 handler 及其两个 helper：不许有任何一条写语句字面量。

    这条与 tests/test_job_queries.py::test_module_contains_no_write_statements
    是同一个判据在 server.py 这一侧的等价物——scope 收窄到这五个函数的函数体，
    不牵连 create_job / confirm / revise / abandon 等本来就该写库的既有端点。
    """
    tree = ast.parse(Path("app/web/server.py").read_text(encoding="utf-8"))

    for name in _READ_ONLY_HANDLER_NAMES:
        literals = _non_docstring_string_literals_in_function(tree, name)
        assert literals is not None, (
            f"app/web/server.py 里找不到函数 {name}——8.1/8.2/8.4 的实现可能"
            "被改名、移动或删除了，这条写守卫需要重新核对覆盖范围。"
        )
        for literal in literals:
            upper = literal.upper()
            for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE", "DROP TABLE"):
                assert statement not in upper, (
                    f"{name}() 里出现了 {statement}：{literal!r}——"
                    "8.1/8.2/8.4 三个只读端点及其 helper 不许有任何写语句。"
                )


def test_queue_contains_exactly_the_needs_manual_jobs_and_is_server_side_state(tmp_path):
    """控制器追加：队列既要精确（不多不少），又要是服务端推导出来的事实而不是
    前端记住的状态——刷新（甚至换一个全新的客户端/进程）之后仍然原样在。"""
    client, conn = _make_app(tmp_path)

    # 两条应该进队列的，理由刻意不同：一条是 JD 歧视性表述被转人工，
    # 一条是修改次数达上限。
    _seed_job(conn, "flagged-jd", status="approved")
    _seed_version(
        conn, "flagged-jd", 1,
        {"job_title": "JD 有问题", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    _seed_job(conn, "maxed-out")
    _seed_version(conn, "maxed-out", 1, {"job_title": "改太多次"})
    for version in range(1, MAX_REVISIONS + 1):
        _seed_review(
            conn, "maxed-out", version, DECISION_REVISION_REQUESTED,
            decided_at=f"2026-09-01 11:0{version}:00",
        )

    # 三条不应该进队列的：一条普通草案、一条画像干净的已确认岗位、一条已放弃
    # （即使放弃前 JD 曾被标记转人工，放弃后也不再流转，见
    # test_queue_excludes_abandoned_jobs）。
    _seed_job(conn, "plain-draft")
    _seed_version(conn, "plain-draft", 1, {"job_title": "普通草案"})

    _seed_job(conn, "clean-approved", status="approved")
    _seed_version(
        conn, "clean-approved", 1, {"job_title": "干净的已确认岗位", "_jd_text": "…"},
        status="approved",
    )

    _seed_job(conn, "abandoned-with-flag", status="abandoned")
    _seed_version(
        conn, "abandoned-with-flag", 1,
        {"job_title": "放弃了", "_jd_text": "…", "_jd_needs_manual": True},
        status="abandoned",
    )

    data = client.get("/api/queues/needs-manual").json()
    ids = {job["job_id"] for job in data["jobs"]}

    expected = {"flagged-jd", "maxed-out"}
    # 双向断言：该在的都在，不该在的一个都不多——按集合比较，⛔ 不按下标或
    # 长度，否则"多一条、少一条但总数凑巧相等"这种错误会被放过。
    assert ids == expected
    assert data["total"] == len(expected)

    # 队列必须在刷新后仍在，因为它是服务端从库里推导出来的，不是前端记住的
    # 状态。_make_app(tmp_path) 用同一个 tmp_path 会算出同一个 db 文件路径
    # （见 _make_app 的 db_path = tmp_path / "views.db"），再调一次就是一个
    # 全新的 create_app + 全新 TestClient，不携带上面那个 app 实例的任何
    # 内存状态，等价于"用户刷新页面、甚至换一台机器打开"。
    fresh_client, _ = _make_app(tmp_path)
    fresh_ids = {job["job_id"] for job in fresh_client.get("/api/queues/needs-manual").json()["jobs"]}

    assert fresh_ids == expected


# ── 控制器追加：三个视图端点 + 静态资源在任意前缀下都真的能跑 ────────────────
#
# 上面的 test_*_is_mounted_under_the_configured_root_path 几条各自只验了一个
# 端点在一个前缀（/hr/recruit-agent）下工作。那证明不了"任意子路径"——都用
# 同一个真实部署前缀，硬编码成那一个值也会让它们全部通过。这条测试故意跑两个
# 前缀：一个是真实部署前缀，另一个是随手起的、更深、与部署无关的前缀
# （/zp-7f3a9c/nested/deep），只有两个都过，才说明挂载机制本身是通用的，
# 不是碰巧只对 /hr/recruit-agent 生效（部署约束 1：验收标准是挂到任意子路径
# 下都能正常工作）。

# 二审 Important finding I3：上一版拿 STATIC_DIR.iterdir() 现读磁盘文件名，
# 证明的是"磁盘上的文件在前缀下能被访问到"，证明不了"index.html 页面里
# 实际引用的资源在前缀下能解析到"——如果将来有人加一个
# `<script src="/static/foo.js">`（绝对路径，会打到域根而不是这个前缀），
# 只要那个文件确实存在，旧版这条测试照样绿，完全漏掉这个真实场景。
# 改成直接解析渲染后的 HTML 里 <link href=…> / <script src=…> / <img src=…>
# 三种标签的资源引用，逐个在前缀下 GET，断言 200；磁盘现读那条判据继续保留
# （两条判据角度不同，不是互相替代）。
_ASSET_TAG_RE = re.compile(
    r"""<(?:link|script|img)\b[^>]*?\b(?:href|src)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
# 页面今天是纯内联单文件，预期解析不出任何标签资源引用。即便如此也要留一条
# 更宽的扫描——不限定标签名，只认"href/src 属性值以 / 开头"这个模式——防止
# 解析集合为空时这条测试整个失去意义：将来任何标签、任何属性写成了绝对路径
# 引用，这里都要能红，而不是因为凑巧没有命中上面那个窄标签正则就悄悄放过。
_ANY_ABSOLUTE_HREF_OR_SRC_RE = re.compile(r"""\b(?:href|src)\s*=\s*["'](/[^"']*)["']""")


def test_all_view_endpoints_and_assets_work_under_any_root_path(tmp_path):
    from app.web.server import STATIC_DIR

    for index, prefix in enumerate(("/hr/recruit-agent", "/zp-7f3a9c/nested/deep")):
        # 两个前缀分别用独立的子目录起独立的 db 文件，避免共用一份
        # tmp_path / "views.db" 而互相脏读。
        sub_dir = tmp_path / f"prefix-{index}"
        sub_dir.mkdir()
        client, conn = _make_app(sub_dir, root_path=prefix)
        _seed_job(conn, "j1")
        _seed_version(conn, "j1", 1, {"job_title": "A"})

        index_resp = client.get(f"{prefix}/")
        assert index_resp.status_code == 200
        assert f'<base href="{prefix}/">' in index_resp.text

        assert client.get(f"{prefix}/api/jobs").status_code == 200
        assert client.get(f"{prefix}/api/jobs/j1/profile").status_code == 200
        assert client.get(f"{prefix}/api/queues/needs-manual").status_code == 200

        # 静态资源要在前缀下可用，且不硬编码文件名——文件名从磁盘上真实的
        # static 目录现读，index.html 改名或将来加/删静态文件都不需要跟着改
        # 这条测试。
        static_files = [path.name for path in STATIC_DIR.iterdir() if path.is_file()]
        assert static_files, "static 目录下没有任何文件，挂载点无从验证"
        for filename in static_files:
            resp = client.get(f"{prefix}/static/{filename}")
            assert resp.status_code == 200, f"{filename} 在前缀 {prefix} 下 404 了"

        # 从渲染后的 HTML 本身解析资源引用（而不是从磁盘反推），逐个在这个
        # 前缀下 GET。今天页面全内联，预期这里解析出空列表；一旦将来真的加了
        # 一个 <link>/<script src>/<img>，这段代码自动开始覆盖它，不需要
        # 谁记得回来改测试。
        assets = _ASSET_TAG_RE.findall(index_resp.text)
        absolute_assets = [a for a in assets if a.startswith("/")]
        assert not absolute_assets, (
            f"index.html 里有绝对路径的资源引用 {absolute_assets}——"
            f"挂到前缀 {prefix} 下会被解析到域根，而不是这个前缀（部署约束 1）。"
        )
        for asset in assets:
            if asset.startswith(("http://", "https://", "//")):
                continue  # 外部资源不归本前缀管，不在本测试范围内
            resp = client.get(f"{prefix}/{asset}")
            assert resp.status_code == 200, f"{asset} 在前缀 {prefix} 下解析失败"

        if not assets:
            # 解析集合为空时，用不限定标签名的宽扫描再确认一遍：整份 HTML
            # 里没有任何 href/src 属性写成了绝对路径——即使今天没有真实资产
            # 可测，这条断言仍然会对"未来加了一个绝对路径引用"这件事敏感。
            # ⛔ 排除 <base href="…">：那是部署约束 1 要求的、故意写成绝对
            # 路径的那一个例外（前缀本身就是从域根开始算的），不是本条要挡
            # 的"资源引用写成了绝对路径"。
            html_without_base_tag = re.sub(r"<base\b[^>]*>", "", index_resp.text)
            stray_absolute = _ANY_ABSOLUTE_HREF_OR_SRC_RE.search(html_without_base_tag)
            assert not stray_absolute, (
                f"没有解析到任何 <link>/<script src>/<img> 资源标签，但页面里"
                f"存在一个绝对路径的 href/src 属性：{stray_absolute.group(0)!r}"
                "——需要确认这是不是一个被上面的标签正则漏掉的资源引用。"
            )

        # 反向证明：配了前缀之后，不带前缀的路径必须不是 200——否则前缀就是
        # 摆设，没有真的生效（与既有的 test_*_is_mounted_under_the_configured_
        # root_path 系列同一个判据）。
        assert client.get("/api/jobs").status_code == 404
        assert client.get("/api/queues/needs-manual").status_code == 404
