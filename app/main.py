from app.config import get_settings
from app.llm.gateway import LLMGateway
from app.observability.logging_config import setup_logging
from app.web.server import create_app

settings = get_settings()

# 导入期、早于 create_app：uvicorn 在 Config.__init__ 里先 configure_logging()、
# 之后才 load() 导入本模块，所以这里的 dictConfig 一定后手生效、覆盖 uvicorn 的默认配置。
setup_logging(
    log_dir=settings.log_dir,
    level=settings.log_level,
    retention_days=settings.log_retention_days,
    max_bytes=settings.log_max_bytes,
)


def _gateway_factory() -> LLMGateway:
    return LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
    )


app = create_app(
    db_path=settings.db_path,
    gateway_factory=_gateway_factory,
    root_path=settings.root_path,
)
