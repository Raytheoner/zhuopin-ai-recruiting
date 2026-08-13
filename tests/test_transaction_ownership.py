"""
覆盖 openspec/changes/fix-sqlite-transaction-ownership/specs/effect-transaction-integrity/spec.md
的「单一事务边界所有权」与「事务归属冲突可在任意平台确定性复现」两条 Requirement。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.idempotency import idempotent_effect


def test_checkpointer_and_effect_layer_do_not_share_a_connection(tmp_path):
    """
    单一事务边界所有权(Requirement: 单一事务边界所有权)在连接层面的充分条件：
    如果 checkpointer 与 effect 层共用同一个 sqlite3.Connection 对象，这个连接上
    就必然存在两个独立的提交/回滚发起者——不需要复现具体的交错时序、不需要触发
    具体的 OperationalError，"共用同一个连接对象"这件事本身就是"多个所有者"的
    直接证据。纯 Python 对象恒等性判断，在任意操作系统、任意 SQLite 版本上结果
    都一样，不存在"本地测不出来"的问题（对应 Requirement: 事务归属冲突可在任意
    平台确定性复现）。

    这是本计划的 TDD 红灯基线：在修复前（build_intake_graph 里
    `checkpointer = SqliteSaver(conn)`）必定失败；Task 2 让 checkpointer 改用
    独立连接后必定转绿。
    """
    from app.graph.build import build_intake_graph

    db_path = str(tmp_path / "wiring.db")
    conn = get_connection(db_path)
    init_schema(conn)

    graph = build_intake_graph(db_path, gateway=None, conn=conn, channel=None)

    assert graph.checkpointer.conn is not conn, (
        "checkpointer 与 effect 层共用同一个 sqlite3.Connection，"
        "该连接上事务边界所有权不唯一（工程铁律1 / spec Requirement: 单一事务边界所有权）"
    )


class _CrashableConnection(sqlite3.Connection):
    """
    在指定的那一次 commit() 调用上模拟"进程恰好在真正落盘之前崩溃"——
    与 tests/test_graph_idempotency.py 里的同名辅助类用途一致，这里独立
    定义一份，保持每个测试文件自包含（与仓库现有约定一致）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crash_next_commit = False

    def commit(self):
        if self.crash_next_commit:
            self.crash_next_commit = False
            raise RuntimeError("simulated crash exactly before durable commit")
        return super().commit()


def _open_crashable_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, factory=_CrashableConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_shared_connection_lets_checkpoint_commit_break_effect_atomicity(tmp_path):
    """
    特征测试：证明"checkpointer 与 effect 层共用一个连接"这件事本身，一旦检查点
    持久化恰好发生在一次 effect 写入自己的业务写入与它自己的 effect_log 提交之间
    （spec Scenario:「检查点持久化不打断 effect 的原子性」的"交替执行"），
    就会打破原子性——业务写入被检查点的提交顺带带走，effect_log 却还没写，二者
    不再同生共死。

    不经过 build_intake_graph()：直接构造一个共享连接，把真实的 SqliteSaver.put()
    调用安排在一个被 idempotent_effect 装饰的函数体内部执行——这不是在编造一个
    不会发生的场景，而是把 spec Scenario 里"先执行一个带幂等保护的 effect 写入，
    再触发一次编排层的检查点持久化"这句话，用显式、确定性的调用顺序表达出来，
    不依赖 graph.invoke() 的自然调度时序（已验证：自然调度在本机不稳定复现）。
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    db_path = str(tmp_path / "atomicity.db")
    conn = _open_crashable_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    checkpointer = SqliteSaver(conn)  # 与 effect 层共用同一个连接（修复前的架构）
    checkpoint = {
        "v": 1,
        "id": "chk-1",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": None,
    }
    config = {"configurable": {"thread_id": "job1", "checkpoint_ns": ""}}
    metadata = {"source": "loop", "step": 1, "parents": {}}

    @idempotent_effect("effect_persist_draft_probe")
    def effect_fn(conn, *, thread_id, business_key):
        conn.execute(
            "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
            "VALUES (?, ?, 1, 'drafting', '{}')",
            (f"{thread_id}-v1", thread_id),
        )
        # 模拟编排层在 effect 写入尚未提交时，在同一连接上触发一次真实的
        # 检查点持久化（LangGraph 每个 superstep 之间会做的事）。
        checkpointer.put(config, checkpoint, metadata, {})

    # 只在 idempotent_effect 装饰器自己那次 commit()（effect_log 的提交）上
    # 模拟崩溃——checkpointer.put() 自己的提交（业务写入连带被提交）应该正常
    # 成功，这样才能观察到"业务写入已落盘、effect_log 还没有"的中间状态。
    orig_commit = conn.commit
    calls = {"n": 0}

    def counting_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            conn.crash_next_commit = True
        return orig_commit()

    conn.commit = counting_commit

    with pytest.raises(RuntimeError, match="simulated crash"):
        effect_fn(conn, thread_id="job1", business_key="1")

    conn.close()  # 模拟进程真的死了

    fresh_conn = get_connection(db_path)
    job_profile_rows = fresh_conn.execute("SELECT * FROM job_profile").fetchall()
    effect_log_rows = fresh_conn.execute("SELECT * FROM effect_log").fetchall()

    assert len(job_profile_rows) == 1, "checkpointer 的提交把 effect 的业务写入带走了，这一步应该已经落盘"
    assert len(effect_log_rows) == 0, (
        "原子性被打破的直接证据：业务写入已经落盘，但对应的 effect_log 记录没有——"
        "如果 checkpointer 用独立连接，这两个写入根本不可能被拆开"
    )

    # 进一步验证 Requirement「幂等键与业务写入原子提交在事务中断后仍然成立」
    # 的重放场景：LangGraph 恢复时节点从头整个重跑（工程铁律1），重放应该
    # 把这次没完成的 effect 当作首次执行、干净地补全；但因为业务写入已经
    # 意外落盘、effect_log 却没有，重放会在已存在的主键上再插一次，触发
    # UNIQUE 冲突而不是干净完成——这正是"两者数量不一致"这条禁止条款被违反
    # 的证据。
    fresh_checkpointer = SqliteSaver(fresh_conn)

    @idempotent_effect("effect_persist_draft_probe")
    def effect_fn_replay(conn, *, thread_id, business_key):
        conn.execute(
            "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
            "VALUES (?, ?, 1, 'drafting', '{}')",
            (f"{thread_id}-v1", thread_id),
        )
        fresh_checkpointer.put(config, checkpoint, metadata, {})

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        effect_fn_replay(fresh_conn, thread_id="job1", business_key="1")
