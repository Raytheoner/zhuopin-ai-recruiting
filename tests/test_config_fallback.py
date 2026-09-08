"""WBS 2.3 的配置侧：备用供应商四个字段全部有默认值，不配 = 无备用。"""

import pytest

from app.config import Settings


def test_fallback_defaults_to_nothing_configured():
    """⛔ 默认值绝不指向任何真实供应商：没改过 .env 的机器不得在主家抖动时
    把数据发给一个谁也没批准过的端点。"""
    settings = Settings()
    assert settings.llm_fallback_api_key == ""
    assert settings.llm_fallback_base_url == ""
    assert settings.llm_fallback_model == ""
    assert settings.llm_fallback_supports_json_schema is False


@pytest.mark.parametrize("alias", ["latest", "deepseek-chat:latest", "deepseek-chat-latest"])
def test_fallback_model_rejects_latest_aliases(alias):
    """工程铁律 5 对备用家一视同仁。"""
    with pytest.raises(ValueError, match="latest"):
        Settings(llm_fallback_model=alias).validate_model_version()


def test_a_pinned_fallback_model_passes():
    Settings(llm_fallback_model="qwen-max-2025-01-25").validate_model_version()


def test_empty_fallback_model_is_not_a_version_violation():
    Settings(llm_fallback_model="").validate_model_version()
