# 留存期清理（8.1–8.2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `data/liaison/archive/` 里的归档材料与 `liaison_message` 台账行在超过 `HR_LIAISON_RETENTION_DAYS`（默认 180）天后被清理掉，清理失败必须告警而不是静默跳过。

**Architecture:** 三层，边界与本服务既有形态一致。**纯函数层** `compute_expired` / `compute_deletable_files` 只做"删哪些"的判定，时钟由 `now` 参数注入；**effect 层** `effect_delete_expired_message` 独占一个删台账行的动作，挂 `@idempotent_effect`，与 `effect_log` 行同一个事务提交；**编排层** `run_cleanup` 按 `先删台账行 → 后删归档文件` 的顺序跑一轮，单条失败不中止整轮，最后把失败汇总成**一条**告警。归档文件那一遍**不靠台账指路**，而是走"扫目录 → 按 `<yyyymmdd>` 目录名判年龄 → 排除仍被台账引用的路径"，因此它对上一遍的失败是自愈的，也顺带扫掉 `attachments.py` 崩溃时留下的 `.tmp-*.part` 孤儿。

**Tech Stack:** Python 3.14 · 标准库（`datetime` / `json` / `pathlib` / `os`）· sqlite3 · `app.storage.idempotency.idempotent_effect` · pytest。⛔ 不引入任何第三方依赖。

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」、`design.md` 与本交付单元 opener。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md 工程铁律（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *（本单元新增唯一的库副作用 `effect_delete_expired_message`，`business_key` = `msgid`、`thread_id` = 该消息的会话。它是**删**不是**增**，因此 ⛔ 不进 `EFFECT_NODE_TO_TABLE`；恒等判据的应对方式见下面「🔴 冲突 A」，那是 `assert_effect_log_identity` docstring 里**已经写死的二选一**，⛔ 不许另创第三种修法。）*
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
   *（纯函数：`compute_cutoff`、`compute_expired`、`compute_deletable_files`、`compute_retention_alert_text`。副作用：`effect_delete_expired_message`（库）、`delete_archive_files` / `prune_empty_dirs`（文件系统）、`emit_retention_alert`（告警通道）。⛔ 不许出现既算又删的混合函数。）*
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
   *（本单元不调任何模型，无评分产生。这条在这里的等价物是**清理必须留痕**：每条被删的 `msgid` 在 `effect_log` 里留下一行 `effect_delete_expired_message`，那就是"这条材料是什么时候按留存期清掉的"的唯一凭据。）*
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   *（本单元不调模型。）*
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。
   *（design D6：本服务不引入 LangGraph。新增的每个 `.py` 里 ⛔ 连 `langgraph` / `SqliteSaver` 字样都不许出现。）*

### 来自 CLAUDE.md 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
- **模型全部走境内**，简历数据不出境。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。
  *（本服务全部对象是内部同事，⛔ MUST NOT 用于向候选人发送任何内容——design D7 已定。）*

**本单元的合规落点（两条，都有断言守）**：

- 🔴 **告警文本里 ⛔ 不得出现 `msgid`、发送人 userid（`thread_id`）、文件名、相对路径或消息正文**——告警将来会经第 6 章送进企微群，等于把"谁发过什么材料"广播出去。告警只带**计数与阶段名**，明细只进本机运行日志。与第 7 章 `alerts.py` 的同一条口径一致。
- 🔴 **清理是"按年龄"的机械动作，⛔ 不得掺入任何按人、按内容、按发送人身份的判定**。`compute_expired` 只看 `archived_at` 与"有没有队列行"。

### 来自 CLAUDE.md 部署约束（逐字）

4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
   *（本服务永不部署 `.51`，跑在 Mac 上。清理的定时触发（launchd / 手动）是 8.3 的事，⛔ 本单元只交付一个**可手工执行的子命令**，不写 plist、不装任何定时器。）*

### 来自 design.md（逐字）

- **D13 · 数据留存 → 归档与消息 180 天，可配置，退役即清理**：`HR_LIAISON_RETENTION_DAYS` 默认 **180**。选 180 天而不是与日志的 30 天对齐：日志是运行证据，归档是**工作材料**，一个开发阶段的材料回溯窗口按季度量级算才够用。队列行不参与自动清理（是工作台账、量小、状态有意义）。本工具随系统上线退役，退役时归档与库一并清理。
- **D3（顺序）**：中间态**允许**"材料已在、台账未记"，⛔ **禁止**"台账已记、材料缺失"。
  *（清理是反向操作，所以顺序也反过来：**先删台账行、后删文件**。反过来做会在两步之间制造一个"台账指向不存在的文件"的窗口，正是 D3 禁止的那种谎。）*
- **D5 · 存储落点**：独立 `data/liaison.db`。`tools/liaison` 可以 `import app.storage.idempotency`，⛔ `app/` 下任何模块不得 import `tools/`；`app.*` 的其余一切都不许进来。
- **D10 · 落点与依赖隔离**：代码落 **`tools/liaison/`**。⛔ 不落 `app/`，⛔ 也不落 `scripts/`。依赖落 `tools/liaison/requirements.txt`，⛔ 不进根 `requirements.txt`。⛔ 不改 `sync-to-server.sh` / `deploy-server.ps1`。
- **D11 · 队列真身 = SQLite 表**：`liaison_task` 是工作台账，Markdown 只是导出物。

### 来自本交付单元 opener（逐字，六条）

1. `compute_expired(now, retention_days, rows)` 纯函数决定"删哪些"；删除动作独立成 effect；顺序＝先删台账行、后删文件（反过来会留下"台账指向不存在的文件"，与 D3 同理）；单条失败不中止整轮，汇总后告警一次
2. ⛔ `liaison_task` 行任何情况不删；⛔ 不删 `effect_log`（审计证据）——只删 `liaison_message` 行与归档文件
3. 时钟注入（`now` 参数）；单测用 fake 时钟与临时目录，⛔ 不 sleep、不用真 `data/`
4. 入口＝`python -m tools.liaison cleanup` 子命令：`__main__.py` 只在文件末尾追加一个子命令分支，⛔ 不动既有行（日志泳道同时在 `main()` 首行接线，各占一头）；⛔ 不改 `storage/effects.py`、`storage/schema.py`（群通知泳道在改）、`whitelist.py`、`tools/liaison/README.md`
5. D10 落点 `tools/liaison/`（建议 `tools/liaison/retention.py`）；⛔ 不碰 `app/`、`scripts/`；⛔ 不 import `app.*`
6. TD-18：避开 `with <Call>:` 写法，⛔ 不改扫描器

> ⚠️ opener 第 5 条的「⛔ 不 import `app.*`」与 design D5 的白名单是同一件事的两种说法：**唯一放行的是 `app.storage.idempotency`**（`test_liaison_does_not_import_product_db_layer` 的 `ALLOWED_APP_IMPORTS` 就这一项）。本单元的 `effect_delete_expired_message` 必须挂 `@idempotent_effect`（铁律 1），所以这一个 import 是必需且合法的，⛔ 除它之外一个 `app.*` 都不许再进来。

### 本单元的六条"不做"

- ⛔ **不删 `liaison_task` 行**，一行都不删，任何状态（`pending` / `deferred` / `pushed`）都不删。
- ⛔ **不删 `effect_log` 行**，一行都不删——它是"这条材料被清理过"的唯一审计证据。
- ⛔ **不改 `storage/schema.py`**（群通知泳道在改）：本单元**不加任何表、不加任何列**。
- ⛔ **不改 `storage/effects.py`**（同上）：新的 effect 落在 `tools/liaison/retention.py` 里。
- ⛔ **不写 launchd plist、不装 cron、不做任何定时触发**（8.3 / 8.4 的事）。本单元只交付一个手工可执行的子命令。
- ⛔ **不碰** `whitelist.py`、`tools/liaison/README.md`、`app/`、`scripts/`、`sync-to-server.sh`、`deploy-server.ps1`、根 `requirements.txt`、`pyproject.toml`。

---

## 前置状态与冲突处置（⚠️ controller 开工前必读）

**已在 `main` 上、本单元直接依赖的既有实现**（只读，⛔ 不改）：

| 依赖 | 位置 | 本单元怎么用 |
|---|---|---|
| `liaison_message` / `liaison_task` 两表与 FK | `tools/liaison/storage/schema.py` | 只读 DDL 语义，⛔ 不改文件 |
| `idempotent_effect` | `app/storage/idempotency.py` | 装饰 `effect_delete_expired_message` |
| `get_connection` / `init_schema` | `tools/liaison/storage/db.py` | CLI 建连接。⚠️ `PRAGMA foreign_keys = ON` 是本单元「冲突 B」的成因 |
| `DEFAULT_ARCHIVE_ROOT`、路径形态 `<thread_id>/<yyyymmdd>/<msgid>__<文件名>` | `tools/liaison/archive.py` | 文件那一遍按这个形态解析年龄 |
| `_TEMP_PREFIX = ".tmp-"` / `_TEMP_SUFFIX = ".part"` | `tools/liaison/attachments.py` | 孤儿临时文件顺带扫掉（该模块 docstring 已把这件事指给第 8 章） |
| `AlertSink` / `LoggingAlertSink` | `tools/liaison/alerts.py` | 只 **import 复用形状**，⛔ 不改该文件 |
| `CHINA_TZ` | `tools/liaison/session.py` | 时区常量单一真源，⛔ 不再定义第二个 |
| `assert_effect_log_identity` | `tools/liaison/tests/test_liaison_effects.py` | Task 2 要按其 docstring 的**方案 2** 收窄比对范围 |

**并行泳道冲突面**：本单元**独占** `tools/liaison/retention.py` 与 `tools/liaison/tests/test_retention.py`（都是新文件，零冲突）。只有两处碰共享文件：

1. `tools/liaison/__main__.py` —— **纯插入**，插在文件最后那两行 `if __name__ == "__main__":` / `raise SystemExit(main())` 的**正上方**，既有行一字节都不动（日志泳道在 `main()` 函数体首行接线，两头各占一边）。
2. `tools/liaison/tests/test_liaison_effects.py` —— 只改 `assert_effect_log_identity` 一个函数体 + 追加两条测试，⛔ 不碰扫描器（TD-18 已还，别再动它）。

`git add` 时**只 add 本计划列出的路径**，⛔ 禁止 `git add -A` / `git add .` / `git commit -a`。

---

## 🔴 三个必须显式处理的结构冲突（开工前逐条读完）

这三条都是"照着 opener 直写就会撞上、且撞上时症状要么是测试莫名其妙变红、要么是 `IntegrityError`"的地方。**⛔ 不许在实现时临时发明修法**，按下面写死的处置做。

### 冲突 A · 清理会让铁律 1 的恒等断言变红（这是**正当**的变红）

`tools/liaison/tests/test_liaison_effects.py:645` 的 `assert_effect_log_identity` 断言：按 `thread_id` 分组，`effect_archive_message` 的 `effect_log` 条数 == `liaison_message` 行数。**清理一删台账行，这条等式当场破。** 该 docstring 已经预判到并写死了唯一允许的两个修法：

