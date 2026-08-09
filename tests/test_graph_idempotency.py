import json
import sqlite3

import pytest

from app.channels.base import OutboundMessage
from app.channels.web_channel import WebChannel
from app.graph.nodes import effect_confirm_profile, effect_deliver_message, effect_persist_draft
from app.storage.db import get_connection, init_schema


class _CrashableConnection(sqlite3.Connection):
    """
    sqlite3.Connection 是 C 扩展类型，实例上不能直接 monkeypatch
    conn.commit = ...（会报 "attribute 'commit' is read-only"）。用子类在
    Python 层覆写 commit()，并用一个可以从外部按需翻转的标志位控制"下一次
    commit() 调用是否要模拟崩溃"——这样测试的建表、插种子数据等准备阶段可以
    正常 commit，只在我们关心的那一次 commit() 上模拟"进程恰好在这次真正
    落盘之前崩溃"。因为拦截的是 Python 层 commit() 调用本身，真正的 SQLite
    提交从未发生，效果等价于进程在这个时间点被杀死：本连接这次事务里排队的
    所有写入都不会落盘。
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


class _CommitCountingConnection(sqlite3.Connection):
    """统计 commit() 被调用的次数，不改变其行为。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0

    def commit(self):
        self.commit_count += 1
        return super().commit()


def _open_commit_counting_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, factory=_CommitCountingConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_effect_persist_draft_replay_does_not_duplicate_rows(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    state = {"profile_patch_accumulated": {"job_title": "x"}, "unspecified_fields": []}

    effect_persist_draft(conn, thread_id="job1", business_key="1", state=state)
    effect_persist_draft(conn, thread_id="job1", business_key="1", state=state)  # 模拟节点重跑

    count = conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    assert count == 1


def test_effect_confirm_profile_replay_is_noop_second_time(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'job1', 1, 'drafting', '{}')"
    )
    conn.commit()

    effect_confirm_profile(conn, thread_id="job1", business_key="1", profile_dict={})
    # 第二次调用命中 effect_log，函数体不应再执行（即使这里执行了也是同样结果，
    # 但关键断言是 effect_log 只有一条记录 —— 这是幂等键生效的证据）
    effect_confirm_profile(conn, thread_id="job1", business_key="1", profile_dict={})

    effect_log_count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_confirm_profile'"
    ).fetchone()[0]
    assert effect_log_count == 1


def test_graph_replay_from_scratch_does_not_duplicate_effects(tmp_path):
    """
    真正经过编译好的图 + 真实 SqliteSaver + 真实 sqlite 的重放测试，而不是直接
    调用两次 effect_* 函数（那只是重新验证 Task 3 的装饰器本身）。

    工程铁律1: "LangGraph 恢复时节点从头整个重跑"。本图没有用 interrupt，所以对
    这个架构而言，"恢复"落地为的真实场景是：调用方（HTTP handler / 外部重试逻辑）
    因超时、进程重启等原因，对同一个 thread_id 用同一份输入再 invoke() 一次，
    LangGraph 会从 entry point 把 compute_intake_turn → effect_persist_draft →
    effect_deliver_message 整条链路重新跑一遍。此测试直接调用 build_intake_graph()
    返回的编译图两次，断言 effect_* 节点在第二次重跑时不会重复写库 / 重复投递。
    """
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

    db_path = str(tmp_path / "replay.db")
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    # temperature=0（铁律5）意味着同一份输入重放时，供应商侧应返回同样的内容；
    # 这里用两条完全相同的响应模拟"重放时 LLM 结果不变"这个前提。
    same_response = json.dumps(
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
        client=FakeOpenAIClient([same_response, same_response]),
    )
    channel = WebChannel(conn)
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

    initial_state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }
    config = {"configurable": {"thread_id": "job1"}}

    graph.invoke(initial_state, config=config)
    # 模拟"节点从头整个重跑"：同一 thread_id、同一份原始输入再 invoke 一次
    # （例如调用方超时后重试整个请求，并不知道第一次其实已经成功）。
    graph.invoke(initial_state, config=config)

    profile_count = conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    outbox_count = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    persist_effect_count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_persist_draft' AND thread_id='job1'"
    ).fetchone()[0]
    deliver_effect_count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_deliver_message' AND thread_id='job1'"
    ).fetchone()[0]

    assert profile_count == 1, "重放不应产生第二条 job_profile 草案行"
    assert outbox_count == 1, "重放不应二次投递消息到 outbox"
    assert persist_effect_count == 1
    assert deliver_effect_count == 1


