"""通道适配层：SDK `message` 帧 → `handle_inbound_message` 入参（2026-09-10 接线）。

本文件守两类东西：
1. **形状对得上时字段抠得对**——尤其 `thread_id`（归档目录与幂等键的分量，错了没症状）；
2. **形状对不上时炸得很响**——⛔ 不静默跳过、⛔ 不把取值写进报错。

⚠️ `channel.py` 的 `_BODY_*` 字段名表来自协议文档、**未经真实报文实测**。本文件因此
⛔ **不能**被当成"接线已验证"的证据：它证明的是"按这张表能跑通"，不是"这张表是对的"。
真实报文的核对在 8.6 灰度。
"""

from __future__ import annotations

import pytest

from tools.liaison import channel
from tools.liaison.storage import db as liaison_db

RECEIVED_AT = "2026-09-10T10:30:00+08:00"


def _frame(**body_overrides):
    body = {
        "msgid": "msg-0001",
        "msgtype": "text",
        "chatid": "wrkSHat_chat_001",
        "from": {"userid": "TangLiPing"},
        "text": {"content": "岗位需求：嵌入式工程师 2 人"},
    }
    for key, value in body_overrides.items():
        if value is _MISSING:
            body.pop(key, None)
        else:
            body[key] = value
    return {"headers": {"req_id": "r-1"}, "body": body}


_MISSING = object()


# ── 拆帧：形状对得上 ─────────────────────────────────────────────────────


def test_fields_are_pulled_from_the_documented_body_keys():
    fields = channel.compute_inbound_fields(_frame())
    assert fields.msgid == "msg-0001"
    assert fields.msgtype == "text"
    assert fields.sender_userid == "TangLiPing"
    assert fields.content == "岗位需求：嵌入式工程师 2 人"
    assert fields.carries_attachment is False


def test_thread_id_takes_chatid_because_the_sdk_defines_it_that_way():
    """`chatid` 单聊填 userid、群聊填群 id（SDK `send_message` docstring 逐字），
    与 design D3 对 thread_id 的定义同义 ⇒ 直接取它，⛔ 不按 chattype 自己分支。"""
    assert channel.compute_inbound_fields(_frame()).thread_id == "wrkSHat_chat_001"


def test_thread_id_falls_back_to_the_sender_and_says_so_loudly(caplog):
    """chatid 缺席只在单聊语义下能退回 userid——群消息走到这里归档与幂等键都会错，
    ⛔ 所以必须留 WARNING，不许静默退。"""
    with caplog.at_level("WARNING"):
        fields = channel.compute_inbound_fields(_frame(chatid=_MISSING))
    assert fields.thread_id == "TangLiPing"
    assert any("chatid" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("msgtype", channel.ATTACHMENT_MSGTYPES)
def test_attachment_bearing_types_are_flagged(msgtype):
    fields = channel.compute_inbound_fields(_frame(msgtype=msgtype, text=_MISSING))
    assert fields.carries_attachment is True
    # 没有正文不是错误：图片／文件／语音本来就没有。
    assert fields.content == ""


def test_missing_text_is_not_a_shape_error():
    """⛔ 不许把"没有正文"当成形状错误——那会把最要紧的那条链路（私信发文档 →
    归档）整条打不通。"""
    assert channel.compute_inbound_fields(_frame(text=_MISSING)).content == ""


# ── 拆帧：形状对不上 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"msgid": _MISSING},
        {"msgid": ""},
        {"msgid": "   "},
        {"msgid": 12345},
        {"msgtype": _MISSING},
        {"from": _MISSING},
        {"from": "TangLiPing"},
        {"from": {"userid": ""}},
    ],
)
def test_a_frame_that_does_not_match_the_field_map_raises(overrides):
    """⛔ 不返回 None、⛔ 不"看情况跳过"：静默丢一条入站消息正是本轮要消灭的东西。"""
    with pytest.raises(channel.InboundFrameShapeError):
        channel.compute_inbound_fields(_frame(**overrides))


@pytest.mark.parametrize("frame", [None, "not-a-frame", 42, {"body": "not-a-dict"}, {}])
def test_a_non_frame_raises_instead_of_crashing(frame):
    with pytest.raises(channel.InboundFrameShapeError):
        channel.compute_inbound_fields(frame)


