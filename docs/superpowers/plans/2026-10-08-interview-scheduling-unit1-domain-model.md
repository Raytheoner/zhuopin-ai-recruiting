# 面试排期（interview-scheduling）· 交付单元 U1（面试域模型）Implementation Plan

> 本计划由 `spec-to-plan`（Codex 版）产出。输入＝`openspec/changes/interview-scheduling/`
> 下的 spec + design.md；⛔ 未把 `tasks.md` 当计划输入，只用于确认 U1 章节边界
> （`tasks.md` 第 1 章「面试域模型」，条目 1.1–1.7）。

## 0. 输入与范围

- **变更包**：`openspec/changes/interview-scheduling/`
- **本单元（U1）范围**：`tasks.md` 第 1 章「面试域模型」——六张域表 + 访问留痕表 +
  `stage` 预置 `interview` 行 + 面试官名单维护接口。
- **相关 spec 能力文件**（本单元触碰其「域模型/存储形状」部分）：
  - `specs/interviewer-availability/spec.md`
  - `specs/interview-slot-scheduling/spec.md`
  - `specs/interview-invitation-drafting/spec.md`
  - `specs/candidate-contact-vault/spec.md`
- **design.md 相关 Decisions**：D2（时段来自工作台周视图）、D3（入口只从「进入面试」）、
  D4（四动作四 `effect_*` 节点）、D5（邀约生成/版本化）、D6（联系方式加密保管）、
  D7（面试官身份复用 `hr_account` + `interviewer` 名单表）、D8（提醒只留字段）。
- **本单元不做的**（U2–U5 才做）：冲突检查纯函数、四个 `effect_*` 排期节点、邀约生成/
  回填/门禁接线、联系方式加密/读取/删除、合规断言、`.51` 发版。

## Global Constraints

> 从 `CLAUDE.md` 逐字复制与本单元相关的条目。reviewer 拿它当注意力透镜；缺了会静默漏查。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

> 本单元（U1）是纯域模型，**不引入任何 `effect_*` 节点**；铁律 1 在此约束的是「域模型的
> 存储形状必须让 U2 的四个排期 `effect_*` 节点能落 `effect_log` 同事务」——本单元只建表，
> 不写编排。唯一的「副作用写」是名单维护接口，其幂等口径是「同 `account_id` 重复创建返回
> 既有记录」（业务级幂等，见 Task 7/8）。

### 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。

> 落点：`interview_invitation_draft.ai_generated` / `authorship_marked_by` /
> `authorship_marked_at` 三列是「AI 生成邀约须带标识、标记为人工撰写留痕」的存储层形状；
> 本单元不产生任何 `rejection_record` 写入。

### 部署约束（相关条目逐字）

5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。

> 落点：`candidate_contact` 只存 `phone_enc`/`email_enc` BLOB 密文列，
> `candidate_contact_access_log` 只有 accessor/application_id/purpose/at、无明文列；
> 登录沿用 M2 本地账号（`hr_account` + `hr_session`）。

### 本单元存储层硬约束（tasks.md 第 1 章 + 既有 schema 纪律）

- 六张新表全部 `CREATE TABLE IF NOT EXISTS`，⛔ 不进 `_ADDED_COLUMNS`（加列路径只服务「老库缺列」）。
- `hr_account.role` 是**对既有表加列**：必须同时在 SCHEMA 的 `CREATE TABLE`（新库）与
  `_ADDED_COLUMNS`（老库）两处登记，并纳入 `_DRIFT_GUARDED_TABLES` 漂移守卫。
- `stage` 的 `stage_type` CHECK 从三值放宽到四值：SQLite 无法 `ALTER TABLE` 改 CHECK，
  只能整表重建（与 `db.py` 中 `interview_live_event` 注释同一结论）。

## 1. File Structure（本单元新增/修改）

```
app/storage/db.py                       # 修改：6 张新表 + stage CHECK 放宽 + 重建迁移 + hr_account.role
app/storage/interviewer.py              # 新增：面试官名单维护存储层
app/middleware/auth.py                  # 修改：PROTECTED_PATH_PREFIXES 加 /api/interviewers
app/web/server.py                       # 修改：两个 Pydantic 模型 + _require_hr_role + 3 个路由
tests/test_db_interview_schema.py       # 新增：schema 回归（tasks 1.6）
tests/test_interviewer_api.py           # 新增：名单维护接口契约（tasks 1.7）
tests/test_db_m2_schema.py              # 修改：stage 预置行 3→4、hr_account 列、_ADDED_COLUMNS 集合
tests/test_db_m2_u2_schema.py           # 修改：_ADDED_COLUMNS 新表 stub
tests/test_db_migration.py              # 修改：漂移守卫 + hr_account 历史 DDL
```

---

### Task 1: 新增 `interviewer` 与 `interviewer_availability` 表

**文件**：`app/storage/db.py`

在 SCHEMA 字符串内、`CREATE INDEX IF NOT EXISTS idx_hr_session_account ON hr_session (hr_account_id);`
之后插入以下 DDL（`interviewer` 引用 `hr_account`，放在 hr_session 之后最直观）：

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- 以下属变更包 interview-scheduling（交付单元 U1）。全部新表，走 CREATE TABLE
-- IF NOT EXISTS，**不进 _ADDED_COLUMNS**（加列路径只服务「老库缺列」，新表不需要）。
-- .51 现网 demo.db 既有表一行不改，无数据迁移（design.md Migration Plan 第 1 条）。
-- ─────────────────────────────────────────────────────────────────────────

