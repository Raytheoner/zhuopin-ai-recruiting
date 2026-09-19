"""`.51` 出站客户端（voice-structured-interview U4 tasks 5.2，design D12：
"`.51` 与语音主机之间只有两类交互，全部由 `.51` 主动发起"）。每次请求都用
app/live_voice/signing.py 签名——两侧共享同一份签名源码（设计决策 2/3）。

`http_client` 可注入（与 app/llm/gateway.py::LLMGateway 的 `client` 参数同
一手法），测试用 httpx.MockTransport，不起真实服务器；生产环境不传则用
真实 httpx.Client。
"""
from __future__ import annotations

import time

import httpx

from app.live_voice import signing
from app.schemas.live_turn_event import LiveEventsPollResponse
from app.schemas.session_bundle import SessionBundle


class VoiceHostRequestFailed(Exception):
    def __init__(self, *, status_code: int, body: str):
        super().__init__(f"语音主机请求失败: status={status_code} body={body[:500]!r}")
        self.status_code = status_code
        self.body = body


class VoiceHostClient:
    def __init__(
        self, *, base_url: str, shared_secret: str, timeout: float = 5.0,
        http_client: httpx.Client | None = None,
    ):
        self._secret = shared_secret
        self._client = http_client or httpx.Client(base_url=base_url, timeout=timeout)

    def _signed_headers(self, *, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = str(time.time())
        signature = signing.sign(
            secret=self._secret, method=method, path=path, timestamp=timestamp, body=body,
        )
        return {signing.HEADER_TIMESTAMP: timestamp, signing.HEADER_SIGNATURE: signature}

    def _request(self, method: str, path: str, *, content: bytes = b"", params: dict | None = None) -> httpx.Response:
        headers = self._signed_headers(method=method, path=path, body=content)
        response = self._client.request(method, path, content=content, headers=headers, params=params)
        if response.status_code >= 300:
            raise VoiceHostRequestFailed(status_code=response.status_code, body=response.text)
        return response

    def create_session(self, bundle: SessionBundle) -> dict:
        body = bundle.model_dump_json().encode("utf-8")
        response = self._request("POST", "/sessions", content=body)
        return response.json()

    def poll_events(self, session_id: str, *, since: int) -> LiveEventsPollResponse:
        response = self._request(
            "GET", f"/sessions/{session_id}/events", params={"since": str(since)},
        )
        return LiveEventsPollResponse.model_validate_json(response.content)

    def fetch_recording(self, session_id: str) -> tuple[bytes, str]:
        response = self._request("GET", f"/sessions/{session_id}/recording")
        return response.content, response.headers["X-Recording-SHA256"]

    def notify_delete_artifacts(self, session_id: str) -> None:
        self._request("DELETE", f"/sessions/{session_id}/artifacts")
