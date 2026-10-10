# 面试排期（interview-scheduling）· 交付单元 U2（时段登记与排期动作）Implementation Plan

> 本计划由 `spec-to-plan`（Codex 版）产出。输入＝`openspec/changes/interview-scheduling/`
> 下的 spec + design.md + **上游 U1 已合 main 的真实实现**（磁盘真身为准）；
> ⛔ 未把 `tasks.md` 当计划输入，只用于确认 U2 章节边界（`tasks.md` 第 2 章
> 「时段登记与排期动作」，条目 2.1–2.9）。

## 0. 输入与范围

- **变更包**：`openspec/changes/interview-scheduling/`
- **本单元（U2）范围**：`tasks.md` 第 2 章「时段登记与排期动作」——冲突检查纯函数、
  时段登记接口、四个 `effect_*` 排期节点、history 条数守恒不变式、面试官周视图/当日安排页、
  HR 排期页、U2 e2e。
- **相关 spec 能力文件**（本单元唯二输入）：
  - `specs/interviewer-availability/spec.md`（时段登记 / 只维护本人 / 当日安排视图 / 名单来源）
  - `specs/interview-slot-scheduling/spec.md`（唯一入口 / 安排 / 冲突检查 / 改期取消 /
    标记完成与未出席 / 幂等与流转事实守恒 / 提醒字段预留）
- **design.md 相关 Decisions**：D2（时段来自工作台周视图）、D3（入口只从「进入面试」）、
  D4（四动作四 `effect_*` 节点，流转事实与场次写同事务）、D7（面试官身份复用
  `hr_account` + `interviewer` 名单表）、D8（提醒只留字段）。
- **本单元不做的**（U1 已做、U3–U5 才做）：六张域表与 `stage.interview` 预置行（U1）、
  邀约文案生成/回填/门禁接线（U3）、联系方式加密/读取/删除（U4）、合规断言进 CI 与
  `.51` 发版（U5）。

## Global Constraints

> 从 `CLAUDE.md` 逐字复制与本单元相关的条目。reviewer 拿它当注意力透镜；缺了会静默漏查。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> 本单元落点：四个排期动作各独占一个 `effect_*` 节点、各带幂等键，`interview_slot` /
> `interview_slot_interviewer` / `application_stage_history` / `effect_log` 四写同一事务
> （由 `@idempotent_effect` 装饰器统一提交一次）；冲突检查是纯函数
> `app/agents/conflict_check.py`（无任何 storage 写入）。「流转事实条数＝成功动作次数」
> 不变式由 `tests/test_interview_history_invariant.py` 覆盖（Task 9）。

### 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

> 本单元落点：U2 不写任何 `rejection_record`；面试官当日安排页与 HR 排期页的 JSON
> 输出⛔ 不包含候选人评分、排名、硬门槛标记（`interviewer-availability` spec
> 「视图不含评分信息」与 `interview-slot-scheduling` 的目的行），由
> `tests/test_interview_views.py` 断言字段级不存在（Task 10）。

### 本单元页面与存储层硬约束（spec 原文 + 既有 schema 纪律）

- 面试官当日安排视图 MUST NOT 展示候选人联系方式、简历评分或硬门槛标记
  （`interviewer-availability` spec「当日安排视图」）。
- 时段登记只写本人、重叠拒绝、被场次占用不可撤销、HR `on_behalf` 代登记留痕
  （`interviewer-availability` spec「面试官只能维护自己的时段」）。
- `application_stage_history` 是 M2 已建老表，U2 给它加 `action`/`detail_json` 两列
  **必须同时登记 SCHEMA 的 `CREATE TABLE`（新库）与 `_ADDED_COLUMNS`（老库）**，
  并纳入 `_DRIFT_GUARDED_TABLES` 漂移守卫（与 `hr_account.role` 同一先例，见偏离 D-U2-1）。

## 1. File Structure（本单元新增/修改）

```
app/storage/db.py                       # 修改：application_stage_history 加 action + detail_json（新库 CREATE + 老库 _ADDED_COLUMNS）
app/agents/conflict_check.py            # 新增：冲突检查纯函数（无 storage 写入）
app/storage/interview_scheduling.py     # 新增：可用时段 CRUD + 场次查询 + 冲突输入装配
app/graph/scheduling_nodes.py           # 新增：四个 effect_* 排期节点
app/middleware/auth.py                  # 修改：PROTECTED_PATH_PREFIXES 加 /api/interview-slots
app/web/server.py                       # 修改：Pydantic 模型 + 身份 helper + 时段/排期/页面路由
app/web/static/interviewer_availability.html   # 新增：面试官周视图（登记/撤销）
app/web/static/interviewer_schedule.html       # 新增：面试官当日安排页
app/web/static/application_schedule.html       # 新增：HR 排期页
tests/test_db_m2_schema.py              # 修改：application_stage_history 列集合断言
tests/test_db_migration.py              # 修改：_DRIFT_GUARDED_TABLES + 历史 DDL + _ADDED_COLUMNS 表集合
tests/test_conflict_check.py            # 新增：纯函数单元测试
tests/test_interview_availability_api.py# 新增：时段登记接口契约
tests/test_interview_scheduling_effect.py# 新增：四节点 + 排期路由契约
tests/test_interview_history_invariant.py# 新增：history 条数守恒不变式（tasks 2.6）
tests/test_interview_views.py           # 新增：视图无敏感字段 + 子路径前缀
tests/test_interview_scheduling_e2e.py  # 新增：U2 e2e（tasks 2.9）
```

---

### Task 1: `application_stage_history` 加 `action` + `detail_json`（老表加列迁移）

**文件**：`app/storage/db.py`、`tests/test_db_migration.py`、`tests/test_db_m2_schema.py`

> **偏离登记 D-U2-1（技术方案决策，可代）**：design.md D4 与 tasks 2.3/2.6 明确要求四个
> 排期动作把「安排/改期/取消/完成」作为流转事实写进 `application_stage_history`，并定义
> 不变式「面试流转事实条数＝成功排期动作次数」。但 M2 的 `application_stage_history`
> 只有 `from_stage_id/to_stage_id/actor_type/actor/occurred_at`，**没有动作列、也没有
> 原时刻/新时刻/取消原因的存放处**。本计划给该表加两列：
> `action`（NULL 或 `scheduled/rescheduled/cancelled/completed/no_show`）与
> `detail_json`（改期存原/新时刻、取消存原因）。这是对 M2 老表加列，必须走
> `_ADDED_COLUMNS`（老库）＋ SCHEMA `CREATE TABLE`（新库）＋漂移守卫，与
> `hr_account.role` 同一先例。面试流转事实的 `from_stage_id = to_stage_id = 'interview'`
> （排期不改阶段，符合 D4「阶段不变」）。

**1a. 改 SCHEMA 里的 `application_stage_history` CREATE TABLE**（在 db.py 现有
`application_stage_history` 定义处，把 `actor` 之后、`occurred_at` 之前插入两列）：

```sql
CREATE TABLE IF NOT EXISTS application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    action TEXT CHECK (
        action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show')
    ),
    detail_json TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**1b. `_ADDED_COLUMNS` 追加两条**（老库走 `ALTER TABLE ADD COLUMN`，无常量默认，可空）：

```python
    # interview-scheduling U2：四个排期 effect_* 节点的流转事实动作与详情。
    # application_stage_history 是 M2 已建老表，CREATE TABLE IF NOT EXISTS 对老库
    # 无效，必须走加列迁移；可空是刻意的——既有 stage 流转行没有动作语义。
    ("application_stage_history", "action",
     "TEXT CHECK (action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show'))"),
    ("application_stage_history", "detail_json", "TEXT"),
```

**1c. `tests/test_db_migration.py` 同步四处**：

① 新增历史 DDL 常量（M2 形态，无 action/detail_json）：

```python
_LEGACY_APPLICATION_STAGE_HISTORY_DDL = """
CREATE TABLE application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_application_stage_history_application
    ON application_stage_history (application_id);
"""
```

② `_DRIFT_GUARDED_TABLES` 加 `"application_stage_history"`：

```python
_DRIFT_GUARDED_TABLES = (
    "job_profile", "resume", "job_prep_config", "interview_session", "hr_account",
    "application_stage_history",
)
```

③ `_legacy_db` 里在 `application` 表建好之后、`_LEGACY_INTERVIEW_SESSION_DDL` 之前建它：

```python
    conn.executescript(_LEGACY_APPLICATION_STAGE_HISTORY_DDL)
```

④ `test_audit_tables_never_enter_the_add_column_path` 的预期表集合加
`"application_stage_history"`：

```python
    assert {table for table, _column, _ddl in _ADDED_COLUMNS} == {
        "job_profile", "job", "resume", "job_prep_config", "interview_session",
        "hr_account", "application_stage_history",
    }
