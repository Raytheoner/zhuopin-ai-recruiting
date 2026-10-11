"""onboarding-flow U3 终态节点：effect_complete_onboarding（3.1/3.3）与
effect_enqueue_disposition（3.2）。

夹具与 helpers 与 tests/test_onboarding_nodes.py（U2）刻意分开：那份文件按 U2 的
Task 分节长大，本文件按 U3 的任务分节；两份互不修改，避免并发泳道互相踩。
"""
import json
import logging
import sqlite3

import pytest

from app.graph.onboarding_nodes import (
    DISPOSITION_CATEGORIES,
    effect_enqueue_disposition,
    effect_instantiate_checklist,
    effect_update_item,
)
from app.storage.db import get_connection, init_schema

TODAY = "2026-10-20"
START_DATE = "2026-10-20"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_hired_application(
    conn, application_id="app-1", department="研发部", start_date=START_DATE
):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES ('j1', '嵌入式工程师', ?, 'approved')",
        (department,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', 'hired')",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, department, start_date),
    )
    conn.commit()


def _instantiated(conn, application_id="app-1"):
    """生成清单并返回 checklist_id（走 U2 的真节点，不手写 INSERT）。"""
    effect_instantiate_checklist(
        conn, thread_id=application_id, business_key="instantiate", created_by="hr-user"
    )
    return conn.execute(
        "SELECT id FROM onboarding_checklist WHERE application_id = ?", (application_id,)
    ).fetchone()[0]


def _finish_all_required(conn, checklist_id):
    conn.execute(
        "UPDATE onboarding_item SET status='done' WHERE checklist_id=? AND required=1",
        (checklist_id,),
    )
    conn.commit()


def _confirm(conn, application_id="app-1", **kwargs):
    kwargs.setdefault("actor_username", "hr-user")
    kwargs.setdefault("today", TODAY)
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="hired", **kwargs
    )


def _abandon(conn, application_id="app-1", **kwargs):
    kwargs.setdefault("actor_username", "hr-user")
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="abandoned", **kwargs
    )


def _assert_disposition_pairs(conn, application_id="app-1"):
    """按投递与类别核实队列事实和对应 effect_log 一一配对。"""
    queue_rows = conn.execute(
        "SELECT category, candidate_id, policy_version, planned_action, due_at, "
        "       executed_at, executed_by "
        "FROM data_disposition_queue WHERE application_id=?",
        (application_id,),
    ).fetchall()
    log_rows = conn.execute(
        "SELECT effect_key, thread_id, node_name, business_key FROM effect_log "
        "WHERE node_name='effect_enqueue_disposition' AND thread_id=?",
        (application_id,),
    ).fetchall()
    queue = {
        category: (candidate_id, policy_version, planned_action, due_at, executed_at, executed_by)
        for category, candidate_id, policy_version, planned_action, due_at, executed_at, executed_by
        in queue_rows
    }
    logs = {
        (thread_id, business_key): (effect_key, node_name)
        for effect_key, thread_id, node_name, business_key in log_rows
    }

    assert len(queue_rows) == len(DISPOSITION_CATEGORIES)
    assert len(log_rows) == len(DISPOSITION_CATEGORIES)
    assert set(queue) == set(DISPOSITION_CATEGORIES)
    assert set(logs) == {(application_id, category) for category in DISPOSITION_CATEGORIES}
    for category in DISPOSITION_CATEGORIES:
        assert queue[category] == ("cand-1", None, "pending", None, None, None)
        assert logs[(application_id, category)] == (
            f"{application_id}:effect_enqueue_disposition:{category}",
            "effect_enqueue_disposition",
        )


# ── Task 3：effect_enqueue_disposition ──────────────────────────────────


