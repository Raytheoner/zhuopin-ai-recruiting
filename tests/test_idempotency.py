import pytest

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


def test_partial_write_is_rolled_back_not_leaked_into_later_commit(tmp_path):
    """
    工程铁律1：get_connection() 给出的是全应用共享的单个连接（见
    app/storage/db.py 的 get_connection 注释）。如果被装饰函数在抛异常前
    已经写了一些行，这些行会停留在 SQLite 隐式开启的事务里——如果不回滚，
    它们会在之后任何一次*不相关*的 effect 成功 commit 时被悄悄一并落盘。

    这会污染 LangGraph 恢复语义：失败节点因为没有 effect_log 记录，预期
    应该能从头干净重试；但如果它上一次尝试的部分写入被别的 effect 意外
    提交了，重试就会撞上已存在的数据（例如相同主键的 INSERT 报
    IntegrityError），永久卡死。

    本测试复现该场景：一个 effect 先写一行再抛异常（该行不应该被落盘），
    随后一个*不相关*的 effect 正常成功提交；断言第一个 effect 的写入
    没有随第二个 effect 的 commit 被悄悄带入数据库。
    """
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    @idempotent_effect("effect_partial_write")
    def failing_send(conn, thread_id, business_key):
        conn.execute(
            "INSERT INTO job (id, title) VALUES (?, ?)",
            ("leaked-job", "不应该落盘的行"),
        )
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing_send(conn, thread_id="job1", business_key="v1")

    # 一个完全不相关的 effect 正常成功，触发它自己的 commit。
    @idempotent_effect("effect_unrelated")
    def unrelated_send(conn, thread_id, business_key):
        return "ok"

    result = unrelated_send(conn, thread_id="job2", business_key="v1")
    assert result == "ok"

    # 第一个 effect 失败时写入的行必须已被回滚，不能被第二个 effect 的
    # commit 悄悄带入数据库。
    leaked = conn.execute(
        "SELECT 1 FROM job WHERE id = ?", ("leaked-job",)
    ).fetchone()
    assert leaked is None
