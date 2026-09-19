"""app/live_voice/signing.py：两机接口的 HMAC 签名与 ±60s 防重放窗口
（voice-structured-interview U4 tasks 5.2，本计划「设计决策 3」）。"""
import pytest

from app.live_voice.signing import (
    TIMESTAMP_SKEW_SECONDS,
    SignatureInvalidError,
    sign,
    verify,
)

SECRET = "test-shared-secret"


def test_valid_signature_verifies():
    ts = "1758240000.0"
    body = b'{"session_id": "s1"}'
    signature = sign(secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=body)
    verify(
        secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=body,
        signature=signature, now=1758240000.0,
    )


def test_tampered_body_fails():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=b"original")
    with pytest.raises(SignatureInvalidError):
        verify(
            secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=b"tampered",
            signature=signature, now=1758240000.0,
        )


def test_wrong_secret_fails():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=b"body")
    with pytest.raises(SignatureInvalidError):
        verify(
            secret="another-secret", method="POST", path="/sessions", timestamp=ts, body=b"body",
            signature=signature, now=1758240000.0,
        )


def test_stale_timestamp_outside_skew_window_fails():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"")
    stale_now = 1758240000.0 + TIMESTAMP_SKEW_SECONDS + 1
    with pytest.raises(SignatureInvalidError):
        verify(
            secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"",
            signature=signature, now=stale_now,
        )


def test_timestamp_at_exact_skew_boundary_verifies():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"")
    boundary_now = 1758240000.0 + TIMESTAMP_SKEW_SECONDS
    verify(
        secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"",
        signature=signature, now=boundary_now,
    )


def test_malformed_timestamp_fails():
    with pytest.raises(SignatureInvalidError):
        verify(
            secret=SECRET, method="GET", path="/sessions/s1/events", timestamp="not-a-number",
            body=b"", signature="whatever", now=1758240000.0,
        )
