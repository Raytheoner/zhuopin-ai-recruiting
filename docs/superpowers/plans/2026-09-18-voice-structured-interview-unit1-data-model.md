# 语音结构化面试 · 交付单元 U1（面试域数据模型：建表 ＋ 评分审计接线 ＋ 留存字段）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `app/storage/db.py` 的 `SCHEMA` 追加面试域所需的 8 张全新表（`prep_snapshot`／`prep_question`／`interview_session`／`interview_consent`／`identity_check`／`interview_turn`／`interview_recording_deletion`／`interview_access_log`），把 `analysis_run.run_type` 的语义约定扩展出 `interview` 取值，把 `criterion_score.evidence_ref` 的 JSON 编码约定扩展出 `interview_turn` 类型（含"turn 存在＋偏移合法"的校验函数），并交付三个面试域 AI 调用输入 Pydantic schema（`PrepInput`／`FollowUpInput`／`ScoreInput`，结构性排除候选人身份／核验／声学字段）——**全程零数据迁移**：新表一律 `CREATE TABLE IF NOT EXISTS`，`.51` 现网 `demo.db` 既有表一行不改。

**Architecture:** 全部 DDL 追加进 `app/storage/db.py` 的 `SCHEMA` 常量末尾，不进 `_ADDED_COLUMNS` 加列路径（该路径只服务"老库缺列"，新表不需要）。表间关系：`prep_snapshot` 挂在 `application` 下（一个投递可有多个版本，唯一约束在 `(application_id, version)`），`prep_question` 挂在 `prep_snapshot` 下（唯一约束在 `(snapshot_id, seq)`）；`interview_session` 只存 `prep_snapshot_version`（裸整数，不建跨表复合外键——与 `screening_flag.profile_version` 同一手法，版本号语义关联但不强制引用完整性）；`interview_consent`／`identity_check`／`interview_turn`／`interview_recording_deletion`／`interview_access_log` 均以 `session_id` 挂在 `interview_session` 下。`analysis_run.run_type` 不加列，新增 `"interview-"` 前缀映射（与既有 `parse-`/`rank-` 同一机制）；`criterion_score.evidence_ref` 新增 `{"type":"interview_turn","id","start","end","quote"}` 的可选 JSON 编码（旧的 `{"span_id","start","end"}` 编码不受影响，两者由 `parse_evidence_ref` 按 `type` 键分派）。三个 AI 输入 schema 用 `model_config = ConfigDict(extra="forbid")` 做结构性白名单，字段集合由测试反射核对不含身份/核验/声学关键词。

**Tech Stack:** Python 3.14（`./venv`）· SQLite（标准库 `sqlite3`，WAL + `PRAGMA foreign_keys=ON`）· Pydantic 2.x · pytest（`requirements.txt` diff 为空，全部复用已装依赖）

**Spec:**
- `openspec/changes/voice-structured-interview/design.md`（决策 D4 数据模型、D17 追问选择纯函数、D18 留存起步值、D19 语音主机无库访问、Migration Plan 第 1 条）
- `openspec/changes/voice-structured-interview/specs/interview-invite-and-consent/spec.md`（一次性邀请令牌、双同意、手机号验证码弱核验、身份核验与评分链路物理隔离）
- `openspec/changes/voice-structured-interview/specs/interview-prep-question-engine/spec.md`（按画像与简历弱点生成、难度曲线、业务经理确认冻结、快照版本化）
- `openspec/changes/voice-structured-interview/specs/interview-recording-retention/spec.md`（留存期限建立时固定、到期删除留痕、访问留痕）
- `openspec/changes/voice-structured-interview/specs/interview-scorecard/spec.md`（逐维评分带 turn 回指、evidence 非空、声学信号只展示不计分）
- `openspec/changes/voice-structured-interview/specs/live-voice-interview-session/spec.md`（turn 结构、打断记录、文本作答降级、简历数据不进语音主机）
- `openspec/changes/voice-structured-interview/specs/m3-compliance-assertions/spec.md`（评分证据可回溯、身份核验与评分隔离——本单元只交付被断言检查的表结构，CI 断言本身属 U7）
- `openspec/changes/voice-structured-interview/tasks.md` 第 2 章（2.1–2.8）

## Global Constraints

以下条目从 `CLAUDE.md`（工程铁律、合规红线）逐字复制，每条附"本单元与这条的关系"。每个 Task 的验收隐含包含本节全部内容。

### 工程铁律（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   **不适用，理由**：U1 只建表和写纯函数工具（`run_type.py`／`evidence_ref.py` 的扩展），不新增任何 `effect_*` 节点、不接入 LangGraph 图。真正写 `prep_snapshot`/`interview_session`/`interview_turn` 业务行的 `effect_freeze_prep`/`effect_issue_invite`/`effect_persist_turn` 等节点属 U2/U3/U4/U5（design D20），届时幂等键与事务写法在那几个交付单元的计划里落地。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
   **本单元与这条的关系**：本单元新增的 `app/audit/evidence_ref.py` 扩展函数（`format_interview_turn_evidence_ref`/`validate_interview_turn_evidence`）与 `app/schemas/interview_ai_input.py` 的三个 Pydantic 模型全部是无副作用纯代码（`validate_interview_turn_evidence` 读库但不写库，供未来 U5 的 `compute_align`/`compute_score` 调用）。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
   **本单元与这条的关系**：`analysis_run` 表结构本节不改（M1 已建齐十四列），只追加 `run_type` 的语义约定（`"interview-"` 前缀 → `RUN_TYPE_INTERVIEW`），供 U2/U4/U5 的 prep/追问/评分三处 AI 调用共用同一张持久化表。
4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。
   **本单元与这条的关系**：本单元是这条铁律"面试 turn 回指"分支的存储层落点——`criterion_score.evidence_ref` 现有 `NOT NULL` + 空白 `CHECK` 完全不改，新增的 `{"type":"interview_turn","id","start","end","quote"}` 编码只是让"面试 turn 的 offset"变得可被结构化解析与校验（`validate_interview_turn_evidence` 校验 turn 存在与偏移合法）。真正写入 `criterion_score` 行的评分逻辑属 U5。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。供应商不提供版本号快照时，必须从 API 响应里取回实际的 `model` 字段并持久化。
   **不适用，理由**：本单元不发起任何 LLM 调用。`PrepInput`/`FollowUpInput`/`ScoreInput` 只是结构契约，供 U2（prep 生成）、U4（追问选择）、U5（评分）未来调用网关时作为输入形状；`temperature=0` 与模型版本回存已由既有 LLM 网关与 `AuditHook`（M1/M2 已完成）保证，本单元不涉及。
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
   **不适用，理由**：本单元不涉及企微回调；本包候选人邀约走一次性链接（U3），不经企微。
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。
   **不适用，理由**：本单元不改动依赖版本，`requirements.txt` diff 为空；既有版本锁定不受影响。

### 合规红线（逐字，与本单元相关的条目）

- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。
  **本单元与这条的关系**：`identity_check` 表结构上**没有**图像列（Task 3 建表 + 反证测试守护）；`interview_turn.acoustic_ref` 是只读展示字段，`ScoreInput`/`ScoreInputTurn`（Task 7）结构上不含 `acoustic_ref`/`asr_confidence` 等声学与转写置信度字段，由字段白名单反射测试守护。
- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
  **不适用，理由**：本单元新建的 8 张表都不写 `rejection_record`，也不产生任何淘汰判定；`interview_session`/`prep_snapshot` 的状态机（`pending`/`draft` 等）只描述面试流程自身的进度，不驱动投递阶段流转。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
  **不适用，理由**：AI 生成标识是 U2（题目确认页/候选人端）与 U3（邀约文案）的前端与消息文案职责，不落在数据表结构层；`prep_question.origin` 列（`ai`/`ai_edited`）为该标识提供了存储层的事实来源。
- **模型全部走境内，简历数据不出境。**
  **不适用，理由**：本单元不发起任何模型调用，也不传输任何数据出境。
- **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。
  **本单元与这条的关系**：`PrepInput`（Task 7）的输入形状只包含冻结画像／rubric／简历评分摘录，不包含历史录用结果字段，结构上排除了这类信号混入 prep 生成输入的可能。

### 本单元的任务专属约束（opener `0918AA`，逐字整理）

1. 全部新表 `CREATE TABLE IF NOT EXISTS`，⛔ 不进 `app/storage/db.py` 的 `_ADDED_COLUMNS` 加列路径；`.51` 现网 `demo.db` 既有表一行不改，无数据迁移——Task 8 用复制的 `.51` 库结构核实这一点。
2. `identity_check` 表 MUST NOT 含图像列或任何可用于评分的列（Task 3 建表 + 源码级反证测试）。
3. `criterion_score.evidence_ref` 解析工具函数支持 `{type:'interview_turn', id, start, end, quote}`，并校验 turn 存在与偏移合法；测试覆盖 resume（旧编码）与 interview_turn（新编码）两种类型（Task 6）。
4. 三个 AI 输入 Pydantic schema 的字段白名单里没有姓名／手机号／核验字段／声学字段——测试用反射断言字段集合（Task 7）。
5. 交付前自查：`grep -c '^### Task ' <计划文件>` 必须 ≥1 且等于实际任务数。

**reviewer 机械判据（汇总）**：
- `_ADDED_COLUMNS` 元组本单元 diff 里一个字节都不改。
- 本单元 diff 里不出现任何 `ALTER TABLE`。
- `PRAGMA table_info(identity_check)` 的列名集合与 `sqlite_master.sql` 原文里都不出现 `image`/`photo`/`face` 子串。
- `app/schemas/interview_ai_input.py` 三个模型的 `model_fields` 集合里都不出现 `name`/`phone`/`candidate`/`identity`/`verified`/`verification`/`acoustic`/`face`/`image`/`photo`/`audio` 子串。
- `requirements.txt`（及 `pyproject.toml`，若存在）diff 为空。

---

## 建议拆段点（`lane-dispatch`「长 run-build 拆段」，本计划 8 个 Task）

