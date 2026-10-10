"""U3 tasks 3.1：流转事实表的来源列与 action CHECK 的七值放宽。

⚠️ 这是"两条建库路径"的交叉守护：新库（SCHEMA 的 CREATE TABLE）／老库
（_ADDED_COLUMNS 的 ADD COLUMN）／老库且 action 列已带旧 CHECK（整表重建）。
三者最终必须给出同一个 CHECK，且重建不能丢行、不能丢索引。

⚠️ 命名与计划字面的差异（以磁盘真身为准）：本模块用的是既有公开常量
`STAGE_HISTORY_ACTIONS` 与既有谓词 `_stage_history_action_check_is_current`
（channel-resume-intake U2 已落地并被 tests/test_candidate_merge_schema.py、
tests/test_db_migration.py 引用），不是计划成文时（U2 尚未合入）写的
`_STAGE_HISTORY_ACTIONS` / `_stage_history_action_check_width_ok`。两处一个概念，
⛔ 不再造第二个名字。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import (
    STAGE_HISTORY_ACTIONS,
    _ADDED_COLUMNS,
    _existing_columns,
    init_schema,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def _stage_parents(conn: sqlite3.Connection) -> str:
    """建 application_stage_history 的外键父行，返回一个可用的 application_id。

    ⚠️ 必须真的建：_rebuild_application_stage_history_action_check 结束时会跑
    PRAGMA foreign_key_check，父行缺失会让迁移自己抛错（这正是我们要的严格性，
    测试夹具得先满足它）。
    """
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h1', 'alice', 'boss')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    return "app-1"


_OLD_FIVE_VALUE_DDL = """
DROP TABLE application_stage_history;
CREATE TABLE application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    action TEXT CHECK (
        action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show')
    ),
    detail_json TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_application_stage_history_application
    ON application_stage_history (application_id);
"""


# M2 U1 建表的形态：连 action/detail_json 都还没有（.51 上没跑过 1001R 的库）。
_M2_ERA_DDL = """
DROP TABLE application_stage_history;
CREATE TABLE application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_application_stage_history_application
    ON application_stage_history (application_id);
"""


def test_fresh_stage_history_has_source_column():
    conn = _conn()
    assert "source" in _existing_columns(conn, "application_stage_history")


def test_source_is_registered_for_the_add_column_path():
    """老库路径：source 必须登记在 _ADDED_COLUMNS 里，否则 .51 的 demo.db 永远
    不会有这一列，而 CREATE TABLE IF NOT EXISTS 对已存在的表是彻底 no-op。"""
    assert ("application_stage_history", "source") in {
        (table, column) for table, column, _ddl in _ADDED_COLUMNS
    }


def test_action_check_accepts_every_listed_action_and_null():
    conn = _conn()
    app_id = _stage_parents(conn)
    for action in list(STAGE_HISTORY_ACTIONS) + [None]:
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES (?, ?, 'initial', 'agent', ?)",
            (f"h-{action}", app_id, action),
        )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == len(STAGE_HISTORY_ACTIONS) + 1


def test_action_check_still_rejects_unknown_actions():
    conn = _conn()
    app_id = _stage_parents(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES ('h-bad', ?, 'initial', 'agent', 'promoted')",
            (app_id,),
        )


def test_legacy_five_value_check_is_rebuilt_without_losing_rows():
    """`.51` 现网的形态：action 列已存在且是五值 CHECK，source 列还不存在。
    整表重建后：CHECK 放宽到七值、历史行一条不丢、索引补回、source 列就位。"""
    conn = _conn()
    app_id = _stage_parents(conn)
    conn.executescript(_OLD_FIVE_VALUE_DDL)
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, to_stage_id, actor_type, action, detail_json) "
        "VALUES ('h-old', ?, 'initial', 'human', 'rescheduled', '{\"to\": \"2026-10-12\"}')",
        (app_id,),
    )
    conn.commit()
    assert "source" not in _existing_columns(conn, "application_stage_history")

    init_schema(conn)

    assert "source" in _existing_columns(conn, "application_stage_history")
    row = conn.execute(
        "SELECT application_id, action, detail_json, source FROM application_stage_history "
        "WHERE id = 'h-old'"
    ).fetchone()
    assert row == (app_id, "rescheduled", '{"to": "2026-10-12"}', None)
    # 索引随 DROP TABLE 一起消失过，必须补回。
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='idx_application_stage_history_application'"
    ).fetchone() is not None
    # 旧 CHECK 拒掉的值现在放行；不在七值里的照样拒。
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, to_stage_id, actor_type, action, source) "
        "VALUES ('h-new', ?, 'initial', 'human', 'source_corrected', 'referral')",
        (app_id,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES ('h-bad', ?, 'initial', 'human', 'promoted')",
            (app_id,),
        )


def test_m2_era_table_gains_source_and_accepts_source_corrected():
    """没跑过 1001R 的老库（M2 U1 形态，连 action 列都没有）：加列路径一步到位
    给出七值 CHECK 与 source 列，不经过整表重建——两条路径的终态必须一致。"""
    conn = _conn()
    app_id = _stage_parents(conn)
    conn.executescript(_M2_ERA_DDL)
    conn.commit()
    assert "action" not in _existing_columns(conn, "application_stage_history")

    init_schema(conn)

    assert {"action", "detail_json", "source"} <= _existing_columns(
        conn, "application_stage_history"
    )
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, to_stage_id, actor_type, action, source) "
        "VALUES ('h-new', ?, 'initial', 'agent', 'source_corrected', 'referral')",
        (app_id,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES ('h-bad', ?, 'initial', 'agent', 'promoted')",
            (app_id,),
        )


def test_init_schema_is_idempotent_after_the_rebuild():
    """重建是幂等的：第二次 init_schema 不再重建（DDL 里已有 source_corrected），
    也不报错。"""
    conn = _conn()
    app_id = _stage_parents(conn)
    conn.executescript(_OLD_FIVE_VALUE_DDL)
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, to_stage_id, actor_type, action) "
        "VALUES ('h-old', ?, 'initial', 'agent', 'no_show')",
        (app_id,),
    )
    conn.commit()

    init_schema(conn)
    ddl_after_first = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='application_stage_history'"
    ).fetchone()[0]
    init_schema(conn)
    ddl_after_second = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='application_stage_history'"
    ).fetchone()[0]

    assert ddl_after_first == ddl_after_second
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == 1
