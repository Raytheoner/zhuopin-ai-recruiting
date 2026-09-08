"""WBS 2.5：校验失败重试至多 2 次，仍失败转 needs_manual，⛔ 不产出半成品。"""

from types import SimpleNamespace

import pytest

from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.manual_handoff import REASON_PROVIDER_UNAVAILABLE, REASON_SCHEMA_EXHAUSTED
from app.graph.nodes import compute_intake_turn
from app.llm.gateway import LLMCallMeta, LLMProviderUnavailable, SchemaExtractionFailed
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
        "job1:effect_deliver_manual_handoff:2:schema_retry_exhausted",
        "job1:effect_mark_needs_manual:2:schema_retry_exhausted",
    ]


# ── review I-1：跨轮次恢复路径 ───────────────────────────────────────────


class RecoveringGateway:
    """第一次调用抛异常（网关内部重试已耗尽），此后转为正常应答。

    用于验证"失败轮 → 成功轮"这条跨轮恢复路径：needs_manual 是 checkpoint 里
    一个没有 reducer 的 LastValue 键，⛔ 不能因为上一轮置过 True 就粘滞到
    下一轮——下一轮 compute_intake_turn 必须自己重新写定这个信号。
    """

    def __init__(self, exc):
        self._exc = exc
        self._call_count = 0

    def extract_structured(self, **kwargs):
        raise self._exc

    def extract_structured_with_meta(self, **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            raise self._exc
        parsed = SimpleNamespace(
            is_job_related=True,
            questions=[],
            profile_patch={"team_size": 5},
            unspecified_fields=[],
        )
        meta = LLMCallMeta(latency_ms=1.0, response_model="stub-model", attempts=1)
        return parsed, meta


def test_a_successful_turn_after_a_failing_one_resets_needs_manual_and_persists(tmp_path):
    """
    失败轮之后紧跟一个成功轮，必须恢复到正常落库路径。

    修复前的故障：`compute_intake_turn` 的成功返回以 `**state` 开头、没有
    显式写回 `needs_manual: False`，checkpoint 按 LastValue 语义把上一轮置的
    `True` 原样带到这一轮，`_route_after_compute` 仍然导向 handoff 分支——
    模型明明已经恢复、给出了合法的 profile_patch，这一轮内容却被当成"转人工"
    丢弃，`job_profile` 不落新行，且不报任何错误。
    """
    conn = _seeded_conn(tmp_path)
    channel = WebChannel(conn)
    gateway = RecoveringGateway(SchemaExtractionFailed("x"))
    graph = build_intake_graph(str(tmp_path / "t.db"), gateway=gateway, conn=conn, channel=channel)
    config = {"configurable": {"thread_id": "job1"}}

    # 第 1 轮：失败，转人工。
    graph.invoke(_state(), config=config)
    assert conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id='job1'").fetchone()[0] == 0

    # 第 2 轮：模型已恢复。⚠️ 刻意不在这份输入里传 needs_manual——本用例要验
    # 的正是"真源必须是本轮 compute 是否重新置位"，不能是 checkpoint 里那个
    # 从未被复位过的旧值。
    second_state = {
        "job_id": "job1",
        "history": [
            {"role": "user", "content": "要个做嵌入式开发的"},
            {"role": "user", "content": "团队 5 个人"},
        ],
        "profile_patch_accumulated": {"job_title": "嵌入式软件工程师"},
    }
    graph.invoke(second_state, config=config)

    # 恢复后的这一轮必须真正落库——不能因为上一轮的信号粘滞而再次被丢弃。
    assert conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id='job1'").fetchone()[0] == 1

    # 不该再投递第二条 needs_manual 消息：这一轮走的是正常路径。
    needs_manual_count = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1' AND message_type='needs_manual'"
    ).fetchone()[0]
    assert needs_manual_count == 1

    # checkpoint 里的信号必须已经复位，不能是上一轮遗留的 True。
    snapshot = graph.get_state(config)
    assert snapshot.values.get("needs_manual") is False