```

**1d. `tests/test_db_m2_schema.py` 同步列集合断言**：

```python
def test_application_stage_history_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "application_stage_history")
    assert _columns(conn, "application_stage_history") == {
        "id", "application_id", "from_stage_id", "to_stage_id",
        "actor_type", "actor", "action", "detail_json", "occurred_at",
    }
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_db_migration.py tests/test_db_m2_schema.py -q
```

预期输出：全绿（含 `test_fresh_and_migrated_schemas_have_identical_columns`、
`test_audit_tables_never_enter_the_add_column_path`、
`test_application_stage_history_table_exists_with_expected_columns`）。

---

### Task 2: 冲突检查纯函数 `app/agents/conflict_check.py` + 单元测试

**文件**：`app/agents/conflict_check.py`（新增，整文件）、`tests/test_conflict_check.py`（新增，整文件）

```python
"""面试排期冲突检查（interview-slot-scheduling spec「冲突检查」）。

纯计算、无副作用：⛔ 本模块不得出现任何 storage 写入（不 import sqlite3、
不执行 SQL、不 import app.storage）。三类冲突全部列出，⛔ 不短路只报第一条：

1. interviewer_no_availability  面试官在候选时段没有已登记的可用时段
2. interviewer_busy             面试官该时刻已被另一场未取消面试占用
3. candidate_busy               同一候选人在该时刻已有另一场未取消面试
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONFLICT_NO_AVAILABILITY = "interviewer_no_availability"
CONFLICT_INTERVIEWER_BUSY = "interviewer_busy"
CONFLICT_CANDIDATE_BUSY = "candidate_busy"


@dataclass(frozen=True)
class Conflict:
    code: str
    interviewer_id: str | None = None
    detail: str = ""


class ConflictError(ValueError):
    """携带全部冲突原因的异常；调用方把它渲染成 HTTP 409 detail。"""

    def __init__(self, conflicts: list[Conflict]):
        self.conflicts = conflicts
        super().__init__("; ".join(c.detail for c in conflicts))


@dataclass(frozen=True)
class CandidateWindow:
    application_id: str
    start_at: str
    end_at: str


@dataclass(frozen=True)
class ExistingSlot:
    slot_id: str
    application_id: str
    start_at: str
    end_at: str
    interviewer_ids: list[str] = field(default_factory=list)


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """半开区间：end == start 不算重叠（上一场 15:00 结束、下一场 15:00 开始合法）。"""
    return a_start < b_end and b_start < a_end


def _available_at(
    availability: list[tuple[str, str]], start_at: str, end_at: str
) -> bool:
    """候选时段必须整个落在某一段可用时段内（不跨段拼接）。"""
    for win_start, win_end in availability:
        if win_start <= start_at and end_at <= win_end:
            return True
    return False


def check(
    *,
    candidate: CandidateWindow,
    interviewer_ids: list[str],
    availability_by_interviewer: dict[str, list[tuple[str, str]]],
    existing_slots: list[ExistingSlot],
) -> list[Conflict]:
    conflicts: list[Conflict] = []

    for interviewer_id in interviewer_ids:
        windows = availability_by_interviewer.get(interviewer_id, [])
        if not _available_at(windows, candidate.start_at, candidate.end_at):
            conflicts.append(
                Conflict(
                    code=CONFLICT_NO_AVAILABILITY,
                    interviewer_id=interviewer_id,
                    detail=f"{interviewer_id} 在 {candidate.start_at}–{candidate.end_at} 无可用时段",
                )
            )
            continue
        for slot in existing_slots:
            if interviewer_id not in slot.interviewer_ids:
                continue
            if _overlaps(
                candidate.start_at, candidate.end_at, slot.start_at, slot.end_at
            ):
                conflicts.append(
                    Conflict(
                        code=CONFLICT_INTERVIEWER_BUSY,
                        interviewer_id=interviewer_id,
                        detail=f"{interviewer_id} 在 {slot.start_at}–{slot.end_at} 已有场次 {slot.slot_id}",
                    )
                )
                break

    for slot in existing_slots:
        if slot.application_id != candidate.application_id:
            continue
        if _overlaps(
            candidate.start_at, candidate.end_at, slot.start_at, slot.end_at
        ):
            conflicts.append(
                Conflict(
                    code=CONFLICT_CANDIDATE_BUSY,
                    detail=f"该候选人在 {slot.start_at}–{slot.end_at} 已有场次 {slot.slot_id}",
                )
            )

    return conflicts
```

```python
"""冲突检查纯函数的单元测试（tasks 2.1 的三个 spec Scenario + 边界）。"""
from __future__ import annotations

from app.agents.conflict_check import (
    CONFLICT_CANDIDATE_BUSY,
    CONFLICT_INTERVIEWER_BUSY,
    CONFLICT_NO_AVAILABILITY,
    CandidateWindow,
    ExistingSlot,
    check,
)


def _cand(
    app="a1", start="2026-10-14 06:00", end="2026-10-14 07:00"
) -> CandidateWindow:
    return CandidateWindow(application_id=app, start_at=start, end_at=end)


def test_no_conflicts_when_available_and_free():
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=[],
    )
    assert conflicts == []


def test_missing_availability_reported():
    conflicts = check(
        candidate=_cand(), interviewer_ids=["iv1"],
        availability_by_interviewer={}, existing_slots=[],
    )
    assert [c.code for c in conflicts] == [CONFLICT_NO_AVAILABILITY]


def test_availability_must_fully_contain_window():
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:30", "2026-10-14 08:00")]},
        existing_slots=[],
    )
    assert [c.code for c in conflicts] == [CONFLICT_NO_AVAILABILITY]


def test_interviewer_busy_reported():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a2",
            start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
            interviewer_ids=["iv1"],
        )
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert [c.code for c in conflicts] == [CONFLICT_INTERVIEWER_BUSY]


def test_candidate_busy_reported():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a1",
            start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
            interviewer_ids=["iv2"],
        )
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert [c.code for c in conflicts] == [CONFLICT_CANDIDATE_BUSY]


def test_multiple_conflicts_all_reported():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a2",
            start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
            interviewer_ids=["iv1"],
        ),
        ExistingSlot(
            slot_id="s2", application_id="a1",
            start_at="2026-10-14 06:15", end_at="2026-10-14 06:30",
            interviewer_ids=["iv9"],
        ),
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert sorted(c.code for c in conflicts) == sorted(
        [CONFLICT_INTERVIEWER_BUSY, CONFLICT_CANDIDATE_BUSY]
    )


def test_adjacent_slots_do_not_overlap():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a1",
            start_at="2026-10-14 05:00", end_at="2026-10-14 06:00",
            interviewer_ids=["iv1"],
        )
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert conflicts == []
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_conflict_check.py -q
grep -nE 'sqlite3|import app.storage|conn\.|execute\(' app/agents/conflict_check.py
```

预期输出：`7 passed`；第二条 grep 无任何输出（证明模块内无 storage 写入）。

---

### Task 3: 排期存储层 `app/storage/interview_scheduling.py`

**文件**：`app/storage/interview_scheduling.py`（新增，整文件）

```python
"""面试排期（U2）存储层：面试官可用时段 + 场次查询 + 冲突检查输入装配。

只做两件事：
1. 可用时段 CRUD 的 SQL 与业务校验（重叠拒绝、被占用不可撤、代登记留痕字段）；
2. 冲突检查纯函数（app/agents/conflict_check.py）所需的 DB 输入装配（只读）。

⛔ 本模块不写 interview_slot / application_stage_history —— 那些由
app/graph/scheduling_nodes.py 的四个 effect_* 节点在同一事务里写。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

from app.agents.conflict_check import CandidateWindow, ExistingSlot, check
from app.storage.db import sqlite_utc_now


class AvailabilityOverlapError(ValueError):
    """与既有可用时段重叠。"""


class AvailabilityOccupiedError(ValueError):
    """时段已被某场面试占用，不可撤销。"""


class AvailabilityNotFoundError(ValueError):
    """availability_id 不存在。"""


class InterviewerNotInRosterError(ValueError):
    """interviewer_id 不在面试官名单内。"""


class SlotNotFoundError(ValueError):
    """slot_id 不存在。"""


class SlotStateError(ValueError):
    """场次状态不允许当前动作。"""


class NotInterviewStageError(ValueError):
    """投递当前阶段不是 interview。"""


class CompletionBeforeStartError(ValueError):
    """面试开始时刻之前不可标记完成/未出席。"""


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start < b_end and b_start < a_end


def interviewer_id_for_username(conn: sqlite3.Connection, username: str) -> str | None:
    row = conn.execute(
        "SELECT i.id FROM interviewer i JOIN hr_account a ON a.id = i.account_id "
        "WHERE a.username = ?",
        (username,),
    ).fetchone()
    return row[0] if row else None


def interviewer_exists(conn: sqlite3.Connection, interviewer_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM interviewer WHERE id = ?", (interviewer_id,)
        ).fetchone()
        is not None
    )


def _availability_dict(row) -> dict:
    (
        availability_id, interviewer_id, start_at, end_at, note,
        registered_by, on_behalf, created_at,
    ) = row
    return {
        "id": availability_id,
        "interviewer_id": interviewer_id,
        "start_at": start_at,
        "end_at": end_at,
        "note": note,
        "registered_by": registered_by,
        "on_behalf": bool(on_behalf),
        "created_at": created_at,
    }


def _availability_row(conn: sqlite3.Connection, availability_id: str) -> dict:
    row = conn.execute(
        "SELECT id, interviewer_id, start_at, end_at, note, registered_by, on_behalf, created_at "
        "FROM interviewer_availability WHERE id = ?",
        (availability_id,),
    ).fetchone()
    assert row is not None
    return _availability_dict(row)


def register_availability(
    conn: sqlite3.Connection,
    *,
    interviewer_id: str,
    start_at: str,
    end_at: str,
    note: str | None,
    registered_by: str,
    on_behalf: bool,
) -> dict:
    if not interviewer_exists(conn, interviewer_id):
        raise InterviewerNotInRosterError(f"interviewer_id={interviewer_id} 不在名单内")
    if start_at >= end_at:
        raise ValueError("start_at 必须早于 end_at")

    # 幂等：同面试官同起止时刻重复登记返回既有行（design D2 / tasks 2.2）。
    existing = conn.execute(
        "SELECT id FROM interviewer_availability "
        "WHERE interviewer_id = ? AND start_at = ? AND end_at = ?",
        (interviewer_id, start_at, end_at),
    ).fetchone()
    if existing is not None:
        return _availability_row(conn, existing[0])

    rows = conn.execute(
        "SELECT start_at, end_at FROM interviewer_availability WHERE interviewer_id = ?",
        (interviewer_id,),
    ).fetchall()
    for (s, e) in rows:
        if _overlaps(start_at, end_at, s, e):
            raise AvailabilityOverlapError(f"与既有可用时段 {s}–{e} 重叠")

    availability_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer_availability "
        "(id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            availability_id,
            interviewer_id,
            start_at,
            end_at,
            note,
            registered_by,
            1 if on_behalf else 0,
        ),
    )
    conn.commit()
    return _availability_row(conn, availability_id)


def delete_availability(
    conn: sqlite3.Connection, *, availability_id: str
) -> None:
    row = conn.execute(
        "SELECT interviewer_id, start_at, end_at FROM interviewer_availability WHERE id = ?",
        (availability_id,),
    ).fetchone()
    if row is None:
        raise AvailabilityNotFoundError(f"availability_id={availability_id} 不存在")
    interviewer_id, start_at, end_at = row

    # 被占用不可撤：该时段与任一未取消场次（scheduled/rescheduled）重叠且该场次含该面试官。
    occupied = conn.execute(
        "SELECT 1 FROM interview_slot s "
        "JOIN interview_slot_interviewer x ON x.interview_slot_id = s.id "
        "WHERE x.interviewer_id = ? AND s.status IN ('scheduled', 'rescheduled') "
        "AND s.start_at < ? AND s.end_at > ?",
        (interviewer_id, end_at, start_at),
    ).fetchone()
    if occupied is not None:
        raise AvailabilityOccupiedError(
            "该时段已被某场面试占用，请先由 HR 改期或取消该面试"
        )

    conn.execute(
        "DELETE FROM interviewer_availability WHERE id = ?", (availability_id,)
    )
    conn.commit()


