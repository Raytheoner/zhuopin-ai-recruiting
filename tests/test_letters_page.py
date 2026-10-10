"""文书页静态路由与子路径前缀（U2 tasks 2.7）。"""
from __future__ import annotations

import re
from pathlib import Path

from app.llm.gateway import LLMGateway
from app.web.server import create_app
from fastapi.testclient import TestClient


def _make(root_path: str, tmp_path):
    db_path = str(tmp_path / "p.db")

    def gateway_factory():
        return LLMGateway(
            api_key="k", base_url="https://example.invalid", model="deepseek-chat",
            supports_json_schema=False, client=object(),
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path=root_path)
    return TestClient(app)


def test_letters_page_served_with_relative_paths(make_test_client):
    client, _ = make_test_client()
    resp = client.get("/applications/app1/letters")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert "AI" in html
    assert "api/applications/" in html


def test_letters_page_works_under_subpath_prefix(tmp_path):
    client = _make("/hr/recruit-agent", tmp_path)
    resp = client.get("/hr/recruit-agent/applications/app1/letters")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/hr/recruit-agent/">' in html


def test_letters_page_has_no_absolute_path_strings():
    html = Path("app/web/static/letters.html").read_text(encoding="utf-8")
    without_comments = "\n".join(line.split("//", 1)[0] for line in html.splitlines())
    literals = [
        content
        for _, content in re.findall(r"""(["'`])((?:\\.|(?!\1).)*)\1""", without_comments)
    ]
    absolute = [lit for lit in literals if lit.split("${", 1)[0].startswith("/")]
    assert not absolute, f"发现硬编码的绝对路径字符串字面量: {absolute!r}"


def test_letters_page_edit_and_mark_human_reopen_current_letter():
    """2026-10-11 修正（Spec review 实测）：⛔ 不能把响应体当 letter_id 传给
    openLetter（`api/letters/[object Object]`，成功路径必报假错）。"""
    html = Path("app/web/static/letters.html").read_text(encoding="utf-8")
    assert ".then(openLetter)" not in html
    assert html.count("openLetter(currentId)") >= 2
