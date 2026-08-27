"""
RecorderAuditHook：网关的扁平参数 → DecisionEvent → AuditRecorder。

本文件最重要的两条是**方向相反**的失败语义（计划 Global Constraints 第三条）：
SQLite 失败必须抛，JSONL 失败必须不抛。把它们写成对称是本 Task 最容易犯的错，
所以两条各自独立成用例，任何一侧被改成另一侧的语义都会单独变红。
"""

import json
import sqlite3
import threading

import pytest

from app.audit.events import AI_ANALYSIS, CriterionScore, DecisionEvent
from app.audit.hook import RecorderAuditHook, UnknownAuditContextKey
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "audit.db")


@pytest.fixture
def conn(db_path):
    connection = get_connection(db_path)
    init_schema(connection)
    return connection


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "decisions.jsonl"


@pytest.fixture
def hook(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return RecorderAuditHook(recorder, conn)


def _call(hook, **overrides):
    """一次形状真实的网关回调。默认不带 audit_context（现有调用点就是这样）。"""
    payload = {
        "model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_8802",
        "prompt_version": "intake-v5",
        "temperature": 0,
        "input_hash": "a" * 64,
        "raw_response": '{"ok": true}',
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "latency_ms": 1500.0,
        "attempt": 1,
        "audit_context": None,
    }
    payload.update(overrides)
    hook.record(**payload)


def _rows(conn):
    cursor = conn.execute("SELECT * FROM analysis_run")
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ── 字段映射 ─────────────────────────────────────────────────────────────


def test_configured_and_response_model_land_in_separate_columns(hook, conn):
    """
    spec「一次评分调用完成」逐字：配置侧模型标识与响应返回的模型标识
    **分两个字段各自保存，不互相覆盖**。这两个值在真实环境里经常不同
    （配置写会漂移的别名 deepseek-chat，响应回具体版本）。
    """
    _call(hook)

    row = _rows(conn)[0]
    assert row["configured_model"] == "deepseek-chat"
    assert row["response_model"] == "deepseek-chat-241226"


def test_business_keys_come_from_audit_context(hook, conn):
    _call(
        hook,
        audit_context={
            "thread_id": "job-7",
            "node": "compute_intake_turn",
            "application_id": "app-3",
            "job_id": "job-7",
            "rubric_version": "ecu-embedded-v2",
            "rubric_snapshot": {"skill_match": 0.4},
        },
    )

    row = _rows(conn)[0]
    assert row["application_id"] == "app-3"
    assert row["job_id"] == "job-7"
    assert json.loads(row["rubric_snapshot"]) == {
        "version": "ecu-embedded-v2",
        "snapshot": {"skill_match": 0.4},
    }


def test_unknown_audit_context_key_is_rejected(hook):
    """
    ⭐ spec 硬要求：「系统 MUST NOT 在留痕记录中存储简历原文」。audit_context 是本
    变更唯一新增的"调用方能往留痕里塞东西"的通道——不拒收未登记的键，将来一个
    "方便排查"的 resume_text 会一路流进 JSONL 镜像。

    ⛔ 抛而不是忽略：忽略等于静默丢数据，写错的人永远不知道自己的 job_id 没进库。

    ⚠️ **但抛在留痕写完之后**（review round 2）：那次 API 调用已经付过钱、已经
    发生了，抛在写之前会让它一条记录都不剩——正是 spec 禁止的方向。所以下面
    同时断言"抛了"**和**"留痕还在"，两条缺一不可。
    """
    with pytest.raises(UnknownAuditContextKey, match="resume_text"):
        _call(hook, audit_context={"thread_id": "job-1", "resume_text": "张三，男，1990"})


def test_a_rejected_context_key_still_leaves_a_trail_without_leaking_its_value(
    hook, conn, chain_path
):
    """
    ⭐ 上一条的另一半。未登记的键被丢掉、调用照样留痕，且 error 里**只记键名、
    不记值**——未登记的键正是"可能藏着简历原文"的那些，把值写进 error 等于从
    另一个口子放它进留痕。
    """
    with pytest.raises(UnknownAuditContextKey):
        _call(
            hook,
            audit_context={"thread_id": "job-1", "resume_text": "张三·MARKER-9c1e·某某大学"},
        )

    row = _rows(conn)[0]
    assert row["id"].startswith("llm:")  # 只有 thread_id 没有 node → 走随机 id 分支
    mirrored = json.loads(chain_path.read_text(encoding="utf-8").splitlines()[0])
    assert "resume_text" in mirrored["error"]  # 键名记下来了
    assert "MARKER-9c1e" not in json.dumps(mirrored, ensure_ascii=False)  # 值没进去
    assert "MARKER-9c1e" not in json.dumps(_rows(conn), ensure_ascii=False, default=str)


def test_mirror_line_carries_the_call_timestamp(hook, conn, chain_path):
    """
    ⭐ 镜像是**防篡改的那一份独立证据**（SQLite 行可被 UPDATE，append-only 文件
    的攻击面小得多）。留 created_at=None 让数据库的 datetime('now') 去填的话，
    只有 SQLite 那侧有时刻，镜像里是 "created_at": null——一份说不出"这次调用
    发生在什么时候"的证据基本不成立，而 reconcile() 只比 id、发现不了
    （2026-08-28 review round 2）。

    顺带钉住两侧记的是**同一个**时刻：不是两次各自取 now()。
    """
    _call(hook)

    mirrored = json.loads(chain_path.read_text(encoding="utf-8").splitlines()[0])
    assert mirrored["created_at"]  # 不是 None、不是空串
    assert mirrored["created_at"] == _rows(conn)[0]["created_at"]


def test_missing_system_fingerprint_is_stored_as_null_and_does_not_raise(hook, conn):
    """
    tasks 3.6 / spec「供应商不返回部署指纹」：该字段记为空值，留痕照常写入，
    留痕流程**不因字段缺失而失败**。断言的是"写成功且列为 NULL"，不是抛异常。
    """
    _call(hook, system_fingerprint=None)

    row = _rows(conn)[0]
    assert row["system_fingerprint"] is None
    assert row["configured_model"] == "deepseek-chat"  # 其余字段照常落盘


def test_none_raw_response_is_coerced_and_flagged_in_the_mirror(hook, conn, chain_path):
    """
    analysis_run.raw_response 是 NOT NULL（app/storage/db.py:105）。模型返回空
    响应体时若原样传 None，留痕会撞 NOT NULL。折成空串写入，并在镜像的 error
    字段留下痕迹：真身满足 NOT NULL，"这次是空的"这个事实不丢。

    ⚠️ 本条只证明**留痕这一段**不因空响应而失败。那次调用本身仍会失败——网关
    紧接着 json.loads(None) 抛 TypeError（TD-4）。⛔ 不要把这条读成"空响应
    不会打挂调用"。
    """
    _call(hook, raw_response=None)

    assert _rows(conn)[0]["raw_response"] == ""
    mirrored = json.loads(chain_path.read_text(encoding="utf-8").splitlines()[0])
    assert "raw_response" in mirrored["error"]


# ── 两段式与提交时机 ─────────────────────────────────────────────────────


def test_row_is_already_committed_when_the_mirror_runs(conn, chain_path, db_path):
    """
    ⭐ U2 的两段式约束在 U3 的落点：mirror 必须发生在**事务已提交之后**
    （delivery-units.md §3.4 第 3 条）。

    判据不是"读代码看见 commit 在前"——那不是测试。这里在 mirror 被调用的瞬间
    另开一条连接去查：能查到，就证明提交确实已经发生。第二条连接看不见未提交
    的事务，这是 SQLite 的隔离性替我们做的断言。
    """
    seen_from_outside = []

    class SpyMirror:
        def write(self, event):
            other = sqlite3.connect(db_path)
            seen_from_outside.append(
                other.execute("SELECT count(*) FROM analysis_run").fetchone()[0]
            )
            other.close()
            return True

        def read_all(self):
            return []

    recorder = AuditRecorder(SqliteSink(conn), SpyMirror())
    _call(RecorderAuditHook(recorder, conn))

    assert seen_from_outside == [1]


def test_hook_is_not_an_effect_function(hook):
    """
    ⛔ 禁止在 effect_* 函数体内 append JSONL（delivery-units.md §3.4 第 2 条）。
    本适配器自己 append，所以它**必须不叫** effect_*，否则 U2 的 AST 守护
    tests/test_audit_recorder.py::test_no_effect_function_appends_jsonl 会变红。
    这条是那个守护的可读版本，让"为什么不叫 effect_record"有个明写的理由。
    """
    assert not type(hook).__name__.startswith("effect_")
    assert not any(name.startswith("effect_") for name in dir(hook))


# ── 失败语义：两个方向必须相反 ───────────────────────────────────────────


def test_sqlite_failure_propagates_out_of_the_hook(conn, chain_path):
    """
    ⭐ 方向一。spec 逐字：「留痕写入失败 MUST NOT 被静默忽略：留痕写入失败时
    系统 SHALL 视该次 AI 结果为不可用，其评分 MUST NOT 进入下游排序。」

    异常穿透出网关 = 调用方拿不到解析结果 = 进不了下游，这是唯一自洽的落地。
    """

    class ExplodingStore:
        conn = None

        def write(self, event):
            raise sqlite3.OperationalError("disk I/O error")

        def read_all(self):
            return []

    recorder = AuditRecorder(ExplodingStore(), JsonlChainSink(chain_path))

    with pytest.raises(sqlite3.OperationalError):
        _call(RecorderAuditHook(recorder, conn))


def test_mirror_failure_does_not_propagate_and_the_row_survives(
    hook, conn, chain_path, monkeypatch
):
    """
    ⭐ 方向二，与上一条相反。design D1 / delivery-units.md §3.4 第 3 条：允许的
    偏差**只有单向**——「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据）。镜像
    失败就把整次调用打挂，等于把一个被明确允许的偏差升级成故障。缺行由
    AuditRecorder.reconcile() 检出、backfill() 在链尾补录（U2 已交付）。

    ⚠️ 这条和上一条必须都在。只留一条，实现者把两侧写成同一种语义时，
    另一侧不会有人发现。
    """

    def boom(event):
        raise OSError("镜像文件所在磁盘满了")

    monkeypatch.setattr(hook._recorder, "mirror", boom)

    _call(hook)  # 不抛

    assert len(_rows(conn)) == 1  # 真身还在


def test_failed_sqlite_write_leaves_no_half_written_row(conn, chain_path):
    """
    写失败后必须回滚。不回滚的话，半截写入悬在 SQLite 的隐式事务里，会被**下一个
    不相关的**提交顺手带进库（app/storage/idempotency.py:42-47 逐字描述了这个
    失败模式）。这里用 evidence_ref 为空白的评分项触发数据库 CHECK 失败。
    """
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    event = DecisionEvent(
        id="job-1:compute:hash:1",
        event_type=AI_ANALYSIS,
        configured_model="deepseek-chat",
        prompt_version="v1",
        temperature=0,
        input_hash="b" * 64,
        raw_response="{}",
        scores=(CriterionScore(criterion_key="skill_match", score=0.5, evidence_ref="   "),),
    )

    with pytest.raises(sqlite3.IntegrityError):
        RecorderAuditHook(recorder, conn)._write(event)

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


# ── analysis_run.id 的生成规则 ───────────────────────────────────────────


def test_id_is_deterministic_when_graph_context_is_present(hook, conn):
    """
    tasks 2.2 逐字：id 由调用方以 {thread_id}:{node}:{input_hash} 生成，主键冲突
    即视为已写入、短路返回。U3 在末尾追加 :{attempt}——同一次 extract_structured
    的多次尝试 input_hash 完全相同，不带 attempt 就会互撞，第 2 次尝试被 U2 的
    短路当成"已写过"静默丢掉（app/audit/sinks.py:156-168）。
    """
    context = {"thread_id": "job-7", "node": "compute_intake_turn"}
    _call(hook, audit_context=context, attempt=1)
    _call(hook, audit_context=context, attempt=2)

    ids = sorted(row["id"] for row in _rows(conn))
    assert ids == [
        f"job-7:compute_intake_turn:{'a' * 64}:1",
        f"job-7:compute_intake_turn:{'a' * 64}:2",
    ]


def test_two_identical_calls_without_graph_context_produce_two_rows(hook, conn):
    """
    ⭐ 没有 thread_id/node 时不能沿用确定性 id：两次内容完全相同的调用是**两次真实
    的、各花了一次钱的 API 调用**，确定性 id 会让第二次撞主键、被短路成 False，
    留痕**静默少一条**。确定性 id 的用途是 LangGraph 重放去重（tasks 2.2），
    而没有图上下文的调用根本不在重放路径上。
    """
    _call(hook)
    _call(hook)

    rows = _rows(conn)
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]


