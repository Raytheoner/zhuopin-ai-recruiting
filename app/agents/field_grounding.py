from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

# 模型返回的来源信封的三个键。判定一个值"是不是信封"只认 _VALUE_KEY 的存在，
# 不认另外两个——模型经常只给 value 而漏掉引用，那属于"缺少来源"（照样是信封、
# 记为未溯源），不属于"这个字段的值本来就是个 dict"。
_VALUE_KEY = "value"
_QUOTE_KEY = "source_quote"
_TURN_KEY = "source_turn"


def is_user_turn(turn: dict) -> bool:
    """
    一条历史记录算不算"业务经理的一轮原话"。

    默认值 "user" 与 intake_agent._build_user_prompt 的 turn.get("role", "user")
    **必须保持一致**：prompt 里给用户轮次编号用的是那个口径，校验按编号取原话
    用的是这个口径，两边差一条记录，编号就整体错位一格，表现为"引用明明对得上
    却被判未溯源"。这个故障只在脏数据（缺 role 的历史行）上出现，且错误信息里
    看不出任何线索——所以两处共用这一个谓词，不各写各的。
    """
    return turn.get("role", "user") == "user"


def user_turns(history: list[dict]) -> list[str]:
    """按出场顺序取出用户原话。source_turn 是这个列表的 **1-based** 下标。"""
    return [str(turn.get("content", "")) for turn in history if is_user_turn(turn)]


def normalize_for_grounding(text: Any) -> str:
    """
    比对前的唯一归一化入口（spec「归一化后仍算命中」）。

    NFKC 统一全半角（ＡＳＩＬ→ASIL、（）→()、－→-），随后去掉**全部**空白字符。
    去空白而不是折叠成单空格：中文里空格的有无本来就随手而变，"AUTOSAR CP" 与
    "AUTOSARCP" 在语义上没有区别，折叠成单空格反而会因为一个多余空格判失败。

    代价（写明，不粉饰）：去空白会让英文的词边界消失，"C  A" 能匹配上 "CA"。
    这个方向的误判会把编造判成"已溯源"，即让未溯源率**偏低**——与决策 11 声明的
    「本批要的是一个下界」同向，可接受。
    """
    return "".join(unicodedata.normalize("NFKC", str(text)).split())


@dataclass(frozen=True)
class FieldSource:
    """一个字段声明的来源。两个字段都可空——缺失即未溯源，不是校验失败。"""

    quote: str | None
    turn: int | None


def _coerce_turn(raw: Any) -> int | None:
    """轮次容错：接受 int 与能转成 int 的字符串，其余一律 None（记为未溯源）。"""
    # bool 必须排在 int 之前：Python 里 isinstance(True, int) 为真，
    # 不挡掉的话 source_turn=true 会静默变成"第 1 轮"。
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _coerce_quote(raw: Any) -> str | None:
    """引用容错：只接受非空白字符串。空串是任何字符串的子串，等于万能通行证。"""
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def split_patch_sources(raw_patch: Any) -> tuple[dict, dict[str, FieldSource]]:
    """
    **全流程唯一的拍平点。** 把模型返回的
    {字段: {value, source_quote, source_turn}} 拆成（裸值 patch，来源表）。

    这个函数的返回值第一项是本单元最重要的不变式的载体：它之后的一切——
    profile_patch_accumulated、job_profile.profile_json、JobProfile.model_validate、
    jd_agent 读的 profile_dict——**全部只见得到裸值**。结构升级到此为止。
    不拍平会同时炸三处，见本计划 Global Constraints 第一段。

    容错是刻意的（spec「来源结构缺失时降级而非报错」）：模型没按新提示词输出、
    还给裸值时，值原样保留、来源记空 → 该字段计未溯源，采集照常完成。
    这条路径在提示词刚升到 v5 的头几天一定会被走到。
    """
    if not isinstance(raw_patch, dict):
        return {}, {}

    flat: dict = {}
    sources: dict[str, FieldSource] = {}
    for name, raw in raw_patch.items():
        key = str(name)
        if isinstance(raw, dict) and _VALUE_KEY in raw:
            flat[key] = raw[_VALUE_KEY]
            sources[key] = FieldSource(
                quote=_coerce_quote(raw.get(_QUOTE_KEY)),
                turn=_coerce_turn(raw.get(_TURN_KEY)),
            )
        else:
            flat[key] = raw
            sources[key] = FieldSource(quote=None, turn=None)
    return flat, sources


def verify_field_grounding(
    patch: Any,
    history: list[dict],
    *,
    exempt_fields: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    """
    返回本轮**未溯源**的字段名列表。确定性，不调模型（design.md 决策 11）。

    判据：引用片段（归一化后）必须能在它**自己声明的那一轮**用户原话里原样找到。
    刻意不做"在全部轮次里搜一遍"的兜底——spec 的 Scenario「指错了轮次」明写
    这种情况判未溯源。放宽成全局搜索会让"模型随便填个轮次号"变成免费通行证。

    exempt_fields 用于系统管理字段（tasks 7.4）。之所以走入参而不是在这里
    import intake_agent._SYSTEM_MANAGED_FIELDS：那会形成循环 import，且会让
    这个模块从"零依赖纯函数"退化成"依赖 agent 的模块"。
    """
    _, sources = split_patch_sources(patch)
    turns = [normalize_for_grounding(text) for text in user_turns(history)]

    ungrounded: list[str] = []
    for name, source in sources.items():
        if name in exempt_fields:
            continue
        if source.quote is None or source.turn is None:
            ungrounded.append(name)
            continue
        if not 1 <= source.turn <= len(turns):
            ungrounded.append(name)
            continue
        needle = normalize_for_grounding(source.quote)
        if not needle or needle not in turns[source.turn - 1]:
            ungrounded.append(name)
    return ungrounded