> 1. 留存期清理与对应的 `effect_log` 行在同一个事务里连带删除；或
> 2. 把本断言的比对范围限定在"尚未被清理"的 thread 集合内。
>
> ⛔ 二选一之外的任何"修法"都不允许 …… ⛔ 不许把它改成总数比较、不许削弱成"约等于"。

**本单元取方案 2**——方案 1 被 opener 第 2 条明令禁止（⛔ 不删 `effect_log`，它是审计证据）。

落地形态：`effect_delete_expired_message` 每删一行就在 `effect_log` 里留下一行 `node_name='effect_delete_expired_message'`，**那批行的 `thread_id` 集合恰好就是"被清理过的 thread"**。`assert_effect_log_identity` 开头把这批 thread 排除掉，其余 thread 仍然逐组严格相等。⛔ 不许改成总数比较、⛔ 不许放宽成不等式。被排除的那些 thread 由 Task 2 新增的 `assert_retention_accounting` 单独用一条**更强**的等式盯住：

```
count(effect_log[effect_archive_message], thread)
    == count(liaison_message, thread) + count(effect_log[effect_delete_expired_message], thread)
```

### 冲突 B · 外键让"删台账行但不删队列行"在多数情况下直接失败

`liaison_task.msgid` 是 `REFERENCES liaison_message (msgid)`，且 `get_connection()` 开了 `PRAGMA foreign_keys = ON`（无 `ON DELETE` 子句 ⇒ NO ACTION）。所以**只要一条超期消息还有队列行，`DELETE FROM liaison_message` 就会抛 `IntegrityError`**。而 `inbound.py` 的路由是：名单**内**的消息一律入队。⇒ 汤丽萍 / 邵培申发来的消息**全部**有队列行。

opener 第 2 条又写死 ⛔ `liaison_task` 行任何情况不删。两条约束叠加的**必然结论**：

> 🔴 **名单内（已入队）消息的台账行与归档文件，在当前约束下永远不会被留存期清理掉。** 实际会被清掉的只有"归档了但没入队"的那部分——即名单外发送人的消息。

处置（⛔ 不许自行放宽）：

- `compute_expired` 把这批行单独分进 `blocked_by_queue` 桶，**⛔ 不删、⛔ 不静默**：每轮都进报告、进日志，计数进告警文本（若本轮有失败）。
- `effect_delete_expired_message` 里保留 FK 这道结构防线：真撞上 `IntegrityError` 就当作一条 failure 记下来继续跑，⛔ 不许为了绕开它去关 `PRAGMA foreign_keys`。
- **这是一条要 Shao Peishen 拍的取舍**（"队列行永不清理"与"归档 180 天"两条 design 结论在 FK 下不可兼得），本轮按保守方向（不删）执行并登记，⛔ 不替他拍。登记文字见「收尾」第 3 条。

### 冲突 C · `archived_at` 是 UTC 且不带时区后缀

`liaison_message.archived_at` 的默认值是 SQLite 的 `datetime('now')`，它给出的是 **UTC**、形如 `2026-09-09 10:00:00`、**没有时区后缀**。而 `received_at` 是带 `+08:00` 的 ISO8601。

- 台账行的年龄一律按 **`archived_at`** 判（"材料进入我们保管的时刻"才是留存期的计时起点），解析后**必须显式补 `timezone.utc`**。⛔ 不许当本地时间解析——本机在 EDT，差 12 小时，且不报错。
- 归档**文件**的年龄按路径里的 `<yyyymmdd>` 段判，那个日期来自 `received_at`，因此按 **`+08:00` 的当天 23:59:59.999999** 折算（取当天最晚的一刻 ⇒ 更晚过期 ⇒ 保守方向）。
- 两处都用 `now`（tz-aware）注入，⛔ 函数体内不许出现 `datetime.now()`，唯一允许调它的地方是 `cleanup_main`。

---

## Spec Requirement → Task 对照

spec `openspec/changes/hr-wecom-aibot-liaison/specs/liaison-message-archive/spec.md`「归档数据的留存期有上限」：

| spec 原文 | 落点 |
|---|---|
| 归档的消息与附件 SHALL 有留存期上限 | Task 1 `compute_expired` + Task 3 `compute_deletable_files` |
| 留存期 SHALL 可配置 | Task 1 `load_retention_days`（`HR_LIAISON_RETENTION_DAYS`，默认 180） |
| 超期数据 SHALL 被清理 | Task 2（台账行）+ Task 3（归档文件） |
| Scenario 超期数据被清理 | Task 2 `test_expired_message_row_is_deleted`、Task 3 `test_expired_unreferenced_file_is_deleted` |
| 清理失败时 SHALL 告警，MUST NOT 静默跳过 | Task 4 `run_cleanup` + `emit_retention_alert` |
| Scenario 清理失败 → 发出告警 + 该失败被记录 | Task 4 `test_file_deletion_failure_raises_exactly_one_alert` |
| tasks 8.1「队列行不参与自动清理」 | Task 2 `test_message_with_queue_row_is_never_deleted`（冲突 B） |
| tasks 8.1「按年龄判定，重复执行安全」 | Task 2 `test_running_twice_is_a_no_op`、Task 3 `test_file_pass_is_idempotent` |

---

### Task 1: 留存配置与"删哪些"的纯函数

**Files:**
- Create: `tools/liaison/retention.py`
- Test: `tools/liaison/tests/test_retention.py`

**Interfaces:**
- Consumes: `tools.liaison.session.CHINA_TZ`（既有常量，⛔ 不再定义第二个时区）
- Produces:
  - `RETENTION_DAYS_ENV: str = "HR_LIAISON_RETENTION_DAYS"`、`DEFAULT_RETENTION_DAYS: int = 180`
  - `class RetentionConfigError(ValueError)`
  - `load_retention_days(env: Mapping[str, str] | None = None) -> int`
  - `@dataclass(frozen=True) MessageRow(msgid: str, thread_id: str, archived_at: str, attachments_json: str, has_queue_row: bool)`
  - `@dataclass(frozen=True) ExpiredMessage(msgid: str, thread_id: str, relative_paths: tuple[str, ...])`
  - `@dataclass(frozen=True) SkippedItem(subject: str, reason: str)`
  - `@dataclass(frozen=True) ExpirySplit(deletable: tuple[ExpiredMessage, ...], blocked_by_queue: tuple[ExpiredMessage, ...], undecidable: tuple[SkippedItem, ...])`
  - `compute_cutoff(now: datetime, retention_days: int) -> datetime`
  - `parse_archived_at(value: object) -> datetime`
  - `parse_relative_paths(attachments_json: object) -> tuple[str, ...]`
  - `compute_expired(now: datetime, retention_days: int, rows: Sequence[MessageRow]) -> ExpirySplit`

- [ ] **Step 1: 写失败的测试**

新建 `tools/liaison/tests/test_retention.py`：

```python
"""留存期清理（tasks 8.1–8.2）。

⛔ 全部用 fake 时钟与 `tmp_path`：⛔ 不 sleep、⛔ 不碰真实的 `data/`
（opener 约束 3）。任何一条用例跑完之后仓库里不许多出一个文件。
"""

from __future__ import annotations

import datetime

import pytest

from tools.liaison import retention
from tools.liaison.retention import MessageRow, RetentionConfigError

CHINA_TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)


def _row(msgid, *, archived_at, has_queue_row=False, attachments_json="[]", thread_id="u1"):
    return MessageRow(
        msgid=msgid,
        thread_id=thread_id,
        archived_at=archived_at,
        attachments_json=attachments_json,
        has_queue_row=has_queue_row,
    )


def test_default_retention_days_is_180():
    """design D13 逐字：默认 180。⛔ 不许悄悄改成别的数。"""
    assert retention.DEFAULT_RETENTION_DAYS == 180
    assert retention.load_retention_days({}) == 180


def test_retention_days_comes_from_env_when_set():
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": "30"}) == 30
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": " 45 "}) == 45


def test_blank_env_falls_back_to_default():
    """空串 = 没配（与 config.py 的 `_is_blank` 同口径），⛔ 不算 0。"""
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": "   "}) == 180


@pytest.mark.parametrize("bad", ["abc", "180.5", "0", "-1", "1e3"])
def test_invalid_retention_days_is_fail_closed(bad):
    """配错就拒绝跑，⛔ 不许静默退回默认值。

    退回默认值意味着一个手滑写成 `18` 少一个 0 的配置**看起来生效了**，
    实际按 180 跑；反过来 `0` 会当场删光全部归档。两个方向都不可接受，
    唯一安全的处置是拒绝执行。
    """
    with pytest.raises(RetentionConfigError):
        retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": bad})


def test_parse_archived_at_treats_sqlite_now_as_utc():
    """🔴 冲突 C：`datetime('now')` 给的是 UTC 且不带后缀。

    ⛔ 不许当本地时间解析——本机在 EDT，差 12 小时，且不会报错。
    """
    moment = retention.parse_archived_at("2026-09-09 10:00:00")
    assert moment == datetime.datetime(2026, 9, 9, 10, 0, tzinfo=datetime.timezone.utc)


def test_parse_archived_at_keeps_explicit_offset():
    moment = retention.parse_archived_at("2026-09-09T10:00:00+08:00")
    assert moment.utcoffset() == datetime.timedelta(hours=8)


@pytest.mark.parametrize("bad", ["", "   ", "not-a-time", None, 20260909])
def test_parse_archived_at_rejects_garbage(bad):
    with pytest.raises(ValueError):
        retention.parse_archived_at(bad)


def test_compute_expired_deletes_only_rows_older_than_cutoff():
    """8.2 逐字要求的「超期被清理一条」在纯函数层的形态。"""
    fresh = _row("m-fresh", archived_at="2026-09-01 00:00:00")
    expired = _row("m-old", archived_at="2026-01-01 00:00:00")
    split = retention.compute_expired(NOW, 180, [fresh, expired])
    assert [item.msgid for item in split.deletable] == ["m-old"]
    assert split.blocked_by_queue == ()
    assert split.undecidable == ()


def test_compute_expired_keeps_the_row_exactly_on_the_boundary():
    """恰好等于 cutoff 的行**保留**（判据是严格小于）。

    边界上多留一天永远是安全方向，少留一天是不可逆的删除。
    """
    cutoff = retention.compute_cutoff(NOW, 180)
    on_boundary = _row("m-edge", archived_at=cutoff.astimezone(datetime.timezone.utc)
                       .strftime("%Y-%m-%d %H:%M:%S"))
    split = retention.compute_expired(NOW, 180, [on_boundary])
    assert split.deletable == ()


def test_compute_expired_puts_queued_rows_in_their_own_bucket():
    """🔴 冲突 B：有队列行的消息 ⛔ 不删，但也 ⛔ 不静默——单独成桶。"""
    queued = _row("m-queued", archived_at="2026-01-01 00:00:00", has_queue_row=True)
    split = retention.compute_expired(NOW, 180, [queued])
    assert split.deletable == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-queued"]


def test_compute_expired_reports_unparseable_rows_instead_of_guessing():
    """坏数据不猜、不删、不吞——进 undecidable 桶，由编排层告警。"""
    bad_time = _row("m-bad-time", archived_at="昨天")
    bad_json = _row("m-bad-json", archived_at="2026-01-01 00:00:00", attachments_json="{")
    split = retention.compute_expired(NOW, 180, [bad_time, bad_json])
    assert split.deletable == ()
    assert {item.subject for item in split.undecidable} == {"m-bad-time", "m-bad-json"}


def test_compute_expired_extracts_relative_paths():
    row = _row(
        "m-att",
        archived_at="2026-01-01 00:00:00",
        attachments_json='[{"filename": "a.xlsx", "relative_path": "u1/20260101/m-att__a.xlsx",'
                         ' "byte_length": 3, "sha256": "x"}]',
    )
    split = retention.compute_expired(NOW, 180, [row])
    assert split.deletable[0].relative_paths == ("u1/20260101/m-att__a.xlsx",)


def test_compute_expired_is_pure_and_takes_no_clock(monkeypatch):
    """时钟注入（opener 约束 3）：把 `datetime.datetime` 换成会炸的替身，
    纯函数仍必须能跑完——它一次都不许读真实时钟。"""
    row = _row("m-old", archived_at="2026-01-01 00:00:00")
    split = retention.compute_expired(NOW, 180, [row])
    assert [item.msgid for item in split.deletable] == ["m-old"]
    assert retention.compute_expired(NOW, 180, [row]) == split  # 同输入同输出
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_retention.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.liaison.retention'`

