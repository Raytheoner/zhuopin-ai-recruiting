from __future__ import annotations

import logging
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.observability.context import REQUEST_ID_HEADER, UNSET_REQUEST_ID, request_id_var

logger = logging.getLogger(__name__)

# 业务会话标识在路由里的参数名。Starlette 在把请求交给 endpoint 之前就把
# path_params 写进了 scope，所以即使 endpoint 抛异常也依然读得到是哪个会话
# 出的问题——不需要另行查库反推（spec 明确要求这一点）。
SESSION_PARAM_NAMES = ("job_id", "thread_id")


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


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常的统一记录点，挂在 FastAPI 的 Exception handler 上。

    挂这里而不是挂在中间件的 except 分支里，是因为 ServerErrorMiddleware 位于
    全部用户中间件之外：它捕获异常后用自己的 send 发 500，中间件加的响应头到
    不了那个响应上。而「使用者报告问题时可以提供标识」要救的恰恰是出错这一次。

    注意 request_id 必须显式经 extra 传入：此刻 contextvar 已被中间件的 finally
    复位，RequestIdFilter 只在 record 没有该属性时才回填，extra 优先。
    """
    request_id = getattr(request.state, "request_id", UNSET_REQUEST_ID)
    params = request.scope.get("path_params") or {}
    session = {k: params[k] for k in SESSION_PARAM_NAMES if k in params}
    logger.error(
        "未捕获异常导致服务端错误：method=%s path=%s session=%s",
        request.method,
        request.url.path,
        session or "<无会话上下文>",
        exc_info=exc,
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id},
    )
