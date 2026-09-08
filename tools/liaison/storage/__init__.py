"""HR 值守通道服务的存储层：独立 `data/liaison.db`。

⛔ 不与 `data/demo.db` 混库（design D5）。两个常驻进程写同一个 SQLite 文件 =
写锁竞争 + 间歇性的 `database is locked`。

复用方向**单向**：本包可以 `import app.storage.idempotency`，
⛔ `app/` 下任何模块不得 import `tools/`（由 test_app_does_not_import_tools.py 守着）。
"""
