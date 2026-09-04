"""从岗位画像里提取硬门槛规则草案（m1-job-profile-intake tasks 5.8 / 5.9）。

spec「硬门槛规则草案提取」：每条规则包含字段名、比较运算符、比较值、是否阻断，
并附一句人类可读的说明（用于将来向候选人解释淘汰原因）。

⛔ **本模块是 L3 纯函数，不调模型、不写库、不改入参**（工程铁律 2）。提取必须
确定性：同一份画像重跑两次逐条相同、顺序相同。理由不是洁癖——草案要能被人复核、
被回放对比，一次模型调用就把它变成不可复算的东西，而且会绕开「AI 只做排序推荐、
不做自动淘汰」的边界。

⛔ **本模块只产出规则，不执行规则。** 这里没有任何一行会把候选人筛掉；`blocking`
是给人看的标注，不是执行开关（合规红线）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 运算符封闭集合。⛔ 与 app/storage/db.py 的 hard_requirement.operator CHECK
# 逐字同源，改一处必须同步改另一处——不同步的后果是业务经理点确认的那一刻
# 炸成 IntegrityError。
OPERATORS: tuple[str, ...] = ("gte", "education_gte", "contains", "equals", "is_true")

# 可提取字段白名单，**同时决定输出顺序**（确定性要求）。
#
# ⛔ soft_skill_keywords 刻意不在此列：合规红线「主观描述不得进入硬门槛规则，
# 只能作为软技能关键词」在这里是**结构性**成立的，不靠下面的词表兜底。
# ⛔ job_title / department / headcount 不是候选人可判定的条件；
# ⛔ project_experience_requirement 是自由文本，自动判定必然要靠语义理解，
#    那就回到"调模型"上去了——排除，宁可少一条规则。
EXTRACTABLE_FIELDS: tuple[str, ...] = (
    "education_requirement",
    "experience_years",
    "core_skills",
    "functional_safety",
    "autosar_experience",
    "mcu_family",
    "diag_stack",
    "toolchain",
    "sop_projects",
)

# 学历档位与别名。**按从低到高排列**，第一个命中的就是门槛。
# "本科及以上，硕士优先" → 本科，不是硕士：取最低档是刻意的保守方向，门槛取高
# 了会把合格的人挡在外面，而这条规则将来要用来向候选人解释淘汰原因。
_EDUCATION_LEVELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("大专", ("大专", "专科")),
    ("本科", ("本科", "学士")),
    ("硕士", ("硕士", "研究生")),
    ("博士", ("博士",)),
)

# "没有要求"的等价表述。命中即不产出规则。
_NO_REQUIREMENT_VALUES: frozenset[str] = frozenset(
    {"", "无", "无要求", "不限", "不限制", "未指定", "没有要求", "无特殊要求"}
)

# 年限上限的常见表述。命中即不产出下限规则——上限不是门槛，是偏好。
#
# 误差预算刻意不对称：漏掉一个上限词，会把「最多 3 年」的偏好反向翻译成
# 「至少 3 年」的硬门槛，直接挡掉本该合格的候选人（方向性错误，不可逆）；
# 而把某个短语误判成上限词，代价只是少产出一条规则，人工复核时能补回来
# （spec「画像形状不可信时也不许抛」同一方向：宁可少一条规则）。所以这份
# 词表宁可判得宽，也不要漏。
_EXPERIENCE_UPPER_BOUND_MARKERS: tuple[str, ...] = (
    "以下",
    "以内",
    "不超过",
    "不足",
    "少于",
    "不到",
    "封顶",
    "最多",
)

# 可迁移经验字段：提成规则但不阻断（换个 MCU 平台族两周能上手，把它设成阻断
# 等于用工具品牌筛人）。
_NON_BLOCKING_LIST_FIELDS: tuple[str, ...] = ("mcu_family", "diag_stack", "toolchain")

_LIST_FIELD_SENTENCE: dict[str, str] = {
    "mcu_family": "MCU 平台经验：{value}（加分项，不阻断）",
    "diag_stack": "诊断/总线协议栈经验：{value}（加分项，不阻断）",
    "toolchain": "工具链使用经验：{value}（加分项，不阻断）",
}


@dataclass(frozen=True)
class HardRequirement:
    """一条硬门槛规则草案。

    `blocking` 是**标注**：True 表示"这一项不满足就不通过硬门槛"，False 表示
    "记录下来供筛选时加分参考"。⛔ 它不是执行开关——本变更包内没有任何代码读
    这个字段去淘汰候选人（合规红线：AI 只做排序推荐，不做自动淘汰）。

    `human_readable` 是确定性模板拼接的产物，不是模型生成内容，因此不触发
    《AI 生成合成内容标识办法》的标识义务。⛔ 不要在这里调 LLM 润色。
    """

    field: str
    operator: str
    value: str
    blocking: bool
    human_readable: str


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_no_requirement(text: str) -> bool:
    return text in _NO_REQUIREMENT_VALUES


def _education_floor(text: str) -> str | None:
    """学历要求自由文本 → 学历档位下限。识别不出来就返回 None（不产出规则）。"""
    for level, aliases in _EDUCATION_LEVELS:
        if any(alias in text for alias in aliases):
            return level
    return None


def _experience_floor(text: str) -> str | None:
    """年限要求自由文本 → 年限下限（字符串形式的整数）。

    "3-5年" → "3"；"5 年以上" → "5"；"3 年以下" → None。
    ⛔ 含 `_EXPERIENCE_UPPER_BOUND_MARKERS` 中任一词的是上限，绝不能当成
    下限——那会把一条"最多 3 年"的偏好翻译成"至少 3 年"的门槛，方向完全相反。

    误差预算刻意不对称：漏掉一个上限词会把招聘门槛方向反转（本应放行的候选人
    被挡在外面）；而把某个词误判成上限词，代价只是少产出一条规则、留给人工
    复核时补上。两种错误不等价，所以词表宁可判得宽一些（详见
    `2026-09-04-m1-job-profile-intake-unit-hard-requirement` fix round 1 的
    reviewer 复现：`"不超过3年"` `"少于3年"` `"不到3年"` `"3年封顶"` 这类常见
    表述如果漏判，会被当成 `gte` 下限提取出来）。
    """
    if any(marker in text for marker in _EXPERIENCE_UPPER_BOUND_MARKERS):
        return None
    match = re.search(r"\d+", text)
    return match.group(0) if match else None


def _iter_dicts(value) -> list[dict]:
    """从可能不可信的画像值里取出 dict 列表，形状不对就当空。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _iter_strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(item) for item in value) if text]


