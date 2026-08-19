import asyncio
import logging

from app.observability.context import (
    UNSET_REQUEST_ID,
    RequestIdFilter,
    current_request_id,
    request_id_var,
)


def _record() -> logging.LogRecord:
    return logging.LogRecord("probe", logging.INFO, __file__, 1, "msg", None, None)


def test_filter_injects_current_request_id():
    token = request_id_var.set("abc123")
    try:
        record = _record()
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "abc123"
    finally:
        request_id_var.reset(token)


def test_filter_defaults_when_outside_any_request():
    record = _record()
    RequestIdFilter().filter(record)
    assert record.request_id == UNSET_REQUEST_ID
    assert current_request_id() == UNSET_REQUEST_ID


def test_filter_never_overwrites_an_explicitly_supplied_id():
    """Task 6 的异常处理器用 extra={"request_id": ...} 显式传值——那时 contextvar
    已被中间件的 finally 复位，若这里覆盖就会把错误日志的标识抹成 '-'。"""
    token = request_id_var.set("from-contextvar")
    try:
        record = _record()
        record.request_id = "from-extra"
        RequestIdFilter().filter(record)
        assert record.request_id == "from-extra"
    finally:
        request_id_var.reset(token)


def test_contextvar_isolates_concurrent_async_tasks():
    """异步路由全部跑在同一个事件循环线程上——thread-local 会在这里串号。"""

    async def one(value: str) -> str:
        request_id_var.set(value)
        await asyncio.sleep(0.01)
        return current_request_id()

    async def drive():
        return await asyncio.gather(*(one(f"id-{i}") for i in range(8)))

    assert asyncio.run(drive()) == [f"id-{i}" for i in range(8)]
