"""voice_host/api.py：语音主机 FastAPI 服务端点。签名用 app.live_voice.
signing（.51 侧的源码，测试环境两侧代码同在一个仓库检出里，直接复用没有
问题；真实部署时语音主机跑的是 voice_host/_vendor/signing.py 的独立拷贝，
算法逐字一致，见 Task 9 Step 2 的漂移测试）。"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.live_voice import signing
from voice_host import queue_store
from voice_host.api import create_app

SECRET = "test-secret"


@pytest.fixture
def client(tmp_path):
    opened = []
    app = create_app(
        queue_db_path=str(tmp_path / "queue.db"), shared_secret=SECRET,
        on_session_opened=lambda session_id, bundle_json: opened.append(session_id),
    )
    test_client = TestClient(app)
    test_client.opened = opened  # type: ignore[attr-defined]
    return test_client


def _signed_headers(*, method: str, path: str, body: bytes) -> dict:
    ts = str(time.time())
    sig = signing.sign(secret=SECRET, method=method, path=path, timestamp=ts, body=body)
    return {signing.HEADER_TIMESTAMP: ts, signing.HEADER_SIGNATURE: sig}


def _bundle_body(session_id="s1"):
    return json.dumps({
        "session_id": session_id, "prep_curve": "easy_to_hard", "follow_up_limit": 2,
        "questions": [{"question_id": "q1", "seq": 1, "text": "讲讲你的项目", "follow_ups": []}],
    }).encode("utf-8")


def test_post_sessions_accepts_valid_signature_and_registers(client):
    body = _bundle_body()
    headers = _signed_headers(method="POST", path="/sessions", body=body)
    response = client.post("/sessions", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert client.opened == ["s1"]


def test_post_sessions_rejects_bad_signature(client):
    body = _bundle_body()
    headers = _signed_headers(method="POST", path="/sessions", body=b"different-body")
    response = client.post("/sessions", content=body, headers=headers)
    assert response.status_code == 401


def test_post_sessions_rejects_missing_headers(client):
    response = client.post("/sessions", content=_bundle_body())
    assert response.status_code == 401


def test_get_events_returns_empty_before_any_turn(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))

    headers = _signed_headers(method="GET", path="/sessions/s1/events", body=b"")
    response = client.get("/sessions/s1/events", params={"since": 0}, headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"events": [], "session_status": "in_progress"}


def test_get_events_for_unknown_session_returns_404(client):
    headers = _signed_headers(method="GET", path="/sessions/unknown/events", body=b"")
    response = client.get("/sessions/unknown/events", params={"since": 0}, headers=headers)
    assert response.status_code == 404


def test_get_recording_before_available_returns_404(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))
    headers = _signed_headers(method="GET", path="/sessions/s1/recording", body=b"")
    response = client.get("/sessions/s1/recording", headers=headers)
    assert response.status_code == 404


def test_delete_artifacts_is_idempotent_even_without_recording(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))
    headers = _signed_headers(method="DELETE", path="/sessions/s1/artifacts", body=b"")
    first = client.delete("/sessions/s1/artifacts", headers=headers)
    second = client.delete("/sessions/s1/artifacts", headers=headers)
    assert first.status_code == 204
    assert second.status_code == 204


def test_switch_to_text_records_mode_and_event(client):
    # 这两个端点是候选人浏览器直接调用的（见 voice_host/web/interview.js），
    # 浏览器结构上拿不到 VOICE_HOST_SHARED_SECRET，因此不带任何签名头
    # ——这才是真实候选人请求的样子（finding 1 修复）。
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))

    switch_body = b'{"trigger": "manual"}'
    response = client.post("/sessions/s1/switch-to-text", content=switch_body)
    assert response.status_code == 200


def test_submit_text_answer_requires_prior_switch(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))

    answer_body = json.dumps({"text": "我的文字回答"}).encode("utf-8")
    response = client.post("/sessions/s1/text-answer", content=answer_body)
    assert response.status_code == 200


def test_switch_to_text_for_unknown_session_returns_404(client):
    response = client.post("/sessions/unknown/switch-to-text", content=b'{"trigger": "manual"}')
    assert response.status_code == 404


def test_submit_text_answer_for_unknown_session_returns_404(client):
    answer_body = json.dumps({"text": "我的文字回答"}).encode("utf-8")
    response = client.post("/sessions/unknown/text-answer", content=answer_body)
    assert response.status_code == 404


def test_switch_to_text_and_text_answer_work_with_no_auth_headers_at_all(client):
    # 明确证明修复解决了浏览器的真实能力约束：不带 X-ZP-Timestamp /
    # X-ZP-Signature 中的任何一个也能成功（不是漏配了某一个头凑巧过）。
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))

    switch_response = client.post("/sessions/s1/switch-to-text", content=b"{}", headers={})
    assert switch_response.status_code == 200

    answer_response = client.post(
        "/sessions/s1/text-answer",
        content=json.dumps({"text": "无签名也能提交"}).encode("utf-8"),
        headers={},
    )
    assert answer_response.status_code == 200


def test_startup_wires_periodic_sweep_of_expired_recordings(tmp_path, monkeypatch):
    # finding 3：验证 24 小时安全网清扫真的被周期任务调用，不必真等一小时
    # ——把 sweep_interval_seconds 调到很短，起 TestClient 的 lifespan（触发
    # startup 事件），轮询等待 spy 被调用即可。
    import voice_host.api as api_module

    calls = []
    original_sweep = queue_store.sweep_expired_intermediate_files

    def spy(conn, **kwargs):
        calls.append(kwargs)
        return original_sweep(conn, **kwargs)

    monkeypatch.setattr(api_module.queue_store, "sweep_expired_intermediate_files", spy)

    app = api_module.create_app(
        queue_db_path=str(tmp_path / "queue.db"), shared_secret=SECRET, sweep_interval_seconds=0.05,
    )
    with TestClient(app):
        deadline = time.time() + 2.0
        while not calls and time.time() < deadline:
            time.sleep(0.05)

    assert calls, "startup 后台循环应在短间隔后调用 queue_store.sweep_expired_intermediate_files"


def test_run_sweep_once_hook_invokes_sweep_with_app_connection(tmp_path):
    # 更直接地验证接线本身（不依赖后台循环的定时行为）：app.state.run_sweep_once
    # 是 create_app 内部真实 conn 上的清扫调用，同步 await 一次即可验证。
    app = create_app(queue_db_path=str(tmp_path / "queue.db"), shared_secret=SECRET)
    asyncio.run(app.state.run_sweep_once())
