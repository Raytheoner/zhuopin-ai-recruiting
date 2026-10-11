"""U3 e2e（tasks 3.7）：发起 → 一级退回 → 修改 → 两级通过 → 生成文书 → 导出；
审批留痕条数守恒。

节点链核业务事实守恒；同文件另有 HTTP 整链，生成与导出均走实际端点。
分析记录在夹具准备阶段写入，合成 compute 替身只返回固定 Draft，无 SQL、commit 或 effect 调用。
"""
from __future__ import annotations

import io

import docx
import pytest

from app.agents.letter_drafter import LetterDraft
from app.graph.letter_nodes import effect_persist_letter, record_letter_access
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.offer import OfferNotExportableError, assert_offer_exportable
from app.storage.offer_approval_chain import put_approval_chain

BODY = "【AI 生成】张三：拟录用您担任嵌入式工程师。"


def _seed(conn):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','interview')"
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ],
        updated_by="hr1",
    )


def _login(client, conn, username):
    row = conn.execute(
        "SELECT id FROM hr_account WHERE username = ?", (username,)
    ).fetchone()
    token = create_session(conn, hr_account_id=row[0])
    client.cookies.set("hr_session", token)


def _approve(client, conn, offer_id, *, as_username, level, decision="approved",
             comment=None):
    _login(client, conn, as_username)
    return client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": decision, "level": level, "comment": comment},
    )


@pytest.fixture
def env(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    return client, conn


def test_unit3_end_to_end(env):
    client, conn = env

    # ① 发起 Offer
    _login(client, conn, "hr1")
    created = client.post(
        "/api/applications/app1/offer",
        json={
            "department": "研发部",
            "start_date": "2026-11-01",
            "report_to": "李四",
            "note": "面试表现符合岗位要求",
            "request_id": "req-1",
        },
    )
    assert created.status_code == 201
    offer_id = created.json()["id"]
    assert created.json()["status"] == "pending_approval"
    # 待审批 ⇒ 不可导出（spec Scenario「待审批时导出被拒」）
    with pytest.raises(OfferNotExportableError):
        assert_offer_exportable(conn, "app1")

    # ② 一级退回
    returned = _approve(
        client, conn, offer_id, as_username="mgr", level=1,
        decision="returned", comment="入职日期待与候选人确认",
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "needs_revision"

    # ③ 退回后修改 ⇒ round+1、链从第一级重走
    _login(client, conn, "hr1")
    revised = client.patch(
        f"/api/offers/{offer_id}",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": "入职日期已与候选人确认",
            "request_id": "req-revise-1",
        },
    )
    assert revised.status_code == 200
    assert revised.json()["approval_round"] == 2
    assert revised.json()["current_level"] == 1

    # ④ 两级通过
    assert _approve(
        client, conn, offer_id, as_username="mgr", level=1
    ).json()["status"] == "pending_approval"
    approved = _approve(client, conn, offer_id, as_username="gm", level=2)
    assert approved.json()["status"] == "approved"
    assert_offer_exportable(conn, "app1")  # 已审批 ⇒ 门槛放行

    # ⑤ 生成文书（U2 的 effect_persist_letter：Offer 类前置 = offer.status='approved'）
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-1','deepseek-chat','letter-offer-v1',"
        "0.0,'h','{}')"
    )
    conn.commit()
    letter_id = effect_persist_letter(
        conn,
        thread_id="app1",
        business_key="offer:run-1",
        application_id="app1",
        kind="offer",
        version=1,
        template_version=1,
        draft=LetterDraft(
            kind="offer",
            body=BODY,
            run_id="run-1",
            response_model="deepseek-chat",
            prompt_version="letter-offer-v1",
        ),
        created_by="hr1",
    )
    assert letter_id

    # ⑥ 导出（tasks 3.6 的门槛在导出卡点上；已审批 ⇒ 放行并留痕）
    record_letter_access(
        conn,
        accessor="hr1",
        application_id="app1",
        letter_id=letter_id,
        access_type="export",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id = ? "
        "AND access_type = 'export'",
        (letter_id,),
    ).fetchone()[0] == 1

    # ⑦ 留痕守恒：第 1 轮退回 1 条 ＋ 第 2 轮通过 2 条 = 3 条
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 3
    # 重跑最后一级（同 round/level/approver/decision）⇒ 幂等短路，留痕不增
    replay = _approve(client, conn, offer_id, as_username="gm", level=2)
    assert replay.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 3
    # 恒等式（铁律 1）：审批 effect_log 条数 == offer_approval 行数
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_record_approval' "
        "AND thread_id = ?",
        (offer_id,),
    ).fetchone()[0] == 3
    # 投递阶段留在 offer（终态流转是 U5 的职责，⛔ 本单元不碰 application.status）
    assert conn.execute(
        "SELECT current_stage_id, status FROM application WHERE id = 'app1'"
    ).fetchone() == ("offer", "active")


