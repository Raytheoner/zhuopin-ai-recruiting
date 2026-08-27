"""
留痕的统一入口。业务代码只跟 `AuditRecorder` 打交道，不关心后端。

⚠️ **两段式 API，这是本模块最重要的形状约束**（`delivery-units.md` §2.U2 / §3.4）：

    第一段  record(conn, event)  → 只写 SQLite，进调用方的事务，⛔ 不 commit
    第二段  mirror(event)        → 只 append JSONL，必须在事务**已提交之后**调用

⛔ **不提供把两段打包成一次调用的方法。** 打包会让 append 发生在事务提交之前，
事务一旦回滚就留下「JSONL 有、SQLite 无」——镜像里出现一条数据库里查不到的
记录，design D1 明令这是更糟的偏差方向（审计查不到记录）。允许的偏差只有单向：
「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据），由 `reconcile()` 检出、由
`backfill()` 在链尾补录。

⛔ **禁止在任何 `effect_*` 函数体内调用 `mirror()`。** 落地形态：`record()` 进
`effect_*` 函数体，`mirror()` 由调用点在 `effect_*` **返回之后**触发——此时
`idempotent_effect` 已 `commit`。**这不需要改 `idempotent_effect` 装饰器**，
因为 append 发生在装饰器之外。守护见 `tests/test_audit_recorder.py`。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from app.audit.events import AI_ANALYSIS, BACKFILL, DecisionEvent
from app.audit.sinks import AuditSink, ChainVerification


class TransactionOwnershipError(RuntimeError):
    """
    调用点传进来的连接与真身 sink 绑定的不是同一个对象。

    工程铁律 1：幂等记录与业务写必须同一连接、同一个 `BEGIN`，且该连接上不得
    存在第二个事务管理者。实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 outbox
    （幂等记录已落、业务写没落），见
    `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
    """


@dataclass(frozen=True)
class Reconciliation:
    """
    跨介质对账结果（design D1 的检出手段）。U6 的 6.4 在这之上写断言。

    `unexplained_missing` 才是该报警的那一集：链尾已有 `type=backfill` 补录事件
    的缺行是**已知且已登记**的，把它一直算成违例会让这条断言长期红着，红久了
    就没人看了。
    """

    missing_in_mirror: frozenset[str] = frozenset()
    missing_in_store: frozenset[str] = frozenset()
    backfilled: frozenset[str] = frozenset()

    @property
    def unexplained_missing(self) -> frozenset[str]:
        return frozenset(self.missing_in_mirror - self.backfilled)

    @property
    def ok(self) -> bool:
        return not self.unexplained_missing and not self.missing_in_store


class AuditRecorder:
    """统一入口。形状约束见模块 docstring。"""

    def __init__(self, store: AuditSink, mirror_sink: AuditSink) -> None:
        self._store = store
        self._mirror = mirror_sink

    # ── 第一段：真身 ────────────────────────────────────────────────────

    def record(self, conn: sqlite3.Connection, event: DecisionEvent) -> bool:
        """
        写 SQLite。**进调用方的事务，不 commit。**

        `conn` 在功能上是冗余的（真身 sink 自己就绑着一条连接）——它存在的唯一
        理由是把工程铁律 1 的"同一连接"从一句注释变成调用点上一句测得到的断言。
        冗余在这里是刻意的成本。

        失败即抛，⛔ 调用方不得吞：spec「留痕写入失败」要求该次 AI 结果视为不
        可用，其评分 MUST NOT 进入下游排序，且失败本身可被观测。
        """
        bound = getattr(self._store, "conn", None)
        if bound is not None and conn is not bound:
            raise TransactionOwnershipError(
                "record() 收到的连接与真身 sink 绑定的不是同一个对象；"
                "幂等记录与业务写必须落在同一连接的同一个 BEGIN 里（工程铁律 1）"
            )
        return self._store.write(event)

    # ── 第二段：镜像 ────────────────────────────────────────────────────

    def mirror(self, event: DecisionEvent) -> bool:
        """
        append JSONL。**必须在事务已提交之后调用**（`effect_*` 返回之后）。

        ⛔ 不要为了"少一次调用"把它塞回 `record()` 里，理由见模块 docstring。
        """
        return self._mirror.write(event)

    # ── 查询与自检 ──────────────────────────────────────────────────────

    def query_by(self, **filters: Any) -> list[dict[str, Any]]:
        """spec「留痕可查询」：按业务关联标识、时间区间、模型标识等维度检索。"""
        return self._store.query(**filters)

    def verify_integrity(self) -> ChainVerification:
        """
        链自校验。⚠️ 它只证明"链自身没被改"，**证明不了"该留的痕都留了"**——
        后者是 `reconcile()`。两者不可互相替代（`delivery-units.md` §3.4）。
        """
        return self._mirror.verify_chain()

    # ── 对账与补录（design D1 的检出与补齐手段）──────────────────────────

    def reconcile(self) -> Reconciliation:
        """
        跨介质对账：按 `analysis_run.id` 比对两侧记录集合，差集非空即报告。

        ⚠️ 与 `verify_integrity()` 是两条不同的断言，**不可互相替代**：
        `verify_chain()` 只证明"链自身没被改"，`reconcile()` 才回答"该留的痕都
        留了没有"（`delivery-units.md` §3.4 / §2.U6）。

        只比对 `ai_analysis` 类事件：外发事件的 SQLite 真身是 `pending_approval`
        （U5 的 queue.py 写），不在 `analysis_run` 里，拿它对账会把每一条外发留痕
        都算成"真身缺失"。
        """
        store_ids = {record["id"] for record in self._store.read_all()}

        mirror_ids: set[str] = set()
        backfilled: set[str] = set()
        for record in self._mirror.read_all():
            event_type = record.get("event_type")
            if event_type == AI_ANALYSIS:
                mirror_ids.add(record["id"])
            elif event_type == BACKFILL and record.get("backfill_of"):
                backfilled.add(record["backfill_of"])

        return Reconciliation(
            missing_in_mirror=frozenset(store_ids - mirror_ids),
            missing_in_store=frozenset(mirror_ids - store_ids),
            backfilled=frozenset(backfilled),
        )

    def backfill(self, missing_id: str, *, reason: str) -> bool:
        """
        补齐一条镜像缺行：在**链尾** append 一条 `type=backfill` 事件。

        ⛔ 不插回原位——插回必然断链（design D1）。留下"缺过、什么时候补的"这条
        显式记录，比伪造一条看起来正常的历史行诚实。

        补录**只写镜像**：往 `analysis_run` 里插一行"补录"会污染真身的语义。
        """
        return self._mirror.write(
            DecisionEvent(
                id=f"backfill:{missing_id}",
                event_type=BACKFILL,
                backfill_of=missing_id,
                error=reason,
            )
        )
