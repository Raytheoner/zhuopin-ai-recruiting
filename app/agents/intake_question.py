from __future__ import annotations

import hashlib
from dataclasses import dataclass

# 重问前缀：让"这个你刚才没答"在文本通道里也看得见。判定 is_reask 属第 5 章
# （tasks 5.4），本模块只负责一旦置了标记就渲染出来。
_REASK_PREFIX = "（这个你刚才没答）"

# field 缺失时的 id 前缀。带前缀是为了在库里一眼看出"这个问题没有目标字段"，
# 而不是让它和真实字段名混在一起。
_FREE_ID_PREFIX = "free:"


def derive_question_id(field: str | None, text: str) -> str:
    """
    question_id 由系统按目标字段派生，不让模型自己编（design.md 决策 2）。

    field 缺失时退回文本哈希。代价必须说清楚：文本一变 id 就变，这类问题
    换措辞之后追踪不到——所以"没有 field"是降级，不是等价方案。第 5 章的
    重问追踪只对拿得到 field 的问题成立。

    同一字段的两个递进问题（"要不要 ISO 26262" 与 "要哪个 ASIL 等级"）会撞
    id，撞了就按"重问"处理，这是 design.md 决策 2 已经评估并接受的近似；
    重问次数上限取 2 就是给这种递进留的余量。
    """
    normalized_field = (field or "").strip()
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

    本单元不把 options 渲进文本：第 2 章的自我约束是"只换载体、用户可见行为
    与合并前一致"，选项的可点选控件与"AI 建议选项"标识属第 4 章
    （tasks 4.1/4.3，后者是《AI 生成合成内容标识办法》的要求）。
    """
    lines = []
    for question in questions:
        prefix = _REASK_PREFIX if question.is_reask else ""
        lines.append(f"{prefix}{question.text}")
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
    questions = [
        IntakeQuestion.from_payload({"text": item} if isinstance(item, str) else item)
        for item in raw
    ]
    normalized = {**payload, "questions": [q.to_payload() for q in questions]}
    normalized.setdefault("questions_text", render_questions_text(questions))
    return normalized