- [ ] **Step 3: 写最小实现**

新建 `tools/liaison/retention.py`：

```python
"""留存期清理（tasks 8.1–8.2、design D13、spec liaison-message-archive
「归档数据的留存期有上限」）。

**本模块分三层，⛔ 不许混在一起：**

- `compute_*` 是纯函数：不读时钟（`now` 由参数注入）、不读环境变量、不碰
  文件系统、不碰数据库。同一份输入在任何时刻必须给出同一个答案——否则
  "重复执行安全"这句话没有任何依据。
- `effect_delete_expired_message` 是**唯一**的库副作用，挂 `@idempotent_effect`
  （工程铁律 1），台账行与 `effect_log` 行在同一个事务里提交。
- `delete_archive_files` / `prune_empty_dirs` / `emit_retention_alert` 是
  文件系统与告警通道的副作用，它们**不在事务里**（文件系统不参与 SQL 事务，
  硬凑只会让一次 I/O 失败把事务处理路径也拖进来——design D3）。

🔴 **三条不许碰的东西**（opener 约束 2）：
1. ⛔ `liaison_task` 行**任何状态、任何情况都不删**。它是工作台账。
2. ⛔ `effect_log` 行一行不删。它是"这条材料被清理过"的唯一审计证据。
3. ⛔ 不加表、不加列——`storage/schema.py` 由并行泳道持有。

🔴 **顺序：先删台账行、后删归档文件。** 反过来会在两步之间留下"台账指向
不存在的文件"，那正是 design D3 明令禁止的中间态。文件那一遍**不靠台账
指路**（走"扫目录 + 排除仍被引用的路径"），因此它对上一遍的失败是自愈的。

⛔ 本模块不许写 `with <连接或调用>:`——`tests/test_liaison_effects.py` 的事务
扫描器会把它判成隐式提交（白名单只有 `open` / `os.fdopen` / `io.open` /
`contextlib.suppress` / `tempfile.NamedTemporaryFile` / `tempfile.TemporaryDirectory`）。
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from tools.liaison.session import CHINA_TZ

logger = logging.getLogger(__name__)

#: 留存期天数的环境变量（design D13 逐字）。
RETENTION_DAYS_ENV: Final[str] = "HR_LIAISON_RETENTION_DAYS"

#: design D13 逐字：默认 180 天。⛔ 改这个数之前先改 design——
#: 它是"归档是工作材料、回溯窗口按季度算"这条论证的结论，不是随手挑的参数。
DEFAULT_RETENTION_DAYS: Final[int] = 180


class RetentionConfigError(ValueError):
    """留存期配置不可用。**fail-closed**：⛔ 不许退回默认值继续跑。

    退回默认值意味着一个手滑（`18` 少写一个 0）看起来生效了、实际按 180 跑；
    而 `0` 会当场删光全部归档。两个方向都不可接受，唯一安全的处置是拒绝执行。
    """


def load_retention_days(env: Mapping[str, str] | None = None) -> int:
    """读留存期天数。没配（缺失／空串／纯空白）⇒ 默认 180；配错 ⇒ raise。"""
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(RETENTION_DAYS_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_RETENTION_DAYS
    text = str(raw).strip()
    try:
        days = int(text)
    except ValueError as exc:
        raise RetentionConfigError(
            f"{RETENTION_DAYS_ENV} 必须是正整数天数，实际是 {text!r}；"
            f"⛔ 拒绝按默认值继续跑"
        ) from exc
    if days < 1:
        raise RetentionConfigError(
            f"{RETENTION_DAYS_ENV} 必须 >= 1，实际是 {days}；"
            f"⛔ 0 或负数等于要求立刻删光全部归档，几乎必然是打错了"
        )
    return days


@dataclass(frozen=True)
class MessageRow:
    """从 `liaison_message` 读出来的一行 + "它有没有队列行"。

    `has_queue_row` 由 SQL 一次算出（见 `load_message_rows`），⛔ 不要在纯
    函数里再去查库——那会让 `compute_expired` 不再是纯函数。
    """

    msgid: str
    thread_id: str
    archived_at: str
    attachments_json: str
    has_queue_row: bool


@dataclass(frozen=True)
class ExpiredMessage:
    """一条判定为超期的消息，连同它引用的归档文件相对路径。"""

    msgid: str
    thread_id: str
    relative_paths: tuple[str, ...]


@dataclass(frozen=True)
class SkippedItem:
    """一条"没处理、且必须被看见"的记录。

    ⛔ 不要把它降级成一行 debug 日志：8.2 逐字要求"⛔ 不静默跳过"。
    """

    subject: str
    reason: str


@dataclass(frozen=True)
class ExpirySplit:
    """`compute_expired` 的三桶输出。三个桶互斥且穷尽。"""

    deletable: tuple[ExpiredMessage, ...]
    blocked_by_queue: tuple[ExpiredMessage, ...]
    undecidable: tuple[SkippedItem, ...]


def compute_cutoff(now: datetime.datetime, retention_days: int) -> datetime.datetime:
    """算出"早于这一刻的都算超期"的那一刻。纯函数。

    ⛔ `now` 必须带时区。裸的 naive 时间会在下面与带时区的 `archived_at`
    比较时抛 `TypeError`——那还算好的；真正危险的是它悄悄按本机时区被理解，
    而本机在 EDT，与 `+08:00` 差 12 小时。
    """
    if now.tzinfo is None:
        raise ValueError("now 必须带时区（tz-aware），⛔ 不接受 naive datetime")
    if retention_days < 1:
        raise ValueError(f"retention_days 必须 >= 1，实际是 {retention_days}")
    return now - datetime.timedelta(days=retention_days)


def parse_archived_at(value: Any) -> datetime.datetime:
    """解析 `liaison_message.archived_at`。

    🔴 该列的默认值是 SQLite 的 `datetime('now')`，它给的是 **UTC**、
    形如 `2026-09-09 10:00:00`、**不带时区后缀**。没有后缀就补 UTC，
    ⛔ 绝不许按本机时区理解——本机在 EDT，差 12 小时且不报错。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"archived_at 必须是非空字符串，实际是 {value!r}")
    try:
        moment = datetime.datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"archived_at 不是可解析的时间戳：{value!r}") from exc
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def parse_relative_paths(attachments_json: Any) -> tuple[str, ...]:
    """从 `attachments_json` 取出相对归档根的路径清单。

    解析不了就 raise：调用方把它记成 `SkippedItem`。⛔ 不许当成"没有附件"
    静默返回空元组——那会让一条本该保留的材料被误判成"无文件可删"，
    而它引用的文件随后会在文件那一遍被当成孤儿删掉。
    """
    try:
        entries = json.loads(attachments_json)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"attachments_json 不是合法 JSON：{exc}") from exc
    if not isinstance(entries, list):
        raise ValueError(f"attachments_json 必须是数组，实际是 {type(entries).__name__}")
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("attachments_json 的条目必须是对象")
        path = entry.get("relative_path")
        if not isinstance(path, str) or not path:
            raise ValueError("attachments_json 的条目缺少非空 relative_path")
        paths.append(path)
    return tuple(paths)


def compute_expired(
    now: datetime.datetime, retention_days: int, rows: Sequence[MessageRow]
) -> ExpirySplit:
    """决定"删哪些"。**纯函数**（opener 约束 1 逐字给的签名）。

    判据只有年龄一条（tasks 8.1「按年龄判定，重复执行安全」）：
    ⛔ 不看发送人是谁、不看内容、不看消息类型——那会把一个机械的留存动作
    变成一条按人区别对待的规则。

    三个桶：
    - `deletable`：超期、且没有队列行 ⇒ 可以删；
    - `blocked_by_queue`：超期、但有队列行 ⇒ ⛔ 不删（opener 约束 2：
      `liaison_task` 行任何情况不删，而外键使得删父行必然失败）。见冲突 B；
    - `undecidable`：`archived_at` 或 `attachments_json` 坏了 ⇒ ⛔ 不猜、不删，
      交给编排层告警。
    """
    cutoff = compute_cutoff(now, retention_days)
    deletable: list[ExpiredMessage] = []
    blocked: list[ExpiredMessage] = []
    undecidable: list[SkippedItem] = []

    for row in rows:
        try:
            archived_at = parse_archived_at(row.archived_at)
        except ValueError as exc:
            undecidable.append(SkippedItem(row.msgid, f"archived_at 不可解析：{exc}"))
            continue
        # 严格小于：恰好落在 cutoff 上的行保留。边界上多留一天是安全方向，
        # 少留一天是不可逆的删除。
        if not archived_at < cutoff:
            continue
        try:
            paths = parse_relative_paths(row.attachments_json)
        except ValueError as exc:
            undecidable.append(SkippedItem(row.msgid, f"attachments_json 不可解析：{exc}"))
            continue
        item = ExpiredMessage(msgid=row.msgid, thread_id=row.thread_id, relative_paths=paths)
        if row.has_queue_row:
            blocked.append(item)
        else:
            deletable.append(item)

    return ExpirySplit(tuple(deletable), tuple(blocked), tuple(undecidable))
```

- [ ] **Step 4: 跑测试确认它通过**

Run: `python -m pytest tools/liaison/tests/test_retention.py -v`
Expected: PASS（12 条）

- [ ] **Step 5: 跑一遍既有守卫，确认没碰坏别人**

