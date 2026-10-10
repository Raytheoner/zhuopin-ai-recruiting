# [Mac]1010C Offer 包 U3 内部审批流 实现计划

> 派发：Codex·`[Mac]1001G` ｜ 执行引擎：Codex ｜ 分支：`lane-1010c-offer-unit3-plan`
> 输出：本文件（单文件）。⛔ 只产出这一份计划，不写代码、不建分支、不自行 git 提交。

## 零、交付单元范围

本计划实现 `openspec/changes/offer-generation/tasks.md` **第 3 章「U3 内部审批流」**
（tasks 3.1–3.7）：发起 Offer、逐级审批节点、退回后修改、审批页、审批待办的内部通知、
导出门槛、U3 e2e。

**输入（spec 真源，逐条列出「由」）**：

- `openspec/changes/offer-generation/specs/offer-record-and-approval/spec.md` —— **主输入**。
  - `Requirement: Offer 记录的字段边界`（发起 Offer 的前置、字段边界、⛔ 无报酬字段）
    → Task 1（前置校验）、Task 2（`effect_create_offer`）、Task 5（发起端点）、Task 8。
  - `Requirement: 内部审批链`（逐级审批、每级留痕、退回→待修改、审批链由 HR 维护、
    ⛔ MUST NOT 由 AI 生成或推荐审批人；三个 Scenario：两级通过／一级退回／非审批人操作被拒）
    → Task 1（级数/审批人判定）、Task 3（`effect_record_approval`、`effect_revise_offer`）、
    Task 5（审批动作端点）、Task 6（审批页）、Task 8（e2e）。
  - `Requirement: 审批通过才可导出`（Scenario：待审批时导出被拒）
    → Task 1（`assert_offer_exportable`）、Task 7（导出门槛接线）、Task 8。
  - `Requirement: 审批动作幂等`（Scenario：审批节点重跑，留痕条数不变）
    → Task 3（幂等键 + 唯一索引）、Task 8（守恒断言）。
  - `Requirement: 录用决定不由 AI 做`（Scenario：页面不含 AI 录用建议）
    → Task 6（页面反证测试 + 详情 payload 无 AI 字段）。
- `openspec/changes/offer-generation/specs/outbound-approval-gate/spec.md` —— 由
  `Scenario: 内部通知不受影响`（Offer 审批待办不经候选人外发门禁）与
  `Scenario: Offer 函走门禁`（`offer_letter` 登记，属 U4 tasks 4.1）两条。
  本单元只落**内部通知**一侧（Task 4）：⛔ 本单元不登记 `offer_letter`、不调
  `deliver_candidate_message()`——那是对外通道，属不可代项范围（U4 才做）。
- `openspec/changes/offer-generation/design.md` —— 取 Decisions D2（薪资等敏感字段不入库）、
  D5（流转与终态语义，U3 只用到"发起时流转 `offer` 阶段"这一半）、D6（审批链是**岗位级**
  配置、审批在 Web 工作台、**退回后链从第一级重走新一轮 `round`**、审批待办用内部通知
  ⛔ 不做企微卡片）、D9（U3 前置＝U1；U4 前置＝U2＋M2 `rejection_record`）。
- **上游参照（只读，⛔ 不修改）**：
  - U1 实现真身：`app/storage/offer_approval_chain.py`、`app/storage/db.py` 的
    `offer` / `offer_approval_chain` / `offer_approval` 表（`CREATE TABLE IF NOT EXISTS`，
    `offer(application_id)` 唯一、`offer_approval(offer_id, round, level)` 唯一）、
    `stage` 预置 `offer` / `hired`、`tests/test_offer_approval_chain*.py`。
  - U1 计划（**格式样板**）：`docs/superpowers/plans/2026-10-08-offer-generation-unit1-domain-model.md`。
  - U2 计划：`docs/superpowers/plans/2026-10-10-offer-generation-unit2-letter-engine.md`。

⛔ `tasks.md` 只用于确认第 3 章边界（3.1–3.7），**不作为计划输入**（粒度差一个数量级）。

**磁盘真身核对（2026-10-11 实跑，`git log --oneline -8` 止于 `a4e22a0`；U2 seg1 已合 main）**：

| 依赖 | 磁盘状态 | 本计划如何处置 |
|---|---|---|
| `offer` / `offer_approval_chain` / `offer_approval` 表、`stage` 预置 | ✅ 已落地（`app/storage/db.py`） | 直接用 |
| `app/storage/offer_approval_chain.py::get_approval_chain/put_approval_chain` | ✅ 已落地 | 直接调（审批链级数/审批人判定的唯一真源） |
| `app/storage/idempotency.py::idempotent_effect` | ✅ 已落地 | 四个 effect 节点全部挂它 |
| `app/graph/letter_nodes.py`（含 `record_letter_access`、`effect_persist_letter`） | ✅ 已落地 | Task 7 在此**加一道导出门槛**；Task 8 用它生成文书 |
| `app/storage/letter_template.py` + 占位模板 v1 种子 | ✅ 已落地 | Task 8 生成文书用 |
| **U2 Task 4/5/6/7/8**（`app/letter_docx.py`、`/api/letters*` 端点、`letters.html`） | ❌ **未落地**（磁盘无 `app/letter_docx.py`，`app/web/server.py` 里 grep 不到 `export.docx`） | ⚠️ tasks 3.6 字面引用「2.5 的导出接口」⇒ 本计划把导出门槛做成**卡点式**（Task 7a，落在 U2 已落地的 `record_letter_access` 上，不依赖 Task 4/5），并对 U2 Task 5 的端点接线给出 **probe 后接线／未落地登记留步**的预案（Task 7b、Task 8 步 B） |

**前向依赖与已登记偏差（⛔ 不阻塞本单元，必须让 reviewer 与 Shao Peishen 都看见）**：

1. **`application.status` 词表偏差（沿用 U1 计划「前向依赖与已登记风险」第 1 条）**：
   tasks 3.1 写「`status=ongoing`」，磁盘真身是
   `status CHECK IN ('active','rejected','withdrawn','hired')`（`app/storage/db.py`）。
   本单元把「未终止」实现为 `status == 'active'`，⛔ **不改 CHECK**（SQLite 改 CHECK 要整表重建，
   且那是 M2 契约，不在本包范围）。U5（回填 `hired`／`refused`）必须就地对齐这套词表。
2. **`application_stage_history.action` 受 CHECK 限制**：只允许
   `scheduled/rescheduled/cancelled/completed/no_show` 或 NULL。发起 Offer 的流转事实
   （进 `offer` 阶段）**写 `action=NULL`**——与既有 `app/storage/rejection.py::write_rejection`
   同款（阶段流转行没有动作语义）。⛔ 不为本单元扩这个 CHECK（要整表重建，收益为零）。
3. **审批人角色口径**：审批人是**岗位审批链里配置的可识别账号**
   （`hr_account.username`），他们可能是 `role='dept_manager'` 的业务经理／总经理。
   ⇒ 审批端点只要求**登录**，⛔ 不用 `_require_role(request, "hr")` 卡角色
   （否则 spec 里合法的审批人被 403 挡在门外，且症状是"审批链配了也没人能审"）。
4. **审批链未配置（某级审批人列表为空）⇒ fail-closed 拒绝审批**：U1 的
   `get_approval_chain` 未配置时返回默认一级且 `approver_account_ids=[]`（占位，待人事部#3
   回件 OQ2 回填）。空列表时 `effect_record_approval` 抛
   `ApprovalChainNotConfiguredError`（409），⛔ 不猜默认审批人、⛔ 不放行任意登录账号
   （spec「审批链 MUST 由 HR 维护，MUST NOT 由 AI 生成或推荐审批人」）。
   **残余风险（登记，由 U5 tasks 5.6 的 HR 操作说明承接）**：审批链未配置期间 Offer 会停在
   「待审批」，HR 必须先维护审批链（U1 Task 7 的
   `PUT /api/jobs/{job_id}/offer-approval-chain`）。
5. **U2 未落地部分（上表）**：Task 7b 与 Task 8 步 B 都以
   `grep -q 'export.docx' app/web/server.py` 作 probe；probe 不过时**登记「⏸ 留步」**，
   ⛔ 不假装接线完成，也 ⛔ 不因此判整件失败（导出门槛的判据由 Task 7a 的卡点测试独立覆盖）。
6. **tasks.md 的勾选**：本计划 ⛔ 不改 `openspec/**`（含不勾 3.1–3.7）。勾选在该 plan 的
   final review 通过后由收口方做。

## Global Constraints

以下条目逐字取自 `CLAUDE.md`，`run-build` 的两阶段 reviewer 会把本节当注意力透镜。

