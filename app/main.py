from app.config import get_settings
from app.llm.gateway import LLMGateway
from app.web.server import create_app

settings = get_settings()


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
