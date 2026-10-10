"""onboarding-flow U2 页面与 JSON 端点（tasks 2.4/2.5/2.6）。"""
from datetime import date, timedelta
from pathlib import Path

from app.llm.gateway import LLMGateway
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.web.server import create_app
from fastapi.testclient import TestClient

STATIC = Path("app/web/static")


def _make(root_path: str, tmp_path):
    """子路径前缀用例的 app 工厂（与 tests/test_letters_page.py 同款）。"""

    def gateway_factory():
        return LLMGateway(
            api_key="k", base_url="https://example.invalid", model="deepseek-chat",
            supports_json_schema=False, client=object(),
        )

    app = create_app(
        db_path=str(tmp_path / "p.db"), gateway_factory=gateway_factory, root_path=root_path
    )
    return TestClient(app)


def _login(client, conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id))
    conn.commit()
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))
    return account_id


def _seed_hired(conn, application_id="app-1", department="研发部", start_date="2026-10-20"):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES ('j1', '嵌入式工程师', ?, 'approved')",
        (department,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', 'hired')",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, department, start_date),
    )
    conn.commit()


def _instantiate(client, application_id="app-1", request_id="r1"):
    return client.post(
        f"/api/applications/{application_id}/onboarding/instantiate",
        json={"request_id": request_id},
    )


# ── Task 5：HR 清单页 ──────────────────────────────────────────────────


def test_checklist_page_html_has_no_upload_control():
    html = (STATIC / "onboarding_checklist.html").read_text(encoding="utf-8")
    assert 'type="file"' not in html
    assert "multipart/form-data" not in html
    assert 'enctype="multipart/form-data"' not in html


def test_checklist_page_extracts_application_id_from_middle_segment():
    """2026-10-11 修正：⛔ 不能取 URL 末段（那是字面量 "onboarding"）。"""
    html = (STATIC / "onboarding_checklist.html").read_text(encoding="utf-8")
    assert "match(/\\/applications\\/([^/]+)\\/onboarding\\/?$/)" in html
    assert 'split("/").filter(Boolean).pop()' not in html


def test_checklist_page_works_under_subpath_prefix(tmp_path):
    """2026-10-11 修正（Spec review 实测缺口）：Global Constraints 部署约束要求
    「任意挂载前缀下页面路由 + 相对路径可用」——补与 letters 页同款的子路径用例。"""
    client = _make("/hr/recruit-agent", tmp_path)
    resp = client.get("/hr/recruit-agent/applications/app-1/onboarding")
    assert resp.status_code == 200
    assert '<base href="/hr/recruit-agent/">' in resp.text