def test_effect_persist_draft_crash_before_decorator_commit_leaves_nothing_durable(tmp_path):
    """
    回归测试（修复 review 发现的 Critical bug）：effect_persist_draft 之前在函数体内部
    自己 conn.commit() 一次，idempotent_effect 装饰器随后又 commit 一次——如果进程恰好
    在"函数体自己的 commit"和"装饰器自己的 commit"之间崩溃，job_profile 行已经落盘但
    effect_log 里没有对应记录；重放时用同一个 business_key 重新执行，会撞上已存在的
    主键（UNIQUE constraint failed），永久卡死，而不是干净地重试。

    修掉之后（effect_persist_draft 不再自己 commit），整个函数体的写入和装饰器追加的
    effect_log 行落在同一个事务里、只由装饰器提交一次。本测试直接模拟"进程在这唯一一次
    commit() 真正落盘之前崩溃"：用另一个指向同一个数据库文件的全新连接（模拟进程重启后
    重新连接）验证 job_profile 和 effect_log 都没有任何行落盘，然后用这个干净连接重放，
    断言重放能成功且最终只有 1 行——而不是 0 行卡死或 2 行重复。
    """
    db_path = str(tmp_path / "test.db")
    conn = _open_crashable_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    state = {"profile_patch_accumulated": {"job_title": "x"}, "unspecified_fields": []}

    # 只在“装饰器自己触发的那一次 commit()”上模拟崩溃——effect_persist_draft
    # 函数体本身修复后不再调用 commit()，所以这唯一一次 commit() 调用就是
    # idempotent_effect 包装器里的那一次。
    conn.crash_next_commit = True

    with pytest.raises(RuntimeError, match="simulated crash"):
        effect_persist_draft(conn, thread_id="job1", business_key="1", state=state)

    conn.close()  # 模拟进程真的死了：从未真正落盘的事务随连接一起消失

    # 模拟进程重启：开一个全新连接指向同一个数据库文件。
    fresh_conn = get_connection(db_path)
    profile_count_after_crash = fresh_conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    effect_log_count_after_crash = fresh_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_persist_draft'"
    ).fetchone()[0]
    assert profile_count_after_crash == 0, "崩溃点之前不应有任何行真正落盘"
    assert effect_log_count_after_crash == 0

    # 重放：用干净连接重新执行同一个 business_key，必须干净成功，不应报
    # UNIQUE constraint failed，最终也不应该出现重复行。
    effect_persist_draft(fresh_conn, thread_id="job1", business_key="1", state=state)

    final_profile_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    assert final_profile_count == 1


def test_effect_deliver_message_crash_before_decorator_commit_leaves_nothing_durable(tmp_path):
    """
    同上一测试，但覆盖 review 里指出的更隐蔽的那个后果：effect_deliver_message 背后的
    WebChannel.deliver 之前自己 commit 一次。outbox 表没有唯一约束，所以修复前这个
    "崩溃在两次 commit 之间"的场景重放时不会报错，而是静默地在 outbox 里多插入一行——
    也就是消息被静默重复投递给候选人，这正是工程铁律1要防止的最坏情形（不是报错卡死，
    而是悄悄错误地多做一次副作用）。

    验证方式与上一测试相同：模拟崩溃 → 换新连接确认崩溃前什么都没落盘 → 重放 → 断言
    outbox 最终只有 1 行，不是 2 行。
    """
    db_path = str(tmp_path / "test.db")
    conn = _open_crashable_connection(db_path)
    init_schema(conn)
    conn.commit()

    channel = WebChannel(conn)
    message = OutboundMessage(type="question", payload={"questions": ["Q1"]})

    conn.crash_next_commit = True

    with pytest.raises(RuntimeError, match="simulated crash"):
        effect_deliver_message(
            conn, thread_id="job1", business_key="hash1", channel=channel, message=message
        )

    conn.close()

    fresh_conn = get_connection(db_path)
    outbox_count_after_crash = fresh_conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    effect_log_count_after_crash = fresh_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_deliver_message'"
    ).fetchone()[0]
    assert outbox_count_after_crash == 0, "崩溃点之前不应有任何行真正落盘"
    assert effect_log_count_after_crash == 0

    fresh_channel = WebChannel(fresh_conn)
    effect_deliver_message(
        fresh_conn, thread_id="job1", business_key="hash1", channel=fresh_channel, message=message
    )

    final_outbox_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    assert final_outbox_count == 1, "重放不应静默产生第二条 outbox 消息（重复投递）"


