"""`scripts/codex_jsonl_summary.py` 的 `peak`（第 13 列 `upeak`）口径测试（2026-09-30 `0930I`）。

覆盖：
  ① 两条累计 `turn.completed` ⇒ `peak` 取逐轮增量 `input + cache_write` 的最大值；
  ② 0930F 真实 usage 样本 ⇒ `peak == "6454677"`（旧实现会记 12,849,173）；
  ③ 全非 JSON 输入 ⇒ 七列全 `-` 且文本原样兜底（防退）；
  ④ `agent_message` 文本拼接顺序不变。

⛔ 只碰临时文件，不读写任何真实批次目录。
"""
from __future__ import annotations

import json

from scripts.codex_jsonl_summary import summarize


def _line(obj: dict) -> str:
    return json.dumps(obj)


def test_peak_is_per_turn_increment_max():
    """两条累计 turn.completed：peak = max(增量 input + 增量 cache_write)。"""
    seq = "\n".join([
        _line({"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 10}}),
        _line({"type": "turn.completed", "usage": {"input_tokens": 3000, "output_tokens": 40}}),
    ])
    fields, _txt, saw = summarize(seq + "\n")
    assert saw is True
    assert fields[6] == "2000"  # 第二轮增量 = 3000 - 1000
    # 累计列与 turns 语义不变
    assert fields[1] == "3000"  # 累计 input
    assert fields[2] == "40"    # 累计 output
    assert fields[5] == "2"     # turns


def test_peak_single_event_includes_cache_write_not_cache_read():
    """单事件（codex 常态）：peak = input + cache_write，⛔ 不叠加 cache_read。"""
    ev = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 5000,
            "cached_input_tokens": 4000,
            "cache_write_input_tokens": 700,
            "output_tokens": 100,
        },
    }
    fields, _txt, _saw = summarize(_line(ev) + "\n")
    assert fields[6] == "5700"  # 5000 + 700，不含 cached 4000
    assert fields[3] == "4000"  # cache_read 累计列仍照记
    assert fields[4] == "700"   # cache_write 累计列仍照记


def test_peak_real_0930f_sample():
    """0930F 真实样本：peak 由旧口径 12,849,173 归正为 6,454,677。"""
    real = _line({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 6454677,
            "cached_input_tokens": 6394496,
            "cache_write_input_tokens": 0,
            "output_tokens": 52930,
            "reasoning_output_tokens": 24921,
        },
    })
    fields, _txt, _saw = summarize(real + "\n")
    assert fields[6] == "6454677"
    assert fields[1] == "6454677"
    assert fields[3] == "6394496"
    assert fields[5] == "1"


def test_missing_usage_fields_treated_as_zero():
    """缺字段（老版本 codex）按 0 处理，⛔ 不让整份解析退化成 `-`。"""
    ev = {"type": "turn.completed", "usage": {"input_tokens": 1234}}
    fields, _txt, saw = summarize(_line(ev) + "\n")
    assert saw is True
    assert fields == ["-", "1234", "0", "0", "0", "1", "1234"]


def test_all_non_json_falls_back_to_dash_and_raw_text():
    """全非 JSON 输入 ⇒ 七列全 `-`，原文整段当文本兜底（防退）。"""
    raw = "not json at all\n"
    fields, txt, saw = summarize(raw)
    assert fields == ["-"] * 7
    assert saw is False
    assert "not json at all" in txt


def test_agent_message_text_order_preserved():
    """agent_message 文本按事件顺序拼接。"""
    seq = "\n".join([
        _line({"type": "item.completed", "item": {"type": "agent_message", "text": "第一段"}}),
        _line({"type": "turn.completed", "usage": {"input_tokens": 100}}),
        _line({"type": "item.completed", "item": {"type": "agent_message", "text": "第二段"}}),
    ])
    _fields, txt, saw = summarize(seq + "\n")
    assert saw is True
    assert txt == "第一段\n第二段\n"
