"""审批链维护接口 GET/PUT /api/jobs/{job_id}/offer-approval-chain 的行为测试。"""
import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


@pytest.fixture
def client(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()
    return client, conn


def test_get_requires_login(make_test_client):
    client, _ = make_test_client()
    resp = client.get("/api/jobs/j1/offer-approval-chain")
    assert resp.status_code == 401


def test_get_returns_default_chain(client):
    client, _ = client
    resp = client.get("/api/jobs/j1/offer-approval-chain")
    assert resp.status_code == 200
    assert resp.json()["chain"] == [
        {"level": 1, "approver_account_ids": [], "default": True}
    ]


def test_get_unknown_job_returns_404(client):
    client, _ = client
    resp = client.get("/api/jobs/no-such-job/offer-approval-chain")
    assert resp.status_code == 404


def test_put_requires_login(make_test_client):
    client, _ = make_test_client()
    resp = client.put(
        "/api/jobs/j1/offer-approval-chain",
        json={"chain": [{"level": 1, "approver_account_ids": ["alice"]}]},
    )
    assert resp.status_code == 401


def test_put_unknown_job_returns_404(client):
    client, _ = client
    resp = client.put(
        "/api/jobs/no-such-job/offer-approval-chain",
        json={"chain": [{"level": 1, "approver_account_ids": ["alice"]}]},
    )
    assert resp.status_code == 404


def test_put_unknown_approver_is_rejected(client):
    client, _ = client
    resp = client.put(
        "/api/jobs/j1/offer-approval-chain",
        json={"chain": [{"level": 1, "approver_account_ids": ["nobody"]}]},
    )
    assert resp.status_code == 422
    assert "nobody" in resp.json()["detail"]


def test_put_non_contiguous_levels_is_rejected(client):
    client, _ = client
    resp = client.put(
        "/api/jobs/j1/offer-approval-chain",
        json={
            "chain": [
                {"level": 1, "approver_account_ids": ["alice"]},
                {"level": 3, "approver_account_ids": ["alice"]},
            ]
        },
    )
    assert resp.status_code == 422


def test_put_then_get_roundtrips(client):
    client, conn = client
    resp = client.put(
        "/api/jobs/j1/offer-approval-chain",
        json={"chain": [{"level": 1, "approver_account_ids": ["alice"]}]},
    )
    assert resp.status_code == 200
    assert resp.json()["chain"][0]["approver_account_ids"] == ["alice"]
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval_chain WHERE job_id = 'j1'"
    ).fetchone()[0] == 1


def test_repeated_identical_put_is_noop(client):
    client, conn = client
    body = {"chain": [{"level": 1, "approver_account_ids": ["alice"]}]}
    client.put("/api/jobs/j1/offer-approval-chain", json=body)
    before = conn.execute(
        "SELECT level, approver_account_ids, updated_by, updated_at "
        "FROM offer_approval_chain WHERE job_id = 'j1'"
    ).fetchall()

    second = client.put("/api/jobs/j1/offer-approval-chain", json=body)

    assert second.status_code == 200
    assert second.json()["chain"][0]["approver_account_ids"] == ["alice"]
    after = conn.execute(
        "SELECT level, approver_account_ids, updated_by, updated_at "
        "FROM offer_approval_chain WHERE job_id = 'j1'"
    ).fetchall()
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval_chain WHERE job_id = 'j1'"
    ).fetchone()[0] == 1
    assert after == before  # 同内容重复 PUT：行数与 updated_at 都不动
