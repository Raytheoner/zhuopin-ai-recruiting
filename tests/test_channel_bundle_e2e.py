from __future__ import annotations

import io
import zipfile

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _stored_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _corrupt_entry(data: bytes, name: str) -> bytes:
    raw = bytearray(data)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    info = zf.getinfo(name)
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw[data_offset] ^= 0xFF
    return bytes(raw)


def _bundle() -> bytes:
    pdf = _blank_pdf()
    raw = _stored_zip({
        "report.xlsx": b"not xlsx",
        "bad.pdf": b"X" * 64,
        "same1.pdf": pdf,
        "same2.pdf": pdf,
        "a/b/resume.pdf": pdf,
    })
    return _corrupt_entry(raw, "bad.pdf")


def _client_and_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def test_bundle_upload_and_replay(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _bundle()

    first = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]

    by_name = {r["file_name"]: r for r in first}
    assert by_name["report.xlsx"]["status"] == "rejected"
    assert "不支持的类型" in by_name["report.xlsx"]["reason"]
    assert by_name["bad.pdf"]["status"] == "rejected"
    assert "无法读取" in by_name["bad.pdf"]["reason"]
    assert by_name["resume.pdf"]["status"] == "rejected"
    assert "目录层级过深" in by_name["resume.pdf"]["reason"]
    assert by_name["same1.pdf"]["status"] == "accepted"
    assert by_name["same2.pdf"]["status"] == "intra_bundle_duplicate"

    resume_count_before = conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0]
    assert resume_count_before == 1

    second = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]

    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == resume_count_before