def test_checklist_page_is_served(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    assert client.get("/applications/app-1/onboarding").status_code == 200


def test_instantiate_endpoint_rejects_multipart(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    resp = client.post(
        "/api/applications/app-1/onboarding/instantiate",
        data={"request_id": "r1"},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code in (415, 422)
    assert conn.execute("SELECT COUNT(*) FROM onboarding_checklist WHERE application_id='app-1'").fetchone()[0] == 0


def test_instantiate_requires_hr(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    assert _instantiate(client).status_code == 401
    _login(client, conn, "iv", "interviewer")
    assert _instantiate(client).status_code == 403
    _login(client, conn, "mgr", "dept_manager", department="研发部")
    assert _instantiate(client).status_code == 403


def test_hr_checklist_detail_endpoint(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    resp = _instantiate(client)
    assert resp.status_code == 200
    detail = client.get("/api/applications/app-1/onboarding")
    assert detail.status_code == 200
    body = detail.json()
    assert body["application_id"] == "app-1"
    assert body["progress_percent"] == 0
    assert len(body["items"]) == 6
    assert all(it["status"] == "pending" for it in body["items"])


def test_hr_checklist_detail_requires_hr(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    _instantiate(client)
    _login(client, conn, "iv", "interviewer")
    assert client.get("/api/applications/app-1/onboarding").status_code == 403


def test_hr_item_update_endpoint(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    _instantiate(client)
    item_id = conn.execute(
        "SELECT id FROM onboarding_item WHERE checklist_id="
        "(SELECT id FROM onboarding_checklist WHERE application_id='app-1') AND owner_party='hr' LIMIT 1"
    ).fetchone()[0]
    resp = client.post(
        f"/api/onboarding/items/{item_id}",
        json={"to_status": "done", "reason": None, "request_id": "r-done"},
    )
    assert resp.status_code == 200
    assert conn.execute("SELECT status FROM onboarding_item WHERE id=?", (item_id,)).fetchone()[0] == "done"


# ── Task 6：HR 总览页 ──────────────────────────────────────────────────


def _seed_hired_x(conn, application_id, job_id, candidate_name, department, start_date):
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


def test_hr_overview_lists_open_checklists_sorted_by_start_date(make_test_client):
    client, conn = make_test_client()
    # 2026-10-11 修正（Task 6 执行实测）：⛔ 不能写死日历日期。逾期口径是
    # 「status='pending' 且 入职日 + due_offset_days < 今天」，默认模板里「体检报告」
    # 的 due_offset_days = -5（app/storage/db.py::_ONBOARDING_DEFAULT_TEMPLATE_ITEMS），
    # 于是 plan 原文写死的 2026-10-15 只在计划成稿当天（10-10，due 恰等于今天）不逾期，
    # 10-11 起 overdue_count 恒为 1——断言 `== 0` 会随日历漂移。
    # 改成相对今天取未来两个相距 5 天的入职日：断言（升序 / 姓名 / 进度 0 / 逾期 0）
    # 与 plan 完全一致，且不再依赖运行日期。
    late = (date.today() + timedelta(days=30)).isoformat()
    early = (date.today() + timedelta(days=25)).isoformat()
    _seed_hired_x(conn, "app-late", "j-late", "张三", "研发部", late)
    _seed_hired_x(conn, "app-early", "j-early", "李四", "研发部", early)
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-late", request_id="r-late")
    _instantiate(client, "app-early", request_id="r-early")
    resp = client.get("/api/onboarding")
    assert resp.status_code == 200
    checklists = resp.json()["checklists"]
    assert [c["application_id"] for c in checklists] == ["app-early", "app-late"]
    assert checklists[0]["candidate_name"] == "李四"
    assert checklists[0]["progress_percent"] == 0
    assert checklists[0]["overdue_count"] == 0


def test_hr_overview_requires_hr(make_test_client):
    client, conn = make_test_client()
    assert client.get("/api/onboarding").status_code == 401
    _login(client, conn, "iv", "interviewer")
    assert client.get("/api/onboarding").status_code == 403
    _login(client, conn, "mgr", "dept_manager", department="研发部")
    assert client.get("/api/onboarding").status_code == 403


def test_overview_page_is_served(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    assert client.get("/onboarding").status_code == 200


def test_overview_page_works_under_subpath_prefix(tmp_path):
    """Global Constraints 部署约束 1：任意挂载前缀下页面路由 + 相对路径都可用。

    与 Task 5 的 test_checklist_page_works_under_subpath_prefix 同款判据。
    """
    client = _make("/hr/recruit-agent", tmp_path)
    resp = client.get("/hr/recruit-agent/onboarding")
    assert resp.status_code == 200
    assert '<base href="/hr/recruit-agent/">' in resp.text
    # 页面里是相对 fetch("api/onboarding")——带前缀时应解析到
    # /hr/recruit-agent/api/onboarding（路由可达 ⇒ 401 未登录，而不是 404）。
    assert client.get("/hr/recruit-agent/api/onboarding").status_code == 401


def test_hr_overview_excludes_closed_checklists(make_test_client):
    """Interface 字面是「全部 `open` 清单」——U2 没有把清单置 closed 的代码路径
    （关闭归 U3），所以这条过滤只能直接改库来覆盖。"""
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-a", "j-a", "张三", "研发部", (date.today() + timedelta(days=30)).isoformat())
    _seed_hired_x(conn, "app-b", "j-b", "李四", "研发部", (date.today() + timedelta(days=31)).isoformat())
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-a", request_id="r-a")
    _instantiate(client, "app-b", request_id="r-b")
    conn.execute("UPDATE onboarding_checklist SET status='closed', closed_reason='已入职' WHERE application_id='app-a'")
    conn.commit()
    checklists = client.get("/api/onboarding").json()["checklists"]
    assert [c["application_id"] for c in checklists] == ["app-b"]
