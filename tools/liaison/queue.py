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

from app.storage.idempotency import idempotent_effect
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


def _require_single_row(cursor, *, msgid: str, action: str) -> None:
    """UPDATE 必须命中恰好一行，否则抛 `TaskNotFound`。

    🔴 SQLite 对匹配 0 行的 UPDATE **不报错**。不显式检查 rowcount，装饰器就会
    照常写下 `effect_log` 行并提交——这次"成功"是假的，而这个 msgid 的该动作
    从此**永远不会重试**（铁律 1 的失效方向）。
    在 `@idempotent_effect` 函数体里抛异常是安全的：装饰器会回滚并把异常原样抛出，
    半截写入与幂等记录都不会留下。
    """
    if cursor.rowcount != 1:
        raise TaskNotFound(f"{action} 找不到队列条目：msgid={msgid!r}（命中 {cursor.rowcount} 行）")


@idempotent_effect("effect_defer_task")
def effect_defer_task(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str
) -> str:
    """把一条队列条目转入「暂缓」。`business_key` 即 `msgid`。

    ⛔ **本函数不判断旧状态。** 「暂缓只能来自待发」由
    `trg_liaison_task_defer_only_from_pending` 触发器拒绝——那是唯一真源。
    在这里再判一遍会制造第二份规则，两边分叉时**没有任何症状**。

    ⚠️ 本 effect 是 UPDATE，⛔ 永远不许登记进 `EFFECT_NODE_TO_TABLE`
    （见 `queue.py` 模块 docstring 与本计划判断 4）。
    """
    cursor = conn.execute(
        "UPDATE liaison_task SET send_status = 'deferred' WHERE msgid = ?", (business_key,)
    )
    _require_single_row(cursor, msgid=business_key, action="暂缓")
    return business_key


@idempotent_effect("effect_mark_task_pushed")
def effect_mark_task_pushed(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, pushed_at: str
) -> str:
    """把一条队列条目标记为「已推送」并写入推送时间戳。

    ⛔ **不在这里取当前时间。** 时间戳由调用方传入——effect 读时钟会让
    "同一输入产生同一结果"不再成立，测试只能靠 monkeypatch 才写得动。
    「已推送必带时间戳」由表上的等式 CHECK 拒绝，⛔ 本函数不预判。

    ⚠️ UPDATE 型 effect，⛔ 不登记进 `EFFECT_NODE_TO_TABLE`。
    """
    cursor = conn.execute(
        "UPDATE liaison_task SET send_status = 'pushed', pushed_at = ? WHERE msgid = ?",
        (pushed_at, business_key),
    )
    _require_single_row(cursor, msgid=business_key, action="标记已推送")
    return business_key


def _translate_transition_error(exc: sqlite3.IntegrityError, *, msgid: str, action: str):
    return TaskTransitionRejected(
        f"存储层拒绝了这次状态变更：{action} msgid={msgid!r}。"
        f"三态 CHECK / pushed_at 等式 CHECK / 暂缓转移触发器之一命中。原始约束：{exc}"
    )


def defer_task(conn: sqlite3.Connection, *, thread_id: str, msgid: str) -> bool:
    """转入「暂缓」。返回 `True` 表示这次真的改了状态；**从不返回 `False`**。

    ⚠️ 对一条已经是「暂缓」的条目再调一次会抛 `TaskTransitionRejected`
    （触发器的条件是 `OLD.send_status <> 'pending'`），⛔ 不许吞掉它——
    "暂缓只能来自待发"是 spec 的要求，不是可以体谅的边界情况。

    🔴 **`effect_defer_task` 幂等命中（返回 `None`）时不能当无操作放过。**
    `idempotent_effect` 的预检在 `effect_key` 命中时会在 fn 函数体跑之前就短路
    返回——`effect_defer_task` 唯一会把这把 `{thread_id}:effect_defer_task:{msgid}`
    写进 `effect_log` 的路径是**上一次成功的 pending→deferred 转移**（转移失败时
    异常在 effect_log 写入之前就已回滚抛出，不会落这行）。换句话说，命中幂等键
    本身就等价于"这条 msgid 已经离开过 pending 状态"——再调一次在语义上必然是
    非法转移，不需要真的重放 UPDATE 让触发器确认一遍。
    这与 `mark_task_pushed` 刻意不同：那边的重复推送是"同一件事所以别再做"，
    这边的重复暂缓是"这个状态机形状本来就不允许重复进入"（同一份 `TaskTransitionRejected`
    docstring 已经写明这个不对称）。⛔ 不要为了让两者看起来对称而把这支
    `raise` 改成 `return False`。
    """
    try:
        applied = effect_defer_task(conn, thread_id=thread_id, business_key=msgid)
    except sqlite3.IntegrityError as exc:
        raise _translate_transition_error(exc, msgid=msgid, action="暂缓") from exc
    if applied is None:
        raise TaskTransitionRejected(
            f"存储层拒绝了这次状态变更：暂缓 msgid={msgid!r}。"
            "幂等键命中——这条队列条目此前已经成功从「待发」转入过「暂缓」，"
            "「暂缓只能来自待发」使得再次转入必然非法（未重放 UPDATE，"
            "命中幂等键本身即可推出结论）。"
        )
    return True


def mark_task_pushed(
    conn: sqlite3.Connection, *, thread_id: str, msgid: str, pushed_at: str
) -> bool:
    """标记「已推送」。返回 `True` 表示这次真的改了状态，`False` 表示幂等命中。

    幂等命中时 ⛔ 不覆盖已有时间戳——第一次推送的那个时刻才是事实。
    """
    try:
        applied = effect_mark_task_pushed(
            conn, thread_id=thread_id, business_key=msgid, pushed_at=pushed_at
        )
    except sqlite3.IntegrityError as exc:
        raise _translate_transition_error(exc, msgid=msgid, action="标记已推送") from exc
    return applied is not None


def list_tasks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """按接收时间升序列出全部队列条目。只读，⛔ 不写任何东西。

    显式设 `row_factory` 而不是要求调用方先设——渲染层按列名取值，
    位置索引在加列时会静默错位。
    """
    cursor = conn.execute(
        "SELECT id, msgid, thread_id, sender_userid, received_at, summary, "
        "send_status, pushed_at, created_at "
        "FROM liaison_task ORDER BY received_at, id"
    )
    cursor.row_factory = sqlite3.Row
    return cursor.fetchall()
