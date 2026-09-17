# M2 简历解析与排序 · 交付单元 U1（数据模型：ATS 域＋评分审计域接线）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `app/storage/db.py` 的 `SCHEMA` 追加 ATS 域与评分审计接线所需的 14 张全新表（候选人／投递／阶段流转／拒绝记录／简历访问留痕／校对队列／硬门槛标记／向量／评测集／HR 账号），把 `analysis_run.run_type` 与 `criterion_score.evidence_ref` 的 M2 语义约定落成工具函数，把 `app/audit/assertions.py` 里"拒绝记录表缺失即放行"的 M1 过渡分支改判为 fail-closed（U7 任务 8.1，与 U1 建表合并，不跨 session），并交付 `scripts/create_hr_account.py` 幂等建账号脚本——**全程零数据迁移**：新表一律 `CREATE TABLE IF NOT EXISTS`，`.51` 现网 `demo.db` 既有表一行不改。

**Architecture:** 全部 DDL 追加进 `app/storage/db.py` 的 `SCHEMA` 常量末尾，不进 `_ADDED_COLUMNS` 加列路径（该路径只服务"老库缺列"，新表不需要）。表间关系：`resume` 在上传时即知道 `job_id`（尚不知道候选人身份，解析前 `candidate_id` 未知），`candidate` 由解析后的姓名＋手机号哈希去重创建，`application` 是唯一把 `candidate_id / job_id / resume_id` 三者接起来的实体（`resume_id` 唯一，1:1）——这条设计是本计划对 tasks.md/design.md 未显式画出的 FK 图的具体化，理由见 Task 1/Task 2 的说明。`analysis_run.run_type` 不加列，改用 `prompt_version` 前缀（`parse-*` / `rank-*`）语义约定 + `app/audit/run_type.py` 解析函数；`criterion_score.evidence_ref` 新增一种可选的 JSON 编码约定（`{"span_id","start","end"}`）+ `app/audit/evidence_ref.py` 解析函数，列本身仍是自由文本，不加 CHECK（M1 intake 场景仍可写自由格式回指）。`rejection_record` 建表后，`app/audit/assertions.py::assert_no_ai_score_rejections` 的"表不存在 → 放行"分支翻转为"表不存在 → fail-closed"，随之修复 `tests/test_audit_assertions.py` / `tests/test_audit_assertion_effectiveness.py` 里依赖"表还不存在"这一假设的 fixture（此前这些测试临时 `CREATE TABLE rejection_record`，U1 建表后这条路径会撞上"表已存在"报错，必须在同一个任务里一并修）。

**Tech Stack:** Python 3.14（`./venv`）· SQLite（标准库 `sqlite3`，WAL + `PRAGMA foreign_keys=ON`）· pytest 8.3.4 · 标准库 `hashlib`/`secrets`/`hmac`（口令哈希，**不引入新依赖**，`requirements.txt` diff 必须为空）

**Spec:**
- `openspec/changes/m2-resume-parse-and-rank/design.md`（决策 D11 数据模型、D5 置信度、D6 硬门槛只标记、Migration Plan、Risks）
- `openspec/changes/m2-resume-parse-and-rank/specs/resume-upload-and-gate/spec.md`（批量上传、真实简历入库闸、访问留痕、可识别到人的登录）
- `openspec/changes/m2-resume-parse-and-rank/specs/resume-parsing/spec.md`（字段回指、置信度校对队列、解析版本）
- `openspec/changes/m2-resume-parse-and-rank/specs/hard-requirement-screening/spec.md`（`screening_flag` 的 fail 必带证据、`rejection_record` 的 CHECK、申诉状态机）
- `openspec/changes/m2-resume-parse-and-rank/specs/candidate-ranking/spec.md`（evidence 回指、评分留痕字段）
- `openspec/changes/m2-resume-parse-and-rank/specs/m2-compliance-assertions/spec.md`（拒绝记录表缺失即失败、断言有效性反证）
- `openspec/changes/m2-resume-parse-and-rank/specs/eval-set-and-metrics/spec.md`（评测集禁止训练用途、样本来源、导入幂等与历史保留）
- `openspec/changes/m2-resume-parse-and-rank/tasks.md` 第 2 章（2.1–2.8）＋第 8 章 8.1

## Global Constraints

以下条目从 `CLAUDE.md`（工程铁律 1–4、合规红线）与本次 opener（`0917BE`）逐字复制。每个 Task 的验收隐含包含本节全部内容。

### 工程铁律（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   **本单元与这条的关系**：U1 不新增任何 `effect_*` 节点、不写任何业务行（脚本 `create_hr_account.py` 的写入是一次性运维动作，不经 LangGraph、不需要幂等键，幂等性由"同用户名更新而不是插入新行"保证，见 Task 8）。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
   **本单元与这条的关系**：`app/audit/run_type.py` 与 `app/audit/evidence_ref.py` 都是纯函数模块（字符串/JSON 解析与格式化，不读库不读时钟），U1 不产生任何 `compute_*` / `effect_*` 节点。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
   **本单元与这条的关系**：`analysis_run` 表本身在 M1 已建齐十四列，U1 不改其结构，只追加 `run_type` 的**语义约定**（不加列，读 `prompt_version` 前缀区分 `parse-*` / `rank-*`）。
4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。
   **本单元与这条的关系**：`criterion_score.evidence_ref` 的 `NOT NULL` + 空白 `CHECK` 在 M1 已落（`app/storage/db.py` 现有 DDL），U1 不改这条 CHECK；只新增一种可选的 JSON 编码约定服务 M2 的简历 span 回指，M1 intake 场景的自由格式字符串继续合法。

### 合规红线（逐字，与本单元相关的两条）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。

### 本单元的任务专属约束（opener `0917BE` §二，逐字）

1. 全部新表 `CREATE TABLE IF NOT EXISTS`，⛔ 不进 `app/storage/db.py` 的 `_ADDED_COLUMNS` 加列路径（新表不需要加列迁移）；`.51` 现网 `demo.db` 既有表一行不改，无数据迁移；Task 7 的测试用复制的 demo.db 结构核实这一点。
2. `candidate` 按（姓名＋手机号哈希）唯一；手机号本期只用于去重，以哈希存储，明文不落库（D11 已裁决 2026-09-17）。
3. `rejection_record.reason_type` CHECK IN `('hard_rule','human_decision')`，直接 INSERT `reason_type='ai_score'` 必须被 CHECK 拒绝并写反证测试。
4. `screening_flag.verdict='fail'` 时 `evidence_ref` 非空 CHECK。
5. `analysis_run` 只加 `run_type` 语义约定（`parse`/`rank`），⛔ 不加列——已有可空列够用，用 `prompt_version` 前缀区分。
6. `eval_sample`／`eval_annotation`／`eval_import_batch` 要带「禁止训练用途」表注释，与 `analysis_run` 口径一致。
7. U7 任务 8.1（`REJECTION_TABLE` 缺表分支从「M1 现状放行」改判失败）与本单元建表须在同一交付单元合并完成，⛔ 不跨 session。
8. 交付前自查：`grep -c '^### Task ' <计划文件>` 必须 ≥1 且等于实际任务数。

**reviewer 机械判据（汇总）**：
- `_ADDED_COLUMNS` 元组本单元 diff 里一个字节都不改（仍是 `{"job_profile"}` 那一组）。
- 本单元 diff 里不出现新的 `ALTER TABLE`。
- `PRAGMA table_info(rejection_record)` 中 `reason_type` 列的 `CHECK` 表达式包含 `hard_rule` 与 `human_decision`，不包含 `ai_score`。
- `PRAGMA table_info(screening_flag)` 对应的建表 SQL（`sqlite_master.sql`）里含一条对 `verdict='fail'` 的 CHECK 引用 `evidence_ref`。
- `requirements.txt` 与 `pyproject.toml`（若存在）diff 为空。

---

## 建议拆段点（`lane-dispatch`「长 run-build 拆段」，本计划 8 个 Task）

- **Segment A：Task 1–3**（候选人/投递域 + rejection_record 与断言翻转，含最复杂的 ripple 修复）
- **Segment B：Task 4–6**（留痕/校对/标记域 + 向量/评测集/账号表 + run_type/evidence_ref 工具函数）
- **Segment C：Task 7–8**（跨库回归测试 + 建账号脚本）

---

## 前置：确认当前 `app/storage/db.py` 与两份审计测试文件的基线行号

以下任务里引用的行号基于 2026-09-17 的文件状态。执行者在动手前先跑一次确认基线未漂移：

```bash
grep -n '^"""$' app/storage/db.py | tail -1   # SCHEMA 三引号字符串结束位置
wc -l app/storage/db.py app/audit/assertions.py tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py
```

若行号与任务描述不符，以 `grep` 定位到的锚点文本（如 `CREATE TABLE IF NOT EXISTS hard_requirement`）为准，不要按绝对行号硬改。

---

### Task 1: `candidate` / `resume` / `resume_text_span`（tasks 2.1）

**Files:**
- Modify: `app/storage/db.py`（在 `SCHEMA` 常量末尾、`hard_requirement` 表 DDL 之后追加）
- Test: `tests/test_db_m2_schema.py`（本任务先建文件，后续任务继续往里加测试函数）

**Interfaces:**
- Consumes: 无（本任务是本计划第一个写表的任务）
- Produces: 表 `candidate(id, name, phone_hash, created_at)`；表 `resume(id, job_id, sample_class, file_name, content_sha256, status, parsed_json, parse_confidence, parser_version, uploaded_by, uploaded_at)`；表 `resume_text_span(resume_id, span_id, start, end, text)`。后续任务（Task 2 的 `application`、Task 4 的 `field_review_queue`/`screening_flag`）会 `REFERENCES resume(id)` 或 `REFERENCES candidate(id)`，字段名与类型以此为准。

**设计说明（为什么 `resume` 没有 `candidate_id` 列）**：上传简历时（U2 的 `POST /resumes/upload`）系统只知道 `job_id`，候选人身份要等解析完成、拿到姓名与手机号才能去重创建 `candidate` 行（design D11「候选人去重」）。若 `resume.candidate_id` 现在就设为 `NOT NULL`，上传当刻无值可写；设为可空又会在解析完成前造出一个"名义上关联但实际为 NULL"的字段，容易被将来的查询误用。改为让 `application`（Task 2）持有 `candidate_id + job_id + resume_id` 三者的唯一关联——`resume` 本身只对 `job_id` 负责，谁的简历、连到哪个候选人，由 `application` 一次性接起来。

- [ ] **Step 1: 写 `tests/test_db_m2_schema.py`，先写候选人与简历表的失败测试**

