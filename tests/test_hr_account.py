"""hr_account 的口令哈希与幂等建账号（tasks 2.8）。

密码用 PBKDF2-HMAC-SHA256 加盐哈希，标准库实现，⛔ 不引入 bcrypt/argon2 等
新依赖——.51 是 Windows 无 Docker 环境，新依赖必须先冒烟（design.md「外部依赖
现状」），登录这种非评测热路径的功能没有必要为此扩大依赖面。
"""
import hashlib
import os
import subprocess
import sqlite3
import sys
from pathlib import Path

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.hr_account import hash_password, upsert_account, verify_password


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "acct.db"))
    init_schema(c)
    return c


def test_hash_password_generates_new_salt_when_not_given():
    h1, s1 = hash_password("correct horse battery staple")
    h2, s2 = hash_password("correct horse battery staple")
    assert s1 != s2, "两次不传 salt 应该生成不同的随机盐"
    assert h1 != h2, "不同盐下同一口令的哈希必须不同"


def test_hash_password_deterministic_given_same_salt():
    h1, salt = hash_password("hunter2")
    h2, _ = hash_password("hunter2", salt=salt)
    assert h1 == h2


def test_verify_password_accepts_correct_password():
    password_hash, salt = hash_password("hunter2")
    assert verify_password("hunter2", password_hash, salt) is True


def test_verify_password_rejects_wrong_password():
    password_hash, salt = hash_password("hunter2")
    assert verify_password("wrong-password", password_hash, salt) is False


def test_upsert_account_creates_new_account(conn):
    account_id = upsert_account(conn, username="tangliping", password="initial-pw")

    row = conn.execute(
        "SELECT id, username, password_hash, password_salt FROM hr_account WHERE username='tangliping'"
    ).fetchone()
    assert row[0] == account_id
    assert verify_password("initial-pw", row[2], row[3]) is True


def test_upsert_account_is_idempotent_on_username_and_only_updates_password(conn):
    first_id = upsert_account(conn, username="tangliping", password="pw-1")
    second_id = upsert_account(conn, username="tangliping", password="pw-2")

    assert first_id == second_id
    count = conn.execute(
        "SELECT COUNT(*) FROM hr_account WHERE username='tangliping'"
    ).fetchone()[0]
    assert count == 1

    row = conn.execute(
        "SELECT password_hash, password_salt FROM hr_account WHERE id=?", (first_id,)
    ).fetchone()
    assert verify_password("pw-2", row[0], row[1]) is True
    assert verify_password("pw-1", row[0], row[1]) is False


def test_upsert_account_rejects_blank_username(conn):
    with pytest.raises(ValueError, match="用户名"):
        upsert_account(conn, username="  ", password="pw")


def test_upsert_account_rejects_blank_password(conn):
    with pytest.raises(ValueError, match="口令"):
        upsert_account(conn, username="someone", password="")


def test_hash_password_output_is_self_describing():
    password_hash, _ = hash_password("hunter2")
    assert password_hash.startswith("pbkdf2_sha256$600000$")


def test_verify_password_accepts_hash_with_different_iteration_count():
    salt = "00" * 16
    old_digest = hashlib.pbkdf2_hmac(
        "sha256", b"pw", bytes.fromhex(salt), 1000
    ).hex()
    old_style_hash = f"pbkdf2_sha256$1000${old_digest}"
    assert verify_password("pw", old_style_hash, salt) is True


@pytest.mark.parametrize(
    "malformed_hash",
    ["deadbeef", "pbkdf2_sha256$abc$00", "md5$1$00"],
)
def test_verify_password_rejects_malformed_stored_hash_without_raising(
    malformed_hash,
):
    assert verify_password("pw", malformed_hash, "00" * 16) is False


def test_create_hr_account_cli_create_then_update_with_padded_username(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "cli.db"

    def run_cli(username: str, password: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.create_hr_account",
                "--username",
                username,
                "--password",
                password,
                "--db-path",
                str(db_path),
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env={**os.environ},
            check=True,
        )

    created = run_cli("smoke", "pw-1")
    assert "已创建账号 smoke" in created.stdout

    updated = run_cli("smoke", "pw-2")
    assert "已更新账号 smoke" in updated.stdout

    padded_updated = run_cli(" smoke ", "pw-3")
    assert "已更新账号 smoke" in padded_updated.stdout

    db_conn = sqlite3.connect(str(db_path))
    try:
        count = db_conn.execute(
            "SELECT COUNT(*) FROM hr_account WHERE username='smoke'"
        ).fetchone()[0]
        assert count == 1

        password_hash, salt = db_conn.execute(
            "SELECT password_hash, password_salt FROM hr_account WHERE username='smoke'"
        ).fetchone()
    finally:
        db_conn.close()

    assert verify_password("pw-3", password_hash, salt) is True
