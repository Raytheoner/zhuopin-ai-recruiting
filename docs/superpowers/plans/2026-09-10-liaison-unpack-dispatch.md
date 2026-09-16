# P1 · 信号与打标即开班（liaison-unpack-dispatch）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「回件已到」打标之后，在同一次调用里非阻塞起一个拆件会话：信号去重落文件、并发守卫按 pid 判活、起活失败只审计不上抛、拆件会话以受限权限启动、信号只由 CLI 清除。

**Architecture:** 三个纯文件操作模块（`signal.py` 判活/信号、`dispatch.py` 并发守卫+起活）+ 一层把 `dispatch.py` 的结果接进 P0 `bridge.run_bridge` 的审计包装（走 P0 `storage/effects.py` 的 `effect_unpack_audit`）+ 两条 CLI 子命令（`unpack-signal`、`unpack-dispatch`）+ `.env.example` 补两个可选变量。全部信号/锁文件操作用「写临时文件 + `os.replace`」原子替换（`session.py::effect_write_liveness_stamp` 的既有手法），全部有副作用的审计写走 `effect_*` 节点。

**Tech Stack:** Python 3.11+（生产在 `tools/liaison/.venv`，本机验证用 3.14 同样兼容——本单元代码只用标准库 `json`/`os`/`subprocess`/`shutil`/`argparse`/`dataclasses`，不依赖 aibot SDK）、pytest、sqlite3（仅 Task 5 的审计包装间接经由 `idempotent_effect`）。

**Spec:** `openspec/changes/liaison-reply-bridge-and-patrol/specs/liaison-unpack-dispatch/spec.md`（本计划的行为契约来源）＋ `openspec/changes/liaison-reply-bridge-and-patrol/design.md` D4/D9/D10/D11/D12（技术决策）。执行者必须两份都读——spec 定义 MUST/SHALL，design 给出落位与理由。

## Global Constraints

- **铁律 1**：每个有副作用的动作必须独占一个节点并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 并加唯一索引。**幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者。（本单元：`dispatch_started` / `dispatch_skipped_busy` / `dispatch_failed` 三种审计走同一条 effect 路径）
- **铁律 2**：副作用只在 `effect_*` 节点执行，节点命名区分 `compute_*` / `effect_*`。（本单元：`compute_is_alive` / `compute_is_busy` 必须是纯函数，⛔ 不在里面起进程、不写文件）
- 🔴 **合规红线 · 候选人对外通道**：候选人入口一律用一次性邀请链接；拒信/邀约对外发送属**不可代项**。⇒ 无头会话的 `allowedTools` 白名单 ⛔ **不得含 `send-followup`、不得含 `git push`**，⛔ **不得用 `--dangerously-skip-permissions`**（Shao Peishen 2026-09-10 答 Q3：权限层用 `acceptEdits` ＋ 白名单）。这条要成为可机器判读的断言，不是注释。
- 🔴 **凭据口径**：`.env.example` 只写变量名与说明，⛔ **不写任何取值**、不回显；`test_liaison_boundaries` 的凭据守卫必须仍然全绿。
- **部署约束**：目标服务器是 Windows、没有 Docker，⛔ 不要引入容器。

---

## Spec Requirement → Task 对应表

| spec.md `### Requirement:` | 落在哪个/哪些 Task |
|---|---|
| 打标、落信号、起活在同一次调用内完成且不阻塞 | Task 1（信号非阻塞落盘）＋ Task 4（`Popen` 非阻塞）＋ Task 5（接线不等待） |
| 信号项按消息标识去重且信号文件损坏时不丢新项 | Task 1 |
| 并发守卫按 pid 判活，查询失败归不存活 | Task 2 |
| 起活失败只审计不上抛且从不动信号 | Task 4（四类失败不上抛、不碰信号文件）＋ Task 5（审计落库） |
| 拆件会话以受限权限启动 | Task 3（argv/白名单）＋ Task 4（实际起进程） |
| 信号只由 CLI 清除且只清检查点之前 | Task 1（`clear_signal_before`）＋ Task 6（CLI 子命令） |
| 起活审计三态 | Task 5 |

（tasks.md §2 的 2.1–2.9 全部覆盖：2.1+2.2→Task1，2.3→Task2，2.4→Task3，2.5+2.6→Task4，2.7→Task5，2.8→Task6，2.9→Task7）

---

## 已知的跨计划接口假设（⚠️ 非本计划可单方面决定，供 P0/P2 执行者核对）

本单元（P1）依赖两个**此刻还不存在**、由并行会话编写的模块：

1. **P0 `tools/liaison/unpack/bridge.py`**（`liaison-reply-bridge` 变更包，`[Mac]0910G` 编）：`run_bridge(conn, *, inbound_result, sender_name, ledger_path, signal_path, now, dispatch)`。`dispatch` 形参按 tasks.md 1.6/2.7 的措辞是一个**注入点**。本计划 Task 5 把它实现为 `dispatch.bridge_dispatch(conn, *, thread_id, msgid, sender_userid, letter_number, charter_text, prompt, now) -> DispatchOutcome`，签名写在 Task 5 里。**若 P0 合入后的真实调用约定与此不同，以 P0 为准调整 Task 5 那一处调用，其余七个 Task 不受影响**（它们只依赖标准库与 P0 已确定存在的 `storage/effects.py`）。
2. **P0 `tools/liaison/storage/effects.py::effect_unpack_audit`**（tasks.md 1.5，design D11）：本计划假设签名为 `effect_unpack_audit(conn, *, thread_id, business_key, msgid, sender_userid, letter_number, kind, detail)`，其中调用方按 `business_key = f"{msgid}:{kind}"` 拼出（`idempotent_effect` 装饰器的幂等键只吃 `thread_id`+`business_key` 两段，design D11 要求的键是 `{thread_id}:effect_unpack_audit:{msgid}:{kind}` 四段，所以 `msgid`+`kind` 必须拼进 `business_key`）。同上，若签名不同只改 Task 5 一处。
3. **P2 `tools/liaison/unpack/charter.py`**（`liaison-unpack-charter`，`[Mac]0910I` 编）：本单元 Task 4 的 `dispatch_headless_unpack` **不 import 它**——`charter_text` 与 `prompt` 作为已解析好的字符串由调用方传入（`charter_text=None` 即代表"读不到"，见 Task 4）。Task 5 的 `bridge_dispatch` 目前用仓库根下 `.claude/skills/liaison-unpack/SKILL.md` 的**固定相对路径常量**自己读文件（P2 那份 `charter.CHARTER_RELATIVE_PATH` 尚不存在），构造的 prompt 只是「前言 + 章程全文」的最简形式，⛔ 不做 D13 要求的丰富前言字段。**P2 落地后，`[Mac]0910I` 的执行者需要把 Task 5 里 `bridge_dispatch` 读章程/拼 prompt 那几行换成调用 `charter.read_charter` / `charter.compute_prompt`，删掉本计划里那个临时常量**——这是本计划遗留给 P2 的唯一收尾项，已经在 Task 5 的代码注释里用 `# P2-TODO` 标出，⛔ 不是"以后再说"的占位符，是当前可运行、待替换实现的真代码。

---

### Task 1: 信号文件——去重追加、探测、按检查点清除

**Files:**
- Create: `tools/liaison/unpack/__init__.py`
- Create: `tools/liaison/unpack/signal.py`
- Test: `tools/liaison/tests/test_unpack_signal.py`

**Interfaces:**
- Produces：`append_signal(path: Path, item: dict) -> bool`（返回是否发生了"损坏替换"）；`probe_signal(path: Path) -> bool`；`clear_signal_before(path: Path, checkpoint: str) -> None`。`item` 的形状固定为 `{"letter_number": str, "msgid": str, "archived_relpath": str, "at": str}`（design D12）。`checkpoint`/`at` 均为 `tools.liaison.session.format_instant()` 产出的 ISO8601 字符串（含 `+08:00` 偏移、微秒定宽），可安全按字符串字典序比较。

- [ ] **Step 1: 建包骨架**