def list_availability(
    conn: sqlite3.Connection, *, interviewer_id: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, interviewer_id, start_at, end_at, note, registered_by, on_behalf, created_at "
        "FROM interviewer_availability WHERE interviewer_id = ? ORDER BY start_at, id",
        (interviewer_id,),
    ).fetchall()
    return [_availability_dict(r) for r in rows]


def _needs_invitation_followup(
    status: str, invitation_status: str, created_at: str
) -> bool:
    """已安排但邀约结果未回填超过 2 天的醒目标记（页面级计算，无定时任务，design 风险表）。"""
    if status not in ("scheduled", "rescheduled"):
        return False
    if invitation_status != "none":
        return False
    created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    now = datetime.strptime(sqlite_utc_now(), "%Y-%m-%d %H:%M:%S")
    return now >= created + timedelta(days=2)


def slot_for(conn: sqlite3.Connection, slot_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, application_id, round, start_at, end_at, mode, location_or_link, "
        "status, cancel_reason, invitation_status, created_by, updated_by, created_at, updated_at "
        "FROM interview_slot WHERE id = ?",
        (slot_id,),
    ).fetchone()
    if row is None:
        return None
    (
        id_, application_id, round_, start_at, end_at, mode, location_or_link,
        status, cancel_reason, invitation_status, created_by, updated_by,
        created_at, updated_at,
    ) = row
    interviewer_ids = [
        r[0]
        for r in conn.execute(
            "SELECT interviewer_id FROM interview_slot_interviewer "
            "WHERE interview_slot_id = ? ORDER BY interviewer_id",
            (slot_id,),
        ).fetchall()
    ]
    return {
        "slot_id": id_,
        "application_id": application_id,
        "round": round_,
        "start_at": start_at,
        "end_at": end_at,
        "mode": mode,
        "location_or_link": location_or_link,
        "status": status,
        "cancel_reason": cancel_reason,
        "invitation_status": invitation_status,
        "created_by": created_by,
        "updated_by": updated_by,
        "created_at": created_at,
        "updated_at": updated_at,
        "interviewer_ids": interviewer_ids,
        "needs_invitation_followup": _needs_invitation_followup(
            status, invitation_status, created_at
        ),
    }


def list_slots_for_application(
    conn: sqlite3.Connection, application_id: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT id FROM interview_slot WHERE application_id = ? ORDER BY created_at, id",
        (application_id,),
    ).fetchall()
    return [slot_for(conn, r[0]) for r in rows]


def _overlapping_active_slots(
    conn: sqlite3.Connection,
    start_at: str,
    end_at: str,
    *,
    exclude_slot_id: str | None = None,
) -> list[ExistingSlot]:
    sql = (
        "SELECT s.id, s.application_id, s.start_at, s.end_at FROM interview_slot s "
        "WHERE s.status IN ('scheduled', 'rescheduled') AND s.start_at < ? AND s.end_at > ?"
    )
    params: list[object] = [end_at, start_at]
    if exclude_slot_id is not None:
        sql += " AND s.id != ?"
        params.append(exclude_slot_id)
    rows = conn.execute(sql, params).fetchall()
    slots: list[ExistingSlot] = []
    for slot_id, application_id, s, e in rows:
        interviewer_ids = [
            r[0]
            for r in conn.execute(
                "SELECT interviewer_id FROM interview_slot_interviewer "
                "WHERE interview_slot_id = ? ORDER BY interviewer_id",
                (slot_id,),
            ).fetchall()
        ]
        slots.append(
            ExistingSlot(
                slot_id=slot_id,
                application_id=application_id,
                start_at=s,
                end_at=e,
                interviewer_ids=interviewer_ids,
            )
        )
    return slots


def load_conflict_inputs(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    interviewer_ids: list[str],
    start_at: str,
    end_at: str,
    exclude_slot_id: str | None = None,
) -> tuple[CandidateWindow, dict[str, list[tuple[str, str]]], list[ExistingSlot]]:
    availability_by_interviewer: dict[str, list[tuple[str, str]]] = {}
    for interviewer_id in interviewer_ids:
        rows = conn.execute(
            "SELECT start_at, end_at FROM interviewer_availability "
            "WHERE interviewer_id = ? ORDER BY start_at",
            (interviewer_id,),
        ).fetchall()
        availability_by_interviewer[interviewer_id] = [(r[0], r[1]) for r in rows]
    existing = _overlapping_active_slots(
        conn, start_at, end_at, exclude_slot_id=exclude_slot_id
    )
    candidate = CandidateWindow(
        application_id=application_id, start_at=start_at, end_at=end_at
    )
    return candidate, availability_by_interviewer, existing


def current_stage_type(conn: sqlite3.Connection, application_id: str) -> str | None:
    row = conn.execute(
        "SELECT st.stage_type FROM application a JOIN stage st ON st.id = a.current_stage_id "
        "WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    return row[0] if row else None


def active_slot_for_round(
    conn: sqlite3.Connection, application_id: str, round_: int
) -> dict | None:
    row = conn.execute(
        "SELECT id FROM interview_slot WHERE application_id = ? AND round = ? "
        "AND status IN ('scheduled', 'rescheduled')",
        (application_id, round_),
    ).fetchone()
    return slot_for(conn, row[0]) if row else None


def day_schedule(
    conn: sqlite3.Connection, *, interviewer_id: str, days: int = 7
) -> list[dict]:
    now = sqlite_utc_now()
    end_str = (
        datetime.strptime(now, "%Y-%m-%d %H:%M:%S") + timedelta(days=days)
    ).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT s.id FROM interview_slot s "
        "JOIN interview_slot_interviewer x ON x.interview_slot_id = s.id "
        "WHERE x.interviewer_id = ? AND s.status IN ('scheduled', 'rescheduled') "
        "AND s.end_at > ? AND s.start_at < ? ORDER BY s.start_at, s.id",
        (interviewer_id, now, end_str),
    ).fetchall()
    out: list[dict] = []
    for (slot_id,) in rows:
        slot = slot_for(conn, slot_id)
        assert slot is not None
        app = conn.execute(
            "SELECT candidate_id, job_id FROM application WHERE id = ?",
            (slot["application_id"],),
        ).fetchone()
        candidate_id, job_id = app
        candidate_name = conn.execute(
            "SELECT name FROM candidate WHERE id = ?", (candidate_id,)
        ).fetchone()[0]
        job_title = conn.execute(
            "SELECT title FROM job WHERE id = ?", (job_id,)
        ).fetchone()[0]
        out.append(
            {
                "slot_id": slot["slot_id"],
                "application_id": slot["application_id"],
                "candidate_name": candidate_name,
                "job_title": job_title,
                "round": slot["round"],
                "start_at": slot["start_at"],
                "end_at": slot["end_at"],
                "mode": slot["mode"],
                "status": slot["status"],
            }
        )
    return out


