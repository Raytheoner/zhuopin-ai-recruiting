from __future__ import annotations

import logging
import uuid

from starlette.datastructures import MutableHeaders

from app.observability.context import REQUEST_ID_HEADER, request_id_var

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    """纯 ASGI 中间件：生成请求标识、写 contextvars、回写响应头。

    刻意不用 starlette 的 BaseHTTPMiddleware：它把下游 app 放进另一个 anyio
    任务里跑，contextvars 的设置无法可靠地传到 endpoint。纯 ASGI 中间件在同一个
    协程里 await 下游，传播是确定的（同步路由经 run_in_threadpool 也会复制上下文）。

    标识同时写进 scope["state"]：未捕获异常的 500 响应由更外层的
    ServerErrorMiddleware 生成，走的是它自己的 send、绕过本类的 send 包装，
    而那时 contextvar 已经被下面的 finally 复位了。unhandled_exception_handler
    从 request.state 取标识，不依赖 contextvar 的生命周期。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_header(message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)
