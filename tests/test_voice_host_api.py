"""voice_host/api.py：语音主机 FastAPI 服务端点。签名用 app.live_voice.
signing（.51 侧的源码，测试环境两侧代码同在一个仓库检出里，直接复用没有
问题；真实部署时语音主机跑的是 voice_host/_vendor/signing.py 的独立拷贝，
算法逐字一致，见 Task 9 Step 2 的漂移测试）。"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.live_voice import signing
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
