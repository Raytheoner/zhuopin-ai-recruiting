"""
候选人外发入口。判定一次 → 分流 → 留痕 → 提交后镜像。

⚠️ 本模块是**唯一**允许把候选人信件交给通道的地方。⛔ 不提供任何"跳过门禁"的
参数或开关（design.md 迁移计划回滚策略：真要恢复无门禁投递必须显式移除门禁节点）。
"""

import ast
import json
from pathlib import Path

import pytest

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.outbound import queue
from app.outbound.delivery import _audit_event, deliver_candidate_message
from app.outbound.gate import compute_outbound_gate
from app.outbound.messages import CandidateOutboundMessage
from app.storage.db import get_connection, init_schema

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
    conn = get_connection(str(tmp_path / "d.db"))
    init_schema(conn)
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return conn, chain_path, recorder, SpyChannel()


def _msg(**over):
    payload = {
        "message_type": "rejection_letter",
        "recipient": "cand-9@example.com",
        "body": AI_BODY,
    }
    payload.update(over)
    return CandidateOutboundMessage(**payload)


def _mirror_lines(chain_path):
    text = chain_path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines()] if text else []


def test_a_draft_without_a_signature_is_queued_not_delivered(wired):
    """spec「未带确认人的高风险消息」：拦截、入队、留痕原因为「等待人工确认」。"""
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg(),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: True,
    )

    assert decision.allowed is False
    assert channel.delivered == []
    pending = queue.list_pending(conn)
    assert len(pending) == 1
    assert pending[0]["blocked_reason"] == decision.reason
    assert _mirror_lines(chain_path)[0]["event_type"] == OUTBOUND_BLOCKED


def test_a_signed_draft_with_the_switch_on_is_delivered(wired):
    """spec「两道闸都通过」：消息被外发；留痕动作类型为已发送且含 confirmed_by。"""
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg().with_confirmation("张三"),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: True,
    )

    assert decision.allowed is True
    assert len(channel.delivered) == 1
    assert queue.list_pending(conn) == []  # 直接放行的不进队列
    line = _mirror_lines(chain_path)[0]
    assert line["event_type"] == OUTBOUND_DELIVERED
    assert line["confirmed_by"] == "张三"


def test_a_signed_draft_with_the_switch_off_is_blocked_and_queued(wired):
    """
    spec「总开关关闭时已确认的消息」：拦截，原因记「外发总开关关闭」，
    **与「等待人工确认」区分开**——U6 的 6.5 靠这个分布做判断。
    """
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg().with_confirmation("张三"),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: False,
    )

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert channel.delivered == []


def test_the_gate_is_evaluated_exactly_once_and_its_evidence_is_carried_verbatim(wired):
    """
    ⭐ design D4 / GateDecision docstring 逐字：evidence 直接塞进
    DecisionEvent.evidence，⛔ 不重新求值一遍——重新求值会制造"判定时未知、
    留痕时又变成已知"的不一致。

    断言的是**对象同一性**：相等允许中途拷一份再改几个键，同一性不允许。
    顺带用一个只肯被求值一次的开关钉住"判定只发生一次"。
    """
    conn, chain_path, recorder, channel = wired
    calls = []

    def switch_once():
        calls.append(1)
        return True

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg().with_confirmation("张三"),
        channel=channel,
        recorder=recorder,
        outbound_enabled=switch_once,
    )

    assert len(calls) == 1  # 门禁只判了一次
    line = _mirror_lines(chain_path)[0]
    assert line["evidence"] == decision.evidence
    assert line["evidence"] is not None and line["evidence"] != {}


def test_delivery_module_calls_the_gate_exactly_once():
    """
    ⭐ 上一条的结构版阳性对照。行为测试只能证明"当前实现判了一次"；这条证明
    源码里 compute_outbound_gate 只出现一次，将来有人"顺手在留痕前再判一次"
    会立刻变红。
    """

    def gate_calls_in(source: str) -> int:
        return sum(
            1
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
            == "compute_outbound_gate"
        )

    source = (Path(__file__).resolve().parents[1] / "app" / "outbound" / "delivery.py").read_text(
        encoding="utf-8"
    )
    assert gate_calls_in(source) == 1
    # 阳性对照
    assert gate_calls_in("a = compute_outbound_gate(m, s)\nb = compute_outbound_gate(m, s)\n") == 2


def test_evidence_object_identity_is_preserved_not_copied():
    """
    补充用例（不改动 brief 给的其余用例）：上面「对象同一性」那条断言的是
    `line["evidence"] == decision.evidence`——但 `line` 是从 JSONL 文件
    `json.loads` 回来的，经过序列化往返后**永远**是一个新对象，这条等值断言
    分辨不出"同一个对象"与"内容相同的拷贝"（`dict(decision.evidence)` 这种
    写法会被它放过；手工验证：临时把 `_audit_event()` 里的
    `evidence=decision.evidence` 改成 `evidence=dict(decision.evidence)`，
    brief 给的 6 条用例全绿不变）。

    这条直接钉在往返之前——`_audit_event()` 返回的 `DecisionEvent.evidence`
    必须 `is` 判定所返回的 `decision.evidence`，不是它的拷贝：拷贝这一步本身
    就是在打开"可以在两者之间悄悄插入改动"的口子，即使当下内容还相等
    （design D4「不重新求值一遍」）。
    """
    decision = compute_outbound_gate(_msg().with_confirmation("张三"), lambda: True)
    event = _audit_event("job-7", _msg().with_confirmation("张三"), decision)
    assert event.evidence is decision.evidence


def test_no_bypass_parameter_exists():
    """
    ⛔ design.md 迁移计划回滚策略逐字：**不提供"一键放行全部"的配置项**，
    避免它成为红线的旁路。这条扫入口函数的参数名。
    """
    source = (Path(__file__).resolve().parents[1] / "app" / "outbound" / "delivery.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "deliver_candidate_message":
            names = [a.arg for a in node.args.args + node.args.kwonlyargs]
            assert not any(
                bad in name.lower()
                for name in names
                for bad in ("bypass", "skip_gate", "force", "no_gate")
            ), names
            return
    raise AssertionError("没找到 deliver_candidate_message")
