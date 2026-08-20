from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, field_validator

from app.agents.ecu_knowledge import (
    FALLBACK_FIELD_ORDER,
    FALLBACK_QUESTION_TEXT,
    FOLLOWUP_RULES,
    FollowupSpec,
    fallback_options_for_field,
    match_ambiguous_terms,
)
from app.agents.intake_question import IntakeQuestion, derive_question_id, render_questions_text
from app.llm.gateway import LLMGateway
from app.schemas.job_profile import SYSTEM_MANAGED_FIELDS, JobProfile

# 有产出轮的预算：只对 is_productive=1 的行计数（design.md 决策 5）。
MAX_ROUNDS = 5
# 总轮次硬上限：对 job_profile 总行数计数，让"零产出轮不消耗预算"不会把对话
# 拖成无限。任一命中即收尾。取 8 = 5 轮有产出 + 最多 3 轮空转（design.md
# Open Questions 里写明这个数字是拍的，上线后拿真实空转轮分布复核）。
MAX_TOTAL_ROUNDS = 8
MAX_QUESTIONS_PER_ROUND = 3

# unspecified_fields 由系统在追问超限降级时填写，不该出现在给模型的字段表里，
# 否则模型会把它当成一个可以自己往 profile_patch 里塞的业务字段。
# 真源在 app/schemas/job_profile.py：intake_question 也要用同一份清单，
# 从这里导入会形成循环。
_SYSTEM_MANAGED_FIELDS = SYSTEM_MANAGED_FIELDS

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
    "【追问的拆分规则】questions 里一个条目**只能承载一个可独立作答的子问题**。"
    "反例：「是否需要熟悉 IATF 16949 或 ISO 26262？」——这是两个独立要求，"
    "用户只会答其中一个，另一个就永远悬着。必须拆成两条："
    "「是否要求熟悉 IATF 16949？」与「是否要求熟悉 ISO 26262？」。\n"
    "\n"
    "【追问的字段形状】questions 的每一项是一个对象：\n"
    "- text（必填，字符串）：问给用户看的那句话\n"
    "- field（选填，字符串）：这个问题想补全上面字段表里的哪个字段名；"
    "拿不准就留 null，不要硬填一个不存在的字段名\n"
    "- options（选填，字符串数组）：可供用户直接选择的具体档位；"
    "没有可枚举档位的问题（如「具体车型与量产时间」）留空数组\n"
    "- allow_free_text（选填，布尔）：是否允许用户自由文本作答，默认 true\n"
    "不要输出 question_id，那个由系统按 field 派生；你自己编的 id 会被丢弃。\n"
    "\n"
    "输出 JSON，字段：is_job_related(bool), questions(上述问题对象的数组), "
    "profile_patch(object), unspecified_fields(string[], 可选)。"
)


class _IntakeQuestionSchema(BaseModel):
    """
    模型侧的问题形状。**不含 question_id / is_reask**——那两个由系统派生与判定
    （design.md 决策 2），放进模型 schema 等于邀请模型自己编 id。
    """

    text: str
    field: str | None = None
    options: list[str] = []
    allow_free_text: bool = True


class _IntakeTurnSchema(BaseModel):
    is_job_related: bool
    questions: list[_IntakeQuestionSchema] = []
    profile_patch: dict = {}
    unspecified_fields: list[str] = []

    @field_validator("questions", mode="before")
    @classmethod
    def _tolerate_plain_strings(cls, value):
        """
        模型只给一句文本时降级成纯文本问题，而不是校验失败、重试三次、
        最后抛 SchemaExtractionFailed 把整轮采集废掉（design.md 风险表第 1 条）。

        这条路径是真实会走到的：本 schema 含自由 dict（profile_patch），
        _has_free_form_object 命中后网关始终走 json_object 模式，供应商只保证
        "是合法 JSON"、不校验形状（见 app/llm/gateway.py 的 _call_model）。

        只兜"整项是字符串"这一种退化。dict 里缺 text 之类的结构性错误仍然走
        既有的 SchemaExtractionFailed 重试路径——那是模型没按 schema 输出，
        重试一次比猜一个 text 更对。
        """
        if not isinstance(value, list):
            return value
        return [{"text": item} if isinstance(item, str) else item for item in value]


