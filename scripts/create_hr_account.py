# scripts/create_hr_account.py
"""建 / 更新一个 HR 本地账号（tasks 2.8）。

幂等：同用户名重复运行只更新口令并提示，不产生第二条账号记录。

用法：
    python -m scripts.create_hr_account --username tangliping
    python -m scripts.create_hr_account --username tangliping --password 'xxx'  # 非交互场景

不传 --password 时用 getpass 交互输入，避免口令出现在 shell 历史与进程列表里。
"""
from __future__ import annotations

import argparse
import getpass

from app.config import get_settings
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--password",
        default=None,
        help="不传则交互输入（推荐）；仅供非交互脚本化场景使用",
    )
    parser.add_argument("--db-path", default=None, help="默认读 Settings.db_path")
    args = parser.parse_args()

    # 归一化必须与 upsert_account 内部的 strip 口径一致，否则带首尾空白的
    # 用户名会在这里查不到既有账号、却在 upsert_account 里更新了它。
    username = args.username.strip()

    password = args.password or getpass.getpass(f"为 {username} 设置口令: ")

    db_path = args.db_path or get_settings().db_path
    conn = get_connection(db_path)
    init_schema(conn)

    existing = conn.execute(
        "SELECT 1 FROM hr_account WHERE username = ?", (username,)
    ).fetchone()

    account_id = upsert_account(conn, username=username, password=password)

    if existing:
        print(f"已更新账号 {username}（id={account_id}）的口令。")
    else:
        print(f"已创建账号 {username}（id={account_id}）。")


if __name__ == "__main__":
    main()