- **Segment A：Task 1–3**（prep 域两张表 + interview_session + 同意/核验两张表，含最复杂的 CHECK 设计）
- **Segment B：Task 4–5**（interview_turn + 留存删除/访问留痕两张表）
- **Segment C：Task 6–8**（run_type/evidence_ref 工具函数扩展 + AI 输入 schema + 跨库回归测试收口）

---

## 前置：确认当前 `app/storage/db.py` 的 SCHEMA 结尾行号未漂移

以下任务引用的锚点文本基于 2026-09-18 的文件状态。执行者动手前先跑一次确认：

```bash
grep -n "CREATE INDEX IF NOT EXISTS idx_hr_session_account" app/storage/db.py
wc -l app/storage/db.py
```

若锚点文本找不到，说明 `SCHEMA` 结尾已变化——以 `grep` 定位到的、**当前 `SCHEMA` 常量里最后一条语句**为准，本计划所有"追加到 SCHEMA 末尾"的操作都改为追加在那条语句之后、收尾三重引号 `"""` 之前，不要按本计划写的绝对行号硬改。

---

### Task 1: `prep_snapshot` / `prep_question`（tasks 2.1）

**Files:**
- Modify: `app/storage/db.py`（在 `SCHEMA` 常量末尾追加，`CREATE INDEX IF NOT EXISTS idx_hr_session_account ...;` 之后、收尾 `"""` 之前）
- Create: `tests/test_db_m3_schema.py`（本任务建文件，后续任务继续往里加测试函数）
- Create: `tests/fixtures/zp51_demo_db_schema_pre_m3.sql`（Task 8 使用的"老库"快照，本任务第一步生成，**必须在修改 `SCHEMA` 之前生成**，否则快照会带上本单元自己加的表）

**Interfaces:**
- Consumes: 无（本任务是本计划第一个写表的任务）
- Produces: 表 `prep_snapshot(id, application_id, version, profile_version, resume_run_id, gen_run_id, confirmed_by, confirmed_at, status, created_at)`；表 `prep_question(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin, ai_text, created_at)`。后续任务（Task 4 的 `interview_turn.question_id`）会 `REFERENCES prep_question(id)`，字段名与类型以此为准。

**设计说明**：`prep_snapshot` 没有走"snapshot_id 复合自然键当主键"的路子（不同于 `resume_text_span`），而是给了独立的 `id TEXT PRIMARY KEY`——因为 `prep_question.snapshot_id`、`interview_session` 都要引用它，且 `(application_id, version)` 只是它的业务唯一键，不是最佳的外键载体（外键列越少，后续表越简单）。`resume_run_id` 可空：spec「简历评分尚未完成」场景下 prep 只按画像生成通用题目，这种情况下没有简历评分 run 可关联。`gen_run_id` 不可空：无论是否有简历弱点输入，prep 生成本身都是一次 AI 调用，必须留痕。

- [ ] **Step 1: 先生成"老库"快照 fixture（在改动 `SCHEMA` 之前跑，顺序不能颠倒）**

```bash
./venv/bin/python3 -c "
from app.storage.db import get_connection, init_schema
conn = get_connection('/tmp/pre_m3_snapshot.db')
init_schema(conn)
rows = conn.execute(
    \"SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' \"
    \"ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name\"
).fetchall()
with open('tests/fixtures/zp51_demo_db_schema_pre_m3.sql', 'w', encoding='utf-8') as f:
    f.write(';\n\n'.join(r[0] for r in rows) + ';\n')
"
rm -f /tmp/pre_m3_snapshot.db
wc -l tests/fixtures/zp51_demo_db_schema_pre_m3.sql
```

⚠️ **排序必须是"先全部表、再全部索引，组内再按名字排"**（`ORDER BY CASE type ... END, name`），⛔ 不能单纯 `ORDER BY name`——单纯按名字排会把 `idx_pending_approval_content` 这类索引排在它所属的表 `pending_approval` 前面（字母 `i` < `p`），`executescript` 跑到该索引语句时表还不存在，直接报 `no such table`（本计划写作时已用这条命令实测跑通全部 8 张表 + 老库升级，此前用纯 `ORDER BY name` 版本复现过这个失败，已在此修正）。

Expected: 输出一个行数（约 420-440 行左右均属正常，取决于当前 `SCHEMA` 的确切表数）。这份文件代表"本单元落地前，`.51` 现网库应有的完整表结构"，Task 8 的老库升级测试要用它。

- [ ] **Step 2: 写 `tests/test_db_m3_schema.py`，先写 prep 域两张表的失败测试**

```python
"""M3 U1 面试域数据模型：新库建表齐全、老库升级既有表不变、全部 CHECK 反证。

本文件随 tasks.md 2.1–2.8 逐任务追加：
  ① 新库 fresh init_schema() 后逐表齐全 —— 各任务各自建表时先写
  ② 老库（复制 .51 demo.db 结构）升级后既有表一行不改 —— Task 8 统一补
  ③ 全部新增 CHECK 的反证（直接 INSERT，绕过应用层）—— 各表在各自任务里先写
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "m3.db"))
    init_schema(c)
    return c


def _seed_job_candidate_resume_application(
    conn, job_id="j1", candidate_id="c1", resume_id="r1", application_id="app1"
):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'approved')", (job_id,)
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (candidate_id,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'sha-1', 'hr-1')",
        (resume_id, job_id),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (application_id, candidate_id, job_id, resume_id),
    )
    conn.commit()


def _seed_analysis_run(conn, run_id="run1", prompt_version="interview-prep-v1"):
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES (?, 'deepseek-chat', ?, 0.0, 'hash-1', '{}')",
        (run_id, prompt_version),
    )
    conn.commit()


# ── prep_snapshot / prep_question（tasks 2.1）────────────────────────────


def test_prep_snapshot_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "prep_snapshot")
    assert _columns(conn, "prep_snapshot") == {
        "id", "application_id", "version", "profile_version", "resume_run_id",
        "gen_run_id", "confirmed_by", "confirmed_at", "status", "created_at",
    }


def test_prep_snapshot_unique_on_application_and_version(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
            "VALUES ('snap-2', 'app1', 1, 1, 'run1')"
        )


def test_prep_snapshot_allows_multiple_versions_per_application(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn, run_id="run1")
    _seed_analysis_run(conn, run_id="run2")
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-2', 'app1', 2, 1, 'run2')"
    )
    conn.commit()


def test_prep_snapshot_resume_run_id_is_nullable(conn):
    """spec「简历评分尚未完成」场景：prep 只按画像生成通用题目，无简历 run 可关联。"""
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT resume_run_id FROM prep_snapshot WHERE id='snap-1'"
    ).fetchone()
    assert row[0] is None


def test_prep_snapshot_status_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
            "VALUES ('snap-bad', 'app1', 1, 1, 'run1', 'published')"
        )


@pytest.mark.parametrize("status", ["draft", "frozen", "expired"])
def test_prep_snapshot_status_check_accepts_three_values(conn, status):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES (?, 'app1', 1, 1, 'run1', ?)",
        (f"snap-{status}", status),
    )
    conn.commit()


def test_prep_snapshot_defaults_to_draft(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES ('snap-1', 'app1', 1, 1, 'run1')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT status FROM prep_snapshot WHERE id='snap-1'"
    ).fetchone()[0] == "draft"


def test_prep_question_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "prep_question")
    assert _columns(conn, "prep_question") == {
        "id", "snapshot_id", "seq", "dimension", "difficulty", "text",
        "rubric_json", "follow_ups_json", "rationale", "origin", "ai_text", "created_at",
    }


def _seed_prep_snapshot(conn, snapshot_id="snap-1", application_id="app1"):
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id) "
        "VALUES (?, ?, 1, 1, 'run1')",
        (snapshot_id, application_id),
    )
    conn.commit()


def test_prep_question_unique_on_snapshot_and_seq(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale) "
        "VALUES ('q-1', 'snap-1', 1, 'diag_stack', 'medium', '问题', '{}', '[]', '依据画像')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_question "
            "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale) "
            "VALUES ('q-2', 'snap-1', 1, 'toolchain', 'easy', '另一题', '{}', '[]', '依据画像')"
        )


def test_prep_question_origin_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prep_question "
            "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin) "
            "VALUES ('q-bad', 'snap-1', 1, 'diag_stack', 'medium', '问题', '{}', '[]', '依据画像', 'human_written')"
        )


@pytest.mark.parametrize("origin", ["ai", "ai_edited"])
def test_prep_question_origin_check_accepts_two_values(conn, origin):
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin) "
        "VALUES (?, 'snap-1', 1, 'diag_stack', 'medium', '问题', '{}', '[]', '依据画像', ?)",
        (f"q-{origin}", origin),
    )
    conn.commit()


def test_prep_question_ai_text_preserves_original_after_edit(conn):
    """spec「业务经理修改题面」：人工改过的题标 ai_edited，原 AI 文本仍可追溯。"""
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, "
        "origin, ai_text) "
        "VALUES ('q-1', 'snap-1', 1, 'diag_stack', 'medium', '人工改写后的题面', '{}', '[]', '依据画像', "
        "'ai_edited', 'AI 原始生成的题面')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT text, ai_text FROM prep_question WHERE id='q-1'"
    ).fetchone()
    assert row[0] == "人工改写后的题面"
    assert row[1] == "AI 原始生成的题面"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -v`
Expected: 全部 FAIL，报 `sqlite3.OperationalError: no such table: prep_snapshot`（或 `prep_question`）。

- [ ] **Step 4: 在 `app/storage/db.py` 的 `SCHEMA` 末尾追加两张表**

在 `CREATE INDEX IF NOT EXISTS idx_hr_session_account ON hr_session (hr_account_id);` 之后、收尾 `"""` 之前插入：

