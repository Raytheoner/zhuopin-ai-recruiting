# U3 邀约与同意流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 voice-structured-interview 变更包第 4 章 U3——一次性邀请链接的签发/校验/续入、邀约经既有外发门禁投递、候选人双同意留痕、手机号验证码弱核验（含短信通道未配置的人工转告降级）、真实候选人开闸与联系方式来源接线，以及 HR 签发页与候选人端 e2e。

**Architecture:** 新增 `app/graph/invite_nodes.py` 承载本单元全部 `compute_*`/`effect_*` 节点（与 `app/graph/interview_prep_nodes.py` 同一形态：Web 通道下的"挂起等人确认"由 HTTP 端点直接调用普通 Python 函数实现，不建真实 LangGraph `interrupt()`）；`app/storage/live_interview_gate.py`、`app/storage/consent_terms.py`、`app/storage/contact_source.py` 三个小模块各自独占一个结构性前置（开闸求值、条款版本、联系方式来源）；`app/web/server.py` 加 HR 签发页与候选人端路由；`config/consent/` 下新增条款文本。所有写库动作走 `idempotent_effect` 装饰器，幂等键逐字对齐 tasks.md 4.1/4.4/4.6/4.7 给出的公式。

**Tech Stack:** Python、FastAPI、SQLite（`app/storage/db.py` 已有 `interview_session`/`interview_consent`/`identity_check`/`interview_turn` 等表）、pytest（真实 SQLite `tmp_path`，不 mock DB）。

**Spec:** `openspec/changes/voice-structured-interview/specs/interview-invite-and-consent/spec.md`；设计决策见 `openspec/changes/voice-structured-interview/design.md` D5（一次性链接＋双同意）、D6（邀约只经 `deliver_candidate_message()`）、D13（手机号验证码弱核验）、D14（真实候选人开闸）、D18（留存起步值 90 天）、D20（幂等键与节点序列）。WBS：`openspec/changes/voice-structured-interview/tasks.md` 第 4 章 4.1–4.11。

## Global Constraints

以下条目逐字复制自 `CLAUDE.md`「工程铁律」与「合规红线」两节，与本单元相关的部分标注适用方式，不适用的标注理由——`subagent-driven-development` 的 reviewer 必须用这段作注意力透镜。

**工程铁律**

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引；幂等记录与业务写必须在同一个事务、同一个连接里提交。**适用**：本单元每个 `effect_*` 节点（`effect_issue_invite`、`effect_open_invite`、`effect_issue_resume_token`、`effect_consume_resume_token`、`effect_deliver_invitation`、`effect_record_consent`、`effect_issue_verification_code`、`effect_verify_phone`、`effect_display_verification_code_to_hr`、`effect_log_invite_access_denied`、`effect_create_interview_session`）全部套 `app.storage.idempotency.idempotent_effect`，thread_id 统一取 `session_id`（design D20），business_key 逐条对齐 tasks.md 给出的公式。reviewer 判据：每个 `effect_*` 节点的写库语句都在被装饰函数体内，函数体内不出现 `conn.commit()`。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。**适用但有例外**：本单元没有新的 L3 Agent（不调用 LLM）；`compute_*`/纯编排函数（如 `open_invite`、`verify_phone_code`）可以做只读查库（与 `interview_prep_nodes.py::compute_prep` 同一先例），但任何写库语句必须封在 `effect_*` 节点里，编排函数只负责校验、组装参数、调用 `effect_*`。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。**不适用**：本单元不调用任何 LLM。
4. **每条 `criterion_score` 必须有 `evidence_ref`**。**不适用**：本单元不写 `criterion_score`。
5. **`temperature=0`；模型版本优先显式锁定**。**不适用**：本单元不调用 LLM 网关。
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。**不适用**：本单元不涉及企微回调；候选人邀约走既有 `deliver_candidate_message()` 出站通道，不是入站回调。
7. **`langgraph >= 1.0.10`**。**适用**：本单元不升级/不降级该依赖，`requirements.txt` 不改动此行。

**合规红线**

