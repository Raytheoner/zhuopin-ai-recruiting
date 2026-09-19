"""真实候选人开闸（voice-structured-interview design D14，tasks 4.9）。

与 app/storage/live_resume_gate.py 同一口径：默认关、每次求值、⛔ 不缓存、
⛔ 不抛出任何异常。本闸多一个 AND 前置——合规验收 #2 签认文件存在，这是
"法务复核未完成前真实候选人开闸在结构上不可能打开"的落点。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ENV_VAR = "LIVE_INTERVIEW_ENABLED"


def is_live_interview_enabled() -> bool:
    """真实候选人开闸求值。⛔ 绝不抛出任何异常——任何一步出错，结果都是 False。"""
    try:
        return _evaluate()
    except Exception:
        logger.exception("真实候选人开闸求值过程出错，按关闭处理")
        return False


def _evaluate() -> bool:
    if not _base_switch():
        return False
    return _compliance_signoff_exists()


def _base_switch() -> bool:
    raw_env = os.environ.get(_ENV_VAR)
    if raw_env is not None:
        return raw_env.strip().lower() in _TRUTHY
    try:
        settings = get_settings()
    except Exception:
        return False
    return settings.live_interview_enabled


def _compliance_signoff_exists() -> bool:
    try:
        settings = get_settings()
        path = Path(settings.m3_compliance_signoff_path)
    except Exception:
        return False
    return path.is_file()
