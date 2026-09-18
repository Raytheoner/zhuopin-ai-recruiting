from __future__ import annotations

import docx
import pytest
from fastapi.testclient import TestClient

from app.llm.gateway import LLMCallMeta, LLMGateway
from app.schemas.resume_fields import (
    EducationField, ListField, NumberField, ResumeFields, TextField,
)
from app.storage.auth_session import create_session
from app.storage.db import get_connection
from app.storage.hr_account import upsert_account
from app.web.server import create_app
from tests.test_resume_upload import _docx_bytes


ROOT_PATH = "/hr/recruit-agent"


@pytest.fixture
def slice_client(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app(
        db_path=db_path,
        gateway_factory=lambda: LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        ),
        root_path=ROOT_PATH,
        resume_storage_dir=str(tmp_path / "resumes"),
    )
    # ⚠️ 不传 base_url 里带路径段的写法，理由同 Task 2 的注释——httpx 会把
    # base_url 自带的路径段和请求里以 "/" 开头的绝对路径拼接两次导致 404。
    client = TestClient(app)
    conn = get_connection(db_path)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()
    yield client, conn
    conn.close()


def _login(client, conn):
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def _stub_parse(monkeypatch, *, name_confidence: float):
    import app.web.server as server_mod

    def _fake_compute_parse(_gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value="李四", confidence=name_confidence,
                            spans=[{"span_id": 1, "quote": "李四", "start": 0, "end": 2}]),
            years_of_experience=NumberField(value=5.0, confidence=0.9,
                                             spans=[{"span_id": 2, "quote": "5年", "start": 10, "end": 12}]),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1, run_id="run-id-stub")

    monkeypatch.setattr(server_mod, "compute_parse", _fake_compute_parse)


_FILLER = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"


def test_visible_slice_full_flow_under_root_path_prefix(slice_client, monkeypatch):
    client, conn = slice_client
    _login(client, conn)
    _stub_parse(monkeypatch, name_confidence=0.2)  # 低置信度 → 进校对队列

    # 1. 上传入口页可见，root_path 前缀下 base href 正确
    upload_page = client.get(f"{ROOT_PATH}/resumes/upload")
    assert upload_page.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in upload_page.text
    assert 'value="live"' not in upload_page.text

    # 2. 上传一份文本 Word 简历（含"李四"+填充正文）
    files = [("files", ("word.docx", _docx_bytes(["李四", _FILLER]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    upload_resp = client.post(
        f"{ROOT_PATH}/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"}, files=files,
    )
    assert upload_resp.status_code == 200
    resume_id = upload_resp.json()["results"][0]["resume_id"]
    assert upload_resp.json()["results"][0]["parse_status"] == "parsed"

    # 2b. live 样本类别在接口侧仍被拒收（页面不可选，接口仍是最后一道闸）
    live_files = [("files", ("word2.docx", _docx_bytes(["王五", _FILLER]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    live_resp = client.post(
        f"{ROOT_PATH}/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "live"}, files=live_files,
    )
    assert live_resp.json()["results"][0]["status"] == "rejected"
    assert "入库闸未开启" in live_resp.json()["results"][0]["reason"]

    # 3. 未登录访问列表接口被拒
    anon_client = TestClient(client.app)
    anon_resp = anon_client.get(f"{ROOT_PATH}/api/resumes/by-job/j1")
    assert anon_resp.status_code == 401

    # 4. 登录后解析结果列表页可见该简历
    list_page = client.get(f"{ROOT_PATH}/jobs/j1/resumes")
    assert list_page.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in list_page.text

    list_resp = client.get(f"{ROOT_PATH}/api/resumes/by-job/j1")
    assert list_resp.status_code == 200
    items = list_resp.json()["resumes"]
    assert len(items) == 1
    assert items[0]["resume_id"] == resume_id
    assert items[0]["pending_review_count"] == 1  # name 置信度 0.2 < 阈值 0.7

    # 5. 字段校对页：拿到 spans，断言高亮子串与原文一致（按偏移切片）
    review_page = client.get(f"{ROOT_PATH}/resumes/{resume_id}/review")
    assert review_page.status_code == 200

    text_resp = client.get(f"{ROOT_PATH}/api/resumes/{resume_id}/text")
    raw_text = text_resp.json()["raw_text"]
    parsed_resp = client.get(f"{ROOT_PATH}/api/resumes/{resume_id}/parsed")
    name_field = parsed_resp.json()["parsed_json"]["name"]
    span = name_field["spans"][0]
    assert raw_text[span["start"]:span["end"]] == span["quote"] == "李四"

    # 6. 提交校对确认，字段离开待校对队列
    review_resp = client.post(
        f"{ROOT_PATH}/api/resumes/{resume_id}/fields/name/review",
        json={"human_value": "李四四"},
    )
    assert review_resp.status_code == 200

    list_resp_after = client.get(f"{ROOT_PATH}/api/resumes/by-job/j1")
    item_after = list_resp_after.json()["resumes"][0]
    assert item_after["pending_review_count"] == 0
    field_by_name = {f["field"]: f for f in item_after["fields"]}
    assert field_by_name["name"]["review_status"] == "reviewed"
    assert field_by_name["name"]["reviewed_by"] == "alice"

    # 7. 每次"读取解析结果"各留一条痕（本用例调了 3 次 GET .../by-job/j1，
    #    每次对这一份 resume 各记一条 parsed_result 访问）
    access_count = conn.execute(
        "SELECT COUNT(*) FROM resume_access_log WHERE resume_id = ? AND access_type = 'parsed_result'",
        (resume_id,),
    ).fetchone()[0]
    assert access_count == 3