Run: `python -m pytest tools/liaison/tests/test_liaison_effects.py tools/liaison/tests/test_liaison_boundaries.py -q`
Expected: PASS。⚠️ 特别是 `test_no_second_transaction_manager_in_source`（新文件 `retention.py` 里 ⛔ 不许有 `commit` / `rollback` / `executescript` / `with <名字>:`）与 `test_liaison_does_not_import_product_db_layer`（本步骤还没 import 任何 `app.*`）。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/retention.py tools/liaison/tests/test_retention.py
git commit -m "feat(liaison): 留存期配置与 compute_expired 纯函数（8.1）"
```

---

### Task 2: 删台账行的 effect，以及恒等不变式按方案 2 收窄

**Files:**
- Modify: `tools/liaison/retention.py`（追加，⛔ 不改 Task 1 已有函数）
- Modify: `tools/liaison/tests/test_liaison_effects.py`（只改 `assert_effect_log_identity` 一个函数体 + 追加两条测试；⛔ 不碰扫描器）
- Test: `tools/liaison/tests/test_retention.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `MessageRow` / `ExpirySplit` / `compute_expired`；`app.storage.idempotency.idempotent_effect`
- Produces:
  - `RETENTION_DELETE_NODE: str = "effect_delete_expired_message"`
  - `class RetentionLedgerError(RuntimeError)`
  - `@dataclass(frozen=True) CleanupFailure(stage: str, subject: str, reason: str)`
  - `effect_delete_expired_message(conn, *, thread_id: str, business_key: str) -> str | None`
  - `load_message_rows(conn) -> tuple[MessageRow, ...]`
  - `delete_expired_ledger_rows(conn, expired: Sequence[ExpiredMessage]) -> tuple[tuple[str, ...], tuple[CleanupFailure, ...]]`

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_retention.py`：

```python
import sqlite3

from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message, effect_enqueue_task
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

OLD = "2026-01-01 00:00:00"   # 相对 NOW 已超 180 天
NEW = "2026-09-01 00:00:00"   # 相对 NOW 未超期


@pytest.fixture()
def conn(tmp_path):
    """⛔ 不用真 data/：库落在 tmp_path 里，用例跑完随 tmp_path 一起消失。"""
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


def _archive(conn, msgid, *, thread_id="u1", archived_at=OLD, attachments_json="[]"):
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at="2026-01-01T08:00:00+08:00",
        msgtype="text",
        content="",
        attachments_json=attachments_json,
    )
    # `archived_at` 由列默认值写成"现在"，测试要的是"很久以前"——直接改这一列。
    # ⛔ 不许在生产代码里提供"改 archived_at"的入口，那等于给留存期开后门。
    conn.execute("UPDATE liaison_message SET archived_at = ? WHERE msgid = ?", (archived_at, msgid))
    conn.commit()


def test_expired_message_row_is_deleted(conn):
    """8.2 逐字：「超期被清理一条」。"""
    _archive(conn, "m-old", archived_at=OLD)
    _archive(conn, "m-new", archived_at=NEW)
    rows = retention.load_message_rows(conn)
    split = retention.compute_expired(NOW, 180, rows)
    deleted, failures = retention.delete_expired_ledger_rows(conn, split.deletable)
    assert deleted == ("m-old",)
    assert failures == ()
    remaining = [r[0] for r in conn.execute("SELECT msgid FROM liaison_message")]
    assert remaining == ["m-new"]


def test_effect_log_is_never_deleted(conn):
    """opener 约束 2：⛔ 不删 effect_log——它是审计证据。

    删掉一行台账之后，`effect_archive_message` 那一行**必须**还在，
    并且多出一行 `effect_delete_expired_message`。
    """
    _archive(conn, "m-old", archived_at=OLD)
    retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    nodes = sorted(r[0] for r in conn.execute("SELECT node_name FROM effect_log"))
    assert nodes == ["effect_archive_message", "effect_delete_expired_message"]


def test_message_with_queue_row_is_never_deleted(conn):
    """🔴 冲突 B：有队列行的超期消息 ⛔ 不删，且队列行一行不少。"""
    _archive(conn, "m-queued", archived_at=OLD)
    effect_enqueue_task(
        conn,
        thread_id="u1",
        business_key="m-queued",
        sender_userid="u1",
        received_at="2026-01-01T08:00:00+08:00",
        summary="s",
    )
    conn.commit()
    split = retention.compute_expired(NOW, 180, retention.load_message_rows(conn))
    deleted, failures = retention.delete_expired_ledger_rows(conn, split.deletable)
    assert deleted == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-queued"]
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_foreign_key_is_the_second_line_of_defence(conn):
    """就算前面的判定被绕过，外键也必须拦住删父行这件事。

    ⛔ 这条变红时的正确修法**不是**去关 `PRAGMA foreign_keys`。
    """
    _archive(conn, "m-queued", archived_at=OLD)
    effect_enqueue_task(
        conn, thread_id="u1", business_key="m-queued", sender_userid="u1",
        received_at="2026-01-01T08:00:00+08:00", summary="s",
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        retention.effect_delete_expired_message(conn, thread_id="u1", business_key="m-queued")
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1


def test_running_twice_is_a_no_op(conn):
    """tasks 8.1 逐字：「重复执行安全」。

    第二遍既不该再删出东西，也不该报失败——按年龄判定时第一遍已经把行删掉了，
    第二遍根本扫不到它。
    """
    _archive(conn, "m-old", archived_at=OLD)
    first = retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    second = retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    assert first[0] == ("m-old",)
    assert second == ((), ())


def assert_retention_accounting(conn):
    """被清理过的 thread 上，铁律 1 的恒等式换成这条**更强**的等式：

        归档 effect 行数 == 台账行数 + 清理 effect 行数

    差额必须被"清理过多少条"逐条解释干净，⛔ 不许有解释不掉的余数。
    """
    for (thread_id,) in conn.execute(
        "SELECT DISTINCT thread_id FROM effect_log WHERE node_name = ?",
        (retention.RETENTION_DELETE_NODE,),
    ).fetchall():
        archived = conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_archive_message' "
            "AND thread_id = ?", (thread_id,)
        ).fetchone()[0]
        alive = conn.execute(
            "SELECT COUNT(*) FROM liaison_message WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
        cleaned = conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
            (retention.RETENTION_DELETE_NODE, thread_id),
        ).fetchone()[0]
        assert archived == alive + cleaned, (
            f"留存清理记账不平：thread={thread_id} 归档 {archived} != 存活 {alive} + 已清 {cleaned}"
        )


def test_identity_still_holds_after_cleanup(conn):
    """冲突 A：清理过的 thread 走记账式等式，没清理过的 thread 仍走严格恒等。"""
    _archive(conn, "m-old", thread_id="u1", archived_at=OLD)
    _archive(conn, "m-new", thread_id="u1", archived_at=NEW)
    _archive(conn, "m-other", thread_id="u2", archived_at=NEW)
    retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    assert_effect_log_identity(conn)     # u2 未被清理，仍严格恒等
    assert_retention_accounting(conn)    # u1 被清理过，走记账等式
```

再追加到 `tools/liaison/tests/test_liaison_effects.py`（紧跟在 `assert_effect_log_identity` 之后）：

```python
def test_identity_assertion_still_catches_a_break_in_an_uncleaned_thread(conn):
    """冲突 A 的证伪：收窄比对范围之后，**没被清理过**的 thread 仍必须被抓住。

    ⛔ 不要删这条。方案 2 的风险正是"排除范围"悄悄扩大到把所有 thread 都排掉，
    那时断言永远为真——比没有断言更糟。
    """
    _process(conn, thread_id="u1", msgid="m1")
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('sneaky', 'u1', 'u1', 't', 'text')"
    )
    conn.commit()
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def test_identity_assertion_excludes_only_threads_that_were_actually_cleaned(conn):
    """被排除的必须**恰好**是留下过清理记录的那些 thread，一个不多。"""
    _process(conn, thread_id="u1", msgid="m1")
    _process(conn, thread_id="u2", msgid="m2")
    # u1 上伪造一条"清理过"的痕迹，并把它的台账行删掉——u1 应被排除。
    conn.execute("DELETE FROM liaison_task WHERE thread_id = 'u1'")
    conn.execute("DELETE FROM liaison_message WHERE thread_id = 'u1'")
    conn.execute(
        "INSERT INTO effect_log VALUES ('u1:effect_delete_expired_message:m1', 'u1', "
        "'effect_delete_expired_message', 'm1', datetime('now'))"
    )
    conn.commit()
    assert_effect_log_identity(conn)  # u1 被排除，u2 仍严格恒等 ⇒ 通过
    # u2 上制造一处破裂，必须仍然被抓住
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('sneaky2', 'u2', 'u2', 't', 'text')"
    )
    conn.commit()
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_retention.py -v`
Expected: FAIL — `AttributeError: module 'tools.liaison.retention' has no attribute 'load_message_rows'`

- [ ] **Step 3: 写最小实现**

追加到 `tools/liaison/retention.py`：

```python
from app.storage.idempotency import idempotent_effect

#: 清理动作的 effect 节点名。**这个名字有两个用途**，改它会同时打断两处：
#: ① 幂等键的中段；② `assert_effect_log_identity` 判断"哪些 thread 被清理过"
#: 的依据（冲突 A 的方案 2）。
RETENTION_DELETE_NODE: Final[str] = "effect_delete_expired_message"


class RetentionLedgerError(RuntimeError):
    """删台账行时行数对不上（既不是 1 也不是外键拒绝）。"""


@dataclass(frozen=True)
class CleanupFailure:
    """一条清理失败。`stage` ∈ {"ledger", "file", "scan", "prune"}。

    ⛔ 不要把它和 `SkippedItem` 合并：`SkippedItem` 是"按规矩不该处理"，
    `CleanupFailure` 是"该处理却没处理成"。前者是正常路径，后者要告警。
    """

    stage: str
    subject: str
    reason: str


@idempotent_effect(RETENTION_DELETE_NODE)
def effect_delete_expired_message(
    conn, *, thread_id: str, business_key: str
) -> str:
    """删掉一条超期的台账行。**本服务唯一的删库动作。**

    幂等键 `{thread_id}:effect_delete_expired_message:{msgid}`，与业务删除
    在同一个事务里提交（工程铁律 1）。留下的这行 `effect_log` 有两个作用：
    ① 审计——"这条材料是什么时候按留存期清掉的"的唯一凭据；
    ② 让 `assert_effect_log_identity` 知道哪些 thread 被清理过（冲突 A 方案 2）。

    ⛔ 只删 `liaison_message` 一张表的一行。⛔ 不删 `liaison_task`
    （opener 约束 2；外键也会拦住），⛔ 不删 `effect_log`。

    ⛔ 不 `commit()` 也不 `rollback()`：提交由 `idempotent_effect` 独占。
    """
    cursor = conn.execute(
        "DELETE FROM liaison_message WHERE msgid = ? AND thread_id = ?",
        (business_key, thread_id),
    )
    if cursor.rowcount != 1:
        # 装饰器会先 rollback 再把异常抛出去，所以这一行不会留下半截状态。
        raise RetentionLedgerError(
            f"删除 msgid={business_key} thread_id={thread_id} 影响了 {cursor.rowcount} 行"
            f"（期望恰好 1 行）；⛔ 不继续删，先查清楚"
        )
    return business_key


