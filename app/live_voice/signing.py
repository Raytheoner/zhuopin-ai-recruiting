"""两机接口签名（voice-structured-interview U4 tasks 5.2，design D12；本计划
「设计决策 3」）。

`.51` 出站请求与语音主机的每一次交互都用预共享密钥 + HMAC-SHA256 签名 +
时间戳防重放（±60s）。本文件是签名算法的唯一源码——`.51` 侧
`app/live_voice/client.py`（Task 4）签名请求，语音主机侧 `voice_host/api.py`
（Task 9）验证请求，两边用的是同一份被 `sync-to-voice-host.sh`（Task 14）
物理拷贝过去的代码，不是"两边各自实现一遍"，不会出现算法漂移。
"""
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