@dataclass
class IntakeTurnResult:
    is_job_related: bool
    questions: list[IntakeQuestion]
    profile_patch: dict
    is_complete: bool
    unspecified_fields: list[str] = field(default_factory=list)
    # 已渲染的问题文本。带在结果里而不是让调用方自己 join：history 里的
    # assistant 文本与下发给通道的文本必须同源（design.md 决策 1「代价」）。
    questions_text: str = ""
    # 本轮 LLM 累计耗时（含重试），由 effect_persist_draft 落库（第 1 章）。
    llm_latency_ms: float = 0.0
    # API 响应里实际返回的模型标识（铁律 5）。本单元只透出不落库——落库属
    # 第 7 章（字段溯源要按模型版本归因），而 intake-turn-observability 明确
    # 要求时序留痕不记模型标识。
    llm_response_model: str | None = None
    # 本轮是否有产出（新画像内容 **或** 问出了未问过的 question_id）。
    # 由 effect_persist_draft 落进 job_profile.is_productive，追问预算按它计数
    # （design.md 决策 5）。默认 True：判定路径没接上时的行为与今天一致。
    is_productive: bool = True
    # 本轮实际问出的问题（已问台账的本轮增量），落进
    # job_profile.asked_questions。第 5 章在其上扩"已答 / 重问次数"。
    asked_questions: list[IntakeQuestion] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 模糊回复与反问的确定性判定（design.md 决策 3）
# ---------------------------------------------------------------------------

# 模糊表态词表。**硬编码的中文规则，会有漏判**（用户用没收录的说法表达"不知道"），
# 漏判的后果是退回今天的行为、不会更差；误判的后果是多给一组选项，也不致命——
# 但绝不允许影响 profile_patch 的写入（design.md 决策 3「代价」）。
_VAGUE_MARKERS: tuple[str, ...] = (
    "不知道",
    "不太了解",
    "不了解",
    "不清楚",
    "不确定",
    "没想好",
    "说不好",
    "不理解你想问",
    "不理解你的问题",
    "你决定",
    "您决定",
    "你看着办",
    "您看着办",
    "你定吧",
    "随便",
    "无所谓",
    "都行",
    "都可以",
    "你有什么建议",
    "有什么建议",
    "你觉得呢",
    "你说呢",
    "听你的",
    "看你的",
)

_QUESTION_MARKS = ("？", "?")

# 反问判定里要忽略的通用二字片段：它们在任何一句问句里都会出现，算作"线索"
# 会让反问判定几乎永远不触发。
_STOPWORD_BIGRAMS: frozenset[str] = frozenset(
    {
        "是否",
        "要求",
        "请问",
        "哪些",
        "什么",
        "这个",
        "那个",
        "需要",
        "可以",
        "具体",
        "岗位",
        "方面",
        "多少",
        "建议",
        "相关",
        "经验",
        "以及",
        "或者",
        "如果",
        "我们",
        "你们",
    }
)

_NON_WORD = re.compile(r"[^0-9A-Za-z一-鿿]+")


def _compact(text: str) -> str:
    """去掉全部空白与标点，只留中文/字母/数字。比对必须在同一个归一化面上做。"""
    return _NON_WORD.sub("", str(text))


def _bigrams(text: str) -> set[str]:
    compact = _compact(text)
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _looks_like_counter_question(text: str, asked_questions: list[IntakeQuestion]) -> bool:
    """
    反问模式：用户回复以问号结尾，且**不含任何上一轮问题的线索**。

    "不含线索"用二字片段（bigram）交集判定：上一轮问「该岗位采购的『一般材料』
    指哪些品类」，用户回「一般材料是什么，你都不知道吗」——共享"一般/般材/材料"，
    这条判不成反问，靠 _VAGUE_MARKERS 的"不知道"命中；而「你们公司是做什么的？」
    跟上一轮毫无交集，判成反问。这样切能把"追着上一轮问细节"（有信息）和
    "把问题原样丢回来"（没信息）分开。
    """
    stripped = str(text).strip().rstrip("。！!」』\"'）) 　")
    if not stripped.endswith(_QUESTION_MARKS):
        return False
    clues: set[str] = set()
    for question in asked_questions:
        clues |= _bigrams(question.text)
    clues -= _STOPWORD_BIGRAMS
    return not (_bigrams(text) & clues)


