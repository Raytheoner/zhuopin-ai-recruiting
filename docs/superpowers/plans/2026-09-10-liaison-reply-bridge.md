# P0 · 回件桥＋第九态 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 名单内发送人的一条入站消息落库后，桥按串行原则定位该人在台账里的在途跟进信，把它改写成"第九态"（回件已到，待拆件，仍在途），失败绝不影响归档/入队，重投绝不重复标记。

**Architecture:** 一条 `compute_bridge_decision` 纯函数按台账文本＋发送人姓名判出四态之一（`marked` / `skipped_no_inflight` / `refused_serial_violation` / `skipped_already_marked`）并算出新台账文本；`run_bridge` 编排函数在值守线程里把归档结果喂给它，原子写台账、写审计 effect、（视情况）追加信号与起活注入点；`__main__.py` 在 `handle_inbound_message` 返回且 `route.admitted` 为真后调用它，整个调用包在 `try/except` 里，任何失败都只落一条 `bridge_failed` 审计，不上抛。

**Tech Stack:** Python 3.14、pytest、sqlite3（`tools/liaison/storage`）、复用 `app/storage/idempotency.py` 的 `idempotent_effect`。

**Spec:** `openspec/changes/liaison-reply-bridge-and-patrol/specs/liaison-reply-bridge/spec.md`（唯一需求源）＋ `openspec/changes/liaison-reply-bridge-and-patrol/design.md`（D1/D2/D3/D8/D9/D10/D11 与本单元相关）。

## Global Constraints

- 铁律 1：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作必须独占一个节点并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。为什么：业务写失败而幂等记录成功 ⇒ 系统判定"已执行"⇒ 永不重试。幂等本是防重复的保护，拆开事务后变成永久丢失的保证。
- 铁律 2：L3 Agent 全部是无副作用纯函数，副作用只在编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。本单元：`compute_bridge_decision` / `compute_ninth_state_cell` 必须是纯函数——⛔ 不读文件、不读时钟、不记日志。
- 合规红线：AI 只做排序推荐，不做自动淘汰；淘汰必须有人工确认节点并留痕。第九态的语义是"仍在途、串行闸仍锁"，⛔ 不得由本单元的任何分支把一封信自动推进到闭环态。
- 数据模型：`liaison_unpack_audit` 是 append-only 事实表，⛔ 不得就地改写既有行。

---

## 与 design.md 现状偏离的实现说明（review 必读）

本节记录三处 2026-09-16 实测代码现状与 design.md 字面假设之间的真实缺口，以及本计划采取的技术方案。这是 plan 层的技术决策（CLAUDE.md 决策代理表「design.md / plan 的技术方案审查」可代事项），不是对 spec 需求本身的变更。

### 偏离 1：`archived_relpath`（"入信归档件的相对路径"）在当前系统里几乎总是不存在

`tools/liaison/__main__.py:249-252` 明写：入站消息的 `attachment` 参数**恒为 `None`**（SDK 附件句柄下载是一条已登记的技术债，本包不解决）。这意味着 `archive_message()` 返回的 `ArchiveOutcome.attachments` **在当前系统里永远是空元组**——2026-09-16 的一条真实入站消息（`msgid=2a376681026302c7c9343bdf78aac10f`）证实了这一点：`liaison_message.attachments_json = '[]'`，`data/liaison/archive/` 目录下没有任何文件。

但 spec Requirement「第九态改写只动命中行且原状态原样接后」要求新状态列必须含"入信归档件的相对路径"，design D9 的文案模板里也写死了这一段。**这不是可以跳过的边缘情况，而是当前唯一会发生的情况。**

**方案**（Task 6）：`run_bridge` 判定要标记时，若 `outcome.attachments` 非空则用其第一项的 `relative_path`；若为空（当前恒为此），调用 `archive.compute_archive_path(thread_id=, msgid=, received_at=, filename="正文.txt", archive_root=)` 算出一个确定性路径，再调用 `attachments.store_attachment(content.encode("utf-8"), destination, archive_root=archive_root)` 把消息正文本身当"归档件"落盘——复用现成的临时文件＋fsync＋原子rename＋基于路径存在性与内容哈希的幂等短路逻辑，不新写一遍落盘代码。这样"入信归档件的相对路径"在当前唯一会出现的场景（纯文本、无附件）下也是一个真实存在的文件。

### 偏离 2：发送人姓名的名单查找目前不存在

`tools/liaison/whitelist.py::load_whitelist()` 只返回 `frozenset[str]`（userid 集合），`_validated_userid()` 在校验时丢弃了 `name` 字段。但桥要按"发送人姓名"匹配台账「收信人」列（台账写的是中文姓名，不是 userid）。

**方案**（Task 5）：重构 `whitelist.py` 内部，把校验步骤从"只提取 userid"改成"提取 (userid, name) 二元组"，`load_whitelist()` 的公开签名与既有行为逐字不变（只用元组的 userid 分量），新增 `load_whitelist_names(path) -> dict[str, str]` 复用同一套 YAML 解析与 fail-closed 管线，暴露 name 分量。

### 偏离 3：`run_bridge` 的信号追加／起活是给 P1 的注入点，不是 P0 的实现

design D10 的 `run_bridge(conn, *, inbound_result, sender_name, ledger_path, signal_path, now, dispatch)` 签名里的"信号追加"与"起活"分别对应 P1（`liaison-unpack-dispatch`，另一条并行 plan 泳道）的 `unpack/signal.py::append_signal` 与 `unpack/dispatch.py::dispatch_headless_unpack`——这两个模块在本 P0 单元完成时尚不存在。

**方案**（Task 7）：`run_bridge` 把"信号追加"与"起活"做成两个可选注入参数 `append_signal: Callable[[pathlib.Path, dict], bool] | None = None` 与 `dispatch: Callable[[], object] | None = None`，`None` 时只记一条 WARNING（"P1 尚未接入，本次不追加信号/不起活"），不是异常、不阻断台账与审计。这与 `tools/liaison/__main__.py::InboundPorts.reply`（同样是"留空壳接入点"，`None` 时留 WARNING 不静默）是同一约定（CLAUDE.md 部署约束③「鉴权中间件留空壳接入点……将来只换实现不换调用方」的同一模式）。P1 落地后，`__main__.py` 的接线点把这两个参数换成真实函数即可，`run_bridge` 内部逻辑不用动。

也因此，spec Requirement「同一消息重投不重复标记」里"信号里该 msgid 只出现一次"这一条断言，P0 只能验证"`append_signal` 被调用且传入正确的 item"（因为 dedup 是 P1 `append_signal` 自己的契约），完整的端到端信号去重由 P1 的测试与 §5 真实起活验收共同覆盖。

### run_bridge 的调用形状与 tasks.md 字面签名的差异

tasks.md 1.6 写的 `run_bridge(conn, *, inbound_result, ...)` 里 `inbound_result` 单个对象装不下 `thread_id`／`sender_userid`／`content`／`msgtype`／`received_at`（`InboundResult` 只有 `route`/`outcome`/`replied`/`enqueued`，`outcome` 只有 `msgid`/`newly_archived`/`attachments`）。Task 7 的 `run_bridge` 改为直接接收这些原语作为显式关键字参数，不透传整个 `InboundResult`——原因与 `handle_inbound_message` 本身的参数风格一致（该函数也是显式关键字参数而非"传一个大对象"）。

---

## Requirement → Task 对应表

| Spec Requirement | Task |
|---|---|
| 桥在归档与入队之后执行，且失败不影响二者 | Task 7（`try/except` 包裹＋`bridge_failed`）、Task 8（调用时机） |
| 名单内入站按串行原则定位在途信 | Task 2（`compute_bridge_decision` 按姓名＋状态前缀匹配）、Task 8（只在 `route.admitted` 为真时调用） |
| 无在途信则不标、不起活 | Task 2（`skipped_no_inflight`）、Task 7（该分支不写信号/不起活） |
| 同一收信人多封在途则拒标并告警 | Task 2（`refused_serial_violation`＋涉及编号列表）、Task 7（`effect_emit_alert`） |
| 第九态改写只动命中行且原状态原样接后 | Task 1（`compute_ninth_state_cell` 文案）、Task 2（只重写命中行） |
| 同一消息重投不重复标记 | Task 2（`skipped_already_marked` 幂等短路）、Task 4（`effect_unpack_audit` 幂等键）、Task 7（signal/dispatch 仍会被调用） |
| 桥的审计记录可按消息与结果计数 | Task 4（`liaison_unpack_audit` 表＋`assert_effect_log_identity`） |

---

### Task 1: 第九态文案纯函数 `compute_ninth_state_cell`

**Files:**
- Create: `tools/liaison/unpack/__init__.py`
- Create: `tools/liaison/unpack/bridge.py`
- Test: `tools/liaison/tests/test_unpack_bridge.py`

**Interfaces:**
- Produces: `tools/liaison/unpack/bridge.py::NINTH_STATE_MARKER: str`（`"📨 回件已到，待拆件"`）
- Produces: `compute_ninth_state_cell(original_cell_text: str, *, archived_relpath: str, now_cst: datetime) -> str`

**Files 内容说明**：`unpack/__init__.py` 为空文件（包标记）。`unpack/bridge.py` 本任务只写文案生成部分，Task 2/3/6/7 继续往同一文件追加。

- [ ] **Step 1: 建包骨架**

创建 `tools/liaison/unpack/__init__.py`（空文件）。

创建 `tools/liaison/unpack/bridge.py`：

