"""`app/outbound` 门禁纯函数的行为面测试（交付单元 U4）。

⚠️ 本文件里的期望值一律写**字面量**，不引用被测模块的常量。
理由：判据和构造共用同一个常量 = 自我实现的测试，改常量时两边一起变、
永远不红。本仓库已经栽过两次（U1 拿 init_schema() 和 init_schema() 互比；
单元 E 用 [[…]] * MAX_ASKS_PER_QUESTION 造数据）。
枚举用被测常量（新增取值时强制作者面对），断言用字面量。
"""

from app.outbound.contracts import (
    GATE_FIELDS,
    KNOWN_SEVERITIES,
    MAX_SEVERITY,
    REGISTERED_MESSAGE_TYPES,
)


def test_registered_message_types_are_exactly_the_two_candidate_facing_kinds():
    """
    spec「门禁覆盖范围」：拒信与邀约两类走门禁，内部通知不在范围内。
    断言**相等**而不是包含——多登记一类就是多开一个候选人外发口子，
    属不可代事项，必须在这里变红而不是静默通过。
    """
    assert REGISTERED_MESSAGE_TYPES == frozenset({"rejection_letter", "interview_invitation"})


def test_severity_vocabulary_is_ordered_and_its_top_is_the_blocking_one():
    """风险等级词表是有序的，最高级单独有名字——判定要用它做等值比较。"""
    assert KNOWN_SEVERITIES == ("low", "medium", "high")
    assert MAX_SEVERITY == "high"
    assert MAX_SEVERITY == KNOWN_SEVERITIES[-1]


def test_gate_fields_cover_every_attribute_the_gate_reads():
    """
    这六个名字是 fail-closed 的作用面：门禁只从这六个属性取信息，
    证据也只记这六项（body 除外，见 gate.py 的 EVIDENCE_KEYS 注释）。
    """
    assert GATE_FIELDS == (
        "message_type",
        "requires_confirmation",
        "severity",
        "recipient",
        "body",
        "confirmed_by",
    )


def test_protocol_is_not_runtime_checkable():
    """
    结构性守护：`OutboundGateMessage` 绝不能是 @runtime_checkable。
    一旦可以 isinstance()，下一个人就会在门禁入口写
    `if not isinstance(msg, OutboundGateMessage): return`——而 fail-closed
    的前提正是"来的东西可能什么属性都没有"，那种消息必须走完判定被拦下并
    留痕，不能在门口被一个类型判断吃掉（拦截留痕是误拦的唯一观测手段，
    见 design 风险表第 2 条）。
    """
    from app.outbound.contracts import OutboundGateMessage

    assert not getattr(OutboundGateMessage, "_is_runtime_protocol", False)
