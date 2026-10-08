from __future__ import annotations

import io
import zipfile

import pytest

from app.intake.bundle import BundleLimits, BundleTooLarge, unpack_bundle


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _stored_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _corrupt_entry(data: bytes, name: str) -> bytes:
    """把 STORED 条目的数据区第一字节翻转，制造 CRC 损坏条目。"""
    raw = bytearray(data)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    info = zf.getinfo(name)
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw[data_offset] ^= 0xFF
    return bytes(raw)


def test_unpack_one_level_and_whitelist():
    entries = unpack_bundle(_zip({"a.pdf": b"%PDF", "sub/b.docx": b"PK"}))
    assert [(e.filename, e.kind) for e in entries] == [("a.pdf", "ok"), ("b.docx", "ok")]


def test_unsupported_and_deep_and_corrupt_are_marked():
    raw = _stored_zip({
        "report.xlsx": b"not xlsx",
        "bad.pdf": b"X" * 64,
        "a/b/c.pdf": b"%PDF",
        "ok.pdf": b"%PDF",
    })
    raw = _corrupt_entry(raw, "bad.pdf")
    entries = unpack_bundle(raw)
    assert {e.filename: e.kind for e in entries} == {
        "report.xlsx": "unsupported",
        "bad.pdf": "unreadable",
        "c.pdf": "too_deep",
        "ok.pdf": "ok",
    }


def test_over_file_count_rejects_whole_bundle():
    data = _zip({f"{i}.pdf": b"%PDF" for i in range(5)})
    with pytest.raises(BundleTooLarge):
        unpack_bundle(data, BundleLimits(max_files=3, max_total_bytes=10_000_000, max_ratio=100.0))


def test_over_total_size_rejects_whole_bundle():
    data = _zip({"a.pdf": b"%PDF" * 10_000})
    with pytest.raises(BundleTooLarge):
        unpack_bundle(data, BundleLimits(max_files=10, max_total_bytes=10_000, max_ratio=100.0))


def test_over_ratio_rejects_zip_bomb():
    data = _zip({"a.pdf": b"\x00" * 200_000})
    with pytest.raises(BundleTooLarge):
        unpack_bundle(data, BundleLimits(max_files=10, max_total_bytes=10_000_000, max_ratio=5.0))


def test_unpack_bundle_writes_no_files(tmp_path):
    unpack_bundle(_zip({"a.pdf": b"%PDF", "sub/b.docx": b"PK"}))
    assert list(tmp_path.iterdir()) == []