```python
"""M2 U1 数据模型：新库建表齐全、老库升级既有表不变、全部 CHECK 反证。

本文件三段结构（随后续任务继续追加）：
  ① 新库 fresh init_schema() 后逐表齐全 —— 本任务先写 candidate/resume/resume_text_span 三张
  ② 老库（复制 .51 demo.db 结构）升级后既有表一行不改 —— Task 7 统一补
  ③ 全部新增 CHECK 的反证（直接 INSERT，绕过应用层）—— 各表在各自任务里先写，Task 7 汇总检查覆盖面
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
    c = get_connection(str(tmp_path / "m2.db"))
    init_schema(c)
    return c


# ── candidate / resume / resume_text_span（tasks 2.1）───────────────────


def test_candidate_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "candidate")
    assert _columns(conn, "candidate") == {"id", "name", "phone_hash", "created_at"}


def test_candidate_has_no_status_column(conn):
    """CLAUDE.md 数据模型要点：状态属于投递不属于候选人。"""
    assert "status" not in _columns(conn, "candidate")
    assert "current_stage_id" not in _columns(conn, "candidate")


def test_candidate_unique_on_name_and_phone_hash(conn):
    conn.execute(
        "INSERT INTO candidate (id, name, phone_hash) VALUES ('c-1', '张三', 'hash-abc')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate (id, name, phone_hash) VALUES ('c-2', '张三', 'hash-abc')"
        )


def test_candidate_allows_multiple_rows_with_null_phone_hash(conn):
    """解析没能拿到手机号时 phone_hash 可空；SQLite 的 UNIQUE 把多个 NULL 视为互不相等，
    这正是我们想要的行为——没有手机号就不该被强行合并成同一人。"""
    conn.execute("INSERT INTO candidate (id, name, phone_hash) VALUES ('c-3', '李四', NULL)")
    conn.execute("INSERT INTO candidate (id, name, phone_hash) VALUES ('c-4', '李四', NULL)")
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM candidate WHERE name='李四'").fetchone()[0]
    assert rows == 2


def test_resume_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume")
    assert _columns(conn, "resume") == {
        "id", "job_id", "sample_class", "file_name", "content_sha256",
        "status", "parsed_json", "parse_confidence", "parser_version",
        "uploaded_by", "uploaded_at",
    }


@pytest.mark.parametrize("bad_class", ["Live", "real", "", "LIVE "])
def test_resume_sample_class_check_rejects_invalid_values(conn, bad_class):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES ('r-bad', 'j1', ?, 'a.pdf', 'sha-x', 'hr-1')",
            (bad_class,),
        )


@pytest.mark.parametrize("good_class", ["synthetic", "anonymized", "departed", "live"])
def test_resume_sample_class_check_accepts_all_four_values(conn, good_class):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.commit()
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, 'j1', ?, 'a.pdf', ?, 'hr-1')",
        (f"r-{good_class}", good_class, f"sha-{good_class}"),
    )
    conn.commit()


def test_resume_dedup_unique_index_on_job_and_content_hash(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-1', 'j1', 'synthetic', 'a.pdf', 'sha-same', 'hr-1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES ('r-2', 'j1', 'synthetic', 'b.pdf', 'sha-same', 'hr-1')"
        )


def test_resume_same_content_hash_allowed_across_different_jobs(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j2', '供应链总监', 'approved')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-1', 'j1', 'synthetic', 'a.pdf', 'sha-same', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-2', 'j2', 'synthetic', 'a.pdf', 'sha-same', 'hr-1')"
    )
    conn.commit()


def test_resume_status_check_accepts_three_values(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.commit()
    for status in ("pending", "parsed", "unreadable"):
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, status, uploaded_by) "
            "VALUES (?, 'j1', 'synthetic', 'a.pdf', ?, ?, 'hr-1')",
            (f"r-{status}", f"sha-{status}", status),
        )
    conn.commit()


def test_resume_text_span_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume_text_span")
    assert _columns(conn, "resume_text_span") == {"resume_id", "span_id", "start", "end", "text"}


def test_resume_text_span_primary_key_is_resume_and_span(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r-1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
        "VALUES ('r-1', 1, 0, 5, '张三简历')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
            "VALUES ('r-1', 1, 10, 15, '重复的 span_id')"
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v`
Expected: 全部 FAIL（`sqlite3.OperationalError: no such table: candidate` 等）

- [ ] **Step 3: 在 `app/storage/db.py` 追加三张表**

在 `SCHEMA` 常量里，紧跟在现有 `hard_requirement` 表 DDL（`PRIMARY KEY (job_id, profile_version, field, operator, value)\n);\n"""` 之前的那个 `"""` 之前）插入：

```sql

-- ─────────────────────────────────────────────────────────────────────────
-- 以下 14 张表属变更包 m2-resume-parse-and-rank（交付单元 U1）。全部新表，
-- 走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：加列路径只服务
-- "老库缺列"这一种情况，新表不需要它。.51 上 data/demo.db 既有表一行不改，
-- 无数据迁移（design.md Migration Plan 第 1 条）。
-- ─────────────────────────────────────────────────────────────────────────

-- 候选人：全局唯一、无状态（CLAUDE.md 数据模型要点「状态属于投递不属于候选人」，
-- 状态挂在 application 上，这里不设任何状态列）。
--
-- 去重键是 (name, phone_hash)——design D11「候选人去重」：手机号本期只用于
-- 去重，以哈希存储，明文不落库；phone_hash 允许 NULL（解析没能拿到手机号时），
-- SQLite 的 UNIQUE 索引把多个 NULL 视为互不相等，多个"没手机号的李四"不会
-- 被误合并成一个人——这是刻意的保守选择，宁可留重复候选人，也不错误合并。
CREATE TABLE IF NOT EXISTS candidate (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    phone_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_name_phone
    ON candidate (name, phone_hash);

-- 简历文件记录。⛔ 刻意不设 candidate_id 列：上传时（U2 POST /resumes/upload）
-- 只知道 job_id，候选人身份要等解析完成才能确定并去重创建 candidate 行。
-- resume 与 candidate 的关联由 application（下方）一次性接起来，不在 resume
-- 上留一个"上传时必为 NULL、解析后才回填"的悬空外键。
--
-- sample_class 的四个取值对应 resume-upload-and-gate spec「批量上传入口」：
-- synthetic（U0 合成替身样本）/ anonymized（脱敏样本）/ departed（历史离职）/
-- live（真实在招，受真实简历入库闸拦截，D2）。
--
-- status 三态对应 resume-parsing spec「扫描件与不可读文件」：pending（刚上传
-- 未解析）/ parsed（解析完成）/ unreadable（识别后有效字符不足，进人工队列，
-- MUST NOT 以空字段进入后续判定与排序）。
--
-- parsed_json 存 app/schemas/resume_fields.py::ResumeFields 的 model_dump_json()；
-- parser_version 支持"同一份简历用新版本解析器重解析，新旧两版并存"
-- （resume-parsing spec「解析留痕与版本」）——重解析在 U2 会插入**新的 resume
-- 行**而不是覆盖本行，parser_version 是区分同一 content_sha256 下哪次解析
-- 结果最新的依据。
CREATE TABLE IF NOT EXISTS resume (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES job(id),
    sample_class TEXT NOT NULL CHECK (
        sample_class IN ('synthetic', 'anonymized', 'departed', 'live')
    ),
    file_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'parsed', 'unreadable')),
    parsed_json TEXT,
    parse_confidence REAL,
    parser_version TEXT,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 重复上传去重（resume-upload-and-gate spec「同一文件重复上传」）：按
-- (job_id, content_sha256) 唯一——同一文件传给不同岗位算两条独立记录
-- （代表两次独立的投递意图），同一文件传给同一岗位两次算重复。
CREATE UNIQUE INDEX IF NOT EXISTS idx_resume_job_content_hash
    ON resume (job_id, content_sha256);

CREATE INDEX IF NOT EXISTS idx_resume_job ON resume (job_id);

-- 简历原文分片 + 偏移量（resume-parsing spec「原文分片与字段回指」），字段与
-- app/parsing/spans.py::TextSpan(span_id, start, end, text) 一一对应，
-- start/end 是全文字符偏移，text 是该分片原文（去空白后的非空行）。
--
-- 复合主键 (resume_id, span_id)：与 hard_requirement 表同一形态，天然键就是
-- "这份简历的第几个分片"，不设代理主键。
CREATE TABLE IF NOT EXISTS resume_text_span (
    resume_id TEXT NOT NULL REFERENCES resume(id),
    span_id INTEGER NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (resume_id, span_id)
);
```

放置位置：插入到现有 `hard_requirement` 表 DDL 之后、`SCHEMA` 常量结束的 `"""` 之前。

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 跑全量回归，确认没有破坏既有测试**

Run: `./venv/bin/pytest -q`
Expected: 除本文件新增用例外，其余全部保持原有通过数（新表不影响任何既有查询）

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_db_m2_schema.py
git commit -m "feat(m2-u1): add candidate/resume/resume_text_span tables"
```

---

### Task 2: `application` / `stage` / `application_stage_history`（tasks 2.2）

**Files:**
- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m2_schema.py`（追加测试函数）

**Interfaces:**
- Consumes: `candidate(id)`（Task 1）、`resume(id)`（Task 1）、`job(id)`（既有）
- Produces: 表 `application(id, candidate_id, job_id, resume_id, current_stage_id, status, kanban_state, created_at)`；表 `stage(id, name, stage_type)`（预置三行 `id='initial'/'screening'/'rejected'`）；表 `application_stage_history(id, application_id, from_stage_id, to_stage_id, actor_type, actor, occurred_at)`。Task 3 的 `rejection_record.application_id` 会 `REFERENCES application(id)`；Task 4 的 `screening_flag.application_id` 同理。

**设计说明（`kanban_state` 现在就加列）**：`application.kanban_state='pending_reject'` 是 U5 任务 6.4「标记淘汰」写入的列，但那时 `application` 表已经存在于 `.51` 上，届时加列必须走 `_ADDED_COLUMNS` 那条 `ALTER TABLE` 路径——而新表能一步到位的字段没必要拆成两步。这与 `human_review.batch_id` 在 M1 阶段就预留给 M2 批量确认用是同一手法（`app/storage/db.py` 现有注释：「M2 批量确认的预留列，现在没有写入方，必须可空」）。

- [ ] **Step 1: 追加失败测试**

在 `tests/test_db_m2_schema.py` 末尾追加：