def application_schedule_data(
    conn: sqlite3.Connection, application_id: str
) -> dict | None:
    app = conn.execute(
        "SELECT a.id, c.name, j.title, st.stage_type FROM application a "
        "JOIN candidate c ON c.id = a.candidate_id "
        "JOIN job j ON j.id = a.job_id "
        "JOIN stage st ON st.id = a.current_stage_id WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if app is None:
        return None
    interviewers = [
        {"id": r[0], "name": r[1], "department": r[2]}
        for r in conn.execute(
            "SELECT id, name, department FROM interviewer WHERE enabled = 1 "
            "ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    ]
    return {
        "application_id": app[0],
        "candidate_name": app[1],
        "job_title": app[2],
        "stage_type": app[3],
        "interviewers": interviewers,
        "slots": list_slots_for_application(conn, application_id),
    }
```

**命令与预期输出**：

```bash
python3 -c "import app.storage.interview_scheduling"
```

预期输出：无输出（导入成功，无异常）。

---

### Task 4: 四个排期 `effect_*` 节点 `app/graph/scheduling_nodes.py`

> **修正（2026-10-11，`1001G`，Spec review 实测两处缺口）**
> ① 节点层入参校验：`effect_schedule_slot` 必须拒绝 **0 面试官**（spec「指定面试官
> 一至多位」）与 **start_at ≥ end_at**；`effect_reschedule_slot` 同样拒绝反向时刻。
> 落地＝新增 `ScheduleInputError(ValueError)`＋在两节点对应位置断言（见下方代码）。
> ② **四个新节点必须登记进仓库级铁律 1 守卫 `tests/test_effect_idempotency_suite.py`**
> （`EFFECT_NODE_MANIFEST` ＋ `build_recipes()` 四条崩溃-恢复配方；⚠️ 该文件不在
> 本 Task 文件清单里但**必改**——`test_every_effect_node_has_a_recovery_recipe`
> 漏登记必红）。配方口径：schedule（种子＝`interview` 阶段投递＋面试官名下可用时段；
> 事实＝`interview_slot` 行数）；reschedule（种子＝手插 `scheduled` 场次、⛔ 不调节点；
> 事实＝`action='rescheduled'` 留痕数）；cancel（事实＝`action='cancelled'` 留痕数）；
> complete（种子场次开始时刻已过；事实＝`action='completed'` 留痕数）。

**文件**：`app/graph/scheduling_nodes.py`（新增，整文件）

```python
"""面试排期（U2）的四个 effect_* 节点（interview-slot-scheduling spec
「安排/改期/取消/完成」；design D4）。

每个有副作用的动作独占一个节点、各带幂等键（工程铁律 1/2）。幂等键由
@idempotent_effect 装饰器按 {thread_id}:{node_name}:{business_key} 落
effect_log，并与业务写同事务提交（装饰器统一 commit，节点内⛔不 commit）。

thread_id = application_id（design D4 的 {application_id}:{node_name}:{...}）。
business_key 约定：
- schedule   = slot_id（slot 主键即客户端生成的 request_id，见偏离 D-U2-2）
- reschedule = f"{slot_id}:{request_id}"（同一场次可多次改期，每次改期是新动作）
- cancel     = slot_id（终态，每场次至多一次）
- complete   = f"{slot_id}:{target_status}"（终态，每场次每目标至多一次）

⛔ 不在节点内 conn.commit()——写入与 effect_log 记录必须由 idempotent_effect
装饰器在同一事务里一次性提交（工程铁律 1）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.agents.conflict_check import ConflictError, check
from app.storage.db import sqlite_utc_now
from app.storage.idempotency import idempotent_effect
from app.storage.interview_scheduling import (
    CompletionBeforeStartError,
    NotInterviewStageError,
    SlotNotFoundError,
    SlotStateError,
    active_slot_for_round,
    current_stage_type,
    load_conflict_inputs,
    slot_for,
)

HISTORY_ACTION_SCHEDULED = "scheduled"
HISTORY_ACTION_RESCHEDULED = "rescheduled"
HISTORY_ACTION_CANCELLED = "cancelled"

_SCHEDULABLE_STATUSES = ("scheduled", "rescheduled")


class ScheduleInputError(ValueError):
    """排期节点入参校验失败（0 面试官 / 反向时刻）。"""


def _assert_interview_stage(conn: sqlite3.Connection, application_id: str) -> None:
    stage_type = current_stage_type(conn, application_id)
    if stage_type is None:
        raise SlotNotFoundError(f"application_id={application_id} 不存在")
    if stage_type != "interview":
        raise NotInterviewStageError(
            "仅对当前阶段为 interview 的投递可安排面试（design D3）"
        )


def _assert_no_conflicts(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    interviewer_ids: list[str],
    start_at: str,
    end_at: str,
    exclude_slot_id: str | None = None,
) -> None:
    candidate, availability_by_interviewer, existing = load_conflict_inputs(
        conn,
        application_id=application_id,
        interviewer_ids=interviewer_ids,
        start_at=start_at,
        end_at=end_at,
        exclude_slot_id=exclude_slot_id,
    )
    conflicts = check(
        candidate=candidate,
        interviewer_ids=interviewer_ids,
        availability_by_interviewer=availability_by_interviewer,
        existing_slots=existing,
    )
    if conflicts:
        raise ConflictError(conflicts)


def _insert_history(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    action: str,
    actor: str,
    detail: dict | None,
) -> None:
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, detail_json) "
        "VALUES (?, ?, 'interview', 'interview', 'human', ?, ?, ?)",
        (
            str(uuid.uuid4()),
            application_id,
            actor,
            action,
            json.dumps(detail, ensure_ascii=False) if detail is not None else None,
        ),
    )


@idempotent_effect("effect_schedule_slot")
def effect_schedule_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    application_id: str,
    interviewer_ids: list[str],
    round_: int,
    start_at: str,
    end_at: str,
    mode: str,
    location_or_link: str | None,
    actor: str,
) -> str:
    _assert_interview_stage(conn, application_id)
    if not interviewer_ids:
        raise ScheduleInputError("至少指定一位面试官（spec「指定面试官一至多位」）")
    if not start_at < end_at:
        raise ScheduleInputError(f"开始时刻必须早于结束时刻：{start_at} → {end_at}")
    if active_slot_for_round(conn, application_id, round_) is not None:
        raise SlotStateError(
            f"该投递第 {round_} 轮已存在未取消场次，请先改期或取消既有场次"
        )
    _assert_no_conflicts(
        conn,
        application_id=application_id,
        interviewer_ids=interviewer_ids,
        start_at=start_at,
        end_at=end_at,
    )
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, location_or_link, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (slot_id, application_id, round_, start_at, end_at, mode, location_or_link, actor),
    )
    for interviewer_id in interviewer_ids:
        conn.execute(
            "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
            "VALUES (?, ?)",
            (slot_id, interviewer_id),
        )
    _insert_history(
        conn,
        application_id=application_id,
        action=HISTORY_ACTION_SCHEDULED,
        actor=actor,
        detail={
            "round": round_,
            "start_at": start_at,
            "end_at": end_at,
            "mode": mode,
        },
    )
    return slot_id


@idempotent_effect("effect_reschedule_slot")
def effect_reschedule_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    start_at: str,
    end_at: str,
    actor: str,
) -> None:
    slot = slot_for(conn, slot_id)
    if slot is None:
        raise SlotNotFoundError(f"slot_id={slot_id} 不存在")
    if slot["status"] not in _SCHEDULABLE_STATUSES:
        raise SlotStateError("仅已安排（scheduled/rescheduled）的场次可改期")
    if not start_at < end_at:
        raise ScheduleInputError(f"开始时刻必须早于结束时刻：{start_at} → {end_at}")
    _assert_no_conflicts(
        conn,
        application_id=slot["application_id"],
        interviewer_ids=slot["interviewer_ids"],
        start_at=start_at,
        end_at=end_at,
        exclude_slot_id=slot_id,
    )
    original_start, original_end = slot["start_at"], slot["end_at"]
    conn.execute(
        "UPDATE interview_slot SET start_at = ?, end_at = ?, status = 'rescheduled', "
        "updated_by = ?, updated_at = ? WHERE id = ?",
        (start_at, end_at, actor, sqlite_utc_now(), slot_id),
    )
    _insert_history(
        conn,
        application_id=slot["application_id"],
        action=HISTORY_ACTION_RESCHEDULED,
        actor=actor,
        detail={
            "original_start_at": original_start,
            "original_end_at": original_end,
            "new_start_at": start_at,
            "new_end_at": end_at,
        },
    )


@idempotent_effect("effect_cancel_slot")
def effect_cancel_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    cancel_reason: str,
    actor: str,
) -> None:
    slot = slot_for(conn, slot_id)
    if slot is None:
        raise SlotNotFoundError(f"slot_id={slot_id} 不存在")
    if slot["status"] not in _SCHEDULABLE_STATUSES:
        raise SlotStateError("仅已安排的场次可取消")
    reason = (cancel_reason or "").strip()
    if not reason:
        raise ValueError("取消原因不能为空")
    conn.execute(
        "UPDATE interview_slot SET status = 'cancelled', cancel_reason = ?, "
        "updated_by = ?, updated_at = ? WHERE id = ?",
        (reason, actor, sqlite_utc_now(), slot_id),
    )
    _insert_history(
        conn,
        application_id=slot["application_id"],
        action=HISTORY_ACTION_CANCELLED,
        actor=actor,
        detail={"cancel_reason": reason},
    )


@idempotent_effect("effect_complete_slot")
def effect_complete_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    target_status: str,
    actor: str,
) -> None:
    if target_status not in ("completed", "no_show"):
        raise ValueError("target_status 只能是 completed 或 no_show")
    slot = slot_for(conn, slot_id)
    if slot is None:
        raise SlotNotFoundError(f"slot_id={slot_id} 不存在")
    if slot["status"] not in _SCHEDULABLE_STATUSES:
        raise SlotStateError("仅已安排的场次可标记完成/未出席")
    if sqlite_utc_now() < slot["start_at"]:
        raise CompletionBeforeStartError("面试开始时刻之前不可标记完成/未出席")
    conn.execute(
        "UPDATE interview_slot SET status = ?, updated_by = ?, updated_at = ? WHERE id = ?",
        (target_status, actor, sqlite_utc_now(), slot_id),
    )
    _insert_history(
        conn,
        application_id=slot["application_id"],
        action=target_status,
        actor=actor,
        detail=None,
    )
```

**命令与预期输出**：

```bash
python3 -c "import app.graph.scheduling_nodes"
grep -c '^@idempotent_effect' app/graph/scheduling_nodes.py
```

预期输出：无异常；`4`（四个节点各带幂等键）。

---

### Task 5: 时段登记接口 `POST/DELETE /api/interviewers/me/availability` + 契约测试

**文件**：`app/web/server.py`（修改）、`tests/test_interview_availability_api.py`（新增，整文件）

**5a. `app/web/server.py`**——顶部 import 加：

```python
from app.storage.interview_scheduling import (
    AvailabilityNotFoundError,
    AvailabilityOccupiedError,
    AvailabilityOverlapError,
    InterviewerNotInRosterError,
    delete_availability,
    interviewer_id_for_username,
    list_availability,
    register_availability,
)
```

**5b. 模块级 Pydantic 模型**（放在 `InterviewerPatchRequest` 之后）：

```python
class AvailabilityRegisterRequest(BaseModel):
    start_at: str
    end_at: str
    note: str | None = None
    on_behalf: bool = False
    interviewer_id: str | None = None
```

**5c. `create_app` 内新增身份 helper**（放在 `_require_hr_role` 之后）：

```python
    def _authenticated_username(request: Request) -> str:
        auth = getattr(request.state, "auth", None)
        if not getattr(auth, "authenticated", False):
            raise HTTPException(status_code=401, detail="未登录")
        username = getattr(auth, "user_id", None)
        if not username:
            raise HTTPException(status_code=401, detail="未登录")
        return username

    def _account_role(username: str) -> str | None:
        row = conn.execute(
            "SELECT role FROM hr_account WHERE username = ?", (username,)
        ).fetchone()
        return row[0] if row else None
```

**5d. 时段登记/撤销/查询三条路由**（放在 `interviewers_update` 路由之后）：

```python
    @router.get("/api/interviewers/me/availability")
    def my_availability(request: Request, interviewer_id: str | None = None):
        username = _authenticated_username(request)
        role = _account_role(username)
        if role == "hr":
            if interviewer_id is None:
                raise HTTPException(status_code=422, detail="HR 只读查看需指定 interviewer_id")
            target = interviewer_id
        elif role == "interviewer":
            target = interviewer_id_for_username(conn, username)
            if target is None:
                raise HTTPException(status_code=403, detail="当前账号不在面试官名单内")
            if interviewer_id is not None and interviewer_id != target:
                raise HTTPException(status_code=403, detail="面试官只能查看本人的可用时段")
        else:
            raise HTTPException(status_code=403, detail="仅面试官或 HR 可访问")
        return {"interviewer_id": target, "availability": list_availability(conn, interviewer_id=target)}

    @router.post("/api/interviewers/me/availability", status_code=201)
    def register_my_availability(req: AvailabilityRegisterRequest, request: Request):
        username = _authenticated_username(request)
        role = _account_role(username)
        if role == "interviewer":
            interviewer_id = interviewer_id_for_username(conn, username)
            if interviewer_id is None:
                raise HTTPException(status_code=403, detail="当前账号不在面试官名单内")
            on_behalf = False
        elif role == "hr":
            if not req.on_behalf or not req.interviewer_id:
                raise HTTPException(
                    status_code=422, detail="HR 代登记需 on_behalf=true 且指定 interviewer_id"
                )
            interviewer_id = req.interviewer_id
            on_behalf = True
        else:
            raise HTTPException(status_code=403, detail="仅面试官或 HR 可登记时段")
        try:
            return register_availability(
                conn,
                interviewer_id=interviewer_id,
                start_at=req.start_at,
                end_at=req.end_at,
                note=req.note,
                registered_by=username,
                on_behalf=on_behalf,
            )
        except (InterviewerNotInRosterError, AvailabilityOverlapError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/api/interviewers/me/availability/{availability_id}")
    def delete_my_availability(availability_id: str, request: Request):
        username = _authenticated_username(request)
        role = _account_role(username)
        if role != "interviewer":
            raise HTTPException(status_code=403, detail="仅面试官本人可撤销自己的时段")
        interviewer_id = interviewer_id_for_username(conn, username)
        if interviewer_id is None:
            raise HTTPException(status_code=403, detail="当前账号不在面试官名单内")
        row = conn.execute(
            "SELECT interviewer_id FROM interviewer_availability WHERE id = ?",
            (availability_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="时段不存在")
        if row[0] != interviewer_id:
            raise HTTPException(status_code=403, detail="只能撤销本人的时段")
        try:
            delete_availability(conn, availability_id=availability_id)
        except (AvailabilityNotFoundError, AvailabilityOccupiedError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True}
```

```python
"""时段登记接口契约（tasks 2.2）：只写本人、重叠拒绝、被占用不可撤、HR on_behalf 留痕。"""
from __future__ import annotations

import uuid

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _account(conn, username: str, role: str = "interviewer") -> str:
    account_id = upsert_account(conn, username=username, password="pw123456")
    conn.execute("UPDATE hr_account SET role = ? WHERE username = ?", (role, username))
    conn.commit()
    return account_id


def _roster(conn, account_id: str, name: str = "汤丽萍") -> str:
    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, ?, '人事部', '[]', 1)",
        (interviewer_id, account_id, name),
    )
    conn.commit()
    return interviewer_id


def _login(client, conn, account_id: str) -> None:
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def test_interviewer_registers_own_availability(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-1")
    _roster(conn, account_id)
    _login(client, conn, account_id)

    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["registered_by"] == "iv-1"
    assert body["on_behalf"] is False


def test_overlap_rejected(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-1")
    _roster(conn, account_id)
    _login(client, conn, account_id)
    client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
    })
    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 07:00", "end_at": "2026-10-14 09:00",
    })
    assert resp.status_code == 422
    assert "重叠" in resp.json()["detail"]


def test_duplicate_window_returns_existing_row(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-1")
    _roster(conn, account_id)
    _login(client, conn, account_id)
    payload = {"start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00"}
    first = client.post("/api/interviewers/me/availability", json=payload)
    second = client.post("/api/interviewers/me/availability", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_interviewer_cannot_read_others(make_test_client):
    client, conn = make_test_client()
    a_id = _account(conn, "iv-a")
    _account(conn, "iv-b")
    _roster(conn, a_id, name="A")
    _login(client, conn, a_id)

    resp = client.get("/api/interviewers/me/availability?interviewer_id=some-other-id")
    assert resp.status_code == 403


def test_non_roster_account_cannot_register(make_test_client):
    client, conn = make_test_client()
    account_id = _account(conn, "iv-nobody")
    _login(client, conn, account_id)
    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
    })
    assert resp.status_code == 403


def test_hr_on_behalf_register_is_marked(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1", role="hr")
    target_id = _account(conn, "iv-1")
    target_roster = _roster(conn, target_id)
    _login(client, conn, hr_id)

    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
        "on_behalf": True, "interviewer_id": target_roster,
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["registered_by"] == "hr-1"
    assert body["on_behalf"] is True


def test_hr_register_without_on_behalf_rejected(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1", role="hr")
    target_id = _account(conn, "iv-1")
    target_roster = _roster(conn, target_id)
    _login(client, conn, hr_id)
    resp = client.post("/api/interviewers/me/availability", json={
        "start_at": "2026-10-14 06:00", "end_at": "2026-10-14 08:00",
        "interviewer_id": target_roster,
    })
    assert resp.status_code == 422
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_availability_api.py -q
```

预期输出：`7 passed`。

---

### Task 6: 面试官周视图页 + 当日安排页 + `/api/interviewers/me/schedule`

**文件**：`app/web/static/interviewer_availability.html`、`app/web/static/interviewer_schedule.html`（新增）、
`app/web/server.py`（修改）

**6a. 页面路由 + 当日安排 JSON 端点**（放在 Task 5 的路由之后）：

```python
    @router.get("/api/interviewers/me/schedule")
    def my_schedule(request: Request, interviewer_id: str | None = None):
        username = _authenticated_username(request)
        role = _account_role(username)
        if role == "hr":
            if interviewer_id is None:
                raise HTTPException(status_code=422, detail="HR 只读查看需指定 interviewer_id")
            target = interviewer_id
        elif role == "interviewer":
            target = interviewer_id_for_username(conn, username)
            if target is None:
                raise HTTPException(status_code=403, detail="当前账号不在面试官名单内")
        else:
            raise HTTPException(status_code=403, detail="仅面试官或 HR 可访问")
        return {"interviewer_id": target, "slots": day_schedule(conn, interviewer_id=target)}

    @router.get("/interviewers/me/availability")
    def interviewer_availability_page():
        return _render_static_page("interviewer_availability.html", root_path)

    @router.get("/interviewers/me/schedule")
    def interviewer_schedule_page():
        return _render_static_page("interviewer_schedule.html", root_path)
```

同时顶部 import 加：

```python
from app.storage.interview_scheduling import day_schedule
```

**6b. 周视图页**（`app/web/static/interviewer_availability.html`，整文件）：

```html
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>我的可用时段</title><!--BASE_HREF--></head>
<body>
<h1>我的可用时段（周视图）</h1>
<form id="f">
  <label>开始 <input name="start_at" placeholder="YYYY-MM-DD HH:MM:SS" required></label>
  <label>结束 <input name="end_at" placeholder="YYYY-MM-DD HH:MM:SS" required></label>
  <label>备注 <input name="note"></label>
  <button type="submit">登记</button>
</form>
<ul id="list"></ul>
<script>
const api = "api/interviewers/me/availability";
async function refresh() {
  const r = await fetch(api);
  if (r.status === 401) { location.href = "login"; return; }
  const d = await r.json();
  document.getElementById("list").innerHTML = d.availability.map(a =>
    `<li>${a.start_at}–${a.end_at} ${a.note || ""}${a.on_behalf ? "（代登记）" : ""} ` +
    `<button data-id="${a.id}" class="del">撤销</button></li>`).join("");
}
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {start_at: fd.get("start_at"), end_at: fd.get("end_at"), note: fd.get("note") || null};
  const r = await fetch(api, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!r.ok) { alert((await r.json()).detail || JSON.stringify(await r.json())); }
  refresh();
});
document.getElementById("list").addEventListener("click", async (e) => {
  if (!e.target.classList.contains("del")) return;
  const r = await fetch(api + "/" + e.target.dataset.id, {method: "DELETE"});
  if (!r.ok) { alert((await r.json()).detail); }
  refresh();
});
refresh();
</script>
</body>
</html>
```

**6c. 当日安排页**（`app/web/static/interviewer_schedule.html`，整文件）：

```html
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>当日安排</title><!--BASE_HREF--></head>
<body>
<h1>当日安排（未来 7 日）</h1>
<ul id="list"></ul>
<script>
async function refresh() {
  const r = await fetch("api/interviewers/me/schedule");
  if (r.status === 401) { location.href = "login"; return; }
  const d = await r.json();
  document.getElementById("list").innerHTML = d.slots.map(s =>
    `<li>${s.start_at}–${s.end_at} · ${s.candidate_name} · ${s.job_title} · ` +
    `第${s.round}轮 · ${s.mode} · ${s.status}</li>`).join("");
}
refresh();
</script>
</body>
</html>
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_views.py -q
```

预期输出：全绿（`test_interview_views.py` 在 Task 10 落地，含当日安排与子路径断言）。

---

### Task 7: HR 排期页 + 四个动作路由（含 `/api/interview-slots` 鉴权前缀）

> **修正（2026-10-11，`1001G`，Spec review 实测）**：① blocker——`application_schedule.html`
> 的 `appId` ⛔ 不能取 URL 末段（字面量 `"schedule"`），改为与 letters 页同款正则取中段；
> ② 页面补非 401 失败的就地提示（404＝投递不存在）；③ POST 排期路线的
> `except SlotNotFoundError` 必须排在宽 `ValueError` 分支**之前**（子类捕获顺序，
> 否则死分支、404 退化成 422）；④ `_require_hr_role` 的 403 文案由「仅 HR 角色可维护
> 面试官名单」改为通用措辞（被排期场景复用时属文案串岗）。回归钉（已随本次落地在
> `tests/test_interview_views.py`）：取法断言／非 401 分支断言／POST-404 用例／
> HR 排期页子路径渲染用例。
> **补充（同日 Task 8 review 后）**：⑤ `ScheduleSlotRequest` 加 `round: int = Field(ge=1)`
> 与 `mode: Literal["onsite","phone","online"]`（文件顶部补 import `Field`/`Literal`）——
> 非法值 422，⛔ 不漏到 DB CHECK 的 IntegrityError；⑥ reschedule／cancel／complete
> 三路由的 `except SlotNotFoundError` 同样单独前置（404 语义），配套用例见 Task 8
> （`test_reschedule_must_pass_conflict_check`／`test_schedule_rejects_invalid_mode_and_round`）。

**文件**：`app/web/server.py`（修改）、`app/middleware/auth.py`（修改）、
`app/web/static/application_schedule.html`（新增）

**7a. `app/middleware/auth.py`**——`PROTECTED_PATH_PREFIXES` 加 `/api/interview-slots`：

```python
PROTECTED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/candidates",
    "/api/resumes",
    "/api/applications",
    "/api/rejections",
    "/api/interviewers",
    "/api/interview-slots",
)
```

**7b. `app/web/server.py`**——顶部 import 加：

```python
from app.agents.conflict_check import ConflictError
from app.graph.scheduling_nodes import (
    effect_cancel_slot,
    effect_complete_slot,
    effect_reschedule_slot,
    effect_schedule_slot,
)
from app.storage.interview_scheduling import (
    CompletionBeforeStartError,
    NotInterviewStageError,
    SlotNotFoundError,
    SlotStateError,
    application_schedule_data,
    slot_for,
)
```

**7c. 模块级 Pydantic 模型**（放在 `AvailabilityRegisterRequest` 之后）：

```python
class ScheduleSlotRequest(BaseModel):
    request_id: str
    interviewer_ids: list[str]
    # 2026-10-11 修正（Spec review 实测）：round≥1、mode 白名单——⛔ 不能让非法值
    # 漏到 DB CHECK（IntegrityError 未处理 ⇒ 500）；Pydantic 层直接 422。
    round: int = Field(ge=1)
    start_at: str
    end_at: str
    mode: Literal["onsite", "phone", "online"]
    location_or_link: str | None = None


class RescheduleSlotRequest(BaseModel):
    request_id: str
    start_at: str
    end_at: str


class CancelSlotRequest(BaseModel):
    cancel_reason: str


class CompleteSlotRequest(BaseModel):
    target_status: str
```

**7d. 排期路由**（放在 Task 6 的路由之后）：

```python
    def _can_complete_slot(slot: dict, username: str, role: str | None) -> bool:
        if role == "hr":
            return True
        if role != "interviewer":
            return False
        assigned = [
            r[0]
            for r in conn.execute(
                "SELECT a.username FROM interview_slot_interviewer x "
                "JOIN interviewer i ON i.id = x.interviewer_id "
                "JOIN hr_account a ON a.id = i.account_id "
                "WHERE x.interview_slot_id = ?",
                (slot["slot_id"],),
            ).fetchall()
        ]
        return username in assigned

    @router.get("/applications/{application_id}/schedule")
    def application_schedule_page(application_id: str):
        return _render_static_page("application_schedule.html", root_path)

    @router.get("/api/applications/{application_id}/schedule")
    def application_schedule_data_route(application_id: str, request: Request):
        _require_hr_role(request)
        data = application_schedule_data(conn, application_id)
        if data is None:
            raise HTTPException(status_code=404, detail="投递不存在")
        return data

    @router.post("/api/applications/{application_id}/schedule", status_code=201)
    def schedule_slot(application_id: str, req: ScheduleSlotRequest, request: Request):
        _require_hr_role(request)
        actor = reviewer_of(request)
        slot_id = req.request_id
        try:
            effect_schedule_slot(
                conn,
                thread_id=application_id,
                business_key=slot_id,
                slot_id=slot_id,
                application_id=application_id,
                interviewer_ids=req.interviewer_ids,
                round_=req.round,
                start_at=req.start_at,
                end_at=req.end_at,
                mode=req.mode,
                location_or_link=req.location_or_link,
                actor=actor,
            )
        except ConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "conflicts": [c.code for c in exc.conflicts],
                    "details": [c.detail for c in exc.conflicts],
                },
            ) from exc
        except SlotNotFoundError as exc:
            # 2026-10-11 修正（Spec review）：SlotNotFoundError 是 ValueError 子类，
            # ⛔ 必须排在下面的宽 ValueError 分支之前，否则是死分支、404 退化成 422。
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (NotInterviewStageError, SlotStateError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return slot_for(conn, slot_id)

    @router.post("/api/interview-slots/{slot_id}/reschedule")
    def reschedule_slot(slot_id: str, req: RescheduleSlotRequest, request: Request):
        _require_hr_role(request)
        actor = reviewer_of(request)
        slot = slot_for(conn, slot_id)
        if slot is None:
            raise HTTPException(status_code=404, detail="场次不存在")
        try:
            effect_reschedule_slot(
                conn,
                thread_id=slot["application_id"],
                business_key=f"{slot_id}:{req.request_id}",
                slot_id=slot_id,
                start_at=req.start_at,
                end_at=req.end_at,
                actor=actor,
            )
        except ConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "conflicts": [c.code for c in exc.conflicts],
                    "details": [c.detail for c in exc.conflicts],
                },
            ) from exc
        except SlotNotFoundError as exc:
            # 2026-10-11 修正（Spec review）：SlotNotFoundError 是 ValueError 子类，
            # ⛔ 必须单独前置——否则"场次中途消失"的 404 语义被宽分支吞成 422。
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (SlotStateError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return slot_for(conn, slot_id)

    @router.post("/api/interview-slots/{slot_id}/cancel")
    def cancel_slot(slot_id: str, req: CancelSlotRequest, request: Request):
        _require_hr_role(request)
        actor = reviewer_of(request)
        slot = slot_for(conn, slot_id)
        if slot is None:
            raise HTTPException(status_code=404, detail="场次不存在")
        try:
            effect_cancel_slot(
                conn,
                thread_id=slot["application_id"],
                business_key=slot_id,
                slot_id=slot_id,
                cancel_reason=req.cancel_reason,
                actor=actor,
            )
        except SlotNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (SlotStateError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return slot_for(conn, slot_id)

    @router.post("/api/interview-slots/{slot_id}/complete")
    def complete_slot(slot_id: str, req: CompleteSlotRequest, request: Request):
        username = _authenticated_username(request)
        role = _account_role(username)
        slot = slot_for(conn, slot_id)
        if slot is None:
            raise HTTPException(status_code=404, detail="场次不存在")
        if not _can_complete_slot(slot, username, role):
            raise HTTPException(status_code=403, detail="仅 HR 或该场面试官可标记完成/未出席")
        try:
            effect_complete_slot(
                conn,
                thread_id=slot["application_id"],
                business_key=f"{slot_id}:{req.target_status}",
                slot_id=slot_id,
                target_status=req.target_status,
                actor=reviewer_of(request),
            )
        except SlotNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (SlotStateError, CompletionBeforeStartError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return slot_for(conn, slot_id)
```

**7e. HR 排期页**（`app/web/static/application_schedule.html`，整文件）：

```html
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>HR 排期</title><!--BASE_HREF--></head>
<body>
<h1>HR 排期</h1>
<div id="info"></div>
<form id="f">
  <label>轮次 <input name="round" type="number" min="1" required></label>
  <label>开始 <input name="start_at" placeholder="YYYY-MM-DD HH:MM:SS" required></label>
  <label>结束 <input name="end_at" placeholder="YYYY-MM-DD HH:MM:SS" required></label>
  <label>形式
    <select name="mode">
      <option value="onsite">现场</option>
      <option value="phone">电话</option>
      <option value="online">线上</option>
    </select>
  </label>
  <label>地点/链接 <input name="location_or_link"></label>
  <div id="interviewers"></div>
  <button type="submit">安排</button>
</form>
<ul id="slots"></ul>
<script>
// 2026-10-11 修正（1001G，Spec review 实测 blocker）：⛔ 不能取 URL 末段
// （那是字面量 "schedule"），否则本页首个数据请求恒 404。用与 letters.html
// 同款正则从路径中段取。
const appId = location.pathname.match(/\/applications\/([^/]+)\/schedule\/?$/)[1];
const api = `api/applications/${appId}/schedule`;
async function refresh() {
  const r = await fetch(api);
  if (r.status === 401) { location.href = "login"; return; }
  if (!r.ok) {
    // 非 401 的失败也是一等状态：就地显示原因，⛔ 不留白也不静默。
    const err = document.getElementById("info");
    err.textContent = r.status === 404
      ? "投递不存在（链接可能已失效）"
      : `读取排期失败（HTTP ${r.status}），请稍后重试`;
    return;
  }
  const d = await r.json();
  document.getElementById("info").textContent = `${d.candidate_name} · ${d.job_title} · ${d.stage_type}`;
  document.getElementById("interviewers").innerHTML = d.interviewers.map(i =>
    `<label><input type="checkbox" name="iv" value="${i.id}">${i.name}</label>`).join("");
  document.getElementById("slots").innerHTML = d.slots.map(s =>
    `<li>第${s.round}轮 ${s.start_at}–${s.end_at} ${s.mode} ${s.status}` +
    `${s.needs_invitation_followup ? " ⚠未回填邀约" : ""}</li>`).join("");
}
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const ivs = [...document.querySelectorAll('input[name="iv"]:checked')].map(i => i.value);
  const body = {
    request_id: crypto.randomUUID(), interviewer_ids: ivs, round: Number(fd.get("round")),
    start_at: fd.get("start_at"), end_at: fd.get("end_at"), mode: fd.get("mode"),
    location_or_link: fd.get("location_or_link") || null,
  };
  const r = await fetch(api, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!r.ok) { alert(JSON.stringify(await r.json())); }
  refresh();
});
refresh();
</script>
</body>
</html>
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_scheduling_effect.py -q
```

预期输出：全绿（`test_interview_scheduling_effect.py` 在 Task 8 落地）。

---

### Task 8: `tests/test_interview_scheduling_effect.py`（四节点 + 排期路由契约）

**文件**：`tests/test_interview_scheduling_effect.py`（新增，整文件）

```python
"""四节点 + 排期路由契约（tasks 2.3/2.4/2.5）：阶段门槛、幂等重跑、改期重过冲突、
取消保留记录、开始前标完成被拒、HR/面试官授权。"""
from __future__ import annotations

import uuid

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _account(conn, username: str, role: str) -> str:
    account_id = upsert_account(conn, username=username, password="pw123456")
    conn.execute("UPDATE hr_account SET role = ? WHERE username = ?", (role, username))
    conn.commit()
    return account_id


def _seed(conn, *, stage: str = "interview"):
    """HR 账号 + 面试官名单 + job/candidate/resume/application。"""
    hr_id = _account(conn, "hr-1", "hr")
    iv_account = _account(conn, "iv-1", "interviewer")
    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, '汤丽萍', '人事部', '[]', 1)",
        (interviewer_id, iv_account),
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', ?)",
        (stage,),
    )
    conn.commit()
    return {"hr_id": hr_id, "iv_account": iv_account, "interviewer_id": interviewer_id}


def _login(client, conn, account_id: str) -> None:
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def _avail(conn, interviewer_id: str) -> None:
    # 2026-10-11 修正（Spec review）：窗口取到 3000 年——test_complete_before_start_rejected
    # 排的是 2999-01-01 的场次，窗口若只到 2020 该场次会被判 interviewer_no_availability
    # 而根本没建成（complete 退化成 404 而非 422）。窗口必须覆盖本文件全部用例。
    conn.execute(
        "INSERT INTO interviewer_availability (id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES (?, ?, '2020-01-01 00:00', '3000-01-01 00:00', NULL, 'iv-1', 0)",
        (str(uuid.uuid4()), interviewer_id),
    )
    conn.commit()


def _schedule(client, *, request_id="req-1", interviewer_ids=None, start="2020-01-01 10:00",
              end="2020-01-01 11:00", round_=1):
    return client.post("/api/applications/app1/schedule", json={
        "request_id": request_id,
        "interviewer_ids": interviewer_ids,
        "round": round_, "start_at": start, "end_at": end, "mode": "onsite",
    })


def test_schedule_requires_interview_stage(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn, stage="screening")
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 422


def test_hr_can_schedule(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "scheduled"
    assert body["interviewer_ids"] == [seed["interviewer_id"]]


def test_schedule_conflict_lists_all_reasons(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _login(client, conn, seed["hr_id"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 409
    assert "interviewer_no_availability" in resp.json()["detail"]["conflicts"]


def test_schedule_rerun_creates_no_second_slot(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    first = _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    second = _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["slot_id"] == second.json()["slot_id"] == "req-1"
    count = conn.execute("SELECT COUNT(*) FROM interview_slot WHERE application_id='app1'").fetchone()[0]
    assert count == 1


def test_duplicate_round_rejected(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    assert _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]]).status_code == 201
    resp = _schedule(client, request_id="req-2", interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 422


def test_reschedule_must_pass_conflict_check(make_test_client):
    """改期重过冲突检查：改到可用窗口之外 ⇒ 409（删掉 `_assert_no_conflicts` 必红）。"""
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    assert _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]]).status_code == 201
    resp = client.post("/api/interview-slots/req-1/reschedule", json={
        "request_id": "rr-1", "start_at": "2019-01-01 10:00", "end_at": "2019-01-01 11:00",
    })
    assert resp.status_code == 409
    assert "interviewer_no_availability" in resp.json()["detail"]["conflicts"]


def test_schedule_rejects_invalid_mode_and_round(make_test_client):
    """非法 mode / round<1 ⇒ 422（⛔ 不能漏到 DB CHECK 的 IntegrityError ⇒ 500）。"""
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    bad_mode = client.post("/api/applications/app1/schedule", json={
        "request_id": "req-m", "interviewer_ids": [seed["interviewer_id"]], "round": 1,
        "start_at": "2020-01-01 10:00", "end_at": "2020-01-01 11:00", "mode": "video",
    })
    assert bad_mode.status_code == 422
    bad_round = client.post("/api/applications/app1/schedule", json={
        "request_id": "req-r", "interviewer_ids": [seed["interviewer_id"]], "round": 0,
        "start_at": "2020-01-01 10:00", "end_at": "2020-01-01 11:00", "mode": "onsite",
    })
    assert bad_round.status_code == 422


def test_reschedule_updates_time_and_history(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    resp = client.post("/api/interview-slots/req-1/reschedule", json={
        "request_id": "rr-1", "start_at": "2020-01-01 12:00", "end_at": "2020-01-01 13:00",
    })
    assert resp.status_code == 200
    assert resp.json()["start_at"] == "2020-01-01 12:00"
    assert resp.json()["status"] == "rescheduled"
    rows = conn.execute(
        # 2026-10-11 修正（Spec review）：occurred_at 是秒级、id 是随机 uuid ⇒ 同秒落库时
        # 排序随机；改按插入顺序 rowid 稳定断言 scheduled → rescheduled。
        "SELECT action FROM application_stage_history WHERE application_id='app1' "
        "ORDER BY occurred_at, rowid"
    ).fetchall()
    assert [r[0] for r in rows] == ["scheduled", "rescheduled"]


def test_cancel_keeps_record(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    resp = client.post("/api/interview-slots/req-1/cancel", json={"cancel_reason": "候选人临时有事"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert conn.execute("SELECT COUNT(*) FROM interview_slot WHERE id='req-1'").fetchone()[0] == 1


def test_complete_before_start_rejected(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-future", interviewer_ids=[seed["interviewer_id"]],
              start="2999-01-01 10:00", end="2999-01-01 11:00")
    resp = client.post("/api/interview-slots/req-future/complete", json={"target_status": "completed"})
    assert resp.status_code == 422


def test_interviewer_can_complete_own_slot(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["hr_id"])
    _schedule(client, request_id="req-1", interviewer_ids=[seed["interviewer_id"]])
    _login(client, conn, seed["iv_account"])
    resp = client.post("/api/interview-slots/req-1/complete", json={"target_status": "completed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_non_hr_cannot_schedule(make_test_client):
    client, conn = make_test_client()
    seed = _seed(conn)
    _avail(conn, seed["interviewer_id"])
    _login(client, conn, seed["iv_account"])
    resp = _schedule(client, interviewer_ids=[seed["interviewer_id"]])
    assert resp.status_code == 403
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_scheduling_effect.py -q
```

预期输出：`10 passed`。

---

### Task 9: `tests/test_interview_history_invariant.py`（history 条数守恒，tasks 2.6）

**文件**：`tests/test_interview_history_invariant.py`（新增，整文件）

```python
"""history 条数守恒不变式（tasks 2.6）：对任一投递，application_stage_history 中
action 非空的面试流转事实条数 = effect_log 中四个排期节点的条数 = 成功排期动作次数。
穷举含重跑的序列。"""
from __future__ import annotations

from app.graph.scheduling_nodes import (
    effect_cancel_slot,
    effect_complete_slot,
    effect_reschedule_slot,
    effect_schedule_slot,
)
from app.storage.db import get_connection, init_schema


_NODE_NAMES = (
    "effect_schedule_slot",
    "effect_reschedule_slot",
    "effect_cancel_slot",
    "effect_complete_slot",
)


def _seed(conn):
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('h1', 'hr-1', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('iv1', 'h1', '汤丽萍', '人事部', '[]', 1)"
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interviewer_availability (id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES ('av1', 'iv1', '2020-01-01 00:00', '2020-01-01 23:00', NULL, 'hr-1', 0)"
    )
    conn.commit()


def _history_count(conn, application_id="app1"):
    return conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id = ? AND action IS NOT NULL",
        (application_id,),
    ).fetchone()[0]


def _effect_count(conn, application_id="app1"):
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id = ? AND node_name IN (?, ?, ?, ?)",
        (application_id, *_NODE_NAMES),
    ).fetchone()[0]


def _schedule(conn, *, slot_id):
    return effect_schedule_slot(
        conn, thread_id="app1", business_key=slot_id, slot_id=slot_id,
        application_id="app1", interviewer_ids=["iv1"], round_=1,
        start_at="2020-01-01 10:00", end_at="2020-01-01 11:00",
        mode="onsite", location_or_link=None, actor="hr-1",
    )


def test_schedule_reschedule_cancel_with_reruns_keeps_invariant(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    _seed(conn)

    _schedule(conn, slot_id="s1")
    assert _schedule(conn, slot_id="s1") is None  # 重跑：命中 effect_log，不产生第二场次
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r1", slot_id="s1",
        start_at="2020-01-01 12:00", end_at="2020-01-01 13:00", actor="hr-1",
    )
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r1", slot_id="s1",
        start_at="2020-01-01 12:00", end_at="2020-01-01 13:00", actor="hr-1",
    )  # 重跑
    effect_cancel_slot(
        conn, thread_id="app1", business_key="s1", slot_id="s1",
        cancel_reason="候选人临时有事", actor="hr-1",
    )
    effect_cancel_slot(
        conn, thread_id="app1", business_key="s1", slot_id="s1",
        cancel_reason="候选人临时有事", actor="hr-1",
    )  # 重跑

    assert conn.execute(
        "SELECT COUNT(*) FROM interview_slot WHERE application_id='app1'"
    ).fetchone()[0] == 1
    assert _history_count(conn) == 3
    assert _effect_count(conn) == 3
    assert _history_count(conn) == _effect_count(conn)


def test_schedule_complete_keeps_invariant(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    _seed(conn)

    _schedule(conn, slot_id="s1")
    effect_complete_slot(
        conn, thread_id="app1", business_key="s1:completed", slot_id="s1",
        target_status="completed", actor="iv-1",
    )
    assert _history_count(conn) == 2
    assert _effect_count(conn) == 2


def test_two_distinct_reschedules_are_two_actions(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    _seed(conn)

    _schedule(conn, slot_id="s1")
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r1", slot_id="s1",
        start_at="2020-01-01 12:00", end_at="2020-01-01 13:00", actor="hr-1",
    )
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r2", slot_id="s1",
        start_at="2020-01-01 14:00", end_at="2020-01-01 15:00", actor="hr-1",
    )
    assert _history_count(conn) == 3
    assert _effect_count(conn) == 3
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_history_invariant.py -q
```

预期输出：`3 passed`。

---

### Task 10: `tests/test_interview_views.py`（视图无敏感字段 + 子路径前缀）

**文件**：`tests/test_interview_views.py`（新增，整文件）

```python
"""视图硬约束：当日安排/HR 排期页⛔不显示评分/排名/硬门槛/联系方式；子路径前缀正确。"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.llm.gateway import LLMGateway
from app.storage.auth_session import create_session
from app.storage.db import get_connection
from app.storage.hr_account import upsert_account
from app.web.server import create_app


ROOT_PATH = "/hr/recruit-agent"
_FORBIDDEN_KEYS = {"phone", "email", "contact", "score", "rank", "hard_gate", "hard_requirement"}


def _make(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app(
        db_path=db_path,
        gateway_factory=lambda: LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        ),
        root_path=ROOT_PATH,
        resume_storage_dir=str(tmp_path / "resumes"),
    )
    client = TestClient(app)
    conn = get_connection(db_path)
    return client, conn


def _seed(conn):
    hr_id = upsert_account(conn, username="hr-1", password="pw123456")
    iv_account = upsert_account(conn, username="iv-1", password="pw123456")
    conn.execute("UPDATE hr_account SET role='interviewer' WHERE username='iv-1'")
    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, '汤丽萍', '人事部', '[]', 1)",
        (interviewer_id, iv_account),
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interviewer_availability (id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES ('av1', ?, '2999-01-01 00:00', '2999-01-01 23:00', NULL, 'iv-1', 0)",
        (interviewer_id,),
    )
    slot_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode, status, created_by) "
        "VALUES (?, 'app1', 1, datetime('now', '+1 hour'), datetime('now', '+2 hour'), "
        "'onsite', 'scheduled', 'hr-1')",
        (slot_id,),
    )
    conn.execute(
        "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) VALUES (?, ?)",
        (slot_id, interviewer_id),
    )
    conn.commit()
    return {"hr_id": hr_id, "iv_account": iv_account, "interviewer_id": interviewer_id}


