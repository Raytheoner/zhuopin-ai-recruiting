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


import json

import pytest

from app.outbound.gate import GateDecision, compute_outbound_gate


class _Message:
    """按需构造的消息桩：**只设置显式传入的属性**。

    不给默认值、不继承任何基类——"某个属性根本不存在"这个状态必须能被
    构造出来，它是本单元主防线的输入（delivery-units §3.3 第 1 条）。
    """

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


_LABELLED_BODY = (
    "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。"
    "很遗憾，本次未能与您继续推进。"
)


def _valid_message(**overrides):
    """六条 fail-closed 全部合格、且带确认人的消息——唯一放行路径的输入。

    ⚠️ body 里的 AI 标识是**逐字写死的字面量**，不用
    AI_LABEL_TEMPLATE.format() 生成。用 format() 生成就是"构造和判据共用
    同一个常量"，jd_agent 那个模板被改掉时两边一起变、测试永远不红——
    而那句标识是《AI 生成合成内容标识办法》要求的合规资产，被改掉时就该红。
    """
    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }
    fields.update(overrides)
    return _Message(**fields)


def test_evidence_records_every_judged_field_even_when_blocked_by_the_first_rule():
    """
    证据不跟着判定短路：被第一条规则（未登记类型）拦下时，其余字段的原始
    取值同样在证据里。否则留痕里读不出"是哪一条 fail-closed 触发的"，
    而那正是 spec 对拦截留痕的原文要求。
    """
    decision = compute_outbound_gate(
        _valid_message(message_type="offer_letter"), lambda: True
    )

    assert decision.allowed is False
    assert set(decision.evidence) == {
        "message_type",
        "requires_confirmation",
        "severity",
        "recipient",
        "confirmed_by",
        "ai_label_present",
        "outbound_enabled",
    }
    assert decision.evidence["message_type"] == "offer_letter"
    assert decision.evidence["severity"] == "low"
    assert decision.evidence["confirmed_by"] == "shao-peishen"