```python

# ── application / stage / application_stage_history（tasks 2.2）─────────


def _seed_job_candidate_resume(conn, job_id="j1", candidate_id="c1", resume_id="r1"):
    conn.execute("INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'approved')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (candidate_id,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'sha-1', 'hr-1')",
        (resume_id, job_id),
    )
    conn.commit()


def test_stage_table_preloads_three_rows(conn):
    rows = dict(conn.execute("SELECT id, stage_type FROM stage").fetchall())
    assert rows == {"initial": "initial", "screening": "screening", "rejected": "rejected"}


def test_stage_type_check_rejects_unknown_type(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage (id, name, stage_type) VALUES ('offer', '发 offer', 'offer')"
        )


def test_application_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "application")
    assert _columns(conn, "application") == {
        "id", "candidate_id", "job_id", "resume_id", "current_stage_id",
        "status", "kanban_state", "created_at",
    }


def test_application_resume_id_is_unique(conn):
    """一条 resume 只能挂一条 application——1:1。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES ('app-2', 'c1', 'j1', 'r1', 'initial')"
        )


def test_application_status_check(conn):
    _seed_job_candidate_resume(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
            "VALUES ('app-bad', 'c1', 'j1', 'r1', 'initial', 'unknown_status')"
        )


def test_application_kanban_state_defaults_null_and_accepts_pending_reject(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT kanban_state FROM application WHERE id='app-1'"
    ).fetchone()[0] is None

    conn.execute("UPDATE application SET kanban_state='pending_reject' WHERE id='app-1'")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE application SET kanban_state='bogus' WHERE id='app-1'")


def test_application_stage_history_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "application_stage_history")
    assert _columns(conn, "application_stage_history") == {
        "id", "application_id", "from_stage_id", "to_stage_id",
        "actor_type", "actor", "occurred_at",
    }


def test_application_stage_history_actor_type_check(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type) "
            "VALUES ('h-1', 'app-1', 'screening', 'system')"
        )


def test_status_lives_on_application_not_candidate(conn):
    """CLAUDE.md 数据模型要点：不要合并 candidate 和 application，状态挂在投递上。"""
    assert "status" not in _columns(conn, "candidate")
    assert "status" in _columns(conn, "application")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v -k "application or stage"`
Expected: FAIL（无表）

- [ ] **Step 3: 追加 DDL**

紧接 Task 1 新增的 `resume_text_span` 表之后追加：

```sql

-- 阶段池：全局共享，stage_type 是语义标签（逻辑只认类型），name 是可自定义
-- 显示名（CLAUDE.md 数据模型要点）。M2 预置三行，id 与 stage_type 同名——
-- 这三行现在就是全部合法阶段，日后要加自定义显示名的同类型阶段，走应用层
-- INSERT 新行（相同 stage_type、不同 id/name），本表结构不必改。
CREATE TABLE IF NOT EXISTS stage (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    stage_type TEXT NOT NULL CHECK (stage_type IN ('initial', 'screening', 'rejected'))
);

INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected');

-- 投递：独立实体，状态挂在这里而不是 candidate（CLAUDE.md 数据模型要点，
-- Horilla 的坑）。resume_id 唯一——一条简历对应一次投递意图，1:1（design D11）。
--
-- kanban_state 现在就加列（不等 U5 再 ALTER TABLE）：U5 tasks 6.4「标记淘汰」
-- 写 'pending_reject'，投递进入待确认清单但**不产生拒绝记录、阶段不变**
-- （hard-requirement-screening spec「淘汰只由人确认并可申诉」的前置状态）。
-- 现在没有写入方，必须可空——与 human_review.batch_id 同一手法。
CREATE TABLE IF NOT EXISTS application (
    id TEXT PRIMARY KEY NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    job_id TEXT NOT NULL REFERENCES job(id),
    resume_id TEXT NOT NULL REFERENCES resume(id),
    current_stage_id TEXT NOT NULL REFERENCES stage(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'withdrawn')),
    kanban_state TEXT CHECK (kanban_state IS NULL OR kanban_state IN ('pending_reject')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_application_resume ON application (resume_id);
CREATE INDEX IF NOT EXISTS idx_application_job ON application (job_id);
CREATE INDEX IF NOT EXISTS idx_application_candidate ON application (candidate_id);

-- 流转事实表：所有报表的基础（CLAUDE.md 数据模型要点）。actor_type 区分
-- 人工流转与系统流转（申诉 overturned 恢复阶段、批量确认淘汰流转都会写这里）。
-- from_stage_id 允许 NULL：投递创建时的第一条"进入 initial"没有"从哪来"。
CREATE TABLE IF NOT EXISTS application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_application_stage_history_application
    ON application_stage_history (application_id);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/pytest -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_db_m2_schema.py
git commit -m "feat(m2-u1): add application/stage/application_stage_history tables"
```

---

### Task 3: `rejection_record` 建表 ＋ 合规断言 fail-closed 翻转（tasks 2.3 ＋ U7 任务 8.1，同一交付单元合并）

**为什么这两件事必须是同一个 Task**：`app/audit/assertions.py::assert_no_ai_score_rejections` 现在的"表不存在"分支返回 `ok=True`（"M1 现状放行"）。一旦本 Task 建了 `rejection_record` 真表，`tests/test_audit_assertions.py` 与 `tests/test_audit_assertion_effectiveness.py` 里那个"临时 `CREATE TABLE rejection_record`（不用 `IF NOT EXISTS`）来模拟表存在"的 fixture 函数 `create_rejection_table()` 会当场撞上 `table already exists`——因为它们用的 `conn` fixture 已经跑过 `init_schema()`，真表已经在那了。如果只建表、不在同一个 Task 里把断言分支和这两份测试一起改完，这两个测试文件会在 Task 3 结束时保持红——违反"每个 Task 结束时全量测试绿"的执行纪律，也正是 opener 明确要求"与 U1 建表在同一交付单元合并，不跨 session"的原因（design.md Risks 原话：「`rejection_record` 缺表放行改为判失败，会让 M1 分支上的 CI 红」）。

**Files:**
- Modify: `app/storage/db.py`
- Modify: `app/audit/assertions.py`
- Modify: `tests/test_audit_assertions.py`
- Modify: `tests/test_audit_assertion_effectiveness.py`
- Modify: `tests/test_db_m2_schema.py`（追加表结构测试）

**Interfaces:**
- Consumes: `application(id)`（Task 2）；`app/audit/assertions.py` 现有常量 `REJECTION_TABLE = "rejection_record"`、`REJECTION_REASON_COLUMN = "reason_type"`、`AI_SCORE_REASON = "ai_score"`（本 Task 不改这三个常量的取值，只改判定逻辑）
- Produces: 表 `rejection_record(id, application_id, reason_type, rule_ref, human_readable, decided_by, batch_id, appeal_status, decided_at)`；`assert_no_ai_score_rejections()` 的返回值语义变化：表不存在 → `ok=False`

- [ ] **Step 1: 追加表结构失败测试（`tests/test_db_m2_schema.py`）**

```python

# ── rejection_record（tasks 2.3）─────────────────────────────────────────


def test_rejection_record_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "rejection_record")
    assert _columns(conn, "rejection_record") == {
        "id", "application_id", "reason_type", "rule_ref", "human_readable",
        "decided_by", "batch_id", "appeal_status", "decided_at",
    }


def test_rejection_record_reason_type_rejects_ai_score(conn):
    """合规红线机器判据：直接 INSERT reason_type='ai_score' 必须被 CHECK 拒绝。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
            "VALUES ('rej-1', 'app-1', 'ai_score', 'hr-1')"
        )


@pytest.mark.parametrize("reason_type", ["hard_rule", "human_decision"])
def test_rejection_record_reason_type_accepts_legal_values(conn, reason_type):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES (?, 'app-1', ?, 'hr-1')",
        (f"rej-{reason_type}", reason_type),
    )
    conn.commit()


def test_rejection_record_decided_by_cannot_be_blank(conn):
    """淘汰必须有人工确认节点并留痕——决策人为空的留痕等于没留痕
    （与 human_review.reviewer 同一 CHECK 手法）。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
            "VALUES ('rej-blank', 'app-1', 'hard_rule', '   ')"
        )


def test_rejection_record_appeal_status_defaults_none_and_check(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rej-1', 'app-1', 'hard_rule', 'hr-1')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT appeal_status FROM rejection_record WHERE id='rej-1'"
    ).fetchone()[0] == "none"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE rejection_record SET appeal_status='approved' WHERE id='rej-1'"
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v -k rejection`
Expected: FAIL（无表）

- [ ] **Step 3: 追加 DDL**

紧接 Task 2 新增的 `application_stage_history` 表之后追加：

```sql

-- 拒绝记录：淘汰事实的唯一落点（hard-requirement-screening spec「淘汰只由
-- 人确认并可申诉」）。reason_type 的 CHECK 是合规红线「AI 只做排序推荐，
-- 不做自动淘汰」在存储层的落点——⛔ 不得出现第三个取值，绕过应用层直接
-- INSERT 'ai_score' 同样被拒。
--
-- decided_by 的 CHECK 与 human_review.reviewer 同一手法（trim 第二参数显式
-- 列出空格/制表/换行/回车，SQLite 单参 trim() 只剥空格）：决策人为空的
-- 拒绝记录等于没有人为这次淘汰负责，红线「淘汰必须有人工确认并留痕」不允许
-- 这种记录存在。
--
-- appeal_status 状态机 none → requested → under_review → upheld | overturned
-- （hard-requirement-screening spec「淘汰只由人确认并可申诉」），流转合法性
-- 由应用层校验（U3 tasks 4.5），CHECK 只保证取值合法。
--
-- batch_id 支持批量确认（U5 tasks 6.5/6.6）共用同一批次标识，可空——单条
-- 逐份确认（U5 tasks 6.3）不产生批次。
CREATE TABLE IF NOT EXISTS rejection_record (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    reason_type TEXT NOT NULL CHECK (reason_type IN ('hard_rule', 'human_decision')),
    rule_ref TEXT,
    human_readable TEXT,
    decided_by TEXT NOT NULL CHECK (
        decided_by IS NOT NULL
        AND trim(decided_by, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    batch_id TEXT,
    appeal_status TEXT NOT NULL DEFAULT 'none' CHECK (
        appeal_status IN ('none', 'requested', 'under_review', 'upheld', 'overturned')
    ),
    decided_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rejection_record_application
    ON rejection_record (application_id);

CREATE INDEX IF NOT EXISTS idx_rejection_record_batch
    ON rejection_record (batch_id);
```

- [ ] **Step 4: 跑表结构测试确认通过**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 跑全量回归，观察 ripple**

Run: `./venv/bin/pytest -q`
Expected: `tests/test_audit_assertions.py` 与 `tests/test_audit_assertion_effectiveness.py` 出现 FAIL——`create_rejection_table()` 尝试 `CREATE TABLE rejection_record`（无 `IF NOT EXISTS`）撞上真表已存在。这是本 Step 预期看到的红，下一步修复。

- [ ] **Step 6: 翻转 `app/audit/assertions.py` 的 fail-closed 分支**

打开 `app/audit/assertions.py`，定位 `assert_no_ai_score_rejections` 函数（约第 104 行），把"表不存在"分支从 `ok=True` 改为 `ok=False`：

