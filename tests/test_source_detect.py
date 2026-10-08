from __future__ import annotations

import json
from pathlib import Path

from app.intake.source import Source, detect_source

_FIXTURE = Path(__file__).parent / "fixtures" / "source_rules" / "filename_cases.json"


def test_filename_cases_fixture():
    cases = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    for filename, expected in cases.items():
        got = detect_source(filename, "")
        assert (got.value if got else None) == expected, filename


def test_detect_source_returns_only_enum_or_none():
    assert detect_source("boss直聘-张三.pdf", "") is Source.BOSS
    assert detect_source("张三-简历.pdf", "猎聘 首页页眉 无锡") is None


def test_first_page_keywords_placeholder_is_inert():
    assert detect_source("张三-简历.pdf", "智联招聘") is None
