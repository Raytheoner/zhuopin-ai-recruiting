"""`tools/liaison/tests` 的公共夹具。

**为什么必须有这个文件**：8.4 Task 4 会把 `setup_logging()` 接进 `main()`，
届时现存的一批 `test_main_*` 用例会是真的会调 `liaison_main.main()` 的。没有
下面这条 autouse fixture，本机跑一次 pytest 就会在 `data/liaison/logs/` 下拉出
真实日志文件——`data/` 虽在 `.gitignore` 里不会被提交，但「日志是个人信息的
第二份拷贝」（`.gitignore:17` 原话），让它在开发机上无声堆积不是可以接受的
默认。

⚠️ 截至本 commit，Task 4 的接线尚未落地，`main()` 还不会调 `setup_logging()`，
本 fixture 今天是空转的——没有它也不会有文件落盘。它的负载能力已用模拟实验
验证：按 Task 4 即将接的方式手工调一次 `setup_logging()` 再跑全量套件，去掉
本 fixture 会在 `data/liaison/logs/liaison.log` 写入 37,607 字节；接上本
fixture 后写入量为 0。等 Task 4 落地，这条 fixture 从「预防性」变成
「真正兜底」，行为不必等到那时候再验证。
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