- **AI 只做排序推荐，不做自动淘汰。** **不适用**：本单元不产生淘汰判定；候选人拒绝同意时场次转 `abandoned` 是候选人自己的选择，不是 AI 淘汰。
- **禁止人脸/表情分析。** **适用**：`identity_check` 表结构性无图像列（U1 已建，本单元不改表结构，只写 `result='skipped'` 一行）；本单元代码不引入任何图像/视频采集。
- **AI 生成的 JD、拒信、邀约须带标识。** **适用**：邀约正文必须带 AI 生成标识，Task 7 复用 `app.agents.jd_agent.AI_LABEL_TEMPLATE`（design D6 原文："复用 jd_agent 的机制，不另写一套"）。
- **模型全部走境内，简历数据不出境。** **不适用**：本单元不调用任何模型、不传输简历数据。
- **绝不用历史录用结果做监督信号。** **不适用**：本单元不训练/不调用评分模型。
- **候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。** **适用，本单元核心交付**：Task 4/5 的令牌签发与校验就是这条红线的实现——32 字节随机令牌、库内只存哈希、首次打开即失效（状态机转移，非重复使用）。
- **主观描述不得进入硬门槛规则。** **不适用**：本单元不涉及候选人评价规则。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/storage/db.py` | 改 | 新增 `interview_invite_event` 表；`_ADDED_COLUMNS` 追加 `job_prep_config.invite_expiry_days`、`interview_session.phone_code_hash`、`interview_session.phone_code_expires_at` |
| `config/consent/ai_interview-v1.md` | 建 | AI 面试同意条款占位文本（标"待法务 #2 定稿"） |
| `config/consent/identity_check-v1.md` | 建 | 身份核验同意条款占位文本 |
| `app/storage/consent_terms.py` | 建 | 条款文件加载与版本号解析 |
| `app/storage/live_interview_gate.py` | 建 | `is_live_interview_enabled()`，D14 真实候选人开闸 |
| `app/storage/contact_source.py` | 建 | `candidate-contact-vault`（姊妹包 interview-scheduling）可用性探测与手机号读取的容错适配 |
| `app/graph/invite_nodes.py` | 建 | 本单元全部 `compute_*`/`effect_*`/编排函数 |
| `app/web/server.py` | 改 | HR 签发页与候选人端路由 |
| `app/web/static/interview_invite_issue.html` | 建 | HR 签发页（选投递→签发→展示链接/验证码/状态） |
| `app/web/static/interview_consent.html` | 建 | 候选人端页面（打开链接→双同意→验证码） |
| `tests/test_db_m3_schema.py` | 改 | 新表/新列的建表齐全性与老库升级断言 |
| `tests/test_consent_terms.py` | 建 | 条款加载与版本解析 |
| `tests/test_live_interview_gate.py` | 建 | 开闸求值 |
| `tests/test_contact_source.py` | 建 | vault 不可用时 fail-closed |
| `tests/test_invite_nodes.py` | 建 | 令牌签发/校验/续入/邀约投递/同意/验证码全部节点 |
| `tests/test_invite_web_e2e.py` | 建 | HR 签发页 + 候选人端路由 e2e |

---

### Task 1: 数据库新增（`interview_invite_event` 表 ＋ 三个新列）

**Files:**
- Modify: `app/storage/db.py`
- Test: `tests/test_db_m3_schema.py`

**Interfaces:**
- Produces: 表 `interview_invite_event(id, session_id, event_type, detail, at)`；`job_prep_config.invite_expiry_days`；`interview_session.phone_code_hash`、`interview_session.phone_code_expires_at`。后续所有 Task 的 `effect_*` 节点都写这张表或这些列。

- [ ] **Step 1: 读现有 schema 定位插入点**

Run: `grep -n "job_prep_config\|_ADDED_COLUMNS" app/storage/db.py`

Expected: 看到 `job_prep_config` 的 `CREATE TABLE` 块（`CREATE TABLE IF NOT EXISTS job_prep_config (` 起始行）与 `_ADDED_COLUMNS` 元组定义行号，供下面精确定位插入。

- [ ] **Step 2: 写失败测试——新表与新列应存在**

在 `tests/test_db_m3_schema.py` 末尾追加：

```python
def test_interview_invite_event_table_exists(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(interview_invite_event)")}
    assert cols == {"id", "session_id", "event_type", "detail", "at"}


def test_interview_invite_event_type_check_rejects_unknown_value(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class) "
        "VALUES ('s1', 'a1', 1, '2027-01-01T00:00:00+00:00', 'v1-90d', 'internal_sim')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_invite_event (id, session_id, event_type) "
            "VALUES ('e1', 's1', 'not_a_real_event_type')"
        )


def test_job_prep_config_invite_expiry_days_default(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'ECU 工程师', 'open')")
    conn.execute("INSERT INTO job_prep_config (job_id) VALUES ('j1')")
    row = conn.execute(
        "SELECT invite_expiry_days FROM job_prep_config WHERE job_id = 'j1'"
    ).fetchone()
    assert row[0] == 7


def test_interview_session_phone_code_columns_exist(tmp_path):
    conn = get_connection(str(tmp_path / "new.db"))
    init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(interview_session)")}
    assert {"phone_code_hash", "phone_code_expires_at"} <= cols


def test_legacy_db_gains_new_columns_via_migration(tmp_path):
    """老库（U1/U2 已建表，没有本单元新列）跑 apply_column_migrations 后补齐，
    既有行一列不丢（工程铁律「老库升级后既有表一行不改」的延伸：只加列不改值）。"""
    db_path = str(tmp_path / "legacy.db")
    conn = get_connection(db_path)
    init_schema(conn)
    # 模拟老库：手工删掉本单元要加的三列，重建成 U2 时代的形状
    conn.execute("ALTER TABLE job_prep_config RENAME TO job_prep_config_old")
    conn.execute(
        "CREATE TABLE job_prep_config (job_id TEXT PRIMARY KEY, "
        "prep_curve TEXT NOT NULL DEFAULT 'easy_to_hard', "
        "prep_question_count INTEGER NOT NULL DEFAULT 10)"
    )
    conn.execute(
        "INSERT INTO job_prep_config (job_id, prep_curve, prep_question_count) "
        "SELECT job_id, prep_curve, prep_question_count FROM job_prep_config_old"
    )
    conn.execute("DROP TABLE job_prep_config_old")
    conn.commit()
    added = apply_column_migrations(conn)
    assert "job_prep_config.invite_expiry_days" in added
    cols = {row[1] for row in conn.execute("PRAGMA table_info(job_prep_config)")}
    assert "invite_expiry_days" in cols
```

在文件顶部 import 区确认已有 `import sqlite3`、`import pytest`、`from app.storage.db import get_connection, init_schema, apply_column_migrations`（没有则补）。

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_db_m3_schema.py -k "interview_invite_event or invite_expiry_days or phone_code_columns or legacy_db_gains" -v`
Expected: 5 个测试全部 FAIL（`sqlite3.OperationalError: no such table: interview_invite_event` 等）。

- [ ] **Step 4: 实现——加表加列**

在 `app/storage/db.py` 的 `job_prep_config` 那个 `CREATE TABLE` 块之后（`_SCHEMA_SQL` 三引号字符串内，`job_prep_config` 表定义结束的 `);` 之后、字符串结尾 `"""` 之前）插入：

```sql

-- 邀约与同意的场次级事件留痕（voice-structured-interview U3 tasks
-- 4.1/4.2/4.3/4.6/4.7/4.8）。⛔ 不与 interview_access_log 合并：
-- interview_access_log 记的是"面试官/HR 读取录音/转写/ScoreCard 内容"这四类
-- 固定入口（interview-recording-retention spec），本表记的是"场次生命周期里
-- 发生了什么事件"，语义不同、增长速率不同，合并会让内容访问留痕表的
-- CHECK 枚举无限膨胀。
CREATE TABLE IF NOT EXISTS interview_invite_event (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES interview_session(id),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'issued', 'reissued', 'opened', 'expired_access', 'reused_access',
        'resume_issued', 'consent_declined', 'verification_locked',
        'manual_handoff', 'delivered', 'code_displayed_to_hr'
    )),
    detail TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interview_invite_event_session
    ON interview_invite_event (session_id);
```

在 `_ADDED_COLUMNS` 元组末尾（`("resume", "raw_text", "TEXT"),` 之后）追加三行：

```python
    # voice-structured-interview U3 tasks 4.1：邀约有效期是岗位级配置，
    # 默认 7 天。job_prep_config 在 U2 已建表并可能已存在于任何一个 U2 之后
    # 建的库里，CREATE TABLE IF NOT EXISTS 对已存在的表无效，必须走加列迁移。
    ("job_prep_config", "invite_expiry_days", "INTEGER NOT NULL DEFAULT 7"),
    # tasks 4.7：验证码本身不落明文，只存哈希与过期时刻；phone_attempts 与
    # phone_verified_at 已在 U1 建好，这两列是本单元独有的新增。
    ("interview_session", "phone_code_hash", "TEXT"),
    ("interview_session", "phone_code_expires_at", "TEXT"),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_db_m3_schema.py -k "interview_invite_event or invite_expiry_days or phone_code_columns or legacy_db_gains" -v`
Expected: 5 个测试全部 PASS。

- [ ] **Step 6: 跑全量 schema 测试防回归**

Run: `pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS（含已有的老库升级、CHECK 反证等用例）。

- [ ] **Step 7: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py
git commit -m "feat(voice-interview): U3 新增 interview_invite_event 表与三个迁移列"
```

---

### Task 2: 同意条款文件与版本解析

**Files:**
- Create: `config/consent/ai_interview-v1.md`
- Create: `config/consent/identity_check-v1.md`
- Create: `app/storage/consent_terms.py`
- Test: `tests/test_consent_terms.py`

**Interfaces:**
- Consumes: 无（本任务不依赖前面任务）
- Produces: `ConsentTerm(kind, version, text)` dataclass；`load_consent_term(kind: str, version: str) -> ConsentTerm`；`latest_consent_version(kind: str) -> str`；`KNOWN_CONSENT_KINDS = ("ai_interview", "identity_check")`。Task 8（同意记录）与 Task 10（候选人端页面）消费这三样。

- [ ] **Step 1: 写条款占位文本**

`config/consent/ai_interview-v1.md`:

```markdown
# AI 面试同意条款（v1，待法务 #2 定稿）

本条款为占位文本，正式版本以法务合规验收 #2 结论为准；升版后新签发场次自动使用
新版本号，已同意的旧场次记录保持原版本号不变。

在开始 AI 结构化面试前，请确认您已知悉并同意以下事项：

1. **录音留存**：本次面试的语音、文本与评分数据将被留存，留存期限见
   `docs/compliance/`（起步值 90 天，终值以法务复核结论为准）。
2. **AI 评分仅作参考**：系统会对您的作答生成结构化评分供面试官参考，
   **AI 不会自动做出淘汰决定**，最终结果由人工面试官确认。
3. **可申请人工面试替代**：如您不希望参与 AI 面试，可联系 HR 申请改约
   人工面试，不会因此影响您的应聘结果。

勾选即表示您已阅读并同意以上内容。
```

`config/consent/identity_check-v1.md`:

```markdown
# 身份核验同意条款（v1，待法务 #2 定稿）

本条款为占位文本，正式版本以法务合规验收 #2 结论为准。

为确认面试候选人身份，系统将向您发送一次性验证码（经短信或由 HR 人工转告），
请在有效期内输入以完成核验。本环节：

1. **不进行人脸识别或活体检测**——一期身份核验仅为手机号验证码弱核验。
2. **核验结果与评分链路物理隔离**——核验数据不会进入您的 AI 面试评分。
3. 验证码有效期 5 分钟，连续输错超过上限次数将锁定本场次，请联系 HR 重新
   获取。

勾选即表示您已阅读并同意以上内容。
```

- [ ] **Step 2: 写失败测试**

`tests/test_consent_terms.py`:

```python
"""app/storage/consent_terms.py：同意条款文件加载与版本号解析
（voice-structured-interview U3 tasks 4.5）。"""

import pytest

from app.storage.consent_terms import (
    ConsentTerm,
    ConsentTermNotFoundError,
    KNOWN_CONSENT_KINDS,
    latest_consent_version,
    load_consent_term,
)


def test_known_consent_kinds():
    assert KNOWN_CONSENT_KINDS == ("ai_interview", "identity_check")


@pytest.mark.parametrize("kind", KNOWN_CONSENT_KINDS)
def test_latest_consent_version_is_v1(kind):
    assert latest_consent_version(kind) == "v1"


@pytest.mark.parametrize("kind", KNOWN_CONSENT_KINDS)
def test_load_consent_term_returns_nonempty_text(kind):
    version = latest_consent_version(kind)
    term = load_consent_term(kind, version)
    assert isinstance(term, ConsentTerm)
    assert term.kind == kind
    assert term.version == version
    assert len(term.text) > 0


def test_load_consent_term_unknown_kind_raises():
    with pytest.raises(ConsentTermNotFoundError):
        load_consent_term("not_a_kind", "v1")


def test_load_consent_term_unknown_version_raises():
    with pytest.raises(ConsentTermNotFoundError):
        load_consent_term("ai_interview", "v999")


def test_latest_consent_version_picks_max_version_number(tmp_path, monkeypatch):
    """升版场景：目录下同时有 v1 与 v2，latest 取数值最大的那个（不是字符串
    排序——避免 'v10' 字符串序小于 'v2' 的坑）。"""
    import app.storage.consent_terms as module

    consent_dir = tmp_path / "consent"
    consent_dir.mkdir()
    (consent_dir / "ai_interview-v1.md").write_text("v1 text", encoding="utf-8")
    (consent_dir / "ai_interview-v10.md").write_text("v10 text", encoding="utf-8")
    (consent_dir / "ai_interview-v2.md").write_text("v2 text", encoding="utf-8")
    monkeypatch.setattr(module, "CONSENT_DIR", consent_dir)

    assert module.latest_consent_version("ai_interview") == "v10"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_consent_terms.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.storage.consent_terms'`）。

- [ ] **Step 4: 实现**

`app/storage/consent_terms.py`:

```python
"""同意条款文件的加载与版本号解析（voice-structured-interview U3 tasks 4.5，
design D5）。条款文本本身不进代码仓库的"正式内容"——`config/consent/*.md`
是运营可编辑的配置文件，本模块只负责发现版本号与读取文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CONSENT_DIR = Path("config/consent")

KNOWN_CONSENT_KINDS: tuple[str, ...] = ("ai_interview", "identity_check")

_FILENAME_PATTERN = re.compile(r"^(?P<kind>[a-z_]+)-v(?P<version>\d+)\.md$")


class ConsentTermNotFoundError(Exception):
    """请求的 kind/version 组合没有对应的条款文件。"""


@dataclass(frozen=True)
class ConsentTerm:
    kind: str
    version: str
    text: str


def _available_versions(kind: str) -> list[int]:
    if kind not in KNOWN_CONSENT_KINDS:
        return []
    versions: list[int] = []
    for path in CONSENT_DIR.glob(f"{kind}-v*.md"):
        match = _FILENAME_PATTERN.match(path.name)
        if match and match.group("kind") == kind:
            versions.append(int(match.group("version")))
    return versions


def latest_consent_version(kind: str) -> str:
    """该 kind 目前文件名里版本号数值最大的那个，格式化回 'v<N>'。
    ⛔ 不按字符串排序——'v10' 字符串序小于 'v2'。"""
    versions = _available_versions(kind)
    if not versions:
        raise ConsentTermNotFoundError(f"未找到 kind={kind!r} 的任何条款文件")
    return f"v{max(versions)}"


def load_consent_term(kind: str, version: str) -> ConsentTerm:
    if kind not in KNOWN_CONSENT_KINDS:
        raise ConsentTermNotFoundError(f"未知的条款类型: {kind!r}")
    path = CONSENT_DIR / f"{kind}-{version}.md"
    if not path.is_file():
        raise ConsentTermNotFoundError(f"未找到条款文件: {path}")
    return ConsentTerm(kind=kind, version=version, text=path.read_text(encoding="utf-8"))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_consent_terms.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add config/consent/ai_interview-v1.md config/consent/identity_check-v1.md \
  app/storage/consent_terms.py tests/test_consent_terms.py
git commit -m "feat(voice-interview): U3 同意条款文件与版本解析"
```

---

### Task 3: 真实候选人开闸 ＋ 联系方式来源适配

**Files:**
- Modify: `app/config.py`
- Create: `app/storage/live_interview_gate.py`
- Create: `app/storage/contact_source.py`
- Test: `tests/test_live_interview_gate.py`
- Test: `tests/test_contact_source.py`

**Interfaces:**
- Consumes: `app.config.get_settings`（既有）
- Produces: `is_live_interview_enabled() -> bool`（Task 4 的场次创建消费）；`is_contact_vault_available() -> bool`、`resolve_live_candidate_phone(conn, application_id) -> str | None`（Task 4 消费）

- [ ] **Step 1: 写失败测试——开闸求值**

`tests/test_live_interview_gate.py`:

```python
"""app/storage/live_interview_gate.py：真实候选人开闸（design D14，tasks 4.9）。
默认关闭、每次求值、AND 合规验收 #2 签认文件存在。"""

import pytest

from app.config import get_settings
from app.storage.live_interview_gate import is_live_interview_enabled


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("LIVE_INTERVIEW_ENABLED", raising=False)
    yield
    get_settings.cache_clear()


def test_default_is_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert is_live_interview_enabled() is False


def test_env_true_without_signoff_file_still_closed(tmp_path, monkeypatch):
    """开关开了，但合规验收 #2 签认文件不存在 ⇒ 仍然关闭（AND 语义）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_INTERVIEW_ENABLED", "1")
    assert is_live_interview_enabled() is False


def test_env_true_with_signoff_file_present_opens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_INTERVIEW_ENABLED", "1")
    signoff_dir = tmp_path / "docs" / "compliance"
    signoff_dir.mkdir(parents=True)
    (signoff_dir / "m3-interview-signoff.md").write_text("签认", encoding="utf-8")
    assert is_live_interview_enabled() is True


def test_never_raises_on_broken_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_INTERVIEW_ENABLED", "not-a-real-bool-but-fine-since-truthy-check")
    # 非标准取值不在 _TRUTHY 集合里 ⇒ 按关闭处理，不抛异常
    assert is_live_interview_enabled() is False
```

- [ ] **Step 2: 写失败测试——contact_source**

`tests/test_contact_source.py`:

```python
"""app/storage/contact_source.py：candidate-contact-vault（姊妹变更包
interview-scheduling 交付）的可用性探测与读取适配（tasks 4.10）。
本包不建 vault 模块本身，vault 不存在时一律按"未开启"处理（fail-closed）。
"""

import sqlite3

from app.storage.contact_source import is_contact_vault_available, resolve_live_candidate_phone


def test_vault_module_absent_reports_unavailable():
    # 开发环境此时 app.storage.contact_vault 尚不存在（interview-scheduling
    # 未交付）——is_contact_vault_available 必须优雅返回 False，不抛 ImportError。
    assert is_contact_vault_available() is False


def test_resolve_live_candidate_phone_returns_none_when_vault_unavailable(tmp_path):
    conn = sqlite3.connect(":memory:")
    assert resolve_live_candidate_phone(conn, application_id="a1") is None
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_live_interview_gate.py tests/test_contact_source.py -v`
Expected: 全部 FAIL（`ModuleNotFoundError`）。

- [ ] **Step 4: 实现 config.py 新字段**

在 `app/config.py` 的 `Settings` 类内、`live_resume_intake_enabled` 字段定义之后追加：

```python

    # 真实候选人开闸（design D14）。默认关闭；每次签发时求值，⛔ 不缓存——
    # 唯一合法入口是 app/storage/live_interview_gate.py 的
    # is_live_interview_enabled()，业务代码不得直接读这个字段。
    live_interview_enabled: bool = False

    # 合规验收 #2 的签认文件路径。存在即视为"法务已签认"，本单元不校验
    # 文件内容——内容校验是人的事，代码只做"文件存在与否"这一层结构性门禁
    # （与 live_resume_gate.py 的 _access_log_probe 同一层级：结构性前置，
    # 不替代人工判断）。
    m3_compliance_signoff_path: str = "docs/compliance/m3-interview-signoff.md"
```

- [ ] **Step 5: 实现 live_interview_gate.py**

```python
"""真实候选人开闸（voice-structured-interview design D14，tasks 4.9）。

与 app/storage/live_resume_gate.py 同一口径：默认关、每次求值、⛔ 不缓存、
⛔ 不抛出任何异常。本闸多一个 AND 前置——合规验收 #2 签认文件存在，这是
"法务复核未完成前真实候选人开闸在结构上不可能打开"的落点。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ENV_VAR = "LIVE_INTERVIEW_ENABLED"


def is_live_interview_enabled() -> bool:
    """真实候选人开闸求值。⛔ 绝不抛出任何异常——任何一步出错，结果都是 False。"""
    try:
        return _evaluate()
    except Exception:
        logger.exception("真实候选人开闸求值过程出错，按关闭处理")
        return False


def _evaluate() -> bool:
    if not _base_switch():
        return False
    return _compliance_signoff_exists()


def _base_switch() -> bool:
    raw_env = os.environ.get(_ENV_VAR)
    if raw_env is not None:
        return raw_env.strip().lower() in _TRUTHY
    try:
        settings = get_settings()
    except Exception:
        return False
    return settings.live_interview_enabled


def _compliance_signoff_exists() -> bool:
    try:
        settings = get_settings()
        path = Path(settings.m3_compliance_signoff_path)
    except Exception:
        return False
    return path.is_file()
```

- [ ] **Step 6: 实现 contact_source.py**

```python
"""candidate-contact-vault（姊妹变更包 interview-scheduling U4 交付）的可用性
探测与读取适配（voice-structured-interview design D13/D14，tasks 4.10）。

⚠️ 本模块不建 vault 本身——vault 属于 interview-scheduling 变更包
（`app/storage/contact_vault.py::is_candidate_contact_vault_enabled()` /
`read_contact()`），本包只是它的第一个调用方之一。写这份计划时该模块尚未
交付（`openspec/changes/interview-scheduling/tasks.md` 4.1-4.4 未勾选）。

⛔ 不在模块顶部 `from app.storage.contact_vault import ...`——那会在 vault
未交付期间让本包的任何 import 链路直接 ImportError。改用运行时惰性 import，
"模块不存在"与"模块存在但开关关闭"两种情况统一折成 False/None
（fail-closed：未知即当作不可用，与 tasks 4.10「vault 开关关 ⇒ 签发被拒」
同一口径）。
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def is_contact_vault_available() -> bool:
    """vault 模块已交付且开关开启才算可用。模块不存在、开关关闭、或求值过程
    出任何异常，一律返回 False。"""
    try:
        from app.storage.contact_vault import is_candidate_contact_vault_enabled
    except ImportError:
        return False
    try:
        return is_candidate_contact_vault_enabled()
    except Exception:
        logger.exception("candidate-contact-vault 开关求值异常，按不可用处理")
        return False


def resolve_live_candidate_phone(conn: sqlite3.Connection, *, application_id: str) -> str | None:
    """live 场次专用：从 vault 读取该投递的候选人手机号（拍平字符串）。
    vault 不可用（未交付/未开启/读取异常）一律返回 None——调用方据此拒绝
    签发 live 场次邀请（tasks 4.10）。internal_sim 场次不应调用本函数
    （spec「internal_sim 场次不经 vault」）。"""
    if not is_contact_vault_available():
        return None
    try:
        from app.storage.contact_vault import read_contact
    except ImportError:
        return None
    try:
        contact = read_contact(
            conn,
            application_id=application_id,
            accessor="system:voice-interview-invite",
            purpose="live_interview_invite",
        )
    except Exception:
        logger.exception(
            "application_id=%s 读取 candidate-contact-vault 失败，按不可用处理",
            application_id,
        )
        return None
    return getattr(contact, "phone", None)
```

- [ ] **Step 7: 跑测试确认通过**

Run: `pytest tests/test_live_interview_gate.py tests/test_contact_source.py -v`
Expected: 全部 PASS。

- [ ] **Step 8: Commit**

```bash
git add app/config.py app/storage/live_interview_gate.py app/storage/contact_source.py \
  tests/test_live_interview_gate.py tests/test_contact_source.py
git commit -m "feat(voice-interview): U3 真实候选人开闸与 contact-vault 容错适配"
```

---

### Task 4: 场次创建 ＋ 令牌签发（`effect_create_interview_session` / `effect_issue_invite`）

**Files:**
- Create: `app/graph/invite_nodes.py`（本任务先落模块骨架与前两个节点，后续任务在同一文件里继续追加）
- Test: `tests/test_invite_nodes.py`（本任务先落文件与前两组用例，后续任务追加）

**Interfaces:**
- Consumes: `app.storage.idempotency.idempotent_effect`；`app.storage.live_interview_gate.is_live_interview_enabled`；`app.storage.contact_source.is_contact_vault_available`
- Produces: `RETENTION_POLICY_VERSION`、`RETENTION_DAYS`、`compute_new_session()`、`effect_create_interview_session()`、`LiveInterviewNotEnabledError`、`ContactVaultUnavailableError`、`assert_invite_issuance_allowed()`、`generate_invite_token()`、`load_invite_expiry_days()`、`compute_new_invite_token()`、`effect_issue_invite()`。Task 5 起的所有节点都在同一文件里追加，消费这些前缀。

- [ ] **Step 1: 写失败测试**

`tests/test_invite_nodes.py`（新建）:

```python
"""app/graph/invite_nodes.py：U3 邀约与同意流程的全部 compute_*/effect_*
节点（voice-structured-interview tasks 4.1-4.11）。数据库用真实 SQLite
（tmp_path），与 tests/test_interview_prep_nodes.py 同一风格。"""

from __future__ import annotations

import sqlite3

import pytest

from app.graph.invite_nodes import (
    ContactVaultUnavailableError,
    LiveInterviewNotEnabledError,
    assert_invite_issuance_allowed,
    compute_new_invite_token,
    compute_new_session,
    effect_create_interview_session,
    effect_issue_invite,
    load_invite_expiry_days,
)
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "test.db"))
    init_schema(c)
    c.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'ECU 工程师', 'open')")
    c.execute(
        "INSERT INTO application (id, job_id, resume_id, stage_id, status) "
        "VALUES ('app1', 'j1', 'r1', 'st1', 'active')"
    )
    c.commit()
    return c


def _freeze_prep(conn, application_id="app1", version=1):
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, status) "
        "VALUES ('snap1', ?, ?, 1, 'frozen')",
        (application_id, version),
    )
    conn.commit()


class TestSessionCreation:
    def test_compute_new_session_defaults_pending_and_90_day_retention(self, conn):
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        assert session["application_id"] == "app1"
        assert session["sample_class"] == "internal_sim"
        assert session["retention_policy_version"] == "v1-90d"
        assert session["id"]

    def test_effect_create_interview_session_persists_pending_status(self, conn):
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        session_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        row = conn.execute(
            "SELECT status, sample_class FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        assert row == ("pending", "internal_sim")

    def test_effect_create_interview_session_idempotent_same_request_id(self, conn):
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        first_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        second = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        assert second is None  # 幂等短路
        count = conn.execute(
            "SELECT COUNT(*) FROM interview_session WHERE application_id = 'app1'"
        ).fetchone()[0]
        assert count == 1


class TestIssuanceGate:
    def test_internal_sim_allowed_without_any_gate(self, conn):
        assert_invite_issuance_allowed(conn, sample_class="internal_sim")  # 不抛异常

    def test_live_rejected_when_live_interview_disabled(self, conn, monkeypatch):
        import app.graph.invite_nodes as module

        monkeypatch.setattr(module, "is_live_interview_enabled", lambda: False)
        with pytest.raises(LiveInterviewNotEnabledError):
            assert_invite_issuance_allowed(conn, sample_class="live")

    def test_live_rejected_when_vault_unavailable_even_if_gate_open(self, conn, monkeypatch):
        import app.graph.invite_nodes as module

        monkeypatch.setattr(module, "is_live_interview_enabled", lambda: True)
        monkeypatch.setattr(module, "is_contact_vault_available", lambda: False)
        with pytest.raises(ContactVaultUnavailableError):
            assert_invite_issuance_allowed(conn, sample_class="live")

    def test_live_allowed_when_both_gates_open(self, conn, monkeypatch):
        import app.graph.invite_nodes as module

        monkeypatch.setattr(module, "is_live_interview_enabled", lambda: True)
        monkeypatch.setattr(module, "is_contact_vault_available", lambda: True)
        assert_invite_issuance_allowed(conn, sample_class="live")  # 不抛异常


class TestInviteIssuance:
    def test_load_invite_expiry_days_defaults_to_7(self, conn):
        assert load_invite_expiry_days(conn, "j1") == 7

    def test_load_invite_expiry_days_reads_job_prep_config(self, conn):
        conn.execute(
            "INSERT INTO job_prep_config (job_id, invite_expiry_days) VALUES ('j1', 3)"
        )
        conn.commit()
        assert load_invite_expiry_days(conn, "j1") == 3

    def test_compute_new_invite_token_is_url_safe_and_hashable(self, conn):
        token, token_hash, expires_at = compute_new_invite_token(conn, job_id="j1")
        assert len(token) > 20
        assert len(token_hash) == 64  # sha256 hex
        assert expires_at  # ISO8601 字符串

    def test_effect_issue_invite_persists_hash_not_plaintext(self, conn):
        _freeze_prep(conn)
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        session_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        token, token_hash, expires_at = compute_new_invite_token(conn, job_id="j1")
        effect_issue_invite(
            conn, thread_id=session_id, business_key=token_hash,
            session_id=session_id, token_hash=token_hash, expires_at=expires_at,
        )
        row = conn.execute(
            "SELECT invite_token_hash, invite_expires_at FROM interview_session WHERE id = ?",
            (session_id,),
        ).fetchone()
        assert row[0] == token_hash
        assert token not in (row[0] or "")

    def test_effect_issue_invite_reissue_invalidates_old_token_and_logs(self, conn):
        _freeze_prep(conn)
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        session_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        _, hash1, exp1 = compute_new_invite_token(conn, job_id="j1")
        effect_issue_invite(
            conn, thread_id=session_id, business_key=hash1,
            session_id=session_id, token_hash=hash1, expires_at=exp1,
        )
        _, hash2, exp2 = compute_new_invite_token(conn, job_id="j1")
        effect_issue_invite(
            conn, thread_id=session_id, business_key=hash2,
            session_id=session_id, token_hash=hash2, expires_at=exp2,
        )
        row = conn.execute(
            "SELECT invite_token_hash FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        assert row[0] == hash2  # 旧哈希已被覆盖，旧令牌查不到

        events = conn.execute(
            "SELECT event_type FROM interview_invite_event WHERE session_id = ? ORDER BY at",
            (session_id,),
        ).fetchall()
        assert [e[0] for e in events] == ["issued", "reissued"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invite_nodes.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.graph.invite_nodes'`）。

- [ ] **Step 3: 实现模块骨架与本任务两个节点**

`app/graph/invite_nodes.py`（新建，本文件后续任务继续追加内容，不要覆盖）:

```python
"""U3 邀约与同意流程 L4 编排层（voice-structured-interview tasks 4.1-4.11,
design D5/D6/D13/D14/D20）。

与 app/graph/interview_prep_nodes.py 同一形态：Web 通道下"挂起等人确认"由
HTTP 端点直接调用普通 Python 函数达成，不建真实 LangGraph interrupt()
（2026-08-26 判例，见 interview_prep_nodes.py 模块 docstring）。

thread_id 统一取 session_id（design D20 invite 子图定义）。本文件按 tasks.md
4.1→4.11 顺序组织：场次创建/令牌签发/令牌校验/续入令牌/邀约投递/同意/
验证码/HR 展示。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.storage.contact_source import is_contact_vault_available
from app.storage.idempotency import idempotent_effect
from app.storage.live_interview_gate import is_live_interview_enabled

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32
DEFAULT_INVITE_EXPIRY_DAYS = 7
RETENTION_DAYS = 90
RETENTION_POLICY_VERSION = "v1-90d"  # design D18 起步值


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ── 4.1 前置：场次创建 ──────────────────────────────────────────────

class PrepSnapshotNotFrozenForInviteError(Exception):
    """选中的投递没有已冻结的 prep 快照，不能签发邀约（tasks 4.11 前置）。"""


def compute_new_session(
    conn: sqlite3.Connection, *, application_id: str, prep_snapshot_version: int, sample_class: str
) -> dict[str, Any]:
    """纯计算：组装新场次的字段，不写库。retention_until 在这里算好——铁律
    要求留存期限必须在场次建立那一刻由应用层写入，不允许留空（U1 已有的
    NOT NULL 约束）。"""
    row = conn.execute(
        "SELECT status FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, prep_snapshot_version),
    ).fetchone()
    if row is None or row[0] != "frozen":
        raise PrepSnapshotNotFrozenForInviteError(
            f"投递 {application_id!r} 版本 {prep_snapshot_version} 的 prep 快照未冻结"
        )
    retention_until = (_utcnow() + timedelta(days=RETENTION_DAYS)).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "application_id": application_id,
        "prep_snapshot_version": prep_snapshot_version,
        "sample_class": sample_class,
        "retention_until": retention_until,
        "retention_policy_version": RETENTION_POLICY_VERSION,
    }


