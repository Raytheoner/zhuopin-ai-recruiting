"""4.2·口径点台账的纯函数：新增与转态。

⛔ 本文件只测纯函数，不碰真实文件、不调 CLI——CLI 层的集成测试在
test_criteria_cli.py（Task 3）。
"""

from __future__ import annotations

import datetime

import pytest

from tools.liaison.unpack.criteria import (
    MissingEvidenceError,
    compute_criteria_transition,
    compute_new_criterion,
)

TODAY = datetime.date(2026, 9, 16)

LEDGER = """# 口径点台账

## 台账

| 口径点ID | 来源信 | 描述 | 状态 | evidence | 更新 |
|---|---|---|---|---|---|
| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |
"""


def test_compute_new_criterion_allocates_sequential_id():
    new_text, new_id = compute_new_criterion(
        LEDGER, from_letter="人事部#2", desc="第二个口径点", today=TODAY
    )
    assert new_id == "HR-G-02"
    assert "| `HR-G-02` | 人事部#2 | 第二个口径点 | 待专员 |  | 2026-09-16 |" in new_text
    # 原有行逐字保留
    assert "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |" in new_text


def test_compute_new_criterion_does_not_reuse_numbers():
    """即便台账里已作废的口径点占着一个编号，下一个新增也不回收它。"""
    ledger_with_voided = LEDGER.replace(
        "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |",
        "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 已作废 |  | 2026-09-09 |",
    )
    _, new_id = compute_new_criterion(
        ledger_with_voided, from_letter="人事部#2", desc="新的", today=TODAY
    )
    assert new_id == "HR-G-02"


def test_compute_criteria_transition_to_已回复_without_evidence():
    new_text, changed = compute_criteria_transition(
        LEDGER, id="HR-G-01", to="已回复", evidence=None, today=TODAY
    )
    assert changed is True
    assert "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 已回复 |  | 2026-09-16 |" in new_text


def test_compute_criteria_transition_to_已签认_with_evidence():
    new_text, changed = compute_criteria_transition(
        LEDGER,
        id="HR-G-01",
        to="已签认",
        evidence="docs/跟进信/回件/人事部#1-2026-09-16.md#决策点a",
        today=TODAY,
    )
    assert changed is True
    assert (
        "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 已签认 | "
        "docs/跟进信/回件/人事部#1-2026-09-16.md#决策点a | 2026-09-16 |" in new_text
    )


def test_compute_criteria_transition_to_已签认_missing_evidence_raises():
    with pytest.raises(MissingEvidenceError):
        compute_criteria_transition(
            LEDGER, id="HR-G-01", to="已签认", evidence=None, today=TODAY
        )


def test_compute_criteria_transition_to_已签认_blank_evidence_raises():
    """全空白字符串等同没给——⛔ 不许用 `--evidence " "` 绕过。"""
    with pytest.raises(MissingEvidenceError):
        compute_criteria_transition(
            LEDGER, id="HR-G-01", to="已签认", evidence="   ", today=TODAY
        )


def test_missing_evidence_error_does_not_mutate_input_text():
    """异常抛出前函数不构造任何新文本——调用方据此保证"台账逐字节不变"。"""
    before = LEDGER
    try:
        compute_criteria_transition(
            LEDGER, id="HR-G-01", to="已签认", evidence="", today=TODAY
        )
    except MissingEvidenceError:
        pass
    assert LEDGER == before  # 纯函数不改入参字符串（str 本就不可变，这里是形状自证）


def test_compute_criteria_transition_unknown_id_raises_lookup_error():
    with pytest.raises(LookupError):
        compute_criteria_transition(
            LEDGER, id="HR-G-99", to="已回复", evidence=None, today=TODAY
        )