def test_replaying_the_same_failing_turn_delivers_exactly_one_message(tmp_path):
    """LangGraph 恢复时节点从头整个重跑——重跑不得再投递一次。"""
    conn, _channel, graph = _run_failing_turn(tmp_path)
    graph.invoke(_state(), config={"configurable": {"thread_id": "job1"}})

    assert conn.execute("SELECT COUNT(*) FROM outbox WHERE thread_id='job1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='job1'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id='job1'").fetchone()[0] == 0


def test_effect_log_count_matches_the_business_write_per_thread(tmp_path):
    """reviewer 判据：effect_log 条数与业务表行数按 thread 恒等。
    本单元的业务写是 job.status 一次 + outbox 一行，各对应一条 effect_log。"""
    conn, _channel, _graph = _run_failing_turn(tmp_path)

    outbox_rows = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    deliver_effects = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id='job1' "
        "AND node_name='effect_deliver_manual_handoff'"
    ).fetchone()[0]
    assert outbox_rows == deliver_effects == 1


def test_provider_outage_produces_its_own_reason_code(tmp_path):
    """两类失败的人工处置完全不同，队列里必须分得开。"""
    _conn, channel, _graph = _run_failing_turn(
        tmp_path, exc=LLMProviderUnavailable("主备都不可用")
    )
    assert channel.latest("job1").payload["reason_code"] == REASON_PROVIDER_UNAVAILABLE


def test_the_needs_manual_queue_can_finally_see_it(tmp_path):
    """job_queries.py:224「今天恒为空，2.5 落地当天自动生效」——就是这一刻。
    ⚠️ 本用例⛔ 不改 app/web/server.py，只证明它读得到。"""
    conn, _channel, _graph = _run_failing_turn(tmp_path)
    status = conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0]

    reasons = derive_needs_manual_reasons(
        job_status=status, profile={}, revision_count=0, max_revisions=3
    )
    assert [reason["code"] for reason in reasons] == ["job_status"]


class _SequencedGateway:
    """按脚本逐次抛出不同异常。用于验证同一个 round_count 内先后两种不同原因
    的转人工不会互相吞掉——round_count 来自 job_profile 行数，转人工轮不落
    画像行，所以同一 round_count 会被连续两次命中（见 review I-2）。"""

    def __init__(self, exceptions):
        self._exceptions = list(exceptions)

    def extract_structured(self, **kwargs):
        raise self._exceptions.pop(0)

    def extract_structured_with_meta(self, **kwargs):
        raise self._exceptions.pop(0)


def test_second_failure_in_the_same_round_with_a_different_reason_is_not_swallowed(tmp_path):
    """review I-2：business_key 只用 round_count 时，同一轮内第二次转人工
    （原因码不同）会被幂等键当成"已处理过"静默跳过，业务经理拿到的还是第一次
    那条、原因码是错的。business_key 必须把 needs_manual_reason_code 也编进去，
    真正的重放（同轮同因）仍然要正确去重。"""
    conn = _seeded_conn(tmp_path)
    channel = WebChannel(conn)
    gateway = _SequencedGateway(
        [
            SchemaExtractionFailed("3 次尝试后仍未通过 Schema 校验"),
            LLMProviderUnavailable("主备都不可用"),
        ]
    )
    graph = build_intake_graph(str(tmp_path / "t.db"), gateway=gateway, conn=conn, channel=channel)
    config = {"configurable": {"thread_id": "job1"}}

    graph.invoke(_state(), config=config)  # round_count=2，schema 耗尽
    graph.invoke(_state(), config=config)  # 仍是 round_count=2，这次是供应商故障

    rows = conn.execute(
        "SELECT message_type, payload_json FROM outbox WHERE thread_id='job1' ORDER BY id"
    ).fetchall()
    assert len(rows) == 2, "两次不同原因的转人工都必须真实投递，不能被幂等键吞掉第二条"

    import json

    reason_codes = [json.loads(payload)["reason_code"] for _type, payload in rows]
    assert reason_codes == [REASON_SCHEMA_EXHAUSTED, REASON_PROVIDER_UNAVAILABLE]
