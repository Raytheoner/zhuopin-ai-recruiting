"""history 条数守恒不变式（tasks 2.6）：对任一投递，application_stage_history 中
action 非空的面试流转事实条数 = effect_log 中四个排期节点的条数 = 成功排期动作次数。
穷举含重跑的序列。"""
from __future__ import annotations

from app.graph.scheduling_nodes import (
    effect_cancel_slot,
    effect_complete_slot,
    effect_reschedule_slot,
    effect_schedule_slot,
)
from app.storage.db import get_connection, init_schema


_NODE_NAMES = (
    "effect_schedule_slot",
    "effect_reschedule_slot",
    "effect_cancel_slot",
    "effect_complete_slot",
)


def _seed(conn):
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('h1', 'hr-1', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('iv1', 'h1', '汤丽萍', '人事部', '[]', 1)"
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interviewer_availability (id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES ('av1', 'iv1', '2020-01-01 00:00', '2020-01-01 23:00', NULL, 'hr-1', 0)"
    )
    conn.commit()


def _history_count(conn, application_id="app1"):
    return conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id = ? AND action IS NOT NULL",
        (application_id,),
    ).fetchone()[0]


def _effect_count(conn, application_id="app1"):
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id = ? AND node_name IN (?, ?, ?, ?)",
        (application_id, *_NODE_NAMES),
    ).fetchone()[0]


def _schedule(conn, *, slot_id):
    return effect_schedule_slot(
        conn, thread_id="app1", business_key=slot_id, slot_id=slot_id,
        application_id="app1", interviewer_ids=["iv1"], round_=1,
        start_at="2020-01-01 10:00", end_at="2020-01-01 11:00",
        mode="onsite", location_or_link=None, actor="hr-1",
    )


def test_schedule_reschedule_cancel_with_reruns_keeps_invariant(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    _seed(conn)

    _schedule(conn, slot_id="s1")
    assert _schedule(conn, slot_id="s1") is None  # 重跑：命中 effect_log，不产生第二场次
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r1", slot_id="s1",
        start_at="2020-01-01 12:00", end_at="2020-01-01 13:00", actor="hr-1",
    )
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r1", slot_id="s1",
        start_at="2020-01-01 12:00", end_at="2020-01-01 13:00", actor="hr-1",
    )  # 重跑
    effect_cancel_slot(
        conn, thread_id="app1", business_key="s1", slot_id="s1",
        cancel_reason="候选人临时有事", actor="hr-1",
    )
    effect_cancel_slot(
        conn, thread_id="app1", business_key="s1", slot_id="s1",
        cancel_reason="候选人临时有事", actor="hr-1",
    )  # 重跑

    assert conn.execute(
        "SELECT COUNT(*) FROM interview_slot WHERE application_id='app1'"
    ).fetchone()[0] == 1
    assert _history_count(conn) == 3
    assert _effect_count(conn) == 3
    assert _history_count(conn) == _effect_count(conn)


def test_schedule_complete_keeps_invariant(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    _seed(conn)

    _schedule(conn, slot_id="s1")
    effect_complete_slot(
        conn, thread_id="app1", business_key="s1:completed", slot_id="s1",
        target_status="completed", actor="iv-1",
    )
    assert _history_count(conn) == 2
    assert _effect_count(conn) == 2


def test_two_distinct_reschedules_are_two_actions(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    _seed(conn)

    _schedule(conn, slot_id="s1")
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r1", slot_id="s1",
        start_at="2020-01-01 12:00", end_at="2020-01-01 13:00", actor="hr-1",
    )
    effect_reschedule_slot(
        conn, thread_id="app1", business_key="s1:r2", slot_id="s1",
        start_at="2020-01-01 14:00", end_at="2020-01-01 15:00", actor="hr-1",
    )
    assert _history_count(conn) == 3
    assert _effect_count(conn) == 3
