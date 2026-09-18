import logging

import pytest
from fastapi.testclient import TestClient

from app.llm.gateway import LLMGateway
from app.observability.logging_config import UVICORN_LOGGERS
from app.storage.db import get_connection
from app.web.server import create_app


@pytest.fixture
def make_test_client(tmp_path):
    """⚠️ resume_storage_dir 显式指到 tmp_path 下——不传的话会落到
    Settings.resume_storage_dir 的默认值 "data/resumes"（相对 pytest 的运行
    目录），把测试上传的文件真的写进仓库工作区。

    gateway_factory 返回一个"不会被真正调用"的 LLMGateway：create_app() 在
    构造阶段就会调用一次 gateway_factory() 来装配图（app/web/server.py
    "gateway = gateway_factory()"），所以这里不能像别处那样用抛异常的桩，
    只能给一个不连真实网络的 client 占位（client=object()）——Task 7 起的
    用例都会 monkeypatch 掉 app.web.server.compute_parse 本身，不会真的走到
    gateway 内部去发请求。
    """

    def _make():
        db_path = str(tmp_path / "test.db")
        resume_dir = str(tmp_path / "resumes")

        def _gateway_factory():
            return LLMGateway(
                api_key="test", base_url="https://example.invalid",
                model="deepseek-chat", supports_json_schema=False, client=object(),
            )

        app = create_app(
            db_path=db_path, gateway_factory=_gateway_factory, root_path="",
            resume_storage_dir=resume_dir,
        )
        client = TestClient(app)
        conn = get_connection(db_path)
        return client, conn

    return _make


@pytest.fixture(autouse=True)
def _release_log_file_handles():
    """每个用例结束后关闭并摘掉指向文件的 handler。

    setup_logging() 会把文件 handler 挂到 root 与 uvicorn 三个 logger 上，测试
    里指向的是 tmp_path。**Windows 删不掉仍有打开句柄的文件**，句柄不释放会让
    pytest 的 tmp 目录清理失败——这个故障在 macOS/Linux 上永远不现形，只会在
    CI 的 windows-latest runner 上炸（跟 SQLite 事务冲突那次是同一类教训）。
    """
    yield
    targets = [logging.getLogger()] + [logging.getLogger(n) for n in UVICORN_LOGGERS]
    for logger in targets:
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                logger.removeHandler(handler)
