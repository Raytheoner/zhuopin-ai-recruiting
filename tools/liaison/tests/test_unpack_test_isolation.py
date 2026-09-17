"""TD-49（0917O）：测试套件 ⛔ 不得碰真实 `data/liaison/` 的信号/锁/日志，
⛔ 更不得起真实 `claude` 子进程。

**根因（2026-09-17 实证）**：`test_inbound_wiring.py` 里两条「打标」用例
（`test_worker_marks_the_ledger_*`）走的是 `run_session_worker` 的真实链路——
`__main__.py` 把 `signal_path=UNPACK_SIGNAL_PATH`（模块级 import 的
`unpack_cli.DEFAULT_SIGNAL_PATH`，绝对路径）与真实的 `dispatch_wiring.bridge_dispatch`
绑进 `run_bridge`，于是每跑一次 pytest 就：① 往真实 `data/liaison/unpack-signal.json`
追加 `MSGID0001`/`人事部#1`；② 用 `subprocess.Popen` 真的起一个 `claude -p …
--max-budget-usd 5` 拆件会话（cwd＝仓库根、prompt＝真章程），在 worktree 里复现时
抓到了活着的 pid 57622。`0917K`/`0917M`/`0917N` 观察到的"无头会话自发提交"，
相当一部分正是这些**由测试起的**会话干的。

`conftest.py::unpack_side_effects_to_tmp` 把四处默认路径顶到 `tmp_path`，并把
`HR_LIAISON_CLAUDE_BIN` 指到一个不存在的文件——`Popen` 当场 `FileNotFoundError`
⇒ `process_create_failed`，⛔ 起不了任何真实进程。本文件跑同一条真实链路，
用「真实路径 (存在, mtime_ns) 前后不变」做回归判据，⛔ 不读真实文件内容。
"""

from __future__ import annotations

import queue

from tools.liaison.tests.test_inbound_wiring import (  # noqa: F401 —— 夹具靠导入进本模块命名空间才生效
    ReplySpy,
    StoppingEvent,
    T0,
    liaison_main,
    make_frame,
    mapped,
    roster,
    svc,
)
from tools.liaison.unpack import unpack_cli

# ⚠️ 从未被 monkeypatch 的 `REPO_ROOT` 现算真实路径，⛔ 不用 `DEFAULT_SIGNAL_PATH`
# （它已被 conftest 顶到 tmp_path，拿它判等于什么都没判）。
_REAL_DATA_ROOT = unpack_cli.REPO_ROOT / "data" / "liaison"
_REAL_SIGNAL = _REAL_DATA_ROOT / "unpack-signal.json"
_REAL_LOCK = _REAL_DATA_ROOT / "unpack-session.lock"


def _fingerprint(path) -> tuple[bool, int | None]:
    return (path.exists(), path.stat().st_mtime_ns if path.exists() else None)


def test_打标链路不写真实信号文件也不起真实claude(svc, roster, mapped, tmp_path):
    before = (_fingerprint(_REAL_SIGNAL), _fingerprint(_REAL_LOCK))

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        "| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |\n"
        "|---|---|---|---|---|---|\n"
        "| `人事部#1` | 2026-09-09 | 汤丽萍 | 事项 | 无 | `✅ 已推送 2026-09-09` |\n",
        encoding="utf-8",
    )
    ports = liaison_main.InboundPorts(
        archive_root=tmp_path / "archive",
        whitelist_path=roster,
        reply=ReplySpy(),
        ledger_path=ledger_path,
    )
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame()))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )

    # 链路确实走到了打标 ⇒ 信号一定被追加到了**某处**——必须是 tmp 里，不是真实路径
    assert "📨 回件已到，待拆件" in ledger_path.read_text(encoding="utf-8")
    assert liaison_main.UNPACK_SIGNAL_PATH != _REAL_SIGNAL
    assert liaison_main.UNPACK_SIGNAL_PATH.is_file(), "信号应落在被隔离的路径上"
    assert (_fingerprint(_REAL_SIGNAL), _fingerprint(_REAL_LOCK)) == before, (
        "测试把真实 data/liaison/ 的信号或锁写了：conftest 的隔离夹具失效"
    )

    # 起活尝试必须以「进程创建失败」收场——这是"没起真实 claude"的可观察判据
    rows = svc.conn.execute(
        "SELECT kind, detail FROM liaison_unpack_audit WHERE kind LIKE 'dispatch_%'"
    ).fetchall()
    assert rows == [("dispatch_failed", "process_create_failed")], rows
