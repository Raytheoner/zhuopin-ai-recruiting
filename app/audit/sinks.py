"""
留痕的两个 sink。design D1：SQLite 为可查询真身，JSONL hash-chain 为防篡改镜像。

**两者互为独立证据**才是这个组合的意义——SQLite 行可被 UPDATE / DELETE，有写
权限的人可以从被改那行往后全部重算 prev_hash 让链照样通过；append-only 文件的
攻击面小得多，且与库文件是两套介质，同时改两处才能无痕。

⛔ 本模块不 import `app.config` / `app.graph`：路径与连接一律由调用方传入。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Protocol

from app.audit.events import AI_ANALYSIS, EVENT_TYPES, CriterionScore, DecisionEvent


class AuditSink(Protocol):
    """一个留痕落点。write 返回"是否真的落了一行"，read_all 返回原始记录字典。"""

    def write(self, event: DecisionEvent) -> bool: ...

    def read_all(self) -> list[dict[str, Any]]: ...


_ANALYSIS_RUN_COLUMNS = (
    "id",
    "application_id",
    "job_id",
    "configured_model",
    "response_model",
    "system_fingerprint",
    "prompt_version",
    "temperature",
    "input_hash",
    "rubric_snapshot",
    "raw_response",
    "token_usage",
    "latency_ms",
)


def _rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """
    ⚠️ 刻意不设 `conn.row_factory`：conn 是全应用共享的一条连接
    （`app/storage/db.py:get_connection` 的注释），在这里顺手换掉 row_factory
    会让所有按下标取值的既有代码静默改变行为。
    """
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    return None if raw is None else json.loads(raw)


class SqliteSink:
    """
    留痕的真身。写 U1 的 `analysis_run` 与 `criterion_score`。

    ⛔ **不自行 `commit`。** 与 `effect_persist_draft` 同一约定（见
    `app/graph/nodes.py` 那段「不在这里 conn.commit()」的注释）：被
    `idempotent_effect` 装饰的函数体里的写入，必须与装饰器追加的 `effect_log`
    行落在同一个事务里、由装饰器统一提交一次（工程铁律 1）。这里先提交一次，
    进程在"这次提交"与"装饰器提交 effect_log"之间崩溃就会留下「业务写已落盘、
    幂等记录没落」，重放撞主键、重试永久失败。

    连接由调用方传入并**绑定在实例上**：`AuditRecorder.record()` 会断言调用点
    传进来的 conn 与这里绑定的是同一个对象，把"同一连接、同一个 BEGIN"这条不
    变式变成调用点上一句测得到的断言。
    """

    SUPPORTED_EVENT_TYPES = frozenset({AI_ANALYSIS})

    # 可检索的列白名单。列名不能参数化、只能拼进 SQL，所以必须走白名单而不是
    # "看起来像列名就放行"。raw_response 刻意排除：全文检索会全表扫，且没有
    # 审计场景需要它。
    FILTERABLE = frozenset(
        {
            "id",
            "application_id",
            "job_id",
            "configured_model",
            "response_model",
            "system_fingerprint",
            "prompt_version",
            "input_hash",
        }
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── 写 ──────────────────────────────────────────────────────────────

    def write(self, event: DecisionEvent) -> bool:
        if event.event_type not in EVENT_TYPES:
            raise ValueError(f"未登记的事件类型: {event.event_type!r}")
        if event.event_type not in self.SUPPORTED_EVENT_TYPES:
            # 外发事件的 SQLite 真身是 pending_approval（U5 的 queue.py 写），
            # 补录事件只存在于镜像链上。这里 ⛔ 不替它们凭空造一张表——返回
            # False 让调用方与对账（U6 6.4）都看得见"这里没有它的真身"。
            return False

        try:
            self.conn.execute(
                f"INSERT INTO analysis_run ({', '.join(_ANALYSIS_RUN_COLUMNS)}, created_at) "
                f"VALUES ({', '.join('?' * len(_ANALYSIS_RUN_COLUMNS))}, "
                f"COALESCE(?, datetime('now')))",
                (
                    event.id,
                    event.application_id,
                    event.job_id,
                    event.configured_model,
                    event.response_model,
                    event.system_fingerprint,
                    event.prompt_version,
                    event.temperature,
                    event.input_hash,
                    self._pack_rubric(event),
                    event.raw_response,
                    _dumps(event.token_usage),
                    event.latency_ms,
                    event.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # ⚠️ 只短路"这条 run 已经写过"这一种情形。把 except 写宽一格
            # （`except sqlite3.IntegrityError: return False`），evidence_ref
            # 的 CHECK 失败就会被当成"已写入"静默放过——铁律 4 当场从"数据库
            # 强制"退回"静默放过"，而所有正常用例照样全绿。
            if not _is_analysis_run_pk_conflict(exc):
                raise
            return False

        for score in event.scores:
            self.conn.execute(
                "INSERT INTO criterion_score "
                "(id, analysis_run_id, criterion_key, score, evidence_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
                (
                    score.id or f"{event.id}:{score.criterion_key}",
                    event.id,
                    # ⛔ 这里**不做** criterion_key 白名单校验：白名单必须集中在
                    # 一处 Python 定义里（U3 的 3.4），散成两处会出现"一处放行
                    # 一处拒绝"的分叉，而分叉的那一侧就是红线的缺口。
                    score.criterion_key,
                    score.score,
                    score.evidence_ref,
                    event.created_at,
                ),
            )
        return True

    @staticmethod
    def _pack_rubric(event: DecisionEvent) -> str | None:
        """
        rubric_version 与 rubric_snapshot 合并落进一列（偏离登记 1）：U1 的
        analysis_run 没有 version 列，而 U2 ⛔ 不改 U1 的表。spec 要的是"所用
        rubric 的完整快照"，版本是快照的属性，合并落盘完整满足且 round-trip 无损。
        """
        if event.rubric_version is None and event.rubric_snapshot is None:
            return None
        return _dumps({"version": event.rubric_version, "snapshot": event.rubric_snapshot})

    # ── 读 ──────────────────────────────────────────────────────────────

    def read_all(self) -> list[dict[str, Any]]:
        return self.query()

    def query(
        self,
        *,
        created_from: str | None = None,
        created_to: str | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """
        spec「留痕可查询」：按业务关联标识、时间区间、模型标识等维度检索。
        """
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in filters.items():
            if key not in self.FILTERABLE:
                raise ValueError(
                    f"不可检索的字段: {key!r}；可用: {sorted(self.FILTERABLE)}"
                )
            clauses.append(f"{key} = ?")
            params.append(value)
        if created_from is not None:
            clauses.append("created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            clauses.append("created_at <= ?")
            params.append(created_to)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        runs = _rows_as_dicts(
            self.conn.execute(
                f"SELECT * FROM analysis_run{where} ORDER BY created_at, id", params
            )
        )
        if not runs:
            return []

        scores = _rows_as_dicts(
            self.conn.execute("SELECT * FROM criterion_score ORDER BY criterion_key")
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in scores:
            grouped.setdefault(row["analysis_run_id"], []).append(row)

        for run in runs:
            packed = _loads(run.pop("rubric_snapshot"))
            run["rubric_version"] = (packed or {}).get("version")
            run["rubric_snapshot"] = (packed or {}).get("snapshot")
            run["token_usage"] = _loads(run["token_usage"])
            run["scores"] = grouped.get(run["id"], [])
        return runs


def _is_analysis_run_pk_conflict(exc: sqlite3.IntegrityError) -> bool:
    message = str(exc)
    return "UNIQUE constraint failed" in message and "analysis_run.id" in message
