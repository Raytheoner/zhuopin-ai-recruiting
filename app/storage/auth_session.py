"""本地账号会话（design D12）。token 本身即会话主键，⛔ 不做滑动续期——
固定 TTL，到期后 resolve_session 返回 None，前端收到 401 后引导重新登录。
"""
from __future__ import annotations

import secrets
import sqlite3

from app.storage.db import sqlite_utc_now

SESSION_TTL_SECONDS = 12 * 3600  # 12 小时，一个 HR 的正常工作时段


def create_session(conn: sqlite3.Connection, *, hr_account_id: str) -> str:
    token = secrets.token_urlsafe(32)
    # SQLite modifier 语法要求正数显式带 "+"、负数自带 "-"（"+-1 seconds" 这种
    # 双符号写法会被 datetime() 判为非法 modifier 并静默返回 NULL，进而在这里
    # 撞 NOT NULL 约束）。{:+d} 一次性把两种符号都写对：
    # SESSION_TTL_SECONDS=-1（测试用 monkeypatch 制造"立刻过期"）→ "-1 seconds"；
    # 正常值 43200 → "+43200 seconds"。
    modifier = f"{SESSION_TTL_SECONDS:+d} seconds"
    conn.execute(
        "INSERT INTO hr_session (id, hr_account_id, expires_at) "
        "VALUES (?, ?, datetime('now', ?))",
        (token, hr_account_id, modifier),
    )
    conn.commit()
    return token


def resolve_session(conn: sqlite3.Connection, token: str) -> str | None:
    """返回该会话对应的用户名；不存在或已过期返回 None。

    过期判断用字符串比较——sqlite_utc_now() 与 SQLite datetime('now') 产出
    的格式（"YYYY-MM-DD HH:MM:SS"）逐位可比较，这与仓库里其它时刻比较的
    既有约定一致。
    """
    row = conn.execute(
        "SELECT hr_account.username, hr_session.expires_at "
        "FROM hr_session JOIN hr_account ON hr_account.id = hr_session.hr_account_id "
        "WHERE hr_session.id = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    username, expires_at = row
    if expires_at <= sqlite_utc_now():
        return None
    return username


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM hr_session WHERE id = ?", (token,))
    conn.commit()