```python
"""P0·回件桥＋第九态（design.md D1/D2/D3/D9/D10）。

**本模块分两层，⛔ 不许混在一起**（与 archive.py 同一纪律）：
- `compute_*` 是纯函数（工程铁律 2）：不读文件、不读时钟、不记日志。
- `run_bridge` 是编排点，在值守线程里被 `__main__.py::handle_message_frame`
  调用，做台账写、effect 写、信号/起活注入——全部包在 `try/except` 里，
  任何失败都不上抛（design D10）。
"""

from __future__ import annotations

from datetime import datetime

#: 第九态的语义标记：回件已到、待人工/会话拆件、仍在途、串行闸仍锁。
#: ⛔ 这段文字本身就是被 spec Scenario 逐字断言的契约，改动前先读
#: spec.md「第九态改写只动命中行且原状态原样接后」。
NINTH_STATE_MARKER = "📨 回件已到，待拆件"

#: 台账既有约定：`✅ 已推送` 起头的状态视为在途（followup.py::_ALREADY_SENT
#: 是同一枚举值的另一份拷贝，两边各自独立维护——那边管"是否已推送完成"，
#: 这边管"是否处于回件桥意义上的在途"，语义不同，⛔ 不合并成一个常量）。
ALREADY_PUSHED_PREFIX = "✅ 已推送"


def compute_ninth_state_cell(
    original_cell_text: str, *, archived_relpath: str, now_cst: datetime
) -> str:
    """把"发送状态"列的原文改写成第九态文案（design D9 逐字模板）。

    纯函数：不读时钟（`now_cst` 由调用方传入，工程铁律 2）、不读文件。

    ⚠️ `original_cell_text` 必须是**该单元格的完整原文**（调用方从 markdown
    表格行的 `split("|")` 结果里原样取出、不做任何清洗），因为 spec 要求
    "原状态列的完整原文"逐字接在分隔符之后——本函数不对它做 strip 之外的
    任何改写，`.strip()` 只是去掉表格单元格惯用的首尾空白（followup.py
    写单元格时也是 `f" {value} "` 这种前后各一个空格的padding，⛔ 不去掉
    这一层会让新文案两侧多出不对称的空白）。
    """
    time_text = now_cst.strftime("%Y-%m-%d %H:%M") + " CST"
    return (
        f"{NINTH_STATE_MARKER} {time_text}"
        f"（值守服务自动标记，入信归档 `{archived_relpath}`；"
        f"仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一）"
        f" ━━━ 原状态 ━━━ {original_cell_text.strip()}"
    )
```

- [ ] **Step 2: 写失败测试**

创建 `tools/liaison/tests/test_unpack_bridge.py`：

