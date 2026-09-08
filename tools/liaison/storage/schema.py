"""`data/liaison.db` 的全部 DDL。

DDL 以字面量常量的形式集中在本文件，**不散落在建表函数里**：Task 1 的同构断言要把
`EFFECT_LOG_SCHEMA` 单独建进一个内存库去和产品库比对，散在函数里就比不了。
"""

from __future__ import annotations

#: 与 `app/storage/db.py:59-67` **逐字同构**。同列、同类型、同 NOT NULL、同主键、
#: 同唯一索引——由 tests/test_liaison_schema.py 的同构断言机器判定。
#:
#: ⛔ 改这段之前先想清楚：产品库那份是不是也要改？两边分叉不会报错，
#: 只会让"复用同一个 idempotent_effect"这句话在某天悄悄变成假的。
EFFECT_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_log_key ON effect_log (effect_key);
"""

#: `send_status` 的三个合法取值。**存的是这三个 ASCII 枚举值**，
#: emoji（🆕 待发 / ⏸ 暂缓 / ✅ 已推送）只允许出现在第 5 章的 Markdown 渲染层。
#: 语义照搬参考服务已验证的三态（design D11），存储形态不照搬。
SEND_STATUS_PENDING = "pending"
SEND_STATUS_DEFERRED = "deferred"
SEND_STATUS_PUSHED = "pushed"
SEND_STATUSES = (SEND_STATUS_PENDING, SEND_STATUS_DEFERRED, SEND_STATUS_PUSHED)

MESSAGE_AND_TASK_SCHEMA = """
-- 消息台账。**主键是 msgid**（企微协议内单条消息的唯一标识），日期只用于分目录
-- 与排序，⛔ 绝不作为键的最细一级——参考服务的生产 bug「归档覆盖」（同人同天多条
-- 消息互相覆盖）正是把日期当了键（design D4）。
CREATE TABLE IF NOT EXISTS liaison_message (
    msgid TEXT PRIMARY KEY,
    -- 来源会话：私聊取 userid，群聊取 chatid。这同时是幂等键的 thread_id 分量。
    thread_id TEXT NOT NULL,
    sender_userid TEXT NOT NULL,
    received_at TEXT NOT NULL,
    msgtype TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    -- 附件清单（JSON 数组）。**第 2 章只建列不写入**：附件的字节流落盘、fsync +
    -- 原子 rename、SHA-256 校验全在第 4 章（design D4）。默认 '[]' 表示"这条消息
    -- 没有附件"，⛔ 不要拿它表示"附件还没处理"——那两件事必须能区分开。
    attachments_json TEXT NOT NULL DEFAULT '[]',
    archived_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_liaison_message_thread
    ON liaison_message (thread_id, received_at);

-- 队列真身（design D11）。Markdown 只是从这张表渲染出来的只读导出物，
-- ⛔ 人在导出文件里改的任何东西都不回写这里。
CREATE TABLE IF NOT EXISTS liaison_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- UNIQUE + FK 一起用：UNIQUE 保证"一条消息最多一条队列条目"（重复投递的结构性
    -- 保险，与幂等装饰器互为两道防线）；FK 保证"条目必然指向一条真实存在的消息"，
    -- 即 spec 的「条目缺少来源信息不允许写入」。
    -- ⚠️ FK 只在连接开了 PRAGMA foreign_keys = ON 时生效，见 storage/db.py。
    msgid TEXT NOT NULL UNIQUE REFERENCES liaison_message (msgid),
    thread_id TEXT NOT NULL,
    sender_userid TEXT NOT NULL,
    received_at TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    -- 三态钉死在存储层。⛔ 存 emoji 就等于把展示层的文案变成数据契约。
    send_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (send_status IN ('pending', 'deferred', 'pushed')),
    pushed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 「已推送必带时间戳」与「未推送必不带时间戳」一条表约束同时钉住。
    -- 写成等式而不是两条 CHECK：两个方向必须同时成立，拆开写容易只加一半。
    CHECK ((send_status = 'pushed') = (pushed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_liaison_task_status
    ON liaison_task (send_status, received_at);

-- 「暂缓」只能由「待发」转入。CHECK 表达不了状态**转移**（它看不到旧值），
-- 所以用 BEFORE UPDATE 触发器。⛔ 不要只在业务层判——业务层可以被绕过，
-- 而这张表是队列的真身，它自己必须拒绝非法转移。
CREATE TRIGGER IF NOT EXISTS trg_liaison_task_defer_only_from_pending
BEFORE UPDATE OF send_status ON liaison_task
FOR EACH ROW WHEN NEW.send_status = 'deferred' AND OLD.send_status <> 'pending'
BEGIN
    SELECT RAISE(ABORT, 'send_status: deferred may only be entered from pending');
END;
"""

#: 本服务的全量 DDL。
SCHEMA = EFFECT_LOG_SCHEMA + MESSAGE_AND_TASK_SCHEMA