```sql

-- ─────────────────────────────────────────────────────────────────────────
-- 以下 8 张表属变更包 voice-structured-interview（交付单元 U1）。全部新表，
-- 走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：加列路径只服务
-- "老库缺列"这一种情况，新表不需要它。.51 现网 demo.db 既有表一行不改，
-- 无数据迁移（design.md Migration Plan 第 1 条）。
-- ─────────────────────────────────────────────────────────────────────────

-- prep 出题快照（interview-prep-question-engine spec「题目快照版本化且可
-- 追溯到输入」）。resume_run_id 可空：spec「简历评分尚未完成」场景下 prep
-- 只按画像生成通用题目，没有简历评分 run 可关联；gen_run_id 不可空——不管
-- 有没有简历弱点输入，prep 生成本身都是一次 AI 调用，必须留痕（工程铁律 3）。
-- status 三态对应 spec「业务经理确认后才冻结」的状态机：draft（待确认）/
-- frozen（已冻结，开场校验只认这个状态）/ expired（画像升版后旧版本过期）。
CREATE TABLE IF NOT EXISTS prep_snapshot (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    version INTEGER NOT NULL,
    profile_version INTEGER NOT NULL,
    resume_run_id TEXT REFERENCES analysis_run(id),
    gen_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    confirmed_by TEXT,
    confirmed_at TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'frozen', 'expired')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prep_snapshot_application_version
    ON prep_snapshot (application_id, version);

-- prep 题目（同一 spec「按画像与简历弱点生成题目」「难度曲线」）。origin 记
-- 「AI 生成」还是「AI 生成、人工修改」（spec「AI 生成标识」的存储层落点）；
-- ai_text 只在 origin='ai_edited' 时有值，保留人工改写前的原文可追溯。
-- (snapshot_id, seq) 唯一：同一份快照内题序不重复，也是 live 段"按冻结题序
-- 出题"的天然索引。
CREATE TABLE IF NOT EXISTS prep_question (
    id TEXT PRIMARY KEY NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES prep_snapshot(id),
    seq INTEGER NOT NULL,
    dimension TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    text TEXT NOT NULL,
    rubric_json TEXT NOT NULL,
    follow_ups_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'ai' CHECK (origin IN ('ai', 'ai_edited')),
    ai_text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prep_question_snapshot_seq
    ON prep_question (snapshot_id, seq);
```

- [ ] **Step 5: 运行测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py tests/fixtures/zp51_demo_db_schema_pre_m3.sql
git commit -m "feat(m3-u1): add prep_snapshot and prep_question tables"
```

---

### Task 2: `interview_session`（tasks 2.2）

**Files:**
- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m3_schema.py`

**Interfaces:**
- Consumes: 表 `application`（Task 1 已用过的 `_seed_job_candidate_resume_application` helper）
- Produces: 表 `interview_session(id, application_id, prep_snapshot_version, invite_token_hash, invite_expires_at, resume_token_hash, phone_verified_at, phone_attempts, recording_uri, retention_until, retention_policy_version, sample_class, status, created_at)`。后续任务（Task 3–5）的 `interview_consent`/`identity_check`/`interview_turn`/`interview_recording_deletion`/`interview_access_log` 均以 `session_id TEXT ... REFERENCES interview_session(id)` 挂在本表下。

**设计说明**：`prep_snapshot_version` 是裸 `INTEGER`，不建 `(application_id, prep_snapshot_version) → prep_snapshot(application_id, version)` 的复合外键——与 `screening_flag.profile_version` 同一手法（M2 先例）：版本号在语义上关联但不做引用完整性强制，因为签发邀约时 `prep_snapshot` 必然已冻结存在，复合外键只会让测试 fixture 的搭建复杂度上升而不增加真实保护。`invite_token_hash` 可空（会话建立与令牌签发可能是两个先后步骤，属 U3 职责）但唯一——同一哈希不能对应两个场次。

- [ ] **Step 1: 在 `tests/test_db_m3_schema.py` 追加失败测试**

```python
# ── interview_session（tasks 2.2）───────────────────────────────────────


def _seed_interview_session(
    conn, session_id="sess-1", application_id="app1",
    retention_until="2026-12-01 00:00:00", sample_class="internal_sim",
):
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, retention_until, "
        "retention_policy_version, sample_class) "
        "VALUES (?, ?, 1, ?, 'v1', ?)",
        (session_id, application_id, retention_until, sample_class),
    )
    conn.commit()


def test_interview_session_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_session")
    assert _columns(conn, "interview_session") == {
        "id", "application_id", "prep_snapshot_version", "invite_token_hash",
        "invite_expires_at", "resume_token_hash", "phone_verified_at",
        "phone_attempts", "recording_uri", "retention_until",
        "retention_policy_version", "sample_class", "status", "created_at",
    }


def test_interview_session_retention_until_cannot_be_null(conn):
    """recording-retention spec「留存期限在场次建立时固定」：retention_until
    MUST 在建立时非空写入，不允许留空等以后补。"""
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-bad', 'app1', 1, NULL, 'v1', 'internal_sim')"
        )


def test_interview_session_retention_policy_version_cannot_be_null(conn):
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-bad', 'app1', 1, '2026-12-01 00:00:00', NULL, 'internal_sim')"
        )


def test_interview_session_sample_class_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-bad', 'app1', 1, '2026-12-01 00:00:00', 'v1', 'staged')"
        )


@pytest.mark.parametrize("sample_class", ["internal_sim", "live"])
def test_interview_session_sample_class_check_accepts_two_values(conn, sample_class):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn, session_id=f"sess-{sample_class}", sample_class=sample_class)


def test_interview_session_status_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, retention_until, "
            "retention_policy_version, sample_class, status) "
            "VALUES ('sess-bad', 'app1', 1, '2026-12-01 00:00:00', 'v1', 'internal_sim', 'archived')"
        )


@pytest.mark.parametrize(
    "status", ["pending", "in_progress", "completed", "interrupted", "abandoned", "locked"]
)
def test_interview_session_status_check_accepts_six_values(conn, status):
    _seed_job_candidate_resume_application(conn)
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, retention_until, "
        "retention_policy_version, sample_class, status) "
        "VALUES (?, 'app1', 1, '2026-12-01 00:00:00', 'v1', 'internal_sim', ?)",
        (f"sess-{status}", status),
    )
    conn.commit()


def test_interview_session_status_defaults_to_pending(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    assert conn.execute(
        "SELECT status FROM interview_session WHERE id='sess-1'"
    ).fetchone()[0] == "pending"


def test_interview_session_invite_token_hash_is_unique(conn):
    _seed_job_candidate_resume_application(conn, application_id="app1")
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j2', '供应链总监', 'approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r2', 'j2', 'synthetic', 'b.pdf', 'sha-2', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app2', 'c2', 'j2', 'r2', 'initial')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, invite_token_hash, retention_until, "
        "retention_policy_version, sample_class) "
        "VALUES ('sess-1', 'app1', 1, 'hash-same', '2026-12-01 00:00:00', 'v1', 'internal_sim')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_session "
            "(id, application_id, prep_snapshot_version, invite_token_hash, retention_until, "
            "retention_policy_version, sample_class) "
            "VALUES ('sess-2', 'app2', 1, 'hash-same', '2026-12-01 00:00:00', 'v1', 'internal_sim')"
        )


def test_interview_session_allows_multiple_null_invite_token_hash(conn):
    """令牌尚未签发时可空；SQLite 的 UNIQUE 把多个 NULL 视为互不相等
    （与 candidate.phone_hash 同一手法）。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn, session_id="sess-1")
    conn.execute(
        "INSERT INTO interview_session "
        "(id, application_id, prep_snapshot_version, retention_until, "
        "retention_policy_version, sample_class) "
        "VALUES ('sess-2', 'app1', 1, '2026-12-01 00:00:00', 'v1', 'internal_sim')"
    )
    conn.commit()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -k interview_session -v`
Expected: 全部 FAIL，报 `no such table: interview_session`。

- [ ] **Step 3: 在 `SCHEMA` 末尾（Task 1 追加内容之后）追加**

```sql

-- 面试场次（live-voice-interview-session spec「开场前置条件」；
-- interview-recording-retention spec「留存期限在场次建立时固定」）。
-- prep_snapshot_version 是裸整数，不建到 prep_snapshot 的复合外键——与
-- screening_flag.profile_version 同一手法：版本号语义关联但不强制引用
-- 完整性。retention_until / retention_policy_version 均 NOT NULL 且无默认
-- 值：留存期限必须在场次建立那一刻由应用层算好并写入，不允许留空。
-- status 六态覆盖 spec「场次状态 MUST 至少区分」的枚举。
CREATE TABLE IF NOT EXISTS interview_session (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    prep_snapshot_version INTEGER NOT NULL,
    invite_token_hash TEXT,
    invite_expires_at TEXT,
    resume_token_hash TEXT,
    phone_verified_at TEXT,
    phone_attempts INTEGER NOT NULL DEFAULT 0,
    recording_uri TEXT,
    retention_until TEXT NOT NULL,
    retention_policy_version TEXT NOT NULL,
    sample_class TEXT NOT NULL CHECK (sample_class IN ('internal_sim', 'live')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'in_progress', 'completed', 'interrupted', 'abandoned', 'locked')
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_interview_session_invite_token
    ON interview_session (invite_token_hash);

CREATE INDEX IF NOT EXISTS idx_interview_session_application
    ON interview_session (application_id);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py
git commit -m "feat(m3-u1): add interview_session table"
```

---

### Task 3: `interview_consent` / `identity_check`（tasks 2.3）

**Files:**
- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m3_schema.py`

**Interfaces:**
- Consumes: 表 `interview_session`（Task 2）
- Produces: 表 `interview_consent(session_id, kind, result, consent_version, at)`；表 `identity_check(session_id, result, checked_at)`。

**设计说明**：`interview_consent` 用复合主键 `(session_id, kind)`——与 `hard_requirement`/`resume_text_span` 同一手法，天然键就是"这个场次的这一项同意"，不设代理主键（同一场次同一 kind 出现两次就是 bug）。`identity_check` 用 `session_id` 本身做主键——一期只做手机号验证码弱核验（D13），一个场次只产生一行核验结果。

- [ ] **Step 1: 在 `tests/test_db_m3_schema.py` 追加失败测试**

```python
# ── interview_consent / identity_check（tasks 2.3）──────────────────────


def test_interview_consent_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_consent")
    assert _columns(conn, "interview_consent") == {
        "session_id", "kind", "result", "consent_version", "at",
    }


