"""名单内／外的分支接线（4.10）。

本章只做三件事：**归档两条分支都做**、**名单外回一条礼貌说明**、
**⛔ 队列条目数不变**。入队是第 5 章的事，本文件有一条 AST 断言把这条钉死。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import tools.liaison.inbound as inbound_module
from tools.liaison.archive import InboundAttachment
from tools.liaison.inbound import (
    POLITE_NOTICE,
    compute_inbound_route,
    handle_inbound_message,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:30:00+08:00"
ADMITTED_USERID = "tanglp"
OUTSIDER_USERID = "someone-else"


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


@pytest.fixture
def roster(tmp_path):
    """一份只含 ADMITTED_USERID 的名单文件。

    ⛔ 不依赖仓库里那份真实的 `config/whitelist.yaml`——它的内容会随 D2 的
    名单变更而变，把测试绑在上面等于让"改名单"顺手打红一批用例。
    """
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        f"  - userid: {ADMITTED_USERID}\n"
        "    name: 汤丽萍\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    return path


class ReplySpy:
    """记账用的 reply port。真实通道在第 6／7 章，本章只认这个可调用契约。"""

    def __init__(self):
        self.calls = []

    def __call__(self, thread_id: str, text: str) -> None:
        self.calls.append((thread_id, text))


def _handle(conn, root, roster, *, sender, msgid="m1", thread_id=None, reply=None, payload=None):
    return handle_inbound_message(
        conn,
        thread_id=thread_id or sender,
        msgid=msgid,
        sender_userid=sender,
        received_at=RECEIVED_AT,
        msgtype="file" if payload is not None else "text",
        content="材料在这里",
        attachment=InboundAttachment(filename="表.xlsx", payload=payload) if payload is not None else None,
        archive_root=root,
        whitelist_path=roster,
        reply=reply,
    )


def _task_count(conn):
    return conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0]


def _message_count(conn):
    return conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────
# 纯函数：路由决策
# ─────────────────────────────────────────────────────────────────────────


def test_route_for_an_admitted_sender():
    route = compute_inbound_route(True)
    assert (route.admitted, route.should_enqueue, route.reply_text) == (True, True, None)


def test_route_for_an_outsider():
    route = compute_inbound_route(False)
    assert route.admitted is False
    assert route.should_enqueue is False, "⛔ 名单外不得生成队列条目"
    assert route.reply_text == POLITE_NOTICE


def test_polite_notice_is_marked_as_automated_and_leaks_no_roster():
    """回复必须让人看出这是自动发的（⛔ 不能被误以为是 Shao Peishen 本人回的），
    且 ⛔ 不得透露名单里有谁（那是不必要的个人信息披露）。
    """
    assert "自动" in POLITE_NOTICE
    assert "Shao Peishen" in POLITE_NOTICE
    assert "汤丽萍" not in POLITE_NOTICE and ADMITTED_USERID not in POLITE_NOTICE


def test_compute_inbound_route_is_pure():
    import inspect

    tree = ast.parse(inspect.getsource(compute_inbound_route))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not (called & {"open", "now", "today", "getenv", "admit", "print"}), called


# ─────────────────────────────────────────────────────────────────────────
# 4.10 / whitelist spec「名单外消息只归档并礼貌回复」
# ─────────────────────────────────────────────────────────────────────────


def test_outsider_message_is_archived_replied_and_never_enqueued(conn, root, roster):
    """spec Scenario「名单外成员发消息」：归档 + 礼貌回复 + ⛔ 零队列条目。"""
    spy = ReplySpy()
    before = _task_count(conn)

    result = _handle(conn, root, roster, sender=OUTSIDER_USERID, reply=spy)

    assert result.route.admitted is False
    assert _message_count(conn) == 1, "名单外的消息**仍然要归档**（事后可查谁发过什么）"
    assert spy.calls == [(OUTSIDER_USERID, POLITE_NOTICE)]
    assert _task_count(conn) == before == 0, "⛔ 队列条目数必须不变"
    assert result.replied is True
    assert_effect_log_identity(conn)


def test_outsider_group_message_behaves_the_same(conn, root, roster):
    """spec Scenario「名单外的群消息」：行为与私聊一致——只归档、不入队。

    群聊的 thread_id 取 chatid（design D3），发送人仍是 userid。
    """
    spy = ReplySpy()
    result = _handle(
        conn, root, roster, sender=OUTSIDER_USERID, thread_id="wrkSHat_chat_001", reply=spy
    )

    assert result.route.should_enqueue is False
    assert _message_count(conn) == 1
    assert _task_count(conn) == 0
    assert spy.calls == [("wrkSHat_chat_001", POLITE_NOTICE)]
    assert_effect_log_identity(conn)


def test_outsider_attachment_is_still_archived_and_retrievable(conn, root, roster):
    """名单外也归档材料——"谁在什么时候发过什么"要可查。"""
    payload = b"\x00\x01\x02outsider"
    result = _handle(conn, root, roster, sender=OUTSIDER_USERID, payload=payload, reply=ReplySpy())

    stored = result.outcome.attachments[0]
    assert (root / stored.relative_path).read_bytes() == payload
    assert _task_count(conn) == 0


def test_admitted_message_is_archived_without_a_reply(conn, root, roster):
    """名单内不发礼貌回复——那条文案的意思是"你不在名单里"，发错人是骚扰。"""
    spy = ReplySpy()
    result = _handle(conn, root, roster, sender=ADMITTED_USERID, reply=spy)

    assert result.route.admitted is True
    assert result.route.should_enqueue is True, "第 5 章据此入队"
    assert spy.calls == []
    assert _message_count(conn) == 1
    assert_effect_log_identity(conn)


def test_distinct_admitted_messages_each_enqueue_exactly_once(conn, root, roster):
    """第 5 章接线后的更新版："此处只接线并加断言'队列条目数不变'"这条 4.10 遗留断言
    已随入队接通改为反向——名单内每条不同的消息都应各自入队一次。

    （原断言"本章跑完队列必须还是空的"是 4.10 阶段的钉子，第 5 章的入队接线
    正是要让它变红——这条测试把它换成新阶段该有的样子，而不是删掉覆盖率。）
    """
    for n in range(3):
        _handle(conn, root, roster, sender=ADMITTED_USERID, msgid=f"m{n}", reply=ReplySpy())

    assert _message_count(conn) == 3
    assert _task_count(conn) == 3
    assert_effect_log_identity(conn)


def test_replaying_an_outsider_message_does_not_reply_twice(conn, root, roster):
    """重投同一 msgid ⇒ 归档幂等命中 ⇒ ⛔ 不重复发回复。

    判据挂在 `newly_archived` 上，而不是"查一下台账里有没有"——后者是另一次
    查询、另一个时刻，并发下会两条都发出去。
    """
    spy = ReplySpy()
    first = _handle(conn, root, roster, sender=OUTSIDER_USERID, msgid="m1", reply=spy)
    second = _handle(conn, root, roster, sender=OUTSIDER_USERID, msgid="m1", reply=spy)

    assert first.replied is True
    assert second.replied is False
    assert len(spy.calls) == 1
    assert _message_count(conn) == 1
    assert _task_count(conn) == 0
    assert_effect_log_identity(conn)


def test_missing_reply_port_is_logged_not_silently_dropped(conn, root, roster, caplog):
    """没接通道时（第 7 章之前）必须留下痕迹，⛔ 不许静默吞掉。"""
    import logging

    with caplog.at_level(logging.WARNING, logger=inbound_module.__name__):
        result = _handle(conn, root, roster, sender=OUTSIDER_USERID, reply=None)

    assert result.replied is False
    assert _message_count(conn) == 1, "回复发不出去 ⛔ 不影响归档"
    assert any("礼貌回复" in record.message for record in caplog.records)


def test_a_failing_reply_port_does_not_undo_the_archive(conn, root, roster, caplog):
    """回复通道炸了 ⇒ 记 ERROR 继续，⛔ 不许把已经归档的材料回滚掉。"""
    import logging

    def boom(thread_id, text):
        raise RuntimeError("通道断了")

    with caplog.at_level(logging.ERROR, logger=inbound_module.__name__):
        result = _handle(conn, root, roster, sender=OUTSIDER_USERID, reply=boom)

    assert result.replied is False
    assert _message_count(conn) == 1
    assert _task_count(conn) == 0
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_broken_roster_file_falls_closed_to_outsider(conn, root, tmp_path):
    """名单文件坏了 ⇒ 全部判为名单外 ⇒ 只归档不入队（第 3 章的 fail-closed 贯通到本章）。"""
    broken = tmp_path / "broken.yaml"
    broken.write_text("members: [unclosed", encoding="utf-8")
    spy = ReplySpy()

    result = _handle(conn, root, broken, sender=ADMITTED_USERID, reply=spy)

    assert result.route.admitted is False
    assert _task_count(conn) == 0
    assert len(spy.calls) == 1


# ─────────────────────────────────────────────────────────────────────────
# 结构性断言：本章 ⛔ 不实现入队
# ─────────────────────────────────────────────────────────────────────────


def test_admitted_message_is_archived_and_enqueued(conn, root, roster):
    """4.10 + 5.1：名单内 ⇒ 归档 + 入队，且两者都只发生一次。"""
    result = handle_inbound_message(
        conn,
        thread_id=ADMITTED_USERID,
        msgid="msg-1",
        sender_userid=ADMITTED_USERID,
        received_at=RECEIVED_AT,
        msgtype="text",
        content="报价单已发",
        archive_root=root,
        whitelist_path=roster,
    )

    assert result.route.should_enqueue is True
    assert result.enqueued is True
    rows = conn.execute("SELECT msgid, summary, send_status FROM liaison_task").fetchall()
    assert rows == [("msg-1", "报价单已发", "pending")]


def test_outsider_message_is_archived_but_never_enqueued(conn, root, roster):
    """4.10 逐字：名单外只归档 + 礼貌回复，⛔ 不生成任何队列条目。"""
    result = handle_inbound_message(
        conn,
        thread_id=OUTSIDER_USERID,
        msgid="msg-2",
        sender_userid=OUTSIDER_USERID,
        received_at=RECEIVED_AT,
        msgtype="text",
        content="你好",
        archive_root=root,
        whitelist_path=roster,
        reply=lambda *_: None,
    )

    assert result.route.should_enqueue is False
    assert result.enqueued is False
    assert _task_count(conn) == 0
    assert _message_count(conn) == 1


def test_enqueue_happens_even_when_the_archive_was_an_idempotent_hit(conn, root, roster):
    """🔴 本章最重要的一条回归。

    模拟"归档已提交、入队之前进程被杀"：先只跑归档，再走完整入站。
    第二次归档会幂等命中（`newly_archived is False`）；如果入队跟着这个布尔值走，
    这条待办就**永远不会出现**且毫无症状。断言它照样入队。
    """
    from tools.liaison.archive import archive_message

    archive_message(
        conn,
        thread_id=ADMITTED_USERID,
        msgid="msg-1",
        sender_userid=ADMITTED_USERID,
        received_at=RECEIVED_AT,
        msgtype="text",
        content="报价单已发",
        archive_root=root,
    )
    assert _task_count(conn) == 0

    result = handle_inbound_message(
        conn,
        thread_id=ADMITTED_USERID,
        msgid="msg-1",
        sender_userid=ADMITTED_USERID,
        received_at=RECEIVED_AT,
        msgtype="text",
        content="报价单已发",
        archive_root=root,
        whitelist_path=roster,
    )

    assert result.outcome.newly_archived is False, "前置没造对：这次归档应当是幂等命中"
    assert result.enqueued is True, (
        "归档幂等命中时没有入队——这条待办已经永久丢失。⛔ 入队不许用 newly_archived 做门槛"
    )
    assert _task_count(conn) == 1


def test_redelivering_the_same_message_twice_yields_exactly_one_task(conn, root, roster):
    """SDK 重连重投：待办 ⛔ 不许变成两条。"""
    for _ in range(2):
        handle_inbound_message(
            conn,
            thread_id=ADMITTED_USERID,
            msgid="msg-1",
            sender_userid=ADMITTED_USERID,
            received_at=RECEIVED_AT,
            msgtype="text",
            content="报价单已发",
            archive_root=root,
            whitelist_path=roster,
        )
    assert _task_count(conn) == 1


def test_inbound_module_never_commits_by_itself():
    """提交由 `idempotent_effect` 独占（第 2 章的单一事务管理者约束）。"""
    source = pathlib.Path(inbound_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback")
    ]
    assert offenders == [], f"inbound.py 里出现了 commit/rollback，行号 {offenders}"