```python
"""P0·回件桥＋第九态。四态判定、原子写、审计 effect、`run_bridge` 编排。"""

from __future__ import annotations

from datetime import datetime

import pytest

from tools.liaison import session
from tools.liaison.unpack.bridge import (
    ALREADY_PUSHED_PREFIX,
    NINTH_STATE_MARKER,
    compute_ninth_state_cell,
)

T0 = datetime(2026, 9, 10, 14, 3, 0, tzinfo=session.CHINA_TZ)


def test_ninth_state_cell_matches_the_literal_design_template():
    """design.md D9 的逐字模板：标记＋时刻＋归档路径＋分隔符＋原状态原文。"""
    result = compute_ninth_state_cell(
        "`✅ 已推送 2026-09-09`",
        archived_relpath="data/liaison/archive/ShaoPeiShen/20260910/msg__正文.txt",
        now_cst=T0,
    )
    assert result == (
        "📨 回件已到，待拆件 2026-09-10 14:03 CST"
        "（值守服务自动标记，入信归档 "
        "`data/liaison/archive/ShaoPeiShen/20260910/msg__正文.txt`；"
        "仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一） "
        "━━━ 原状态 ━━━ `✅ 已推送 2026-09-09`"
    )


def test_ninth_state_cell_preserves_original_text_verbatim_including_backticks():
    """「原状态列的完整原文」＝原样保留，⛔ 不剥离台账既有的反引号包裹。"""
    result = compute_ninth_state_cell(
        "`✅ 已推送 2026-09-09`", archived_relpath="x/y.txt", now_cst=T0
    )
    assert result.endswith("━━━ 原状态 ━━━ `✅ 已推送 2026-09-09`")


def test_ninth_state_cell_strips_only_the_table_cell_padding_whitespace():
    """调用方从 `split(\"|\")` 拿到的单元格通常带一层 padding 空格，
    只去掉这一层，不去掉原文内部的任何字符。"""
    result = compute_ninth_state_cell(
        "  ✅ 已推送 2026-09-09  ", archived_relpath="x/y.txt", now_cst=T0
    )
    assert result.endswith("━━━ 原状态 ━━━ ✅ 已推送 2026-09-09")


def test_ninth_state_marker_constant_matches_the_spec_literal_text():
    assert NINTH_STATE_MARKER == "📨 回件已到，待拆件"


def test_already_pushed_prefix_constant_matches_the_ledger_convention():
    assert ALREADY_PUSHED_PREFIX == "✅ 已推送"
```

- [ ] **Step 3: 跑测试确认全部通过**

Run: `cd /Users/paulshao/Projects/HumanResource && python -m pytest tools/liaison/tests/test_unpack_bridge.py -v`
Expected: 5 个用例全部 PASS（本任务只实现了 `compute_ninth_state_cell`，测试文件里其余函数留给后续 Task 追加，本步骤只跑当前已存在的这 5 条）。

- [ ] **Step 4: Commit**

```bash
git add tools/liaison/unpack/__init__.py tools/liaison/unpack/bridge.py tools/liaison/tests/test_unpack_bridge.py
git commit -m "feat(liaison): P0 第九态文案纯函数 compute_ninth_state_cell"
```

---

### Task 2: 决策纯函数 `compute_bridge_decision`（四态，D1/D2 先红后绿）

**Files:**
- Modify: `tools/liaison/unpack/bridge.py`
- Test: `tools/liaison/tests/test_unpack_bridge.py`

**Interfaces:**
- Consumes: Task 1 的 `compute_ninth_state_cell`、`NINTH_STATE_MARKER`、`ALREADY_PUSHED_PREFIX`
- Produces: `BridgeOutcome`（枚举风格的 `str` 常量：`"marked"` / `"skipped_no_inflight"` / `"refused_serial_violation"` / `"skipped_already_marked"`）
- Produces: `@dataclass(frozen=True) class BridgeDecision`：字段 `outcome: str`、`new_ledger_text: str | None`（仅 `marked` 时非 `None`）、`matched_letter_numbers: tuple[str, ...]`（`marked` 时恰一个；`refused_serial_violation` 时 ≥2 个；其余为空元组）
- Produces: `compute_bridge_decision(ledger_text: str, *, sender_name: str, archived_relpath: str, now_cst: datetime) -> BridgeDecision`

台账表格布局（`docs/跟进信/README-跟进信清单.md` 实测，第 41 行起）：

```
| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |
```

按 `line.split("|")` 后（行以 `|` 起止，产出首尾各一个空串），列索引固定为 `cells[1]`＝编号、`cells[3]`＝收信人、`cells[-2]`＝发送状态——与 `followup.py::compute_backfilled_ledger` 用 `cells[1]`（编号）/`cells[-2]`（发送状态）是同一张表、同一约定，这里额外用 `cells[3]` 取收信人。**本函数不 import `followup.py`**：`followup.py::compute_backfilled_ledger` 按编号做**精确匹配**且只处理"改成已推送"这一种转移，本函数按姓名做匹配、按状态前缀做在途判定、且要处理四种转移——签名与匹配谓词都不同，硬套用会强迫 `followup.py` 为了适配这里新增参数，牵动一个已经上线且有独立测试覆盖的模块；两边复用的是"按 `|` 分列、只重写命中行、`\"\".join(lines)` 拼回"这个**手法**，不是同一段可调用的代码。

- [ ] **Step 1: 写失败测试（D1：无在途 → `skipped_no_inflight`）**

在 `tools/liaison/tests/test_unpack_bridge.py` 追加：

```python
from tools.liaison.unpack.bridge import BridgeDecision, compute_bridge_decision

LEDGER_HEADER = (
    "| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |\n"
    "|---|---|---|---|---|---|\n"
)


def _row(number: str, recipient: str, status: str) -> str:
    return f"| `{number}` | 2026-09-09 | {recipient} | 事项 | 无 | {status} |\n"


def test_no_inflight_row_leaves_the_ledger_untouched():
    """D1：发送人在台账里没有任何在途信 ⇒ skipped_no_inflight，台账逐字节不变。"""
    ledger = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 无需回复`")
    decision = compute_bridge_decision(
        ledger, sender_name="汤丽萍", archived_relpath="x/y.txt", now_cst=T0
    )
    assert decision.outcome == "skipped_no_inflight"
    assert decision.new_ledger_text is None
    assert decision.matched_letter_numbers == ()


def test_sender_not_in_ledger_at_all_is_also_no_inflight():
    ledger = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`")
    decision = compute_bridge_decision(
        ledger, sender_name="邵培申", archived_relpath="x/y.txt", now_cst=T0
    )
    assert decision.outcome == "skipped_no_inflight"
```

- [ ] **Step 2: 跑测试确认先红**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "no_inflight" -v`
Expected: FAIL（`compute_bridge_decision` 尚未定义，`ImportError`）。

- [ ] **Step 3: 追加 D2（多封在途 → `refused_serial_violation`）的失败测试**

追加：

```python
def test_two_inflight_rows_refuse_and_list_both_numbers():
    """D2：同一收信人 ≥2 行在途 ⇒ 拒标，涉及编号可枚举，台账不变。"""
    ledger = (
        LEDGER_HEADER
        + _row("人事部#2", "汤丽萍", "`✅ 已推送 2026-09-08`")
        + _row("人事部#3", "汤丽萍", "`✅ 已推送 2026-09-09`")
    )
    decision = compute_bridge_decision(
        ledger, sender_name="汤丽萍", archived_relpath="x/y.txt", now_cst=T0
    )
    assert decision.outcome == "refused_serial_violation"
    assert decision.new_ledger_text is None
    assert set(decision.matched_letter_numbers) == {"人事部#2", "人事部#3"}
```

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "two_inflight" -v`
Expected: FAIL（`ImportError`，与上面同一个原因——两条 D1/D2 用例此刻都处于"先红"）。

- [ ] **Step 4: 实现 `compute_bridge_decision`（让 D1/D2 与后续用例一起转绿）**

在 `tools/liaison/unpack/bridge.py` 追加（`from __future__ import annotations` 与现有 import 保持在文件顶部不变，新增下列 import 与代码）：

```python
from dataclasses import dataclass

OUTCOME_MARKED = "marked"
OUTCOME_SKIPPED_NO_INFLIGHT = "skipped_no_inflight"
OUTCOME_REFUSED_SERIAL_VIOLATION = "refused_serial_violation"
OUTCOME_SKIPPED_ALREADY_MARKED = "skipped_already_marked"


@dataclass(frozen=True)
class BridgeDecision:
    """`compute_bridge_decision` 的判定结果。纯数据。"""

    outcome: str
    new_ledger_text: str | None
    matched_letter_numbers: tuple[str, ...]


def _strip_outer_backtick(cell: str) -> str:
    """去掉状态列惯用的单层反引号包裹，只为**判定**用，⛔ 不用于改写输出
    （改写时 `compute_ninth_state_cell` 保留完整原文，见该函数 docstring）。
    """
    text = cell.strip()
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        return text[1:-1].strip()
    return text


def _is_inflight_status(status_cell: str) -> bool:
    text = _strip_outer_backtick(status_cell)
    return text.startswith(ALREADY_PUSHED_PREFIX) or NINTH_STATE_MARKER in text


def _is_already_marked_status(status_cell: str) -> bool:
    return NINTH_STATE_MARKER in _strip_outer_backtick(status_cell)


def _extract_letter_number(number_cell: str) -> str | None:
    text = number_cell.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return text[1:-1].strip()
    return text or None


def compute_bridge_decision(
    ledger_text: str,
    *,
    sender_name: str,
    archived_relpath: str,
    now_cst: datetime,
) -> BridgeDecision:
    """按 spec「名单内入站按串行原则定位在途信」判出四态之一。

    纯函数：不读文件、不读时钟（`now_cst` 由调用方传入）、不记日志（工程铁律 2）。

    判定顺序（spec 逐字）：
    1. 按 `sender_name` 匹配"收信人"列，筛出"发送状态"以 `✅ 已推送` 起头
       或已含第九态标记的行（在途行）。
    2. 0 行 ⇒ `skipped_no_inflight`。
    3. ≥2 行 ⇒ `refused_serial_violation`，`matched_letter_numbers` 列出全部命中编号。
    4. 恰 1 行且已是第九态 ⇒ `skipped_already_marked`（幂等短路，同一消息重投
       或第九态期间又来一条新消息都会落进这一支——两者在"台账层面"看起来
       完全一样，`run_bridge` 用审计表的幂等键区分"是否要重复起活"）。
    5. 恰 1 行且未是第九态 ⇒ `marked`，改写该行"发送状态"列为第九态文案，
       台账其它行逐字节不变。
    """
    lines = ledger_text.splitlines(keepends=True)
    inflight_indices: list[int] = []
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 6:
            continue
        recipient = cells[3].strip()
        if recipient != sender_name:
            continue
        if _is_inflight_status(cells[-2]):
            inflight_indices.append(index)

    if not inflight_indices:
        return BridgeDecision(
            outcome=OUTCOME_SKIPPED_NO_INFLIGHT, new_ledger_text=None, matched_letter_numbers=()
        )

    if len(inflight_indices) > 1:
        numbers = tuple(
            number
            for index in inflight_indices
            if (number := _extract_letter_number(lines[index].split("|")[1])) is not None
        )
        return BridgeDecision(
            outcome=OUTCOME_REFUSED_SERIAL_VIOLATION,
            new_ledger_text=None,
            matched_letter_numbers=numbers,
        )

    hit_index = inflight_indices[0]
    hit_cells = lines[hit_index].split("|")
    letter_number = _extract_letter_number(hit_cells[1])
    matched = (letter_number,) if letter_number is not None else ()

    if _is_already_marked_status(hit_cells[-2]):
        return BridgeDecision(
            outcome=OUTCOME_SKIPPED_ALREADY_MARKED,
            new_ledger_text=None,
            matched_letter_numbers=matched,
        )

    hit_cells[-2] = (
        f" {compute_ninth_state_cell(hit_cells[-2], archived_relpath=archived_relpath, now_cst=now_cst)} "
    )
    lines[hit_index] = "|".join(hit_cells)
    return BridgeDecision(
        outcome=OUTCOME_MARKED,
        new_ledger_text="".join(lines),
        matched_letter_numbers=matched,
    )
```

- [ ] **Step 5: 跑 D1/D2 用例确认转绿**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "no_inflight or two_inflight or sender_not_in_ledger" -v`
Expected: 3 个用例全部 PASS。

- [ ] **Step 6: 补齐 `marked` 与 `skipped_already_marked` 两态的测试（含"只改命中行"断言）**

追加：

```python
def test_exactly_one_inflight_row_gets_marked_and_other_rows_are_untouched():
    other_row = _row("人事部#9", "邵培申", "`✅ 已推送 2026-09-01`")
    ledger = LEDGER_HEADER + other_row + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`")
    decision = compute_bridge_decision(
        ledger,
        sender_name="汤丽萍",
        archived_relpath="data/liaison/archive/x/20260910/y__正文.txt",
        now_cst=T0,
    )
    assert decision.outcome == "marked"
    assert decision.matched_letter_numbers == ("人事部#1",)
    new_lines = decision.new_ledger_text.splitlines(keepends=True)
    old_lines = ledger.splitlines(keepends=True)
    # 表头与其它行逐字节不变。
    assert new_lines[0] == old_lines[0]
    assert new_lines[1] == old_lines[1]
    assert new_lines[2] == old_lines[2]  # 邵培申那一行
    assert new_lines[4] == old_lines[4].replace(
        "`✅ 已推送 2026-09-09`",
        "📨 回件已到，待拆件 2026-09-10 14:03 CST"
        "（值守服务自动标记，入信归档 "
        "`data/liaison/archive/x/20260910/y__正文.txt`；"
        "仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一） "
        "━━━ 原状态 ━━━ `✅ 已推送 2026-09-09`",
    )


def test_already_ninth_state_row_is_skipped_and_ledger_unchanged():
    ninth_state_cell = (
        "`📨 回件已到，待拆件 2026-09-10 14:03 CST"
        "（值守服务自动标记，入信归档 `data/liaison/archive/x/20260910/y__正文.txt`；"
        "仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一） "
        "━━━ 原状态 ━━━ ✅ 已推送 2026-09-09`"
    )
    ledger = LEDGER_HEADER + _row("人事部#1", "汤丽萍", ninth_state_cell)
    decision = compute_bridge_decision(
        ledger, sender_name="汤丽萍", archived_relpath="whatever/new.txt", now_cst=T0
    )
    assert decision.outcome == "skipped_already_marked"
    assert decision.new_ledger_text is None
    assert decision.matched_letter_numbers == ("人事部#1",)
```

- [ ] **Step 7: 跑全部测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -v`
Expected: 全部 PASS（此刻应有 11 条：Task 1 的 5 条 ＋ 本 Task 的 6 条）。

- [ ] **Step 8: Commit**

```bash
git add tools/liaison/unpack/bridge.py tools/liaison/tests/test_unpack_bridge.py
git commit -m "feat(liaison): P0 compute_bridge_decision 四态判定（D1/D2 先红后绿）"
```

---

### Task 3: 台账原子写 `write_ledger_atomic`

**Files:**
- Modify: `tools/liaison/unpack/bridge.py`
- Test: `tools/liaison/tests/test_unpack_bridge.py`

**Interfaces:**
- Consumes: 无（独立工具函数）
- Produces: `write_ledger_atomic(path: pathlib.Path, text: str) -> None`（写失败原样抛给调用方，由 `run_bridge` 转 `bridge_failed`）

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_write_ledger_atomic_replaces_file_content(tmp_path):
    from tools.liaison.unpack.bridge import write_ledger_atomic

    target = tmp_path / "README-跟进信清单.md"
    target.write_text("旧内容", encoding="utf-8")
    write_ledger_atomic(target, "新内容")
    assert target.read_text(encoding="utf-8") == "新内容"


def test_write_ledger_atomic_leaves_no_temp_file_behind(tmp_path):
    from tools.liaison.unpack.bridge import write_ledger_atomic

    target = tmp_path / "README-跟进信清单.md"
    write_ledger_atomic(target, "内容")
    assert list(tmp_path.iterdir()) == [target]


def test_write_ledger_atomic_propagates_failure_to_the_caller(tmp_path):
    from tools.liaison.unpack.bridge import write_ledger_atomic

    missing_parent = tmp_path / "no-such-dir" / "README-跟进信清单.md"
    with pytest.raises(OSError):
        write_ledger_atomic(missing_parent, "内容")
```

- [ ] **Step 2: 跑测试确认先红**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "write_ledger_atomic" -v`
Expected: FAIL（`ImportError: cannot import name 'write_ledger_atomic'`）。

- [ ] **Step 3: 实现**

在 `tools/liaison/unpack/bridge.py` 追加（新增顶部 import `os`、`pathlib`、`tempfile`）：

```python
import os
import pathlib
import tempfile


def write_ledger_atomic(path: pathlib.Path, text: str) -> None:
    """临时文件 ＋ `os.replace` 写台账（design D10 逐字）。⛔ 不用 `with`
    （第 2 章事务扫描器会把 `with <名字>:` 判为隐式提交违规——虽然本函数不碰
    数据库，但扫描器按语法结构扫，不区分"这段是不是真的在碰事务"）。

    写失败（目录不存在、权限不足、磁盘满）原样向上抛，⛔ 不在这里吞——
    `run_bridge` 接住它转 `bridge_failed` 审计，见 design D10「台账写失败 ⇒
    不落信号、不起活」。
    """
    handle_fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    temp_path = pathlib.Path(temp_name)
    handle = os.fdopen(handle_fd, "w", encoding="utf-8")
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(temp_path, path)
```

⚠️ 注意：`test_write_ledger_atomic_propagates_failure_to_the_caller` 依赖 `tempfile.mkstemp(dir=missing_parent.parent, ...)` 在目录不存在时抛 `FileNotFoundError`（是 `OSError` 子类）——`mkstemp` 本身就会在 `dir` 不存在时抛这个异常，不需要额外处理。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "write_ledger_atomic" -v`
Expected: 3 个用例全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/bridge.py tools/liaison/tests/test_unpack_bridge.py
git commit -m "feat(liaison): P0 台账原子写 write_ledger_atomic"
```

---

### Task 4: `liaison_unpack_audit` 表 ＋ `effect_unpack_audit`

**Files:**
- Modify: `tools/liaison/storage/schema.py`
- Modify: `tools/liaison/storage/effects.py`
- Test: `tools/liaison/tests/test_liaison_effects.py`

**Interfaces:**
- Produces: `tools/liaison/storage/schema.py::UNPACK_AUDIT_SCHEMA`（拼进 `SCHEMA`）
- Produces: `tools/liaison/storage/effects.py::effect_unpack_audit(conn, *, thread_id, business_key, sender_userid, letter_number, kind, detail="") -> str`（`business_key` 由调用方传 `f"{msgid}:{kind}"`，见下）
- Consumes: `app/storage/idempotency.py::idempotent_effect`（已存在，不改）

- [ ] **Step 1: 在 schema.py 加审计表 DDL**

`tools/liaison/storage/schema.py` 追加（`OUTAGE_WINDOW_SCHEMA` 与 `GROUP_NOTIFY_SCHEMA` 之间任意位置，`SCHEMA` 总常量里补上这一段）：

```python
#: P0/P1 共用的拆件审计表（design D11）。append-only，⛔ 不得就地改写既有行
#: （合规红线：本表是"AI 只做排序推荐，不做自动淘汰"之外另一条不可回滚的
#: 审计契约——第九态是否真的发生、何时发生，只有这张表说了算）。
#:
#: `kind` 的九个取值里，本表由 P0（回件桥）建，但 P1（信号与打标即开班）
#: 会往同一张表写 `signal_file_replaced`/`dispatch_*` 四个值——CHECK 约束
#: 必须把全部九个值一次性放进去，不能只放 P0 用得到的五个，否则 P1 一上线
#: 这条 CHECK 就会拒绝合法写入。
UNPACK_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS liaison_unpack_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 与 effect_log 同域：assert_effect_log_identity 的通用脚手架按 thread_id
    -- 分组比对 effect_log 与业务表的行数，本表必须带这一列才能被那套脚手架
    -- 直接复用（端到端提取验证实测发现：不带这一列会在 Task 4 Step 6 炸出
    -- `sqlite3.OperationalError: no such column: thread_id`）。
    thread_id TEXT NOT NULL,
    msgid TEXT NOT NULL,
    sender_userid TEXT NOT NULL,
    letter_number TEXT,
    kind TEXT NOT NULL CHECK (kind IN (
        'bridge_marked',
        'bridge_skipped_no_inflight',
        'bridge_refused_serial_violation',
        'bridge_skipped_already_marked',
        'bridge_failed',
        'signal_file_replaced',
        'dispatch_started',
        'dispatch_skipped_busy',
        'dispatch_failed'
    )),
    detail TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 「按消息与结果计数」（spec 原文）要按 msgid 分组，也要按 kind 分组。
CREATE INDEX IF NOT EXISTS idx_liaison_unpack_audit_msgid ON liaison_unpack_audit (msgid);
CREATE INDEX IF NOT EXISTS idx_liaison_unpack_audit_kind ON liaison_unpack_audit (kind, at);
"""
```

并把 `SCHEMA` 总常量（文件末尾）改成：

```python
SCHEMA = (
    EFFECT_LOG_SCHEMA
    + MESSAGE_AND_TASK_SCHEMA
    + OUTAGE_WINDOW_SCHEMA
    + GROUP_NOTIFY_SCHEMA
    + UNPACK_AUDIT_SCHEMA
)
```

- [ ] **Step 2: 写失败测试（表存在＋CHECK 约束）**

在 `tools/liaison/tests/test_liaison_effects.py` 追加（复用已有的 `conn` fixture）：

```python
def test_unpack_audit_table_exists_with_all_nine_kind_values(conn):
    kinds = [
        "bridge_marked",
        "bridge_skipped_no_inflight",
        "bridge_refused_serial_violation",
        "bridge_skipped_already_marked",
        "bridge_failed",
        "signal_file_replaced",
        "dispatch_started",
        "dispatch_skipped_busy",
        "dispatch_failed",
    ]
    for kind in kinds:
        conn.execute(
            "INSERT INTO liaison_unpack_audit (thread_id, msgid, sender_userid, kind) "
            "VALUES (?, ?, ?, ?)",
            ("t1", f"msg-{kind}", "u1", kind),
        )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM liaison_unpack_audit").fetchone()[0] == len(kinds)


def test_unpack_audit_table_rejects_an_unknown_kind(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_unpack_audit (thread_id, msgid, sender_userid, kind) "
            "VALUES (?, ?, ?, ?)",
            ("t1", "msg-1", "u1", "not_a_real_kind"),
        )
```

Run: `python -m pytest tools/liaison/tests/test_liaison_effects.py -k "unpack_audit_table" -v`
Expected: FAIL（表不存在，`sqlite3.OperationalError: no such table`）。

- [ ] **Step 3: 跑测试确认转绿**

Run: `python -m pytest tools/liaison/tests/test_liaison_effects.py -k "unpack_audit_table" -v`
Expected: 2 个用例 PASS（Step 1 的 DDL 已经落地）。

- [ ] **Step 4: 加 `effect_unpack_audit` ＋ 登记 `EFFECT_NODE_TO_TABLE`**

修改 `tools/liaison/storage/effects.py`：

```python
EFFECT_NODE_TO_TABLE = {
    "effect_archive_message": "liaison_message",
    "effect_enqueue_task": "liaison_task",
    "effect_unpack_audit": "liaison_unpack_audit",
}
```

并在文件末尾追加：

```python
@idempotent_effect("effect_unpack_audit")
def effect_unpack_audit(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    sender_userid: str,
    letter_number: str | None,
    kind: str,
    detail: str = "",
) -> str:
    """写一条拆件审计记录。`business_key` 调用方必须传 `f"{msgid}:{kind}"`
    （design D11 的幂等键是 `{thread_id}:effect_unpack_audit:{msgid}:{kind}`，
    而 `idempotent_effect` 固定拼 `f"{thread_id}:{node_name}:{business_key}"`，
    ⛔ 不能只传 `msgid`——那会让同一条消息的 `bridge_marked`／`bridge_failed`
    等不同 `kind` 互相当成重复短路掉）。

    `msgid` 本身不单独入参：调用方已经把它编进 `business_key`，这里如果
    再单独存一列 `msgid`，两处必须永远一致，不如从 `business_key` 里切出来，
    但为了 SQL 按 msgid 查询方便，仍然显式建了 `msgid` 列——因此调用方
    还要把裸 `msgid` 通过下面这行插入。⚠️ 这不是重复存储两份真源：
    `business_key`（幂等键分量）与 `msgid`（业务列）服务于不同目的，
    `msgid` 列的值就是从 `business_key` 按 `:` 切出来的第一段，
    由调用方保证一致（`run_bridge` 是唯一调用方）。
    """
    msgid = business_key.split(":", 1)[0]
    cursor = conn.execute(
        "INSERT INTO liaison_unpack_audit "
        "(thread_id, msgid, sender_userid, letter_number, kind, detail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, msgid, sender_userid, letter_number, kind, detail),
    )
    return str(cursor.lastrowid)
```

- [ ] **Step 5: 写幂等键测试**

追加：

```python
def test_effect_unpack_audit_is_idempotent_per_msgid_and_kind(conn):
    from tools.liaison.storage.effects import effect_unpack_audit

    effect_unpack_audit(
        conn,
        thread_id="ShaoPeiShen",
        business_key="msg-1:bridge_marked",
        sender_userid="ShaoPeiShen",
        letter_number="人事部#1",
        kind="bridge_marked",
    )
    effect_unpack_audit(
        conn,
        thread_id="ShaoPeiShen",
        business_key="msg-1:bridge_marked",
        sender_userid="ShaoPeiShen",
        letter_number="人事部#1",
        kind="bridge_marked",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM liaison_unpack_audit WHERE msgid = 'msg-1'"
    ).fetchone()[0] == 1


def test_effect_unpack_audit_allows_different_kinds_for_the_same_msgid(conn):
    from tools.liaison.storage.effects import effect_unpack_audit

    effect_unpack_audit(
        conn,
        thread_id="ShaoPeiShen",
        business_key="msg-1:bridge_marked",
        sender_userid="ShaoPeiShen",
        letter_number="人事部#1",
        kind="bridge_marked",
    )
    effect_unpack_audit(
        conn,
        thread_id="ShaoPeiShen",
        business_key="msg-1:dispatch_started",
        sender_userid="ShaoPeiShen",
        letter_number="人事部#1",
        kind="dispatch_started",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM liaison_unpack_audit WHERE msgid = 'msg-1'"
    ).fetchone()[0] == 2


def test_unpack_audit_effect_log_identity_holds(conn):
    """铁律 1 的恒等不变式：登记进 EFFECT_NODE_TO_TABLE 之后，通用脚手架
    `assert_effect_log_identity` 直接可以验证本节点，不需要另写断言。"""
    from tools.liaison.storage.effects import effect_unpack_audit

    effect_unpack_audit(
        conn,
        thread_id="ShaoPeiShen",
        business_key="msg-1:bridge_marked",
        sender_userid="ShaoPeiShen",
        letter_number="人事部#1",
        kind="bridge_marked",
    )
    assert_effect_log_identity(conn)
```

- [ ] **Step 6: 跑全部测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: 全部 PASS（含既有用例与本任务新增的 5 条）。

- [ ] **Step 7: Commit**

```bash
git add tools/liaison/storage/schema.py tools/liaison/storage/effects.py tools/liaison/tests/test_liaison_effects.py
git commit -m "feat(liaison): P0 liaison_unpack_audit 表与 effect_unpack_audit"
```

---

### Task 5: `whitelist.load_whitelist_names`（姓名映射，不改变 `load_whitelist` 既有行为）

**Files:**
- Modify: `tools/liaison/whitelist.py`
- Test: `tools/liaison/tests/test_whitelist.py`

**Interfaces:**
- Produces: `load_whitelist_names(path: Path | None = None) -> dict[str, str]`（userid → name）
- Modifies internal: `_validated_userid` → `_validated_member`（返回 `tuple[str, Any] | None`）；`_read_roster` → `_read_roster_members`（返回 `list[tuple[str, Any]]`）
- **不变**：`load_whitelist(path) -> frozenset[str]`、`admit(sender_userid, path) -> bool`、`compute_admission` 的公开签名与行为逐字不变——`tools/liaison/tests/test_whitelist.py` 现有全部用例（约 30 条）必须原样全绿，不许修改。

- [ ] **Step 1: 写新函数的失败测试**

在 `tools/liaison/tests/test_whitelist.py` 追加（复用文件已有的 import 与 `SHIPPED_CONFIG` 等既有常量，不新增 import 冲突）：

```python
def test_load_whitelist_names_maps_userid_to_name(tmp_path):
    from tools.liaison.whitelist import load_whitelist_names

    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        "  - userid: TangLiPing\n"
        "    name: 汤丽萍\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    assert load_whitelist_names(path) == {"TangLiPing": "汤丽萍"}


def test_load_whitelist_names_returns_empty_dict_on_missing_file(tmp_path):
    from tools.liaison.whitelist import load_whitelist_names

    assert load_whitelist_names(tmp_path / "absent.yaml") == {}


def test_load_whitelist_names_does_not_break_load_whitelist_when_name_is_blank(tmp_path):
    """userid 合法但 name 是空字符串：`load_whitelist()` 仍然要放行这个
    userid（既有行为不变），`load_whitelist_names()` 只是不给它一个姓名映射。"""
    from tools.liaison.whitelist import load_whitelist, load_whitelist_names

    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        "  - userid: TangLiPing\n"
        "    name: \"\"\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    assert load_whitelist(path) == frozenset({"TangLiPing"})
    assert load_whitelist_names(path) == {}


def test_load_whitelist_names_matches_the_shipped_config():
    from tools.liaison.whitelist import load_whitelist_names

    assert load_whitelist_names(SHIPPED_CONFIG) == {
        "TangLiPing": "汤丽萍",
        "ShaoPeiShen": "邵培申",
    }
```

⚠️ Step 1 里最后一条用例引用了文件里已有的 `SHIPPED_CONFIG` 常量（第 333 行 `load_whitelist(SHIPPED_CONFIG)` 已经在用它）——先用 `grep -n "SHIPPED_CONFIG =" tools/liaison/tests/test_whitelist.py` 确认真实成员姓名拼写（`ShaoPeiShen` 的大小写以 `tools/liaison/config/whitelist.yaml` 实际内容为准，不要凭记忆敲）。

Run: `grep -n "SHIPPED_CONFIG\s*=" tools/liaison/tests/test_whitelist.py; cat tools/liaison/config/whitelist.yaml`
Expected: 看到 `SHIPPED_CONFIG` 指向 `tools/liaison/config/whitelist.yaml`（或其常量路径），以及文件里邵培申那条的 `userid`/`name` 精确拼写，据此核对/修正上面测试用例里的字面量。

- [ ] **Step 2: 跑测试确认先红**

Run: `python -m pytest tools/liaison/tests/test_whitelist.py -k "load_whitelist_names" -v`
Expected: FAIL（`ImportError: cannot import name 'load_whitelist_names'`）。

- [ ] **Step 3: 重构 `_validated_userid` → `_validated_member`**

把 `tools/liaison/whitelist.py` 里的：

```python
def _validated_userid(
    entry: Any, index: int, path: Path, failures: _FailureLog
) -> str | None:
```

整段函数体保持不变（字段校验逻辑一字不改），只改函数名与返回值：

```python
def _validated_member(
    entry: Any, index: int, path: Path, failures: _FailureLog
) -> tuple[str, Any] | None:
    """校验单条名单条目，返回 `(userid, 原始 name 值)` 或 `None`（整条丢弃）。

    ⛔ 校验规则与原 `_validated_userid` 逐字相同——只有 `userid` 无效才丢弃
    整条。`name` 的取值合法性检查刻意**不**放在这里，放到
    `load_whitelist_names` 自己的聚合步骤：如果这里连 `name` 的值也校验，
    一条"`userid` 合法但 `name` 恰好是空串"的记录会突然从 `load_whitelist()`
    的结果里消失——那是新增 `name` 消费方引入的、与 `load_whitelist()`
    毫无关系的副作用，`load_whitelist()` 现有近 30 条用例都假设了这条不发生。
    """
```

（函数体其余部分——`Mapping` 校验、`extra`/`missing` 字段检查——原样保留，只把最后一段：

```python
    userid = entry["userid"]
    if not isinstance(userid, str) or not userid.strip():
        failures.error(
            "准入名单第 %d 条 userid 为空或非字符串，整条丢弃（该成员不会被准入）：path=%s",
            index,
            path,
        )
        return None

    return userid.strip()
```

改成：

```python
    userid = entry["userid"]
    if not isinstance(userid, str) or not userid.strip():
        failures.error(
            "准入名单第 %d 条 userid 为空或非字符串，整条丢弃（该成员不会被准入）：path=%s",
            index,
            path,
        )
        return None

    return userid.strip(), entry["name"]
```

）

- [ ] **Step 4: 重构 `_read_roster` → `_read_roster_members`**

把 `_read_roster` 函数名改为 `_read_roster_members`，返回类型标注改为 `list[tuple[str, Any]]`，函数体内**所有** `return frozenset()` 改成 `return []`，最后一段：

```python
    admitted = {
        userid
        for index, entry in enumerate(members)
        if (userid := _validated_userid(entry, index, path, failures)) is not None
    }

    if not admitted:
        failures.error("准入名单为零条有效条目，全部发送人判为未命中：path=%s", path)

    return frozenset(admitted)
```

改成：

```python
    validated = [
        pair
        for index, entry in enumerate(members)
        if (pair := _validated_member(entry, index, path, failures)) is not None
    ]

    if not validated:
        failures.error("准入名单为零条有效条目，全部发送人判为未命中：path=%s", path)

    return validated
```

- [ ] **Step 5: 重写 `load_whitelist`，新增 `load_whitelist_names`**

把 `load_whitelist` 函数体里唯一一处调用点：

```python
    try:
        return _read_roster(path, failures)
    except Exception as exc:  # noqa: BLE001
```

改成：

```python
    try:
        members = _read_roster_members(path, failures)
    except Exception as exc:  # noqa: BLE001
```

并把该 `try` 块正常路径的返回值（原来是 `return _read_roster(path, failures)` 已经在 try 里直接 return，现在要在 try 块末尾显式转换）改写为：

```python
    if path is None:
        path = DEFAULT_WHITELIST_PATH
    failures = _FailureLog()
    try:
        members = _read_roster_members(path, failures)
        return frozenset(userid for userid, _name in members)
    except Exception as exc:  # noqa: BLE001
        ...  # 原有异常处理逻辑一字不动，仍然 return frozenset()
    finally:
        failures.finish()
```

（`except` 分支内部那大段"未预期异常"的处理逻辑——含 `frames = "; ".join(...)`、`failures.error(...)`——原样保留，不做任何改动，只是它所在的函数体上面多了一行 `members = _read_roster_members(...)` 和下面多了一行 `return frozenset(...)`。）

在 `load_whitelist` 函数之后、`admit` 函数之前，插入新函数：

```python
def load_whitelist_names(path: Path | None = None) -> dict[str, str]:
    """读名单文件，返回 userid → name 的映射。

    与 `load_whitelist()` 共用 `_read_roster_members()` 这同一套 YAML 解析、
    fail-closed、TD-15 去重日志管线，⛔ 不重写一份新的解析逻辑。

    `name` 为空或非字符串的成员**不会**出现在返回的映射里（但仍然会出现在
    `load_whitelist()` 的 userid 集合里——两个函数对同一条脏数据的容忍度
    不同是刻意的：`load_whitelist()` 只关心"能不能识别这个人"，本函数关心
    "能不能显示这个人的名字"，后者的门槛更高、后果更轻——查不到姓名只是
    `run_bridge` 那边把这条消息当"无在途信"处理，不是拒绝整个人的准入）。
    """
    if path is None:
        path = DEFAULT_WHITELIST_PATH
    failures = _FailureLog()
    try:
        members = _read_roster_members(path, failures)
    except Exception as exc:  # noqa: BLE001
        frames = "; ".join(
            f"{frame.filename}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)
        )
        if failures.fingerprint is None:
            failures.fingerprint = _content_fingerprint(
                f"{type(exc).__name__}|{path!r}".encode("utf-8", "replace")
            )
        failures.error(
            "准入名单加载出现未预期异常（姓名映射），按空映射处理（⛔ 不记录异常消息本身）："
            "path=%s error_type=%s frames=%s",
            path,
            type(exc).__name__,
            frames,
        )
        return {}
    finally:
        failures.finish()

    names: dict[str, str] = {}
    for userid, raw_name in members:
        if isinstance(raw_name, str) and raw_name.strip():
            names[userid] = raw_name.strip()
        else:
            failures.error(
                "准入名单里 userid=%s 的 name 为空或非字符串，该成员不会出现在姓名映射里"
                "（仍出现在 load_whitelist() 的 userid 集合里）：path=%s",
                userid,
                path,
            )
    return names
```

⚠️ 上面 `except` 分支复制了 `load_whitelist` 现有 `except` 分支的写法（同样的"只记类型与帧位置、不记异常消息"纪律）——这是刻意的重复，不是遗漏共用：两个函数各自独立捕获，互不影响对方的 `_FailureLog` 去重槽状态。

- [ ] **Step 6: 跑全部 whitelist 测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_whitelist.py -v`
Expected: 全部 PASS，含 Step 1 新增的 4 条与文件里原有全部约 30 条（`load_whitelist`/`admit`/`compute_admission` 的既有用例逐字不变、逐条仍需 PASS——这是本任务"不改变既有行为"的机器判据）。

- [ ] **Step 7: Commit**

```bash
git add tools/liaison/whitelist.py tools/liaison/tests/test_whitelist.py
git commit -m "feat(liaison): P0 whitelist.load_whitelist_names，load_whitelist 行为不变"
```

---

### Task 6: 归档件相对路径解析（附件优先，否则落盘正文快照）

**Files:**
- Modify: `tools/liaison/unpack/bridge.py`
- Test: `tools/liaison/tests/test_unpack_bridge.py`

**Interfaces:**
- Consumes: `tools/liaison/archive.py::compute_archive_path`、`tools/liaison/attachments.py::store_attachment`、`tools/liaison/archive.py::ArchiveOutcome`
- Produces: `ARCHIVE_ROOT_RELATIVE = "data/liaison/archive"`（单点常量，仓库相对路径前缀）
- Produces: `resolve_reply_archive_relpath(outcome, *, thread_id, msgid, received_at, content, archive_root) -> str`（返回**仓库相对路径**字符串）

- [ ] **Step 1: 写失败测试（有附件时用附件路径，不重复落盘）**

追加：

```python
from tools.liaison.archive import ArchiveOutcome
from tools.liaison.attachments import StoredAttachment


def test_resolve_reply_archive_relpath_uses_the_attachment_when_present(tmp_path):
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    outcome = ArchiveOutcome(
        msgid="msg-1",
        newly_archived=True,
        attachments=(
            StoredAttachment(
                filename="简历.pdf",
                relative_path="ShaoPeiShen/20260910/msg-1__简历.pdf",
                byte_length=10,
                sha256="deadbeef",
            ),
        ),
    )
    relpath = resolve_reply_archive_relpath(
        outcome,
        thread_id="ShaoPeiShen",
        msgid="msg-1",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文不重要",
        archive_root=tmp_path,
    )
    assert relpath == "data/liaison/archive/ShaoPeiShen/20260910/msg-1__简历.pdf"
    assert list(tmp_path.rglob("*")) == []  # 没有额外落盘


def test_resolve_reply_archive_relpath_snapshots_content_when_no_attachment(tmp_path):
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    outcome = ArchiveOutcome(msgid="msg-2", newly_archived=True, attachments=())
    relpath = resolve_reply_archive_relpath(
        outcome,
        thread_id="ShaoPeiShen",
        msgid="msg-2",
        received_at="2026-09-16T13:55:09+08:00",
        content="下周要两个嵌入式",
        archive_root=tmp_path,
    )
    assert relpath == "data/liaison/archive/ShaoPeiShen/20260916/msg-2__正文.txt"
    stored_file = tmp_path / "ShaoPeiShen" / "20260916" / "msg-2__正文.txt"
    assert stored_file.read_bytes() == "下周要两个嵌入式".encode("utf-8")


def test_resolve_reply_archive_relpath_is_idempotent_on_repeated_calls(tmp_path):
    from tools.liaison.unpack.bridge import resolve_reply_archive_relpath

    outcome = ArchiveOutcome(msgid="msg-3", newly_archived=True, attachments=())
    kwargs = dict(
        thread_id="ShaoPeiShen",
        msgid="msg-3",
        received_at="2026-09-16T13:55:09+08:00",
        content="重复调用同一条消息",
        archive_root=tmp_path,
    )
    first = resolve_reply_archive_relpath(outcome, **kwargs)
    second = resolve_reply_archive_relpath(outcome, **kwargs)
    assert first == second
```

- [ ] **Step 2: 跑测试确认先红**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "resolve_reply_archive_relpath" -v`
Expected: FAIL（`ImportError`）。

- [ ] **Step 3: 实现**

在 `tools/liaison/unpack/bridge.py` 追加（新增 import `from tools.liaison.archive import ArchiveOutcome, compute_archive_path` 与 `from tools.liaison.attachments import store_attachment`）：

```python
from tools.liaison.archive import ArchiveOutcome, compute_archive_path
from tools.liaison.attachments import store_attachment

#: `archive.DEFAULT_ARCHIVE_ROOT` 相对仓库根的那一段。写死成字面量而不是
#: 从 `archive.DEFAULT_ARCHIVE_ROOT.relative_to(repo_root)` 反算——`run_bridge`
#: 的调用方（`__main__.py`）本来就可能传一个 `tmp_path` 当 `archive_root`
#: （测试用），那种情况下"相对仓库根"这个说法本身就不成立；design D9
#: 要的是"仓库相对路径这个**展示形态**"，不是真的要求调用方的
#: `archive_root` 参数必须落在仓库里，两件事分开处理。
ARCHIVE_ROOT_RELATIVE = "data/liaison/archive"

#: 无附件时，把消息正文当"归档件"落盘用的固定文件名。
_REPLY_SNAPSHOT_FILENAME = "正文.txt"


def resolve_reply_archive_relpath(
    outcome: ArchiveOutcome,
    *,
    thread_id: str,
    msgid: str,
    received_at: str,
    content: str,
    archive_root: pathlib.Path,
) -> str:
    """算出（必要时落盘）"入信归档件"的仓库相对路径。

    见本文件模块 docstring 引用的计划文档「与 design.md 现状偏离的实现说明·
    偏离 1」：`outcome.attachments` 在当前系统里恒为空，本函数是让
    「入信归档件的相对路径」这句 spec 契约在当前唯一会出现的场景（纯文本、
    无附件）下也对应一个真实存在的文件，而不是编造的路径字符串。

    有附件（`outcome.attachments` 非空）时直接用第一项的 `relative_path`，
    ⛔ 不重复落盘——附件已经在 `archive_message()` 那一步落过了。
    """
    if outcome.attachments:
        relative = outcome.attachments[0].relative_path
    else:
        destination = compute_archive_path(
            thread_id=thread_id,
            msgid=msgid,
            received_at=received_at,
            filename=_REPLY_SNAPSHOT_FILENAME,
            archive_root=archive_root,
        )
        stored = store_attachment(
            content.encode("utf-8"), destination, archive_root=archive_root
        )
        relative = stored.relative_path
    return f"{ARCHIVE_ROOT_RELATIVE}/{relative}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "resolve_reply_archive_relpath" -v`
Expected: 3 个用例全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/bridge.py tools/liaison/tests/test_unpack_bridge.py
git commit -m "feat(liaison): P0 resolve_reply_archive_relpath（附件优先，否则落盘正文快照）"
```

---

### Task 7: `run_bridge` 编排（决策 → 台账写 → 审计 → 信号/起活注入点）

**Files:**
- Modify: `tools/liaison/unpack/bridge.py`
- Test: `tools/liaison/tests/test_unpack_bridge.py`

**Interfaces:**
- Consumes: Task 1–6 的全部函数；`tools/liaison/alerts.py::effect_emit_alert`、`LoggingAlertSink`；`tools/liaison/session.py::CHINA_TZ`
- Produces: `run_bridge(conn, *, thread_id, msgid, sender_userid, sender_name, received_at, content, outcome, route_admitted, ledger_path, archive_root, now, signal_path=None, append_signal=None, dispatch=None, alert_sink=None) -> str`（返回值＝最终 `BridgeDecision.outcome`，`route_admitted=False` 或桥内部异常时返回 `"bridge_failed"` 或 `"not_admitted"`，供调用方/测试断言）

- [ ] **Step 1: 写失败测试（正常路径：marked ⇒ 台账写、审计、信号、起活全部发生）**

追加：

```python
def test_run_bridge_marks_and_records_all_side_effects(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    archive_root = tmp_path / "archive"
    signal_calls = []
    dispatch_calls = []

    outcome_result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-1",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="下周要两个嵌入式",
        outcome=_archive_outcome("msg-1"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=archive_root,
        now=T0,
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append((path, item)) or True,
        dispatch=lambda: dispatch_calls.append(True),
    )

    assert outcome_result == "marked"
    new_text = ledger_path.read_text(encoding="utf-8")
    assert "📨 回件已到，待拆件" in new_text
    audit_rows = conn.execute(
        "SELECT kind FROM liaison_unpack_audit WHERE msgid = 'msg-1'"
    ).fetchall()
    assert [row[0] for row in audit_rows] == ["bridge_marked"]
    assert len(signal_calls) == 1
    assert len(dispatch_calls) == 1
```

（测试文件顶部追加一个小工厂函数，放在 `_row` 附近：）

```python
def _archive_outcome(msgid: str):
    from tools.liaison.archive import ArchiveOutcome

    return ArchiveOutcome(msgid=msgid, newly_archived=True, attachments=())
```

- [ ] **Step 2: 跑测试确认先红**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "run_bridge_marks" -v`
Expected: FAIL（`ImportError`）。

- [ ] **Step 3: 实现 `run_bridge`**

在 `tools/liaison/unpack/bridge.py` 追加（新增 import `import logging`、`import sqlite3`、`from collections.abc import Callable`、`from tools.liaison import alerts`、`from tools.liaison.storage.effects import effect_unpack_audit`）：

```python
import logging
import sqlite3
from collections.abc import Callable

from tools.liaison import alerts
from tools.liaison.storage.effects import effect_unpack_audit

logger = logging.getLogger(__name__)

#: run_bridge 判定"不需要往下走"的两个终态，⛔ 不落信号、不起活。
_NO_FURTHER_ACTION_OUTCOMES = frozenset(
    {OUTCOME_SKIPPED_NO_INFLIGHT, OUTCOME_REFUSED_SERIAL_VIOLATION}
)


def run_bridge(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    sender_name: str | None,
    received_at: str,
    content: str,
    outcome,  # tools.liaison.archive.ArchiveOutcome
    route_admitted: bool,
    ledger_path: pathlib.Path,
    archive_root: pathlib.Path,
    now: datetime,
    signal_path: pathlib.Path | None = None,
    append_signal: Callable[[pathlib.Path, dict], bool] | None = None,
    dispatch: Callable[[], object] | None = None,
    alert_sink: alerts.AlertSink | None = None,
) -> str:
    """归档＋入队之后的桥编排（design D10）。整个函数体 ⛔ 永不上抛——
    任何失败都落一条 `bridge_failed` 审计并返回同一个字符串，调用方
    （`__main__.py::handle_message_frame`）因此**不需要**再包一层
    `try/except` 就能安全调用，但仍然建议调用方外面再包一层保险丝——
    见 Task 8。

    `route_admitted=False`（名单外）时直接返回 `"not_admitted"`，不做
    任何事：spec「名单外发送人 MUST NOT 触发桥」。

    `sender_name` 为 `None`（whitelist 里查不到这个 userid 对应的姓名，
    理论上不该发生，见 Task 5 的防御性设计）时，按"无法判定收信人"
    处理成 `skipped_no_inflight` 同款效果，但审计 `detail` 里注明原因。
    """
    if not route_admitted:
        return "not_admitted"

    sink = alert_sink if alert_sink is not None else alerts.LoggingAlertSink()

    try:
        if sender_name is None:
            decision_outcome = OUTCOME_SKIPPED_NO_INFLIGHT
            matched_numbers: tuple[str, ...] = ()
            new_ledger_text = None
            detail = "whitelist 未提供该 userid 对应的姓名，视同无在途信"
            logger.error(
                "桥无法判定发送人姓名，按无在途信处理：thread_id=%s msgid=%s",
                thread_id,
                msgid,
            )
        else:
            ledger_text = ledger_path.read_text(encoding="utf-8")
            archived_relpath = resolve_reply_archive_relpath(
                outcome,
                thread_id=thread_id,
                msgid=msgid,
                received_at=received_at,
                content=content,
                archive_root=archive_root,
            )
            decision = compute_bridge_decision(
                ledger_text,
                sender_name=sender_name,
                archived_relpath=archived_relpath,
                now_cst=now,
            )
            decision_outcome = decision.outcome
            matched_numbers = decision.matched_letter_numbers
            new_ledger_text = decision.new_ledger_text
            detail = ""

            if decision_outcome == OUTCOME_MARKED:
                write_ledger_atomic(ledger_path, new_ledger_text)
            elif decision_outcome == OUTCOME_REFUSED_SERIAL_VIOLATION:
                alert_text = (
                    "【HR 值守通道·串行原则冲突】"
                    f"{sender_name} 名下同时有 {', '.join(matched_numbers)} 处于在途，"
                    "违反串行原则，桥已拒绝改写台账，请人工归属后再处理。"
                )
                effect_emit_alert(sink, alert_text)

        letter_number = matched_numbers[0] if matched_numbers else None
        audit_kind = f"bridge_{decision_outcome}"
        effect_unpack_audit(
            conn,
            thread_id=thread_id,
            business_key=f"{msgid}:{audit_kind}",
            sender_userid=sender_userid,
            letter_number=letter_number,
            kind=audit_kind,
            detail=detail,
        )

        if decision_outcome not in _NO_FURTHER_ACTION_OUTCOMES:
            _emit_signal_and_dispatch(
                signal_path=signal_path,
                append_signal=append_signal,
                dispatch=dispatch,
                letter_number=letter_number,
                msgid=msgid,
                archived_relpath=archived_relpath if sender_name is not None else "",
                now=now,
            )

        return decision_outcome

    except Exception:  # noqa: BLE001 —— design D10：桥失败 ⛔ 不上抛
        logger.error(
            "回件桥处理失败，归档与入队已提交、不回滚。thread_id=%s msgid=%s",
            thread_id,
            msgid,
            exc_info=True,
        )
        try:
            effect_unpack_audit(
                conn,
                thread_id=thread_id,
                business_key=f"{msgid}:bridge_failed",
                sender_userid=sender_userid,
                letter_number=None,
                kind="bridge_failed",
                detail="见运行日志 exc_info",
            )
        except Exception:  # noqa: BLE001 —— 连审计都写不进去，只记日志，⛔ 不再抛
            logger.error(
                "回件桥失败审计本身也写入失败。thread_id=%s msgid=%s",
                thread_id,
                msgid,
                exc_info=True,
            )
        return "bridge_failed"


def _emit_signal_and_dispatch(
    *,
    signal_path: pathlib.Path | None,
    append_signal: Callable[[pathlib.Path, dict], bool] | None,
    dispatch: Callable[[], object] | None,
    letter_number: str | None,
    msgid: str,
    archived_relpath: str,
    now: datetime,
) -> None:
    """P1 的注入点（见计划文档「偏离 3」）。`append_signal`/`dispatch` 为
    `None` 时只记 WARNING，⛔ 不阻断台账与审计——与
    `__main__.py::InboundPorts.reply` 是同一约定。
    """
    if append_signal is None or signal_path is None:
        logger.warning(
            "P1（信号与打标即开班）尚未接入，本次不追加信号：msgid=%s", msgid
        )
    else:
        append_signal(
            signal_path,
            {
                "letter_number": letter_number,
                "msgid": msgid,
                "archived_path": archived_relpath,
                "at": now.isoformat(),
            },
        )

    if dispatch is None:
        logger.warning("P1（信号与打标即开班）尚未接入，本次不起活：msgid=%s", msgid)
    else:
        dispatch()
```

- [ ] **Step 4: 跑测试确认转绿**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -k "run_bridge_marks" -v`
Expected: PASS。

- [ ] **Step 5: 补齐 D10（异常隔离）、D1/D2 不落信号、幂等（不重复标记）、`skipped_already_marked` 仍会起活 五组测试**

追加：

```python
def test_run_bridge_never_raises_when_the_ledger_file_is_missing(tmp_path, conn):
    """design D10：桥的任何失败都只落 bridge_failed 审计，⛔ 不上抛
    ——归档与入队已经提交，本函数是"归档之后"的独立环节。"""
    from tools.liaison.unpack.bridge import run_bridge

    result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-missing",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        outcome=_archive_outcome("msg-missing"),
        route_admitted=True,
        ledger_path=tmp_path / "不存在的台账.md",
        archive_root=tmp_path / "archive",
        now=T0,
    )
    assert result == "bridge_failed"
    kinds = [
        row[0]
        for row in conn.execute(
            "SELECT kind FROM liaison_unpack_audit WHERE msgid = 'msg-missing'"
        ).fetchall()
    ]
    assert kinds == ["bridge_failed"]


def test_run_bridge_no_inflight_writes_no_signal_and_no_ledger_change(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    original = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 无需回复`")
    ledger_path.write_text(original, encoding="utf-8")
    signal_calls = []

    result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-2",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="新的岗位需求",
        outcome=_archive_outcome("msg-2"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append(item) or True,
    )
    assert result == "skipped_no_inflight"
    assert ledger_path.read_text(encoding="utf-8") == original
    assert signal_calls == []


def test_run_bridge_serial_violation_alerts_and_writes_no_signal(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER
        + _row("人事部#2", "汤丽萍", "`✅ 已推送 2026-09-08`")
        + _row("人事部#3", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    sink = RecordingSink()
    signal_calls = []

    result = run_bridge(
        conn,
        thread_id="TangLiPing",
        msgid="msg-3",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="回复",
        outcome=_archive_outcome("msg-3"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append(item) or True,
        alert_sink=sink,
    )
    assert result == "refused_serial_violation"
    assert signal_calls == []
    assert sink.texts and "人事部#2" in sink.texts[0] and "人事部#3" in sink.texts[0]


def test_run_bridge_same_msgid_replayed_does_not_duplicate_anything(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    kwargs = dict(
        thread_id="TangLiPing",
        msgid="msg-dup",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        outcome=_archive_outcome("msg-dup"),
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
    )
    first = run_bridge(conn, **kwargs)
    text_after_first = ledger_path.read_text(encoding="utf-8")
    second = run_bridge(conn, **kwargs)

    assert first == "marked"
    assert second == "skipped_already_marked"
    assert ledger_path.read_text(encoding="utf-8") == text_after_first
    marked_rows = conn.execute(
        "SELECT COUNT(*) FROM liaison_unpack_audit "
        "WHERE msgid = 'msg-dup' AND kind = 'bridge_marked'"
    ).fetchone()[0]
    assert marked_rows == 1


def test_run_bridge_already_marked_row_still_signals_and_dispatches_for_a_new_msgid(
    tmp_path, conn
):
    """spec「第九态期间又来一条新消息」：台账不变，但信号追加与起活仍要发生
    ——`skipped_already_marked` 不在 `_NO_FURTHER_ACTION_OUTCOMES` 里。"""
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    ledger_path.write_text(
        LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`"),
        encoding="utf-8",
    )
    base_kwargs = dict(
        thread_id="TangLiPing",
        sender_userid="TangLiPing",
        sender_name="汤丽萍",
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        route_admitted=True,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
    )
    run_bridge(conn, msgid="msg-first", outcome=_archive_outcome("msg-first"), **base_kwargs)
    text_after_first = ledger_path.read_text(encoding="utf-8")

    signal_calls = []
    dispatch_calls = []
    result = run_bridge(
        conn,
        msgid="msg-second",
        outcome=_archive_outcome("msg-second"),
        signal_path=tmp_path / "signal.json",
        append_signal=lambda path, item: signal_calls.append(item) or True,
        dispatch=lambda: dispatch_calls.append(True),
        **base_kwargs,
    )
    assert result == "skipped_already_marked"
    assert ledger_path.read_text(encoding="utf-8") == text_after_first
    assert len(signal_calls) == 1 and signal_calls[0]["msgid"] == "msg-second"
    assert len(dispatch_calls) == 1


def test_run_bridge_not_admitted_does_nothing(tmp_path, conn):
    from tools.liaison.unpack.bridge import run_bridge

    ledger_path = tmp_path / "README-跟进信清单.md"
    original = LEDGER_HEADER + _row("人事部#1", "汤丽萍", "`✅ 已推送 2026-09-09`")
    ledger_path.write_text(original, encoding="utf-8")

    result = run_bridge(
        conn,
        thread_id="someone-else",
        msgid="msg-outsider",
        sender_userid="someone-else",
        sender_name=None,
        received_at="2026-09-10T14:03:00+08:00",
        content="正文",
        outcome=_archive_outcome("msg-outsider"),
        route_admitted=False,
        ledger_path=ledger_path,
        archive_root=tmp_path / "archive",
        now=T0,
    )
    assert result == "not_admitted"
    assert ledger_path.read_text(encoding="utf-8") == original
    assert conn.execute("SELECT COUNT(*) FROM liaison_unpack_audit").fetchone()[0] == 0
```

（测试文件顶部追加 `RecordingSink` 小工具，与 `conftest.py` 里的同名类逐字相同，不 import conftest 里的（那是给 `__main__.py` 用的另一份），本文件自己放一份：）

```python
class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)
```

- [ ] **Step 6: 跑全部测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_unpack_bridge.py -v`
Expected: 全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add tools/liaison/unpack/bridge.py tools/liaison/tests/test_unpack_bridge.py
git commit -m "feat(liaison): P0 run_bridge 编排（D10 异常隔离、D1/D2 不落信号、幂等）"
```

---

### Task 8: `__main__.py` 接线（`handle_message_frame` → `run_bridge`）

**Files:**
- Modify: `tools/liaison/__main__.py`
- Test: `tools/liaison/tests/test_inbound_wiring.py`

**Interfaces:**
- Consumes: Task 7 的 `run_bridge`；Task 5 的 `load_whitelist_names`
- Modifies: `InboundPorts` 新增字段 `ledger_path: Path`；`handle_message_frame` 在 `inbound.handle_inbound_message(...)` 返回后接 `run_bridge`

- [ ] **Step 1: 写失败测试**

在 `tools/liaison/tests/test_inbound_wiring.py` 追加（复用文件已有的 `roster`/`mapped`/`svc` fixture）：

```python
def test_worker_marks_the_ledger_for_an_admitted_sender_with_an_inflight_letter(
    svc, roster, mapped, tmp_path
):
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
    assert "📨 回件已到，待拆件" in ledger_path.read_text(encoding="utf-8")
    audit_count = svc.conn.execute(
        "SELECT COUNT(*) FROM liaison_unpack_audit WHERE kind = 'bridge_marked'"
    ).fetchone()[0]
    assert audit_count == 1


