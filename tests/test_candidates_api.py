from __future__ import annotations

import re

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.source import candidate_source


def _client_and_candidates(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '张 三')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
                 "uploaded_by, source) VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h1', 'alice', 'boss')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
                 "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
                 "uploaded_by, source) VALUES ('r2', 'j1', 'synthetic', 'b.pdf', 'h2', 'alice', 'liepin')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
                 "VALUES ('a2', 'c2', 'j1', 'r2', 'initial')")
    conn.commit()
    return client, conn


def test_candidate_list_shows_source_and_suspected_flag(make_test_client):
    client, conn = _client_and_candidates(make_test_client)
    body = client.get("/api/candidates").json()
    by_id = {c["candidate_id"]: c for c in body["candidates"]}
    assert by_id["c1"]["source"] == "boss"
    assert by_id["c1"]["suspected_duplicate_ids"] == ["c2"]


def test_merge_endpoint_reassigns_and_can_be_unmerged(make_test_client):
    client, conn = _client_and_candidates(make_test_client)
    resp = client.post("/api/candidates/merge", json={
        "primary_id": "c1", "secondary_id": "c2", "reason": "电话确认同一人",
        "request_id": "req-1", "keep_application_per_job": {"j1": "a1"},
    })
    assert resp.status_code == 200
    assert candidate_source(conn, "c1") == "boss"
    assert conn.execute(
        "SELECT merged_into FROM candidate WHERE id = 'c2'"
    ).fetchone()[0] == "c1"

    merge_log_id = resp.json()["merge_log_id"]
    undo = client.post(f"/api/candidates/merge/{merge_log_id}/unmerge")
    assert undo.status_code == 200
    assert conn.execute(
        "SELECT candidate_id FROM application WHERE resume_id = 'r2'"
    ).fetchone()[0] == "c2"


def test_merge_rejects_empty_reason(make_test_client):
    client, _conn = _client_and_candidates(make_test_client)
    resp = client.post("/api/candidates/merge", json={
        "primary_id": "c1", "secondary_id": "c2", "reason": "  ", "request_id": "req-2",
    })
    assert resp.status_code == 422


def test_candidate_pages_render(make_test_client):
    client, _conn = _client_and_candidates(make_test_client)
    assert client.get("/candidates").status_code == 200
    assert client.get("/candidates/c1/merge").status_code == 200


# ── 追加用例（执行期，2026-10-11 Task 7 落地时登记为 additive，非计划偏离）────
#
# 计划 Step 6 的四条用例只覆盖了列表页数据与合并/撤销主路径，本任务**产出**的
# 几个面因此没有判据：① `GET /api/candidates/{id}/merge` 与它的
# `_conflict_jobs` helper（合并页全靠它拿到"同岗位双投递保留哪份"的选项）；
# ② 双投递未指定保留份时的 409 门（spec「同岗位双投递」的拦截面）；
# ③ `/api/candidates*` 的登录墙（Global Constraints 第 17 条明确适用）；
# ④ 两个新页面的 `<base href>` 替换与相对路径（第 15 条）。
# 四条都是 additive pin，⛔ 不改动计划文本里的四条用例。


def test_candidate_apis_require_login(make_test_client):
    """Global Constraints 第 17 条：`/api/candidates*` 未登录一律 401。"""
    client, _conn = make_test_client()
    payload = {
        "primary_id": "c1", "secondary_id": "c2", "reason": "x", "request_id": "req-x",
    }
    assert client.get("/api/candidates").status_code == 401
    assert client.get("/api/candidates/c1/merge").status_code == 401
    assert client.post("/api/candidates/merge", json=payload).status_code == 401
    assert client.post("/api/candidates/merge/ml-1/unmerge").status_code == 401


def test_merge_data_endpoint_reports_suspects_conflicts_and_history(make_test_client):
    """合并页的服务端一半：疑似重复 + 同岗位双投递选项 + 合并历史（含撤销标记）。"""
    client, _conn = _client_and_candidates(make_test_client)

    body = client.get("/api/candidates/c1/merge").json()
    assert body["candidate"] == {
        "candidate_id": "c1", "name": "张三", "source": "boss", "merged_into": None,
    }
    assert [(s["candidate_id"], s["source"]) for s in body["suspected_duplicates"]] == [
        ("c2", "liepin"),
    ]
    # 双投递的两个候选投递 id 都要给到前端，HR 才选得出保留哪份
    assert body["suspected_duplicates"][0]["conflict_jobs"] == [
        {"job_id": "j1", "applications": ["a1", "a2"]},
    ]
    assert body["merge_history"] == []

    resp = client.post("/api/candidates/merge", json={
        "primary_id": "c1", "secondary_id": "c2", "reason": "电话确认同一人",
        "request_id": "req-h1", "keep_application_per_job": {"j1": "a2"},
    })
    assert resp.status_code == 200
    merge_log_id = resp.json()["merge_log_id"]

    history = client.get("/api/candidates/c1/merge").json()["merge_history"]
    assert len(history) == 1
    assert history[0]["merge_log_id"] == merge_log_id
    assert (history[0]["primary_id"], history[0]["secondary_id"]) == ("c1", "c2")
    assert history[0]["unmerged_at"] is None

    assert client.post(f"/api/candidates/merge/{merge_log_id}/unmerge").status_code == 200
    after = client.get("/api/candidates/c1/merge").json()
    # 撤销后 c2 回到独立候选人，仍与 c1 同岗位同姓名 ⇒ 重新以疑似重复出现
    assert [s["candidate_id"] for s in after["suspected_duplicates"]] == ["c2"]
    assert after["merge_history"][0]["unmerged_at"] is not None


def test_merge_conflict_without_keep_choice_is_409_and_writes_nothing(make_test_client):
    """spec「同岗位双投递」：没指定保留哪份就拒绝合并；被拒的效果不留任何痕迹
    （工程铁律 1：业务写与 effect_log 同事务，失败即整笔回滚）。"""
    client, conn = _client_and_candidates(make_test_client)
    resp = client.post("/api/candidates/merge", json={
        "primary_id": "c1", "secondary_id": "c2", "reason": "电话确认同一人",
        "request_id": "req-3",
    })
    assert resp.status_code == 409
    assert "双投递" in resp.json()["detail"]

    assert conn.execute(
        "SELECT merged_into FROM candidate WHERE id = 'c2'"
    ).fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM candidate_merge_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0


def test_new_pages_substitute_base_href_and_keep_every_path_relative():
    """Global Constraints 第 15 条（部署约束 1）：两个新页面的资源与接口调用必须是
    相对路径，靠 `<!--BASE_HREF-->` 占位符换成真实 `<base href>`——挂到
    `/hr/recruit-agent` 下时 4 个路径（候选页 / 合并页 / 列表接口 / 合并接口）都要
    解析到该前缀内，⛔ 不能有写死开头的 "/" 的引用。"""
    from app.web.server import _render_static_page

    for name in ("candidate_list.html", "candidate_merge.html"):
        html = _render_static_page(name, "/hr/recruit-agent").body.decode("utf-8")
        assert '<base href="/hr/recruit-agent/">' in html
        assert "<!--BASE_HREF-->" not in html, f"{name} 的占位符没被替换"
        # <base href> 本身就是从域根开始的那一个刻意例外，扫描前先摘掉它。
        without_base = re.sub(r"<base\b[^>]*>", "", html)
        strays = re.findall(r"""(?:fetch\(\s*|(?:href|src)\s*=\s*)["'`]/[^"'`]*""", without_base)
        assert not strays, f"{name} 里有绝对路径引用 {strays}——挂到前缀下会打到域根"
        assert '"api/candidates' in html
