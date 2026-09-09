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

#: 第 7 章·连接生命周期。窗口的键是**起始时间**——7.3 的幂等策略「按窗口起始时间
#: 去重」在这里有两道防线：主键（结构）与 idempotent_effect 的幂等键（机制），
#: 与第 2 章 liaison_task 的 UNIQUE + 装饰器同一手法。
#:
#: ⛔ 不要把 recovered_at 设成 NOT NULL DEFAULT ''：本表全靠 `recovered_at IS NULL`
#: 表达"这个窗口还没闭合"，空串会让"未闭合"与"闭合于空时间"两件事无法区分，
#: 而"未闭合"正是 7.3 启动期补记要找的那批行。
OUTAGE_WINDOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS liaison_outage_window (
    -- 窗口起始时间（ISO8601、带 +08:00、精确到微秒），同时是幂等键的 business_key。
    started_at TEXT PRIMARY KEY,
    -- 恒等分组用。连接不是一个会话，取固定哨兵值 __liaison_connection__。
    thread_id TEXT NOT NULL,
    detected_by TEXT NOT NULL
        CHECK (detected_by IN ('disconnect_event', 'startup_gap')),
    recovered_at TEXT,
    closed_by TEXT
        CHECK (closed_by IS NULL OR closed_by IN ('reconnect', 'startup_backfill')),
    -- 告警**送出成功**后才落。⛔ 闭窗时不许顺手填——填了就等于宣称一条可能
    -- 根本没送出去的告警已经送到了，而这正是本章要消灭的那类静默缺口。
    alerted_at TEXT,
    -- 「闭合」必须两列同时有值。写成等式而不是两条 CHECK：拆开写容易只加一半。
    CHECK ((recovered_at IS NULL) = (closed_by IS NULL)),
    -- 没闭合的窗口不可能已经告警过（告警文本必须含恢复时间）。
    CHECK (alerted_at IS NULL OR recovered_at IS NOT NULL)
);

-- 启动期补记要找 recovered_at IS NULL 的行；补发告警要找 alerted_at IS NULL 的行。
CREATE INDEX IF NOT EXISTS idx_liaison_outage_open
    ON liaison_outage_window (recovered_at, started_at);
"""

#: 第 6 章·群通知台账。**一次通知恰好一行**，三种终局共用同一张表。
#:
#: ⛔ 不要拆成"成功表 + 待重发表"：拆开之后铁律 1 的恒等式
#: 「`effect_log` 条数 == 业务表行数」就跨了两张表，需要求和才成立；等式一旦需要
#: 求和，下一次有人加第四种终局时它会因为一个完全正当的理由变红，而那时最顺手的
#: "修法"就是削弱等式本身。
#:
#: 主键是 **(thread_id, 内容摘要)** 复合键——与幂等键
#: `{thread_id}:effect_send_group_notify:{digest}` **同域**。
#:
#: ⛔ 不要退回成 `digest TEXT PRIMARY KEY`：那样结构防线是全表唯一、机制防线按
#: thread 分域，两道防线口径不一致。后果不是"多挡一次"而是真丢账：同内容换一个
#: thread_id 再来时，幂等预检不命中 ⇒ `deliver()` **真的把消息发进群** ⇒ INSERT
#: 撞主键 ⇒ 整个事务回滚 ⇒ 群里多一条通知，而台账与 effect_log 各 0 行。恒等式
#: 因为 `0 == 0` 仍然是绿的，机器判据抓不到——正是本章要消灭的那类谎。
#: tests/test_notify_store.py::test_same_content_on_two_threads_each_writes_its_own_row
#: 守着这条。
GROUP_NOTIFY_SCHEMA = """
CREATE TABLE IF NOT EXISTS liaison_group_notify (
    digest TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    -- 通道名。两条通道阈值独立（D9），台账里也要能分得出这行是哪条通道发的。
    channel TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('sent', 'pending_resend', 'rejected')),
    mode TEXT NOT NULL
        CHECK (mode IN ('direct', 'degraded', 'reject')),
    byte_length INTEGER NOT NULL,
    limit_bytes INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_errcode INTEGER,
    last_error TEXT,
    -- **完整原文**。⛔ 存的绝不是提要——待重发靠这一列重发，存提要等于把
    -- "降级"变成"丢内容"，而那正是本章要消灭的东西。
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT,
    -- 「已送达必带时间戳」与「未送达必不带时间戳」一条等式同时钉住。
    -- 写成等式而不是两条 CHECK：两个方向必须同时成立，拆开写容易只加一半。
    CHECK ((state = 'sent') = (sent_at IS NOT NULL)),
    -- TD-28：上面几条 CHECK 只守**各字段各自的取值域**，跨字段的荒唐组合照样写得进去
    -- （登记时用直连 SQL 实证过：`state='sent'` + `mode='reject'`、
    -- `state='rejected'` + `mode='direct'` + `attempts=99`、`byte_length=-5` 全部成功）。
    -- 下面两条补的是跨字段与取值域的下界。
    --
    -- 「拒发」在状态与模式上是**同一件事的两个投影**，必须同真同假。同样写成等式而
    -- 不是两条单向 CHECK：只加一半的话，"rejected 但 mode=direct" 这种行仍然进得来，
    -- 而这张表是台账——一行"被拒发但已送达"会让所有基于它的报表悄悄说谎。
    CHECK ((state = 'rejected') = (mode = 'reject')),
    -- 长度与次数的下界。`limit_bytes` 是 **> 0** 而不是 >= 0：阈值为 0 意味着任何正文
    -- 都超限，那不是阈值是死锁。`byte_length = 0`（空正文）与 `attempts = 0`
    -- （还没发过）都是合法的，⛔ 不许顺手收成 > 0 误伤正路。
    CHECK (attempts >= 0 AND byte_length >= 0 AND limit_bytes > 0),
    -- 与幂等键 {thread_id}:effect_send_group_notify:{digest} 同域（见上）。
    PRIMARY KEY (thread_id, digest)
);

-- 第 8 章的重发驱动器要找 state='pending_resend' 的行。
CREATE INDEX IF NOT EXISTS idx_liaison_group_notify_state
    ON liaison_group_notify (state, created_at);
"""

#: 本服务的全量 DDL。
SCHEMA = (
    EFFECT_LOG_SCHEMA + MESSAGE_AND_TASK_SCHEMA + OUTAGE_WINDOW_SCHEMA + GROUP_NOTIFY_SCHEMA
)