def test_worker_leaves_the_ledger_untouched_for_an_outsider(svc, roster, mapped, tmp_path):
    ledger_path = tmp_path / "README-跟进信清单.md"
    original = (
        "| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |\n"
        "|---|---|---|---|---|---|\n"
        "| `人事部#1` | 2026-09-09 | 汤丽萍 | 事项 | 无 | `✅ 已推送 2026-09-09` |\n"
    )
    ledger_path.write_text(original, encoding="utf-8")
    ports = liaison_main.InboundPorts(
        archive_root=tmp_path / "archive",
        whitelist_path=roster,
        reply=ReplySpy(),
        ledger_path=ledger_path,
    )
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame(sender=OUTSIDER_USERID)))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )
    assert ledger_path.read_text(encoding="utf-8") == original
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_unpack_audit").fetchone()[0] == 0


def test_worker_survives_a_missing_ledger_file_without_killing_the_thread(
    svc, roster, mapped, tmp_path
):
    """D10：台账文件缺失 ⇒ bridge_failed 审计，⛔ 值守线程不因此崩溃——
    与「一条畸形消息不配打死值守线程」（`handle_message_frame` docstring）
    是同一条纪律的延伸。"""
    ports = liaison_main.InboundPorts(
        archive_root=tmp_path / "archive",
        whitelist_path=roster,
        reply=ReplySpy(),
        ledger_path=tmp_path / "不存在.md",
    )
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame()))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    kinds = [
        row[0]
        for row in svc.conn.execute("SELECT kind FROM liaison_unpack_audit").fetchall()
    ]
    assert kinds == ["bridge_failed"]