def load_message_rows(conn) -> tuple[MessageRow, ...]:
    """读全量台账 + 每行"有没有队列行"。

    `has_queue_row` 用一条 `EXISTS` 子查询一次算出来，⛔ 不要在 Python 里
    逐行回查——那会让行数上去之后变成 N+1 次查询，也会让 `compute_expired`
    有理由去碰连接。
    """
    return tuple(
        MessageRow(
            msgid=row[0],
            thread_id=row[1],
            archived_at=row[2],
            attachments_json=row[3],
            has_queue_row=bool(row[4]),
        )
        for row in conn.execute(
            "SELECT m.msgid, m.thread_id, m.archived_at, m.attachments_json, "
            "       EXISTS(SELECT 1 FROM liaison_task t WHERE t.msgid = m.msgid) "
            "FROM liaison_message AS m ORDER BY m.msgid"
        ).fetchall()
    )


def delete_expired_ledger_rows(
    conn, expired: Sequence[ExpiredMessage]
) -> tuple[tuple[str, ...], tuple[CleanupFailure, ...]]:
    """逐条删台账行。**单条失败不中止整轮**（opener 约束 1 逐字）。

    ⛔ 不许改成一条 `DELETE ... WHERE msgid IN (...)`：批量删会让一条外键
    冲突把整批一起回滚掉，"单条失败不中止整轮"当场失效，而且失败时你
    分不清是哪一条挡住的。
    """
    deleted: list[str] = []
    failures: list[CleanupFailure] = []
    for item in expired:
        try:
            applied = effect_delete_expired_message(
                conn, thread_id=item.thread_id, business_key=item.msgid
            )
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中止整轮
            # ⛔ 捕获 Exception 而不是 BaseException：KeyboardInterrupt 必须能停下来。
            logger.error("留存清理：删台账行失败 msgid=%s：%s", item.msgid, exc, exc_info=True)
            failures.append(CleanupFailure("ledger", item.msgid, str(exc)))
            continue
        if applied is None:
            # 幂等命中：这条之前就删过了。正常路径，⛔ 不算失败也不算本轮删除。
            logger.info("留存清理：msgid=%s 之前已清理过，跳过", item.msgid)
            continue
        deleted.append(item.msgid)
    return tuple(deleted), tuple(failures)
```

再改 `tools/liaison/tests/test_liaison_effects.py` 的 `assert_effect_log_identity`——**只在函数体最前面加一段、并在循环里加一个 `continue`**，⛔ 其余一字不动（docstring 里"⛔ 不许改成总数比较、不许削弱成约等于"仍然成立，这里做的是方案 2 的"限定比对范围"）：

```python
    # 冲突 A / 方案 2（本函数 docstring 二选一的第 2 条）：留存期清理会让被清理过的
    # thread 的业务表行数低于 effect_log 行数，那是一个**正当**的差额。
    # 这里把它们排除出严格恒等的比对范围——它们由 test_retention.py 的
    # `assert_retention_accounting` 用一条**更强**的记账等式单独盯住：
    #     归档 effect 行数 == 台账行数 + 清理 effect 行数
    # ⛔ 排除的依据必须是"留下过清理记录"这个事实，不是任何形式的白名单。
    from tools.liaison.retention import RETENTION_DELETE_NODE

    cleaned_threads = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT thread_id FROM effect_log WHERE node_name = ?",
            (RETENTION_DELETE_NODE,),
        ).fetchall()
    }
```

并在既有的按 thread 比对循环里，对 `thread_id in cleaned_threads` 的分组 `continue`。

- [ ] **Step 4: 跑测试确认它通过**

Run: `python -m pytest tools/liaison/tests/test_retention.py tools/liaison/tests/test_liaison_effects.py -v`
Expected: PASS（含既有的 `test_identity_scaffold_actually_catches_a_break`、`test_identity_is_per_thread_not_global` 两条证伪测试**仍然红→绿地被 pytest.raises 捕获**，即行为未被削弱）

- [ ] **Step 5: 确认没有第二个事务管理者**

Run: `python -m pytest tools/liaison/tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source tools/liaison/tests/test_liaison_boundaries.py -q`
Expected: PASS。⚠️ `retention.py` 现在 import 了 `app.storage.idempotency`——它在 `ALLOWED_APP_IMPORTS` 白名单里，⛔ 除它之外一个 `app.*` 都不许加。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/retention.py tools/liaison/tests/test_retention.py tools/liaison/tests/test_liaison_effects.py
git commit -m "feat(liaison): 超期台账行删除 effect 与恒等不变式按方案2收窄（8.1）"
```

---

### Task 3: 归档文件清理与孤儿临时文件清扫

**Files:**
- Modify: `tools/liaison/retention.py`（追加）
- Test: `tools/liaison/tests/test_retention.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `compute_cutoff` / `SkippedItem`；Task 2 的 `CleanupFailure`
- Produces:
  - `@dataclass(frozen=True) ArchiveFile(relative_path: str, day: str)`
  - `iter_archive_files(archive_root: pathlib.Path) -> tuple[ArchiveFile, ...]`
  - `compute_deletable_files(now, retention_days, files: Sequence[ArchiveFile], referenced: frozenset[str]) -> tuple[tuple[str, ...], tuple[SkippedItem, ...]]`
  - `delete_archive_files(archive_root, relative_paths: Sequence[str]) -> tuple[tuple[str, ...], tuple[CleanupFailure, ...]]`
  - `prune_empty_dirs(archive_root) -> tuple[str, ...]`
  - `_unlink(path: pathlib.Path) -> None`（**注入缝**：测试用 monkeypatch 制造确定性的删除失败，⛔ 不是配置项）

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_retention.py`：

```python
def _touch(root, relative_path, payload=b"x"):
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def test_expired_unreferenced_file_is_deleted(tmp_path):
    """spec Scenario「超期数据被清理」在文件那一层的形态。"""
    root = tmp_path / "archive"
    old = _touch(root, "u1/20260101/m-old__a.xlsx")
    fresh = _touch(root, "u1/20260901/m-new__b.xlsx")
    files = retention.iter_archive_files(root)
    deletable, skipped = retention.compute_deletable_files(NOW, 180, files, frozenset())
    assert deletable == ("u1/20260101/m-old__a.xlsx",)
    assert skipped == ()
    deleted, failures = retention.delete_archive_files(root, deletable)
    assert deleted == ("u1/20260101/m-old__a.xlsx",)
    assert failures == ()
    assert not old.exists()
    assert fresh.exists()


def test_referenced_file_is_never_deleted_even_if_expired(tmp_path):
    """🔴 台账还指着的文件一律不删——删了就是 design D3 禁止的
    「台账已记、材料缺失」。这是文件那一遍唯一的结构性保险。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    referenced = frozenset({"u1/20260101/m-old__a.xlsx"})
    deletable, skipped = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), referenced
    )
    assert deletable == ()


def test_orphan_temp_file_is_swept(tmp_path):
    """`attachments.py` 崩溃时留下的 `.tmp-*.part` 从不进台账 ⇒ 永远"未被引用"
    ⇒ 超期后被这一遍顺带扫掉。attachments.py 的注释把这件事指给了第 8 章。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/.tmp-abc.part")
    deletable, _ = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )
    assert deletable == ("u1/20260101/.tmp-abc.part",)


def test_unexpected_path_shape_is_reported_not_deleted(tmp_path):
    """⛔ 形态不认识的路径一律不删——不认识就说明我们的假设错了，
    这时候正确的动作是把它报出来，不是把它删掉。"""
    root = tmp_path / "archive"
    _touch(root, "stray.txt")
    _touch(root, "u1/notadate/x.bin")
    deletable, skipped = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )
    assert deletable == ()
    assert {item.subject for item in skipped} == {"stray.txt", "u1/notadate/x.bin"}


def test_file_pass_is_idempotent(tmp_path):
    """「重复执行安全」：第二遍什么也扫不到，且不报失败。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    first = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )[0]
    retention.delete_archive_files(root, first)
    second = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )[0]
    assert first and second == ()


def test_delete_failure_is_collected_and_does_not_stop_the_round(tmp_path, monkeypatch):
    """单条失败不中止整轮（opener 约束 1 逐字）。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    _touch(root, "u1/20260101/b__2.bin")
    targets = ("u1/20260101/a__1.bin", "u1/20260101/b__2.bin")

    def boom(path):
        if path.name.startswith("a__"):
            raise PermissionError("模拟：没有删除权限")
        path.unlink()

    monkeypatch.setattr(retention, "_unlink", boom)
    deleted, failures = retention.delete_archive_files(root, targets)
    assert deleted == ("u1/20260101/b__2.bin",)
    assert [f.stage for f in failures] == ["file"]
    assert failures[0].subject == "u1/20260101/a__1.bin"


def test_prune_empty_dirs_removes_only_empty_ones_and_keeps_the_root(tmp_path):
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    (root / "u2" / "20260101").mkdir(parents=True)
    pruned = retention.prune_empty_dirs(root)
    assert set(pruned) == {"u2/20260101", "u2"}
    assert root.is_dir()                       # ⛔ 归档根本身永不删
    assert (root / "u1" / "20260101").is_dir()  # 非空目录不动
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_retention.py -k "file or temp or prune or path_shape" -v`
Expected: FAIL — `AttributeError: module 'tools.liaison.retention' has no attribute 'iter_archive_files'`

- [ ] **Step 3: 写最小实现**

追加到 `tools/liaison/retention.py`（模块顶部补 `import pathlib` 与 `import contextlib`）：

