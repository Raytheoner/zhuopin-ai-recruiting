"""6.5 —— 按 message_type × 拦截原因统计，让"某类消息一直在被拦"可被发现。

这是 fail-closed 误拦的**兜底观测**，不是守护测试（design.md Risks 第 2 条）。
没有它，一个新增的消息类型忘了登记就会被静默拦下，只能等业务方投诉。

数据源是 JSONL 镜像：外发事件在 SqliteSink 里没有真身（SUPPORTED_EVENT_TYPES
只含 ai_analysis），镜像是它唯一的记录；而 pending_approval 表会漏掉"放行复发
被拦"那一整类（app/outbound/delivery.py:93-96 只对首道拦截入队）。
"""

import pytest

from app.audit.assertions import (
    UNKNOWN_MESSAGE_TYPE,
    UNRECORDED_REASON,
    outbound_block_stats,
)
from app.audit.events import (
    OUTBOUND_BLOCKED,
    OUTBOUND_DELIVERED,
    AI_ANALYSIS,
    DecisionEvent,
)
from app.audit.sinks import JsonlChainSink
from app.outbound.gate import ALL_BLOCK_REASONS  # ⚠️ 只在测试里 import 上层


@pytest.fixture(autouse=True)
def _clear_chain_class_state():
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


@pytest.fixture
def mirror(tmp_path):
    return JsonlChainSink(tmp_path / "audit" / "decisions.jsonl")


def blocked(mirror, *, index: int, message_type, reason):
    mirror.write(
        DecisionEvent(
            id=f"t-1:effect_record_outbound_audit:h{index}:False",
            event_type=OUTBOUND_BLOCKED,
            thread_id="t-1",
            message_type=message_type,
            recipient="user-1",
            blocked_reason=reason,
        )
    )


def delivered(mirror, *, index: int, message_type):
    mirror.write(
        DecisionEvent(
            id=f"t-1:effect_record_outbound_audit:h{index}:True",
            event_type=OUTBOUND_DELIVERED,
            thread_id="t-1",
            message_type=message_type,
            recipient="user-1",
            confirmed_by="shao",
        )
    )


def test_counts_by_type_and_reason(mirror):
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")
    blocked(mirror, index=2, message_type="rejection_letter", reason="等待人工确认")
    blocked(mirror, index=3, message_type="rejection_letter", reason="外发总开关关闭")
    blocked(mirror, index=4, message_type="interview_invitation", reason="外发总开关关闭")

    stats = outbound_block_stats(mirror)

    assert stats.blocked_by_type == {"rejection_letter": 3, "interview_invitation": 1}
    assert stats.blocked_by_reason == {"等待人工确认": 2, "外发总开关关闭": 2}
    assert stats.blocked_by_type_and_reason == {
        "rejection_letter": {"等待人工确认": 2, "外发总开关关闭": 1},
        "interview_invitation": {"外发总开关关闭": 1},
    }


def test_delivered_events_are_counted_separately(mirror):
    """光有拦截数回答不了"这类消息是不是**一直**在被拦"——要跟放行数对照。"""
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")
    delivered(mirror, index=2, message_type="rejection_letter")
    blocked(mirror, index=3, message_type="interview_invitation", reason="未登记的消息类型")

    stats = outbound_block_stats(mirror)

    assert stats.delivered_by_type == {"rejection_letter": 1}
    assert stats.blocked_by_type == {"rejection_letter": 1, "interview_invitation": 1}


def test_always_blocked_types_is_the_actionable_signal(mirror):
    """拦过、且**一次都没发出去过**的类型 —— 这才是要人去看的那一列。

    原始计数表放在运维面前，人得自己做减法；这个字段替他做完。
    """
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")
    delivered(mirror, index=2, message_type="rejection_letter")
    blocked(mirror, index=3, message_type="interview_invitation", reason="未登记的消息类型")
    blocked(mirror, index=4, message_type="interview_invitation", reason="未登记的消息类型")

    stats = outbound_block_stats(mirror)

    assert stats.always_blocked_types == ("interview_invitation",)


def test_missing_type_and_reason_get_explicit_buckets(mirror):
    """字段缺失的事件 ⛔ 不许丢弃——被拦下的草稿最常见的原因**正是**这些字段缺失。

    丢弃等于让最该被看见的那一类从统计里消失。
    """
    blocked(mirror, index=1, message_type=None, reason="未登记的消息类型")
    blocked(mirror, index=2, message_type="rejection_letter", reason=None)

    stats = outbound_block_stats(mirror)

    assert stats.blocked_by_type[UNKNOWN_MESSAGE_TYPE] == 1
    assert stats.blocked_by_reason[UNRECORDED_REASON] == 1


