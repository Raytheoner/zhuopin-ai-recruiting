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
    path = _write_docx(tmp_path / "a.docx", ["张三", "工作年限：5年", "技能：Python"])
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


def test_unsupported_type_raises(tmp_path):
    from app.parsing.extract_text import UnsupportedFileType

    bad = tmp_path / "a.xlsx"
    bad.write_bytes(b"not a real xlsx")
    with pytest.raises(UnsupportedFileType):
        ingest_resume_text(bad)
