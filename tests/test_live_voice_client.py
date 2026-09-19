"""app/live_voice/client.py：`.51` 出站客户端。用 httpx.MockTransport 模拟
语音主机响应——不依赖真实网络或真实语音主机进程。"""
import json

import httpx
import pytest

from app.live_voice import signing
from app.live_voice.client import VoiceHostClient, VoiceHostRequestFailed
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion

SECRET = "test-secret"


def _bundle():
    return SessionBundle(
        session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
        questions=[SessionBundleQuestion(question_id="q1", seq=1, text="讲讲你的项目", follow_ups=[])],
    )


def _client_with_handler(handler):
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://voice-host.invalid")
    return VoiceHostClient(base_url="https://voice-host.invalid", shared_secret=SECRET, http_client=http_client)


def _assert_signed(request: httpx.Request):
    ts = request.headers[signing.HEADER_TIMESTAMP]
    sig = request.headers[signing.HEADER_SIGNATURE]
    signing.verify(
        secret=SECRET, method=request.method, path=request.url.path, timestamp=ts,
        body=request.content, signature=sig,
    )


def test_create_session_sends_signed_request_and_parses_ack():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.method == "POST"
        assert request.url.path == "/sessions"
        body = json.loads(request.content)
        assert body["session_id"] == "s1"
        return httpx.Response(200, json={"accepted": True})

    client = _client_with_handler(handler)
    ack = client.create_session(_bundle())
    assert ack == {"accepted": True}


def test_poll_events_parses_response_into_typed_model():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.url.path == "/sessions/s1/events"
        assert request.url.params["since"] == "0"
        return httpx.Response(200, json={"events": [], "session_status": "in_progress"})

    client = _client_with_handler(handler)
    poll = client.poll_events("s1", since=0)
    assert poll.session_status == "in_progress"
    assert poll.events == []


def test_fetch_recording_returns_content_and_sha_header():
    content = b"fake-recording-bytes"
    import hashlib
    sha = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.url.path == "/sessions/s1/recording"
        return httpx.Response(200, content=content, headers={"X-Recording-SHA256": sha})

    client = _client_with_handler(handler)
    got_content, got_sha = client.fetch_recording("s1")
    assert got_content == content
    assert got_sha == sha


def test_notify_delete_artifacts_sends_delete():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.method == "DELETE"
        assert request.url.path == "/sessions/s1/artifacts"
        return httpx.Response(204)

    client = _client_with_handler(handler)
    client.notify_delete_artifacts("s1")


def test_non_2xx_response_raises_with_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _client_with_handler(handler)
    with pytest.raises(VoiceHostRequestFailed) as exc_info:
        client.create_session(_bundle())
    assert exc_info.value.status_code == 500