def is_vague_reply(text: str, *, asked_questions: list[IntakeQuestion] | None = None) -> bool:
    """
    纯函数：这条用户回复是不是"没有给出可提取信息"。**不调模型**。

    spec「模糊回复与反问的兜底档位」明确要求"判定 MUST 是确定性的，不得只依赖
    模型自觉"——这次事故本身就是"提示词说了、模型没做"。

    空串返回 False 而不是 True：没有用户发言的场景（第一轮之前）不该被当成
    模糊回复，否则系统会在用户还没说话时就开始塞档位。

    **已知且接受的代价（2026-08-20 review 定论，未改算法）**：反问判定只看
    「问号结尾 + 与上一轮问题无二字片段交集」，**不检查回复本身是否已经带了
    实质信息**。所以一句本身有实质内容、但顺带反问了一句的回复——例如
    「是要社招还是校招都可以，你说的是哪种？」——会被判成 True。这不是漏改的
    bug，是刻意选择的方向：错判成本是"多给一组用户不需要的档位"，漏判成本是
    "真的遇到模糊回复却不给档位、把用户晾在原地"——后者正是这个交付单元要修的
    那次事故本身。两者不对称，所以判定刻意偏向"宁可多给，不可漏给"。
    这个误判**不占用追问预算**：`is_productive`（Task 4/6）只看新增的 profile
    字段与新的 question_id，不读 `is_vague_reply` 的结果。如果未来有单元要收紧
    这条反问启发式，预期 `test_substantive_reply_ending_in_a_question_is_still_treated_as_vague`
    会变红——那条红是信号，不是回归。
    """
    if not str(text).strip():
        return False
    compact = _compact(text)
    if any(_compact(marker) in compact for marker in _VAGUE_MARKERS):
        return True
    return _looks_like_counter_question(text, list(asked_questions or []))


def suggested_followups(history: list[dict]) -> list[FollowupSpec]:
    """
    根据历史里的**用户**发言命中 ecu_knowledge 的术语规则，给出该问的领域追问。

    只看 role="user" 的轮次：助手自己问出的"是否有功能安全等级（ASIL）要求？"里
    含有"功能安全"这个术语，如果把助手轮次也算进来，规则问过一次之后就会永远
    自我触发，把同一组问题反复推给模型。
    """
    user_text = "\n".join(
        str(turn.get("content", "")) for turn in history if turn.get("role") == "user"
    )
    specs: list[FollowupSpec] = []
    for term in match_ambiguous_terms(user_text):
        for spec in FOLLOWUP_RULES[term]:
            if spec not in specs:
                specs.append(spec)
    return specs


def _render_followup_line(spec: FollowupSpec) -> str:
    """把一条知识库追问渲染进 prompt。带上 field 与 options，模型才知道该照抄
    哪个字段名、有哪些现成档位可用——否则它只看得到问题文本，档位又要自己编。"""
    parts = [f"- {spec.text}"]
    if spec.field:
        parts.append(f"（目标字段：{spec.field}）")
    if spec.options:
        parts.append("（可选档位：" + "、".join(spec.options) + "）")
    return "".join(parts)


def _build_user_prompt(
    history: list[dict], profile_patch_accumulated: dict, followups: list[FollowupSpec]
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
            + "\n".join(_render_followup_line(spec) for spec in followups)
        )

    return "\n\n".join(sections)


def _repeats_earlier_assistant_turn(candidate_text: str, history: list[dict]) -> bool:
    """
    判断这轮生成的问题文本是否和历史上**任意一轮** assistant 说过的内容只有
    空白差异地相同。

    2026-08-10 真实环境试跑发现：用户回答模糊（如对"CP 还是 AP"这种二选一问题
    回答"是的"）时，profile_patch 常年提不出任何字段，ECU 知识库的追问建议又
    逐轮原样重新注入 prompt，模型在 temperature=0 下倾向于生成和上一轮几乎
    一字不差的问题——不能靠 MAX_ROUNDS 兜底，那之前每一轮都在把同一组问题
    原样再发一次给用户。

    2026-08-16 姚祖怡试跑反馈"重复问了同一件事情"，追查发现只比对"上一轮"不够：
    只要中间隔了一轮问别的，第 1 轮问过的问题在第 3 轮被模型重新问出来，跟
    "上一轮"（第 2 轮）文本不同，原先的检测完全看不到——比对范围改为历史上
    **所有** assistant 轮次，而不只是最后一轮。

    2026-08-19：入参从 list[str] 改为**已渲染的文本**，渲染由
    app/agents/intake_question.render_questions_text 唯一负责——两处各渲染一遍
    会让这里比对到与实际下发不一致的文本，逐字比对静默失效。

    这道防线**保留不动**。同期取证（docs/findings/2026-08-13-sqlite-事务归属冲突.md
    §8.5）证明"用户体感重复"还有第三种成因：投递丢失导致用户没收到上一轮回复，
    模型从 checkpoint 读到自己问过、便道歉并换措辞重问。那一层已由
    fix-sqlite-transaction-ownership 修复，与本函数无关。按 question_id 追踪
    未答子问题是 m1-intake-quality-fixes 第 5 章的事（tasks 5.8 给本函数去留的
    结论），本单元不动它的判定逻辑。
    """
    if not candidate_text:
        return False
    normalize = lambda s: "".join(str(s).split())
    candidate = normalize(candidate_text)
    earlier_assistant_turns = (
        turn.get("content", "") for turn in history if turn.get("role") == "assistant"
    )
    return any(candidate == normalize(turn) for turn in earlier_assistant_turns)


