from __future__ import annotations

import re

from app.intake.phone_hash import extract_phone_hash, hash_phone
from app.parsing.spans import TextSpan


def _spans(*lines: str) -> list[TextSpan]:
    return [
        TextSpan(span_id=i, start=0, end=len(t), text=t)
        for i, t in enumerate(lines, start=1)
    ]


def test_returns_hash_of_first_phone():
    spans = _spans("张三", "手机：13800138000", "邮箱：a@b.c")
    assert extract_phone_hash(spans) == hash_phone("13800138000")


def test_result_does_not_match_phone_regex():
    value = extract_phone_hash(_spans("13800138000"))
    assert value is not None
    assert re.search(r"1[3-9]\d{9}", value) is None


def test_no_phone_returns_none():
    assert extract_phone_hash(_spans("张三", "无手机号")) is None


def test_rejects_ten_digit_number():
    # 只有 10 位，不是合法手机号
    assert extract_phone_hash(_spans("1380013800")) is None


def test_no_plaintext_in_logs(caplog):
    extract_phone_hash(_spans("13800138000"))
    assert "13800138000" not in caplog.text
