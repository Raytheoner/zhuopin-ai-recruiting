from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FollowupSpec:
    """
    领域知识库里的一条追问：问什么、补哪个字段、有哪些具体档位。

    从 list[str] 升级成对象是第 3 章的地基（design.md 决策 4）：档位会被写进
    岗位硬性要求，来源必须可追溯、可评审，所以优先取知识库而不是让模型现场编
    ——"编造 MCU 型号"正是这类风险的高发面。

    field 必须是 app.agents.intake_question.QUESTION_TARGET_FIELDS 里的真实
    字段名，或者 None；写一个不存在的字段名会被 derive_question_id 静默降级成
    文本哈希 id，第 5 章的重问追踪就跟丢了。test_ecu_knowledge 有守卫测试。

    options 要么为空，要么 2-3 个：spec「模糊回复与反问的兜底档位」写死了
    "2 至 3 个具体的候选档位"，1 个不算选择，4 个开始变成新的负担。
    """

    text: str
    field: str | None = None
    options: tuple[str, ...] = ()


# 术语 → 追问（每条不超过 3 个，满足"每轮追问不超过 3 个问题"约束）
FOLLOWUP_RULES: dict[str, list[FollowupSpec]] = {
    "嵌入式开发": [
        FollowupSpec(
            "是否涉及 AUTOSAR（CP/AP）？",
            field="autosar_experience",
            options=("CP", "AP", "无要求"),
        ),
        FollowupSpec(
            "MCU 平台族是？（如英飞凌 Aurix / NXP S32K / TI）",
            field="mcu_family",
            options=("英飞凌 Aurix", "NXP S32K", "不限"),
        ),
        FollowupSpec(
            "是否有功能安全等级（ASIL）要求？",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        ),
    ],
    "驱动开发": [
        FollowupSpec(
            "驱动对接的总线类型是？（CAN-FD / LIN / 以太网）",
            field="core_skills",
            options=("CAN-FD", "LIN", "车载以太网"),
        ),
        FollowupSpec(
            "是否要求 UDS 诊断栈经验？",
            field="diag_stack",
            options=("UDS（ISO 14229）", "OBD 诊断", "无要求"),
        ),
    ],
    "功能安全": [
        FollowupSpec(
            "具体到 ASIL 哪个等级？",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        ),
        # field 刻意留 None：同一轮里它和上面那条都指向 functional_safety 会
        # 撞同一个 question_id，_to_intake_questions 的去重会把它整条丢掉。
        # 留 None 走文本哈希，两条问题都问得出来，代价是第 5 章追踪不到它。
        FollowupSpec(
            "是否要求 FuSa 工程师认证？",
            options=("要求", "不要求"),
        ),
    ],
    "算法开发": [
        FollowupSpec(
            "是感知/控制/诊断算法中的哪一类？",
            field="core_skills",
            options=("感知算法", "控制算法", "诊断算法"),
        ),
        FollowupSpec(
            "是否要求量产项目（SOP）经验？",
            field="sop_projects",
            options=("要求量产（SOP）经验", "预研/样件经验即可", "不限"),
        ),
    ],
    # 以下四条是非 ECU（采购/供应链）侧词条。姚祖怡那场就是卡死在"一般材料"
    # 上——知识库当时一个采购词条都没有，兜底只能退回空话（design.md 决策 4）。
    "一般材料": [
        FollowupSpec(
            "该岗位采购的「一般材料」指哪些品类？",
            field="project_experience_requirement",
            options=("原材料（钢材/塑料粒子等）", "电子元器件", "五金标准件与包装辅材"),
        ),
    ],
    "办公采购": [
        FollowupSpec(
            "办公采购的范围主要是哪一类？",
            field="project_experience_requirement",
            options=("办公用品与耗材", "IT 设备与软件", "行政服务外包"),
        ),
    ],
    "非标产品": [
        FollowupSpec(
            "非标产品指的是哪一类定制件？",
            field="project_experience_requirement",
            options=("按图定制机加工件", "定制工装夹具", "定制自动化设备"),
        ),
    ],
    "供应商开发": [
        FollowupSpec(
            "供应商开发这块最看重哪一项能力？",
            field="core_skills",
            options=("新供应商导入与审核", "供应商绩效与降本", "供应商质量改善"),
        ),
    ],
}


