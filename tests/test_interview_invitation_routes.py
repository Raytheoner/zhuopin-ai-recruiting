import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label
from app.storage.hr_account import upsert_account


@pytest.fixture
def api(make_test_client):
    client, conn = make_test_client()
    upsert_account(conn, username="hr-test", password="secret")
    client.post("/api/auth/login", json={"username": "hr-test", "password": "secret"})
    conn.execute("INSERT INTO job (id, title) VALUES ('j', '工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c', '测试')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) VALUES ('r', 'j', 'synthetic', 'a.pdf', 'sha', 'hr-test')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) VALUES ('a', 'c', 'j', 'r', 'interview')")
    for n in (1, 2):
        conn.execute("INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) VALUES (?, 'a', ?, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')", (f"s{n}", n))
    conn.commit()
    return client, conn


def test_prefixes_require_login(make_test_client):
    client, _ = make_test_client()
    for path in ("/api/invitation-templates", "/api/interview-slots/s1/invitation"):
        assert client.get(path).status_code == 401


def test_template_actor_and_validation(api):
    client, _ = api
    assert client.get("/api/invitation-templates").status_code == 200
    response = client.put("/api/invitation-templates", json={"body": "你好 {candidate_name}", "updated_by": "spoof"})
    assert response.status_code == 200
    assert response.json()["updated_by"] == "hr-test"
    assert client.put("/api/invitation-templates", json={"body": "{score}"}).status_code == 422


def test_draft_lifecycle(api, monkeypatch):
    client, conn = api
    counter = iter(range(2))
    def compute(*args, **kwargs):
        return InvitationDraft(body=enforce_ai_label("邀请面试", generated_at="2026-10-10T00:00:00+00:00"), run_id=f"run-{next(counter)}", response_model="actual-v1", prompt_version="invite-v1"), "v1"
    monkeypatch.setattr("app.web.server.compute_invitation_draft_for_slot", compute, raising=False)
    url = "/api/interview-slots/s1/invitation"
    first = client.post(url + "/generate", json={})
    assert first.status_code == 201
    draft_id = first.json()["created_draft_id"]
    second = client.post(url + "/generate", json={})
    assert [d["version"] for d in second.json()["drafts"]] == [1, 2]
    for action, body in (("edit", {"body": "改写"}), ("mark-human-written", {})):
        wrong = client.post(f"/api/interview-slots/s2/invitation/drafts/{draft_id}/{action}", json=body)
        assert wrong.status_code == 404
    edited = client.post(f"{url}/drafts/{draft_id}/edit", json={"body": "改写"})
    assert AI_LABEL_PREFIX in edited.json()["body"]
    assert client.post(f"{url}/drafts/{draft_id}/edit", json={"body": "改写"}).json() == edited.json()
    marked = client.post(f"{url}/drafts/{draft_id}/mark-human-written")
    assert AI_LABEL_PREFIX not in marked.json()["body"]
    assert client.post(f"{url}/drafts/{draft_id}/mark-human-written").json() == marked.json()
    assert conn.execute("SELECT authorship_marked_by FROM interview_invitation_draft WHERE id=?", (draft_id,)).fetchone()[0] == "hr-test"
    assert client.post(url + "/send").json()["delivery_mode"] == "blocked"


def test_outcome_replay_and_round_scope(api):
    client, conn = api
    for slot in ("s1", "s2"):
        url = f"/api/interview-slots/{slot}/invitation/outcome"
        response = client.post(url, json={"status": "declined", "reason": "时间冲突", "actor": "spoof"})
        assert response.status_code == 200
        assert response.json()["outcome"]["actor"] == "hr-test"
        assert client.post(url, json={"status": "declined", "reason": "时间冲突"}).json()["outcome"] == response.json()["outcome"]
    assert conn.execute("SELECT COUNT(*) FROM invitation_outcome_log").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == 0
    assert conn.execute("SELECT current_stage_id FROM application WHERE id='a'").fetchone()[0] == "interview"


def test_interviewer_denied(api):
    client, conn = api
    conn.execute("UPDATE hr_account SET role='interviewer' WHERE username='hr-test'")
    conn.commit()
    for method, path, body in (
        ("get", "/api/invitation-templates", None),
        ("put", "/api/invitation-templates", {"body": "邀请"}),
        ("get", "/api/interview-slots/s1/invitation", None),
        ("post", "/api/interview-slots/s1/invitation/generate", {}),
        ("post", "/api/interview-slots/s1/invitation/drafts/d/edit", {"body": "编辑"}),
        ("post", "/api/interview-slots/s1/invitation/drafts/d/mark-human-written", {}),
        ("post", "/api/interview-slots/s1/invitation/outcome", {"status": "confirmed"}),
        ("post", "/api/interview-slots/s1/invitation/send", {}),
    ):
        assert client.request(method, path, json=body).status_code == 403


def test_missing_cancelled_and_invalid_requests(api):
    client, conn = api
    base = "/api/interview-slots/missing/invitation"
    assert client.get(base).status_code == 404
    assert client.post(base + "/generate", json={}).status_code == 404
    assert client.post(base + "/send").status_code == 404
    assert client.post(base + "/outcome", json={"status": "confirmed"}).status_code == 404
    url = "/api/interview-slots/s1/invitation"
    assert client.post(url + "/send").status_code == 409
    assert client.post(url + "/outcome", json={"status": "invalid"}).status_code == 422
    conn.execute("UPDATE interview_slot SET status='cancelled', cancel_reason='测试' WHERE id='s1'")
    conn.commit()
    assert client.post(url + "/generate", json={}).status_code == 409
    assert client.post(url + "/outcome", json={"status": "confirmed"}).status_code == 409


def test_existing_outcome_with_other_key_is_success(api):
    from app.graph.invitation_nodes import effect_backfill_invitation_outcome
    client, conn = api
    original = effect_backfill_invitation_outcome(
        conn, thread_id="a", business_key="other-key", slot_id="s1",
        status="declined", actor="first-hr", reason="时间冲突", channel=None,
    )
    response = client.post("/api/interview-slots/s1/invitation/outcome", json={
        "status": "declined", "reason": "另一个理由",
    })
    assert response.status_code == 200
    assert response.json()["outcome"] == original
    assert conn.execute("SELECT COUNT(*) FROM invitation_outcome_log").fetchone()[0] == 1


def test_generate_replay_returns_persisted_draft_id(api, monkeypatch):
    client, conn = api
    draft = InvitationDraft(
        body=enforce_ai_label("邀请面试", generated_at="2026-10-10T00:00:00+00:00"),
        run_id="same-run", response_model="actual-v1", prompt_version="invite-v1",
    )
    monkeypatch.setattr(
        "app.web.server.compute_invitation_draft_for_slot",
        lambda *args, **kwargs: (draft, "v1"),
    )
    url = "/api/interview-slots/s1/invitation/generate"
    first = client.post(url, json={})
    replay = client.post(url, json={})
    assert first.status_code == replay.status_code == 201
    assert replay.json()["created_draft_id"] == first.json()["created_draft_id"]
    assert len(replay.json()["drafts"]) == 1
