import logging
import sqlite3

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


def test_cleanup_rollback_failure_does_not_mask_original_exception(tmp_path):
    """
    spec Requirement「异常路径不掩盖原始错误」：一个带幂等保护的写入函数体
    执行过程中抛出异常时，兜底的 conn.rollback() 如果自己也失败（例如连接
    上已经没有活跃事务——事务已被另一个所有者提前结束），调用方最终看到的
    必须是导致失败的原始异常，而不是清理动作产生的次生异常。

    用一个"rollback() 总是抛异常"的连接子类直接、确定性地制造这个条件，
    不依赖任何 SQLite 版本/平台对"在无活跃事务的连接上调用 rollback()"这件
    事是否报错的行为差异（已验证：本机 Python 3.14.6 + SQLite 3.53.3 上这
    是静默 no-op，不会自然报错——所以必须用连接子类主动模拟，而不是指望
    自然触发）。
    """
    import sqlite3

    from app.storage.idempotency import idempotent_effect

    class _RollbackFailingConnection(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError(
                "cannot rollback - no transaction is active (simulated)"
            )

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False, factory=_RollbackFailingConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)  # init_schema 只用 commit()，不用 rollback()，对这个子类安全

    @idempotent_effect("effect_masking_probe")
    def failing_effect(conn, thread_id, business_key):
        raise ValueError("original business failure")

    with pytest.raises(ValueError, match="original business failure"):
        failing_effect(conn, thread_id="job1", business_key="1")


def test_cleanup_rollback_failure_is_logged(tmp_path, caplog):
    """
    review Finding 2：当清理用的 conn.rollback() 自己也失败时，此前的行为是
    `except Exception: pass`——原始业务异常仍然正确传播（这是 Task 3 的核心
    修复，必须保持不变），但 rollback 失败这件事本身没有留下任何痕迹。被
    rollback undo 掉的那次部分写入会一直留在连接的隐式事务里，直到下一个
    *不相关*的 effect 调用 conn.commit() 时被悄悄一并落盘
    （tests/test_idempotency.py::test_partial_write_is_rolled_back_not_leaked_into_later_commit
    保护的正是这个不变式），而这个场景——rollback 本身失败——之前完全没有
    测试覆盖。

    本测试同时验证两件事：(a) 原始业务异常仍然原样传播（不能被 rollback
    的次生异常掩盖，这是回归防护）；(b) rollback 失败现在会打一条 ERROR
    级别日志，带上 effect_key，不再是彻底静默。
    """
    import logging
    import sqlite3

    from app.storage.idempotency import idempotent_effect

    class _RollbackFailingConnection(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError(
                "cannot rollback - no transaction is active (simulated)"
            )

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False, factory=_RollbackFailingConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)

    @idempotent_effect("effect_masking_probe")
    def failing_effect(conn, thread_id, business_key):
        raise ValueError("original business failure")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError, match="original business failure"):
            failing_effect(conn, thread_id="job1", business_key="1")

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "rollback 失败时应该有 ERROR 级别日志，而不是彻底静默"
    assert any("job1:effect_masking_probe:1" in r.getMessage() for r in error_records), (
        "日志里应该包含 effect_key，方便定位是哪个 effect 的部分写入被泄漏"
    )