def test_enqueue_disposition_writes_six_rows(conn):
    _seed_hired_application(conn)

    count = effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

    assert count == 6
    rows = conn.execute(
        "SELECT category, candidate_id, policy_version, planned_action, due_at, "
        "       executed_at, executed_by "
        "FROM data_disposition_queue WHERE application_id='app-1' ORDER BY category"
    ).fetchall()
    assert [r[0] for r in rows] == sorted(DISPOSITION_CATEGORIES)
    for category, candidate_id, policy_version, planned_action, due_at, executed_at, executed_by in rows:
        assert candidate_id == "cand-1"
        assert policy_version is None      # 策略未签认 ⇒ 待策略
        assert planned_action == "pending"
        assert due_at is None
        assert executed_at is None         # 🔴 未签认 ⇒ 执行时刻恒空
        assert executed_by is None
    _assert_disposition_pairs(conn)


def test_enqueue_disposition_writes_one_effect_log_key_per_category(conn):
    _seed_hired_application(conn)

    effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

    keys = {
        row[0]
        for row in conn.execute(
            "SELECT effect_key FROM effect_log WHERE node_name='effect_enqueue_disposition'"
        ).fetchall()
    }
    assert keys == {
        f"app-1:effect_enqueue_disposition:{category}" for category in DISPOSITION_CATEGORIES
    }
    _assert_disposition_pairs(conn)


def test_enqueue_disposition_rerun_keeps_six_rows(conn):
    _seed_hired_application(conn)

    effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")
    second = effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

    assert second == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key LIKE "
        "'app-1:effect_enqueue_disposition:%'"
    ).fetchone()[0] == 6
    _assert_disposition_pairs(conn)


@pytest.mark.parametrize("preexisting_side", ["queue", "effect_log"])
def test_enqueue_disposition_repairs_single_sided_legacy_row(conn, preexisting_side):
    _seed_hired_application(conn)
    category = DISPOSITION_CATEGORIES[0]
    if preexisting_side == "queue":
        conn.execute(
            "INSERT INTO data_disposition_queue "
            "(id, application_id, candidate_id, category, policy_version, planned_action) "
            "VALUES ('legacy-queue', 'app-1', 'cand-1', ?, NULL, 'pending')",
            (category,),
        )
    else:
        conn.execute(
            "INSERT INTO effect_log "
            "(effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, 'app-1', 'effect_enqueue_disposition', ?, datetime('now'))",
            (f"app-1:effect_enqueue_disposition:{category}", category),
        )
    conn.commit()

    count = effect_enqueue_disposition(
        conn, application_id="app-1", candidate_id="cand-1"
    )

    assert count == 6
    _assert_disposition_pairs(conn)


@pytest.mark.parametrize("fail_at", [2, 3])
@pytest.mark.parametrize("failure_kind", ["queue", "effect_log"])
def test_enqueue_disposition_rolls_back_after_category_write_failure(
    tmp_path, fail_at, failure_kind
):
    """第二/第三类写入失败时，独立入口必须清理事务中的部分登记。"""
    db_path = str(tmp_path / "onboarding.db")

    class FailingCategoryConnection(sqlite3.Connection):
        category_writes = 0

        def execute(self, sql, parameters=()):
            if "INSERT OR IGNORE INTO data_disposition_queue" in sql:
                self.category_writes += 1
                if failure_kind == "queue" and self.category_writes == fail_at:
                    raise sqlite3.OperationalError(
                        f"category {fail_at} queue write failed"
                    )
            elif (
                failure_kind == "effect_log"
                and "INSERT OR IGNORE INTO effect_log" in sql
                and self.category_writes == fail_at
            ):
                raise sqlite3.OperationalError(
                    f"category {fail_at} effect_log write failed"
                )
            return super().execute(sql, parameters)

    writer = sqlite3.connect(db_path, factory=FailingCategoryConnection)
    init_schema(writer)
    _seed_hired_application(writer)

    with pytest.raises(
        sqlite3.OperationalError,
        match=f"category {fail_at} {failure_kind} write failed",
    ):
        effect_enqueue_disposition(
            writer, application_id="app-1", candidate_id="cand-1"
        )

    writer.execute(
        "INSERT INTO job (id, title, department, status) VALUES ('unrelated', 'x', 'x', 'draft')"
    )
    writer.commit()
    reader = sqlite3.connect(db_path)
    assert reader.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 0
    assert reader.execute(
        "SELECT COUNT(*) FROM effect_log "
        "WHERE node_name='effect_enqueue_disposition' AND thread_id='app-1'"
    ).fetchone()[0] == 0
    assert reader.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == reader.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_enqueue_disposition' "
        "AND thread_id='app-1'"
    ).fetchone()[0] == 0
    reader.close()

    # 故障已按调用次数自动解除；同一 writer 连接可完成正常重放，且两表逐类配对。
    assert effect_enqueue_disposition(
        writer, application_id="app-1", candidate_id="cand-1"
    ) == len(DISPOSITION_CATEGORIES)
    _assert_disposition_pairs(writer)
    writer.close()


