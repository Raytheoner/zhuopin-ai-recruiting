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

import json
import pathlib
import sqlite3
from dataclasses import dataclass

from app.storage.idempotency import idempotent_effect
from tools.liaison.archive import DEFAULT_ARCHIVE_ROOT
from tools.liaison.attachments import StoredAttachment
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


class _TaskAlreadyPushed(Exception):
    """内部信号：这条队列条目已经是「已推送」，本次 UPDATE 一行都没改。

    ⛔ 不外泄给调用方——`mark_task_pushed` 把它翻成 `False`（幂等命中）。
    做成异常而不是返回值，是因为它必须从 `@idempotent_effect` 的**函数体里**
    抛出来才能触发装饰器的回滚：返回一个"什么都没做"的值，装饰器会照常
    写下 `effect_log` 行并提交，`effect_mark_task_pushed` 就变成 2 行——
    正是 TD-24 要堵的那个形状。
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
        # 语义判据，⛔ 不匹配错误文案——SQLite 的消息措辞不是契约
        # （`app/storage/idempotency.py` 同一句注释）。
        #
        # ⚠️ 判据不能查 `liaison_task` 本身：这个 except 分支可能是在
        # `effect_enqueue_task` 的业务 INSERT **已经成功、只是还没提交**的情况下
        # 进来的（例如 `effect_log` 撞键、随后 `idempotent_effect` 尝试回滚也失败——
        # 那种事故形态下这次调用自己刚写下的行仍留在这个连接自己的未提交事务里，
        # 同连接的 SELECT 会看见它自己的这行，把"我刚写坏的"误判成"早就提交好的"，
        # 原地重演一次同样的静默吞错）。
        #
        # `effect_log` 不会有这个问题：它是这条 INSERT 本身失败的那张表——无论是
        # 真撞键还是这里刻意制造的失败，本次调用自己都**没有**在 `effect_log` 里
        # 留下任何行（真撞键时没插进去，装饰器判定"命中"时也是探测到*别人*那行）。
        # 用 `node_name` + `business_key` 两列查，不用组合出来的 `effect_key` 字符串
        # ——这样查询天然不看 `thread_id`，与「一条消息最多一条队列条目」的判据
        # （不是「一个 thread_id 一条」）对齐，跨 thread_id 撞 `liaison_task.msgid`
        # UNIQUE 的第二道防线场景也能命中。
        already_applied = conn.execute(
            "SELECT 1 FROM effect_log WHERE node_name = 'effect_enqueue_task' AND business_key = ?",
            (msgid,),
        ).fetchone()
        if already_applied is not None:
            # 第二道防线命中：这条消息早就有队列条目了（可能是另一个 thread_id 投的，
            # 幂等键因此没命中；也可能是幂等键本身撞车）。这是**正常路径**，
            # ⛔ 不是异常——一条消息最多一条待办本来就是 schema 的契约。
            return False
        # 走到这里说明这次真的不是幂等命中。是否「缺来源消息」也用语义判据：
        # msgid 为空，或者 `liaison_message` 里根本没有这条——两者都直接对应
        # spec「条目缺少来源信息不允许写入」。其余任何完整性冲突（例如
        # `thread_id`/`sender_userid`/`received_at` 的 `NOT NULL`，或本函数上面
        # 那种 `effect_log` 回滚失败的真事故）都不是"缺来源"，⛔ 不许被这个分支
        # 误标——原样把 `exc` 抛给调用方，SQLite 自己的报文已经点名是哪一列。
        if not msgid or conn.execute(
            "SELECT 1 FROM liaison_message WHERE msgid = ?", (msgid,)
        ).fetchone() is None:
            raise MissingSourceMessage(
                f"队列条目缺少可回指的来源消息：msgid={msgid!r} thread_id={thread_id!r}；"
                f"⛔ 必须先归档再入队。原始约束：{exc}"
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


#: `effect_defer_task` 的幂等键里「代际号」与 `msgid` 的分隔符。
#:
#: 🔴 代际号在前、且是纯数字（不含本分隔符）⇒ 按**第一个** `#` 切分永远唯一，
#: 所以 `{代际}#{msgid}` 这个组合**不可能**与另一个 (代际, msgid) 组合撞车，
#: 无论真实 msgid 里含多少个 `#`。TD-22 那类「分隔符恰好出现在真实 msgid 里就
#: 静默串味」的坑在这个方向上不成立——⛔ 不要把顺序调成 `{msgid}#{代际}`，
#: 那个方向就撞得上（msgid `M#1` 与 msgid `M` 的第 1 代会得到同一把键）。
DEFER_GENERATION_SEPARATOR = "#"


def _defer_business_key(msgid: str, generation: int) -> str:
    """第 `generation` 次暂缓这条 msgid 所用的 `business_key`。"""
    return f"{generation}{DEFER_GENERATION_SEPARATOR}{msgid}"


def _next_defer_generation(conn: sqlite3.Connection, *, msgid: str) -> int:
    """这条 msgid **下一次**暂缓的代际号 ＝ 它此前成功暂缓过的次数。只读。

    🔴 **为什么暂缓的幂等键必须带代际号**（TD-23）：`pending → deferred` 是一条
    **可重复发生**的转移——第 6 章的「撤销暂缓」会把行改回 `pending`，之后再暂缓
    是完全合法的。而「一个 msgid 一把幂等键」只能表达"这件事发生过至少一次"，
    表达不了"这是第几次"。用它做键，第二次合法暂缓会被预检短路掉，
    **该 msgid 的暂缓从此再也做不成**，且报错指向错误的原因。
    代际号让每一次暂缓拿到自己的键：重放同一次暂缓仍然命中（键相同），
    一次**新的**暂缓则拿到新键、照常走到存储层触发器。

    ⚠️ 逐个代际**精确匹配**地探，⛔ 不用 `LIKE` / `GLOB` 前缀匹配：msgid 是外部
    来的字符串，里面的 `_` `%` 会被当通配符——那正是 TD-22 记的那个坑。
    循环次数 ＝ 这条 msgid 的暂缓次数，实际就是个位数。

    ⚠️ 查 `business_key` 而**不查** `effect_key`，因此天然不看 `thread_id`：
    「一条消息暂缓了几次」是 msgid 这个全局唯一业务身份的属性，不是某个会话的属性
    （与 `enqueue_task` 里那段"判据不看 thread_id"同一条理由，也是 TD-24 的教训）。
    """
    generation = 0
    while conn.execute(
        "SELECT 1 FROM effect_log WHERE node_name = 'effect_defer_task' AND business_key = ?",
        (_defer_business_key(msgid, generation),),
    ).fetchone() is not None:
        generation += 1
    return generation


@idempotent_effect("effect_defer_task")
def effect_defer_task(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, msgid: str
) -> str:
    """把一条队列条目转入「暂缓」。

    ⚠️ `business_key` 是 `{代际}#{msgid}`（见 `_next_defer_generation`），**不是**
    裸 msgid，所以要改哪一行由单独传进来的 `msgid` 决定。⛔ 不要从 `business_key`
    里反解 msgid——真实 msgid 可以含 `#`，反解在那一刻就开始静默改错行。

    ⛔ **本函数不判断旧状态。** 「暂缓只能来自待发」由
    `trg_liaison_task_defer_only_from_pending` 触发器拒绝——那是唯一真源。
    在这里再判一遍会制造第二份规则，两边分叉时**没有任何症状**。

    ⚠️ 本 effect 是 UPDATE，⛔ 永远不许登记进 `EFFECT_NODE_TO_TABLE`
    （见 `queue.py` 模块 docstring 与本计划判断 4）。
    """
    cursor = conn.execute(
        "UPDATE liaison_task SET send_status = 'deferred' WHERE msgid = ?", (msgid,)
    )
    _require_single_row(cursor, msgid=msgid, action="暂缓")
    return business_key


@idempotent_effect("effect_mark_task_pushed")
def effect_mark_task_pushed(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, pushed_at: str
) -> str:
    """把一条队列条目标记为「已推送」并写入推送时间戳。

    ⛔ **不在这里取当前时间。** 时间戳由调用方传入——effect 读时钟会让
    "同一输入产生同一结果"不再成立，测试只能靠 monkeypatch 才写得动。
    「已推送必带时间戳」由表上的等式 CHECK 拒绝，⛔ 本函数不预判。

    🔴 **`AND send_status <> 'pushed'` 是「第一次推送时刻」的第二道防线**（TD-24）。
    表上的三态 CHECK 与等式 CHECK 都拦不住 `pushed → pushed`：那一步在表层完全合法，
    只是把 `pushed_at` 换了个值。幂等键是第一道防线，但它带 `thread_id` 前缀，
    而队列条目的业务身份是全局唯一的 `msgid`——调用方换一个 `thread_id`
    （第 6 章按**推送目标群**取 thread_id 就会发生）幂等键就不命中了。
    `mark_task_pushed` 已经把 thread_id 改成从行里读回来堵住那条缝，本条件是它的
    兜底：即便哪天又有人从别的路径凑出一把新键，UPDATE 自己也一行都不会改。

    ⚠️ UPDATE 型 effect，⛔ 不登记进 `EFFECT_NODE_TO_TABLE`。
    """
    cursor = conn.execute(
        "UPDATE liaison_task SET send_status = 'pushed', pushed_at = ? "
        "WHERE msgid = ? AND send_status <> 'pushed'",
        (pushed_at, business_key),
    )
    if cursor.rowcount == 0:
        # 0 行有两种完全不同的成因，⛔ 不许混为一谈：条目根本不存在（真错，
        # `_require_single_row` 抛 `TaskNotFound`），还是它已经是「已推送」
        # （幂等命中，翻成 `False`）。不分开，一次正常的重复推送会被报成
        # "找不到队列条目"，排查会被带偏到完全错误的方向。
        current = conn.execute(
            "SELECT send_status FROM liaison_task WHERE msgid = ?", (business_key,)
        ).fetchone()
        if current is not None and current[0] == "pushed":
            raise _TaskAlreadyPushed(business_key)
    _require_single_row(cursor, msgid=business_key, action="标记已推送")
    return business_key


def _translate_transition_error(exc: sqlite3.IntegrityError, *, msgid: str, action: str):
    return TaskTransitionRejected(
        f"存储层拒绝了这次状态变更：{action} msgid={msgid!r}。"
        f"三态 CHECK / pushed_at 等式 CHECK / 暂缓转移触发器之一命中。原始约束：{exc}"
    )


def defer_task(conn: sqlite3.Connection, *, thread_id: str, msgid: str) -> None:
    """转入「暂缓」。成功即返回（无返回值）；非法转移一律抛异常，**从不返回 `False`**。

    ⚠️ 对一条已经是「暂缓」的条目再调一次会抛 `TaskTransitionRejected`
    （触发器的条件是 `OLD.send_status <> 'pending'`），⛔ 不许吞掉它——
    "暂缓只能来自待发"是 spec 的要求，不是可以体谅的边界情况。

    🔴 **⛔ 不从「幂等键命中」推断状态**（TD-23 还债，2026-09-09）。
    旧实现把命中幂等键当作"这条 msgid 已经离开过 pending"的证据直接拒绝。
    但**「离开过 pending」不等于「现在不在 pending」**：`deferred → pending`
    在 schema 上合法（触发器只在 `NEW.send_status='deferred'` 时触发），第 6 章的
    「撤销暂缓」正走这条路。于是一条被撤销过暂缓、此刻确实在 `pending` 的条目，
    再次暂缓**完全合法却被永久拒绝**，而且该 msgid 的暂缓从此再也做不成
    （幂等键永远命中），报错还声称原因是"此前已转入过暂缓"，排查被带偏。

    **两条还债动作里选了②「读状态」，⛔ 没选①「加 TRIGGER 禁止 deferred→pending」**，
    两条理由：① 那条 TRIGGER 会让第 6 章的「撤销暂缓」在存储层永远不可能实现——
    等于用删掉功能来消灭场景，而这个场景正是 TD-23 自己写明的触发场景；
    ② 它落在 `storage/schema.py`，不在本次还债的触碰区内。

    实现分两层，**判断状态的地方只有一处**：
      1. 幂等键带**代际号**（`_next_defer_generation`）⇒ 一次新的暂缓拿到新键、
         照常走到存储层触发器，由触发器裁定合法与否。**这一层不读状态。**
      2. 键仍然命中（只可能是并发下两条路径撞同一代际）⇒ 才读一次真状态，
         按真状态决定返回还是抛什么。这是"读状态"，⛔ 不是"从键反推状态"。

    ⛔ **仍然从不返回 `False`。** 非法转移一律抛——"暂缓只能来自待发"是 spec 的要求，
    不是可以体谅的边界情况。这与 `mark_task_pushed` 刻意不对称：那边的重复推送是
    "同一件事所以别再做"，这边的重复暂缓是"这个状态机形状不允许重复进入"。
    """
    business_key = _defer_business_key(msgid, _next_defer_generation(conn, msgid=msgid))
    try:
        applied = effect_defer_task(
            conn, thread_id=thread_id, business_key=business_key, msgid=msgid
        )
    except sqlite3.IntegrityError as exc:
        raise _translate_transition_error(exc, msgid=msgid, action="暂缓") from exc
    if applied is None:
        # 走到这里说明代际键仍被抢先占用 —— 只可能是另一条路径在本次
        # `_next_defer_generation` 与 effect 之间做完了同一代际的暂缓。
        # 🔴 ⛔ 不许再据此推断状态（那正是 TD-23 的病灶），读一次真状态。
        current = conn.execute(
            "SELECT send_status FROM liaison_task WHERE msgid = ?", (msgid,)
        ).fetchone()
        if current is None:
            raise TaskNotFound(f"暂缓 找不到队列条目：msgid={msgid!r}（命中 0 行）")
        if current[0] == "deferred":
            # 别人刚把它暂缓成功了，结果正是本次要的。⛔ 不抛：这次调用要达成的
            # 状态已经达成，抛异常只会让调用方以为暂缓没做成而去做补偿。
            return
        # ⚠️ 文案只陈述**读到的真状态**，⛔ 不从"键命中"反推任何结论——旧实现
        # 硬编码"此前已成功从待发转入过暂缓"，在 `pending→deferred→pushed→再 defer`
        # 路径下就是假话，排查会被带偏（TD-23 的附带缺陷）。
        raise TaskTransitionRejected(
            f"存储层拒绝了这次状态变更：暂缓 msgid={msgid!r}。"
            f"幂等键 {business_key!r} 已被占用（另一条路径抢先做完了同一代际的暂缓），"
            f"而这条条目当前的真实状态是 {current[0]!r}——本次暂缓未执行。"
        )


def mark_task_pushed(
    conn: sqlite3.Connection, *, thread_id: str, msgid: str, pushed_at: str
) -> bool:
    """标记「已推送」。返回 `True` 表示这次真的改了状态，`False` 表示幂等命中。

    幂等命中时 ⛔ 不覆盖已有时间戳——第一次推送的那个时刻才是事实。

    🔴 **`thread_id` 只是调用方的上下文，⛔ 不参与幂等键**（TD-24 还债，2026-09-09）。
    队列条目的业务身份是**全局唯一**的 `msgid`（`liaison_task.msgid UNIQUE`），
    而幂等键的形状是 `{thread_id}:{node}:{business_key}`。让调用方决定 thread_id，
    就等于让调用方决定"这次算不算重复"：msgid `M` 归档时 thread_id 是私聊
    (`u_tang`)、10:00 推送成功；第 6 章群通知重试时若按**推送目标群**取
    thread_id (`chat_xxx`) 再调一次，键不命中、UPDATE 真的执行，
    `pushed_at` 被静默改写成 11:30，`effect_log` 里多出一行。
    `effect_mark_task_pushed` 是 UPDATE 型、不进 `EFFECT_NODE_TO_TABLE`，
    铁律 1 的恒等断言抓不到它——**没有任何症状**。

    **两条还债动作里选了②「从任务行读回 thread_id」，⛔ 没选①「加 TRIGGER」**：
    ① 落在 `storage/schema.py`，不在本次还债的触碰区内；而且 ② 才是治本的那条——
    它让幂等键回到 msgid 这个真正的业务身份上，任何调用方都再也凑不出第二把键，
    ① 只是在凑出第二把键之后再拦一道。两者不互斥，① 的效果由
    `effect_mark_task_pushed` 里的 `AND send_status <> 'pushed'` 在**同一层**补上，
    合起来就是这条时间戳的两道防线。

    ⚠️ 参数 `thread_id` 保留是为了调用点的可读性（说明这次推送是在哪个会话上下文里
    发生的），⛔ 但它不再影响任何判定。删掉它会连累 `test_retention.py` 等调用点，
    而那些文件不在本次触碰区。
    """
    # 幂等键的 thread_id 从行里读回来，⛔ 不用入参。顺带把"条目不存在"提前到
    # 进 effect 之前——与 `_require_single_row` 同一个 `TaskNotFound` 结论，
    # 只是不必先让 UPDATE 空跑一趟。
    owner = conn.execute(
        "SELECT thread_id FROM liaison_task WHERE msgid = ?", (msgid,)
    ).fetchone()
    if owner is None:
        raise TaskNotFound(f"标记已推送 找不到队列条目：msgid={msgid!r}（命中 0 行）")
    try:
        applied = effect_mark_task_pushed(
            conn, thread_id=owner[0], business_key=msgid, pushed_at=pushed_at
        )
    except _TaskAlreadyPushed:
        # 第二道防线命中：这条已经是「已推送」，UPDATE 一行都没改，
        # `effect_log` 也没多出一行（装饰器已回滚）。这是**正常路径**。
        return False
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


@dataclass(frozen=True)
class TaskSource:
    """一条队列条目连同它回指到的来源消息与全部附件。

    spec「每条队列条目可回指来源消息」的取回形态。⛔ 不做懒加载：
    调用这个函数的场景是"点开一条待办看材料"，多一次往返换来的省略毫无意义，
    而懒加载会让"取得回来"这件事在调用点之外的某个时刻才失败。
    """

    task_id: int
    msgid: str
    thread_id: str
    sender_userid: str
    received_at: str
    send_status: str
    pushed_at: str | None
    summary: str
    msgtype: str
    content: str
    attachments: tuple[StoredAttachment, ...]

    def attachment_paths(
        self, *, archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT
    ) -> tuple[pathlib.Path, ...]:
        """把台账里的相对路径还原成可读的绝对路径。

        台账存的是**相对归档根**的路径（`attachments.StoredAttachment` 的注释）：
        归档根将来可能整体搬家，存绝对路径会让台账里所有的行一起失效。
        """
        return tuple(archive_root / item.relative_path for item in self.attachments)


def _parse_attachments(attachments_json: str) -> tuple[StoredAttachment, ...]:
    """把 `liaison_message.attachments_json` 解析成结构化附件清单。

    坏 JSON / 不是数组 / 缺字段 ⇒ 返回空元组，⛔ 不抛。取回材料和核对材料是
    两件事：核对由 `consistency.verify_ledger_against_archive` 负责报告不一致，
    在这里抛异常只会让"点开一条待办"变成一次崩溃。
    """
    try:
        entries = json.loads(attachments_json)
    except (TypeError, ValueError):
        return ()
    if not isinstance(entries, list):
        return ()
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            parsed.append(
                StoredAttachment(
                    filename=entry["filename"],
                    relative_path=entry["relative_path"],
                    byte_length=entry["byte_length"],
                    sha256=entry["sha256"],
                )
            )
        except KeyError:
            continue
    return tuple(parsed)


def load_task_source(conn: sqlite3.Connection, *, task_id: int) -> TaskSource | None:
    """按队列条目 id 取回它的来源消息与全部附件。找不到返回 `None`。

    只读，⛔ 不写任何东西。JOIN 而不是两次查询：外键保证消息一定在，
    分两次查会给"消息中途被删"这种本不可能的状态留一个分支。
    """
    row = conn.execute(
        "SELECT t.id, t.msgid, t.thread_id, t.sender_userid, t.received_at, "
        "       t.send_status, t.pushed_at, t.summary, m.msgtype, m.content, m.attachments_json "
        "FROM liaison_task AS t "
        "JOIN liaison_message AS m ON m.msgid = t.msgid "
        "WHERE t.id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return TaskSource(
        task_id=row[0],
        msgid=row[1],
        thread_id=row[2],
        sender_userid=row[3],
        received_at=row[4],
        send_status=row[5],
        pushed_at=row[6],
        summary=row[7],
        msgtype=row[8],
        content=row[9],
        attachments=_parse_attachments(row[10]),
    )
