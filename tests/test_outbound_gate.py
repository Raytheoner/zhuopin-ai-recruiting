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
@pytest.mark.parametrize(
    "field_name", ["requires_confirmation", "severity", "recipient", "body"]
)
@pytest.mark.parametrize("bad_kind", ["absent", "none", "empty"])
def test_registered_types_are_blocked_for_every_unknown_field_value(
    message_type, field_name, bad_kind
):
    """
    「已登记类型 × 判定字段 × {字段缺失, 字段为 None, 字段为空串}」的笛卡尔积
    （delivery-units §3.3 第 1 条）。新增一个消息类型时，参数化会强制作者
    面对每一种未知取值——这正是它铺满的意义。

    ⚠️ field_name 这一维**不含 confirmed_by**：它属于第一道闸而不是消息
    自身的畸形，单独由 Task 4 的
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


def test_confirmation_flag_true_and_unknown_are_not_the_same_kind_of_block():
    """
    "消息自称需要确认"与"这个标志读不出来"是两回事，D-6 取 (b) 之后这个
    区别变成了**能不能被人清关**：

    - 自称需确认 = **已知的**高风险 → 带 confirmed_by 可放行
    - 标志读不出来 = 消息**畸形** → 终局拦截，人也清不掉（不知道它是什么，
      就没人能替它签字）
    """
    explicit_with_confirmer = compute_outbound_gate(
        _valid_message(requires_confirmation=True), lambda: True
    )
    unknown_with_confirmer = compute_outbound_gate(
        _valid_message(requires_confirmation=None), lambda: True
    )

    assert explicit_with_confirmer.allowed is True
    assert unknown_with_confirmer.allowed is False
    assert unknown_with_confirmer.reason == "确认标志缺失或取值未知"


@pytest.mark.parametrize("severity", [None, "", "  ", "critical", "LOW", "低", 3])
def test_unknown_severity_is_blocked(severity):
    """词表外的取值一律未知。大小写不同也算未知——不做归一化。"""
    decision = compute_outbound_gate(_valid_message(severity=severity), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "风险等级缺失或未登记"


def test_top_severity_without_a_confirmer_is_blocked_with_its_own_reason():
    """最高级 = 已知的高风险：没人签字就拦，且原因单列（6.5 要统计它）。"""
    decision = compute_outbound_gate(
        _valid_message(severity="high", confirmed_by=None), lambda: True
    )

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


from app.config import get_settings, is_candidate_outbound_enabled


def test_the_only_release_path():
    """
    tasks 4.7：放行的唯一路径 = 类型已登记 + requires_confirmation 显式为假
    + severity 已知非最高级 + 标识齐备 + 带 confirmed_by + 总开关开启。
    这条是全套用例里**唯一**一条 allowed is True，改动它等于改动红线。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: True)

    assert decision.allowed is True
    assert decision.reason is None
    assert decision.error is None


@pytest.mark.parametrize("confirmed_by", [None, "", "   ", 0, False, ["shao"]])
def test_missing_or_blank_confirmer_is_blocked_awaiting_confirmation(confirmed_by):
    """
    spec「人工确认才放行」：确认人标识为空的高风险消息 MUST 被拦截。
    空白串也算空——一个全是空格的 confirmed_by 不是人。
    """
    decision = compute_outbound_gate(
        _valid_message(confirmed_by=confirmed_by), lambda: True
    )

    assert decision.allowed is False
    assert decision.reason == "等待人工确认"


