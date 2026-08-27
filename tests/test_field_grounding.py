from app.agents.field_grounding import (
    FieldSource,
    is_user_turn,
    normalize_for_grounding,
    split_patch_sources,
    user_turns,
    verify_field_grounding,
)

# 这段历史是本文件的公共夹具：user#1 与 user#2 是两轮用户原话，
# 中间那条 assistant 不参与编号——source_turn=2 指的是"第 2 轮用户原话"。
HISTORY = [
    {"role": "user", "content": "要招一个嵌入式工程师"},
    {"role": "assistant", "content": "需要熟悉 AUTOSAR 吗？"},
    {"role": "user", "content": "需要熟悉 AUTOSAR CP，量产项目至少两个"},
]


def test_user_turns_skips_assistant():
    assert user_turns(HISTORY) == ["要招一个嵌入式工程师", "需要熟悉 AUTOSAR CP，量产项目至少两个"]


def test_user_turns_defaults_missing_role_to_user():
    """
    与 _build_user_prompt 的 turn.get("role", "user") 口径必须一模一样。
    两处对"没有 role 的那条算不算用户轮"如果答案不同，编号就会错位一格，
    而错位的表现是"引用明明对得上却被判未溯源"——一个只在脏数据上出现、
    从错误信息里完全看不出成因的故障。
    """
    assert user_turns([{"content": "没有 role"}]) == ["没有 role"]
    assert is_user_turn({"content": "x"}) is True
    assert is_user_turn({"role": "assistant", "content": "x"}) is False


def test_normalize_folds_width_and_whitespace():
    assert normalize_for_grounding("ＡＳＩＬ－Ｄ") == normalize_for_grounding("ASIL-D")
    assert normalize_for_grounding("量产 项目\n至少两个") == "量产项目至少两个"


def test_split_returns_bare_values():
    flat, sources = split_patch_sources(
        {"headcount": {"value": 2, "source_quote": "两个", "source_turn": 2}}
    )
    assert flat == {"headcount": 2}
    assert sources["headcount"] == FieldSource(quote="两个", turn=2)


def test_split_tolerates_bare_patch():
    """模型没按新提示词输出（还是老的裸值形态）时不能崩：值原样保留、记为无来源。"""
    flat, sources = split_patch_sources({"headcount": 2})
    assert flat == {"headcount": 2}
    assert sources["headcount"] == FieldSource(quote=None, turn=None)


def test_split_keeps_dict_without_value_key_as_bare():
    """没有 value 键的 dict 不是来源信封，原样当值——判据只认 value 键。"""
    flat, _ = split_patch_sources({"weird": {"a": 1}})
    assert flat == {"weird": {"a": 1}}


def test_split_on_non_dict():
    assert split_patch_sources("garbage") == ({}, {})