```python
# tools/liaison/unpack/__init__.py
"""P0–P3 共用的「拆件」子包：回件桥、信号、并发起活、章程、口径点台账。

⛔ 本包任何模块都不碰值守线程独占的 sqlite 连接，除了显式经 `storage/effects.py`
的 `effect_*` 节点（P0 task 1.5）——文件操作与库操作分得越干净，并发守卫与信号
文件就越容易在不持锁的情况下保证安全。
"""

from __future__ import annotations
```

- [ ] **Step 2: 写失败测试（去重追加）**

```python
# tools/liaison/tests/test_unpack_signal.py
"""信号文件（`data/liaison/unpack-signal.json`）的去重追加/探测/按检查点清除。

design D12：`{"pending":[{"letter_number","msgid","archived_relpath","at"}]}`；
追加按 `msgid` 去重；文件缺失/损坏时以只含本次一项的新文件替换。
"""

from __future__ import annotations

import json

import pytest

from tools.liaison.unpack.signal import append_signal, clear_signal_before, probe_signal


def _item(msgid: str, at: str = "2026-09-10T14:03:00.000000+08:00") -> dict:
    return {
        "letter_number": "人事部#1",
        "msgid": msgid,
        "archived_relpath": f"data/liaison/archive/x/20260910/{msgid}.md",
        "at": at,
    }


def test_append_creates_file_when_missing(tmp_path):
    path = tmp_path / "unpack-signal.json"
    replaced = append_signal(path, _item("m1"))
    assert replaced is False
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m1"]


def test_append_dedupes_by_msgid(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1", at="2026-09-10T14:00:00.000000+08:00"))
    append_signal(path, _item("m1", at="2026-09-10T15:00:00.000000+08:00"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["pending"]) == 1
    assert payload["pending"][0]["at"] == "2026-09-10T15:00:00.000000+08:00"


def test_append_replaces_corrupted_file_and_reports_it(tmp_path):
    path = tmp_path / "unpack-signal.json"
    path.write_text("{not json", encoding="utf-8")
    replaced = append_signal(path, _item("m1"))
    assert replaced is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m1"]


def test_probe_signal_empty_file_is_no_signal(tmp_path):
    path = tmp_path / "unpack-signal.json"
    assert probe_signal(path) is False
    path.write_text(json.dumps({"pending": []}), encoding="utf-8")
    assert probe_signal(path) is False


def test_probe_signal_true_when_pending_nonempty(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1"))
    assert probe_signal(path) is True


def test_clear_signal_before_keeps_items_at_or_after_checkpoint(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1", at="2026-09-10T14:00:00.000000+08:00"))
    append_signal(path, _item("m2", at="2026-09-10T15:00:00.000000+08:00"))
    clear_signal_before(path, "2026-09-10T14:30:00.000000+08:00")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m2"]


def test_clear_signal_before_on_missing_file_is_noop(tmp_path):
    path = tmp_path / "unpack-signal.json"
    clear_signal_before(path, "2026-09-10T14:30:00.000000+08:00")
    assert not path.exists()
```

- [ ] **Step 2b: 确认测试失败（模块还不存在）**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_signal.py -v`
Expected: `ModuleNotFoundError: No module named 'tools.liaison.unpack'`（或 `signal`），全部 collection error。

- [ ] **Step 3: 实现 `signal.py`**

```python
# tools/liaison/unpack/signal.py
"""信号文件：拆件会话「有没有活要干」的旗子（design D8：不是数据，是旗子）。

⛔ 本模块不 import `tools.liaison.storage.db`——spec「子命令不碰库」要求
`unpack-signal` 子命令能在不碰值守数据库的情况下跑，模块层的 import 边界是
最结实的保证方式（AST 扫描守不住"函数体内 late import"，但堵住模块层 import
足以覆盖本模块自己的直接调用面，Task 6 的 AST 测试另行扫整个子命令模块）。

追加/清除都走「写临时文件 + `os.replace`」原子替换（复用 `session.py::
effect_write_liveness_stamp` 的手法）：外部读者要么看到替换前完整的一份，
要么看到替换后完整的一份，⛔ 不会看到半截 JSON。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _read_pending(path: Path) -> tuple[list[dict], bool]:
    """读 `pending` 数组。返回 `(数组, 文件是否存在但读不出合法内容)`。

    第二个返回值就是"损坏"判据：文件不存在 ⇒ `(每, False)`（正常的"还没有信号"）；
    文件存在但不是合法 JSON、或没有 `pending` 数组 ⇒ `([], True)`（损坏，调用方据此
    决定是否要在追加后打「已替换」审计）。
    """
    if not path.is_file():
        return [], False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], True
    if not isinstance(payload, dict) or not isinstance(payload.get("pending"), list):
        return [], True
    return payload["pending"], False


def _write_pending_atomic(path: Path, pending: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps({"pending": pending}, ensure_ascii=False), encoding="utf-8"
    )
    # 原子替换：追加期间进程被杀，外部读到的仍是替换前完整的一份。
    os.replace(tmp_path, path)


def append_signal(path: Path, item: dict) -> bool:
    """追加一项，按 `msgid` 去重（同 msgid 的旧项被新项覆盖，取最新时刻）。

    返回是否发生了"文件缺失/损坏 ⇒ 以只含本次一项的新文件替换"（spec 要求
    调用方据此留一条 `signal_file_replaced` 审计记录，本函数不碰库，只报状态）。
    """
    pending, was_corrupted = _read_pending(path)
    if was_corrupted:
        pending = []
    deduped = [entry for entry in pending if entry.get("msgid") != item["msgid"]]
    deduped.append(item)
    _write_pending_atomic(path, deduped)
    return was_corrupted


def probe_signal(path: Path) -> bool:
    """有没有待处理信号。空文件、`pending: []`、文件不存在都算「没有」。"""
    pending, _ = _read_pending(path)
    return bool(pending)


def clear_signal_before(path: Path, checkpoint: str) -> None:
    """只清 `at < checkpoint` 的项，保留 `at >= checkpoint` 的项。

    文件不存在 ⇒ 什么都不用清，直接返回（⛔ 不创建文件——清除操作不该凭空
    造出一个空信号文件，那会让下一次 `probe_signal` 的"没有信号"判断多一层
    不必要的文件系统访问）。

    ⚠️ `checkpoint`/`at` 必须是同一口径的 ISO8601 字符串（`session.format_instant`
    产出、固定 `+08:00` 偏移、微秒定宽）才能安全按字符串比较；调用方负责这一点，
    本函数不做时区归一化——归一化需要解析时间，那会把这个本该是纯字符串操作
    的函数拖进 `datetime` 解析的错误处理泥潭，超出"只清检查点之前"这条职责。
    """
    pending, was_corrupted = _read_pending(path)
    if was_corrupted or not pending:
        return
    kept = [entry for entry in pending if entry.get("at", "") >= checkpoint]
    if len(kept) == len(pending):
        return
    _write_pending_atomic(path, kept)
```

- [ ] **Step 4: 确认测试通过**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_signal.py -v`
Expected: 7 个用例全部 `PASSED`。

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/__init__.py tools/liaison/unpack/signal.py tools/liaison/tests/test_unpack_signal.py
git commit -m "feat(liaison): P1 task1 —— 信号文件去重追加/探测/按检查点清除"
```

---

### Task 2: 并发守卫——pid 判活、锁文本 → 忙/闲

**Files:**
- Create: `tools/liaison/unpack/dispatch.py`（本 Task 只写判活/判忙两个纯函数，锁读写留给 Task 4）
- Test: `tools/liaison/tests/test_unpack_dispatch.py`

**Interfaces:**
- Consumes：无（纯函数，不依赖 Task 1）
- Produces：`compute_is_alive(pid: int) -> bool`；`compute_is_busy(lock_text: str | None, is_alive: Callable[[int], bool]) -> bool`。Task 4/5 都会 import 这两个函数。

- [ ] **Step 1: 写失败测试**

```python
# tools/liaison/tests/test_unpack_dispatch.py
"""`tools/liaison/unpack/dispatch.py` 的并发守卫与起活。

