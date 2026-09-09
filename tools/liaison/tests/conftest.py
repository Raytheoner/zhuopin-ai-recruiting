"""`tools/liaison/tests` 的公共夹具。

**为什么必须有这个文件**：8.4 把 `setup_logging()` 接进了 `main()`，而现存的一批
`test_main_*` 用例是真的会调 `liaison_main.main()` 的。没有下面这条 autouse
fixture，本机跑一次 pytest 就会在 `data/liaison/logs/` 下拉出真实日志文件——
`data/` 虽在 `.gitignore` 里不会被提交，但「日志是个人信息的第二份拷贝」
（`.gitignore:17` 原话），让它在开发机上无声堆积不是可以接受的默认。
"""

from __future__ import annotations

import pytest

from tools.liaison import logsetup


@pytest.fixture(autouse=True)
def liaison_logs_to_tmp(tmp_path, monkeypatch):
    """把日志目录顶到本用例的 tmp_path，并在收尾时把 handler 摘掉关掉。

    ⚠️ teardown 里的 `teardown_logging()` ⛔ 不能省：不摘的话，上一条用例挂的
    file handler 会一直攥着一个已被 pytest 删掉的目录。
    """
    monkeypatch.setenv(logsetup.LOG_DIR_ENV, str(tmp_path / "liaison-logs"))
    yield
    logsetup.teardown_logging()
