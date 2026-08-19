import logging

import pytest

from app.observability.logging_config import UVICORN_LOGGERS


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