```python
def assert_no_ai_score_rejections(conn: sqlite3.Connection) -> AssertionResult:
    """合规红线「AI 只做排序推荐，不做自动淘汰」的机器判据。

    自 m2-resume-parse-and-rank U1 起，rejection_record 已建表（M1 的"缺表
    放行"过渡分支停用）。表不存在 MUST 判失败——m2-compliance-assertions
    spec「拒绝记录表缺失即失败」：这不再是"还没到能验证的时候"，是"该在的
    表不在"，fail-closed。
    """
    if not _table_exists(conn, REJECTION_TABLE):
        return AssertionResult(
            name=ASSERTION_NO_AI_SCORE_REJECTION,
            ok=False,
            violations=({"table": REJECTION_TABLE, "issue": "table_missing"},),
            detail=(
                f"{REJECTION_TABLE} 表不存在。自 M2 U1 起本表应始终存在——"
                "缺表意味着红线「AI 只做排序推荐，不做自动淘汰」完全没有机器守护，"
                "fail-closed：验不了就算不通过。"
            ),
        )

    columns = _columns(conn, REJECTION_TABLE)
```

（其余分支——缺列、有违例行——保持不变，紧跟在这段之后的既有代码不动。）

- [ ] **Step 7: 修复 `tests/test_audit_assertions.py` 的 fixture ripple**

第一处：`create_rejection_table()` 辅助函数（约第 79–98 行）不再需要"建表"——表现在总是已经存在（`conn` fixture 跑了 `init_schema()`）。把它整个替换为一个插入辅助函数：

**⚠️ `rejection_record.application_id` 带真实外键**（`REFERENCES application(id)`，本 Task Step 3 刚建），与 `resume_access_log` 那类"按事件记事实、刻意不加外键"的审计表不同——拒绝记录是业务数据，必须指向一条真实存在的投递。旧的 `create_rejection_table()` 时代表没有外键，任意字符串 `application_id` 都能插进去；新表有外键后，插入前必须先有一条真实的 `application` 行（连带 `job`/`candidate`/`resume`），否则撞 `sqlite3.IntegrityError: FOREIGN KEY constraint failed`。所以插入辅助函数要顺带把这条链路造好：

```python
def _ensure_application_exists(conn: sqlite3.Connection, application_id: str) -> None:
    """rejection_record.application_id 有真实外键，必须指向一条真实存在的投递。
    INSERT OR IGNORE 让重复调用同一个 application_id 幂等，测试可以放心多次调用。"""
    job_id = f"{application_id}-job"
    candidate_id = f"{application_id}-cand"
    resume_id = f"{application_id}-resume"
    conn.execute(
        "INSERT OR IGNORE INTO job (id, title, status) VALUES (?, 'x', 'approved')",
        (job_id,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO candidate (id, name) VALUES (?, 'x')", (candidate_id,)
    )
    conn.execute(
        "INSERT OR IGNORE INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', ?, 'hr-1')",
        (resume_id, job_id, f"{application_id}-sha"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (application_id, candidate_id, job_id, resume_id),
    )
    conn.commit()


def insert_rejection_record(
    conn: sqlite3.Connection,
    *,
    row_id: str,
    reason_type: str,
    application_id: str = "app-9",
    decided_by: str = "hr-1",
) -> None:
    """往真实的 rejection_record 表插一行（表由 init_schema() 建好，本函数不再建表）。

    ⚠️ reason_type 必须是 CHECK 允许的取值（hard_rule / human_decision）；
    要模拟"绕过应用层写入 ai_score"，调用方自己开 PRAGMA ignore_check_constraints
    （见 tests/test_audit_assertion_effectiveness.py 里 insert_score 的
    ignore_checks 同一手法）——本函数不做这件事，保持"只是插入"的单一职责。
    """
    _ensure_application_exists(conn, application_id)
    conn.execute(
        f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}, decided_by) "
        f"VALUES (?, ?, ?, ?)",
        (row_id, application_id, reason_type, decided_by),
    )
    conn.commit()
```

第二处：`test_ai_score_rejection_assertion_passes_when_table_absent`（约第 106–114 行）改名并翻转断言，且改用**不跑 `init_schema()` 的裸连接**（因为标准 `conn` fixture 现在总有真表）：

```python
def test_ai_score_rejection_assertion_fails_when_table_absent():
    """M2 起：表本该在但不在 → 断言必须失败，⛔ 不再是"M1 现状放行"。

    用裸连接而不是 conn fixture——conn fixture 跑 init_schema() 后表总是
    存在，这里要测的正是"表缺失"这个分支，必须绕开 init_schema()。
    """
    bare = sqlite3.connect(":memory:")

    result = assert_no_ai_score_rejections(bare)

    assert result.ok is False
    assert result.violations != ()
    assert REJECTION_TABLE in result.detail
```

第三处：`test_ai_score_rejection_assertion_passes_on_clean_table`（约第 117–125 行）改用新插入辅助函数，`reason_type` 换成合法值：

```python
def test_ai_score_rejection_assertion_passes_on_clean_table(conn):
    insert_rejection_record(conn, row_id="rej-1", reason_type="human_decision")

    result = assert_no_ai_score_rejections(conn)

    assert result.ok is True
    assert result.violations == ()
```

第四处：`test_ai_score_rejection_assertion_fails_when_reason_column_missing`（约第 131–139 行）改用裸连接手工建一张缺列的表（这个场景——表存在但缺列——依然有意义，模拟一次改坏的迁移）：

```python
def test_ai_score_rejection_assertion_fails_when_reason_column_missing():
    """表建了但没有 reason_type 列 → 判失败，⛔ 不判通过。"""
    bare = sqlite3.connect(":memory:")
    bare.execute(
        f"CREATE TABLE {REJECTION_TABLE} (id TEXT PRIMARY KEY, application_id TEXT)"
    )
    bare.commit()

    result = assert_no_ai_score_rejections(bare)

    assert result.ok is False
    assert result.violations != ()
    assert REJECTION_REASON_COLUMN in str(result.violations)
```

删除文件顶部不再需要的 `import sqlite3` 冲突检查（该 import 已存在，保留）；`create_rejection_table` 的原有导入方（`tests/test_audit_assertion_effectiveness.py`）在下一步同步改名导入。

- [ ] **Step 8: 修复 `tests/test_audit_assertion_effectiveness.py` 的 fixture ripple**

顶部 import（约第 33–37 行）把 `create_rejection_table` 换成 `insert_rejection_record`，并同时导入 `_ensure_application_exists`——下面三处直接绕过 `insert_rejection_record`、手写 `PRAGMA ignore_check_constraints` 的 INSERT 同样要先有真实的 `application` 行（理由同 Step 7）：

```python
from tests.test_audit_assertions import (
    _ensure_application_exists,
    insert_rejection_record,
    insert_run,
    insert_score,
)
```

`test_ai_score_rejection_is_detected`（约第 79–98 行）：真表现在有 CHECK 拒绝 `ai_score`，要模拟"绕过应用层"必须先关闭 CHECK（与同文件里 `insert_score` 的 `ignore_checks` 手法一致），验证 Python 侧断言是 CHECK 之上的第二道防线：

```python
def test_ai_score_rejection_is_detected(conn):
    """故意插一条 reason_type='ai_score' 的拒绝记录 → 断言必须失败。

    reason_type 现在有数据库 CHECK 挡着（Task 3 新增），要造出"CHECK 被绕过"
    的违例场景必须先关掉它——这正是 design.md Risks「SQLite 的 CHECK 可以被
    ignore_check_constraints 关掉，所以事后断言是 CHECK 之上的纵深防御」
    要验证的那条：CHECK 挡住了应用层，Python 断言挡住"CHECK 被人为关掉"这种
    更极端的情况。application_id 上还有真实外键，先用 _ensure_application_exists
    把它指向的那条投递造出来，否则这条 INSERT 会撞 FOREIGN KEY constraint failed。
    """
    _ensure_application_exists(conn, "app-9")
    conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        conn.execute(
            f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}, decided_by) "
            f"VALUES ('rej-bad', 'app-9', ?, 'hr-1')",
            (AI_SCORE_REASON,),
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA ignore_check_constraints = OFF")

    result = assert_no_ai_score_rejections(conn)

    assert result.ok is False, "插了 ai_score 拒绝记录，断言仍然通过 = 断言恒真"
    assert len(result.violations) == 1
    assert result.violations[0]["id"] == "rej-bad"
    assert result.violations[0][REJECTION_REASON_COLUMN] == AI_SCORE_REASON
```

`test_ai_score_rejection_assertion_ignores_other_reasons`（约第 101–116 行）：对照组的两个"非 ai_score"取值换成 CHECK 允许的合法值（原先的 `manual_review` / `candidate_withdrew` 现在会被 CHECK 拒绝，且这两个值本来就不是本项目定义的合法 `reason_type`）：

```python
def test_ai_score_rejection_assertion_ignores_other_reasons(conn):
    """反向对照：非 ai_score 的拒绝记录**不该**触发这条断言。"""
    for row_id, reason in (("r-1", "hard_rule"), ("r-2", "human_decision")):
        insert_rejection_record(conn, row_id=row_id, reason_type=reason)

    assert assert_no_ai_score_rejections(conn).ok is True
```

`test_run_compliance_assertions_reports_every_broken_line_at_once`（约第 282–310 行）里的 `create_rejection_table(conn)` + 手写 INSERT（约第 287–292 行）换成同样的 CHECK 绕过写法，同样先 `_ensure_application_exists`：

```python
    _ensure_application_exists(conn, "app-9")
    conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        conn.execute(
            f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}, decided_by) "
            f"VALUES ('rej-bad', 'app-9', ?, 'hr-1')",
            (AI_SCORE_REASON,),
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA ignore_check_constraints = OFF")
```

（该函数其余部分——`insert_run` / `insert_score` 调用与后续断言——不变。）

`test_failing_assertion_always_carries_violations`（约第 313–324 行）里的 `create_rejection_table(conn, with_reason_column=False)` 不再可行（真表已存在，且该场景改用裸连接测试更合适，本函数只需要"至少一条断言失败且带 violations"，改用 CHECK 绕过插入 `ai_score` 行即可达到同样效果，不必单独制造缺列表）：

```python
def test_failing_assertion_always_carries_violations(conn):
    """结构性守护：任何 ok=False 的结果都必须带 violations。"""
    _ensure_application_exists(conn, "app-9")
    conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        conn.execute(
            f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}, decided_by) "
            f"VALUES ('rej-bad', 'app-9', ?, 'hr-1')",
            (AI_SCORE_REASON,),
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA ignore_check_constraints = OFF")

    results = run_compliance_assertions(conn)

    for result in results:
        if not result.ok:
            assert result.violations, f"{result.name} 失败但没有指出违例记录"
```

- [ ] **Step 9: 全量回归**

Run: `./venv/bin/pytest -q`
Expected: 全绿，包括 `tests/test_audit_assertions.py`、`tests/test_audit_assertion_effectiveness.py`、`tests/test_db_m2_schema.py`

- [ ] **Step 10: Commit**

