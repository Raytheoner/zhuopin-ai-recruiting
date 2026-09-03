"""
tasks 5.6–5.9。拦截 → 入队 → 放行 → 投递的完整一圈，外加两条守护：
重放不重复副作用，以及 M1 的内部通知**结构上到不了**候选人门禁。

⚠️ 与 `tests/test_outbound_delivery.py` 的分工：那边测 `deliver_candidate_message`
这一个函数的单元行为（判定一次、分流、镜像时机），本文件测**整圈**——门禁、队列、
两个 effect 节点、留痕四样连起来跑，以及采集图与这一圈的隔离。
"""

import ast
import json
from collections import Counter
from pathlib import Path

import pytest

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.outbound import queue
from app.outbound.delivery import deliver_candidate_message
from app.outbound.messages import CandidateOutboundMessage
from app.storage.db import get_connection, init_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
AI_BODY = "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"


class SpyChannel:
    def __init__(self):
        self.delivered = []

    def deliver(self, thread_id, message):
        self.delivered.append((thread_id, message))

    def latest(self, thread_id):
        return None


@pytest.fixture
def wired(tmp_path):
    conn = get_connection(str(tmp_path / "e2e.db"))
    init_schema(conn)
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return conn, chain_path, recorder, SpyChannel()


def _msg():
    return CandidateOutboundMessage(
        message_type="rejection_letter", recipient="cand-9@example.com", body=AI_BODY
    )


def _mirror(chain_path):
    text = chain_path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines()] if text else []


def test_blocked_letter_is_queued_with_reason_and_evidence(wired):
    """
    tasks 5.6：一封无 confirmed_by 的拒信 → 未投递、入队为 pending、
    留痕含拦截原因**与判定字段原始取值**。

    ⚠️ evidence 那半句是重点：只断言"有留痕"的话，一条 evidence 全空的留痕
    照样绿，而 U6 的 6.5 正是靠 evidence 才能 group 出"哪一类消息一直在被拦"。
    """
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg(),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: True,
    )

    assert channel.delivered == []
    row = queue.list_pending(conn)[0]
    assert row["status"] == "pending"
    assert row["blocked_reason"] == decision.reason
    line = _mirror(chain_path)[0]
    assert line["event_type"] == OUTBOUND_BLOCKED
    assert line["blocked_reason"] == decision.reason
    assert line["evidence"]["message_type"] == "rejection_letter"
    assert line["evidence"]["severity"] == "high"


def test_approving_the_queued_letter_delivers_it_and_leaves_a_second_trail(wired):
    """
    tasks 5.7：队列 approve + 总开关开启 → 投递发生、队列转 approved、
    留痕动作类型为「已发送」且含 confirmed_by。

    ⭐ 这条在 D-6 口径 (a) 下**不可能通过**——那是它存在的意义。
    """
    conn, chain_path, recorder, channel = wired
    deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg(),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: True,
    )
    approval_id = queue.list_pending(conn)[0]["id"]

    decision = queue.approve(
        conn,
        approval_id,
        confirmed_by="张三",
        outbound_enabled=lambda: True,
        deliver=lambda m: deliver_candidate_message(
            conn,
            thread_id="job-7",
            message=m,
            channel=channel,
            recorder=recorder,
            outbound_enabled=lambda: True,
        ),
    )
    conn.commit()

    assert decision.allowed is True
    assert len(channel.delivered) == 1
    assert queue.get(conn, approval_id)["status"] == "approved"
    assert queue.list_pending(conn) == []
    delivered_line = [
        l for l in _mirror(chain_path) if l["event_type"] == OUTBOUND_DELIVERED
    ]
    assert len(delivered_line) == 1
    assert delivered_line[0]["confirmed_by"] == "张三"


