import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 只有这几个取值算"开"。其余一切（拼错、空串、"maybe"、读不到）都算"关"——
# 未知即拦截，与门禁的 fail-closed 同一口径。
_TRUTHY = frozenset({"1", "true", "yes", "on"})


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

    # ── ai-audit-trail-and-outbound-gate（U1 一次加齐，U3/U4 只读不写）──
    # 留痕 JSONL 镜像的落盘路径（design D1：SQLite 为真身，JSONL 为防篡改
    # 镜像）。相对路径按进程工作目录解析，与 db_path 同一约定。
    audit_jsonl_path: str = "data/audit/decisions.jsonl"

    # 候选人外发总开关的**基线**取值，默认关闭。⛔ 业务代码不得直接读这个
    # 字段——Settings 由 get_settings() 缓存，直接读等于"启动时缓存一次"，
    # 违反 spec「总开关 MUST 在每次外发时求值」。唯一合法入口是本模块的
    # is_candidate_outbound_enabled()。
    candidate_outbound_enabled: bool = False

    # 热改开关文件。存在即以它为准，优先级高于环境变量与上面的基线值。
    # 为什么需要它：.51 是 Windows 计划任务拉起的单进程，改环境变量必须重启
    # 整机上的服务，而机器上还跑着另外 7 个服务。出事时要能立刻全拦，改一个
    # 文件就够（Shao Peishen 2026-08-26 拍板：允许热改、不重启生效）。
    candidate_outbound_switch_file: str = "data/candidate_outbound.switch"

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


def _as_switch(raw: str | None) -> bool:
    """把任意原始取值折成布尔，**未知一律折成 False**。"""
    if raw is None:
        return False
    for line in raw.splitlines():
        token = line.strip().lower()
        if token:
            return token in _TRUTHY
    return False  # 空文件 / 全空行：未知即关


def is_candidate_outbound_enabled() -> bool:
    """
    候选人外发总开关，**每次外发时求值**。

    ⛔ 禁止把返回值存成模块级常量、`__init__` 里的属性、或任何单例上的字段。
    ⛔ 调用点必须带括号求值：`is_candidate_outbound_enabled()`。函数对象本身
       恒为真，漏掉括号会让 fail-closed 静默变成 fail-open。

    取值优先级（前者存在即短路）：

    1. 开关文件 `Settings.candidate_outbound_switch_file`——热改通道，改文件
       立刻生效、不重启（Shao Peishen 2026-08-26 拍板）
    2. 环境变量 `CANDIDATE_OUTBOUND_ENABLED`——每次读 os.environ，不走
       get_settings() 的 lru_cache
    3. `Settings.candidate_outbound_enabled` 基线值，默认 False

    任何一层读不出明确的"开"，结果都是 False：未知即拦截。文件读失败
    （权限、目录占位、编码坏）同样返回 False——出错的方向只能是更保守的
    那一侧。
    """
    settings = get_settings()

    switch_file = Path(settings.candidate_outbound_switch_file)
    try:
        if switch_file.exists():
            return _as_switch(switch_file.read_text(encoding="utf-8"))
    except OSError:
        return False
    except UnicodeDecodeError:
        return False

    raw_env = os.environ.get("CANDIDATE_OUTBOUND_ENABLED")
    if raw_env is not None:
        return _as_switch(raw_env)

    return settings.candidate_outbound_enabled
