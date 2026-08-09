from __future__ import annotations

from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


@dataclass
class AuthContext:
    """
    当前请求的鉴权上下文。demo 阶段恒为未鉴权直通（user_id=None）。
    企微 OAuth SSO 接入时，只替换 AuthMiddleware.dispatch 内部的解析逻辑，
    调用方（路由处理函数）读取 request.state.auth 的方式不变。
    """

    user_id: str | None
    authenticated: bool


class AuthMiddleware(BaseHTTPMiddleware):
    """
    鉴权中间件空壳接入点（部署约束 3）。demo 阶段不校验、不拒绝任何请求，
    无条件放行；user_id 恒为 None。签名对齐未来企微 OAuth SSO：
    真实实现落地时只替换 dispatch 内部逻辑，路由处理函数读取
    request.state.auth 的方式保持不变。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.auth = AuthContext(user_id=None, authenticated=False)
        return await call_next(request)
