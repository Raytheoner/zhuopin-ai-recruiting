from app.parsing.spans import TextSpan, locate_quote, render_for_prompt, resolve_span_ref, split_into_spans

SAMPLE = "张明远\n\n  期望工作城市：无锡  \n技能\nC、AUTOSAR CP、CAN/LIN\n"


def test_split_skips_blank_lines_and_offsets_index_original_text():
    spans = split_into_spans(SAMPLE)
    assert [s.span_id for s in spans] == [1, 2, 3, 4]
    for s in spans:
        assert SAMPLE[s.start:s.end] == s.text
    assert spans[1].text == "期望工作城市：无锡"


def test_locate_exact_quote_returns_absolute_offsets():
    spans = split_into_spans(SAMPLE)
    loc = locate_quote(spans[1], "无锡")
    assert loc is not None
    assert SAMPLE[loc[0]:loc[1]] == "无锡"


def test_locate_tolerates_fullwidth_and_whitespace_differences():
    spans = split_into_spans(SAMPLE)
    loc = locate_quote(spans[3], "AUTOSAR  ＣＰ")  # 多空格 + 全角
    assert loc is not None
    assert SAMPLE[loc[0]:loc[1]] == "AUTOSAR CP"


def test_locate_returns_none_when_quote_absent_or_blank():
    spans = split_into_spans(SAMPLE)
    assert locate_quote(spans[0], "李四") is None
    assert locate_quote(spans[0], "   ") is None


def test_resolve_span_ref_unknown_id_is_none():
    spans = split_into_spans(SAMPLE)
    assert resolve_span_ref(spans, 99, "无锡") is None
    assert resolve_span_ref(spans, 2, "无锡") == locate_quote(spans[1], "无锡")


def test_render_for_prompt_numbers_each_span():
    rendered = render_for_prompt(split_into_spans("a\nb"))
    assert rendered == "[#1] a\n[#2] b"


def test_textspan_is_frozen():
    s = TextSpan(span_id=1, start=0, end=1, text="a")
    try:
        s.text = "b"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("TextSpan 必须不可变")