def test_interview_consent_primary_key_is_session_and_kind(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
        "VALUES ('sess-1', 'ai_interview', 'accepted', 'v1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
            "VALUES ('sess-1', 'ai_interview', 'declined', 'v1')"
        )


def test_interview_consent_allows_two_independent_kinds_per_session(conn):
    """spec「AI 面试与身份核验各自单独同意」：两项各自独立留痕。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
        "VALUES ('sess-1', 'ai_interview', 'accepted', 'v1')"
    )
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
        "VALUES ('sess-1', 'identity_check', 'accepted', 'v1')"
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM interview_consent WHERE session_id='sess-1'"
    ).fetchone()[0]
    assert count == 2


def test_interview_consent_kind_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
            "VALUES ('sess-1', 'video_interview', 'accepted', 'v1')"
        )


def test_interview_consent_result_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) "
            "VALUES ('sess-1', 'ai_interview', 'maybe', 'v1')"
        )


def test_identity_check_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "identity_check")
    assert _columns(conn, "identity_check") == {"session_id", "result", "checked_at"}


def test_identity_check_result_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', 'pending')"
        )


@pytest.mark.parametrize("result", ["pass", "fail", "skipped"])
def test_identity_check_result_check_accepts_three_values(conn, result):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', ?)", (result,)
    )
    conn.commit()


def test_identity_check_session_id_is_unique_primary_key(conn):
    """一期一个场次只产生一行核验结果（D13：一律 skipped，保留给活体/证件比对）。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', 'skipped')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO identity_check (session_id, result) VALUES ('sess-1', 'pass')"
        )


# ── m3-compliance-assertions spec「身份核验与评分隔离」的源码级反证 ───────
#
# identity_check 表结构上不允许出现图像列或任何可用于评分的列。这不是数据库
# CHECK 能表达的约束（CHECK 只能限制值域，不能限制"未来会不会加这一列"），
# 所以判据下沉到源码：直接扫描 sqlite_master.sql 的建表原文与
# PRAGMA table_info 的列名，任何一处出现 image/photo/face 子串就判违规。
# 真正把这条接入 CI 断言是 U7 tasks 8.1 的职责，这里只是 U1 自己的结构守护。


def test_identity_check_has_no_image_or_scoring_columns(conn):
    banned_substrings = ("image", "photo", "face", "video", "biometric")
    columns = _columns(conn, "identity_check")
    for column in columns:
        lowered = column.lower()
        assert not any(bad in lowered for bad in banned_substrings), (
            f"identity_check.{column} 命中禁止列名模式，疑似引入图像/生物特征列"
        )
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='identity_check'"
    ).fetchone()[0].lower()
    for bad in banned_substrings:
        assert bad not in ddl, f"identity_check 建表原文里出现了禁止词 {bad!r}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -k "consent or identity_check" -v`
Expected: 全部 FAIL，报 `no such table: interview_consent`（或 `identity_check`）。

- [ ] **Step 3: 在 `SCHEMA` 末尾（Task 2 追加内容之后）追加**

```sql

-- 双同意留痕（interview-invite-and-consent spec「AI 面试与身份核验各自
-- 单独同意」）。复合主键 (session_id, kind)：天然键就是"这个场次的这一项
-- 同意"，与 hard_requirement/resume_text_span 同一手法，不设代理主键。
CREATE TABLE IF NOT EXISTS interview_consent (
    session_id TEXT NOT NULL REFERENCES interview_session(id),
    kind TEXT NOT NULL CHECK (kind IN ('ai_interview', 'identity_check')),
    result TEXT NOT NULL CHECK (result IN ('accepted', 'declined')),
    consent_version TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, kind)
);

-- 身份核验结果（interview-invite-and-consent spec「手机号验证码弱核验」
-- D13：一期只做手机号验证码，result 一律 'skipped'，保留给活体/证件比对；
-- 弱核验的通过时刻/尝试次数记在 interview_session 上，不进本表）。
-- ⛔ 刻意不设图像列或任何评分相关列——见下方 test_identity_check_has_no_
-- image_or_scoring_columns 的源码级反证测试；未来任何人往这张表加列都会被
-- 这条测试拦下来。
CREATE TABLE IF NOT EXISTS identity_check (
    session_id TEXT PRIMARY KEY NOT NULL REFERENCES interview_session(id),
    result TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'skipped')),
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py
git commit -m "feat(m3-u1): add interview_consent and identity_check tables"
```

---

### Task 4: `interview_turn`（tasks 2.4）

**Files:**
- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m3_schema.py`

**Interfaces:**
- Consumes: 表 `interview_session`（Task 2）、表 `prep_question`（Task 1）
- Produces: 表 `interview_turn(id, session_id, seq, question_id, question_text, answer_text, answer_mode, audio_start_ms, audio_end_ms, latency_json, follow_up_of, interrupted_at_ms, asr_confidence, acoustic_ref, created_at)`。Task 6 的 `format_interview_turn_evidence_ref`/`validate_interview_turn_evidence` 会读本表的 `id`/`answer_text` 两列。

**设计说明**：`follow_up_of` 自引用 `interview_turn(id)`——记录"这条追问 turn 是针对哪条 turn 追问的"（live-voice-interview-session spec「选择预埋追问」场景：`turn 记录 follow_up_of 指向被追问的 turn`）。`question_id` 引用 `prep_question(id)`（Task 1 给 `prep_question` 加的代理主键，正是为这里准备的）。`answer_mode='text'` 时 `audio_start_ms`/`audio_end_ms` 必须为空——spec「文本作答降级」明确"文本作答的 turn MUST NOT 有音频起止"。

- [ ] **Step 1: 在 `tests/test_db_m3_schema.py` 追加失败测试**

```python
# ── interview_turn（tasks 2.4）───────────────────────────────────────────


def _seed_prep_question(conn, question_id="q-1", snapshot_id="snap-1", seq=1):
    conn.execute(
        "INSERT INTO prep_question "
        "(id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale) "
        "VALUES (?, ?, ?, 'diag_stack', 'medium', '问题文本', '{}', '[]', '依据画像')",
        (question_id, snapshot_id, seq),
    )
    conn.commit()


def _seed_turn_chain(conn):
    """搭好 interview_turn 需要的完整前置链：job/candidate/resume/application
    → prep_snapshot/prep_question → interview_session。"""
    _seed_job_candidate_resume_application(conn)
    _seed_analysis_run(conn)
    _seed_prep_snapshot(conn)
    _seed_prep_question(conn)
    _seed_interview_session(conn)


def test_interview_turn_table_exists_with_expected_columns(conn):
    _seed_turn_chain(conn)
    assert _table_exists(conn, "interview_turn")
    assert _columns(conn, "interview_turn") == {
        "id", "session_id", "seq", "question_id", "question_text", "answer_text",
        "answer_mode", "audio_start_ms", "audio_end_ms", "latency_json",
        "follow_up_of", "interrupted_at_ms", "asr_confidence", "acoustic_ref", "created_at",
    }


def test_interview_turn_unique_on_session_and_seq(conn):
    _seed_turn_chain(conn)
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_mode) "
        "VALUES ('turn-1', 'sess-1', 1, 'q-1', '问题文本', 'voice')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode) "
            "VALUES ('turn-2', 'sess-1', 1, 'q-1', '问题文本', 'text')"
        )


def test_interview_turn_answer_mode_check_rejects_unknown_value(conn):
    _seed_turn_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode) "
            "VALUES ('turn-bad', 'sess-1', 1, 'q-1', '问题文本', 'video')"
        )


def test_interview_turn_text_mode_rejects_audio_offsets(conn):
    """live-voice-interview-session spec「文本作答降级」：文本作答的 turn
    MUST NOT 有音频起止。"""
    _seed_turn_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode, audio_start_ms) "
            "VALUES ('turn-bad', 'sess-1', 1, 'q-1', '问题文本', 'text', 100)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_turn "
            "(id, session_id, seq, question_id, question_text, answer_mode, audio_end_ms) "
            "VALUES ('turn-bad2', 'sess-1', 1, 'q-1', '问题文本', 'text', 3000)"
        )


def test_interview_turn_voice_mode_allows_audio_offsets(conn):
    _seed_turn_chain(conn)
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_text, answer_mode, "
        "audio_start_ms, audio_end_ms) "
        "VALUES ('turn-1', 'sess-1', 1, 'q-1', '问题文本', '回答文本', 'voice', 0, 3000)"
    )
    conn.commit()


def test_interview_turn_follow_up_of_points_to_another_turn(conn):
    """spec「选择预埋追问」：turn 记录 follow_up_of 指向被追问的 turn。"""
    _seed_turn_chain(conn)
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_mode) "
        "VALUES ('turn-1', 'sess-1', 1, 'q-1', '问题文本', 'voice')"
    )
    conn.execute(
        "INSERT INTO interview_turn "
        "(id, session_id, seq, question_id, question_text, answer_mode, follow_up_of) "
        "VALUES ('turn-2', 'sess-1', 2, 'q-1', '追问文本', 'voice', 'turn-1')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT follow_up_of FROM interview_turn WHERE id='turn-2'"
    ).fetchone()
    assert row[0] == "turn-1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -k interview_turn -v`
Expected: 全部 FAIL，报 `no such table: interview_turn`。

- [ ] **Step 3: 在 `SCHEMA` 末尾（Task 3 追加内容之后）追加**