def test_an_absent_confirmed_by_attribute_is_blocked_awaiting_confirmation():
    """
    「属性根本不存在」这一态：Task 3 的笛卡尔积不覆盖 confirmed_by（那道闸
    当时还没接上），这里补齐。⛔ 缺属性走的必须是同一条拦截路径，
    不能因为读不到就掉进别的分支。
    """
    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
    }

    decision = compute_outbound_gate(_Message(**fields), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "等待人工确认"
    assert decision.absent_fields == ("confirmed_by",)


def test_confirmed_message_is_still_blocked_when_the_master_switch_is_off():
    """
    tasks 4.8 / spec「第二道结构性总开关」：总开关关闭时，**即便消息已携带
    人工确认人标识**也不外发，且 reason 与「等待人工确认」区分开。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: False)

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert decision.evidence["confirmed_by"] == "shao-peishen"
    assert decision.evidence["outbound_enabled"] is False


def test_awaiting_confirmation_wins_over_switch_off_so_the_observation_window_stays_readable():
    """
    判定顺序的锁定用例（见 plan 的 D-3）：没带确认人 + 总开关也关着时，
    reason 是「等待人工确认」而不是「外发总开关关闭」。

    为什么这条重要：U5 合并时总开关保持关闭（design 迁移计划第 4 步），
    那段观察期里**每一条**外发都会撞上关着的总开关。若总开关先判，
    观察期内所有拦截留痕的 reason 都是同一句话，"某类消息一直在被拦"
    这个 6.5 想回答的问题就永远读不出答案。
    """
    decision = compute_outbound_gate(_valid_message(confirmed_by=None), lambda: False)

    assert decision.allowed is False
    assert decision.reason == "等待人工确认"


@pytest.mark.parametrize("switch_value", ["true", "1", 1, "false", [], object()])
def test_only_the_literal_true_opens_the_switch(switch_value):
    """
    ⚠️ 开关回来的必须**恰好是 True 这个对象**。用真值性判断的话，
    一个返回字符串 "false" 的开关会把闸门打开——"false" 的真值性是 True。
    这正是 U1 的 _as_switch() 在配置那一侧堵的同一个洞（未知即关）。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: switch_value)

    assert decision.allowed is False


@pytest.mark.parametrize("not_callable", [True, False, 1, "true", None])
def test_a_non_callable_switch_is_structural_misuse_and_blocks(not_callable):
    """
    delivery-units §3.5 硬约束 1：⛔ 禁止在模块导入期、__init__ 里、或任何
    单例上把开关读成一个常量。传进来一个 bool 就是那个失败形状的现场——
    值是什么已经不重要，它已经被缓存过了。判拦截，并给一个能一眼看懂的原因。
    """
    decision = compute_outbound_gate(_valid_message(), not_callable)

    assert decision.allowed is False
    assert decision.reason == "外发总开关未以 callable 形式传入"


def test_switch_callable_is_invoked_exactly_once_per_decision():
    """
    "每次外发时求值"的两面：不能一次都不调（那就是缓存），也不能调多次
    （多次调用之间开关可能变，一次判定里出现两个不同的开关状态）。
    """
    calls = []

    def switch():
        calls.append(1)
        return True

    compute_outbound_gate(_valid_message(), switch)
    assert len(calls) == 1

    compute_outbound_gate(_valid_message(message_type="offer_letter"), switch)
    assert len(calls) == 2  # 被第一条规则拦下也照样求值，证据里要有它


def test_switch_flipped_at_runtime_takes_effect_on_the_next_decision():
    """
    spec「总开关运行期间被关闭」：此后的外发请求立即被拦截，**无需重启**。
    """
    state = {"on": True}

    def switch():
        return state["on"]

    assert compute_outbound_gate(_valid_message(), switch).allowed is True

    state["on"] = False

    second = compute_outbound_gate(_valid_message(), switch)
    assert second.allowed is False
    assert second.reason == "外发总开关关闭"


# ── 合规默认值必须真正参与求值 ──────────────────────────────────────────
#
# U1 那轮的教训（tasks.md「1.x 落地偏离登记」偏离 5 末段）：合规默认值被改
# 成 True 却无人发现，是因为所有用例都在喂桩、没有一条逼真实默认值参与。
# 下面这一对用例把 app/config.py 的**真实** is_candidate_outbound_enabled
# 接进门禁，且消息在其余六条上全部合格——只有这样才能走到最后一道闸，
# 让基线默认值 False 成为唯一的拦截理由。⛔ 不允许用喂空消息走 None 分支
# 的方式"覆盖"这条：那是把默认值绕过去，不是逼它求值。