```

- [ ] **Step 2: 跑测试确认先红**

Run: `python -m pytest tools/liaison/tests/test_inbound_wiring.py -k "ledger or missing_ledger_file" -v`
Expected: FAIL（`TypeError: InboundPorts.__init__() got an unexpected keyword argument 'ledger_path'`）。

- [ ] **Step 3: 修改 `InboundPorts` 与 `handle_message_frame`**

`tools/liaison/__main__.py` 顶部 import 追加：

```python
from tools.liaison.unpack.bridge import run_bridge
from tools.liaison.whitelist import load_whitelist_names
```

`REPO_ROOT` 常量下方新增单点常量：

```python
#: 台账真身路径（design D10 单点常量要求）。⛔ 不写死绝对路径给测试用——
#: `InboundPorts.ledger_path` 的默认值就是这一份，测试通过构造
#: `InboundPorts(ledger_path=tmp_path / ...)` 覆盖它。
LEDGER_PATH = REPO_ROOT / "docs" / "跟进信" / "README-跟进信清单.md"
```

把 `InboundPorts` 类改成：

```python
@dataclass(frozen=True)
class InboundPorts:
    """入站处理要用到的四个外部落点。**默认值就是生产用的那一份。**

    `ledger_path`：回件桥（P0）改写的跟进信台账，⚠️ 与 `whitelist_path`
    同一纪律——⛔ 不许给它加环境变量开关，测试把它顶到 `tmp_path`。
    """

    archive_root: Path = DEFAULT_ARCHIVE_ROOT
    whitelist_path: Path | None = None
    reply: Callable[[str, str], object] | None = None
    ledger_path: Path = LEDGER_PATH