```sql

-- 面试逐轮问答（live-voice-interview-session spec「全程录制与 turn 对齐」
-- 「打断处理」「文本作答降级」）。question_id 引用 prep_question(id)——
-- 冻结快照里的具体某一题；follow_up_of 自引用本表，记录"这条追问针对哪条
-- turn"。answer_mode='text' 时 audio_start_ms/audio_end_ms 必须为空的
-- CHECK 是 spec「文本作答的 turn MUST NOT 有音频起止」的存储层落点。
-- acoustic_ref 只读展示字段（合规红线「声学信号只展示不计分」），文本作答
-- turn 恒为空，不受 CHECK 约束（列本身允许 NULL，评分输入结构性不读它，
-- 见 app/schemas/interview_ai_input.py 的 ScoreInputTurn）。
CREATE TABLE IF NOT EXISTS interview_turn (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES interview_session(id),
    seq INTEGER NOT NULL,
    question_id TEXT NOT NULL REFERENCES prep_question(id),
    question_text TEXT NOT NULL,
    answer_text TEXT,
    answer_mode TEXT NOT NULL CHECK (answer_mode IN ('voice', 'text')),
    audio_start_ms INTEGER,
    audio_end_ms INTEGER,
    latency_json TEXT,
    follow_up_of TEXT REFERENCES interview_turn(id),
    interrupted_at_ms INTEGER,
    asr_confidence REAL,
    acoustic_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        answer_mode != 'text'
        OR (audio_start_ms IS NULL AND audio_end_ms IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_interview_turn_session_seq
    ON interview_turn (session_id, seq);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py
git commit -m "feat(m3-u1): add interview_turn table"
```

---

### Task 5: `interview_recording_deletion` / `interview_access_log`（tasks 2.5）

**Files:**
- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m3_schema.py`

**Interfaces:**
- Consumes: 表 `interview_session`（Task 2）
- Produces: 表 `interview_recording_deletion(session_id, deleted_at, scope, reason, actor)`；表 `interview_access_log(id, accessor, session_id, access_type, at)`。

**设计说明**：`interview_recording_deletion.session_id` 直接做主键——"session_id 唯一"（tasks 2.5 原话），一个场次的录音只会被彻底删除一次，重复扫描不产生第二行（interview-recording-retention spec「重复扫描」场景）。`interview_access_log` 上 `session_id` 刻意不加外键——与 `resume_access_log` 同一形态：留痕表按事件记事实，把它的可写性绑在业务表上会让"留痕写不进去"变成"读取整个失败"，而这条**应该**由应用层的写入顺序保证（先留痕后返回内容），不该由外键去意外触发。`access_type` 四态对应 spec 里明确提到的四种访问入口：录音回放、查看转写、查看 ScoreCard、导出一致性评估包。

- [ ] **Step 1: 在 `tests/test_db_m3_schema.py` 追加失败测试**

```python
# ── interview_recording_deletion / interview_access_log（tasks 2.5）─────


def test_interview_recording_deletion_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_recording_deletion")
    assert _columns(conn, "interview_recording_deletion") == {
        "session_id", "deleted_at", "scope", "reason", "actor",
    }


def test_interview_recording_deletion_session_id_is_unique(conn):
    """interview-recording-retention spec「重复扫描」：已删除的场次再次被
    扫描到不产生新的删除留痕。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
        "VALUES ('sess-1', 'recording+transcript', 'expired', 'system')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
            "VALUES ('sess-1', 'recording+transcript', 'expired', 'system')"
        )


def test_interview_recording_deletion_reason_check_rejects_unknown_value(conn):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
            "VALUES ('sess-1', 'recording', 'user_request', 'system')"
        )


@pytest.mark.parametrize("reason", ["expired", "withdrawn", "terminated"])
def test_interview_recording_deletion_reason_check_accepts_three_values(conn, reason):
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    conn.execute(
        "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
        "VALUES ('sess-1', 'recording', ?, 'hr-1')",
        (reason,),
    )
    conn.commit()


def test_interview_recording_deletion_actor_cannot_be_blank(conn):
    """删除动作必须有执行者——空 actor 等于没留痕（与 human_review.reviewer
    同一 CHECK 手法）。"""
    _seed_job_candidate_resume_application(conn)
    _seed_interview_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_recording_deletion (session_id, scope, reason, actor) "
            "VALUES ('sess-1', 'recording', 'expired', '   ')"
        )


def test_interview_access_log_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_access_log")
    assert _columns(conn, "interview_access_log") == {
        "id", "accessor", "session_id", "access_type", "at",
    }


def test_interview_access_log_has_no_content_columns(conn):
    """interview-recording-retention spec「访问留痕」：留痕 MUST 不含录音或
    转写内容。"""
    cols = _columns(conn, "interview_access_log")
    assert not ({"text", "content", "transcript", "audio"} & cols)


def test_interview_access_log_accessor_cannot_be_blank(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_access_log (id, accessor, session_id, access_type) "
            "VALUES ('log-1', '  ', 'sess-x', 'recording_playback')"
        )


@pytest.mark.parametrize(
    "access_type", ["recording_playback", "transcript_view", "scorecard_view", "export"]
)
def test_interview_access_log_access_type_accepts_four_values(conn, access_type):
    conn.execute(
        "INSERT INTO interview_access_log (id, accessor, session_id, access_type) "
        "VALUES (?, 'interviewer-1', 'sess-x', ?)",
        (f"log-{access_type}", access_type),
    )
    conn.commit()


def test_interview_access_log_access_type_rejects_unknown_value(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_access_log (id, accessor, session_id, access_type) "
            "VALUES ('log-bad', 'interviewer-1', 'sess-x', 'preview')"
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -k "recording_deletion or access_log" -v`
Expected: 全部 FAIL，报 `no such table: interview_recording_deletion`（或 `interview_access_log`）。

- [ ] **Step 3: 在 `SCHEMA` 末尾（Task 4 追加内容之后）追加**

```sql

-- 录音删除留痕（interview-recording-retention spec「到期自动删除并留痕」
-- 「候选人撤回或终止」）。session_id 直接做主键：一个场次的录音只彻底删除
-- 一次，重复扫描不产生第二行（spec「重复扫描」场景，删除动作本身幂等）。
-- actor 的 CHECK 与 human_review.reviewer 同一手法：trim 第二参数显式列出
-- 空格/制表/换行/回车（SQLite 单参 trim() 只剥空格）——空执行者等于没留痕。
CREATE TABLE IF NOT EXISTS interview_recording_deletion (
    session_id TEXT PRIMARY KEY NOT NULL REFERENCES interview_session(id),
    deleted_at TEXT NOT NULL DEFAULT (datetime('now')),
    scope TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('expired', 'withdrawn', 'terminated')),
    actor TEXT NOT NULL CHECK (
        actor IS NOT NULL AND trim(actor, ' ' || char(9) || char(10) || char(13)) != ''
    )
);

-- 录音/转写/ScoreCard 访问留痕（interview-recording-retention spec「访问
-- 留痕」；interview-scorecard spec「面试官视图与回放」「一致性评估的数据
-- 导出」）。⛔ session_id 上刻意不加外键——与 resume_access_log 同一形态：
-- 留痕表按事件记事实，把它的可写性绑在业务表上会让"留痕写不进去"变成
-- "读取整个失败"，而 spec 明确"留痕写入失败 MUST 读取失败"，这条约束该由
-- 应用层的写入顺序保证（先留痕后返回内容），不该由外键去意外触发。
-- 无内容列（spec「留痕 MUST 不含录音或转写内容」）。access_type 四态对应
-- spec 里明确的四种读取入口：录音回放/查看转写/查看 ScoreCard/导出一致性
-- 评估包。accessor 的 CHECK 与 resume_access_log.accessor 同一手法。
CREATE TABLE IF NOT EXISTS interview_access_log (
    id TEXT PRIMARY KEY NOT NULL,
    accessor TEXT NOT NULL CHECK (
        accessor IS NOT NULL AND trim(accessor, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    session_id TEXT NOT NULL,
    access_type TEXT NOT NULL CHECK (
        access_type IN ('recording_playback', 'transcript_view', 'scorecard_view', 'export')
    ),
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interview_access_log_session ON interview_access_log (session_id);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS（此时 `tests/test_db_m3_schema.py` 应覆盖全部 8 张新表）。

- [ ] **Step 5: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py
git commit -m "feat(m3-u1): add interview_recording_deletion and interview_access_log tables"
```

---

### Task 6: `run_type` 扩展 ＋ `evidence_ref` 的 `interview_turn` 编码（tasks 2.6）

**Files:**
- Modify: `app/audit/run_type.py`
- Modify: `app/audit/evidence_ref.py`
- Modify: `tests/test_run_type_and_evidence_ref.py`

**Interfaces:**
- Consumes: 表 `interview_turn`（Task 4，`validate_interview_turn_evidence` 要查它的 `id`/`answer_text` 两列）
- Produces: `app/audit/run_type.py::RUN_TYPE_INTERVIEW = "interview"`；`app/audit/evidence_ref.py::InterviewTurnEvidenceRef(id, start, end, quote)`、`format_interview_turn_evidence_ref(turn_id, start, end, quote) -> str`、`validate_interview_turn_evidence(conn, ref: InterviewTurnEvidenceRef) -> None`（校验失败抛 `ValueError`）；`parse_evidence_ref(raw) -> EvidenceRef | InterviewTurnEvidenceRef`（按 JSON 里的 `type` 键分派，无 `type` 键时走原有 `span_id` 分支，**向后兼容、旧调用方零改动**）。这三者供 U5 tasks 6.2/6.3/6.6（评分 Agent 与 `effect_persist_scorecard`）调用。

**设计说明**：`run_type.py` 的模块级约束是"纯函数，不 import app.storage"——新增前缀映射不违反这条，仍是字符串前缀判定。`evidence_ref.py` 没有同等的"不碰数据库"约束（`parse_evidence_ref`/`format_evidence_ref` 一直是纯函数，但本任务新增的 `validate_interview_turn_evidence` 必须读 `interview_turn` 表才能验证"turn 存在"，这是 tasks 2.6 明确要求的行为，因此该函数签名接收 `sqlite3.Connection`）。

- [ ] **Step 1: 在 `tests/test_run_type_and_evidence_ref.py` 追加失败测试**

```python
# 追加在文件顶部 import 之后（替换原 import 行，新增 InterviewTurnEvidenceRef /
# format_interview_turn_evidence_ref / validate_interview_turn_evidence /
# RUN_TYPE_INTERVIEW）：
#
# from app.audit.evidence_ref import (
#     EvidenceRef,
#     InterviewTurnEvidenceRef,
#     format_evidence_ref,
#     format_interview_turn_evidence_ref,
#     parse_evidence_ref,
#     validate_interview_turn_evidence,
# )
# from app.audit.run_type import RUN_TYPE_INTERVIEW, RUN_TYPE_PARSE, RUN_TYPE_RANK, run_type_of
#
# 以下函数追加到文件末尾：

import sqlite3


def test_run_type_of_interview_prefix():
    assert run_type_of("interview-prep-v1") == RUN_TYPE_INTERVIEW
    assert run_type_of("interview-score-v1") == RUN_TYPE_INTERVIEW
    assert run_type_of("interview-followup-v1") == RUN_TYPE_INTERVIEW


def test_format_interview_turn_evidence_ref_produces_expected_json():
    raw = format_interview_turn_evidence_ref("turn-1", start=3, end=10, quote="AUTOSAR CP")
    assert json.loads(raw) == {
        "type": "interview_turn", "id": "turn-1", "start": 3, "end": 10, "quote": "AUTOSAR CP",
    }


def test_parse_evidence_ref_dispatches_interview_turn_type():
    raw = format_interview_turn_evidence_ref("turn-1", start=3, end=10, quote="AUTOSAR CP")
    ref = parse_evidence_ref(raw)
    assert ref == InterviewTurnEvidenceRef(id="turn-1", start=3, end=10, quote="AUTOSAR CP")


def test_parse_evidence_ref_still_dispatches_legacy_span_id_type():
    """旧的 resume span_id 编码不受影响——无 type 键时走原分支，零改动。"""
    raw = format_evidence_ref(span_id=6, start=120, end=180)
    ref = parse_evidence_ref(raw)
    assert ref == EvidenceRef(span_id=6, start=120, end=180)


def _turn_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE interview_turn (id TEXT PRIMARY KEY, answer_text TEXT)")
    conn.execute(
        "INSERT INTO interview_turn VALUES ('turn-1', '我熟悉 AUTOSAR CP 平台的诊断栈')"
    )
    return conn


def test_validate_interview_turn_evidence_accepts_legal_offset():
    conn = _turn_db()
    ref = InterviewTurnEvidenceRef(id="turn-1", start=3, end=9, quote="AUTOSAR")
    validate_interview_turn_evidence(conn, ref)  # 不抛异常即通过


def test_validate_interview_turn_evidence_rejects_missing_turn():
    conn = _turn_db()
    ref = InterviewTurnEvidenceRef(id="turn-missing", start=0, end=1, quote="x")
    with pytest.raises(ValueError, match="不存在"):
        validate_interview_turn_evidence(conn, ref)


def test_validate_interview_turn_evidence_rejects_offset_out_of_range():
    conn = _turn_db()
    ref = InterviewTurnEvidenceRef(id="turn-1", start=5, end=999, quote="x")
    with pytest.raises(ValueError, match="偏移越界"):
        validate_interview_turn_evidence(conn, ref)


def test_validate_interview_turn_evidence_rejects_start_not_before_end():
    conn = _turn_db()
    ref = InterviewTurnEvidenceRef(id="turn-1", start=8, end=8, quote="x")
    with pytest.raises(ValueError, match="偏移越界"):
        validate_interview_turn_evidence(conn, ref)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python3 -m pytest tests/test_run_type_and_evidence_ref.py -v`
Expected: `ImportError`（`InterviewTurnEvidenceRef`/`format_interview_turn_evidence_ref`/`validate_interview_turn_evidence`/`RUN_TYPE_INTERVIEW` 尚不存在）。

- [ ] **Step 3: 修改 `app/audit/run_type.py`**

把文件替换为：

```python
"""analysis_run.run_type 语义约定：不加列，用 prompt_version 前缀区分。