def test_unit3_http_end_to_end(env, monkeypatch):
    """真实跑通 U3 HTTP 状态机与文书/导出路由，不调用外部 LLM。"""
    client, conn = env

    # 合成分析记录属于测试夹具；compute 替身只读取参数并返回固定结果。
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('http-run-1','synthetic-stub',"
        "'letter-offer-v1',0.0,'synthetic-hash','{}')"
    )
    conn.commit()

    # 只替换纯计算层；持久化 effect、文书 HTTP 路由和 docx 导出均走真实实现。
    import app.web.server as server

    def synthetic_draft(conn, *, application_id, kind, gateway):
        assert application_id == "app1"
        assert kind == "offer"
        return (
            LetterDraft(
                kind="offer",
                body=BODY,
                run_id="http-run-1",
                response_model="synthetic-stub",
                prompt_version="letter-offer-v1",
            ),
            1,
        )

    monkeypatch.setattr(server, "compute_letter_draft_for_application", synthetic_draft)

    # 发起并重放：Offer、阶段事实、审批待办与创建幂等日志各只有一份。
    _login(client, conn, "hr1")
    create_payload = {
        "department": "研发部",
        "start_date": "2026-11-01",
        "report_to": "李四",
        "note": "面试表现符合岗位要求",
        "request_id": "http-req-1",
    }
    created = client.post("/api/applications/app1/offer", json=create_payload)
    assert created.status_code == 201
    offer_id = created.json()["id"]
    assert created.json()["status"] == "pending_approval"
    replay_create = client.post("/api/applications/app1/offer", json=create_payload)
    assert replay_create.status_code == 201
    assert replay_create.json()["id"] == offer_id
    assert conn.execute("SELECT COUNT(*) FROM offer WHERE application_id='app1'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_create_offer' "
        "AND thread_id='app1'"
    ).fetchone()[0] == 1

    # 一级退回，随后 HR 修改；PATCH 重放不能再次增加轮次或修订事实。
    returned = _approve(
        client, conn, offer_id, as_username="mgr", level=1,
        decision="returned", comment="入职日期待与候选人确认",
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "needs_revision"
    _login(client, conn, "hr1")
    revision_payload = {
        "department": "研发部",
        "start_date": "2026-12-01",
        "report_to": "李四",
        "note": "入职日期已与候选人确认",
        "request_id": "http-revise-1",
    }
    revised = client.patch(f"/api/offers/{offer_id}", json=revision_payload)
    assert revised.status_code == 200
    assert revised.json()["approval_round"] == 2
    assert revised.json()["current_level"] == 1
    replay_revision = client.patch(f"/api/offers/{offer_id}", json=revision_payload)
    assert replay_revision.status_code == 200
    assert replay_revision.json()["approval_round"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_revise_offer' "
        "AND thread_id=?", (offer_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT start_date, note, approval_round, status FROM offer "
        "WHERE id=?", (offer_id,),
    ).fetchone() == (
        "2026-12-01", "入职日期已与候选人确认", 2, "pending_approval"
    )

    # 新一轮一级、二级通过；审批记录与审批 effect_log 恒等，末级重放不增行。
    level1 = _approve(client, conn, offer_id, as_username="mgr", level=1)
    assert level1.status_code == 200
    assert level1.json()["status"] == "pending_approval"
    level2 = _approve(client, conn, offer_id, as_username="gm", level=2)
    assert level2.status_code == 200
    assert level2.json()["status"] == "approved"
    replay_approval = _approve(client, conn, offer_id, as_username="gm", level=2)
    assert replay_approval.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id=?", (offer_id,)
    ).fetchone()[0] == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_record_approval' "
        "AND thread_id=?", (offer_id,),
    ).fetchone()[0] == 3
    assert conn.execute(
        "SELECT round, level, decision FROM offer_approval "
        "WHERE offer_id=? ORDER BY round, level", (offer_id,),
    ).fetchall() == [(1, 1, "returned"), (2, 1, "approved"), (2, 2, "approved")]

    # 生成与导出走真实 HTTP；只使用合成草稿，不联网、不触达真实简历或外发通道。
    analysis_run_count = conn.execute(
        "SELECT COUNT(*) FROM analysis_run WHERE id='http-run-1'"
    ).fetchone()[0]
    generated = client.post("/api/applications/app1/letters", json={"kind": "offer"})
    assert generated.status_code == 201
    assert conn.execute(
        "SELECT COUNT(*) FROM analysis_run WHERE id='http-run-1'"
    ).fetchone()[0] == analysis_run_count == 1
    letter_id = generated.json()["id"]
    assert generated.json()["ai_generated"] is True
    assert generated.json()["body"] == BODY
    exported = client.get(f"/api/letters/{letter_id}/export.docx")
    assert exported.status_code == 200
    assert exported.content.startswith(b"PK")
    document = docx.Document(io.BytesIO(exported.content))
    assert BODY in "\n".join(paragraph.text for paragraph in document.paragraphs)

    letter = conn.execute(
        "SELECT sent_status, approval_round FROM candidate_letter "
        "JOIN offer ON offer.application_id=candidate_letter.application_id "
        "WHERE candidate_letter.id=?", (letter_id,),
    ).fetchone()
    assert letter == ("exported", 2)
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id=? AND access_type='export'",
        (letter_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate_letter WHERE id=?", (letter_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_persist_letter' "
        "AND thread_id='app1'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT current_stage_id, status FROM application WHERE id='app1'"
    ).fetchone() == ("offer", "active")
