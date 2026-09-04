from __future__ import annotations

from typing import Any

from app.agents.field_grounding import normalize_for_grounding

# 字段之间的分隔符。**必须有**：每个字段的值各自归一化之后空白已经被去光，
# 直接首尾相接会让两个不相干字段凭空拼出第三个术语（"CAN" + "oe" → "CANoe"），
# 把真正的编造判成已溯源。
# 写成 "\u0000" 转义而**不是**敲一个真的 NUL 字符：源码里的裸 NUL 会让 git、
# grep、编辑器一律把这个文件当二进制处理。needle 归一化后本来就不含空白，
# 用空格也够用，选 NUL 只是为了一眼看出"这里绝不可能是术语的一部分"。
# ⛔ 不要改成会出现在真实画像值里的字符。
_FIELD_SEPARATOR = "\u0000"

# JD 里可能出现的技术术语 → 画像侧可接受的等价写法。
#
# **为什么是闭集词表而不是"从文案里自动抽术语"**：自动抽取要么靠模型（决策 11
# 否决过：判官自己会编，且不可复算），要么靠分词（引入新依赖，且中英混排的
# ECU 术语切不准）。闭集词表是确定性的、可评审的、可复算的，代价是漏掉词表外的
# 术语——那与决策 11 声明的"本批要的是一个下界"同向，可接受。
#
# **⛔ 不要加两个字母以内的纯拉丁词条**（TI / AP / CP / IO）：归一化去掉空白后
# 它们会命中大量无关词的内部片段，噪声会淹没真正的编造。它们只能作为别名出现。
#
# **大小写敏感**：本模块⛔ 不做大小写折叠——那等于在 normalize_for_grounding
# 之外另造一套归一化口径（Global Constraints 第 7 条）。代价是文案写
# "Autosar" 而词表写 "AUTOSAR" 时该词条整条漏掉（漏检，不是误报），方向仍是下界。
# 确实高频的大小写变体单独立一个词条，见下面的 "Autosar"。
#
# **加词条时同时想清楚 aliases**：aliases 是"画像里写成这样也算数"的清单，
# 不是"文案里写成这样也算命中"的清单。方向搞反会让词条恒判未溯源，
# test_every_term_grounds_itself 会当场抓到。
JD_TECHNICAL_TERMS: dict[str, tuple[str, ...]] = {
    # ── AUTOSAR ────────────────────────────────────────────────────────
    # ⚠️ "CP" / "AP" 作为**别名**出现是刻意的：画像里 autosar_experience 存的就是
    # AutosarLayer 枚举值 "CP"/"AP"（app/schemas/job_profile.py），"AUTOSAR" 这个词
    # 在画像里一个字都不会出现。不给这两个别名，凡是文案里写 AUTOSAR 的都会被判
    # 未溯源——一条恒真的噪声。代价是画像里别处出现 "AP"（"APP 开发"、"SAP"）也会
    # 把 AUTOSAR 判成已溯源，方向是漏检（下界），与决策 11 同向。
    "AUTOSAR": ("AUTOSAR", "CP", "AP", "Classic Platform", "Adaptive Platform"),
    "Autosar": ("AUTOSAR", "Autosar", "CP", "AP"),
    "AUTOSAR CP": ("CP", "Classic Platform", "AUTOSAR"),
    "AUTOSAR AP": ("AP", "Adaptive Platform", "AUTOSAR"),
    # ── 功能安全 ───────────────────────────────────────────────────────
    "ISO 26262": ("ISO 26262", "功能安全", "ASIL"),
    "ASIL-A": ("ASIL-A", "ASIL A"),
    "ASIL-B": ("ASIL-B", "ASIL B"),
    "ASIL-C": ("ASIL-C", "ASIL C"),
    "ASIL-D": ("ASIL-D", "ASIL D"),
    "FuSa": ("FuSa", "功能安全", "ASIL"),
    # ── MCU 平台 ───────────────────────────────────────────────────────
    "TriCore": ("TriCore", "TC3", "TC2", "Aurix", "英飞凌", "Infineon"),
    "Aurix": ("Aurix", "TC3", "TC2", "英飞凌", "Infineon"),
    "Infineon": ("Infineon", "英飞凌", "Aurix"),
    "英飞凌": ("英飞凌", "Infineon", "Aurix"),
    "S32K": ("S32K", "NXP"),
    "NXP": ("NXP", "S32K"),
    "STM32": ("STM32", "ST"),
    "Renesas": ("Renesas", "瑞萨"),
    "瑞萨": ("瑞萨", "Renesas"),
    # ── 总线与诊断 ─────────────────────────────────────────────────────
    "CAN-FD": ("CAN-FD", "CANFD", "CAN FD"),
    "CAN": (),
    "LIN": (),
    "FlexRay": (),
    "车载以太网": ("车载以太网", "以太网", "Ethernet"),
    "SOME/IP": ("SOME/IP", "SOMEIP"),
    "UDS": ("UDS", "ISO 14229", "ISO14229", "诊断"),
    "ISO 14229": ("ISO 14229", "ISO14229", "UDS"),
    "OBD": (),
    "XCP": (),
    "CCP": (),
    # ── 工具链 ─────────────────────────────────────────────────────────
    "CANoe": ("CANoe", "Vector"),
    "CANape": ("CANape", "Vector"),
    "DaVinci": ("DaVinci", "Vector"),
    "Vector": (),
    "INCA": ("INCA", "ETAS"),
    "ETAS": ("ETAS", "INCA"),
    "Keil": (),
    "IAR": (),
    "Lauterbach": ("Lauterbach", "Trace32", "TRACE32"),
    "Trace32": ("Trace32", "TRACE32", "Lauterbach"),
    "Simulink": ("Simulink", "MATLAB", "Matlab"),
    "MATLAB": ("MATLAB", "Matlab", "Simulink"),
    # ── 规范与流程 ─────────────────────────────────────────────────────
    "MISRA": ("MISRA", "MISRA C", "MISRAC"),
    "A-SPICE": ("A-SPICE", "ASPICE", "Automotive SPICE"),
    "ASPICE": ("ASPICE", "A-SPICE", "Automotive SPICE"),
}