🔴 本文件里**真实 `subprocess.Popen`／真实 `os.kill` 一律不调用**——`popen`/
`is_alive` 全部用 fake 注入。真实 `os.kill(pid, 0)` 只在 `compute_is_alive`
自身的单测里对**本进程自己的 pid**（`os.getpid()`）调一次，用来验证"确实活着"
这一条正向路径，不构造/依赖任何外部进程。
"""

from __future__ import annotations

import os

import pytest

from tools.liaison.unpack.dispatch import compute_is_alive, compute_is_busy


def test_compute_is_alive_true_for_self_pid():
    assert compute_is_alive(os.getpid()) is True


def test_compute_is_alive_false_for_process_lookup_error():
    def _raiser(pid, sig):
        raise ProcessLookupError()

    assert compute_is_alive(-1, _kill=_raiser) is False


def test_compute_is_alive_false_for_permission_error():
    def _raiser(pid, sig):
        raise PermissionError()

    assert compute_is_alive(1, _kill=_raiser) is False


def test_compute_is_alive_false_for_any_other_exception():
    def _raiser(pid, sig):
        raise OSError("boom")

    assert compute_is_alive(1, _kill=_raiser) is False


def test_compute_is_busy_true_when_lock_valid_and_alive():
    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, lambda pid: True) is True


def test_compute_is_busy_false_when_alive_returns_false():
    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, lambda pid: False) is False


def test_compute_is_busy_false_when_lock_missing():
    assert compute_is_busy(None, lambda pid: True) is False


def test_compute_is_busy_false_when_lock_corrupted_json():
    assert compute_is_busy("{not json", lambda pid: True) is False


def test_compute_is_busy_false_when_lock_missing_pid_field():
    assert compute_is_busy('{"started_at": "x"}', lambda pid: True) is False


def test_compute_is_busy_false_when_is_alive_raises():
    def _raiser(pid):
        raise RuntimeError("判活查询本身抛异常")

    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, _raiser) is False
```

- [ ] **Step 2: 确认测试失败**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch.py -v`
Expected: `ModuleNotFoundError: No module named 'tools.liaison.unpack.dispatch'`。

- [ ] **Step 3: 实现判活/判忙**

```python
# tools/liaison/unpack/dispatch.py
"""并发守卫 ＋ 非阻塞起活（design D4/D12，spec「并发守卫按 pid 判活」「拆件会话
以受限权限启动」「起活失败只审计不上抛」）。

⛔ 本模块 ⛔ 不 import `tools.liaison.storage.db`（spec「子命令不碰库」，Task 6
的 AST 测试守着整条 `unpack-dispatch` 子命令的 import 面，含本模块）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger("tools.liaison.unpack.dispatch")


def compute_is_alive(pid: int, *, _kill: Callable[[int, int], None] = os.kill) -> bool:
    """`pid` 对应的进程是否仍在运行。**查询失败一律按不存活处理**（spec 明写）。

    `_kill` 是测试注入缝（⛔ 不是配置项）：`os.kill(pid, 0)` 不发信号只探测，
    `ProcessLookupError`（进程不存在）/`PermissionError`（进程存在但探测不到，
    如属于别的用户）/**任何其它异常**统一判「不存活」——design D12 逐字要求这三者
    同一处置，⛔ 不许把 `PermissionError` 特殊化成"存活但探测不到"。
    """
    try:
        _kill(pid, 0)
    except Exception:
        return False
    return True


def compute_is_busy(lock_text: str | None, is_alive: Callable[[int], bool]) -> bool:
    """锁文本 + 判活函数 → 是否忙。四种形状全部落在「不忙」这一侧，除了
    「锁存在、pid 合法、`is_alive` 返回 True」这一种。

    `is_alive` 是调用方注入的判活函数（生产传 `compute_is_alive`，单测传 fake）。
    ⚠️ 本函数**自己也**包一层 `try/except`——不因为不信任 `compute_is_alive`
    （它已经不会抛），而是「查询失败归不存活」是 spec 对"判忙"这整条判据的要求，
    不该只在 `compute_is_alive` 一处兜底，未来换一个判活实现时这条防线不能丢。
    """
    if not lock_text:
        return False
    try:
        payload: Any = json.loads(lock_text)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        return bool(is_alive(pid))
    except Exception:
        return False
```

- [ ] **Step 4: 确认测试通过**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch.py -v`
Expected: 10 个用例全部 `PASSED`。

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/dispatch.py tools/liaison/tests/test_unpack_dispatch.py
git commit -m "feat(liaison): P1 task2 —— 并发守卫 pid 判活/锁文本判忙"
```

---

### Task 3: 二进制解析 ＋ 受限权限 argv 构造

**Files:**
- Modify: `tools/liaison/unpack/dispatch.py`
- Modify: `tools/liaison/tests/test_unpack_dispatch.py`

**Interfaces:**
- Consumes：无新依赖
- Produces：`CLAUDE_BIN_ENV = "HR_LIAISON_CLAUDE_BIN"`；`BUDGET_ENV = "HR_LIAISON_UNPACK_BUDGET_USD"`；`DEFAULT_BUDGET_USD = "5"`；`resolve_claude_bin(env: Mapping[str, str]) -> str | None`；`build_headless_argv(claude_bin: str, budget: str) -> list[str]`。Task 4 直接调用这两个函数。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tools/liaison/tests/test_unpack_dispatch.py 末尾

from pathlib import Path

from tools.liaison.unpack.dispatch import (
    BUDGET_ENV,
    CLAUDE_BIN_ENV,
    DEFAULT_BUDGET_USD,
    build_headless_argv,
    resolve_claude_bin,
)


def test_resolve_claude_bin_prefers_env_override():
    assert resolve_claude_bin({CLAUDE_BIN_ENV: "/opt/claude/bin/claude"}) == "/opt/claude/bin/claude"


def test_resolve_claude_bin_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.shutil.which", lambda name: "/usr/local/bin/claude"
    )
    assert resolve_claude_bin({}) == "/usr/local/bin/claude"


def test_resolve_claude_bin_falls_back_to_known_install_path(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.liaison.unpack.dispatch.shutil.which", lambda name: None)
    fake_home = tmp_path / "home"
    fake_local_bin = fake_home / ".local" / "bin"
    fake_local_bin.mkdir(parents=True)
    (fake_local_bin / "claude").write_text("", encoding="utf-8")
    monkeypatch.setattr("tools.liaison.unpack.dispatch.Path.home", lambda: fake_home)
    assert resolve_claude_bin({}) == str(fake_local_bin / "claude")


def test_resolve_claude_bin_none_when_all_three_fail(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.liaison.unpack.dispatch.shutil.which", lambda name: None)
    monkeypatch.setattr("tools.liaison.unpack.dispatch.Path.home", lambda: tmp_path / "empty-home")
    assert resolve_claude_bin({}) is None


def test_build_headless_argv_shape():
    argv = build_headless_argv("/usr/local/bin/claude", "5")
    assert argv[0] == "/usr/local/bin/claude"
    assert "-p" in argv
    assert "--output-format" in argv and "text" in argv
    assert "--permission-mode" in argv and "acceptEdits" in argv
    assert "--max-budget-usd" in argv and "5" in argv
    assert "--dangerously-skip-permissions" not in argv
    joined = " ".join(argv)
    assert "send-followup" not in joined
    assert "git push" not in joined
    for required in (
        "Read", "Edit", "Write", "Glob", "Grep",
        "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)",
        "Bash(git diff:*)", "Bash(git log:*)",
        "Bash(python -m tools.liaison unpack-signal:*)",
        "Bash(python -m tools.liaison criteria:*)",
    ):
        assert required in argv


def test_default_budget_env_name_and_value():
    assert BUDGET_ENV == "HR_LIAISON_UNPACK_BUDGET_USD"
    assert DEFAULT_BUDGET_USD == "5"
```

- [ ] **Step 2: 确认测试失败**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch.py -v -k "resolve_claude_bin or build_headless_argv or default_budget"`
Expected: `ImportError: cannot import name 'resolve_claude_bin'`。

- [ ] **Step 3: 实现**

```python
# 追加到 tools/liaison/unpack/dispatch.py（紧接 import 块之后）

import shutil
from pathlib import Path

