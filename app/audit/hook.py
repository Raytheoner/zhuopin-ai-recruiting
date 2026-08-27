"""
`AuditHook`（`app/llm/gateway.py` 的 Protocol）到 `AuditRecorder` 的适配层。

**这是整条留痕链路上唯一知道招聘业务语义的地方**：网关只管把扁平参数交出来、
不解释 `audit_context`（design D6）；`AuditRecorder` 只管两段式分发。中间这层
负责把两者对上。

⚠️ **本适配器持有一条专属的 SQLite 连接并自己提交**，不复用全应用共享的那条。
理由（三条，缺一条这个决定就不成立）：

1. 钩子的触发点在 `LLMGateway` 内部，那里**根本没有 conn**。两个真实调用点的
   形状还不一样：`compute_intake_turn` 完全不在事务里（`app/graph/nodes.py:17`
   的 docstring：纯函数，只调用 LLM 与做数据转换，不写库），
   `effect_generate_and_persist_jd` 在事务里但 `conn` 没传进网关
   （`app/graph/nodes.py:237`）。
2. 复用共享连接会踩 `app/storage/idempotency.py:41-68`：被装饰函数抛异常时装饰器
   `conn.rollback()`，**留痕行被一起回滚**——而那次 LLM 调用是真的发生过、真的
   花了钱。spec「留痕写入失败 MUST NOT 被静默忽略」在这条路径上会变成"留痕被
   静默撤销"。
3. 工程铁律 1 禁止的是"**同一条连接上有第二个事务管理者**"。专属连接上的管理者
   只有本适配器一个。本仓库已在跑两条连接写同一个库文件的形态，
   `journal_mode=WAL` 与 `busy_timeout=5000` 就是为此设成连接默认值的
   （`app/storage/db.py:245-253`）。

**语义后果是对的那一侧**：业务事务回滚时留痕仍在——那次 AI 调用真的发生过，
留痕记录它是事实陈述。反过来（调用发生了却没有留痕）才是 spec 禁止的方向。
（Shao Peishen 2026-08-28 追认。）

⛔ 本模块不 import `app.config` / `app.graph`：连接与路径一律由 `app/main.py` 传入。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any
from uuid import uuid4

from app.audit.events import AI_ANALYSIS, DecisionEvent
from app.audit.recorder import AuditRecorder

logger = logging.getLogger(__name__)


class UnknownAuditContextKey(ValueError):
    """`audit_context` 里出现了未登记的键。"""


# audit_context 允许承载的键。⛔ 白名单，不是黑名单——未登记即拒绝。
# 这个通道是唯一"调用方能往留痕里塞东西"的入口，放开它等于放开
# spec「MUST NOT 在留痕记录中存储简历原文」的唯一入口。
ALLOWED_CONTEXT_KEYS = frozenset(
    {
        "thread_id",  # 会话/岗位标识，与幂等键同源
        "node",  # 图节点名，与幂等键同源
        "application_id",
        "job_id",
        "rubric_version",
        "rubric_snapshot",
    }
)


def _validated_context(audit_context: dict[str, Any] | None) -> dict[str, Any]:
    if audit_context is None:
        return {}
    unknown = sorted(set(audit_context) - ALLOWED_CONTEXT_KEYS)
    if unknown:
        raise UnknownAuditContextKey(
            f"audit_context 出现未登记的键: {unknown}；已登记: "
            f"{sorted(ALLOWED_CONTEXT_KEYS)}。⛔ 新增键必须过 review——这个通道"
            "是留痕里唯一能被调用方塞进任意内容的地方。"
        )
    return dict(audit_context)


def _event_id(context: dict[str, Any], input_hash: str, attempt: int) -> str:
    """
    有图上下文时用确定性 id（tasks 2.2 的 `{thread_id}:{node}:{input_hash}`
    加上 `:{attempt}`）；没有图上下文时带随机后缀。

    为什么不无条件用确定性 id：确定性的用途是 **LangGraph 重放去重**。没有图
    上下文的调用（jd_agent、compare_models.py）不在重放路径上，而两次内容相同
    的调用是两次真实的、各花了一次钱的 API 调用——确定性 id 会让第二次撞主键、
    被 `SqliteSink` 短路成 `False`，留痕静默少一条。
    """
    thread_id = context.get("thread_id")
    node = context.get("node")
    if thread_id and node:
        return f"{thread_id}:{node}:{input_hash}:{attempt}"
    return f"llm:{input_hash}:{attempt}:{uuid4().hex}"


class RecorderAuditHook:
    """生产用的审计钩子。装配点只有一处：`app/main.py:_gateway_factory()`。"""

    def __init__(self, recorder: AuditRecorder, conn: sqlite3.Connection) -> None:
        self._recorder = recorder
        self._conn = conn

    def record(
        self,
        *,
        model: str,
        response_model: str | None,
        system_fingerprint: str | None,
        prompt_version: str,
        temperature: float,
        input_hash: str,
        raw_response: str | None,
        token_usage: dict[str, Any],
        latency_ms: float,
        attempt: int,
        audit_context: dict[str, Any] | None = None,
    ) -> None:
        context = _validated_context(audit_context)

        event = DecisionEvent(
            id=_event_id(context, input_hash, attempt),
            event_type=AI_ANALYSIS,
            thread_id=context.get("thread_id"),
            application_id=context.get("application_id"),
            job_id=context.get("job_id"),
            configured_model=model,
            response_model=response_model,
            system_fingerprint=system_fingerprint,
            prompt_version=prompt_version,
            temperature=temperature,
            input_hash=input_hash,
            rubric_version=context.get("rubric_version"),
            rubric_snapshot=context.get("rubric_snapshot"),
            # analysis_run.raw_response 是 NOT NULL（app/storage/db.py:105）。
            # 模型返回空响应体时原样传 None 会撞 NOT NULL，把"模型没说话"升级成
            # "系统故障"。折成空串，并把这个事实记进 error 让镜像留痕。
            raw_response=raw_response or "",
            token_usage=token_usage,
            latency_ms=latency_ms,
            error=(
                None
                if raw_response is not None
                else "raw_response 为 None（模型返回空响应体），已折成空串写入"
            ),
        )

        self._write(event)

        # 第二段：镜像。⛔ 失败不抛——允许的偏差只有单向「SQLite 有、JSONL 缺行」，
        # 把它升级成故障就等于把一个被明确允许的偏差当成事故。缺行由
        # AuditRecorder.reconcile() 检出、backfill() 在链尾补录。
        try:
            self._recorder.mirror(event)
        except Exception:
            logger.error(
                "留痕镜像 append 失败，真身已落库（id=%s）。这是被允许的单向偏差，"
                "由对账检出、链尾补录；⛔ 不要改成抛异常。",
                event.id,
                exc_info=True,
            )

    def _write(self, event: DecisionEvent) -> None:
        """
        第一段：真身。**失败即抛**——spec：留痕写入失败时该次 AI 结果视为不可用，
        其评分 MUST NOT 进入下游排序。异常穿透出网关正是这条的落地形态。

        失败时先回滚：半截写入悬在隐式事务里会被下一次提交顺手带进库
        （`app/storage/idempotency.py:42-47` 描述的正是这个失败模式）。
        """
        try:
            self._recorder.record(self._conn, event)
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                logger.error(
                    "留痕写入失败后的 rollback 也失败了（id=%s）；半截写入可能被"
                    "下一次提交带进库",
                    event.id,
                    exc_info=True,
                )
            raise