def test_ai_analysis_events_are_ignored(mirror):
    """同一条链上还躺着 AI 评分事件，⛔ 不能把它们算进外发统计。"""
    mirror.write(
        DecisionEvent(
            id="run-1",
            event_type=AI_ANALYSIS,
            configured_model="deepseek-chat",
            prompt_version="score-v1",
            temperature=0.0,
            input_hash="sha256:abc",
            raw_response="{}",
        )
    )
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")

    stats = outbound_block_stats(mirror)

    assert sum(stats.blocked_by_type.values()) == 1
    assert stats.delivered_by_type == {}


def test_every_registered_block_reason_survives_the_stats_path(mirror):
    """门禁那边登记的**每一条**拦截原因都要能在统计里出现。

    ⚠️ 这条用例是 ALL_BLOCK_REASONS（app/outbound/gate.py）与本统计之间的
    唯一绑定。gate.py 那边加一条新原因、忘了加进 ALL_BLOCK_REASONS 时，本条
    不会红——它守的是另一半：统计路径不会把任何一条已登记原因吃掉（比如被
    某个"过滤掉不认识的原因"的实现悄悄丢弃）。

    ⛔ assertions.py 模块内不 import app.outbound（分层：audit 是下层）。
    这个 import 只出现在测试里。
    """
    for index, reason in enumerate(sorted(ALL_BLOCK_REASONS)):
        blocked(mirror, index=index, message_type="rejection_letter", reason=reason)

    stats = outbound_block_stats(mirror)

    assert set(stats.blocked_by_reason) == set(ALL_BLOCK_REASONS)
    assert all(count == 1 for count in stats.blocked_by_reason.values())


def test_empty_mirror_yields_empty_stats(mirror):
    stats = outbound_block_stats(mirror)

    assert stats.blocked_by_type == {}
    assert stats.blocked_by_reason == {}
    assert stats.blocked_by_type_and_reason == {}
    assert stats.delivered_by_type == {}
    assert stats.always_blocked_types == ()


def test_a_second_block_on_the_same_draft_gets_its_own_bucket(tmp_path):
    """
    ⭐ tasks 2.4 / TD-9 的可观测性目标本身。

    TD-9 描述的"系统性缺席"：同一封拒信被拦两次（首次入队、放行时被总开关拦下），
    6.5 的 blocked_by_type_and_reason 里只能看到**第一次**的那个 reason 桶——
    第二次被幂等机制吞掉，而"一直发不出去的那批信"恰恰是最该被看见的那批。

    ⚠️ 与本文件其余用例的分工：那些手工投喂事件给 mirror，证明的是统计函数的
    分桶逻辑；这条走**真实外发路径**（deliver_candidate_message → queue.approve），
    证明的是真实路径产出的事件能被统计看见。两者不互相替代。

    ⛔ 本用例不改 app/audit/assertions.py：统计函数按现有逻辑遍历镜像即可看见
    （proposal Non-goals 逐字）。若需要改统计代码才能绿，说明留痕这一侧还没修对。
    """
    from app.audit.recorder import AuditRecorder
    from app.audit.sinks import JsonlChainSink, SqliteSink
    from app.outbound import queue
    from app.outbound.delivery import deliver_candidate_message
    from app.outbound.gate import REASON_OUTBOUND_DISABLED
    from app.outbound.messages import CandidateOutboundMessage
    from app.storage.db import get_connection, init_schema

    AI_BODY = (
        "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"
    )

    class SpyChannel:
        def __init__(self):
            self.delivered = []

        def deliver(self, thread_id, message):
            self.delivered.append((thread_id, message))

        def latest(self, thread_id):
            return None

    conn = get_connection(str(tmp_path / "stats.db"))
    init_schema(conn)
    mirror_sink = JsonlChainSink(tmp_path / "decisions.jsonl")
    recorder = AuditRecorder(SqliteSink(conn), mirror_sink)
    channel = SpyChannel()
    message = CandidateOutboundMessage(
        message_type="rejection_letter", recipient="cand-9@example.com", body=AI_BODY
    )

    first = deliver_candidate_message(
        conn, thread_id="job-7", message=message, channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )
    approval_id = queue.list_pending(conn)[0]["id"]
    queue.approve(
        conn,
        approval_id,
        confirmed_by="张三",
        outbound_enabled=lambda: False,
        deliver=lambda m: None,
        recorder=recorder,
    )
    conn.commit()

    stats = outbound_block_stats(mirror_sink)
    buckets = stats.blocked_by_type_and_reason["rejection_letter"]

    assert buckets == {first.reason: 1, REASON_OUTBOUND_DISABLED: 1}, (
        f"两次不同原因的拦截应各占一个桶，实得 {buckets}——TD-9 的'系统性缺席'还在"
    )
    assert stats.blocked_by_type["rejection_letter"] == 2
    assert stats.blocked_by_reason[REASON_OUTBOUND_DISABLED] == 1
    assert stats.always_blocked_types == ("rejection_letter",)
    assert channel.delivered == []
