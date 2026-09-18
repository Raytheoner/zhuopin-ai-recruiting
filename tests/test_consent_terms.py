"""app/storage/consent_terms.py：同意条款文件加载与版本号解析
（voice-structured-interview U3 tasks 4.5）。"""

import pytest

from app.storage.consent_terms import (
    ConsentTerm,
    ConsentTermNotFoundError,
    KNOWN_CONSENT_KINDS,
    latest_consent_version,
    load_consent_term,
)


def test_known_consent_kinds():
    assert KNOWN_CONSENT_KINDS == ("ai_interview", "identity_check")


@pytest.mark.parametrize("kind", KNOWN_CONSENT_KINDS)
def test_latest_consent_version_is_v1(kind):
    assert latest_consent_version(kind) == "v1"


@pytest.mark.parametrize("kind", KNOWN_CONSENT_KINDS)
def test_load_consent_term_returns_nonempty_text(kind):
    version = latest_consent_version(kind)
    term = load_consent_term(kind, version)
    assert isinstance(term, ConsentTerm)
    assert term.kind == kind
    assert term.version == version
    assert len(term.text) > 0


def test_load_consent_term_unknown_kind_raises():
    with pytest.raises(ConsentTermNotFoundError):
        load_consent_term("not_a_kind", "v1")


def test_load_consent_term_unknown_version_raises():
    with pytest.raises(ConsentTermNotFoundError):
        load_consent_term("ai_interview", "v999")


def test_latest_consent_version_picks_max_version_number(tmp_path, monkeypatch):
    """升版场景：目录下同时有 v1 与 v2，latest 取数值最大的那个（不是字符串
    排序——避免 'v10' 字符串序小于 'v2' 的坑）。"""
    import app.storage.consent_terms as module

    consent_dir = tmp_path / "consent"
    consent_dir.mkdir()
    (consent_dir / "ai_interview-v1.md").write_text("v1 text", encoding="utf-8")
    (consent_dir / "ai_interview-v10.md").write_text("v10 text", encoding="utf-8")
    (consent_dir / "ai_interview-v2.md").write_text("v2 text", encoding="utf-8")
    monkeypatch.setattr(module, "CONSENT_DIR", consent_dir)

    assert module.latest_consent_version("ai_interview") == "v10"
