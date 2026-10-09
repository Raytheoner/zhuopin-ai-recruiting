from __future__ import annotations

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _client_and_resume(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice')"
    )
    conn.commit()
    return client, conn, "r1"


def test_correct_source_writes_log_and_updates(make_test_client):
    client, conn, resume_id = _client_and_resume(make_test_client)
    resp = client.post(f"/api/resumes/{resume_id}/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert resp.json() == {"resume_id": resume_id, "source": "referral", "already_corrected": False}
    row = conn.execute("SELECT from_source, to_source FROM source_correction_log WHERE resume_id = ?", (resume_id,)).fetchone()
    assert row is not None and row[0] is None and row[1] == "referral"
    source, origin = conn.execute("SELECT source, source_origin FROM resume WHERE id = ?", (resume_id,)).fetchone()
    assert source == "referral" and origin == "corrected"


def test_same_value_is_idempotent(make_test_client):
    client, conn, resume_id = _client_and_resume(make_test_client)
    client.post(f"/api/resumes/{resume_id}/source", json={"source": "referral"})
    resp = client.post(f"/api/resumes/{resume_id}/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert resp.json()["already_corrected"] is True
    count = conn.execute("SELECT COUNT(*) FROM source_correction_log WHERE resume_id = ?", (resume_id,)).fetchone()[0]
    assert count == 1


def test_invalid_source_422_and_unknown_resume_404(make_test_client):
    client, conn, resume_id = _client_and_resume(make_test_client)
    assert client.post(f"/api/resumes/{resume_id}/source", json={"source": "unknown"}).status_code == 422
    assert client.post("/api/resumes/nope/source", json={"source": "referral"}).status_code == 404
