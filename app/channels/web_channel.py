from __future__ import annotations

import json
import sqlite3

from app.channels.base import OutboundMessage


class WebChannel:
    """
    第一个 Channel 实现。Web 是同步请求/响应，"投递"等价于写 outbox，
    HTTP handler 处理完请求后读 latest() 拿去当响应体。
    未来接企微时新增 WeComChannel，实现同样的 deliver/latest，
    graph 节点侧代码不需要改。

    deliver() 本身不 conn.commit()：写入的 outbox 行落在调用方持有的事务里，
    由调用方决定何时提交。graph 侧的 effect_deliver_message
    （app/graph/nodes.py）用 idempotent_effect 装饰，要求 outbox 的写入与
    effect_log 记录在同一个事务里由装饰器统一提交一次——这里如果自己先提交，
    一旦进程恰好在"这次提交"和"装饰器提交 effect_log"之间崩溃，重放时会
    在没有自然键约束的 outbox 上悄悄插入第二行，造成同一条消息被重复投递
    （工程铁律1要防的正是这个）。同一连接内先 deliver() 再 latest() 仍能读到
    刚写入但未提交的行（SQLite 同连接内可见自己的未提交写入），所以不依赖
    idempotent_effect 的直接调用方（如本文件的单元测试）不受影响。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def deliver(self, thread_id: str, message: OutboundMessage) -> None:
        self._conn.execute(
            "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, ?, ?)",
            (thread_id, message.type, json.dumps(message.payload, ensure_ascii=False)),
        )

    def latest(self, thread_id: str) -> OutboundMessage | None:
        row = self._conn.execute(
            "SELECT message_type, payload_json FROM outbox "
            "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        return OutboundMessage(type=row[0], payload=json.loads(row[1]))