def extract_hard_requirements(profile: dict) -> list[HardRequirement]:
    """画像 dict → 硬门槛规则草案列表。纯函数，⛔ 不改入参、不调模型、不写库。

    ⚠️ 入参可能是 LLM 自由生成的裸 dict（形状不可信）。**任何形状都不许抛
    异常**——抛了就是业务经理点确认的那一刻炸成 500。形状不对的部分静默跳过，
    保守方向是"少一条规则"而不是"整个确认失败"。
    """
    if not isinstance(profile, dict):
        return []

    unspecified = set(_iter_strings(profile.get("unspecified_fields")))
    rules: list[HardRequirement] = []

    for field in EXTRACTABLE_FIELDS:
        # 追问超限用"未指定"填充的字段⛔ 不得变成门槛（spec「追问达到上限」）。
        if field in unspecified:
            continue
        rules.extend(_extract_field(field, profile.get(field)))

    return _dedupe(rules)


def _extract_field(field: str, value) -> list[HardRequirement]:
    if field == "education_requirement":
        return _extract_education(value)
    if field == "experience_years":
        return _extract_experience(value)
    if field == "core_skills":
        return _extract_core_skills(value)
    if field == "functional_safety":
        return _extract_functional_safety(value)
    if field == "autosar_experience":
        return _extract_autosar(value)
    if field in _NON_BLOCKING_LIST_FIELDS:
        return _extract_non_blocking_list(field, value)
    if field == "sop_projects":
        return _extract_sop_projects(value)
    return []


