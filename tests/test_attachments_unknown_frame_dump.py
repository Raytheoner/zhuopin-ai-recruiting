"""0921E·未知附件帧取证 ＋ 附件只走私信口径（正文见
`docs/openers/0921E-附件未知帧取证与只走私信口径.md`）。

本文件只钉死 0921E 新引入的两条行为，⛔ 不重复
`tools/liaison/tests/test_inbound_attachment_wiring.py` 已覆盖的下载/落盘/幂等用例：

1. `frames.frame_key_types` 是纯函数，产出只含键路径与类型名，⛔ 不含任何取值。
2. 群帧（`chattype != "single"`）附件一律忽略：⛔ 不抛错、⛔ 不落取证文件。
3. 单聊帧遇到未知附件映射（生产 `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE` 现状为空）仍
   fail-closed（附件不落盘、正文照常归档），且把帧的键结构（⛔ 无取值）落到
   `InboundPorts.unknown_attachment_log_dir`。

⚠️ 帧用的是 `frames.py` 里已实测的真实路径（`body.msgid`/`body.from.userid`/
`body.chattype`/`body.chatid`，AT-1b），⛔ 不是占位键名——这样测的才是生产真的会
走到的分支。
"""

from __future__ import annotations

import json
import queue

from tools.liaison import __main__ as liaison_main
from tools.liaison import frames
from tools.liaison.tests.test_inbound_wiring import (
    OUTSIDER_USERID,
    T0,
    StoppingEvent,
    ports,  # noqa: F401 —— pytest fixture，按名字注入
    roster,  # noqa: F401
    svc,  # noqa: F401
)


def run_one(svc, ports, frame):
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, frame))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )


def make_single_file_frame(*, msgid="MSGID0201", sender=OUTSIDER_USERID):
    """单聊、未知映射的文件帧：生产 `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE` 现状为空。"""
    return {
        "cmd": frames.MESSAGE_CALLBACK_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "file",
            "chattype": "single",
            "msgid": msgid,
            "from": {"userid": sender},
            "file": {"url": "https://example.invalid/media/unknown-single", "filesize": 999},
        },
    }


def make_group_file_frame(*, msgid="MSGID0202", sender=OUTSIDER_USERID):
    """群帧：0921E 口径生效后附件一律忽略，⛔ 不走取证/fail-closed 分支。"""
    return {
        "cmd": frames.MESSAGE_CALLBACK_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "file",
            "chattype": "group",
            "msgid": msgid,
            "chatid": "groupA",
            "from": {"userid": sender},
            "file": {"url": "https://example.invalid/media/unknown-group", "filesize": 111},
        },
    }


# ─────────────────────────────────────────────────────────────────────────
# ① frame_key_types：纯函数，只出键路径与类型名
# ─────────────────────────────────────────────────────────────────────────


def test_frame_key_types_carries_only_key_paths_and_type_names_no_values():
    frame = {
        "body": {
            "msgtype": "file",
            "chattype": "single",
            "file": {"url": "https://secret.example/abc", "filesize": 12345},
        }
    }
    result = frames.frame_key_types(frame)
    assert result == {
        "body.msgtype": "str",
        "body.chattype": "str",
        "body.file.url": "str",
        "body.file.filesize": "int",
    }
    dumped = json.dumps(result, ensure_ascii=False)
    assert "secret.example" not in dumped
    assert "12345" not in dumped


# ─────────────────────────────────────────────────────────────────────────
# ② 群帧附件一律忽略：不抛错、不落取证文件、正文照常归档
# ─────────────────────────────────────────────────────────────────────────


def test_group_frame_attachment_is_ignored_and_leaves_no_dump(svc, ports):
    run_one(svc, ports, make_group_file_frame())

    row = svc.conn.execute(
        "SELECT attachments_json FROM liaison_message WHERE msgid = ?", ("MSGID0202",)
    ).fetchone()
    assert row is not None, "正文应照常归档，群帧忽略只影响附件"
    assert json.loads(row[0]) == []
    assert not ports.unknown_attachment_log_dir.exists() or (
        list(ports.unknown_attachment_log_dir.glob("*")) == []
    ), "群帧附件 ⛔ 不许落取证文件"


# ─────────────────────────────────────────────────────────────────────────
# ③ 单聊未知附件帧：fail-closed ＋ 落一份只含键名/类型的取证文件
# ─────────────────────────────────────────────────────────────────────────


def test_single_chat_unknown_attachment_frame_fails_closed_and_dumps_key_types_only(
    svc, ports
):
    assert frames.ATTACHMENT_FIELD_PATHS_BY_MSGTYPE == {}, "本用例验的是生产现状：表未填"

    run_one(svc, ports, make_single_file_frame())

    row = svc.conn.execute(
        "SELECT attachments_json FROM liaison_message WHERE msgid = ?", ("MSGID0201",)
    ).fetchone()
    assert row is not None, "正文应照常归档"
    assert json.loads(row[0]) == [], "fail-closed：附件 ⛔ 不落盘"

    dumped = list(ports.unknown_attachment_log_dir.glob("*-file.json"))
    assert len(dumped) == 1, "单聊未知附件帧必须留一份取证文件"
    dumped_text = dumped[0].read_text(encoding="utf-8")
    payload = json.loads(dumped_text)
    assert payload.get("body.file.url") == "str"
    assert payload.get("body.file.filesize") == "int"
    assert "example.invalid" not in dumped_text, "⛔ 取证文件里不许出现取值"
    assert "999" not in dumped_text, "⛔ 取证文件里不许出现取值（含数值/长度）"