```bash
git add app/storage/db.py app/audit/assertions.py tests/test_db_m2_schema.py \
        tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py
git commit -m "feat(m2-u1): add rejection_record table, flip missing-table assertion to fail-closed"
```

---

### Task 4: `resume_access_log` / `field_review_queue` / `screening_flag`（tasks 2.4）

**Files:**
- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m2_schema.py`

**Interfaces:**
- Consumes: `resume(id)`（Task 1）、`application(id)`（Task 2）
- Produces: 表 `resume_access_log(id, accessor, resume_id, access_type, accessed_at)`；表 `field_review_queue(id, resume_id, field, machine_value, confidence, status, reviewed_by, reviewed_at, human_value, created_at)`；表 `screening_flag(id, application_id, profile_version, rule_ref, verdict, reason, evidence_ref, created_at)`

- [ ] **Step 1: 追加失败测试**

```python

# ── resume_access_log / field_review_queue / screening_flag（tasks 2.4）─


def test_resume_access_log_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume_access_log")
    assert _columns(conn, "resume_access_log") == {
        "id", "accessor", "resume_id", "access_type", "accessed_at",
    }


def test_resume_access_log_has_no_content_columns(conn):
    """spec「简历访问留痕」：留痕记录 MUST NOT 包含简历内容本身。"""
    cols = _columns(conn, "resume_access_log")
    assert not ({"text", "content", "parsed_json"} & cols)


def test_resume_access_log_accessor_cannot_be_blank(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
            "VALUES ('log-1', '  ', 'r-x', 'raw_text')"
        )


@pytest.mark.parametrize("access_type", ["raw_text", "spans", "parsed_result", "download"])
def test_resume_access_log_access_type_accepts_four_values(conn, access_type):
    conn.execute(
        "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
        "VALUES (?, 'hr-1', 'r-x', ?)",
        (f"log-{access_type}", access_type),
    )
    conn.commit()


def test_resume_access_log_access_type_rejects_unknown_value(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
            "VALUES ('log-bad', 'hr-1', 'r-x', 'preview')"
        )


def test_field_review_queue_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "field_review_queue")
    assert _columns(conn, "field_review_queue") == {
        "id", "resume_id", "field", "machine_value", "confidence",
        "status", "reviewed_by", "reviewed_at", "human_value", "created_at",
    }


def test_field_review_queue_status_check(conn):
    _seed_job_candidate_resume(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO field_review_queue (id, resume_id, field, status) "
            "VALUES ('q-1', 'r1', 'years_of_experience', 'closed')"
        )


