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
    signed = _msg().with_confirmation("张三")
    decision = compute_outbound_gate(signed, lambda: True)
    event = _audit_event("job-7", signed, decision, signed.content_hash())
    assert event.evidence is decision.evidence


def test_replaying_a_blocked_delivery_does_not_duplicate_the_mirror_line(wired):
    """
    review round 2 Important：`effect_record_outbound_audit` 被 `idempotent_effect`
    包裹，命中同一个 `(thread_id, business_key)` 时返回 `None` 而不真的执行——
    这次不是"这个事件类型在 SqliteSink 里没有真身"（那是 `False`），是"这条决策
    以前处理过，业务写已经跳过了"。外发事件在 `SqliteSink` 里天生没有真身
    （`SUPPORTED_EVENT_TYPES` 排除它），JSONL 这一行是它**唯一**的记录——如果
    `None` 也无条件 mirror，重放 N 次就会在链上写出 N 条同 `id` 的行，腐蚀这份
    唯一记录，而 `reconcile()` 的 id 集合差集看不出这种重复
    （precedent: app/audit/hook.py:184-218 已经踩过同一个坑并留了实测证据）。

    ⚠️ reviewer 特别提醒：只断言 `effect_log` 行数是假绿——`effect_log` 本来就
    该恒为 1（这是幂等键要保证的），能证明"没有重复留痕"的只有**数 JSONL 的
    行数**。这条直接数链文件的行数，不碰 effect_log。

    这条测的是**拦截 → 入队**分支：未带签名的草稿被拦、入队，用同一个 thread_id
    连续调用三次（同一条草稿内容 → 同一个 content_hash → 同一个 business_key）。
    """
    conn, chain_path, recorder, channel = wired
    message = _msg()

    for _ in range(3):
        deliver_candidate_message(
            conn,
            thread_id="job-7",
            message=message,
            channel=channel,
            recorder=recorder,
            outbound_enabled=lambda: True,
        )

    lines = _mirror_lines(chain_path)
    assert len(lines) == 1, f"预期 JSONL 恰好 1 行，实得 {len(lines)} 行：{lines}"
    assert lines[0]["event_type"] == OUTBOUND_BLOCKED
    # 入队本身也是幂等的（ON CONFLICT DO NOTHING），顺带确认队列没有跟着重复。
    assert len(queue.list_pending(conn)) == 1


def test_replaying_an_allowed_delivery_does_not_duplicate_the_mirror_line(wired):
    """
    同上，覆盖**放行 → 投递**分支——它的 business_key 与拦截分支不同
    （`audit_business_key()` 里 `decision.allowed` 不同），
    是独立的幂等键，必须单独用例覆盖，不能靠上一条顺带证明。
    """
    conn, chain_path, recorder, channel = wired
    message = _msg().with_confirmation("张三")

    for _ in range(3):
        deliver_candidate_message(
            conn,
            thread_id="job-7",
            message=message,
            channel=channel,
            recorder=recorder,
            outbound_enabled=lambda: True,
        )

    lines = _mirror_lines(chain_path)
    assert len(lines) == 1, f"预期 JSONL 恰好 1 行，实得 {len(lines)} 行：{lines}"
    assert lines[0]["event_type"] == OUTBOUND_DELIVERED


def test_the_business_key_carries_the_block_reason(wired):
    """
    design.md 决策 1。TD-9 成因②：旧公式 `{content_hash}:{allowed}` 只分辨
    "拦截 vs 放行"，分辨不出"是哪一条拦截"——同一草稿的第二次拦截撞上 effect_log
    已有行被短路，镜像里一行都不多。

    ⚠️ content_hash 刻意不含 confirmed_by（test_content_hash_ignores_the_
    confirmation_signature 钉死），所以"未签名被拦"与"签名后被开关拦"两次的
    content_hash 必然相同、allowed 必然都是 False——旧公式下两个 key 逐字相同。
    """
    from app.outbound.delivery import audit_business_key

    message = _msg()
    signed = message.with_confirmation("张三")
    blocked_by_confirmation = compute_outbound_gate(message, lambda: True)
    blocked_by_switch = compute_outbound_gate(signed, lambda: False)

    assert message.content_hash() == signed.content_hash()  # 前提，先钉住
    assert blocked_by_confirmation.allowed is False
    assert blocked_by_switch.allowed is False
    assert blocked_by_confirmation.reason != blocked_by_switch.reason

    key_a = audit_business_key(message.content_hash(), blocked_by_confirmation)
    key_b = audit_business_key(signed.content_hash(), blocked_by_switch)
    assert key_a != key_b, "两条不同原因的拦截必须落在两个不同的幂等键上"
    assert key_a.endswith(f":{blocked_by_confirmation.reason}")
    assert key_b.endswith(f":{blocked_by_switch.reason}")


