from __future__ import annotations

import logging
import logging.config
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.observability.context import RequestIdFilter
from app.observability.handlers import DailyRotatingFileHandler, purge_expired_logs

LOG_FILENAME = "app.log"
LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"

# uvicorn 的三个 logger 必须一并接管：--log-config 只管得到它们，管不到
# app.storage.idempotency 那条 logger.error——而这次事故要救的恰恰是应用侧。
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@dataclass
class LoggingStatus:
    """日志子系统的当前状态，供健康检查端点读取。"""

    configured: bool = False
    degraded: bool = False
    reason: str | None = None
    log_file: str | None = None
    handlers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "configured": self.configured,
            "degraded": self.degraded,
            "reason": self.reason,
            "log_file": self.log_file,
            "handlers": list(self.handlers),
        }


_status = LoggingStatus()


def logging_status() -> LoggingStatus:
    return _status


def _probe_writable(log_dir: Path) -> str | None:
    """返回 None 表示可写，否则返回不可写的原因（人类可读）。"""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def setup_logging(
    *,
    log_dir: str,
    level: str = "INFO",
    retention_days: int = 30,
    max_bytes: int = 50 * 1024 * 1024,
) -> LoggingStatus:
    """进程启动时调用一次，统一配置根 logger、应用 logger 与 uvicorn 三个 logger。

    日志目录不可写时**不崩溃、不阻断业务功能**：退回只有 stdout 的配置，并把
    降级事实记进 LoggingStatus 供 /health 暴露——在一个没有控制台、日志本身
    又坏了的进程里，健康检查端点是唯一还能对外说话的通道。
    """
    global _status

    directory = Path(log_dir).expanduser()
    reason = _probe_writable(directory)
    log_path = directory / LOG_FILENAME

    handlers: dict[str, dict] = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "standard",
            "filters": ["request_id"],
            "level": level,
        }
    }
    if reason is None:
        handlers["file"] = {
            "()": DailyRotatingFileHandler,
            "filename": str(log_path),
            "retention_days": retention_days,
            "max_bytes": max_bytes,
            "encoding": "utf-8",
            "formatter": "standard",
            "filters": ["request_id"],
            "level": level,
        }

    handler_names = list(handlers)
    logging.config.dictConfig(
        {
            "version": 1,
            # uvicorn 自己的 dictConfig 也是 False；置 True 会把先于本次配置
            # 创建出来的模块级 logger（如 app.storage.idempotency）整个关掉。
            "disable_existing_loggers": False,
            "filters": {
                "request_id": {"()": RequestIdFilter},
            },
            "formatters": {"standard": {"format": LOG_FORMAT}},
            "handlers": handlers,
            "root": {"level": level, "handlers": handler_names},
            "loggers": {
                name: {"level": level, "handlers": handler_names, "propagate": False}
                for name in UVICORN_LOGGERS
            },
        }
    )

    if reason is None:
        purge_expired_logs(directory, retention_days)

    _status = LoggingStatus(
        configured=True,
        degraded=reason is not None,
        reason=reason,
        log_file=str(log_path) if reason is None else None,
        handlers=handler_names,
    )

    if _status.degraded:
        # 这一条只能落到 stdout（文件通道正是坏掉的那个），但它必须被记录：
        # spec 要求 MUST NOT 静默降级为「什么都不记录」。
        logging.getLogger(__name__).error(
            "日志文件通道不可用，已降级为仅 stdout：目录=%s 原因=%s。"
            "业务功能不受影响，但排障证据不会落盘——请检查该目录的存在性与写权限",
            directory,
            reason,
        )
    return _status
