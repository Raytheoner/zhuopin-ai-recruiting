"""channel-resume-intake U2 Task 4 的存储层验收：`candidate.merged_into`、
`application_stage_history.action`、`candidate_merge_log`。

两处与计划 Task 4 的字面文本不同，均已登记（见计划「偏离登记 D-CU2-1/D-CU2-2」）：

① `action` 不是本包首建。计划写这一步时磁盘真身里该表还没有 `action`，但
   interview-scheduling U2（1001R）已先落地了「五值 CHECK」版本。计划字面要求的
   `action TEXT` 若照抄，会连 `detail_json` 与那个 CHECK 一起删掉——那是别的
   单元的已交付产物。本包 Task 5 的 `_write_closed_by_merge` 需要第六个取值，
   所以走「放宽 CHECK」而不是「去掉 CHECK」：既拿到本包要的可写性，又不丢
   排期包钉住的取值域（`test_legacy_application_stage_history_action_check_survives_migration`
   仍然守着 `'promoted'` 被拒）。
② 计划 Step 6 的 `with sqlite3.IntegrityError():` 在 Python 3 里不是断言——
   异常类不支持上下文管理器协议，会直接 `TypeError`。改写成 `pytest.raises`。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import STAGE_HISTORY_ACTIONS, _ADDED_COLUMNS, _existing_columns, init_schema


def _conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def _conn_with_application():
    """带一条真实投递链的库：`application_stage_history` 要真写一次才知道
    CHECK 放行哪些值（列集合相同不代表约束相同）。"""
    conn = _conn()
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h1', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'cand1', 'j1', 'r1', 'initial')"
    )
    return conn


def test_candidate_has_merged_into_and_history_has_action():
    conn = _conn()
    assert "merged_into" in _existing_columns(conn, "candidate")
    assert "action" in _existing_columns(conn, "application_stage_history")


def test_candidate_merge_log_table_exists():
    conn = _conn()
    cols = _existing_columns(conn, "candidate_merge_log")
    assert cols >= {
        "id", "primary_id", "secondary_id", "reason", "secondary_snapshot",
        "merged_by", "merged_at", "unmerged_by", "unmerged_at",
    }


def test_added_columns_registered():
    cols = {(t, c) for t, c, _ in _ADDED_COLUMNS}
    assert ("candidate", "merged_into") in cols
    assert ("application_stage_history", "action") in cols


def test_merge_log_rejects_empty_reason_and_actor():
    conn = _conn()
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
    # reason 只有空白：trim 的第二参数显式列出制表/换行（SQLite 单参 trim() 只剥空格）。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate_merge_log (id, primary_id, secondary_id, reason, "
            "secondary_snapshot, merged_by) VALUES ('m1', 'c1', 'c2', '  ', '{}', 'alice')"
        )
    # merged_by 只有空白：空操作人等于没留痕（与 human_review.reviewer 同一手法）。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate_merge_log (id, primary_id, secondary_id, reason, "
            "secondary_snapshot, merged_by) VALUES ('m2', 'c1', 'c2', '同人', '{}', '\t\n')"
        )


def test_merge_log_active_secondary_is_unique():
    """`idx_candidate_merge_log_active_secondary` 是合并幂等的第二道防线
    （第一道是 Task 5 的 effect_log 唯一键）：同一被合并方在撤销前不能被合并两次。
    多行留痕本身合法——撤销后重新合并会产生第二行。"""
    conn = _conn()
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c3', '王五')")
    conn.execute(
        "INSERT INTO candidate_merge_log (id, primary_id, secondary_id, reason, "
        "secondary_snapshot, merged_by) VALUES ('m1', 'c1', 'c2', '同人', '{}', 'alice')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate_merge_log (id, primary_id, secondary_id, reason, "
            "secondary_snapshot, merged_by) VALUES ('m2', 'c3', 'c2', '同人', '{}', 'bob')"
        )
    conn.execute(
        "UPDATE candidate_merge_log SET unmerged_by = 'alice', "
        "unmerged_at = '2026-10-10 12:00:00' WHERE id = 'm1'"
    )
    conn.execute(
        "INSERT INTO candidate_merge_log (id, primary_id, secondary_id, reason, "
        "secondary_snapshot, merged_by) VALUES ('m3', 'c3', 'c2', '重新合并', '{}', 'bob')"
    )


def test_stage_history_action_allowlist_accepts_every_registered_value():
    """新库（SCHEMA 的 CREATE TABLE）逐值真写一次：`STAGE_HISTORY_ACTIONS` 是本表
    action 取值域的真源，SCHEMA / `_ADDED_COLUMNS` / 重建 DDL 三处任一处漏加即
    在本用例或 test_db_migration.py 的迁移用例上失败。"""
    conn = _conn_with_application()
    for index, value in enumerate(STAGE_HISTORY_ACTIONS):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action) "
            "VALUES (?, 'a1', 'initial', 'initial', 'human', 'alice', ?)",
            (f"h-{index}", value),
        )
    # 普通阶段流转没有动作语义，NULL 必须放行。
    conn.execute(
        "INSERT INTO application_stage_history (id, application_id, to_stage_id, actor_type) "
        "VALUES ('h-null', 'a1', 'initial', 'agent')"
    )
    # 未在允许清单里的取值仍然被拒：放宽 CHECK 不等于去掉 CHECK。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES ('h-bad', 'a1', 'initial', 'human', 'promoted')"
        )


def test_closed_by_merge_is_the_value_this_unit_writes():
    """本包 Task 5 的 `_write_closed_by_merge` 写的就是这个字面值；测试把它钉住，
    免得将来有人「顺手统一」成别的拼写而让合并动作在服务器上撞 IntegrityError。"""
    assert "closed_by_merge" in STAGE_HISTORY_ACTIONS
