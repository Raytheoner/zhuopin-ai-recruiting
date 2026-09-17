"""
简历原文分片与字段回指（spec「原文分片与字段回指」，design D5／D7「quote 反查校正偏移」）。

纯函数：不读文件、不读时钟。分片粒度 = 非空行。行级分片让 quote 反查的搜索空间小、
偏移可核对（字段校对页按 offset 直接切字符串高亮，design D8）。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class TextSpan:
    span_id: int
    start: int
    end: int
    text: str


def split_into_spans(text: str) -> list[TextSpan]:
    spans: list[TextSpan] = []
    pos = 0
    span_id = 0
    for line in text.split("\n"):
        line_start = pos
        pos = line_start + len(line) + 1  # +1 是被 split 吃掉的 "\n"
        stripped = line.strip()
        if not stripped:
            continue
        lead = len(line) - len(line.lstrip())
        start = line_start + lead
        end = start + len(stripped)
        span_id += 1
        spans.append(TextSpan(span_id=span_id, start=start, end=end, text=stripped))
    return spans


def _index_map(text: str) -> tuple[str, list[int]]:
    """NFKC 逐字归一化并去空白，返回 (归一化串, 每个归一化字符对应的原始下标)。"""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace():
                continue
            chars.append(c)
            index.append(i)
    return "".join(chars), index


def locate_quote(span: TextSpan, quote: str) -> tuple[int, int] | None:
    q = quote.strip()
    if not q:
        return None
    i = span.text.find(q)
    if i >= 0:
        return span.start + i, span.start + i + len(q)
    norm_text, index = _index_map(span.text)
    norm_q, _ = _index_map(q)
    if not norm_q:
        return None
    j = norm_text.find(norm_q)
    if j < 0:
        return None
    first = index[j]
    last = index[j + len(norm_q) - 1]
    return span.start + first, span.start + last + 1


def resolve_span_ref(spans: list[TextSpan], span_id: int, quote: str) -> tuple[int, int] | None:
    for span in spans:
        if span.span_id == span_id:
            return locate_quote(span, quote)
    return None


def render_for_prompt(spans: list[TextSpan]) -> str:
    return "\n".join(f"[#{s.span_id}] {s.text}" for s in spans)