def test_an_allowed_decision_keeps_the_empty_reason_segment(wired):
    """
    design.md 决策 1 的归一化规则：`reason` 在 allowed=True 时恒为 None，
    拼接前统一取 `decision.reason or ""`，放行事件的 key 形如
    `{content_hash}:True:`（末尾空段）。

    ⛔ 不为放行分支单独省略 `:{reason}` 段——两条分支必须共用同一个求值表达式，
    分叉的公式迟早会被改错其中一半而没人发现。
    """
    from app.outbound.delivery import audit_business_key

    signed = _msg().with_confirmation("张三")
    allowed = compute_outbound_gate(signed, lambda: True)

    assert allowed.allowed is True and allowed.reason is None
    assert audit_business_key(signed.content_hash(), allowed) == (
        f"{signed.content_hash()}:True:"
    )


def test_two_different_block_reasons_leave_two_mirror_lines(wired):
    """
    ⭐ TD-9 成因②的正面回归，判据是 **JSONL 镜像行数**（Global Constraint 6）。
    ⛔ 不许改成断言 effect_log 计数：重放时 idempotent_effect 返回 None、函数体
    根本没跑，effect_log 本来就恒为 1 条，对"镜像被写重/被吞"零分辨力。

    2026-09-04 在 main 上实测：镜像行数 = 1（应为 2），WARNING 里可见
    「外发留痕已存在（重放），跳过镜像 append」。
    """
    conn, chain_path, recorder, channel = wired
    message = _msg()

    first = deliver_candidate_message(
        conn, thread_id="job-7", message=message, channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )
    second = deliver_candidate_message(
        conn, thread_id="job-7", message=message.with_confirmation("张三"),
        channel=channel, recorder=recorder, outbound_enabled=lambda: False,
    )

    assert first.allowed is False and second.allowed is False
    assert first.reason != second.reason
    lines = _mirror_lines(chain_path)
    assert len(lines) == 2, f"两次不同原因的拦截应各留一条痕，实得 {len(lines)} 行：{lines}"
    assert {l["blocked_reason"] for l in lines} == {first.reason, second.reason}
    assert len({l["id"] for l in lines}) == 2, "两条痕的 id 必须可分别检索"


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


def test_record_outbound_decision_is_callable_on_its_own(wired):
    """
    design.md 决策 2：留痕逻辑提炼成公共函数、两个调用点共用，⛔ 不许在
    queue.py 里手写第二份。

    这条钉住"它能被独立调用"这一件事本身——`queue.approve()` 的被拦分支
    （Task 3）拿不到 channel、也不该经过 deliver_candidate_message 的分流逻辑，
    它只需要留痕这一段。

    为什么必须提炼而不是重写一份：`result is None` / `is False` 的区分是两轮
    review 才收敛出的非显然结论（None=重放、不许再 append；False=真跑了、
    只是这个事件类型在 SqliteSink 里没有真身，必须 append）。手写第二份等于给
    这条不变式开一个可能读漏、写歪的第二入口。
    """
    from app.outbound.delivery import record_outbound_decision

    conn, chain_path, recorder, _channel = wired
    signed = _msg().with_confirmation("张三")
    decision = compute_outbound_gate(signed, lambda: False)

    record_outbound_decision(
        conn, thread_id="job-7", message=signed, decision=decision, recorder=recorder
    )

    lines = _mirror_lines(chain_path)
    assert len(lines) == 1
    assert lines[0]["blocked_reason"] == decision.reason
    assert lines[0]["confirmed_by"] == "张三"
    # 它只留痕：⛔ 不投递、⛔ 不入队
    assert queue.list_pending(conn) == []


def test_record_outbound_decision_does_not_duplicate_on_replay(wired):
    """
    提炼不得削弱判重：同一条决策连调三次，镜像仍恰好一行（判据是 JSONL 行数，
    ⛔ 不是 effect_log 计数——见 Global Constraint 6）。
    """
    from app.outbound.delivery import record_outbound_decision

    conn, chain_path, recorder, _channel = wired
    signed = _msg().with_confirmation("张三")
    decision = compute_outbound_gate(signed, lambda: False)

    for _ in range(3):
        record_outbound_decision(
            conn, thread_id="job-7", message=signed, decision=decision, recorder=recorder
        )

    assert len(_mirror_lines(chain_path)) == 1
