"""第 7 章·存活戳（7.1 / 7.7 的一半）。

spec liaison-channel-session「存活戳区分空闲与断线」：存活戳 MUST 在连接健康但
无消息往来（空闲）时继续更新，MUST NOT 因为"一段时间没有消息"被判定为断线。
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from datetime import datetime, timedelta

import pytest

from tools.liaison import session

T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)
SESSION_SOURCE = pathlib.Path(session.__file__)


def test_writing_a_stamp_creates_the_file_with_the_contract_keys(tmp_path):
    """三个契约键 ＋ TD-42 的 `last_event_at`（没有事件时为 null，键仍在）。"""
    path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_CONNECTED, now=T0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "state": "connected",
        "stamp_at": session.format_instant(T0),
        "since": session.format_instant(T0),
        session.LIVENESS_EVENT_KEY: None,
    }


def test_writing_a_stamp_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "data" / "liaison" / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_STARTING, now=T0)
    assert path.is_file()


def test_stamp_keeps_refreshing_through_two_idle_hours(tmp_path):
    """spec Scenario「长时间无消息但连接健康」：两小时零消息，存活戳照更。

    ⛔ 这条用例里**根本没有"消息"这个概念**——因为存活戳的实现里也不该有。
    """
    path = tmp_path / "liveness.json"
    connected_since = T0
    stamps = []
    for minute in range(0, 121):
        now = T0 + timedelta(minutes=minute)
        session.effect_write_liveness_stamp(
            path, state=session.STATE_CONNECTED, now=now, since=connected_since
        )
        stamps.append(json.loads(path.read_text(encoding="utf-8"))["stamp_at"])
    assert stamps[-1] == session.format_instant(T0 + timedelta(hours=2))
    assert len(set(stamps)) == 121, "每一次都必须真的刷新，⛔ 不许写一次就不写了"
    assert json.loads(path.read_text(encoding="utf-8"))["since"] == session.format_instant(T0)


def test_stamp_is_replaced_atomically_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_CONNECTED, now=T0)
    session.effect_write_liveness_stamp(
        path, state=session.STATE_CONNECTED, now=T0 + timedelta(seconds=30)
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["liveness.json"], (
        "临时文件必须被 os.replace 掉，⛔ 不许留下半截文件让外部读到"
    )


def test_reading_a_missing_stamp_returns_none(tmp_path):
    assert session.read_liveness_stamp(tmp_path / "nope.json") is None


def test_reading_a_corrupt_stamp_returns_none_and_logs(tmp_path, caplog):
    """存活戳坏了 ⇒ 当作"没有上一次的记录"，⛔ 不许炸掉启动流程。

    存活戳是诊断信息，不是账本；中断窗口的账在库里。为一个坏掉的诊断文件拒绝
    启动，等于让服务因为温度计坏了就不上班。
    """
    path = tmp_path / "liveness.json"
    path.write_text("{不是 JSON", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert session.read_liveness_stamp(path) is None
    assert "存活戳" in caplog.text


def test_reading_a_stamp_missing_required_keys_returns_none(tmp_path):
    path = tmp_path / "liveness.json"
    path.write_text(json.dumps({"state": "connected"}), encoding="utf-8")
    assert session.read_liveness_stamp(path) is None


def test_stamp_payload_carries_no_personal_information(tmp_path):
    """合规：窗口与存活戳只需要时间。⛔ 不许出现消息正文、姓名、手机号。"""
    path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_CONNECTED, now=T0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"state", "stamp_at", "since", session.LIVENESS_EVENT_KEY}


# ─────────────────────────────────────────────────────────────────────────
# 结构断言：让"按无消息判断线"这个 bug 在 session.py 里写不出来
# ─────────────────────────────────────────────────────────────────────────

_FORBIDDEN_IDENTIFIER = re.compile(r"(?i)(last_?message|no_?message|idle|silence|quiet)")


def _identifiers(tree: ast.AST) -> set[str]:
    """只收标识符：变量名、属性名、形参名、函数名。

    ⛔ 刻意不看字符串与注释——在注释里写「⛔ 不按无消息判断线」是**应该**的，
    把它变成一个变量才是问题。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_session_module_has_no_message_timing_state():
    """7.1 逐字：存活戳只跟连接健康走，"一段时间无消息"≠断线。

    参考服务混淆过这两件事（06-企业AI转型资产借鉴清单.md §三），修正过一次。
    本断言让同样的 bug 在这个模块里**写不出来**：一旦有人加了 `last_message_at`
    之类的状态，这条当场红。
    ⛔ 红了不要往正则里加豁免——先问"这个模块为什么需要知道消息的时间"。
    """
    tree = ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"), filename=str(SESSION_SOURCE))
    offenders = sorted(n for n in _identifiers(tree) if _FORBIDDEN_IDENTIFIER.search(n))
    assert offenders == [], f"session.py 出现了与消息时序有关的标识符：{offenders}"


