"""Finding 5（final review）：index.html（主壳页）在这之前完全没有任何入口
指向 M2 U2.5 的三个新页面（resumes/upload、jobs/{id}/resumes、
resumes/{id}/review）——HR 指南第一步"打开'上传简历'页面"、"或者直接从岗位
页面进入"都是假的，页面上根本点不到。

本单元修的是最小闭环：在 index.html 的顶部导航区加一个到 resumes/upload 的
静态入口。范围明确排除按岗位挂深链（design 与 finding 5 都写明这是后续任务）。

index.html 的按钮式视图切换（提需求/岗位列表/转人工队列）明确注释了"⛔ 不用
<a href>"——但那条禁令针对的是"用 <a href> 在三个视图之间切换"这件事本身
（会跟 <base href> 解析纠缠出只在挂 root_path 时才现形的 bug）。这里加的是
真的跳到另一个独立页面（resumes/upload 是另一条路由，不是 index.html 内的
视图），相对路径 + <base href> 解析对普通页面跳转本来就是好使的——
resume_list.html 里 `<a href="resumes/{id}/review">`、upload.html 里
`toListLink.href = jobs/${jobId}/resumes` 已经是这么用的——所以这不是在违反
那条禁令，是同一套已验证过的相对路径跳转机制多用一次。
"""
from __future__ import annotations

from pathlib import Path

INDEX_HTML = Path("app/web/static/index.html").read_text(encoding="utf-8")


def test_index_html_links_to_resume_upload_entry_point():
    assert 'href="resumes/upload"' in INDEX_HTML
    # 相对路径，不带开头的 "/"（部署约束 1：挂任意 root_path 前缀都要能解析）。
    assert 'href="/resumes/upload"' not in INDEX_HTML


def test_resume_upload_link_resolves_under_root_path_prefix():
    """经 _render_index() 渲染后，这个入口链接仍然完好，且与 <base href>
    配合能解析到正确的挂载前缀下的地址（部署约束 1：挂任意子路径都要能用）。"""
    from app.web.server import _render_index

    for root_path, expected_base in [
        ("", '<base href="/">'),
        ("/hr/recruit-agent", '<base href="/hr/recruit-agent/">'),
    ]:
        html = _render_index(root_path)
        assert expected_base in html
        assert 'href="resumes/upload"' in html


def test_index_page_served_via_http_contains_entry_point(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="resumes/upload"' in resp.text
