# U5 post 转写对齐 ＋ rubric 评分 ＋ ScoreCard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 面试场次完成后，把 `interview_turn` 转写整理为可定位证据单元，按冻结 rubric 逐维评分（每条评分项强制带 turn 回指），派生要点提示与声学参考展示字段，把结果幂等落库为 ScoreCard，并提供批处理入口——全程只写参考性数据，不触发阶段流转或淘汰。

**Architecture:** L3 Agent（`app/agents/interview_scoring.py::score()`）只调 LLM 网关，纯函数无副作用；L4 编排层（`app/graph/interview_scoring_nodes.py`）的 `compute_align`/`compute_score` 只读查库组装输入，`effect_write_acoustic_refs`/`effect_persist_scorecard`/`effect_mark_scoring_failed` 用 `@idempotent_effect` 装饰器独占写库；`run_post_scoring()` 用普通 Python 函数把上述节点串成 `compute_align → compute_score → (evidence 反查校正) → effect_persist_scorecard`（失败分支转 `effect_mark_scoring_failed`），不建真实编译的 LangGraph StateGraph（既有判例，见下文「设计决策 7」）；`scripts/run_interview_post_scoring.py` 是给 `.51` 计划任务调用的批处理入口，扫描 `status='completed'` 且未成功评分的场次逐条调用编排函数。

**Tech Stack:** Python、pytest、SQLite（`app/storage/db.py`）、LLM 调用经 `app/llm/gateway.py::LLMGateway`（不直接调 OpenAI SDK，不新增网关逻辑，不涉及 LangGraph 编译图）。

**Spec:** `openspec/changes/voice-structured-interview/specs/interview-scorecard/spec.md`（本计划只实现该 spec 中「转写按 turn 对齐」「逐维评分带 turn 回指」「ScoreCard 与要点提示只作参考」「声学信号只展示不计分」四条 Requirement；「面试官视图与回放」「HR 进度与完成率视图」「一致性评估的数据导出」三条属 U6/第 7 章，不在本计划内，但本计划的落库形态必须让 U6 直接消费）。对应 `openspec/changes/voice-structured-interview/tasks.md` 第 6 章 6.1–6.8。设计依据 `openspec/changes/voice-structured-interview/design.md` 决策 D2/D3/D4/D20。

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」与「合规红线」两节逐字复制，每条工程铁律与合规红线相关条目都出现，不适用的写明理由，不省略。

**工程铁律：**

1. LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。**（适用）** `effect_write_acoustic_refs`/`effect_persist_scorecard`/`effect_mark_scoring_failed` 全部用 `@idempotent_effect` 装饰，幂等键字面匹配 `{session_id}:effect_persist_scorecard:{analysis_run_id}` 等，见 Task 7/8。
2. L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。**（适用）** `app/agents/interview_scoring.py::score()` 是 L3，只调网关，不 `import app.storage`；`app/graph/interview_scoring_nodes.py` 里 `compute_align`/`compute_score` 只读不写，`effect_*` 前缀函数才写。
3. 所有 AI 评分必须持久化：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。**（适用）** 沿用 `LLMGateway.extract_structured_with_meta` 既有机制，本单元只需正确传 `prompt_version="interview-score-v1"` 与 `audit_context`。
4. 每条 `criterion_score` 必须有 `evidence_ref`（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。**（适用）** 本单元核心约束：反查失败或证据指向不存在的 turn ⇒ 整次评分不写任何 `criterion_score`，见 Task 6。
5. `temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。供应商不提供带版本号快照时，必须从 API 响应里取回实际的 `model` 字段并持久化。**（适用）** `LLMGateway.TEMPERATURE` 已是常量 0，本单元不新增供应商配置，沿用既有网关。
6. 企微回调先落库再处理：只推一次、5 秒无响应即丢弃。**（不适用）** 本单元不涉及企微回调，无外发动作。
7. `langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。**（不适用于本单元的具体改动）** 本单元不新增或改动 `requirements.txt` 里的 `langgraph` 版本，也不建真实 StateGraph（见设计决策 7）；项目全局约束仍然有效，reviewer 需确认本单元没有引入更低版本的间接依赖。

**合规红线：**

- AI 只做排序推荐，不做自动淘汰。淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。**（适用）** `effect_persist_scorecard` 只写 `analysis_run`/`criterion_score`/`interview_scorecard`/`interview_scorecard_tip`，不碰 `application.current_stage_id`、不写 `rejection_record`；`rejection_record.reason_type` 的 CHECK 本来就只允许 `hard_rule`/`human_decision`，`ai_score` 不是合法取值（存储层已经堵死）。Task 9 的 e2e 断言 `rejection_record`/`application.current_stage_id` 前后不变。
- 禁止人脸/表情分析（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。**（适用）** `acoustic_ref` 只写 `interview_turn` 展示列，`ScoreInput`/`ScoreInputTurn`（U1 已交付）结构上不含该字段；本单元不涉及人脸/表情识别，无相关代码路径。
- AI 生成的 JD、拒信、邀约须带标识（《AI 生成合成内容标识办法》2025-09-01 施行）。**（部分不适用）** 本单元不产出任何对外发送的文本，也不做 UI 页面——"AI 生成标识"的展示义务落在 U6 的 ScoreCard 页面上；本单元只需保证 `interview_scorecard`/`interview_scorecard_tip` 的数据形态足够让 U6 页面展示这个标识，不需要自己渲染。
- 模型全部走境内，简历数据不出境。**（适用）** 沿用既有网关配置，本单元不引入新的模型供应商。
- 绝不用历史录用结果做监督信号（Amazon 2018 教训），只用显式岗位能力 rubric。**（适用）** 评分输入只来自冻结 `prep_snapshot`/`prep_question` 的 rubric 维度，不读取任何历史录用结果。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。**（不适用）** 本单元不新增候选人入口，邀约链接是 U3 已交付范围。
- 主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。**（不适用）** 本单元的评分与要点提示都是参考性展示，不驱动任何硬门槛/自动流转规则。

---

## Spec Requirement → Task 映射

| Spec Requirement | 覆盖 Task |
|---|---|
| 转写按 turn 对齐（含「常规对齐」「低置信度转写」两个 Scenario） | Task 4（`compute_align`）、Task 7（`acoustic_ref`/低置信度标记消费） |
| 逐维评分带 turn 回指（含「一个场次的评分」「模型未给出证据」「回指校正」三个 Scenario） | Task 2（评分输出 schema）、Task 3（`score()` L3 Agent）、Task 5（`compute_score`）、Task 6（证据反查校正） |
| ScoreCard 与要点提示只作参考（含「低分场次」Scenario） | Task 1（`interview_scorecard`/`interview_scorecard_tip` 建表）、Task 7（要点提示派生）、Task 8（`effect_persist_scorecard`）、Task 9（e2e 断言不触发流转/淘汰） |
| 声学信号只展示不计分（含「面试官查看声学参考」Scenario） | Task 7（`compute_acoustic_ref`/`effect_write_acoustic_refs`）、既有 `ScoreInputTurn` 反射测试（U1 已交付，Task 5 不得破坏） |

## 本计划补的技术决策（design.md/tasks.md 字面未写全，裁定如下，不留 TBD）

1. **ScoreCard 总体摘要与要点提示的存储位置**：D4 只列了复用审计三件套，没给"总体摘要""要点提示"的表。裁定新增两张表（`CREATE TABLE IF NOT EXISTS`，不进 `_ADDED_COLUMNS`）：`interview_scorecard`（一个场次一份定稿，`UNIQUE(session_id)`，失败重试不写这张表）、`interview_scorecard_tip`（每条指向具体维度与 turn）。是 U6 ScoreCard 页要读的数据源，本计划只建表写入，不做页面。
2. **`interview_session` 新增 `post_scoring_status`/`post_scored_at` 两列**（走 `_ADDED_COLUMNS`）：取值 `pending`/`failed_retry`/`scored`（应用层校验，不加 SQL CHECK——`_ADDED_COLUMNS` 现有先例都不带 CHECK）。批处理按 `status='completed' AND post_scoring_status IN ('pending','failed_retry')` 选取待处理场次，是"重试不重复落分"的关键机制；`run_post_scoring()` 自身在入口再查一次该状态并对 `scored` 短路返回（Task 8），双重保险——不能只靠批处理的 `WHERE` 过滤，`interview_scorecard` 的 `UNIQUE(session_id)` 决定了对已评分场次重复跑必须在业务逻辑层面短路，而不是指望撞上唯一键再处理异常。
3. **失败标注走独立的 `effect_mark_scoring_failed` 节点**：`business_key` 用调用方传入的一次性 uuid（每次失败尝试都要能落一条 `effect_log`，语义类比 `interview_invite_event` 的可重复事件，不是"只能发生一次"）。与 D20 只列 `effect_persist_scorecard` 一个节点不矛盾——D20 列的是成功路径，失败路径的旁支效果节点参考 `app/graph/invite_nodes.py::effect_log_invite_access_denied` 先例。
4. **声学参考的计算口径是启发式近似，非精确 VAD**：`interview_turn` 只有 turn 级起止毫秒、没有逐词时间戳，无法做真正的停顿检测。裁定：`speech_rate_cpm = 字符数 / (音频时长_ms / 60000)`；`expected_speaking_ms = 字符数 / 4.5(字/秒，普通话正常语速经验值) * 1000`；`pause_ratio = clamp((音频时长_ms - expected_speaking_ms) / 音频时长_ms, 0, 1)`；`silence_ratio` 本层数据粒度无法与 `pause_ratio` 区分，取同值；JSON 里带 `note` 字段说明这是"turn 级近似估算，非逐词 VAD"。音频起止任一为空（文本作答 turn）⇒ `acoustic_ref` 整体为 `NULL`。
5. **要点提示派生规则**：低分维度（`score < job_prep_config.low_score_threshold`，新增列，走 `_ADDED_COLUMNS`，默认 2.0，同一 0.0–5.0 量表下的"不及格"经验分界）⇒ 生成一条指向该维度证据 turn 的 tip；低置信度语音 turn（6.1 标记）⇒ 生成一条指向该 turn 的 tip（维度取该 turn 若恰好是某维度证据来源时的维度，否则退回"第 N 题"标注）；同一 `(dimension, turn_id)` 去重，低分文案优先。
6. **6.8 e2e 的"场次到 completed"是测试专用 fixture，不新增生产端点**：候选人文本作答提交端点是 U4 第 5 章 5.7/5.8 范围，晚于本单元交付。测试直接用 sqlite3 连接 INSERT `interview_turn` 行并 UPDATE `interview_session.status='completed'`，测试文件顶部注释写明这是绕过尚未交付端点的专用 fixture。
7. **不建真实 LangGraph StateGraph**：`compute_align`/`compute_score`/`effect_persist_scorecard`/`effect_mark_scoring_failed` 用普通 Python 函数 `run_post_scoring()` 串联，依据 `app/graph/interview_prep_nodes.py` 顶部 docstring 记录的既有判例（2026-08-26，Shao Peishen 判定"行为等价"）。
8. **批处理脚本**：`scripts/run_interview_post_scoring.py` 扫描待处理场次逐条调用，单条失败不中断整批（`try/except` 包住每条）。Windows 计划任务的实际安装是 tasks.md 8.6 范围，本单元只交付脚本本身。

---

### Task 1: 数据库 schema——新表与新列