# 字段 → 通用候选档位。知识库未命中时的第二道，覆盖 JobProfile 的全部可问字段。
# spec「领域外的字段也要有兜底」要求这里必须给得出档位，且含"无要求 / 不限"
# 这类明确的否定档位——否则用户会被逼着在三个"要"里挑一个。
GENERIC_FIELD_OPTIONS: dict[str, tuple[str, ...]] = {
    "job_title": ("沿用现有岗位名称", "按职级重新拟定", "由 HR 拟定"),
    "department": ("研发部门", "供应链/采购部门", "其他部门（我来补充）"),
    "headcount": ("1 人", "2-3 人", "3 人以上"),
    "education_requirement": ("大专及以上", "本科及以上", "硕士及以上"),
    "experience_years": ("3 年以下", "3-5 年", "5 年以上"),
    "core_skills": ("按岗位常规技能即可", "有明确必会项（我来补充）", "不限"),
    "project_experience_requirement": ("要求同行业经验", "要求同岗位经验", "不限"),
    # 软技能档位只能落到 soft_skill_keywords（合规红线：主观描述不得进硬门槛）。
    "soft_skill_keywords": ("沟通协调", "跨部门推动", "供应商/客户谈判"),
    "autosar_experience": ("CP", "AP", "无要求"),
    "functional_safety": ("ASIL-B", "ASIL-D", "无要求"),
    "mcu_family": ("英飞凌 Aurix", "NXP S32K", "不限"),
    "diag_stack": ("UDS（ISO 14229）", "OBD 诊断", "无要求"),
    "sop_projects": ("要求量产（SOP）经验", "预研/样件经验即可", "不限"),
    "toolchain": ("Vector（CANoe/CANape）", "ETAS INCA", "不限"),
}

# 最后一道：连字段都没有（field=None）的问题也必须给得出选择。
LAST_RESORT_OPTIONS: tuple[str, ...] = ("无要求 / 不限", "按行业惯例即可", "有明确要求（我来补充）")

# 系统自己合成兜底问题时用的问法。第 6 章会另外引入一份"字段中文名"映射
# （tasks 6.4，用于确认前的缺口警示），那份是给人看字段名的，这份是给人回答
# 的问句，用途不同不要合并；6.4 落地时在这里加一条注释互相指认即可。
FALLBACK_QUESTION_TEXT: dict[str, str] = {
    "job_title": "这个岗位对外挂什么岗位名称？",
    "department": "这个岗位归在哪个部门？",
    "headcount": "这次计划招几个人？",
    "education_requirement": "学历上有什么要求？",
    "experience_years": "工作年限上有什么要求？",
    "core_skills": "有哪些必须会的核心技能？",
    "project_experience_requirement": "对项目经历有什么要求？",
    "soft_skill_keywords": "软技能上更看重哪一项？",
    "autosar_experience": "是否涉及 AUTOSAR（CP/AP）？",
    "functional_safety": "功能安全等级（ASIL）上有什么要求？",
    "mcu_family": "MCU 平台族有指定吗？",
    "diag_stack": "诊断栈上有什么要求？",
    "sop_projects": "对量产（SOP）项目经验有什么要求？",
    "toolchain": "工具链上有什么要求？",
}

# 合成兜底问题时的字段优先级：先问决定寻源方向的，再问细节。
# 顺序是刻意固定的——同一份对话重跑必须问出同一个问题，否则 8.1 的回放对比
# 不可复算。
FALLBACK_FIELD_ORDER: tuple[str, ...] = (
    "job_title",
    "department",
    "headcount",
    "experience_years",
    "education_requirement",
    "core_skills",
    "functional_safety",
    "autosar_experience",
    "mcu_family",
    "diag_stack",
    "toolchain",
    "sop_projects",
    "project_experience_requirement",
    "soft_skill_keywords",
)


def match_ambiguous_terms(text: str) -> list[str]:
    return [term for term in FOLLOWUP_RULES if term in text]


def library_options_for_field(field: str | None) -> tuple[str, ...]:
    """知识库里为这个字段登记过的档位，取第一条命中的。没有则返回空。"""
    if not field:
        return ()
    for specs in FOLLOWUP_RULES.values():
        for spec in specs:
            if spec.field == field and spec.options:
                return spec.options
    return ()


def fallback_options_for_field(field: str | None) -> tuple[str, ...]:
    """
    兜底档位的三级取数：领域选项库 → 通用字段档位 → 最后一道。

    **保证非空且长度在 2-3 之间**——spec「领域外的字段也要有兜底」要求
    "不得因为知识库未命中而退回空话"，返回空元组就是退回空话。
    """
    options = library_options_for_field(field)
    if not options and field:
        options = GENERIC_FIELD_OPTIONS.get(field, ())
    if not options:
        options = LAST_RESORT_OPTIONS
    return options[:3]
