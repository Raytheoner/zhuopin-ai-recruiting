"""hr_account 口令哈希与幂等建账号（design D12：鉴权从空壳换成本地账号）。

PBKDF2-HMAC-SHA256，600,000 次迭代（OWASP 2023 Password Storage Cheat Sheet 对
PBKDF2-HMAC-SHA256 的推荐值），标准库实现——
⛔ 不引入 bcrypt/argon2：.51 是 Windows 无 Docker 环境，新依赖必须先在
Windows 上冒烟（design.md「外部依赖现状」），登录这种非评测热路径的功能
没有必要为此扩大依赖面。

`password_hash` 落库格式自描述为 `pbkdf2_sha256$<iterations>$<hexdigest>`
（`salt` 仍单独一列存十六进制字符串，格式不变）：迭代次数写进哈希本身而不是
只依赖模块常量，未来提高 `_PBKDF2_ITERATIONS` 时，老账号仍能用哈希里记录的
旧迭代次数校验通过，不需要强制重置口令。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid

_PBKDF2_ITERATIONS = 600_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    password_hash = f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${digest.hex()}"
    return password_hash, salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 3:
        return False
    algorithm, iterations_str, hex_digest = parts
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iterations_str)
    except ValueError:
        return False
    if iterations <= 0:
        return False
    try:
        salt_bytes = bytes.fromhex(salt)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, iterations
    )
    return hmac.compare_digest(candidate.hex(), hex_digest)


def upsert_account(conn: sqlite3.Connection, *, username: str, password: str) -> str:
    """幂等建账号：同用户名重复调用只更新口令，返回既有 id；用户名不存在则新建。

    ⛔ 不做任何 LangGraph 幂等键接入——这是运维脚本的一次性写入，不经
    effect_* 节点、不在 checkpointer 恢复路径上（工程铁律 1 的适用范围是
    图节点的副作用，不是运维 CLI）。"""
    username = username.strip()
    if not username:
        raise ValueError("用户名不能为空")
    if not password:
        raise ValueError("口令不能为空")

    existing = conn.execute(
        "SELECT id FROM hr_account WHERE username = ?", (username,)
    ).fetchone()
    password_hash, salt = hash_password(password)

    if existing:
        account_id = existing[0]
        conn.execute(
            "UPDATE hr_account SET password_hash = ?, password_salt = ? WHERE id = ?",
            (password_hash, salt, account_id),
        )
    else:
        account_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO hr_account (id, username, password_hash, password_salt) "
            "VALUES (?, ?, ?, ?)",
            (account_id, username, password_hash, salt),
        )
    conn.commit()
    return account_id