def _login(client, conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def _assert_no_forbidden_keys(obj):
    if isinstance(obj, dict):
        for key in obj:
            assert key not in _FORBIDDEN_KEYS, f"视图泄漏敏感字段 {key}"
            _assert_no_forbidden_keys(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_forbidden_keys(item)


def test_day_schedule_json_has_no_sensitive_fields(tmp_path):
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    _login(client, conn, seed["iv_account"])
    resp = client.get(f"{ROOT_PATH}/api/interviewers/me/schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slots"][0]["candidate_name"] == "张三"
    assert body["slots"][0]["job_title"] == "嵌入式工程师"
    _assert_no_forbidden_keys(body)


def test_hr_schedule_json_has_no_sensitive_fields(tmp_path):
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    _login(client, conn, seed["hr_id"])
    resp = client.get(f"{ROOT_PATH}/api/applications/app1/schedule")
    assert resp.status_code == 200
    _assert_no_forbidden_keys(resp.json())


def test_pages_render_under_root_path_prefix(tmp_path):
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    _login(client, conn, seed["iv_account"])
    for page in ("interviewers/me/availability", "interviewers/me/schedule"):
        resp = client.get(f"{ROOT_PATH}/{page}")
        assert resp.status_code == 200
        assert f'<base href="{ROOT_PATH}/">' in resp.text
    _login(client, conn, seed["hr_id"])
    resp = client.get(f"{ROOT_PATH}/applications/app1/schedule")
    assert resp.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in resp.text


def test_stale_invitation_followup_marker(tmp_path):
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    conn.execute(
        "UPDATE interview_slot SET created_at = datetime('now', '-3 days') WHERE application_id='app1'"
    )
    conn.commit()
    _login(client, conn, seed["hr_id"])
    resp = client.get(f"{ROOT_PATH}/api/applications/app1/schedule")
    assert resp.status_code == 200
    assert resp.json()["slots"][0]["needs_invitation_followup"] is True


def test_hr_read_only_view_of_any_interviewer(tmp_path):
    client, conn = _make(tmp_path)
    seed = _seed(conn)
    _login(client, conn, seed["hr_id"])
    resp = client.get(
        f"{ROOT_PATH}/api/interviewers/me/availability?interviewer_id={seed['interviewer_id']}"
    )
    assert resp.status_code == 200
    assert len(resp.json()["availability"]) == 1
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_views.py -q
```

预期输出：`5 passed`。

---

### Task 11: `tests/test_interview_scheduling_e2e.py`（U2 e2e，tasks 2.9）

**文件**：`tests/test_interview_scheduling_e2e.py`（新增，整文件）

```python
"""U2 e2e（tasks 2.9）：夹具流转到 interview → 登记时段 → 安排 → 冲突拒绝 → 改期 → 完成；
全程子路径前缀；history 条数守恒。"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.llm.gateway import LLMGateway
from app.storage.auth_session import create_session
from app.storage.db import get_connection
from app.storage.hr_account import upsert_account
from app.web.server import create_app


ROOT_PATH = "/hr/recruit-agent"


def _make(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app(
        db_path=db_path,
        gateway_factory=lambda: LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        ),
        root_path=ROOT_PATH,
        resume_storage_dir=str(tmp_path / "resumes"),
    )
    client = TestClient(app)
    conn = get_connection(db_path)
    hr_id = upsert_account(conn, username="hr-1", password="pw123456")
    iv_account = upsert_account(conn, username="iv-1", password="pw123456")
    conn.execute("UPDATE hr_account SET role='interviewer' WHERE username='iv-1'")
    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, '汤丽萍', '人事部', '[]', 1)",
        (interviewer_id, iv_account),
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.commit()
    return client, conn, {"hr_id": hr_id, "iv_account": iv_account, "interviewer_id": interviewer_id}


def _login(client, conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def test_full_scheduling_flow_under_root_path(tmp_path):
    client, conn, ids = _make(tmp_path)

    # 1. HR 排期页可访问，base href 正确
    _login(client, conn, ids["hr_id"])
    page = client.get(f"{ROOT_PATH}/applications/app1/schedule")
    assert page.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in page.text

    # 2. 面试官未登记时段 → 安排被拒（冲突：无可用时段）
    rejected = client.post(f"{ROOT_PATH}/api/applications/app1/schedule", json={
        "request_id": "req-1", "interviewer_ids": [ids["interviewer_id"]],
        "round": 1, "start_at": "2020-01-01 10:00", "end_at": "2020-01-01 11:00",
        "mode": "onsite",
    })
    assert rejected.status_code == 409
    assert "interviewer_no_availability" in rejected.json()["detail"]["conflicts"]

    # 3. 面试官登记时段
    _login(client, conn, ids["iv_account"])
    reg = client.post(f"{ROOT_PATH}/api/interviewers/me/availability", json={
        "start_at": "2020-01-01 00:00", "end_at": "2020-01-01 23:00",
    })
    assert reg.status_code == 201

    # 4. HR 安排
    _login(client, conn, ids["hr_id"])
    scheduled = client.post(f"{ROOT_PATH}/api/applications/app1/schedule", json={
        "request_id": "req-1", "interviewer_ids": [ids["interviewer_id"]],
        "round": 1, "start_at": "2020-01-01 10:00", "end_at": "2020-01-01 11:00",
        "mode": "onsite",
    })
    assert scheduled.status_code == 201
    assert scheduled.json()["slot_id"] == "req-1"

    # 5. 改期
    rescheduled = client.post(f"{ROOT_PATH}/api/interview-slots/req-1/reschedule", json={
        "request_id": "rr-1", "start_at": "2020-01-01 12:00", "end_at": "2020-01-01 13:00",
    })
    assert rescheduled.status_code == 200
    assert rescheduled.json()["status"] == "rescheduled"

    # 6. 该场面试官标记完成
    _login(client, conn, ids["iv_account"])
    completed = client.post(f"{ROOT_PATH}/api/interview-slots/req-1/complete", json={
        "target_status": "completed",
    })
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    # 7. history 条数守恒：安排/改期/完成 = 3 条，且 effect_log 恒等
    history = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1' AND action IS NOT NULL"
    ).fetchone()[0]
    effect = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id='app1' AND node_name IN "
        "('effect_schedule_slot','effect_reschedule_slot','effect_cancel_slot','effect_complete_slot')"
    ).fetchone()[0]
    slots = conn.execute(
        "SELECT COUNT(*) FROM interview_slot WHERE application_id='app1'"
    ).fetchone()[0]
    assert history == effect == 3
    assert slots == 1
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_scheduling_e2e.py -q
```

预期输出：`1 passed`。

---

## 2. 交付前自查

- [x] 任务标题全部三级 `### Task N: `（`grep -c '^### Task '` 应等于 11）。
- [x] 有 **Global Constraints** 段，内容与 CLAUDE.md 一致（工程铁律 1/2、合规红线
      「AI 只做排序推荐，不做自动淘汰」逐字，并标注 U2 落点）。
- [x] spec 每条 `### Requirement:` 都能指到至少一个 Task（见下「spec 覆盖对照」）。
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出。
- [x] 无 TBD / TODO /「适当处理错误」占位符。
- [x] 前后 Task 的表名、列名、函数签名、路由路径一致。
- [x] 每个有副作用的动作独占一个 `effect_*` 节点且带幂等键（Task 4，第一铁律）。
- [x] 冲突检查纯函数 `app/agents/conflict_check.py` 内无任何 storage 写入
      （Task 2 的 grep 判据，第二铁律）。

## 3. spec 覆盖对照

| Requirement（能力文件） | 本单元落点（Task） |
|---|---|
| interviewer-availability「面试官登记可用时段」 | Task 3/5（登记/撤销/重叠拒绝） |
| interviewer-availability「面试官只能维护自己的时段」 | Task 5（只读本人、HR 代登记留痕） |
| interviewer-availability「当日安排视图」 | Task 3/6（`day_schedule` + 页面） |
| interviewer-availability「面试官记录来自 HR 维护的名单」 | Task 5（名单外拒绝；名单表 U1 已建） |
| interview-slot-scheduling「进入排期的唯一入口」 | Task 4/7（`effect_schedule_slot` 校验 `stage_type=interview`） |
| interview-slot-scheduling「安排一场面试」 | Task 4/7（`effect_schedule_slot` + 路由） |
| interview-slot-scheduling「冲突检查」 | Task 2/3/4（纯函数 + 输入装配 + 409 全量原因） |
| interview-slot-scheduling「改期与取消」 | Task 4/7（`effect_reschedule_slot`/`effect_cancel_slot`） |
| interview-slot-scheduling「标记完成与未出席」 | Task 4/7（`effect_complete_slot`，开始前拒绝） |
| interview-slot-scheduling「排期动作的幂等与流转事实守恒」 | Task 4/9（`@idempotent_effect` + 不变式测试） |
| interview-slot-scheduling「提醒字段预留但不发送」 | Task 3/7（`needs_invitation_followup` 页面计算，无定时任务） |

## 4. 本计划相对 `tasks.md` / `design.md` 的偏离登记

1. **D-U2-1（技术方案决策，可代）**：`application_stage_history` 加 `action` +
   `detail_json` 两列（老表加列，走 `_ADDED_COLUMNS` + 漂移守卫 + 历史 DDL）。
   design D4 与 tasks 2.3/2.6 要求面试流转事实落该表，但 M2 该表没有动作/详情列。
   详见 Task 1。
2. **D-U2-2（实现细节）**：幂等键落点具体化——`thread_id=application_id`；
   `schedule` 的 `business_key = slot_id`（slot 主键即客户端生成的 `request_id`，
   双击/重试复用同一 `request_id` 即幂等）；`reschedule` 加 `:{request_id}` 后缀
   （同一场次可多次改期）；`complete` 加 `:{target_status}` 后缀。design D4 的
   `{slot_id|request_id}` 二选一由此落到具体值。详见 Task 4。
3. **D-U2-3（实现细节）**：U2 不新增 LangGraph 图；四个 `effect_*` 节点由排期 API
   路由直接调用（每次请求＝一次节点执行＝一个幂等键），与 `app/storage/rejection.py`
   的 HTTP 直调先例一致。spec 的「编排流程恢复重跑」语义由 `@idempotent_effect`
   的 `effect_log` 唯一键保证（双击/客户端重试/反向代理重发同款防护）。详见 Task 4/7。
4. **D-U2-4（实现细节）**：页面为静态 HTML 外壳 + 受保护 JSON 端点（沿用既有
   `_render_static_page` 与 `/api` 前缀鉴权模式）；页面路由本身不鉴权，数据全部经
   `/api` 受保护端点（与 `resumes/upload` 页同一形态）。详见 Task 6/7。

## 5. 提取验证记录（`spec-to-plan` 第 6 步，本计划写作时已做的最小核验）

- 本泳道只产出 plan（opener「边界与红线」：⛔ 不改 `app/**`/`tests/**`），
  **完整「原样提取全部代码块、独立 venv 跑全量测试」的第 6 步不在本泳道执行**，
  由 `run-build` 在执行本计划时完成（与 U1 计划同一口径）。
- 写作时已核对磁盘真身：`interviewer` / `interviewer_availability` /
  `interview_slot` / `interview_slot_interviewer` 六张表列名与 `app/storage/db.py`
  完全一致；`app/storage/interviewer.py` 的名单 CRUD 与本计划直接复用；
  `app/storage/idempotency.py::idempotent_effect` 的签名与提交语义按实文引用。

## 6. 完成判据（`tasks.md` 第 2 章 checkbox 在这些全部成立后才勾）

- `python3 -m pytest tests/test_conflict_check.py tests/test_interview_availability_api.py \
  tests/test_interview_scheduling_effect.py tests/test_interview_history_invariant.py \
  tests/test_interview_views.py tests/test_interview_scheduling_e2e.py -q` 全绿；
- `python3 -m pytest tests/test_db_migration.py tests/test_db_m2_schema.py -q` 全绿；
- `python3 -m pytest -q` 全量无回归。

