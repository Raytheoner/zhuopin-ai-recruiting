from __future__ import annotations


def test_upload_page_served_and_uses_relative_paths(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/upload")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert 'fetch("api/jobs")' in html
    assert 'fetch("api/resumes/upload"' in html
    assert 'value="live"' not in html  # live 不出现在页面选项（design D2）


def test_upload_page_served_under_root_path_prefix(tmp_path):
    from app.llm.gateway import LLMGateway
    from app.storage.db import get_connection
    from app.web.server import create_app
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "test.db")
    app = create_app(
        db_path=db_path,
        gateway_factory=lambda: LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        ),
        root_path="/hr/recruit-agent",
        resume_storage_dir=str(tmp_path / "resumes"),
    )
    # ⚠️ 不要给 TestClient 传 base_url 里带路径段（如
    # `base_url="http://testserver/hr/recruit-agent"`）——httpx 对以 "/" 开头的
    # 绝对路径请求不会去掉 base_url 自带的路径段，两段会拼接成
    # "/hr/recruit-agent/hr/recruit-agent/..." 导致 404（本计划编写时已用一次性
    # 脚本实测复现）。本仓库现成的 root_path 测试写法见
    # `tests/test_health_endpoint.py`：`TestClient(app)`（不传 base_url）+
    # 请求路径自己带全 root_path 前缀，照抄这个手法。
    client = TestClient(app)
    resp = client.get("/hr/recruit-agent/resumes/upload")
    assert resp.status_code == 200
    assert '<base href="/hr/recruit-agent/">' in resp.text
    get_connection(db_path).close()