### 工程铁律

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须
   独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加
   唯一索引。**幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上
   不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的
   `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分
   `compute_*` / `effect_*`。

> 本单元四条副作用各有独占节点且各带幂等键：
> `effect_create_offer`（`{application_id}:effect_create_offer:{request_id}`）、
> `effect_record_approval`（`{offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}`）、
> `effect_revise_offer`（`{offer_id}:effect_revise_offer:{request_id}`）、
> `effect_publish_approval_todo`（`{offer_id}:effect_publish_approval_todo:{round}:{level}`）。
> 铁律 3/4/5（AI 评分持久化、`criterion_score.evidence_ref`、`temperature=0`）在本单元
> **不适用**：U3 一行 AI 调用都没有（`app/graph/offer_nodes.py` 里 ⛔ 不 import 任何
> `app/agents/**`），这正是 `Requirement: 录用决定不由 AI 做` 的结构性保证。

### 数据模型要点（逐字）

- `application` 是独立实体，状态属于投递不属于候选人（**不要合并 candidate 和 application**）
- `application_stage_history` 流转事实表是所有报表的基础
- `stage.stage_type` 是语义标签，显示名可自定义，逻辑只认类型
- 自定义字段用 JSON（`property_definition` / `property_value`），不用 EAV

### 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：
  `rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。

### 本单元专项约束（逐字摘自 spec 与 CLAUDE.md 决策代理表）

- **薪资等敏感字段不入库**：`offer` 表只存岗位 / 部门 / 入职日 / 汇报对象 / 备注 / 审批状态 /
  答复，⛔ 不设薪资、股权、签字费、津贴等任何报酬列（`offer-record-and-approval` spec
  「Offer 记录的字段边界」，design D2）。本单元新增的任何端点、payload、页面与日志都
  ⛔ 不新增报酬字段（`offer` 表列名反证断言在 U1 已落地，本单元不做破坏性扩展）。
- **审批链 MUST 由 HR 维护，MUST NOT 由 AI 生成或推荐审批人**（spec「内部审批链」）。
  ⇒ `effect_record_approval` 只认 `offer_approval_chain` 里配的人，⛔ 不做任何"推荐/默认/
  顺延审批人"的兜底。
- **录用决定不由 AI 做**（spec）：Offer 发起与审批页面 MUST NOT 展示 AI 评分作为决策依据的
  推荐语。⇒ Task 6 的页面反证测试 + 详情 payload 无反证词表字段。
- **不可代项（`CLAUDE.md`「决策代理」，本单元一律 ⛔ 不触碰）**：候选人对外通道的开关
  （一次性邀请链接发放、拒信/邀约对外发送）、真实简历数据处理范围的变更、生产服务器 `.51`
  的发版决定、合规红线七条的任何变更或单次例外、预算与外部采购。
  ⇒ 本单元**不发任何对外消息**（唯一的"通知"是内部 outbox 待办，Task 4），
  **不做 `.51` 发版**（tasks 5.8 是 Shao Peishen 的 🔴 项）。

## 机器判据（交付前自查）

```bash
test -n "$(ls docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md
grep -q 'Global Constraints' docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md
grep -q 'effect_record_approval' docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md
```

本计划含 `### Task 1`–`### Task 9`，共 9 个任务。

---

### Task 1: 存储层判定真源 `app/storage/offer.py`（tasks 3.1 / 3.2 / 3.3 / 3.6 的只读判定）

**文件**：`app/storage/offer.py`（新建）

```python
"""Offer 记录与内部审批流的存储层判定真源（offer-generation U3，tasks 3.1–3.3/3.6）。

本模块只做**读 + 判定 + 抛具名异常**，⛔ 不写任何业务行：
- `offer` / `offer_approval` 的写入是 `app/graph/offer_nodes.py` 四个 effect_* 节点的
  独占职责（工程铁律 1：每个副作用独占一个节点、带幂等键、与 effect_log 同事务）。
- 本模块的读者有三个：`app/graph/offer_nodes.py`（节点内重做前置校验）、
  `app/web/server.py`（HTTP 面 4xx 映射）、`app/graph/letter_nodes.py`（导出门槛卡点）。

⛔ 本模块不 import 任何 `app/agents/**`：U3 一行 AI 调用都没有——这是 spec
「录用决定不由 AI 做」在当前代码面上的结构性保证。
"""
from __future__ import annotations

import sqlite3

from app.storage.offer_approval_chain import get_approval_chain

# 阶段语义序（CLAUDE.md 数据模型要点：stage_type 是语义标签，逻辑只认类型）。
# ⚠️ 'rejected' 刻意不在序里：淘汰阶段与主链不是同一条线，发起 Offer 的前置由
# application.status（active）与 rejection_record 两条判据负责，⛔ 不靠阶段位次推断。
STAGE_ORDER: tuple[str, ...] = ("initial", "screening", "interview", "offer", "hired")

# tasks 3.1「前置投递阶段 ≥ interview」。
OFFER_CREATABLE_MIN_STAGE: str = "interview"

# ⚠️ tasks 3.1 写「status=ongoing」，磁盘真身是
# application.status CHECK IN ('active','rejected','withdrawn','hired')
# （app/storage/db.py）。⛔ 本单元不改这个 CHECK（SQLite 改 CHECK 要整表重建，且
# 那是 M2 的契约）："投递未终止"在现网词表里就是 'active'。
ACTIVE_APPLICATION_STATUS: str = "active"

# 导出门槛（tasks 3.6；spec「审批通过才可导出」）：只有这五种状态可导出文书。
# pending_approval / needs_revision 一律拒绝。
EXPORTABLE_OFFER_STATUSES: frozenset[str] = frozenset(
    {"approved", "exported", "accepted", "declined", "negotiating"}
)

OFFER_STATUS_LABELS: dict[str, str] = {
    "pending_approval": "待审批",
    "needs_revision": "待修改",
    "approved": "已审批",
    "exported": "已导出",
    "accepted": "已接受",
    "declined": "已拒绝",
    "negotiating": "谈判中",
}


class OfferNotFoundError(LookupError):
    """投递没有 Offer 记录，或 offer_id 不存在。"""


class OfferNotCreatableError(ValueError):
    """发起 Offer 的前置不满足（阶段过早／投递已终止／已有淘汰记录／已有 Offer）。"""


class OfferNotEditableError(ValueError):
    """只有 needs_revision 的 Offer 可以修改（tasks 3.3）。"""


class OfferNotPendingApprovalError(ValueError):
    """Offer 不在待审批状态，不接受审批动作。"""


class ApprovalChainNotConfiguredError(ValueError):
    """该岗位该级没有配置审批人——fail-closed。

    ⛔ 不猜默认审批人、⛔ 不放行任意登录账号（spec「审批链 MUST 由 HR 维护，
    MUST NOT 由 AI 生成或推荐审批人」）。
    """


class NotAnApproverError(PermissionError):
    """操作人不在该级审批人列表内（spec Scenario「非审批人操作被拒」）。"""


class ApprovalOutOfOrderError(ValueError):
    """跳级审批：本轮更低级尚未通过（spec「逐级审批」）。"""


class ApprovalAlreadyRecordedError(ValueError):
    """该 (offer, round, level) 已有留痕——唯一索引告诉你这件事已经做完了。"""


class OfferNotExportableError(ValueError):
    """Offer 状态不在可导出白名单内（spec「审批通过才可导出」）。"""


def offer_status_label(status: str) -> str:
    return OFFER_STATUS_LABELS.get(status, status)


def stage_rank(stage_type: str) -> int:
    """阶段语义序的位次；未知（含 'rejected'）返回 -1。"""
    try:
        return STAGE_ORDER.index(stage_type)
    except ValueError:
        return -1


_OFFER_SELECT = (
    "SELECT o.id, o.application_id, o.job_id, o.department, o.start_date, o.report_to, "
    "o.note, o.status, o.approval_round, o.created_by, o.created_at, o.updated_by, "
    "o.updated_at, j.title, c.name "
    "FROM offer o "
    "JOIN job j ON j.id = o.job_id "
    "JOIN application a ON a.id = o.application_id "
    "JOIN candidate c ON c.id = a.candidate_id "
)


def _offer_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "application_id": row[1],
        "job_id": row[2],
        "department": row[3],
        "start_date": row[4],
        "report_to": row[5],
        "note": row[6],
        "status": row[7],
        "status_label": offer_status_label(row[7]),
        "approval_round": row[8],
        "created_by": row[9],
        "created_at": row[10],
        "updated_by": row[11],
        "updated_at": row[12],
        "job_title": row[13],
        "candidate_name": row[14],
    }


def get_offer(conn: sqlite3.Connection, offer_id: str) -> dict:
    row = conn.execute(_OFFER_SELECT + "WHERE o.id = ?", (offer_id,)).fetchone()
    if row is None:
        raise OfferNotFoundError(f"Offer 不存在: {offer_id!r}")
    return _offer_row_to_dict(row)


def get_offer_by_application(
    conn: sqlite3.Connection, application_id: str
) -> dict | None:
    row = conn.execute(
        _OFFER_SELECT + "WHERE o.application_id = ?", (application_id,)
    ).fetchone()
    return None if row is None else _offer_row_to_dict(row)


def assert_offer_creatable(conn: sqlite3.Connection, *, application_id: str) -> None:
    """tasks 3.1 的发起前置，逐条给出可执行的拒绝理由（调用方各映射成一个 4xx）。"""
    row = conn.execute(
        "SELECT a.status, s.stage_type FROM application a "
        "JOIN stage s ON s.id = a.current_stage_id WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise OfferNotFoundError(f"投递不存在: {application_id!r}")
    status, stage_type = row
    if status != ACTIVE_APPLICATION_STATUS:
        raise OfferNotCreatableError(
            f"投递状态为 {status!r}，已终止的投递不能发起 Offer"
        )
    if stage_rank(stage_type) < stage_rank(OFFER_CREATABLE_MIN_STAGE):
        raise OfferNotCreatableError(
            f"投递当前阶段为 {stage_type!r}，只有 {OFFER_CREATABLE_MIN_STAGE} 及之后的"
            "阶段才能发起 Offer"
        )
    if (
        conn.execute(
            "SELECT 1 FROM rejection_record WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        is not None
    ):
        raise OfferNotCreatableError("该投递已有淘汰记录，不能发起 Offer")
    if (
        conn.execute(
            "SELECT 1 FROM offer WHERE application_id = ?", (application_id,)
        ).fetchone()
        is not None
    ):
        raise OfferNotCreatableError("该投递已有 Offer 记录")


def approval_levels(conn: sqlite3.Connection, job_id: str) -> list[int]:
    """该岗位审批链的级号序列（升序）。未配置 ⇒ U1 的默认一级占位 [1]。"""
    return [item["level"] for item in get_approval_chain(conn, job_id)]


def total_approval_levels(conn: sqlite3.Connection, job_id: str) -> int:
    """最后一级的级号——「最后一级 approved ⇒ offer.status=approved」的判据。"""
    levels = approval_levels(conn, job_id)
    return max(levels) if levels else 1


def approvers_for_level(
    conn: sqlite3.Connection, job_id: str, level: int
) -> list[str]:
    """该岗位该级的审批人列表；未配置该级或该级无审批人 ⇒ 空列表（fail-closed）。"""
    for item in get_approval_chain(conn, job_id):
        if item["level"] == level:
            return list(item["approver_account_ids"])
    return []


def current_approval_level(conn: sqlite3.Connection, offer_id: str) -> int:
    """本轮**当前待审**的级号 = 本轮已通过的最大级号 + 1（无通过记录 ⇒ 1）。

    退回后 `round+1`，新一轮没有通过记录 ⇒ 自然回到第 1 级——这就是
    design D6「HR 改后链从第一级重走」的落点，⛔ 不额外写"重置"语句。
    """
    offer = get_offer(conn, offer_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(level), 0) FROM offer_approval "
        "WHERE offer_id = ? AND round = ? AND decision = 'approved'",
        (offer_id, offer["approval_round"]),
    ).fetchone()
    return int(row[0]) + 1


def approval_history(conn: sqlite3.Connection, offer_id: str) -> list[dict]:
    """全部轮次的审批留痕（升序）。⛔ 无 AI 字段——只有人、时刻、结论、意见。"""
    rows = conn.execute(
        "SELECT round, level, approver, decision, comment, at FROM offer_approval "
        "WHERE offer_id = ? ORDER BY round ASC, level ASC",
        (offer_id,),
    ).fetchall()
    return [
        {
            "round": row[0],
            "level": row[1],
            "approver": row[2],
            "decision": row[3],
            "comment": row[4],
            "at": row[5],
        }
        for row in rows
    ]


def list_pending_offers_for(conn: sqlite3.Connection, approver: str) -> list[dict]:
    """「本人待审」列表（tasks 3.4 的 `GET /offers/pending`）。

    判据不是"岗位审批链里有这个人"，而是**他此刻正好站在本轮当前级上**：
    取 `status='pending_approval'` 的 Offer，逐条算本轮当前级，再取该级审批人
    列表比对。年 Offer 量两位数（design「规模」），逐条算的开销可忽略；
    ⛔ 不在 SQL 里塞 JSON 解析（`approver_account_ids` 是 JSON 文本，
    在应用层走 U1 的 `get_approval_chain` 才是唯一真源）。
    """
    rows = conn.execute(
        _OFFER_SELECT
        + "WHERE o.status = 'pending_approval' ORDER BY o.created_at ASC, o.id ASC"
    ).fetchall()
    items: list[dict] = []
    for row in rows:
        offer = _offer_row_to_dict(row)
        level = current_approval_level(conn, offer["id"])
        approvers = approvers_for_level(conn, offer["job_id"], level)
        if approver in approvers:
            offer["current_level"] = level
            offer["total_levels"] = total_approval_levels(conn, offer["job_id"])
            offer["current_level_approvers"] = approvers
            items.append(offer)
    return items


def assert_offer_exportable(conn: sqlite3.Connection, application_id: str) -> None:
    """tasks 3.6 的导出门槛：offer.status ∉ EXPORTABLE_OFFER_STATUSES ⇒ 拒绝。

    两个读者共用本函数（唯一真源，⛔ 不许任何调用点自己再判一遍状态）：
    `app/graph/letter_nodes.py::record_letter_access`（导出路径的唯一卡点）与
    `app/web/server.py` 的导出路由（把异常映射成 409 + 可读提示）。
    """
    offer = get_offer_by_application(conn, application_id)
    if offer is None:
        raise OfferNotFoundError(f"投递 {application_id!r} 尚无 Offer 记录")
    if offer["status"] not in EXPORTABLE_OFFER_STATUSES:
        raise OfferNotExportableError(
            f"Offer 当前状态为「{offer['status_label']}」，审批通过后才可导出文书"
        )
```

**文件**：`tests/test_offer_storage.py`（新建）

```python
"""app/storage/offer.py 的判定真源（U3 tasks 3.1/3.2/3.3/3.6）。"""
from __future__ import annotations

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer import (
    OfferNotCreatableError,
    OfferNotFoundError,
    OfferNotExportableError,
    approvers_for_level,
    assert_offer_creatable,
    assert_offer_exportable,
    current_approval_level,
    get_offer,
    list_pending_offers_for,
    stage_rank,
    total_approval_levels,
)
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn, *, stage="interview", status="active"):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, "
        "status) VALUES ('app1','c1','j1','r1',?,?)",
        (stage, status),
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-storage.db"))
    init_schema(c)
    _seed(c)
    return c


def _offer(conn, *, status="pending_approval", round_no=1, offer_id="o1"):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, approval_round, created_by) "
        "VALUES (?, 'app1', 'j1', '研发部', '2026-11-01', '李四', ?, ?, 'hr1')",
        (offer_id, status, round_no),
    )
    conn.commit()
    return offer_id


def _two_level_chain(conn, job_id="j1"):
    put_approval_chain(
        conn,
        job_id=job_id,
        chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ],
        updated_by="hr1",
    )


# ── 阶段位次 ────────────────────────────────────────────────────


def test_stage_rank_order_and_unknown():
    assert stage_rank("initial") < stage_rank("screening") < stage_rank("interview")
    assert stage_rank("interview") < stage_rank("offer") < stage_rank("hired")
    assert stage_rank("rejected") == -1
    assert stage_rank("no-such-stage") == -1


# ── 发起前置（tasks 3.1） ───────────────────────────────────────


def test_creatable_at_interview(conn):
    assert_offer_creatable(conn, application_id="app1")  # 不抛 = 过


def test_creatable_rejects_early_stage(conn):
    conn.execute("UPDATE application SET current_stage_id='screening' WHERE id='app1'")
    with pytest.raises(OfferNotCreatableError, match="阶段"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_terminated_status(conn):
    conn.execute("UPDATE application SET status='withdrawn' WHERE id='app1'")
    with pytest.raises(OfferNotCreatableError, match="已终止"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_rejection_record(conn):
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rej1','app1','human_decision','mgr')"
    )
    with pytest.raises(OfferNotCreatableError, match="淘汰记录"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_existing_offer(conn):
    _offer(conn)
    with pytest.raises(OfferNotCreatableError, match="已有 Offer"):
        assert_offer_creatable(conn, application_id="app1")


def test_creatable_rejects_unknown_application(conn):
    with pytest.raises(OfferNotFoundError):
        assert_offer_creatable(conn, application_id="no-such-app")


# ── 审批级数与当前级（tasks 3.2/3.3） ───────────────────────────


def test_default_chain_is_one_level_with_no_approvers(conn):
    assert total_approval_levels(conn, "j1") == 1
    assert approvers_for_level(conn, "j1", 1) == []


def test_configured_chain_levels_and_approvers(conn):
    _two_level_chain(conn)
    assert total_approval_levels(conn, "j1") == 2
    assert approvers_for_level(conn, "j1", 1) == ["mgr"]
    assert approvers_for_level(conn, "j1", 2) == ["gm"]
    assert approvers_for_level(conn, "j1", 3) == []


def test_current_level_starts_at_one_then_advances(conn):
    offer_id = _offer(conn)
    assert current_approval_level(conn, offer_id) == 1
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a1', ?, 1, 1, 'mgr', 'approved')",
        (offer_id,),
    )
    conn.commit()
    assert current_approval_level(conn, offer_id) == 2


def test_current_level_resets_on_new_round(conn):
    offer_id = _offer(conn, round_no=2)
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a0', ?, 1, 1, 'mgr', 'approved')",
        (offer_id,),
    )
    conn.commit()
    # 第 2 轮没有任何通过记录 ⇒ 回到第 1 级（链从第一级重走）。
    assert current_approval_level(conn, offer_id) == 1


# ── 本人待审列表（tasks 3.4） ───────────────────────────────────


def test_pending_list_only_returns_offers_where_approver_stands(conn):
    _two_level_chain(conn)
    offer_id = _offer(conn)
    assert [item["id"] for item in list_pending_offers_for(conn, "mgr")] == [offer_id]
    # 总经理在第 1 级尚未通过时看不到这条（逐级，不是"在链里就能审"）。
    assert list_pending_offers_for(conn, "gm") == []
    assert list_pending_offers_for(conn, "hr1") == []


def test_pending_list_advances_to_next_level(conn):
    _two_level_chain(conn)
    offer_id = _offer(conn)
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a1', ?, 1, 1, 'mgr', 'approved')",
        (offer_id,),
    )
    conn.commit()
    assert list_pending_offers_for(conn, "mgr") == []
    assert [item["current_level"] for item in list_pending_offers_for(conn, "gm")] == [2]


def test_pending_list_excludes_non_pending_offers(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["mgr"]}],
        updated_by="hr1",
    )
    _offer(conn, status="approved")
    assert list_pending_offers_for(conn, "mgr") == []


def test_pending_list_has_no_ai_decision_fields(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["mgr"]}],
        updated_by="hr1",
    )
    _offer(conn)
    item = list_pending_offers_for(conn, "mgr")[0]
    forbidden = ("score", "ranking", "recommend", "总分", "排名", "评分", "建议")
    flat = " ".join(str(key) for key in item) + " " + " ".join(
        str(value) for value in item.values()
    )
    for word in forbidden:
        assert word not in flat


# ── 导出门槛（tasks 3.6） ───────────────────────────────────────


@pytest.mark.parametrize(
    "status", ["approved", "exported", "accepted", "declined", "negotiating"]
)
def test_exportable_statuses_pass(conn, status):
    _offer(conn, status=status)
    assert_offer_exportable(conn, "app1")  # 不抛 = 过


@pytest.mark.parametrize("status", ["pending_approval", "needs_revision"])
def test_pending_and_needs_revision_are_rejected(conn, status):
    _offer(conn, status=status)
    with pytest.raises(OfferNotExportableError, match="审批通过后才可导出"):
        assert_offer_exportable(conn, "app1")


def test_exportable_rejects_missing_offer(conn):
    with pytest.raises(OfferNotFoundError):
        assert_offer_exportable(conn, "app1")


def test_get_offer_carries_labels_and_joins(conn):
    offer = get_offer(conn, _offer(conn))
    assert offer["job_title"] == "嵌入式工程师"
    assert offer["candidate_name"] == "张三"
    assert offer["status_label"] == "待审批"
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_storage.py -q
```

预期：全部通过（0 failed，24 passed）。`ApprovalChainNotConfiguredError` 在本 Task
定义、Task 3 抛出，本 Task 的测试不覆盖它的抛出路径。

---

### Task 2: `effect_create_offer` 节点（tasks 3.1）

**文件**：`app/graph/offer_nodes.py`（新建）

```python
"""Offer 内部审批流的 L4 编排层（offer-generation U3 tasks 3.1–3.3/3.5）。

四个 effect_* 节点，每个独占一类副作用、各带幂等键（工程铁律 1）：

| 节点 | 副作用 | 幂等键 |
|---|---|---|
| `effect_create_offer` | 写 `offer(pending_approval, round=1)` ＋ 流转 `offer` 阶段 ＋ 流转事实 | `{application_id}:effect_create_offer:{request_id}` |
| `effect_record_approval` | 写 `offer_approval` 一行 ＋ 推进 `offer.status` | `{offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}` |
| `effect_revise_offer` | 改 `offer` 字段 ＋ `round+1` ＋ `status=pending_approval` | `{offer_id}:effect_revise_offer:{request_id}` |
| `effect_publish_approval_todo` | 写内部通知（outbox） | `{offer_id}:effect_publish_approval_todo:{round}:{level}` |

⛔ 节点内不 `conn.commit()`：写入与 `effect_log` 记录由 `idempotent_effect`
装饰器在**同一个事务**里一次性提交（工程铁律 1）。
⛔ 本模块不 import 任何 `app/agents/**`，也不 import / 不调 `app/outbound/**`
（候选人外发门禁的唯一入口在 `app/outbound/delivery.py`）：U3 没有任何 AI 调用、
没有任何对外发送（spec「录用决定不由 AI 做」＋`CLAUDE.md` 决策代理表的不可代项
"候选人对外通道"）。
⚠️ 本模块源码里**刻意不出现那个入口函数的名字**：Task 4 的
`test_offer_nodes_never_calls_the_candidate_gate` 用源码扫描做反证，文档里写一遍
就足以让该断言失守（2026-10-10 提取验证实测踩到过，别再加回来）。
"""
from __future__ import annotations

import logging
import sqlite3
import uuid

from app.channels.base import Channel, OutboundMessage
from app.storage.idempotency import idempotent_effect
from app.storage.offer import (
    ApprovalAlreadyRecordedError,
    ApprovalChainNotConfiguredError,
    ApprovalOutOfOrderError,
    NotAnApproverError,
    OfferNotEditableError,
    OfferNotPendingApprovalError,
    approvers_for_level,
    assert_offer_creatable,
    current_approval_level,
    get_offer,
    total_approval_levels,
)

logger = logging.getLogger(__name__)


def _require_non_empty(**fields: str | None) -> None:
    for name, value in fields.items():
        if not value or not str(value).strip():
            raise ValueError(f"{name} 不能为空")


@idempotent_effect("effect_create_offer")
def effect_create_offer(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    department: str,
    start_date: str,
    report_to: str,
    note: str | None,
    created_by: str,
) -> str:
    """effect_* 节点：发起 Offer（tasks 3.1）。**三写同一个事务**：

    ① `offer(pending_approval, approval_round=1)`；
    ② `application.current_stage_id` → `offer`（`status` 保持 `active`——
    投递终止是 U5 的事）；
    ③ `application_stage_history`（`actor_type='human'`、`action=NULL`——
    该列 CHECK 只允许排期类动作，见 Task 1 的说明）。

    幂等键 = `{application_id}:effect_create_offer:{request_id}`（tasks 3.1 字面
    公式）：`thread_id=application_id`、`business_key=请求方给的 request_id`。
    重跑（同 request_id）被 `idempotent_effect` 短路返回 `None`，调用方按
    "已有 Offer" 读回落库真身（见 Task 5 的端点与 Task 2 的重跑测试）。

    ⚠️ 前置校验在这里**重做一遍**：路由的 compute 面到 effect 面之间投递状态
    可能被改（例如同一时刻有人确认淘汰），只信事务内的这次判定。
    """
    _require_non_empty(
        created_by=created_by,
        department=department,
        start_date=start_date,
        report_to=report_to,
    )
    assert_offer_creatable(conn, application_id=application_id)
    row = conn.execute(
        "SELECT job_id, current_stage_id FROM application WHERE id = ?",
        (application_id,),
    ).fetchone()
    job_id, from_stage_id = row
    offer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, note, status, approval_round, created_by, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_approval', 1, ?, ?)",
        (
            offer_id,
            application_id,
            job_id,
            department,
            start_date,
            report_to,
            note,
            created_by,
            created_by,
        ),
    )
    conn.execute(
        "UPDATE application SET current_stage_id = 'offer' WHERE id = ?",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO application_stage_history (id, application_id, from_stage_id, "
        "to_stage_id, actor_type, actor) VALUES (?, ?, ?, 'offer', 'human', ?)",
        (str(uuid.uuid4()), application_id, from_stage_id, created_by),
    )
    return offer_id
```

**文件**：`tests/test_offer_nodes_create.py`（新建）

```python
"""effect_create_offer 的三写同事务、幂等与前置（U3 tasks 3.1）。"""
from __future__ import annotations

import pytest

from app.graph.offer_nodes import effect_create_offer
from app.storage.db import get_connection, init_schema
from app.storage.offer import OfferNotCreatableError


def _seed(conn, *, stage="interview", status="active"):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, "
        "status) VALUES ('app1','c1','j1','r1',?,?)",
        (stage, status),
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-nodes-create.db"))
    init_schema(c)
    _seed(c)
    return c


def _create(conn, *, request_id="req-1", **overrides):
    kwargs = dict(
        thread_id="app1",
        business_key=request_id,
        application_id="app1",
        department="研发部",
        start_date="2026-11-01",
        report_to="李四",
        note="面试表现符合岗位要求",
        created_by="hr1",
    )
    kwargs.update(overrides)
    return effect_create_offer(conn, **kwargs)


def test_create_writes_offer_stage_and_history_in_one_transaction(conn):
    offer_id = _create(conn)
    assert offer_id
    assert conn.execute(
        "SELECT status, approval_round, department, start_date, report_to, note "
        "FROM offer WHERE id = ?",
        (offer_id,),
    ).fetchone() == (
        "pending_approval", 1, "研发部", "2026-11-01", "李四", "面试表现符合岗位要求",
    )
    assert conn.execute(
        "SELECT current_stage_id, status FROM application WHERE id = 'app1'"
    ).fetchone() == ("offer", "active")
    assert conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor, action "
        "FROM application_stage_history WHERE application_id = 'app1'"
    ).fetchall() == [("interview", "offer", "human", "hr1", None)]
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key = ?",
        ("app1:effect_create_offer:req-1",),
    ).fetchone()[0] == 1


def test_create_replay_same_request_id_is_noop(conn):
    assert _create(conn)
    assert _create(conn) is None  # 幂等命中：装饰器短路，函数体没跑
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key = ?",
        ("app1:effect_create_offer:req-1",),
    ).fetchone()[0] == 1


def test_create_rejects_rejected_application(conn):
    conn.execute("UPDATE application SET status='rejected' WHERE id='app1'")
    conn.commit()
    with pytest.raises(OfferNotCreatableError):
        _create(conn)
    # 事务内校验失败 ⇒ 业务行一行不留（装饰器回滚）。
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key = ?",
        ("app1:effect_create_offer:req-1",),
    ).fetchone()[0] == 0


def test_create_rejects_blank_fields(conn):
    with pytest.raises(ValueError, match="department 不能为空"):
        _create(conn, department="   ")
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_nodes_create.py -q
```

预期：全部通过（0 failed，4 passed）。⚠️ 若第一条测试报 `no such column: action`，说明
`application_stage_history.action` 的迁移没跑到，用
`python -c "from app.storage.db import get_connection, init_schema; c = get_connection('/tmp/u3-check.db'); init_schema(c); print([r[1] for r in c.execute('PRAGMA table_info(application_stage_history)')])"`
核对——本 Task 的断言依赖该列存在（U1 已落地的 `_ADDED_COLUMNS` 条目）。

---

### Task 3: `effect_record_approval` 与 `effect_revise_offer`（tasks 3.2 / 3.3）

**文件**：`app/graph/offer_nodes.py`（追加到 Task 2 之后；⛔ import 区不变——
本 Task 用到的名字 Task 2 已全部 import）

```python
@idempotent_effect("effect_record_approval")
def effect_record_approval(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    offer_id: str,
    level: int,
    approver: str,
    decision: str,
    comment: str | None = None,
) -> dict:
    """effect_* 节点：记一级审批并推进状态（tasks 3.2；spec「内部审批链」三 Scenario）。

    幂等键 = `{offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}`
    （tasks 3.2 字面公式）：`thread_id=offer_id`、
    `business_key=f"{round}:{level}:{approver}:{decision}"`。
    存储层第二道防线是 U1 建的 `offer_approval(offer_id, round, level)` 唯一索引
    ——撞上它说明这件事已经被另一条路径做完了，转换成
    `ApprovalAlreadyRecordedError`（HTTP 409），⛔ 不让 IntegrityError 冒成 500。

    状态机（design D6）：
      - `returned`（任一级）⇒ `offer.status='needs_revision'`，**后续级不被触发**：
        逐级校验保证了本轮更高一级还没有行，退回后"当前级"仍指向被退的那一级，
        后续级过不了 `level == current_approval_level()` 这道闸。
      - `approved` 且 `level == 最后一级` ⇒ `offer.status='approved'`。
      - `approved` 且非最后一级 ⇒ 状态不变（仍 `pending_approval`）。

    ⛔ 逐级强制（`level == 当前级`）：spec 写的是"**逐级**审批"，不是"链里任何
    一级都能先审"。没有这道闸，总经理可以越过业务经理直接通过，而
    `offer_approval(offer_id, round, level)` 唯一索引完全挡不住（两级都还没行）。
    ⛔ 空审批人列表 fail-closed（不猜审批人，见 Task 1 的
    `ApprovalChainNotConfiguredError`）。
    """
    if decision not in ("approved", "returned"):
        raise ValueError(f"decision 只能是 approved/returned，收到: {decision!r}")
    _require_non_empty(approver=approver)
    if decision == "returned" and not (comment or "").strip():
        raise ValueError("退回必须填意见（spec Scenario「一级退回：退回并填意见」）")

    offer = get_offer(conn, offer_id)
    if offer["status"] != "pending_approval":
        raise OfferNotPendingApprovalError(
            f"Offer 当前状态为「{offer['status_label']}」，不接受审批动作"
        )
    round_no = offer["approval_round"]
    expected_level = current_approval_level(conn, offer_id)
    if level != expected_level:
        raise ApprovalOutOfOrderError(
            f"第 {expected_level} 级尚未通过，不能直接审批第 {level} 级"
        )
    approvers = approvers_for_level(conn, offer["job_id"], level)
    if not approvers:
        raise ApprovalChainNotConfiguredError(
            f"岗位 {offer['job_id']!r} 第 {level} 级尚未配置审批人，"
            "请先由 HR 维护该岗位的审批链"
        )
    if approver not in approvers:
        raise NotAnApproverError(f"{approver!r} 不是第 {level} 级审批人")

    try:
        conn.execute(
            "INSERT INTO offer_approval (id, offer_id, round, level, approver, "
            "decision, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), offer_id, round_no, level, approver, decision, comment),
        )
    except sqlite3.IntegrityError as exc:
        raise ApprovalAlreadyRecordedError(
            f"第 {level} 级审批已记录（offer={offer_id} round={round_no}）"
        ) from exc

    if decision == "returned":
        new_status = "needs_revision"
    elif level >= total_approval_levels(conn, offer["job_id"]):
        new_status = "approved"
    else:
        new_status = "pending_approval"
    conn.execute(
        "UPDATE offer SET status = ?, updated_by = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (new_status, approver, offer_id),
    )
    return {
        "offer_id": offer_id,
        "round": round_no,
        "level": level,
        "approver": approver,
        "decision": decision,
        "status": new_status,
    }


@idempotent_effect("effect_revise_offer")
def effect_revise_offer(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    offer_id: str,
    department: str,
    start_date: str,
    report_to: str,
    note: str | None,
    updated_by: str,
) -> int:
    """effect_* 节点：退回后修改（tasks 3.3）。返回新一轮的轮次号。

    仅 `needs_revision` 可改；改后 `approval_round+1`、`status='pending_approval'`。
    ⛔ 不删、不改旧轮的 `offer_approval` 行：那几行是"上一次为什么被退"的唯一
    证据（spec「退回 MUST 让 Offer 记录回到待修改，退回留痕保存」）；新一轮靠
    `round` 号与旧轮并存，`(offer_id, round, level)` 唯一索引天然允许。
    "链从第一级重走"由 `current_approval_level`（本轮已通过的最大级 + 1）算出，
    ⛔ 不写任何"重置级号"的语句。

    幂等键 = `{offer_id}:effect_revise_offer:{request_id}`（request_id 由调用方在
    请求体里携带并跨重试保持稳定）：⛔ 不用"内容哈希"当 business_key——同一内容
    重试必须短路，而"HR 把正文改回上一版"又必须真的进新一轮，内容哈希在第二种
    情形下会把 HR 永久卡在「待修改」（第二轮改回第一轮的内容 ⇒ 键与第一轮相同）。
    """
    _require_non_empty(
        updated_by=updated_by,
        department=department,
        start_date=start_date,
        report_to=report_to,
    )
    offer = get_offer(conn, offer_id)
    if offer["status"] != "needs_revision":
        raise OfferNotEditableError(
            f"Offer 当前状态为「{offer['status_label']}」，只有「待修改」可以修改"
        )
    new_round = offer["approval_round"] + 1
    conn.execute(
        "UPDATE offer SET department = ?, start_date = ?, report_to = ?, note = ?, "
        "status = 'pending_approval', approval_round = ?, updated_by = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (department, start_date, report_to, note, new_round, updated_by, offer_id),
    )
    return new_round
```

**文件**：`tests/test_offer_nodes_approval.py`（新建）

```python
"""effect_record_approval / effect_revise_offer 的状态机、逐级与幂等（tasks 3.2/3.3）。"""
from __future__ import annotations

import pytest

from app.graph.offer_nodes import effect_record_approval, effect_revise_offer
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer import (
    ApprovalAlreadyRecordedError,
    ApprovalChainNotConfiguredError,
    ApprovalOutOfOrderError,
    NotAnApproverError,
    OfferNotEditableError,
    OfferNotPendingApprovalError,
)
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn, *, chain=True, stage="interview"):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1',?)",
        (stage,),
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()
    if chain:
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[
                {"level": 1, "approver_account_ids": ["mgr"]},
                {"level": 2, "approver_account_ids": ["gm"]},
            ],
            updated_by="hr1",
        )


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-nodes-approval.db"))
    init_schema(c)
    _seed(c)
    return c


def _offer(conn, *, status="pending_approval", round_no=1):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, approval_round, created_by) "
        "VALUES ('o1','app1','j1','研发部','2026-11-01','李四',?,?,'hr1')",
        (status, round_no),
    )
    conn.commit()
    return "o1"


def _approve(conn, *, level, approver, decision="approved", comment=None, round_no=1):
    return effect_record_approval(
        conn,
        thread_id="o1",
        business_key=f"{round_no}:{level}:{approver}:{decision}",
        offer_id="o1",
        level=level,
        approver=approver,
        decision=decision,
        comment=comment,
    )


def _status(conn):
    return conn.execute(
        "SELECT status, approval_round FROM offer WHERE id = 'o1'"
    ).fetchone()


# ── 两级通过（spec Scenario） ──────────────────────────────────


def test_two_level_approval_ends_approved(conn):
    _offer(conn)
    assert _approve(conn, level=1, approver="mgr")["status"] == "pending_approval"
    assert _status(conn) == ("pending_approval", 1)
    assert _approve(conn, level=2, approver="gm")["status"] == "approved"
    assert _status(conn) == ("approved", 1)
    rows = conn.execute(
        "SELECT level, approver, decision, at FROM offer_approval "
        "WHERE offer_id = 'o1' ORDER BY level"
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (1, "mgr", "approved"),
        (2, "gm", "approved"),
    ]
    assert all(r[3] for r in rows)  # 每条留痕都有时刻（spec「各含审批人与时刻」）


# ── 一级退回（spec Scenario） ──────────────────────────────────


def test_first_level_return_blocks_next_level(conn):
    _offer(conn)
    result = _approve(conn, level=1, approver="mgr", decision="returned",
                      comment="入职日期待与候选人确认")
    assert result["status"] == "needs_revision"
    assert _status(conn) == ("needs_revision", 1)
    # 后续级不被触发：第 2 级任何审批动作都被状态闸挡下。
    with pytest.raises(OfferNotPendingApprovalError):
        _approve(conn, level=2, approver="gm")
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 1
    assert conn.execute(
        "SELECT comment FROM offer_approval WHERE level = 1"
    ).fetchone()[0] == "入职日期待与候选人确认"  # 退回意见保存


def test_return_requires_comment(conn):
    _offer(conn)
    with pytest.raises(ValueError, match="退回必须填意见"):
        _approve(conn, level=1, approver="mgr", decision="returned", comment="  ")


# ── 非审批人 / 跳级 / 未配置（spec Scenario + fail-closed） ─────


def test_non_approver_is_rejected(conn):
    _offer(conn)
    with pytest.raises(NotAnApproverError):
        _approve(conn, level=1, approver="hr1")
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0


def test_skipping_a_level_is_rejected(conn):
    _offer(conn)
    with pytest.raises(ApprovalOutOfOrderError, match="第 1 级尚未通过"):
        _approve(conn, level=2, approver="gm")
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0


def test_unconfigured_chain_fails_closed(tmp_path):
    c = get_connection(str(tmp_path / "no-chain.db"))
    init_schema(c)
    _seed(c, chain=False)
    _offer(c)
    with pytest.raises(ApprovalChainNotConfiguredError, match="尚未配置审批人"):
        _approve(c, level=1, approver="mgr")
    assert c.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 0


def test_approval_rejected_when_not_pending(conn):
    _offer(conn, status="approved")
    with pytest.raises(OfferNotPendingApprovalError):
        _approve(conn, level=1, approver="mgr")


# ── 幂等：重跑不产生第二条留痕（spec Scenario「审批节点重跑」） ──


def test_replay_same_decision_is_noop(conn):
    _offer(conn)
    assert _approve(conn, level=1, approver="mgr") is not None
    assert _approve(conn, level=1, approver="mgr") is None  # 同键短路
    assert _approve(conn, level=2, approver="gm") is not None
    assert _approve(conn, level=2, approver="gm") is None
    assert conn.execute("SELECT COUNT(*) FROM offer_approval").fetchone()[0] == 2
    assert _status(conn) == ("approved", 1)
    # 恒等式（铁律 1）：该 offer 的审批 effect_log 条数 == offer_approval 行数。
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_record_approval' "
        "AND thread_id = 'o1'"
    ).fetchone()[0] == 2


def test_duplicate_row_hits_unique_index_and_maps_to_domain_error(conn):
    """绕过幂等键直接插同 (offer, round, level) 行 ⇒ 具名异常，⛔ 不是 IntegrityError。"""
    _offer(conn)
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision, "
        "comment) VALUES ('other', 'o1', 1, 1, 'mgr', 'returned', '先退回')"
    )
    conn.commit()
    with pytest.raises(ApprovalAlreadyRecordedError, match="已记录"):
        _approve(conn, level=1, approver="mgr", decision="returned", comment="重复")


# ── 退回后修改：round+1、链从第一级重走（tasks 3.3） ───────────


def test_revise_bumps_round_and_restarts_from_level_one(conn):
    _offer(conn)
    _approve(conn, level=1, approver="mgr", decision="returned", comment="改入职日期")
    assert effect_revise_offer(
        conn,
        thread_id="o1",
        business_key="req-2",
        offer_id="o1",
        department="研发部",
        start_date="2026-12-01",
        report_to="李四",
        note="入职日期已与候选人确认",
        updated_by="hr1",
    ) == 2
    assert _status(conn) == ("pending_approval", 2)
    # 旧轮留痕原样保留。
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE round = 1"
    ).fetchone()[0] == 1
    # 新一轮从第 1 级重走。
    with pytest.raises(ApprovalOutOfOrderError):
        _approve(conn, level=2, approver="gm", round_no=2)
    _approve(conn, level=1, approver="mgr", round_no=2)
    _approve(conn, level=2, approver="gm", round_no=2)
    assert _status(conn) == ("approved", 2)
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = 'o1'"
    ).fetchone()[0] == 3


def test_revise_replay_is_noop(conn):
    _offer(conn)
    _approve(conn, level=1, approver="mgr", decision="returned", comment="改")
    kwargs = dict(
        thread_id="o1",
        business_key="req-2",
        offer_id="o1",
        department="研发部",
        start_date="2026-12-01",
        report_to="李四",
        note=None,
        updated_by="hr1",
    )
    assert effect_revise_offer(conn, **kwargs) == 2
    assert effect_revise_offer(conn, **kwargs) is None  # 同 request_id 短路
    assert _status(conn) == ("pending_approval", 2)  # ⛔ 不会变成第 3 轮


def test_revise_requires_needs_revision(conn):
    _offer(conn)
    with pytest.raises(OfferNotEditableError, match="只有「待修改」可以修改"):
        effect_revise_offer(
            conn,
            thread_id="o1",
            business_key="req-2",
            offer_id="o1",
            department="研发部",
            start_date="2026-12-01",
            report_to="李四",
            note=None,
            updated_by="hr1",
        )


def test_offer_table_still_has_no_salary_column(conn):
    """U3 ⛔ 不因任何改动给 offer 表加报酬列（合规红线；U1 反证断言的护栏）。"""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(offer)")]
    forbidden = ("salary", "pay", "compensation", "bonus", "薪")
    assert not [c for c in columns if any(k in c.lower() or k in c for k in forbidden)]
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_nodes_approval.py -q
```

预期：全部通过（0 failed，13 passed）。

---

### Task 4: `effect_publish_approval_todo` 审批待办的内部通知（tasks 3.5）

**文件**：`app/graph/offer_nodes.py`（追加到 Task 3 之后）

```python
@idempotent_effect("effect_publish_approval_todo")
def effect_publish_approval_todo(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    channel: Channel,
    offer_id: str,
    application_id: str,
    job_title: str,
    round_no: int,
    level: int,
    approvers: list[str],
) -> None:
    """effect_* 节点：把"该你审了"写进**内部**通知通道（tasks 3.5）。

    幂等键 = `{offer_id}:effect_publish_approval_todo:{round}:{level}`
    （tasks 3.5「幂等键含 offer_id/round/level」的字面要求）：`thread_id=offer_id`、
    `business_key=f"{round}:{level}"`。同一轮同一级只会有一条待办——重复触发
    （重跑、双击、客户端重试）被 `idempotent_effect` 短路。

    ⛔ **不走候选人外发门禁**：本函数只调 `channel.deliver()`（`WebChannel` ⇒ 写
    `outbox`），⛔ 不 import 也不调 `app/outbound/**` 里的候选人外发入口——
    `outbound-approval-gate` spec Scenario「内部通知不受影响」明写内部员工通知
    "不经候选人外发门禁"。这一点由 `tests/test_offer_nodes_todo.py::
    test_offer_nodes_never_calls_the_candidate_gate` 做源码级反证（⛔ 所以本模块
    **不能出现那个入口函数的名字**，写进注释也会让断言失守）。

    ⚠️ 无通道的处置**不在本节点**：`channel` 为空时由 `publish_approval_todo()`
    在调用本节点**之前**返回（⛔ 不写 `effect_log`，否则"没发出去的待办"会在幂等
    表上留下一条"已完成"的假记录）。因此本节点对 `channel is None` 直接报错，
    而不是静默通过。

    ⚠️ `channel.deliver()` 自身不 `conn.commit()`（`WebChannel` 的契约）：outbox
    行落在这个未提交事务里，由 `idempotent_effect` 装饰器与 `effect_log` 一次性
    提交——这正是铁律 1 要的"业务写与幂等记录同事务"。
    """
    if channel is None:
        raise ValueError(
            "channel 不能为空——无通道的情形由 publish_approval_todo() 在调用本节点"
            "之前处置（tasks 3.5：无通道时只靠列表页，且 ⛔ 不写 effect_log）"
        )
    channel.deliver(
        offer_id,
        OutboundMessage(
            type="offer_approval_todo",
            payload={
                "offer_id": offer_id,
                "application_id": application_id,
                "job_title": job_title,
                "round": round_no,
                "level": level,
                "approvers": list(approvers),
                # 相对路径（部署约束 1：前端资源与接口调用一律相对路径，⛔ 不硬编码
                # 挂载前缀）：页面路由见 Task 6。
                "approve_url": f"/offers/{offer_id}/approve",
            },
        ),
    )


def publish_approval_todo(
    conn: sqlite3.Connection, *, offer: dict, channel: Channel | None
) -> None:
    """把本轮当前级的待办推给该级审批人（**非 effect 节点**，只是调用封装）。

    三处调用点：发起 Offer 之后（第 1 轮第 1 级）、某级通过之后（下一级）、
    退回修改之后（新一轮第 1 级）。三处都只传 `offer`（`app.storage.offer.get_offer`
    的返回）——级号与审批人由本函数现算，⛔ 调用点不许自己算一遍（两处算法迟早漂移）。

    `channel is None` ⇒ 只记 WARNING 直接返回：tasks 3.5「无通道时只靠列表页
    （不阻塞）」。列表页 `GET /offers/pending`（Task 6）是待办**权威**视图，
    内部通知只是顺手的提醒。
    """
    if channel is None:
        logger.warning(
            "内部通知通道未装配：Offer %s 第 %s 轮的审批待办仅靠列表页",
            offer["id"],
            offer["approval_round"],
        )
        return
    level = current_approval_level(conn, offer["id"])
    effect_publish_approval_todo(
        conn,
        thread_id=offer["id"],
        business_key=f"{offer['approval_round']}:{level}",
        channel=channel,
        offer_id=offer["id"],
        application_id=offer["application_id"],
        job_title=offer["job_title"],
        round_no=offer["approval_round"],
        level=level,
        approvers=approvers_for_level(conn, offer["job_id"], level),
    )
```

**文件**：`app/channels/base.py`（修改，一行注释）

把 `OutboundMessage.type` 那一行的注释扩成：

```python
    type: str  # "question" | "confirmation_prompt" | "jd_result" | "needs_manual"
    #          | "offer_approval_todo"（offer-generation U3：Offer 审批待办，内部通知）
```

**文件**：`tests/test_offer_nodes_todo.py`（新建）

```python
"""effect_publish_approval_todo 的内部通知、逐级幂等与"不碰候选人门禁"（tasks 3.5）。"""
from __future__ import annotations

import inspect
import json

import pytest

from app.channels.web_channel import WebChannel
from app.graph import offer_nodes
from app.graph.offer_nodes import effect_publish_approval_todo, publish_approval_todo
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer import get_offer
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','interview')"
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, approval_round, created_by) "
        "VALUES ('o1','app1','j1','研发部','2026-11-01','李四','pending_approval',1,'hr1')"
    )
    conn.commit()
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ],
        updated_by="hr1",
    )


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-nodes-todo.db"))
    init_schema(c)
    _seed(c)
    return c


def _outbox(conn):
    return conn.execute(
        "SELECT thread_id, message_type, payload_json FROM outbox ORDER BY id"
    ).fetchall()


def test_publish_writes_todo_to_internal_outbox(conn):
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=WebChannel(conn))
    rows = _outbox(conn)
    assert len(rows) == 1
    thread_id, message_type, payload_json = rows[0]
    assert (thread_id, message_type) == ("o1", "offer_approval_todo")
    payload = json.loads(payload_json)
    assert payload["level"] == 1
    assert payload["round"] == 1
    assert payload["approvers"] == ["mgr"]
    assert payload["approve_url"] == "/offers/o1/approve"
    assert payload["job_title"] == "嵌入式工程师"
    assert payload["application_id"] == "app1"
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_publish_approval_todo'"
    ).fetchone()[0] == 1


def test_publish_is_idempotent_per_round_and_level(conn):
    offer = get_offer(conn, "o1")
    channel = WebChannel(conn)
    publish_approval_todo(conn, offer=offer, channel=channel)
    publish_approval_todo(conn, offer=offer, channel=channel)  # 同 (round, level)
    assert len(_outbox(conn)) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key = ?",
        ("o1:effect_publish_approval_todo:1:1",),
    ).fetchone()[0] == 1


def test_next_level_gets_its_own_todo(conn):
    channel = WebChannel(conn)
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=channel)
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a1','o1',1,1,'mgr','approved')"
    )
    conn.commit()
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=channel)
    rows = _outbox(conn)
    assert len(rows) == 2
    assert json.loads(rows[1][2])["approvers"] == ["gm"]
    assert json.loads(rows[1][2])["level"] == 2


def test_no_channel_does_not_block_and_leaves_no_effect_log(conn):
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=None)
    assert _outbox(conn) == []
    # ⛔ 不写 effect_log：否则"没发出去的待办"会在幂等表上留下一条"已完成"的假记录。
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_publish_approval_todo'"
    ).fetchone()[0] == 0


def test_effect_node_rejects_none_channel(conn):
    with pytest.raises(ValueError, match="channel 不能为空"):
        effect_publish_approval_todo(
            conn,
            thread_id="o1",
            business_key="1:1",
            channel=None,
            offer_id="o1",
            application_id="app1",
            job_title="嵌入式工程师",
            round_no=1,
            level=1,
            approvers=["mgr"],
        )


def test_offer_nodes_never_calls_the_candidate_gate():
    """源码级反证：U3 的内部通知 ⛔ 不经过候选人外发门禁、⛔ 不含任何 AI 调用。

    两个断言合起来是 spec 两条硬约束在代码面上的护栏：
    `outbound-approval-gate` spec Scenario「内部通知不受影响」（不走门禁）与
    `offer-record-and-approval` spec「录用决定不由 AI 做」（零 AI 调用）。
    """
    source = inspect.getsource(offer_nodes)
    assert "deliver_candidate_message" not in source
    assert "app.outbound" not in source
    assert "app.agents" not in source
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_nodes_todo.py -q
```

预期：全部通过（0 failed，6 passed）。

---

### Task 5: server.py 接线——发起 Offer / 逐级审批 / 退回后修改（tasks 3.1–3.3）

**文件**：`app/web/server.py`（修改，四处）

**5a. 顶部 import 追加**（在现有 `from app.storage.offer_approval_chain import (...)` 之后）：

```python
from app.graph.offer_nodes import (
    effect_create_offer,
    effect_record_approval,
    effect_revise_offer,
    publish_approval_todo,
)
from app.storage.offer import (
    ApprovalAlreadyRecordedError,
    ApprovalChainNotConfiguredError,
    ApprovalOutOfOrderError,
    NotAnApproverError,
    OfferNotCreatableError,
    OfferNotEditableError,
    OfferNotFoundError,
    OfferNotPendingApprovalError,
    approval_history,
    current_approval_level,
    get_offer,
    get_offer_by_application,
    total_approval_levels,
)
```

**5b. 模块级请求模型**（在 `class OfferApprovalChainRequest(BaseModel)` 之后追加）：

```python
class OfferCreateRequest(BaseModel):
    department: str
    start_date: str
    report_to: str
    note: str | None = None
    # 幂等键的业务键（tasks 3.1：`{application_id}:effect_create_offer:{request_id}`）。
    # 由调用方生成并在重试时保持稳定，与 InterviewSessionCreateRequest.request_id 同款。
    request_id: str


class OfferReviseRequest(BaseModel):
    department: str
    start_date: str
    report_to: str
    note: str | None = None
    request_id: str


class OfferApprovalRequest(BaseModel):
    decision: str  # approved | returned
    comment: str | None = None
    # 审批级号：**页面必须带**（从详情载荷的 current_level 原样取）。它是幂等键
    # `{offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}` 的
    # 一部分——重试时同一个 level 才能命中同一条键、真正短路（见路由里的说明）。
    # 缺省（不传）时按"本轮当前级"推断，只适用于首次提交。
    level: int | None = None
```

**5c. 路由**（追加在 U1「岗位级审批链维护接口」段之后）：

```python
    # ── offer-generation U3：发起 Offer / 逐级审批 / 退回后修改（tasks 3.1–3.3）──
    # ⚠️ `/api/offers/**` 不在 AuthMiddleware 的 PROTECTED_PATH_PREFIXES 里
    # （app/middleware/auth.py），每个路由都要手动过 _require_login。

    def _require_login(request: Request) -> str:
        """U3 的登录闸，返回 username。

        ⛔ 不判 `role == 'hr'`：审批人是**岗位审批链**里配置的可识别账号
        （spec「内部审批链」），在本仓库是 `hr_account.role ∈ {'hr','interviewer',
        'dept_manager'}` 里的账号——按 HR 角色卡会把 spec 里合法的业务经理／总经理
        403 挡在门外，而症状是"审批链配了也没人能审"。逐级的**是否该级审批人**由
        `effect_record_approval` 按审批链判定（非审批人 ⇒ 403）。
        """
        auth = getattr(request.state, "auth", None)
        if not getattr(auth, "authenticated", False):
            raise HTTPException(status_code=401, detail="未登录")
        return getattr(auth, "user_id", None)

    def _offer_payload(offer_id: str) -> dict:
        """审批页/接口统一载荷：⛔ 只有 Offer 与投递的非敏感事实，无任何 AI 字段
        （spec「录用决定不由 AI 做」；反证测试见 Task 6）。"""
        offer = get_offer(conn, offer_id)
        level = current_approval_level(conn, offer_id)
        return {
            "id": offer["id"],
            "application_id": offer["application_id"],
            "job_id": offer["job_id"],
            "job_title": offer["job_title"],
            "candidate_name": offer["candidate_name"],
            "department": offer["department"],
            "start_date": offer["start_date"],
            "report_to": offer["report_to"],
            "note": offer["note"],
            "status": offer["status"],
            "status_label": offer["status_label"],
            "approval_round": offer["approval_round"],
            "current_level": level,
            "total_levels": total_approval_levels(conn, offer["job_id"]),
            "approvals": approval_history(conn, offer_id),
            "created_by": offer["created_by"],
            "created_at": offer["created_at"],
        }

    @router.post("/api/applications/{application_id}/offer", status_code=201)
    def create_offer(
        application_id: str, req: OfferCreateRequest, request: Request
    ):
        username = _require_login(request)
        # ⚠️ ⛔ 这里**不做** `assert_offer_creatable` 前置预检（2026-10-10 提取验证
        # 实测踩到）：预检在 effect 节点**之前**跑，而"重试同一 request_id"的第二次
        # 请求此时 Offer 已经存在 ⇒ 预检直接 409，永远走不到幂等短路，客户端拿不到
        # 第一次的结果（`test_create_replay_same_request_id_returns_same_offer` 就是
        # 这条的判据）。前置校验的唯一权威在节点的事务内那一遍，这里只把它的异常
        # 映射成 4xx。
        try:
            offer_id = effect_create_offer(
                conn,
                thread_id=application_id,
                business_key=req.request_id,
                application_id=application_id,
                department=req.department,
                start_date=req.start_date,
                report_to=req.report_to,
                note=req.note,
                created_by=username,
            )
        except OfferNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OfferNotCreatableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            # department/start_date/report_to 为空等入参级问题（上面那个具名异常是
            # ValueError 子类，已被更靠前的分支接住——顺序不能调）。
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if offer_id is None:
            # 幂等命中（同 request_id 重试）：读回落库真身，⛔ 不把它当失败。
            existing = get_offer_by_application(conn, application_id)
            if existing is None:
                raise HTTPException(
                    status_code=409,
                    detail="该 request_id 已被处理，但找不到对应 Offer 记录",
                )
            offer_id = existing["id"]
        publish_approval_todo(conn, offer=get_offer(conn, offer_id), channel=channel)
        return _offer_payload(offer_id)

    @router.patch("/api/offers/{offer_id}")
    def revise_offer(
        offer_id: str, req: OfferReviseRequest, request: Request
    ):
        username = _require_login(request)
        try:
            effect_revise_offer(
                conn,
                thread_id=offer_id,
                business_key=req.request_id,
                offer_id=offer_id,
                department=req.department,
                start_date=req.start_date,
                report_to=req.report_to,
                note=req.note,
                updated_by=username,
            )
        except OfferNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OfferNotEditableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # 新一轮第 1 级的待办（幂等键含新 round，⛔ 不会被上一轮的键短路）。
        publish_approval_todo(conn, offer=get_offer(conn, offer_id), channel=channel)
        return _offer_payload(offer_id)

    @router.post("/api/offers/{offer_id}/approval")
    def record_offer_approval(
        offer_id: str, req: OfferApprovalRequest, request: Request
    ):
        username = _require_login(request)
        if req.decision not in ("approved", "returned"):
            raise HTTPException(
                status_code=422, detail="decision 只能是 approved 或 returned"
            )
        try:
            offer = get_offer(conn, offer_id)
        except OfferNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # ⚠️ 幂等键与 tasks 3.2 逐字一致：
        # {offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}
        # 级号优先取请求体里带的那个（页面从详情载荷的 current_level 原样回传）：
        # **重试必须命中同一条键**。若这里一律现算"本轮当前级"，那么"第 1 级通过后
        # 客户端重试同一请求"会算出第 2 级 ⇒ 键变了 ⇒ 装饰器不短路 ⇒ 一路走到
        # `NotAnApproverError`，用户看到 403 而不是幂等成功（2026-10-10 写作时
        # review 发现，已由 `tests/test_offer_flow_endpoints.py::
        # test_replay_approval_is_idempotent_over_http` 锁住）。
        # 不传 level 时退化为"本轮当前级"，只覆盖首次提交这一条路径。
        # 逐级仍由节点强制（`level != current_approval_level()` ⇒ 409）。
        level = (
            req.level if req.level is not None
            else current_approval_level(conn, offer_id)
        )
        try:
            effect_record_approval(
                conn,
                thread_id=offer_id,
                business_key=f"{offer['approval_round']}:{level}:{username}:{req.decision}",
                offer_id=offer_id,
                level=level,
                approver=username,
                decision=req.decision,
                comment=req.comment,
            )
        except ApprovalChainNotConfiguredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotAnApproverError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ApprovalOutOfOrderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ApprovalAlreadyRecordedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OfferNotPendingApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            # "退回必须填意见"等入参级问题（上面几个具名异常都是 ValueError 子类，
            # 已经先被接住——顺序不能调）。
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        refreshed = get_offer(conn, offer_id)
        if refreshed["status"] == "pending_approval":
            # 还有下一级 ⇒ 给下一级发待办；已经是四级全过（approved）就不发。
            publish_approval_todo(conn, offer=refreshed, channel=channel)
        return _offer_payload(offer_id)
```

**文件**：`tests/test_offer_flow_endpoints.py`（新建）

```python
"""U3 的 HTTP 面：发起 Offer / 逐级审批 / 退回后修改（tasks 3.1–3.3）。"""
from __future__ import annotations

import json

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn, *, chain=True):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','interview')"
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()
    if chain:
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[
                {"level": 1, "approver_account_ids": ["mgr"]},
                {"level": 2, "approver_account_ids": ["gm"]},
            ],
            updated_by="hr1",
        )


def _login(client, conn, username):
    row = conn.execute(
        "SELECT id FROM hr_account WHERE username = ?", (username,)
    ).fetchone()
    token = create_session(conn, hr_account_id=row[0])
    client.cookies.set("hr_session", token)


@pytest.fixture
def env(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    return client, conn


_OFFER_BODY = {
    "department": "研发部",
    "start_date": "2026-11-01",
    "report_to": "李四",
    "note": "面试表现符合岗位要求",
    "request_id": "req-1",
}


def _create(client, conn, *, as_username="hr1", body=None, request_id=None):
    _login(client, conn, as_username)
    payload = dict(body or _OFFER_BODY)
    if request_id is not None:
        payload["request_id"] = request_id
    return client.post("/api/applications/app1/offer", json=payload)


# ── 发起 Offer（tasks 3.1） ─────────────────────────────────────


def test_create_requires_login(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    assert client.post("/api/applications/app1/offer", json=_OFFER_BODY).status_code == 401


def test_create_offer_success(env):
    client, conn = env
    resp = _create(client, conn)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["status_label"] == "待审批"
    assert body["approval_round"] == 1
    assert body["current_level"] == 1
    assert body["total_levels"] == 2
    assert body["approvals"] == []
    assert body["job_title"] == "嵌入式工程师"
    assert body["candidate_name"] == "张三"
    assert conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0] == "offer"
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id = 'app1'"
    ).fetchone()[0] == 1
    # tasks 3.5：发起后本轮第 1 级的待办进内部通道（outbox）。
    rows = conn.execute(
        "SELECT thread_id, message_type FROM outbox"
    ).fetchall()
    assert rows == [(body["id"], "offer_approval_todo")]


def test_create_replay_same_request_id_returns_same_offer(env):
    client, conn = env
    first = _create(client, conn).json()
    second = _create(client, conn).json()
    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_create_conflicts_when_offer_exists_with_new_request_id(env):
    client, conn = env
    _create(client, conn)
    resp = _create(client, conn, request_id="req-2")
    assert resp.status_code == 409
    assert "已有 Offer" in resp.json()["detail"]


def test_create_rejected_for_rejected_application(env):
    client, conn = env
    conn.execute("UPDATE application SET status = 'rejected' WHERE id = 'app1'")
    conn.commit()
    resp = _create(client, conn)
    assert resp.status_code == 409
    assert "已终止" in resp.json()["detail"]


def test_create_unknown_application_returns_404(env):
    client, conn = env
    _login(client, conn, "hr1")
    resp = client.post("/api/applications/no-such-app/offer", json=_OFFER_BODY)
    assert resp.status_code == 404


# ── 逐级审批（tasks 3.2） ───────────────────────────────────────


def test_approval_requires_login(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    client.cookies.clear()
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "approved"})
    assert resp.status_code == 401


def test_non_approver_gets_403(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "hr1")
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "approved"})
    assert resp.status_code == 403
    assert "不是第 1 级审批人" in resp.json()["detail"]


def test_return_without_comment_is_422(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    resp = client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": "returned", "comment": "   "},
    )
    assert resp.status_code == 422
    assert "退回必须填意见" in resp.json()["detail"]


def test_bad_decision_is_422(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "maybe"})
    assert resp.status_code == 422


def test_unconfigured_chain_is_409(make_test_client):
    client, conn = make_test_client()
    _seed(conn, chain=False)
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "approved"})
    assert resp.status_code == 409
    assert "尚未配置审批人" in resp.json()["detail"]


def test_two_level_approval_via_http(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    first = client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved", "level": 1}
    )
    assert first.status_code == 200
    assert first.json()["status"] == "pending_approval"  # 还没到最终级
    _login(client, conn, "gm")
    second = client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved", "level": 2}
    )
    assert second.status_code == 200
    assert second.json()["status"] == "approved"
    assert [a["level"] for a in second.json()["approvals"]] == [1, 2]
    assert [a["approver"] for a in second.json()["approvals"]] == ["mgr", "gm"]
    # 二级通过后不再有待办（已是终态）。
    assert conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE message_type = 'offer_approval_todo'"
    ).fetchone()[0] == 2


def test_replay_approval_is_idempotent_over_http(env):
    """带 level 的重放命中同一条幂等键 ⇒ 200 且留痕不增（spec Scenario「审批节点重跑」）。"""
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    body = {"decision": "approved", "level": 1}
    assert client.post(f"/api/offers/{offer_id}/approval", json=body).status_code == 200
    assert client.post(f"/api/offers/{offer_id}/approval", json=body).status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 1


def test_replay_without_level_after_advance_is_rejected(env):
    """不带 level 的重放（当前级已推进）⇒ 403，⛔ 不是"顺手把第 2 级也替人签了字"。

    这条锁住"级号来自请求或状态、⛔ 不凭猜测顺延"：若实现改成"谁重放就给谁当前级"，
    第一次重放就会变成业务经理替总经理签第 2 级——一条没有任何症状的越权。
    """
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved", "level": 1}
    ).status_code == 200
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).status_code == 403
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 1


# ── 退回后修改（tasks 3.3） ─────────────────────────────────────


def test_return_then_revise_then_two_levels(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    returned = client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": "returned", "comment": "入职日期待与候选人确认"},
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "needs_revision"
    _login(client, conn, "hr1")
    revised = client.patch(
        f"/api/offers/{offer_id}",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": "入职日期已确认",
            "request_id": "req-revise-1",
        },
    )
    assert revised.status_code == 200
    assert revised.json()["approval_round"] == 2
    assert revised.json()["status"] == "pending_approval"
    assert revised.json()["current_level"] == 1  # 链从第一级重走
    _login(client, conn, "mgr")
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).json()["status"] == "pending_approval"
    _login(client, conn, "gm")
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).json()["status"] == "approved"
    # 留痕守恒：3 条（第 1 轮退回 1 条 ＋ 第 2 轮通过 2 条）。
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 3


def test_revise_replay_same_request_id_is_idempotent(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": "returned", "comment": "改"},
    )
    _login(client, conn, "hr1")
    body = {
        "department": "研发部",
        "start_date": "2026-12-01",
        "report_to": "李四",
        "note": None,
        "request_id": "req-revise-1",
    }
    assert client.patch(f"/api/offers/{offer_id}", json=body).json()["approval_round"] == 2
    assert client.patch(f"/api/offers/{offer_id}", json=body).json()["approval_round"] == 2
    assert conn.execute(
        "SELECT approval_round FROM offer WHERE id = ?", (offer_id,)
    ).fetchone()[0] == 2


def test_revise_requires_needs_revision_status(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "hr1")
    resp = client.patch(
        f"/api/offers/{offer_id}",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": None,
            "request_id": "req-revise-1",
        },
    )
    assert resp.status_code == 409
    assert "待修改" in resp.json()["detail"]


def test_revise_unknown_offer_returns_404(env):
    client, conn = env
    _login(client, conn, "hr1")
    resp = client.patch(
        "/api/offers/no-such-offer",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": None,
            "request_id": "req-revise-1",
        },
    )
    assert resp.status_code == 404


# ── 反证：载荷里没有 AI 录用结论（spec「录用决定不由 AI 做」） ──


def test_offer_payload_has_no_ai_decision_text(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    payload = client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).json()
    flat = json.dumps(payload, ensure_ascii=False)
    for word in ("建议录用", "录用建议", "评分", "排名", "总分", "score", "ranking",
                 "recommend"):
        assert word not in flat
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_flow_endpoints.py -q
```

预期：全部通过（0 failed，19 passed）。

---

### Task 6: 审批页——待审列表、Offer 详情与两个页面（tasks 3.4）

**文件**：`app/web/server.py`（修改，三处）

**6a. import 追加**（Task 5 的 `from app.storage.offer import (...)` 括号内加一行）：

```python
    list_pending_offers_for,
```

**6b. 路由**（追加在 Task 5 的路由之后）：

```python
    # ── offer-generation U3：审批页数据与页面（tasks 3.4）──
    # ⚠️ 声明顺序：/api/offers/pending 必须在 /api/offers/{offer_id} **之前**
    # （FastAPI 按声明顺序匹配路径，反过来 "pending" 会被当成 offer_id 吃掉，
    # 症状是待审列表接口稳定 404）。
    @router.get("/api/offers/pending")
    def list_my_pending_offers(request: Request):
        username = _require_login(request)
        return {"offers": list_pending_offers_for(conn, username)}

    @router.get("/api/offers/{offer_id}")
    def get_offer_detail(offer_id: str, request: Request):
        _require_login(request)
        try:
            return _offer_payload(offer_id)
        except OfferNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/offers/pending")
    def offers_pending_page():
        return _render_static_page("offers_pending.html", root_path)

    @router.get("/offers/{offer_id}/approve")
    def offer_approve_page(offer_id: str):
        return _render_static_page("offer_approve.html", root_path)
```

**6c. 静态页 `app/web/static/offers_pending.html`（新建）**：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261010">
  <title>Offer 审批 · 卓品智能招聘助手</title>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">业务经理 · Offer 审批</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">待我审批的 Offer</h1>
    <p class="notice"><strong>本页只展示 Offer 记录里的非敏感字段与投递基本信息；Offer 记录不含薪资等报酬信息，薪资由 HR 在文书里手工填写。</strong></p>
    <div class="card">
      <table class="data-table">
        <thead>
          <tr><th>岗位</th><th>候选人</th><th>部门</th><th>拟入职日期</th>
              <th>汇报对象</th><th>审批进度</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody id="offer-body"></tbody>
      </table>
      <p id="empty-hint" class="empty-hint" style="display:none">当前没有待你审批的 Offer。</p>
    </div>
    <p id="error" style="color:red"></p>
  </main>

  <script>
    async function loadPending() {
      const resp = await fetch("api/offers/pending");
      if (resp.status === 401) {
        window.location.href = "login";
        return;
      }
      if (!resp.ok) {
        document.getElementById("error").textContent = "加载失败";
        return;
      }
      const body = await resp.json();
      const tbody = document.getElementById("offer-body");
      tbody.innerHTML = "";
      if (body.offers.length === 0) {
        document.getElementById("empty-hint").style.display = "";
        return;
      }
      for (const offer of body.offers) {
        const tr = document.createElement("tr");
        const cells = [
          offer.job_title,
          offer.candidate_name,
          offer.department,
          offer.start_date,
          offer.report_to,
          `第 ${offer.current_level}/${offer.total_levels} 级`,
          offer.status_label,
        ];
        for (const text of cells) {
          const td = document.createElement("td");
          td.textContent = text;
          tr.appendChild(td);
        }
        const actionTd = document.createElement("td");
        const link = document.createElement("a");
        link.href = `offers/${offer.id}/approve`;
        link.textContent = "查看并审批";
        link.className = "link";
        actionTd.appendChild(link);
        tr.appendChild(actionTd);
        tbody.appendChild(tr);
      }
    }

    loadPending();
  </script>
</body>
</html>
```

**6d. 静态页 `app/web/static/offer_approve.html`（新建）**：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261010">
  <title>Offer 审批详情 · 卓品智能招聘助手</title>
  <style>
    .kv { margin: 4px 0; }
    .kv .k { color: var(--color-text-muted); display: inline-block; min-width: 96px; }
    textarea { width: 100%; min-height: 72px; }
    .actions button { margin-right: 8px; }
    #status-bar { padding: 12px 16px; margin-bottom: 16px; border-radius: 8px; }
    .status-pending { background: #fff3cd; }
    .status-approved { background: #d3f9d8; }
    .status-revision { background: #ffe3e3; }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">业务经理 · Offer 审批</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">Offer 审批</h1>
    <div id="status-bar">加载中…</div>
    <div class="card" id="offer-card"></div>
    <div class="card">
      <h2>审批留痕</h2>
      <table class="data-table">
        <thead><tr><th>轮次</th><th>级</th><th>审批人</th><th>结论</th><th>意见</th><th>时刻</th></tr></thead>
        <tbody id="history-body"></tbody>
      </table>
    </div>
    <div class="card">
      <h2>审批操作</h2>
      <p><label for="comment">意见（退回必填）</label></p>
      <textarea id="comment"></textarea>
      <div class="actions toolbar">
        <button id="approve-btn" class="btn btn-primary">通过</button>
        <button id="return-btn" class="btn btn-danger">退回</button>
      </div>
    </div>
    <p id="error" style="color:red"></p>
  </main>

  <script>
    const DECISION_LABELS = { approved: "通过", returned: "退回" };
    const STATUS_CLASS = {
      pending_approval: "status-pending",
      needs_revision: "status-revision",
      approved: "status-approved",
    };

    function offerIdFromPath() {
      const m = window.location.pathname.match(/\/offers\/([^/]+)\/approve\/?$/);
      return m ? m[1] : null;
    }

    function kv(label, value) {
      const p = document.createElement("p");
      p.className = "kv";
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = label;
      const v = document.createElement("span");
      v.textContent = value === null || value === undefined || value === "" ? "-" : value;
      p.appendChild(k);
      p.appendChild(v);
      return p;
    }

    let currentOfferId = null;
    let currentLevel = null;

    async function loadOffer() {
      currentOfferId = offerIdFromPath();
      if (!currentOfferId) {
        document.getElementById("error").textContent = "无法识别 Offer 标识";
        return;
      }
      const resp = await fetch(`api/offers/${currentOfferId}`);
      if (resp.status === 401) {
        window.location.href = "login";
        return;
      }
      if (!resp.ok) {
        document.getElementById("error").textContent = "加载失败";
        return;
      }
      const offer = await resp.json();
      currentLevel = offer.current_level;
      const bar = document.getElementById("status-bar");
      bar.className = STATUS_CLASS[offer.status] || "";
      bar.textContent = `状态：${offer.status_label}（第 ${offer.approval_round} 轮，当前第 ${offer.current_level}/${offer.total_levels} 级）`;

      const card = document.getElementById("offer-card");
      card.innerHTML = "";
      card.appendChild(kv("岗位", offer.job_title));
      card.appendChild(kv("候选人", offer.candidate_name));
      card.appendChild(kv("部门", offer.department));
      card.appendChild(kv("拟入职日期", offer.start_date));
      card.appendChild(kv("汇报对象", offer.report_to));
      card.appendChild(kv("备注", offer.note));
      card.appendChild(kv("发起人", offer.created_by));

      const tbody = document.getElementById("history-body");
      tbody.innerHTML = "";
      for (const item of offer.approvals) {
        const tr = document.createElement("tr");
        for (const text of [
          `第 ${item.round} 轮`,
          `第 ${item.level} 级`,
          item.approver,
          DECISION_LABELS[item.decision] || item.decision,
          item.comment || "",
          item.at,
        ]) {
          const td = document.createElement("td");
          td.textContent = text;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }

      const actionable = offer.status === "pending_approval";
      document.getElementById("approve-btn").disabled = !actionable;
      document.getElementById("return-btn").disabled = !actionable;
    }

    async function decide(decision) {
      document.getElementById("error").textContent = "";
      const comment = document.getElementById("comment").value;
      const resp = await fetch(`api/offers/${currentOfferId}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // level 必须回传：它与 round/approver/decision 一起构成审批的幂等键，
        // 重试同一个 level 才会命中同一条键（否则重放会撞 403）。
        body: JSON.stringify({
          decision: decision,
          comment: comment || null,
          level: currentLevel,
        }),
      });
      if (resp.status === 401) {
        window.location.href = "login";
        return;
      }
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        document.getElementById("error").textContent = body.detail || "操作失败";
        return;
      }
      document.getElementById("comment").value = "";
      await loadOffer();
    }

    document.getElementById("approve-btn").addEventListener("click", () => decide("approved"));
    document.getElementById("return-btn").addEventListener("click", () => decide("returned"));
    loadOffer();
  </script>
</body>
</html>
```

**文件**：`tests/test_offer_pages.py`（新建）

```python
"""审批页与页面数据的渲染、登录跳转与"页面不含 AI 录用结论"反证（tasks 3.4）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.offer_approval_chain import put_approval_chain

STATIC = Path("app/web/static")

# spec Scenario「页面不含 AI 录用建议」的反证词表。⛔ 页面与详情载荷里
# 一个都不许出现——包括"我们没用到评分"这类自我说明（说明本身也含该词）。
FORBIDDEN_AI_DECISION_WORDS = (
    "建议录用", "录用建议", "评分", "排名", "总分", "score", "ranking", "recommend",
)


def _seed(conn):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','interview')"
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ],
        updated_by="hr1",
    )


def _login(client, conn, username):
    row = conn.execute(
        "SELECT id FROM hr_account WHERE username = ?", (username,)
    ).fetchone()
    token = create_session(conn, hr_account_id=row[0])
    client.cookies.set("hr_session", token)


def _create_offer(client, conn):
    _login(client, conn, "hr1")
    resp = client.post(
        "/api/applications/app1/offer",
        json={
            "department": "研发部",
            "start_date": "2026-11-01",
            "report_to": "李四",
            "note": "面试表现符合岗位要求",
            "request_id": "req-1",
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture
def env(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    return client, conn


# ── 页面本身（静态路由 + 窄屏 viewport + 401 跳登录） ────────────


def test_pages_render(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    pending = client.get("/offers/pending")
    assert pending.status_code == 200
    assert "Offer" in pending.text
    assert "<!--BASE_HREF-->" not in pending.text  # base href 已被替换
    detail = client.get(f"/offers/{offer_id}/approve")
    assert detail.status_code == 200
    assert "审批留痕" in detail.text


def test_pages_redirect_to_login_on_401():
    for name in ("offers_pending.html", "offer_approve.html"):
        html = (STATIC / name).read_text(encoding="utf-8")
        assert 'window.location.href = "login"' in html, (
            f"{name} 缺 401 → 跳登录的分支：页面路由不在 AuthMiddleware 的"
            "PROTECTED_PATH_PREFIXES 里，未登录时页面会照常返回 HTML，"
            "必须由页面 JS 自己把用户送回登录页（与 resume_list.html 同款）"
        )
        assert "width=device-width" in html


def test_pages_have_no_ai_decision_text():
    for name in ("offers_pending.html", "offer_approve.html"):
        flat = (STATIC / name).read_text(encoding="utf-8").lower()
        for word in FORBIDDEN_AI_DECISION_WORDS:
            assert word not in flat, f"{name} 里出现反证词：{word}"


# ── 页面数据（API） ─────────────────────────────────────────────


def test_pending_api_requires_login(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    assert client.get("/api/offers/pending").status_code == 401


def test_pending_api_lists_only_current_approver(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    _login(client, conn, "mgr")
    mine = client.get("/api/offers/pending").json()["offers"]
    assert [item["id"] for item in mine] == [offer_id]
    assert mine[0]["current_level"] == 1
    _login(client, conn, "gm")
    assert client.get("/api/offers/pending").json()["offers"] == []
    _login(client, conn, "hr1")
    assert client.get("/api/offers/pending").json()["offers"] == []


def test_detail_api_requires_login_and_returns_fields(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    client.cookies.clear()
    assert client.get(f"/api/offers/{offer_id}").status_code == 401
    _login(client, conn, "mgr")
    body = client.get(f"/api/offers/{offer_id}").json()
    assert body["department"] == "研发部"
    assert body["start_date"] == "2026-11-01"
    assert body["report_to"] == "李四"
    assert body["status_label"] == "待审批"
    assert body["approvals"] == []


def test_detail_api_unknown_offer_returns_404(env):
    client, conn = env
    _login(client, conn, "mgr")
    assert client.get("/api/offers/no-such-offer").status_code == 404


def test_detail_payload_has_no_ai_decision_text(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    _login(client, conn, "mgr")
    flat = json.dumps(
        client.get(f"/api/offers/{offer_id}").json(), ensure_ascii=False
    ).lower()
    for word in FORBIDDEN_AI_DECISION_WORDS:
        assert word not in flat, f"详情载荷里出现反证词：{word}"
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_pages.py -q
```

预期：全部通过（0 failed，8 passed）。

---

### Task 7: 导出门槛（tasks 3.6）

> ⚠️ **依赖现状**（本计划「零」节表格）：U2 Task 5 的
> `GET /api/letters/{letter_id}/export.docx` **当前磁盘上不存在**。因此门槛分两处落：
> **7a 卡点**（落在 U2 已落地的 `record_letter_access` 上，与 U2 进度无关，判据自足）、
> **7b 路由显式提示**（probe 后接线；未落地则登记留步）。⛔ 两处都指向同一个
> `assert_offer_exportable`，不存在第二份判定。

**7a. 文件**：`app/graph/letter_nodes.py`（修改，两处）

**7a-1. import 追加**（在 `from app.storage.letter_template import get_letter_template` 之后）：

```python
from app.storage.offer import OfferNotExportableError, assert_offer_exportable
```

**7a-2. `record_letter_access` 改为**（原 docstring 全文保留，末尾追加下面这段说明）：

```python
def record_letter_access(
    conn: sqlite3.Connection,
    *,
    accessor: str,
    application_id: str,
    letter_id: str,
    access_type: str,
) -> None:
    """文书查看/导出留痕（candidate-letter-engine spec「导出 docx」「文书草稿的
    查看留痕」）。写入失败不吞任何异常——调用方（路由）不 catch 就是正确行为
    （FastAPI 未捕获异常 ⇒ 500，读取自然失败，不返回正文）。

    2026-10-10（U3 tasks 3.6）：**export 在此加导出门槛**——Offer 类文书在
    `offer.status ∉ {approved, exported, accepted, declined, negotiating}` 时抛
    `OfferNotExportableError`，于是留痕与 docx 文件都不会产生（spec「审批通过才
    可导出」）。⛔ 门槛放在这里而不是只放在导出路由里：本函数是 U2 既定契约
    「导出先留痕再产文件」上**每个调用方都必经**的一步，而导出路由属 U2（磁盘上
    尚未落地）——放这里判据不依赖 U2 的进度。路由侧再加一层显式 409 提示（7b）。
    ⛔ `view` 不受影响（查看留痕与本门槛无关）；⛔ 拒信（kind='rejection'）不受影响
    （它没有 offer 记录，spec 的门槛只针对 Offer 记录）。
    """
    if access_type == "export":
        row = conn.execute(
            "SELECT application_id, kind FROM candidate_letter WHERE id = ?",
            (letter_id,),
        ).fetchone()
        if row is None:
            raise LetterNotFoundError(f"文书不存在: {letter_id!r}")
        letter_application_id, letter_kind = row
        if letter_kind == "offer":
            assert_offer_exportable(conn, letter_application_id)
    conn.execute(
        "INSERT INTO letter_access_log (id, accessor, application_id, letter_id, access_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), accessor, application_id, letter_id, access_type),
    )
    conn.commit()
```

**7b. 文件**：`app/web/server.py`（修改，**probe 通过才做**）

**probe 命令**：

```bash
grep -n 'export.docx' app/web/server.py
```

- **有输出** ⇒ U2 Task 5 已落地，在该 handler 里把留痕调用包一层（并给
  `from app.storage.offer import (...)` 补 `OfferNotExportableError`）：

```python
        try:
            record_letter_access(
                conn,
                accessor=username,
                application_id=letter["application_id"],
                letter_id=letter_id,
                access_type="export",
            )
        except OfferNotExportableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
```

- **无输出** ⇒ U2 Task 5 未落地：**登记留步**——在本计划「端到端提取验证」段末尾追加一行
  `⏸ 留步（2026-10-10）：U2 Task 5 的 GET /api/letters/{id}/export.docx 未落地，
  导出门槛的 409 显式提示待 U2 落地后接线；7a 的卡点判定已独立生效并有测试覆盖`，
  然后在最终答复里照抄一句。⛔ 不假装接线完成，⛔ 不因此判整件失败（导出门槛的
  判据由 7a 的测试独立满足）。

**文件**：`tests/test_offer_export_gate.py`（新建）

```python
"""导出门槛：offer.status 白名单在导出卡点上生效（U3 tasks 3.6）。"""
from __future__ import annotations

import uuid

import pytest

from app.graph.letter_nodes import record_letter_access
from app.storage.db import get_connection, init_schema
from app.storage.offer import OfferNotExportableError


def _seed(conn, *, status="pending_approval", kind="offer"):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','offer')"
    )
    if kind == "offer":
        conn.execute(
            "INSERT INTO offer (id, application_id, job_id, department, start_date, "
            "report_to, status, approval_round, created_by) "
            "VALUES ('o1','app1','j1','研发部','2026-11-01','李四',?,1,'hr1')",
            (status,),
        )
    conn.execute(
        "INSERT INTO candidate_letter (id, application_id, kind, version, "
        "template_version, body, ai_generated, created_by) "
        "VALUES ('l1','app1',?,1,1,'【AI 生成】正文',1,'hr1')",
        (kind,),
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-export-gate.db"))
    init_schema(c)
    return c


def _export(conn, letter_id="l1"):
    record_letter_access(
        conn,
        accessor="hr1",
        application_id="app1",
        letter_id=letter_id,
        access_type="export",
    )


def _log_count(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id = 'l1'"
    ).fetchone()[0]


@pytest.mark.parametrize("status", ["pending_approval", "needs_revision"])
def test_export_blocked_while_not_approved(conn, status):
    _seed(conn, status=status)
    with pytest.raises(OfferNotExportableError, match="审批通过后才可导出"):
        _export(conn)
    # 门槛先于留痕：⛔ 拦截时一行留痕都不写（没导出就谈不上"访问过文书"）。
    assert _log_count(conn) == 0


@pytest.mark.parametrize(
    "status", ["approved", "exported", "accepted", "declined", "negotiating"]
)
def test_export_allowed_when_approved(conn, status):
    _seed(conn, status=status)
    _export(conn)
    assert _log_count(conn) == 1
    assert conn.execute(
        "SELECT access_type FROM letter_access_log WHERE letter_id = 'l1'"
    ).fetchone()[0] == "export"


def test_view_is_not_gated(conn):
    _seed(conn, status="pending_approval")
    record_letter_access(
        conn,
        accessor="hr1",
        application_id="app1",
        letter_id="l1",
        access_type="view",
    )
    assert _log_count(conn) == 1


def test_rejection_letter_export_is_not_gated(conn):
    """拒信没有 offer 记录，spec 的门槛只针对 Offer 记录 ⇒ 不受影响。"""
    _seed(conn, kind="rejection")
    _export(conn)
    assert _log_count(conn) == 1


def test_export_unknown_letter_raises_not_found(conn):
    _seed(conn, status="approved")
    with pytest.raises(Exception, match="文书不存在"):
        _export(conn, letter_id=f"missing-{uuid.uuid4()}")
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_export_gate.py -q
```

预期：全部通过（0 failed，10 passed）。加跑 U2 已落地的文书测试确认没回归：

```bash
python -m pytest tests/test_letter_nodes.py tests/test_letter_md_to_docx.py -q
```

预期：全部通过（0 failed）。

---

### Task 8: U3 e2e（tasks 3.7）

**文件**：`tests/test_offer_unit3_e2e.py`（新建）

> ⚠️ **这段为什么走节点而不是 HTTP**：tasks 3.7 的链路是
> 「发起 → 一级退回 → 修改 → 两级通过 → 生成文书 → 导出」，其中**前四段走 U3 自己的
> HTTP 端点**（本文件用 `make_test_client` 真跑），**后两段走节点/卡点**
> （`effect_persist_letter` + `record_letter_access`）——文书生成/导出的 HTTP 端点属
> U2 Task 5，本计划写作时磁盘上还没有（见「零」节表格）。tasks 3.7 要验的是 U3 的
> 链路是否把 Offer 推到"可导出"，节点级足以判定；U2 落地后由步 B 补 HTTP 版。

```python
"""U3 e2e（tasks 3.7）：发起 → 一级退回 → 修改 → 两级通过 → 生成文书 → 导出；
审批留痕条数守恒。

⚠️ 文书生成/导出走节点与卡点（`effect_persist_letter` + `record_letter_access`），
不走 `/api/letters*`：那是 U2 Task 5 的端点，本计划写作时尚未落地（见「零」节表格）。
U2 落地后应补一条 HTTP 版（把下面第 5/6 段换成 POST /api/applications/{id}/letters
与 GET /api/letters/{id}/export.docx），本文件保持不动。
"""
from __future__ import annotations

import pytest

from app.agents.letter_drafter import LetterDraft
from app.graph.letter_nodes import effect_persist_letter, record_letter_access
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.offer import OfferNotExportableError, assert_offer_exportable
from app.storage.offer_approval_chain import put_approval_chain

BODY = "【AI 生成】张三：拟录用您担任嵌入式工程师。"


def _seed(conn):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','interview')"
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ],
        updated_by="hr1",
    )


def _login(client, conn, username):
    row = conn.execute(
        "SELECT id FROM hr_account WHERE username = ?", (username,)
    ).fetchone()
    token = create_session(conn, hr_account_id=row[0])
    client.cookies.set("hr_session", token)


def _approve(client, conn, offer_id, *, as_username, level, decision="approved",
             comment=None):
    _login(client, conn, as_username)
    return client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": decision, "level": level, "comment": comment},
    )


@pytest.fixture
def env(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    return client, conn


def test_unit3_end_to_end(env):
    client, conn = env

    # ① 发起 Offer
    _login(client, conn, "hr1")
    created = client.post(
        "/api/applications/app1/offer",
        json={
            "department": "研发部",
            "start_date": "2026-11-01",
            "report_to": "李四",
            "note": "面试表现符合岗位要求",
            "request_id": "req-1",
        },
    )
    assert created.status_code == 201
    offer_id = created.json()["id"]
    assert created.json()["status"] == "pending_approval"
    # 待审批 ⇒ 不可导出（spec Scenario「待审批时导出被拒」）
    with pytest.raises(OfferNotExportableError):
        assert_offer_exportable(conn, "app1")

    # ② 一级退回
    returned = _approve(
        client, conn, offer_id, as_username="mgr", level=1,
        decision="returned", comment="入职日期待与候选人确认",
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "needs_revision"

    # ③ 退回后修改 ⇒ round+1、链从第一级重走
    _login(client, conn, "hr1")
    revised = client.patch(
        f"/api/offers/{offer_id}",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": "入职日期已与候选人确认",
            "request_id": "req-revise-1",
        },
    )
    assert revised.status_code == 200
    assert revised.json()["approval_round"] == 2
    assert revised.json()["current_level"] == 1

    # ④ 两级通过
    assert _approve(
        client, conn, offer_id, as_username="mgr", level=1
    ).json()["status"] == "pending_approval"
    approved = _approve(client, conn, offer_id, as_username="gm", level=2)
    assert approved.json()["status"] == "approved"
    assert_offer_exportable(conn, "app1")  # 已审批 ⇒ 门槛放行

    # ⑤ 生成文书（U2 的 effect_persist_letter：Offer 类前置 = offer.status='approved'）
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-1','deepseek-chat','letter-offer-v1',"
        "0.0,'h','{}')"
    )
    conn.commit()
    letter_id = effect_persist_letter(
        conn,
        thread_id="app1",
        business_key="offer:run-1",
        application_id="app1",
        kind="offer",
        version=1,
        template_version=1,
        draft=LetterDraft(
            kind="offer",
            body=BODY,
            run_id="run-1",
            response_model="deepseek-chat",
            prompt_version="letter-offer-v1",
        ),
        created_by="hr1",
    )
    assert letter_id

    # ⑥ 导出（tasks 3.6 的门槛在导出卡点上；已审批 ⇒ 放行并留痕）
    record_letter_access(
        conn,
        accessor="hr1",
        application_id="app1",
        letter_id=letter_id,
        access_type="export",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id = ? "
        "AND access_type = 'export'",
        (letter_id,),
    ).fetchone()[0] == 1

    # ⑦ 留痕守恒：第 1 轮退回 1 条 ＋ 第 2 轮通过 2 条 = 3 条
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 3
    # 重跑最后一级（同 round/level/approver/decision）⇒ 幂等短路，留痕不增
    replay = _approve(client, conn, offer_id, as_username="gm", level=2)
    assert replay.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 3
    # 恒等式（铁律 1）：审批 effect_log 条数 == offer_approval 行数
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_record_approval' "
        "AND thread_id = ?",
        (offer_id,),
    ).fetchone()[0] == 3
    # 投递阶段留在 offer（终态流转是 U5 的职责，⛔ 本单元不碰 application.status）
    assert conn.execute(
        "SELECT current_stage_id, status FROM application WHERE id = 'app1'"
    ).fetchone() == ("offer", "active")
```

**命令与预期输出**：

```bash
python -m pytest tests/test_offer_unit3_e2e.py -q
```

预期：全部通过（0 failed，1 passed）。

**步 B（probe 后接线，⛔ 未落地不许假装）**：

```bash
grep -q 'export.docx' app/web/server.py && echo "U2 Task 5 已落地" || echo "U2 Task 5 未落地 ⇒ 登记留步"
```

- **已落地** ⇒ 追加 `tests/test_offer_export_gate_endpoint.py`（HTTP 面判据）：

```python
"""导出门槛的 HTTP 面：未审批 Offer 导出被拒（U3 tasks 3.6；需 U2 Task 5 已落地）。"""
from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _seed(conn, *, offer_status):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','offer')"
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, approval_round, created_by) "
        "VALUES ('o1','app1','j1','研发部','2026-11-01','李四',?,1,'hr1')",
        (offer_status,),
    )
    conn.execute(
        "INSERT INTO candidate_letter (id, application_id, kind, version, "
        "template_version, body, ai_generated, created_by) "
        "VALUES ('l1','app1','offer',1,1,'【AI 生成】正文',1,'hr1')"
    )
    account_id = upsert_account(conn, username="hr1", password="s3cret!")
    conn.commit()
    return create_session(conn, hr_account_id=account_id)


def test_export_endpoint_rejects_pending_approval(make_test_client):
    client, conn = make_test_client()
    token = _seed(conn, offer_status="pending_approval")
    client.cookies.set("hr_session", token)
    resp = client.get("/api/letters/l1/export.docx")
    assert resp.status_code == 409
    assert "审批通过后才可导出" in resp.json()["detail"]
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id = 'l1'"
    ).fetchone()[0] == 0


def test_export_endpoint_allows_approved(make_test_client):
    client, conn = make_test_client()
    token = _seed(conn, offer_status="approved")
    client.cookies.set("hr_session", token)
    resp = client.get("/api/letters/l1/export.docx")
    assert resp.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id = 'l1' "
        "AND access_type = 'export'"
    ).fetchone()[0] == 1
```

  命令与预期：`python -m pytest tests/test_offer_export_gate_endpoint.py -q` ⇒ 2 passed。

- **未落地** ⇒ 在最终答复里登记
  `⏸ 留步：U2 Task 5（GET /api/letters/{id}/export.docx）未落地，导出门槛的 HTTP 面测试与 409 显式提示待 U2 落地后接线；Task 7a 的卡点判定已独立生效并有测试覆盖`，
  ⛔ 不创建该测试文件（它此时必然失败，会把"未就绪"伪装成"实现有问题"）。

---

### Task 9: 全量回归

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1010c-offer-unit3-plan
python -m pytest \
  tests/test_offer_storage.py \
  tests/test_offer_nodes_create.py \
  tests/test_offer_nodes_approval.py \
  tests/test_offer_nodes_todo.py \
  tests/test_offer_flow_endpoints.py \
  tests/test_offer_pages.py \
  tests/test_offer_export_gate.py \
  tests/test_offer_unit3_e2e.py \
  tests/test_offer_approval_chain.py \
  tests/test_offer_approval_chain_endpoint.py \
  tests/test_db_offer_schema.py \
  tests/test_db_m3_schema.py \
  tests/test_letter_nodes.py \
  tests/test_letter_template.py \
  tests/test_letter_drafter.py \
  tests/test_outbound_delivery.py \
  tests/test_outbound_end_to_end.py -q
```

预期：全部通过（0 failed）。三组必须原样通过、⛔ 一行都不许改的既有测试：

- `tests/test_offer_approval_chain*.py` + `tests/test_db_offer_schema.py`（U1 的产物）
- `tests/test_letter_*.py`（U2 已落地的产物——Task 7a 改了 `record_letter_access`，
  这里就是它的回归闸）
- `tests/test_outbound_*.py`（候选人外发门禁：U3 ⛔ 不碰 `app/outbound/**`）

再跑一次**全量**确认没有跨模块回归：

```bash
python -m pytest -q
```

预期：0 failed（U1 的一段实测基线是 4228 passed，本单元只加测试、不改既有断言）。

---

## Requirement → Task 覆盖表

| spec 文件 · Requirement / Scenario | 本单元落点 | Task |
|---|---|---|
| offer-record-and-approval · Offer 记录的字段边界 · Scenario「发起 Offer」 | `assert_offer_creatable` + `effect_create_offer`（三写同事务）＋ `POST /api/applications/{id}/offer` | 1, 2, 5 |
| offer-record-and-approval · Offer 记录的字段边界 · Scenario「对已淘汰投递发起被拒」 | 前置判据（`status != 'active'` / 有 `rejection_record`）＋ 409 | 1, 2, 5 |
| offer-record-and-approval · 内部审批链 | `offer_approval_chain` 读判定 + `effect_record_approval`（逐级、留痕、退回）＋ 审批端点 | 1, 3, 5 |
| offer-record-and-approval · 内部审批链 · Scenario「两级通过」 | `total_approval_levels` + 最后一级 ⇒ `approved` | 1, 3, 5, 8 |
| offer-record-and-approval · 内部审批链 · Scenario「一级退回」 | `returned` ⇒ `needs_revision`；后续级过不了逐级闸 | 3, 5, 8 |
| offer-record-and-approval · 内部审批链 · Scenario「非审批人操作被拒」 | `NotAnApproverError` ⇒ 403 | 1, 3, 5 |
| offer-record-and-approval · 内部审批链（退回后重走） | `effect_revise_offer`（`round+1`）+ `current_approval_level` 回第 1 级 | 1, 3, 5, 8 |
| offer-record-and-approval · 审批通过才可导出 · Scenario「待审批时导出被拒」 | `assert_offer_exportable` + `record_letter_access` 卡点（+ 端点 409） | 1, 7, 8 |
| offer-record-and-approval · 审批动作幂等 · Scenario「审批节点重跑」 | 幂等键 `{offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}` ＋ `(offer_id, round, level)` 唯一索引 | 3, 5, 8 |
| offer-record-and-approval · 录用决定不由 AI 做 · Scenario「页面不含 AI 录用建议」 | 两个页面 + 详情载荷的反证测试；`offer_nodes` 零 AI 调用的源码扫描 | 4, 6 |
| outbound-approval-gate · Scenario「内部通知不受影响」 | 待办走内部 outbox（`offer_approval_todo`），⛔ 不进门禁 | 4, 5 |
| outbound-approval-gate · Scenario「Offer 函走门禁」（`offer_letter` 登记） | ⛔ 不在本单元：U4 tasks 4.1（属对外通道，不可代项） | — |
| tasks 3.4 审批页（本单元边界内，无独立 Requirement） | `GET /api/offers/pending` + `GET /api/offers/{id}` + 两个静态页 | 1, 6 |
| tasks 3.5 审批待办通知（无独立 Requirement） | `effect_publish_approval_todo`（幂等键含 offer_id/round/level；无通道不阻塞） | 4 |
| tasks 3.7 U3 e2e（无独立 Requirement） | `tests/test_offer_unit3_e2e.py` | 8 |

> `candidate-letter-*` 与 `offer-outcome-and-transition` 两个 spec 的 Requirement
> 分别属 U2 与 U5，本单元只在导出卡点（U2 的 `record_letter_access`）与"发起时流转
> `offer` 阶段"两处与它们相接，⛔ 不改它们的行为契约。

## 端到端提取验证（已做）

按 `.claude/skills/spec-to-plan/SKILL.md` 第 6 节，把本计划里「（新建）」与「追加」
两类代码块**机械抽取**到 `/private/tmp` 的一份仓库副本（`app/` 与 `tests/` 全量拷贝），
再用真实 SQLite 与真实仓库代码跑测试；Task 5/6/7b 的 `app/web/server.py` 片段与 7a 的
`letter_nodes.py` 片段无法机械抽取（它们是对既有文件的修改），在本轮验证里按计划文本
**手工施加**到副本后再跑。命令：

```bash
cd <tmp 副本目录>
python -m pytest tests/test_offer_storage.py tests/test_offer_nodes_create.py \
  tests/test_offer_nodes_approval.py tests/test_offer_nodes_todo.py \
  tests/test_offer_flow_endpoints.py tests/test_offer_pages.py \
  tests/test_offer_export_gate.py tests/test_offer_unit3_e2e.py -q
```

**实测结果（2026-10-11 实跑）**：

- 本单元 8 个测试文件：**85 passed / 0 failed**（修复前分别撞到下面缺陷 2、缺陷 3，
  各一次 1 failed）。
- 既有回归集（`test_letter_nodes/template/drafter`、`test_offer_approval_chain*`、
  `test_db_offer_schema`、`test_db_m3_schema`、`test_outbound_delivery`、
  `test_outbound_end_to_end`）：**187 passed / 0 failed**——Task 7a 对
  `record_letter_access` 的改动没有打破 U2 的既有契约。

**提取验证揪出的真实缺陷（已回写本计划）**：

1. **审批重放拿不到幂等键**（Task 5）：原设计里路由**现算**"本轮当前级"当 `level`。
   "第 1 级通过后客户端重试同一请求"会算出第 2 级 ⇒ 幂等键变了 ⇒ 装饰器不短路 ⇒
   一路走到 `NotAnApproverError`，用户看到 403 而不是幂等成功（更糟的修法——"谁重放
   就给谁当前级"——会让业务经理替总经理签字）。修法：`OfferApprovalRequest.level`
   由页面回传，同一次提交的重试级号不变；新增
   `test_replay_without_level_after_advance_is_rejected` 锁住"级号⛔ 不顺延"，
   页面（Task 6d）跟随带上 `level`。
2. **源码级反证被自己的文档打破**（Task 2 模块 docstring / Task 4 节点 docstring）：
   文档里写了候选人外发入口的函数名，`test_offer_nodes_never_calls_the_candidate_gate`
   当场失守。修法：两处 docstring 改成"`app/outbound/**` 里的候选人外发入口"，
   ⛔ 不写函数名，并在计划里留一句"别再加回来"。
3. **发起端点的前置预检吃掉了幂等重放**（Task 5）：原设计在调 `effect_create_offer`
   之前先跑一遍 `assert_offer_creatable`。第二次携带**同一** `request_id` 的请求此时
   Offer 已存在 ⇒ 预检先抛 `OfferNotCreatableError` ⇒ 客户端拿到 409，永远走不到
   `idempotent_effect` 的短路（"重试能拿到第一次的结果"是幂等键的全部意义）。
   修法：删掉端点侧预检，前置校验只留节点事务内那一遍，端点负责把节点的具名异常
   映射成 4xx（`assert_offer_creatable` 从 `server.py` 的 import 里一并移除）。
   `test_create_replay_same_request_id_returns_same_offer` 就是这条的判据。

**⏸ 留步登记位（Task 7b/8 步 B 用）**：本轮提取验证时 `app/web/server.py` 里
`grep -q 'export.docx'` **无命中**（U2 Task 5 未落地）⇒ 按计划书登记：
⏸ 留步：U2 Task 5（`GET /api/letters/{id}/export.docx`）未落地，导出门槛的 HTTP 面
测试与 409 显式提示待 U2 落地后接线；Task 7a 的卡点判定已独立生效并有测试覆盖。

（本验证只证明"代码可执行且内部自洽"，spec 合规由 `run-build` 的两阶段 review 负责。）

## 下一步

用 `run-build` 执行本计划（`scripts/codex_sdd_runner.py` 按 `### Task N:` 抽取）。
U3 完成后，`offer-generation` 的 U4（门禁接线与拒信）才可发车——其前置是 U2 与 M2 的
`rejection_record`（design D9），而 **U5 的前置是 U3＋U4**。
⛔ tasks.md 的勾选在该 plan 的 final review 通过后由收口方做，本计划不勾。