def test_effect_persist_draft_commits_exactly_once_per_call(tmp_path):
    """
    直接证明这次修复的根因已经消除：effect_persist_draft 不应该在函数体内部自己
    conn.commit() 一次、再让 idempotent_effect 装饰器额外 commit 一次——这两次
    commit 之间的窗口正是 review 发现的 Critical bug 的成因。用一个统计
    commit() 调用次数的连接子类跑一次完整成功调用，断言 commit() 总共只被
    调用了 1 次（由装饰器统一提交）。

    这个测试在修复前的代码上跑会失败（内部 commit 一次 + 装饰器再 commit
    一次 = 2 次，而不是 1 次）——已经手动在修复前的版本上验证过，见本任务
    report 的 fix 记录。
    """
    setup_conn = get_connection(str(tmp_path / "test.db"))
    init_schema(setup_conn)
    setup_conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    setup_conn.commit()
    setup_conn.close()

    conn = _open_commit_counting_connection(str(tmp_path / "test.db"))
    state = {"profile_patch_accumulated": {"job_title": "x"}, "unspecified_fields": []}

    effect_persist_draft(conn, thread_id="job1", business_key="1", state=state)

    assert conn.commit_count == 1, (
        "effect_persist_draft 一次成功调用应该只触发 1 次 conn.commit()"
        f"（由 idempotent_effect 装饰器统一提交），实际触发了 {conn.commit_count} 次"
    )


def test_effect_confirm_profile_commits_exactly_once_per_call(tmp_path):
    """同上，覆盖 effect_confirm_profile。"""
    setup_conn = get_connection(str(tmp_path / "test.db"))
    init_schema(setup_conn)
    setup_conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    setup_conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'job1', 1, 'drafting', '{}')"
    )
    setup_conn.commit()
    setup_conn.close()

    conn = _open_commit_counting_connection(str(tmp_path / "test.db"))

    effect_confirm_profile(conn, thread_id="job1", business_key="1", profile_dict={})

    assert conn.commit_count == 1, (
        "effect_confirm_profile 一次成功调用应该只触发 1 次 conn.commit()"
        f"（由 idempotent_effect 装饰器统一提交），实际触发了 {conn.commit_count} 次"
    )


def test_effect_deliver_message_commits_exactly_once_per_call(tmp_path):
    """同上，覆盖 effect_deliver_message（背后是 WebChannel.deliver）。"""
    setup_conn = get_connection(str(tmp_path / "test.db"))
    init_schema(setup_conn)
    setup_conn.commit()
    setup_conn.close()

    conn = _open_commit_counting_connection(str(tmp_path / "test.db"))
    channel = WebChannel(conn)
    message = OutboundMessage(type="question", payload={"questions": ["Q1"]})

    effect_deliver_message(
        conn, thread_id="job1", business_key="hash1", channel=channel, message=message
    )

    assert conn.commit_count == 1, (
        "effect_deliver_message 一次成功调用应该只触发 1 次 conn.commit()"
        f"（由 idempotent_effect 装饰器统一提交，WebChannel.deliver 自己不应该 commit），"
        f"实际触发了 {conn.commit_count} 次"
    )


def test_deliver_business_key_distinguishes_rounds_with_identical_content(tmp_path):
    """
    Important 发现 #2 的修复验证：message_business_key() 本身只是 payload 内容的哈希，
    如果两轮问出的问题恰好完全相同（例如用户没有回答，LLM 在下一轮原样重问同一个
    问题），纯内容哈希会让第二轮这次真实、合法的投递被误判成第一轮的重放而静默跳过——
    候选人再也收不到追问。app/graph/build.py 的 _deliver_node 现在把 round_count
    拼进 business_key 前缀，本测试验证：两次不同轮次、payload 内容完全相同时，
    outbox 里应该有 2 条消息，而不是被去重成 1 条。
    """
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

    db_path = str(tmp_path / "rounds.db")
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    # 两轮 LLM 都问一模一样的问题（模拟用户没回答、LLM 原样重问）。
    same_question_response = json.dumps(
        {
            "is_job_related": True,
            "questions": ["是否涉及 AUTOSAR？"],
            "profile_patch": {},
        }
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient([same_question_response, same_question_response]),
    )
    channel = WebChannel(conn)
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)
    config = {"configurable": {"thread_id": "job1"}}

    # 第 1 轮：round_count 从 0 开始。
    graph.invoke(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
        },
        config=config,
    )
    # 第 2 轮：不同的 round_count（用户还是没回答，模拟又发了一条新消息触发下一轮）。
    graph.invoke(
        {
            "job_id": "job1",
            "history": [
                {"role": "user", "content": "要个做嵌入式开发的"},
                {"role": "user", "content": "还有别的要求吗"},
            ],
            "round_count": 1,
            "profile_patch_accumulated": {},
        },
        config=config,
    )

    outbox_count = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    assert outbox_count == 2, (
        "两轮内容相同的问题都应该真实投递，不应该被 business_key 的内容哈希"
        f"误判成同一次重放而去重，实际 outbox 只有 {outbox_count} 条"
    )