@idempotent_effect("effect_create_interview_session")
def effect_create_interview_session(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session: dict[str, Any]
) -> str:
    """effect_* 节点：写 interview_session 一行，status='pending'。business_key
    由调用方传 HR 点击签发时生成的 request_id（每次点击必须产生一次意图，即使
    参数逐字相同——与 effect_regenerate_prep_question 的 request_id 用法同一
    先例）。"""
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (
            session["id"], session["application_id"], session["prep_snapshot_version"],
            session["retention_until"], session["retention_policy_version"], session["sample_class"],
        ),
    )
    return session["id"]


# ── 4.9 / 4.10：签发前置闸 ───────────────────────────────────────────

class LiveInterviewNotEnabledError(Exception):
    """真实候选人开闸未开启，live 场次签发被拒（design D14，tasks 4.9）。"""


class ContactVaultUnavailableError(Exception):
    """candidate-contact-vault 未开启/未交付，live 场次签发被拒（tasks 4.10）。"""


def assert_invite_issuance_allowed(conn: sqlite3.Connection, *, sample_class: str) -> None:
    """签发前的结构性前置校验。internal_sim 场次不受这两道闸约束（spec
    「开关关闭时只允许为内部模拟场次签发」）；live 场次必须两道闸都通过。
    ⛔ 不在这里捕获异常——调用方（Web 路由）据异常类型返回 4xx 并留痕。"""
    if sample_class != "live":
        return
    if not is_live_interview_enabled():
        raise LiveInterviewNotEnabledError("真实候选人开闸未开启")
    if not is_contact_vault_available():
        raise ContactVaultUnavailableError("candidate-contact-vault 未开启，live 场次签发被拒")