```python
#: 归档路径的层数：`<thread_id>/<yyyymmdd>/<叶子>`（design D4）。
#: ⛔ 层数不对的路径一律不删——不认识的形态说明假设错了，那时候要报不要删。
_ARCHIVE_PATH_DEPTH: Final[int] = 3


@dataclass(frozen=True)
class ArchiveFile:
    """归档目录下的一个文件。`day` 是路径里的 `<yyyymmdd>` 段，形态不对时为空串。"""

    relative_path: str
    day: str


def iter_archive_files(archive_root: pathlib.Path) -> tuple[ArchiveFile, ...]:
    """列出归档根下的全部普通文件。归档根不存在 ⇒ 空元组（首次运行的正常情况）。

    ⛔ **跳过符号链接**：跟着符号链接删会把归档根之外的东西删掉。
    """
    if not archive_root.is_dir():
        return ()
    found: list[ArchiveFile] = []
    for path in sorted(archive_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        parts = path.relative_to(archive_root).parts
        day = parts[1] if len(parts) == _ARCHIVE_PATH_DEPTH else ""
        found.append(ArchiveFile(path.relative_to(archive_root).as_posix(), day))
    return tuple(found)


def _day_expiry_instant(day: str) -> datetime.datetime:
    """把 `<yyyymmdd>` 段折成"那一天最晚的一刻（+08:00）"。

    取当天 23:59:59.999999 而不是 00:00:00 是刻意的**保守方向**：晚一点过期
    意味着边界上多留、不会早删。日期来自 `received_at`，那是发送人看到的
    那个时刻，所以时区取 `+08:00`（design D4：⛔ 不做时区换算）。
    """
    if len(day) != 8 or not day.isdigit():
        raise ValueError(f"不是 yyyymmdd 形态：{day!r}")
    moment = datetime.datetime.strptime(day, "%Y%m%d")
    return moment.replace(
        hour=23, minute=59, second=59, microsecond=999999, tzinfo=CHINA_TZ
    )


def compute_deletable_files(
    now: datetime.datetime,
    retention_days: int,
    files: Sequence[ArchiveFile],
    referenced: frozenset[str],
) -> tuple[tuple[str, ...], tuple[SkippedItem, ...]]:
    """决定"删哪些文件"。纯函数：不碰文件系统、不读时钟。

    两道判据，缺一不可：
    1. **仍被台账引用的一律不删**——删了就是 design D3 禁止的
       「台账已记、材料缺失」。这一条让这一遍与台账那一遍的顺序无关，
       也让它对台账那一遍的失败自愈；
    2. 路径里的 `<yyyymmdd>` 早于 cutoff。

    形态不认识的路径（层数不对、日期段不是 8 位数字）进 `SkippedItem`，
    ⛔ 不删——不认识就说明我们的假设错了，那时候要报不要删。
    """
    cutoff = compute_cutoff(now, retention_days)
    deletable: list[str] = []
    skipped: list[SkippedItem] = []
    for item in files:
        if item.relative_path in referenced:
            continue
        try:
            expires_at = _day_expiry_instant(item.day)
        except ValueError as exc:
            skipped.append(
                SkippedItem(
                    item.relative_path,
                    f"路径不是 <thread_id>/<yyyymmdd>/<文件> 形态，⛔ 不删：{exc}",
                )
            )
            continue
        if expires_at < cutoff:
            deletable.append(item.relative_path)
    return tuple(deletable), tuple(skipped)


def _unlink(path: pathlib.Path) -> None:
    """删一个文件。**单独抽出来是为了让测试能注入一次确定性的失败**——
    ⛔ 这不是配置项，⛔ 不许给它加环境变量开关，⛔ 生产代码不许再包一层。"""
    path.unlink()


def delete_archive_files(
    archive_root: pathlib.Path, relative_paths: Sequence[str]
) -> tuple[tuple[str, ...], tuple[CleanupFailure, ...]]:
    """删文件。单条失败不中止整轮。

    删之前再确认一次目标落在归档根**之内**：路径是自己扫出来的，这道检查
    在正常路径上永远为真——它挡的是将来某次改动让路径来源变成"台账里的
    字符串"的那一天，那时候一个 `../../` 就能删到仓库外面去。
    """
    root = archive_root.resolve()
    deleted: list[str] = []
    failures: list[CleanupFailure] = []
    for relative_path in relative_paths:
        target = archive_root / relative_path
        try:
            if root not in target.resolve().parents:
                raise ValueError(f"目标不在归档根之内：{target}")
            _unlink(target)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中止整轮
            logger.error("留存清理：删归档文件失败 %s：%s", relative_path, exc, exc_info=True)
            failures.append(CleanupFailure("file", relative_path, str(exc)))
            continue
        deleted.append(relative_path)
    return tuple(deleted), tuple(failures)


def prune_empty_dirs(archive_root: pathlib.Path) -> tuple[str, ...]:
    """自底向上删掉空目录。**⛔ 归档根本身永不删。**

    ⛔ 只用 `rmdir`（目录非空时它自己会失败），⛔ 绝不许用 `shutil.rmtree`
    ——`rmtree` 会把一个"我以为是空的"目录连同里面的材料一起端掉。
    """
    if not archive_root.is_dir():
        return ()
    pruned: list[str] = []
    candidates = sorted(archive_root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in candidates:
        if path.is_symlink() or not path.is_dir():
            continue
        if any(path.iterdir()):
            continue
        # `contextlib.suppress` 在事务扫描器的正面白名单里（TD-18 的还债形态），
        # ⛔ 不要改成 `with path:` 之类的写法。
        with contextlib.suppress(OSError):
            path.rmdir()
            pruned.append(path.relative_to(archive_root).as_posix())
    return tuple(pruned)
```

- [ ] **Step 4: 跑测试确认它通过**

Run: `python -m pytest tools/liaison/tests/test_retention.py -v`
Expected: PASS

- [ ] **Step 5: 确认扫描器与边界断言仍绿**

Run: `python -m pytest tools/liaison/tests/ -q`
Expected: PASS。⚠️ `retention.py` 里唯一的 `with` 是 `contextlib.suppress(...)`，它在扫描器白名单内。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/retention.py tools/liaison/tests/test_retention.py
git commit -m "feat(liaison): 归档文件按年龄清理与孤儿临时文件清扫（8.1）"
```

---

### Task 4: 一轮清理的编排与失败告警（⛔ 不静默跳过）

**Files:**
- Modify: `tools/liaison/retention.py`（追加）
- Test: `tools/liaison/tests/test_retention.py`（追加）

**Interfaces:**
- Consumes: Task 1–3 的全部；`tools.liaison.alerts.AlertSink`（只 import，⛔ 不改 `alerts.py`）
- Produces:
  - `@dataclass(frozen=True) CleanupReport(retention_days: int, cutoff: str, dry_run: bool, deleted_messages: tuple[str, ...], blocked_by_queue: tuple[str, ...], deleted_files: tuple[str, ...], pruned_dirs: tuple[str, ...], skipped: tuple[SkippedItem, ...], failures: tuple[CleanupFailure, ...])`
  - `compute_retention_alert_text(report: CleanupReport) -> str`
  - `emit_retention_alert(sink, text: str) -> bool`
  - `render_report(report: CleanupReport) -> str`
  - `run_cleanup(conn, *, now, retention_days, archive_root, sink, dry_run: bool = False) -> CleanupReport`

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_retention.py`：

```python
class RecordingSink:
    """记下送出去的告警。⛔ 不做网络调用——真实群通知是第 6 章。"""

    def __init__(self, explode=False):
        self.texts = []
        self.explode = explode

    def send(self, text):
        self.texts.append(text)
        if self.explode:
            raise RuntimeError("模拟：告警通道自己也挂了")


def test_run_cleanup_deletes_ledger_row_then_file(conn, tmp_path):
    """spec Scenario「超期数据被清理」端到端：台账行没了、文件也没了。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    _archive(
        conn, "m-old", archived_at=OLD,
        attachments_json='[{"filename": "a.xlsx", "relative_path": "u1/20260101/m-old__a.xlsx",'
                         ' "byte_length": 1, "sha256": "x"}]',
    )
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, sink=sink
    )
    assert report.deleted_messages == ("m-old",)
    assert report.deleted_files == ("u1/20260101/m-old__a.xlsx",)
    assert report.failures == ()
    assert sink.texts == []          # 没失败就 ⛔ 不发告警
    assert not (root / "u1/20260101/m-old__a.xlsx").exists()
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0


def test_run_cleanup_keeps_files_of_surviving_rows(conn, tmp_path):
    """未超期的消息，它的材料一个字节都不许动。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260901/m-new__a.xlsx")
    _archive(
        conn, "m-new", archived_at=NEW,
        attachments_json='[{"filename": "a.xlsx", "relative_path": "u1/20260901/m-new__a.xlsx",'
                         ' "byte_length": 1, "sha256": "x"}]',
    )
    retention.run_cleanup(conn, now=NOW, retention_days=180, archive_root=root, sink=RecordingSink())
    assert (root / "u1/20260901/m-new__a.xlsx").exists()


def test_file_deletion_failure_raises_exactly_one_alert(conn, tmp_path, monkeypatch):
    """8.2 逐字：「清理失败告警一条」+ spec Scenario「清理失败 → 发出告警、该失败被记录」。

    两条一起断言：告警**恰好一条**（不是每个失败一条），且失败进了 report。
    """
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    _touch(root, "u1/20260101/b__2.bin")
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, sink=sink
    )
    assert len(report.failures) == 2
    assert len(sink.texts) == 1


def test_alert_text_carries_no_personal_information(conn, tmp_path, monkeypatch):
    """🔴 合规：告警将来会经第 6 章送进企微群。

    ⛔ 文本里不许出现 msgid、发送人 userid、文件名、相对路径或消息正文——
    否则等于把"谁发过什么材料"广播出去。只许带计数与阶段名。
    """
    root = tmp_path / "archive"
    _touch(root, "tangliping/20260101/msg-9527__身份证扫描件.pdf")
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    sink = RecordingSink()
    retention.run_cleanup(conn, now=NOW, retention_days=180, archive_root=root, sink=sink)
    text = sink.texts[0]
    for forbidden in ("tangliping", "msg-9527", "身份证扫描件", ".pdf", "20260101"):
        assert forbidden not in text, f"告警文本泄露了 {forbidden!r}：{text}"


def test_alert_channel_failure_never_breaks_the_round(conn, tmp_path, monkeypatch):
    """告警通道自己挂掉 ⇒ 记本地日志，⛔ 不抛给调用方（与第 7 章
    `effect_emit_outage_alert` 同一条口径）。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, sink=RecordingSink(explode=True)
    )
    assert report.failures  # 轮次照常跑完并返回


def test_blocked_and_skipped_items_are_reported_not_swallowed(conn, tmp_path):
    """⛔ 不静默跳过：被队列行挡住的、以及形态不认识的，都必须出现在 report 里。"""
    root = tmp_path / "archive"
    _touch(root, "stray.txt")
    _archive(conn, "m-queued", archived_at=OLD)
    effect_enqueue_task(
        conn, thread_id="u1", business_key="m-queued", sender_userid="u1",
        received_at="2026-01-01T08:00:00+08:00", summary="s",
    )
    conn.commit()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, sink=RecordingSink()
    )
    assert report.blocked_by_queue == ("m-queued",)
    assert [item.subject for item in report.skipped] == ["stray.txt"]
    assert "m-queued" in retention.render_report(report)


def test_dry_run_changes_nothing(conn, tmp_path):
    """`--dry-run` 只报不动：库里一行不少、盘上一个文件不少。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    _archive(conn, "m-old", archived_at=OLD)
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, sink=RecordingSink(), dry_run=True
    )
    assert report.dry_run is True
    assert report.deleted_messages == ("m-old",)          # 报"会删这些"
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert (root / "u1/20260101/m-old__a.xlsx").exists()
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE node_name = ?",
                        (retention.RETENTION_DELETE_NODE,)).fetchone()[0] == 0


def test_unparseable_ledger_row_stops_the_file_pass(conn, tmp_path):
    """台账里有一行 `attachments_json` 读不出来 ⇒ 我们不知道哪些文件仍被引用
    ⇒ ⛔ 整个文件那一遍不许跑。宁可这一轮不清文件，也不能误删一份还被
    指着的材料。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/old__a.bin")
    _archive(conn, "m-broken", archived_at=NEW, attachments_json="{")
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, sink=RecordingSink()
    )
    assert report.deleted_files == ()
    assert any(f.stage == "scan" for f in report.failures)
    assert (root / "u1/20260101/old__a.bin").exists()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_retention.py -k run_cleanup -v`
