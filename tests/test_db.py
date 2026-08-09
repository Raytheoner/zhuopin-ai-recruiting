import sqlite3

from app.storage.db import get_connection, init_schema


def test_init_schema_creates_all_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"job", "job_profile", "effect_log", "outbox"} <= tables


def test_effect_log_has_unique_index_on_effect_key(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
        "VALUES ('job1:effect_x:1', 'job1', 'effect_x', '1', datetime('now'))"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES ('job1:effect_x:1', 'job1', 'effect_x', '1', datetime('now'))"
        )
        conn.commit()


import pytest  # noqa: E402  (保持在文件顶部导入也可，这里为可读性放在使用前)