def test_field_review_queue_rejects_second_pending_row_for_same_field(conn):
    """结构性幂等guard：同一简历同一字段不该同时有两条待校对行。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO field_review_queue (id, resume_id, field) VALUES ('q-1', 'r1', 'years_of_experience')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO field_review_queue (id, resume_id, field) VALUES ('q-2', 'r1', 'years_of_experience')"
        )


def test_screening_flag_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "screening_flag")
    assert _columns(conn, "screening_flag") == {
        "id", "application_id", "profile_version", "rule_ref",
        "verdict", "reason", "evidence_ref", "created_at",
    }


def test_screening_flag_verdict_check(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict) "
            "VALUES ('sf-1', 'app-1', 1, 'edu-gte-bachelor', 'maybe')"
        )


def test_screening_flag_fail_requires_evidence_ref(conn):
    """工程铁律 4：每条 fail 标记必须带 evidence_ref，为空不允许写入。"""
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict, evidence_ref) "
            "VALUES ('sf-1', 'app-1', 1, 'edu-gte-bachelor', 'fail', NULL)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict, evidence_ref) "
            "VALUES ('sf-2', 'app-1', 1, 'edu-gte-bachelor', 'fail', '   ')"
        )


def test_screening_flag_pass_and_skipped_allow_null_evidence(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO screening_flag "
        "(id, application_id, profile_version, rule_ref, verdict) "
        "VALUES ('sf-pass', 'app-1', 1, 'edu-gte-bachelor', 'pass')"
    )
    conn.execute(
        "INSERT INTO screening_flag "
        "(id, application_id, profile_version, rule_ref, verdict) "
        "VALUES ('sf-skip', 'app-1', 1, 'years-gte-3', 'skipped')"
    )
    conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v -k "access_log or field_review or screening_flag"`
Expected: FAIL（无表）

- [ ] **Step 3: 追加 DDL**

紧接 Task 3 新增的 `rejection_record` 表之后追加：

```sql

-- 简历访问留痕（resume-upload-and-gate spec「简历访问留痕」）。⛔ resume_id
-- 上刻意不加外键——与 human_review.job_id、effect_log.thread_id 同一形态：
-- 留痕表按事件记事实，把它的可写性绑在业务表上，"留痕写不进去"就会变成
-- "读取整个失败"，而 spec 明确"留痕写入失败 MUST 读取失败"——这条约束应该
-- 由应用层的写入顺序保证（先留痕后返回内容），不该由外键去意外触发。
--
-- 无内容列（spec「留痕记录 MUST NOT 包含简历内容本身」）：只有访问者/简历
-- 标识/时刻/类型四列。
--
-- accessor 的 CHECK 与 human_review.reviewer / rejection_record.decided_by
-- 同一手法：空访问者等于没有留痕。
--
-- access_type 四态对应 resume-parsing 管线的四种读取入口（tasks 3.9）：
-- raw_text（原文）/ spans（分片）/ parsed_result（解析结果）/ download（下载）。
CREATE TABLE IF NOT EXISTS resume_access_log (
    id TEXT PRIMARY KEY NOT NULL,
    accessor TEXT NOT NULL CHECK (
        accessor IS NOT NULL
        AND trim(accessor, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    resume_id TEXT NOT NULL,
    access_type TEXT NOT NULL CHECK (
        access_type IN ('raw_text', 'spans', 'parsed_result', 'download')
    ),
    accessed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_resume_access_log_resume ON resume_access_log (resume_id);

-- 人工校对队列（resume-parsing spec「字段级置信度与人工校对队列」）。一条
-- pending 行代表"这个字段机器没把握，等人校对"；校对完成后本行 status 改为
-- reviewed 并落 human_value/reviewed_by/reviewed_at（U2 tasks 3.10），⛔ 不产生
-- 第二条行——校对动作 MUST 幂等。
--
-- 部分唯一索引（WHERE status='pending'）是这条幂等性的结构性第二道防线：
-- 同一简历同一字段最多同时存在一条 pending 行，⛔ 不会出现两条队列行互相
-- 矛盾地等待同一个字段被校对。
CREATE TABLE IF NOT EXISTS field_review_queue (
    id TEXT PRIMARY KEY NOT NULL,
    resume_id TEXT NOT NULL REFERENCES resume(id),
    field TEXT NOT NULL,
    machine_value TEXT,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'reviewed')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    human_value TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_field_review_queue_resume ON field_review_queue (resume_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_field_review_queue_pending_unique
    ON field_review_queue (resume_id, field)
    WHERE status = 'pending';

-- 硬门槛标记（hard-requirement-screening spec「逐条判定只标记不淘汰」）。
-- verdict 三态：pass / fail / skipped。fail 必带 evidence_ref 的 CHECK 是
-- 工程铁律 4「每条 criterion_score 必须有 evidence_ref」在硬门槛标记这一侧
-- 的对应落点——fail 标记同样是"判定"，同样不能没有依据（spec「每条 fail
-- 标记带原文依据」：回指为空的 fail 标记 MUST NOT 写入）。pass/skipped 不要求
-- evidence_ref：pass 代表满足、skipped 代表跳过判定，两者都不是"依据某处原文
-- 判定不符合"，不适用同一约束。
CREATE TABLE IF NOT EXISTS screening_flag (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    profile_version INTEGER NOT NULL,
    rule_ref TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'skipped')),
    reason TEXT,
    evidence_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        verdict != 'fail'
        OR (
            evidence_ref IS NOT NULL
            AND trim(evidence_ref, ' ' || char(9) || char(10) || char(13)) != ''
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_screening_flag_application ON screening_flag (application_id);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/pytest -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_db_m2_schema.py
git commit -m "feat(m2-u1): add resume_access_log/field_review_queue/screening_flag tables"
```

---

### Task 5: `resume_embedding` / `eval_import_batch` / `eval_sample` / `eval_annotation` / `hr_account`（tasks 2.5）

**Files:**
- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m2_schema.py`

**Interfaces:**
- Consumes: `resume(id)`（Task 1）
- Produces: 表 `resume_embedding(resume_id, model, dim, vector, created_at)`；表 `eval_import_batch(id, job_id, source_archive_path, imported_by, imported_at, row_count)`；表 `eval_sample(id, job_id, sample_ref, import_batch_id, created_at)`；表 `eval_annotation(id, eval_sample_id, field_values_json, human_rank, annotated_by, annotated_at, import_batch_id, created_at)`；表 `hr_account(id, username, password_hash, password_salt, created_at)`。Task 8 的 `scripts/create_hr_account.py` 直接写 `hr_account`。

**设计说明（`eval_annotation` 为什么不对 `eval_sample_id` 加唯一索引）**：eval-set-and-metrics spec「判例批改表格式与导入校验」要求"标注值变化时以最新一次为准并保留历史"——同一样本可能被多批回件重复标注（例如标注人改口径后重新标一批），每次导入都要留痕而不是覆盖。真正需要幂等的是"同一批次重复导入"，所以唯一索引落在 `(import_batch_id, eval_sample_id)` 而不是单独的 `eval_sample_id`；查询"当前有效标注"由应用层按 `annotated_at`/`created_at` 取最新一行（U6 tasks 7.2/7.3 的范围，不在本任务实现）。

- [ ] **Step 1: 追加失败测试**

```python

# ── resume_embedding / eval_* / hr_account（tasks 2.5）───────────────────


def test_resume_embedding_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "resume_embedding")
    assert _columns(conn, "resume_embedding") == {
        "resume_id", "model", "dim", "vector", "created_at",
    }


def test_resume_embedding_primary_key_is_resume_and_model(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
        "VALUES ('r1', 'bge-m3', 1024, X'0102')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
            "VALUES ('r1', 'bge-m3', 1024, X'0304')"
        )


def test_resume_embedding_allows_multiple_models_per_resume(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
        "VALUES ('r1', 'bge-m3', 1024, X'0102')"
    )
    conn.execute(
        "INSERT INTO resume_embedding (resume_id, model, dim, vector) "
        "VALUES ('r1', 'bge-m3-v2', 1024, X'0304')"
    )
    conn.commit()


def test_eval_import_batch_table_exists_and_has_training_ban_comment(conn):
    assert _table_exists(conn, "eval_import_batch")
    assert _columns(conn, "eval_import_batch") == {
        "id", "job_id", "source_archive_path", "imported_by", "imported_at", "row_count",
    }
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='eval_import_batch'"
    ).fetchone()[0]
    assert "训练" in sql


def test_eval_sample_table_exists_and_has_training_ban_comment(conn):
    assert _table_exists(conn, "eval_sample")
    assert _columns(conn, "eval_sample") == {
        "id", "job_id", "sample_ref", "import_batch_id", "created_at",
    }
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='eval_sample'"
    ).fetchone()[0]
    assert "训练" in sql


def test_eval_annotation_table_exists_and_has_training_ban_comment(conn):
    assert _table_exists(conn, "eval_annotation")
    assert _columns(conn, "eval_annotation") == {
        "id", "eval_sample_id", "field_values_json", "human_rank",
        "annotated_by", "annotated_at", "import_batch_id", "created_at",
    }
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='eval_annotation'"
    ).fetchone()[0]
    assert "训练" in sql


def _seed_eval_batch_and_sample(conn, batch_id="b1", sample_id="s1"):
    conn.execute(
        "INSERT INTO eval_import_batch (id, job_id, imported_by, row_count) "
        "VALUES (?, 'j1', 'hr-1', 1)",
        (batch_id,),
    )
    conn.execute(
        "INSERT INTO eval_sample (id, job_id, sample_ref, import_batch_id) "
        "VALUES (?, 'j1', 'sample-001', ?)",
        (sample_id, batch_id),
    )
    conn.commit()


def test_eval_annotation_same_batch_reimport_is_rejected_by_unique_index(conn):
    """同一批次重复导入不产生重复标注——唯一索引在 (import_batch_id, eval_sample_id)。"""
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    _seed_eval_batch_and_sample(conn)
    conn.execute(
        "INSERT INTO eval_annotation "
        "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
        "VALUES ('ann-1', 's1', '{}', '汤丽萍', '2026-09-17 10:00:00', 'b1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO eval_annotation "
            "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
            "VALUES ('ann-2', 's1', '{}', '汤丽萍', '2026-09-17 10:05:00', 'b1')"
        )


def test_eval_annotation_keeps_history_across_different_batches(conn):
    """不同批次可以对同一样本再标注一次——保留历史，不覆盖。"""
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    _seed_eval_batch_and_sample(conn, batch_id="b1", sample_id="s1")
    conn.execute(
        "INSERT INTO eval_import_batch (id, job_id, imported_by, row_count) VALUES ('b2', 'j1', 'hr-1', 1)"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO eval_annotation "
        "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
        "VALUES ('ann-1', 's1', '{}', '汤丽萍', '2026-09-17 10:00:00', 'b1')"
    )
    conn.execute(
        "INSERT INTO eval_annotation "
        "(id, eval_sample_id, field_values_json, annotated_by, annotated_at, import_batch_id) "
        "VALUES ('ann-2', 's1', '{}', '汤丽萍', '2026-09-20 10:00:00', 'b2')"
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM eval_annotation WHERE eval_sample_id='s1'"
    ).fetchone()[0]
    assert count == 2


def test_hr_account_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "hr_account")
    assert _columns(conn, "hr_account") == {
        "id", "username", "password_hash", "password_salt", "created_at",
    }


def test_hr_account_username_is_unique(conn):
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('acc-1', 'tangliping', 'h1', 's1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO hr_account (id, username, password_hash, password_salt) "
            "VALUES ('acc-2', 'tangliping', 'h2', 's2')"
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v -k "embedding or eval_ or hr_account"`
Expected: FAIL（无表）

- [ ] **Step 3: 追加 DDL**

紧接 Task 4 新增的 `screening_flag` 表之后追加：

```sql

-- 简历向量（design D7「向量存储」：SQLite 上进程内实现，BLOB 存表，召回时
-- 全量读入 numpy 算 cosine，⛔ 不引入 pgvector/FAISS）。复合主键
-- (resume_id, model)：同一简历可能有多个模型版本的向量并存（U0 model 定型
-- 前的对比阶段），U4 tasks 5.1 的幂等键 {resume_id}:embed:{model} 与此对应。
CREATE TABLE IF NOT EXISTS resume_embedding (
    resume_id TEXT NOT NULL REFERENCES resume(id),
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resume_id, model)
);

-- ⚠️ 禁止训练用途注释**刻意放在括号内、紧跟 CREATE TABLE 之后**（不是放在
-- 语句前面）：SQLite 的 sqlite_master.sql 只保存语句本身的文本，语句前的
-- 独立注释行不会被收进去——Task 7 的反证测试要靠 `SELECT sql FROM
-- sqlite_master` 机器检查这行注释存在，放在语句外会让该检查读到空气、
-- 静默总是通过（Task 7 的 test_eval_*_table_exists_and_has_training_ban_comment
-- 三条用例已经把这条踩过一次）。三张表同一口径，与 analysis_run 表头注释
-- 一致：本表内容禁止用作任何模型的训练、微调、prompt 自动优化输入。理由：
-- 历史标注与人工排序携带既有偏见，拿它当监督信号会把偏见放大并固化
-- （Amazon 2018 教训，CLAUDE.md 合规红线「绝不用历史录用结果做监督信号」）。
-- 三张表只服务离线指标计算（U6）。
CREATE TABLE IF NOT EXISTS eval_import_batch (
    -- ⚠️ 禁止训练用途：本表内容禁止用作任何模型的训练、微调、prompt 自动优化输入。
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    source_archive_path TEXT,
    imported_by TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    row_count INTEGER NOT NULL
);

-- sample_ref 是「判例批改表」里的样本标识（eval-set-and-metrics spec「判例
-- 批改表格式与导入校验」），不直接存简历内容——评测集样本文件本身在
-- data/eval/ 目录（不进版本库，U6 tasks 7.5）。
CREATE TABLE IF NOT EXISTS eval_sample (
    -- ⚠️ 禁止训练用途，同上。
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    sample_ref TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES eval_import_batch(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- field_values_json 存六字段人工标注值，human_rank 存人工排序名次
-- （eval-set-and-metrics spec「四项验收指标可一键计算」用它算 Spearman/
-- Top-10 召回）。
--
-- ⛔ 不对 eval_sample_id 单独加唯一索引：spec「标注值变化时以最新一次为准
-- 并保留历史」要求同一样本可以被不同批次重复标注、旧标注保留。唯一索引落在
-- (import_batch_id, eval_sample_id)：这条防的是"同一批次文件重复导入"产生
-- 重复行（eval-set-and-metrics spec「导入 MUST 幂等」），不同批次对同一样本
-- 的标注视为历史演进，两者都合法存在。"当前有效标注"取最新一行是应用层
-- （U6 report 命令）的查询逻辑，不是本表结构的责任。
CREATE TABLE IF NOT EXISTS eval_annotation (
    -- ⚠️ 禁止训练用途，同上。
    id TEXT PRIMARY KEY NOT NULL,
    eval_sample_id TEXT NOT NULL REFERENCES eval_sample(id),
    field_values_json TEXT NOT NULL,
    human_rank INTEGER,
    annotated_by TEXT NOT NULL,
    annotated_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES eval_import_batch(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_annotation_batch_sample
    ON eval_annotation (import_batch_id, eval_sample_id);

-- HR 本地账号（design D12：鉴权从空壳换成本地账号，签名不变）。密码以
-- PBKDF2-HMAC-SHA256 加盐哈希存储（app/storage/hr_account.py，Task 8），
-- ⛔ 不存明文、不存可逆加密。username 唯一——每人一个账号（部署约束 5
-- 「共享口令不满足」）。
CREATE TABLE IF NOT EXISTS hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/pytest -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_db_m2_schema.py
git commit -m "feat(m2-u1): add resume_embedding/eval_*/hr_account tables"
```

---

### Task 6: `analysis_run.run_type` 语义约定 ＋ `criterion_score.evidence_ref` 的 JSON 回指约定（tasks 2.6）

**Files:**
- Create: `app/audit/run_type.py`
- Create: `app/audit/evidence_ref.py`
- Test: `tests/test_run_type_and_evidence_ref.py`

**Interfaces:**
- Consumes: 无新表依赖（`analysis_run.prompt_version` 与 `criterion_score.evidence_ref` 是 M1 已有列，本任务不改列结构）
- Produces: `app.audit.run_type.run_type_of(prompt_version: str) -> str`（返回 `"parse"` 或 `"rank"`，未登记前缀抛 `ValueError`）；`app.audit.evidence_ref.EvidenceRef`（`span_id: int, start: int, end: int` 的 frozen dataclass）、`format_evidence_ref(span_id, start, end) -> str`、`parse_evidence_ref(raw: str) -> EvidenceRef`（U4 tasks 5.4/5.5 在精排评分落库时会用这两个函数）

- [ ] **Step 1: 写失败测试**

```python
"""analysis_run.run_type 语义约定 ＋ criterion_score.evidence_ref 的 JSON 回指约定（tasks 2.6）。

两者都不改表结构：run_type 不加列，用 prompt_version 前缀区分（design.md
「analysis_run 增加 run_type 语义约定...⛔ 不加列」）；evidence_ref 列本身
仍是自由文本（M1 intake 场景继续可写自由格式字符串），这里只新增一种可选的
JSON 编码约定，服务 M2 精排把 evidence 存成 {span_id,start,end} 的场景。
"""
import json

import pytest

from app.audit.evidence_ref import EvidenceRef, format_evidence_ref, parse_evidence_ref
from app.audit.run_type import RUN_TYPE_PARSE, RUN_TYPE_RANK, run_type_of


def test_run_type_of_parse_prefix():
    assert run_type_of("parse-v1") == RUN_TYPE_PARSE
    assert run_type_of("parse-v2") == RUN_TYPE_PARSE


def test_run_type_of_rank_prefix():
    assert run_type_of("rank-v1") == RUN_TYPE_RANK


def test_run_type_of_unregistered_prefix_raises():
    with pytest.raises(ValueError, match="未登记"):
        run_type_of("score-v1")


def test_format_evidence_ref_produces_expected_json():
    raw = format_evidence_ref(span_id=6, start=120, end=180)
    assert json.loads(raw) == {"span_id": 6, "start": 120, "end": 180}


def test_parse_evidence_ref_round_trips():
    raw = format_evidence_ref(span_id=6, start=120, end=180)
    ref = parse_evidence_ref(raw)
    assert ref == EvidenceRef(span_id=6, start=120, end=180)


def test_parse_evidence_ref_rejects_non_json_string():
    """M1 intake 的自由格式回指（如 "resume-1#120-180"）不是本约定的合法输入，
    调用方必须先判断是否是 JSON 再调用本函数——本函数不做静默兜底。"""
    with pytest.raises(json.JSONDecodeError):
        parse_evidence_ref("resume-1#120-180")


def test_parse_evidence_ref_rejects_missing_keys():
    with pytest.raises(KeyError):
        parse_evidence_ref(json.dumps({"span_id": 6, "start": 120}))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/pytest tests/test_run_type_and_evidence_ref.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.audit.run_type'`）

- [ ] **Step 3: 实现 `app/audit/run_type.py`**

```python
"""analysis_run.run_type 语义约定：不加列，用 prompt_version 前缀区分。

