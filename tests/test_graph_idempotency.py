import json

from app.graph.nodes import effect_confirm_profile, effect_persist_draft
from app.storage.db import get_connection, init_schema


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
