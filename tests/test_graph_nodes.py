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
from app.schemas.job_profile import JobProfile
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
    model: str | None = None


class FakeChatCompletions:
    def __init__(self, responses, response_model: str | None = None):
        self._responses = list(responses)
        self._response_model = response_model

    def create(self, **kwargs):
        content = self._responses.pop(0)
        return FakeResponse(
            choices=[FakeChoice(message=FakeMessage(content=content))],
            model=self._response_model,
        )


class FakeChat:
    def __init__(self, responses, response_model: str | None = None):
        self.completions = FakeChatCompletions(responses, response_model=response_model)


class FakeOpenAIClient:
    def __init__(self, responses, response_model: str | None = None):
        self.chat = FakeChat(responses, response_model=response_model)


def make_gateway(responses, response_model: str | None = None):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses, response_model=response_model),
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
    assert latest.payload["questions_text"] == "是否涉及 AUTOSAR？"


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


def test_effect_persist_draft_writes_turn_timing_in_the_same_row(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    effect_persist_draft(
        conn,
        thread_id="job1",
        business_key="0",
        state={
            "profile_patch_accumulated": {"job_title": "x"},
            "unspecified_fields": [],
            "turn_started_at": "2026-08-19 01:02:03",
            "llm_latency_ms": 8123.5,
        },
    )

    row = conn.execute(
        "SELECT turn_started_at, llm_latency_ms, created_at FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == "2026-08-19 01:02:03"
    assert row[1] == 8123.5
    # 轮次结束时刻沿用 created_at，不另加列；两者同格式所以可直接比较
    assert row[2] >= row[0]


def test_timing_does_not_exist_when_profile_write_fails(tmp_path):
    """
    intake-turn-observability「时序与画像同生共死」+ 铁律 1 的不变式：
    业务写失败时，effect_log 也不能留下记录——否则重放会判定"已执行"而静默
    跳过，这正是 .51 现网 2026-08-10/08-12 各丢一轮 outbox 的机理
    （docs/findings/2026-08-13-sqlite-事务归属冲突.md §8.5）。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    # 不插 job 行：job_profile.job_id 的外键（PRAGMA foreign_keys=ON）会让
    # INSERT 直接失败，模拟"这一轮的画像写不进去"
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        effect_persist_draft(
            conn,
            thread_id="ghost-job",
            business_key="0",
            state={
                "profile_patch_accumulated": {"job_title": "x"},
                "unspecified_fields": [],
                "turn_started_at": "2026-08-19 01:02:03",
                "llm_latency_ms": 8123.5,
            },
        )

    profiles = conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='ghost-job'"
    ).fetchone()[0]
    effects = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id='ghost-job'"
    ).fetchone()[0]
    assert profiles == 0
    assert effects == 0  # 画像与幂等记录按 thread 恒等，都是 0


def test_persisted_latency_covers_llm_retries(tmp_path, monkeypatch):
    """intake-turn-observability「重试计入耗时」的端到端版本：
    模型第一次返回非法 JSON、第二次成功，落库的耗时必须覆盖两次。"""
    from app.llm import gateway as gateway_module

    ticks = iter([0.0, 1.5, 10.0, 12.25])
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: next(ticks))

    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            "这不是 JSON",
            json.dumps(
                {"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}}
            ),
        ]
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要一个人"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-19 01:02:03",
        },
        gateway=gateway,
    )

    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    latency = conn.execute(
        "SELECT llm_latency_ms FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    assert latency == pytest.approx(3750.0)  # 1500 + 2250，不是只记最后一次


def test_system_time_and_user_think_time_are_separable(tmp_path):
    """
    intake-turn-observability「系统延迟与用户思考时长可分离」。
    这条测试同时是**统计口径的可执行文档**：下面这段 SQL 就是运维查"业务经理
    到底等了多久"要跑的东西。修复前只有 created_at（轮次结束时刻），相邻两轮
    的间隔把 LLM 耗时和用户打字时间混在一起，问不出"单轮等待感受"。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    # 直接写两行：本测试验证的是报表口径能不能算出来，不是 effect 的行为
    conn.executemany(
        "INSERT INTO job_profile "
        "(id, job_id, version, status, profile_json, turn_started_at, created_at, llm_latency_ms) "
        "VALUES (?, 'job1', ?, 'drafting', '{}', ?, ?, ?)",
        [
            ("job1-v1", 1, "2026-08-18 09:00:00", "2026-08-18 09:00:12", 12000.0),
            ("job1-v2", 2, "2026-08-18 09:01:30", "2026-08-18 09:01:45", 15000.0),
        ],
    )
    conn.commit()

    rows = conn.execute(
        """
        SELECT version,
               CAST(strftime('%s', created_at) AS INTEGER)
                 - CAST(strftime('%s', turn_started_at) AS INTEGER) AS system_seconds,
               CAST(strftime('%s', turn_started_at) AS INTEGER)
                 - LAG(CAST(strftime('%s', created_at) AS INTEGER)) OVER (ORDER BY version)
                 AS user_seconds
        FROM job_profile WHERE job_id='job1' ORDER BY version
        """
    ).fetchall()

    assert rows[0][1] == 12 and rows[0][2] is None  # 第一轮没有"上一轮"
    assert rows[1][1] == 15  # 系统处理耗时
    assert rows[1][2] == 78  # 用户思考与输入耗时，与系统耗时分开

    total = conn.execute(
        "SELECT CAST(strftime('%s', MAX(created_at)) AS INTEGER) "
        "- CAST(strftime('%s', MIN(turn_started_at)) AS INTEGER) FROM job_profile "
        "WHERE job_id='job1'"
    ).fetchone()[0]
    assert total == 105


def test_timing_trace_carries_no_model_identity_of_its_own(tmp_path):
    """
    intake-turn-observability「时序留痕不承担审计职责」：**时序那两列**里只有
    时间与耗时，不含模型标识。

    2026-08-25 更新（第 7 章 intake-field-grounding 上岗）：llm_response_model
    这一列从此**有值**了，但它是溯源归因写的，不是时序留痕写的——原断言
    `row[0] is None` 记录的是"第 7 章还没做"这个事实，不是一条永久契约
    （单元 A 在原注释里就写明了"它归第 7 章"）。这里改为守住真正的契约：
    时序两列不因模型标识而改变，模型标识来自 result.llm_response_model 这条
    独立通道。**这不是把测试改松，是把它改准。**
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}})],
        response_model="deepseek-chat-20260801",
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要一个人"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-25 01:02:03",
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT turn_started_at, llm_latency_ms, llm_response_model "
        "FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == "2026-08-25 01:02:03"
    assert row[1] is not None
    # 模型标识经溯源通道落库，与时序两列互不干涉
    assert row[2] == "deepseek-chat-20260801"
    assert state["llm_response_model"] == "deepseek-chat-20260801"


def test_grounding_columns_land_in_same_insert(tmp_path):
    """
    tasks 7.5 / 7.9 + spec「来源与画像同生共死」：未溯源清单、写入字段清单、
    响应模型标识与画像草案在**同一条 INSERT** 里落库，不新增 effect 节点、
    business_key 不变。effect_log 条数与 job_profile 行数按 thread 恒等（铁律 1）。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {
                        "headcount": {"value": 2, "source_quote": "要两个人", "source_turn": 1},
                        "mcu_family": {
                            "value": ["TriCore"],
                            "source_quote": "我们一直用 TriCore",
                            "source_turn": 1,
                        },
                    },
                }
            )
        ],
        response_model="deepseek-chat-20260801",
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要两个人"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-25 01:02:03",
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT ungrounded_fields, written_fields, llm_response_model, profile_json "
        "FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert json.loads(row[0]) == ["mcu_family"]          # 引用是编的 → 未溯源
    assert sorted(json.loads(row[1])) == ["headcount", "mcu_family"]
    # 铁律 5：记的是**响应返回的**标识，不是配置里的别名 deepseek-chat-241226
    assert row[2] == "deepseek-chat-20260801"
    assert row[2] != "deepseek-chat-241226"

    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM job_profile WHERE job_id='job1'), "
        "(SELECT COUNT(*) FROM effect_log WHERE thread_id='job1' "
        " AND effect_key LIKE '%effect_persist_draft%')"
    ).fetchone()
    assert counts[0] == counts[1] == 1


