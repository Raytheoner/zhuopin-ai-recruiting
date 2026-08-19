import logging
import os
import time

from app.observability.handlers import DailyRotatingFileHandler, purge_expired_logs


def test_rotation_keeps_old_unit_and_loses_no_line(tmp_path):
    """大小上界触发轮转：旧单元保留为独立文件、新单元继续记录、无行丢失。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    handler = DailyRotatingFileHandler(
        str(log_dir / "app.log"), retention_days=30, max_bytes=2048
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("rotation-probe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    total = 200
    for i in range(total):
        logger.info("line-%03d %s", i, "x" * 60)
    handler.close()

    units = sorted(log_dir.glob("app.log*"))
    assert len(units) > 1, f"大小上界没有触发轮转，只有 {units}"

    seen = []
    for unit in units:
        seen.extend(
            line for line in unit.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    assert len(seen) == total, f"轮转丢了行：期望 {total} 实得 {len(seen)}"
    assert {f"line-{i:03d}" for i in range(total)} == {line.split()[0] for line in seen}


def test_same_day_rotation_does_not_overwrite_previous_unit(tmp_path):
    """同一天内二次轮转必须落到带序号的新名字，不能覆盖当天已有单元。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    handler = DailyRotatingFileHandler(
        str(log_dir / "app.log"), retention_days=30, max_bytes=512
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("same-day-probe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    for i in range(60):
        logger.info("payload-%02d %s", i, "y" * 40)
    handler.close()

    rotated = sorted(p for p in log_dir.glob("app.log.*"))
    assert len(rotated) >= 2, f"同日多次轮转没有产出多个单元：{rotated}"
    assert any(p.name.endswith(".1") for p in rotated), f"没有带序号的单元：{rotated}"


def test_retention_purge_removes_only_expired_units(tmp_path):
    """超期单元被清理、未超期的保留；重复执行结果一致（幂等）。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old = log_dir / "app.log.2026-06-01"
    fresh = log_dir / "app.log.2026-08-18"
    current = log_dir / "app.log"
    for p in (old, fresh, current):
        p.write_text("x\n", encoding="utf-8")

    ancient = time.time() - 40 * 86400
    os.utime(old, (ancient, ancient))

    removed = purge_expired_logs(log_dir, retention_days=30)
    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()
    assert current.exists(), "当前正在写入的 app.log 不带日期后缀，不应被清理"

    assert purge_expired_logs(log_dir, retention_days=30) == []
