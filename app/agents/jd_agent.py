from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from pydantic import BaseModel

from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile

AI_LABEL_TEMPLATE = (
    "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {generated_at}。"
)

# 标识行的识别前缀。它**必须**是 AI_LABEL_TEMPLATE 真正的开头：对不上的话
# strip_ai_label 一行都剥不掉，而"标识不可删"这条保护就静默失效了——不报错、
# 不失败，只是编辑一次标识就没了。改模板时**必须**同步改这里，
# tests/test_jd_agent.py::test_prefix_is_the_actual_head_of_the_template 会当场抓到。
# ⛔ 不要写成从模板里 split 出来的表达式：那种写法在模板措辞变化时会静默退化成
# "整个模板头"，而守卫断言照样是绿的。
AI_LABEL_PREFIX = "【AI 生成】"

# 回读不出生成时间时的占位串。⛔ 绝不拿"现在"冒充生成时间：一个错的时间戳
# 比一个诚实的空缺更难被发现，审计那天也解释不过去（合规红线：AI 生成内容
# 标识办法要的是让人看见真实情况）。
UNKNOWN_GENERATED_AT = "未知（该文案生成时间未留存）"

# 回读用的正则从模板**拆**出来，不是另抄一份。模板改了正则自动跟着变，
# 不会出现"模板换了措辞、回读静默失效、编辑一次就把真实生成时间换成未知"
# 这种无症状故障。
_LABEL_HEAD, _LABEL_TAIL = AI_LABEL_TEMPLATE.split("{generated_at}")
_LABEL_PATTERN = re.compile(
    re.escape(_LABEL_HEAD) + r"(?P<generated_at>.*?)" + re.escape(_LABEL_TAIL)
)

DISCRIMINATORY_PATTERNS: dict[str, list[str]] = {
    "性别": ["仅限男性", "仅限女性", "限男性", "限女性", "男性优先", "女性优先"],
    "年龄": ["35岁以下", "30岁以下", "限35周岁", "年轻化团队"],
    "婚育": ["已婚已育", "未婚未育", "限已婚"],
    "地域": ["仅限本地户口", "限本地生源"],
    "民族": ["仅限汉族"],
    "健康状况": ["无乙肝", "限健康人士"],
}

JD_SYSTEM_PROMPT = (
    "你是招聘文案助手。基于给定的岗位画像 JSON 生成招聘文案正文（不含 AI 标识，"
    "标识由系统另行拼接），包含岗位职责、任职要求（必备/加分分列）、简短团队介绍。"
    "文案中出现的技术要求必须能追溯到画像字段，不得凭空新增。"
    "禁止出现任何性别/年龄/婚育/地域/民族/健康状况相关的限制性表述。"
    "输出 JSON，字段：body(string)。"
)


class _JDBodySchema(BaseModel):
    body: str


@dataclass
class JDGenerationResult:
    text: str
    needs_manual: bool
    blocked_categories: list[str]


def contains_discriminatory_language(text: str) -> list[str]:
    hits = []
    for category, keywords in DISCRIMINATORY_PATTERNS.items():
        if any(keyword in text for keyword in keywords):
            hits.append(category)
    return hits


def _compose_with_label(body: str, generated_at: str) -> str:
    label = AI_LABEL_TEMPLATE.format(generated_at=generated_at)
    # 正文被清空时只留标识：拼成 "\n\n【AI 生成】…" 会在文案顶上留两个空行，
    # 复制出去贴到招聘平台上就是两行空白。标识本身一个字都不能少。
    if not body:
        return label
    return f"{body}\n\n{label}"


def generate_jd(
    gateway: LLMGateway, profile: JobProfile, *, max_retries: int = 2
) -> JDGenerationResult:
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    last_body = ""
    last_hits: list[str] = []

    # max_retries 是总生成尝试次数（不是"首次+N次重试"），对齐 spec「连续 N 次仍
    # 出现则转人工处理」的字面语义——默认 2 次，与 job-description spec 的
    # 「拦截歧视性表述」Scenario 保持一致（不同于 LLMGateway.max_retries 的
    # "首次+N次重试"约定，两者是不同函数，各自的语义以各自测试为准）。
    for _ in range(max_retries):
        parsed = gateway.extract_structured(
            system_prompt=JD_SYSTEM_PROMPT,
            user_prompt=profile.model_dump_json(),
            schema=_JDBodySchema,
            prompt_version="jd-v1",
        )
        last_body = parsed.body
        last_hits = contains_discriminatory_language(parsed.body)

        if not last_hits:
            return JDGenerationResult(
                text=_compose_with_label(parsed.body, generated_at),
                needs_manual=False,
                blocked_categories=[],
            )

    return JDGenerationResult(
        text=_compose_with_label(last_body, generated_at),
        needs_manual=True,
        blocked_categories=last_hits,
    )


def strip_ai_label(text: str) -> str:
    """去掉文本里所有的 AI 标识行。

    ⛔ **这不是"给用户删标识"的功能。** 它只有两个合法调用点：
    ① enforce_ai_label 内部——剥掉再重贴，保证不管用户提交什么都只有一行标识；
    ② effect_mark_jd_human_written——「标记为人工撰写」是唯一能真的去掉标识的
       路径，且必须留痕（合规红线：AI 生成内容标识办法）。
    ⛔ 任何 HTTP handler 都不得直接调用它。

    判据是"整行以标识前缀开头"，不是"文本里含标识前缀"：正文里提到 AI 的句子
    不能被吃掉。行首空白先 strip 掉再判——历史文本里的标识行可能带缩进。
    """
    kept = [
        line
        for line in str(text).splitlines()
        if not line.strip().startswith(AI_LABEL_PREFIX)
    ]
    return "\n".join(kept).strip()


def extract_label_generated_at(text: str) -> str | None:
    """从已带标识的文本里回读**原始生成时间**。读不出返回 None。

    为什么要回读而不是重新取"现在"：标识记录的是"这份文案是什么时候由 AI
    生成的"，不是"HR 什么时候编辑的"。编辑一次就把时间往后推，这条标识就
    从事实退化成噪声。

    为什么不落一个 `_jd_generated_at` 键：那要改 app/graph/nodes.py 的既有节点
    effect_generate_and_persist_jd，被本交付单元的边界禁止（Global Constraints
    第 12 条）。时间戳本来就完整地印在标识行里，回读是无损的。
    """
    match = _LABEL_PATTERN.search(str(text))
    if match is None:
        return None
    return match.group("generated_at").strip() or None


def enforce_ai_label(text: str, *, generated_at: str) -> str:
    """先剥干净、再重贴唯一一行标识。

    这是 7.5「常规编辑不可删标识」的**唯一**实现方式：服务端不去检查用户有没有
    删标识（检查就有绕过空间——改一个字、换个标点、插一行空白都能骗过检查），
    而是无条件把提交上来的文本当作正文重新贴标识。用户删不删都一样。
    """
    return _compose_with_label(strip_ai_label(text), generated_at)
