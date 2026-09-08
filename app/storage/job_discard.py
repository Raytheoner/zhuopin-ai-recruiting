from __future__ import annotations

import sqlite3

# ─────────────────────────────────────────────────────────────────────────────
# 丢弃一个"从未成立"的岗位（m1-job-profile-intake tasks 5.3）。
#
# 为什么是"落后即删"而不是"先判再建"：job_profile.job_id 有外键指向 job，
# 且连接上 PRAGMA foreign_keys = ON。而 is_job_related 要跑完 compute 才知道，
# 那时同一次 graph.invoke 里的 effect_persist_draft 已经要写 job_profile 了——
# 先判后建在结构上做不到，除非把建 job 行挪进 effect 节点（那要改
# app/graph/nodes.py，超出本交付单元边界）。
#
# ⛔ 不要改成"标一个 status 了事"。业务经理的岗位列表走 LEFT JOIN
# （app/storage/job_queries.py 的 latest_profile_rows，注释写明是刻意的），
# 任何留下来的 job 行都会真的出现在列表里；再加一个状态去过滤，就等于
# 把"没建岗位"实现成了"建了个看不见的岗位"——两者在 PIPL 的数据最小化
# 与业务经理的直觉上都不是一回事。
# ─────────────────────────────────────────────────────────────────────────────

# 删除顺序：子表先于父表。全库只有 job_profile 一条外键指向 job
# （app/storage/db.py:16），顺序错了会直接 IntegrityError。
_BUSINESS_DELETES: tuple[tuple[str, str], ...] = (
    ("job_profile", "job_id"),
    ("conversation", "thread_id"),
    ("outbox", "thread_id"),
    # effect_log 必须一起删。铁律1 的 reviewer 判据是"每个 effect_* 节点的
    # effect_log 条数与其业务表行数按 thread 恒等"；业务行没了、幂等记录还在，
    # 这条不变式当场破，且破得完全没有症状。
    ("effect_log", "thread_id"),
    ("job", "id"),
)

# ⛔ analysis_run **不在**上面这张表里，这是刻意的。那次模型调用真实发生过：
# 铁律3/4 要求每一次调用的模型标识、版本、prompt 版本、输入哈希、原始响应
# 可解释可审计，PIPL 第 24 条的说明权也建立在它上面。岗位可以当作从未成立，
# "我们调过一次模型"这个事实不可以。
#
# 留下来的这些行，job_id 今天是什么状态：intake 这条路径上从未把
# audit_context 传给网关（见 app/web/server.py:677-699、docs/tech-debt.md
# TD-1），所以它们的 job_id **是 NULL**，不是"指不到 job 的悬空 id"——
# 今天关联不回被丢弃的那一轮，是因为压根没关联，不是关联断了。
# 一旦 TD-1 第 ① 步落地、调用点接上 audit_context={"job_id": ...}，这些
# NULL 才会变成真正悬空的 job_id（analysis_run.job_id 是裸 TEXT、无外键，
# 指不到 job 不会报错）。**到那时**，任何把 analysis_run JOIN 到 job 的
# 报表都必须用外连接——内连接会把这些行悄悄丢掉而不报错，报表会悄悄少算。


def discard_unstarted_job(conn: sqlite3.Connection, job_id: str) -> None:
    """把这一轮为 ``job_id`` 写下的业务行整体抹掉，一个事务提交一次。

    只在"首轮就判定不是用人需求"这一种情形下调用。⛔ 不要用它删已经开始
    追问的岗位——那是"放弃"（effect_abandon_profile），语义完全不同：放弃
    要保留已采集内容并留痕，这里是让记录从未存在过。
    """
    for table, column in _BUSINESS_DELETES:
        conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (job_id,))
    conn.commit()


def discard_thread_checkpoints(saver, thread_id: str) -> None:
    """删掉 SqliteSaver 为该 thread 留下的 checkpoint 与 writes 行。

    ``saver`` 按鸭子类型使用：只碰它的 ``.lock`` 与 ``.conn``，⛔ 不 import
    langgraph——storage 层不该依赖编排框架。

    ⚠️ **⛔ 不要改成 saver.delete_thread()**：那个方法在
    ``langgraph-checkpoint-sqlite==2.0.6``（requirements.txt 钉死的版本）里
    是 ``raise NotImplementedError``，2026-09-08 对 venv 内实际安装版本反射
    确认过。这里直接对它自己那两张表发 DELETE，用的是库本身的加锁方式
    （``with saver.lock, saver.conn``）。

    表名改了就让它抛 sqlite3.OperationalError，⛔ 不要 try/except 兜住：
    静默空转的症状是 checkpoint 残留悄悄回来，而所有测试仍然是绿的。
    """
    with saver.lock, saver.conn:
        saver.conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        saver.conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
