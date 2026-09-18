from __future__ import annotations

from pathlib import Path

import docx
import pytest

from app.parsing.resume_ingest import ingest_resume_text


def _write_docx(path: Path, paragraphs: list[str]) -> Path:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    document.save(str(path))
    return path


def test_readable_docx_returns_spans(tmp_path):
    path = _write_docx(tmp_path / "a.docx", [
        "张三",
        "工作年限：5年，现就职于阿里巴巴从事后端开发工作",
        "技能：Python、Java、Go、SQL、Docker等多种技术栈，熟悉分布式系统设计"
    ])
    result = ingest_resume_text(path)
    assert result.readable is True
    assert result.kind == "docx"
    assert len(result.spans) == 3
    assert result.spans[0].text == "张三"
    assert result.raw_text.startswith("张三")


def test_unreadable_when_ocr_unavailable(tmp_path, monkeypatch):
    """扫描件路径：pypdf 抽出空文本 ⇒ 走 OCR ⇒ OcrUnavailable ⇒ readable=False。"""
    import app.parsing.extract_text as extract_text_mod

    def _empty_pdf_text(_path):
        return ""

    def _raise_ocr_unavailable(_path):
        raise extract_text_mod.OcrUnavailable("PaddleOCR 未安装")

    monkeypatch.setattr(extract_text_mod, "extract_pdf_text", _empty_pdf_text)
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")
    result = ingest_resume_text(fake_pdf, ocr=_raise_ocr_unavailable)
    assert result.readable is False
    assert result.spans == []


def test_unreadable_when_text_too_short(tmp_path, monkeypatch):
    """文本太短（<50字）路径：extract_text 返回 readable=False ⇒ 转成人工队列。"""
    import app.parsing.resume_ingest as ingest_mod
    from app.parsing.extract_text import ExtractedText

    def _mock_extract(_path, *, ocr=None):
        return ExtractedText(
            text="x",
            kind="docx",
            effective_chars=1,
            readable=False
        )

    monkeypatch.setattr(ingest_mod, "extract_text", _mock_extract)
    fake_docx = tmp_path / "short.docx"
    fake_docx.write_bytes(b"fake docx")
    result = ingest_resume_text(fake_docx)
    assert result.readable is False
    assert result.spans == []
    assert result.raw_text == "x"
    assert result.kind == "docx"


def test_unsupported_type_raises(tmp_path):
    from app.parsing.extract_text import UnsupportedFileType

    bad = tmp_path / "a.xlsx"
    bad.write_bytes(b"not a real xlsx")
    with pytest.raises(UnsupportedFileType):
        ingest_resume_text(bad)