#: 环境变量名。⛔ 只在这里写死一次——`.env.example`（Task 7）与
#: `build_headless_argv` 的调用方都从这里 import，⛔ 不许各处重写字面量。
CLAUDE_BIN_ENV = "HR_LIAISON_CLAUDE_BIN"
BUDGET_ENV = "HR_LIAISON_UNPACK_BUDGET_USD"

#: 预算默认值（design D16，Shao Peishen 2026-09-10 答 2a）。取字符串——它只会被
#:拼进 argv，⛔ 不参与任何数值运算，存成 `str` 免得调用方还要 `str(int(...))`。
DEFAULT_BUDGET_USD = "5"


def resolve_claude_bin(env: Mapping[str, str]) -> str | None:
    """`claude` 二进制路径解析（design D12 三级顺序）。均找不到 ⇒ `None`，
    调用方（Task 4）据此转 `failed(reason=binary_not_found)`。

    ⛔ 不在这里抛异常——"找不到"是一个**正常、被 spec 预期到**的结果，抛异常
    会强迫调用方用 `try/except` 来处理一个其实只是"返回值是 None"的情形。
    """
    override = env.get(CLAUDE_BIN_ENV)
    if override and override.strip():
        return override.strip()
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.is_file():
        return str(fallback)
    return None


#: 受限权限 argv 模板（design D4）。⛔ **唯一真源**——白名单是否放行某条命令、
#: 预算参数是否存在，全部由 `subagent-driven-development` 的 reviewer 对着*这个
#: 常量*核对，⛔ 不许在别处再拼一份 argv 字面量。
HEADLESS_ARGV_FIXED_PART: tuple[str, ...] = (
    "-p",
    "--output-format", "text",
    "--permission-mode", "acceptEdits",
    "--allowedTools",
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)",
    "Bash(git diff:*)", "Bash(git log:*)",
    "Bash(python -m tools.liaison unpack-signal:*)",
    "Bash(python -m tools.liaison criteria:*)",
)


def build_headless_argv(claude_bin: str, budget: str) -> list[str]:
    """拼出完整 argv（含二进制路径与预算值）。**纯函数**，⛔ 不读环境、不起进程。

    白名单里 ⛔ **不出现** `send-followup`（合规红线：对外通道不可代）、
    ⛔ 不出现 `git push`（design D7），且权限模式固定 `acceptEdits`——
    ⛔ 绝不使用跳过全部确认的模式（合规红线 + design D4）。
    """
    return [claude_bin, *HEADLESS_ARGV_FIXED_PART, "--max-budget-usd", budget]
```

- [ ] **Step 4: 确认测试通过**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch.py -v`
Expected: 全部（Task 2 的 10 个 ＋ Task 3 的 6 个）`PASSED`。

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/dispatch.py tools/liaison/tests/test_unpack_dispatch.py
git commit -m "feat(liaison): P1 task3 —— claude 二进制解析与受限权限 argv 构造"
```

---

### Task 4: 非阻塞起活——四类失败只审计不上抛

**Files:**
- Modify: `tools/liaison/unpack/dispatch.py`
- Modify: `tools/liaison/tests/test_unpack_dispatch.py`

**Interfaces:**
- Consumes：Task 2 的 `compute_is_alive`/`compute_is_busy`、Task 3 的 `resolve_claude_bin`/`build_headless_argv`/`BUDGET_ENV`/`DEFAULT_BUDGET_USD`
- Produces：`DispatchOutcome`（frozen dataclass：`status: str`（`"started"`/`"skipped_busy"`/`"failed"`）、`reason: str | None`、`pid: int | None`、`log_path: str | None`）；`dispatch_headless_unpack(*, charter_text, prompt, log_dir, lock_path, env, now, popen=subprocess.Popen) -> DispatchOutcome`。Task 5 直接调用本函数。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tools/liaison/tests/test_unpack_dispatch.py 末尾
"""🔴 本节起，测试文件文首那条纪律再强调一次：以下用例的 `popen` 参数一律是
fake（`_FakeProcess`/`_raising_popen`），⛔ 没有一条调用真实 subprocess.Popen。
"""

import io
from datetime import datetime, timezone

from tools.liaison.unpack.dispatch import DispatchOutcome, dispatch_headless_unpack


class _FakeStdin(io.BytesIO):
    def close(self):
        self.closed_with = self.getvalue()
        super().close()


class _FakeProcess:
    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.stdin = _FakeStdin()


def _fake_popen_factory(process: "_FakeProcess"):
    calls = []

    def _popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    _popen.calls = calls
    return _popen


NOW = datetime(2026, 9, 10, 14, 3, 0, tzinfo=timezone.utc)


def test_dispatch_charter_missing_is_failed_without_touching_anything(tmp_path):
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text=None,
        prompt="prompt",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome == DispatchOutcome(status="failed", reason="charter_missing", pid=None, log_path=None)
    assert popen.calls == []
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "lock.json").exists()


def test_dispatch_skipped_busy_when_lock_alive(tmp_path, monkeypatch):
    lock_path = tmp_path / "lock.json"
    lock_path.write_text('{"pid": 99999, "started_at": "x", "log": "y"}', encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: True
    )
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        log_dir=tmp_path / "logs",
        lock_path=lock_path,
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "skipped_busy"
    assert popen.calls == []


def test_dispatch_log_dir_unwritable_is_failed(tmp_path, monkeypatch):
    # 把 log_dir 的父目录做成一个文件，mkdir(parents=True) 必炸 NotADirectoryError（OSError 子类）。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        log_dir=blocker / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "log_file_failed"
    assert popen.calls == []


def test_dispatch_binary_not_found_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: None
    )
    popen = _fake_popen_factory(_FakeProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "binary_not_found"
    assert popen.calls == []


def test_dispatch_popen_raises_is_process_create_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )

    def _raising_popen(argv, **kwargs):
        raise FileNotFoundError("二进制没了")

    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=_raising_popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "process_create_failed"
    assert not (tmp_path / "lock.json").exists()


def test_dispatch_started_writes_lock_and_stdin_and_does_not_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )
    process = _FakeProcess(pid=4242)
    popen = _fake_popen_factory(process)
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="给拆件会话的完整 prompt",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "started"
    assert outcome.pid == 4242
    # 非阻塞：fake process 没有 wait() 方法，能走到这里就证明本函数没调它。
    assert not hasattr(process, "wait_called")
    # stdin 写了 prompt 并关闭。
    assert process.stdin.closed_with == "给拆件会话的完整 prompt".encode("utf-8")
    assert process.stdin.closed is True
    # 锁文件已写。
    lock_payload = json.loads((tmp_path / "lock.json").read_text(encoding="utf-8"))
    assert lock_payload["pid"] == 4242
    # popen 的 argv/cwd/stdin/stdout/stderr 形状正确。
    argv, kwargs = popen.calls[0]
    assert argv[0] == "/usr/local/bin/claude"
    assert kwargs["stdin"] is not None
    assert kwargs["cwd"] == str(_repo_root_for_test())


def _repo_root_for_test():
    from tools.liaison.unpack.dispatch import REPO_ROOT

    return REPO_ROOT


def test_dispatch_never_raises_even_on_unexpected_stdin_error(tmp_path, monkeypatch):
    """第四类失败——「其它未预期异常」：stdin.write 抛异常时不上抛、返回 failed。"""
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.compute_is_alive", lambda pid: False
    )
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch.resolve_claude_bin", lambda env: "/usr/local/bin/claude"
    )

    class _BrokenStdinProcess:
        pid = 5555

        class _stdin:
            @staticmethod
            def write(data):
                raise BrokenPipeError("对端已关闭")

            @staticmethod
            def close():
                pass

        stdin = _stdin()

    popen = _fake_popen_factory(_BrokenStdinProcess())
    outcome = dispatch_headless_unpack(
        charter_text="章程全文",
        prompt="prompt",
        log_dir=tmp_path / "logs",
        lock_path=tmp_path / "lock.json",
        env={},
        now=NOW,
        popen=popen,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "unexpected_error"
```

- [ ] **Step 2: 确认测试失败**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch.py -v -k dispatch_`
Expected: `ImportError: cannot import name 'DispatchOutcome'`。

- [ ] **Step 3: 实现**

```python
# 追加到 tools/liaison/unpack/dispatch.py

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

