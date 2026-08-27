"""
AI 决策留痕。design D1：SQLite 为可查询真身，JSONL hash-chain 为防篡改镜像。

⚠️ 审计资产，禁止用作任何模型的训练/微调/调优输入——理由见 events.py 模块 docstring。

**本包不 import `app.config` 与 `app.graph`**：前者会让审计路径在启动时绑死配置、
并让 U3 的注入点不再是唯一一处，后者是反向依赖。路径与连接一律由调用方传入。
"""

from app.audit.events import (
    AI_ANALYSIS,
    BACKFILL,
    EVENT_TYPES,
    OUTBOUND_BLOCKED,
    OUTBOUND_DELIVERED,
    CriterionScore,
    DecisionEvent,
)
from app.audit.sinks import AuditSink, SqliteSink

__all__ = [
    "AI_ANALYSIS",
    "BACKFILL",
    "EVENT_TYPES",
    "OUTBOUND_BLOCKED",
    "OUTBOUND_DELIVERED",
    "CriterionScore",
    "DecisionEvent",
    "AuditSink",
    "SqliteSink",
]
