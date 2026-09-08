"""值守任务队列的业务层：入队、状态流转、条目回指材料。

**队列真身是 `liaison.db` 的 `liaison_task` 表**（design D11）。Markdown 只是
`queue_view.py` 渲染出的只读导出物，⛔ 系统不从那个文件读任何状态。

⛔ **本模块不做任何"内容清洗"**：不归一化竖线、不删换行、不截断正文。
参考服务需要那些是因为它拿 Markdown 表格当数据库；本形态下任意字符都只是列里的
一个字符串值，清洗只会静默改掉用户真正发来的字。转义在渲染层做（`escape_cell`）。

⛔ **本模块不许出现 `conn.commit()` / `conn.rollback()` / `conn.executescript()`。**
提交由 `idempotent_effect` 独占——它在同一个隐式事务里写业务行与 `effect_log` 行
然后一次性提交。自己再提交一次就是制造第二个事务管理者，也就是
`docs/findings/2026-08-13-sqlite-事务归属冲突.md` 那场事故的成因。

⛔ **本模块不许写 `with` 语句**（含 `contextlib.suppress`）：第 2 章的
`test_no_second_transaction_manager_in_source` 把 `tools/liaison/` 非测试代码里
任何 `with X:` 判为隐式提交违规，它认形状不认语义。
"""

from __future__ import annotations

import sqlite3

from tools.liaison.storage.effects import effect_enqueue_task

#: 摘要的默认长度上限（字符数，不是字节）。摘要只进 Markdown 视图与队列列表，
#: 不参与任何完整性校验，所以按字符截断即可——⛔ 不要在这里引入字节口径，
#: 那是第 6 章外发长度守卫的事，两个数字来源不同，共用等于埋一个静默截断。
DEFAULT_SUMMARY_MAX_CHARS = 120


class MissingSourceMessage(ValueError):
    """队列条目没有可回指的来源消息（`msgid` 为空，或库里根本没有那条消息）。

    spec「条目缺少来源信息不允许写入」的领域异常形态。真正拒绝它的是
    `liaison_task.msgid` 的 `NOT NULL` 与 `REFERENCES liaison_message (msgid)`，
    本异常只是把 sqlite 的 `IntegrityError` 翻译成调用方能分辨的东西。
    """


class TaskNotFound(LookupError):
    """要改状态的队列条目不存在。

    ⚠️ 这条异常存在的理由是：`UPDATE ... WHERE msgid = ?` 匹配 0 行时 SQLite
    **不报错**。不显式检查 `rowcount`，一次针对不存在条目的"推送标记"会安静成功、
    并且留下一条 `effect_log` 记录——从此这个 msgid 的推送标记**永远不会重试**。
    这正是铁律 1 那句"幂等本是防重复的保护，拆开后变成永久丢失的保证"。
    """


class TaskTransitionRejected(ValueError):
    """存储层拒绝了这次状态取值或状态转移。

    ⛔ 本模块**不预判**任何一条转移规则——三态 CHECK、`pushed_at` 等式 CHECK、
    `trg_liaison_task_defer_only_from_pending` 触发器都在 `storage/schema.py` 里，
    那是唯一真源（本计划判断 2）。本异常只是它的翻译层。
    """


def compute_task_summary(
    content: str, *, msgtype: str, max_chars: int = DEFAULT_SUMMARY_MAX_CHARS
) -> str:
    """纯函数（铁律 2 的形状）：把消息正文压成一行摘要。

    **机械截断，⛔ 不调用任何模型。** 本服务不做 AI 评分、不调 LLM（design D7），
    队列摘要更没有理由成为第一个例外——那会让一条待办的文字来源变得不可解释。

    ⛔ 不做竖线/换行清洗：原样保留，转义由渲染层负责（本模块 docstring）。
    """
    text = content if isinstance(content, str) else ""
    if not text.strip():
        # 图片/文件/语音这类没有正文的消息。写成 `[file]` 而不是空串——
        # 空摘要在队列视图里看不出这条待办到底是什么。
        return f"[{msgtype}]"
    if len(text) <= max_chars:
        return text
    # 截断标记占一个字符，保证返回长度恰好等于 max_chars。
    return text[: max_chars - 1] + "…"


def enqueue_task(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    received_at: str,
    summary: str = "",
) -> bool:
    """把一条已归档的消息变成一条值守任务。返回 `True` 表示这次真的新建了条目。

    ⚠️ **调用方必须先归档再入队**（`liaison_task.msgid` 外键指向 `liaison_message`）。
    顺序反了会直接撞外键——这不是额外校验，它就是 spec「条目缺少来源信息不允许写入」。

    🔴 **⛔ 不要用 `ArchiveOutcome.newly_archived` 给这次调用加门槛。**
    归档已提交、入队之前进程被杀，重投时归档幂等命中（`newly_archived is False`），
    跟着它走就意味着这条待办**永远不会出现**，而且没有任何症状。重复由本函数下面
    的幂等键与 UNIQUE 两道防线挡住，代价只是一次空转。
    （礼貌回复相反——那条该跟 `newly_archived` 走，重发是骚扰。丢一条告知可接受，
    丢一条待办不可接受。）
    """
    try:
        applied = effect_enqueue_task(
            conn,
            thread_id=thread_id,
            business_key=msgid,
            sender_userid=sender_userid,
            received_at=received_at,
            summary=summary,
        )
    except sqlite3.IntegrityError as exc:
        detail = str(exc)
        if "UNIQUE" in detail:
            # 第二道防线命中：这条消息早就有队列条目了（可能是另一个 thread_id 投的，
            # 幂等键因此没命中）。这是**正常路径**，⛔ 不是异常——一条消息最多一条待办
            # 本来就是 schema 的契约。装饰器已经回滚，库里没有半截写入。
            return False
        if "NOT NULL" in detail or "FOREIGN KEY" in detail:
            raise MissingSourceMessage(
                f"队列条目缺少可回指的来源消息：msgid={msgid!r} thread_id={thread_id!r}；"
                f"⛔ 必须先归档再入队。原始约束：{detail}"
            ) from exc
        raise
    return applied is not None
