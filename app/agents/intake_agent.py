from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.agents.ecu_knowledge import FOLLOWUP_RULES, match_ambiguous_terms
from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile

MAX_ROUNDS = 5
MAX_QUESTIONS_PER_ROUND = 3

# unspecified_fields 由系统在追问超限降级时填写，不该出现在给模型的字段表里，
# 否则模型会把它当成一个可以自己往 profile_patch 里塞的业务字段。
_SYSTEM_MANAGED_FIELDS = {"unspecified_fields"}

_PRIMITIVE_NAMES = {
    "string": "字符串",
    "integer": "整数",
    "number": "数字",
    "boolean": "布尔值",
}


def _describe_schema_node(node: dict, defs: dict) -> str:
    """把 JSON Schema 的一个节点渲染成给模型看的中文类型说明（含枚举取值）。"""
    if "$ref" in node:
        return _describe_schema_node(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    if "enum" in node:
        values = "、".join(json.dumps(v, ensure_ascii=False) for v in node["enum"])
        return f"枚举，取值必须原样是其中之一：{values}"
    if "anyOf" in node:
        parts = [
            _describe_schema_node(sub, defs)
            for sub in node["anyOf"]
            if sub.get("type") != "null"
        ]
        return " 或 ".join(parts) + "（可为 null）"

    node_type = node.get("type")
    if node_type == "array":
        return f"数组，元素为「{_describe_schema_node(node.get('items', {}), defs)}」"
    if node_type == "object":
        inner = "、".join(
            f"{name}({_describe_schema_node(sub, defs)})"
            for name, sub in node.get("properties", {}).items()
        )
        return f"对象，字段为 {inner}"
    return _PRIMITIVE_NAMES.get(node_type, node_type or "任意")


def _render_profile_field_guide() -> str:
    """
    从 JobProfile 自身的 JSON Schema 渲染字段表。

    为什么要生成而不是手写：profile_patch 是模型自由生成的裸 dict，最终却要通过
    JobProfile 的校验。字段名、类型、枚举取值一旦和 schema 漂移，模型就会写出
    "ASIL B"（正确是 "ASIL-B"）、headcount="2人" 这类在确认那一步才炸掉的值。
    由 schema 生成保证这份说明永远和真实约束同步。
    """
    schema = JobProfile.model_json_schema()
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))

    lines = []
    for name, prop in schema.get("properties", {}).items():
        if name in _SYSTEM_MANAGED_FIELDS:
            continue
        mark = "必填" if name in required else "选填"
        lines.append(f"- {name}（{mark}）：{_describe_schema_node(prop, defs)}")
    return "\n".join(lines)


PROFILE_FIELD_GUIDE = _render_profile_field_guide()

SYSTEM_PROMPT = (
    "你是招聘助手，服务于一家汽车电子（ECU）研发制造企业。\n"
    "任务：判断用户消息是否是用人需求；如果是，结合 ECU 行业知识生成至多 3 个追问问题，"
    "并把本轮能确定的字段整理进 profile_patch。"
    "如果不是用人需求，questions 里放一句引导语，is_job_related=false，profile_patch 为空对象。\n"
    "\n"
    "【profile_patch 字段规范】键必须取自下面这份岗位画像字段表，值必须符合对应类型；"
    "枚举字段必须原样使用列出的取值（不要改大小写、不要把连字符换成空格）；"
    "拿不准的字段宁可不写，也不要编造或改写。\n"
    f"{PROFILE_FIELD_GUIDE}\n"
    "\n"
    "【累积规则】profile_patch 只放本轮新确定或需要修正的字段。"
    "用户消息里的「已确认字段」是前几轮已经收集到的内容，不要重复追问，也不要原样重复输出。\n"
    "\n"
    "【回答模糊/不知道时怎么办】如果用户的回复没有给出具体信息——比如"
    "「不知道」「你决定」「随便」「你有什么建议」这类模糊表态——不要只回一句"
    "「我来帮你整理」这样的空话，那等于浪费一轮却什么都没问出来。这种情况下"
    "questions 里必须给出 2-3 个具体的可选项（例如该细分领域行业内常见的档位"
    "或惯例做法），让用户下一轮回一个选项、「都要」或「随便选」就能推进，而"
    "不是继续面对一个自己答不出来的开放问题。但 profile_patch 仍然只能放用户"
    "已经明确选定或确认的字段——不能因为用户说「你决定」，就自己把猜的值直接"
    "写进 profile_patch；画像里的要求必须由用户明确选定，不是模型代替业务经理"
    "做的决定。\n"
    "\n"
    "输出 JSON，字段：is_job_related(bool), questions(string[]), profile_patch(object), "
    "unspecified_fields(string[], 可选)。"
)


