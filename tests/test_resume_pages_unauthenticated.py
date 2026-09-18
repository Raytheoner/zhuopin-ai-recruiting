"""Finding 3（final review）：三个新页面对"未登录/会话过期"的处理要一致。

resume_list.html 的 401 → 跳 login 是参照行为。resume_review.html 的
loadParsed() 之前完全没查状态码（只有 loadRawText() 查了），未登录时会把
401 响应体的 undefined 字段当正常数据渲染成"空字段、无报错"的假象。
upload.html 提交上传遇到 401 时只给一句不带跳转的中文提示，没有登录入口。

这三处都是纯前端 JS，本仓库没有浏览器/Node 执行环境的测试基础设施（见
test_resume_review_evidence_offset.py 顶部说明），所以：
1. 用字符串断言锁住"修复后的代码路径确实出现在响应体里"——沿用
   test_upload_page.py / test_resume_review_page.py 已有的"断言字面量在页面
   里"写法；
2. 顺带用真实 HTTP 请求锁住这些页面的 JS 所依赖的后端契约没变：
   `/api/resumes/*` 未登录必 401（app/middleware/auth.py
   PROTECTED_PATH_PREFIXES），`/api/jobs` 未登录不拦（upload.html 依赖这个
   来渲染岗位下拉框）——JS 侧的分支覆盖是否正确，前提是这个契约成立。
"""
from __future__ import annotations


def test_resume_review_page_checks_401_in_both_loaders(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/r1/review")
    assert resp.status_code == 200
    html = resp.text
    # 共享的跳转判断函数只定义一次……
    assert html.count("function redirectIfUnauthorized(resp)") == 1
    # ……但 loadRawText 和 loadParsed 两处都要用到它，一个都不能少。
    assert html.count("if (redirectIfUnauthorized(resp)) return") >= 2
    # init() 在 loadRawText 已经跳转的情况下不得继续跑 loadParsed()。
    assert "const rawOk = await loadRawText();" in html
    assert "if (!rawOk) return;" in html


def test_upload_page_redirects_to_login_on_401_submit(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/upload")
    assert resp.status_code == 200
    html = resp.text
    assert "function redirectIfUnauthorized(resp)" in html
    assert 'window.location.href = "login"' in html
    assert "if (redirectIfUnauthorized(resp)) return;" in html
    # 旧的"上传失败，请确认已登录"提示仍保留在 !resp.ok 分支（非 401 的失败
    # 仍要有话可说），但 401 必须先被上面那条分支拦下并跳转。
    assert "上传失败，请确认已登录" in html


def test_api_resumes_endpoints_401_without_session(make_test_client):
    """resume_review.html 两个 loader 都打 /api/resumes/*，锁住这两个端点
    未登录确实 401——这是上面字符串断言成立的前提契约。"""
    client, _conn = make_test_client()
    assert client.get("/api/resumes/r1/text").status_code == 401
    assert client.get("/api/resumes/r1/parsed").status_code == 401


def test_api_resumes_upload_401_without_session(make_test_client):
    client, _conn = make_test_client()
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("a.docx", b"not-a-real-docx", "application/octet-stream"))],
    )
    assert resp.status_code == 401


def test_api_jobs_stays_unauthenticated_for_upload_dropdown(make_test_client):
    """upload.html 依赖 api/jobs 不鉴权来渲染岗位下拉框——这是本单元的既有
    架构决定（app/middleware/auth.py 里 /api/jobs* 明确不在保护前缀里），不是
    这次修复动的东西，这里只是锁住这条契约没有被顺手改掉。"""
    client, _conn = make_test_client()
    resp = client.get("/api/jobs")
    assert resp.status_code != 401
