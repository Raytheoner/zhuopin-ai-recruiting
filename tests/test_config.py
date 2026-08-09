import pytest

from app.config import Settings


def test_rejects_latest_model_alias():
    settings = Settings(llm_model="latest")
    with pytest.raises(ValueError, match="latest"):
        settings.validate_model_version()


def test_rejects_provider_latest_suffix():
    settings = Settings(llm_model="deepseek-chat:latest")
    with pytest.raises(ValueError, match="latest"):
        settings.validate_model_version()


def test_accepts_pinned_version():
    settings = Settings(llm_model="deepseek-chat-241226")
    settings.validate_model_version()  # 不应抛异常


def test_default_root_path_is_hr_recruit_agent():
    settings = Settings()
    assert settings.root_path == "/hr/recruit-agent"


def test_root_path_overridable_via_env(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "/foo/bar")
    settings = Settings()
    assert settings.root_path == "/foo/bar"