def test_enqueue_disposition_rolls_back_after_commit_failure(tmp_path):
    db_path = str(tmp_path / "onboarding.db")

    class FailingCommitConnection(sqlite3.Connection):
        fail_next_commit = False

        def commit(self):
            if self.fail_next_commit:
                self.fail_next_commit = False
                raise sqlite3.OperationalError("commit failed")
            return super().commit()

    writer = sqlite3.connect(db_path, factory=FailingCommitConnection)
    init_schema(writer)
    _seed_hired_application(writer)
    writer.fail_next_commit = True

    with pytest.raises(sqlite3.OperationalError, match="commit failed"):
        effect_enqueue_disposition(
            writer, application_id="app-1", candidate_id="cand-1"
        )

    writer.execute(
        "INSERT INTO job (id, title, department, status) VALUES ('unrelated', 'x', 'x', 'draft')"
    )
    writer.commit()
    reader = sqlite3.connect(db_path)
    assert reader.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 0
    assert reader.execute(
        "SELECT COUNT(*) FROM effect_log "
        "WHERE node_name='effect_enqueue_disposition' AND thread_id='app-1'"
    ).fetchone()[0] == 0
    assert reader.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == reader.execute(
        "SELECT COUNT(*) FROM effect_log "
        "WHERE node_name='effect_enqueue_disposition' AND thread_id='app-1'"
    ).fetchone()[0] == 0
    reader.close()

    assert effect_enqueue_disposition(
        writer, application_id="app-1", candidate_id="cand-1"
    ) == len(DISPOSITION_CATEGORIES)
    _assert_disposition_pairs(writer)
    writer.close()


def test_enqueue_disposition_preserves_write_error_when_rollback_fails(tmp_path, caplog):
    db_path = str(tmp_path / "onboarding.db")

    class FailingWriteAndRollbackConnection(sqlite3.Connection):
        rollback_calls = 0
        fail_writes = True

        def execute(self, sql, parameters=()):
            if self.fail_writes and "INSERT OR IGNORE INTO data_disposition_queue" in sql:
                raise sqlite3.OperationalError("category write failed")
            return super().execute(sql, parameters)

        def rollback(self):
            self.rollback_calls += 1
            if self.rollback_calls == 1:
                raise sqlite3.OperationalError("rollback failed")
            return super().rollback()

    writer = sqlite3.connect(db_path, factory=FailingWriteAndRollbackConnection)
    init_schema(writer)
    _seed_hired_application(writer)

    with caplog.at_level("ERROR", logger="app.graph.onboarding_nodes"):
        with pytest.raises(sqlite3.OperationalError, match="category write failed"):
            effect_enqueue_disposition(
                writer, application_id="app-1", candidate_id="cand-1"
            )

    assert "rollback failed while cleaning up effect_enqueue_disposition" in caplog.text
    writer.rollback()
    writer.fail_writes = False
    assert effect_enqueue_disposition(
        writer, application_id="app-1", candidate_id="cand-1"
    ) == len(DISPOSITION_CATEGORIES)
    _assert_disposition_pairs(writer)
    writer.close()
