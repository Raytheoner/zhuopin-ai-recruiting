import logging
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.context import REQUEST_ID_HEADER
from app.observability.logging_config import setup_logging
from app.observability.middleware import RequestIdMiddleware, unhandled_exception_handler
from app.storage.db import get_connection, init_schema
from app.storage.idempotency import idempotent_effect


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs"
    setup_logging(log_dir=str(path), level="INFO", retention_days=30)
    yield path / "app.log"
    logging.shutdown()


def _boom_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom/{job_id}")
    def boom(job_id: str):
        raise RuntimeError("kaboom")

    return app


def test_server_error_leaves_request_id_exception_type_and_stack(log_file):
    client = TestClient(_boom_app(), raise_server_exceptions=False)
    resp = client.get("/boom/J-ERR")

    assert resp.status_code == 500
    request_id = resp.headers[REQUEST_ID_HEADER]

    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    error_lines = [line for line in text.splitlines() if "未捕获异常导致服务端错误" in line]
    assert len(error_lines) == 1, f"未捕获异常没有留下恰好一条错误日志：{error_lines}"

    line = error_lines[0]
    assert request_id in line, "错误日志里没有请求标识，无法对应到用户报告的那一次"
    assert "'job_id': 'J-ERR'" in line, "错误日志里没有业务会话标识，还得查库反推"
    assert "RuntimeError" in text and "kaboom" in text, "缺异常类型"
    assert "Traceback (most recent call last)" in text, "缺完整调用栈"
    assert "raise RuntimeError" in text, "调用栈没到抛出点"


def test_idempotency_rollback_alert_reaches_the_persistent_log(log_file, tmp_path):
    """findings 第 8.3 节：这条 logger.error 是本变更的直接触发原因——
    修复给它加了兜底告警，而现网零日志让它什么都不会留下。"""
    conn = get_connection(str(tmp_path / "probe.db"))
    init_schema(conn)

    class RollbackExplodes:
        def __init__(self, real): self._real = real
        def __getattr__(self, name): return getattr(self._real, name)
        def rollback(self): raise sqlite3.OperationalError("rollback boom")

    @idempotent_effect("effect_probe")
    def effect_probe(conn, *, thread_id, business_key):
        raise RuntimeError("business write exploded")

    with pytest.raises(RuntimeError, match="business write exploded"):
        effect_probe(RollbackExplodes(conn), thread_id="T1", business_key="B1")

    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    assert "rollback failed" in text, "兜底告警没有落进持久化日志"
    assert "T1:effect_probe:B1" in text, "告警里没有幂等键，无法定位是哪个 effect"