def test_replaying_the_whole_flow_repeats_no_side_effect(wired):
    """
    tasks 5.8：外发相关节点被从头重跑 → 已外发不重复外发、已入队不重复入队
    （effect_log 命中短路），**且留痕不被写重**。LangGraph 恢复时节点从头整个
    重跑，这是铁律 1 的前提。

    🔴 **四样都断言，第四样是这条测试真正的守卫。** 只断言前三样（投递次数、
    队列行数、effect_log 条数）会**假绿**：`idempotent_effect` 重放时返回 None、
    函数体根本没跑，effect_log 本来就恒为 1 条——它对"镜像被写重了没有"完全
    没有分辨力。而外发事件在 `SqliteSink` 里**没有真身**
    （`SUPPORTED_EVENT_TYPES` 只收 ai_analysis，真身是 `pending_approval`），
    JSONL 那一行是这条决策**唯一**的留痕；`reconcile()` 比的是 id **集合**差集，
    同一个 id 出现三次对它完全隐形（`app/audit/hook.py:174-218` 有实测证据：
    SQLite 1 行、JSONL 2 行，`reconcile().ok` 仍为 True）。

    ⚠️ 断言的量与构造的量**刻意来自不同的源**：构造侧是重放 `REPLAYS` 次，
    断言侧是常量 1——1 来自领域规则「一次决策留一条痕」，不是从 REPLAYS 推的。
    ⛔ 不许写成 `== REPLAYS` 或任何由 REPLAYS 导出的值，那是自我实现的测试。
    """
    conn, chain_path, recorder, channel = wired
    signed = _msg().with_confirmation("张三")
    REPLAYS = 3
    assert REPLAYS > 1, "重放次数必须 > 1，否则这条测试什么也没测"

    for _ in range(REPLAYS):
        deliver_candidate_message(
            conn,
            thread_id="job-7",
            message=signed,
            channel=channel,
            recorder=recorder,
            outbound_enabled=lambda: True,
        )

    assert len(channel.delivered) == 1
    assert queue.list_pending(conn) == []
    counts = dict(
        conn.execute(
            "SELECT node_name, count(*) FROM effect_log GROUP BY node_name"
        ).fetchall()
    )
    assert counts["effect_deliver_message"] == 1
    assert counts["effect_record_outbound_audit"] == 1

    # 第四样：镜像行数。id 由生产代码的构成规则重算（delivery.py:_audit_event），
    # 与被断言的对象同源，⛔ 但与 REPLAYS 无关。
    expected_id = (
        f"job-7:effect_record_outbound_audit:{signed.content_hash()}:True"
    )
    lines = _mirror(chain_path)
    by_id = Counter(line["id"] for line in lines)
    assert by_id[expected_id] == 1, (
        f"同一个 event.id 在 JSONL 里出现了 {by_id[expected_id]} 次；"
        f"外发留痕的唯一真源被写重了。全部行 id: {list(by_id.items())}"
    )
    assert len(lines) == 1, f"预期镜像恰好 1 行，实得 {len(lines)} 行"


def test_the_intake_graph_cannot_reach_the_candidate_gate():
    """
    ⭐⭐ tasks 5.9 / spec「内部通知不受影响」。**判据是结构性的，不是跑一遍看它
    没报错**——后者在"门禁被误插进采集图但恰好放行"时同样是绿的，而那时候红线
    已经破了（内部通知被候选人开关左右）。

    这里断言 app/graph/build.py 既不 import 候选人门禁的任何符号，也不调用
    deliver_candidate_message：采集图那条路径**结构上到不了**这道闸。
    """

    def imported_names(source: str) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
        return names

    source = (REPO_ROOT / "app" / "graph" / "build.py").read_text(encoding="utf-8")
    names = imported_names(source)

    assert not any(n.startswith("app.outbound") for n in names), names
    assert "deliver_candidate_message" not in names
    assert "compute_outbound_gate" not in names
    assert "deliver_candidate_message" not in source  # 连字符串引用都没有

    # 阳性对照：检查器确实能抓到
    assert "app.outbound.delivery" in imported_names(
        "from app.outbound.delivery import deliver_candidate_message\n"
    )


def test_internal_notifications_still_deliver_unconditionally(tmp_path):
    """
    tasks 5.9 的行为面：M1 现有投递行为与本变更前一致。画像确认卡片
    （type="confirmation_prompt"）**不带**门禁要的六个字段，若它被误接进候选人
    门禁，会因"未登记的消息类型"当场被拦——这条会红。

    ⚠️ 它与上一条互补：上一条证明"结构上到不了"，这条证明"就算到了也会立刻暴露"。
    """
    from app.channels.base import OutboundMessage
    from app.graph.nodes import effect_deliver_message, message_business_key

    conn = get_connection(str(tmp_path / "m1.db"))
    init_schema(conn)
    channel = SpyChannel()
    payload = {"question": "这个岗位需要几个人？"}

    effect_deliver_message(
        conn,
        thread_id="job-7",
        business_key=message_business_key(payload),
        channel=channel,
        message=OutboundMessage(type="confirmation_prompt", payload=payload),
    )

    assert len(channel.delivered) == 1
    assert channel.delivered[0][1].type == "confirmation_prompt"
    assert queue.list_pending(conn) == []  # 内部通知不进候选人待审批队列