@pytest.fixture
def _real_switch_env(tmp_path, monkeypatch):
    """把真实开关指到一个不存在的临时文件，清干净环境变量与 Settings 缓存。

    形状照抄 tests/test_config_audit_and_outbound.py 的 switch_path 夹具——
    那是 U1 已经验证过的隔离方式，别另起一套。
    """
    path = tmp_path / "candidate_outbound.switch"
    monkeypatch.setenv("CANDIDATE_OUTBOUND_SWITCH_FILE", str(path))
    monkeypatch.delenv("CANDIDATE_OUTBOUND_ENABLED", raising=False)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_real_config_baseline_default_closes_the_gate_for_an_otherwise_valid_message(
    _real_switch_env,
):
    """
    没有开关文件、没有环境变量 → 走到基线值 candidate_outbound_enabled=False。
    消息本身完全合格，所以拦截理由只可能来自那个默认值。

    变异验证：把 app/config.py 的
    `candidate_outbound_enabled: bool = False` 改成 True，本条必须变红。
    """
    decision = compute_outbound_gate(_valid_message(), is_candidate_outbound_enabled)

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert decision.evidence["outbound_enabled"] is False


def test_real_switch_file_opens_the_same_message_that_the_default_closed(
    _real_switch_env,
):
    """
    ⭐ 上一条的**阳性对照**，缺了它上一条就是"恒真"的：一条永远过不了
    其余六条的消息也会拿到 allowed is False，看不出默认值有没有参与。
    同一条消息、只把开关文件写上 true，就必须放行——两条合起来才证明
    "拦截确实是那个默认值造成的"。
    """
    _real_switch_env.parent.mkdir(parents=True, exist_ok=True)
    _real_switch_env.write_text("true", encoding="utf-8")

    decision = compute_outbound_gate(_valid_message(), is_candidate_outbound_enabled)

    assert decision.allowed is True
    assert decision.reason is None


def test_all_block_reasons_is_the_closed_set_u6_will_group_by():
    """
    U6 的 6.5「按 message_type 与拦截原因统计」要求原因取值来自一个有限
    集合。断言用字面量集合——加一个原因就该在这里显性变红，让作者顺手去
    U6 补一行统计口径，而不是让新原因静默地掉进"其他"桶里。
    """
    from app.outbound.gate import ALL_BLOCK_REASONS

    assert ALL_BLOCK_REASONS == frozenset(
        {
            "未登记的消息类型",
            "确认标志缺失或取值未知",
            "消息自称需要人工确认",
            "风险等级缺失或未登记",
            "风险等级为最高级",
            "缺少 AI 生成标识",
            "收件对象缺失或为空",
            "等待人工确认",
            "外发总开关关闭",
            "外发总开关未以 callable 形式传入",
            "门禁判定内部异常",
        }
    )


