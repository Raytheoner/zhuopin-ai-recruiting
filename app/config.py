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


class _SwitchFileBroken(Exception):
    """内部哨兵：开关文件路径结构性损坏（不是"文件不存在"），必须直接关闭，
    不允许继续降级去看环境变量。"""


def _read_switch_file(switch_file: Path) -> str | None:
    """读开关文件的内容。

    返回值：
    - 文件内容（`str`）—— 正常读到
    - `None` —— 文件**确实不存在**（`FileNotFoundError` / ENOENT），这是
      "没配"，调用方应当降级去看环境变量

    其余任何读取失败——路径结构损坏（ENOTDIR：某个上级路径段本身是个普通
    文件）、符号链接自环（ELOOP）、权限、目录占位（IsADirectoryError）、
    编码坏（UnicodeDecodeError）、路径本身带 NUL 字节（`ValueError:
    embedded null byte`，round 2 发现：Windows 上 PowerShell 用 UTF-16
    写 `.env`，被当 UTF-8 解出来的字符串会带 NUL，dotenv 原样传下来，
    `Path()`/`read_text()` 一碰 NUL 就抛 `ValueError`，且它不是
    `OSError` 也不是 `UnicodeDecodeError` 的子类，round 1 的 except 元组
    接不住）——一律抛 `_SwitchFileBroken`。这些是"配置坏了"，不是
    "没配"，必须直接判定为关，**不能**降级去看环境变量：一个结构损坏的
    开关文件路径不该让 `.51` 的 `.env` 说开就开。

    `ValueError` 和 `UnicodeDecodeError` 两个都显式列出：后者本来就是
    前者的子类、单列 `ValueError` 已经够用，但保留 `UnicodeDecodeError`
    是为了让"编码坏"这个具体成因在代码里可读，不必翻文档才知道这条路
    在防什么。

    用 `read_text()`（内部就是 `open()`）而不是 `Path.exists()` 判断是否
    存在——`exists()` 对 ENOTDIR/ELOOP 这类结构性错误同样返回 `False`，
    没法把它们和"真的没有这个文件"区分开。
    """
    try:
        return switch_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _SwitchFileBroken from exc


def is_candidate_outbound_enabled() -> bool:
    """
    候选人外发总开关，**每次外发时求值**。

    ⛔ 禁止把返回值存成模块级常量、`__init__` 里的属性、或任何单例上的字段。
    ⛔ 调用点必须带括号求值：`is_candidate_outbound_enabled()`。函数对象本身
       恒为真，漏掉括号会让 fail-closed 静默变成 fail-open。
    ⛔ 本函数**不允许抛出任何异常**。任何一步出错——包括 `Settings` 本身
       构造失败（比如 `.env` 里某个字段类型不对，pydantic 抛
       `ValidationError`；或 `validate_model_version()` 抛的
       `ValueError`，跟外发开关毫无关系）——结果都必须是 `False`。配置
       崩了 = 全拦，这比"让异常经调用方某个 `except Exception` 冒泡、最终
       被更上游的兜底变成放行"更保守，也是「未知即拦截」在异常这个维度上
       唯一自洽的读法。

    round 1 靠在内部逐层挂 `try/except` 枚举具体异常类型（`OSError`、
    `UnicodeDecodeError`）来兑现"不抛异常"，round 2 发现 NUL 字节路径
    抛的是二者都不是的 `ValueError`，照样逃了出去——枚举挂一漏万，枚举
    本身就是会失手的方法。所以这里额外加一层**结构性**兜底：整个函数体
    委托给 `_evaluate_candidate_outbound_switch()`，本函数只做一件事——
    不管里面抛出什么类型（哪怕是内部枚举完全没预料到的新类型），一律
    在这一层截停，返回 `False`。之后任何人往内部再加一段没包线的新
    异常来源，也不需要专门再补一轮修复。
    """
    try:
        return _evaluate_candidate_outbound_switch()
    except Exception:
        return False


def _evaluate_candidate_outbound_switch() -> bool:
    """
    实际取值逻辑，可能抛出异常——调用方 `is_candidate_outbound_enabled()`
    统一兜底捕获。内部这几个 `try/except` 不是为了"不抛异常"（外层已经
    兜底），而是为了在各自的分支上选对下一步該去哪一层取值，语义更清楚。

    取值优先级（前者存在即短路）：

    1. 开关文件 `Settings.candidate_outbound_switch_file`——热改通道，改文件
       立刻生效、不重启（Shao Peishen 2026-08-26 拍板）
    2. 环境变量 `CANDIDATE_OUTBOUND_ENABLED`——每次读 os.environ，不走
       get_settings() 的 lru_cache
    3. `Settings.candidate_outbound_enabled` 基线值，默认 False

    任何一层读不出明确的"开"，结果都是 False：未知即拦截。文件读失败
    （权限、目录占位、路径结构损坏、编码坏、路径带 NUL 字节）同样返回
    False——出错的方向只能是更保守的那一侧。
    """
    try:
        settings = get_settings()
    except Exception:
        # Settings 本身都构不出来（字段类型不对、validate_model_version()
        # 抛出等）——配置已经坏了，直接关，连开关文件都不看。
        return False

    switch_file = Path(settings.candidate_outbound_switch_file)
    try:
        raw = _read_switch_file(switch_file)
    except _SwitchFileBroken:
        return False
    if raw is not None:
        return _as_switch(raw)

    try:
        raw_env = os.environ.get("CANDIDATE_OUTBOUND_ENABLED")
    except Exception:
        return False
    if raw_env is not None:
        return _as_switch(raw_env)

    return settings.candidate_outbound_enabled
