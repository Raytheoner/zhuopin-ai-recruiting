"""U3 e2e（tasks 3.8）：安排 → 生成 → 复制 → 回填已发出 → 回填确认；
另跑一条「候选人拒绝」路径，断言投递阶段不变、淘汰记录不新增。

⚠️ 场次由夹具直接 `INSERT INTO interview_slot` 造（U1 建表、U2 的
`effect_schedule_slot` 尚未落地）。U2 落地后可把 `_seed_slot` 换成对排期接口的调用，
本文件其余部分逐字不变。

全程走 HTTP（相对路径与登录 cookie 与生产一致），⛔ 不直接调节点函数。
"""
from __future__ import annotations

import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label
from app.middleware.auth import SESSION_COOKIE_NAME
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.web import server as server_module


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    # 「安排」这一步：U2 的 effect_schedule_slot 落地前由夹具直插（见模块 docstring）。
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, location_or_link, created_by) "
        "VALUES ('slot-1', 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite', "
        "'无锡市新吴区××路 1 号', 'tester')"
    )
    conn.commit()


def _login(conn, client) -> None:
    account_id = upsert_account(conn, username="tester", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)


def _fake_compute(run_ids=("run-e2e-1",)):
    remaining = list(run_ids)

    def _compute(conn, *, slot_id, gateway, contact_hint=None):
        run_id = remaining.pop(0) if remaining else run_ids[-1]
        return (
            InvitationDraft(
                body=enforce_ai_label(
                    "张三您好：诚邀您参加面试。",
                    generated_at="2026-10-10T00:00:00+00:00",
                ),
                run_id=run_id,
                response_model="deepseek-chat-actual-v1",
                prompt_version="invite-v1",
            ),
            "v1",
        )

    return _compute


@pytest.fixture
def client(make_test_client, monkeypatch):
    c, conn = make_test_client()
    _seed(conn)
    _login(conn, c)
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot", _fake_compute()
    )
    return c, conn


def test_e2e_generate_copy_backfill_sent_then_confirmed(client):
    c, _conn = client
    generated = c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    assert generated.status_code == 201
    draft = generated.json()["drafts"][-1]
    assert draft["version"] == 1

    # 「复制」＝页面把正文交给剪贴板；后端能给出的就是这份正文本身，
    # 断言它带 AI 标识（合规红线：AI 生成的邀约须带标识）。
    detail = c.get("/api/interview-slots/slot-1/invitation").json()
    copied_body = detail["drafts"][-1]["body"]
    assert AI_LABEL_PREFIX in copied_body

    sent = c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "sent", "channel": "wechat", "reason": None},
    )
    assert sent.status_code == 200
    assert sent.json()["slot"]["invitation_status"] == "sent"
    assert sent.json()["slot"]["sent_channel"] == "wechat"

    confirmed = c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "confirmed", "channel": None, "reason": None},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["slot"]["invitation_status"] == "confirmed"
    # 渠道在 confirmed 之后仍然记得（COALESCE 不把 sent_channel 抹掉）。
    assert confirmed.json()["slot"]["sent_channel"] == "wechat"
    assert [o["status"] for o in confirmed.json()["outcomes"]] == ["sent", "confirmed"]


def test_e2e_declined_keeps_stage_and_no_rejection_record(client):
    c, conn = client
    c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "sent", "channel": "email", "reason": None},
    )
    res = c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "declined", "channel": None, "reason": "已接受其他 offer"},
    )
    assert res.status_code == 200
    assert res.json()["slot"]["invitation_status"] == "declined"
    assert (
        conn.execute("SELECT current_stage_id FROM application WHERE id = 'app1'").fetchone()[0]
        == "interview"
    )
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM application_stage_history WHERE application_id = 'app1'"
        ).fetchone()[0]
        == 0
    )


def test_e2e_regenerate_appends_version_and_old_draft_still_readable(client, monkeypatch):
    c, _conn = client
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot",
        _fake_compute(("run-e2e-1", "run-e2e-2", "run-e2e-3")),
    )
    c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    detail = c.get("/api/interview-slots/slot-1/invitation").json()
    assert [d["version"] for d in detail["drafts"]] == [1, 2]
    assert all(d["analysis_run_id"] for d in detail["drafts"])
