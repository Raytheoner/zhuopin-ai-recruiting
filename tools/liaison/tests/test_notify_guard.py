"""第 6 章·长度守卫与降级决策（6.2 / 6.3 / 6.8 的判定部分）。

本文件**全部是纯函数测试**：不建库、不起网络、不 sleep。
"""

from __future__ import annotations

import inspect

import pytest

from tools.liaison.notify import guard


def test_byte_length_counts_utf8_bytes_not_characters():
    """6.2 逐字：按 UTF-8 **字节数**判定，⛔ 不按字符数。"""
    assert guard.compute_byte_length("abc") == 3
    assert guard.compute_byte_length("中文") == 6
    assert len("中文") == 2  # 反面对照：字符数会给出完全不同的答案


def test_char_count_under_limit_but_bytes_over_is_judged_over_limit():
    """spec 场景「中文内容按字节判定」：字符数不超、字节数超 → 判超限。

    反证的两个数就摆在断言里：**同一段中文**，按字符数看远在阈值之下，
    按 UTF-8 字节数看已经超了 1904 字节。按字符判就是一条静默截断。
    """
    text = "中" * 2000  # 2000 字符 < 4096；6000 字节 > 4096
    assert len(text) == 2000  # 字符数
    assert len(text.encode("utf-8")) == 6000  # UTF-8 字节数
    assert len(text) < guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    assert len(text.encode("utf-8")) > guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    verdict = guard.compute_length_guard(
        text, limit_bytes=guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    )
    assert verdict.byte_length == 6000
    assert verdict.over_limit is True


@pytest.mark.parametrize(
    ("byte_length", "expected_over_limit"),
    [(4095, False), (4096, False), (4097, True)],
)
def test_boundary_at_the_limit_and_one_byte_either_side(byte_length, expected_over_limit):
    """边界：阈值 - 1 / 恰好等于阈值 / 阈值 + 1。**等于阈值不算超。**"""
    text = "a" * byte_length
    verdict = guard.compute_length_guard(text, limit_bytes=4096)
    assert verdict.byte_length == byte_length
    assert verdict.limit_bytes == 4096
    assert verdict.over_limit is expected_over_limit


def test_boundary_is_measured_in_bytes_even_for_multibyte_text():
    """同样的三个边界点，用多字节文本再钉一遍：动的是字节数，不是字符数。"""
    under = "中" * 1365  # 1365 字符 / 4095 字节
    assert len(under) == 1365
    assert len(under.encode("utf-8")) == 4095
    assert guard.compute_length_guard(under, limit_bytes=4096).over_limit is False

    at_limit = under + "a"  # 4096 字节，恰好等于阈值
    assert len(at_limit.encode("utf-8")) == 4096
    assert guard.compute_length_guard(at_limit, limit_bytes=4096).over_limit is False

    over = under + "aa"  # 4097 字节
    assert len(over.encode("utf-8")) == 4097
    assert guard.compute_length_guard(over, limit_bytes=4096).over_limit is True


def test_two_channels_judge_the_same_text_independently():
    """spec 场景「两个通道阈值不同」：同一段内容按各自阈值独立判定。"""
    text = "中" * 2000  # 6000 字节：aibot(20480) 不超，群 webhook(4096) 超
    aibot = guard.compute_length_guard(text, limit_bytes=guard.AIBOT_CHANNEL.limit_bytes)
    group = guard.compute_length_guard(
        text, limit_bytes=guard.GROUP_WEBHOOK_CHANNEL.limit_bytes
    )
    assert aibot.over_limit is False
    assert group.over_limit is True


def test_the_two_channel_limits_are_two_different_configured_values():
    """D9 逐字：⛔ 不共用同一个阈值常量。

    这条断言的意义不在于"4096 != 20480"这个算术事实，而在于：谁要是把两条通道
    合并成一个常量，这里会当场变红。
    """
    assert guard.AIBOT_CHANNEL_LIMIT_BYTES == 20480
    assert guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES == 4096
    assert guard.AIBOT_CHANNEL_LIMIT_BYTES != guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    assert guard.AIBOT_CHANNEL.limit_bytes != guard.GROUP_WEBHOOK_CHANNEL.limit_bytes