def test_grounded_quote_hits():
    patch = {
        "autosar_experience": {
            "value": ["CP"],
            "source_quote": "熟悉 AUTOSAR CP",
            "source_turn": 2,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == []


def test_normalized_hit_counts():
    """spec「归一化后仍算命中」：差异只有空白与全半角时必须算命中。"""
    patch = {
        "autosar_experience": {
            "value": ["CP"],
            "source_quote": "熟悉ＡＵＴＯＳＡＲ  CP",
            "source_turn": 2,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == []


def test_fabricated_quote_is_ungrounded():
    """spec「引用对不上」：模型凭空生成引用。"""
    patch = {
        "mcu_family": {
            "value": ["ARM Cortex-M"],
            "source_quote": "用的是 ARM Cortex-M",
            "source_turn": 2,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == ["mcu_family"]


def test_wrong_turn_is_ungrounded():
    """spec「引用对不上」的另一半：引用是真的，但指错了轮次。逐轮判定，不做全局搜索。"""
    patch = {
        "autosar_experience": {
            "value": ["CP"],
            "source_quote": "熟悉 AUTOSAR CP",
            "source_turn": 1,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == ["autosar_experience"]


def test_out_of_range_turn_is_ungrounded():
    patch = {"headcount": {"value": 2, "source_quote": "两个", "source_turn": 99}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_zero_turn_is_ungrounded():
    """轮次是 1-based。0 与负数一律越界，不许被当成 Python 的反向索引。"""
    patch = {"headcount": {"value": 2, "source_quote": "两个", "source_turn": 0}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_missing_source_is_ungrounded():
    """spec「缺少来源」：不给引用 → 未溯源，而不是被当作已溯源放行。"""
    assert verify_field_grounding({"headcount": 2}, HISTORY) == ["headcount"]


def test_empty_quote_is_ungrounded():
    """空串/纯空白引用在任何原话里都是子串，必须在判定前就拦掉，否则它等于万能通行证。"""
    patch = {"headcount": {"value": 2, "source_quote": "   ", "source_turn": 2}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_garbage_source_structure_degrades():
    """spec「来源结构缺失时降级而非报错」：结构完全不合法时不抛异常，值照留，全计未溯源。"""
    patch = {
        "job_title": {
            "value": "嵌入式工程师",
            "source_quote": {"nested": "dict"},
            "source_turn": [1, 2],
        },
        "headcount": {"value": 2},
    }
    flat, _ = split_patch_sources(patch)
    assert flat == {"job_title": "嵌入式工程师", "headcount": 2}
    assert sorted(verify_field_grounding(patch, HISTORY)) == ["headcount", "job_title"]


def test_string_turn_is_coerced():
    """模型把轮次写成字符串 "2" 是常见退化，能救就救，不因此判未溯源。"""
    patch = {"headcount": {"value": 2, "source_quote": "至少两个", "source_turn": "2"}}
    assert verify_field_grounding(patch, HISTORY) == []


def test_bool_turn_is_not_an_index():
    """Python 里 True == 1。不显式挡掉的话，source_turn=true 会静默变成"第 1 轮"。"""
    patch = {"headcount": {"value": 2, "source_quote": "至少两个", "source_turn": True}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_exempt_fields_skip_verification():
    """tasks 7.4(a)：系统管理字段不参与校验。"""
    patch = {"unspecified_fields": {"value": ["mcu_family"]}}
    assert verify_field_grounding(patch, HISTORY, exempt_fields={"unspecified_fields"}) == []


def test_normalized_value_with_verbatim_quote_is_grounded():
    """
    tasks 7.7 归纳负例：用户说"MISRA C"，字段值写成规范化枚举值，但引用逐字命中
    → 必须判为已溯源。**校验的是引用的真实性，不是值与引用的等价性**（决策 11）。
    """
    history = [{"role": "user", "content": "要求熟悉 MISRA C 规范"}]
    patch = {
        "toolchain": {
            "value": ["MISRA-C:2012"],
            "source_quote": "熟悉 MISRA C 规范",
            "source_turn": 1,
        }
    }
    assert verify_field_grounding(patch, history) == []


def test_selected_option_is_grounded_without_a_special_case():
    """
    tasks 7.4(b) 的核实结论，钉成测试。

    单元 C 的 collectSelections()（app/web/static/index.html:271）把点选拼成
    `问题原文：档位A、档位B` 一行、与自由文本合并成一条 message 提交给既有的
    POST /reply（API 契约未变，见 tests/test_static_frontend.py 的
    test_reply_api_contract_has_no_selected_options）。于是**被选中的档位文本
    逐字出现在该轮用户原话里**，7.3 的子串判定天然命中——不需要任何
    "点选例外"分支。

    **这个测试将来若失败，说明前端的拼接格式变了、(b) 重新变成真问题。
    那是一次设计对话（要不要给 ReplyRequest 加回 selected_options），
    不是一个可以删掉的测试。**
    """
    # 逐字复刻 collectSelections() 的输出形态：问题原文 + "：" + 档位、顿号分隔
    history = [
        {"role": "user", "content": "要招个做 ECU 的"},
        {"role": "assistant", "content": "是否有功能安全等级要求？"},
        {
            "role": "user",
            "content": "是否有功能安全等级要求？：ASIL-D\n量产项目要求几个？：2 个及以上",
        },
    ]
    patch = {
        "functional_safety": {"value": "ASIL-D", "source_quote": "ASIL-D", "source_turn": 2},
        "project_experience_requirement": {
            "value": "2 个及以上量产项目",
            "source_quote": "2 个及以上",
            "source_turn": 2,
        },
    }
    assert verify_field_grounding(patch, history) == []


def test_free_text_mixed_with_selection_still_grounds():
    """点选 + 自由文本混合提交（单元 C 支持的第三条路径）同样天然命中。"""
    history = [
        {"role": "user", "content": "MCU 用哪个系列？：Infineon TriCore\n另外要会 CAPL 脚本"}
    ]
    patch = {
        "mcu_family": {"value": ["TriCore"], "source_quote": "Infineon TriCore", "source_turn": 1},
        "toolchain": {"value": ["CAPL"], "source_quote": "会 CAPL 脚本", "source_turn": 1},
    }
    assert verify_field_grounding(patch, history) == []