design.md 决策：「analysis_run 增加 run_type 语义约定（parse/rank，可空列已存在
的用 prompt_version 前缀区分，⛔ 不加列）」；voice-structured-interview
design.md tasks 2.6 追加 interview 取值——prep 出题/追问选择/post 评分三处
AI 调用统一归为 interview 语义类别，具体调用共用一个 "interview-" 前缀
（如 "interview-prep-v1"/"interview-score-v1"），与 parse-/rank- 同一机制。
⛔ 不要给这个模块加数据库读写——它是纯函数，只做字符串前缀判定，不 import
app.storage。
"""
from __future__ import annotations

RUN_TYPE_PARSE = "parse"
RUN_TYPE_RANK = "rank"
RUN_TYPE_INTERVIEW = "interview"

# 前缀 → run_type。design.md 与 tasks.md 里已出现的具体值：
# "parse-v1"（resume-parsing spec，U2 tasks 3.5）、"rank-v1"（candidate-ranking
# spec，U4 tasks 5.4）、"interview-*"（voice-structured-interview，M3 U1
# tasks 2.6，覆盖 prep/追问/评分三处调用）。新增运行类型时在这里加一行，
# ⛔ 不要在别处再判一次前缀——散成两处会出现"一处判 parse 一处判 rank"的分叉。
_PREFIX_TO_RUN_TYPE: dict[str, str] = {
    "parse-": RUN_TYPE_PARSE,
    "rank-": RUN_TYPE_RANK,
    "interview-": RUN_TYPE_INTERVIEW,
}


def run_type_of(prompt_version: str) -> str:
    for prefix, run_type in _PREFIX_TO_RUN_TYPE.items():
        if prompt_version.startswith(prefix):
            return run_type
    raise ValueError(
        f"未登记的 prompt_version 前缀: {prompt_version!r}；"
        f"已登记前缀: {sorted(_PREFIX_TO_RUN_TYPE)}"
    )
```

- [ ] **Step 4: 修改 `app/audit/evidence_ref.py`**

把文件替换为：

```python
"""criterion_score.evidence_ref 的 JSON 回指约定。

两种编码并存：
  ① resume span 回指（M2 精排，U4 tasks 5.4/5.5）：{"span_id","start","end"}
  ② interview turn 回指（M3 评分，U5 tasks 6.2/6.6）：
     {"type":"interview_turn","id","start","end","quote"}

列本身仍是自由文本（M1 intake 场景可以继续写 "resume-1#120-180" 这类自由
格式字符串）；两种 JSON 编码都是**可选**的程序化约定，`parse_evidence_ref`
按 `type` 键是否等于 "interview_turn" 分派——没有 `type` 键（① 的旧编码）
走原有分支，**旧调用方零改动、旧测试零改动**。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceRef:
    span_id: int
    start: int
    end: int


@dataclass(frozen=True)
class InterviewTurnEvidenceRef:
    id: str
    start: int
    end: int
    quote: str


def format_evidence_ref(span_id: int, start: int, end: int) -> str:
    return json.dumps(
        {"span_id": span_id, "start": start, "end": end}, ensure_ascii=False
    )


def format_interview_turn_evidence_ref(turn_id: str, start: int, end: int, quote: str) -> str:
    return json.dumps(
        {"type": "interview_turn", "id": turn_id, "start": start, "end": end, "quote": quote},
        ensure_ascii=False,
    )


def parse_evidence_ref(raw: str) -> EvidenceRef | InterviewTurnEvidenceRef:
    """⛔ 不做静默兜底：非 JSON 输入直接抛 json.JSONDecodeError，缺键直接抛
    KeyError。调用方在写入前自己保证格式，读取历史 M1 自由文本行的调用方要
    先判断是否是本约定的 JSON，不能指望本函数替它兜底。"""
    data = json.loads(raw)
    if data.get("type") == "interview_turn":
        return InterviewTurnEvidenceRef(
            id=data["id"], start=data["start"], end=data["end"], quote=data["quote"]
        )
    return EvidenceRef(span_id=data["span_id"], start=data["start"], end=data["end"])


