"""WBS 2.5 的两个 effect_* 节点（app/graph/manual_handoff.py），单独调用。
图上的接线由 tests/test_intake_needs_manual.py 覆盖。"""

import json

import pytest

from app.channels.web_channel import WebChannel
from app.graph.manual_handoff import (
    REASON_PROVIDER_UNAVAILABLE,
    REASON_SCHEMA_EXHAUSTED,
    effect_deliver_manual_handoff,
    effect_mark_needs_manual,
    manual_handoff_text,
)
from app.storage.db import get_connection, init_schema


def _seeded_conn(tmp_path, status="drafting"):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('job1', '待确定', ?)", (status,)
    )
    conn.commit()
    return conn


def test_marking_writes_the_status_and_its_effect_log_together(tmp_path):
    """工程铁律 1：业务写与幂等记录由装饰器在同一个事务里提交一次。"""
    conn = _seeded_conn(tmp_path)

    effect_mark_needs_manual(
        conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
    )

    status = conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0]
    logged = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key='job1:effect_mark_needs_manual:0'"
    ).fetchone()[0]
    assert (status, logged) == ("needs_manual", 1)


def test_marking_twice_is_a_no_op(tmp_path):
    """LangGraph 恢复时节点从头整个重跑。"""
    conn = _seeded_conn(tmp_path)
    for _ in range(2):
        effect_mark_needs_manual(
            conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
        )
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1


@pytest.mark.parametrize("terminal", ["approved", "abandoned"])
def test_terminal_statuses_are_never_overwritten(tmp_path, terminal):
    """一次模型故障⛔ 不得把已冻结/已放弃的岗位拽回「待人工处理」。"""
    conn = _seeded_conn(tmp_path, status=terminal)
    effect_mark_needs_manual(
        conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
    )
    assert conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0] == terminal


def test_delivery_puts_exactly_one_needs_manual_message_in_the_outbox(tmp_path):
    conn = _seeded_conn(tmp_path)
    channel = WebChannel(conn)

    for _ in range(2):  # 重放
        effect_deliver_manual_handoff(
            conn,
            thread_id="job1",
            business_key="0",
            channel=channel,
            reason_code=REASON_PROVIDER_UNAVAILABLE,
            round_count=0,
        )

    rows = conn.execute(
        "SELECT message_type, payload_json FROM outbox WHERE thread_id='job1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "needs_manual"
    payload = json.loads(rows[0][1])
    assert payload["reason_code"] == REASON_PROVIDER_UNAVAILABLE
    assert payload["message"]


@pytest.mark.parametrize(
    "code", [REASON_SCHEMA_EXHAUSTED, REASON_PROVIDER_UNAVAILABLE, "something-new"]
)
def test_every_reason_code_renders_non_empty_chinese_text(code):
    """⛔ 未登记的原因码不许抛：这条路径本来就是"出事之后"的路径。
    ⚠️ _run_turn 会把这段文案当响应体返回给业务经理，空串等于一个空白页面。"""
    text = manual_handoff_text(code)
    assert text and "转交 HR 人工处理" in text


@pytest.mark.compliance
def test_automatic_handoff_never_writes_a_human_review_row(tmp_path):
    """转人工是**系统判定**。决策人只能是人——⛔ 不许往 human_review 里塞机器决策。"""
    conn = _seeded_conn(tmp_path)
    effect_mark_needs_manual(
        conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
    )
    assert conn.execute("SELECT COUNT(*) FROM human_review").fetchone()[0] == 0
