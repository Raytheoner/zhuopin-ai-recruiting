"""
`criterion_key` 白名单——本仓库对"什么可以作为评分维度"的**唯一定义**。

⚠️ **强制方式是白名单（未登记即拒绝），不是黑名单。** 黑名单对"没想到的新维度"
默认放行，而红线要防的恰恰是没想到的那些。下面的 `RED_LINE_EXAMPLES` **只参与
报错信息**，不参与放行判定——它存在的意义是让被拦下的人立刻看懂"这不是漏配，
是红线"，而不是去提 PR 把维度加进白名单。

**加一个维度 = 改这里一行 + 一次 review**（design.md Risks 最后一条）。⛔ 不要
在任何别的地方再写第二份判定：散成两处就会出现"一处放行一处拒绝"的分叉，而分叉
的那一侧就是红线的缺口。

⚠️ **`criterion_key` 存的是「评分维度」，不是 rubric 里的具体条目**（Shao Peishen
2026-08-28 拍板，口径 A）。某个嵌入式岗位的 `autosar` / `can_bus` 这类条目落在
`analysis_run.rubric_snapshot` 里，不落这里——否则白名单会从"七个维度的闸门"
退化成"所有岗位所有技能的登记处"，一年后没人敢再拒绝任何 key。

合规依据：
- 声学情绪信号（语速、停顿、静默时长）只允许展示给面试官参考，MUST NOT 作为
  评分项写入（specs/ai-decision-audit 「评分项白名单约束」）。
- 生物特征类维度（人脸、表情）MUST NOT 出现在任何评分项中
  （《人脸识别技术应用安全管理办法》2025-06-01 施行）。
"""

from __future__ import annotations


class ForbiddenCriterionKey(ValueError):
    """试图把一个未登记的维度写成评分项。"""


# 已登记的评分维度。⛔ 增删必须过 review——这一行就是合规红线的闸门。
CRITERION_KEY_WHITELIST = frozenset(
    {
        "skill_match",  # 技能与岗位要求的匹配度
        "experience_depth",  # 相关经验的深度
        "project_relevance",  # 项目经历与岗位的相关性
        "domain_knowledge",  # 行业/领域知识
        "education_fit",  # 学历与专业要求的匹配
        "language_proficiency",  # 岗位要求的语言能力
        "role_seniority_fit",  # 职级与岗位定位的匹配
    }
)

# ⚠️ 只用于报错信息，**不参与放行判定**。判定恒为"是否在白名单里"。
RED_LINE_EXAMPLES = frozenset(
    {
        "speech_rate",
        "speech_tempo",
        "pause_duration",
        "silence_ratio",
        "voice_emotion",
        "facial_expression",
        "micro_expression",
        "face_match",
        "emotion_score",
        "gaze_stability",
    }
)


def validate_criterion_key(key: str) -> str:
    """合法则原样返回；未登记则抛 `ForbiddenCriterionKey`。"""
    if key in CRITERION_KEY_WHITELIST:
        return key

    if key in RED_LINE_EXAMPLES:
        raise ForbiddenCriterionKey(
            f"{key!r} 属合规红线维度（声学情绪信号 / 人脸表情），"
            "MUST NOT 作为评分项写入。这不是漏配，⛔ 不要把它加进白名单。"
        )

    raise ForbiddenCriterionKey(
        f"未登记的评分维度: {key!r}；已登记: {sorted(CRITERION_KEY_WHITELIST)}。"
        "未登记即拒绝（fail-closed）——新增维度请改 app/audit/criteria.py 并过 review。"
    )
