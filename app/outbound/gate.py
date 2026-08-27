"""候选人外发门禁的判定纯函数。

⛔ 本模块**不得** import `app.config` / `app.storage` / `app.channels` /
`app.graph` / `app.audit` / `app.web` / `sqlite3`（结构测试机器可查）。
外发总开关由调用方以**零参 callable** 形式传入——`app/config.py` 的
`is_candidate_outbound_enabled` 就是为此做成函数而不是常量的
（Shao Peishen 2026-08-26 拍板：允许热改、不重启生效）。

⛔ 本模块**禁止出现带默认值的属性读取**（`getattr(x, k, <default>)` /
`<dict>.get(k, <default>)`）：取不到就是未知，未知就是拦截。默认值这个
概念本身与 fail-closed 互斥（delivery-units §3.3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agents.jd_agent import AI_LABEL_TEMPLATE
from app.outbound.contracts import (
    GATE_FIELDS,
    KNOWN_SEVERITIES,
    MAX_SEVERITY,
    REGISTERED_MESSAGE_TYPES,
)

# AI 生成标识的不变前缀：把模板里的 {generated_at} 之前的部分取出来当作
# 判定依据（生成时间每封信都不同，不能参与匹配）。
# ⚠️ tasks 4.4：**复用** jd_agent 的机制，不另写一套。这里 import 的是同一个
# 常量对象本身，结构测试 test_ai_label_source_is_the_jd_agent_constant 用
# `is` 钉住这一点。
_LABEL_PLACEHOLDER = "{generated_at}"
AI_LABEL_PREFIX: str = AI_LABEL_TEMPLATE.partition(_LABEL_PLACEHOLDER)[0]

# 证据字典的固定键序。body 不在其中——正文是候选人可识别内容，只记
# "标识在不在"这个判定结果；正文指纹由 U5 的 content_hash 承担。
EVIDENCE_KEYS: tuple[str, ...] = (
    "message_type",
    "requires_confirmation",
    "severity",
    "recipient",
    "confirmed_by",
    "ai_label_present",
    "outbound_enabled",
)


# 拦截原因。取值是**中文字面量**而不是英文枚举码：spec 逐字写了
# 「外发总开关关闭」与「等待人工确认」两条要能区分开，U5 会把它原样写进
# pending_approval.blocked_reason，U6 的 6.5 直接 GROUP BY 这一列。
REASON_UNREGISTERED_TYPE = "未登记的消息类型"
REASON_CONFIRMATION_FLAG_UNKNOWN = "确认标志缺失或取值未知"
REASON_CONFIRMATION_REQUIRED = "消息自称需要人工确认"
REASON_SEVERITY_UNKNOWN = "风险等级缺失或未登记"
REASON_SEVERITY_MAX = "风险等级为最高级"
REASON_MISSING_AI_LABEL = "缺少 AI 生成标识"
# 第七条。⚠️ spec 的 fail-closed 条件清单只列了六条，recipient 不在其中；
# 本条是 2026-08-28 Shao Peishen 拍板「五项口径一律取最保险一侧」的产物。
# 方向是更严（拦得更多），不放松任何闸门。specs/outbound-approval-gate
# 需同步补这一条，见 plan 的 D-2 记录。
REASON_RECIPIENT_UNKNOWN = "收件对象缺失或为空"
REASON_AWAITING_CONFIRMATION = "等待人工确认"
REASON_OUTBOUND_DISABLED = "外发总开关关闭"
REASON_SWITCH_NOT_CALLABLE = "外发总开关未以 callable 形式传入"
REASON_GATE_ERROR = "门禁判定内部异常"

# U6 的 6.5 按拦截原因统计，需要一个封闭集合。新增原因必须同时加进这里。
ALL_BLOCK_REASONS: frozenset[str] = frozenset(
    {
        REASON_UNREGISTERED_TYPE,
        REASON_CONFIRMATION_FLAG_UNKNOWN,
        REASON_CONFIRMATION_REQUIRED,
        REASON_SEVERITY_UNKNOWN,
        REASON_SEVERITY_MAX,
        REASON_MISSING_AI_LABEL,
        REASON_RECIPIENT_UNKNOWN,
        REASON_AWAITING_CONFIRMATION,
        REASON_OUTBOUND_DISABLED,
        REASON_SWITCH_NOT_CALLABLE,
        REASON_GATE_ERROR,
    }
)


class _Absent:
    """"这个属性根本不存在"的哨兵。

    ⛔ 不用 None 表示缺失：spec 要求留痕能区分"没给这个字段"与"给了个空
    值"，两者在证据 dict 里都落成 null，区别由 GateDecision.absent_fields
    承载。用 None 当哨兵，这个区别当场消失。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 只为调试可读
        return "<absent>"