# ── review round 1 的两条回归钉子 ────────────────────────────────────────


def test_concurrent_calls_do_not_lose_rows_or_diverge(conn, chain_path):
    """
    ⭐ 一条 SQLite 连接只有**一个**事务，而本适配器是模块级单例、被 FastAPI 的
    工作线程池共用（每请求一个线程，见 app/storage/db.py:238-242）。不加锁时
    并发请求互相踩：A 的 rollback() 抹掉 B 已执行未提交的 INSERT，A 的 commit()
    把 B 写了一半的事务提前落盘。

    2026-08-28 review round 1 实测（未加锁）：20 线程并发 → SQLite 只剩 12 行、
    JSONL 17 行、3 个 sqlite3.InterfaceError。按 spec，留痕写入失败意味着那次
    AI 结果不可用，所以那 3 个异常各会打挂一个真实请求，另外 8 条是**真实付费
    调用的留痕被静默丢掉**。

    ⚠️ 断言三样都要：行数、镜像行数、异常数。只断言行数的话，"写进去了但两侧
    对不上"照样绿。
    """
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    hook = RecorderAuditHook(recorder, conn)
    errors: list[str] = []

    def worker(index: int) -> None:
        try:
            _call(hook, input_hash=f"{index:064d}")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(_rows(conn)) == 20
    assert len(chain_path.read_text(encoding="utf-8").strip().splitlines()) == 20


def test_a_deduped_write_does_not_append_a_second_mirror_line(hook, conn, chain_path):
    """
    ⭐ SQLite 因主键短路没落行时，镜像**也不能**再 append 一条。否则链上会多出
    一条真身里没有对应新增的记录，而 reconcile() 比的是**集合**差集，看不见这种
    重复——2026-08-28 review round 1 实测：SQLite 1 行、JSONL 2 行、
    reconcile().ok 仍为 True，偏差对唯一的检出手段完全隐形。

    这也是 2026-08-28 对残留 B 的拍板在本适配器上的落地：`record()` 返回 False
    时调用点⛔ 不反推原因——本适配器只造 AI_ANALYSIS 事件，False 只可能是
    "这条 id 已经写过"。
    """
    context = {"thread_id": "job-7", "node": "compute_score"}
    _call(hook, audit_context=context)
    _call(hook, audit_context=context)  # 完全相同 → 确定性 id 撞主键

    assert len(_rows(conn)) == 1
    assert len(chain_path.read_text(encoding="utf-8").strip().splitlines()) == 1
    assert hook._recorder.reconcile().ok is True
