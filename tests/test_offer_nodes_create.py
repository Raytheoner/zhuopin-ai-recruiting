"""effect_create_offer 的三写同事务、幂等、锁与失败回滚（U3 task 3.1）。"""
from __future__ import annotations

import sqlite3
import threading

import pytest

import app.graph.offer_nodes as offer_nodes
from app.graph.offer_nodes import effect_create_offer
from app.storage.db import get_connection, init_schema
from app.storage.offer import OfferNotCreatableError


def _seed(conn, *, stage="interview", status="active"):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) VALUES ('app1','c1','j1','r1',?,?)", (stage, status))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer-nodes-create.db"))
    init_schema(c)
    _seed(c)
    yield c
    c.close()


def _create(conn, *, request_id="req-1", **overrides):
    kwargs = dict(thread_id="app1", business_key=request_id, application_id="app1", department="研发部", start_date="2026-11-01", report_to="李四", note="面试表现符合岗位要求", created_by="hr1")
    kwargs.update(overrides)
    return effect_create_offer(conn, **kwargs)


def test_create_writes_offer_stage_and_history_in_one_transaction(conn):
    offer_id = _create(conn)
    assert offer_id
    assert conn.execute("SELECT status, approval_round, department, start_date, report_to, note FROM offer WHERE id = ?", (offer_id,)).fetchone() == ("pending_approval", 1, "研发部", "2026-11-01", "李四", "面试表现符合岗位要求")
    assert conn.execute("SELECT current_stage_id, status FROM application WHERE id = 'app1'").fetchone() == ("offer", "active")
    assert conn.execute("SELECT from_stage_id, to_stage_id, actor_type, actor, action FROM application_stage_history WHERE application_id = 'app1'").fetchall() == [("interview", "offer", "human", "hr1", None)]
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE effect_key = ?", ("app1:effect_create_offer:req-1",)).fetchone()[0] == 1
    assert not conn.in_transaction


def test_successful_create_does_not_leak_transaction_into_unrelated_commit(conn):
    offer_id = _create(conn)
    assert offer_id
    assert not conn.in_transaction

    # The effect decorator owns and closes its transaction before returning.
    # A later unrelated commit must not be responsible for the Offer facts.
    conn.execute("INSERT INTO candidate (id, name) VALUES ('unrelated-success', '无关')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM offer WHERE application_id='app1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='app1'").fetchone()[0] == 1


def test_create_replay_same_request_id_is_noop(conn):
    assert _create(conn)
    assert _create(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE effect_key = ?", ("app1:effect_create_offer:req-1",)).fetchone()[0] == 1


def test_create_rejects_rejected_application(conn):
    conn.execute("UPDATE application SET status='rejected' WHERE id='app1'")
    conn.commit()
    with pytest.raises(OfferNotCreatableError):
        _create(conn)
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE effect_key = ?", ("app1:effect_create_offer:req-1",)).fetchone()[0] == 0


def test_create_rejects_blank_fields(conn):
    with pytest.raises(ValueError, match="department 不能为空"):
        _create(conn, department="   ")


@pytest.mark.parametrize("cached", [False, True])
def test_create_rejects_swapped_application_before_new_or_cached_key(conn, cached):
    if cached:
        assert _create(conn)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2','李四')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, status, uploaded_by) VALUES ('r2','j1','synthetic','b.pdf','sha2','parsed','hr')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) VALUES ('app2','c2','j1','r2','interview','active')")
    conn.commit()
    before = (
        conn.execute("SELECT * FROM application ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM offer ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM application_stage_history ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall(),
    )
    with pytest.raises(ValueError, match="thread_id must match application_id"):
        _create(conn, application_id="app2")
    after = (
        conn.execute("SELECT * FROM application ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM offer ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM application_stage_history ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM effect_log ORDER BY effect_key").fetchall(),
    )
    assert after == before


def test_create_binds_thread_to_application_and_rejects_blank_request_id(conn):
    with pytest.raises(ValueError, match="thread_id must match application_id"):
        _create(conn, thread_id="other")
    with pytest.raises(ValueError, match="business_key 不能为空"):
        _create(conn, request_id="  ")
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='app1'").fetchone()[0] == 0


@pytest.mark.parametrize("failure_point", ["stage", "history"])
def test_midwrite_failure_rolls_back_all_facts_and_unrelated_commit(conn, failure_point):
    if failure_point == "stage":
        conn.execute("CREATE TRIGGER fail_offer_stage BEFORE UPDATE OF current_stage_id ON application BEGIN SELECT RAISE(ABORT, 'stage failure'); END")
    else:
        conn.execute("CREATE TRIGGER fail_offer_history BEFORE INSERT ON application_stage_history BEGIN SELECT RAISE(ABORT, 'history failure'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _create(conn)
    # 模拟同一共享连接后续做了无关写并提交，不得带出失败 effect 的半截事实。
    conn.execute("INSERT INTO candidate (id, name) VALUES ('unrelated', '无关')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
    assert conn.execute("SELECT current_stage_id FROM application WHERE id='app1'").fetchone()[0] == "interview"
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='app1'").fetchone()[0] == 0


def test_create_holds_write_lock_before_validation_and_through_all_writes(conn):
    other = get_connection(conn.execute("PRAGMA database_list").fetchone()[2])
    other.execute("PRAGMA busy_timeout=0")
    validated = threading.Event()
    rejection_finished = threading.Event()
    attempts = []
    original_validator = offer_nodes.assert_offer_creatable

    def pause_after_validation(connection, *, application_id):
        result = original_validator(connection, application_id=application_id)
        validated.set()
        assert rejection_finished.wait(timeout=5)
        return result

    def try_reject():
        assert validated.wait(timeout=5)
        try:
            other.execute("UPDATE application SET status='rejected' WHERE id='app1'")
            other.commit()
        except sqlite3.OperationalError as exc:
            attempts.append(str(exc))
        else:
            attempts.append("UPDATE unexpectedly succeeded")
        finally:
            rejection_finished.set()

    offer_nodes.assert_offer_creatable = pause_after_validation
    contender = threading.Thread(target=try_reject)
    contender.start()
    try:
        _create(conn)
    finally:
        contender.join(timeout=6)
        offer_nodes.assert_offer_creatable = original_validator
        other.close()
    assert attempts and "locked" in attempts[0].lower()
    assert conn.execute("SELECT status, current_stage_id FROM application WHERE id='app1'").fetchone() == ("active", "offer")


def test_create_rejects_existing_transaction_without_rolling_back_callers_writes(conn):
    conn.execute("INSERT INTO candidate (id, name) VALUES ('sentinel', '调用方')")
    assert conn.in_transaction
    with pytest.raises(RuntimeError, match="active transaction"):
        _create(conn)
    assert conn.in_transaction
    conn.commit()
    assert conn.execute("SELECT name FROM candidate WHERE id='sentinel'").fetchone()[0] == "调用方"
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='app1'").fetchone()[0] == 0


def test_create_requires_clean_connection_even_on_idempotent_replay(conn):
    _create(conn)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('sentinel-replay', '调用方')")
    with pytest.raises(RuntimeError, match="active transaction"):
        _create(conn)
    assert conn.in_transaction
    conn.commit()
    assert conn.execute("SELECT name FROM candidate WHERE id='sentinel-replay'").fetchone()[0] == "调用方"
    assert conn.execute("SELECT COUNT(*) FROM offer WHERE application_id='app1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='app1'").fetchone()[0] == 1
