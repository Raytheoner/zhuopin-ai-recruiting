"""`tools/liaison/tests` 的公共夹具。

**为什么必须有这个文件**：`main()` 的第一句就是 `setup_logging()`（8.4 Task 4，
commit `046bdf9` 已落地），现存的一批 `test_main_*` 用例是真的会调
`liaison_main.main()` 的。没有下面这条 autouse fixture，本机跑一次 pytest 就
会在 `data/liaison/logs/` 下拉出真实日志文件——`data/` 虽在 `.gitignore` 里不会
被提交，但「日志是个人信息的第二份拷贝」（`.gitignore:17` 原话），让它在开发机
上无声堆积不是可以接受的默认。

⚠️ **这条 fixture 今天是真正兜底的，⛔ 不是空转、不可删**：它的负载能力已用
模拟实验验证过——按 Task 4 接线的方式手工调一次 `setup_logging()` 再跑全量
套件，去掉本 fixture 会在 `data/liaison/logs/liaison.log` 写入 37,607 字节；
接上本 fixture 后写入量为 0。`main()` 的接线已经落地，任何 `test_main_*` 用例
今天就在真实调用它——把本 fixture 当"预防性、还没派上用场"的东西删掉，下一次
跑测试就会往仓库外的 `data/liaison/logs/` 写真实个人信息。
"""

from __future__ import annotations

import pytest

from tools.liaison import logsetup
from tools.liaison.tests import netguard_support


@pytest.fixture(autouse=True)
def liaison_logs_to_tmp(tmp_path, monkeypatch):
    """把日志目录顶到本用例的 tmp_path，并在收尾时把 handler 摘掉关掉。

    ⚠️ teardown 里的 `teardown_logging()` ⛔ 不能省：不摘的话，上一条用例挂的
    file handler 会一直攥着一个已被 pytest 删掉的目录。

    ⚠️ **level/max-bytes/backup-count 三个环境变量也要清空**（final review
    Minor 4）：只顶 `LOG_DIR` 的话，开发者本机 shell 里若导出了
    `HR_LIAISON_LOG_LEVEL`（比如调试时设过 `ERROR`），会漏进每一条调
    `setup_logging()` 不显式传 `level=` 的用例，让它们的通过与否跟着宿主环境
    漂——同一批用例，装了这三行 delenv 前后应该在「导出/不导出」两种环境下
    结果一致，这条本身就是「测试套件是否 hermetic」的判据。
    """
    monkeypatch.setenv(logsetup.LOG_DIR_ENV, str(tmp_path / "liaison-logs"))
    monkeypatch.delenv(logsetup.LOG_LEVEL_ENV, raising=False)
    monkeypatch.delenv(logsetup.LOG_MAX_BYTES_ENV, raising=False)
    monkeypatch.delenv(logsetup.LOG_BACKUP_COUNT_ENV, raising=False)
    yield
    logsetup.teardown_logging()


@pytest.fixture(autouse=True)
def no_outbound_network():
    """本目录的用例一律 ⛔ 不许连外部地址（TD-36）。

    **为什么必须有这条**：`test_liaison_credentials.py` 的两条用例带着假凭据
    （`bot-1`/`sec-1`）起真子进程跑 `python -m tools.liaison`。TD-19 之前它们停在
    SDK 表面校验、在任何网络动作之前，**纯属运气**；TD-19 落地后那道拦阻就没了。
    没有本闸门，"跑一次本地测试 = 对企微生产端点做几次失败认证"这件事
    ⛔ **不会有任何报错告诉你**。

    ⚠️ 本 fixture 只罩**进程内**。子进程另有一条：
    `netguard_support.subprocess_env()` 把同一份闸门塞进子进程的 `PYTHONPATH`，
    由 `site` 在解释器启动时自动装上。两条缺一不可。

    ⚠️ 回环放行、⛔ 不按用例开口子——按用例开口子等于没有闸门。
    """
    netguard_support.install()
    yield
    netguard_support.uninstall()
