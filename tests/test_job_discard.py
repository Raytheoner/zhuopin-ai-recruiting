import json
import sqlite3
import threading

import pytest

from app.storage.db import init_schema
from app.storage.job_discard import discard_thread_checkpoints, discard_unstarted_job


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "discard.db"))
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


def _seed_one_turn(conn, job_id):
    """照抄一次首轮 invoke 在库里真实写下的每一行，一行不多一行不少。"""
    conn.execute("INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,))
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) VALUES (?, ?, 1, 'drafting', '{}')",
        (f"{job_id}-v1", job_id),
    )
    conn.execute(
        "INSERT INTO conversation (thread_id, history_json) VALUES (?, ?)",
        (job_id, json.dumps([{"role": "user", "content": "今天中午吃什么"}])),
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, 'question', '{}')",
        (job_id,),
    )
    for node in ("effect_persist_draft", "effect_deliver_message"):
        conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, '0', datetime('now'))",
            (f"{job_id}:{node}:0", job_id, node),
        )
    conn.execute(
        "INSERT INTO analysis_run (id, job_id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES (?, ?, 'deepseek-chat-241226', 'intake-v5', 0.0, 'h', '{}')",
        (f"{job_id}-run1", job_id),
    )
    conn.commit()


def _counts(conn, job_id):
    return {
        "job": conn.execute("SELECT COUNT(*) FROM job WHERE id=?", (job_id,)).fetchone()[0],
        "job_profile": conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)).fetchone()[0],
        "conversation": conn.execute("SELECT COUNT(*) FROM conversation WHERE thread_id=?", (job_id,)).fetchone()[0],
        "outbox": conn.execute("SELECT COUNT(*) FROM outbox WHERE thread_id=?", (job_id,)).fetchone()[0],
        "effect_log": conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id=?", (job_id,)).fetchone()[0],
        "analysis_run": conn.execute("SELECT COUNT(*) FROM analysis_run WHERE job_id=?", (job_id,)).fetchone()[0],
    }


def test_discard_removes_every_business_row_of_that_job(conn):
    _seed_one_turn(conn, "job-a")
    assert _counts(conn, "job-a") == {
        "job": 1, "job_profile": 1, "conversation": 1, "outbox": 1, "effect_log": 2, "analysis_run": 1
    }

    discard_unstarted_job(conn, "job-a")

    counts = _counts(conn, "job-a")
    assert counts["job"] == 0
    assert counts["job_profile"] == 0
    assert counts["conversation"] == 0
    assert counts["outbox"] == 0
    # effect_log 必须一起删：铁律1 的 reviewer 判据是"每个 effect_* 节点的
    # effect_log 条数与其业务表行数按 thread 恒等"。业务行删了、幂等记录
    # 留着，这条不变式当场破，而且破得没有症状。
    assert counts["effect_log"] == 0


def test_discard_keeps_the_audit_record_of_the_model_call(conn):
    """那次模型调用真的发生过。

    岗位可以当作从未成立，"我们调过一次模型"这个事实不可以——铁律3/4 要求
    每一次调用可解释、可审计，PIPL 第 24 条的说明权也建立在它上面。
    """
    _seed_one_turn(conn, "job-b")

    discard_unstarted_job(conn, "job-b")

    assert _counts(conn, "job-b")["analysis_run"] == 1


def test_discard_touches_no_other_job(conn):
    _seed_one_turn(conn, "job-c")
    _seed_one_turn(conn, "job-d")

    discard_unstarted_job(conn, "job-c")

    assert _counts(conn, "job-d") == {
        "job": 1, "job_profile": 1, "conversation": 1, "outbox": 1, "effect_log": 2, "analysis_run": 1
    }


def test_discard_is_committed(conn, tmp_path):
    """另开一条连接看得见结果 —— 没 commit 的话下一个请求还会看见这行。"""
    _seed_one_turn(conn, "job-e")
    discard_unstarted_job(conn, "job-e")

    other = sqlite3.connect(str(tmp_path / "discard.db"))
    try:
        assert other.execute("SELECT COUNT(*) FROM job WHERE id='job-e'").fetchone()[0] == 0
    finally:
        other.close()


class _FakeSaver:
    """鸭子类型的 checkpointer：只需要 .lock 与 .conn。"""

    def __init__(self, connection):
        self.lock = threading.Lock()
        self.conn = connection


def test_discard_checkpoints_removes_only_that_thread(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "cp.db"))
    try:
        # 表名与列名取自 langgraph-checkpoint-sqlite 2.0.6 的建表语句。
        connection.execute(
            "CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, "
            "checkpoint_id TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, "
            "checkpoint_id TEXT NOT NULL)"
        )
        for thread in ("keep", "drop"):
            connection.execute("INSERT INTO checkpoints VALUES (?, '', 'c1')", (thread,))
            connection.execute("INSERT INTO writes VALUES (?, '', 'c1')", (thread,))
        connection.commit()

        discard_thread_checkpoints(_FakeSaver(connection), "drop")

        assert connection.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='drop'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM writes WHERE thread_id='drop'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='keep'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM writes WHERE thread_id='keep'").fetchone()[0] == 1
    finally:
        connection.close()


def test_discard_checkpoints_fails_loudly_when_the_tables_are_gone(tmp_path):
    """升级 langgraph 把两张表改名时，这里必须红，⛔ 不许静默空转。

    静默空转的症状是：checkpoint 残留悄悄回来，而所有测试仍然是绿的。
    """
    connection = sqlite3.connect(str(tmp_path / "empty.db"))
    try:
        with pytest.raises(sqlite3.OperationalError):
            discard_thread_checkpoints(_FakeSaver(connection), "whatever")
    finally:
        connection.close()
