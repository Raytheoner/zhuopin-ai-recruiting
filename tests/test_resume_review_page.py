from __future__ import annotations


def test_resume_review_page_served_with_relative_fetches(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/r1/review")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert 'fetch(`api/resumes/${resumeId}/text`)' in html
    assert 'fetch(`api/resumes/${resumeId}/parsed`)' in html
    assert 'fields/${fieldName}/review`' in html