def validate_interview_turn_evidence(conn: sqlite3.Connection, ref: InterviewTurnEvidenceRef) -> None:
    """校验 interview_turn 证据回指：turn 必须存在，偏移必须落在该 turn
    answer_text 的合法范围内（0 <= start < end <= len(answer_text)）。

    校验失败抛 ValueError 而不是返回 bool——调用方（U5 compute_score/
    compute_align）要的是"这次评分整体判不可用"的硬失败，不是一个可能被
    忽略的布尔值（interview-scorecard spec「模型未给出证据」场景：证据
    校验失败时该次评分整体不写入）。
    """
    row = conn.execute(
        "SELECT answer_text FROM interview_turn WHERE id = ?", (ref.id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_turn 不存在: {ref.id!r}")
    answer_text = row[0] or ""
    if not (0 <= ref.start < ref.end <= len(answer_text)):
        raise ValueError(
            f"证据偏移越界: start={ref.start}, end={ref.end}, "
            f"answer_text 长度={len(answer_text)}"
        )
```

- [ ] **Step 5: 更新 `tests/test_run_type_and_evidence_ref.py` 的 import 行**

把文件开头的 import 段：

```python
from app.audit.evidence_ref import EvidenceRef, format_evidence_ref, parse_evidence_ref
from app.audit.run_type import RUN_TYPE_PARSE, RUN_TYPE_RANK, run_type_of
```

替换为：

```python
import sqlite3

from app.audit.evidence_ref import (
    EvidenceRef,
    InterviewTurnEvidenceRef,
    format_evidence_ref,
    format_interview_turn_evidence_ref,
    parse_evidence_ref,
    validate_interview_turn_evidence,
)
from app.audit.run_type import RUN_TYPE_INTERVIEW, RUN_TYPE_PARSE, RUN_TYPE_RANK, run_type_of
```

（Step 1 里追加的测试函数末尾另有一个局部 `import sqlite3`——去掉那个重复的局部 import，改用这里的模块级 import。）

- [ ] **Step 6: 运行测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_run_type_and_evidence_ref.py -v`
Expected: 全部 PASS（含 Task 6 之前就存在的 6 条旧测试——零回归）。

- [ ] **Step 7: Commit**

```bash
git add app/audit/run_type.py app/audit/evidence_ref.py tests/test_run_type_and_evidence_ref.py
git commit -m "feat(m3-u1): extend run_type and evidence_ref for interview domain"
```

---

### Task 7: 面试域 AI 输入 Pydantic schema（tasks 2.7）

**Files:**
- Create: `app/schemas/interview_ai_input.py`
- Create: `tests/test_interview_ai_input_schema.py`

**Interfaces:**
- Consumes: 无（纯 schema 定义，不依赖数据库）
- Produces: `PrepInput(profile, rubric_dimensions, resume_scores)`、`ResumeScoreItem(criterion_key, score, evidence_excerpt)`、`FollowUpInput(question_text, follow_ups, transcript)`、`ScoreInput(rubric_dimensions, turns)`、`ScoreInputTurn(turn_id, seq, question_text, answer_text)`。供 U2 `app/agents/interview_prep.py::generate`（tasks 3.1）、U4 `app/agents/follow_up_selector.py::select`（tasks 5.4，D17）、U5 `app/agents/interview_scoring.py::score`（tasks 6.2）在实现时把入参组装成这几个模型。

**设计说明**：三个模型与两个子模型全部 `model_config = ConfigDict(extra="forbid")`——字段白名单是结构性的，不是运行时校验补丁：调用方传错键（比如手滑传了 `candidate_name`）在构造 Pydantic 对象那一刻就失败，而不是被悄悄透传进最终拼给 LLM 网关的 prompt。`ScoreInputTurn` 只有 `turn_id`/`seq`/`question_text`/`answer_text` 四个字段——刻意不包含 `interview_turn` 表里的 `asr_confidence`/`acoustic_ref`/`audio_start_ms`/`audio_end_ms`，这是合规红线"声学信号只展示不计分"在 AI 输入契约层面的结构性保证。

- [ ] **Step 1: 写 `tests/test_interview_ai_input_schema.py`**

```python
"""面试域 AI 调用输入 schema 的字段白名单反射测试（tasks 2.7）。

判据是结构性的：扫描每个模型的 model_fields，字段名不得命中任何一个禁止
子串（身份/核验/声学关键词）。这条测试守护的不是"今天没传错值"，而是
"这几个模型的字段集合本身就不可能长出这些字段"——加字段的人会在这里被
直接拦下来，而不是等到评分输出里混进声学噪声才被发现。
"""
import pytest
from pydantic import ValidationError

from app.schemas.interview_ai_input import (
    FollowUpInput,
    PrepInput,
    ResumeScoreItem,
    ScoreInput,
    ScoreInputTurn,
)

_FORBIDDEN_SUBSTRINGS = (
    "name", "phone", "candidate", "identity", "verified", "verification",
    "acoustic", "face", "image", "photo", "audio", "biometric",
)

_ALL_MODELS = (PrepInput, ResumeScoreItem, FollowUpInput, ScoreInput, ScoreInputTurn)


@pytest.mark.parametrize("model", _ALL_MODELS)
def test_ai_input_schema_has_no_forbidden_fields(model):
    for field_name in model.model_fields:
        lowered = field_name.lower()
        hit = [bad for bad in _FORBIDDEN_SUBSTRINGS if bad in lowered]
        assert not hit, f"{model.__name__}.{field_name} 命中禁止字段模式 {hit}"


def test_prep_input_accepts_well_formed_payload():
    p = PrepInput(
        profile={"job_title": "底层软件工程师", "diag_stack": ["UDS", "CAN"]},
        rubric_dimensions=["diag_stack", "toolchain"],
        resume_scores=[
            ResumeScoreItem(criterion_key="diag_stack", score=3.0, evidence_excerpt="曾用 UDS 协议调试"),
        ],
    )
    assert p.rubric_dimensions == ["diag_stack", "toolchain"]
    assert p.resume_scores[0].criterion_key == "diag_stack"


def test_prep_input_allows_empty_resume_scores():
    """spec「简历评分尚未完成」场景：只按画像生成通用题目。"""
    p = PrepInput(profile={"job_title": "底层软件工程师"}, rubric_dimensions=["diag_stack"])
    assert p.resume_scores == []


def test_prep_input_forbids_extra_fields():
    with pytest.raises(ValidationError):
        PrepInput(
            profile={}, rubric_dimensions=["x"], resume_scores=[],
            candidate_name="张三",  # type: ignore[call-arg]
        )


def test_follow_up_input_accepts_well_formed_payload():
    f = FollowUpInput(
        question_text="请描述一次你排查 CAN 总线故障的经历",
        follow_ups=["具体用了哪些诊断工具？", "故障最终怎么定位到的？"],
        transcript="我用示波器抓了波形，后来发现是终端电阻的问题",
    )
    assert len(f.follow_ups) == 2


def test_follow_up_input_forbids_extra_fields():
    with pytest.raises(ValidationError):
        FollowUpInput(
            question_text="q", follow_ups=["a"], transcript="t",
            phone_verified=True,  # type: ignore[call-arg]
        )


def test_score_input_accepts_well_formed_payload():
    s = ScoreInput(
        rubric_dimensions=["diag_stack"],
        turns=[
            ScoreInputTurn(turn_id="turn-1", seq=1, question_text="问题", answer_text="回答"),
        ],
    )
    assert s.turns[0].turn_id == "turn-1"


def test_score_input_turn_has_no_acoustic_or_audio_fields():
    """合规红线「声学信号只展示不计分」在评分输入契约层的结构性保证：
    interview_turn 表里的 asr_confidence/acoustic_ref/audio_start_ms/
    audio_end_ms 一个都不出现在 ScoreInputTurn 里。"""
    forbidden = {"asr_confidence", "acoustic_ref", "audio_start_ms", "audio_end_ms"}
    assert not (forbidden & set(ScoreInputTurn.model_fields))


def test_score_input_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ScoreInputTurn(
            turn_id="t1", seq=1, question_text="q", answer_text="a",
            asr_confidence=0.9,  # type: ignore[call-arg]
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python3 -m pytest tests/test_interview_ai_input_schema.py -v`
Expected: `ModuleNotFoundError: No module named 'app.schemas.interview_ai_input'`。

- [ ] **Step 3: 写 `app/schemas/interview_ai_input.py`**

```python
"""M3 面试域 AI 调用输入 schema：prep 出题／追问选择／post 评分三处 LLM 调用
的输入契约（design D1/D2/D17/D19，tasks.md 2.7）。

字段白名单是结构性的，不是运行时校验补丁：三个模型物理上不存在候选人身份
字段（姓名/手机号）、身份核验字段（核验结果/尝试次数/核验时刻）、声学情绪
字段（语速/停顿/静默）；`model_config = ConfigDict(extra="forbid")` 让调用方
传错键在构造对象那一刻直接失败，而不是被悄悄透传进最终拼给 LLM 网关的
prompt。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResumeScoreItem(BaseModel):
    """简历评分摘录（M2 candidate-ranking 产出的逐维得分+证据摘录）。"""
    model_config = ConfigDict(extra="forbid")

    criterion_key: str
    score: float
    evidence_excerpt: str


class PrepInput(BaseModel):
    """prep 出题（app/agents/interview_prep.py::generate，U2 tasks 3.1）的输入。

    interview-prep-question-engine spec「按画像与简历弱点生成题目」：
    输入 MUST 只包含冻结画像与 rubric、该投递的简历评分结果；
    MUST NOT 包含候选人姓名、联系方式等身份字段。
    """
    model_config = ConfigDict(extra="forbid")

    profile: dict
    rubric_dimensions: list[str] = Field(min_length=1)
    resume_scores: list[ResumeScoreItem] = Field(default_factory=list)


class FollowUpInput(BaseModel):
    """追问选择（app/agents/follow_up_selector.py::select，design D17，
    U4 tasks 5.4）的输入。

    live-voice-interview-session spec「追问只在预埋集合内选择」：追问选择
    是无副作用纯函数，输入只有当前题目、其预埋追问集合、候选人本轮转写。
    """
    model_config = ConfigDict(extra="forbid")

    question_text: str
    follow_ups: list[str] = Field(min_length=1)
    transcript: str


class ScoreInputTurn(BaseModel):
    """post 评分的单条 turn 输入。⛔ 刻意不包含 interview_turn 表里的
    asr_confidence/acoustic_ref/audio_start_ms/audio_end_ms——合规红线
    「声学信号只展示不计分」的结构性保证。"""
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    seq: int
    question_text: str
    answer_text: str


class ScoreInput(BaseModel):
    """post 评分（app/agents/interview_scoring.py::score，U5 tasks 6.2）的
    输入。

    interview-scorecard spec「逐维评分带 turn 回指」「声学信号只展示不计分」：
    评分输入 MUST NOT 包含身份核验数据、声学情绪信号或候选人身份字段。
    """
    model_config = ConfigDict(extra="forbid")

    rubric_dimensions: list[str] = Field(min_length=1)
    turns: list[ScoreInputTurn] = Field(min_length=1)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_interview_ai_input_schema.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/schemas/interview_ai_input.py tests/test_interview_ai_input_schema.py
git commit -m "feat(m3-u1): add interview AI input schemas with field whitelist"
```

---

### Task 8: 老库升级回归 ＋ 全量测试收口（tasks 2.8）

**Files:**
- Modify: `tests/test_db_m3_schema.py`

**Interfaces:**
- Consumes: `tests/fixtures/zp51_demo_db_schema_pre_m3.sql`（Task 1 Step 1 生成）、Task 1–5 建的全部 8 张新表
- Produces: 无新公共接口——本任务只补齐"老库升级不炸、既有表一行不改"的回归测试，并跑一次全量测试收口整个交付单元。

**设计说明**：`tests/fixtures/zp51_demo_db_schema_pre_m3.sql` 代表"M3 U1 落地前，`.51` 现网库应有的完整表结构"（Task 1 Step 1 已生成，内容是当时 `init_schema()` 产出的全部 `CREATE TABLE`/`CREATE INDEX` 原文）。本任务从这份快照重建一个"老库"，跑升级后的 `init_schema()`（此时已包含 Task 1–5 加的全部 DDL），验证：① 8 张新表全部出现；② 快照里原有的每一张表、每一条索引，`sqlite_master.sql` 原文逐字不变（不只是列集合相同——CHECK/DEFAULT/REFERENCES 措辞被悄悄改写不会被"列集合相同"这种弱判据发现）；③ 挑几张老表插入的历史数据行数不变。

- [ ] **Step 1: 在 `tests/test_db_m3_schema.py` 顶部追加 import，文件末尾追加老库回归测试**

在文件顶部 `import sqlite3` 之后加一行：

```python
from pathlib import Path
```

在文件末尾追加：

```python
# ── 老库升级：M3 U1 落地前的 .51 现网库真实形态 ───────────────────────────
#
# 基线不是从 SCHEMA 裁剪，而是刻意固定成 Task 1 生成的历史快照——它代表
# "M3 U1 上线前，.51 上的库长什么样"，不随 SCHEMA 一起演进（与
# tests/test_db_migration.py 顶部注释同一理由：派生的话测试会随 SCHEMA 一起
# 演进，永远测不出"老库升级不了"这个真正要防的故障）。

_LEGACY_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zp51_demo_db_schema_pre_m3.sql"

_M3_NEW_TABLES = (
    "prep_snapshot", "prep_question", "interview_session", "interview_consent",
    "identity_check", "interview_turn", "interview_recording_deletion",
    "interview_access_log",
)


def _legacy_pre_m3_db(tmp_path):
    c = get_connection(str(tmp_path / "legacy_pre_m3.db"))
    ddl = _LEGACY_FIXTURE_PATH.read_text(encoding="utf-8")
    c.executescript(ddl)
    # sqlite_master.sql 只存 DDL，不存 SCHEMA 常量里紧跟 stage 建表之后的
    # `INSERT OR IGNORE INTO stage ...` 三行种子数据——这三行必须在这里手工
    # 补上，否则下面插入 application 行会因为 current_stage_id 的外键指向
    # 一张空的 stage 表而失败（本计划写作时已实测踩到这个 FOREIGN KEY
    # constraint failed，在此补齐修正）。
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected')")
    # 挑几张跨 M1/M2 都在用的老表插入历史数据，验证升级后行数与内容不变。
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
    c.commit()
    return c


def _legacy_sqlite_master_sql(conn: sqlite3.Connection, known_names: set[str]) -> dict[str, str]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {name: sql for name, sql in rows if name in known_names}


def test_legacy_pre_m3_db_gains_all_new_tables_after_init_schema(tmp_path):
    conn = _legacy_pre_m3_db(tmp_path)

    init_schema(conn)

    for table in _M3_NEW_TABLES:
        assert _table_exists(conn, table), f"{table} 应该在 init_schema 后出现"


def test_legacy_pre_m3_db_existing_tables_and_rows_are_untouched(tmp_path):
    """M3 U1 的字面判据：老库升级后既有表一行不改。既比列集合，也比
    sqlite_master.sql 原文（CHECK/DEFAULT/REFERENCES 措辞是否被悄悄改写），
    还比几张关键表的行数。"""
    conn = _legacy_pre_m3_db(tmp_path)
    known_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    before_sql = _legacy_sqlite_master_sql(conn, known_names)
    before_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("job", "candidate", "resume", "application")
    }

    init_schema(conn)

    after_sql = _legacy_sqlite_master_sql(conn, known_names)
    after_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("job", "candidate", "resume", "application")
    }

    assert after_sql == before_sql
    assert after_counts == before_counts


def test_legacy_pre_m3_db_init_schema_is_idempotent(tmp_path):
    """重跑三次不报错——UNIQUE INDEX 与 CHECK 都必须带 IF NOT EXISTS 的
    幂等性（M3 新加的 8 张表同样要满足）。"""
    conn = _legacy_pre_m3_db(tmp_path)

    init_schema(conn)
    init_schema(conn)
    init_schema(conn)

    for table in _M3_NEW_TABLES:
        assert _table_exists(conn, table)


def test_m3_new_tables_never_enter_the_add_column_path():
    """本单元的第二条硬约束：8 张全新表一个都不许进 _ADDED_COLUMNS——加列
    路径只服务"老库缺列"，把新表塞进去会让 apply_column_migrations 对着一张
    不存在的表执行 ALTER TABLE。"""
    from app.storage.db import _ADDED_COLUMNS

    tables_touched = {table for table, _column, _ddl in _ADDED_COLUMNS}
    assert not (set(_M3_NEW_TABLES) & tables_touched)


def test_fresh_and_legacy_upgraded_schemas_have_identical_m3_tables(tmp_path):
    """新库直接 init_schema() 与老库升级后，M3 新表的列集合必须完全一致——
    两条路径不能产生两种不同形状的表。"""
    fresh = get_connection(str(tmp_path / "fresh.db"))
    init_schema(fresh)

    legacy = _legacy_pre_m3_db(tmp_path)
    init_schema(legacy)

    for table in _M3_NEW_TABLES:
        assert _columns(fresh, table) == _columns(legacy, table), table
```

- [ ] **Step 2: 运行本文件全部测试确认通过**

Run: `./venv/bin/python3 -m pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS（此时文件应有 8 张表的建表/CHECK 反证测试 + 本任务的 5 条老库回归测试）。

- [ ] **Step 3: 跑本单元三份测试文件 + 全量回归，确认零破坏**

```bash
./venv/bin/python3 -m pytest tests/test_db_m3_schema.py tests/test_run_type_and_evidence_ref.py tests/test_interview_ai_input_schema.py -v
./venv/bin/python3 -m pytest -q
```

Expected: 前一条全部 PASS；后一条（全仓库测试）除本单元新增的用例外，其余用例数与合并前一致、全部 PASS——尤其关注 `tests/test_db_migration.py`、`tests/test_db_m2_schema.py`、`tests/test_audit_assertions.py`：这三份分别守护"老库加列路径"、"M2 建表历史快照"、"合规断言"，本单元任何一处误改 `app/storage/db.py` 已有内容都会在这里现形。

⚠️ **已知与本单元无关的既存失败（本计划写作时在未改动的 main 上验证过，合并前后同样失败即正常，⛔ 不要当成本单元引入的回归去排查）**：`tests/test_effect_idempotency_suite.py::test_manifest_matches_the_source_tree` 与 `tests/test_probe_m3_voice.py` 的 4 条用例——后者报 `ModuleNotFoundError: No module named 'funasr'`，是 U0 探针脚本依赖的可选包本机未装，与数据模型无关。若全量跑出来的失败列表**不止**这 5 条，才需要排查是不是本单元引入的。

- [ ] **Step 4: 自查 Global Constraints reviewer 机械判据**

```bash
git diff --stat main -- requirements.txt pyproject.toml    # 期望：空输出（无依赖变更）
git diff main -- app/storage/db.py | grep -c "ALTER TABLE"  # 期望：0
git diff main -- app/storage/db.py | grep -A2 "_ADDED_COLUMNS" | head -20  # 期望：本单元的 diff 里看不到这个元组的定义被改动
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_db_m3_schema.py
git commit -m "test(m3-u1): add legacy db upgrade regression for interview tables"
```

---

## 交付单元收口自查（对照 CLAUDE.md 「粒度映射」）

完成 Task 1–8 后，在 `openspec/changes/voice-structured-interview/tasks.md` 里把第 2 章 2.1–2.8 全部勾选为 `[x]`（本单元本身不触发归档——`tasks.md` 还有 U2–U7 未完成，归档时限只在全部 76 项勾完后触发，见 `03-工具链协作规则.md` §4）。

**Spec 覆盖核对**（每条 spec Requirement → 对应 Task）：
- `interview-prep-question-engine`「题目快照版本化且可追溯到输入」→ Task 1（`prep_snapshot`）
- `interview-prep-question-engine`「按画像与简历弱点生成题目」「难度曲线」「业务经理确认后才冻结」「题目与 AI 生成标识」→ Task 1（`prep_question` 的 `origin`/`ai_text`）
- `live-voice-interview-session`「开场前置条件」→ Task 2（`interview_session.status`）
- `interview-recording-retention`「留存期限在场次建立时固定」→ Task 2（`retention_until`/`retention_policy_version` NOT NULL）
- `interview-invite-and-consent`「AI 面试与身份核验各自单独同意」「手机号验证码弱核验」「身份核验与评分链路物理隔离」→ Task 3
- `live-voice-interview-session`「语音回路与延迟观测」「追问只在预埋集合内选择」「打断处理」「全程录制与 turn 对齐」「文本作答降级」→ Task 4
- `interview-recording-retention`「到期自动删除并留痕」「候选人撤回或终止」「访问留痕」→ Task 5
- `interview-scorecard`「逐维评分带 turn 回指」（`evidence_ref` 的 `interview_turn` 编码 + 校验）→ Task 6
- `interview-scorecard`「声学信号只展示不计分」（AI 输入结构性排除）+ `interview-prep-question-engine`「按画像与简历弱点生成题目」（输入不含身份字段）→ Task 7
- `m3-compliance-assertions`「身份核验与评分隔离」（`identity_check` 无图像列的源码级反证）→ Task 3；「录音到期删除率 100%」所需的表结构 → Task 2/Task 5；这两条断言的 CI 接线本身属 U7 tasks 8.1，不在本单元范围内

**占位符扫描**：本计划全部 8 个 Task 的每个 Step 都含完整可执行代码/命令，无 `TBD`/`TODO`/"适当处理"类占位符。

**类型一致性**：`interview_turn.question_id` ↔ `prep_question.id`（Task 1 产出，Task 4 消费）；`interview_consent.session_id`/`identity_check.session_id`/`interview_turn.session_id`/`interview_recording_deletion.session_id`/`interview_access_log.session_id` 全部 ↔ `interview_session.id`（Task 2 产出）；`InterviewTurnEvidenceRef.id` ↔ `interview_turn.id`（Task 4 产出，Task 6 消费）——全部核对一致。

---

## Plan complete

计划已保存到 `docs/superpowers/plans/2026-09-18-voice-structured-interview-unit1-data-model.md`。

**本条 opener 不合并、只提交计划文件本身**（无头执行指令要求），执行选择留给下一次 `run-build` 调用时决定：

1. **Subagent-Driven（推荐）**——每个 Task 派一个新子代理，两阶段 review
2. **Inline Execution**——本 session 内批量执行，checkpoint 间人工审阅