def _extract_education(value) -> list[HardRequirement]:
    text = _text(value)
    if _is_no_requirement(text):
        return []
    level = _education_floor(text)
    if level is None:
        return []
    return [
        HardRequirement(
            field="education_requirement",
            operator="education_gte",
            value=level,
            blocking=True,
            human_readable=f"学历要求：{level}及以上（不满足则不通过硬门槛）",
        )
    ]


def _extract_experience(value) -> list[HardRequirement]:
    text = _text(value)
    if _is_no_requirement(text):
        return []
    floor = _experience_floor(text)
    if floor is None:
        return []
    return [
        HardRequirement(
            field="experience_years",
            operator="gte",
            value=floor,
            blocking=True,
            human_readable=f"工作年限要求：{floor} 年及以上（不满足则不通过硬门槛）",
        )
    ]


def _extract_core_skills(value) -> list[HardRequirement]:
    rules: list[HardRequirement] = []
    for item in _iter_dicts(value):
        name = _text(item.get("name"))
        if not name or _is_no_requirement(name):
            continue
        required = bool(item.get("required"))
        sentence = (
            f"必会技能：{name}（不满足则不通过硬门槛）"
            if required
            else f"加分技能：{name}（加分项，不阻断）"
        )
        rules.append(
            HardRequirement(
                field="core_skills",
                operator="contains",
                value=name,
                blocking=required,
                human_readable=sentence,
            )
        )
    return rules


def _extract_functional_safety(value) -> list[HardRequirement]:
    text = _text(value)
    if _is_no_requirement(text):
        return []
    return [
        HardRequirement(
            field="functional_safety",
            operator="equals",
            value=text,
            blocking=True,
            human_readable=f"功能安全等级要求：{text}（不满足则不通过硬门槛）",
        )
    ]


def _extract_autosar(value) -> list[HardRequirement]:
    rules: list[HardRequirement] = []
    for layer in _iter_strings(value):
        if _is_no_requirement(layer):
            continue
        rules.append(
            HardRequirement(
                field="autosar_experience",
                operator="contains",
                value=layer,
                blocking=True,
                human_readable=f"需具备 AUTOSAR {layer} 开发经验（不满足则不通过硬门槛）",
            )
        )
    return rules


def _extract_non_blocking_list(field: str, value) -> list[HardRequirement]:
    rules: list[HardRequirement] = []
    for name in _iter_strings(value):
        if _is_no_requirement(name):
            continue
        rules.append(
            HardRequirement(
                field=field,
                operator="contains",
                value=name,
                blocking=False,
                human_readable=_LIST_FIELD_SENTENCE[field].format(value=name),
            )
        )
    return rules


def _extract_sop_projects(value) -> list[HardRequirement]:
    """量产（SOP）经历。多个项目只产出**一条**规则——"有没有量产经历"是一个
    布尔事实，逐个车型建规则会把车型型号变成筛人条件。"""
    if not any(item.get("is_mass_production") for item in _iter_dicts(value)):
        return []
    return [
        HardRequirement(
            field="sop_projects",
            operator="is_true",
            value="is_mass_production",
            blocking=True,
            human_readable="需具备量产（SOP）项目经历（不满足则不通过硬门槛）",
        )
    ]


def _dedupe(rules: list[HardRequirement]) -> list[HardRequirement]:
    """按天然键去重、保持首次出现的顺序。

    天然键 = (field, operator, value)，与 hard_requirement 的复合主键同粒度。
    ⛔ 不在这里"去重后取 blocking 更严的那条"——同名技能一必会一加分是画像
    本身的矛盾，静默挑一个会把矛盾藏起来；保留首次出现，让它在人复核草案时
    仍然看得见。
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[HardRequirement] = []
    for rule in rules:
        key = (rule.field, rule.operator, rule.value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(rule)
    return unique