-- 面试官名单（interviewer-availability spec「面试官记录来自 HR 维护的名单」；
-- design D7）。account_id UNIQUE 外键到 hr_account——每个面试官对应一个可登录账号，
-- 账号与名单行一一对应。interviewable_jobs 存 JSON 数组（可面岗位）。
-- ⛔ 名单 MUST NOT 由 AI 生成或推荐：本表只有 HR 手工维护，无模型调用。
CREATE TABLE IF NOT EXISTS interviewer (
    id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL UNIQUE REFERENCES hr_account(id),
    name TEXT NOT NULL,
    department TEXT,
    interviewable_jobs TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 面试官可用时段（interviewer-availability spec「面试官登记可用时段」；
-- design D2）。时段属于面试官，不属于任何投递。start_at/end_at 是 SQLite
-- datetime('now') 同格式的 UTC 文本。同一面试官时段不重叠由应用层校验＋测试
-- （本表不加 CHECK——SQLite 无法在表级表达「跨行互不重叠」）。
CREATE TABLE IF NOT EXISTS interviewer_availability (
    id TEXT PRIMARY KEY NOT NULL,
    interviewer_id TEXT NOT NULL REFERENCES interviewer(id),
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    note TEXT,
    registered_by TEXT NOT NULL,
    on_behalf INTEGER NOT NULL DEFAULT 0 CHECK (on_behalf IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interviewer_availability_interviewer
    ON interviewer_availability (interviewer_id);
```

**命令与预期输出**：

```bash
python3 -c "from app.storage.db import get_connection, init_schema; import tempfile, pathlib; p=pathlib.Path(tempfile.mkdtemp())/'t.db'; c=get_connection(str(p)); init_schema(c); print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('interviewer','interviewer_availability')\")))"
```

预期输出（顺序无关）：

```
['interviewer', 'interviewer_availability']
```

---

### Task 2: 新增 `interview_slot` 与 `interview_slot_interviewer` 表

**文件**：`app/storage/db.py`

在 Task 1 插入的 DDL 之后继续追加：

```sql
-- 面试场次（interview-slot-scheduling spec「安排/改期/取消/完成」；design D4）。
-- 状态挂在 application 上（CLAUDE.md 数据模型要点：状态属投递不属候选人）。
-- mode/status/invitation_status/kind 的 CHECK 是 spec 枚举在存储层的落点。
-- reminder_sent_count 默认 0 只预留字段，本包不实现任何定时发送（design D8）。
-- kind 一期只有 'human'，M3 若纳入自动排期再加 'ai_live'，本包不预建。
CREATE TABLE IF NOT EXISTS interview_slot (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    round INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('onsite', 'phone', 'online')),
    location_or_link TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (
        status IN ('scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show')
    ),
    cancel_reason TEXT,
    invitation_status TEXT NOT NULL DEFAULT 'none' CHECK (
        invitation_status IN ('none', 'drafted', 'sent', 'confirmed', 'declined', 'reschedule_requested')
    ),
    sent_channel TEXT,
    reminder_sent_count INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'human' CHECK (kind IN ('human')),
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_interview_slot_application ON interview_slot (application_id);

-- 场次与面试官的多对多（interview-slot-scheduling spec「指定面试官一至多位」）。
-- 复合主键天然保证同一场次同一面试官只出现一次。
CREATE TABLE IF NOT EXISTS interview_slot_interviewer (
    interview_slot_id TEXT NOT NULL REFERENCES interview_slot(id),
    interviewer_id TEXT NOT NULL REFERENCES interviewer(id),
    PRIMARY KEY (interview_slot_id, interviewer_id)
);

CREATE INDEX IF NOT EXISTS idx_interview_slot_interviewer_interviewer
    ON interview_slot_interviewer (interviewer_id);
```

**命令与预期输出**：

```bash
python3 -c "from app.storage.db import get_connection, init_schema; import tempfile, pathlib; p=pathlib.Path(tempfile.mkdtemp())/'t.db'; c=get_connection(str(p)); init_schema(c); print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('interview_slot','interview_slot_interviewer')\")))"
```

预期输出：

```
['interview_slot', 'interview_slot_interviewer']
```

---

### Task 3: 新增 `interview_invitation_draft` 与 `invitation_template` 表

**文件**：`app/storage/db.py`

在 Task 2 插入的 DDL 之后继续追加：

```sql
-- 邀约文案草稿（interview-invitation-drafting spec「按场次生成邀约文案」
-- 「人工改写后的标识处置」；design D5）。version 是同一场次内的递增草稿版本，
-- (slot_id, version) 唯一——重复生成产生新版本、旧版永久保留。
-- ai_generated + authorship_marked_by/at 是「AI 生成标识 + 标记为人工撰写留痕」
-- 的存储层落点（合规红线「AI 生成的邀约须带标识」）。
CREATE TABLE IF NOT EXISTS interview_invitation_draft (
    id TEXT PRIMARY KEY NOT NULL,
    slot_id TEXT NOT NULL REFERENCES interview_slot(id),
    version INTEGER NOT NULL,
    template_version TEXT NOT NULL,
    body TEXT NOT NULL,
    ai_generated INTEGER NOT NULL CHECK (ai_generated IN (0, 1)),
    authorship_marked_by TEXT,
    authorship_marked_at TEXT,
    analysis_run_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (slot_id, version)
);

-- 邀约文案模板（interview-invitation-drafting spec「文案模板的来源与版本」；
-- design D5）。version 是单调递增的字符串标签（'v1'/'v2'/...），一版一行、不覆盖。
-- 模板 MUST NOT 含候选人评分/排名/淘汰理由的占位符（由 U3 模板内容测试反证）。
CREATE TABLE IF NOT EXISTS invitation_template (
    version TEXT PRIMARY KEY NOT NULL,
    body TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**命令与预期输出**：

```bash
python3 -c "from app.storage.db import get_connection, init_schema; import tempfile, pathlib; p=pathlib.Path(tempfile.mkdtemp())/'t.db'; c=get_connection(str(p)); init_schema(c); print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('interview_invitation_draft','invitation_template')\")))"
```

预期输出：

```
['interview_invitation_draft', 'invitation_template']
```

---

### Task 4: 新增 `candidate_contact` 与 `candidate_contact_access_log` 表

**文件**：`app/storage/db.py`

在 Task 3 插入的 DDL 之后继续追加：

```sql
-- 候选人面试阶段联系方式（candidate-contact-vault spec；design D6）。
-- application_id 唯一：一份投递只有一条联系方式记录，登记覆盖＝更新同一行。
-- phone_enc/email_enc 存 AES-GCM 密文 BLOB，⛔ 无任何明文列。
-- source 的 CHECK 是 spec「来源 HR 手填/候选人口头确认」枚举的存储层落点。
CREATE TABLE IF NOT EXISTS candidate_contact (
    application_id TEXT PRIMARY KEY NOT NULL REFERENCES application(id),
    phone_enc BLOB,
    email_enc BLOB,
    registered_by TEXT NOT NULL,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL CHECK (source IN ('hr_manual', 'candidate_confirmed')),
    purged_at TEXT,
    purge_reason TEXT
);

-- 联系方式访问留痕（candidate-contact-vault spec「每次读取留痕，留痕失败则
-- 读取失败」；design D6）。⛔ 不建 application_id 外键——与 resume_access_log
-- 同一形态：留痕表按事件记事实，把可写性绑在业务表上会让「留痕写不进去」变成
-- 「读取整个失败」，而「先留痕后返回内容」应由应用层写入顺序保证。
-- 无内容列（spec「留痕本身 MUST NOT 含明文」）。
CREATE TABLE IF NOT EXISTS candidate_contact_access_log (
    id TEXT PRIMARY KEY NOT NULL,
    accessor TEXT NOT NULL,
    application_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_candidate_contact_access_log_application
    ON candidate_contact_access_log (application_id);
```

**命令与预期输出**：

```bash
python3 -c "from app.storage.db import get_connection, init_schema; import tempfile, pathlib; p=pathlib.Path(tempfile.mkdtemp())/'t.db'; c=get_connection(str(p)); init_schema(c); print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('candidate_contact','candidate_contact_access_log')\")))"
```

预期输出：

```
['candidate_contact', 'candidate_contact_access_log']
```

---

### Task 5: `stage` 预置 `interview` 行 + `stage_type` CHECK 放宽（重建迁移）

**文件**：`app/storage/db.py`

**5a. 改 SCHEMA 里的 `stage` 定义**。把现有三值 CHECK 与三条 `INSERT OR IGNORE`
改成四值，并在三条种子行后追加第四条：

```sql
CREATE TABLE IF NOT EXISTS stage (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    stage_type TEXT NOT NULL CHECK (stage_type IN ('initial', 'screening', 'rejected', 'interview'))
);

INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('interview', '面试', 'interview');
```

**5b. 文件顶部补 `logging`**（`_migrate_stage_for_interview` 的回滚失败日志要用）：

```python
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)
```

**5c. 在 `apply_column_migrations` 之后新增重建函数**：

```python
_INTERVIEW_STAGE_ROW = ("interview", "面试", "interview")


def _stage_has_interview_row(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM stage WHERE id = 'interview'").fetchone()
        is not None
    )


def _migrate_stage_for_interview(conn: sqlite3.Connection) -> None:
    """把 interview 预置行加进 stage，必要时放宽 stage_type 的 CHECK。

    新库：SCHEMA 的 CREATE TABLE 已把 CHECK 放宽为四值，第四条 INSERT OR IGNORE
    一步到位，本函数 early return。

    老库（.51 上 M2 已建的三值 stage）：CREATE TABLE IF NOT EXISTS 是彻底的
    no-op，第四条 INSERT OR IGNORE 被旧 CHECK 静默拒掉（不报错、行不出现）。
    SQLite 无法用 ALTER TABLE 改 CHECK 枚举，只能整表重建（本文件
    interview_live_event 表注释同一结论）。
    """
    if _stage_has_interview_row(conn):
        return

    try:
        conn.execute(
            "INSERT INTO stage (id, name, stage_type) VALUES (?, ?, ?)",
            _INTERVIEW_STAGE_ROW,
        )
        conn.commit()
        return
    except sqlite3.IntegrityError:
        pass  # CHECK 仍为三值 → 整表重建

    conn.commit()  # 关掉可能的未决事务后再切 foreign_keys（PRAGMA 在事务内是 no-op）
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            "CREATE TABLE stage_new ("
            "id TEXT PRIMARY KEY NOT NULL, "
            "name TEXT NOT NULL, "
            "stage_type TEXT NOT NULL CHECK ("
            "stage_type IN ('initial', 'screening', 'rejected', 'interview')))"
        )
        conn.execute(
            "INSERT INTO stage_new (id, name, stage_type) "
            "SELECT id, name, stage_type FROM stage"
        )
        conn.execute(
            "INSERT INTO stage_new (id, name, stage_type) VALUES (?, ?, ?)",
            _INTERVIEW_STAGE_ROW,
        )
        conn.execute("DROP TABLE stage")
        conn.execute("ALTER TABLE stage_new RENAME TO stage")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.error(
                "rollback failed while rebuilding stage for interview stage_type",
                exc_info=rollback_exc,
            )
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
```

**5d. 在 `init_schema` 里调用**：

```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    apply_column_migrations(conn)
    _migrate_stage_for_interview(conn)
    conn.commit()
```

**5e. 同步既有 M2 测试**：`tests/test_db_m2_schema.py` 的
`test_stage_table_preloads_three_rows` 改为四行：

```python
def test_stage_table_preloads_four_rows(conn):
    rows = dict(conn.execute("SELECT id, stage_type FROM stage").fetchall())
    assert rows == {
        "initial": "initial",
        "screening": "screening",
        "rejected": "rejected",
        "interview": "interview",
    }
```

（`test_stage_type_check_rejects_unknown_type` 不用改：`offer` 仍不在四值枚举里，
照旧被拒。）

**命令与预期输出**：

```bash
python3 -m pytest tests/test_db_m2_schema.py::test_stage_table_preloads_four_rows tests/test_db_m2_schema.py::test_stage_type_check_rejects_unknown_type -q
```

预期输出：`2 passed`

---

### Task 6: `hr_account` 增加 `role` 列（HR 角色授权基础设施）

> **偏离登记 D-U1-1（技术方案决策，可代）**：design.md D7 只写「面试官身份复用
> `hr_account`，加 `interviewer` 名单表」，未定义「HR 角色」的存储形态。本计划新增
> `hr_account.role`（`CHECK (role IN ('hr','interviewer'))`，默认 `'hr'`）作为
> HR 角色授权的最小实现：现有账号全部是 HR，默认 `'hr'` 保持行为不变；U2 面试官
> 自助登记时段时把面试官账号设成 `'interviewer'`。这是唯一能让 tasks 1.7 的
> 「非 HR 角色 403」有一个可测的非 HR 账号的诚实做法。供 reviewer 确认。

**文件**：`app/storage/db.py`

**6a. 改 SCHEMA 里的 `hr_account` CREATE TABLE**（加 `role` 列）：

```sql
CREATE TABLE IF NOT EXISTS hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'hr' CHECK (role IN ('hr', 'interviewer')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**6b. `_ADDED_COLUMNS` 追加一条**（老库走 `ALTER TABLE ADD COLUMN`）：

```python
    # interview-scheduling U1：HR 角色授权。hr_account 是 M2 已建老表，CREATE
    # TABLE IF NOT EXISTS 对老库无效，必须走加列迁移；默认 'hr' 让 .51 现有
    # 账号（全是 HR）行为与今天完全一致。
    ("hr_account", "role", "TEXT NOT NULL DEFAULT 'hr' CHECK (role IN ('hr', 'interviewer'))"),
```

**6c. 同步既有测试（四处）**：

`tests/test_db_m2_schema.py`：

```python
def test_hr_account_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "hr_account")
    assert _columns(conn, "hr_account") == {
        "id", "username", "password_hash", "password_salt", "role", "created_at",
    }
```

`tests/test_db_m2_schema.py::test_added_columns_tuple_still_only_touches_job_profile`
与 `tests/test_db_migration.py::test_audit_tables_never_enter_the_add_column_path`
的预期集合都加 `"hr_account"`：

```python
    tables_in_added_columns = {row[0] for row in _ADDED_COLUMNS}
    assert tables_in_added_columns == {
        "job_profile",
        "job",
        "resume",
        "job_prep_config",
        "interview_session",
        "hr_account",
    }
```

`tests/test_db_m2_u2_schema.py::test_old_job_table_gains_parse_confidence_threshold_via_migration`
补一个 `hr_account` 空壳（否则 `apply_column_migrations` 遍历到 hr_account 条目时
报 "no such table"）：

```python
    conn.execute("CREATE TABLE hr_account (id TEXT PRIMARY KEY)")
```

**6d. 漂移守卫**（`tests/test_db_migration.py`）：

- `_DRIFT_GUARDED_TABLES` 加 `"hr_account"`：

```python
_DRIFT_GUARDED_TABLES = ("job_profile", "resume", "job_prep_config", "interview_session", "hr_account")
```

- 新增历史 DDL 常量（M2 的 hr_account，无 role）：

```python
_LEGACY_HR_ACCOUNT_DDL = """
CREATE TABLE hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
```

- `_legacy_db` 里建它（与其它历史表同一位置）：

```python
    conn.executescript(
        _LEGACY_JOB_DDL
        + _LEGACY_JOB_PROFILE_DDL
        + _LEGACY_RESUME_DDL
        + _LEGACY_JOB_PREP_CONFIG_DDL
        + _LEGACY_HR_ACCOUNT_DDL
    )
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_db_migration.py tests/test_db_m2_schema.py tests/test_db_m2_u2_schema.py -q
```

预期输出：全绿（这些文件内全部相关测试通过）。

---

### Task 7: 面试官名单维护存储层 `app/storage/interviewer.py`

**文件**：`app/storage/interviewer.py`（新增，整文件）

```python
"""面试官名单维护（interviewer-availability spec「面试官记录来自 HR 维护的
名单」，tasks 1.7）。

⛔ 名单 MUST NOT 由 AI 生成或推荐：本模块只有 HR 手工维护的 CRUD，没有任何
模型调用，也没有任何按候选人/评分做推荐的分支。

幂等：同一 account_id 重复创建返回既有记录（design D7「能登记时段的账号＝
名单内账号」，account_id 有 UNIQUE 约束，账号与名单行一一对应）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid


class InterviewerAccountMissing(ValueError):
    """account_id 在 hr_account 中不存在。"""


class InterviewerNotFound(ValueError):
    """interviewer_id 不存在。"""


_SELECT = (
    "SELECT i.id, i.account_id, i.name, i.department, i.interviewable_jobs, "
    "i.enabled, i.created_at, a.username "
    "FROM interviewer i JOIN hr_account a ON a.id = i.account_id"
)


def _row_to_dict(row) -> dict:
    (
        interviewer_id, account_id, name, department, interviewable_jobs,
        enabled, created_at, username,
    ) = row
    return {
        "id": interviewer_id,
        "account_id": account_id,
        "username": username,
        "name": name,
        "department": department,
        "interviewable_jobs": json.loads(interviewable_jobs or "[]"),
        "enabled": bool(enabled),
        "created_at": created_at,
    }


def list_interviewers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(_SELECT + " ORDER BY i.name COLLATE NOCASE, i.id").fetchall()
    return [_row_to_dict(row) for row in rows]


def _find_by_account_id(conn: sqlite3.Connection, account_id: str) -> dict | None:
    row = conn.execute(_SELECT + " WHERE i.account_id = ?", (account_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _find_by_id(conn: sqlite3.Connection, interviewer_id: str) -> dict | None:
    row = conn.execute(_SELECT + " WHERE i.id = ?", (interviewer_id,)).fetchone()
    return _row_to_dict(row) if row else None


def create_interviewer(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    name: str,
    department: str | None,
    interviewable_jobs: list[str],
    enabled: bool,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("姓名不能为空")
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id 不能为空")

    existing = _find_by_account_id(conn, account_id)
    if existing is not None:
        return existing  # 幂等：同 account_id 返回既有记录

    account = conn.execute(
        "SELECT id FROM hr_account WHERE id = ?", (account_id,)
    ).fetchone()
    if account is None:
        raise InterviewerAccountMissing(f"account_id={account_id} 不存在")

    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer "
        "(id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            interviewer_id,
            account_id,
            name,
            department,
            json.dumps(interviewable_jobs, ensure_ascii=False),
            1 if enabled else 0,
        ),
    )
    conn.commit()
    created = _find_by_id(conn, interviewer_id)
    assert created is not None
    return created


def update_interviewer(
    conn: sqlite3.Connection,
    *,
    interviewer_id: str,
    updates: dict,
) -> dict:
    existing = _find_by_id(conn, interviewer_id)
    if existing is None:
        raise InterviewerNotFound(f"interviewer_id={interviewer_id} 不存在")

    # 列名只来自下面的固定白名单，⛔ 不来自用户输入，不存在注入面。
    set_clauses: list[str] = []
    params: list[object] = []

    if "name" in updates:
        name = (updates["name"] or "").strip()
        if not name:
            raise ValueError("姓名不能为空")
        set_clauses.append("name = ?")
        params.append(name)
    if "department" in updates:
        set_clauses.append("department = ?")
        params.append(updates["department"])
    if "interviewable_jobs" in updates:
        set_clauses.append("interviewable_jobs = ?")
        params.append(json.dumps(updates["interviewable_jobs"], ensure_ascii=False))
    if "enabled" in updates:
        set_clauses.append("enabled = ?")
        params.append(1 if updates["enabled"] else 0)

    if set_clauses:
        params.append(interviewer_id)
        conn.execute(
            f"UPDATE interviewer SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        conn.commit()

    return _find_by_id(conn, interviewer_id)
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interviewer_api.py -q
```

预期输出：全绿（该文件内的存储层行为通过 API 测试覆盖）。

---

### Task 8: 面试官名单维护接口 `GET/POST/PATCH /api/interviewers`（HR 角色）

**文件**：`app/middleware/auth.py`、`app/web/server.py`

**8a. `app/middleware/auth.py`**——`PROTECTED_PATH_PREFIXES` 加 `/api/interviewers`：

```python
PROTECTED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/candidates",
    "/api/resumes",
    "/api/applications",
    "/api/rejections",
    "/api/interviewers",
)
```

**8b. `app/web/server.py`**——顶部 import 加：

```python
from app.storage.interviewer import (
    InterviewerAccountMissing,
    InterviewerNotFound,
    create_interviewer,
    list_interviewers,
    update_interviewer,
)
```

**8c. 模块级 Pydantic 模型**（放在 `VerifyCodeRequest` 之后）：

```python
class InterviewerCreateRequest(BaseModel):
    account_id: str
    name: str
    department: str | None = None
    interviewable_jobs: list[str] = []
    enabled: bool = True


class InterviewerPatchRequest(BaseModel):
    name: str | None = None
    department: str | None = None
    interviewable_jobs: list[str] | None = None
    enabled: bool | None = None
```

**8d. `create_app` 内新增 HR 角色闸与三条路由**（放在 login/logout 路由之后）：

```python
    def _require_hr_role(request: Request) -> None:
        """面试官名单维护的 HR 角色闸。

        与 _require_hr_login 的区别：登录只证明「你是谁」，这里是「你是不是 HR」。
        现阶段只给 HR 开放名单维护；面试官账号（role='interviewer'）登录后仍然 403。
        """
        auth = getattr(request.state, "auth", None)
        if not getattr(auth, "authenticated", False):
            raise HTTPException(status_code=401, detail="未登录")
        username = getattr(auth, "user_id", None)
        row = conn.execute(
            "SELECT role FROM hr_account WHERE username = ?", (username,)
        ).fetchone()
        if row is None or row[0] != "hr":
            raise HTTPException(status_code=403, detail="仅 HR 角色可维护面试官名单")

    @router.get("/api/interviewers")
    def interviewers_list(request: Request):
        _require_hr_role(request)
        return list_interviewers(conn)

    @router.post("/api/interviewers", status_code=201)
    def interviewers_create(req: InterviewerCreateRequest, request: Request):
        _require_hr_role(request)
        try:
            return create_interviewer(
                conn,
                account_id=req.account_id,
                name=req.name,
                department=req.department,
                interviewable_jobs=req.interviewable_jobs,
                enabled=req.enabled,
            )
        except InterviewerAccountMissing as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.patch("/api/interviewers/{interviewer_id}")
    def interviewers_update(
        interviewer_id: str, req: InterviewerPatchRequest, request: Request
    ):
        _require_hr_role(request)
        updates = req.model_dump(exclude_unset=True)
        try:
            return update_interviewer(
                conn, interviewer_id=interviewer_id, updates=updates
            )
        except InterviewerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interviewer_api.py -q
```

预期输出：全绿（含「未登录 401」「非 HR 角色 403」「幂等创建」「404 分支」）。

---

### Task 9: `tests/test_db_interview_schema.py`

**文件**：`tests/test_db_interview_schema.py`（新增，整文件）

> 老库基线复用 `tests/fixtures/zp51_demo_db_schema_pre_m3.sql`——它是 M2 状态
> 快照：`stage` 三值 CHECK、`hr_account` 无 role、无任何面试域表，恰好就是
> interview-scheduling U1 落地前的老库形态。

```python
"""interview-scheduling U1 的 schema 回归测试（tasks 1.6）。

覆盖三类判据：
- 新库建表齐全（8 张面试域表 + stage.interview 预置行 + hr_account.role 列）
- 复制 M2 结构的老库升级后既有表/既有行不改、既有三行 stage 不变
- 全部 CHECK 反证（非法 status/mode/kind/source/enabled 被拒）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.storage.db import get_connection, init_schema


_INTERVIEW_TABLES = (
    "interviewer",
    "interviewer_availability",
    "interview_slot",
    "interview_slot_interviewer",
    "interview_invitation_draft",
    "invitation_template",
    "candidate_contact",
    "candidate_contact_access_log",
)

_LEGACY_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "zp51_demo_db_schema_pre_m3.sql"
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn, name):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _fresh_conn(tmp_path):
    c = get_connection(str(tmp_path / "fresh.db"))
    init_schema(c)
    return c


def _legacy_db(tmp_path):
    c = get_connection(str(tmp_path / "legacy.db"))
    c.executescript(_LEGACY_FIXTURE_PATH.read_text(encoding="utf-8"))
    # 快照只含 DDL，stage 三条种子行需手工补（与 test_db_m3_schema.py 同一理由）。
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected')")
    c.execute("INSERT INTO job (id, title, status) VALUES ('old-job', '底层软件工程师', 'approved')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('old-cand', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('old-resume', 'old-job', 'synthetic', 'a.pdf', 'sha-old', 'hr-1')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('old-app', 'old-cand', 'old-job', 'old-resume', 'initial')"
    )
    c.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('old-acct', 'tangliping', 'h', 's')"
    )
    c.commit()
    return c


# ── 新库 ─────────────────────────────────────────────────────────────────


def test_fresh_db_has_all_interview_tables(tmp_path):
    c = _fresh_conn(tmp_path)
    for table in _INTERVIEW_TABLES:
        assert _table_exists(c, table), f"{table} 应该在 init_schema 后出现"


def test_stage_preloads_interview_row(tmp_path):
    c = _fresh_conn(tmp_path)
    rows = dict(c.execute("SELECT id, stage_type FROM stage").fetchall())
    assert rows == {
        "initial": "initial",
        "screening": "screening",
        "rejected": "rejected",
        "interview": "interview",
    }


def test_stage_check_rejects_unknown_type(tmp_path):
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO stage (id, name, stage_type) VALUES ('offer', '发 offer', 'offer')")


def test_hr_account_has_role_column_with_hr_default(tmp_path):
    c = _fresh_conn(tmp_path)
    assert "role" in _columns(c, "hr_account")
    c.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('a1', 'alice', 'h', 's')"
    )
    c.commit()
    assert c.execute("SELECT role FROM hr_account WHERE username='alice'").fetchone()[0] == "hr"


# ── 老库升级 ─────────────────────────────────────────────────────────────


def test_legacy_db_gains_all_interview_tables_after_init_schema(tmp_path):
    c = _legacy_db(tmp_path)
    init_schema(c)
    for table in _INTERVIEW_TABLES:
        assert _table_exists(c, table)


def test_legacy_db_existing_rows_are_untouched(tmp_path):
    """既有 job/candidate/resume/application 行内容不变；stage 原三行保留并新增
    interview 行；hr_account 只新增 role 列且老行默认 'hr'。"""
    c = _legacy_db(tmp_path)
    before_job = c.execute("SELECT id, title, status FROM job WHERE id='old-job'").fetchone()
    before_candidate = c.execute("SELECT id, name FROM candidate WHERE id='old-cand'").fetchone()
    before_application = c.execute(
        "SELECT id, current_stage_id FROM application WHERE id='old-app'"
    ).fetchone()
    before_stage_rows = sorted(c.execute("SELECT id, stage_type FROM stage").fetchall())
    before_hr_cols = _columns(c, "hr_account")

    init_schema(c)

    assert before_job == c.execute(
        "SELECT id, title, status FROM job WHERE id='old-job'"
    ).fetchone()
    assert before_candidate == c.execute(
        "SELECT id, name FROM candidate WHERE id='old-cand'"
    ).fetchone()
    assert before_application == c.execute(
        "SELECT id, current_stage_id FROM application WHERE id='old-app'"
    ).fetchone()

    stage_rows = sorted(c.execute("SELECT id, stage_type FROM stage").fetchall())
    assert before_stage_rows <= stage_rows
    assert ("interview", "interview") in stage_rows

    assert _columns(c, "hr_account") == before_hr_cols | {"role"}
    assert c.execute("SELECT role FROM hr_account WHERE id='old-acct'").fetchone()[0] == "hr"


# ── CHECK 反证 ───────────────────────────────────────────────────────────


def _insert_slot(c, *, status="scheduled", mode="onsite", kind="human"):
    c.execute("INSERT INTO job (id, title) VALUES ('j', '岗位')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c', '候选人')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r', 'j', 'synthetic', 'a.pdf', 'sha', 'hr')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a', 'c', 'j', 'r', 'interview')"
    )
    c.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, status, kind) "
        "VALUES ('s', 'a', 1, '2026-10-09 14:00', '2026-10-09 15:00', ?, ?, ?)",
        (mode, status, kind),
    )


@pytest.mark.parametrize(
    "status", ["scheduled", "rescheduled", "cancelled", "completed", "no_show"]
)
def test_interview_slot_accepts_valid_status(tmp_path, status):
    c = _fresh_conn(tmp_path)
    _insert_slot(c, status=status)
    c.commit()


def test_interview_slot_rejects_invalid_status(tmp_path):
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(c, status="bogus")


def test_interview_slot_rejects_invalid_mode(tmp_path):
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(c, mode="bogus")


def test_interview_slot_rejects_invalid_kind(tmp_path):
    c = _fresh_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(c, kind="ai_live")


def test_candidate_contact_rejects_invalid_source(tmp_path):
    c = _fresh_conn(tmp_path)
    c.execute("INSERT INTO job (id, title) VALUES ('j', '岗位')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c', '候选人')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r', 'j', 'synthetic', 'a.pdf', 'sha', 'hr')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a', 'c', 'j', 'r', 'interview')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO candidate_contact (application_id, registered_by, source) "
            "VALUES ('a', 'hr-1', 'bogus')"
        )


def test_interviewer_rejects_invalid_enabled(tmp_path):
    c = _fresh_conn(tmp_path)
    c.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('a1', 'alice', 'h', 's')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO interviewer (id, account_id, name, enabled) "
            "VALUES ('i1', 'a1', '张三', 2)"
        )


def test_interview_invitation_draft_version_is_unique_per_slot(tmp_path):
    c = _fresh_conn(tmp_path)
    c.execute("INSERT INTO job (id, title) VALUES ('j', '岗位')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c', '候选人')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r', 'j', 'synthetic', 'a.pdf', 'sha', 'hr')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a', 'c', 'j', 'r', 'interview')"
    )
    c.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s', 'a', 1, '2026-10-09 14:00', '2026-10-09 15:00', 'onsite')"
    )
    c.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "VALUES ('d1', 's', 1, 'v1', '正文', 1)"
    )
    c.commit()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO interview_invitation_draft "
            "(id, slot_id, version, template_version, body, ai_generated) "
            "VALUES ('d2', 's', 1, 'v1', '另一版', 1)"
        )
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_db_interview_schema.py -q
```

预期输出：全绿。

---

### Task 10: `tests/test_interviewer_api.py`（名单维护接口契约）

**文件**：`tests/test_interviewer_api.py`（新增，整文件）

```python
"""面试官名单维护接口（tasks 1.7）的 API 契约测试。

鉴权三层：未登录 → 401；已登录但 role != 'hr' → 403；role = 'hr' → 放行。
"""
from __future__ import annotations

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _account(conn, username: str, role: str = "hr") -> str:
    account_id = upsert_account(conn, username=username, password="testpass123")
    if role != "hr":
        conn.execute(
            "UPDATE hr_account SET role = ? WHERE username = ?", (role, username)
        )
        conn.commit()
    return account_id


def _login(client, conn, account_id: str) -> None:
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def test_interviewers_requires_login(make_test_client):
    client, conn = make_test_client()
    assert client.get("/api/interviewers").status_code == 401


def test_interviewers_rejects_non_hr_role(make_test_client):
    client, conn = make_test_client()
    interviewer_id = _account(conn, "interviewer-1", role="interviewer")
    _login(client, conn, interviewer_id)

    assert client.get("/api/interviewers").status_code == 403
    assert client.post(
        "/api/interviewers", json={"account_id": "any", "name": "张三"}
    ).status_code == 403


def test_hr_can_create_and_list_interviewer(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1")
    target_id = _account(conn, "interviewee-1", role="interviewer")
    _login(client, conn, hr_id)

    created = client.post("/api/interviewers", json={
        "account_id": target_id,
        "name": "汤丽萍",
        "department": "人事部",
        "interviewable_jobs": ["嵌入式软件工程师"],
        "enabled": True,
    })
    assert created.status_code == 201
    body = created.json()
    assert body["account_id"] == target_id
    assert body["name"] == "汤丽萍"
    assert body["department"] == "人事部"
    assert body["interviewable_jobs"] == ["嵌入式软件工程师"]
    assert body["enabled"] is True

    listing = client.get("/api/interviewers")
    assert listing.status_code == 200
    assert [row["account_id"] for row in listing.json()] == [target_id]


def test_create_interviewer_is_idempotent_by_account_id(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1")
    target_id = _account(conn, "interviewee-1", role="interviewer")
    _login(client, conn, hr_id)

    payload = {"account_id": target_id, "name": "汤丽萍", "interviewable_jobs": ["嵌入式"]}
    first = client.post("/api/interviewers", json=payload)
    second = client.post("/api/interviewers", json={**payload, "name": "改名不应生效"})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["name"] == "汤丽萍"


def test_create_interviewer_missing_account_returns_404(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, _account(conn, "hr-1"))
    resp = client.post("/api/interviewers", json={
        "account_id": "no-such-account", "name": "张三"
    })
    assert resp.status_code == 404


def test_patch_updates_only_provided_fields(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1")
    target_id = _account(conn, "interviewee-1", role="interviewer")
    _login(client, conn, hr_id)

    created = client.post("/api/interviewers", json={
        "account_id": target_id,
        "name": "汤丽萍",
        "department": "人事部",
        "interviewable_jobs": ["嵌入式"],
        "enabled": True,
    }).json()

    resp = client.patch(
        f"/api/interviewers/{created['id']}", json={"department": "行政部"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["department"] == "行政部"
    assert body["name"] == "汤丽萍"
    assert body["interviewable_jobs"] == ["嵌入式"]


def test_patch_missing_interviewer_returns_404(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, _account(conn, "hr-1"))
    resp = client.patch("/api/interviewers/no-such-id", json={"name": "张三"})
    assert resp.status_code == 404
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interviewer_api.py -q
```

预期输出：全绿。

---

## 2. 交付前自查

- [x] 任务标题全部三级 `### Task N: `（`grep -c '^### Task '` 应等于 10）。
- [x] 有 **Global Constraints** 段，内容与 CLAUDE.md 一致（工程铁律 1、合规红线两条、
      部署约束 5 逐字，并标注 U1 落点）。
- [x] spec 每条 `### Requirement:` 都能指到至少一个 Task（见下「spec 覆盖对照」）。
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出。
- [x] 无 TBD / TODO /「适当处理错误」占位符。
- [x] 前后 Task 的表名、列名、函数签名一致。
- [x] 本单元无 `effect_*` 节点；唯一副作用写（名单 CRUD）的幂等口径为「同
      `account_id` 重复创建返回既有记录」（Task 7/8）。

## 3. spec 覆盖对照

| Requirement（能力文件） | 本单元落点（Task） | 行为归属 |
|---|---|---|
| interviewer-availability「面试官登记可用时段」 | Task 1（`interviewer_availability` 表） | 登记/撤销/重叠校验在 U2 |
| interviewer-availability「面试官只能维护自己的时段」 | Task 6（`hr_account.role`） | 读写本人时段在 U2 |
| interviewer-availability「当日安排视图」 | Task 2（`interview_slot`/`interview_slot_interviewer`） | 视图在 U2 |
| interviewer-availability「面试官记录来自 HR 维护的名单」 | Task 1/7/8（`interviewer` 表 + 名单 API） | 名单维护＝本单元 |
| interview-slot-scheduling「进入排期的唯一入口」 | Task 5（`stage.interview`） | 入口校验在 U2 |
| interview-slot-scheduling「安排一场面试」 | Task 2（`interview_slot`） | 动作在 U2 |
| interview-slot-scheduling「冲突检查」 | Task 1/2（`interviewer_availability`/`interview_slot` 形状） | 纯函数在 U2 |
| interview-slot-scheduling「改期与取消」 | Task 2（`status`/`cancel_reason`/`updated_at`） | 动作在 U2 |
| interview-slot-scheduling「标记完成与未出席」 | Task 2（`status ∈ completed/no_show`） | 动作在 U2 |
| interview-slot-scheduling「排期动作的幂等与流转事实守恒」 | Task 2（`interview_slot` 供 U2 `effect_*` 同事务写） | 节点在 U2 |
| interview-slot-scheduling「提醒字段预留但不发送」 | Task 2（`reminder_sent_count DEFAULT 0`） | 定时发送随 TD-11 |
| interview-invitation-drafting「按场次生成邀约文案」 | Task 3（`interview_invitation_draft`） | 生成在 U3 |
| interview-invitation-drafting「HR 复制发送与结果回填」 | Task 2（`invitation_status`/`sent_channel`） | 回填在 U3 |
| interview-invitation-drafting「人工改写后的标识处置」 | Task 3（`ai_generated`/`authorship_marked_by/at`） | 处置在 U3 |
| interview-invitation-drafting「系统外发一律经既有门禁」 | Task 2/3（场次/草稿形状） | 接线在 U3 |
| interview-invitation-drafting「文案模板的来源与版本」 | Task 3（`invitation_template`） | 模板维护在 U3 |
| candidate-contact-vault「联系方式保管功能受合规开关约束」 | Task 4（`candidate_contact` 形状） | 开关在 U4 |
| candidate-contact-vault「只在面试阶段及之后允许登记」 | Task 4/5（`candidate_contact` + `stage.interview`） | 门槛校验在 U4 |
| candidate-contact-vault「字段级加密与密钥不落库」 | Task 4（`phone_enc`/`email_enc` BLOB） | 加密在 U4 |
| candidate-contact-vault「每次读取留痕，留痕失败则读取失败」 | Task 4（`candidate_contact_access_log`） | 读取在 U4 |
| candidate-contact-vault「到期删除与终止删除」 | Task 4（`purged_at`/`purge_reason`） | 删除在 U4 |
| candidate-contact-vault「面试阶段前的哈希口径不变」 | Task 4（不建明文列、不改 `candidate` 哈希） | 断言在 U4 |

## 4. 本计划相对 `tasks.md` / `design.md` 的偏离登记

1. **D-U1-1（技术方案决策，可代）**：新增 `hr_account.role`（`hr`/`interviewer`，
   默认 `hr`）。design.md D7 未定义角色存储，tasks 1.7 又要求「非 HR 角色 403」；
   本计划以加列方式补齐，U2 复用。详见 Task 6。
2. **D-U1-2（实现细节）**：`stage` 的 CHECK 放宽必须整表重建（SQLite 无法
   `ALTER TABLE` 改 CHECK）。tasks 1.5 只说「预置行追加 interview」，未点明重建；
   本计划补 `_migrate_stage_for_interview`。详见 Task 5。
3. **D-U1-3（测试基线）**：tasks 1.6 说「复制 M2 结构的老库」，本计划复用现成的
   `tests/fixtures/zp51_demo_db_schema_pre_m3.sql`（M2 状态快照）作为老库基线，
   不新建快照文件。详见 Task 9。

## 5. 提取验证记录（`spec-to-plan` 第 6 步，本计划写作时已做的最小核验）

- 已在本会话沙箱实测：`stage` 三值 CHECK → 四值 CHECK 的整表重建在带
  `application` 外键引用的情况下成功，`PRAGMA foreign_key_check` 为空、既有行保留。
- 已实测：`ALTER TABLE hr_account ADD COLUMN role ... CHECK (...)` 在 SQLite 合法且
  老行默认 `'hr'`。
- 已实测：8 张新表 DDL 全部可 `CREATE`，`interview_slot.status/mode/kind` 的
  非法取值被 CHECK 拒。

> 完整「把计划里全部代码块原样提取、独立 venv 跑全量测试」的第 6 步**不在本
> plan-writing 泳道执行**（本泳道只产出 plan，⛔ 不改 `app/`/`tests/`）。该步由
> `run-build` 在执行本计划时完成。

## 6. 完成判据（`tasks.md` 第 1 章 checkbox 在这些全部成立后才勾）

- `pytest tests/test_db_interview_schema.py tests/test_interviewer_api.py -q` 全绿；
- `pytest tests/test_db_migration.py tests/test_db_m2_schema.py tests/test_db_m2_u2_schema.py -q` 全绿；
- `python3 -m pytest -q` 全量无回归。