# ── 4.1：令牌签发 ───────────────────────────────────────────────────

def generate_invite_token() -> str:
    """32 字节随机、URL-safe 编码（spec「MUST 不可猜测」）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def load_invite_expiry_days(conn: sqlite3.Connection, job_id: str) -> int:
    row = conn.execute(
        "SELECT invite_expiry_days FROM job_prep_config WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return DEFAULT_INVITE_EXPIRY_DAYS
    return row[0]


def compute_new_invite_token(conn: sqlite3.Connection, *, job_id: str) -> tuple[str, str, str]:
    """纯计算：生成明文令牌、其哈希、到期时刻（ISO8601 UTC）。不写库。
    返回 (明文令牌, 哈希, 到期时刻字符串)——明文令牌只在这一次调用里出现，
    调用方负责把它拼进候选人链接，之后系统只认哈希。"""
    token = generate_invite_token()
    token_hash = _hash_token(token)
    days = load_invite_expiry_days(conn, job_id)
    expires_at = (_utcnow() + timedelta(days=days)).isoformat()
    return token, token_hash, expires_at


@idempotent_effect("effect_issue_invite")
def effect_issue_invite(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, token_hash: str, expires_at: str,
) -> None:
    """effect_* 节点：business_key = token_hash（tasks 4.1 字面幂等键公式）。
    "同一场次重复签发 ⇒ 旧令牌作废" 由 UPDATE 覆盖旧哈希实现——旧哈希一旦被
    覆盖，任何用旧明文令牌算出的哈希都查不到匹配行，天然作废，不需要额外
    的"已作废"标记。"并留痕" 由 interview_invite_event 承担。"""
    prior = conn.execute(
        "SELECT invite_token_hash FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    was_reissue = prior is not None and prior[0] is not None
    conn.execute(
        "UPDATE interview_session SET invite_token_hash = ?, invite_expires_at = ? WHERE id = ?",
        (token_hash, expires_at, session_id),
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type, detail) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), session_id, "reissued" if was_reissue else "issued", token_hash),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_invite_nodes.py -v`
Expected: 本任务写的用例全部 PASS（`TestSessionCreation`、`TestIssuanceGate`、`TestInviteIssuance`）。

- [ ] **Step 5: Commit**

```bash
git add app/graph/invite_nodes.py tests/test_invite_nodes.py
git commit -m "feat(voice-interview): U3 场次创建与令牌签发节点"
```

---

### Task 5: 令牌校验端点（首次打开 / 重复 / 过期）

**Files:**
- Modify: `app/graph/invite_nodes.py`（追加，不覆盖已有内容）
- Modify: `tests/test_invite_nodes.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `_hash_token`、`_parse_iso`
- Produces: `InviteTokenInvalidError`、`find_session_by_token()`、`effect_log_invite_access_denied()`、`effect_open_invite()`、`open_invite()`。Task 10（Web 路由）消费 `open_invite()`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_invite_nodes.py` 末尾追加：

```python
from app.graph.invite_nodes import (
    InviteTokenInvalidError,
    open_invite,
)


def _issue_token(conn, session_id, job_id="j1"):
    from app.graph.invite_nodes import compute_new_invite_token, effect_issue_invite

    token, token_hash, expires_at = compute_new_invite_token(conn, job_id=job_id)
    effect_issue_invite(
        conn, thread_id=session_id, business_key=token_hash,
        session_id=session_id, token_hash=token_hash, expires_at=expires_at,
    )
    return token


def _new_pending_session(conn):
    _freeze_prep(conn)
    session = compute_new_session(
        conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
    )
    return effect_create_interview_session(conn, thread_id="app1", business_key="req1", session=session)


class TestOpenInvite:
    def test_first_open_transitions_to_in_progress(self, conn):
        session_id = _new_pending_session(conn)
        token = _issue_token(conn, session_id)

        opened_id = open_invite(conn, token)

        assert opened_id == session_id
        status = conn.execute(
            "SELECT status FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()[0]
        assert status == "in_progress"

    def test_second_open_rejected_with_unified_error(self, conn):
        session_id = _new_pending_session(conn)
        token = _issue_token(conn, session_id)
        open_invite(conn, token)

        with pytest.raises(InviteTokenInvalidError):
            open_invite(conn, token)

        events = conn.execute(
            "SELECT event_type FROM interview_invite_event WHERE session_id = ? "
            "AND event_type = 'reused_access'",
            (session_id,),
        ).fetchall()
        assert len(events) == 1

    def test_unknown_token_rejected(self, conn):
        with pytest.raises(InviteTokenInvalidError):
            open_invite(conn, "this-token-was-never-issued")

    def test_expired_token_rejected_and_logged(self, conn):
        session_id = _new_pending_session(conn)
        from app.graph.invite_nodes import compute_new_invite_token, effect_issue_invite

        token, token_hash, _ = compute_new_invite_token(conn, job_id="j1")
        past = "2020-01-01T00:00:00+00:00"
        effect_issue_invite(
            conn, thread_id=session_id, business_key=token_hash,
            session_id=session_id, token_hash=token_hash, expires_at=past,
        )

        with pytest.raises(InviteTokenInvalidError):
            open_invite(conn, token)

        events = conn.execute(
            "SELECT event_type FROM interview_invite_event WHERE session_id = ? "
            "AND event_type = 'expired_access'",
            (session_id,),
        ).fetchall()
        assert len(events) == 1
        status = conn.execute(
            "SELECT status FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()[0]
        assert status == "pending"  # 过期打开不改变场次状态
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invite_nodes.py -k TestOpenInvite -v`
Expected: FAIL（`ImportError: cannot import name 'InviteTokenInvalidError'`）。

- [ ] **Step 3: 实现**

在 `app/graph/invite_nodes.py` 末尾追加（`effect_issue_invite` 之后）：

```python
# ── 4.2：令牌校验端点 ────────────────────────────────────────────────

class InviteTokenInvalidError(Exception):
    """统一失效页情形：令牌未知、已用、已过期。⛔ 三种原因对候选人展示同一个
    页面文案（spec「MUST NOT 泄露场次或候选人信息」），区分只在留痕里。"""


def find_session_by_token(conn: sqlite3.Connection, token: str) -> str | None:
    token_hash = _hash_token(token)
    row = conn.execute(
        "SELECT id FROM interview_session WHERE invite_token_hash = ?", (token_hash,)
    ).fetchone()
    return row[0] if row else None


@idempotent_effect("effect_log_invite_access_denied")
def effect_log_invite_access_denied(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, reason: str
) -> None:
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), session_id, reason),
    )


@idempotent_effect("effect_open_invite")
def effect_open_invite(conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str) -> None:
    """首次打开：status pending → in_progress。这一步状态转移本身就是"令牌
    已使用"的落点——第二次打开时 open_invite() 会看到 status != 'pending'
    并拒绝，等价于 spec 要求的"打开即失效"，不需要额外的 used_at 列。"""
    conn.execute(
        "UPDATE interview_session SET status = 'in_progress' WHERE id = ? AND status = 'pending'",
        (session_id,),
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type) VALUES (?, ?, 'opened')",
        (str(uuid.uuid4()), session_id),
    )


def open_invite(conn: sqlite3.Connection, token: str) -> str:
    """L4 编排：查找 → 校验过期 → 校验未用 → 标记已用，四步必须在同一次
    请求内顺序发生（单连接 SQLite，无并发行锁问题，见 app/storage/db.py
    的单连接模型）。返回 session_id；任何一步不满足抛 InviteTokenInvalidError。
    """
    session_id = find_session_by_token(conn, token)
    if session_id is None:
        raise InviteTokenInvalidError("令牌无效")

    row = conn.execute(
        "SELECT invite_expires_at, status FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    expires_at_raw, status = row

    if _utcnow() > _parse_iso(expires_at_raw):
        effect_log_invite_access_denied(
            conn, thread_id=session_id, business_key=f"expired:{uuid.uuid4().hex}",
            session_id=session_id, reason="expired_access",
        )
        raise InviteTokenInvalidError("令牌已过期")

    if status != "pending":
        effect_log_invite_access_denied(
            conn, thread_id=session_id, business_key=f"reused:{uuid.uuid4().hex}",
            session_id=session_id, reason="reused_access",
        )
        raise InviteTokenInvalidError("令牌已使用")

    effect_open_invite(conn, thread_id=session_id, business_key="open", session_id=session_id)
    return session_id
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_invite_nodes.py -k TestOpenInvite -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/graph/invite_nodes.py tests/test_invite_nodes.py
git commit -m "feat(voice-interview): U3 令牌校验端点（首次/重复/过期三态）"
```

---

### Task 6: 续入令牌

**Files:**
- Modify: `app/graph/invite_nodes.py`（追加）
- Modify: `tests/test_invite_nodes.py`（追加）

**Interfaces:**
- Consumes: Task 5 的 `InviteTokenInvalidError`、`_hash_token`、`_utcnow`
- Produces: `MAX_RESUME_ISSUANCES`、`ResumeTokenLimitExceededError`、`resume_issuance_count()`、`compute_new_resume_token()`、`effect_issue_resume_token()`、`effect_consume_resume_token()`、`open_resume()`。Task 10 消费 `open_resume()`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_invite_nodes.py`：

```python
from app.graph.invite_nodes import (
    MAX_RESUME_ISSUANCES,
    ResumeTokenLimitExceededError,
    compute_new_resume_token,
    effect_issue_resume_token,
    open_resume,
)


class TestResumeToken:
    def test_issue_and_open_resume_returns_next_seq_one_when_no_turns(self, conn):
        session_id = _new_pending_session(conn)
        conn.execute("UPDATE interview_session SET status = 'interrupted' WHERE id = ?", (session_id,))
        conn.commit()

        token, token_hash = compute_new_resume_token(conn, session_id=session_id)
        effect_issue_resume_token(
            conn, thread_id=session_id, business_key=token_hash,
            session_id=session_id, token_hash=token_hash,
        )

        opened_id, next_seq = open_resume(conn, token)
        assert opened_id == session_id
        assert next_seq == 1

    def test_open_resume_skips_already_answered_turns(self, conn):
        session_id = _new_pending_session(conn)
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, origin) "
            "VALUES ('q1', 'snap1', 1, 'd', 'easy', 'text', 'ai')"
        )
        conn.execute(
            "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, answer_mode) "
            "VALUES ('t1', ?, 1, 'q1', 'text', 'text')",
            (session_id,),
        )
        conn.execute("UPDATE interview_session SET status = 'interrupted' WHERE id = ?", (session_id,))
        conn.commit()

        token, token_hash = compute_new_resume_token(conn, session_id=session_id)
        effect_issue_resume_token(
            conn, thread_id=session_id, business_key=token_hash,
            session_id=session_id, token_hash=token_hash,
        )
        _, next_seq = open_resume(conn, token)
        assert next_seq == 2  # 不重复出第 1 题

    def test_resume_token_is_one_time_use(self, conn):
        session_id = _new_pending_session(conn)
        conn.execute("UPDATE interview_session SET status = 'interrupted' WHERE id = ?", (session_id,))
        conn.commit()
        token, token_hash = compute_new_resume_token(conn, session_id=session_id)
        effect_issue_resume_token(
            conn, thread_id=session_id, business_key=token_hash,
            session_id=session_id, token_hash=token_hash,
        )
        open_resume(conn, token)

        from app.graph.invite_nodes import InviteTokenInvalidError

        with pytest.raises(InviteTokenInvalidError):
            open_resume(conn, token)

    def test_resume_issuance_capped_at_max(self, conn):
        session_id = _new_pending_session(conn)
        conn.execute("UPDATE interview_session SET status = 'interrupted' WHERE id = ?", (session_id,))
        conn.commit()

        for _ in range(MAX_RESUME_ISSUANCES):
            token, token_hash = compute_new_resume_token(conn, session_id=session_id)
            effect_issue_resume_token(
                conn, thread_id=session_id, business_key=token_hash,
                session_id=session_id, token_hash=token_hash,
            )

        with pytest.raises(ResumeTokenLimitExceededError):
            compute_new_resume_token(conn, session_id=session_id)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invite_nodes.py -k TestResumeToken -v`
Expected: FAIL（`ImportError`）。

- [ ] **Step 3: 实现**

追加到 `app/graph/invite_nodes.py`：

```python
# ── 4.3：续入令牌 ───────────────────────────────────────────────────

MAX_RESUME_ISSUANCES = 3


class ResumeTokenLimitExceededError(Exception):
    """续入令牌签发次数已达上限（tasks 4.3：一次性、上限 3 次）。"""


def resume_issuance_count(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM interview_invite_event WHERE session_id = ? AND event_type = 'resume_issued'",
        (session_id,),
    ).fetchone()
    return row[0]


def compute_new_resume_token(conn: sqlite3.Connection, *, session_id: str) -> tuple[str, str]:
    """纯计算：生成续入令牌明文与哈希。不写库；上限校验在这里抛出——签发
    次数是只读查询，不需要等到 effect 节点才发现超限。"""
    if resume_issuance_count(conn, session_id) >= MAX_RESUME_ISSUANCES:
        raise ResumeTokenLimitExceededError(
            f"场次 {session_id!r} 续入令牌已达上限 {MAX_RESUME_ISSUANCES} 次"
        )
    token = generate_invite_token()
    token_hash = _hash_token(token)
    return token, token_hash


@idempotent_effect("effect_issue_resume_token")
def effect_issue_resume_token(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, token_hash: str
) -> None:
    """business_key = token_hash。旧续入令牌（若有）同样被覆盖作废——同一
    场次同一时刻只有一枚有效续入令牌，与主令牌同一手法。"""
    conn.execute(
        "UPDATE interview_session SET resume_token_hash = ? WHERE id = ?", (token_hash, session_id)
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type, detail) "
        "VALUES (?, ?, 'resume_issued', ?)",
        (str(uuid.uuid4()), session_id, token_hash),
    )


@idempotent_effect("effect_consume_resume_token")
def effect_consume_resume_token(conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str) -> None:
    """一次性消费：清空 resume_token_hash，场次回到 in_progress。
    business_key 由调用方传 token_hash——同一枚令牌只能被消费一次，
    第二次打开同一 token 时 open_resume() 在查找阶段就已经因
    resume_token_hash 已被清空而查不到 session，不会重复走到这里。"""
    conn.execute(
        "UPDATE interview_session SET resume_token_hash = NULL, status = 'in_progress' WHERE id = ?",
        (session_id,),
    )


def open_resume(conn: sqlite3.Connection, token: str) -> tuple[str, int]:
    """L4 编排：校验续入令牌并返回 (session_id, next_seq)。next_seq = 该场次
    已落库 interview_turn 的最大 seq + 1（没有 turn 时为 1）——⛔ 不重复
    出题：出题内容仍是冻结快照里原来的题，本函数只决定从第几题继续
    （与 5.9 联动，本单元只交付这个查询本身）。"""
    token_hash = _hash_token(token)
    row = conn.execute(
        "SELECT id, status FROM interview_session WHERE resume_token_hash = ?", (token_hash,)
    ).fetchone()
    if row is None:
        raise InviteTokenInvalidError("续入令牌无效")
    session_id, status = row
    if status not in ("interrupted", "in_progress"):
        raise InviteTokenInvalidError("续入令牌已失效")

    turn_row = conn.execute(
        "SELECT MAX(seq) FROM interview_turn WHERE session_id = ?", (session_id,)
    ).fetchone()
    next_seq = (turn_row[0] or 0) + 1

    effect_consume_resume_token(conn, thread_id=session_id, business_key=token_hash, session_id=session_id)
    return session_id, next_seq
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_invite_nodes.py -k TestResumeToken -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/graph/invite_nodes.py tests/test_invite_nodes.py
git commit -m "feat(voice-interview): U3 续入令牌签发与消费（不重复出题）"
```

---

### Task 7: 邀约投递（经既有外发门禁）

**Files:**
- Modify: `app/graph/invite_nodes.py`（追加）
- Modify: `tests/test_invite_nodes.py`（追加）

**Interfaces:**
- Consumes: `app.outbound.delivery.deliver_candidate_message`、`app.outbound.messages.CandidateOutboundMessage`、`app.agents.jd_agent.AI_LABEL_TEMPLATE`
- Produces: `render_invite_body()`、`compose_invite_draft()`、`effect_deliver_invitation()`。Task 10 消费。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_invite_nodes.py`（顶部 import 区追加所需符号）：

```python
from app.audit.recorder import AuditRecorder
from app.channels.web_channel import WebChannel
from app.graph.invite_nodes import compose_invite_draft, effect_deliver_invitation, render_invite_body


class TestDeliverInvitation:
    def test_render_invite_body_contains_ai_label(self):
        body = render_invite_body(candidate_link="https://x.example/i/tok123", job_title="ECU 工程师")
        from app.agents.jd_agent import AI_LABEL_PREFIX  # noqa: F401 — 若无此导出改用下方兜底

    def test_compose_invite_draft_returns_draft_id_and_body(self):
        draft_id, body = compose_invite_draft(
            candidate_link="https://x.example/i/tok123", job_title="ECU 工程师"
        )
        assert draft_id
        assert "https://x.example/i/tok123" in body

    def test_effect_deliver_invitation_blocked_when_outbound_disabled_logs_manual_handoff(
        self, conn, tmp_path
    ):
        session_id = _new_pending_session(conn)
        draft_id, body = compose_invite_draft(
            candidate_link="https://x.example/i/tok123", job_title="ECU 工程师"
        )
        recorder = AuditRecorder(jsonl_path=str(tmp_path / "decisions.jsonl"))
        channel = WebChannel()

        effect_deliver_invitation(
            conn, thread_id=session_id, business_key=draft_id,
            session_id=session_id, recipient="candidate:app1", body=body,
            channel=channel, recorder=recorder, outbound_enabled=lambda: False,
            confirmed_by="hr:tester",
        )

        event = conn.execute(
            "SELECT event_type FROM interview_invite_event WHERE session_id = ? "
            "AND event_type = 'manual_handoff'",
            (session_id,),
        ).fetchone()
        assert event is not None

    def test_effect_deliver_invitation_allowed_when_outbound_enabled_logs_delivered(
        self, conn, tmp_path
    ):
        session_id = _new_pending_session(conn)
        draft_id, body = compose_invite_draft(
            candidate_link="https://x.example/i/tok123", job_title="ECU 工程师"
        )
        recorder = AuditRecorder(jsonl_path=str(tmp_path / "decisions.jsonl"))
        channel = WebChannel()

        effect_deliver_invitation(
            conn, thread_id=session_id, business_key=draft_id,
            session_id=session_id, recipient="candidate:app1", body=body,
            channel=channel, recorder=recorder, outbound_enabled=lambda: True,
            confirmed_by="hr:tester",
        )

        event = conn.execute(
            "SELECT event_type FROM interview_invite_event WHERE session_id = ? "
            "AND event_type = 'delivered'",
            (session_id,),
        ).fetchone()
        assert event is not None
```

如果 `AuditRecorder`/`WebChannel` 的构造参数与上面假设的关键字不同，Step 2 跑测试时会先看到 `TypeError`——以 `tests/test_interview_prep_nodes.py` 或 `tests/test_outbound_delivery.py` 里的真实构造方式为准调整这两行，不要凭空发明参数名。

- [ ] **Step 2: 跑测试确认失败，并核对 AuditRecorder/WebChannel 构造签名**

Run: `grep -n "class AuditRecorder" -A 15 app/audit/recorder.py`
Run: `grep -n "class WebChannel" -A 15 app/channels/web_channel.py`

按实际构造参数修正上面测试里的 `AuditRecorder(...)`/`WebChannel()` 调用（常见形态是 `AuditRecorder(jsonl_path=...)` 与无参 `WebChannel()`，以 grep 结果为准）。

Run: `pytest tests/test_invite_nodes.py -k TestDeliverInvitation -v`
Expected: FAIL（`ImportError: cannot import name 'compose_invite_draft'`）。

- [ ] **Step 3: 实现**

追加到 `app/graph/invite_nodes.py`（顶部 import 区补充）：

```python
from app.agents.jd_agent import AI_LABEL_TEMPLATE
from app.outbound.delivery import deliver_candidate_message
from app.outbound.messages import CandidateOutboundMessage
```

文件末尾追加：

```python
# ── 4.4：邀约投递（经既有外发门禁） ──────────────────────────────────

def render_invite_body(*, candidate_link: str, job_title: str) -> str:
    """草稿正文，复用 jd_agent 的 AI 生成标识模板（design D6：不另写一套）。"""
    generated_at = _utcnow().isoformat()
    label = AI_LABEL_TEMPLATE.format(generated_at=generated_at)
    return (
        f"您好，您已进入「{job_title}」岗位的 AI 结构化面试环节。\n"
        f"请点击以下链接开始（链接仅可使用一次，请勿转发给他人）：\n{candidate_link}\n\n"
        f"{label}"
    )


def compose_invite_draft(*, candidate_link: str, job_title: str) -> tuple[str, str]:
    """返回 (draft_id, body)。draft_id 是这次拟稿的稳定标识，用作
    effect_deliver_invitation 的幂等键（tasks 4.4 字面公式
    `{session_id}:effect_deliver_invitation:{draft_id}`）——同一次拟稿只投递
    一次，重新拟稿（如改了文案）产生新 draft_id，允许重新走一次门禁。"""
    draft_id = uuid.uuid4().hex
    body = render_invite_body(candidate_link=candidate_link, job_title=job_title)
    return draft_id, body


@idempotent_effect("effect_deliver_invitation")
def effect_deliver_invitation(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, recipient: str, body: str, channel, recorder,
    outbound_enabled, confirmed_by: str | None = None,
) -> None:
    """effect_* 节点：调既有门禁唯一入口 deliver_candidate_message()。
    ⛔ 本函数不 import app.channels.*.deliver 或任何通道具体实现之外的直连
    路径——反证测试 test_no_direct_channel_deliver_import 会源码级扫描本
    文件确认不出现 `import channel.deliver` / `from app.channels.*.deliver`。

    总开关关闭 ⇒ 门禁拒绝（REASON_OUTBOUND_DISABLED）⇒ 留 'manual_handoff'
    事件，链接由调用方（Web 路由）已经拿在手里、直接展示在 HR 工作台，不需要
    本函数额外处理；总开关开启且 confirmed_by 非空 ⇒ 门禁放行 ⇒ 留
    'delivered' 事件。"""
    message = CandidateOutboundMessage(
        message_type="interview_invitation",
        recipient=recipient,
        body=body,
        confirmed_by=confirmed_by,
    )
    decision = deliver_candidate_message(
        conn, thread_id=thread_id, message=message, channel=channel,
        recorder=recorder, outbound_enabled=outbound_enabled,
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type, detail) VALUES (?, ?, ?, ?)",
        (
            str(uuid.uuid4()), session_id,
            "delivered" if decision.allowed else "manual_handoff",
            decision.reason or "",
        ),
    )
```

- [ ] **Step 4: 反证测试——不得直连 channel.deliver**

追加到 `tests/test_invite_nodes.py`：

```python
def test_no_direct_channel_deliver_import():
    """tasks 4.4 反证：代码里不得 import channel.deliver。"""
    import pathlib

    source = pathlib.Path("app/graph/invite_nodes.py").read_text(encoding="utf-8")
    assert "channel.deliver" not in source
    assert "from app.channels" not in source or "from app.channels.base import" in source
```

（若 `app/channels/base.py` 的 `Channel`/`OutboundMessage` 类型注解确有必要 import，上面第二条断言按需放宽为只检查没有从具体通道实现模块 import `deliver` 符号——以实际需要为准，不要为了让断言变松而弱化它的检测力。）

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_invite_nodes.py -k "TestDeliverInvitation or no_direct_channel_deliver" -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add app/graph/invite_nodes.py tests/test_invite_nodes.py
git commit -m "feat(voice-interview): U3 邀约投递经既有外发门禁，总开关关闭走人工转达"
```

---

### Task 8: 双同意记录

**Files:**
- Modify: `app/graph/invite_nodes.py`（追加）
- Modify: `tests/test_invite_nodes.py`（追加）

**Interfaces:**
- Consumes: 无新依赖（沿用 `idempotent_effect`）
- Produces: `CONSENT_KINDS`、`effect_record_consent()`。Task 10 消费。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_invite_nodes.py`：

```python
from app.graph.invite_nodes import CONSENT_KINDS, effect_record_consent


class TestConsent:
    def test_consent_kinds(self):
        assert CONSENT_KINDS == ("ai_interview", "identity_check")

    def test_accept_both_kinds_persists_two_rows_session_stays_pending_state(self, conn):
        session_id = _new_pending_session(conn)
        for kind in CONSENT_KINDS:
            effect_record_consent(
                conn, thread_id=session_id, business_key=f"{kind}:v1",
                session_id=session_id, kind=kind, result="accepted", version="v1",
            )
        rows = conn.execute(
            "SELECT kind, result, consent_version FROM interview_consent WHERE session_id = ? ORDER BY kind",
            (session_id,),
        ).fetchall()
        assert rows == [("ai_interview", "accepted", "v1"), ("identity_check", "accepted", "v1")]
        status = conn.execute(
            "SELECT status FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()[0]
        assert status != "abandoned"

    def test_decline_one_kind_abandons_session_and_logs(self, conn):
        session_id = _new_pending_session(conn)
        effect_record_consent(
            conn, thread_id=session_id, business_key="ai_interview:v1",
            session_id=session_id, kind="ai_interview", result="declined", version="v1",
        )
        status = conn.execute(
            "SELECT status FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()[0]
        assert status == "abandoned"
        event = conn.execute(
            "SELECT detail FROM interview_invite_event WHERE session_id = ? AND event_type = 'consent_declined'",
            (session_id,),
        ).fetchone()
        assert event == ("ai_interview",)

    def test_consent_upsert_on_resubmit_same_kind(self, conn):
        session_id = _new_pending_session(conn)
        effect_record_consent(
            conn, thread_id=session_id, business_key="ai_interview:v1",
            session_id=session_id, kind="ai_interview", result="declined", version="v1",
        )
        effect_record_consent(
            conn, thread_id=session_id, business_key="ai_interview:v1-retry",
            session_id=session_id, kind="ai_interview", result="accepted", version="v1",
        )
        row = conn.execute(
            "SELECT result FROM interview_consent WHERE session_id = ? AND kind = 'ai_interview'",
            (session_id,),
        ).fetchone()
        assert row[0] == "accepted"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invite_nodes.py -k TestConsent -v`
Expected: FAIL（`ImportError`）。

- [ ] **Step 3: 实现**

追加到 `app/graph/invite_nodes.py`：

```python
# ── 4.6：双同意记录 ─────────────────────────────────────────────────

CONSENT_KINDS: tuple[str, ...] = ("ai_interview", "identity_check")


@idempotent_effect("effect_record_consent")
def effect_record_consent(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, kind: str, result: str, version: str,
) -> None:
    """effect_* 节点：business_key = f"{kind}:{version}"（tasks 4.6 字面幂等
    键公式 `{session_id}:effect_record_consent:{kind}:{version}`）。同一
    (session_id, kind) 重复提交按 UPSERT 处理（候选人改主意重新勾选）。
    任一拒绝 ⇒ 场次 abandoned ⇒ 留痕——spec「任一拒绝 ⇒ 场次不开始」，
    每次调用独立判断，顺序不敏感（不管先提交哪一项，只要某一项是拒绝，
    场次就会被置为 abandoned）。"""
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(session_id, kind) DO UPDATE SET "
        "result = excluded.result, consent_version = excluded.consent_version, at = datetime('now')",
        (session_id, kind, result, version),
    )
    if result == "declined":
        conn.execute(
            "UPDATE interview_session SET status = 'abandoned' WHERE id = ? AND status != 'abandoned'",
            (session_id,),
        )
        conn.execute(
            "INSERT INTO interview_invite_event (id, session_id, event_type, detail) "
            "VALUES (?, ?, 'consent_declined', ?)",
            (str(uuid.uuid4()), session_id, kind),
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_invite_nodes.py -k TestConsent -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/graph/invite_nodes.py tests/test_invite_nodes.py
git commit -m "feat(voice-interview): U3 双同意留痕，任一拒绝转场次 abandoned"
```

---

### Task 9: 手机号验证码（签发/校验/锁定/HR 展示）

**Files:**
- Modify: `app/graph/invite_nodes.py`（追加）
- Modify: `tests/test_invite_nodes.py`（追加）

**Interfaces:**
- Consumes: 无新依赖
- Produces: `CODE_LENGTH`、`CODE_TTL_MINUTES`、`MAX_CODE_ATTEMPTS`、`generate_verification_code()`、`effect_issue_verification_code()`、`issue_verification_code()`、`VerificationLockedError`、`VerificationExpiredError`、`VerificationIncorrectError`、`effect_verify_phone()`、`verify_phone_code()`、`effect_display_verification_code_to_hr()`、`effect_send_verification_code()`（占位）。Task 10 消费。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_invite_nodes.py`：

```python
from app.graph.invite_nodes import (
    CODE_LENGTH,
    MAX_CODE_ATTEMPTS,
    VerificationExpiredError,
    VerificationIncorrectError,
    VerificationLockedError,
    effect_display_verification_code_to_hr,
    effect_send_verification_code,
    issue_verification_code,
    verify_phone_code,
)


class TestVerificationCode:
    def test_issued_code_has_expected_length(self, conn):
        session_id = _new_pending_session(conn)
        code = issue_verification_code(conn, session_id=session_id)
        assert len(code) == CODE_LENGTH
        assert code.isdigit()

    def test_correct_code_passes_and_writes_identity_check_skipped(self, conn):
        session_id = _new_pending_session(conn)
        code = issue_verification_code(conn, session_id=session_id)

        verify_phone_code(conn, session_id=session_id, submitted_code=code)

        row = conn.execute(
            "SELECT phone_verified_at FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        assert row[0] is not None
        identity = conn.execute(
            "SELECT result FROM identity_check WHERE session_id = ?", (session_id,)
        ).fetchone()
        assert identity == ("skipped",)

    def test_incorrect_code_raises_and_increments_attempts(self, conn):
        session_id = _new_pending_session(conn)
        issue_verification_code(conn, session_id=session_id)

        with pytest.raises(VerificationIncorrectError):
            verify_phone_code(conn, session_id=session_id, submitted_code="000000")

        attempts = conn.execute(
            "SELECT phone_attempts FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()[0]
        assert attempts == 1

    def test_five_wrong_attempts_locks_session(self, conn):
        session_id = _new_pending_session(conn)
        issue_verification_code(conn, session_id=session_id)

        for i in range(MAX_CODE_ATTEMPTS):
            with pytest.raises((VerificationIncorrectError, VerificationLockedError)):
                verify_phone_code(conn, session_id=session_id, submitted_code="000000")

        status = conn.execute(
            "SELECT status FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()[0]
        assert status == "locked"
        event = conn.execute(
            "SELECT 1 FROM interview_invite_event WHERE session_id = ? AND event_type = 'verification_locked'",
            (session_id,),
        ).fetchone()
        assert event is not None

    def test_verify_rejected_once_locked(self, conn):
        session_id = _new_pending_session(conn)
        issue_verification_code(conn, session_id=session_id)
        for _ in range(MAX_CODE_ATTEMPTS):
            try:
                verify_phone_code(conn, session_id=session_id, submitted_code="000000")
            except Exception:
                pass
        with pytest.raises(VerificationLockedError):
            verify_phone_code(conn, session_id=session_id, submitted_code="000000")

    def test_verify_without_issued_code_raises_expired(self, conn):
        session_id = _new_pending_session(conn)
        with pytest.raises(VerificationExpiredError):
            verify_phone_code(conn, session_id=session_id, submitted_code="123456")

    def test_expired_code_rejected(self, conn):
        session_id = _new_pending_session(conn)
        code = issue_verification_code(conn, session_id=session_id)
        past = "2020-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE interview_session SET phone_code_expires_at = ? WHERE id = ?", (past, session_id)
        )
        conn.commit()
        with pytest.raises(VerificationExpiredError):
            verify_phone_code(conn, session_id=session_id, submitted_code=code)

    def test_display_to_hr_logs_event(self, conn):
        session_id = _new_pending_session(conn)
        effect_display_verification_code_to_hr(
            conn, thread_id=session_id, business_key=uuid.uuid4().hex,
            session_id=session_id, accessor="hr:tester",
        )
        event = conn.execute(
            "SELECT 1 FROM interview_invite_event WHERE session_id = ? AND event_type = 'code_displayed_to_hr'",
            (session_id,),
        ).fetchone()
        assert event is not None

    def test_send_verification_code_is_unimplemented_stub(self):
        with pytest.raises(NotImplementedError):
            effect_send_verification_code()
```

在文件顶部 import 区补 `import uuid`（若还没有——本文件已在 Task 4 引入过，检查后按需补）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invite_nodes.py -k TestVerificationCode -v`
Expected: FAIL（`ImportError`）。

- [ ] **Step 3: 实现**

追加到 `app/graph/invite_nodes.py`：

```python
# ── 4.7 / 4.8：手机号验证码 ──────────────────────────────────────────

CODE_LENGTH = 6
CODE_TTL_MINUTES = 5
MAX_CODE_ATTEMPTS = 5


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_verification_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


@idempotent_effect("effect_issue_verification_code")
def effect_issue_verification_code(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, code_hash: str, expires_at: str,
) -> None:
    """签发新验证码：落哈希与过期时刻，尝试次数清零（重新签发即重新给
    5 次机会——候选人请求新码是自己的选择，不是绕过锁定，锁定后场次
    status='locked'，签发前的编排函数会先挡住已锁定场次，见
    issue_verification_code）。"""
    conn.execute(
        "UPDATE interview_session SET phone_code_hash = ?, phone_code_expires_at = ?, "
        "phone_attempts = 0 WHERE id = ?",
        (code_hash, expires_at, session_id),
    )


class SessionLockedError(Exception):
    """场次已锁定，不能再签发新验证码。"""


def issue_verification_code(conn: sqlite3.Connection, *, session_id: str) -> str:
    """L4 编排：生成新验证码、落库、返回明文（仅此一次）。调用方决定展示给
    谁：短信通道未配置（本单元现状）⇒ 调用方接着调
    effect_display_verification_code_to_hr 展示在 HR 工作台；短信通道配置了
    ⇒ 调用方改调 effect_send_verification_code（本单元只留接口，OQ-10 未定
    前不接线，见该函数）。"""
    status_row = conn.execute(
        "SELECT status FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    if status_row is not None and status_row[0] == "locked":
        raise SessionLockedError(f"场次 {session_id!r} 已锁定，不能签发新验证码")

    code = generate_verification_code()
    code_hash = _hash_code(code)
    expires_at = (_utcnow() + timedelta(minutes=CODE_TTL_MINUTES)).isoformat()
    effect_issue_verification_code(
        conn, thread_id=session_id, business_key=uuid.uuid4().hex,
        session_id=session_id, code_hash=code_hash, expires_at=expires_at,
    )
    return code


class VerificationLockedError(Exception):
    """场次已锁定（连续输错超限）。"""


class VerificationExpiredError(Exception):
    """验证码未签发、或已过期。"""


class VerificationIncorrectError(Exception):
    """验证码错误（未超限时的单次失败）。"""


@idempotent_effect("effect_verify_phone")
def effect_verify_phone(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, correct: bool
) -> None:
    """business_key = str(attempt_no)（tasks 4.7 字面幂等键公式
    `{session_id}:effect_verify_phone:{attempt_no}`）。correct 由调用方
    （verify_phone_code，纯比对哈希）算好传入——本节点只做落库这一件事：
    通过则写 phone_verified_at + identity_check(result=skipped)；不通过则
    计数，超限则锁定场次并留痕。"""
    if correct:
        conn.execute(
            "UPDATE interview_session SET phone_verified_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        conn.execute(
            "INSERT INTO identity_check (session_id, result) VALUES (?, 'skipped') "
            "ON CONFLICT(session_id) DO NOTHING",
            (session_id,),
        )
        return

    conn.execute(
        "UPDATE interview_session SET phone_attempts = phone_attempts + 1 WHERE id = ?", (session_id,)
    )
    attempts = conn.execute(
        "SELECT phone_attempts FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()[0]
    if attempts >= MAX_CODE_ATTEMPTS:
        conn.execute("UPDATE interview_session SET status = 'locked' WHERE id = ?", (session_id,))
        conn.execute(
            "INSERT INTO interview_invite_event (id, session_id, event_type) VALUES (?, ?, 'verification_locked')",
            (str(uuid.uuid4()), session_id),
        )


def verify_phone_code(conn: sqlite3.Connection, *, session_id: str, submitted_code: str) -> None:
    """L4 编排：过期/锁定校验 → 比对哈希 → 落库。⛔ 不返回布尔，抛出对应
    异常——调用方（Web 路由）据异常类型映射 HTTP 状态与候选人文案。"""
    row = conn.execute(
        "SELECT status, phone_code_hash, phone_code_expires_at, phone_attempts "
        "FROM interview_session WHERE id = ?",
        (session_id,),
    ).fetchone()
    status, code_hash, expires_at_raw, attempts = row

    if status == "locked":
        raise VerificationLockedError("场次已锁定")
    if code_hash is None or expires_at_raw is None:
        raise VerificationExpiredError("验证码未签发或已失效")
    if _utcnow() > _parse_iso(expires_at_raw):
        raise VerificationExpiredError("验证码已过期")

    attempt_no = attempts + 1
    correct = _hash_code(submitted_code) == code_hash
    effect_verify_phone(
        conn, thread_id=session_id, business_key=str(attempt_no),
        session_id=session_id, correct=correct,
    )
    if not correct:
        post_row = conn.execute(
            "SELECT status FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        if post_row[0] == "locked":
            raise VerificationLockedError("连续输错已达上限，场次锁定")
        raise VerificationIncorrectError("验证码错误")


@idempotent_effect("effect_display_verification_code_to_hr")
def effect_display_verification_code_to_hr(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, accessor: str
) -> None:
    """短信通道未配置时的降级路径（tasks 4.8）：验证码在候选人请求时生成，
    本节点只留痕"HR 看过这个场次的验证码"这件事，⛔ 不落验证码明文本身——
    明文已经在 issue_verification_code 的返回值里，由 Web 路由直接吐给
    HR 工作台的响应体，不进数据库。"""
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type, detail) VALUES (?, ?, 'code_displayed_to_hr', ?)",
        (str(uuid.uuid4()), session_id, accessor),
    )


def effect_send_verification_code(*args, **kwargs):
    """短信通道自动发送节点（design D13）。⏸ 门禁口径 OQ-10（自动发送验证码
    是否属于"已人工确认邀约的从属动作"、可否免逐次门禁确认）未定前，本节点
    只留接口、默认不启用、不接线——tasks 4.8 字面要求"该节点只留接口"。
    ⛔ 不消费任何真实短信供应商 API（当前无供应商可消费）。调用方（Web 路由）
    不得引用这个函数；本单元的候选人验证码路径只有
    effect_display_verification_code_to_hr 一条。"""
    raise NotImplementedError("短信验证码通道未采购/未接线（OQ-10 未决），本单元不启用")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_invite_nodes.py -k TestVerificationCode -v`
Expected: 全部 PASS。

- [ ] **Step 5: 全量跑一次 invite_nodes 测试防回归**

Run: `pytest tests/test_invite_nodes.py -v`
Expected: 全部 PASS（含前面 6 个任务累积的所有用例）。

- [ ] **Step 6: Commit**

```bash
git add app/graph/invite_nodes.py tests/test_invite_nodes.py
git commit -m "feat(voice-interview): U3 验证码签发/校验/锁定，短信未配置走 HR 展示降级"
```

---

### Task 10: HR 签发页 ＋ 候选人端路由 ＋ e2e

**Files:**
- Modify: `app/web/server.py`
- Create: `app/web/static/interview_invite_issue.html`
- Create: `app/web/static/interview_consent.html`
- Test: `tests/test_invite_web_e2e.py`

**Interfaces:**
- Consumes: Task 4-9 的全部 `invite_nodes` 符号；`app.storage.consent_terms.load_consent_term`/`latest_consent_version`；既有 `reviewer_of`、`AuditRecorder`、`WebChannel`、`_render_static_page`
- Produces: 6 个新路由（见 Step 3），完成本交付单元。

- [ ] **Step 1: 写失败测试**

`tests/test_invite_web_e2e.py`:

```python
"""U3 邀约与同意流程 e2e：签发 → 打开 → 双同意 → 验证码 → in_progress
（tasks 4.11 e2e 场景）。"""

import pytest

from app.storage.db import get_connection, init_schema


@pytest.fixture
def client(make_test_client):
    c, conn = make_test_client
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'ECU 工程师', 'open')")
    conn.execute(
        "INSERT INTO application (id, job_id, resume_id, stage_id, status) "
        "VALUES ('app1', 'j1', 'r1', 'st1', 'active')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, status) "
        "VALUES ('snap1', 'app1', 1, 1, 'frozen')"
    )
    conn.commit()
    return c


def test_full_invite_to_in_progress_e2e(client):
    # 1. HR 签发（internal_sim，总开关关闭走人工转达，链接直接回在响应体里）
    resp = client.post(
        "/api/applications/app1/interview-sessions",
        json={"prep_snapshot_version": 1, "sample_class": "internal_sim", "request_id": "req-e2e-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    session_id = body["session_id"]
    token = body["invite_token"]
    assert body["delivery_mode"] == "manual_handoff"

    # 2. 候选人首次打开链接
    resp = client.get(f"/interview/invite/{token}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id

    # 3. 双同意
    resp = client.post(
        f"/interview/sessions/{session_id}/consent",
        json={"kind": "ai_interview", "result": "accepted"},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/interview/sessions/{session_id}/consent",
        json={"kind": "identity_check", "result": "accepted"},
    )
    assert resp.status_code == 200

    # 4. 验证码：候选人请求 → HR 工作台读取展示 → 候选人提交
    resp = client.post(f"/interview/sessions/{session_id}/verification-code/request")
    assert resp.status_code == 200
    # HR 工作台读取路径回吐明文供人工转告
    hr_view = client.get(f"/api/interview-sessions/{session_id}/verification-code")
    assert hr_view.status_code == 200
    code = hr_view.json()["code"]

    resp = client.post(
        f"/interview/sessions/{session_id}/verification-code/verify", json={"code": code}
    )
    assert resp.status_code == 200

    # 5. 前置校验通过：场次已进入 in_progress，可以开场（U4 的事，这里只验证
    # 状态机走到了正确的终点）
    status_resp = client.get(f"/api/interview-sessions/{session_id}")
    assert status_resp.json()["status"] == "in_progress"


def test_reopen_used_invite_link_returns_unified_invalid_page(client):
    resp = client.post(
        "/api/applications/app1/interview-sessions",
        json={"prep_snapshot_version": 1, "sample_class": "internal_sim", "request_id": "req-e2e-2"},
    )
    token = resp.json()["invite_token"]
    client.get(f"/interview/invite/{token}")

    resp = client.get(f"/interview/invite/{token}")
    assert resp.status_code == 410
    assert "无效" in resp.json()["detail"] or "已使用" in resp.json()["detail"]


def test_decline_consent_abandons_session(client):
    resp = client.post(
        "/api/applications/app1/interview-sessions",
        json={"prep_snapshot_version": 1, "sample_class": "internal_sim", "request_id": "req-e2e-3"},
    )
    session_id = resp.json()["session_id"]
    token = resp.json()["invite_token"]
    client.get(f"/interview/invite/{token}")

    resp = client.post(
        f"/interview/sessions/{session_id}/consent",
        json={"kind": "ai_interview", "result": "declined"},
    )
    assert resp.status_code == 200

    status_resp = client.get(f"/api/interview-sessions/{session_id}")
    assert status_resp.json()["status"] == "abandoned"


def test_live_session_rejected_without_open_gates(client):
    resp = client.post(
        "/api/applications/app1/interview-sessions",
        json={"prep_snapshot_version": 1, "sample_class": "live", "request_id": "req-e2e-4"},
    )
    assert resp.status_code == 403
    assert "开闸" in resp.json()["detail"] or "vault" in resp.json()["detail"].lower() or "开启" in resp.json()["detail"]
```

先检查 `make_test_client` fixture 返回形状——`tests/conftest.py:12` 的 `make_test_client` 具体返回 `(client, conn)` 还是别的组合，以 conftest.py 的实际实现为准调整上面 `client` fixture 的解包写法（`c, conn = make_test_client` 若不对则按实际返回值改）。

- [ ] **Step 2: 跑测试确认失败**

Run: `grep -n "def make_test_client" -A 35 tests/conftest.py`

核对 fixture 真实签名与返回值后按需调整 Step 1 的 `client` fixture。

Run: `pytest tests/test_invite_web_e2e.py -v`
Expected: 全部 FAIL（404，路由不存在）。

- [ ] **Step 3: 实现 Web 路由**

在 `app/web/server.py` 的 import 区补充（`from app.graph.interview_prep_nodes import (...)` 之后）：

```python
from app.graph.invite_nodes import (
    CONSENT_KINDS,
    ContactVaultUnavailableError,
    InviteTokenInvalidError,
    LiveInterviewNotEnabledError,
    PrepSnapshotNotFrozenForInviteError,
    ResumeTokenLimitExceededError,
    SessionLockedError,
    VerificationExpiredError,
    VerificationIncorrectError,
    VerificationLockedError,
    assert_invite_issuance_allowed,
    compose_invite_draft,
    compute_new_invite_token,
    compute_new_resume_token,
    compute_new_session,
    effect_create_interview_session,
    effect_deliver_invitation,
    effect_display_verification_code_to_hr,
    effect_issue_invite,
    effect_issue_resume_token,
    effect_record_consent,
    issue_verification_code,
    open_invite,
    open_resume,
    verify_phone_code,
)
```

在 `app/web/server.py` 内、`create_app` 函数体中已有的 `router = APIRouter()` 之后、`prep_review_page`/`index` 路由所在区域附近追加以下路由函数（沿用文件里既有的 `conn`、`root_path`、`channel`、`recorder`、`gateway` 等闭包变量——它们已在 `create_app` 参数或函数体前部定义，直接引用，不要重新构造）：

```python
    class InterviewSessionCreateRequest(BaseModel):
        prep_snapshot_version: int
        sample_class: str
        request_id: str

    @router.post("/api/applications/{application_id}/interview-sessions")
    def create_interview_session(application_id: str, req: InterviewSessionCreateRequest, request: Request):
        try:
            assert_invite_issuance_allowed(conn, sample_class=req.sample_class)
        except LiveInterviewNotEnabledError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ContactVaultUnavailableError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        try:
            session = compute_new_session(
                conn, application_id=application_id,
                prep_snapshot_version=req.prep_snapshot_version, sample_class=req.sample_class,
            )
        except PrepSnapshotNotFrozenForInviteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        session_id = effect_create_interview_session(
            conn, thread_id=application_id, business_key=req.request_id, session=session
        )
        if session_id is None:
            row = conn.execute(
                "SELECT id FROM interview_session WHERE application_id = ? "
                "AND prep_snapshot_version = ? ORDER BY created_at DESC LIMIT 1",
                (application_id, req.prep_snapshot_version),
            ).fetchone()
            session_id = row[0]

        job_row = conn.execute(
            "SELECT job.id, job.title FROM application JOIN job ON job.id = application.job_id "
            "WHERE application.id = ?",
            (application_id,),
        ).fetchone()
        job_id, job_title = job_row

        token, token_hash, expires_at = compute_new_invite_token(conn, job_id=job_id)
        effect_issue_invite(
            conn, thread_id=session_id, business_key=token_hash,
            session_id=session_id, token_hash=token_hash, expires_at=expires_at,
        )

        candidate_link = f"{root_path}/interview/invite/{token}"
        draft_id, invite_body = compose_invite_draft(candidate_link=candidate_link, job_title=job_title)
        reviewer = reviewer_of(request)
        effect_deliver_invitation(
            conn, thread_id=session_id, business_key=draft_id,
            session_id=session_id, recipient=f"candidate:{application_id}", body=invite_body,
            channel=channel, recorder=recorder, outbound_enabled=outbound_enabled,
            confirmed_by=reviewer,
        )
        event = conn.execute(
            "SELECT event_type FROM interview_invite_event WHERE session_id = ? "
            "ORDER BY at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        delivery_mode = "delivered" if event and event[0] == "delivered" else "manual_handoff"

        return {
            "session_id": session_id,
            "invite_token": token,
            "invite_expires_at": expires_at,
            "delivery_mode": delivery_mode,
            "candidate_link": candidate_link,
        }

    @router.get("/interview/invite/{token}")
    def open_interview_invite(token: str):
        try:
            session_id = open_invite(conn, token)
        except InviteTokenInvalidError:
            raise HTTPException(status_code=410, detail="链接已失效")
        return {
            "session_id": session_id,
            "consent_terms": [
                {"kind": kind, "version": latest_consent_version_for(kind)}
                for kind in CONSENT_KINDS
            ],
        }

    @router.get("/interview/resume/{token}")
    def open_interview_resume(token: str):
        try:
            session_id, next_seq = open_resume(conn, token)
        except InviteTokenInvalidError:
            raise HTTPException(status_code=410, detail="续入链接已失效")
        return {"session_id": session_id, "next_seq": next_seq}

    class ConsentSubmitRequest(BaseModel):
        kind: str
        result: str

    @router.post("/interview/sessions/{session_id}/consent")
    def submit_consent(session_id: str, req: ConsentSubmitRequest):
        if req.kind not in CONSENT_KINDS:
            raise HTTPException(status_code=422, detail="未知的同意类型")
        version = latest_consent_version_for(req.kind)
        effect_record_consent(
            conn, thread_id=session_id, business_key=f"{req.kind}:{version}",
            session_id=session_id, kind=req.kind, result=req.result, version=version,
        )
        return {"ok": True, "kind": req.kind, "result": req.result, "version": version}

    @router.post("/interview/sessions/{session_id}/verification-code/request")
    def request_verification_code(session_id: str):
        try:
            code = issue_verification_code(conn, session_id=session_id)
        except SessionLockedError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        conn.execute(
            "UPDATE interview_session SET phone_code_hash = phone_code_hash WHERE id = ?", (session_id,)
        )  # no-op：保持事务一致的占位读写点，真正落库已在 issue_verification_code 内完成
        return {"ok": True}

    class VerifyCodeRequest(BaseModel):
        code: str

    @router.post("/interview/sessions/{session_id}/verification-code/verify")
    def verify_verification_code(session_id: str, req: VerifyCodeRequest):
        try:
            verify_phone_code(conn, session_id=session_id, submitted_code=req.code)
        except VerificationLockedError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except VerificationExpiredError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        except VerificationIncorrectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True}

    @router.get("/api/interview-sessions/{session_id}/verification-code")
    def hr_view_verification_code(session_id: str, request: Request):
        row = conn.execute(
            "SELECT phone_code_hash FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise HTTPException(status_code=404, detail="验证码尚未生成")
        code = conn.execute(
            "SELECT id FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        reviewer = reviewer_of(request)
        effect_display_verification_code_to_hr(
            conn, thread_id=session_id, business_key=uuid.uuid4().hex,
            session_id=session_id, accessor=reviewer,
        )
        return {"code": _pending_verification_codes.pop(session_id, None)}

    @router.get("/api/interview-sessions/{session_id}")
    def get_interview_session_status(session_id: str):
        row = conn.execute(
            "SELECT status, sample_class, invite_expires_at FROM interview_session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="场次不存在")
        return {"session_id": session_id, "status": row[0], "sample_class": row[1], "invite_expires_at": row[2]}

    @router.get("/applications/{application_id}/interview-invite")
    def interview_invite_issue_page(application_id: str):
        return _render_static_page("interview_invite_issue.html", root_path)

    @router.get("/interview/{token}")
    def interview_consent_page(token: str):
        return _render_static_page("interview_consent.html", root_path)
```

⚠️ **Step 3 里 `hr_view_verification_code` 依赖一个明文验证码的临时内存暂存 `_pending_verification_codes`**——这是本任务必须补的一处：`issue_verification_code()` 的返回值（明文）只在调用它的那次 HTTP 请求栈里存在，`request_verification_code` 端点接住之后必须暂存起来才能被后续 `hr_view_verification_code` 读到（数据库按设计只存哈希，这是刻意的，见 Task 9 `effect_issue_verification_code` 的 docstring）。在 `create_app` 函数体顶部（`router = APIRouter()` 之前）加一个模块级/闭包级字典：

```python
    _pending_verification_codes: dict[str, str] = {}
```

并把 `request_verification_code` 端点里那句无意义的 no-op UPDATE 替换成真正把明文存进这个字典：

```python
    @router.post("/interview/sessions/{session_id}/verification-code/request")
    def request_verification_code(session_id: str):
        try:
            code = issue_verification_code(conn, session_id=session_id)
        except SessionLockedError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        _pending_verification_codes[session_id] = code
        return {"ok": True}
```

（把上面 Step 3 草稿里那句 `conn.execute("UPDATE interview_session SET phone_code_hash = phone_code_hash ...")` 整行删掉，那是设计推演过程中的错误占位，不要保留在最终代码里。）

再加一个小辅助函数（`create_app` 内，路由定义之前）：

```python
    def latest_consent_version_for(kind: str) -> str:
        from app.storage.consent_terms import latest_consent_version

        return latest_consent_version(kind)
```

在 `app/web/server.py` 顶部 import 区确认已有 `import uuid`（既有代码已 import，见文件开头 `import uuid` 一行，不需要重复加）。

- [ ] **Step 4: 写两个静态页面**

`app/web/static/interview_invite_issue.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>面试邀约签发</title>
  <base href="<!--BASE_HREF-->" />
</head>
<body>
  <h1>面试邀约签发</h1>
  <p>选择已冻结题目快照的投递，签发一次性面试邀请链接。</p>
  <form id="issue-form">
    <label>投递 ID: <input name="application_id" required /></label>
    <label>题目快照版本: <input name="prep_snapshot_version" type="number" required /></label>
    <label>
      场次类型:
      <select name="sample_class">
        <option value="internal_sim">内部模拟</option>
        <option value="live">真实候选人</option>
      </select>
    </label>
    <button type="submit">签发</button>
  </form>
  <pre id="result"></pre>
  <script>
    document.getElementById('issue-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = new FormData(e.target);
      const applicationId = form.get('application_id');
      const requestId = crypto.randomUUID();
      const resp = await fetch(`api/applications/${applicationId}/interview-sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prep_snapshot_version: Number(form.get('prep_snapshot_version')),
          sample_class: form.get('sample_class'),
          request_id: requestId,
        }),
      });
      document.getElementById('result').textContent = JSON.stringify(await resp.json(), null, 2);
    });
  </script>
</body>
</html>
```

`app/web/static/interview_consent.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>AI 结构化面试 - 同意与核验</title>
  <base href="<!--BASE_HREF-->" />
</head>
<body>
  <h1>进入 AI 结构化面试前，请确认以下事项</h1>
  <div id="consent-section"></div>
  <div id="verify-section" style="display:none">
    <p>请输入 HR 告知您的 6 位验证码：</p>
    <input id="code-input" maxlength="6" />
    <button id="verify-btn">提交</button>
  </div>
  <pre id="status"></pre>
  <script>
    const token = window.location.pathname.split('/').pop();
    let sessionId = null;

    async function openInvite() {
      const resp = await fetch(`interview/invite/${token}`);
      const data = await resp.json();
      if (!resp.ok) {
        document.getElementById('status').textContent = data.detail || '链接已失效';
        return;
      }
      sessionId = data.session_id;
      const section = document.getElementById('consent-section');
      data.consent_terms.forEach((term) => {
        const btn = document.createElement('button');
        btn.textContent = `同意 ${term.kind}`;
        btn.onclick = () => submitConsent(term.kind, 'accepted');
        section.appendChild(btn);
      });
    }

    async function submitConsent(kind, result) {
      await fetch(`interview/sessions/${sessionId}/consent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, result }),
      });
      document.getElementById('verify-section').style.display = 'block';
    }

    document.getElementById('verify-btn').addEventListener('click', async () => {
      const code = document.getElementById('code-input').value;
      const resp = await fetch(`interview/sessions/${sessionId}/verification-code/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      const data = await resp.json();
      document.getElementById('status').textContent = resp.ok ? '核验通过，即将进入面试' : (data.detail || '验证码错误');
    });

    openInvite();
  </script>
</body>
</html>
```

在 `create_app` 内、既有 `@router.get("/")` 路由之前，找到 `app.include_router(router, prefix=root_path)` 那一行——不需要改动，新增的 `@router.get(...)` 路由已经通过同一个 `router` 对象注册，会自动带上 `root_path` 前缀。

- [ ] **Step 5: 跑测试，按报错迭代修正**

Run: `pytest tests/test_invite_web_e2e.py -v`

这一步大概率需要 2-3 轮迭代（`outbound_enabled` 闭包变量名是否与文件里实际的变量一致、`channel`/`recorder`/`gateway` 等闭包变量的真实名字、`BaseModel` 的 import 是否已存在等）——每次失败先 `grep -n "def create_app" -A 30 app/web/server.py` 核对闭包变量的真实名字，不要凭空假设。

Expected: 最终全部 PASS。

- [ ] **Step 6: 跑全量测试防回归**

Run: `pytest tests/ -v`
Expected: 全部 PASS（含 U1/U2 已交付的测试与本单元全部新增测试）。

- [ ] **Step 7: Commit**

```bash
git add app/web/server.py app/web/static/interview_invite_issue.html \
  app/web/static/interview_consent.html tests/test_invite_web_e2e.py
git commit -m "feat(voice-interview): U3 HR 签发页与候选人端路由，完整 e2e 打通"
```

---

## 范围外与登记

- **4.8 短信通道自动发送**：`effect_send_verification_code` 只留接口抛 `NotImplementedError`，不接线、不消费任何供应商 API——OQ-10（门禁口径：自动发送是否算"已确认邀约的从属动作"）与短信通道采购均未决。判据见 tasks.md 4.8 原文与 design.md Open Questions OQ-10。
- **4.9/4.10 的两个不可代开关本身**：`live_interview_enabled`、`candidate-contact-vault` 开关的**开启**动作不属本计划范围（CLAUDE.md 决策代理表"候选人对外通道的开关"不可代）；本计划只交付两道闸的**结构性求值代码**，默认关闭。
- **`candidate-contact-vault` 模块本身**：由姊妹变更包 `interview-scheduling` 交付（`app/storage/contact_vault.py`），本计划只做容错适配（Task 3 `contact_source.py`），不实现该模块。判据：`openspec/changes/interview-scheduling/tasks.md` 4.1-4.4 勾选后，`app.storage.contact_source.is_contact_vault_available()` 会自动开始返回真实求值结果，无需回来改本单元代码。
- **HR 页面的鉴权**：现有 `PROTECTED_PATH_PREFIXES`（`app/middleware/auth.py`）未覆盖 `/api/applications/{id}/interview-sessions` 与 `/api/interview-sessions/*`——本计划不修改鉴权中间件的保护前缀列表（改动鉴权范围超出本交付单元，需要单独评估对既有页面的影响）。**登记为技术债**：U3 合并后，`/api/interview-sessions*` 与 `/applications/*/interview-invite` 应补进 `PROTECTED_PATH_PREFIXES`，避免未登录访问；候选人端 `/interview/invite/{token}`、`/interview/sessions/{id}/*` 路径本身按设计不需要登录（令牌即凭证），不受此项影响。
