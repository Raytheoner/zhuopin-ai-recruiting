"""app/storage/live_interview_gate.py：真实候选人开闸（design D14，tasks 4.9）。
默认关闭、每次求值、AND 合规验收 #2 签认文件存在。"""

import pytest

from app.config import get_settings
from app.storage.live_interview_gate import is_live_interview_enabled


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("LIVE_INTERVIEW_ENABLED", raising=False)
    yield
    get_settings.cache_clear()


def test_default_is_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert is_live_interview_enabled() is False


def test_env_true_without_signoff_file_still_closed(tmp_path, monkeypatch):
    """开关开了，但合规验收 #2 签认文件不存在 ⇒ 仍然关闭（AND 语义）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_INTERVIEW_ENABLED", "1")
    assert is_live_interview_enabled() is False


def test_env_true_with_signoff_file_present_opens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_INTERVIEW_ENABLED", "1")
    signoff_dir = tmp_path / "docs" / "compliance"
    signoff_dir.mkdir(parents=True)
    (signoff_dir / "m3-interview-signoff.md").write_text("签认", encoding="utf-8")
    assert is_live_interview_enabled() is True


def test_never_raises_on_broken_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_INTERVIEW_ENABLED", "not-a-real-bool-but-fine-since-truthy-check")
    # 非标准取值不在 _TRUTHY 集合里 ⇒ 按关闭处理，不抛异常
    assert is_live_interview_enabled() is False
