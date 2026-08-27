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
import threading
from typing import Any
from uuid import uuid4

from app.audit.events import AI_ANALYSIS, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.storage.db import sqlite_utc_now

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


def _split_context(
    audit_context: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """
    拆成「已登记的键」与「未登记的键名」两半。

    ⚠️ **这里只拆不抛。** 抛在留痕写完之后——调用点传了个错键时，那次 API 调用
    **已经付过钱、已经发生了**，此刻直接抛会让它一条记录都不剩，正是模块
    docstring 里说 spec 禁止的那个方向。先按已登记的键把能记的记下来（并在
    `error` 里写明哪些键被丢了），再抛。

    ⛔ 丢掉的键名只记名字，**不记值**：未登记的键正是"可能藏着简历原文"的那些，
    把值写进 error 等于从另一个口子放它进留痕。
    """
    if audit_context is None:
        return {}, []
    unknown = sorted(set(audit_context) - ALLOWED_CONTEXT_KEYS)
    known = {k: v for k, v in audit_context.items() if k in ALLOWED_CONTEXT_KEYS}
    return known, unknown


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
        # ⚠️ 一条连接只有**一个**事务。FastAPI 把同步路由分派进工作线程池
        # （每请求一个线程，见 app/storage/db.py:238-242），而本适配器是模块级
        # 单例、被所有线程共用——不加锁时两个并发请求会互相踩：A 的 rollback()
        # 会把 B 已执行但未提交的 INSERT 一起抹掉，A 的 commit() 会把 B 写了一半
        # 的事务提前落盘。实测 20 线程并发：SQLite 只剩 12 行、JSONL 17 行、
        # 3 个 sqlite3.InterfaceError（2026-08-28 review round 1 复现）。
        #
        # 用锁而不是每线程一条连接：审计写入很小，串行化的代价可忽略，而
        # "一条连接、一个事务管理者"这个说法只有在串行时才成立。JsonlChainSink
        # 本来就是按路径共享进程内锁的同一形态。
        self._write_lock = threading.Lock()

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
        context, rejected_keys = _split_context(audit_context)

        notes: list[str] = []
        if raw_response is None:
            notes.append("raw_response 为 None（模型返回空响应体），已折成空串写入")
        if rejected_keys:
            notes.append(f"audit_context 未登记的键已被丢弃: {rejected_keys}")

        event = DecisionEvent(
            id=_event_id(context, input_hash, attempt),
            event_type=AI_ANALYSIS,
            # ⚠️ 显式打时刻，⛔ 不要留 None 让数据库的 datetime('now') 去填：那样
            # 只有 SQLite 那一侧有时刻，JSONL 镜像里是 "created_at": null。镜像
            # 是**防篡改的那一份独立证据**（SQLite 行可被 UPDATE），一份说不出
            # "这次调用发生在什么时候"的证据基本不成立，而 reconcile() 只比 id、
            # 发现不了。显式打还有个副作用是对的：两侧记的是同一个时刻。
            # 格式必须与 datetime('now') 完全一致，所以用 sqlite_utc_now()。
            created_at=sqlite_utc_now(),
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
            # 模型返回空响应体时原样传 None 会撞 NOT NULL。折成空串，并把这个
            # 事实记进 error 让镜像留痕。
            #
            # ⚠️ 这只保住**留痕**：钩子在 json.loads 之前触发，所以这一行照样落库。
            # 那次调用本身仍会失败——`app/llm/gateway.py` 紧接着 json.loads(None)
            # 抛 TypeError，而它不在 `except (JSONDecodeError, ValidationError)`
            # 里，会直接穿透出去、连重试都不消耗（2026-08-28 review round 1 实测）。
            # ⛔ 不要把这句注释读成"空响应不会打挂调用"——它挡住的是"留痕本身
            # 因为 NOT NULL 而失败"，登记为 docs/tech-debt.md TD-4。
            raw_response=raw_response or "",
            token_usage=token_usage,
            latency_ms=latency_ms,
            error="；".join(notes) or None,
        )

        # ⚠️ `stored is False` 只有一种含义：这条 id 之前已经写过，被主键短路了
        # （`SqliteSink` 的另一种 False 是"非 ai_analysis 事件在这个 sink 里没有
        # 真身"，而本适配器只造 AI_ANALYSIS 事件，走不到那一支）。这与
        # 2026-08-28 对残留 B 的拍板一致：调用点自己知道在写什么类型，
        # ⛔ 不从 False 反推原因。
        stored = self._write(event)
        if stored:
            # 第二段：镜像。⛔ 失败不抛——允许的偏差只有单向「SQLite 有、JSONL
            # 缺行」，把它升级成故障就等于把一个被明确允许的偏差当成事故。缺行由
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
        else:
            # 已经写过 = 镜像里也已经有它那一行。再 append 一条同 id 的行，链上
            # 就多出一条真身里没有对应新增的记录，而 `reconcile()` 比的是**集合**
            # 差集，看不见这种重复（2026-08-28 review round 1 实测：SQLite 1 行、
            # JSONL 2 行，reconcile().ok 仍为 True）。
            #
            # ⚠️ WARNING 不是 DEBUG：`log_level` 默认 INFO（app/config.py:28），
            # 打 DEBUG 等于"丢掉一次真实付费调用的留痕，且不留任何可观测痕迹"。
            # 已知会撞上它的一处：`app/agents/jd_agent.py:69` 的 generate_jd()
            # 循环最多两次，两次 prompt **逐字相同**、gateway 的 attempt 都是 1，
            # 所以一旦有人给它接上 audit_context，第二次（也就是"上一次生成了
            # 歧视性表述所以重试"的那次）会被确定性 id 直接去重掉。接 audit_context
            # 的那个单元必须先解决这个，判据就是这条日志有没有出现。
            logger.warning(
                "留痕已存在，跳过镜像 append（id=%s）。同一 id 被写第二次通常意味着"
                "调用方在用逐字相同的 prompt 重试，而确定性 id 分辨不出来。",
                event.id,
            )

        # ⚠️ 抛在最后：调用点传了未登记的键时，那次 API 调用**已经付过钱、已经
        # 发生了**。先把能记的记完再抛，否则就成了"一次真实调用一条记录都没有"
        # ——正是本模块 docstring 里说 spec 禁止的那个方向。
        if rejected_keys:
            raise UnknownAuditContextKey(
                f"audit_context 出现未登记的键: {rejected_keys}；已登记: "
                f"{sorted(ALLOWED_CONTEXT_KEYS)}。⛔ 新增键必须过 review——这个通道"
                "是留痕里唯一能被调用方塞进任意内容的地方。"
                f"（本次调用已按已登记的键留痕，id={event.id}）"
            )

    def _write(self, event: DecisionEvent) -> bool:
        """
        第一段：真身。**失败即抛**——spec：留痕写入失败时该次 AI 结果视为不可用，
        其评分 MUST NOT 进入下游排序。异常穿透出网关正是这条的落地形态。

        返回"这次是不是真的落了一行"：`False` 表示这条 id 之前已经写过、被主键
        短路了，调用方据此跳过镜像 append。

        失败时先回滚：半截写入悬在隐式事务里会被下一次提交顺手带进库
        （`app/storage/idempotency.py:42-47` 描述的正是这个失败模式）。

        ⚠️ 整段在锁内：一条连接只有一个事务，并发线程共用它会互相踩，理由与实测
        数字见 `__init__` 里 `_write_lock` 那段注释。
        """
        with self._write_lock:
            try:
                stored = self._recorder.record(self._conn, event)
                self._conn.commit()
                return stored
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
