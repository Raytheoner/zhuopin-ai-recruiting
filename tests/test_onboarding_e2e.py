"""onboarding-flow U2 e2e（tasks 2.7）：生成清单 → 勾选/豁免 → 经理只读 → 跨部门 403 → 逾期。"""
from datetime import date

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _login(client, conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id))
    conn.commit()
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))


def _seed_hired(conn, application_id, job_id, candidate_name, department, start_date):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES (?, '工程师', ?, 'approved')",
        (job_id, department),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, ?)", (f"cand-{application_id}", candidate_name))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.docx', ?, 'hr-1')",
        (f"res-{application_id}", job_id, f"sha-{application_id}"),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, ?, ?, ?, 'hired', 'hired')",
        (application_id, f"cand-{application_id}", job_id, f"res-{application_id}"),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, job_id, department, start_date),
    )
    conn.commit()


def test_u2_e2e(make_test_client):
    client, conn = make_test_client()
    start_date = date.today().isoformat()  # 入职日=今天 ⇒ 负 offset 的待办条目逾期
    _seed_hired(conn, "app-1", "j1", "张三", "研发部", start_date)

    _login(client, conn, "hr", "hr")
    assert client.post(
        "/api/applications/app-1/onboarding/instantiate", json={"request_id": "r-instantiate"}
    ).status_code == 200

    items = conn.execute(
        "SELECT i.id, i.owner_party FROM onboarding_item i "
        "JOIN onboarding_checklist c ON c.id = i.checklist_id "
        "WHERE c.application_id='app-1' ORDER BY i.rowid"
    ).fetchall()
    hr_items = [iid for iid, party in items if party == "hr"]
    assert len(hr_items) >= 3
    for i, to_status, reason in [
        (hr_items[0], "done", None),
        (hr_items[1], "done", None),
        (hr_items[2], "waived", "体检报告无需提交"),
    ]:
        resp = client.post(
            f"/api/onboarding/items/{i}",
            json={"to_status": to_status, "reason": reason, "request_id": f"r-{i}"},
        )
        assert resp.status_code == 200

    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    detail = client.get("/api/onboarding/department/app-1")
    assert detail.status_code == 200
    body = detail.json()
    assert body["progress_percent"] > 0
    assert body["candidate_name"] == "张三"

    _login(client, conn, "mgr-proc", "dept_manager", department="采购部")
    assert client.get("/api/onboarding/department/app-1").status_code == 403

    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    body = client.get("/api/onboarding/department/app-1").json()
    assert any(it["overdue"] for it in body["items"])

    assert conn.execute("SELECT COUNT(*) FROM onboarding_item_history").fetchone()[0] == 3
