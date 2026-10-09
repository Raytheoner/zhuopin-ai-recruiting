from __future__ import annotations

import io
import zipfile

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _blank_pdf(width: int = 612, height: int = 792) -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=height)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _client_and_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def test_zip_tags_detected_source_and_default_source(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({
        "boss_张三.pdf": _blank_pdf(),
        "plain.pdf": _blank_pdf(width=595, height=842),
    })
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic", "default_source": "liepin"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    results = resp.json()["results"]
    assert [(r["source"], r["source_origin"]) for r in results] == [
        ("boss", "detected"),
        ("liepin", "default"),
    ]
    rows = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT file_name, source, source_origin FROM resume")
    }
    assert rows == {"boss_张三.pdf": ("boss", "detected"), "plain.pdf": ("liepin", "default")}


def test_zip_falls_back_to_unknown_without_detection_or_default(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({"plain.pdf": _blank_pdf()})
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    result = resp.json()["results"][0]
    assert (result["source"], result["source_origin"]) == ("unknown", None)
    row = conn.execute("SELECT source, source_origin FROM resume").fetchone()
    assert (row[0], row[1]) == ("unknown", None)


def test_zip_accepts_whitelist_and_rejects_non_whitelist(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({"a.pdf": _blank_pdf(), "report.xlsx": b"not xlsx"})
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[1]["status"] == "rejected"
    assert "不支持的类型" in results[1]["reason"]


def test_zip_intra_bundle_duplicate(make_test_client):
    client, conn = _client_and_job(make_test_client)
    pdf = _blank_pdf()
    zdata = _zip({"same1.pdf": pdf, "same2.pdf": pdf})
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[1]["status"] == "intra_bundle_duplicate"
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == 1


def test_zip_live_gate_closed_rejected_with_trace(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({"a.pdf": _blank_pdf()})
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "live"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["status"] == "rejected"
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == 0
    row = conn.execute("SELECT status FROM bundle_ingest_result WHERE job_id = 'j1'").fetchone()
    assert row is not None and row[0] == "rejected_gate"


def test_zip_replay_returns_same_results_and_no_new_resumes(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({"a.pdf": _blank_pdf()})
    first = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]
    second = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]
    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == 1
