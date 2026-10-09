"""onboarding-flow U1 模板维护接口（tasks 1.6）：HR 角色、版本递增、同内容幂等。"""
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _account_with_role(conn, username: str, role: str) -> str:
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role = ? WHERE id = ?", (role, account_id))
    conn.commit()
    return account_id


def _login(client, conn, username: str, role: str) -> str:
    account_id = _account_with_role(conn, username, role)
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))
    return account_id


_SEED_ITEMS = [
    {"name": "签劳动合同", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "交入职材料", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "体检报告", "owner_party": "hr", "due_offset_days": -5, "required": True},
    {"name": "配置设备", "owner_party": "it", "due_offset_days": -2, "required": True},
    {"name": "开通账号", "owner_party": "it", "due_offset_days": -1, "required": True},
    {"name": "指定带教人", "owner_party": "dept", "due_offset_days": 0, "required": False},
]


def test_get_template_requires_hr_role(make_test_client):
    client, conn = make_test_client()
    assert client.get("/api/onboarding-templates/department/default").status_code == 401

    _login(client, conn, "interviewer", "interviewer")
    assert client.get("/api/onboarding-templates/department/default").status_code == 403

    _login(client, conn, "manager", "dept_manager")
    assert client.get("/api/onboarding-templates/department/default").status_code == 403

    _login(client, conn, "hr", "hr")
    resp = client.get("/api/onboarding-templates/department/default")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope_type"] == "department"
    assert body["scope_id"] == "default"
    assert body["version"] == 1
    assert len(body["items"]) == 6


def test_get_template_404_for_unknown_scope(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    assert client.get("/api/onboarding-templates/department/nonexistent").status_code == 404


def test_get_template_rejects_invalid_scope_type(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    assert client.get("/api/onboarding-templates/team/x").status_code == 422


def test_put_template_creates_new_version(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": [_SEED_ITEMS[0]]},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["unchanged"] is False
    count = conn.execute(
        "SELECT COUNT(*) FROM onboarding_template "
        "WHERE scope_type='department' AND scope_id='default'"
    ).fetchone()[0]
    assert count == 2


def test_put_template_same_content_no_new_version(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": _SEED_ITEMS},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 1
    assert resp.json()["unchanged"] is True
    count = conn.execute(
        "SELECT COUNT(*) FROM onboarding_template "
        "WHERE scope_type='department' AND scope_id='default'"
    ).fetchone()[0]
    assert count == 1


def test_put_template_rejects_invalid_owner_party(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": [{"name": "x", "owner_party": "bogus", "due_offset_days": 0, "required": True}]},
    )
    assert resp.status_code == 422


def test_put_template_rejects_blank_name(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": [{"name": "  ", "owner_party": "hr", "due_offset_days": 0, "required": True}]},
    )
    assert resp.status_code == 422


def test_put_template_requires_hr_role(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "manager", "dept_manager")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": []},
    )
    assert resp.status_code == 403
