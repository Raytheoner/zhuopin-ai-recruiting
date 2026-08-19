from __future__ import annotations

import contextvars
import logging

REQUEST_ID_HEADER = "X-Request-ID"
UNSET_REQUEST_ID = "-"

# contextvars 而不是 thread-local：异步路由全部跑在同一个事件循环线程上，
# thread-local 会让并发的异步请求互相覆盖标识（spec「并发请求互不串扰」）。
# contextvars 在 asyncio 任务与 Starlette 派发同步路由用的 run_in_threadpool
# 两侧都能正确复制上下文。
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=UNSET_REQUEST_ID
)


def current_request_id() -> str:
    return request_id_var.get()


class RequestIdFilter(logging.Filter):
    """给每条 record 注入 request_id，使格式串可以无条件引用 %(request_id)s。

    挂在 handler 而不是 logger 上：handler 能看到所有路由到它的 record，
    包括 uvicorn 与第三方库的——业务代码里已有的 logger.* 调用点一行都不用改。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True
