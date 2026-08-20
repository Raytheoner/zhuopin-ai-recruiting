from __future__ import annotations

import hashlib
import threading
from collections import Counter
from dataclasses import dataclass

from app.schemas.job_profile import SYSTEM_MANAGED_FIELDS, JobProfile

# 重问前缀：让"这个你刚才没答"在文本通道里也看得见。判定 is_reask 属第 5 章
# （tasks 5.4），本模块只负责一旦置了标记就渲染出来。
_REASK_PREFIX = "（这个你刚才没答）"

# 档位在纯文本通道里的呈现。"以下为 AI 建议"是《AI 生成合成内容标识办法》
# （2025-09-01 施行）的要求：候选档位是 AI 生成内容，渲染出来就必须带标识。
# 第 4 章给 Web 通道做可点选控件时同样要带标识（tasks 4.3），两处各自负责
# 自己的呈现形态，标识不能只做一处。
_OPTIONS_PREFIX = "可选（以下为 AI 建议选项，也可自由作答）："
_OPTIONS_SEPARATOR = " / "

# field 缺失时的 id 前缀。带前缀是为了在库里一眼看出"这个问题没有目标字段"，
# 而不是让它和真实字段名混在一起。
_FREE_ID_PREFIX = "free:"

# 允许作为追问目标的字段 = JobProfile 的字段表减去系统管理字段。
# 从 model_fields 取而不是手写一份：手写的清单会和 schema 悄悄漂移，而这里
# 一旦漂移，合法字段会被当成野字段降级——降级不报错，没人会发现。
QUESTION_TARGET_FIELDS: frozenset[str] = frozenset(JobProfile.model_fields) - SYSTEM_MANAGED_FIELDS

# question_id 派生的降级计数。第 8 章 8.1 回放时看比例用：野 field 占比高
# 说明 SYSTEM_PROMPT 的字段表没被模型遵守，null-field 占比高说明模型普遍
# 拿不准目标字段——两者都会让第 5 章的重问追踪失效，必须看得见。
#
# 只在进程内累计、不进日志：野 field 名是模型自由生成的不可信字符串，
# 打进日志既可能撞上 RedactionFilter 的高危键名正则，也没有留存价值——
# 需要的是比例，不是每一次的现场。
#
# 计数上界注意事项（不在本 Task 范围内修复，读 8.1 回放数字前必须知道）：
# LangGraph 恢复时节点从头整个重跑（工程铁律 1）。derive_question_id 是
# L3 纯函数边界内唯一的例外——它靠这个模块全局计数器做统计，本身带副
# 作用。节点被重跑时会对同一次真实事件重复调用，计数器无法区分"重跑"
# 与"首次执行"，于是重复计数。8.1 回放读到的降级比例因此是**降级次数
# 的上界，不是精确的一次性事件计数**。
_METRICS_LOCK = threading.Lock()
_metrics_counter: Counter = Counter()

# 部署形态是 Windows 计划任务/常驻进程，只在失败时重启、不会被例行回收
# （04-部署与门户挂载.md）。unknown_field:<name> 的 key 由模型自由生成的
# 字符串（拼错、幻觉字段名）驱动，不设上限的话进程存活越久、明细 key 越
# 多，是一处无界内存增长点。这里只给"明细"设上限：聚合计数 unknown_field
# 本身不受影响，永远精确；超过上限的新字段名统一并入下面的溢出桶。
_MAX_UNKNOWN_FIELD_NAMES = 50
# 溢出桶的 key。名字刻意用双下划线包起来、不像任何真实字段名，避免 8.1
# 回放的人把它当成模型真的幻觉出了一个叫 "__overflow__" 的字段来读。
_UNKNOWN_FIELD_OVERFLOW_KEY = "unknown_field:__overflow__"
_tracked_unknown_field_names: set[str] = set()


