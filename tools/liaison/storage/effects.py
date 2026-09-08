"""本服务全部有副作用的写库动作。

**每个有副作用的动作独占一个 `effect_*` 函数**（工程铁律 1、2）。计算类
（白名单判定、内容归一化、长度守卫）一律是别处的 `compute_*` 纯函数，
⛔ 不许混进本模块。

幂等键 = `{thread_id}:{node_name}:{business_key}`：
- `thread_id` = 消息来源会话标识（私聊取 `userid`，群聊取 `chatid`）
- `business_key` = 企微 `msgid`

⛔ **本模块不许出现任何 `conn.commit()` / `conn.rollback()`。** 提交由
`idempotent_effect` 独占——它在同一个隐式事务里写业务行与 `effect_log` 行然后
一次性提交。自己再提交一次，就是在制造第二个事务管理者，也就是
`docs/findings/2026-08-13-sqlite-事务归属冲突.md` 那场事故的成因。
本约束由 tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source 守着。

**范围**：本模块属第 2 章存储基座，只做"一条消息 → 一行台账 / 一行队列"的最小写入。
白名单准入（第 3 章）、附件落盘（第 4 章）、队列渲染与状态流转（第 5 章）
⛔ 都不在这里。
"""

from __future__ import annotations

import sqlite3

from app.storage.idempotency import idempotent_effect

#: 每个 effect 节点写的是哪张业务表。Task 4 的恒等不变式断言按它遍历，
#: 新增 effect 时**必须**同步登记——漏登记会让那个 effect 悄悄逃过恒等检查。
EFFECT_NODE_TO_TABLE = {
    "effect_archive_message": "liaison_message",
    "effect_enqueue_task": "liaison_task",
}


@idempotent_effect("effect_archive_message")
def effect_archive_message(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    sender_userid: str,
    received_at: str,
    msgtype: str,
    content: str = "",
    attachments_json: str = "[]",
) -> str:
    """把一条消息写进台账。`business_key` 即 `msgid`，也是台账主键。

    ⚠️ 调用方必须**先把附件落盘**再调本函数（design D3 钉死的顺序）。
    "文件已落、DB 行未写"是可收敛的中间态；"DB 行已写、文件缺失"是不可恢复的谎。
    附件落盘本身是第 4 章的事，本函数不做也不检查——它只负责写这一行。
    """
    conn.execute(
        "INSERT INTO liaison_message "
        "(msgid, thread_id, sender_userid, received_at, msgtype, content, attachments_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (business_key, thread_id, sender_userid, received_at, msgtype, content, attachments_json),
    )
    return business_key


@idempotent_effect("effect_enqueue_task")
def effect_enqueue_task(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    sender_userid: str,
    received_at: str,
    summary: str = "",
) -> int:
    """把一条消息变成一条值守任务条目。返回新条目的 rowid。

    `send_status` 走表默认值 `pending`，⛔ 不在这里显式传——默认值是表的契约，
    在调用点重复一遍只会制造两个真源。
    """
    cursor = conn.execute(
        "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at, summary) "
        "VALUES (?, ?, ?, ?, ?)",
        (business_key, thread_id, sender_userid, received_at, summary),
    )
    return cursor.lastrowid
