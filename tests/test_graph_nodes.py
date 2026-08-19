import json
import sqlite3
from dataclasses import dataclass

import pytest

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

    assert [q["text"] for q in new_state["pending_questions"]] == ["是否涉及 AUTOSAR？"]
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
    assert [q["text"] for q in latest.payload["questions"]] == ["是否涉及 AUTOSAR？"]


def test_compute_intake_turn_emits_serializable_structured_questions():
    """
    state 会被 SqliteSaver 序列化进 checkpoint，所以 pending_questions 必须是
    纯 dict——放 dataclass 会让"重放后类型变了"成为只在恢复路径上炸的故障。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {
                            "text": "要哪个 ASIL 等级？",
                            "field": "functional_safety",
                            "options": ["ASIL-B", "ASIL-D", "无"],
                        }
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做功能安全的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    question = new_state["pending_questions"][0]
    assert isinstance(question, dict)
    assert question["question_id"] == "functional_safety"
    assert question["options"] == ["ASIL-B", "ASIL-D", "无"]
    assert question["is_reask"] is False
    # 整份 state 必须能 json 序列化，否则 checkpoint 写入会在运行时才炸
    json.dumps(new_state["pending_questions"], ensure_ascii=False)


def test_compute_intake_turn_carries_llm_latency():
    gateway = make_gateway(
        [
            json.dumps(
                {"is_job_related": True, "questions": [], "profile_patch": {"headcount": 2}}
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要两个人"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    assert new_state["llm_latency_ms"] >= 0


def test_compute_intake_turn_passes_through_turn_started_at():
    """轮次起始时刻由 HTTP 层打（那才是"用户开始等"的时刻），节点不许改写它。"""
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个人"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
        "turn_started_at": "2026-08-19 01:02:03",
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    assert new_state["turn_started_at"] == "2026-08-19 01:02:03"


def test_assistant_history_text_equals_rendered_questions():
    """
    history 里的 assistant 文本必须来自唯一的渲染函数
    （design.md 决策 1「代价」）：这里和下发给通道的文本一旦分叉，
    _repeats_earlier_assistant_turn 就在比对一个从未下发过的字符串。
    """
    from app.agents.intake_question import IntakeQuestion, render_questions_text

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "要哪个 ASIL 等级？", "field": "functional_safety"},
                        {"text": "招几个人？", "field": "headcount"},
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做功能安全的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    expected = render_questions_text(
        [IntakeQuestion.from_payload(q) for q in new_state["pending_questions"]]
    )
    assert new_state["history"][-1] == {"role": "assistant", "content": expected}