```

把 `handle_message_frame` 函数体里 `inbound.handle_inbound_message(...)` 那一段（原第 240–265 行）改成：

```python
    try:
        result = inbound.handle_inbound_message(
            svc.conn,
            thread_id=fields.thread_id,
            msgid=fields.msgid,
            sender_userid=fields.sender_userid,
            received_at=session.format_instant(moment),
            msgtype=fields.msgtype,
            content=fields.content,
            attachment=None,
            archive_root=ports.archive_root,
            whitelist_path=ports.whitelist_path,
            reply=ports.reply,
        )
    except Exception:  # noqa: BLE001 —— 见 docstring：⛔ 不许打死值守线程
        logger.error(
            "入站消息处理失败，本条未落库。thread_id=%s msgid=%s",
            fields.thread_id,
            fields.msgid,
            exc_info=True,
        )
        return False

    if result.route.admitted:
        # 8.5bis 之后新增：归档＋入队已提交，桥（P0）在此之后跑，失败
        # ⛔ 不影响上面已经提交的归档/入队（design D10）。`run_bridge` 自身
        # 永不上抛，这里仍然包一层 try/except 作为第二道保险丝——
        # `run_bridge` 的实现今后若有变化，这条纪律不应该系在"相信它"上。
        try:
            names = load_whitelist_names(ports.whitelist_path)
            run_bridge(
                svc.conn,
                thread_id=fields.thread_id,
                msgid=fields.msgid,
                sender_userid=fields.sender_userid,
                sender_name=names.get(fields.sender_userid),
                received_at=session.format_instant(moment),
                content=fields.content,
                outcome=result.outcome,
                route_admitted=True,
                ledger_path=ports.ledger_path,
                archive_root=ports.archive_root,
                now=moment,
            )
        except Exception:  # noqa: BLE001 —— 双保险，见上
            logger.error(
                "回件桥调用本身失败（run_bridge 理论上不应该抛到这里）。"
                "thread_id=%s msgid=%s",
                fields.thread_id,
                fields.msgid,
                exc_info=True,
            )
    return True