def test_the_structural_guard_actually_catches_a_break():
    """证伪：喂一段带 last_message_at 的源码，上面那条判据必须抓到。"""
    tree = ast.parse(
        "def tick(now, last_message_at):\n"
        "    if now - last_message_at > 7200:\n"
        "        return 'disconnected'\n"
    )
    offenders = sorted(n for n in _identifiers(tree) if _FORBIDDEN_IDENTIFIER.search(n))
    assert offenders == ["last_message_at"]


def test_session_module_never_uses_a_with_statement():
    """⛔ tools/liaison 非测试代码里不许出现 with —— 第 2 章的事务扫描器会把任何
    `with <名字|属性|调用>:` 判为隐式提交违规（它认形状不认语义）。

    这条断言把那个远处的失败搬到本模块自己的测试里，省掉"为什么一个文件读写会让
    事务测试变红"这段排查。
    """
    tree = ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"), filename=str(SESSION_SOURCE))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))]


# ─────────────────────────────────────────────────────────────────────────
# TD-42 · 存活戳带「距上次真实 SDK 事件」（`last_event_at`）
# ─────────────────────────────────────────────────────────────────────────
#
# 2026-09-10 实测：`state=connected` ＋ `stamp_at` 推进了 **10 小时**，实际一条
# 都没收到。`stamp_at` 只证明**值守线程**活着，⛔ 证明不了 SDK 在收东西。
# 从此存活戳多带一个字段：SDK 最近一次真的把东西送到本进程的时刻。
#
# ⚠️ 它 ⛔ **不是**「上一条消息什么时候来的」：心跳回包、认证成功、连接事件
# 都算——这是**连接健康**的证据，与消息多不多无关。上面的结构断言仍然守着
# 「无消息 ≠ 断线」；`tick()` 的判据也仍然只有连接状态。


def test_stamp_carries_last_event_at_when_given(tmp_path):
    path = tmp_path / "liveness.json"
    seen = T0 - timedelta(seconds=20)
    session.effect_write_liveness_stamp(
        path, state=session.STATE_CONNECTED, now=T0, last_event_at=seen
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[session.LIVENESS_EVENT_KEY] == session.format_instant(seen)
    assert payload["stamp_at"] == session.format_instant(T0)


def test_stamp_writes_the_event_key_as_null_when_nothing_arrived_yet(tmp_path):
    """键**必须在**、值为 null：`starting` 状态下还没有任何 SDK 事件是正常的。

    ⛔ 不许干脆不写这个键：「键缺席」是留给**旧格式**的信号（看门狗按「未知、
    需要关注」处理），与「本进程还没收到事件」是两回事。
    """
    path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_STARTING, now=T0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert session.LIVENESS_EVENT_KEY in payload
    assert payload[session.LIVENESS_EVENT_KEY] is None


def test_reading_an_old_format_stamp_still_returns_the_payload_without_the_event_key(tmp_path):
    """兼容：旧格式（三个键）读得出来，⛔ 不许因为少了新键就当「没有上一次记录」——
    `start()` 还要靠它补记停机窗口。「按未知处理」是**看门狗**那一层的事。"""
    path = tmp_path / "liveness.json"
    path.write_text(
        json.dumps({"state": "connected", "stamp_at": session.format_instant(T0),
                    "since": session.format_instant(T0)}),
        encoding="utf-8",
    )
    payload = session.read_liveness_stamp(path)
    assert payload is not None
    assert session.LIVENESS_EVENT_KEY not in payload
