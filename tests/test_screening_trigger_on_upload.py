"""触发点一——上传/重解析后立即判定（task-5-brief.md）。

⚠️ 只验黑盒行为：上传一份不满足硬门槛的简历后，
GET /api/applications/{id}/screening-flags 必须能看到一条 fail 标记。
不复验 screen_and_persist/latest_approved_profile_version 本身的判定逻辑——
那是 Task 3/4 的覆盖范围（tests/test_screening_nodes_rule_loader.py、
tests/test_screening_nodes_compute_effect.py、tests/test_hard_requirement_screening.py）。

⚠️ 上传格式用 .docx 而非 .txt：app/parsing/extract_text.py 的
SUPPORTED_SUFFIXES 只含 (".pdf", ".docx")，.txt 会在 _ingest_one_resume 里被
提前拒收（status="rejected"），走不到 compute_parse/effect_persist_parse。
_docx_bytes 复用 tests/test_resume_upload.py 里已有的最小构造方式，
⛔ 不新发明一种（tests/ 目录没有 __init__.py，模块间不能互相 import，
所以这里原样复制同一个辅助函数，不是重新设计）。
"""
from __future__ import annotations

import io

import docx
import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


@pytest.fixture
def screening_client(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="hr1", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)

    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', 1, 'experience_years', 'gte', '3', 1, '工作年限要求：3 年及以上')"
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'j1', 1, 'approved', '{}')"
    )
    conn.commit()
    return client, conn


def _fake_compute_parse(gateway, *, spans, audit_context):
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, SpanRef, TextField,
    )

    fields = ResumeFields(
        name=TextField(value="张三", confidence=0.9, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=1.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="1年", start=5, end=7)]
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )

    class _Meta:
        response_model = "test-model"

    return fields, _Meta()


def test_upload_triggers_initial_screening(screening_client, monkeypatch):
    client, _conn = screening_client
    monkeypatch.setattr("app.web.server.compute_parse", _fake_compute_parse)

    files = [
        ("files", ("a.docx", _docx_bytes(["张三，1年工作经验。" * 6]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
    ]
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=files,
    )
    assert resp.status_code == 200
    body = resp.json()["results"][0]
    assert body["parse_status"] == "parsed"
    application_id = body["application_id"]
    assert application_id is not None

    flags = client.get(f"/api/applications/{application_id}/screening-flags").json()
    assert len(flags["flags"]) == 1
    assert flags["flags"][0]["verdict"] == "fail"


def test_reparse_triggers_screening(screening_client, monkeypatch):
    """第二个触发点：reparse_resume 同一插入块也要生效（brief 3b 段要求对
    reparse_resume 做同样的插入，此处一并黑盒验证，不额外新建测试文件）。

    reparse 用新的 parser_version（v2）重新解析，screen_and_persist 的
    business_key = f"{profile_version}:{parse_version}" 因此与首次上传（v1）
    不同，不会被幂等短路——预期 reparse 后再新增一条 fail 标记（共 2 条）。
    """
    client, _conn = screening_client
    monkeypatch.setattr("app.web.server.compute_parse", _fake_compute_parse)

    files = [
        ("files", ("a.docx", _docx_bytes(["张三，1年工作经验。" * 6]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
    ]
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=files,
    )
    resume_id = resp.json()["results"][0]["resume_id"]
    application_id = resp.json()["results"][0]["application_id"]

    reparse_resp = client.post(f"/api/resumes/{resume_id}/reparse")
    assert reparse_resp.status_code == 200

    flags = client.get(f"/api/applications/{application_id}/screening-flags").json()
    assert len(flags["flags"]) == 2
    assert all(f["verdict"] == "fail" for f in flags["flags"])
