import re
from pathlib import Path

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

# 只读探针：定位仓库根用 __file__ 而不是 Path("app/…") 相对路径——后者依赖
# pytest 的当前工作目录，换个目录起 pytest 就会读错文件（tests/test_static_frontend.py
# 的写法依赖 CWD==仓库根，这里刻意不复用那个假设，改用更稳的 __file__ 定位）。
# 这段只读取，不修改 index.html——修改它会违反本单元"业务可见性为零"的约束。
_INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "index.html"
_INDEX_HTML = _INDEX_HTML_PATH.read_text(encoding="utf-8")

# 抓出 collectSelections() 的函数体，而不是在全文件里裸搜分隔符——避免其它
# 无关代码里偶然出现同样的标点导致假阴性（测试该红时没红）。
_COLLECT_SELECTIONS_RE = re.compile(r"function collectSelections\(\)\s*\{(.*?)\n {4}\}", re.DOTALL)


def _collect_selections_body() -> str:
    match = _COLLECT_SELECTIONS_RE.search(_INDEX_HTML)
    assert match, (
        "index.html 里找不到 collectSelections() 函数——点选拼接逻辑可能被"
        "重命名、移动或删除了，tasks 7.4(b)『点选天然命中子串判定』的核实"
        "结论需要重新核对，不要绕过这条断言。"
    )
    return match.group(1)


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

    这条测试本身用的是手工复刻的字面量，不读取 index.html，所以它**不能**
    单独充当"前端格式漂移探测器"——那份工作交给下面的
    test_collect_selections_format_still_matches_the_fixture，它会真的去读
    index.html。这两条测试要放在一起看：前者证明"这种格式下判定会通过"，
    后者证明"index.html 里现在确实还是这种格式"。任何一条红了都值得停下来，
    重新判断 7.4(b) 是否还成立——不是可以删掉的测试。
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


def test_collect_selections_format_still_matches_the_fixture():
    """
    review 修复（Important 1）：上面两条测试的 history 都是手工复刻的字面量，
    不读取 index.html，所以它们对"前端拼接格式真的漂移了"这件事完全不知情。
    这条测试是唯一真正读 index.html 的一条——只读、不修改，diff 里不会
    出现 app/web/static/index.html。

    锁的是 collectSelections() 函数体里三个要素的**组合**，而不是整行字面量
    或单个标点：
    - `block.dataset.qtext`：拼接里仍然带着问题原文（不是只发送档位 ID），
      这正是"点选文本会落进用户原话"这条结论成立的前提；
    - `"："`：问题原文与档位之间的分隔符；
    - `picked.join("、")`：档位仍然以字符串 join 的方式拼进同一行文本，
      而不是被序列化成结构化数据（比如 JSON.stringify(picked)）。

    只锁标点（太松）会漏掉"改成结构化 ID"这类真正让 (b) 重新成立的漂移；
    锁整行字面量（太紧）会被无关的换行/空格/变量名重排等格式化改动误伤。
    三者组合命中的是"点选内容是否仍以可被子串搜到的文本形式，混进用户
    原话"这一件事——这正是本 Task 的核实结论所依赖的事实。

    这条测试将来若失败，说明 collectSelections() 的拼接方式变了，
    tasks 7.4(b) 需要重新判断是否要给 API 加回 selected_options
    ——不是可以删掉的测试。
    """
    body = _collect_selections_body()
    assert "block.dataset.qtext" in body, (
        "collectSelections() 不再把问题原文拼进去了——点选内容可能只剩档位 ID，"
        "7.4(b)『点选文本天然落进用户原话』的前提已经不成立。"
    )
    assert '"："' in body, "问题原文与档位之间的分隔符变了，需要重新核对 7.4(b)。"
    assert 'picked.join("、")' in body, (
        "档位不再以字符串 join 的形式拼接（可能改成了结构化数据），"
        "子串判定天然命中的假设需要重新核对。"
    )