# tools/liaison/unpack/dispatch.py → parents[0]=unpack, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DispatchOutcome:
    """一次起活尝试的结果。`status` 三态对应 spec「起活审计三态」。"""

    status: str  # "started" | "skipped_busy" | "failed"
    reason: str | None = None
    pid: int | None = None
    log_path: str | None = None


class _ClaudeBinaryNotFound(Exception):
    """内部哨兵：区分「二进制解析失败」与「popen 本身抛异常」，两者的审计
    `reason` 不同（`binary_not_found` vs `process_create_failed`），⛔ 不让
    调用方看到——只在本函数体内捕获。"""


def _utc_log_stamp(now: datetime) -> str:
    """日志文件名用 UTC 戳（design D12，⚠️ 与台账的 CST 口径故意不同——
    台账是给人看的，日志戳只用于排序与去重，用 UTC 免去夏令时/时区换算的坑）。
    """
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_lock_atomic(path: Path, *, pid: int, started_at: datetime, log_path: Path) -> None:
    payload = {
        "pid": pid,
        "started_at": started_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        "log": str(log_path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def dispatch_headless_unpack(
    *,
    charter_text: str | None,
    prompt: str,
    log_dir: Path,
    lock_path: Path,
    env: Mapping[str, str],
    now: datetime,
    popen: Callable[..., Any] = subprocess.Popen,
) -> DispatchOutcome:
    """非阻塞起一个拆件会话。⛔ **本函数不读写信号文件**（spec 明写）——信号由
    `bridge.run_bridge` 追加、由 `unpack-signal --clear` 清除，两头都不是这里。

    四类失败——`charter_missing` / `log_file_failed` /
    `binary_not_found`+`process_create_failed`（同属「进程创建失败」一类，
    两个具体原因） / `unexpected_error`——**全部**转成 `DispatchOutcome(status="failed")`
    返回，⛔ 一个 `raise` 都不许漏到调用方。
    """
    if charter_text is None:
        return DispatchOutcome(status="failed", reason="charter_missing")

    try:
        lock_text = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else None
    except OSError:
        # 锁文件读不出来，按 spec「查询失败归不存活」同一精神处理——不存活即不忙。
        lock_text = None
    if compute_is_busy(lock_text, compute_is_alive):
        return DispatchOutcome(status="skipped_busy")

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{_utc_log_stamp(now)}.log"
        log_file = open(log_path, "wb")
    except OSError as exc:
        logger.error("拆件会话日志文件建不了：%s", log_path if 'log_path' in dir() else log_dir, exc_info=True)
        return DispatchOutcome(status="failed", reason="log_file_failed")

    try:
        try:
            claude_bin = resolve_claude_bin(env)
            if claude_bin is None:
                raise _ClaudeBinaryNotFound()
            budget = (env.get(BUDGET_ENV) or DEFAULT_BUDGET_USD).strip()
            argv = build_headless_argv(claude_bin, budget)
            process = popen(
                argv,
                cwd=str(REPO_ROOT),
                stdin=subprocess.PIPE,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=dict(env),
            )
        except _ClaudeBinaryNotFound:
            return DispatchOutcome(status="failed", reason="binary_not_found")
        except Exception as exc:
            logger.error("拆件会话进程创建失败：%s", exc, exc_info=True)
            return DispatchOutcome(status="failed", reason="process_create_failed")
    finally:
        # Popen 已经把 log_file 的 fd 复制给子进程（POSIX 语义）；父进程这边
        # 关闭它不影响子进程继续写——⛔ 不许因为"看起来该等"就调 process.wait()，
        # 那会把值守线程拖进拆件会话的整个生命周期，违反「不阻塞」。
        log_file.close()

    try:
        if process.stdin is not None:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
        _write_lock_atomic(lock_path, pid=process.pid, started_at=now, log_path=log_path)
    except Exception as exc:
        logger.error(
            "拆件会话已起（pid=%s）但收尾步骤失败（写 prompt / 写锁）：%s",
            getattr(process, "pid", None), exc, exc_info=True,
        )
        return DispatchOutcome(status="failed", reason="unexpected_error")

    return DispatchOutcome(status="started", pid=process.pid, log_path=str(log_path))
```

- [ ] **Step 4: 确认测试通过**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch.py -v`
Expected: 全部（Task 2+3+4，共 24 个）`PASSED`。

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/dispatch.py tools/liaison/tests/test_unpack_dispatch.py
git commit -m "feat(liaison): P1 task4 —— 非阻塞起活，四类失败只审计不上抛"
```

---

### Task 5: 接入 `bridge.run_bridge` 的 dispatch 注入点 ＋ 三态审计

**Files:**
- Create: `tools/liaison/unpack/dispatch_wiring.py`
- Test: `tools/liaison/tests/test_unpack_dispatch_wiring.py`

⚠️ 本 Task 依赖 P0 的 `tools/liaison/storage/effects.py::effect_unpack_audit`（tasks.md 1.5）——若 P0 尚未合入 main，`Step 4` 的真实 import 会失败；见计划顶部「已知的跨计划接口假设」。**若执行到本 Task 时 `tools/liaison/storage/effects.py` 里还没有 `effect_unpack_audit`，登记「⏸ 留步：P0 未合入，`effect_unpack_audit` 不存在」，Task 1–4／6／7 不受影响、照常推进，本 Task 待 P0 合入后由下一个 run-build 续做。**

**Files (续):**
- Modify: `tools/liaison/storage/effects.py`（若 Step 4 探测到 `effect_unpack_audit` 已存在，本文件不改；若不存在，见留步处置，⛔ 不在本计划里代 P0 写它）

**Interfaces:**
- Consumes：Task 4 的 `DispatchOutcome`/`dispatch_headless_unpack`；P0 `storage/effects.py::effect_unpack_audit(conn, *, thread_id, business_key, msgid, sender_userid, letter_number, kind, detail)`（假设签名，见顶部说明）
- Produces：`bridge_dispatch(conn, *, thread_id, msgid, sender_userid, letter_number, charter_relpath, now) -> DispatchOutcome`——这是绑给 P0 `bridge.run_bridge(..., dispatch=bridge_dispatch)` 的那个可调用对象。

- [ ] **Step 1: 探测 P0 是否已合入**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -c "from tools.liaison.storage.effects import effect_unpack_audit" 2>&1`
Expected 两种之一：
- 无输出（import 成功）⇒ 继续 Step 2
- `ImportError: cannot import name 'effect_unpack_audit'` ⇒ 按上面「⚠️」处置：登记 `⏸ 留步：P0 未合入，effect_unpack_audit 不存在`，跳过本 Task 剩余步骤，继续执行 Task 6。

- [ ] **Step 2: 写失败测试**

```python
# tools/liaison/tests/test_unpack_dispatch_wiring.py
"""`bridge_dispatch`：把 Task 4 的 `dispatch_headless_unpack` 接成 P0
`bridge.run_bridge(..., dispatch=...)` 的注入点，并把结果转成三态审计
（design D11：`dispatch_started` / `dispatch_skipped_busy` / `dispatch_failed`）。

⚠️ 本文件的用例全部注入 fake `dispatch_headless_unpack`，⛔ 不真实起进程
（起进程的行为已经在 test_unpack_dispatch.py 里覆盖过，这里只测「结果 → 审计」
这一段转换）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import EFFECT_NODE_TO_TABLE
from tools.liaison.unpack.dispatch import DispatchOutcome
from tools.liaison.unpack.dispatch_wiring import bridge_dispatch

NOW = datetime(2026, 9, 10, 14, 3, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "test.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


def _audit_rows(conn, msgid: str) -> list[tuple]:
    return conn.execute(
        "SELECT kind FROM liaison_unpack_audit WHERE msgid = ? ORDER BY id", (msgid,)
    ).fetchall()


def test_started_outcome_writes_dispatch_started_audit(conn, monkeypatch, tmp_path):
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )
    outcome = bridge_dispatch(
        conn,
        thread_id="t1",
        msgid="m1",
        sender_userid="u1",
        letter_number="人事部#1",
        charter_relpath=str(charter_path.relative_to(charter_path.parents[0])),
        charter_root=charter_path.parent,
        now=NOW,
    )
    assert outcome.status == "started"
    rows = _audit_rows(conn, "m1")
    assert rows == [("dispatch_started",)]


def test_skipped_busy_outcome_writes_dispatch_skipped_busy_audit(conn, monkeypatch, tmp_path):
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="skipped_busy"),
    )
    bridge_dispatch(
        conn, thread_id="t1", msgid="m2", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    assert _audit_rows(conn, "m2") == [("dispatch_skipped_busy",)]


def test_failed_outcome_writes_dispatch_failed_audit_with_reason(conn, monkeypatch, tmp_path):
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="failed", reason="binary_not_found"),
    )
    bridge_dispatch(
        conn, thread_id="t1", msgid="m3", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    assert _audit_rows(conn, "m3") == [("dispatch_failed",)]


def test_charter_missing_still_produces_one_audit_row(conn, monkeypatch, tmp_path):
    """章程文件真的不存在（本 Task 自己解析，⛔ 不 import P2 的 charter.py）
    ⇒ `dispatch_headless_unpack` 收到 `charter_text=None` ⇒ `failed`。"""
    outcome = bridge_dispatch(
        conn, thread_id="t1", msgid="m4", sender_userid="u1", letter_number="人事部#1",
        charter_relpath="不存在.md", charter_root=tmp_path, now=NOW,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "charter_missing"
    assert _audit_rows(conn, "m4") == [("dispatch_failed",)]


def test_same_msgid_dispatch_twice_only_one_started_audit_row(conn, monkeypatch, tmp_path):
    """幂等策略：同 msgid 同 kind 只落一行（design D11 / 铁律1）。"""
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )
    kwargs = dict(
        conn=conn, thread_id="t1", msgid="m5", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    bridge_dispatch(**kwargs)
    bridge_dispatch(**kwargs)
    assert _audit_rows(conn, "m5") == [("dispatch_started",)]


def test_audit_write_failure_is_swallowed_and_outcome_still_returned(conn, monkeypatch, tmp_path, caplog):
    """审计写失败本身吞掉只记日志（spec「审计写入自身失败时也 MUST 被吞掉」）。"""
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )

    def _raising_effect(*args, **kwargs):
        raise RuntimeError("磁盘满")

    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.effects.effect_unpack_audit", _raising_effect
    )
    outcome = bridge_dispatch(
        conn, thread_id="t1", msgid="m6", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    assert outcome.status == "started"  # dispatch 本身的结果不受审计失败影响
```

- [ ] **Step 3: 确认测试失败**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch_wiring.py -v`
Expected: `ModuleNotFoundError: No module named 'tools.liaison.unpack.dispatch_wiring'`。

- [ ] **Step 4: 实现**

```python
# tools/liaison/unpack/dispatch_wiring.py
"""把 Task 4 的 `dispatch_headless_unpack` 接成 P0 `bridge.run_bridge` 的
`dispatch=` 注入点，并把结果转成三态审计（design D11、tasks.md 2.7）。

⚠️ **跨计划接口假设**（详见计划文件顶部「已知的跨计划接口假设」）：
1. `bridge.run_bridge(..., dispatch=bridge_dispatch)`——若 P0 合入后的真实调用
   约定与 `bridge_dispatch` 的签名不同，改这一个文件即可，其余 P1 模块不受影响。
2. `effects.effect_unpack_audit` 的签名。

# P2-TODO（`liaison-unpack-charter` 落地后由该变更包的执行者做）：
本文件目前自己拼「前言 + 章程全文」这个最简 prompt、自己解析章程相对路径，
是因为 P2 的 `unpack/charter.py`（`charter.read_charter` / `charter.compute_prompt`）
此刻还不存在。P2 落地后：
  - 删除本文件里的 `_read_charter_text` 与 `_build_minimal_prompt`；
  - 改成 `from tools.liaison.unpack import charter` 并调
    `charter.read_charter(repo_root)` / `charter.compute_prompt(...)`；
  - `charter_relpath`/`charter_root` 两个参数可能随之被
    `charter.CHARTER_RELATIVE_PATH` 取代，按 P2 的 design D13 实际落地情况调整。
本文件当前的实现是**完整可运行**的最简版本，不是留空占位。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from tools.liaison.storage import effects
from tools.liaison.unpack.dispatch import DispatchOutcome, dispatch_headless_unpack

logger = logging.getLogger("tools.liaison.unpack.dispatch_wiring")

#: 起活尝试的结果 → 审计 `kind`（design D11 的九个取值里，本模块只产出这三个）。
_OUTCOME_STATUS_TO_AUDIT_KIND = {
    "started": "dispatch_started",
    "skipped_busy": "dispatch_skipped_busy",
    "failed": "dispatch_failed",
}

#: 信号/锁/日志的默认落位（design D12）。⛔ 单点常量——`__main__.py`
#: 的 CLI 子命令（Task 6）与本模块共用同一份，防止两处路径字面量漂移。
DEFAULT_SIGNAL_ROOT = Path("data/liaison")
DEFAULT_LOG_DIR = DEFAULT_SIGNAL_ROOT / "logs" / "unpack-headless"
DEFAULT_LOCK_PATH = DEFAULT_SIGNAL_ROOT / "unpack-session.lock"


def _read_charter_text(charter_root: Path, charter_relpath: str) -> str | None:
    """# P2-TODO：本函数整体会被 `charter.read_charter` 取代，见模块 docstring。"""
    path = charter_root / charter_relpath
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _build_minimal_prompt(*, letter_number: str, msgid: str, charter_text: str) -> str:
    """# P2-TODO：本函数会被 `charter.compute_prompt` 取代，见模块 docstring。

    最简前言（编号/msgid）＋ 章程全文——满足 spec「章程全文逐字结尾、前有非空
    前言」的最低要求，⛔ 不含 D13 要求的完整字段集（检查点/信号相对路径等），
    那些字段需要与 P2 `unpack-signal --probe` 的循环规则一起设计，属 P2 范围。
    """
    preamble = f"你在处理信件 {letter_number}（msgid={msgid}）的拆件流程。以下是完整章程：\n"
    return preamble + charter_text


def bridge_dispatch(
    conn,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    letter_number: str,
    charter_relpath: str,
    charter_root: Path,
    now: datetime,
) -> DispatchOutcome:
    """P0 `bridge.run_bridge` 的 `dispatch=` 注入点。**结果三态一律落一条审计**，
    审计写失败本身吞掉只记日志（spec 明写），⛔ 不让审计失败掩盖起活本身的结果。
    """
    charter_text = _read_charter_text(charter_root, charter_relpath)
    prompt = (
        _build_minimal_prompt(letter_number=letter_number, msgid=msgid, charter_text=charter_text)
        if charter_text is not None
        else ""
    )
    outcome = dispatch_headless_unpack(
        charter_text=charter_text,
        prompt=prompt,
        log_dir=DEFAULT_LOG_DIR,
        lock_path=DEFAULT_LOCK_PATH,
        env=__import__("os").environ,
        now=now,
    )

    kind = _OUTCOME_STATUS_TO_AUDIT_KIND[outcome.status]
    detail = outcome.reason or outcome.status
    try:
        effects.effect_unpack_audit(
            conn,
            thread_id=thread_id,
            business_key=f"{msgid}:{kind}",
            msgid=msgid,
            sender_userid=sender_userid,
            letter_number=letter_number,
            kind=kind,
            detail=detail,
        )
    except Exception:
        logger.error(
            "起活审计写入失败（msgid=%s kind=%s），起活本身的结果不受影响",
            msgid, kind, exc_info=True,
        )

    return outcome
```

⚠️ Step 4 实现里 `env=__import__("os").environ` 是刻意的行内 import（避免在模块顶层拉一个只在这一行用得到的 `os`），若 reviewer 认为不够清晰，等价替换为模块顶部 `import os` ＋ `env=os.environ`，⛔ 语义不变，纯风格选择，不影响测试断言。

- [ ] **Step 5: 确认测试通过**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_dispatch_wiring.py -v`
Expected: 6 个用例全部 `PASSED`（前提：Step 1 探测到 `effect_unpack_audit` 已存在；若探测未过，本 Task 已在 Step 1 留步，不会跑到这里）。

- [ ] **Step 6: Commit（仅当 Step 1 探测通过、Step 5 测试通过时）**

```bash
git add tools/liaison/unpack/dispatch_wiring.py tools/liaison/tests/test_unpack_dispatch_wiring.py
git commit -m "feat(liaison): P1 task5 —— 接入 bridge dispatch 注入点，起活三态审计"
```

---

### Task 6: CLI 子命令 `unpack-signal` / `unpack-dispatch`

**Files:**
- Modify: `tools/liaison/__main__.py`
- Create: `tools/liaison/tests/test_unpack_cli.py`

**Interfaces:**
- Consumes：Task 1 的 `signal.py`（`probe_signal`/`clear_signal_before`）、Task 4 的 `dispatch.py`（`dispatch_headless_unpack`、`DEFAULT_LOG_DIR`/`DEFAULT_LOCK_PATH` 由 Task 5 定义，若 Task 5 留步则本 Task 自己在 `unpack_cli.py` 里重复这两个路径常量——**不依赖 Task 5 是否完成**）
- Produces：`python -m tools.liaison unpack-signal --probe`（打印 `[SIGNAL]`/`[NO-SIGNAL]`，退出码 0/1）；`unpack-signal --clear --before <ISO时刻>`；`unpack-dispatch --dry-run`（打印 argv，不起）；`unpack-dispatch --force`（跳过并发守卫，仅供验收）

- [ ] **Step 1: 写失败测试**

```python
# tools/liaison/tests/test_unpack_cli.py
"""`unpack-signal`／`unpack-dispatch` 两条 CLI 子命令。

spec「信号只由 CLI 清除且只清检查点之前」＋「该子命令 MUST NOT 打开值守数据库」：
本文件专门守「两条子命令的模块 ⛔ 不 import tools.liaison.storage.db」这条
（AST 静态扫描，⛔ 不是"跑起来观察行为"——观察不到"没 import 什么"）。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_cli(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.liaison", *args],
        cwd=cwd, capture_output=True, text=True,
    )


def test_unpack_signal_probe_no_signal(tmp_path, monkeypatch):
    signal_path = tmp_path / "unpack-signal.json"
    monkeypatch.setenv("HR_LIAISON_SIGNAL_PATH", str(signal_path))
    result = _run_cli("unpack-signal", "--probe")
    assert result.returncode == 1
    assert "[NO-SIGNAL]" in result.stdout


def test_unpack_signal_probe_has_signal(tmp_path, monkeypatch):
    signal_path = tmp_path / "unpack-signal.json"
    signal_path.write_text(
        json.dumps({"pending": [{"msgid": "m1", "letter_number": "x", "archived_relpath": "y", "at": "z"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HR_LIAISON_SIGNAL_PATH", str(signal_path))
    result = _run_cli("unpack-signal", "--probe")
    assert result.returncode == 0
    assert "[SIGNAL]" in result.stdout


def test_unpack_signal_clear_before(tmp_path, monkeypatch):
    signal_path = tmp_path / "unpack-signal.json"
    signal_path.write_text(
        json.dumps({"pending": [
            {"msgid": "m1", "letter_number": "x", "archived_relpath": "y", "at": "2026-09-10T14:00:00.000000+08:00"},
            {"msgid": "m2", "letter_number": "x", "archived_relpath": "y", "at": "2026-09-10T15:00:00.000000+08:00"},
        ]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HR_LIAISON_SIGNAL_PATH", str(signal_path))
    result = _run_cli("unpack-signal", "--clear", "--before", "2026-09-10T14:30:00.000000+08:00")
    assert result.returncode == 0
    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m2"]


def test_unpack_dispatch_dry_run_prints_argv_without_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("HR_LIAISON_CLAUDE_BIN", "/usr/local/bin/claude")
    result = _run_cli("unpack-dispatch", "--dry-run")
    assert result.returncode == 0
    assert "/usr/local/bin/claude" in result.stdout
    assert "-p" in result.stdout
    assert "--dangerously-skip-permissions" not in result.stdout


def _module_source_files() -> list[Path]:
    return [
        REPO_ROOT / "tools" / "liaison" / "unpack" / "signal.py",
        REPO_ROOT / "tools" / "liaison" / "unpack" / "dispatch.py",
        REPO_ROOT / "tools" / "liaison" / "unpack" / "unpack_cli.py",
    ]


def test_unpack_subcommand_modules_do_not_import_storage_db():
    for path in _module_source_files():
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "storage.db" not in node.module and node.module != "db", (
                    f"{path} 违规 import 了 storage.db：{node.module}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "storage.db" not in alias.name, (
                        f"{path} 违规 import 了 storage.db：{alias.name}"
                    )
```

- [ ] **Step 2: 确认测试失败**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_cli.py -v`
Expected: `unpack-signal`/`unpack-dispatch` 未接线 ⇒ `SystemExit` 走到 `main()` 的凭据校验分支，`test_unpack_signal_probe_no_signal` 等用例因缺 `HR_LIAISON_BOT_ID` 报 exit code 2 而非预期的 0/1，断言失败。

- [ ] **Step 3: 实现子命令模块**

```python
# tools/liaison/unpack/unpack_cli.py
"""`unpack-signal` / `unpack-dispatch` 两条 CLI 子命令（spec「信号只由 CLI 清除」
「子命令不碰库」）。

⛔ 本模块不 import `tools.liaison.storage.db`——两条子命令都不需要值守数据库：
`unpack-signal` 只操作信号文件；`unpack-dispatch` 只操作锁文件与起进程。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tools.liaison.unpack.dispatch import (
    BUDGET_ENV,
    DEFAULT_BUDGET_USD,
    build_headless_argv,
    dispatch_headless_unpack,
    resolve_claude_bin,
)
from tools.liaison.unpack.signal import clear_signal_before, probe_signal

#: 三个落位默认值（design D12）。⚠️ 与 `dispatch_wiring.py` 的
#: `DEFAULT_SIGNAL_ROOT`/`DEFAULT_LOG_DIR`/`DEFAULT_LOCK_PATH` 是**同一份路径
#: 字面量的独立副本**——本模块刻意不 import `dispatch_wiring`（那个模块 import
#: 了 `storage.effects`，间接可能拉库依赖），两处路径值必须逐字相同，
#: 改一处务必同步改另一处（Task 7 的 `.env.example` 注释里会提醒）。
_DATA_ROOT = Path("data/liaison")
DEFAULT_SIGNAL_PATH = _DATA_ROOT / "unpack-signal.json"
DEFAULT_LOG_DIR = _DATA_ROOT / "logs" / "unpack-headless"
DEFAULT_LOCK_PATH = _DATA_ROOT / "unpack-session.lock"

#: 测试专用逃生口：指一个不同的信号文件路径，免得单测互相踩生产路径。
#: ⛔ 不写进 `.env.example`——生产环境不该调它。
SIGNAL_PATH_ENV = "HR_LIAISON_SIGNAL_PATH"

EXIT_SIGNAL_PRESENT = 0
EXIT_NO_SIGNAL = 1
EXIT_BAD_ARGS = 2


def _resolve_signal_path() -> Path:
    override = os.environ.get(SIGNAL_PATH_ENV)
    return Path(override) if override else DEFAULT_SIGNAL_PATH


def build_unpack_signal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison unpack-signal",
        description="探测/清除拆件信号文件。⛔ 不打开值守数据库。",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="有信号 → exit 0 且打印 [SIGNAL]；没有 → exit 1 且打印 [NO-SIGNAL]")
    mode.add_argument("--clear", action="store_true", help="按 --before 清除")
    parser.add_argument("--before", help="ISO8601 检查点，--clear 时必填，只清此刻之前的项")
    return parser


def unpack_signal_main(argv: list[str]) -> int:
    args = build_unpack_signal_parser().parse_args(argv)
    signal_path = _resolve_signal_path()

    if args.probe:
        if probe_signal(signal_path):
            print("[SIGNAL]")
            return EXIT_SIGNAL_PRESENT
        print("[NO-SIGNAL]")
        return EXIT_NO_SIGNAL

    if not args.before:
        print("--clear 必须搭配 --before <ISO时刻>", file=sys.stderr)
        return EXIT_BAD_ARGS
    clear_signal_before(signal_path, args.before)
    print(f"已清除 {args.before} 之前的信号项：{signal_path}")
    return 0


def build_unpack_dispatch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison unpack-dispatch",
        description="非阻塞起一个拆件会话。⛔ 不打开值守数据库。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只打印 argv 与解析到的 claude 路径，不起")
    mode.add_argument("--force", action="store_true", help="跳过并发守卫，仅供验收（tasks §5）")
    return parser


def unpack_dispatch_main(argv: list[str]) -> int:
    """⚠️ 本命令的 `--force`/正常两条路径都不接 `bridge_dispatch`（Task 5）——
    它们直接调 Task 4 的 `dispatch_headless_unpack`，且**没有章程**（验收用，
    传一个最小占位 prompt 即可，spec 对这条命令行为的唯一要求是"起活"本身可控）。
    """
    from datetime import datetime, timezone

    args = build_unpack_dispatch_parser().parse_args(argv)
    env = os.environ
    claude_bin = resolve_claude_bin(env)

    if args.dry_run:
        budget = (env.get(BUDGET_ENV) or DEFAULT_BUDGET_USD).strip()
        if claude_bin is None:
            print("找不到 claude 二进制（HR_LIAISON_CLAUDE_BIN / PATH / ~/.local/bin/claude 均未命中）")
            return 0
        print(f"claude 路径：{claude_bin}")
        print("argv：" + " ".join(build_headless_argv(claude_bin, budget)))
        return 0

    now = datetime.now(timezone.utc)
    if args.force and DEFAULT_LOCK_PATH.exists():
        # 仅验收用：直接移走旧锁，⛔ 不是生产路径的一部分（生产路径靠 pid 判活，
        # 不需要人工清锁）。
        DEFAULT_LOCK_PATH.unlink()

    outcome = dispatch_headless_unpack(
        charter_text="[unpack-dispatch --force] 验收占位：本次调用不携带真实章程",
        prompt="[unpack-dispatch --force] 这是一次验收起活，不代表真实拆件任务。",
        log_dir=DEFAULT_LOG_DIR,
        lock_path=DEFAULT_LOCK_PATH,
        env=env,
        now=now,
    )
    print(f"status={outcome.status} reason={outcome.reason} pid={outcome.pid} log={outcome.log_path}")
    return 0 if outcome.status == "started" else 1
```

- [ ] **Step 4: 接进 `__main__.py`（纯插入，仿 `cleanup`/`send-followup` 两段的既有手法）**

```python
# 插入到 tools/liaison/__main__.py，紧接现有的 send-followup 插入段之后、
# `if __name__ == "__main__" and ... SELF_CHECK_ARG` 之前（原文件第 483 行前）：

# ── P1·打标即开班：unpack-signal / unpack-dispatch 子命令 ─────────────────
# ⛔ 又一段**纯插入**：既有入口一字节未动。判据顺序同 cleanup/send-followup——
# 两条子命令都不需要企微凭据（不建连接），必须短路在 main() 的
# load_credentials() 之前，否则一台还没配 BOT_ID 的机器测不出这两条命令。
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "unpack-signal":
    from tools.liaison.unpack.unpack_cli import unpack_signal_main

    raise SystemExit(unpack_signal_main(sys.argv[2:]))

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "unpack-dispatch":
    from tools.liaison.unpack.unpack_cli import unpack_dispatch_main

    raise SystemExit(unpack_dispatch_main(sys.argv[2:]))
```

- [ ] **Step 5: 确认测试通过**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_cli.py -v`
Expected: 5 个用例全部 `PASSED`。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/unpack/unpack_cli.py tools/liaison/__main__.py tools/liaison/tests/test_unpack_cli.py
git commit -m "feat(liaison): P1 task6 —— unpack-signal/unpack-dispatch CLI 子命令"
```

---

### Task 7: `.env.example` 补两个可选变量

**Files:**
- Modify: `tools/liaison/.env.example`
- Test: `tools/liaison/tests/test_unpack_env_example.py`

**Interfaces:**
- Consumes：Task 3 的 `CLAUDE_BIN_ENV`/`BUDGET_ENV`/`DEFAULT_BUDGET_USD`
- Produces：无新代码接口，只新增文档行 + 回归测试

- [ ] **Step 1: 写失败测试**

```python
# tools/liaison/tests/test_unpack_env_example.py
"""`.env.example` 只写变量名与说明，⛔ 不写任何取值（Global Constraints 逐字要求）。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / "tools" / "liaison" / ".env.example"


def test_env_example_mentions_claude_bin_and_budget_vars():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "HR_LIAISON_CLAUDE_BIN=" in text
    assert "HR_LIAISON_UNPACK_BUDGET_USD=" in text


def test_env_example_new_vars_carry_no_credential_values():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("HR_LIAISON_CLAUDE_BIN=") or line.startswith("HR_LIAISON_UNPACK_BUDGET_USD="):
            assert line.rstrip() in ("HR_LIAISON_CLAUDE_BIN=", "HR_LIAISON_UNPACK_BUDGET_USD="), (
                f"{line!r} 携带了取值，Global Constraints 要求只写变量名"
            )


def test_env_example_documents_default_budget_of_five():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "默认" in text and "5" in text
```

- [ ] **Step 2: 确认测试失败**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_env_example.py -v`
Expected: `test_env_example_mentions_claude_bin_and_budget_vars` 等用例 `FAILED`（两个变量名还没写进去）。

- [ ] **Step 3: 追加到 `.env.example` 末尾**

```
# P1·打标即开班（liaison-unpack-dispatch）无头拆件会话的两项可选配置。
# 两项都**不参与启动期 fail-closed**：不配就用内置默认值/自动探测。
#
# 本机 claude 二进制路径。不配 ⇒ 依次尝试 `shutil.which("claude")` →
# `~/.local/bin/claude`；三处都找不到 ⇒ 起活失败（reason=binary_not_found）。
HR_LIAISON_CLAUDE_BIN=

# 无头拆件会话的 --max-budget-usd 预算上限。不配 ⇒ 默认 5（design D16）。
HR_LIAISON_UNPACK_BUDGET_USD=
```

- [ ] **Step 4: 确认测试通过**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_unpack_env_example.py -v`
Expected: 3 个用例全部 `PASSED`。

- [ ] **Step 5: 回归 `test_liaison_boundaries` 与凭据守卫**

Run: `cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python3 -m pytest tools/liaison/tests/test_liaison_boundaries.py tools/liaison/tests/test_liaison_credentials.py tools/liaison/tests/test_liaison_no_secrets_in_vcs.py -v`
Expected: 全部 `PASSED`（Global Constraints「凭据口径」要求本次改动不许把这三份守卫改红）。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/.env.example tools/liaison/tests/test_unpack_env_example.py
git commit -m "docs(liaison): P1 task7 —— .env.example 补 CLAUDE_BIN/UNPACK_BUDGET 两项可选变量"
```

---

## 自查（对照 spec-to-plan §5）

- `grep -c '^### Task '` 应为 7（见下方执行者自查命令）
- Global Constraints 段与 CLAUDE.md 逐字一致：已在计划顶部
- spec 每条 Requirement → Task：见「Spec Requirement → Task 对应表」，7 条全覆盖
- 每个 Task 有确切文件路径、完整代码、确切命令与预期输出：全部给出，无 TBD
- 前后 Task 类型名/签名一致：`DispatchOutcome`（Task4 定义，Task5/6 消费）、`resolve_claude_bin`/`build_headless_argv`（Task3 定义，Task4/6 消费）、`probe_signal`/`clear_signal_before`（Task1 定义，Task6 消费）
- 每个有副作用的动作独占一个步骤且带幂等键：Task 5 的 `effect_unpack_audit` 调用，幂等键 `{thread_id}:effect_unpack_audit:{msgid}:{kind}`（`business_key=f"{msgid}:{kind}"`）
- 端到端提取验证：见下节
