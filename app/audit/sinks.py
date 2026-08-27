"""
留痕的两个 sink。design D1：SQLite 为可查询真身，JSONL hash-chain 为防篡改镜像。

**两者互为独立证据**才是这个组合的意义——SQLite 行可被 UPDATE / DELETE，有写
权限的人可以从被改那行往后全部重算 prev_hash 让链照样通过；append-only 文件的
攻击面小得多，且与库文件是两套介质，同时改两处才能无痕。

⛔ 本模块不 import `app.config` / `app.graph`：路径与连接一律由调用方传入。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.audit.events import AI_ANALYSIS, EVENT_TYPES, DecisionEvent


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

            for score in event.scores:
                self.conn.execute(
                    "INSERT INTO criterion_score "
                    "(id, analysis_run_id, criterion_key, score, evidence_ref, created_at) "
                    "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
                    (
                        score.id or f"{event.id}:{score.criterion_key}",
                        event.id,
                        # ⛔ 这里**不做** criterion_key 白名单校验：白名单必须集中
                        # 在一处 Python 定义里（U3 的 3.4），散成两处会出现"一处
                        # 放行一处拒绝"的分叉，而分叉的那一侧就是红线的缺口。
                        score.criterion_key,
                        score.score,
                        score.evidence_ref,
                        event.created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            # ⚠️ 只短路"这条 run 已经写过"这一种情形——判据精确到
            # analysis_run.id 的 UNIQUE 冲突。criterion_score 的 INSERT 现在
            # 也落在这个 try 里，是刻意的：evidence_ref 的 CHECK 失败发生在
            # criterion_score 一侧，narrowing 逻辑必须同时罩住两条语句，否则
            # "只短路 analysis_run 冲突"这句话名不副实。把 except 写宽一格
            # （`except sqlite3.IntegrityError: return False`），evidence_ref
            # 的 CHECK 失败就会被当成"已写入"静默放过——铁律 4 当场从"数据库
            # 强制"退回"静默放过"，而所有正常用例照样全绿。
            if not _is_analysis_run_pk_conflict(exc):
                raise
            return False

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


# 第一行的 prev_hash 哨兵。它是**写入侧的约定**，不是可校验的主张——第 1 行
# 没有前驱，拿什么和它比？verify_chain() 因此不校验第 1 行的取值，只校验
# 「第 2 行起必须有这个字段」（spec：仅第 1 条记录可豁免，向前兼容既有文件）。
GENESIS_PREV_HASH = "0" * 64


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    total: int
    broken_at: int | None = None
    error: str | None = None
    tail_hash: str | None = None


class JsonlChainSink:
    """
    留痕的防篡改镜像：append-only JSONL，每行嵌上一行**落盘字节**的 SHA-256。

    ⚠️ 只做**进程内**互斥（design Non-Goals：不做跨进程写锁）。当前部署形态是
    单个 Windows 计划任务拉起的单进程，假设成立；多进程部署会断链。M2 迁
    Postgres 时由数据库承担并发写，JSONL 若仍保留需改为单写入者或按进程分文件。
    技术债登记是 U7 的 7.6。

    锁与游标都是**类级、按解析后的绝对路径共享**：两个指向同一文件的实例必须
    用同一把锁、同一个游标，否则交替写就会断链。
    """

    _REGISTRY_LOCK = threading.Lock()
    _LOCKS: dict[str, threading.Lock] = {}
    _CURSORS: dict[str, str] = {}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # 按解析后的绝对路径做身份：按传进来的字符串做身份的话，
        # "data/x.jsonl" 与 "/abs/data/x.jsonl" 会拿到两把不同的锁，
        # 写的却是同一个文件——互斥失效且不报错。
        self._key = str(self.path.resolve())

    # ── 写 ──────────────────────────────────────────────────────────────

    def write(self, event: DecisionEvent) -> bool:
        return self._append(event.to_dict())

    def _append(self, payload: dict[str, Any]) -> bool:
        with self._lock_for(self._key):
            prev = self._CURSORS.get(self._key)
            if prev is None:
                # ⛔ 不当 genesis：游标缺失（进程重启、新实例）时必须从磁盘末行
                # 重算，否则重启后第一行的 prev_hash 会是 64 个 0，链从那行起
                # 永久断裂，而且**写入时不报错**（tasks 2.3）。
                prev = self._tail_digest() or GENESIS_PREV_HASH

            body = dict(payload)
            body["prev_hash"] = prev
            # sort_keys 让同一份内容的字节可复现；ensure_ascii=False 让中文按
            # UTF-8 原样落盘（链算的是字节，中文不需要转义成 \uXXXX）。
            # json.dumps 会把真实换行转义成 "\\n" 两个字符，所以一条记录永远
            # 占且只占一行——这是"按 b'\\n' 切行"成立的前提。
            line = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")

            self.path.parent.mkdir(parents=True, exist_ok=True)
            # ⚠️ 必须二进制。文本模式在 Windows 上会把 "\n" 翻成 \r\n 落盘，
            # 链在 Mac 上全绿、推到 .51 上整条报断（部署约束 4）。
            with open(self.path, "ab") as handle:
                handle.write(line + b"\n")
                handle.flush()
                os.fsync(handle.fileno())

            self._CURSORS[self._key] = hashlib.sha256(line).hexdigest()
        return True

    @classmethod
    def _lock_for(cls, key: str) -> threading.Lock:
        # 注册表本身要加锁：setdefault(key, threading.Lock()) 会让两个并发线程
        # 各造一把 Lock、只有一把胜出，败者拿着自己那把去 append——互斥当场失效。
        with cls._REGISTRY_LOCK:
            lock = cls._LOCKS.get(key)
            if lock is None:
                lock = cls._LOCKS[key] = threading.Lock()
            return lock

    def _tail_digest(self) -> str | None:
        for line in reversed(self._raw_lines()):
            return hashlib.sha256(line).hexdigest()
        return None

    def _raw_lines(self) -> list[bytes]:
        if not self.path.exists():
            return []
        with open(self.path, "rb") as handle:
            return [line for line in handle.read().split(b"\n") if line.strip()]

    # ── 读 ──────────────────────────────────────────────────────────────

    def read_all(self) -> list[dict[str, Any]]:
        return [json.loads(line.decode("utf-8")) for line in self._raw_lines()]

    # ── 自校验 ──────────────────────────────────────────────────────────

    def verify_chain(self) -> ChainVerification:
        """
        链完整性校验：能检出任意一行被删除、插入或修改。

        **对磁盘原始字节重算 SHA-256**（design D3 第 2 条）——⛔ 不做 JSON 解析
        后重新 `dumps` 的规范化。重排序、`ensure_ascii` 差异、空格差异都会让哈希
        对不上，导致明明没被改的中文记录报断链。链的定义就是"上一行落盘字节的
        SHA-256"，不是"上一行内容的某种规范形式的 SHA-256"。

        **第 2 条记录起，缺 `prev_hash` 即判定断链；仅第 1 条可豁免**（design D3
        第 1 条）。否则攻击者删光全文件的 `prev_hash` 字段重写，整链会因"每行都
        豁免"而通过校验——这是平台侧修过的绕过。

        第 1 行的 `prev_hash` **取值不校验**：它没有前驱，拿什么和它比？
        `GENESIS_PREV_HASH` 是写入侧的约定，不是可校验的主张。硬要求第 1 行等于
        哨兵，会把"接管一份既有文件"变成永久断链的误报。

        ⚠️ 已知边界：检不出**最后一行**被修改（没有后继来暴露它）。这是哈希链的
        固有性质。返回 `tail_hash` 供外部锚定，本层不做锚定。
        """
        lines = self._raw_lines()
        expected: str | None = None

        for index, line in enumerate(lines, start=1):
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return ChainVerification(
                    ok=False,
                    total=len(lines),
                    broken_at=index,
                    error=f"第 {index} 行不是合法的 UTF-8 JSON: {exc}",
                )

            if not isinstance(record, dict):
                # json.loads 对合法但非对象的 JSON（标量 42/null/true、数组、
                # 字符串）不会抛异常——"prev_hash" not in record 对标量会是
                # TypeError 而不是成员测试。攻击者只需 append 一行 "null\n"
                # 就能让校验器整个抛出未处理异常，而不是按契约返回一个断链
                # 结果。ChainVerification.error 字段存在的意义就是让损坏输入
                # 变成"被报告的一次断链"，而不是一个异常，处理权不该被交给
                # 上游某个恰好包住这里的 except。
                return ChainVerification(
                    ok=False,
                    total=len(lines),
                    broken_at=index,
                    error=f"第 {index} 行不是 JSON 对象",
                )

            if "prev_hash" not in record:
                if index > 1:
                    return ChainVerification(
                        ok=False,
                        total=len(lines),
                        broken_at=index,
                        error=(
                            f"第 {index} 行缺少 prev_hash 字段；"
                            "缺字段豁免只对第 1 行生效（design D3 第 1 条）"
                        ),
                    )
            elif index > 1 and record["prev_hash"] != expected:
                return ChainVerification(
                    ok=False,
                    total=len(lines),
                    broken_at=index,
                    error=(
                        f"第 {index} 行的 prev_hash 与上一行落盘字节的 SHA-256 不一致："
                        f"期望 {expected}，实得 {record['prev_hash']}"
                    ),
                )

            expected = hashlib.sha256(line).hexdigest()

        return ChainVerification(ok=True, total=len(lines), tail_hash=expected)
