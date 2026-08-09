from app.storage.db import get_connection, init_schema
from app.storage.idempotency import idempotent_effect


def test_effect_runs_once_on_first_call(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    calls = []

    @idempotent_effect("effect_send_something")
    def send(conn, thread_id, business_key):
        calls.append((thread_id, business_key))
        return "sent"

    result = send(conn, thread_id="job1", business_key="v1")
    assert result == "sent"
    assert calls == [("job1", "v1")]


def test_effect_skipped_on_replay_with_same_business_key(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    calls = []

    @idempotent_effect("effect_send_something")
    def send(conn, thread_id, business_key):
        calls.append((thread_id, business_key))
        return "sent"

    send(conn, thread_id="job1", business_key="v1")
    result_second = send(conn, thread_id="job1", business_key="v1")

    assert len(calls) == 1  # 副作用只发生一次
    assert result_second is None  # 第二次是跳过，不是重新执行


def test_different_business_key_runs_independently(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    calls = []

    @idempotent_effect("effect_send_something")
    def send(conn, thread_id, business_key):
        calls.append(business_key)

    send(conn, thread_id="job1", business_key="v1")
    send(conn, thread_id="job1", business_key="v2")

    assert calls == ["v1", "v2"]