```

⚠️ 注意原函数最后一行 `return True` 保持不变（含义不变：是否真的落库了），只是在它之前插入了桥调用。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_inbound_wiring.py -v`
Expected: 全部 PASS，含本任务新增 3 条与文件里原有全部用例（`InboundPorts` 新增字段带默认值，不破坏任何现有构造调用）。

- [ ] **Step 5: 跑 test_main_wiring.py 回归**

Run: `python -m pytest tools/liaison/tests/test_main_wiring.py -v`
Expected: 全部 PASS（`InboundPorts`/`handle_message_frame` 的改动不影响连接事件路径与启动期自检）。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/__main__.py tools/liaison/tests/test_inbound_wiring.py
git commit -m "feat(liaison): P0 __main__.py 接线 run_bridge（8.5bis 之后、route.admitted 为真时）"
```

---

### Task 9: 台账文档更新（第九态说明行）

**Files:**
- Modify: `docs/跟进信/README-跟进信清单.md`

**Interfaces:** 无（纯文档）。

- [ ] **Step 1: 加第九态行**

在 `docs/跟进信/README-跟进信清单.md` 第 26–35 行那张"状态"图例表（`| 状态 | 含义 | 怎么变过来 |`）里，`| ❌ 已作废 | ... |` 那一行**之后**插入一行：

```markdown
| `📨 回件已到，待拆件 <时刻>` | 第九态：回件已到，**仍在途、串行闸仍锁** | 值守服务自动写（回件桥），拆件回灌后转闭环四态之一，或判非实质回件按后缀原状态还原 |
```

并在该表下方（若已有"串行原则"说明段落，找到那一段；若没有，在图例表结束后另起一段）追加一句：

```markdown
> 第九态视同在途：串行闸的判定只看"是否已推送或已是第九态"，第九态期间不会被误判为"可以发下一封"。
```

Run: `grep -n "📨 回件已到" "docs/跟进信/README-跟进信清单.md"`
Expected: 命中新增的这一行，确认写入成功。

- [ ] **Step 2: Commit**

```bash
git add "docs/跟进信/README-跟进信清单.md"
git commit -m "docs(liaison): 跟进信台账加第九态图例（P0 回件桥）"
```

---

## 自查清单（交付前逐条核对）

- [x] `grep -c '^### Task '` 等于 9（本计划文件），三级标题。
- [x] 有 Global Constraints 段，内容与 CLAUDE.md 工程铁律逐字一致。
- [x] spec 的 7 条 `### Requirement:` 每条都能指到至少一个 Task（见上方对应表）。
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出，无 TBD/TODO/占位符。
- [x] 前后 Task 的类型名、函数签名、字段名一致（`BridgeDecision`/`compute_bridge_decision`/`run_bridge`/`resolve_reply_archive_relpath` 在各 Task 间引用一致）。
- [x] 每个有副作用的动作独占一个 Task 步骤且带幂等键：Task 3（台账写，由 Task 7 的幂等短路——已是第九态不重写——间接保护）、Task 4（`effect_unpack_audit` 幂等键 `{thread_id}:effect_unpack_audit:{msgid}:{kind}`）。
- [x] D1/D2 两条先红后绿：Task 2 Step 1–5。
- [x] 与 design.md 现状偏离的实现说明已落进计划文件本身（见上方专门小节），非仅在对话中提及。

