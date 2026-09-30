"""0930L·回推**成功之后**回写队列状态（`queue.mark_task_pushed` 的第一个生产调用点）。

**本文件守的是 0930K 那半条的闭环**：回推真投递成功（`liaison_group_notify.state='sent'`）
之后，那条待办的 `liaison_task.send_status` 必须从 `pending` 变成 `pushed`、并带上
`pushed_at`——否则队列视图（`queue_view`）会一直显示「未推送」，与群里那条真消息的事实相反。

三条判据（逐条对应 opener「二、回写判据」）：
① 终态 `sent` ⇒ 回写；② `rejected`（地址缺失）⇒ ⛔ 不回写；③ 同 `msgid` 重放 ⇒ 幂等，
`pushed_at` 不被改写、`effect_log` 不多出第二行。

⚠️ 全部用既有测试替身（`test_group_notify_relay` 的帧／relay 工厂 ＋
`test_notify_webhook` 的假传输），⛔ 一条真消息都不发；地址一律 `example.invalid` 占位。
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta

import pytest

from tools.liaison import session
from tools.liaison.config import GROUP_WEBHOOK_ENV
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.conftest import FAKE_WEBHOOK, FakeClock, RecordingSink
from tools.liaison.tests.test_group_notify_relay import (
    ADMITTED_USERID,
    GROUP_CHAT_ID,
    make_frame,
    make_ports,
    make_relay,
    run_frames,
)
from tools.liaison.tests.test_notify_webhook import FakeTransport

T0 = datetime(2026, 9, 30, 10, 0, 0, tzinfo=session.CHINA_TZ)

#: `queue.mark_task_pushed` 写下的排队回写点的 effect 节点名。
PUSHED_NODE = "effect_mark_task_pushed"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def svc(tmp_path):
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    return session.LiaisonSession(
        conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
    )


@pytest.fixture
def roster(tmp_path) -> pathlib.Path:
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        f"  - userid: {ADMITTED_USERID}\n"
        "    name: 汤丽萍\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    return path


def _task_status(conn, msgid: str):
    return conn.execute(
        "SELECT send_status, pushed_at FROM liaison_task WHERE msgid = ?", (msgid,)
    ).fetchone()


def _pushed_effect_rows(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?", (PUSHED_NODE,)
    ).fetchone()[0]


def test_a_sent_group_notify_marks_the_task_pushed(svc, roster, tmp_path, clock):
    """① 回推真投递成功（`state='sent'`）⇒ 队列行 `pushed` ＋ `pushed_at` 非空 ＋
    恰好多出一行 `effect_mark_task_pushed`。

    `pushed_at` 断言用 `session.format_instant(T0)`：与同一条消息的 `received_at`
    同源（本次事件那一刻）同格式（ISO8601 `+08:00`、微秒级），⛔ 不是自造的第三种字面量。
    """
    fake = FakeTransport([0])
    ports = make_ports(
        tmp_path,
        roster,
        group_notify=make_relay(clock, fake, env={GROUP_WEBHOOK_ENV: FAKE_WEBHOOK}),
    )

    run_frames(svc, ports, [(T0, make_frame(chattype="group", msgid="MSG-P-1"))])

    notify_state = svc.conn.execute(
        "SELECT state FROM liaison_group_notify"
    ).fetchall()
    assert notify_state == [("sent",)], "回推终态是 sent，才谈得上回写"
    assert _task_status(svc.conn, "MSG-P-1") == ("pushed", session.format_instant(T0))
    assert _pushed_effect_rows(svc.conn) == 1, "回写恰好一行 effect_mark_task_pushed"


def test_a_rejected_group_notify_leaves_the_task_pending(svc, roster, tmp_path, clock):
    """② 地址缺失 ⇒ 回推 `rejected`：队列行⛔ 仍 `pending`、`pushed_at` 仍 NULL、
    ⛔ 没有 `effect_mark_task_pushed` 行——「发不出去」必须是看得见的。"""
    fake = FakeTransport([0])
    ports = make_ports(
        tmp_path, roster, group_notify=make_relay(clock, fake, env={})
    )

    run_frames(svc, ports, [(T0, make_frame(chattype="group", msgid="MSG-P-2"))])

    notify_state = svc.conn.execute("SELECT state FROM liaison_group_notify").fetchall()
    assert notify_state == [("rejected",)], "缺地址走拒发分支"
    assert _task_status(svc.conn, "MSG-P-2") == ("pending", None), "拒发⛔ 不许回写成 pushed"
    assert _pushed_effect_rows(svc.conn) == 0


def test_replaying_the_same_msgid_does_not_rewrite_pushed_at(svc, roster, tmp_path, clock):
    """③ 同 `msgid` 重放（重连重投）⇒ 幂等：`pushed_at` 保持第一次那个时刻、
    `effect_log` 不出现第二行——第一道防线是既有的幂等键，第二道是 `send_status <> 'pushed'`。"""
    fake = FakeTransport([0])
    ports = make_ports(
        tmp_path,
        roster,
        group_notify=make_relay(clock, fake, env={GROUP_WEBHOOK_ENV: FAKE_WEBHOOK}),
    )
    frame = make_frame(chattype="group", msgid="MSG-P-3")

    run_frames(
        svc,
        ports,
        [(T0, frame), (T0 + timedelta(minutes=3), frame)],
    )

    assert _task_status(svc.conn, "MSG-P-3") == ("pushed", session.format_instant(T0))
    assert _pushed_effect_rows(svc.conn) == 1, "重放⛔ 不许新增第二行 effect"
    # 待办与群通知台账都只有一条（既有幂等的回归）。
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert (
        svc.conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 1
    )
    # 会话标识取来源群 chatid——回写走的也是这批 thread_id 上下文。
    assert (
        svc.conn.execute(
            "SELECT thread_id FROM liaison_task WHERE msgid = 'MSG-P-3'"
        ).fetchone()[0]
        == GROUP_CHAT_ID
    )
