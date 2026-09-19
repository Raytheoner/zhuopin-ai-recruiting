"""两机接口签名——本文件是 app/live_voice/signing.py 的部署期拷贝（本计划
「设计决策 2」）。⛔ 不要在这个文件里手改逻辑：`sync-to-voice-host.sh`
（Task 14）每次部署都会用 `.51` 仓库里的最新版本覆盖它；`tests/test_voice_
host_no_forbidden_imports.py` 的漂移测试会在两份内容不一致时报警，提醒你
改的是错误的文件。"""
from __future__ import annotations

import hashlib
import hmac
import time

HEADER_TIMESTAMP = "X-ZP-Timestamp"
HEADER_SIGNATURE = "X-ZP-Signature"
TIMESTAMP_SKEW_SECONDS = 60


def canonical_string(*, method: str, path: str, timestamp: str, body: bytes) -> str:
    body_sha256 = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{body_sha256}"


def sign(*, secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    message = canonical_string(method=method, path=path, timestamp=timestamp, body=body)
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


class SignatureInvalidError(Exception):
    """签名不匹配、时间戳格式非法、或时间戳超出 ±60s 窗口（防重放）。三种
    原因统一成一个异常类型——调用方（Task 9 的 FastAPI 依赖）对三者的处理
    完全一样：拒绝请求、不泄露具体是哪种原因（避免帮攻击者调试签名）。"""


def verify(
    *, secret: str, method: str, path: str, timestamp: str, body: bytes, signature: str,
    now: float | None = None,
) -> None:
    now_value = time.time() if now is None else now
    try:
        ts_value = float(timestamp)
    except ValueError as exc:
        raise SignatureInvalidError(f"时间戳格式非法: {timestamp!r}") from exc

    if abs(now_value - ts_value) > TIMESTAMP_SKEW_SECONDS:
        raise SignatureInvalidError(f"时间戳超出 ±{TIMESTAMP_SKEW_SECONDS}s 窗口: {timestamp!r}")

    expected = sign(secret=secret, method=method, path=path, timestamp=timestamp, body=body)
    if not hmac.compare_digest(expected, signature):
        raise SignatureInvalidError("签名不匹配")
