from __future__ import annotations

# 术语 → 追问问题（每条不超过 3 个，满足"每轮追问不超过 3 个问题"约束）
FOLLOWUP_RULES: dict[str, list[str]] = {
    "嵌入式开发": [
        "是否涉及 AUTOSAR（CP/AP）？",
        "MCU 平台族是？（如英飞凌 Aurix / NXP S32K / TI）",
        "是否有功能安全等级（ASIL）要求？",
    ],
    "驱动开发": [
        "驱动对接的总线类型是？（CAN-FD / LIN / 以太网）",
        "是否要求 UDS 诊断栈经验？",
    ],
    "功能安全": [
        "具体到 ASIL 哪个等级？",
        "是否要求 FuSa 工程师认证？",
    ],
    "算法开发": [
        "是感知/控制/诊断算法中的哪一类？",
        "是否要求量产项目（SOP）经验？",
    ],
}


def match_ambiguous_terms(text: str) -> list[str]:
    return [term for term in FOLLOWUP_RULES if term in text]