def test_profile_json_stays_flat_end_to_end(tmp_path):
    """
    Global Constraints 第一条的**终点判据**：落库后的 profile_json 反序列化出来
    必须是裸值，且能直接喂进 JobProfile.model_validate（headcount 收到 int 而
    不是 dict）。这条炸了就是 POST /confirm 的 422，以及 jd_agent 读到一堆 dict。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {
                        "job_title": {"value": "嵌入式工程师", "source_quote": "嵌入式工程师", "source_turn": 1},
                        "department": {"value": "研发部", "source_quote": "研发部", "source_turn": 1},
                        "headcount": {"value": 2, "source_quote": "两个", "source_turn": 1},
                        "education_requirement": {"value": "本科", "source_quote": "本科", "source_turn": 1},
                        "experience_years": {"value": "3-5年", "source_quote": "3-5年", "source_turn": 1},
                    },
                }
            )
        ]
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [
                {"role": "user", "content": "研发部要两个嵌入式工程师，本科，3-5年经验"}
            ],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-25 01:02:03",
        },
        gateway=gateway,
    )
    # 累积态本身也必须是裸值——它是下一轮 prompt 的输入，信封会污染下一轮
    assert state["profile_patch_accumulated"]["headcount"] == 2

    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)
    stored = json.loads(
        conn.execute("SELECT profile_json FROM job_profile WHERE job_id='job1'").fetchone()[0]
    )
    assert stored["headcount"] == 2
    JobProfile.model_validate(stored)  # 不抛 = POST /confirm 那一步不会 422


def _job1_conn(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()
    return conn


def test_persist_writes_is_productive_and_asked_questions_in_the_same_insert(tmp_path):
    """
    这一轮的画像、这一轮有没有产出、这一轮问了什么，是同一轮的三份事实。
    分开写就会出现"画像有这一轮、台账没这一轮"，而追问预算正是按后两列取数的。
    """
    conn = _job1_conn(tmp_path)
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要个嵌入式工程师"}],
            "round_count": 0,
            "productive_round_count": 0,
            "profile_patch_accumulated": {},
            "asked_question_ids_before": [],
            "previous_questions": [],
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT is_productive, asked_questions FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == 1
    assert [item["question_id"] for item in json.loads(row[1])] == ["headcount"]


def test_persist_records_zero_productive_for_an_idle_turn(tmp_path):
    conn = _job1_conn(tmp_path)
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "嗯"}],
            "round_count": 2,
            "productive_round_count": 1,
            "profile_patch_accumulated": {"job_title": "嵌入式工程师"},
            "asked_question_ids_before": ["headcount"],
            "previous_questions": [],
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="2", state=state)

    assert conn.execute(
        "SELECT is_productive FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0] == 0


def test_persist_records_off_topic_turn_as_not_productive_but_asks_guidance(tmp_path):
    """
    离题轮：profile_patch 恒为 {}，没有任何产出，不能落成 is_productive=1
    （否则连续几条离题消息就能耗光 MAX_ROUNDS）。但引导语确实下发给了用户，
    已问台账必须如实记它，否则第 5 章的重问追踪会漏掉这一条
    （app/agents/intake_agent.py 的 is_job_related=False 分支，Task 4 review 发现1）。
    """
    conn = _job1_conn(tmp_path)
    gateway = make_gateway(
        [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "今天天气不错"}],
            "round_count": 0,
            "productive_round_count": 0,
            "profile_patch_accumulated": {},
            "asked_question_ids_before": [],
            "previous_questions": [],
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT is_productive, asked_questions FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == 0
    ledger = json.loads(row[1])
    assert len(ledger) == 1
    assert ledger[0]["text"]  # 引导语文本真的进了台账，不是空列表


# --- 推导值与模型自称值分两列落库（tasks 6.2 / 6.5） -------------------------


def test_persist_draft_splits_derived_and_model_claimed_into_two_columns(tmp_path):
    """
    tasks 6.2/6.5：推导值进 derived_unspecified_fields（真源），模型自称值留在
    unspecified_fields（对照）。⛔ 两列同值等于毁掉 8.1 回放对比的对照组。
    """
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 't', 'drafting')")

    effect_persist_draft(
        conn,
        thread_id="j1",
        business_key="0",
        state={
            "profile_patch_accumulated": {"job_title": "嵌入式软件工程师"},
            "unspecified_fields": ["toolchain", "mcu_family"],
            "model_claimed_unspecified_fields": ["functional_safety"],
            "history": [],
        },
    )

    row = conn.execute(
        "SELECT derived_unspecified_fields, unspecified_fields FROM job_profile WHERE job_id='j1'"
    ).fetchone()

    assert json.loads(row[0]) == ["toolchain", "mcu_family"]
    assert json.loads(row[1]) == ["functional_safety"]


def test_persist_draft_tolerates_state_without_model_claimed_key(tmp_path):
    """重放/老 checkpoint 里没有这个新键时按空列表处理，不能 KeyError。"""
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j2', 't', 'drafting')")

    effect_persist_draft(
        conn,
        thread_id="j2",
        business_key="0",
        state={"profile_patch_accumulated": {}, "unspecified_fields": [], "history": []},
    )

    row = conn.execute(
        "SELECT unspecified_fields FROM job_profile WHERE job_id='j2'"
    ).fetchone()
    assert json.loads(row[0]) == []


def test_confirm_profile_persists_acknowledgement_in_the_same_write(tmp_path):
    """
    变异检查补的守卫（铁律 1）：知情留痕必须由 effect_confirm_profile 自己连同
    status='approved' 一起落库。

    只从 HTTP 层断言"留痕最后查得到"是不够的——后面的 effect_generate_and_persist_jd
    会用 {**profile_dict, "_jd_text": ...} 整体重写 profile_json，把留痕又带回来。
    于是把这条 UPDATE 里的 profile_json 写入整个删掉，端到端用例依然全绿，而真实
    故障是：JD 生成失败时画像已 approved、留痕却丢了——正是铁律 1 要杜绝的
    "业务写与幂等/留痕不同生共死"。
    """
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j3', 't', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('j3-v1', 'j3', 1, 'drafting', ?)",
        (json.dumps({"job_title": "嵌入式软件工程师"}, ensure_ascii=False),),
    )

    effect_confirm_profile(
        conn,
        thread_id="j3",
        business_key="1",
        profile_dict={
            "job_title": "嵌入式软件工程师",
            "_gap_acknowledgement": {"acknowledged": True, "had_gaps": True},
        },
    )

    status, profile_json = conn.execute(
        "SELECT status, profile_json FROM job_profile WHERE job_id='j3' AND version=1"
    ).fetchone()

    assert status == "approved"
    persisted = json.loads(profile_json)
    assert persisted["_gap_acknowledgement"]["acknowledged"] is True, (
        "留痕没有跟 status='approved' 一起落库——JD 生成失败时就会丢"
    )


def test_compute_passes_the_per_round_ledger_through_to_the_agent(tmp_path):
    """
    compute_intake_turn 是 compute_* 节点：只透传，不查库（工程铁律 2）。
    按轮台账由 _run_turn 查出来放进 state，这里断言它真的到达了 agent——
    没到达的话重问标注会静默失效（不报错、不失败，只是从来不打标记）。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "再说说"}],
            "round_count": 1,
            "productive_round_count": 1,
            "profile_patch_accumulated": {"job_title": "嵌入式软件工程师"},
            "asked_question_ids_before": ["functional_safety"],
            "previous_questions": [],
            "asked_question_rounds": [
                [{"text": "功能安全等级（ASIL）上有什么要求？", "field": "functional_safety"}]
            ],
        },
        gateway=gateway,
    )

    (question,) = state["pending_questions"]
    assert question["is_reask"] is True


def test_compute_still_works_without_the_per_round_ledger_key(tmp_path):
    """老调用方（没放这个键）行为与今天逐字一致，不打重问标记。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "功能安全等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "再说说"}],
            "round_count": 1,
            "productive_round_count": 1,
            "profile_patch_accumulated": {},
            "asked_question_ids_before": ["functional_safety"],
            "previous_questions": [],
        },
        gateway=gateway,
    )

    (question,) = state["pending_questions"]
    assert question["is_reask"] is False
