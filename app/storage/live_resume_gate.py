"""真实简历入库闸（resume-upload-and-gate spec「真实简历入库闸」，design D2）。

与 app/config.py::is_candidate_outbound_enabled() 同一口径：默认关、每次求值、
⛔ 不缓存。唯一区别是本闸多两个结构性前置（登录身份可识别 + 访问留痕已启用），
这两条把部署约束 5 变成代码而不是流程——"登录没换成真实身份就开闸"在结构上
不可能发生。
"""
from __future__ import annotations

import logging
import os
import sqlite3

from app.config import get_settings
from app.middleware.auth import AuthContext

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ENV_VAR = "LIVE_RESUME_INTAKE_ENABLED"
_PROBE_MARKER = "00000000-0000-0000-0000-live-gate"


def is_live_resume_intake_enabled(*, auth: AuthContext, conn: sqlite3.Connection) -> bool:
    """真实简历入库闸求值。⛔ 绝不抛出任何异常——任何一步出错，结果都是 False。"""
    try:
        return _evaluate(auth=auth, conn=conn)
    except Exception:
        logger.exception("真实简历入库闸求值过程出错，按关闭处理")
        return False


def _evaluate(*, auth: AuthContext, conn: sqlite3.Connection) -> bool:
    base = _base_switch()
    if not base:
        return False
    if not _identity_recognizable(auth):
        return False
    return _access_log_probe(conn)


def _base_switch() -> bool:
    """优先级：环境变量 > Settings 基线值。⛔ 不读 lru_cache 的 get_settings()
    结果去判断环境变量——环境变量必须每次读 os.environ，理由与
    is_candidate_outbound_enabled() 完全一致。"""
    raw_env = os.environ.get(_ENV_VAR)
    if raw_env is not None:
        return raw_env.strip().lower() in _TRUTHY
    try:
        settings = get_settings()
    except Exception:
        return False
    return settings.live_resume_intake_enabled


def _identity_recognizable(auth: AuthContext | None) -> bool:
    if auth is None:
        return False
    if not auth.authenticated:
        return False
    user_id = auth.user_id
    if not user_id:
        return False
    return not user_id.startswith("unknown:")


def _access_log_probe(conn: sqlite3.Connection) -> bool:
    """在一个 SAVEPOINT 里试写一行 resume_access_log 并回滚，探测表存在且可写。

    ⛔ 不用 sqlite_master 查表名了事：那只能证明表存在，证不了这条连接现在
    真的能写（磁盘满、只读文件系统这类失败查表名看不出来）。
    """
    try:
        conn.execute("SAVEPOINT live_gate_probe")
        conn.execute(
            "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
            "VALUES (?, ?, ?, ?)",
            (_PROBE_MARKER, "probe:live-gate", "probe", "raw_text"),
        )
        conn.execute("ROLLBACK TO live_gate_probe")
        conn.execute("RELEASE live_gate_probe")
        return True
    except Exception:
        try:
            conn.execute("ROLLBACK TO live_gate_probe")
        except Exception:
            pass
        return False
