import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply_column_migrations(conn: sqlite3.Connection) -> list[str]:
    """
    幂等加列：逐列独立判断、缺哪列补哪列，返回本次真的加上的列名。

    逐列独立是刻意的（design.md 风险表「服务器 SQLite 加列失败或部分成功」）：
    一列失败不影响其余列，重跑一次会把上次没加上的补齐。
    """
    added: list[str] = []
    for table, column, ddl in _ADDED_COLUMNS:
        if column in _existing_columns(conn, table):
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        added.append(column)
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