def test_evidence_never_carries_the_message_body():
    """
    ⛔ body 不进证据：拒信正文是候选人可识别内容，而留痕会被 U6 的对账、
    U7 的运维文档反复读取。判定结果 ai_label_present 进证据就够了，
    正文的指纹由 U5 的 content_hash 承担（tasks 5.3）。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: True)

    assert "body" not in decision.evidence
    assert decision.evidence["ai_label_present"] is True
    for value in decision.evidence.values():
        assert "很遗憾" not in str(value)


@pytest.mark.parametrize(
    "missing_field",
    ["message_type", "requires_confirmation", "severity", "recipient", "body", "confirmed_by"],
)
def test_absent_attribute_is_distinguishable_from_an_explicit_none(missing_field):
    """
    "属性根本不存在"与"属性存在但值是 None"在证据里都记成 None（U2 的
    DecisionEvent.evidence 是扁平 dict[str, Any]，见
    tests/test_audit_events.py::test_outbound_event_carries_gate_evidence），
    两者的区别由 absent_fields 单独承载——运维要判断"这个字段是没给，
    还是给了个空"，靠的是这个元组。
    """
    absent = compute_outbound_gate(
        _valid_message(**{missing_field: None}), lambda: True
    )
    assert missing_field not in absent.absent_fields

    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }
    del fields[missing_field]
    truly_absent = compute_outbound_gate(_Message(**fields), lambda: True)
    assert missing_field in truly_absent.absent_fields


def test_evidence_stays_json_serialisable_for_an_exotic_field_value():
    """
    U5 会把 evidence 原样塞进 DecisionEvent 并 json.dumps 落 JSONL。
    一个非 JSON 原生类型的字段值若原样带过去，序列化会在 effect 里抛错。
    门禁在这里就把它折成 repr 字符串——信息不丢，序列化炸不了。
    """

    class _Weird:
        def __repr__(self):
            return "<Weird severity>"

    decision = compute_outbound_gate(_valid_message(severity=_Weird()), lambda: True)

    assert decision.allowed is False
    assert json.dumps(decision.evidence, ensure_ascii=False)
    assert decision.evidence["severity"] == "<Weird severity>"


def test_decision_is_frozen():
    """判定结果是事实，不是可以被下游改写的草稿。"""
    decision = compute_outbound_gate(_valid_message(), lambda: True)

    with pytest.raises(Exception):
        decision.allowed = True


from app.outbound.contracts import REGISTERED_MESSAGE_TYPES


def test_a_bare_object_with_no_attributes_at_all_is_blocked():
    """
    ⭐ **本单元的主防线**（delivery-units §3.3 第 1 条逐字要求）。

    这条用例是唯一能在"后来者写一句 getattr(msg, 'requires_confirmation',
    False) 当作合理默认值"式重构下变红的：一个连 message_type 属性都没有
    的裸对象喂进来，必须拦。所有既有用例喂的都是字段齐全的消息，那种
    重构在它们眼里全绿。
    """
    decision = compute_outbound_gate(object(), lambda: True)

    assert decision.allowed is False
    assert decision.reason is not None
    assert decision.absent_fields == (
        "message_type",
        "requires_confirmation",
        "severity",
        "recipient",
        "body",
        "confirmed_by",
    )


@pytest.mark.parametrize("message_type", sorted(REGISTERED_MESSAGE_TYPES))
@pytest.mark.parametrize("field_name", ["requires_confirmation", "severity", "body"])
@pytest.mark.parametrize("bad_kind", ["absent", "none", "empty"])
def test_registered_types_are_blocked_for_every_unknown_field_value(
    message_type, field_name, bad_kind
):
    """
    「已登记类型 × 判定字段 × {字段缺失, 字段为 None, 字段为空串}」的笛卡尔积
    （delivery-units §3.3 第 1 条）。新增一个消息类型时，参数化会强制作者
    面对每一种未知取值——这正是它铺满的意义。

    ⚠️ field_name 这一维**不含 confirmed_by**：那道闸在下一个 Task 才接上，
    放进来会让本 Task 的用例在实现尚未落地时就红。confirmed_by 的缺失 /
    None / 空白三态由 Task 4 的
    test_missing_or_blank_confirmer_is_blocked_awaiting_confirmation 与
    test_an_absent_confirmed_by_attribute_is_blocked_awaiting_confirmation 覆盖。

    ⚠️ 枚举用 REGISTERED_MESSAGE_TYPES（新增类型自动进入覆盖），
    但判据是字面量 False，不引用任何被测常量。
    """
    fields = {
        "message_type": message_type,
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }
    if bad_kind == "absent":
        del fields[field_name]
    elif bad_kind == "none":
        fields[field_name] = None
    else:
        fields[field_name] = ""

    decision = compute_outbound_gate(_Message(**fields), lambda: True)

    assert decision.allowed is False


def test_unregistered_message_type_is_blocked_with_its_own_reason():
    decision = compute_outbound_gate(
        _valid_message(message_type="offer_letter"), lambda: True
    )

    assert decision.allowed is False
    assert decision.reason == "未登记的消息类型"


@pytest.mark.parametrize("flag", [None, "", "false", "true", 0, 1, "False"])
def test_non_boolean_confirmation_flag_is_unknown_and_blocked(flag):
    """
    ⚠️ 严格 `is False` / `is True` 判定，不用真值性。
    字符串 "false" 的真值性是 True，"0" 也是 True——用 if flag: 写这条
    规则，一个字符串开关就把 fail-closed 变成了 fail-open。
    整数 0/1 同理：它们不是布尔，就是未知。
    """
    decision = compute_outbound_gate(
        _valid_message(requires_confirmation=flag), lambda: True
    )

    assert decision.allowed is False


def test_confirmation_flag_true_and_unknown_have_different_reasons():
    """
    "消息自称需要确认"与"这个标志读不出来"是两回事：前者是消息作者的
    显式意图，后者是消息畸形。6.5 按拦截原因统计时两者必须分得开。
    """
    explicit = compute_outbound_gate(
        _valid_message(requires_confirmation=True), lambda: True
    )
    unknown = compute_outbound_gate(
        _valid_message(requires_confirmation=None), lambda: True
    )

    assert explicit.allowed is False
    assert unknown.allowed is False
    assert explicit.reason != unknown.reason


@pytest.mark.parametrize("severity", [None, "", "  ", "critical", "LOW", "低", 3])
def test_unknown_severity_is_blocked(severity):
    """词表外的取值一律未知。大小写不同也算未知——不做归一化。"""
    decision = compute_outbound_gate(_valid_message(severity=severity), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "风险等级缺失或未登记"


def test_top_severity_is_blocked_with_its_own_reason():
    decision = compute_outbound_gate(_valid_message(severity="high"), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "风险等级为最高级"


def test_missing_ai_label_is_blocked():
    """《AI 生成合成内容标识办法》：拒信/邀约缺标识按拦截处理（tasks 4.4）。"""
    decision = compute_outbound_gate(
        _valid_message(body="很遗憾，本次未能与您继续推进。"), lambda: True
    )

    assert decision.allowed is False
    assert decision.reason == "缺少 AI 生成标识"


@pytest.mark.parametrize(
    "body",
    [
        "AI 生成：本文案由系统自动生成。",  # 缺【】书名号，不是那句标识
        "【AI生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。",  # 少一个空格
        "【AI 生成】",  # 只有标记头，没有那句话
        b"\xe3\x80\x90AI",  # 根本不是 str
    ],
)
def test_near_miss_labels_do_not_count_as_labelled(body):
    """
    近似但不相同的标识不算数。判据是 jd_agent 那句模板的不变前缀全量匹配
    （见 plan 的「需 Shao Peishen 拍板」D-1，当前取最严的一侧）。
    """
    decision = compute_outbound_gate(_valid_message(body=body), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "缺少 AI 生成标识"
