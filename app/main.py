from app.audit import AuditRecorder, JsonlChainSink, RecorderAuditHook, SqliteSink
from app.config import get_settings
from app.llm.gateway import LLMGateway
from app.observability.logging_config import setup_logging
from app.storage.db import get_connection
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


# ── 留痕装配（ai-audit-trail-and-outbound-gate，交付单元 U3）─────────────
# **注入点只有这一处**，回滚 = 把下面 _gateway_factory 里的 audit_hook 参数删掉
# 一行（design.md 迁移计划第 3 步）。
#
# ⚠️ 审计走**专属连接**，不复用 create_app() 内那条全应用共享的连接：钩子在
# LLMGateway 内部触发，那里没有 conn；复用共享连接会让留痕行被 idempotent_effect
# 的 rollback 一起撤销（app/storage/idempotency.py:41-68）。完整理由见
# app/audit/hook.py 的模块 docstring。
#
# ⚠️ 在模块级构造一次，⛔ 不要挪进 _gateway_factory()：那个工厂被调用两处
# （app/web/server.py:66 启动时、:278 每次请求），挪进去等于每个 HTTP 请求泄漏
# 一条 SQLite 连接。守护见 tests/test_main_wiring.py。
#
# 建表由 create_app() 里的 init_schema() 负责（app/web/server.py:55-56）；本连接
# 只写不建表，首次写入发生在第一个 HTTP 请求，那时表一定已经在了。
_audit_conn = get_connection(settings.db_path)
_audit_recorder = AuditRecorder(
    SqliteSink(_audit_conn),
    JsonlChainSink(settings.audit_jsonl_path),
)
_audit_hook = RecorderAuditHook(_audit_recorder, _audit_conn)


def _gateway_factory() -> LLMGateway:
    return LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
        audit_hook=_audit_hook,
    )


app = create_app(
    db_path=settings.db_path,
    gateway_factory=_gateway_factory,
    root_path=settings.root_path,
)
