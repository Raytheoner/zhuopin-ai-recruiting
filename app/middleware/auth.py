from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.storage.auth_session import resolve_session

SESSION_COOKIE_NAME = "hr_session"

# 未登录即 401 的路径前缀（resume-upload-and-gate spec「可识别到人的登录」）。
# 按 root_path 拼接后做前缀匹配。⛔ /api/jobs* 与静态资源不在这个列表——M1
# 的岗位画像流程本单元不改变可见性。
PROTECTED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/candidates",
    "/api/resumes",
    "/api/applications",
)


@dataclass
class AuthContext:
    """
    当前请求的鉴权上下文。
    企微 OAuth SSO 接入时，只替换 AuthMiddleware.dispatch 内部的解析逻辑，
    调用方（路由处理函数）读取 request.state.auth 的方式不变。
    """

    user_id: str | None
    authenticated: bool


UNKNOWN_REVIEWER = "unknown:web-session"


def reviewer_of(request: Request) -> str:
    """当前请求的决策人标识。SSO 落地后本函数不用改。"""
    auth = getattr(request.state, "auth", None)
    return getattr(auth, "user_id", None) or UNKNOWN_REVIEWER


class AuthMiddleware(BaseHTTPMiddleware):
    """
    鉴权中间件（design D12）：读会话 cookie → 查 hr_session/hr_account。
    `/candidates* /resumes* /applications*` 未登录一律 401，不返回任何数据。

    ⚠️ conn 是全应用共享的单连接（app/storage/db.py::get_connection），这里
    只做只读 SELECT，不与其它写路径的事务冲突。root_path 用于把
    PROTECTED_PATH_PREFIXES 的相对前缀换算成实际请求路径的前缀。
    """

    def __init__(self, app, *, conn: sqlite3.Connection, root_path: str = "") -> None:
        super().__init__(app)
        self._conn = conn
        self._root_path = root_path

    def _is_protected(self, path: str) -> bool:
        return any(
            path.startswith(f"{self._root_path}{prefix}")
            for prefix in PROTECTED_PATH_PREFIXES
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        username = resolve_session(self._conn, token) if token else None
        if username is not None:
            request.state.auth = AuthContext(user_id=username, authenticated=True)
        else:
            request.state.auth = AuthContext(user_id=None, authenticated=False)

        if self._is_protected(request.url.path) and username is None:
            return JSONResponse(status_code=401, content={"detail": "未登录"})

        return await call_next(request)
