from __future__ import annotations

import io
import zipfile

import docx

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _stub_compute_parse(monkeypatch, name: str):
    import app.web.server as server_mod
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, TextField,
    )

    def _fake(gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value=name, confidence=0.95),
            years_of_experience=NumberField(not_mentioned=True, value=None, confidence=1.0),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1, run_id="run-id")

    monkeypatch.setattr(server_mod, "compute_parse", _fake)


def _client_and_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def _upload_zip(client, entries: dict[str, list[str]]) -> list[dict]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, paragraphs in entries.items():
            zf.writestr(name, _docx_bytes(paragraphs))
    files = [("files", ("bundle.zip", buf.getvalue(), "application/zip"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    assert resp.status_code == 200
    return resp.json()["results"]


def _upload_single(client, file_name: str, paragraphs: list[str]) -> dict:
    files = [("files", (file_name, _docx_bytes(paragraphs),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    assert resp.status_code == 200
    return resp.json()["results"][0]


def _paragraphs(phone: bool, tag: str) -> list[str]:
    head = "张三 13800138000" if phone else "张三"
    return [head, f"工作经历：{tag}，某某公司嵌入式工程师，负责车身控制器固件开发，" * 3]


def test_u2_e2e_attach_merge_unmerge(make_test_client, monkeypatch):
    client, conn = _client_and_job(make_test_client)
    _stub_compute_parse(monkeypatch, name="张三")

    # 两份不同来源、同手机号 ⇒ 自动挂接同一候选人
    results = _upload_zip(client, {
        "boss-张三.docx": _paragraphs(phone=True, tag="boss直聘导出"),
        "liepin-张三.docx": _paragraphs(phone=True, tag="猎聘导出"),
    })
    assert sum(1 for r in results if r["status"] == "accepted") == 2
    assert conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1

    # 无手机号、同名、同岗位 → 新建候选人并被标疑似重复
    nophone = _upload_single(client, "boss-张三-无手机号.docx", _paragraphs(phone=False, tag="无手机号"))
    assert nophone["parse_status"] == "parsed"
    assert conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 2

    body = client.get("/api/candidates").json()
    by_id = {c["candidate_id"]: c for c in body["candidates"]}
    # 手机号候选人有 2 份投递；无手机号候选人有 1 份
    primary_id = next(c["candidate_id"] for c in body["candidates"] if c["resume_count"] == 2)
    secondary_id = next(c["candidate_id"] for c in body["candidates"] if c["resume_count"] == 1)
    assert primary_id in by_id[secondary_id]["suspected_duplicate_ids"]

    # 同岗位双投递：HR 选择保留无手机号候选人的那份投递
    secondary_app = conn.execute(
        "SELECT id FROM application WHERE candidate_id = ? AND job_id = 'j1'", (secondary_id,)
    ).fetchone()[0]
    merge_resp = client.post("/api/candidates/merge", json={
        "primary_id": primary_id, "secondary_id": secondary_id,
        "reason": "电话确认同一人", "request_id": "e2e-req-1",
        "keep_application_per_job": {"j1": secondary_app},
    })
    assert merge_resp.status_code == 200
    merge_log_id = merge_resp.json()["merge_log_id"]
    log_count_after_merge = conn.execute("SELECT COUNT(*) FROM candidate_merge_log").fetchone()[0]

    undo = client.post(f"/api/candidates/merge/{merge_log_id}/unmerge")
    assert undo.status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM candidate_merge_log").fetchone()[0] == log_count_after_merge
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate WHERE id = ? AND merged_into IS NULL", (secondary_id,)
    ).fetchone()[0] == 1
    # 撤销后无手机号候选人的投递归还
    assert conn.execute(
        "SELECT COUNT(*) FROM application WHERE candidate_id = ?", (secondary_id,)
    ).fetchone()[0] == 1