def test_the_shape_error_lists_key_names_but_never_values():
    """报错会进日志、聊天与 issue —— 那是最不设防的路径，⛔ 不许带个人信息。"""
    with pytest.raises(channel.InboundFrameShapeError) as excinfo:
        channel.compute_inbound_fields(_frame(msgid=_MISSING))
    text = str(excinfo.value)
    assert "TangLiPing" not in text
    assert "岗位需求：嵌入式工程师 2 人" not in text
    assert "msgtype" in text and "chatid" in text


# ── 落库 ────────────────────────────────────────────────────────────────


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    return connection


def test_dispatch_archives_and_enqueues_a_whitelisted_message(conn, tmp_path):
    whitelist = tmp_path / "whitelist.yaml"
    # 三个字段一个都不能少：多一个／少一个都会让该条**整条丢弃**（whitelist.py）。
    whitelist.write_text(
        'members:\n  - userid: "TangLiPing"\n    name: 汤丽萍\n    role: HR AI 专员\n',
        encoding="utf-8",
    )
    result = channel.dispatch_inbound_frame(
        conn,
        _frame(),
        received_at=RECEIVED_AT,
        archive_root=tmp_path / "archive",
        whitelist_path=whitelist,
    )
    assert result.route.admitted is True
    assert result.outcome.newly_archived is True
    assert result.enqueued is True
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1


def test_dispatch_still_archives_a_message_from_outside_the_whitelist(conn, tmp_path):
    """spec 原文：名单外的消息 SHALL 仍然归档（事后可查"谁在什么时候发过什么"）。"""
    result = channel.dispatch_inbound_frame(
        conn,
        _frame(**{"from": {"userid": "SomeoneElse"}}),
        received_at=RECEIVED_AT,
        archive_root=tmp_path / "archive",
        whitelist_path=tmp_path / "nobody.yaml",
    )
    assert result.route.admitted is False
    assert result.outcome.newly_archived is True
    assert result.enqueued is False


def test_dispatch_never_sends_the_polite_notice(conn, tmp_path):
    """⛔ 本层不接对外回复端口：对外发送属 🔴 不可代办一档，且要走 SDK 的事件循环，
    而这里是值守线程。名单外发送人因此只归档、收不到礼貌说明（已登记缺口）。"""
    result = channel.dispatch_inbound_frame(
        conn,
        _frame(**{"from": {"userid": "SomeoneElse"}}),
        received_at=RECEIVED_AT,
        archive_root=tmp_path / "archive",
        whitelist_path=tmp_path / "nobody.yaml",
    )
    assert result.replied is False


def test_dispatch_is_idempotent_on_a_redelivered_msgid(conn, tmp_path):
    """同一 `msgid` 重投 ⇒ 归档幂等命中，⛔ 不许落第二行。"""
    for _ in range(2):
        result = channel.dispatch_inbound_frame(
            conn,
            _frame(),
            received_at=RECEIVED_AT,
            archive_root=tmp_path / "archive",
            whitelist_path=tmp_path / "nobody.yaml",
        )
    assert result.outcome.newly_archived is False
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_an_attachment_bearing_message_is_archived_but_says_the_bytes_are_missing(
    conn, tmp_path, caplog
):
    """附件字节这一轮 ⛔ 不下载（未经实测的 url/aeskey 字段名，TD-19 的教训）。
    消息本身照常归档、照常入队，但**必须留 WARNING**——不然 8.6 会误以为
    「私信发文档 → 归档」这条链路已经验通了。"""
    whitelist = tmp_path / "whitelist.yaml"
    whitelist.write_text(
        'members:\n  - userid: "TangLiPing"\n    name: 汤丽萍\n    role: HR AI 专员\n',
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        result = channel.dispatch_inbound_frame(
            conn,
            _frame(msgtype="file", text=_MISSING),
            received_at=RECEIVED_AT,
            archive_root=tmp_path / "archive",
            whitelist_path=whitelist,
        )
    assert result.outcome.newly_archived is True
    assert result.outcome.attachments == ()
    assert any("未下载附件字节" in r.getMessage() for r in caplog.records)
