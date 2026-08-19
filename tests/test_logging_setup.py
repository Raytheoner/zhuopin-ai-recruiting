import logging
import os
import stat
import sys

import pytest

from app.observability.logging_config import logging_status, setup_logging


def test_logs_land_in_file_without_any_console(tmp_path, monkeypatch):
    """无控制台环境（计划任务以 SYSTEM 身份拉起，stdout 无处可去）下，
    日志仍必须落到持久化位置。"""
    monkeypatch.setattr(sys, "stdout", open(os.devnull, "w", encoding="utf-8"))
    log_dir = tmp_path / "logs"

    status = setup_logging(log_dir=str(log_dir), level="INFO", retention_days=30)

    logging.getLogger("app.storage.idempotency").error("rollback failed while cleaning up")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).info("hello from %s", name)
    logging.shutdown()

    log_file = log_dir / "app.log"
    assert status.degraded is False
    assert log_file.exists()
    text = log_file.read_text(encoding="utf-8")
    assert "rollback failed while cleaning up" in text
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert f"hello from {name}" in text, f"{name} 的输出没落进文件"


@pytest.mark.skipif(os.name == "nt", reason="Windows 上 chmod 不阻止 SYSTEM/管理员写入")
def test_unwritable_log_dir_degrades_instead_of_crashing(tmp_path):
    """日志目录不可写时不崩溃，且降级事实必须可被察觉。"""
    parent = tmp_path / "readonly"
    parent.mkdir()
    parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        status = setup_logging(log_dir=str(parent / "logs"), level="INFO")
        assert status.degraded is True
        assert status.reason
        assert status.log_file is None
        assert logging_status().degraded is True
        logging.getLogger("app").info("业务仍然可以打日志，不抛异常")
    finally:
        parent.chmod(stat.S_IRWXU)
