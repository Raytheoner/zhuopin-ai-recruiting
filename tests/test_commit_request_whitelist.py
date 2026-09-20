"""`scripts/commit_request.py` 白名单纳入 `.claude/skills/<名>/SKILL.md`（0920L）。

0920R 改完 `.claude/skills/lane-watch-relay/SKILL.md` 提交被拒（不在白名单内），改动滞留
主工作区未提交。本测试锁住放宽后的边界：SKILL.md 放行，`.claude/**` 其余一律仍拒
（尤其 `settings.json`／`hooks/*`／`handoff/**` 三者不能被误放开）。
"""

from __future__ import annotations

from scripts.commit_request import validate_path


def test_docs_still_whitelisted() -> None:
    assert validate_path("docs/x.md") is None


def test_skill_md_whitelisted() -> None:
    assert validate_path(".claude/skills/lane-watch-relay/SKILL.md") is None


def test_settings_json_still_rejected() -> None:
    assert validate_path(".claude/settings.json") is not None


def test_skill_non_skill_md_file_rejected() -> None:
    assert validate_path(".claude/skills/x/hooks.py") is not None