**Files:**
- Modify: `app/storage/db.py`（在 `SCHEMA` 字符串里 `interview_invite_event` 建表块之后、闭合 `"""` 之前追加两张新表；在 `_ADDED_COLUMNS` 元组末尾追加四条新列）
- Test: `tests/test_db_m3_schema.py`（追加）
- Test: `tests/test_db_migration.py`（追加，验证新列走加列路径且漂移守卫不报警）

**Interfaces:**
- Produces：新表 `interview_scorecard(id, session_id, analysis_run_id, summary, created_at)`、`interview_scorecard_tip(id, scorecard_id, dimension, turn_id, tip_text, seq)`；`job_prep_config` 新列 `low_confidence_threshold REAL`、`low_score_threshold REAL`；`interview_session` 新列 `post_scoring_status TEXT`、`post_scored_at TEXT`。后续 Task 4/5/7/8 直接按这些列名读写。

- [ ] **Step 1: 在 `app/storage/db.py` 的 `SCHEMA` 字符串里追加两张新表**

在 `app/storage/db.py` 第 866-868 行（`interview_invite_event` 的索引语句之后、闭合 `"""` 之前）插入：

```sql

-- ScoreCard 总体摘要与要点提示（voice-structured-interview U5 tasks 6.6/6.4；
-- design D4 未列出这两张表，是本单元对"总体摘要"与"要点提示"存储位置的补充
-- 设计决策——见 docs/superpowers/plans/2026-09-19-u5-post-scoring.md「设计决策 1」）。
-- UNIQUE(session_id)：一个场次只有一份定稿 ScoreCard；失败重试不写这张表，
-- 只有整次评分判定可用（全部维度都有合法证据）才写一次。
CREATE TABLE IF NOT EXISTS interview_scorecard (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES interview_session(id),
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_interview_scorecard_session
    ON interview_scorecard (session_id);

-- 要点提示（interview-scorecard spec「ScoreCard 与要点提示只作参考」：每条
-- MUST 指向具体维度与 turn）。turn_id 建外键——要点提示离开了它指向的 turn
-- 就没有意义，不像 interview_access_log 那样需要"留痕独立于内容表可写性"。
CREATE TABLE IF NOT EXISTS interview_scorecard_tip (
    id TEXT PRIMARY KEY NOT NULL,
    scorecard_id TEXT NOT NULL REFERENCES interview_scorecard(id),
    dimension TEXT NOT NULL,
    turn_id TEXT NOT NULL REFERENCES interview_turn(id),
    tip_text TEXT NOT NULL,
    seq INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_scorecard_tip_scorecard
    ON interview_scorecard_tip (scorecard_id);
```

- [ ] **Step 2: 在 `_ADDED_COLUMNS` 元组末尾追加四条新列**

在 `app/storage/db.py` 第 907-912 行区（`_ADDED_COLUMNS` 元组内 `interview_session` 现有两条之后、闭合 `)` 之前）追加：

```python
    # voice-structured-interview U5 tasks 6.1/6.4：低置信度转写阈值、要点
    # 提示低分阈值，均为岗位级配置。job_prep_config 在 U2 建表，CREATE TABLE
    # IF NOT EXISTS 对已存在的表无效，必须走加列迁移（与 invite_expiry_days
    # 同一先例）。
    ("job_prep_config", "low_confidence_threshold", "REAL NOT NULL DEFAULT 0.6"),
    ("job_prep_config", "low_score_threshold", "REAL NOT NULL DEFAULT 2.0"),
    # U5 tasks 6.6/6.7：post 评分状态机，批处理靠它判断该场次是否需要（重新）
    # 评分。interview_session 在 M3 U1 建表，同样走加列迁移。
    ("interview_session", "post_scoring_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("interview_session", "post_scored_at", "TEXT"),
```

- [ ] **Step 3: 写新表结构测试**

在 `tests/test_db_m3_schema.py` 末尾追加：

```python


# ── interview_scorecard / interview_scorecard_tip（U5 tasks 6.6）────────


def test_interview_scorecard_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_scorecard")
    assert _columns(conn, "interview_scorecard") == {
        "id", "session_id", "analysis_run_id", "summary", "created_at",
    }


def test_interview_scorecard_unique_on_session(conn):
    _seed_job_candidate_resume_application(conn)
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class) "
        "VALUES ('sess1', 'app1', 1, '2099-01-01', 'v1', 'internal_sim')"
    )
    _seed_analysis_run(conn)
    conn.commit()
    conn.execute(
        "INSERT INTO interview_scorecard (id, session_id, analysis_run_id, summary) "
        "VALUES ('sc-1', 'sess1', 'run1', '总体表现良好')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_scorecard (id, session_id, analysis_run_id, summary) "
            "VALUES ('sc-2', 'sess1', 'run1', '重复场次')"
        )


def test_interview_scorecard_tip_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_scorecard_tip")
    assert _columns(conn, "interview_scorecard_tip") == {
        "id", "scorecard_id", "dimension", "turn_id", "tip_text", "seq",
    }


# ── job_prep_config / interview_session 新列（U5 tasks 6.1/6.4/6.6）──────


def test_job_prep_config_has_scoring_threshold_columns(conn):
    assert {"low_confidence_threshold", "low_score_threshold"} <= _columns(conn, "job_prep_config")


def test_interview_session_has_post_scoring_status_columns(conn):
    assert {"post_scoring_status", "post_scored_at"} <= _columns(conn, "interview_session")
```

- [ ] **Step 4: 修复既有的 `interview_session` 列集合硬编码测试**

`tests/test_db_m3_schema.py` 里已有一条从 M3 U1 就存在的测试
`test_interview_session_table_exists_with_expected_columns`，用 `==` 硬编码
了 `interview_session` 的完整列集合。Step 2 给这张表加了两列后，这条测试会
因为集合不相等而失败——**这不是新写的测试，是需要同步更新的既有测试**，找到
它（大约在文件中段，紧邻 `test_interview_session_retention_until_cannot_be_
null` 之前）并把新列并进断言里：

```python
def test_interview_session_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_session")
    assert _columns(conn, "interview_session") == {
        "id", "application_id", "prep_snapshot_version", "invite_token_hash",
        "invite_expires_at", "resume_token_hash", "phone_verified_at",
        "phone_attempts", "recording_uri", "retention_until",
        "retention_policy_version", "sample_class", "status", "created_at",
        "phone_code_hash", "phone_code_expires_at",
        "post_scoring_status", "post_scored_at",
    }
```

（原集合只多了 `"post_scoring_status", "post_scored_at"` 两项，其余逐字不变。）

- [ ] **Step 5: 跑 `test_db_m3_schema.py` 全量确认通过**

Run: `pytest tests/test_db_m3_schema.py -v`
Expected: 全部 PASS——不只是本任务新增的用例，Step 4 修的既有用例也必须一起
绿，⛔ 不要只用 `-k` 过滤跑新增用例，那样会让 Step 4 要修的问题继续潜伏到
后面某个任务才暴露。

- [ ] **Step 6: 更新迁移漂移守卫测试的说明性注释**

在 `tests/test_db_migration.py` 第 386-389 行区的 `test_audit_tables_never_enter_the_add_column_path` docstring 末尾（`assert` 语句之前）追加一段说明（**不改 `assert` 本身**——`job_prep_config`/`interview_session` 已经在这个集合里，本次只是给两张表各加一列，不引入新表）：

```python
    U5 task 1 继续往 job_prep_config / interview_session 加列（低置信度阈值、
    低分阈值、post 评分状态机），两张表都已在这个集合里，assert 本身不变。
    """
```

- [ ] **Step 7: 跑漂移守卫与老库迁移测试全套**

Run: `pytest tests/test_db_migration.py -v`
Expected: 全部 PASS，包括 `test_fresh_and_migrated_schemas_have_identical_columns`（新库 `CREATE TABLE` 与老库 `_ADDED_COLUMNS` 迁移后列集合一致）与 `test_audit_tables_never_enter_the_add_column_path`（表集合断言不变，因为没引入新表进 `_ADDED_COLUMNS`）。

