"""候选人侧窄屏（`09-UI-Design.md` §8／§10 判据 4）与共用层提示条变体的静态守卫。

⛔ 本仓库 venv 没有 playwright、也没有浏览器二进制 ⇒ 这里**做不了** 375px 的
真机/浏览器实测（无横向滚动这件事在浏览器里才看得出来）。本文件锁的是三条
**静态判据**：① 候选人两页声明了 viewport；② 三个面试页与 app.css 里没有
>375px 的固定像素宽度（固定宽度是窄屏撑破视口最常见的来源）；③ app.css 有
`@media (max-width: 480px)` 且块内给 `.btn` 全宽。

它们能挡住"顺手加回一个 720px 固定宽度 / 删掉媒体查询"这类回退，但**不能**
替代真机验收——如果哪天接上无头浏览器，这条静态守卫应当保留并叠加一条真实
的 375px 横向滚动断言，而不是把本文件删掉。
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path("app/web/static")

# 候选人两页：由候选人在企微内置浏览器（手机）上打开。
CANDIDATE_PAGES = (
    "interview_consent.html",
    "interview_invite_issue.html",
)

# 三个面试页：窄屏固定宽度扫描的范围（含 HR 侧的题目复核页，与 §二.2 同口径）。
INTERVIEW_PAGES = (
    "interview_consent.html",
    "interview_invite_issue.html",
    "interview_prep_review.html",
)

# <meta name="viewport" content="width=device-width, ..."> —— 只要有
# width=device-width 就足以让移动端按设备宽度渲染（initial-scale 是加分项，不强制）。
_VIEWPORT_RE = re.compile(
    r'<meta\s+name="viewport"\s+content="[^"]*width=device-width',
)

# width:Npx / min-width:Npx —— 只认像素固定宽度。百分比、em、vh 不参与这条判据：
# `width: 100%` 与 `max-width: 70vh` 都不会撑破视口。
_FIXED_WIDTH_RE = re.compile(r"(?<![\w-])(?:min-)?width\s*:\s*(\d+)px")

_NARROW_MEDIA_RE = re.compile(r"@media\s*\(max-width:\s*480px\)\s*\{(.*)\}", re.DOTALL)


def test_candidate_pages_declare_viewport():
    """① 候选人两页必须声明 viewport，否则手机上会按桌面宽度缩放。"""
    for name in CANDIDATE_PAGES:
        html = (STATIC / name).read_text(encoding="utf-8")
        assert _VIEWPORT_RE.search(html), (
            f"{name} 缺少 <meta name=\"viewport\" content=\"width=device-width...\">——"
            "候选人在手机上打开会按桌面宽度缩放，按钮/文字挤成一团。"
        )


def test_interview_pages_and_app_css_have_no_fixed_width_above_375():
    """② 三个面试页的内联样式与 app.css 里都不允许出现 >375px 的固定像素宽度。"""
    for name in INTERVIEW_PAGES:
        css = (STATIC / name).read_text(encoding="utf-8")
        oversized = [int(v) for v in _FIXED_WIDTH_RE.findall(css) if int(v) > 375]
        assert not oversized, (
            f"{name} 含 >375px 的固定宽度 {oversized}——这是窄屏横向滚动的常见来源；"
            "改用百分比/最大宽度，或收进媒体查询。"
        )

    app_css = (STATIC / "app.css").read_text(encoding="utf-8")
    oversized_css = [int(v) for v in _FIXED_WIDTH_RE.findall(app_css) if int(v) > 375]
    assert not oversized_css, (
        f"app.css 含 >375px 的固定宽度 {oversized_css}——共用层一旦写死宽像素，"
        "所有引用它的页面在窄屏下都会被撑破。"
    )


def test_app_css_has_narrow_media_query_with_full_width_buttons():
    """③ app.css 必须有 `@media (max-width: 480px)` 块，且块内给 `.btn` 全宽。"""
    app_css = (STATIC / "app.css").read_text(encoding="utf-8")
    match = _NARROW_MEDIA_RE.search(app_css)
    assert match, "app.css 缺少 @media (max-width: 480px) 块（候选人窄屏的兜底样式）。"

    block = match.group(1)
    assert re.search(r"\.btn\s*\{[^}]*width:\s*100%", block, re.DOTALL), (
        "窄屏媒体查询块内没有给 .btn 全宽（width: 100%）——§8 要求候选人侧"
        "按钮在窄屏下全宽可点。"
    )
