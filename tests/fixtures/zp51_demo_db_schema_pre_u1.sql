-- .51 现网 demo.db 在 M2·U1 上线前的结构快照（sqlite_master.sql，只取 DDL、不含任何数据行）
-- 取样：2026-09-18，ssh zp51 → C:\apps\zhuopin-recruit-agent\data\demo.db（opener 0918B）
-- 用途：tests/test_db_m2_schema.py 用它建"老库"，验证 init_schema 升级后既有表一行不改。
-- 刻意硬编码为历史事实，⛔ 不要从 app/storage/db.py 的 SCHEMA 再生成（否则测不出"老库升级不了"）。
-- 每条语句以 ; 结尾（sqlite_master.sql 原文没有分号，这是唯一的后处理）。
-- 注意：sqlite_sequence 是 SQLite 内部表，不能手工 CREATE，加载方须跳过。

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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
, is_productive INTEGER NOT NULL DEFAULT 1, turn_started_at TEXT, llm_latency_ms REAL, derived_unspecified_fields TEXT NOT NULL DEFAULT '[]', ungrounded_fields TEXT NOT NULL DEFAULT '[]', written_fields TEXT NOT NULL DEFAULT '[]', llm_response_model TEXT, asked_questions TEXT NOT NULL DEFAULT '[]');

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

CREATE TABLE sqlite_sequence(name,seq);

CREATE TABLE checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );

CREATE TABLE writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                type TEXT,
                value BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            );

CREATE TABLE analysis_run (
    -- ⚠️ 审计资产：本表内容禁止用作任何模型的训练、微调或调优输入。
    -- 理由：历史评分与录用结果携带既有偏见，拿它当监督信号会把偏见放大并
    -- 固化（Amazon 2018 教训，见 CLAUDE.md 合规红线「绝不用历史录用结果做
    -- 监督信号」）。本表只服务两件事：PIPL 第 24 条说明权（"这条评分是哪个
    -- 模型、哪个版本、按哪份 rubric 打的"）与 CI 里的合规断言。
    --
    -- 可空性是刻意设计，不是偷懒：业务关联列与 rubric 列一律允许 NULL。
    -- U3 把 RecorderAuditHook 接到 app/main.py:_gateway_factory() 之后，M1
    -- 现有的岗位画像采集调用会立刻开始写本表，而采集期没有投递、没有 rubric。
    -- 任何一列 NOT NULL 都会在 U3 合并当天把 M1 的采集流程打挂。
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

CREATE INDEX idx_analysis_run_application
    ON analysis_run (application_id);

CREATE TABLE criterion_score (
    -- ⚠️ 审计资产：与 analysis_run 同，禁止用作训练/微调/调优输入。
    --
    -- evidence_ref 的 CHECK 是工程铁律 4 的存储层落点：证据回指为空的评分项
    -- 不允许写入，且这条**由数据库强制**——绕过应用层直接 INSERT 同样被拒。
    -- trim 的第二参数显式列出空格/制表/换行/回车：SQLite 的单参 trim() 只剥
    -- 空格，只写 trim(evidence_ref) 的话一个纯制表符的 evidence_ref 会通过，
    -- 那就等于铁律 4 有一个静默缺口。
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

CREATE INDEX idx_criterion_score_run
    ON criterion_score (analysis_run_id);

CREATE TABLE pending_approval (
    -- 被门禁拦下、等人工放行的候选人外发草稿。**不复用 outbox**：outbox 的
    -- 语义是"已决定要投递的消息"，本表的语义相反（"尚未获批、可能永远不发"）。
    -- 合表就要求每个读 outbox 的地方都加状态过滤，漏一处 = 未审批的拒信被发
    -- 出去（design D5）。
    --
    -- message_type / recipient 可空是刻意的：草稿被拦下的常见原因**正是**这
    -- 些字段缺失或未知（fail-closed）。把它们设成 NOT NULL，会让"拦下一条畸
    -- 形消息"从入队变成 IntegrityError——异常穿透到调用方，一个 except 就是
    -- fail-open。可空性在这里是 fail-closed 的一部分。
    --
    -- confirmed_by 现阶段不可信：鉴权是空壳（AuthContext.user_id 恒为 None），
    -- 值只能由调用方传入。SSO 落地后同一字段变可信，表结构不改（design D7）。
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

CREATE INDEX idx_pending_approval_status
    ON pending_approval (status);

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
    -- M2 批量确认的预留列（tasks 1.4）。现在没有写入方，必须可空。
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
