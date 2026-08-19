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

    # 日志配置全部带默认值，零配置即生效（design 决策 6）。.51 的 .env 是
    # 服务器上独立维护、不随代码同步的生产凭据文件；若日志功能依赖 .env 新增
    # 字段，"推代码"与"改 .env"就成了两个必须同时做对的步骤，漏一个就静默地
    # 没有日志——正是这次要根治的失败模式。
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_retention_days: int = 30
    log_max_bytes: int = 50 * 1024 * 1024

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