class _IntakeTurnSchema(BaseModel):
    is_job_related: bool
    questions: list[str] = []
    profile_patch: dict = {}
    unspecified_fields: list[str] = []


@dataclass
class IntakeTurnResult:
    is_job_related: bool
    questions: list[str]
    profile_patch: dict
    is_complete: bool
    unspecified_fields: list[str] = field(default_factory=list)


def suggested_followups(history: list[dict]) -> list[str]:
    """
    根据历史里的**用户**发言命中 ecu_knowledge 的术语规则，给出该问的领域追问。

    只看 role="user" 的轮次：助手自己问出的"是否有功能安全等级（ASIL）要求？"里
    含有"功能安全"这个术语，如果把助手轮次也算进来，规则问过一次之后就会永远
    自我触发，把同一组问题反复推给模型。
    """
    user_text = "\n".join(
        str(turn.get("content", "")) for turn in history if turn.get("role") == "user"
    )
    questions: list[str] = []
    for term in match_ambiguous_terms(user_text):
        for question in FOLLOWUP_RULES[term]:
            if question not in questions:
                questions.append(question)
    return questions


def _build_user_prompt(
    history: list[dict], profile_patch_accumulated: dict, followups: list[str]
) -> str:
    transcript = "\n".join(
        f"{turn.get('role', 'user')}: {turn.get('content', '')}" for turn in history
    )
    sections = [f"【对话历史】\n{transcript or '（空）'}"]

    if profile_patch_accumulated:
        # sort_keys 保证同一份内容渲染结果稳定：extract_structured 用
        # system+user 文本的哈希做 input_hash，顺序抖动会让审计记录对不上。
        sections.append(
            "【已确认字段】\n"
            + json.dumps(profile_patch_accumulated, ensure_ascii=False, sort_keys=True, indent=2)
        )
    else:
        sections.append("【已确认字段】\n（暂无）")

    if followups:
        sections.append(
            "【本行业标准追问】以下问题由 ECU 领域知识库根据用户提到的术语给出，"
            "请优先从中挑选（最多 3 个）；已确认字段对应的问题跳过：\n"
            + "\n".join(f"- {q}" for q in followups)
        )

    return "\n\n".join(sections)


def _repeats_earlier_assistant_turn(questions: list[str], history: list[dict]) -> bool:
    """
    判断这轮生成的问题是否和历史上**任意一轮** assistant 说过的内容只有空白差异地相同。

    2026-08-10 真实环境试跑发现：用户回答模糊（如对"CP 还是 AP"这种二选一问题
    回答"是的"）时，profile_patch 常年提不出任何字段，ECU 知识库的追问建议又
    逐轮原样重新注入 prompt，模型在 temperature=0 下倾向于生成和上一轮几乎
    一字不差的问题——不能靠 MAX_ROUNDS 兜底，那之前每一轮都在把同一组问题
    原样再发一次给用户。

    2026-08-16 姚祖怡试跑反馈"重复问了同一件事情"，追查发现只比对"上一轮"不够：
    只要中间隔了一轮问别的，第 1 轮问过的问题在第 3 轮被模型重新问出来，跟
    "上一轮"（第 2 轮）文本不同，原先的检测完全看不到——比对范围改为历史上
    **所有** assistant 轮次，而不只是最后一轮。
    """
    if not questions:
        return False
    normalize = lambda s: "".join(str(s).split())
    candidate = normalize("\n".join(questions))
    earlier_assistant_turns = (
        turn.get("content", "") for turn in history if turn.get("role") == "assistant"
    )
    return any(candidate == normalize(turn) for turn in earlier_assistant_turns)


def run_intake_turn(
    gateway: LLMGateway,
    *,
    history: list[dict],
    round_count: int,
    profile_patch_accumulated: dict | None = None,
) -> IntakeTurnResult:
    user_prompt = _build_user_prompt(
        history, profile_patch_accumulated or {}, suggested_followups(history)
    )

    parsed = gateway.extract_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=_IntakeTurnSchema,
        prompt_version="intake-v2",
    )

    if not parsed.is_job_related:
        return IntakeTurnResult(
            is_job_related=False,
            questions=parsed.questions or ["没听懂是不是用人需求，可以试试：'要招一个做XX的工程师'"],
            profile_patch={},
            is_complete=False,
        )

    at_round_limit = round_count >= MAX_ROUNDS
    capped_questions = [] if at_round_limit else parsed.questions[:MAX_QUESTIONS_PER_ROUND]

    stuck = not at_round_limit and _repeats_earlier_assistant_turn(capped_questions, history)
    give_up = at_round_limit or stuck
    questions = [] if give_up else capped_questions

    return IntakeTurnResult(
        is_job_related=True,
        questions=questions,
        profile_patch=parsed.profile_patch,
        is_complete=give_up or not questions,
        unspecified_fields=parsed.unspecified_fields if give_up else [],
    )
