import sqlite3

import pytest

from app.audit.events import BACKFILL, OUTBOUND_BLOCKED, CriterionScore, DecisionEvent, AI_ANALYSIS
from app.audit.sinks import SqliteSink
from app.storage.db import get_connection, init_schema


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "audit.db")


@pytest.fixture
def conn(db_path):
    c = get_connection(db_path)
    init_schema(c)
    return c


@pytest.fixture
def sink(conn):
    return SqliteSink(conn)


def _event(**overrides) -> DecisionEvent:
    payload = {
        "id": "thread-1:effect_record_analysis:sha256:abc",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "application_id": "app-1",
        "job_id": "job-1",
        "configured_model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_abc",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:abc",
        "rubric_version": "ecu-embedded-v2",
        "rubric_snapshot": {"criteria": [{"key": "autosar", "weight": 0.4}]},
        "raw_response": '{"scores": [3]}',
        "token_usage": {"total_tokens": 128},
        "latency_ms": 812.5,
        "scores": (
            CriterionScore(criterion_key="autosar", score=3.0, evidence_ref="resume-1#120-180"),
            CriterionScore(criterion_key="can_bus", score=2.0, evidence_ref="resume-1#300-360"),
        ),
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


def _run_row(conn, run_id="thread-1:effect_record_analysis:sha256:abc"):
    cursor = conn.execute("SELECT * FROM analysis_run WHERE id = ?", (run_id,))
    columns = [c[0] for c in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None


# ── 写入：铁律 3 的十四列 ────────────────────────────────────────────────


def test_write_persists_the_analysis_run(sink, conn):
    assert sink.write(_event()) is True

    row = _run_row(conn)
    assert row["configured_model"] == "deepseek-chat"
    assert row["prompt_version"] == "score-v1"
    assert row["temperature"] == 0.0
    assert row["input_hash"] == "sha256:abc"
    assert row["raw_response"] == '{"scores": [3]}'
    assert row["latency_ms"] == 812.5
    assert row["created_at"]  # 留空由 datetime('now') 填


def test_configured_and_response_model_are_kept_apart(sink, conn):
    """铁律 5：配置里写的名字不算数，响应返回的才算——两个字段各自保存。"""
    sink.write(_event(configured_model="deepseek-chat", response_model="deepseek-chat-250801"))

    row = _run_row(conn)
    assert row["configured_model"] == "deepseek-chat"
    assert row["response_model"] == "deepseek-chat-250801"


@pytest.mark.parametrize("column", ["response_model", "system_fingerprint"])
def test_missing_vendor_fields_are_stored_as_null(sink, conn, column):
    """
    spec「供应商不返回部署指纹」：该字段记为空值，留痕照常写入，
    **留痕流程不因字段缺失而失败**。断言的是写入成功，不是抛异常。
    """
    assert sink.write(_event(**{column: None})) is True

    assert _run_row(conn)[column] is None


def test_rubric_version_and_snapshot_round_trip(sink):
    """
    偏离登记 1：两者合并落进 rubric_snapshot 一列，read_all() 必须无损拆回。
    """
    sink.write(_event())

    record = sink.read_all()[0]
    assert record["rubric_version"] == "ecu-embedded-v2"
    assert record["rubric_snapshot"] == {"criteria": [{"key": "autosar", "weight": 0.4}]}


def test_scores_get_deterministic_ids(sink, conn):
    sink.write(_event())

    rows = conn.execute(
        "SELECT id, criterion_key, evidence_ref FROM criterion_score ORDER BY criterion_key"
    ).fetchall()
    assert [row[0] for row in rows] == [
        "thread-1:effect_record_analysis:sha256:abc:autosar",
        "thread-1:effect_record_analysis:sha256:abc:can_bus",
    ]
    assert rows[0][2] == "resume-1#120-180"


# ── 事务归属：⛔ 不自行 commit ───────────────────────────────────────────


def test_write_does_not_commit(sink, db_path):
    """
    铁律 1：被 idempotent_effect 装饰的函数体里的写入，必须与装饰器追加的
    effect_log 行落在同一个事务里、由装饰器统一提交一次。这里先提交一次，
    进程在"这次提交"与"装饰器提交 effect_log"之间崩溃就会留下
    「业务写已落盘、幂等记录没落」——重放时撞主键，重试永久失败。
    实证见 docs/findings/2026-08-13-sqlite-事务归属冲突.md §8.5。
    """
    sink.write(_event())

    onlooker = get_connection(db_path)
    assert onlooker.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0

    sink.conn.commit()
    assert onlooker.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 1


def test_rollback_undoes_the_whole_event(sink, conn):
    sink.write(_event())
    conn.rollback()

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM criterion_score").fetchone()[0] == 0


# ── 主键短路 vs. 约束失败：这一格写宽了，铁律 4 当场失效 ─────────────────


def test_duplicate_primary_key_short_circuits(sink, conn):
    """tasks 2.2：主键冲突即视为已写入，短路返回。"""
    assert sink.write(_event()) is True
    assert sink.write(_event()) is False

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM criterion_score").fetchone()[0] == 2


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_empty_evidence_ref_is_not_swallowed(sink, conn, blank):
    """
    铁律 4 由数据库 CHECK 强制（U1）。本层的唯一职责是**不吞异常**——
    把 `except sqlite3.IntegrityError: return False` 写宽一格，铁律 4 就从
    "数据库强制"退回"静默放过"，而所有正常用例照样全绿。
    """
    event = _event(scores=(CriterionScore("autosar", 3.0, blank),))

    with pytest.raises(sqlite3.IntegrityError):
        sink.write(event)


def test_failed_score_leaves_no_orphan_run(sink, conn):
    """
    一半写成功不是可接受的结果：analysis_run 落了、criterion_score 没落，
    审计上就是"有一次调用、但一条评分都没打"，与"根本没调用"无法区分。
    回滚由调用方的事务承担（本层不 commit 正是为了这一点）。
    """
    with pytest.raises(sqlite3.IntegrityError):
        sink.write(_event(scores=(CriterionScore("autosar", 3.0, ""),)))
    conn.rollback()

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


@pytest.mark.parametrize("column", ["prompt_version", "temperature", "input_hash"])
def test_missing_reproducibility_column_raises(sink, column):
    """
    铁律 3 的七个 NOT NULL 列缺一不可，缺了就该炸——静默写一行"复现不了的留痕"
    比不写更糟：它看起来像有记录。
    """
    with pytest.raises(sqlite3.IntegrityError):
        sink.write(_event(**{column: None}))


# ── 不属于本 sink 的事件类型 ─────────────────────────────────────────────


@pytest.mark.parametrize("event_type", [OUTBOUND_BLOCKED, BACKFILL])
def test_non_analysis_events_have_no_body_in_this_sink(sink, conn, event_type):
    """
    外发事件的 SQLite 真身是 pending_approval（U5 的 queue.py 写），补录事件只
    存在于镜像链上。本 sink ⛔ 不替它们凭空造一张表，返回 False 表示"这里没有
    它的真身"，让调用方（AuditRecorder）与对账（U6 6.4）都能看见这个事实。
    """
    assert sink.write(_event(event_type=event_type)) is False
    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


# ── 读取与检索 ───────────────────────────────────────────────────────────


def test_read_all_nests_scores_under_their_run(sink):
    sink.write(_event())

    records = sink.read_all()
    assert len(records) == 1
    assert {score["criterion_key"] for score in records[0]["scores"]} == {"autosar", "can_bus"}


def test_read_all_does_not_mutate_row_factory(sink, conn):
    """
    conn 是全应用共享的一条连接（db.get_connection 的注释）。在这里顺手设
    conn.row_factory = sqlite3.Row，会让所有按下标取值的既有代码静默改变行为。
    """
    before = conn.row_factory
    sink.read_all()

    assert conn.row_factory is before


def test_query_filters_by_application_and_time_range(sink, conn):
    sink.write(_event(id="run-a", application_id="app-1", created_at="2026-08-01 00:00:00"))
    sink.write(_event(id="run-b", application_id="app-2", created_at="2026-08-20 00:00:00"))
    sink.write(_event(id="run-c", application_id="app-1", created_at="2026-08-25 00:00:00"))

    hits = sink.query(application_id="app-1", created_from="2026-08-10 00:00:00")
    assert [record["id"] for record in hits] == ["run-c"]


def test_query_by_model_identifier(sink):
    sink.write(_event(id="run-a", response_model="deepseek-chat-241226"))
    sink.write(_event(id="run-b", response_model="deepseek-chat-250801"))

    hits = sink.query(response_model="deepseek-chat-250801")
    assert [record["id"] for record in hits] == ["run-b"]


@pytest.mark.parametrize(
    "bad_key",
    ["id = '' OR 1 = 1 --", "raw_response"],
)
def test_query_rejects_keys_outside_the_whitelist(sink, bad_key):
    """
    列名不能参数化，只能拼进 SQL——所以列名必须走白名单，不是"看起来像列名就放行"。
    raw_response 被排除是刻意的：按响应全文检索会全表扫，且没有审计场景需要它。
    """
    with pytest.raises(ValueError, match="不可检索的字段"):
        sink.query(**{bad_key: "x"})