def _record_question_id_metric(kind: str, field_name: str | None = None) -> None:
    with _METRICS_LOCK:
        _metrics_counter[kind] += 1
        if field_name is not None:
            if (
                field_name in _tracked_unknown_field_names
                or len(_tracked_unknown_field_names) < _MAX_UNKNOWN_FIELD_NAMES
            ):
                _tracked_unknown_field_names.add(field_name)
                _metrics_counter[f"unknown_field:{field_name}"] += 1
            else:
                _metrics_counter[_UNKNOWN_FIELD_OVERFLOW_KEY] += 1


def question_id_metrics() -> dict[str, int]:
    """派生指标快照。键：total / null_field / unknown_field，以及每个被拒绝的
    字段名一条 `unknown_field:<name>`（只在进程内，不落库不进日志）。

    `unknown_field:<name>` 明细最多保留 `_MAX_UNKNOWN_FIELD_NAMES`（50）个
    不同字段名；超出的新字段名一律并入 `unknown_field:__overflow__`——这是
    一个溢出桶，不是某个真实字段名，读数时不要当成模型幻觉出了一个叫
    `__overflow__` 的字段。聚合计数 `unknown_field` 不受这个上限影响，永远
    精确等于野 field 的总次数。

    注意：LangGraph 节点重跑（工程铁律 1）会让同一次真实事件被重复调用
    derive_question_id、从而重复计数——这里的数字是降级次数的**上界**，
    不是精确的一次性事件计数，8.1 回放读比例时按此口径解读。
    """
    with _METRICS_LOCK:
        return dict(_metrics_counter)


def reset_question_id_metrics() -> None:
    """测试与 8.1 回放前清零。生产路径不调用。"""
    with _METRICS_LOCK:
        _metrics_counter.clear()
        _tracked_unknown_field_names.clear()


def derive_question_id(field: str | None, text: str) -> str:
    """
    question_id 由系统按目标字段派生，不让模型自己编（design.md 决策 2）。

    field 必须是 JobProfile 字段表里的真实字段（QUESTION_TARGET_FIELDS）。
    不在表里的一律按"无 field"降级走文本哈希分支。为什么这是硬要求而不是
    洁癖：第 3 章的 is_productive 按"有没有问出未问过的 question_id"取值，
    模型每轮幻觉一个新字段名就每轮产出一个"新" id，于是每一轮都被判成有
    产出——MAX_ROUNDS 的有产出轮计数当场失效，正是第 3 章要修的那个故障
    换了个形式回来。

    field 缺失时退回文本哈希。代价必须说清楚：文本一变 id 就变，这类问题
    换措辞之后追踪不到——所以"没有 field"是降级，不是等价方案。第 5 章的
    重问追踪只对拿得到 field 的问题成立。

    同一字段的两个递进问题（"要不要 ISO 26262" 与 "要哪个 ASIL 等级"）会撞
    id，撞了就按"重问"处理，这是 design.md 决策 2 已经评估并接受的近似；
    重问次数上限取 2 就是给这种递进留的余量。
    """
    normalized_field = (field or "").strip()
    _record_question_id_metric("total")
    if normalized_field and normalized_field not in QUESTION_TARGET_FIELDS:
        # 野 field（模型拼错、或幻觉出一个不存在的字段名）按"无 field"降级，
        # 不抛异常——降级而非报错是本模块已确立的基调。
        _record_question_id_metric("unknown_field", normalized_field)
        normalized_field = ""
    elif not normalized_field:
        _record_question_id_metric("null_field")
    if normalized_field:
        return normalized_field
    digest = hashlib.sha256("".join(str(text).split()).encode("utf-8")).hexdigest()[:8]
    return f"{_FREE_ID_PREFIX}{digest}"