def test_unique_key_race_short_circuits_and_rolls_back_this_write(tmp_path, caplog):
    """
    工程铁律1 的竞态落点。装饰器第 33 行的 SELECT 预检与随后的
    INSERT effect_log 之间隔着被装饰函数的整个函数体；另一条路径（双击、
    客户端超时重发、反向代理重试）完全可以在这段窗口里**完整**应用同一个
    effect_key。唯一索引才是唯一权威，预检只是省一次无谓执行的优化。

    命中这种情况时正确的语义是"已执行 ⇒ 短路"，⛔ 不是异常：
    - 抛出去 = 用户看到 500（现网 2026-09-08 13:18 实发一次）
    - 抛出去且不回滚 = 本次的业务写留在未提交事务里，被之后任何一次
      不相关的 effect 的 conn.commit() 悄悄带下去 ⇒ 业务表多一行、
      effect_log 仍一行 ⇒ 恒等式破，且没有任何症状。

    时序靠两个连接构造，是确定性的、不靠线程抢跑：sqlite3 的 legacy
    transaction control 下 SELECT 不开事务，所以预检之后 conn 尚未持写锁，
    conn2 此刻可以自由提交；等 conn 写了业务行拿到写锁，conn2 就会被挡住，
    所以抢先者必须抢在本次业务写之前。
    """
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)
    winner_conn = get_connection(db_path)

    calls = []

    @idempotent_effect("effect_race")
    def send(conn, thread_id, business_key):
        calls.append(business_key)
        # 预检已过、本连接尚未持写锁：另一条路径此刻完整应用同一个 effect
        winner_conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (f"{thread_id}:effect_race:{business_key}", thread_id, "effect_race", business_key),
        )
        winner_conn.execute(
            "INSERT INTO job (id, title) VALUES (?, ?)", ("job-winner", "抢先者写的行")
        )
        winner_conn.commit()
        # 本次的业务写（这一行必须被回滚掉）
        conn.execute("INSERT INTO job (id, title) VALUES (?, ?)", ("job-loser", "本次写的行"))
        return "sent"

    with caplog.at_level(logging.WARNING, logger="app.storage.idempotency"):
        result = send(conn, thread_id="job1", business_key="v1")

    # ① 返回 None（按"已执行"短路）  ② 不抛（走到这里就说明没抛）
    assert result is None
    assert calls == ["v1"]  # 函数体确实跑过了——这不是预检短路，是冲突短路

    # ③ 业务表只剩抢先者那一行：本次的写被整体回滚
    #    ⛔ 这一条是 INSERT OR IGNORE 那种写法过不去的判据
    assert [r[0] for r in conn.execute("SELECT id FROM job ORDER BY id")] == ["job-winner"]

    # ④ effect_log 恰一行（恒等式：effect_log 条数 ≡ 业务表行数）
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1

    # 这条路径很罕见，必须在日志里留下痕迹，否则现网只能看见"什么都没发生"
    assert any("effect_race" in r.getMessage() for r in caplog.records)


def test_non_unique_integrity_error_still_propagates(tmp_path):
    """
    "撞 IntegrityError 就当幂等命中"是**错的**修法：effect_log 上还有 NOT NULL
    约束，thread_id 传成 None 一样抛 IntegrityError，但那是调用方的真 bug，
    吞掉它等于把一个必现故障伪装成"这件事已经做过了"。

    判据用的是语义而不是错误文案：回滚之后这把 effect_key 不在库里 ⇒ 不是
    幂等命中 ⇒ 原样上抛。
    """
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    @idempotent_effect("effect_not_null")
    def send(conn, thread_id, business_key):
        return "sent"

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        send(conn, thread_id=None, business_key="v1")

    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0


def test_rollback_failure_during_short_circuit_does_not_fake_success(tmp_path, caplog):
    """
    回滚失败是"回滚失败"这一族里最坏的一支：本次的业务写没被撤掉，仍躺在
    未提交事务里等着被下一次不相关的 commit 带走。此时返回 None 等于告诉
    调用方"一切正常、这件事早做过了"，而恒等式正悬在破掉的边缘。

    正确行为 = 记 ERROR + 把 IntegrityError 抛给调用方（宁可 500 一次，
    也不要静默地让恒等式破掉）。
    """
    db_path = str(tmp_path / "test.db")

    # sqlite3.Connection.rollback 在本机 Python 3.14.6 上是只读 slot，实例级
    # `conn.rollback = _boom` 会直接 AttributeError（已验证，与本文件
    # test_cleanup_rollback_failure_does_not_mask_original_exception 采用同一
    # workaround 的原因相同）：改用连接子类在类级别覆写 rollback，让紧随其后
    # 的 rollback 失败（模拟事务已被别的所有者结束等情况）。
    class _RollbackFailingConnection(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError("rollback exploded")

    conn = sqlite3.connect(db_path, check_same_thread=False, factory=_RollbackFailingConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)  # init_schema 只用 commit()，不用 rollback()，对这个子类安全
    winner_conn = get_connection(db_path)

    @idempotent_effect("effect_race_rb")
    def send(conn, thread_id, business_key):
        winner_conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (f"{thread_id}:effect_race_rb:{business_key}", thread_id, "effect_race_rb", business_key),
        )
        winner_conn.commit()
        conn.execute("INSERT INTO job (id, title) VALUES (?, ?)", ("job-loser", "本次写的行"))
        return "sent"

    with caplog.at_level(logging.ERROR, logger="app.storage.idempotency"):
        with pytest.raises(sqlite3.IntegrityError):
            send(conn, thread_id="job1", business_key="v1")

    assert any(r.levelname == "ERROR" for r in caplog.records)
