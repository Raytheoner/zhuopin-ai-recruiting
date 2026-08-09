import json
from dataclasses import dataclass

from app.channels.base import OutboundMessage
from app.channels.web_channel import WebChannel
from app.graph.nodes import (
    compute_intake_turn,
    effect_confirm_profile,
    effect_deliver_message,
    effect_persist_draft,
)
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema


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


def make_gateway(responses):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses),
    )


def test_compute_intake_turn_updates_state():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {"job_title": "嵌入式软件工程师"},
                }
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    assert new_state["pending_questions"] == ["是否涉及 AUTOSAR？"]
    assert new_state["profile_patch_accumulated"]["job_title"] == "嵌入式软件工程师"
    assert new_state["round_count"] == 1
    assert new_state["is_complete"] is False


def test_effect_persist_draft_writes_job_profile_row(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    effect_persist_draft(
        conn,
        thread_id="job1",
        business_key="1",
        state={"profile_patch_accumulated": {"job_title": "x"}, "unspecified_fields": []},
    )

    row = conn.execute(
        "SELECT status, profile_json FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == "drafting"
    assert json.loads(row[1])["job_title"] == "x"


def test_effect_deliver_message_calls_channel(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)
    message = OutboundMessage(type="question", payload={"questions": ["Q1"]})

    effect_deliver_message(
        conn, thread_id="job1", business_key="hash1", channel=channel, message=message
    )

    assert channel.latest("job1").payload == {"questions": ["Q1"]}


def test_effect_confirm_profile_marks_approved(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'job1', 1, 'drafting', '{}')"
    )
    conn.commit()

    effect_confirm_profile(
        conn, thread_id="job1", business_key="1", profile_dict={"job_title": "x"}
    )

    job_status = conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0]
    profile_status = conn.execute(
        "SELECT status FROM job_profile WHERE job_id='job1' ORDER BY version DESC LIMIT 1"
    ).fetchone()[0]
    assert job_status == "approved"
    assert profile_status == "approved"


def test_build_intake_graph_runs_end_to_end(tmp_path):
    from app.channels.web_channel import WebChannel
    from app.graph.build import build_intake_graph
    from app.storage.db import get_connection, init_schema

    db_path = str(tmp_path / "graph.db")
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {"job_title": "嵌入式软件工程师"},
                }
            )
        ]
    )
    channel = WebChannel(conn)
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

    initial_state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }
    graph.invoke(initial_state, config={"configurable": {"thread_id": "job1"}})

    latest = channel.latest("job1")
    assert latest.type == "question"
    assert latest.payload["questions"] == ["是否涉及 AUTOSAR？"]