- [ ] **Step 8: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py tests/test_db_migration.py
git commit -m "feat(voice-interview): U5 ScoreCard 表与评分阈值配置列"
```

---

### Task 2: 评分 LLM 输出 schema

**Files:**
- Create: `app/schemas/interview_scoring_result.py`
- Test: `tests/test_interview_scoring_result_schema.py`

**Interfaces:**
- Consumes：无（新文件，只依赖 `pydantic`）
- Produces：`ScoreEvidenceOut(turn_id: str, quote: str, start: int|None, end: int|None)`、`ScoreDimensionOut(dimension: str, score: float, rationale: str, evidence: ScoreEvidenceOut)`、`ScoreCardOut(dimensions: list[ScoreDimensionOut], overall_summary: str)`。Task 3 直接把这个 schema 传给 `LLMGateway.extract_structured_with_meta(schema=ScoreCardOut, ...)`。

- [ ] **Step 1: 写 schema 文件**

```python
"""post 评分（app/agents/interview_scoring.py::score，U5 tasks 6.2）的 LLM
输出 schema。

interview-scorecard spec「逐维评分带 turn 回指」：每条评分项 MUST 携带非空
的证据回指（turn 标识＋起止偏移＋原话摘录）。start/end 允许 None——由
app/graph/interview_scoring_nodes.py 的证据反查校正（tasks 6.3）用 quote 在
turn 原文里反查填充/校正，模型给的 start/end 不直接采信（design D4「回指校正」
Scenario：模型给出的偏移与原话摘录不一致时以反查为准）。

维度白名单校验（是否在冻结快照的 rubric 维度集合内）不在这个 schema 里做——
白名单是运行时才知道的动态集合（每个场次的 prep_snapshot 不同），不能写成
Pydantic 的静态 Literal，校验放在 app/agents/interview_scoring.py::score()
里做（与 app/agents/interview_prep.py 的「越界丢弃」同一处理位置）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ScoreEvidenceOut(BaseModel):
    turn_id: str
    quote: str = Field(min_length=1)
    start: int | None = None
    end: int | None = None


class ScoreDimensionOut(BaseModel):
    dimension: str
    score: float = Field(ge=0.0, le=5.0)
    rationale: str = ""
    evidence: ScoreEvidenceOut


class ScoreCardOut(BaseModel):
    dimensions: list[ScoreDimensionOut] = Field(min_length=1)
    overall_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _dimensions_unique(self):
        keys = [d.dimension for d in self.dimensions]
        if len(set(keys)) != len(keys):
            raise ValueError(f"评分维度重复: {keys}")
        return self
```

- [ ] **Step 2: 写 schema 测试**

```python
"""app/schemas/interview_scoring_result.py 的 schema 校验测试。"""
import pytest
from pydantic import ValidationError

from app.schemas.interview_scoring_result import ScoreCardOut, ScoreDimensionOut, ScoreEvidenceOut


def _dim(dimension="AUTOSAR CP", score=4.0, turn_id="t1", quote="做过三年"):
    return {
        "dimension": dimension, "score": score, "rationale": "回答扎实",
        "evidence": {"turn_id": turn_id, "quote": quote},
    }


def test_score_card_out_accepts_well_formed_payload():
    card = ScoreCardOut(dimensions=[_dim()], overall_summary="整体表现良好")
    assert card.dimensions[0].evidence.turn_id == "t1"
    assert card.dimensions[0].evidence.start is None


def test_score_card_out_rejects_duplicate_dimensions():
    with pytest.raises(ValidationError, match="评分维度重复"):
        ScoreCardOut(dimensions=[_dim(), _dim()], overall_summary="x")


def test_score_card_out_rejects_score_out_of_range():
    with pytest.raises(ValidationError):
        ScoreCardOut(dimensions=[_dim(score=5.5)], overall_summary="x")


def test_score_evidence_out_rejects_empty_quote():
    with pytest.raises(ValidationError):
        ScoreEvidenceOut(turn_id="t1", quote="")


def test_score_dimension_out_accepts_explicit_offsets():
    dim = ScoreDimensionOut(**_dim())
    assert dim.evidence.start is None and dim.evidence.end is None
```

- [ ] **Step 3: 跑测试**

Run: `pytest tests/test_interview_scoring_result_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add app/schemas/interview_scoring_result.py tests/test_interview_scoring_result_schema.py
git commit -m "feat(voice-interview): U5 评分 LLM 输出 schema"
```

---

### Task 3: L3 评分 Agent `score()`

**Files:**
- Create: `app/agents/interview_scoring.py`
- Test: `tests/test_interview_scoring_agent.py`

**Interfaces:**
- Consumes：`app.schemas.interview_ai_input.ScoreInput`（U1 已交付，不改）；`app.schemas.interview_scoring_result.ScoreCardOut`（Task 2）；`app.llm.gateway.LLMGateway`
- Produces：`ScoredDimensionDraft(dimension: str, score: float, rationale: str, turn_id: str, quote: str)`（dataclass）、`ScoreCardDraft(dimensions: list[ScoredDimensionDraft], overall_summary: str, dropped_count: int, run_id: str, response_model: str|None)`（dataclass）、`score(gateway, score_input: ScoreInput, *, max_retries: int = 2, audit_context: dict|None = None) -> ScoreCardDraft`、`ScoringGenerationFailed`（异常）、`SCORE_PROMPT_VERSION = "interview-score-v1"`。Task 5 的 `compute_score` 直接调用 `score()`；Task 6 的证据反查校正直接消费 `ScoreCardDraft.dimensions`。

- [ ] **Step 1: 写失败测试（全部维度越界丢弃后判失败）**

```python
"""app/agents/interview_scoring.py 的纯函数测试：不接触数据库，LLM 用脚本化
假客户端（形状与 tests/test_interview_prep_agent.py 一致，本文件不依赖它，
保持独立）。"""
import json

import pytest

from app.agents.interview_scoring import (
    SCORE_PROMPT_VERSION,
    ScoreCardDraft,
    ScoringGenerationFailed,
    score,
)
from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import ScoreInput, ScoreInputTurn


class ScriptedClient:
    def __init__(self, bodies: list[str]):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 10

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class RecordingHook:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return f"run-{len(self.calls)}"


def _gateway(bodies):
    return LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient(bodies), audit_hook=RecordingHook(),
    )


def _score_input():
    return ScoreInput(
        rubric_dimensions=["AUTOSAR CP", "沟通表达"],
        turns=[
            ScoreInputTurn(turn_id="t1", seq=1, question_text="讲讲你的 AUTOSAR 项目", answer_text="做过三年分层开发"),
            ScoreInputTurn(turn_id="t2", seq=2, question_text="怎么跟团队协作", answer_text="每周同步进度"),
        ],
    )


def _body(dimension="AUTOSAR CP", turn_id="t1", quote="做过三年分层开发"):
    return json.dumps(
        {
            "dimensions": [
                {
                    "dimension": dimension, "score": 4.0, "rationale": "回答扎实",
                    "evidence": {"turn_id": turn_id, "quote": quote},
                }
            ],
            "overall_summary": "整体表现良好",
        },
        ensure_ascii=False,
    )


def test_score_returns_draft_with_run_id_and_response_model():
    gateway = _gateway([_body()])
    draft = score(gateway, _score_input())
    assert isinstance(draft, ScoreCardDraft)
    assert draft.dimensions[0].dimension == "AUTOSAR CP"
    assert draft.dimensions[0].turn_id == "t1"
    assert draft.dimensions[0].quote == "做过三年分层开发"
    assert draft.overall_summary == "整体表现良好"
    assert draft.run_id == "run-1"
    assert draft.response_model == "deepseek-chat-241226"
    assert draft.dropped_count == 0


def test_score_drops_dimension_outside_whitelist_but_keeps_valid_ones():
    body = json.dumps(
        {
            "dimensions": [
                {"dimension": "AUTOSAR CP", "score": 4.0, "rationale": "r",
                 "evidence": {"turn_id": "t1", "quote": "做过三年分层开发"}},
                {"dimension": "薪资期望", "score": 3.0, "rationale": "r",
                 "evidence": {"turn_id": "t2", "quote": "每周同步进度"}},
            ],
            "overall_summary": "s",
        },
        ensure_ascii=False,
    )
    gateway = _gateway([body])
    draft = score(gateway, _score_input())
    assert [d.dimension for d in draft.dimensions] == ["AUTOSAR CP"]
    assert draft.dropped_count == 1


def test_score_raises_after_max_retries_when_all_dimensions_out_of_whitelist():
    bad_body = json.dumps(
        {
            "dimensions": [{"dimension": "薪资期望", "score": 3.0, "rationale": "r",
                             "evidence": {"turn_id": "t1", "quote": "x"}}],
            "overall_summary": "s",
        },
        ensure_ascii=False,
    )
    gateway = _gateway([bad_body, bad_body])
    with pytest.raises(ScoringGenerationFailed):
        score(gateway, _score_input(), max_retries=2)


def test_score_uses_score_prompt_version_constant():
    hook = RecordingHook()
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_body()]), audit_hook=hook,
    )
    score(gateway, _score_input())
    assert hook.calls[0]["prompt_version"] == SCORE_PROMPT_VERSION == "interview-score-v1"
```

- [ ] **Step 2: 跑测试确认失败（模块不存在）**

Run: `pytest tests/test_interview_scoring_agent.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'app.agents.interview_scoring'`

- [ ] **Step 3: 实现 `app/agents/interview_scoring.py`**

```python
"""post 评分 L3 Agent（voice-structured-interview U5 tasks 6.2）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库
是 app/graph/interview_scoring_nodes.py 的 effect_* 节点的事；证据反查校正
（tasks 6.3）也不在这里做——反查需要 turn 原文，那是 L4 已经查出来的数据
（app/graph/interview_scoring_nodes.py::compute_align 的返回值），本模块不
import app.storage，拿不到。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import ScoreInput
from app.schemas.interview_scoring_result import ScoreCardOut, ScoreDimensionOut

SCORE_PROMPT_VERSION = "interview-score-v1"

_SCORE_SYSTEM_PROMPT_TEMPLATE = (
    "你是资深技术面试官。以下是一场结构化面试的全部问答记录（turn_id/题号/题面/"
    "回答原文）。请按给定的 rubric 维度白名单逐维评分，0.0-5.0 分。"
    "每个维度 MUST 给出：dimension（必须是白名单中的原样值，不得发明白名单外的"
    "维度）、score、rationale、evidence（turn_id 必须是给定问答记录里出现过的 "
    "turn_id 之一，quote 必须是该 turn 回答原文中的一段连续文字，逐字摘录，不得"
    "改写或概括）。"
    "rubric 维度白名单：{dimensions}。"
    "另外给出 overall_summary：整场面试的一段简要总体评价（100 字以内）。"
    "输出 JSON，字段：dimensions(array)、overall_summary(string)。"
)


class ScoringGenerationFailed(Exception):
    """模型返回的维度评分全部越界丢弃（不在白名单内）后，本次评分判失败（可重
    试），与 app/agents/interview_prep.py::PrepGenerationFailed 同一判据。"""


@dataclass(frozen=True)
class ScoredDimensionDraft:
    dimension: str
    score: float
    rationale: str
    turn_id: str
    quote: str


@dataclass(frozen=True)
class ScoreCardDraft:
    dimensions: list[ScoredDimensionDraft]
    overall_summary: str
    dropped_count: int
    run_id: str
    response_model: str | None


def _build_system_prompt(score_input: ScoreInput) -> str:
    return _SCORE_SYSTEM_PROMPT_TEMPLATE.format(
        dimensions="、".join(score_input.rubric_dimensions)
    )


def _to_draft(item: ScoreDimensionOut) -> ScoredDimensionDraft:
    return ScoredDimensionDraft(
        dimension=item.dimension, score=item.score, rationale=item.rationale,
        turn_id=item.evidence.turn_id, quote=item.evidence.quote,
    )


def _filter_whitelisted(
    items: list[ScoreDimensionOut], *, dimensions: list[str]
) -> tuple[list[ScoredDimensionDraft], int]:
    allowed = set(dimensions)
    kept: list[ScoredDimensionDraft] = []
    dropped = 0
    seen: set[str] = set()
    for item in items:
        if item.dimension in allowed and item.dimension not in seen:
            seen.add(item.dimension)
            kept.append(_to_draft(item))
        else:
            dropped += 1
    return kept, dropped


