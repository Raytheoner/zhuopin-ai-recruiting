from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_supports_json_schema: bool = False
    db_path: str = "data/demo.db"
    root_path: str = "/hr/recruit-agent"

    def validate_model_version(self) -> None:
        if self.llm_model == "latest" or self.llm_model.endswith(":latest"):
            raise ValueError(
                f"禁止使用 latest 类别名锁定模型版本，收到: {self.llm_model!r}"
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_model_version()
    return settings
