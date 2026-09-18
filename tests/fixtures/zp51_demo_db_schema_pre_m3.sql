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

CREATE TABLE appeal_event (
    id TEXT PRIMARY KEY NOT NULL,
    rejection_record_id TEXT NOT NULL REFERENCES rejection_record(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (
        actor IS NOT NULL
        AND trim(actor, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE application (
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

CREATE TABLE application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE candidate (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    phone_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE conversation (
    thread_id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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

CREATE TABLE effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE eval_annotation (
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

CREATE TABLE eval_import_batch (
    -- ⚠️ 禁止训练用途：本表内容禁止用作任何模型的训练、微调、prompt 自动优化输入。
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    source_archive_path TEXT,
    imported_by TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    row_count INTEGER NOT NULL
);

CREATE TABLE eval_sample (
    -- ⚠️ 禁止训练用途，同上。
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    sample_ref TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES eval_import_batch(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE field_review_queue (
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

CREATE TABLE hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE hr_session (
    id TEXT PRIMARY KEY NOT NULL,
    hr_account_id TEXT NOT NULL REFERENCES hr_account(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

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

CREATE TABLE job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    parse_confidence_threshold REAL NOT NULL DEFAULT 0.7
);

CREATE TABLE job_profile (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    unspecified_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 本轮是否有产出（新字段或新问题）。追问预算按有产出轮计数，判定在
    -- compute_intake_turn 里做（m1-intake-quality-fixes 第 3 章）。默认 1
    -- 保证历史行与"未接入判定前"的行为与今天完全一致。
    is_productive INTEGER NOT NULL DEFAULT 1,
    -- 本轮起始时刻（HTTP 请求进入、尚未调模型）。轮次**结束**时刻沿用
    -- created_at，不另加列。两者格式必须一致，见 sqlite_utc_now()。
    turn_started_at TEXT,
    -- 本轮 LLM 累计耗时（含重试），单位毫秒。
    llm_latency_ms REAL,
    -- 系统按画像字段表推导出的未指定字段（第 6 章写）。与上面那列 LLM
    -- 自由生成的 unspecified_fields 并存，前者是真源、后者降级为对照。
    derived_unspecified_fields TEXT NOT NULL DEFAULT '[]',
    -- 本轮未通过来源校验的字段清单（第 7 章写）。
    ungrounded_fields TEXT NOT NULL DEFAULT '[]',
    -- 本轮写入的业务字段名，编造率的分母（第 7 章写）。
    written_fields TEXT NOT NULL DEFAULT '[]',
    -- 本轮 API 响应里实际返回的模型标识（第 7 章写，铁律 5）。
    llm_response_model TEXT,
    -- 本轮实际问出的问题（IntakeQuestion.to_payload() 的 JSON 数组）。
    -- 全部行的并集 = 这个 job 的"已问台账"：is_productive 判定要拿它算
    -- "有没有问出未问过的 question_id"（第 3 章），第 5 章在其上扩
    -- "已答 / 重问次数"。存整份 payload 而不是只存 id：候选档位要能回查，
    -- "用户没选定的档位不得入画像"这条判定需要知道上一轮给过哪些档位。
    asked_questions TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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

CREATE TABLE rejection_record (
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

CREATE TABLE resume (
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
    raw_text TEXT,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE resume_access_log (
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

CREATE TABLE resume_embedding (
    resume_id TEXT NOT NULL REFERENCES resume(id),
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resume_id, model)
);

CREATE TABLE resume_parse_version (
    resume_id TEXT NOT NULL REFERENCES resume(id),
    parser_version TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    model_configured TEXT NOT NULL,
    model_response TEXT,
    prompt_version TEXT NOT NULL,
    parsed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resume_id, parser_version)
);

CREATE TABLE resume_text_span (
    resume_id TEXT NOT NULL REFERENCES resume(id),
    span_id INTEGER NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (resume_id, span_id)
);

CREATE TABLE screening_flag (
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

CREATE TABLE stage (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    stage_type TEXT NOT NULL CHECK (stage_type IN ('initial', 'screening', 'rejected'))
);

CREATE INDEX idx_analysis_run_application
    ON analysis_run (application_id);

CREATE INDEX idx_appeal_event_rejection ON appeal_event (rejection_record_id);

CREATE INDEX idx_application_candidate ON application (candidate_id);

CREATE INDEX idx_application_job ON application (job_id);

CREATE UNIQUE INDEX idx_application_resume ON application (resume_id);

CREATE INDEX idx_application_stage_history_application
    ON application_stage_history (application_id);

CREATE UNIQUE INDEX idx_candidate_name_phone
    ON candidate (name, phone_hash);

CREATE INDEX idx_criterion_score_run
    ON criterion_score (analysis_run_id);

CREATE UNIQUE INDEX idx_effect_log_key ON effect_log (effect_key);

CREATE UNIQUE INDEX idx_eval_annotation_batch_sample
    ON eval_annotation (import_batch_id, eval_sample_id);

CREATE UNIQUE INDEX idx_field_review_queue_pending_unique
    ON field_review_queue (resume_id, field)
    WHERE status = 'pending';

CREATE INDEX idx_field_review_queue_resume ON field_review_queue (resume_id);

CREATE INDEX idx_hr_session_account ON hr_session (hr_account_id);

CREATE UNIQUE INDEX idx_human_review_decision
    ON human_review (job_id, profile_version, decision_type);

CREATE UNIQUE INDEX idx_pending_approval_content
    ON pending_approval (thread_id, content_hash);

CREATE INDEX idx_pending_approval_status
    ON pending_approval (status);

CREATE INDEX idx_rejection_record_application
    ON rejection_record (application_id);

CREATE INDEX idx_rejection_record_batch
    ON rejection_record (batch_id);

CREATE INDEX idx_resume_access_log_resume ON resume_access_log (resume_id);

CREATE INDEX idx_resume_job ON resume (job_id);

CREATE UNIQUE INDEX idx_resume_job_content_hash
    ON resume (job_id, content_sha256);

CREATE INDEX idx_screening_flag_application ON screening_flag (application_id);