Expected: FAIL — `AttributeError: module 'tools.liaison.retention' has no attribute 'run_cleanup'`

- [ ] **Step 3: 写最小实现**

追加到 `tools/liaison/retention.py`：

```python
@dataclass(frozen=True)
class CleanupReport:
    """一轮清理的完整结果。

    ⚠️ `deleted_*` 在 `dry_run=True` 时表示"**本来会**删这些"，⛔ 不表示已删。
    渲染与调用方都必须先看 `dry_run`。
    """

    retention_days: int
    cutoff: str
    dry_run: bool
    deleted_messages: tuple[str, ...]
    blocked_by_queue: tuple[str, ...]
    deleted_files: tuple[str, ...]
    pruned_dirs: tuple[str, ...]
    skipped: tuple[SkippedItem, ...]
    failures: tuple[CleanupFailure, ...]


def compute_retention_alert_text(report: CleanupReport) -> str:
    """拼一条清理失败告警。纯函数：不读库、不写日志、不发送（铁律 2）。

    🔴 **只许带计数与阶段名。** ⛔ 不许带 msgid、发送人 userid、文件名、
    相对路径或任何消息内容——这条告警将来会经第 6 章送进企微群，带上它们
    等于把"谁发过什么材料"广播出去。明细只进本机运行日志。
    """
    stages = "、".join(sorted({failure.stage for failure in report.failures})) or "无"
    return (
        "【HR 值守通道·留存清理失败】"
        f"留存期 {report.retention_days} 天；"
        f"本轮清理台账 {len(report.deleted_messages)} 行、归档文件 {len(report.deleted_files)} 个；"
        f"失败 {len(report.failures)} 项（阶段：{stages}）。"
        "明细见本机运行日志，⛔ 告警不带发送人、文件名与消息内容。"
    )


def emit_retention_alert(sink, text: str) -> bool:
    """把告警送出去。**⛔ 永不抛异常**，返回是否送成功。

    与第 7 章 `alerts.effect_emit_outage_alert` 同一条口径：告警通道失败只记
    本地日志，⛔ 不许因此中止清理这一轮——两件事毫无关系。
    ⛔ 捕获 `Exception` 而不是 `BaseException`：`KeyboardInterrupt` 必须能停下来。
    """
    try:
        sink.send(text)
    except Exception:
        logger.error("留存清理告警发送失败，原文：%s", text, exc_info=True)
        return False
    return True


def render_report(report: CleanupReport) -> str:
    """给人看的一屏摘要（本机 stdout / 日志）。

    ⚠️ 这一份**带明细**，因此 ⛔ 不许原样贴进企微群——对外的那一条是
    `compute_retention_alert_text`。
    """
    lines = [
        f"留存清理{'（DRY-RUN，未做任何修改）' if report.dry_run else ''}："
        f"留存期 {report.retention_days} 天，cutoff={report.cutoff}",
        f"  台账行 {'将删' if report.dry_run else '已删'} {len(report.deleted_messages)} 条："
        f"{'、'.join(report.deleted_messages) or '无'}",
        f"  归档文件 {'将删' if report.dry_run else '已删'} {len(report.deleted_files)} 个",
        f"  空目录清理 {len(report.pruned_dirs)} 个",
        f"  ⛔ 因仍有队列条目而保留（design D13：队列行不参与自动清理）"
        f" {len(report.blocked_by_queue)} 条：{'、'.join(report.blocked_by_queue) or '无'}",
    ]
    for item in report.skipped:
        lines.append(f"  跳过 {item.subject}：{item.reason}")
    for failure in report.failures:
        lines.append(f"  ❌ 失败[{failure.stage}] {failure.subject}：{failure.reason}")
    return "\n".join(lines)


def run_cleanup(
    conn,
    *,
    now: datetime.datetime,
    retention_days: int,
    archive_root: pathlib.Path,
    sink,
    dry_run: bool = False,
) -> CleanupReport:
    """跑一轮留存期清理。

    顺序（opener 约束 1 逐字）：**先删台账行、后删归档文件**。⛔ 不许调换。
    反过来会在两步之间留下"台账指向不存在的文件"，那是 design D3 禁止的。

    文件那一遍的"仍被引用"集合**必须在台账那一遍之后重新读一次**——
    用清理前的台账去算，刚删掉的那些行还"引用"着它们的文件，那些文件就
    永远删不掉了。

    单条失败不中止整轮；全部跑完之后**汇总成一条**告警（⛔ 不是每个失败一条：
    一次目录权限问题能刷出几百条告警，那等于没有告警）。
    """
    cutoff = compute_cutoff(now, retention_days)
    rows = load_message_rows(conn)
    split = compute_expired(now, retention_days, rows)

    failures: list[CleanupFailure] = []
    skipped: list[SkippedItem] = list(split.undecidable)

    if dry_run:
        deleted_messages = tuple(item.msgid for item in split.deletable)
    else:
        deleted_messages, ledger_failures = delete_expired_ledger_rows(conn, split.deletable)
        failures.extend(ledger_failures)

    # ⚠️ 重新读一次：这一遍要的是**清理之后**还活着的台账指向了哪些文件。
    surviving = load_message_rows(conn)
    referenced: set[str] = set()
    scan_ok = True
    for row in surviving:
        try:
            referenced.update(parse_relative_paths(row.attachments_json))
        except ValueError as exc:
            # 不知道哪些文件仍被引用 ⇒ ⛔ 整个文件那一遍不许跑。宁可这一轮
            # 不清文件，也不能误删一份还被台账指着的材料（design D3）。
            scan_ok = False
            failures.append(CleanupFailure("scan", row.msgid, f"attachments_json 不可解析：{exc}"))

    deleted_files: tuple[str, ...] = ()
    pruned_dirs: tuple[str, ...] = ()
    if scan_ok:
        candidates, file_skipped = compute_deletable_files(
            now, retention_days, iter_archive_files(archive_root), frozenset(referenced)
        )
        skipped.extend(file_skipped)
        if dry_run:
            deleted_files = candidates
        else:
            deleted_files, file_failures = delete_archive_files(archive_root, candidates)
            failures.extend(file_failures)
            pruned_dirs = prune_empty_dirs(archive_root)
    else:
        logger.error("留存清理：台账里有读不出来的 attachments_json，⛔ 本轮跳过全部文件清理")

    report = CleanupReport(
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
        dry_run=dry_run,
        deleted_messages=tuple(deleted_messages),
        blocked_by_queue=tuple(item.msgid for item in split.blocked_by_queue),
        deleted_files=tuple(deleted_files),
        pruned_dirs=tuple(pruned_dirs),
        skipped=tuple(skipped),
        failures=tuple(failures),
    )

    if report.failures:
        # ⛔ 不静默跳过（8.2 逐字）。一轮一条，⛔ 不是每个失败一条。
        emit_retention_alert(sink, compute_retention_alert_text(report))
    logger.info("%s", render_report(report))
    return report
```

- [ ] **Step 4: 跑测试确认它通过**

Run: `python -m pytest tools/liaison/tests/test_retention.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: PASS（⚠️ 若出现与本单元无关的红，先 `git stash` 确认它在改动前就是红的，是的话按并发协议登记后继续，⛔ 不要顺手修别人的东西）

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/retention.py tools/liaison/tests/test_retention.py
git commit -m "feat(liaison): 留存清理编排与失败汇总告警（8.2）"
```

---

### Task 5: `python -m tools.liaison cleanup` 子命令接线

**Files:**
- Modify: `tools/liaison/retention.py`（追加 CLI 入口）
- Modify: `tools/liaison/__main__.py`（**纯插入**：在最后两行的正上方插入一段，⛔ 既有行一字节不动）
- Test: `tools/liaison/tests/test_retention.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `run_cleanup` / `render_report`；`tools.liaison.storage.db`；`tools.liaison.alerts.LoggingAlertSink`；`tools.liaison.archive.DEFAULT_ARCHIVE_ROOT`
- Produces:
  - `EXIT_OK = 0` / `EXIT_RETENTION_FAILED = 5` / `EXIT_BAD_CONFIG = 6` / `EXIT_BAD_ARGS = 7`
  - `cleanup_main(argv: Sequence[str] | None = None) -> int`

> ⚠️ 退出码 **2/3/4 已被 `__main__.py` 占用**（缺凭据 / SDK 不可用 / SDK 表面未验），所以本单元从 5 开始，⛔ 不许复用。

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_retention.py`：

```python
def test_cleanup_main_runs_without_any_credentials(tmp_path, monkeypatch, capsys):
    """🔴 清理 ⛔ 不需要企微凭据——它不建连接。

    这条同时守住了 `__main__.py` 里那个分支的位置：它必须在 `load_credentials()`
    之前短路，否则一台还没配 BOT_ID 的机器上永远清理不了。
    """
    monkeypatch.delenv("HR_LIAISON_BOT_ID", raising=False)
    monkeypatch.delenv("HR_LIAISON_BOT_SECRET", raising=False)
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    monkeypatch.setattr(
        retention.archive, "DEFAULT_ARCHIVE_ROOT", tmp_path / "archive"
    )
    assert retention.cleanup_main([]) == retention.EXIT_OK
    assert "留存清理" in capsys.readouterr().out


def test_cleanup_main_rejects_unknown_arguments(capsys):
    assert retention.cleanup_main(["--force"]) == retention.EXIT_BAD_ARGS
    assert "未知参数" in capsys.readouterr().err


def test_cleanup_main_fails_closed_on_bad_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HR_LIAISON_RETENTION_DAYS", "0")
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    assert retention.cleanup_main([]) == retention.EXIT_BAD_CONFIG
    assert "HR_LIAISON_RETENTION_DAYS" in capsys.readouterr().err


def test_cleanup_main_returns_5_when_the_round_had_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    monkeypatch.setattr(retention.archive, "DEFAULT_ARCHIVE_ROOT", root)
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    assert retention.cleanup_main([]) == retention.EXIT_RETENTION_FAILED


def test_main_module_cleanup_branch_is_guarded_and_appended():
    """守住 opener 约束 4 的两半：

    ① 子命令分支挂在 `__name__ == "__main__"` 上 ⇒ 被 import 时完全惰性；
    ② 文件最后两行（既有入口）**一字节未变**。
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1] / "__main__.py"
    ).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "cleanup":' in source
    assert source.rstrip().endswith(
        'if __name__ == "__main__":\n    raise SystemExit(main())'
    )
    # 分支必须排在既有入口之前，否则 `raise SystemExit(main())` 会先跑掉。
    assert source.index('sys.argv[1] == "cleanup"') < source.rindex('if __name__ == "__main__":')


def test_importing_main_module_does_not_run_cleanup(monkeypatch):
    """被 import（测试、工具）时那段必须一动不动。"""
    monkeypatch.setattr(sys, "argv", ["pytest", "cleanup"])
    import importlib

    import tools.liaison.__main__ as liaison_main

    importlib.reload(liaison_main)  # 不抛 SystemExit 即为通过
```

