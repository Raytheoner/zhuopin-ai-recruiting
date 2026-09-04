"""画像摘要渲染（tasks 6.1）。

**这是在修一处现网真实缺陷**：`confirmation_prompt` 的 payload 里
`profile_patch_accumulated` 一直有值，但前端从头到尾没有任何代码读它——
业务经理是在**看不见画像内容**的情况下点的「确认画像，生成 JD」。

本文件的核心断言只有两条，其余都是围着它们的守卫：
  ① 填满的画像必须**每个字段都渲染得出来**（漏一个 = 业务经理又看不见一个）
  ② 输出里**不许出现任何英文字段标识**（payload 里没有它，界面上就不会有它）
"""

import json

import pytest

from app.schemas.job_profile import FIELD_LABELS, summarize_profile

# 每个 JobProfile 字段都给了值的一份画像。⛔ 不要在这里省字段——它正是断言 ①
# 的输入，少一个字段就少验一个字段。
FULL_PROFILE = {
    "job_title": "底层软件工程师",
    "department": "电子电器研发部",
    "headcount": 2,
    "education_requirement": "本科及以上",
    "experience_years": "3-5年",
    "core_skills": [
        {"name": "CAN 驱动开发", "required": True},
        {"name": "Python 脚本", "required": False},
    ],
    "project_experience_requirement": "至少一个量产 ECU 项目",
    "soft_skill_keywords": ["沟通", "抗压"],
    "autosar_experience": ["CP"],
    "functional_safety": "ASIL-B",
    "mcu_family": ["TC3xx", "S32K"],
    "diag_stack": ["UDS", "CANoe"],
    "sop_projects": [
        {
            "vehicle_model": "A05 纯电",
            "sop_date": "2024-06",
            "role": "BSW 负责人",
            "is_mass_production": True,
        }
    ],
    "toolchain": ["Vector DaVinci", "Tasking"],
}


def test_every_profile_field_renders(**_):
    """断言 ①：填满的画像里，FIELD_LABELS 的每一个字段都要出现在摘要里。

    这条测试与 test_job_profile_schema.py 的标签完整性测试配成一对：那条保证
    「新字段有中文名」，这条保证「新字段真的被渲染出来」。少了这条，一个加了
    标签却渲染成空串的字段会静默从确认页上消失。
    """
    summary = summarize_profile(FULL_PROFILE)
    assert [item["label"] for item in summary] == list(FIELD_LABELS.values())
    assert all(item["value"] for item in summary), "有字段渲染成了空串"


def test_no_english_field_identifier_leaks_into_the_output():
    """断言 ②：输出里不许出现任何英文字段标识。

    前端只渲染它拿到的东西。payload 里没有英文 snake_case，界面上就不可能
    出现英文 snake_case——这比"叮嘱前端别渲染"可靠得多
    （index.html:162 那条既有约束的同一条思路）。
    """
    blob = json.dumps(summarize_profile(FULL_PROFILE), ensure_ascii=False)
    for field_name in FIELD_LABELS:
        assert field_name not in blob, f"英文字段名 {field_name} 泄漏进了摘要"


def test_empty_and_missing_fields_are_dropped():
    """"卡片可读，不堆字段"（tasks 6.1 原话）：没值的字段不占版面。"""
    summary = summarize_profile(
        {
            "job_title": "嵌入式工程师",
            "department": "",
            "mcu_family": [],
            "project_experience_requirement": None,
            "sop_projects": [],
        }
    )
    assert summary == [{"label": "岗位名称", "value": "嵌入式工程师"}]


def test_internal_keys_never_appear():
    """`_jd_text` / `_gap_acknowledgement` 是内部键，不是给人看的画像内容。

    这条不靠"记得跳过下划线"成立——`summarize_profile` 遍历的是 FIELD_LABELS
    而不是 profile 的键，所以任何不在字段表里的键**结构上**进不来。
    """
    summary = summarize_profile(
        {
            "job_title": "嵌入式工程师",
            "_jd_text": "【AI 生成】…",
            "_gap_acknowledgement": {"acknowledged": True},
            "unspecified_fields": ["toolchain"],
            "某个模型幻觉出来的键": "值",
        }
    )
    assert summary == [{"label": "岗位名称", "value": "嵌入式工程师"}]


def test_core_skills_render_with_required_marker():
    summary = summarize_profile({"core_skills": FULL_PROFILE["core_skills"]})
    assert summary == [
        {"label": "核心技能", "value": "CAN 驱动开发（必会）、Python 脚本（加分）"}
    ]


def test_sop_projects_render_as_readable_sentence():
    summary = summarize_profile({"sop_projects": FULL_PROFILE["sop_projects"]})
    assert summary == [
        {"label": "量产项目经历", "value": "A05 纯电 · SOP 2024-06 · BSW 负责人 · 已量产"}
    ]


def test_boolean_and_number_render_in_chinese():
    summary = summarize_profile({"headcount": 2})
    assert summary == [{"label": "招聘人数", "value": "2"}]


@pytest.mark.parametrize(
    "profile",
    [
        {"core_skills": "CAN 驱动"},               # 该是列表，模型给了字符串
        {"core_skills": [{"required": True}]},      # 缺 name
        {"headcount": "两个人"},                    # 该是整数，模型给了中文
        {"sop_projects": [{"vehicle_model": None}]},
        {"autosar_experience": [None, "CP"]},
        {"toolchain": {"a": 1}},                    # 该是列表，模型给了对象
    ],
)
def test_malformed_llm_output_never_raises(profile):
    """⚠️ 这个函数跑在 `_deliver_node` 里，抛异常就是**整轮对话当场失败**。

    输入是 LLM 自由生成的裸 dict，还没撞过 JobProfile 的类型约束（那要到
    POST /confirm 才发生）。任何形状都必须能渲染成字符串——渲染得难看可以接受，
    抛异常不行。
    """
    assert isinstance(summarize_profile(profile), list)
