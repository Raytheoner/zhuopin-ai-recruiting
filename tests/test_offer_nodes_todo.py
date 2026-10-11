"""effect_publish_approval_todo 的内部通知、逐级幂等与门禁隔离。"""
from __future__ import annotations

import inspect
import json

import pytest

from app.channels.web_channel import WebChannel
from app.graph import offer_nodes
from app.graph.offer_nodes import effect_publish_approval_todo, publish_approval_todo
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer import get_offer
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','interview')"
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, approval_round, created_by) "
        "VALUES ('o1','app1','j1','研发部','2026-11-01','李四','pending_approval',1,'hr1')"
    )
    conn.commit()
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[
            {"level": 1, "approver_account_ids": ["mgr"]},
            {"level": 2, "approver_account_ids": ["gm"]},
        ],
        updated_by="hr1",
    )


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-nodes-todo.db"))
    init_schema(c)
    _seed(c)
    return c


def _outbox(conn):
    return conn.execute(
        "SELECT thread_id, message_type, payload_json FROM outbox ORDER BY id"
    ).fetchall()


def _effect_count(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_publish_approval_todo' "
        "AND thread_id = 'o1'"
    ).fetchone()[0]


def test_publish_writes_todo_to_internal_outbox(conn):
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=WebChannel(conn))
    rows = _outbox(conn)
    assert len(rows) == _effect_count(conn) == 1
    thread_id, message_type, payload_json = rows[0]
    assert (thread_id, message_type) == ("o1", "offer_approval_todo")
    payload = json.loads(payload_json)
    assert payload["level"] == 1
    assert payload["round"] == 1
    assert payload["approvers"] == ["mgr"]
    assert payload["approve_url"] == "/offers/o1/approve"
    assert payload["job_title"] == "嵌入式工程师"
    assert payload["application_id"] == "app1"


def test_publish_is_idempotent_per_round_and_level(conn):
    offer = get_offer(conn, "o1")
    channel = WebChannel(conn)
    publish_approval_todo(conn, offer=offer, channel=channel)
    publish_approval_todo(conn, offer=offer, channel=channel)
    assert len(_outbox(conn)) == _effect_count(conn) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key = ?",
        ("o1:effect_publish_approval_todo:1:1",),
    ).fetchone()[0] == 1


def test_next_level_gets_its_own_todo(conn):
    channel = WebChannel(conn)
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=channel)
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('a1','o1',1,1,'mgr','approved')"
    )
    conn.commit()
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=channel)
    rows = _outbox(conn)
    assert len(rows) == _effect_count(conn) == 2
    assert json.loads(rows[1][2])["approvers"] == ["gm"]
    assert json.loads(rows[1][2])["level"] == 2


def test_no_channel_does_not_block_and_leaves_no_effect_log(conn):
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=None)
    assert _outbox(conn) == []
    assert _effect_count(conn) == 0


def test_effect_node_rejects_none_channel(conn):
    with pytest.raises(ValueError, match="channel 不能为空"):
        effect_publish_approval_todo(
            conn,
            thread_id="o1",
            business_key="1:1",
            channel=None,
            offer_id="o1",
            application_id="app1",
            job_title="嵌入式工程师",
            round_no=1,
            level=1,
            approvers=["mgr"],
        )
    assert _outbox(conn) == []
    assert _effect_count(conn) == 0


def test_failed_delivery_leaves_no_fact_and_can_be_retried(conn):
    class FailingChannel:
        def __init__(self, connection):
            self.connection = connection

        def deliver(self, thread_id, message):
            self.connection.execute(
                "INSERT INTO outbox (thread_id, message_type, payload_json) "
                "VALUES (?, ?, ?)",
                (thread_id, message.type, json.dumps(message.payload, ensure_ascii=False)),
            )
            raise RuntimeError("outbox unavailable")

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        publish_approval_todo(
            conn, offer=get_offer(conn, "o1"), channel=FailingChannel(conn)
        )
    assert _outbox(conn) == []
    assert _effect_count(conn) == 0

    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=WebChannel(conn))
    assert len(_outbox(conn)) == _effect_count(conn) == 1


def test_thread_binding_rejects_mismatch_before_replay_cache(conn):
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=WebChannel(conn))
    with pytest.raises(ValueError, match="thread_id must match offer_id"):
        effect_publish_approval_todo(
            conn,
            thread_id="wrong-offer",
            business_key="1:1",
            channel=WebChannel(conn),
            offer_id="o1",
            application_id="app1",
            job_title="嵌入式工程师",
            round_no=1,
            level=1,
            approvers=["mgr"],
        )
    assert len(_outbox(conn)) == _effect_count(conn) == 1


def test_fresh_wrong_business_key_rejected_before_write_and_preserves_caller_transaction(conn):
    # A wrong key must be rejected before the effect opens its transaction or
    # reaches the idempotency cache. The caller's pending work remains owned by
    # the caller after rejection.
    conn.execute("INSERT INTO candidate (id, name) VALUES ('caller-tx', 'caller')")
    before_outbox = _outbox(conn)
    before_effects = conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall()

    with pytest.raises(ValueError, match="business_key 必须匹配 round_no:level"):
        effect_publish_approval_todo(
            conn,
            thread_id="o1",
            business_key="wrong:1",
            channel=WebChannel(conn),
            offer_id="o1",
            application_id="app1",
            job_title="嵌入式工程师",
            round_no=1,
            level=1,
            approvers=["mgr"],
        )

    assert conn.in_transaction
    assert _outbox(conn) == before_outbox
    assert conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall() == before_effects
    assert conn.execute("SELECT name FROM candidate WHERE id='caller-tx'").fetchone()[0] == "caller"
    conn.rollback()


def test_cached_key_rejects_different_round_or_level_before_replay(conn):
    publish_approval_todo(conn, offer=get_offer(conn, "o1"), channel=WebChannel(conn))
    before_outbox = _outbox(conn)
    before_effects = conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall()

    # This is a real cache hit key, but the supplied node parameters claim a
    # different round. Validation must happen before the decorator can short-circuit.
    for round_no, level in ((2, 1), (1, 2)):
        with pytest.raises(ValueError, match="business_key 必须匹配 round_no:level"):
            effect_publish_approval_todo(
                conn,
                thread_id="o1",
                business_key="1:1",
                channel=WebChannel(conn),
                offer_id="o1",
                application_id="app1",
                job_title="嵌入式工程师",
                round_no=round_no,
                level=level,
                approvers=["mgr"],
            )

    assert _outbox(conn) == before_outbox
    assert conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall() == before_effects
    assert not conn.in_transaction


def test_offer_nodes_never_calls_the_candidate_gate():
    """源码反证 U3 内部通知不走候选人门禁，也没有 AI 调用。"""
    source = inspect.getsource(offer_nodes)
    assert "deliver_candidate_message" not in source
    assert "app.outbound" not in source
    assert "app.agents" not in source
