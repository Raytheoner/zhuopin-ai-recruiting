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


def test_multiple_requests_reuse_the_same_checkpointer_connection(tmp_path, monkeypatch):
    """
    app/web/server.py::create_app._run_turn 修复前在每次 HTTP 请求时都调用一次
    build_intake_graph()——方向 A 让 checkpointer 拿独立连接后，如果调用位置不
    上提，这个独立连接会每请求泄漏一个（长期运行的 Windows 计划任务进程会
    积累未关闭的文件描述符）。

    用 monkeypatch 在 app.web.server.build_intake_graph 上打一个计数 spy：断言
    它在 create_app() 启动时被调用恰好一次，且两次 HTTP 请求之后调用次数仍然
    是 1——这直接证伪"每请求重建 graph"这个回归场景：如果有人把 _run_turn
    改回每请求调用一次 build_intake_graph()，这个断言会失败。早期版本这里比较
    的是同一个 app.state.graph 属性和它自己，那种写法在任何实现下（包括这个
    回归场景）都恒真，起不到保护作用（review Finding 1）。

    同时验证生命周期收敛的另一半：应用关闭（lifespan shutdown）时必须真的
    关闭 checkpointer 的连接，而不是关错了 conn（effect 层连接）或者根本没关
    （review Finding 2）——TestClient 退出 `with` 块会触发 FastAPI 的 lifespan
    shutdown，之后再对捕获到的 checkpointer 连接执行操作应该报
    sqlite3.ProgrammingError（"Cannot operate on a closed database"）。
    """
    import json
    from dataclasses import dataclass

    from fastapi.testclient import TestClient

    from app.llm.gateway import LLMGateway
    from app.web import server as server_module
    from app.web.server import create_app

    @dataclass
    class FakeMessage:
        content: str

    @dataclass
    class FakeChoice:
        message: FakeMessage

    @dataclass
    class FakeResponse:
        choices: list
        usage: object = None

    class FakeChatCompletions:
        def __init__(self, responses):
            self._responses = list(responses)

        def create(self, **kwargs):
            content = self._responses.pop(0)
            return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])

    class FakeChat:
        def __init__(self, responses):
            self.completions = FakeChatCompletions(responses)

    class FakeOpenAIClient:
        def __init__(self, responses):
            self.chat = FakeChat(responses)

    response = json.dumps(
        {"is_job_related": True, "questions": ["是否涉及 AUTOSAR？"], "profile_patch": {}}
    )
    db_path = str(tmp_path / "lifecycle.db")

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=FakeOpenAIClient([response, response]),
        )

    real_build_intake_graph = server_module.build_intake_graph
    call_count = {"n": 0}
    built_graphs = []

    def counting_build_intake_graph(*args, **kwargs):
        call_count["n"] += 1
        graph = real_build_intake_graph(*args, **kwargs)
        built_graphs.append(graph)
        return graph

    monkeypatch.setattr(server_module, "build_intake_graph", counting_build_intake_graph)

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path="")

    assert call_count["n"] == 1, "build_intake_graph 应该只在 create_app() 启动时被调用一次"
    checkpointer_conn = built_graphs[0].checkpointer.conn

    with TestClient(app) as client:
        r1 = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
        job_id = r1.json()["job_id"]
        r2 = client.post(f"/api/jobs/{job_id}/reply", json={"message": "AUTOSAR CP"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert call_count["n"] == 1, (
        "build_intake_graph 在两次请求之间被重新调用了，说明 graph 构造没有真正"
        "收敛到应用启动阶段（回归成了每请求重建，独立连接会每请求泄漏一个）"
    )

    # TestClient 退出 with 块时已经触发了 lifespan shutdown；此刻应该已经关闭
    # 的是 checkpointer 的独立连接，而不是 conn（effect 层连接）或者压根没关闭
    # 任何连接——两种都是方向 A 这次修复本应防止的错误（review Finding 2）。
    with pytest.raises(sqlite3.ProgrammingError):
        checkpointer_conn.execute("SELECT 1")


class _CrashAfterEffectLogInsertConnection(sqlite3.Connection):
    """
    在"紧跟 INSERT INTO effect_log 之后的下一次 commit()"上模拟崩溃——
    这一次 commit() 在 idempotency.py 里就是 effect 层唯一所有者自己的
    那次提交，与 checkpointer 在（分离后）自己的连接上提交了多少次无关，
    因为这个连接只会被 effect 层 touch。只崩溃一次（crashed_once），避免
    影响后续调用。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._arm_next_commit = False
        self.crashed_once = False

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("INSERT INTO EFFECT_LOG"):
            self._arm_next_commit = True
        return super().execute(sql, *args, **kwargs)

    def commit(self):
        if self._arm_next_commit and not self.crashed_once:
            self._arm_next_commit = False
            self.crashed_once = True
            raise RuntimeError("simulated crash exactly before durable commit")
        return super().commit()


def _open_crash_after_effect_log_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path, check_same_thread=False, factory=_CrashAfterEffectLogInsertConnection
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def test_effect_survives_crash_and_replays_exactly_once_with_checkpointer_attached(tmp_path):
    """
    Requirement「幂等键与业务写入原子提交在事务中断后仍然成立」，Scenario
    「强制中断后重放验证幂等性」，通过真实 build_intake_graph()（已修复，
    checkpointer 用独立连接）走一次完整的 compute→persist→deliver 流程，
    在 effect 层自己即将提交的那一刻强制中断，随后模拟"进程重启、编排引擎
    从该节点开头重新执行"（工程铁律1），断言重放后业务写入与 effect_log
    记录恰好各一份，不多不少。
    """
    import json
    from dataclasses import dataclass

    from app.channels.web_channel import WebChannel
    from app.graph.build import build_intake_graph
    from app.llm.gateway import LLMGateway

    @dataclass
    class FakeMessage:
        content: str

    @dataclass
    class FakeChoice:
        message: FakeMessage

    @dataclass
    class FakeResponse:
        choices: list
        usage: object = None

    class FakeChatCompletions:
        def __init__(self, responses):
            self._responses = list(responses)

        def create(self, **kwargs):
            content = self._responses.pop(0)
            return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])

    class FakeChat:
        def __init__(self, responses):
            self.completions = FakeChatCompletions(responses)

    class FakeOpenAIClient:
        def __init__(self, responses):
            self.chat = FakeChat(responses)

    db_path = str(tmp_path / "crash_replay.db")
    conn = _open_crash_after_effect_log_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    response = json.dumps(
        {
            "is_job_related": True,
            "questions": ["是否涉及 AUTOSAR？"],
            "profile_patch": {"job_title": "嵌入式软件工程师"},
        }
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient([response, response]),
    )
    channel = WebChannel(conn)
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)
    config = {"configurable": {"thread_id": "job1"}}

    initial_state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    with pytest.raises(RuntimeError, match="simulated crash"):
        graph.invoke(initial_state, config=config)

    graph.checkpointer.conn.close()
    conn.close()  # 模拟进程真的死了

    # 模拟进程重启：全新的、不带崩溃拦截的连接，与生产代码路径一致。
    fresh_conn = get_connection(db_path)
    fresh_channel = WebChannel(fresh_conn)
    fresh_gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient([response, response]),
    )
    fresh_graph = build_intake_graph(
        db_path, gateway=fresh_gateway, conn=fresh_conn, channel=fresh_channel
    )

    # 重放：LangGraph 恢复时节点从头整个重跑——同一 thread_id、同一份原始
    # 输入再 invoke 一次。
    fresh_graph.invoke(initial_state, config=config)

    profile_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    outbox_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    persist_effect_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_persist_draft'"
    ).fetchone()[0]
    deliver_effect_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_deliver_message'"
    ).fetchone()[0]

    assert profile_count == 1, "崩溃后重放不应产生重复的 job_profile 草案行"
    assert outbox_count == 1, "崩溃后重放不应二次投递消息到 outbox"
    assert persist_effect_count == 1
    assert deliver_effect_count == 1

    fresh_graph.checkpointer.conn.close()
