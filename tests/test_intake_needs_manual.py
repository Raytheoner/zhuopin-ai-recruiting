"""WBS 2.5：校验失败重试至多 2 次，仍失败转 needs_manual，⛔ 不产出半成品。"""

import pytest

from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.manual_handoff import REASON_PROVIDER_UNAVAILABLE, REASON_SCHEMA_EXHAUSTED
from app.graph.nodes import compute_intake_turn
from app.llm.gateway import LLMProviderUnavailable, SchemaExtractionFailed
from app.storage.db import get_connection, init_schema
from app.storage.job_queries import derive_needs_manual_reasons


class ExplodingGateway:
    """extract_structured* 一律抛。网关内部的重试已经耗尽，抛出来的就是终局。"""

    def __init__(self, exc):
        self._exc = exc

    def extract_structured(self, **kwargs):
        raise self._exc

    def extract_structured_with_meta(self, **kwargs):
        raise self._exc


def _state():
    return {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 2,
        "profile_patch_accumulated": {"job_title": "嵌入式软件工程师"},
    }


def _seeded_conn(tmp_path, status="drafting"):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('job1', '待确定', ?)", (status,)
    )
    conn.commit()
    return conn


# ── compute 侧：纯函数，只置信号 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "exc, expected_code",
    [
        (SchemaExtractionFailed("3 次尝试后仍未通过 Schema 校验"), REASON_SCHEMA_EXHAUSTED),
        (LLMProviderUnavailable("主备都不可用"), REASON_PROVIDER_UNAVAILABLE),
    ],
)
def test_compute_turns_extraction_failure_into_a_needs_manual_signal(exc, expected_code):
    result = compute_intake_turn(_state(), gateway=ExplodingGateway(exc))

    assert result["needs_manual"] is True
    assert result["needs_manual_reason_code"] == expected_code
    assert result["is_complete"] is False
    assert result["is_productive"] is False
    assert result["pending_questions"] == []


def test_compute_returns_the_accumulated_profile_untouched():
    """「不产出半成品」：已采集的照旧，本轮**一个字段都不加**。"""
    state = _state()
    result = compute_intake_turn(
        state, gateway=ExplodingGateway(SchemaExtractionFailed("x"))
    )

    assert result["profile_patch_accumulated"] == {"job_title": "嵌入式软件工程师"}
    # 这一轮系统什么都没说，⛔ 不许往 history 里塞一句模型从未说过的话。
    assert result["history"] == state["history"]
    # round_count 的真源是 job_profile 行数，本轮不落行 ⇒ ⛔ 不自增。
    assert result["round_count"] == 2


# ── 图侧：两个 effect 节点 ───────────────────────────────────────────────


def _run_failing_turn(tmp_path, status="drafting", exc=None):
    conn = _seeded_conn(tmp_path, status=status)
    channel = WebChannel(conn)
    graph = build_intake_graph(
        str(tmp_path / "t.db"),
        gateway=ExplodingGateway(exc or SchemaExtractionFailed("x")),
        conn=conn,
        channel=channel,
    )
    graph.invoke(_state(), config={"configurable": {"thread_id": "job1"}})
    return conn, channel, graph


def test_failing_turn_marks_the_job_needs_manual_and_writes_no_profile_row(tmp_path):
    conn, channel, _graph = _run_failing_turn(tmp_path)

    assert conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0] == "needs_manual"
    # 「不产出半成品」的落库判据：一行都没有。
    assert conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id='job1'").fetchone()[0] == 0

    message = channel.latest("job1")
    assert message.type == "needs_manual"
    assert message.payload["reason_code"] == REASON_SCHEMA_EXHAUSTED
    assert message.payload["message"]  # ⛔ 不能是空串：_run_turn 会把它当响应体返回


def test_each_effect_gets_its_own_idempotency_key(tmp_path):
    """工程铁律 1：每个有副作用的动作独占一个节点，各带幂等键。"""
    conn, _channel, _graph = _run_failing_turn(tmp_path)

    keys = [
        row[0]
        for row in conn.execute(
            "SELECT effect_key FROM effect_log WHERE thread_id='job1' ORDER BY effect_key"
        )
    ]
    assert keys == [
        "job1:effect_deliver_manual_handoff:2",
        "job1:effect_mark_needs_manual:2",
    ]