_ABSENT = _Absent()


@dataclass(frozen=True)
class GateDecision:
    """一次门禁判定的完整事实。

    `evidence` 是**扁平的、json.dumps 得动的** dict —— U5 直接把它塞进
    `DecisionEvent.evidence`，⛔ 不重新求值一遍（tasks 4.2）。重新求值会
    制造"判定时未知、留痕时又变成已知"的不一致（design D4）。
    """

    allowed: bool
    reason: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    absent_fields: tuple[str, ...] = ()
    error: str | None = None


def _defines_attribute(message: object, name: str) -> bool:
    """这个名字在实例字典或类型的 MRO 上有没有定义——**不触发描述符**。

    只服务 `_read` 的一个判断：AttributeError 到底是"没有这个属性"，还是
    "属性有，但它的 getter 自己抛了 AttributeError"。
    """
    try:
        if name in vars(message):
            return True
    except TypeError:
        pass  # 没有 __dict__（__slots__ / 裸 object()）——继续看类型
    return any(name in vars(klass) for klass in type(message).__mro__)


def _read(message: object, name: str) -> Any:
    """读一个属性，**确实没有**才返回 `_ABSENT`。

    ⛔ 用两参 `getattr` + `except AttributeError`，**不用三参 getattr**。
    三参写法把"没有这个属性"和"属性值恰好等于那个默认值"折成同一件事，
    fail-closed 当场变 fail-open（delivery-units §3.3 点名的那种一行重构）。

    ⚠️ AttributeError 有两个来源，必须分开（review round 1 发现 6）：
    - 对象上根本没有这个名字 → `_ABSENT`，如实记进 absent_fields
    - 名字有、但它的 property getter 自己抛了 AttributeError → **原样向上抛**，
      由外壳兜成"门禁判定内部异常"。把后者也记成"缺失"，留痕就会把一个
      坏掉的 getter 写成"调用方忘了设这个字段"，审计拿着它查错方向。

    属性是个会抛**别的**异常的 property 时，异常同样原样向上抛。
    """
    try:
        return getattr(message, name)
    except AttributeError:
        if _defines_attribute(message, name):
            raise
        return _ABSENT


def _json_safe(value: Any) -> Any:
    """把任意取值折成 json.dumps 认识的形状，信息不丢。

    缺失折成 None（U2 的 DecisionEvent.evidence 是扁平 dict）；str / int /
    float / bool / None 原样保留；其余一律 repr()——U5 的 JSONL append 因
    一个奇怪的字段值而抛错，是本可避免的故障。
    """
    if value is _ABSENT:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _has_ai_label(body: Any) -> bool:
    """正文里有没有 AI 生成标识。非字符串正文一律判"没有"（未知即拦截）。"""
    return isinstance(body, str) and AI_LABEL_PREFIX in body