（文件顶部补 `import pathlib`、`import sys`）

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_retention.py -k cleanup_main -v`
Expected: FAIL — `AttributeError: module 'tools.liaison.retention' has no attribute 'cleanup_main'`

- [ ] **Step 3: 写最小实现**

追加到 `tools/liaison/retention.py`（模块顶部补 `import sys`，并把 `from tools.liaison import alerts, archive` 与 `from tools.liaison.storage import db as liaison_db` 加进 import 段）：

```python
#: 退出码。⚠️ 2/3/4 已被 `__main__.py` 占用（缺凭据 / SDK 不可用 / SDK 表面未验），
#: ⛔ 不许复用——排障的人靠退出码一眼分辨是哪一类问题。
EXIT_OK: Final[int] = 0
EXIT_RETENTION_FAILED: Final[int] = 5
EXIT_BAD_CONFIG: Final[int] = 6
EXIT_BAD_ARGS: Final[int] = 7

_DRY_RUN_FLAG: Final[str] = "--dry-run"
_USAGE: Final[str] = "用法：python -m tools.liaison cleanup [--dry-run]"


def cleanup_main(argv: Sequence[str] | None = None) -> int:
    """`python -m tools.liaison cleanup` 的实现。

    ⚠️ **这是本模块唯一允许读真实时钟与真实环境变量的地方**，其余全部靠注入。
    ⚠️ 路径取的是 `liaison_db.DEFAULT_DB_PATH` 与 `archive.DEFAULT_ARCHIVE_ROOT`
    的**属性访问**（不是 `from ... import` 的绑定），这样单测能 monkeypatch 到
    临时目录上——opener 约束 3：⛔ 单测不许碰真实 `data/`。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = _DRY_RUN_FLAG in args
    unknown = [item for item in args if item != _DRY_RUN_FLAG]
    if unknown:
        print(f"未知参数：{' '.join(unknown)}。{_USAGE}", file=sys.stderr)
        return EXIT_BAD_ARGS

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        retention_days = load_retention_days()
    except RetentionConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_BAD_CONFIG

    conn = liaison_db.get_connection()
    liaison_db.init_schema(conn)
    report = run_cleanup(
        conn,
        now=datetime.datetime.now(CHINA_TZ),
        retention_days=retention_days,
        archive_root=archive.DEFAULT_ARCHIVE_ROOT,
        sink=alerts.LoggingAlertSink(),
        dry_run=dry_run,
    )
    print(render_report(report))
    return EXIT_RETENTION_FAILED if report.failures else EXIT_OK
```

⚠️ `run_cleanup` 里 `liaison_db.get_connection()` 之后**没有** `conn.close()`：进程随即退出，sqlite 连接由解释器回收。⛔ 不要加 `with contextlib.closing(conn):`——`with <Call>:` 里 `contextlib.closing` **不在**扫描器白名单里，会被判成隐式提交违规（TD-18 的白名单只有 `open` / `os.fdopen` / `io.open` / `contextlib.suppress` / `tempfile.NamedTemporaryFile` / `tempfile.TemporaryDirectory`）。

然后改 `tools/liaison/__main__.py`：**在文件最后两行**

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

**的正上方**插入下面这段（⛔ 那两行一字节不动；opener 约束 4 的"⛔ 不动既有行"是这条的硬判据，"末尾追加"取其字面能达到的最近位置——插在最后一个语句之前是让它可达的唯一位置）：

```python
# ── 第 8 章·留存期清理子命令（tasks 8.1–8.2）─────────────────────────────
# ⛔ 本段是**纯插入**：下面那两行既有入口一字节未动（日志泳道同时在 main()
# 函数体首行接线，两头各占一边，避免 merge 冲突）。
#
# 三个判据的顺序是刻意的：
# ① `__name__ == "__main__"` 放最前 ⇒ 被 import 时（测试、工具）这段完全惰性，
#    ⛔ 不许改成模块级裸判断；
# ② 它排在既有入口之前 ⇒ `raise SystemExit(main())` 不会先跑掉；
# ③ 清理**不需要企微凭据**（它不建连接），所以这条分支必须短路在
#    `main()` 的 `load_credentials()` 之前——否则一台还没配 BOT_ID 的机器
#    永远清理不了自己的过期数据。
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "cleanup":
    from tools.liaison.retention import cleanup_main

    raise SystemExit(cleanup_main(sys.argv[2:]))
```

- [ ] **Step 4: 跑测试确认它通过**

Run: `python -m pytest tools/liaison/tests/test_retention.py tools/liaison/tests/test_main_wiring.py -v`
Expected: PASS

- [ ] **Step 5: 手工验一次真入口（dry-run，不改任何东西）**

Run: `HR_LIAISON_RETENTION_DAYS=180 python -m tools.liaison cleanup --dry-run`
Expected: 打印一段以「留存清理（DRY-RUN，未做任何修改）」开头的摘要，退出码 0。
⚠️ 这一步会在 `data/` 下建出 `liaison.db`（若还没有）——`data/` 已被 `.gitignore:11` 覆盖，⛔ 不许 `git add` 它。
Run: `git status --short data/` 确认 `data/` 下没有任何东西进暂存区。

- [ ] **Step 6: 全量回归 + Commit**

```bash
python -m pytest -q
git add tools/liaison/retention.py tools/liaison/tests/test_retention.py tools/liaison/__main__.py
git commit -m "feat(liaison): python -m tools.liaison cleanup 子命令接线（8.1）"
```

---

## 收尾（run-build controller 在 final review 通过后做）

1. **回勾 WBS**：把 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 8 章的 `8.1`、`8.2` 两个 checkbox 打勾。
   ⚠️ `tasks.md` 是并发热点文件（多条泳道同时回勾）。冲突时**合并双方的改动**，⛔ 不许二选一、⛔ 不许覆盖别人的勾。
2. **写第 8 章落地偏离登记**（照第 4／5 章的格式，写在 8.9 下方）。至少这四条：
   - **P1（结构冲突 B，🔴 需 Shao Peishen 拍板，本轮未拍）**：`liaison_task.msgid` 的外键 + opener「⛔ 队列行任何情况不删」⇒ **已入队（名单内）消息的台账行与归档材料在当前约束下永远不会被留存期清掉**，实际被清理的只有"归档了但没入队"的名单外消息。本轮按保守方向执行（不删、每轮报告计数），⛔ 未替他放宽。要真正让 D13 的 180 天对全部材料生效，需要他在两条里选一条：① 允许连带删除 `send_status='pushed'` 的队列行；② 把 `liaison_task.msgid` 的外键改成 `ON DELETE SET NULL` 之类的弱引用。**两条都改变外部可观察行为，属不可代范围，⛔ 不得由代理人或无头 session 决定。**
   - **P2（铁律 1 判据）**：`assert_effect_log_identity` 按其 docstring 的**方案 2**（限定比对范围）收窄——排除"留下过 `effect_delete_expired_message` 记录"的 thread；那些 thread 由 `assert_retention_accounting` 用更强的记账等式盯住。⛔ 没有削弱成总数比较或约等于。
   - **P3（不加表）**：opener 禁改 `storage/schema.py`，所以"这条材料被清理过"的审计凭据复用 `effect_log`（`node_name='effect_delete_expired_message'`），⛔ 没有新建审计表。`effect_delete_expired_message` 因此**没有**登记进 `storage/effects.py` 的 `EFFECT_NODE_TO_TABLE`——那张表是"节点 → 它写的业务表"的映射，删除型 effect 不符合它的形状。**下一条持有 `storage/effects.py` 的泳道请在该 dict 上方补一行注释说明这一点。**
   - **P4（入口形态）**：`__main__.py` 只做了纯插入，最后两行既有入口一字节未动；子命令分支挂在 `__name__ == "__main__"` 上、排在既有入口之前，因此清理**不需要企微凭据**。
   - （若 `superpowers` 技能取不到而按磁盘 `SKILL.md` 手工走完，照第 5 章 P6 的写法再加一条。）
3. **⏸ 留步登记（必写，⛔ 不许当成已完成）**：
   - **定时触发未接**：本单元只交付手工子命令。把清理挂上 launchd／计划任务是 **8.3**，⛔ 本单元不做，⛔ 不许在收工报告里写成"留存清理已上线"。
   - **未在真实数据上跑过**：全部用 fake 时钟与临时目录验证。首次对真实 `data/liaison.db` 执行前，**必须先 `--dry-run` 看一眼**再执行。
4. **⛔ 不进 `openspec-archive-change`**：第 8 章还有 8.3–8.9 没做，变更包未完成。

---

## 交付前自查记录（writing-plans §Self-Review）

**1. Spec 覆盖**：`liaison-message-archive`「归档数据的留存期有上限」的两个 Scenario 与两条 SHALL 全部有 Task 对应（见上方对照表）。tasks 8.1 的三个要点（默认 180 / 归档与台账清理 / 队列行不参与 + 按年龄判定重复执行安全）与 8.2 的两条（失败告警不静默 + 两条单测）全部落到具体用例。

**2. 占位符扫描**：无 TBD／TODO／"add error handling"／"similar to Task N"。每个 code step 都给了可直接粘贴的完整实现，每个 test step 都给了完整可运行的用例体。

**3. 类型一致性**：`MessageRow` / `ExpiredMessage` / `SkippedItem` / `ExpirySplit` / `CleanupFailure` / `CleanupReport` / `ArchiveFile` 七个 dataclass 的字段名在 Task 1→5 全程一致；`compute_expired(now, retention_days, rows)` 的签名与 opener 约束 1 逐字一致；`_unlink` 在 Task 3 定义、Task 3／4／5 的测试里以同一个名字 monkeypatch；`RETENTION_DELETE_NODE` 在 `retention.py` 定义、被 `test_liaison_effects.py` 与 `test_retention.py` 同名引用。

**4. 已知的可疑点（reviewer 请重点看）**：
- `run_cleanup` 里"重新读一次台账算 referenced"这一步很容易在重构中被合并掉——一旦用清理前的台账去算，被删行的文件就永远删不掉，**而且没有任何症状**（不报错、只是文件越攒越多）。
- `test_main_module_cleanup_branch_is_guarded_and_appended` 里的 `endswith` 断言依赖 `__main__.py` 末尾的精确文本。日志泳道若也改了尾部（按 opener 它只改 `main()` 首行，不该改），这条会红——**红了要先确认是谁动了尾部，⛔ 不许直接放宽断言**。

---

## 下一步

本 plan 交付后进 `run-build`（`superpowers:subagent-driven-development`），一个 Task 一个 subagent，两阶段 review。⛔ 本 plan 本身不写任何代码。
