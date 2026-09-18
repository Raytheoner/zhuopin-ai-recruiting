from __future__ import annotations


def test_resume_list_page_served_with_ai_disclaimer(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/jobs/j1/resumes")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert "AI" in html
    assert "仅供参考" in html
    assert 'fetch(`api/resumes/by-job/' in html
    assert "score" not in html.lower()
    assert "排名" not in html