## 端到端提取验证记录（2026-09-16，已实测执行，非仅计划）

按本计划 Task 1–8 的最终代码块（Task 9 是纯文档，不涉及可执行代码）在 `/tmp` 下建了一份仓库的隔离副本（`rsync` 排除 `.git`/`venv`/`.claude/worktrees`/`data`），装同版本依赖（`pytest==8.3.4`、`PyYAML==6.0.3`，Python 3.14.6），原样落盘全部新文件与改动，跑了受影响的测试文件与全量 `tools/liaison/tests/`：

- 基线（应用改动前）：`test_whitelist.py` + `test_liaison_effects.py` + `test_inbound_wiring.py` + `test_main_wiring.py` = 161 passed。
- `test_unpack_bridge.py`（Task 1/2/3/6/7 全部测试）：23 passed。
- `test_liaison_effects.py`（含 Task 4 新增 5 条）：**首次跑 15 failed**——`liaison_unpack_audit` 缺 `thread_id` 列，`assert_effect_log_identity` 通用脚手架按 `thread_id` 分组比对时报 `sqlite3.OperationalError: no such column: thread_id`。这是设计 D11 字面列表（`id, msgid, sender_userid, letter_number, kind, detail, at`）遗漏的一列——design D11 没把"要复用 `assert_effect_log_identity`"这个约束体现到列表里。**已修正**：给 `UNPACK_AUDIT_SCHEMA` 加 `thread_id TEXT NOT NULL` 列，`effect_unpack_audit` 的 INSERT 语句同步加这一列，Task 4 Step 2 的两条直接 SQL 测试同步加 `thread_id` 实参——本计划文件里的代码块已按此修正，不是"发现问题但未修"。修正后：66 passed。
- `test_whitelist.py`（含 Task 5 新增 4 条）：65 passed（原有约 61 条 + 新增 4 条，`load_whitelist`/`admit` 的既有行为逐字未变）。
- `test_inbound_wiring.py`（含 Task 8 新增 3 条）：24 passed。
- `test_main_wiring.py`（回归）：18 passed。
- 全量 `tools/liaison/tests/`：994 passed，5 skipped，2 failed——两条失败是 `test_liaison_no_secrets_in_vcs.py` 依赖 `git ls-files`，而验证副本按验证方法本身排除了 `.git` 目录（`fatal: not a git repository`），与本计划的代码改动无关，是验证方法的环境artifact，不是回归。

结论：Task 1–8 的代码在真实依赖版本下端到端可执行、通过率 100%（排除环境artifact），唯一发现的真实缺陷（`liaison_unpack_audit` 缺 `thread_id`）已直接改进本计划文件，不需要执行方在 `run-build` 阶段再踩一次。验证副本已清理（`rm -rf /tmp/plan-verify`）。

---

**下一步**：用 `run-build`（`superpowers:subagent-driven-development`）执行本计划，任务粒度 = 上方 9 个 Task，每个 Task 独立 review gate。执行前必须已具备 worktree（`run-build` 自己会建，本 P0 · 回件桥＋第九态计划本身不建）。
