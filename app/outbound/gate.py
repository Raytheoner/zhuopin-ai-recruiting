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
from app.outbound.contracts import GATE_FIELDS

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


def _read(message: object, name: str) -> Any:
    """读一个属性，读不到返回 `_ABSENT`。

    ⛔ 用两参 `getattr` + `except AttributeError`，**不用三参 getattr**。
    三参写法把"没有这个属性"和"属性值恰好等于那个默认值"折成同一件事，
    fail-closed 当场变 fail-open（delivery-units §3.3 点名的那种一行重构）。

    属性是个会抛别的异常的 property 时，异常原样向上抛——由
    `compute_outbound_gate()` 的外壳统一兜成"拦截"。
    """
    try:
        return getattr(message, name)
    except AttributeError:
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
    if callable(outbound_enabled):
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
    return raw, evidence, absent_fields


def compute_outbound_gate(
    message: object, outbound_enabled: Callable[[], bool]
) -> GateDecision:
    """候选人外发门禁判定。本 Task 只到证据采集，判定在下一个 Task 接上。"""
    _raw, evidence, absent_fields = _collect(message, outbound_enabled)
    return GateDecision(
        allowed=False, reason=None, evidence=evidence, absent_fields=absent_fields
    )
