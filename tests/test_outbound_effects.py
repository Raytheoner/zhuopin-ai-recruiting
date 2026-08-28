"""
两个新的 effect_* 节点。工程铁律 1：幂等记录与业务写必须落在同一个事务里，
由 idempotent_effect 装饰器统一提交一次。
"""

import json
import sqlite3

import pytest

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.nodes import effect_enqueue_pending_approval, effect_record_outbound_audit
from app.outbound import queue
from app.outbound.messages import CandidateOutboundMessage
from app.storage.db import get_connection, init_schema

AI_BODY = "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"


class _CommitCountingConnection(sqlite3.Connection):
    """
    统计 commit() 被调用的次数，不改变其行为。sqlite3.Connection 是内建类型，
    它的 `commit` 属性只读——不能用 monkeypatch.setattr 在实例上打桩（会抛
    `AttributeError: 'sqlite3.Connection' object attribute 'commit' is
    read-only`）。子类化 + `factory=` 是本仓库既有的做法，见
    `tests/test_graph_idempotency.py::_CommitCountingConnection`。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0

    def commit(self):
        self.commit_count += 1
        return super().commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "e.db"))
    init_schema(c)
    return c


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "decisions.jsonl"


@pytest.fixture
def recorder(conn, chain_path):
    return AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))


def _msg(**over):
    payload = {
        "message_type": "rejection_letter",
        "recipient": "cand-9@example.com",
        "body": AI_BODY,
    }
    payload.update(over)
    return CandidateOutboundMessage(**payload)


def _blocked_event(message, reason="等待人工确认"):
    return DecisionEvent(
        id=f"job-7:effect_record_outbound_audit:{message.content_hash()}:False",
        event_type=OUTBOUND_BLOCKED,
        thread_id="job-7",
        message_type=message.message_type,
        recipient=message.recipient,
        content_hash=message.content_hash(),
        blocked_reason=reason,
        evidence={"severity": "high"},
    )


def _effect_log_count(conn, node_name):
    return conn.execute(
        "SELECT count(*) FROM effect_log WHERE node_name = ?", (node_name,)
    ).fetchone()[0]


# ── 入队节点 ─────────────────────────────────────────────────────────────


def test_enqueue_effect_writes_the_row_and_its_effect_log_together(conn):
    """
    ⭐ 工程铁律 1 的不变式：每个 effect_* 节点的 effect_log 条数与其业务表行数
    按 thread 恒等。2026-08-10 / 08-12 现网各丢一轮 outbox 就是这条被破坏的形状
    （docs/findings/2026-08-13-sqlite-事务归属冲突.md §8.5）。
    """
    message = _msg()
    effect_enqueue_pending_approval(
        conn,
        thread_id="job-7",
        business_key=message.content_hash(),
        message=message,
        blocked_reason="等待人工确认",
    )

    assert len(queue.list_pending(conn)) == 1
    assert _effect_log_count(conn, "effect_enqueue_pending_approval") == 1


def test_enqueue_effect_is_idempotent_on_replay(conn):
    """
    tasks 5.8：外发相关节点被从头重跑 → 已入队不重复入队（effect_log 命中短路）。
    幂等键 {thread_id}:effect_enqueue_pending_approval:{content_hash}，与 U1 的
    (thread_id, content_hash) 唯一索引同粒度——两道防线，粒度必须一致。
    """
    message = _msg()
    for _ in range(3):
        effect_enqueue_pending_approval(
            conn,
            thread_id="job-7",
            business_key=message.content_hash(),
            message=message,
            blocked_reason="等待人工确认",
        )

    assert len(queue.list_pending(conn)) == 1
    assert _effect_log_count(conn, "effect_enqueue_pending_approval") == 1


def test_enqueue_effect_commits_exactly_once(tmp_path):
    """
    ⭐⭐⭐ 这是「effect_log 条数与业务表行数按 thread 恒等」这条不变式真正的
    证明，而不是 `test_enqueue_effect_writes_the_row_and_its_effect_log_together`
    那种"跑完看最终状态"的弱形式——那种写法即使
    `effect_enqueue_pending_approval` 函数体内自己先 `commit()` 一次、装饰器
    再 `commit()` 一次（业务写与 effect_log 被拆成两个事务），只要过程中没有
    真的崩溃，最终状态看起来仍然是"两边都是 1"，测试照样通过——**为错误的
    原因通过**。

    这里没有用"崩溃在两次 commit 之间"的连接子类去模拟崩溃点：本地验证过，
    对 sqlite3 这种单机文件事务，一旦 Python 层 `commit()` 被拦截住不再往下
    传导（无论拦第 1 次还是第 2 次调用），SQLite 侧都不会有任何东西真正落盘，
    崩溃前后 pending_approval 与 effect_log 永远同时是 0——这种模拟法测不出
    "业务写已经真的落盘、只是幂等记录没跟上"这种更危险的分叉（`.51` 现网
    2026-08-10/08-12 各丢一轮 outbox 的真实形状），本任务在写这个文件时手动
    验证过：给 `effect_enqueue_pending_approval` 函数体末尾加一行
    `conn.commit()` 造出真的双事务缺陷后，"崩溃在两次 commit 之间"这种写法
    的断言（"崩溃后两张表都是 0 行"）依然通过，属于会为错误原因通过的假阳性
    守护——不能留在测试里误导后来者。

    真正**直接**证明"只有一次 commit"的办法，是数 `conn.commit()` 被调用的
    次数——恰好 1 次即代表业务写与 effect_log 行只可能在同一次原子提交里
    一起落盘或一起不落盘，不存在"业务写单独先落地"的窗口。用统计 commit()
    调用次数的连接子类（既有做法，见
    `tests/test_graph_idempotency.py::_open_commit_counting_connection`）而不是
    monkeypatch：sqlite3.Connection 是内建类型，`commit` 属性只读，实例级
    monkeypatch.setattr 会直接抛 `AttributeError: object attribute 'commit'
    is read-only`，根本装不上去。

    本测试手动验证过会在违规实现上失败：给函数体末尾加一行 `conn.commit()`
    后，本测试报 `AssertionError: ... 实际触发了 2 次`（先于本次提交撤回）；
    去掉那行、恢复成"函数体内不 commit"后本测试转绿。

    ⚠️ 建表用一条独立的连接、提交后关掉，再用计数连接重新打开同一个库文件——
    `init_schema()` 自己也会 `commit()`，如果计数连接从建表开始就在场，那次
    commit 会混进计数、把断言变成"至少 1 次"而不是"恰好 1 次"。
    """
    db_path = str(tmp_path / "e.db")
    setup_conn = get_connection(db_path)
    init_schema(setup_conn)
    setup_conn.close()

    counting_conn = sqlite3.connect(
        db_path, check_same_thread=False, factory=_CommitCountingConnection
    )
    counting_conn.execute("PRAGMA foreign_keys = ON")

    message = _msg()
    effect_enqueue_pending_approval(
        counting_conn,
        thread_id="job-7",
        business_key=message.content_hash(),
        message=message,
        blocked_reason="等待人工确认",
    )

    assert counting_conn.commit_count == 1, (
        "effect_enqueue_pending_approval 一次成功调用应该只触发 1 次 conn.commit()"
        f"（由 idempotent_effect 装饰器统一提交），实际触发了 {counting_conn.commit_count} 次"
    )


def test_enqueue_effect_refuses_a_message_that_already_carries_confirmed_by(conn):
    """
    D5 死锁防线的另一半（Task 2 交付了 approve() 从不重新入队那一半）：一条
    携带 confirmed_by 的消息已经在队列里了（它就是从队列里取出、签上名重走
    门禁的那一条），再把它送进 effect_enqueue_pending_approval 就是把它第二次
    写进同一张表——`queue.enqueue()` 本身不检查 confirmed_by（Task 1 design：
    它「无条件」按 (thread_id, content_hash) 做 ON CONFLICT DO NOTHING），
    所以这道防线必须在调用 enqueue() 之前、在这个 effect 节点里挡住，不能指望
    底下的 queue.enqueue() 替它把关。

    若这道检查被删掉：对一条尚未入队过的签名消息（content_hash 不含
    confirmed_by，但这是它在队列里的"第一次")，effect_enqueue_pending_approval
    会静默把它写进 pending_approval——本该只在放行路径里存在的
    "confirmed_by 已知却仍处于 pending" 的行本不该被入队产生。
    """
    message = _msg().with_confirmation("张三")

    with pytest.raises(ValueError, match="confirmed_by"):
        effect_enqueue_pending_approval(
            conn,
            thread_id="job-7",
            business_key=message.content_hash(),
            message=message,
            blocked_reason="等待人工确认",
        )

    assert len(queue.list_pending(conn)) == 0
    assert _effect_log_count(conn, "effect_enqueue_pending_approval") == 0


# ── 留痕节点 ─────────────────────────────────────────────────────────────


def test_outbound_audit_writes_no_analysis_run_row_and_says_so(conn, recorder):
    """
    ⭐ 外发事件在 analysis_run 里**没有真身**——它的真身是 pending_approval。
    AuditRecorder.record() 因此返回 False，⛔ 调用方不得把它当成"写失败"。
    """
    message = _msg()
    stored = effect_record_outbound_audit(
        conn,
        thread_id="job-7",
        business_key=f"{message.content_hash()}:False",
        recorder=recorder,
        event=_blocked_event(message),
    )

    assert stored is False
    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0
    assert _effect_log_count(conn, "effect_record_outbound_audit") == 1


def test_block_and_release_of_one_draft_each_leave_their_own_trail(conn, recorder):
    """
    tasks 5.4 逐字：business_key = {content_hash}:{allowed}——同一草稿的"拦截"与
    "放行"各留一条痕、重放不重复留痕。

    ⚠️ 若 business_key 只用 content_hash，放行那条会命中拦截那条的 effect_log 而
    被短路，于是**投递发生了却没有留痕**——spec「外发与拦截动作强制留痕」当场破。
    """
    message = _msg()
    for allowed in (False, True):
        for _ in range(2):  # 各跑两遍，验重放
            effect_record_outbound_audit(
                conn,
                thread_id="job-7",
                business_key=f"{message.content_hash()}:{allowed}",
                recorder=recorder,
                event=_blocked_event(message)
                if not allowed
                else DecisionEvent(
                    id=f"job-7:effect_record_outbound_audit:{message.content_hash()}:True",
                    event_type=OUTBOUND_DELIVERED,
                    thread_id="job-7",
                    message_type=message.message_type,
                    content_hash=message.content_hash(),
                    confirmed_by="张三",
                ),
            )

    assert _effect_log_count(conn, "effect_record_outbound_audit") == 2


def test_the_mirror_line_is_written_even_though_sqlite_stored_nothing(
    conn, recorder, chain_path
):
    """
    ⭐⭐ 本 Task 最容易写错的一条。AuditRecorder.record() 对外发事件返回 False，
    但那**不是**"已经写过"——外发事件在这个 sink 里根本没有真身。镜像里那一行是
    外发留痕**唯一的**载体，⛔ 绝不能因为 False 就跳过 append。

    U3 的 RecorderAuditHook 里 False → 跳过镜像，那是因为它只造 ai_analysis 事件、
    False 只可能是"已写过"。两处的 False 含义相反——这正是 2026-08-28 对残留 B
    的拍板要求的：调用点按自己写的 event_type 决定行为，⛔ 不从 False 反推原因。
    """
    message = _msg()
    effect_record_outbound_audit(
        conn,
        thread_id="job-7",
        business_key=f"{message.content_hash()}:False",
        recorder=recorder,
        event=_blocked_event(message),
    )
    conn.commit()
    recorder.mirror(_blocked_event(message))  # 调用点在事务提交后触发（见 Task 4）

    lines = chain_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    mirrored = json.loads(lines[0])
    assert mirrored["event_type"] == OUTBOUND_BLOCKED
    assert mirrored["blocked_reason"] == "等待人工确认"
    assert mirrored["evidence"] == {"severity": "high"}