def test_exception_inside_the_gate_is_treated_as_a_block_not_a_leak():
    """
    tasks 4.3 / spec「门禁判定自身抛错」：按拦截处理，MUST NOT 因判定失败
    而放行。异常穿透到调用方，调用方一个 `except: pass` 就是 fail-open。
    """

    class _Exploding:
        message_type = "rejection_letter"
        requires_confirmation = False
        severity = "low"
        recipient = "candidate-42"
        confirmed_by = "shao-peishen"

        @property
        def body(self):
            raise RuntimeError("读 body 炸了")

    decision = compute_outbound_gate(_Exploding(), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "门禁判定内部异常"
    assert "读 body 炸了" in decision.error


def test_an_exception_type_nobody_enumerated_still_closes_the_gate():
    """
    ⛔ 契约由结构保证，不靠枚举异常类型。U1 的枚举法失败过两次
    （tasks.md 偏离 5）：round 1 漏了 OSError 之外的类型，round 2 被 NUL
    字节路径的裸 ValueError 逃掉。这里直接抛一个本仓库里根本不存在的
    异常类型，闸门照样必须关。
    """

    class _NobodyEverHeardOfThis(Exception):
        pass

    def switch():
        raise _NobodyEverHeardOfThis("全新的失败形状")

    decision = compute_outbound_gate(_valid_message(), switch)

    assert decision.allowed is False
    assert decision.reason == "门禁判定内部异常"


def test_keyboard_interrupt_is_not_swallowed():
    """
    兜底只抓 Exception，⛔ 不抓 BaseException：把 KeyboardInterrupt /
    SystemExit 吞成一条"拦截"会让进程杀不掉，那不是 fail-closed，是挂死。
    """

    def switch():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        compute_outbound_gate(_valid_message(), switch)


@pytest.mark.parametrize(
    "message_factory",
    [
        lambda: _valid_message(),
        lambda: _valid_message(severity="high"),
        lambda: _Message(),
        lambda: object(),
    ],
)
def test_repeated_evaluation_is_identical(message_factory):
    """tasks 4.9 / spec「重复判定结果一致」。"""
    message = message_factory()

    first = compute_outbound_gate(message, lambda: True)
    second = compute_outbound_gate(message, lambda: True)

    assert first == second


def test_judging_writes_nothing_to_disk(tmp_path, monkeypatch):
    """
    tasks 4.9 后半句：判定过程无任何持久化写入与消息投递。
    在一个空目录里当工作目录跑一遍判定，目录必须一个文件都不多。
    """
    monkeypatch.chdir(tmp_path)
    before = {p.name for p in tmp_path.iterdir()}

    compute_outbound_gate(_valid_message(), lambda: True)
    compute_outbound_gate(object(), lambda: False)

    assert {p.name for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "recipient", ["", "   ", None, 0, ["candidate-42"], {"open_id": "ou_x"}]
)
def test_unknown_recipient_is_blocked_per_the_2026_08_28_ruling(recipient):
    """
    ⚠️ **口径锁定用例。批准人：Shao Peishen｜时间：2026-08-28｜事项：plan 的
    D-2 取最保险一侧。**

    spec「fail-closed 判定语义」把拦截条件逐条列成六条，recipient 不在其中，
    本单元最初按 spec 字面落成"只进证据、不参与判定"。2026-08-28 拍板改为
    **第七条拦截规则**：收件对象读不出一个非空字符串就是未知，未知即拦截。

    方向是更严（拦得更多），不放松任何闸门——代价是误拦，而误拦由 U6 的
    6.5 按 message_type 与拦截原因统计兜底观测。

    ⚠️ 非字符串收件人（dict / list）同样判未知：门禁不猜"这个结构里哪个
    键是收件人"。U5 的适配器负责把渠道对象拍平成字符串再喂进来。

    ⛔ 这条比已批准的 spec 条件清单多一条规则，`specs/outbound-approval-gate`
    需要同步补第七条——见 plan 的 D-2 记录。
    """
    decision = compute_outbound_gate(_valid_message(recipient=recipient), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "收件对象缺失或为空"


def test_absent_recipient_attribute_is_blocked_too():
    """「属性根本不存在」这一态：与空串走同一条拦截路径。"""
    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }

    decision = compute_outbound_gate(_Message(**fields), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "收件对象缺失或为空"
    assert decision.absent_fields == ("recipient",)


# ── review round 1 修复的回归钉子 ────────────────────────────────────────
#
# 全部来自 2026-08-28 的一轮 code review。⚠️ 八条发现里**没有一条是 fail-open**
# ——每一条路径都仍然拦。它们伤的是**证据保真度与可观测性**：留痕是这道闸
# 事后可解释的唯一材料，证据被抹掉或归错因，等于闸拦住了但说不清为什么拦。


def test_unhashable_message_type_gets_the_clean_reason_not_an_internal_error():
    """
    review 发现 1：`x not in frozenset` 对不可哈希取值抛 TypeError，于是一个
    没拍平的 list 类型会掉进"门禁判定内部异常"、**证据被整片抹成 None**。

    这个形状不是假想：D-2 的收件人规则就是照着"没拍平的渠道对象"写的，
    同一个调用方同样会把 message_type 传成 list。未登记就是未登记，
    该给的原因和证据一个都不能少。
    """
    decision = compute_outbound_gate(
        _valid_message(message_type=["rejection_letter"]), lambda: True
    )

    assert decision.allowed is False
    assert decision.reason == "未登记的消息类型"
    assert decision.evidence["message_type"] == "['rejection_letter']"


class _ExplodingEq:
    """比较时抛错——用来让异常发生在**判定阶段**（证据已采齐之后）。"""

    def __eq__(self, other):
        raise RuntimeError("severity 比较炸了")

    def __hash__(self):
        return 0

    def __repr__(self):
        return "<exploding severity>"


def test_internal_error_after_collection_keeps_the_evidence_it_already_had():
    """
    review 发现 2：兜底外壳原来无条件把证据重建成全 None，哪怕采集阶段
    早就成功了。U6 的 6.5「按 message_type 与拦截原因统计」于是完全 group
    不了这批记录——最需要解释的一类拦截，反而是留痕最空的一类。
    """
    decision = compute_outbound_gate(_valid_message(severity=_ExplodingEq()), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "门禁判定内部异常"
    assert decision.evidence["message_type"] == "rejection_letter"
    assert decision.evidence["severity"] == "<exploding severity>"
    assert decision.evidence["confirmed_by"] == "shao-peishen"
    assert "severity 比较炸了" in decision.error


def test_cached_switch_is_reported_as_such_even_on_a_draft_awaiting_approval():
    """
    review 发现 4：`REASON_SWITCH_NOT_CALLABLE` 原来排在最后一条，于是一个
    把开关缓存成值的调用方（`is_candidate_outbound_enabled()` 带括号求值）
    在整个 U5 观察期里都被报成"等待人工确认"——**这道守护恰恰在它该响的
    时候是哑的**。

    它是调用方的编程错误，不是消息的畸形，所以最优先报。
    """
    decision = compute_outbound_gate(_valid_message(confirmed_by=None), True)

    assert decision.allowed is False
    assert decision.reason == "外发总开关未以 callable 形式传入"
    # 结构性误用也要留证据，不能因为提前返回就把消息字段丢了
    assert decision.evidence["message_type"] == "rejection_letter"


def test_switch_returning_none_and_a_cached_switch_are_told_apart():
    """review 发现 5：两者原来在证据里都是 None，靠 reason 区分开。"""
    returned_none = compute_outbound_gate(_valid_message(), lambda: None)
    cached = compute_outbound_gate(_valid_message(), True)

    assert returned_none.reason == "外发总开关关闭"
    assert cached.reason == "外发总开关未以 callable 形式传入"


def test_a_property_that_raises_attributeerror_is_not_reported_as_absent():
    """
    review 发现 6：`_read` 原来把 property 内部抛的 AttributeError 也当成
    "这个属性不存在"，于是一个坏掉的 recipient getter 在留痕里被写成
    "调用方忘了设收件人"——审计拿着这条记录会去查错方向。

    属性**是存在的**，是它的 getter 自己炸了：按判定内部异常处理。
    """

    class _BrokenGetter:
        message_type = "rejection_letter"
        requires_confirmation = False
        severity = "low"
        body = _LABELLED_BODY
        confirmed_by = "shao-peishen"

        @property
        def recipient(self):
            raise AttributeError("下游 lookup 挂了")

    decision = compute_outbound_gate(_BrokenGetter(), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "门禁判定内部异常"
    assert "recipient" not in decision.absent_fields
    assert "下游 lookup 挂了" in decision.error


def test_a_genuinely_absent_attribute_is_still_reported_as_absent():
    """发现 6 的阳性对照：真的没设这个属性时，仍然要走 absent 那条路。"""
    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }

    decision = compute_outbound_gate(_Message(**fields), lambda: True)

    assert decision.reason == "收件对象缺失或为空"
    assert decision.absent_fields == ("recipient",)


def test_confirmed_by_clears_a_known_high_risk_block_per_d6_option_b():
    """
    ⚠️ **口径锁定用例。批准人：Shao Peishen｜时间：2026-08-28｜事项：D-6 取 (b)。**

    spec 的模型：fail-closed 条件把消息**分级**，`confirmed_by` 是**清关**。
    「高风险消息 SHALL 仅在携带 confirmed_by 时才被放行外发」+ Scenario
    「人工放行」明写草稿带确认人重走门禁、两道闸都过即外发。

    取 (b) 之前这两条是终局拦截，`queue.approve()` 带着 confirmed_by 重走
    门禁仍被拦——待审批队列里的候选人信件永远发不出去，人工放行路径整体
    失效。这正是本变更包立项要建的能力。
    """
    self_declared = compute_outbound_gate(
        _valid_message(requires_confirmation=True), lambda: True
    )
    top_severity = compute_outbound_gate(_valid_message(severity="high"), lambda: True)

    assert self_declared.allowed is True
    assert top_severity.allowed is True


@pytest.mark.parametrize(
    "malformed",
    [
        {"message_type": "offer_letter"},
        {"requires_confirmation": None},
        {"severity": "critical"},
        {"body": "没有标识的正文"},
        {"recipient": ""},
    ],
)
def test_confirmed_by_cannot_clear_a_malformed_message(malformed):
    """
    ⭐ **(b) 的另一半，比放行那一半更重要**：可清关的只有「已知的高风险」
    两条。消息**畸形**的五条——未登记类型、标志读不出、等级读不出、缺
    AI 标识、收件人读不出——依旧终局，**人也清不掉**。

    理由：签字的前提是知道自己在签什么。一条读不出风险等级的消息，没有人
    能对它做出有意义的确认；允许 confirmed_by 清掉畸形，等于把人工确认
    变成"随便谁点一下就能发任何东西"的橡皮图章。
    """
    decision = compute_outbound_gate(_valid_message(**malformed), lambda: True)

    assert decision.allowed is False
    assert decision.evidence["confirmed_by"] == "shao-peishen"


def test_a_plain_letter_without_a_confirmer_reports_exactly_the_spec_wording():
    """
    spec Scenario「未带确认人的高风险消息」逐字：拦截原因为「等待人工确认」。
    这条喂的就是那个场景的输入——一封没有自称需确认、等级也不是最高级的
    普通拒信，缺的只有确认人。
    """
    decision = compute_outbound_gate(_valid_message(confirmed_by=None), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "等待人工确认"


def test_the_reason_says_why_it_was_high_risk_when_there_is_no_confirmer():
    """
    没有确认人时，原因仍要说清**为什么是高风险**，而不是一律折成
    「等待人工确认」——U5 合并后的观察期与 U6 的 6.5 都靠这个分布做判断
    （与 D-3 同一条理由）。证据里也留着触发字段的原始取值。
    """
    self_declared = compute_outbound_gate(
        _valid_message(requires_confirmation=True, confirmed_by=None), lambda: True
    )
    top_severity = compute_outbound_gate(
        _valid_message(severity="high", confirmed_by=None), lambda: True
    )

    assert self_declared.reason == "消息自称需要人工确认"
    assert top_severity.reason == "风险等级为最高级"


def test_a_cleared_high_risk_message_is_still_stopped_by_the_master_switch():
    """两道闸是**串联**的：人签了字，总开关关着照样不发（spec 4.8 不变）。"""
    decision = compute_outbound_gate(_valid_message(severity="high"), lambda: False)

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
