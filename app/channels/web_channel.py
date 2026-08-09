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
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def deliver(self, thread_id: str, message: OutboundMessage) -> None:
        self._conn.execute(
            "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, ?, ?)",
            (thread_id, message.type, json.dumps(message.payload, ensure_ascii=False)),
        )
        self._conn.commit()

    def latest(self, thread_id: str) -> OutboundMessage | None:
        row = self._conn.execute(
            "SELECT message_type, payload_json FROM outbox "
            "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        return OutboundMessage(type=row[0], payload=json.loads(row[1]))