def _flatten(value: Any) -> list[str]:
    """把任意嵌套结构摊成字符串列表。

    布尔值刻意丢弃：它在画像里的语义是"是/否"（is_mass_production、required），
    摊成 "True" 只会往 haystack 里塞一个不承载任何技术术语的噪声词。
    """
    if isinstance(value, bool):
        return []
    if isinstance(value, dict):
        return [text for item in value.values() for text in _flatten(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _flatten(item)]
    if value is None:
        return []
    return [str(value)]


def profile_grounding_haystack(profile: Any) -> str:
    """画像里所有**业务字段**的值，归一化后用 NUL 串起来。

    ⛔ 以下划线开头的键一律排除，这是本模块最关键的一条不变式：`_jd_text` 就存在
    同一个 profile_json 里（app/graph/nodes.py 的 effect_generate_and_persist_jd
    写进去的），算进 haystack 就等于让文案拿自己当证据，verify 永远返回空。
    这个故障**没有任何症状**：不报错、不失败，只是校验悄悄变成摆设。
    排除规则按"下划线前缀"而不是"具体键名清单"，是为了让以后新增的内部键
    （`_gap_acknowledgement`、`_jd_authorship`、还没想到的那些）自动落在外面。

    ⚠️ 入参是 LLM 自由生成的裸 dict，**任何形状都不许抛异常**——这份画像在
    POST /confirm 之前从没撞过 JobProfile 的类型约束（app/web/server.py 的注释
    写了同一件事）。
    """
    if not isinstance(profile, dict):
        return ""
    parts: list[str] = []
    for name, value in profile.items():
        if str(name).startswith("_"):
            continue
        parts.extend(_flatten(value))
    return _FIELD_SEPARATOR.join(normalize_for_grounding(part) for part in parts)


def verify_jd_grounding(jd_text: Any, profile: Any) -> list[str]:
    """返回文案里**未溯源**的技术术语，按 JD_TECHNICAL_TERMS 的声明序。

    确定性，不调模型（design.md 决策 11 的形状）。判据与 field_grounding 逐字
    同源：两侧都过 normalize_for_grounding，然后做子串判定。

    **只观测不拦截**（决策 12）：调用方⛔ 不得据此拦下文案、重新生成或降级。
    返回空列表 = 词表内的术语全部有画像依据，⛔ 不等于"文案没有编造"——
    词表外的编造这里看不见，这个数字是下界不是精确值。
    """
    haystack = profile_grounding_haystack(profile)
    jd = normalize_for_grounding(jd_text if jd_text is not None else "")

    ungrounded: list[str] = []
    for term, aliases in JD_TECHNICAL_TERMS.items():
        needle = normalize_for_grounding(term)
        if not needle or needle not in jd:
            continue
        accepted = [needle] + [normalize_for_grounding(alias) for alias in aliases]
        if not any(candidate and candidate in haystack for candidate in accepted):
            ungrounded.append(term)
    return ungrounded
