import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    parse_confidence_threshold REAL NOT NULL DEFAULT 0.7
);

CREATE TABLE IF NOT EXISTS job_profile (
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

-- 每个 job（thread_id）一行，存该会话到目前为止的完整对话记录。
-- 对话历史必须落在持久层而不是只活在 LangGraph checkpoint 里：IntakeState.history
-- 没有 reducer，每次 invoke 的输入会覆盖 checkpoint 里的旧值，靠 checkpoint 记不住
-- 多轮上下文（见 app/graph/state.py 的说明）。由 effect_persist_draft 在写画像草案
-- 的同一个事务里 UPSERT，保证画像与对话同生共死。
CREATE TABLE IF NOT EXISTS conversation (
    thread_id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_log_key ON effect_log (effect_key);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────────
-- 以下三张表属变更包 ai-audit-trail-and-outbound-gate（交付单元 U1）。
-- 三张都是新表，全部走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：
-- 加列路径只服务"老库缺列"这一种情况，新表不需要它。.51 上 data/demo.db 的
-- 15 个真实 job 与既有表一行不改，无数据迁移。
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS analysis_run (
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

CREATE INDEX IF NOT EXISTS idx_analysis_run_application
    ON analysis_run (application_id);

CREATE TABLE IF NOT EXISTS criterion_score (
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

CREATE INDEX IF NOT EXISTS idx_criterion_score_run
    ON criterion_score (analysis_run_id);

CREATE TABLE IF NOT EXISTS pending_approval (
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

-- 重复入队的第二道防线（第一道是 U5 的 idempotent_effect）。按
-- (thread_id, content_hash) 而不是单列 content_hash：U5 的幂等键是
-- {thread_id}:effect_enqueue_pending_approval:{content_hash}，两道防线的
-- 粒度必须一致；单列唯一会让两个不同 thread 的同内容草稿撞上 IntegrityError。
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_approval_content
    ON pending_approval (thread_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_pending_approval_status
    ON pending_approval (status);

-- ─────────────────────────────────────────────────────────────────────────
-- 人工决策留痕（m1-job-profile-intake tasks 1.4 / 6.4 / 9.3）。
-- 新表，走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：加列路径只
-- 服务"老库缺列"这一种情况，新表不需要它。.51 上 data/demo.db 的 17 个真实
-- job 与既有表一行不改，无数据迁移。
--
-- ⛔ job_id 上刻意不加外键。与 effect_log.thread_id、pending_approval.thread_id
-- 同一形态：留痕表按 thread 记事实，把它的可写性绑在业务表上，"留痕写不进去"
-- 就会变成"业务动作整个失败"——而留痕孤立远好过留痕丢失。
--
-- decision_type 的三个取值与 app/graph/nodes.py 的 DECISION_* 常量、
-- app/audit/assertions.py 断言四的 TERMINAL_STATUS_DECISIONS 逐字同源。
-- ⛔ 改任何一处都必须同步改另两处，否则留痕会静默落在一个断言查不到的取值上，
-- 而这个故障没有任何症状：不报错、不失败，只是审计那天答不出话。
--
-- reviewer 的 CHECK 是合规红线「淘汰必须有人工确认节点并留痕」在存储层的落点：
-- 决策人为空的留痕等于没留痕，且这条**由数据库强制**。trim 的第二参数显式列出
-- 空格/制表/换行/回车——SQLite 的单参 trim() 只剥空格（与 criterion_score
-- .evidence_ref 的 CHECK 同一理由）。
CREATE TABLE IF NOT EXISTS human_review (
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

-- 重复留痕的第二道防线（第一道是 idempotent_effect）。粒度与幂等键
-- {job_id}:{node_name}:{profile_version} 完全一致——node_name 与 decision_type
-- 一一对应。两道防线粒度不一致时，宽的那道形同虚设。
-- 这条索引同时也是按 job_id 的查询索引（job_id 是最左前缀），
-- ⛔ 不要再单独建一条 (job_id) 的索引。
CREATE UNIQUE INDEX IF NOT EXISTS idx_human_review_decision
    ON human_review (job_id, profile_version, decision_type);

-- ─────────────────────────────────────────────────────────────────────────
-- 硬门槛规则草案（m1-job-profile-intake tasks 1.2b / 5.8 / 5.9）。
-- 新表，走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：加列路径只
-- 服务"老库缺列"这一种情况，新表不需要它。.51 上 data/demo.db 的既有 job 与
-- 既有表一行不改，无数据迁移。
--
-- ⛔ **本表只存规则、不执行规则。** 合规红线「AI 只做排序推荐，不做自动淘汰」
-- 意味着这里没有任何一行会自己把候选人筛掉；blocking 是给人看的标注，不是
-- 执行开关。本变更包内⛔ 不得出现读本表做判定/打分/淘汰的代码路径。
--
-- ⛔ job_id 上刻意不加外键。与 human_review.job_id、effect_log.thread_id 同一
-- 形态：规则草案按 thread 记事实，把它的可写性绑在业务表上，"草案写不进去"
-- 就会变成"画像确认整个失败"。
--
-- ⛔ 不设代理主键 id。天然键就是规则本身——同一版画像里"同字段同运算符同值"
-- 出现两次就是 bug，而不是两条合法数据。复合主键同时充当去重的第二道防线
-- （第一道是 effect_log 里 {job_id}:effect_confirm_profile:{version} 那把键）。
--
-- operator 的 CHECK 取值与 app/agents/hard_requirement.py 的 OPERATORS 常量
-- 逐字同源。⛔ 改一处必须同步改另一处，否则新运算符会在业务经理点确认的那
-- 一刻炸成 IntegrityError。
--
-- human_readable 的 CHECK 是 spec「每条规则附一句人类可读的说明（用于将来向
-- 候选人解释淘汰原因）」在存储层的落点：说明为空的规则等于没有说明。trim 的
-- 第二参数显式列出空格/制表/换行/回车——SQLite 的单参 trim() 只剥空格（与
-- criterion_score.evidence_ref、human_review.reviewer 的 CHECK 同一理由）。
CREATE TABLE IF NOT EXISTS hard_requirement (
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
-- parser_version/parse_confidence/parsed_json 三列缓存"最新一次解析结果"，
-- 每份历史解析（含重解析）的完整记录在 resume_parse_version 表（U2 tasks 3.8），
-- 工作台默认读本表三列即最新版，查历史版本才查 resume_parse_version。
-- raw_text 是抽取出的全文（app/parsing/extract_text.py::ExtractedText.text），
-- resume_text_span 的 start/end 偏移量都是相对这份原文——字段校对页的高亮
-- 必须对着这份原文切字符串，不能对着任何"重新拼接"的文本切（偏移会对不上）。
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
    raw_text TEXT,
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

-- 解析结果的完整历史（resume-parsing spec「解析留痕与版本」）。resume 表的
-- parsed_json/parse_confidence/parser_version 三列缓存"当前最新版"，本表存
-- 每一次解析尝试的完整记录，旧版本永久保留、不删除、不覆盖。
CREATE TABLE IF NOT EXISTS resume_parse_version (
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

-- 申诉流转审计（m2-resume-parse-and-rank U3 tasks 4.6「均记操作人」）。
-- rejection_record.appeal_status 只保留"当前状态"，本表记录每一次合法
-- 流转的操作人与时刻——包括 none→requested 这次"登记"，不仅仅是最终的
-- overturned。新表，走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：
-- 加列路径只服务"老库缺列"这一种情况，新表不需要它。
--
-- actor 的 CHECK 与 rejection_record.decided_by 同一手法：trim 第二参数
-- 显式列出空格/制表/换行/回车（SQLite 单参 trim() 只剥空格）——空操作人
-- 等于没有留痕，且由数据库强制。
CREATE TABLE IF NOT EXISTS appeal_event (
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

CREATE INDEX IF NOT EXISTS idx_appeal_event_rejection ON appeal_event (rejection_record_id);

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

-- 会话（design D12：鉴权从空壳换成本地账号）。id 本身就是不透明的高熵令牌
-- （secrets.token_urlsafe(32)，256 bit），直接当 Cookie 值使用——校验靠"这条
-- 连接查得到这一行"而不是签名验证，与 Django 的 session 表是同一手法。
-- ⛔ 不加 last_seen_at 之类的滑动续期列：会话固定 TTL，简单够用（app/storage/
-- auth_session.py 的 SESSION_TTL_SECONDS）。
CREATE TABLE IF NOT EXISTS hr_session (
    id TEXT PRIMARY KEY NOT NULL,
    hr_account_id TEXT NOT NULL REFERENCES hr_account(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hr_session_account ON hr_session (hr_account_id);

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

-- prep 出题的岗位级配置（voice-structured-interview U2 tasks 3.3）。新表，
-- ⛔ 不直接给既有的 job 表加列——本包（M3）的字面判据是"老库升级后既有表
-- 一行不改"（tests/test_db_m3_schema.py::
-- test_legacy_pre_m3_db_existing_tables_and_rows_are_untouched 逐字比对
-- sqlite_master.sql 原文，job.parse_confidence_threshold 是 M2 已经加过的
-- 列，M3 不能再往 job 上加新列）。没有对应行的岗位视为使用默认值（应用层
-- 查询按 job_id 找不到行时回落到 'easy_to_hard'/10，见
-- app/graph/interview_prep_nodes.py::load_prep_config）。
CREATE TABLE IF NOT EXISTS job_prep_config (
    job_id TEXT PRIMARY KEY NOT NULL REFERENCES job(id),
    prep_curve TEXT NOT NULL DEFAULT 'easy_to_hard'
        CHECK (prep_curve IN ('easy_to_hard', 'by_dimension')),
    prep_question_count INTEGER NOT NULL DEFAULT 10
);

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
"""


# 2026-08-19 起新增的列必须同时出现在两处：上面的 CREATE TABLE（新库）与下面的
# _ADDED_COLUMNS（老库）。CREATE TABLE IF NOT EXISTS 对已存在的表完全无效，
# .51 上 data/demo.db 有 15 个真实 job、部署脚本不重建库——只改 CREATE TABLE
# 的话新列在服务器上永远不会出现，而且不报错（design.md 决策 10）。
# tests/test_db_migration.py 的漂移守卫测试盯着这两处的一致性。
#
# DDL 片段里的 DEFAULT 必须是常量：SQLite 拒绝 ALTER TABLE ADD COLUMN 带
# 非常量默认值（"Cannot add a column with non-constant default"），所以这里
# 不能写 DEFAULT (datetime('now'))。
#
# turn_started_at / llm_latency_ms 是过渡形态，见 docs/tech-debt.md TD-1
# （analysis_run 落地即删）。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("job_profile", "is_productive", "INTEGER NOT NULL DEFAULT 1"),
    ("job_profile", "turn_started_at", "TEXT"),
    ("job_profile", "llm_latency_ms", "REAL"),
    ("job_profile", "derived_unspecified_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "ungrounded_fields", "TEXT NOT NULL DEFAULT '[]'"),
    # 编造率的分母（第 7 章）。profile_json 存的是累积画像，反推不出"本轮写了
    # 几个字段"——同一字段被修正重写时键数不变，差集恒为空、分母恒偏小、
    # 编造率恒偏大。所以逐轮把写入字段名单单独落一列。
    # 默认值必须是常量（SQLite 拒绝非常量默认值的 ALTER TABLE ADD COLUMN）。
    ("job_profile", "written_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "llm_response_model", "TEXT"),
    ("job_profile", "asked_questions", "TEXT NOT NULL DEFAULT '[]'"),
    # design D5：置信度阈值是岗位级配置，默认 0.7 起步（U0 实测后由 U4 定终值）。
    ("job", "parse_confidence_threshold", "REAL NOT NULL DEFAULT 0.7"),
    # M2 U2 tasks 3.3：抽取出的简历全文。resume 表在 U1 就已经建好并可能已经
    # 存在于任何一个 U1 之后建的库里（含 .51 的 demo.db），U2 只往它的
    # CREATE TABLE 里加了 raw_text 一列——CREATE TABLE IF NOT EXISTS 对已存在
    # 的表彻底无效，不在这里登记的话老库上每一次上传都会在
    # "UPDATE resume SET raw_text = ?" 上 500（final review 发现）。
    ("resume", "raw_text", "TEXT"),
    # voice-structured-interview U3 tasks 4.1：邀约有效期是岗位级配置，
    # 默认 7 天。job_prep_config 在 U2 已建表并可能已存在于任何一个 U2 之后
    # 建的库里，CREATE TABLE IF NOT EXISTS 对已存在的表无效，必须走加列迁移。
    ("job_prep_config", "invite_expiry_days", "INTEGER NOT NULL DEFAULT 7"),
    # tasks 4.7：验证码本身不落明文，只存哈希与过期时刻；phone_attempts 与
    # phone_verified_at 已在 U1 建好，这两列是本单元独有的新增。
    ("interview_session", "phone_code_hash", "TEXT"),
    ("interview_session", "phone_code_expires_at", "TEXT"),
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
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply_column_migrations(conn: sqlite3.Connection) -> list[str]:
    """
    幂等加列：逐列独立判断、缺哪列补哪列，返回本次真的加上的列（格式 table.column）。

    逐列独立是刻意的（design.md 风险表「服务器 SQLite 加列失败或部分成功」）：
    一列失败不影响其余列，重跑一次会把上次没加上的补齐。
    """
    added: list[str] = []
    for table, column, ddl in _ADDED_COLUMNS:
        if column in _existing_columns(conn, table):
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        added.append(f"{table}.{column}")
    return added


def sqlite_utc_now() -> str:
    """
    与 SQLite `datetime('now')` 完全一致的 UTC 时间串（秒级、无时区后缀）。

    为什么不用 datetime.now().isoformat()：job_profile.created_at 由
    `datetime('now')`（UTC，格式 "YYYY-MM-DD HH:MM:SS"）写入，代表轮次结束
    时刻；turn_started_at 由 Python 侧写入，代表轮次开始时刻。两者格式必须
    一模一样，否则"结束 − 开始"这个减法要先做时区与格式对齐，而这类对齐
    迟早会有人做错——最省事的做法是从一开始就不给人做错的机会。
    """
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI dispatches sync route handlers into a
    # worker threadpool (a different thread per request), but create_app()
    # holds one shared connection created on the startup thread. Demo scope
    # has no concurrent-write requirement (design.md 非目标: 不追求高并发);
    # M2's move to Postgres replaces this with per-request pooled connections.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: 方向 A 让 checkpointer 与 effect 层各自持有独立连接后，两个连接
    # 写同一个数据库文件；默认 rollback-journal 模式下，一个连接持有写锁时
    # 另一个连接的写操作会立刻收到 database is locked（SQLITE_BUSY）。WAL
    # 是文件级设置，任一连接设置一次即对整个文件生效（design.md 方向 A 代价
    # 分析）。busy_timeout 是纵深防御：已证伪并发写入假设（本图严格线性），
    # 理论上两个连接不会真正竞争同一把写锁，这条只是防御未来假设被打破时
    # 表现为短暂阻塞重试而不是立刻报错崩溃。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 新库走 CREATE TABLE 就已经带全新列，这里是空转；老库（.51 的 demo.db）
    # 靠这一步补列。两条路径的结果必须一致，由 tests/test_db_migration.py 的
    # test_fresh_and_migrated_schemas_have_identical_columns 守着。
    apply_column_migrations(conn)
    conn.commit()
