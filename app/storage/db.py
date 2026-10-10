import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

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
--
-- merged_into（channel-resume-intake U2 tasks 2.4）：被合并候选人指向保留方，
-- 未合并为 NULL。⚠️ 本列按 U2 计划由 Task 4 引入，但 Task 2 的
-- app/intake/merge.py::_find_candidate 已经用 `merged_into IS NULL` 过滤未合并
-- 候选人、Task 2 的测试也在同一批里跑——先落地本列（同 Task 4 Step 1/Step 4）
-- 才能让 Task 2 自身可验收；Task 4 只剩 application_stage_history.action 与
-- candidate_merge_log 新表。
CREATE TABLE IF NOT EXISTS candidate (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    phone_hash TEXT,
    merged_into TEXT REFERENCES candidate(id),
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
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT,
    source_origin TEXT CHECK (source_origin IN ('detected', 'default', 'corrected'))
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

-- 来源改正留痕（channel-resume-intake U1 tasks 1.3）。from_source 允许 NULL：
-- 老库/单文件上传的简历 source 本来就是 NULL，第一次改正的"原值"就是 NULL。
-- corrected_by 的 CHECK 与 human_review.reviewer 同一手法（trim 第二参数显式
-- 列出空格/制表/换行/回车）：空改正人等于没留痕。
CREATE TABLE IF NOT EXISTS source_correction_log (
    id TEXT PRIMARY KEY NOT NULL,
    resume_id TEXT NOT NULL REFERENCES resume(id),
    from_source TEXT,
    to_source TEXT NOT NULL,
    corrected_by TEXT NOT NULL CHECK (
        corrected_by IS NOT NULL
        AND trim(corrected_by, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_source_correction_log_resume
    ON source_correction_log (resume_id);

-- 导出包接收结果快照（channel-resume-intake U1 tasks 1.4）。一行 = 一个
-- (job_id, bundle_sha256) 的接收结果：completed 存逐文件结果 JSON，重跑时原样
-- 返回；rejected_gate 是 live 闸关闭时的一次被拒尝试留痕（不存任何文件内容）。
-- 唯一索引是幂等第二道防线（第一道是 effect_log 唯一键）。
CREATE TABLE IF NOT EXISTS bundle_ingest_result (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'rejected_gate')),
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bundle_ingest_result_job_hash
    ON bundle_ingest_result (job_id, bundle_sha256);

-- 阶段池：全局共享，stage_type 是语义标签（逻辑只认类型），name 是可自定义
-- 显示名（CLAUDE.md 数据模型要点）。M2 预置三行、interview-scheduling U1
-- 补第四行 `interview`（面试排期的唯一入口阶段）、offer-generation U1 再补
-- `offer` / `hired`（offer 审批通过后的阶段与入职阶段），id 与 stage_type 同名
-- ——这六行现在就是全部合法阶段，日后要加自定义显示名的同类型阶段，走应用层
-- INSERT 新行（相同 stage_type、不同 id/name），本表结构不必改。
--
-- ⚠️ SQLite 改不了 CHECK：老库（三值或四值）由 init_schema 里的
-- _migrate_stage_offer_hired 整表重建补齐（见该函数）。
CREATE TABLE IF NOT EXISTS stage (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    stage_type TEXT NOT NULL CHECK (
        stage_type IN ('initial', 'screening', 'rejected', 'interview', 'offer', 'hired')
    )
);

INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('interview', '面试', 'interview');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('offer', 'Offer', 'offer');
INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('hired', '已入职', 'hired');

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
-- role 值域用 onboarding-flow U1（design D2）的三值：hr／面试官／部门经理，
-- 默认 'hr'（.51 现有账号全是 HR）；department 用于「部门经理只读本部门」
-- 过滤，历史账号可空。
CREATE TABLE IF NOT EXISTS hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'hr'
        CHECK (role IN ('hr', 'interviewer', 'dept_manager')),
    department TEXT,
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
-- 以下 6 张表属变更包 onboarding-flow（交付单元 U1 入职域模型）。全部新表，
-- 走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**（新表不需要加列路径）。
-- ⛔ 材料不入库：onboarding_item 无 content / attachment / id_number / file 类列
-- （tests/test_db_onboarding_schema.py 用列名反证）。
-- ─────────────────────────────────────────────────────────────────────────

-- 清单模板（design D1/D6）：按岗位（scope_type='job'）或部门（'department'）维护，
-- 版本化（(scope_type, scope_id, version) 唯一）。items 存 JSON 数组，每个元素
-- {name, owner_party, due_offset_days, required}——只跟踪状态，不存材料内容。
CREATE TABLE IF NOT EXISTS onboarding_template (
    id TEXT PRIMARY KEY NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('job', 'department')),
    scope_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    items TEXT NOT NULL,
    updated_by TEXT NOT NULL CHECK (
        updated_by IS NOT NULL
        AND trim(updated_by, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_onboarding_template_scope_version
    ON onboarding_template (scope_type, scope_id, version);

-- 清单实例（design D3）：一份投递一份清单（application_id 唯一），按模板版本展开。
-- status 两态：open（进行中）/ closed（已关闭，关闭原因落 closed_reason）。
CREATE TABLE IF NOT EXISTS onboarding_checklist (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL UNIQUE REFERENCES application(id),
    template_version INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    closed_reason TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 清单条目：只跟踪状态，⛔ 无材料内容/附件/证件号字段。status 三态：
-- pending（待办）/ done（已完成）/ waived（豁免，reason 必填由应用层校验）。
-- required 用 INTEGER 0/1（SQLite 无原生 BOOLEAN）。
CREATE TABLE IF NOT EXISTS onboarding_item (
    id TEXT PRIMARY KEY NOT NULL,
    checklist_id TEXT NOT NULL REFERENCES onboarding_checklist(id),
    name TEXT NOT NULL,
    owner_party TEXT NOT NULL CHECK (
        owner_party IN ('hr', 'it', 'admin', 'finance', 'dept')
    ),
    due_offset_days INTEGER NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'waived')),
    reason TEXT,
    acted_by TEXT,
    acted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_onboarding_item_checklist
    ON onboarding_item (checklist_id);

-- 条目状态变更留痕（spec「条目勾选与豁免留痕」）：每次变更写一行，from/to 两态。
CREATE TABLE IF NOT EXISTS onboarding_item_history (
    id TEXT PRIMARY KEY NOT NULL,
    item_id TEXT NOT NULL REFERENCES onboarding_item(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    reason TEXT,
    acted_by TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_item_history_item
    ON onboarding_item_history (item_id);

-- 部门经理进度查看留痕（spec「查看写访问留痕」）：只记谁/何时/哪个投递，
-- ⛔ 不含清单内容本身。
CREATE TABLE IF NOT EXISTS onboarding_access_log (
    id TEXT PRIMARY KEY NOT NULL,
    accessor TEXT NOT NULL CHECK (
        accessor IS NOT NULL
        AND trim(accessor, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    application_id TEXT NOT NULL REFERENCES application(id),
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_access_log_application
    ON onboarding_access_log (application_id);

-- 入职后招聘数据处置队列（design D4）：登记与执行分离。策略未签认时
-- policy_version NULL、planned_action='pending'、executed_at 恒空（断言在 U3/U4）。
-- (application_id, category) 唯一是 effect_enqueue_disposition 幂等键的结构性
-- 第二道防线。
CREATE TABLE IF NOT EXISTS data_disposition_queue (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    category TEXT NOT NULL CHECK (
        category IN (
            'resume_file', 'parsed_fields', 'scores',
            'interview', 'contact', 'offer_letter'
        )
    ),
    policy_version TEXT,
    planned_action TEXT NOT NULL DEFAULT 'pending'
        CHECK (planned_action IN ('delete', 'anonymize', 'pending')),
    due_at TEXT,
    executed_at TEXT,
    executed_by TEXT,
    note TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_data_disposition_queue_app_category
    ON data_disposition_queue (application_id, category);

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

-- 场次级 live 段事件留痕（voice-structured-interview U4 tasks 5.9，design
-- D20）。⛔ 不与 interview_invite_event 合并：那张表的 event_type CHECK 枚举
-- 已经固定（'issued'/'reissued'/...），SQLite 的 CHECK 约束不能靠
-- ALTER TABLE ADD COLUMN 追加取值，往里塞新枚举值需要整表重建，风险不值得
-- ——新开一张表是更便宜的选择（与 interview_scorecard 的既有先例同一手法）。
CREATE TABLE IF NOT EXISTS interview_live_event (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES interview_session(id),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'opened', 'turn_persisted', 'closed', 'recording_fetched'
    )),
    detail TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interview_live_event_session
    ON interview_live_event (session_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 以下 6 张表属变更包 offer-generation（交付单元 U1「Offer 域模型」）。
-- 全部新表，走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：加列路径
-- 只服务"老库缺列"这一种情况，新表不需要它。
--
-- 本包合规红线：薪资等敏感字段不入库。offer 表只存岗位/部门/入职日/汇报对象/
-- 备注/审批状态/答复，⛔ 不设薪资、股权、签字费、津贴类列；列名反证断言见
-- tests/test_db_offer_schema.py。
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS letter_template (
    -- 文书模板（Offer/拒信共用）。版本化：每次更新产生新版本而不覆盖旧版
    -- （candidate-letter-engine spec「文书模板由 HR 维护并版本化」）。
    -- (kind, version) 是天然键：同一类文书的同一版本号出现两次即 bug。
    kind TEXT NOT NULL CHECK (kind IN ('offer', 'rejection')),
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (kind, version)
);

CREATE TABLE IF NOT EXISTS candidate_letter (
    -- 一份投递的一版文书草稿（Offer 或拒信）。version 是草稿自身版本（每次重新
    -- 生成递增），template_version 记录生成时所用的模板版本（candidate-letter-
    -- engine spec「每一版都留痕可回溯」）。
    --
    -- ai_generated 用 INTEGER CHECK (0,1) 承载 BOOL（SQLite 无 BOOL，与
    -- hard_requirement.blocking 同一手法）。authorship_* 三列记录"标记为人工
    -- 撰写"的谁/何时/原 AI 版本号（spec「编辑不去标，显式标记人工撰写才去标」，
    -- design D7：标识落本包自己的表，不碰 human_review 的 CHECK）。
    --
    -- analysis_run_id 指向 AI 生成留痕（analysis_run，铁律 3），可空：允许未来
    -- 出现"非 AI 生成"的边界行而不必为它伪造一条评分留痕。
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    kind TEXT NOT NULL CHECK (kind IN ('offer', 'rejection')),
    version INTEGER NOT NULL,
    template_version INTEGER NOT NULL,
    body TEXT NOT NULL,
    ai_generated INTEGER NOT NULL CHECK (ai_generated IN (0, 1)),
    authorship_marked_by TEXT,
    authorship_marked_at TEXT,
    authorship_from_version INTEGER,
    analysis_run_id TEXT REFERENCES analysis_run(id),
    sent_status TEXT NOT NULL DEFAULT 'none' CHECK (
        sent_status IN ('none', 'exported', 'copied', 'sent', 'system_queued')
    ),
    sent_channel TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (application_id, kind, version)
);

CREATE INDEX IF NOT EXISTS idx_candidate_letter_application
    ON candidate_letter (application_id);

CREATE TABLE IF NOT EXISTS offer (
    -- Offer 记录。application_id 唯一：一份投递最多一条 Offer（design D5 语义）。
    --
    -- ⛔ 本包合规红线：无任何薪资/股权/签字费/津贴类列——只存岗位/部门/入职日/
    -- 汇报对象/备注/审批状态/答复（offer-record-and-approval spec「Offer 记录的
    -- 字段边界」）。note 是自由文本，页面提示"不得填薪资"，⛔ 不做内容审查
    -- （design D2：做不准，登记为残余风险）。
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL UNIQUE REFERENCES application(id),
    job_id TEXT NOT NULL REFERENCES job(id),
    department TEXT NOT NULL,
    start_date TEXT NOT NULL,
    report_to TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending_approval' CHECK (
        status IN ('pending_approval', 'needs_revision', 'approved', 'exported',
                   'accepted', 'declined', 'negotiating')
    ),
    approval_round INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS offer_approval_chain (
    -- 审批链是岗位级配置（design D6）。天然键 (job_id, level)：同一岗位同一级
    -- 出现两次即 bug。approver_account_ids 存 JSON 数组，元素是可识别账号
    -- （hr_account.username）——spec「审批人为可识别账号」「MUST NOT 由 AI
    -- 生成或推荐审批人」。
    job_id TEXT NOT NULL REFERENCES job(id),
    level INTEGER NOT NULL,
    approver_account_ids TEXT NOT NULL DEFAULT '[]',
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, level)
);

CREATE TABLE IF NOT EXISTS offer_approval (
    -- 每级审批一行（design D6）。(offer_id, round, level) 唯一是审批幂等的
    -- 存储层第二道防线（第一道是 U3 effect_record_approval 的 effect_log 幂等键，
    -- 本单元只建 schema）。
    --
    -- approver 的非空 CHECK 与 rejection_record.decided_by 同一手法（trim 第二参数
    -- 显式列出空格/制表/换行/回车）：空审批人等于没有留痕，且由数据库强制。
    id TEXT PRIMARY KEY NOT NULL,
    offer_id TEXT NOT NULL REFERENCES offer(id),
    round INTEGER NOT NULL,
    level INTEGER NOT NULL,
    approver TEXT NOT NULL CHECK (
        approver IS NOT NULL
        AND trim(approver, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'returned')),
    comment TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (offer_id, round, level)
);

CREATE INDEX IF NOT EXISTS idx_offer_approval_offer
    ON offer_approval (offer_id);

CREATE TABLE IF NOT EXISTS letter_access_log (
    -- 文书查看/导出留痕（candidate-letter-engine spec「导出 docx」与「文书草稿
    -- 的查看留痕」）。⛔ 无正文列（spec「留痕 MUST NOT 包含文书内容本身」），
    -- 只有访问者/投递/文书标识/类型/时刻。
    --
    -- accessor 的非空 CHECK 与 resume_access_log.accessor 同一手法：空访问者
    -- 等于没有留痕。
    id TEXT PRIMARY KEY NOT NULL,
    accessor TEXT NOT NULL CHECK (
        accessor IS NOT NULL
        AND trim(accessor, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    application_id TEXT NOT NULL REFERENCES application(id),
    letter_id TEXT NOT NULL REFERENCES candidate_letter(id),
    access_type TEXT NOT NULL CHECK (access_type IN ('view', 'export')),
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_letter_access_log_letter
    ON letter_access_log (letter_id);

CREATE INDEX IF NOT EXISTS idx_letter_access_log_application
    ON letter_access_log (application_id);
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
    # channel-resume-intake U2 tasks 2.4：合并标记（可空；未合并为 NULL）。
    # ⚠️ 本次（Task 2）已随 CREATE TABLE 一并落地，理由见 candidate 表定义处的
    # 注释：Task 2 的 _find_candidate 就要这一列。Task 4 执行时本行已存在，
    # ⛔ 不要再追一行（apply_column_migrations 逐列判重，重复行虽无害但属噪音）。
    ("candidate", "merged_into", "TEXT REFERENCES candidate(id)"),
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
    # U4 tasks 5.4：追问次数上限，岗位级配置，默认 2（tasks.md 字面值）。
    # job_prep_config 在 U2 建表，走加列迁移（与 low_confidence_threshold
    # 同一先例）。
    ("job_prep_config", "follow_up_limit", "INTEGER NOT NULL DEFAULT 2"),
    # U4 tasks 5.9：effect_fetch_recording 校验并记录回传录音的 sha256，供
    # 审计与重复拉取判重使用。
    ("interview_session", "recording_sha256", "TEXT"),
    # channel-resume-intake U1 tasks 1.3：resume 加来源与来源赋值机制。source 值域
    # 由应用层约束（Source 枚举），source_origin 三态由 DB CHECK 兜底。
    ("resume", "source", "TEXT"),
    ("resume", "source_origin", "TEXT CHECK (source_origin IN ('detected', 'default', 'corrected'))"),
    # interview-scheduling U1：HR 角色授权。hr_account 是 M2 已建老表，CREATE
    # TABLE IF NOT EXISTS 对老库无效，必须走加列迁移；默认 'hr' 让 .51 现有
    # 账号（全是 HR）行为与今天完全一致。值域由 onboarding-flow U1 tasks 1.4
    # 扩到三值（dept_manager），这里复用同一列、⛔ 不重复登记第二条 role。
    ("hr_account", "role", "TEXT NOT NULL DEFAULT 'hr' CHECK (role IN ('hr', 'interviewer', 'dept_manager'))"),
    # onboarding-flow U1 tasks 1.4：hr_account 加 department（部门经理只读本部门
    # 过滤用，历史账号无部门，故可空）。与 role 同属"老表缺列"，走加列路径。
    ("hr_account", "department", "TEXT"),
)


# onboarding-flow U1 占位模板 v1（design.md 风险表：人事部#3 回件未到，先给一份
# 通用部门级默认，回件到后 HR 在页面改）。due_offset_days 相对入职日（负=入职前）。
# 六条负责方与期限均为常识占位，⛔ 非真值。
_ONBOARDING_DEFAULT_TEMPLATE_ITEMS = [
    {"name": "签劳动合同", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "交入职材料", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "体检报告", "owner_party": "hr", "due_offset_days": -5, "required": True},
    {"name": "配置设备", "owner_party": "it", "due_offset_days": -2, "required": True},
    {"name": "开通账号", "owner_party": "it", "due_offset_days": -1, "required": True},
    {"name": "指定带教人", "owner_party": "dept", "due_offset_days": 0, "required": False},
]


def _seed_onboarding_default_template(conn: sqlite3.Connection) -> None:
    """幂等种子：部门级默认模板，固定主键，重复调用不产生第二行。"""
    items_json = json.dumps(_ONBOARDING_DEFAULT_TEMPLATE_ITEMS, ensure_ascii=False)
    conn.execute(
        "INSERT OR IGNORE INTO onboarding_template "
        "(id, scope_type, scope_id, version, items, updated_by) "
        "VALUES ('template-department-default-v1', 'department', 'default', 1, ?, 'system')",
        (items_json,),
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


def _stage_type_check_complete(conn: sqlite3.Connection) -> bool:
    """stage_type 的 CHECK 是否已含 offer/hired。SQLite 改不了 CHECK，本判断
    决定老库是否要整表重建（见 _rebuild_stage_table）。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'stage'"
    ).fetchone()
    if row is None or not row[0]:
        return False
    return "'offer'" in row[0] and "'hired'" in row[0]


def _rebuild_stage_table(conn: sqlite3.Connection) -> None:
    """把 stage.stage_type 的 CHECK 从三值/四值扩到六值（追加 offer/hired）。

    SQLite 无法用 ALTER TABLE 修改 CHECK（与 interview_live_event 建表注释同一
    结论），追加 stage_type 只能整表重建。stage 是维度表且被 application /
    application_stage_history 外键引用，重建期间必须 PRAGMA foreign_keys=OFF，
    完成后 PRAGMA foreign_key_check 复验。行级数据（initial/screening/rejected/
    interview 及可能已存在的 offer/hired）原样复制，一条不丢。
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE stage_new (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                stage_type TEXT NOT NULL CHECK (
                    stage_type IN ('initial', 'screening', 'rejected', 'interview', 'offer', 'hired')
                )
            )
            """
        )
        conn.execute(
            "INSERT INTO stage_new (id, name, stage_type) "
            "SELECT id, name, stage_type FROM stage"
        )
        conn.execute("DROP TABLE stage")
        conn.execute("ALTER TABLE stage_new RENAME TO stage")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        # ⛔ PRAGMA 在事务内是 no-op：必须等上面 commit/rollback 关掉事务后再重开，
        # 否则连接的外键强制会被留在 OFF（1001O seg2 Spec review 实测 FAIL 的根因）。
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(f"stage 重建后外键不一致: {violations}")


def _seed_offer_hired_stages(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO stage (id, name, stage_type) "
        "VALUES ('offer', 'Offer', 'offer')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO stage (id, name, stage_type) "
        "VALUES ('hired', '已入职', 'hired')"
    )


def _migrate_stage_offer_hired(conn: sqlite3.Connection) -> None:
    """老库 stage.stage_type 缺 offer/hired 时整表重建并补种子行；新库已含则空转。"""
    if _stage_type_check_complete(conn):
        return
    _rebuild_stage_table(conn)
    _seed_offer_hired_stages(conn)
    conn.commit()


def _role_check_allows_dept_manager(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'hr_account'"
    ).fetchone()
    return bool(row and row[0] and "'dept_manager'" in row[0])


def _rebuild_hr_account_role_check(conn: sqlite3.Connection) -> None:
    """把 hr_account.role 的 CHECK 从两值放宽到三值（+dept_manager）。

    SQLite 无法改 CHECK（同 `_rebuild_stage_table` 结论）：仅「列已存在、CHECK 缺
    dept_manager」的老库需要整表重建。hr_account 被 hr_session / interviewer 外键
    引用，重建期间 PRAGMA foreign_keys=OFF，完成后 foreign_key_check 复验。
    ⛔ PRAGMA 在事务内是 no-op（1001O seg2 的 FAIL 根因）：try 内 commit、except 里
    rollback 之后，finally 再重开。
    """
    if _role_check_allows_dept_manager(conn):
        return
    conn.commit()  # 事务外才能切 PRAGMA
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE hr_account_new (
                id TEXT PRIMARY KEY NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'hr'
                    CHECK (role IN ('hr', 'interviewer', 'dept_manager')),
                department TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO hr_account_new (id, username, password_hash, password_salt, role, department, created_at) "
            "SELECT id, username, password_hash, password_salt, role, department, created_at FROM hr_account"
        )
        conn.execute("DROP TABLE hr_account")
        conn.execute("ALTER TABLE hr_account_new RENAME TO hr_account")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(f"hr_account 重建后外键不一致: {violations}")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # executescript 里的 INSERT OR IGNORE 种子行会打开一个隐式事务；PRAGMA
    # foreign_keys 只在事务外生效（_rebuild_stage_table 依赖它），先提交关掉。
    conn.commit()
    _migrate_stage_offer_hired(conn)
    # 新库走 CREATE TABLE 就已经带全新列，这里是空转；老库（.51 的 demo.db）
    # 靠这一步补列。两条路径的结果必须一致，由 tests/test_db_migration.py 的
    # test_fresh_and_migrated_schemas_have_identical_columns 守着。
    apply_column_migrations(conn)
    # 先补列、再放宽 CHECK：排期包（1001R）给 role 落的是两值 CHECK，
    # _ADDED_COLUMNS 因「列已存在」静默跳过，dept_manager 会被旧 CHECK 拒。
    # 新库 SCHEMA 本就是三值 ⇒ 空转。
    _rebuild_hr_account_role_check(conn)
    _migrate_stage_for_interview(conn)
    _seed_onboarding_default_template(conn)
    conn.commit()