design.md 决策：「analysis_run 增加 run_type 语义约定（parse/rank，可空列已存在
的用 prompt_version 前缀区分，⛔ 不加列）」。⛔ 不要给这个模块加数据库读写——
它是纯函数，只做字符串前缀判定，不 import app.storage。
"""
from __future__ import annotations

RUN_TYPE_PARSE = "parse"
RUN_TYPE_RANK = "rank"

# 前缀 → run_type。design.md 与 tasks.md 里已出现的具体值：
# "parse-v1"（resume-parsing spec，U2 tasks 3.5）、"rank-v1"（candidate-ranking
# spec，U4 tasks 5.4）。新增运行类型时在这里加一行，⛔ 不要在别处再判一次前缀
# ——散成两处会出现"一处判 parse 一处判 rank"的分叉。
_PREFIX_TO_RUN_TYPE: dict[str, str] = {
    "parse-": RUN_TYPE_PARSE,
    "rank-": RUN_TYPE_RANK,
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

- [ ] **Step 4: 实现 `app/audit/evidence_ref.py`**

```python
"""criterion_score.evidence_ref 的 JSON 回指约定：{"span_id", "start", "end"}。

列本身仍是自由文本（M1 intake 场景可以继续写 "resume-1#120-180" 这类自由
格式字符串），这里新增的是 M2 精排（U4 tasks 5.4/5.5）落库时使用的一种
**可选**编码方式——评分的 evidence 天然就是 span_id + 偏移量，JSON 编码
让它可以被程序化解析，而不必像自由文本那样正则拆解。
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceRef:
    span_id: int
    start: int
    end: int


def format_evidence_ref(span_id: int, start: int, end: int) -> str:
    return json.dumps(
        {"span_id": span_id, "start": start, "end": end}, ensure_ascii=False
    )


def parse_evidence_ref(raw: str) -> EvidenceRef:
    """⛔ 不做静默兜底：非 JSON 输入直接抛 json.JSONDecodeError，缺键直接抛
    KeyError。调用方（U4）在写入前自己保证格式，读取历史 M1 自由文本行的
    调用方要先判断是否是本约定的 JSON，不能指望本函数替它兜底。"""
    data = json.loads(raw)
    return EvidenceRef(span_id=data["span_id"], start=data["start"], end=data["end"])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `./venv/bin/pytest tests/test_run_type_and_evidence_ref.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 全量回归**

Run: `./venv/bin/pytest -q`
Expected: 全绿

- [ ] **Step 7: Commit**

```bash
git add app/audit/run_type.py app/audit/evidence_ref.py tests/test_run_type_and_evidence_ref.py
git commit -m "feat(m2-u1): add run_type and evidence_ref conventions for analysis_run/criterion_score"
```

---

### Task 7: `tests/test_db_m2_schema.py` 收尾——老库升级回归（tasks 2.7 剩余部分）

**为什么单独一个 Task**：Task 1–6 已经把"新库建表齐全"与"各表 CHECK 反证"覆盖完，tasks 2.7 剩下唯一没做的是"老库（复制 `.51` 的 demo.db 结构）升级后既有表一行不改"——这需要一份独立的、硬编码的"升级前"快照（不能从当前 `SCHEMA` 常量派生，理由与 `tests/test_db_migration.py` 顶部注释一致：派生的话测试会随 SCHEMA 一起演进，永远测不出"老库升级不了"这个真正要防的故障）。这份快照体量较大，独立成一个 Task 更适合单独 review。

**Files:**
- Modify: `tests/test_db_m2_schema.py`

**Interfaces:**
- Consumes: `app.storage.db.init_schema`、`app.storage.db.get_connection`（既有）
- Produces: 无新公共接口，纯测试

- [ ] **Step 1: 追加"老库升级"测试**

在 `tests/test_db_m2_schema.py` 顶部 import 区加入 `import json`（若尚未导入），文件末尾追加：

```python

# ── 老库升级：.51 现网 demo.db 在 U1 上线前的真实形态 ─────────────────────
#
# 刻意硬编码而不是从 SCHEMA 裁剪——这段 DDL 代表"U1 上线前，.51 上的库长
# 什么样"，是一个历史事实，不随 SCHEMA 一起演进（与 tests/test_db_migration.py
# 顶部注释同一理由）。内容 = U1 之前已经上线的全部表：job / job_profile /
# conversation / effect_log / outbox / analysis_run / criterion_score /
# pending_approval / human_review / hard_requirement。

_LEGACY_PRE_U1_DDL = """
CREATE TABLE job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE job_profile (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    unspecified_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_productive INTEGER NOT NULL DEFAULT 1,
    turn_started_at TEXT,
    llm_latency_ms REAL,
    derived_unspecified_fields TEXT NOT NULL DEFAULT '[]',
    ungrounded_fields TEXT NOT NULL DEFAULT '[]',
    written_fields TEXT NOT NULL DEFAULT '[]',
    llm_response_model TEXT,
    asked_questions TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE conversation (
    thread_id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_effect_log_key ON effect_log (effect_key);

CREATE TABLE outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE analysis_run (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT,
    job_id TEXT,
    configured_model TEXT NOT NULL,
    response_model TEXT,
    system_fingerprint TEXT,
    prompt_version TEXT NOT NULL,
    temperature REAL NOT NULL,
    input_hash TEXT NOT NULL,
    rubric_snapshot TEXT,
    raw_response TEXT NOT NULL,
    token_usage TEXT,
    latency_ms REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_analysis_run_application ON analysis_run (application_id);

CREATE TABLE criterion_score (
    id TEXT PRIMARY KEY NOT NULL,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    criterion_key TEXT NOT NULL,
    score REAL NOT NULL,
    evidence_ref TEXT NOT NULL CHECK (
        evidence_ref IS NOT NULL
        AND trim(evidence_ref, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_criterion_score_run ON criterion_score (analysis_run_id);

CREATE TABLE pending_approval (
    id TEXT PRIMARY KEY NOT NULL,
    thread_id TEXT NOT NULL,
    message_type TEXT,
    recipient TEXT,
    payload_json TEXT NOT NULL,
    blocked_reason TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'abandoned')),
    confirmed_by TEXT,
    enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE UNIQUE INDEX idx_pending_approval_content
    ON pending_approval (thread_id, content_hash);

CREATE INDEX idx_pending_approval_status ON pending_approval (status);

CREATE TABLE human_review (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    decision_type TEXT NOT NULL
        CHECK (decision_type IN ('approved', 'revision_requested', 'abandoned')),
    reviewer TEXT NOT NULL CHECK (
        reviewer IS NOT NULL
        AND trim(reviewer, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    feedback TEXT,
    batch_id TEXT,
    decided_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX idx_human_review_decision
    ON human_review (job_id, profile_version, decision_type);

CREATE TABLE hard_requirement (
    job_id TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    field TEXT NOT NULL,
    operator TEXT NOT NULL CHECK (
        operator IN ('gte', 'education_gte', 'contains', 'equals', 'is_true')
    ),
    value TEXT NOT NULL,
    blocking INTEGER NOT NULL CHECK (blocking IN (0, 1)),
    human_readable TEXT NOT NULL CHECK (
        human_readable IS NOT NULL
        AND trim(human_readable, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, profile_version, field, operator, value)
);
"""

_LEGACY_TABLES = (
    "job", "job_profile", "conversation", "effect_log", "outbox",
    "analysis_run", "criterion_score", "pending_approval", "human_review",
    "hard_requirement",
)

_M2_U1_NEW_TABLES = (
    "candidate", "resume", "resume_text_span", "application", "stage",
    "application_stage_history", "rejection_record", "resume_access_log",
    "field_review_queue", "screening_flag", "resume_embedding",
    "eval_import_batch", "eval_sample", "eval_annotation", "hr_account",
)


def _legacy_db(tmp_path):
    c = get_connection(str(tmp_path / "legacy_pre_u1.db"))
    c.executescript(_LEGACY_PRE_U1_DDL)
    c.execute("INSERT INTO job (id, title, status) VALUES ('old-job', '采购工程师', 'approved')")
    c.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('old-job-v1', 'old-job', 1, 'approved', ?)",
        (json.dumps({"job_title": "采购工程师"}, ensure_ascii=False),),
    )
    c.execute(
        "INSERT INTO human_review (id, job_id, profile_version, decision_type, reviewer) "
        "VALUES ('hr-1', 'old-job', 1, 'approved', 'someone')"
    )
    c.commit()
    return c


def test_legacy_pre_u1_db_gains_all_new_tables_after_init_schema(tmp_path):
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    for table in _M2_U1_NEW_TABLES:
        assert _table_exists(conn, table), f"{table} 应该在 init_schema 后出现"


def test_legacy_pre_u1_db_existing_tables_and_rows_are_untouched(tmp_path):
    conn = _legacy_db(tmp_path)
    before_columns = {t: _columns(conn, t) for t in _LEGACY_TABLES}
    before_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in _LEGACY_TABLES
    }

    init_schema(conn)

    after_columns = {t: _columns(conn, t) for t in _LEGACY_TABLES}
    after_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in _LEGACY_TABLES
    }
    assert after_columns == before_columns
    assert after_counts == before_counts


def test_added_columns_tuple_still_only_touches_job_profile():
    """reviewer 机械判据：本单元 diff 不得往 _ADDED_COLUMNS 里塞新表——
    新表一律走 CREATE TABLE IF NOT EXISTS。"""
    from app.storage.db import _ADDED_COLUMNS

    tables_in_added_columns = {row[0] for row in _ADDED_COLUMNS}
    assert tables_in_added_columns == {"job_profile"}


def test_fresh_and_legacy_upgraded_schemas_have_identical_m2_u1_tables(tmp_path):
    """新库直接 init_schema() 与老库升级后，M2 U1 新表的列集合必须完全一致——
    两条路径不能产生两种不同形状的表。"""
    fresh = get_connection(str(tmp_path / "fresh.db"))
    init_schema(fresh)

    legacy = _legacy_db(tmp_path)
    init_schema(legacy)

    for table in _M2_U1_NEW_TABLES:
        assert _columns(fresh, table) == _columns(legacy, table), table
```

- [ ] **Step 2: 跑测试确认通过（新写测试不依赖尚未实现的代码，应直接通过；若失败说明 Task 1–6 的表结构有遗漏，回头核对）**

Run: `./venv/bin/pytest tests/test_db_m2_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 用 `grep` 自查覆盖面**

```bash
grep -c "^def test_" tests/test_db_m2_schema.py
```

Expected: 数字 ≥ 40（Task 1–7 累计新增的测试函数数）

- [ ] **Step 4: 全量回归**

Run: `./venv/bin/pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add tests/test_db_m2_schema.py
git commit -m "test(m2-u1): legacy db upgrade regression for U1 tables"
```

---

### Task 8: `scripts/create_hr_account.py`（tasks 2.8）