_GUIDANCE_TEXT = "没听懂是不是用人需求，可以试试：'要招一个做XX的工程师'"


def _to_intake_questions(raw: list[_IntakeQuestionSchema]) -> list[IntakeQuestion]:
    """
    模型侧形状 → 系统侧一等对象。question_id 在这里派生，模型给的 id 拿不到
    这一步（_IntakeQuestionSchema 里根本没有那个字段，pydantic 默认忽略多余键）。

    同一轮内 question_id 撞了就只留第一条。撞 id 的两条问题在下游是同一个问题
    （台账、is_productive 判定、第 5 章的重问追踪全按 id 走），留着第二条只会
    让"本轮问了几个问题"和"本轮问了几个 question_id"两个数对不上。
    """
    questions: list[IntakeQuestion] = []
    seen: set[str] = set()
    for item in raw:
        question = IntakeQuestion(
            text=item.text,
            question_id=derive_question_id(item.field, item.text),
            field=item.field or None,
            options=tuple(item.options),
            allow_free_text=item.allow_free_text,
        )
        if question.question_id in seen:
            continue
        seen.add(question.question_id)
        questions.append(question)
    return questions


def _guidance_question() -> IntakeQuestion:
    return IntakeQuestion(
        text=_GUIDANCE_TEXT,
        question_id=derive_question_id(None, _GUIDANCE_TEXT),
    )


def run_intake_turn(
    gateway: LLMGateway,
    *,
    history: list[dict],
    round_count: int,
    profile_patch_accumulated: dict | None = None,
    productive_round_count: int | None = None,
    asked_question_ids_before: list[str] | None = None,
    previous_questions: list[IntakeQuestion] | None = None,
) -> IntakeTurnResult:
    """
    round_count = job_profile 总行数（business_key 的口径，不变）。
    productive_round_count = is_productive=1 的行数；省略时退化成 round_count，
    保持"没接上判定前的行为与今天完全一致"。
    asked_question_ids_before / previous_questions 都由调用方从数据库读出来传入
    ——IntakeState 没有 reducer，真源是库（见 app/graph/state.py 的说明）。
    """
    accumulated = dict(profile_patch_accumulated or {})
    asked_before = list(asked_question_ids_before or [])
    prior_questions = list(previous_questions or [])
    productive_rounds = round_count if productive_round_count is None else productive_round_count

    user_prompt = _build_user_prompt(history, accumulated, suggested_followups(history))

    # extract_structured_with_meta 而不是 extract_structured：本轮的 LLM 累计
    # 耗时与实际响应模型标识要透给编排层落库（tasks 1.3）。AuditHook 的签名
    # 没有变（design.md 决策 9）。
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=_IntakeTurnSchema,
        # SYSTEM_PROMPT 改了就必须升版本：input_hash 与 prompt_version 是
        # "这条结果是哪一版提示词产出的"的唯一依据（铁律 5 的可解释性要求）。
        prompt_version="intake-v3",
    )

    if not parsed.is_job_related:
        questions = _to_intake_questions(parsed.questions) or [_guidance_question()]
        return IntakeTurnResult(
            is_job_related=False,
            questions=questions,
            profile_patch={},
            is_complete=False,
            questions_text=render_questions_text(questions),
            llm_latency_ms=meta.latency_ms,
            llm_response_model=meta.response_model,
        )

    at_round_limit = round_count >= MAX_ROUNDS
    capped_questions = (
        [] if at_round_limit else _to_intake_questions(parsed.questions)[:MAX_QUESTIONS_PER_ROUND]
    )

    stuck = not at_round_limit and _repeats_earlier_assistant_turn(
        render_questions_text(capped_questions), history
    )
    give_up = at_round_limit or stuck
    questions = [] if give_up else capped_questions

    profile_patch = parsed.profile_patch

    # 零产出轮判定（design.md 决策 5）：本轮 profile_patch 相对已累积内容有新
    # 字段或改了值，**或**问出了此前未问过的 question_id。两者都没有 = 空转，
    # 不消耗追问预算。
    has_new_profile_content = any(
        name not in accumulated or accumulated[name] != value
        for name, value in profile_patch.items()
    )
    has_new_question = any(question.question_id not in asked_before for question in questions)

    return IntakeTurnResult(
        is_job_related=True,
        questions=questions,
        profile_patch=profile_patch,
        is_complete=give_up or not questions,
        unspecified_fields=parsed.unspecified_fields if give_up else [],
        questions_text=render_questions_text(questions),
        llm_latency_ms=meta.latency_ms,
        llm_response_model=meta.response_model,
        is_productive=has_new_profile_content or has_new_question,
        asked_questions=questions,
    )
