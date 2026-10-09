from __future__ import annotations


def test_upload_page_accepts_zip_and_has_source_columns(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/upload")
    assert resp.status_code == 200
    html = resp.text
    assert 'accept=".pdf,.docx,.zip"' in html
    assert 'id="default-source"' in html
    assert 'value="referral"' in html
    assert "包内重复" in html
    assert "不可读" in html
    assert 'fetch("api/resumes/upload"' in html
    assert 'value="live"' not in html
