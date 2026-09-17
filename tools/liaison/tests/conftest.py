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

#: 群 webhook 占位地址。`.invalid` 是 RFC 2606 保留的顶级域，就算哪天真被误发
#: 也一定解析失败——⛔ 改动中不得把这个假值换成真实域名。
FAKE_WEBHOOK = "https://example.invalid/cgi-bin/webhook/send?key=fake-key-for-tests"


class FakeClock:
    """单调钟 + sleep 的假体。`sleep` 直接把钟推到未来，⛔ 不真等。"""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, f"⛔ 不许睡负数：{seconds}"
        self.slept.append(seconds)
        self.now += seconds


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


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


@pytest.fixture(autouse=True)
def unpack_side_effects_to_tmp(tmp_path, monkeypatch):
    """TD-49（0917O）：拆件起活链路的四处默认落位顶到 tmp_path，并让真实 `claude`
    ⛔ 起不来。

    **为什么必须有这条**：`test_inbound_wiring.py` 的打标用例走的是
    `run_session_worker` 真实链路，`__main__.py` 绑的是模块级常量
    `UNPACK_SIGNAL_PATH`（绝对路径）与真实的 `dispatch_wiring.bridge_dispatch`——
    没有本夹具，每跑一次 pytest 就往真实 `data/liaison/unpack-signal.json` 追加
    `MSGID0001`，并 **真的 `Popen` 一个 `claude -p … --max-budget-usd 5` 拆件会话**
    （2026-09-17 在 worktree 复现抓到活 pid）。`0917K`–`0917N` 看到的"无头会话
    自发提交 docs/session接力.md"相当一部分正是这些测试起的会话。

    - 信号：`HR_LIAISON_SIGNAL_PATH` 环境变量 ＋ `unpack_cli.DEFAULT_SIGNAL_PATH` ＋
      `__main__.UNPACK_SIGNAL_PATH`（三处都要，`__main__` 是 `from … import` 的独立绑定）
    - 锁/日志：`unpack_cli` 与 `dispatch_wiring` 各一份副本，顶成**同一个** tmp 对象
      （`test_signal_root_matches_unpack_cli_data_root` 断言两处相等）
    - 真实进程闸：`HR_LIAISON_CLAUDE_BIN` 指到不存在的文件，`resolve_claude_bin`
      优先取它，`Popen` 当场 `FileNotFoundError` ⇒ `process_create_failed`。
      需要别的行为的用例自己再 `monkeypatch.setenv`（用例级覆盖夹具级）。
    """
    from tools.liaison import __main__ as liaison_main
    from tools.liaison.unpack import dispatch, dispatch_wiring, unpack_cli

    data_root = tmp_path / "liaison-data"
    signal_path = data_root / "unpack-signal.json"
    log_dir = data_root / "logs" / "unpack-headless"
    lock_path = data_root / "unpack-session.lock"

    monkeypatch.setenv(unpack_cli.SIGNAL_PATH_ENV, str(signal_path))
    monkeypatch.setattr(unpack_cli, "DEFAULT_SIGNAL_PATH", signal_path)
    monkeypatch.setattr(unpack_cli, "DEFAULT_LOG_DIR", log_dir)
    monkeypatch.setattr(unpack_cli, "DEFAULT_LOCK_PATH", lock_path)
    monkeypatch.setattr(dispatch_wiring, "DEFAULT_LOG_DIR", log_dir)
    monkeypatch.setattr(dispatch_wiring, "DEFAULT_LOCK_PATH", lock_path)
    monkeypatch.setattr(liaison_main, "UNPACK_SIGNAL_PATH", signal_path)
    monkeypatch.setenv(dispatch.CLAUDE_BIN_ENV, str(tmp_path / "claude-must-not-run"))
