"""文件路径 → (原文, 分片) 的组装层（resume-parsing spec「原文分片与字段回指」
「扫描件与不可读文件」）。纯函数：不写库，只读文件系统。

⛔ 不吞 UnsupportedFileType——那是"文件类型不在白名单"，调用方（Task 7 的
上传接口）需要它来给出"拒收：不支持的类型"这个逐文件结果（spec「批量上传
入口」）。只吞 OcrUnavailable——那是"这份文件本身没问题，只是扫描件识别能力
暂时不可用"，按 design D14 退路转成 readable=False。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.parsing.extract_text import OcrUnavailable, extract_text
from app.parsing.spans import TextSpan, split_into_spans


@dataclass(frozen=True)
class IngestResult:
    readable: bool
    raw_text: str
    spans: list[TextSpan]
    kind: str


def ingest_resume_text(
    path: Path, *, ocr: Callable[[Path], str] | None = None
) -> IngestResult:
    try:
        extracted = extract_text(path, ocr=ocr)
    except OcrUnavailable:
        return IngestResult(readable=False, raw_text="", spans=[], kind="pdf_scan")

    # Successfully extracted text: generate spans and mark as readable.
    # OcrUnavailable is the only condition that makes it unreadable.
    spans = split_into_spans(extracted.text)
    return IngestResult(
        readable=True, raw_text=extracted.text, spans=spans, kind=extracted.kind
    )