def score(
    gateway: LLMGateway,
    score_input: ScoreInput,
    *,
    max_retries: int = 2,
    audit_context: dict | None = None,
) -> ScoreCardDraft:
    """L3 Agent：纯函数，只调 LLM 网关。

    重试判据与 app/agents/interview_prep.py::generate 同一判据："本轮维度评分
    全部越界丢弃"才判失败重试，单条越界只丢弃计数、其余维度保留。
    """
    system_prompt = _build_system_prompt(score_input)
    last_dropped = 0

    for _ in range(max_retries):
        parsed, meta = gateway.extract_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=score_input.model_dump_json(),
            schema=ScoreCardOut,
            prompt_version=SCORE_PROMPT_VERSION,
            audit_context=audit_context,
        )
        kept, dropped = _filter_whitelisted(
            parsed.dimensions, dimensions=score_input.rubric_dimensions
        )
        last_dropped = dropped

        if kept:
            return ScoreCardDraft(
                dimensions=kept,
                overall_summary=parsed.overall_summary,
                dropped_count=dropped,
                run_id=meta.run_id,
                response_model=meta.response_model,
            )

    raise ScoringGenerationFailed(
        f"{max_retries} 次尝试后评分维度全部越界丢弃（最近一次丢弃 {last_dropped} 条）"
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_scoring_agent.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/agents/interview_scoring.py tests/test_interview_scoring_agent.py
git commit -m "feat(voice-interview): U5 post 评分 L3 Agent"
```

---

### Task 4: `compute_align`——转写按 turn 对齐

**Files:**
- Create: `app/graph/interview_scoring_nodes.py`
- Test: `tests/test_interview_scoring_nodes.py`

**Interfaces:**
- Consumes：`sqlite3.Connection`（`interview_turn`/`interview_session`/`application`/`job_prep_config` 表，均 Task 1 之前已建好或本计划扩展）
- Produces：`AlignedTurn(turn_id: str, seq: int, question_id: str, question_text: str, answer_text: str, answer_mode: str, asr_confidence: float|None, audio_start_ms: int|None, audio_end_ms: int|None, low_confidence: bool)`（dataclass）、`compute_align(conn, *, session_id: str) -> list[AlignedTurn]`、`_load_thresholds(conn, session_id: str) -> tuple[float, float]`（内部函数，返回 `(low_confidence_threshold, low_score_threshold)`，Task 7/8 也会用到）。Task 5/6/7 都消费 `AlignedTurn` 列表。

- [ ] **Step 1: 写测试**

```python
"""app/graph/interview_scoring_nodes.py：L4 编排层。数据库用真实 SQLite
（tmp_path），LLM 用脚本化假客户端（本文件的 compute_align 测试不涉及 LLM）。"""
import pytest

from app.graph.interview_scoring_nodes import AlignedTurn, compute_align
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_job_application_session(conn, *, job_id="job-1", application_id="app-1", session_id="sess-1"):
    conn.execute("INSERT INTO job (id, title) VALUES (?, '嵌入式软件工程师')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')", (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')", (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-prep', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', ?, 1, 1, 'run-prep', 'frozen')", (application_id,),
    )
    conn.execute(
        "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
        "rubric_json, follow_ups_json, rationale) VALUES "
        "('q1', 'snap-1', 1, 'AUTOSAR CP', 'easy', '讲讲你的项目', '{}', '[]', 'r')"
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES (?, ?, 1, '2099-01-01', 'v1', 'internal_sim', 'completed')",
        (session_id, application_id),
    )
    conn.commit()


def _insert_turn(conn, *, turn_id, session_id, seq, answer_mode="text", answer_text="回答内容",
                  asr_confidence=None, audio_start_ms=None, audio_end_ms=None):
    conn.execute(
        "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, "
        "answer_text, answer_mode, audio_start_ms, audio_end_ms, asr_confidence) "
        "VALUES (?, ?, ?, 'q1', '讲讲你的项目', ?, ?, ?, ?, ?)",
        (turn_id, session_id, seq, answer_text, answer_mode, audio_start_ms, audio_end_ms, asr_confidence),
    )
    conn.commit()


def test_compute_align_returns_turns_ordered_by_seq(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t2", session_id="sess-1", seq=2, answer_text="第二题回答")
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="第一题回答")

    aligned = compute_align(conn, session_id="sess-1")

    assert [t.turn_id for t in aligned] == ["t1", "t2"]
    assert aligned[0].answer_text == "第一题回答"
    assert all(isinstance(t, AlignedTurn) for t in aligned)


def test_compute_align_marks_low_confidence_voice_turn_using_default_threshold(conn):
    _seed_job_application_session(conn)
    _insert_turn(
        conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
        answer_text="低置信度回答", asr_confidence=0.5, audio_start_ms=0, audio_end_ms=5000,
    )
    _insert_turn(
        conn, turn_id="t2", session_id="sess-1", seq=2, answer_mode="voice",
        answer_text="高置信度回答", asr_confidence=0.9, audio_start_ms=0, audio_end_ms=5000,
    )

    aligned = compute_align(conn, session_id="sess-1")

    by_id = {t.turn_id: t for t in aligned}
    assert by_id["t1"].low_confidence is True
    assert by_id["t2"].low_confidence is False


def test_compute_align_uses_job_level_confidence_threshold_override(conn):
    _seed_job_application_session(conn)
    conn.execute(
        "UPDATE job_prep_config SET low_confidence_threshold = 0.95 WHERE job_id = 'job-1'"
    )
    if conn.execute("SELECT changes()").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO job_prep_config (job_id, low_confidence_threshold) VALUES ('job-1', 0.95)"
        )
    conn.commit()
    _insert_turn(
        conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
        answer_text="回答", asr_confidence=0.8, audio_start_ms=0, audio_end_ms=5000,
    )

    aligned = compute_align(conn, session_id="sess-1")

    assert aligned[0].low_confidence is True  # 0.8 < 岗位级覆盖值 0.95


def test_compute_align_text_turn_has_no_low_confidence_flag(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="text", answer_text="文本作答")

    aligned = compute_align(conn, session_id="sess-1")

    assert aligned[0].low_confidence is False  # asr_confidence 为 None，文本作答 turn 恒不标记
    assert aligned[0].audio_start_ms is None and aligned[0].audio_end_ms is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_interview_scoring_nodes.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'app.graph.interview_scoring_nodes'`

- [ ] **Step 3: 实现 `app/graph/interview_scoring_nodes.py`（本步只写 `compute_align`/`_load_thresholds` 部分，后续 Task 会在同一文件追加）**

```python
"""post 评分 L4 编排层（voice-structured-interview U5 tasks 6.1/6.2/6.3/6.4/
6.5/6.6/6.7）。

compute_align/compute_score 只读查库组装输入，不写库（工程铁律 2，与
app/graph/interview_prep_nodes.py 的 compute_prep 同一先例）。effect_* 节点
独占写库，全部用 @idempotent_effect 装饰。

不建真实编译的 LangGraph StateGraph——post 段是批处理触发（没有 Web 请求/
响应之间的人工确认），本来就不需要 interrupt()/Command(resume=...)；节点
序列用 run_post_scoring() 这个普通函数串联，判例见
app/graph/interview_prep_nodes.py 顶部 docstring（2026-08-26 定「行为等价」）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class AlignedTurn:
    turn_id: str
    seq: int
    question_id: str
    question_text: str
    answer_text: str
    answer_mode: str
    asr_confidence: float | None
    audio_start_ms: int | None
    audio_end_ms: int | None
    low_confidence: bool


def _load_thresholds(conn: sqlite3.Connection, session_id: str) -> tuple[float, float]:
    """读岗位级评分阈值配置（低置信度阈值、低分阈值）。没有对应 job_prep_config
    行的岗位回落到默认值 (0.6, 2.0)——与 app/graph/interview_prep_nodes.py::
    load_prep_config 同一"新表可选、既有岗位零改动"手法。"""
    row = conn.execute(
        "SELECT jpc.low_confidence_threshold, jpc.low_score_threshold "
        "FROM interview_session s "
        "JOIN application a ON a.id = s.application_id "
        "LEFT JOIN job_prep_config jpc ON jpc.job_id = a.job_id "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    low_confidence_threshold, low_score_threshold = row
    return (
        low_confidence_threshold if low_confidence_threshold is not None else 0.6,
        low_score_threshold if low_score_threshold is not None else 2.0,
    )


def compute_align(conn: sqlite3.Connection, *, session_id: str) -> list[AlignedTurn]:
    """L4 compute_* 节点：把该场次的全部 turn 整理为可定位证据单元（interview-
    scorecard spec「转写按 turn 对齐」）。只读，不写库。"""
    low_confidence_threshold, _ = _load_thresholds(conn, session_id)
    rows = conn.execute(
        "SELECT id, seq, question_id, question_text, answer_text, answer_mode, "
        "asr_confidence, audio_start_ms, audio_end_ms FROM interview_turn "
        "WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()

    aligned: list[AlignedTurn] = []
    for (turn_id, seq, question_id, question_text, answer_text, answer_mode,
         asr_confidence, audio_start_ms, audio_end_ms) in rows:
        low_confidence = asr_confidence is not None and asr_confidence < low_confidence_threshold
        aligned.append(
            AlignedTurn(
                turn_id=turn_id, seq=seq, question_id=question_id, question_text=question_text,
                answer_text=answer_text or "", answer_mode=answer_mode, asr_confidence=asr_confidence,
                audio_start_ms=audio_start_ms, audio_end_ms=audio_end_ms, low_confidence=low_confidence,
            )
        )
    return aligned
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k compute_align`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/interview_scoring_nodes.py tests/test_interview_scoring_nodes.py
git commit -m "feat(voice-interview): U5 compute_align 转写按 turn 对齐"
```

---

### Task 5: `compute_score`——组装评分输入并调用 L3 Agent

**Files:**
- Modify: `app/graph/interview_scoring_nodes.py`（追加）
- Modify: `tests/test_interview_scoring_nodes.py`（追加）

**Interfaces:**
- Consumes：Task 4 的 `AlignedTurn`/`_load_thresholds`；Task 3 的 `score()`/`ScoreCardDraft`；`app.schemas.interview_ai_input.ScoreInput`/`ScoreInputTurn`
- Produces：`compute_score(conn, *, session_id: str, aligned_turns: list[AlignedTurn], gateway) -> ScoreCardDraft`。Task 6 直接消费其返回值的 `.dimensions`。

- [ ] **Step 1: 写测试**

在 `tests/test_interview_scoring_nodes.py` 追加（复用 Step 1 已有的 `_seed_job_application_session`/`_insert_turn`/`conn` fixture）：

```python
import json

from app.graph.interview_scoring_nodes import compute_align, compute_score
from app.llm.gateway import LLMGateway


class _ScriptedClient:
    def __init__(self, bodies):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class _RecordingHook:
    def __init__(self, conn):
        self._conn = conn
        self._seq = 0

    def record(self, **kwargs):
        self._seq += 1
        run_id = f"run-{self._seq}"
        audit_context = kwargs.get("audit_context") or {}
        self._conn.execute(
            "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
            "prompt_version, temperature, input_hash, raw_response, created_at) "
            "VALUES (?, ?, ?, 'deepseek-chat', ?, 0, 'hash', ?, datetime('now'))",
            (run_id, audit_context.get("application_id"), audit_context.get("job_id"),
             kwargs["prompt_version"], kwargs["raw_response"]),
        )
        self._conn.commit()
        return run_id


def _score_body(dimension="AUTOSAR CP", turn_id="t1", quote="第一题回答"):
    return json.dumps(
        {
            "dimensions": [
                {"dimension": dimension, "score": 4.0, "rationale": "回答扎实",
                 "evidence": {"turn_id": turn_id, "quote": quote}},
            ],
            "overall_summary": "整体表现良好",
        },
        ensure_ascii=False,
    )


def test_compute_score_builds_score_input_from_frozen_snapshot_dimensions(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="第一题回答")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=_ScriptedClient([_score_body()]), audit_hook=_RecordingHook(conn),
    )

    aligned = compute_align(conn, session_id="sess-1")
    draft = compute_score(conn, session_id="sess-1", aligned_turns=aligned, gateway=gateway)

    assert draft.dimensions[0].dimension == "AUTOSAR CP"
    assert draft.dimensions[0].turn_id == "t1"
    run_row = conn.execute(
        "SELECT application_id, job_id, prompt_version FROM analysis_run WHERE id = ?", (draft.run_id,)
    ).fetchone()
    assert run_row == ("app-1", "job-1", "interview-score-v1")


def test_compute_score_raises_on_unknown_session(conn):
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=_ScriptedClient([]), audit_hook=_RecordingHook(conn),
    )
    with pytest.raises(ValueError, match="interview_session 不存在"):
        compute_score(conn, session_id="no-such-session", aligned_turns=[], gateway=gateway)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k compute_score`
Expected: FAIL，报 `ImportError: cannot import name 'compute_score'`

- [ ] **Step 3: 在 `app/graph/interview_scoring_nodes.py` 追加 `compute_score`**

在文件末尾追加（同时补上 `score`/`ScoreCardDraft`/`ScoreInput`/`ScoreInputTurn` 的 import，插入到文件顶部 import 区）：

```python
# 顶部 import 区追加：
from app.agents.interview_scoring import ScoreCardDraft, score
from app.schemas.interview_ai_input import ScoreInput, ScoreInputTurn
```

```python
def _load_session_context(conn: sqlite3.Connection, session_id: str) -> tuple[str, str, str]:
    """返回 (application_id, job_id, prep_snapshot 的 id)。"""
    row = conn.execute(
        "SELECT s.application_id, a.job_id, s.prep_snapshot_version "
        "FROM interview_session s JOIN application a ON a.id = s.application_id "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    application_id, job_id, snapshot_version = row

    snap_row = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, snapshot_version),
    ).fetchone()
    if snap_row is None:
        raise ValueError(
            f"prep_snapshot 不存在: application_id={application_id!r} version={snapshot_version!r}"
        )
    return application_id, job_id, snap_row[0]


def _load_rubric_dimensions(conn: sqlite3.Connection, snapshot_id: str) -> list[str]:
    """按 seq 顺序取该快照的去重维度列表。⛔ 不用 SELECT DISTINCT ... ORDER BY
    seq——DISTINCT 折叠重复维度后 ORDER BY 引用的 seq 取哪一行未定义，改用
    Python 去重保序。"""
    rows = conn.execute(
        "SELECT dimension FROM prep_question WHERE snapshot_id = ? ORDER BY seq",
        (snapshot_id,),
    ).fetchall()
    dimensions: list[str] = []
    for (dimension,) in rows:
        if dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


def compute_score(
    conn: sqlite3.Connection, *, session_id: str, aligned_turns: list[AlignedTurn], gateway
) -> ScoreCardDraft:
    """L4 compute_* 节点：只读查库组装 ScoreInput，调 L3 Agent 评分（interview-
    scorecard spec「逐维评分带 turn 回指」）。"""
    application_id, job_id, snapshot_id = _load_session_context(conn, session_id)
    rubric_dimensions = _load_rubric_dimensions(conn, snapshot_id)

    score_input = ScoreInput(
        rubric_dimensions=rubric_dimensions,
        turns=[
            ScoreInputTurn(
                turn_id=t.turn_id, seq=t.seq, question_text=t.question_text, answer_text=t.answer_text
            )
            for t in aligned_turns
        ],
    )
    return score(
        gateway,
        score_input,
        audit_context={
            "thread_id": f"{session_id}:post",
            "node": "compute_score",
            "application_id": application_id,
            "job_id": job_id,
            "rubric_version": snapshot_id,
            "rubric_snapshot": {"dimensions": rubric_dimensions},
        },
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k compute_score`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/interview_scoring_nodes.py tests/test_interview_scoring_nodes.py
git commit -m "feat(voice-interview): U5 compute_score 组装评分输入并调用评分 Agent"
```

---

### Task 6: 证据反查校正

**Files:**
- Modify: `app/graph/interview_scoring_nodes.py`（追加）
- Modify: `tests/test_interview_scoring_nodes.py`（追加）

**Interfaces:**
- Consumes：Task 3 的 `ScoreCardDraft`/`ScoredDimensionDraft`；Task 4 的 `AlignedTurn`；`app.parsing.spans.TextSpan`/`locate_quote`（M2 已交付，复用不改）
- Produces：`CorrectedCriterionScore(dimension: str, score: float, turn_id: str, start: int, end: int, quote: str)`（dataclass）、`ScoringEvidenceUnusable`（异常）、`correct_evidence(draft: ScoreCardDraft, aligned_turns: list[AlignedTurn]) -> list[CorrectedCriterionScore]`。Task 8 的 `effect_persist_scorecard` 与 `run_post_scoring` 直接消费。

- [ ] **Step 1: 写测试**

在 `tests/test_interview_scoring_nodes.py` 追加：

```python
from app.agents.interview_scoring import ScoreCardDraft, ScoredDimensionDraft
from app.graph.interview_scoring_nodes import (
    AlignedTurn,
    CorrectedCriterionScore,
    ScoringEvidenceUnusable,
    correct_evidence,
)


def _aligned_turn(turn_id="t1", seq=1, answer_text="做过三年 AUTOSAR CP 分层开发"):
    return AlignedTurn(
        turn_id=turn_id, seq=seq, question_id="q1", question_text="讲讲你的项目",
        answer_text=answer_text, answer_mode="text", asr_confidence=None,
        audio_start_ms=None, audio_end_ms=None, low_confidence=False,
    )


def _draft(dimension="AUTOSAR CP", score=4.0, turn_id="t1", quote="AUTOSAR CP 分层开发"):
    return ScoreCardDraft(
        dimensions=[ScoredDimensionDraft(dimension=dimension, score=score, rationale="r",
                                          turn_id=turn_id, quote=quote)],
        overall_summary="s", dropped_count=0, run_id="run-1", response_model="m",
    )


def test_correct_evidence_locates_exact_offset():
    aligned = [_aligned_turn()]
    corrected = correct_evidence(_draft(), aligned)

    assert len(corrected) == 1
    result = corrected[0]
    assert isinstance(result, CorrectedCriterionScore)
    assert result.dimension == "AUTOSAR CP"
    assert result.quote == "AUTOSAR CP 分层开发"
    answer_text = aligned[0].answer_text
    assert answer_text[result.start:result.end] == "AUTOSAR CP 分层开发"


def test_correct_evidence_raises_when_quote_not_found_in_turn_text():
    aligned = [_aligned_turn(answer_text="完全不相关的回答内容")]
    with pytest.raises(ScoringEvidenceUnusable, match="反查失败"):
        correct_evidence(_draft(), aligned)


def test_correct_evidence_raises_when_turn_id_not_in_session():
    aligned = [_aligned_turn(turn_id="t1")]
    draft = _draft(turn_id="t-not-in-session")
    with pytest.raises(ScoringEvidenceUnusable, match="不属于本场次"):
        correct_evidence(draft, aligned)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k correct_evidence`
Expected: FAIL，报 `ImportError: cannot import name 'CorrectedCriterionScore'`

- [ ] **Step 3: 在 `app/graph/interview_scoring_nodes.py` 追加**

顶部 import 区追加：

```python
from app.parsing.spans import TextSpan, locate_quote
```

文件末尾追加：

```python
class ScoringEvidenceUnusable(Exception):
    """某个维度的证据反查失败，或指向的 turn 不属于本场次——interview-scorecard
    spec Scenario「模型未给出证据」：该次评分整体判不可用，不写入任何评分项。"""


@dataclass(frozen=True)
class CorrectedCriterionScore:
    dimension: str
    score: float
    turn_id: str
    start: int
    end: int
    quote: str


def correct_evidence(
    draft: ScoreCardDraft, aligned_turns: list[AlignedTurn]
) -> list[CorrectedCriterionScore]:
    """用 quote 在 turn 原文里反查校正偏移（interview-scorecard spec「回指
    校正」）：模型给的 start/end 不采信，一律以反查结果为准。任一维度反查失败
    或指向的 turn 不属于本场次，整次评分判不可用（抛异常，⛔ 不做部分写入）。
    """
    turns_by_id = {t.turn_id: t for t in aligned_turns}
    corrected: list[CorrectedCriterionScore] = []

    for dim in draft.dimensions:
        turn = turns_by_id.get(dim.turn_id)
        if turn is None:
            raise ScoringEvidenceUnusable(
                f"维度 {dim.dimension!r} 的证据指向不属于本场次的 turn: {dim.turn_id!r}"
            )
        span = TextSpan(span_id=0, start=0, end=len(turn.answer_text), text=turn.answer_text)
        located = locate_quote(span, dim.quote)
        if located is None:
            raise ScoringEvidenceUnusable(
                f"维度 {dim.dimension!r} 的证据摘录在 turn {dim.turn_id!r} 原文中反查失败: {dim.quote!r}"
            )
        start, end = located
        corrected.append(
            CorrectedCriterionScore(
                dimension=dim.dimension, score=dim.score, turn_id=dim.turn_id,
                start=start, end=end, quote=dim.quote,
            )
        )
    return corrected
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k correct_evidence`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/interview_scoring_nodes.py tests/test_interview_scoring_nodes.py
git commit -m "feat(voice-interview): U5 证据反查校正"
```

---

### Task 7: 要点提示派生 ＋ 声学参考计算与写入

**Files:**
- Modify: `app/graph/interview_scoring_nodes.py`（追加）
- Modify: `tests/test_interview_scoring_nodes.py`（追加）

**Interfaces:**
- Consumes：Task 6 的 `CorrectedCriterionScore`；Task 4 的 `AlignedTurn`/`_load_thresholds`；`app.storage.idempotency.idempotent_effect`
- Produces：`TalkingPoint(dimension: str, turn_id: str, tip_text: str)`（dataclass）、`derive_talking_points(corrected_scores, aligned_turns, *, low_score_threshold: float) -> list[TalkingPoint]`、`BASELINE_CHARS_PER_SECOND = 4.5`、`compute_acoustic_ref(*, answer_text: str, audio_start_ms: int|None, audio_end_ms: int|None) -> str|None`、`effect_write_acoustic_refs(conn, *, thread_id, business_key, session_id, aligned_turns)`。Task 8 的 `run_post_scoring` 调用 `derive_talking_points`/`effect_write_acoustic_refs`。

- [ ] **Step 1: 写测试**

在 `tests/test_interview_scoring_nodes.py` 追加：

```python
import json as _json  # 顶部已 import json 时可省略，按需去重

from app.graph.interview_scoring_nodes import (
    TalkingPoint,
    compute_acoustic_ref,
    derive_talking_points,
    effect_write_acoustic_refs,
)


def test_derive_talking_points_flags_low_score_dimension():
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=1.5, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    aligned = [_aligned_turn(turn_id="t1")]

    tips = derive_talking_points(corrected, aligned, low_score_threshold=2.0)

    assert len(tips) == 1
    assert tips[0].dimension == "AUTOSAR CP"
    assert tips[0].turn_id == "t1"
    assert "得分偏低" in tips[0].tip_text


def test_derive_talking_points_ignores_dimension_above_threshold():
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    aligned = [_aligned_turn(turn_id="t1")]

    tips = derive_talking_points(corrected, aligned, low_score_threshold=2.0)

    assert tips == []


def test_derive_talking_points_flags_low_confidence_voice_turn():
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    low_conf_turn = AlignedTurn(
        turn_id="t2", seq=2, question_id="q2", question_text="沟通方式",
        answer_text="转写内容", answer_mode="voice", asr_confidence=0.3,
        audio_start_ms=0, audio_end_ms=5000, low_confidence=True,
    )
    aligned = [_aligned_turn(turn_id="t1"), low_conf_turn]

    tips = derive_talking_points(corrected, aligned, low_score_threshold=2.0)

    assert any(t.turn_id == "t2" and "置信度低" in t.tip_text for t in tips)


def test_derive_talking_points_dedupes_same_dimension_and_turn():
    low_score_low_conf_turn = AlignedTurn(
        turn_id="t1", seq=1, question_id="q1", question_text="讲讲你的项目",
        answer_text="做过三年", answer_mode="voice", asr_confidence=0.3,
        audio_start_ms=0, audio_end_ms=5000, low_confidence=True,
    )
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=1.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]

    tips = derive_talking_points(corrected, [low_score_low_conf_turn], low_score_threshold=2.0)

    assert len(tips) == 1  # 同一 (dimension, turn_id) 只保留一条，低分文案优先
    assert "得分偏低" in tips[0].tip_text


def test_compute_acoustic_ref_returns_none_for_text_answer():
    assert compute_acoustic_ref(answer_text="文本作答", audio_start_ms=None, audio_end_ms=None) is None


def test_compute_acoustic_ref_computes_speech_rate_and_pause_ratio():
    ref_json = compute_acoustic_ref(answer_text="做过三年 AUTOSAR CP 分层开发", audio_start_ms=0, audio_end_ms=10000)
    assert ref_json is not None
    ref = json.loads(ref_json)
    assert "speech_rate_cpm" in ref
    assert "pause_ratio" in ref
    assert ref["pause_ratio"] == ref["silence_ratio"]
    assert 0.0 <= ref["pause_ratio"] <= 1.0
    assert "note" in ref


def test_effect_write_acoustic_refs_writes_only_voice_turns_with_audio(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
                 answer_text="语音作答内容", audio_start_ms=0, audio_end_ms=8000)
    _insert_turn(conn, turn_id="t2", session_id="sess-1", seq=2, answer_mode="text", answer_text="文本作答")

    aligned = compute_align(conn, session_id="sess-1")
    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )

    row1 = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't1'").fetchone()
    row2 = conn.execute("SELECT acoustic_ref FROM interview_turn WHERE id = 't2'").fetchone()
    assert row1[0] is not None
    assert row2[0] is None


def test_effect_write_acoustic_refs_is_idempotent(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_mode="voice",
                 answer_text="语音作答内容", audio_start_ms=0, audio_end_ms=8000)
    aligned = compute_align(conn, session_id="sess-1")

    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )
    log_count_1 = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]

    effect_write_acoustic_refs(
        conn, thread_id="sess-1:post", business_key="sess-1", session_id="sess-1", aligned_turns=aligned
    )
    log_count_2 = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]

    assert log_count_1 == log_count_2 == 1  # 第二次调用命中幂等键，effect_log 不再增加
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k "talking_points or acoustic_ref"`
Expected: FAIL，报 `ImportError`

- [ ] **Step 3: 在 `app/graph/interview_scoring_nodes.py` 追加**

顶部 import 区追加：

```python
import json
import logging

from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)
```

文件末尾追加：

```python
@dataclass(frozen=True)
class TalkingPoint:
    dimension: str
    turn_id: str
    tip_text: str


def derive_talking_points(
    corrected_scores: list[CorrectedCriterionScore],
    aligned_turns: list[AlignedTurn],
    *,
    low_score_threshold: float,
) -> list[TalkingPoint]:
    """规则派生要点提示（interview-scorecard spec「ScoreCard 与要点提示只作
    参考」），不再调 LLM。两类来源：① 低分维度 ② 低置信度语音 turn；同一
    (dimension, turn_id) 只保留一条，低分文案优先（低分维度先写入 dict，
    低置信度检查时 `if key not in seen` 短路，不覆盖）。"""
    seen: dict[tuple[str, str], TalkingPoint] = {}

    for cs in corrected_scores:
        if cs.score < low_score_threshold:
            key = (cs.dimension, cs.turn_id)
            seen[key] = TalkingPoint(
                dimension=cs.dimension, turn_id=cs.turn_id,
                tip_text=(
                    f"建议终面追问：候选人在【{cs.dimension}】维度得分偏低（{cs.score}），"
                    "可结合本轮回答当面深挖"
                ),
            )

    dimension_by_turn: dict[str, str] = {cs.turn_id: cs.dimension for cs in corrected_scores}
    for turn in aligned_turns:
        if turn.answer_mode == "voice" and turn.low_confidence:
            dimension = dimension_by_turn.get(turn.turn_id, f"第 {turn.seq} 题")
            key = (dimension, turn.turn_id)
            if key not in seen:
                seen[key] = TalkingPoint(
                    dimension=dimension, turn_id=turn.turn_id,
                    tip_text="该 turn 转写置信度低，建议面试官当面复核候选人在此题的实际回答",
                )

    return list(seen.values())


# 普通话正常语速的经验值（字/秒），用于估算"预期朗读时长"——非实测校准，
# 是"没有逐词时间戳时的近似基线"，不是精确测量（design 决策 4）。
BASELINE_CHARS_PER_SECOND = 4.5


def compute_acoustic_ref(
    *, answer_text: str, audio_start_ms: int | None, audio_end_ms: int | None
) -> str | None:
    """turn 级近似估算语速/停顿/静默（interview-scorecard spec「声学信号只
    展示不计分」）。⚠️ 这是启发式近似，不是逐词 VAD——interview_turn 只有
    turn 级起止毫秒，没有逐词时间戳，无法做真正的停顿检测。文本作答 turn
    （音频起止任一为空）恒返回 None。"""
    if audio_start_ms is None or audio_end_ms is None:
        return None
    duration_ms = audio_end_ms - audio_start_ms
    if duration_ms <= 0:
        return None

    char_count = len(answer_text or "")
    speech_rate_cpm = char_count / (duration_ms / 60000)
    expected_speaking_ms = (char_count / BASELINE_CHARS_PER_SECOND) * 1000
    pause_ratio = max(0.0, min(1.0, (duration_ms - expected_speaking_ms) / duration_ms))

    return json.dumps(
        {
            "speech_rate_cpm": round(speech_rate_cpm, 1),
            "pause_ratio": round(pause_ratio, 3),
            "silence_ratio": round(pause_ratio, 3),
            "note": "turn 级近似估算，非逐词 VAD；停顿与静默取同一近似值",
        },
        ensure_ascii=False,
    )


@idempotent_effect("effect_write_acoustic_refs")
def effect_write_acoustic_refs(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    session_id: str,
    aligned_turns: list[AlignedTurn],
) -> None:
    """effect_* 节点：把每个 turn 的声学参考写入 interview_turn.acoustic_ref
    （只读展示字段）。独立于评分成败——即便评分失败待重试，面试官也应该能看到
    已完成场次的声学参考，故本节点不依赖 compute_score 的结果。"""
    for turn in aligned_turns:
        acoustic_ref = compute_acoustic_ref(
            answer_text=turn.answer_text,
            audio_start_ms=turn.audio_start_ms,
            audio_end_ms=turn.audio_end_ms,
        )
        if acoustic_ref is not None:
            conn.execute(
                "UPDATE interview_turn SET acoustic_ref = ? WHERE id = ?",
                (acoustic_ref, turn.turn_id),
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k "talking_points or acoustic_ref"`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/interview_scoring_nodes.py tests/test_interview_scoring_nodes.py
git commit -m "feat(voice-interview): U5 要点提示派生与声学参考计算写入"
```

---

### Task 8: `effect_persist_scorecard` ／ `effect_mark_scoring_failed` ／ `run_post_scoring` 编排

**Files:**
- Modify: `app/graph/interview_scoring_nodes.py`（追加）
- Modify: `tests/test_interview_scoring_nodes.py`（追加）

**Interfaces:**
- Consumes：Task 3/4/5/6/7 的全部产出；`app.audit.evidence_ref.InterviewTurnEvidenceRef`/`format_interview_turn_evidence_ref`/`validate_interview_turn_evidence`（U1 已交付）
- Produces：`effect_persist_scorecard(conn, *, thread_id, business_key, session_id, corrected_scores, overall_summary, talking_points, analysis_run_id)`、`effect_mark_scoring_failed(conn, *, thread_id, business_key, session_id, reason)`、`run_post_scoring(conn, *, session_id: str, gateway) -> str`（返回 `"scored"` 或 `"failed_retry"`）。Task 9 的批处理脚本与 e2e 测试直接调用 `run_post_scoring`。

- [ ] **Step 1: 写测试**

在 `tests/test_interview_scoring_nodes.py` 追加：

```python
import uuid

from app.audit.evidence_ref import parse_evidence_ref
from app.graph.interview_scoring_nodes import (
    effect_mark_scoring_failed,
    effect_persist_scorecard,
    run_post_scoring,
)


def test_effect_persist_scorecard_writes_criterion_score_and_scorecard(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="做过三年 AUTOSAR CP")
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "prompt_version, temperature, input_hash, raw_response) VALUES "
        "('score-run-1', 'app-1', 'job-1', 'deepseek-chat', 'interview-score-v1', 0, 'h', '{}')"
    )
    conn.commit()
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="t1",
                                          start=0, end=5, quote="做过三年")]
    tips = [TalkingPoint(dimension="AUTOSAR CP", turn_id="t1", tip_text="建议追问")]

    effect_persist_scorecard(
        conn, thread_id="sess-1:post", business_key="score-run-1", session_id="sess-1",
        corrected_scores=corrected, overall_summary="整体表现良好", talking_points=tips,
        analysis_run_id="score-run-1",
    )

    cs_row = conn.execute(
        "SELECT criterion_key, score, evidence_ref FROM criterion_score WHERE analysis_run_id = 'score-run-1'"
    ).fetchone()
    assert cs_row[0] == "AUTOSAR CP"
    ref = parse_evidence_ref(cs_row[2])
    assert ref.id == "t1"

    sc_row = conn.execute(
        "SELECT summary FROM interview_scorecard WHERE session_id = 'sess-1'"
    ).fetchone()
    assert sc_row[0] == "整体表现良好"

    tip_row = conn.execute(
        "SELECT tip_text FROM interview_scorecard_tip"
    ).fetchone()
    assert tip_row[0] == "建议追问"

    session_row = conn.execute(
        "SELECT post_scoring_status, post_scored_at FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()
    assert session_row[0] == "scored"
    assert session_row[1] is not None


def test_effect_persist_scorecard_rejects_dangling_evidence_ref(conn):
    _seed_job_application_session(conn)
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "prompt_version, temperature, input_hash, raw_response) VALUES "
        "('score-run-2', 'app-1', 'job-1', 'deepseek-chat', 'interview-score-v1', 0, 'h', '{}')"
    )
    conn.commit()
    corrected = [CorrectedCriterionScore(dimension="AUTOSAR CP", score=4.0, turn_id="no-such-turn",
                                          start=0, end=5, quote="做过三年")]

    with pytest.raises(ValueError, match="interview_turn 不存在"):
        effect_persist_scorecard(
            conn, thread_id="sess-1:post", business_key="score-run-2", session_id="sess-1",
            corrected_scores=corrected, overall_summary="s", talking_points=[],
            analysis_run_id="score-run-2",
        )
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM interview_scorecard").fetchone()[0] == 0


def test_effect_mark_scoring_failed_sets_failed_retry_status(conn):
    _seed_job_application_session(conn)

    effect_mark_scoring_failed(
        conn, thread_id="sess-1:post", business_key=str(uuid.uuid4()),
        session_id="sess-1", reason="维度证据反查失败",
    )

    row = conn.execute("SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'").fetchone()
    assert row[0] == "failed_retry"


def test_run_post_scoring_succeeds_and_marks_scored(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="做过三年 AUTOSAR CP 分层开发")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False,
        client=_ScriptedClient([_score_body(quote="做过三年 AUTOSAR CP 分层开发")]),
        audit_hook=_RecordingHook(conn),
    )

    result = run_post_scoring(conn, session_id="sess-1", gateway=gateway)

    assert result == "scored"
    status = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()[0]
    assert status == "scored"
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 1


def test_run_post_scoring_marks_failed_retry_when_evidence_unusable(conn):
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="完全不相关的内容")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False,
        client=_ScriptedClient([_score_body(quote="AUTOSAR CP 分层开发")]),  # quote 在 turn 原文里反查不到
        audit_hook=_RecordingHook(conn),
    )

    result = run_post_scoring(conn, session_id="sess-1", gateway=gateway)

    assert result == "failed_retry"
    status = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()[0]
    assert status == "failed_retry"
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 0


def test_run_post_scoring_is_idempotent_and_does_not_rescore_after_success(conn):
    """run_post_scoring 自身必须是幂等的，不能只依赖批处理脚本的 WHERE 过滤：
    interview_scorecard 有 UNIQUE(session_id)，对已 scored 的场次重复调用若
    不做短路会撞唯一键报错，必须在函数入口检查状态并直接返回 "scored"，
    ⛔ 不产出第二条 criterion_score。"""
    _seed_job_application_session(conn)
    _insert_turn(conn, turn_id="t1", session_id="sess-1", seq=1, answer_text="做过三年 AUTOSAR CP 分层开发")
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False,
        client=_ScriptedClient([_score_body(quote="做过三年 AUTOSAR CP 分层开发")]),
        audit_hook=_RecordingHook(conn),
    )

    first = run_post_scoring(conn, session_id="sess-1", gateway=gateway)
    assert first == "scored"

    # 第二次调用不应该再消费 gateway 的脚本化响应（只喂了一条）——如果实现
    # 没有在入口短路，这里会因为 ScriptedClient 的响应列表耗尽而报 IndexError，
    # 而不是命中 UNIQUE 约束，两种失败都足以说明幂等短路没生效。
    second = run_post_scoring(conn, session_id="sess-1", gateway=gateway)
    assert second == "scored"
    assert conn.execute("SELECT COUNT(*) FROM criterion_score").fetchone()[0] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_interview_scoring_nodes.py -v -k "persist_scorecard or mark_scoring_failed or run_post_scoring"`
Expected: FAIL，报 `ImportError`

- [ ] **Step 3: 在 `app/graph/interview_scoring_nodes.py` 追加**

顶部 import 区追加：

```python
import uuid

from app.audit.evidence_ref import (
    InterviewTurnEvidenceRef,
    format_interview_turn_evidence_ref,
    validate_interview_turn_evidence,
)
```

文件末尾追加：

```python
@idempotent_effect("effect_persist_scorecard")
def effect_persist_scorecard(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    session_id: str,
    corrected_scores: list[CorrectedCriterionScore],
    overall_summary: str,
    talking_points: list[TalkingPoint],
    analysis_run_id: str,
) -> None:
    """effect_* 节点：写 analysis_run(已由 gateway 落库)＋criterion_score＋
    interview_scorecard＋interview_scorecard_tip，同事务、独占、幂等（interview-
    scorecard spec「逐维评分带 turn 回指」；design D4/D20）。business_key 传
    analysis_run_id（字面幂等键 `{session_id}:effect_persist_scorecard:
    {analysis_run_id}`）。

    悬空/越界的证据回指在这里被 validate_interview_turn_evidence 拒绝（抛
    ValueError）——@idempotent_effect 装饰器会回滚本次调用已经写入的行，
    ⛔ 不会有部分写入残留。
    """
    for cs in corrected_scores:
        ref = InterviewTurnEvidenceRef(id=cs.turn_id, start=cs.start, end=cs.end, quote=cs.quote)
        validate_interview_turn_evidence(conn, ref)
        conn.execute(
            "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), analysis_run_id, cs.dimension, cs.score,
                format_interview_turn_evidence_ref(cs.turn_id, cs.start, cs.end, cs.quote),
            ),
        )

    scorecard_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interview_scorecard (id, session_id, analysis_run_id, summary) VALUES (?, ?, ?, ?)",
        (scorecard_id, session_id, analysis_run_id, overall_summary),
    )
    for seq, tip in enumerate(talking_points, start=1):
        conn.execute(
            "INSERT INTO interview_scorecard_tip (id, scorecard_id, dimension, turn_id, tip_text, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), scorecard_id, tip.dimension, tip.turn_id, tip.tip_text, seq),
        )

    conn.execute(
        "UPDATE interview_session SET post_scoring_status = 'scored', "
        "post_scored_at = datetime('now') WHERE id = ?",
        (session_id,),
    )


@idempotent_effect("effect_mark_scoring_failed")
def effect_mark_scoring_failed(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, reason: str
) -> None:
    """effect_* 节点：把场次标注「评分失败待重试」并记日志，可观测（interview-
    scorecard spec Scenario「模型未给出证据」）。business_key 用调用方传入的
    一次性 uuid——每次失败尝试都要能独立落一条 effect_log（语义类比
    app/graph/invite_nodes.py::effect_log_invite_access_denied 这类可重复
    事件，不是"只能发生一次"的语义）。"""
    logger.warning("post 评分失败，场次 %s 标记 failed_retry：%s", session_id, reason)
    conn.execute(
        "UPDATE interview_session SET post_scoring_status = 'failed_retry' WHERE id = ?",
        (session_id,),
    )


def run_post_scoring(conn: sqlite3.Connection, *, session_id: str, gateway) -> str:
    """post 子图编排（design D20，普通函数链，不建真实 StateGraph——见设计
    决策 7）：compute_align → effect_write_acoustic_refs → compute_score →
    correct_evidence → effect_persist_scorecard（成功）/ effect_mark_scoring_
    failed（证据不可用或全维度越界丢弃）。返回 "scored" 或 "failed_retry"，
    供批处理脚本记日志。

    入口先查 post_scoring_status——本函数自身必须是幂等的，不能只依赖批处理
    脚本的 WHERE 过滤（scripts/run_interview_post_scoring.py::_due_sessions）：
    interview_scorecard 有 UNIQUE(session_id)，已 scored 的场次若不在这里
    短路，重复调用会在 effect_persist_scorecard 里撞唯一键报错。
    """
    from app.agents.interview_scoring import ScoringGenerationFailed  # 避免模块顶层循环 import

    status_row = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    if status_row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    if status_row[0] == "scored":
        return "scored"

    aligned_turns = compute_align(conn, session_id=session_id)
    effect_write_acoustic_refs(
        conn, thread_id=f"{session_id}:post", business_key=session_id,
        session_id=session_id, aligned_turns=aligned_turns,
    )

    try:
        draft = compute_score(conn, session_id=session_id, aligned_turns=aligned_turns, gateway=gateway)
        corrected_scores = correct_evidence(draft, aligned_turns)
    except (ScoringEvidenceUnusable, ScoringGenerationFailed) as exc:
        effect_mark_scoring_failed(
            conn, thread_id=f"{session_id}:post", business_key=str(uuid.uuid4()),
            session_id=session_id, reason=str(exc),
        )
        return "failed_retry"

    _, low_score_threshold = _load_thresholds(conn, session_id)
    talking_points = derive_talking_points(corrected_scores, aligned_turns, low_score_threshold=low_score_threshold)

    effect_persist_scorecard(
        conn, thread_id=f"{session_id}:post", business_key=draft.run_id,
        session_id=session_id, corrected_scores=corrected_scores, overall_summary=draft.overall_summary,
        talking_points=talking_points, analysis_run_id=draft.run_id,
    )
    return "scored"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_scoring_nodes.py -v`
Expected: 全部 PASS（含 Task 4/5/6/7 遗留的全部用例）

- [ ] **Step 5: Commit**

```bash
git add app/graph/interview_scoring_nodes.py tests/test_interview_scoring_nodes.py
git commit -m "feat(voice-interview): U5 effect_persist_scorecard/effect_mark_scoring_failed/run_post_scoring 编排"
```

---

### Task 9: 批处理脚本 ＋ post e2e（文本作答场次）

**Files:**
- Create: `scripts/run_interview_post_scoring.py`
- Test: `tests/test_run_interview_post_scoring_script.py`
- Test: `tests/test_interview_post_scoring_e2e.py`

**Interfaces:**
- Consumes：Task 8 的 `run_post_scoring`；`app.config.get_settings`；`app.audit.{AuditRecorder, JsonlChainSink, RecorderAuditHook, SqliteSink}`（`app/audit/__init__.py` 已导出，与 `app/main.py`/`scripts/replay_pilot_sessions.py` 同一 import 形态）
- Produces：`run_batch(db_path: str, gateway) -> int`（脚本内部函数，供测试直接调用）、`main()`（CLI 入口）

- [ ] **Step 1: 写批处理脚本的单测**

```python
"""scripts/run_interview_post_scoring.py 的批处理选取与错误隔离测试。
LLM 用脚本化假客户端，数据库用真实 SQLite（tmp_path）。"""
import json

import pytest

from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from scripts.run_interview_post_scoring import _due_sessions, run_batch


class _ScriptedClient:
    def __init__(self, bodies):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class _RecordingHook:
    def __init__(self, conn):
        self._conn = conn
        self._seq = 0

    def record(self, **kwargs):
        self._seq += 1
        run_id = f"run-{self._seq}"
        audit_context = kwargs.get("audit_context") or {}
        self._conn.execute(
            "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
            "prompt_version, temperature, input_hash, raw_response, created_at) "
            "VALUES (?, ?, ?, 'deepseek-chat', ?, 0, 'hash', ?, datetime('now'))",
            (run_id, audit_context.get("application_id"), audit_context.get("job_id"),
             kwargs["prompt_version"], kwargs["raw_response"]),
        )
        self._conn.commit()
        return run_id


def _seed_session(conn, *, session_id, status, post_scoring_status, job_id="job-1", application_id="app-1"):
    conn.execute("INSERT OR IGNORE INTO job (id, title) VALUES (?, 'ECU 工程师')", (job_id,))
    conn.execute("INSERT OR IGNORE INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT OR IGNORE INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'hash', 'tester')", (f"resume-{application_id}", job_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, ?, 'initial')", (application_id, job_id, f"resume-{application_id}"),
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status, post_scoring_status) "
        "VALUES (?, ?, 1, '2099-01-01', 'v1', 'internal_sim', ?, ?)",
        (session_id, application_id, status, post_scoring_status),
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def test_due_sessions_selects_completed_and_pending_or_failed_retry_only(conn):
    _seed_session(conn, session_id="s1", status="completed", post_scoring_status="pending")
    _seed_session(conn, session_id="s2", status="completed", post_scoring_status="scored", application_id="app-2")
    _seed_session(conn, session_id="s3", status="in_progress", post_scoring_status="pending", application_id="app-3")
    _seed_session(conn, session_id="s4", status="completed", post_scoring_status="failed_retry", application_id="app-4")

    due = _due_sessions(conn)

    assert set(due) == {"s1", "s4"}


def test_run_batch_continues_after_one_session_raises(conn, monkeypatch):
    _seed_session(conn, session_id="s1", status="completed", post_scoring_status="pending")
    _seed_session(conn, session_id="s2", status="completed", post_scoring_status="pending", application_id="app-2")

    calls = []

    def _fake_run_post_scoring(connection, *, session_id, gateway):
        calls.append(session_id)
        if session_id == "s1":
            raise RuntimeError("模拟未预期异常")
        return "scored"

    monkeypatch.setattr("scripts.run_interview_post_scoring.run_post_scoring", _fake_run_post_scoring)

    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=_ScriptedClient([]), audit_hook=_RecordingHook(conn),
    )
    exit_code = run_batch(conn.execute("PRAGMA database_list").fetchone()[2], gateway)

    assert calls == ["s1", "s2"]  # s1 抛异常后照常处理 s2，不中断整批
    assert exit_code == 1  # 出现未预期异常，退出码非 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_run_interview_post_scoring_script.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'scripts.run_interview_post_scoring'`

- [ ] **Step 3: 实现 `scripts/run_interview_post_scoring.py`**

```python
"""M3 U5 post 评分批处理入口（voice-structured-interview tasks.md 6.7）。

扫描 status='completed' 且尚未成功评分的场次，逐条跑 compute_align →
compute_score → effect_persist_scorecard（app/graph/interview_scoring_nodes.
py::run_post_scoring）。单条场次失败不得让整个批次中断。

真正的 Windows 计划任务安装（SYSTEM 账户/AtStartup/失败重启 3 次）是
tasks.md 8.6 的范围，本脚本只是那个计划任务将来调用的入口，本身不做任何
安装动作。
"""
from __future__ import annotations

import logging
import sys

from app.audit import AuditRecorder, JsonlChainSink, RecorderAuditHook, SqliteSink
from app.config import get_settings
from app.graph.interview_scoring_nodes import run_post_scoring
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema

logger = logging.getLogger(__name__)


def _due_sessions(conn) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM interview_session WHERE status = 'completed' "
        "AND post_scoring_status IN ('pending', 'failed_retry') ORDER BY created_at"
    ).fetchall()
    return [row[0] for row in rows]


def run_batch(db_path: str, gateway: LLMGateway) -> int:
    """返回进程退出码：0＝本轮全部场次都跑完（含判定为 failed_retry 的场次，
    那是预期内的可重试状态，不是批处理本身的故障）；1＝出现未预期异常。"""
    conn = get_connection(db_path)
    init_schema(conn)
    exit_code = 0
    for session_id in _due_sessions(conn):
        try:
            result = run_post_scoring(conn, session_id=session_id, gateway=gateway)
            logger.info("场次 %s 批处理结果: %s", session_id, result)
        except Exception:
            logger.exception(
                "场次 %s 批处理出现未预期异常，跳过本场次继续处理其余场次", session_id
            )
            exit_code = 1
    return exit_code


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    # 审计走专属连接，与 app/main.py::_audit_conn 同形（该文件注释解释了为什么
    # 不能复用共享连接：钩子在 LLMGateway 内部触发，那里没有 conn，复用共享
    # 连接会让留痕行被 idempotent_effect 的 rollback 一起撤销）。本脚本直接
    # 落到生产库 settings.db_path，不是隔离的回放库——批处理本来就是要写
    # 生产数据。
    audit_conn = get_connection(settings.db_path)
    audit_recorder = AuditRecorder(SqliteSink(audit_conn), JsonlChainSink(settings.audit_jsonl_path))
    audit_hook = RecorderAuditHook(audit_recorder, audit_conn)

    gateway = LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
        audit_hook=audit_hook,
        fallback_api_key=settings.llm_fallback_api_key,
        fallback_base_url=settings.llm_fallback_base_url,
        fallback_model=settings.llm_fallback_model,
        fallback_supports_json_schema=settings.llm_fallback_supports_json_schema,
    )

    sys.exit(run_batch(settings.db_path, gateway))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑批处理脚本测试确认通过**

Run: `pytest tests/test_run_interview_post_scoring_script.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 写 post e2e 测试（tasks 6.8）**

```python
"""U5 post 评分完整链路 e2e（voice-structured-interview tasks 6.8）：用 U3
签发的内部模拟场次以文本作答走完 10 题 → post 评分 → 每维有 turn 回指且可
定位 → rejection_record 无新增、application 阶段不变。

⚠️ 候选人文本作答提交端点是 U4 第 5 章 5.7/5.8 范围，晚于本单元交付。本文件
直接用 sqlite3 INSERT interview_turn 行并 UPDATE interview_session.status=
'completed'，是绕过尚未交付端点的**测试专用 fixture**，不代表生产可以这样
写 completed（见 docs/superpowers/plans/2026-09-19-u5-post-scoring.md「设计
决策 6」）。

用真实 RecorderAuditHook（不是脚本化假 hook）：criterion_score.analysis_run_id
是 NOT NULL REFERENCES analysis_run(id)，本测试要验证的正是"AI 评分必须
持久化"这条链路本身，镜像 tests/test_prep_e2e.py 的装配方式。
"""
import json

from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.interview_scoring_nodes import run_post_scoring
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from tests.test_web_api import ScriptedOpenAIClient

RUBRIC_DIMENSIONS = ["AUTOSAR CP", "沟通表达"]

QUESTIONS = [
    ("AUTOSAR CP", "讲讲你做过的 AUTOSAR 项目"),
    ("沟通表达", "怎么跟团队同步进度"),
] * 5  # 10 题，两个维度交替


def _make_conn_with_real_audit(tmp_path):
    db_path = str(tmp_path / "post_scoring.db")
    conn = get_connection(db_path)
    init_schema(conn)
    audit_conn = get_connection(db_path)
    recorder = AuditRecorder(SqliteSink(audit_conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
    hook = RecorderAuditHook(recorder, audit_conn)
    return conn, hook


def _seed_frozen_snapshot_with_ten_questions(conn, *, application_id="app-1", job_id="job-1"):
    conn.execute("INSERT INTO job (id, title) VALUES (?, 'ECU 工程师')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')", (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')", (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-prep', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', ?, 1, 1, 'run-prep', 'frozen')", (application_id,),
    )
    for seq, (dimension, text) in enumerate(QUESTIONS, start=1):
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
            "rubric_json, follow_ups_json, rationale) VALUES (?, 'snap-1', ?, ?, 'easy', ?, '{}', '[]', 'r')",
            (f"q{seq}", seq, dimension, text),
        )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES ('sess-1', ?, 1, '2099-01-01', 'v1', 'internal_sim', 'in_progress')",
        (application_id,),
    )
    conn.commit()
    return application_id


def _complete_session_with_text_answers(conn, *, session_id="sess-1"):
    """测试专用 fixture：直接写 10 条文本作答 turn 并把场次标记 completed，
    绕过尚未交付的候选人提交端点（见文件顶部说明）。"""
    for seq in range(1, 11):
        answer_text = f"针对第 {seq} 题，我做过三年 AUTOSAR CP 分层开发经验，也擅长跨团队同步进度"
        conn.execute(
            "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, "
            "answer_text, answer_mode) VALUES (?, ?, ?, ?, ?, ?, 'text')",
            (f"turn-{seq}", session_id, seq, f"q{seq}", QUESTIONS[seq - 1][1], answer_text),
        )
    conn.execute("UPDATE interview_session SET status = 'completed' WHERE id = ?", (session_id,))
    conn.commit()


def _score_response_body():
    dimensions = []
    for seq in range(1, 11):
        dimension, _ = QUESTIONS[seq - 1]
        dimensions.append({
            "dimension": dimension, "score": 4.0, "rationale": "回答扎实",
            "evidence": {"turn_id": f"turn-{seq}",
                         "quote": f"针对第 {seq} 题，我做过三年 AUTOSAR CP 分层开发经验，也擅长跨团队同步进度"},
        })
    # 每个维度只需要一条评分项（interview-scorecard spec「一个场次的评分」：
    # 每个 rubric 维度各有一条评分项），只取每个维度第一次出现的那条。
    seen = set()
    deduped = []
    for d in dimensions:
        if d["dimension"] not in seen:
            seen.add(d["dimension"])
            deduped.append(d)
    return json.dumps({"dimensions": deduped, "overall_summary": "整体表现良好，AUTOSAR 与沟通均达标"},
                       ensure_ascii=False)


def test_post_scoring_e2e_with_text_answer_session(tmp_path):
    conn, audit_hook = _make_conn_with_real_audit(tmp_path)
    application_id = _seed_frozen_snapshot_with_ten_questions(conn)
    _complete_session_with_text_answers(conn)

    scripted_client = ScriptedOpenAIClient([_score_response_body()])
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat-241226",
        supports_json_schema=False, client=scripted_client, audit_hook=audit_hook,
    )

    rejection_count_before = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    stage_before = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]

    result = run_post_scoring(conn, session_id="sess-1", gateway=gateway)

    assert result == "scored"

    for dimension in RUBRIC_DIMENSIONS:
        row = conn.execute(
            "SELECT cs.evidence_ref FROM criterion_score cs "
            "JOIN analysis_run ar ON ar.id = cs.analysis_run_id "
            "WHERE ar.prompt_version = 'interview-score-v1' AND cs.criterion_key = ?",
            (dimension,),
        ).fetchone()
        assert row is not None, f"维度 {dimension} 没有评分项"
        ref = json.loads(row[0])
        assert ref["type"] == "interview_turn"
        turn_row = conn.execute(
            "SELECT answer_text FROM interview_turn WHERE id = ?", (ref["id"],)
        ).fetchone()
        assert turn_row is not None
        assert turn_row[0][ref["start"]:ref["end"]] == ref["quote"]

    scorecard_row = conn.execute(
        "SELECT summary FROM interview_scorecard WHERE session_id = 'sess-1'"
    ).fetchone()
    assert scorecard_row is not None
    assert scorecard_row[0]

    rejection_count_after = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    stage_after = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    assert rejection_count_after == rejection_count_before == 0
    assert stage_after == stage_before

    session_status = conn.execute(
        "SELECT post_scoring_status FROM interview_session WHERE id = 'sess-1'"
    ).fetchone()[0]
    assert session_status == "scored"
```

- [ ] **Step 6: 跑 e2e 测试确认通过**

Run: `pytest tests/test_interview_post_scoring_e2e.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 跑本单元全部测试确认无回归**

Run: `pytest tests/test_db_m3_schema.py tests/test_db_migration.py tests/test_interview_scoring_result_schema.py tests/test_interview_scoring_agent.py tests/test_interview_scoring_nodes.py tests/test_run_interview_post_scoring_script.py tests/test_interview_post_scoring_e2e.py tests/test_interview_ai_input_schema.py -v`
Expected: 全部 PASS（含 `test_interview_ai_input_schema.py` 的既有反射测试——确认 `ScoreInputTurn` 没有被本单元意外加上声学/音频字段）

- [ ] **Step 8: Commit**

```bash
git add scripts/run_interview_post_scoring.py tests/test_run_interview_post_scoring_script.py tests/test_interview_post_scoring_e2e.py
git commit -m "feat(voice-interview): U5 post 评分批处理脚本与文本作答场次 e2e"
```

---

## 提取验证（spec-to-plan 技能第 6 步，写计划后必须做一遍）

1. 把本计划全部代码块原样提取到临时目录（新文件按 Task 里的 `Create:` 路径落盘；`Modify:` 目标先复制项目当前版本再应用文中给出的追加片段）。
2. 用项目现有 `requirements.txt`（不改版本）在独立 venv 里装依赖。
3. 跑 Step 7 列出的全部测试文件（`pytest tests/test_db_m3_schema.py tests/test_db_migration.py tests/test_interview_scoring_result_schema.py tests/test_interview_scoring_agent.py tests/test_interview_scoring_nodes.py tests/test_run_interview_post_scoring_script.py tests/test_interview_post_scoring_e2e.py tests/test_interview_ai_input_schema.py -v`）。
4. 有失败先在临时副本里定位修复、确认后再同步回本计划文件对应 Task 的代码块，然后清理临时目录重新提取一遍确认无转录误差。
5. 全通只证明代码可执行且内部自洽，不证明符合 spec——spec 合规由 `run-build` 的两阶段 review 负责，执行计划时仍需按 `run-build` 流程走完整两阶段 review。

## 执行方式

计划已保存到 `docs/superpowers/plans/2026-09-19-u5-post-scoring.md`。按本项目 `03-工具链协作规则.md` 的固定映射，执行阶段用 `run-build` skill（`superpowers:subagent-driven-development`），⛔ 不用 `superpowers:executing-plans`（那是通用双选项之一，本项目已固定选型）。