**Files:**
- Create: `app/storage/hr_account.py`
- Create: `scripts/create_hr_account.py`
- Test: `tests/test_hr_account.py`

**Interfaces:**
- Consumes: 表 `hr_account`（Task 5）、`app.storage.db.get_connection` / `init_schema`（既有）
- Produces: `app.storage.hr_account.hash_password(password: str, salt: str | None = None) -> tuple[str, str]`（返回 `(password_hash, salt)`，`salt=None` 时内部生成新盐）；`app.storage.hr_account.verify_password(password: str, password_hash: str, salt: str) -> bool`；`app.storage.hr_account.upsert_account(conn, *, username: str, password: str) -> str`（返回账号 `id`；同用户名已存在则只更新口令，返回既有 `id`）。U2 tasks 3.2（登录中间件）会直接调用 `verify_password`，签名以此为准。

- [ ] **Step 1: 写失败测试**

```python
"""hr_account 的口令哈希与幂等建账号（tasks 2.8）。

密码用 PBKDF2-HMAC-SHA256 加盐哈希，标准库实现，⛔ 不引入 bcrypt/argon2 等
新依赖——.51 是 Windows 无 Docker 环境，新依赖必须先冒烟（design.md「外部依赖
现状」），登录这种非评测热路径的功能没有必要为此扩大依赖面。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.hr_account import hash_password, upsert_account, verify_password


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "acct.db"))
    init_schema(c)
    return c


def test_hash_password_generates_new_salt_when_not_given():
    h1, s1 = hash_password("correct horse battery staple")
    h2, s2 = hash_password("correct horse battery staple")
    assert s1 != s2, "两次不传 salt 应该生成不同的随机盐"
    assert h1 != h2, "不同盐下同一口令的哈希必须不同"


def test_hash_password_deterministic_given_same_salt():
    h1, salt = hash_password("hunter2")
    h2, _ = hash_password("hunter2", salt=salt)
    assert h1 == h2


def test_verify_password_accepts_correct_password():
    password_hash, salt = hash_password("hunter2")
    assert verify_password("hunter2", password_hash, salt) is True


def test_verify_password_rejects_wrong_password():
    password_hash, salt = hash_password("hunter2")
    assert verify_password("wrong-password", password_hash, salt) is False


def test_upsert_account_creates_new_account(conn):
    account_id = upsert_account(conn, username="tangliping", password="initial-pw")

    row = conn.execute(
        "SELECT id, username, password_hash, password_salt FROM hr_account WHERE username='tangliping'"
    ).fetchone()
    assert row[0] == account_id
    assert verify_password("initial-pw", row[2], row[3]) is True


def test_upsert_account_is_idempotent_on_username_and_only_updates_password(conn):
    first_id = upsert_account(conn, username="tangliping", password="pw-1")
    second_id = upsert_account(conn, username="tangliping", password="pw-2")

    assert first_id == second_id
    count = conn.execute(
        "SELECT COUNT(*) FROM hr_account WHERE username='tangliping'"
    ).fetchone()[0]
    assert count == 1

    row = conn.execute(
        "SELECT password_hash, password_salt FROM hr_account WHERE id=?", (first_id,)
    ).fetchone()
    assert verify_password("pw-2", row[0], row[1]) is True
    assert verify_password("pw-1", row[0], row[1]) is False


def test_upsert_account_rejects_blank_username(conn):
    with pytest.raises(ValueError, match="用户名"):
        upsert_account(conn, username="  ", password="pw")


def test_upsert_account_rejects_blank_password(conn):
    with pytest.raises(ValueError, match="口令"):
        upsert_account(conn, username="someone", password="")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/pytest tests/test_hr_account.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.storage.hr_account'`）

- [ ] **Step 3: 实现 `app/storage/hr_account.py`**

```python
"""hr_account 口令哈希与幂等建账号（design D12：鉴权从空壳换成本地账号）。

PBKDF2-HMAC-SHA256，200,000 次迭代（OWASP 2023 推荐下限），标准库实现——
⛔ 不引入 bcrypt/argon2：.51 是 Windows 无 Docker 环境，新依赖必须先在
Windows 上冒烟（design.md「外部依赖现状」），登录这种非评测热路径的功能
没有必要为此扩大依赖面。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt=salt)
    return hmac.compare_digest(candidate, password_hash)


def upsert_account(conn: sqlite3.Connection, *, username: str, password: str) -> str:
    """幂等建账号：同用户名重复调用只更新口令，返回既有 id；用户名不存在则新建。

    ⛔ 不做任何 LangGraph 幂等键接入——这是运维脚本的一次性写入，不经
    effect_* 节点、不在 checkpointer 恢复路径上（工程铁律 1 的适用范围是
    图节点的副作用，不是运维 CLI）。"""
    username = username.strip()
    if not username:
        raise ValueError("用户名不能为空")
    if not password:
        raise ValueError("口令不能为空")

    existing = conn.execute(
        "SELECT id FROM hr_account WHERE username = ?", (username,)
    ).fetchone()
    password_hash, salt = hash_password(password)

    if existing:
        account_id = existing[0]
        conn.execute(
            "UPDATE hr_account SET password_hash = ?, password_salt = ? WHERE id = ?",
            (password_hash, salt, account_id),
        )
    else:
        account_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO hr_account (id, username, password_hash, password_salt) "
            "VALUES (?, ?, ?, ?)",
            (account_id, username, password_hash, salt),
        )
    conn.commit()
    return account_id
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/pytest tests/test_hr_account.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 写 `scripts/create_hr_account.py`**

```python
# scripts/create_hr_account.py
"""建 / 更新一个 HR 本地账号（tasks 2.8）。

幂等：同用户名重复运行只更新口令并提示，不产生第二条账号记录。

用法：
    python -m scripts.create_hr_account --username tangliping
    python -m scripts.create_hr_account --username tangliping --password 'xxx'  # 非交互场景

不传 --password 时用 getpass 交互输入，避免口令出现在 shell 历史与进程列表里。
"""
from __future__ import annotations

import argparse
import getpass

from app.config import get_settings
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--password",
        default=None,
        help="不传则交互输入（推荐）；仅供非交互脚本化场景使用",
    )
    parser.add_argument("--db-path", default=None, help="默认读 Settings.db_path")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"为 {args.username} 设置口令: ")

    db_path = args.db_path or get_settings().db_path
    conn = get_connection(db_path)
    init_schema(conn)

    existing = conn.execute(
        "SELECT 1 FROM hr_account WHERE username = ?", (args.username,)
    ).fetchone()

    account_id = upsert_account(conn, username=args.username, password=password)

    if existing:
        print(f"已更新账号 {args.username}（id={account_id}）的口令。")
    else:
        print(f"已创建账号 {args.username}（id={account_id}）。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 手工冒烟一次（幂等性）**

```bash
./venv/bin/python -m scripts.create_hr_account --username smoke-test --password pw-1 --db-path /tmp/smoke-hr.db
./venv/bin/python -m scripts.create_hr_account --username smoke-test --password pw-2 --db-path /tmp/smoke-hr.db
./venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/tmp/smoke-hr.db')
rows = c.execute(\"SELECT COUNT(*) FROM hr_account WHERE username='smoke-test'\").fetchone()
print('账号行数:', rows[0])
assert rows[0] == 1
print('OK：幂等——第二次调用只更新口令，没有产生第二条账号')
"
rm -f /tmp/smoke-hr.db
```

Expected: 输出 `账号行数: 1` 与 `OK：幂等...`

- [ ] **Step 7: 全量回归**

Run: `./venv/bin/pytest -q`
Expected: 全绿

- [ ] **Step 8: Commit**

```bash
git add app/storage/hr_account.py scripts/create_hr_account.py tests/test_hr_account.py
git commit -m "feat(m2-u1): add hr_account password hashing and create_hr_account script"
```

---

## 端到端提取验证（执行完 8 个 Task 后，run-build 收尾前再跑一次）

1. 确认全量测试绿：`./venv/bin/pytest -q`（不含 `-k`，跑全部文件）
2. 确认 `_ADDED_COLUMNS` 未被本单元触碰：`git diff main -- app/storage/db.py | grep -A2 "_ADDED_COLUMNS: tuple"` 应无输出（该元组定义行本身不在 diff 里，或即便在 diff 里，取值仍是 `{("job_profile", ...)}` 那一组，一行没多）
3. 确认无新增 `ALTER TABLE`：`git diff main -- app/storage/db.py | grep "ALTER TABLE"` 应无输出
4. 确认无新依赖：`git diff main -- requirements.txt` 应无输出
5. 确认 `.51` 迁移路径的核心不变式（Task 7 已覆盖，这里是收尾时的人工复核）：`test_legacy_pre_u1_db_existing_tables_and_rows_are_untouched` 与 `test_added_columns_tuple_still_only_touches_job_profile` 两条必须在测试报告里出现且为 PASS

## Self-Review 记录（写计划时已过一遍，供执行者复核）

- **Spec 覆盖**：resume-upload-and-gate 的「批量上传入口」「真实简历入库闸」「简历访问留痕」「可识别到人的登录」四个 Requirement 对应的表（resume、resume_access_log、hr_account）均有 Task 覆盖，闸的求值逻辑与登录中间件本身不在 U1 范围（U2 tasks 3.1/3.2）；resume-parsing 的「首期字段抽取」「原文分片与字段回指」「字段级置信度与人工校对队列」「解析留痕与版本」「扫描件与不可读文件」对应 resume/resume_text_span/field_review_queue 与 resume.status；hard-requirement-screening 的「逐条判定只标记不淘汰」「每条 fail 标记带原文依据」「低置信度字段跳过判定」「淘汰只由人确认并可申诉」「规则版本随画像冻结版本」对应 screening_flag/rejection_record/application_stage_history；candidate-ranking 的「逐维评分带证据回指」对应 evidence_ref 约定（criterion_score 表结构本身 M1 已建，未改动）；m2-compliance-assertions 的「拒绝记录表缺失即失败」「评分 100% 可回溯」对应 Task 3 的断言翻转（「评分 100% 可回溯」断言本身 U1 前已存在，不在本单元改动范围，仅确认其依赖的 criterion_score 结构未变）；eval-set-and-metrics 的「评测集样本来源与访问控制」「判例批改表格式与导入校验」对应 eval_sample/eval_annotation/eval_import_batch。
- **占位符扫描**：全文无 TBD/TODO，每个 Task 的每个 Step 都是可直接运行的代码或命令。
- **类型一致性**：`upsert_account` 在 Task 8 定义的签名 `(conn, *, username, password) -> str` 与测试文件调用一致；`run_type_of` / `format_evidence_ref` / `parse_evidence_ref` 在 Task 6 定义后测试文件调用签名一致；各表列名在 Task 1–5 之间没有前后不一致（`application.resume_id`、`resume.id` 等在跨 Task 引用时均核对过拼写）。
