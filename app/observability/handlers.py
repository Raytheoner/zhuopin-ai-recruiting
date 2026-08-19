from __future__ import annotations

import os
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_SUFFIX_GLOB = "*.log.*"


def purge_expired_logs(log_dir: Path, retention_days: int) -> list[Path]:
    """按文件 mtime 清理超过留存期的历史日志单元，返回被删掉的路径。

    幂等：判据是「文件时间早于阈值即删」，重复执行结果一致；文件已被别处
    删掉时吞掉 FileNotFoundError，不重复报错。

    用 mtime 而不是 TimedRotatingFileHandler 自带的 backupCount：本 handler
    在同一天内因大小上界二次轮转时会产出 app.log.2026-08-19.1 这种带序号的
    名字，stdlib 的 getFilesToDelete() 用 `^\\d{4}-\\d{2}-\\d{2}$` 匹配后缀，
    认不出带序号的单元，会把它们永远留下。所以 backupCount 置 0、自己算。
    """
    if retention_days <= 0:
        return []
    cutoff = time.time() - retention_days * 86400
    removed: list[Path] = []
    for path in sorted(log_dir.glob(LOG_SUFFIX_GLOB)):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except FileNotFoundError:
            continue
    return removed


class DailyRotatingFileHandler(TimedRotatingFileHandler):
    """按天轮转 + 单文件大小兜底 + 按 mtime 清理留存期。

    ⚠️ 单进程前提：计划任务里的 uvicorn 没有 --workers，全程一个进程。
    Windows 轮转要重命名当前文件，若有第二个进程持有该文件句柄会失败。
    **给 uvicorn 加 --workers 之前必须先更换轮转方案**（见 05-发布运行手册.md）。
    """

    def __init__(
        self,
        filename: str,
        *,
        retention_days: int = 30,
        max_bytes: int = 50 * 1024 * 1024,
        encoding: str = "utf-8",
    ) -> None:
        # backupCount=0：删除交给 purge_expired_logs()，理由见其 docstring。
        super().__init__(
            filename, when="midnight", backupCount=0, encoding=encoding, delay=False
        )
        self.retention_days = retention_days
        self.max_bytes = max_bytes

    def shouldRollover(self, record) -> bool:
        if super().shouldRollover(record):
            return True
        if self.max_bytes <= 0:
            return False
        if self.stream is None:
            self.stream = self._open()
        try:
            size = self.stream.tell()
        except (OSError, ValueError):
            return False
        msg = self.format(record) + self.terminator
        return size + len(msg.encode(self.encoding or "utf-8")) >= self.max_bytes

    def rotation_filename(self, default_name: str) -> str:
        """同一天内的第二次（大小触发）轮转要落到新名字，不能覆盖当天已有单元。

        stdlib 的 doRollover 在算出 dfn 后有一句 `if os.path.exists(dfn): return`
        （「Already rolled over」），直接放弃本次轮转 —— 那会让当天的日志无上界地
        继续写下去，正好违反「日志量超过配置上限」场景。这里保证返回的名字总是
        未被占用的，那条早退分支就永远走不到，也不会删掉上一段。
        """
        candidate = default_name
        index = 0
        while os.path.exists(candidate):
            index += 1
            candidate = f"{default_name}.{index}"
        return candidate

    def doRollover(self) -> None:
        super().doRollover()
        purge_expired_logs(Path(self.baseFilename).parent, self.retention_days)