def _collect(
    message: object, outbound_enabled: Any
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    """一次采齐：原始取值、证据字典、缺失字段清单。

    ⚠️ **不跟着判定短路**：被第一条规则拦下的消息，其余字段的原始取值
    同样要在证据里，否则留痕读不出是哪一条 fail-closed 触发的。
    """
    raw = {name: _read(message, name) for name in GATE_FIELDS}
    absent_fields = tuple(name for name in GATE_FIELDS if raw[name] is _ABSENT)

    # 开关在这里求值，**每次判定恰好一次**。放在采集阶段而不是判定末尾，
    # 是为了让证据里恒有它的原始取值（哪怕消息先被别的规则拦下）。
    switch_is_callable = callable(outbound_enabled)
    if switch_is_callable:
        switch_raw: Any = outbound_enabled()
    else:
        switch_raw = _ABSENT

    evidence = {
        "message_type": _json_safe(raw["message_type"]),
        "requires_confirmation": _json_safe(raw["requires_confirmation"]),
        "severity": _json_safe(raw["severity"]),
        "recipient": _json_safe(raw["recipient"]),
        "confirmed_by": _json_safe(raw["confirmed_by"]),
        "ai_label_present": _has_ai_label(raw["body"]),
        "outbound_enabled": _json_safe(switch_raw),
    }
    raw["_switch"] = switch_raw
    raw["_switch_is_callable"] = switch_is_callable
    return raw, evidence, absent_fields


def _decide(
    raw: dict[str, Any], evidence: dict[str, Any], absent_fields: tuple[str, ...]
) -> GateDecision:
    """按证据判定，**可能抛异常**——外壳 compute_outbound_gate 统一兜成拦截。

    ⚠️ 与采集分开是刻意的（review round 1 发现 2）：采集成功、判定抛错时，
    外壳还拿得到那份已经采齐的证据，不必把它重建成一片 None。

    判定顺序是契约的一部分（见 plan 的 D-3）：**消息自身的六条 fail-closed
    先判，两道闸最后判**。理由是 design 迁移计划第 4 步——U5 合并时总开关
    保持关闭、全拦，要靠这段观察期看拦截留痕是否符合预期；总开关若先判，
    观察期内每一条拦截的 reason 都是"外发总开关关闭"，把其余五条真正的
    畸形消息全部盖住，观察期当场失去意义。
    """
    def blocked(reason: str) -> GateDecision:
        return GateDecision(
            allowed=False, reason=reason, evidence=evidence, absent_fields=absent_fields
        )

    # ⓪ 调用方把开关缓存成了值——这是**编程错误**，不是消息的畸形，最优先
    #    报（review round 1 发现 4）。排在后面的话，一个没带 confirmed_by 的
    #    草稿会把它盖成"等待人工确认"，这道守护恰好在该响的时候是哑的。
    if raw["_switch_is_callable"] is False:
        return blocked(REASON_SWITCH_NOT_CALLABLE)

    # ① 未知类型即拦截。_ABSENT 不在集合里，缺属性天然落进这一条。
    #    ⚠️ isinstance 前置：`x not in frozenset` 对不可哈希取值（没拍平的
    #    list/dict）抛 TypeError，会把一条本该干干净净"未登记"的拦截打成
    #    内部异常（review round 1 发现 1）。未登记就是未登记。
    if (
        not isinstance(raw["message_type"], str)
        or raw["message_type"] not in REGISTERED_MESSAGE_TYPES
    ):
        return blocked(REASON_UNREGISTERED_TYPE)

    # ② 严格布尔。⛔ 不用真值性：字符串 "false" 的真值性是 True。
    #    ⚠️ 读不出布尔 = 消息畸形 = 终局拦截；读得出的 True 是"已知高风险"，
    #    走第 ⑥ 条由人清关。两者的差别就是"能不能被签字清掉"。
    flag = raw["requires_confirmation"]
    if flag is not True and flag is not False:
        return blocked(REASON_CONFIRMATION_FLAG_UNKNOWN)

    # ⚠️ 这里**没有**「flag is True 即拦」这一条了（D-6 取 (b)，2026-08-28
    #    Shao Peishen 拍板）。自称需确认与最高级是**已知的高风险**，按 spec
    #    「高风险消息 SHALL 仅在携带 confirmed_by 时才被放行外发」，它们由
    #    第一道闸的人来清关，见第 ⑥ 条。改成终局拦截会让 queue.approve()
    #    带着 confirmed_by 重走门禁仍被拦——待审批队列里的信件永远发不出去。

    # ③ 风险等级必须是词表里的字符串。不做大小写归一化、不 strip——
    #    归一化就是在猜作者的意图，而未知即拦截不允许猜。
    severity = raw["severity"]
    if severity not in KNOWN_SEVERITIES:
        return blocked(REASON_SEVERITY_UNKNOWN)

    # ④ AI 生成标识（tasks 4.4，复用 jd_agent 的模板）。
    if not evidence["ai_label_present"]:
        return blocked(REASON_MISSING_AI_LABEL)

    # ⑤ 收件对象必须是一个非空字符串（2026-08-28 拍板新增，见常量处注释）。
    #    ⛔ 非字符串同样判未知：门禁不猜"这个结构里哪个键是收件人"，
    #    拍平成字符串是 U5 适配器的活。
    recipient = raw["recipient"]
    if not isinstance(recipient, str) or not recipient.strip():
        return blocked(REASON_RECIPIENT_UNKNOWN)

    # ── 以上五条是**消息畸形**：读不出它是什么，⛔ 人也不能清关。
    #    签字的前提是知道自己在签什么；允许 confirmed_by 清掉畸形，人工确认
    #    就成了"随便谁点一下就能发任何东西"的橡皮图章。
    #    以下是两道闸，处理的是**已知**的高风险。

    # ⑥ 第一道闸：人工确认。spec「人工确认才放行」——确认人标识为空的
    #    高风险消息 MUST 被拦截。空白串不是人。
    #    ⚠️ 没有确认人时，原因要说清**为什么是高风险**，不一律折成
    #    「等待人工确认」——U5 观察期与 U6 的 6.5 靠这个分布做判断（同 D-3）。
    confirmed_by = raw["confirmed_by"]
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        if flag is True:
            return blocked(REASON_CONFIRMATION_REQUIRED)
        if severity == MAX_SEVERITY:
            return blocked(REASON_SEVERITY_MAX)
        return blocked(REASON_AWAITING_CONFIRMATION)

    # ⑦ 第二道闸：外发总开关。只有**恰好是 True** 才算开。
    #    ⚠️ 两道闸**串联**：人签了字，总开关关着照样不发。⛔ 不用真值性：字符串 "false" 的真值性
    #    是 True，一个字符串开关就能把闸门打开。与 U1 的 _as_switch()
    #    在配置那一侧的口径一致——未知即关。
    if raw["_switch"] is not True:
        return blocked(REASON_OUTBOUND_DISABLED)

    return GateDecision(
        allowed=True, reason=None, evidence=evidence, absent_fields=absent_fields
    )


def compute_outbound_gate(
    message: object, outbound_enabled: Callable[[], bool]
) -> GateDecision:
    """候选人外发门禁判定。纯函数：不写库、不发消息、不读配置文件。

    Args:
        message: 待外发消息。**任何形状都合法**——连属性都没有的裸对象
            也必须能喂进来（fail-closed 的输入面），它会被判拦截并带着
            完整证据返回，而不是抛错。
        outbound_enabled: **零参 callable**，每次判定恰好被调用一次
            （spec：总开关 MUST 在每次外发时求值，MUST NOT 启动时缓存）。
            ⛔ 传 bool 属结构性误用，按拦截处理。

    Returns:
        GateDecision。`allowed is True` 是唯一的放行信号；⛔ 调用方不得用
        真值性判断这个对象本身（GateDecision 实例恒为真）。
    """
    evidence: dict[str, Any] = {key: None for key in EVIDENCE_KEYS}
    absent_fields: tuple[str, ...] = ()
    try:
        raw, evidence, absent_fields = _collect(message, outbound_enabled)
        return _decide(raw, evidence, absent_fields)
    except Exception as exc:  # noqa: BLE001 —— 见下方注释，这里就是要抓全部
        # ⛔ 契约由**结构**保证，不靠枚举异常类型。U1 的
        # is_candidate_outbound_enabled() 用枚举法失败过两次（round 1 漏
        # OSError 之外的类型，round 2 被 NUL 字节路径的裸 ValueError 逃掉，
        # 见 tasks.md「1.x 落地偏离登记」偏离 5），最后改成同一个形状：
        # 内部逻辑整体委托给一个私有函数，外层只做一件事——不管里面抛出
        # 什么类型（哪怕是完全没预料到的新类型），一律截停判拦截。
        # 之后任何人往判定里加一段没包线的新异常来源，都不需要再补一轮修复。
        # ⚠️ 证据用**已经采到的那份**，不是重建一片 None（review round 1
        # 发现 2）：采集成功、判定抛错是最常见的形状，把证据抹掉会让 U6 的
        # 6.5 完全 group 不了这批记录——最需要解释的一类拦截留痕最空。
        # 采集阶段自己就抛了的话，这两个变量仍是上面的基线值。
        return GateDecision(
            allowed=False,
            reason=REASON_GATE_ERROR,
            evidence=evidence,
            absent_fields=absent_fields,
            error=repr(exc),
        )