def test_length_guard_has_no_default_limit():
    """结构性：`limit_bytes` 必传且无默认值 ⇒ "忘了传就用上别的通道的数"写不出来。"""
    parameter = inspect.signature(guard.compute_length_guard).parameters["limit_bytes"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        guard.compute_length_guard("x")  # type: ignore[call-arg]


def test_digest_is_sixteen_hex_chars_and_stable():
    """6.6：幂等键的 business_key 分量 = 内容摘要（SHA-256 前 16 位十六进制）。"""
    digest = guard.compute_notify_digest("值守通知")
    assert len(digest) == 16
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert digest == guard.compute_notify_digest("值守通知")
    assert digest != guard.compute_notify_digest("值守通知 ")


def test_prefix_never_splits_a_multibyte_character():
    """按字节预算取前缀时 ⛔ 不切碎多字节字符。"""
    # "中" 占 3 字节，预算 4 字节只能放下一个字
    assert guard.compute_text_prefix_within_bytes("中中中", 4) == "中"
    assert guard.compute_text_prefix_within_bytes("中中中", 6) == "中中"
    assert guard.compute_text_prefix_within_bytes("中中中", 2) == ""


def test_under_limit_content_is_sent_as_is():
    plan = guard.compute_notify_plan("短消息", limit_bytes=4096, attachment_supported=True)
    assert plan.mode == guard.MODE_DIRECT
    assert plan.body == "短消息"
    assert plan.attachment_filename is None
    assert plan.reject_reason is None


def test_over_limit_content_degrades_to_summary_plus_attachment():
    """spec 场景「超长内容降级成功」：群里收到提要与附件，完整内容可从附件取回。"""
    text = "中" * 2000
    plan = guard.compute_notify_plan(text, limit_bytes=4096, attachment_supported=True)
    assert plan.mode == guard.MODE_DEGRADED
    # 提要必须自己不超限
    assert guard.compute_byte_length(plan.body) <= 4096
    # 完整内容一字不少地进了附件
    assert plan.attachment_content == text
    assert plan.attachment_filename is not None
    assert plan.digest in plan.attachment_filename


def test_degraded_summary_declares_the_truncation_and_names_the_attachment():
    """spec 场景「不静默截断」：被裁掉的部分必须在提要里被**声明**出来。"""
    text = "中" * 2000
    plan = guard.compute_notify_plan(text, limit_bytes=4096, attachment_supported=True)
    assert "降级" in plan.body
    assert plan.attachment_filename in plan.body
    assert str(plan.byte_length) in plan.body
    # 反面：提要 ⛔ 不许是原文的一段纯前缀（那就是没声明的截断）
    assert not text.startswith(plan.body)


def test_reject_when_the_channel_has_no_attachment_carrier():
    """spec 场景「无法降级时拒发」之一：该通道没有附件承载方式。"""
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=False)
    assert plan.mode == guard.MODE_REJECT
    assert plan.body == ""
    assert plan.reject_reason == guard.REJECT_NO_ATTACHMENT_CHANNEL


def test_reject_when_the_summary_itself_would_still_be_over_limit():
    """spec 场景「无法降级时拒发」之二：提要本身仍超限。

    通道上限小到连"这是降级提要"这句声明都装不下时，⛔ 不许把声明砍掉硬发——
    砍掉声明发出去的就是一条静默截断的通知。
    """
    plan = guard.compute_notify_plan("中" * 100, limit_bytes=32, attachment_supported=True)
    assert plan.mode == guard.MODE_REJECT
    assert plan.reject_reason == guard.REJECT_SUMMARY_STILL_OVER


@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_limit_is_a_programming_error(limit):
    with pytest.raises(ValueError):
        guard.compute_length_guard("x", limit_bytes=limit)