@dataclass(frozen=True)
class IntakeQuestion:
    """
    一个可独立作答的追问，是贯穿 agent → graph → API → 前端的一等对象
    （design.md 决策 1）。后续接企微卡片只需换渲染层。

    除 text 外全部可空或有默认值：模型退化成只会给一句问题文本时，系统降级成
    "纯文本问题"，绝不因为缺 field/options 就报错（design.md 风险表第 1 条）。

    frozen=True 是刻意的——问题对象在 graph 里被多个节点读到，可变对象会让
    "谁改了它"变成一个需要排查的问题。
    """

    text: str
    question_id: str
    field: str | None = None
    options: tuple[str, ...] = ()
    allow_free_text: bool = True
    is_reask: bool = False

    def to_payload(self) -> dict:
        """转成 JSON 友好的 dict。options 用 list 而不是 tuple：这个 dict 会被
        json.dumps 写进 outbox，也会进 LangGraph checkpoint。"""
        return {
            "question_id": self.question_id,
            "text": self.text,
            "field": self.field,
            "options": list(self.options),
            "allow_free_text": self.allow_free_text,
            "is_reask": self.is_reask,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "IntakeQuestion":
        text = str(payload.get("text", ""))
        field = payload.get("field") or None
        # 任何传入的 question_id 一律丢弃、重新派生——不让不可信来源（模型、
        # 外部 payload）覆盖系统派生结果。这不是漏看，是刻意的：
        # "question_id 由系统派生，不让模型自己编" 的地基如果在这里松口，
        # 就是给不可信 id 开了一条后门。不要把这行"恢复"成读取 payload 里的值。
        return cls(
            text=text,
            question_id=derive_question_id(field, text),
            field=field,
            options=tuple(payload.get("options") or ()),
            allow_free_text=bool(payload.get("allow_free_text", True)),
            is_reask=bool(payload.get("is_reask", False)),
        )


def render_questions_text(questions: list[IntakeQuestion]) -> str:
    """
    问题 → 文本的**唯一**渲染入口。

    为什么必须唯一（design.md 决策 1「代价」）：写进 conversation/history 的
    assistant 文本、以及下发给通道的文本，如果各渲染一遍，
    _repeats_earlier_assistant_turn 就会拿"历史里的那一版"去比对"实际下发的
    另一版"，逐字比对静默失效——而它现在是重复追问的最后一道防线。

    2026-08-19（第 3 章）：options 开始渲进文本。第 2 章刻意没渲是因为那一章
    的自我约束是"用户可见行为与合并前一致"；第 3 章的整个目的就是让用户在
    对话里看见具体档位，**在第 4 章的可点选控件之前就要看得见**——纯文本通道
    （以及第 4 章合并前的 Web）否则拿不到任何档位，spec「用户说不知道，系统给
    档位」在文本侧就落空了。带 AI 建议标识，见 _OPTIONS_PREFIX。
    """
    lines = []
    for question in questions:
        prefix = _REASK_PREFIX if question.is_reask else ""
        lines.append(f"{prefix}{question.text}")
        if question.options:
            lines.append(_OPTIONS_PREFIX + _OPTIONS_SEPARATOR.join(question.options))
    return "\n".join(lines)


def normalize_question_payload(payload: dict) -> dict:
    """
    把任意历史形态的 question payload 归一化成结构化形态。

    为什么需要：.51 现网 data/demo.db 的 outbox 里存着 2026-08-18 及之前写下的
    {"questions": ["问题文本", ...]}（裸字符串）。GET /api/jobs/{id} 会把这些
    历史行原样读回来当响应体，新前端按对象访问 q.text 会在真实数据上直接崩。
    与 design.md 决策 10（老库加列）同一类的坑：本地测试库全是新写的行，
    永远走不到这条路径，所以必须专门测。

    幂等：已经是新形态的 payload 过一遍不变。
    """
    raw = payload.get("questions") or []
    raw = raw if isinstance(raw, list) else []
    questions = [
        IntakeQuestion.from_payload({"text": item} if isinstance(item, str) else item)
        for item in raw
    ]
    normalized = {**payload, "questions": [q.to_payload() for q in questions]}
    normalized.setdefault("questions_text", render_questions_text(questions))
    return normalized
