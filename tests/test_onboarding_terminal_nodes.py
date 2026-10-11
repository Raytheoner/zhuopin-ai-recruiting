"""onboarding-flow U3 终态节点：effect_complete_onboarding（3.1/3.3）与
effect_enqueue_disposition（3.2）。

夹具与 helpers 与 tests/test_onboarding_nodes.py（U2）刻意分开：那份文件按 U2 的
Task 分节长大，本文件按 U3 的任务分节；两份互不修改，避免并发泳道互相踩。
"""
import json
import logging
import sqlite3
import threading

import pytest

from app.graph import onboarding_nodes
from app.graph.onboarding_nodes import (
    OnboardingTerminalRejected,
    TERMINAL_ACTION_ONBOARDED,
    effect_complete_onboarding,
    terminal_history_rows,
    DISPOSITION_CATEGORIES,
    effect_enqueue_disposition,
    enqueue_disposition,
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
    conn, application_id="app-1", department="研发部", start_date=START_DATE,
    current_stage_id="hired",
):
    if current_stage_id != "hired":
        conn.execute(
            "INSERT INTO stage (id, name, stage_type) VALUES (?, '自定义已入职', 'hired')",
            (current_stage_id,),
        )
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
        "VALUES (?, 'cand-1', 'j1', 'res-1', ?, 'hired')",
        (application_id, current_stage_id),
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
    assert set(queue) == {business_key for _, business_key in logs}
    for category in DISPOSITION_CATEGORIES:
        assert queue[category] == ("cand-1", None, "pending", None, None, None)
        assert logs[(application_id, category)] == (
            f"{application_id}:effect_enqueue_disposition:{category}",
            "effect_enqueue_disposition",
        )


def _terminal_business_snapshot(conn, application_id="app-1"):
    """完整快照终态确认会触及的业务表与幂等日志。"""
    tables = (
        "application",
        "offer",
        "application_stage_history",
        "onboarding_checklist",
        "onboarding_item",
        "data_disposition_queue",
        "effect_log",
    )
    return {
        table: tuple(
            tuple(row)
            for row in conn.execute(f"SELECT * FROM {table}").fetchall()
        )
        for table in tables
    }


def test_disposition_pair_guard_rejects_wrong_effect_key_with_same_counts(conn):
    _seed_hired_application(conn)
    enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")
    conn.execute(
        "UPDATE effect_log SET effect_key='app-1:effect_enqueue_disposition:contact-wrong' "
        "WHERE effect_key='app-1:effect_enqueue_disposition:contact'"
    )
    conn.commit()

    with pytest.raises(AssertionError):
        _assert_disposition_pairs(conn)


# ── Task 3：effect_enqueue_disposition ──────────────────────────────────


def test_enqueue_disposition_effect_writes_one_category_fact_and_log(conn):
    _seed_hired_application(conn)
    result = effect_enqueue_disposition(
        conn,
        thread_id="app-1",
        business_key="resume_file",
        candidate_id="cand-1",
    )
    assert result == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key="
        "'app-1:effect_enqueue_disposition:resume_file'"
    ).fetchone()[0] == 1
    assert effect_enqueue_disposition(
        conn,
        thread_id="app-1",
        business_key="resume_file",
        candidate_id="cand-1",
    ) is None
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 1


def test_enqueue_disposition_effect_repairs_existing_queue_without_effect_log(conn):
    _seed_hired_application(conn)
    conn.execute(
        "INSERT INTO data_disposition_queue "
        "(id, application_id, candidate_id, category, policy_version, planned_action) "
        "VALUES ('legacy-queue', 'app-1', 'cand-1', 'resume_file', NULL, 'pending')"
    )
    conn.commit()
    result = effect_enqueue_disposition(
        conn,
        thread_id="app-1",
        business_key="resume_file",
        candidate_id="cand-1",
    )
    assert result == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1' "
        "AND category='resume_file'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key="
        "'app-1:effect_enqueue_disposition:resume_file'"
    ).fetchone()[0] == 1


def test_enqueue_disposition_effect_rolls_back_fact_and_log_on_failure(conn):
    _seed_hired_application(conn)
    conn.execute(
        "CREATE TRIGGER fail_disposition_insert BEFORE INSERT ON data_disposition_queue "
        "BEGIN SELECT RAISE(ABORT, 'queue write failed'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="queue write failed"):
        effect_enqueue_disposition(
            conn,
            thread_id="app-1",
            business_key="resume_file",
            candidate_id="cand-1",
        )
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_enqueue_disposition'"
    ).fetchone()[0] == 0
    conn.execute("DROP TRIGGER fail_disposition_insert")
    assert effect_enqueue_disposition(
        conn,
        thread_id="app-1",
        business_key="resume_file",
        candidate_id="cand-1",
    ) == 1


def test_enqueue_disposition_writes_six_rows(conn):
    _seed_hired_application(conn)

    count = enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

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

    enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

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

    enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")
    second = enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

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

    count = enqueue_disposition(
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
        enqueue_disposition(
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
    assert enqueue_disposition(
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
        enqueue_disposition(
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

    assert enqueue_disposition(
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
            enqueue_disposition(
                writer, application_id="app-1", candidate_id="cand-1"
            )

    assert "rollback failed while cleaning up effect_enqueue_disposition" in caplog.text
    writer.rollback()
    writer.fail_writes = False
    assert enqueue_disposition(
        writer, application_id="app-1", candidate_id="cand-1"
    ) == len(DISPOSITION_CATEGORIES)
    _assert_disposition_pairs(writer)
    writer.close()


# ── Task 4：effect_complete_onboarding(confirm_hired) ──────────────────


def test_confirm_hired_writes_history_checklist_and_disposition(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    history_id = _confirm(conn)

    assert history_id is not None
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor, action, detail_json "
        "FROM application_stage_history WHERE id = ?",
        (history_id,),
    ).fetchone()
    assert row[0] == "hired" and row[1] == "hired"
    assert row[2] == "human" and row[3] == "hr-user"
    assert row[4] == "onboarded"
    detail = json.loads(row[5])
    assert detail["actual_start_date"] == START_DATE
    assert detail["incomplete_required_items"] == []
    assert detail["note"] == ""

    assert conn.execute(
        "SELECT status, closed_reason FROM onboarding_checklist WHERE id = ?", (checklist_id,)
    ).fetchone() == ("closed", "hired")
    assert conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id='app-1'"
    ).fetchone() == ("hired", "hired")
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key='app-1:effect_complete_onboarding:hired'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key LIKE 'app-1:effect_enqueue_disposition:%'"
    ).fetchone()[0] == 6


def test_confirm_hired_records_incomplete_items_and_note(conn):
    """有未完成条目时确认，但必须留痕未完成项与 HR 说明。"""
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    conn.execute(
        "UPDATE onboarding_item SET status='done' WHERE checklist_id=? AND required=1 "
        "AND name != '体检报告'",
        (checklist_id,),
    )
    conn.commit()

    history_id = _confirm(conn, note="体检报告下周补，人已到岗")
    detail = json.loads(conn.execute(
        "SELECT detail_json FROM application_stage_history WHERE id=?", (history_id,)
    ).fetchone()[0])
    assert [item["name"] for item in detail["incomplete_required_items"]] == ["体检报告"]
    assert detail["note"] == "体检报告下周补，人已到岗"


def test_confirm_hired_requires_note_when_required_items_are_pending(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM onboarding_checklist WHERE id=?", (checklist_id,)
    ).fetchone() == ("open",)


def test_confirm_hired_rejects_before_start_date(conn):
    _seed_hired_application(conn, start_date="2026-10-21")
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 0


@pytest.mark.parametrize(
    "invalid_date",
    ["0", "2026-02-30", "2026-1-02", ""],
)
def test_confirm_hired_rejects_invalid_actual_date_without_writes_then_allows_retry(
    conn, invalid_date
):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    before = _terminal_business_snapshot(conn)

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn, actual_start_date=invalid_date)

    assert _terminal_business_snapshot(conn) == before

    history_id = _confirm(conn, actual_start_date="2026-10-19")
    assert history_id is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id='app-1' "
        "AND action='onboarded'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key="
        "'app-1:effect_complete_onboarding:hired'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == len(DISPOSITION_CATEGORIES)
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_enqueue_disposition' "
        "AND thread_id='app-1'"
    ).fetchone()[0] == len(DISPOSITION_CATEGORIES)


def test_confirm_hired_rejects_invalid_injected_today_without_writes(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    before = _terminal_business_snapshot(conn)

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn, today="2026-2-20")

    assert _terminal_business_snapshot(conn) == before


def test_confirm_hired_rejects_without_accepted_offer(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    conn.execute("UPDATE offer SET status='exported' WHERE application_id='app-1'")
    conn.commit()
    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)


def test_confirm_hired_rejects_without_checklist(conn):
    _seed_hired_application(conn)
    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)


def test_confirm_hired_rejects_when_terminal_fact_already_exists(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action) "
        "VALUES ('h-manual', 'app-1', 'hired', 'hired', 'human', 'hr-user', 'onboarded')"
    )
    conn.commit()
    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)


def test_confirm_hired_rerun_is_idempotent(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    first = _confirm(conn)
    second = _confirm(conn)
    assert first is not None and second is None
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6


def test_confirm_hired_preserves_custom_hired_stage_and_history_semantics(conn):
    stage_id = "hired-custom"
    _seed_hired_application(conn, current_stage_id=stage_id)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    history_id = _confirm(conn)

    assert conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id='app-1'"
    ).fetchone() == ("hired", stage_id)
    row = conn.execute(
        "SELECT h.from_stage_id, h.to_stage_id, h.action, s_from.stage_type, s_to.stage_type "
        "FROM application_stage_history h "
        "JOIN stage s_from ON s_from.id = h.from_stage_id "
        "JOIN stage s_to ON s_to.id = h.to_stage_id WHERE h.id=?",
        (history_id,),
    ).fetchone()
    assert row == (stage_id, stage_id, "onboarded", "hired", "hired")


def test_confirm_hired_same_key_replay_preserves_custom_stage_and_single_history(conn):
    stage_id = "hired-custom"
    _seed_hired_application(conn, current_stage_id=stage_id)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    first = _confirm(conn)
    second = _confirm(conn)

    assert first is not None and second is None
    assert conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id='app-1'"
    ).fetchone() == ("hired", stage_id)
    assert conn.execute(
        "SELECT h.from_stage_id, h.to_stage_id, h.action, s_from.stage_type, s_to.stage_type "
        "FROM application_stage_history h "
        "JOIN stage s_from ON s_from.id = h.from_stage_id "
        "JOIN stage s_to ON s_to.id = h.to_stage_id WHERE h.id=?",
        (first,),
    ).fetchone() == (stage_id, stage_id, "onboarded", "hired", "hired")
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id='app-1' "
        "AND action='onboarded'"
    ).fetchone()[0] == 1


def test_confirm_hired_requires_actor(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    with pytest.raises(OnboardingTerminalRejected):
        effect_complete_onboarding(
            conn, thread_id="app-1", business_key="hired", actor_username="  ", today=TODAY
        )


def test_complete_onboarding_rejects_unknown_outcome(conn):
    _seed_hired_application(conn)
    with pytest.raises(OnboardingTerminalRejected):
        effect_complete_onboarding(
            conn, thread_id="app-1", business_key="ghost", actor_username="hr-user", today=TODAY
        )
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0


def test_confirm_hired_rolls_back_everything_when_registration_fails(conn, monkeypatch):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟登记失败")

    monkeypatch.setattr(onboarding_nodes, "_enqueue_disposition_rows", _boom)
    with pytest.raises(RuntimeError):
        _confirm(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM onboarding_checklist WHERE id=?", (checklist_id,)
    ).fetchone() == ("open",)
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key='app-1:effect_complete_onboarding:hired'"
    ).fetchone()[0] == 0


def test_terminal_history_rows_only_counts_terminal_actions(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    assert terminal_history_rows(conn, "app-1") == []
    _confirm(conn)
    actions = [row[1] for row in terminal_history_rows(conn, "app-1")]
    assert actions == ["onboarded"]


@pytest.mark.parametrize("first_outcome", ["hired", "abandoned"])
def test_opposite_terminal_race_is_serialized_and_second_outcome_rejected(
    tmp_path, monkeypatch, first_outcome
):
    """独立连接并发确认相反终态：首个 effect 持锁提交，第二个随后读到终态并拒绝。"""
    db_path = str(tmp_path / f"terminal-race-{first_outcome}.db")
    first_conn = get_connection(db_path)
    init_schema(first_conn)
    _seed_hired_application(first_conn)
    checklist_id = _instantiated(first_conn)
    _finish_all_required(first_conn, checklist_id)

    competing_conn = get_connection(db_path)
    competing_conn.execute("PRAGMA busy_timeout = 0")
    entered_history_read = threading.Event()
    release_first = threading.Event()
    original_history_rows = onboarding_nodes.terminal_history_rows

    def pause_after_lock(conn, application_id):
        entered_history_read.set()
        assert release_first.wait(timeout=5)
        return original_history_rows(conn, application_id)

    monkeypatch.setattr(onboarding_nodes, "terminal_history_rows", pause_after_lock)
    first_error = []

    def run_first():
        try:
            outcome = first_outcome
            kwargs = {"actor_username": "hr-user", "today": TODAY}
            if outcome == "abandoned":
                kwargs["reason"] = "个人原因"
            else:
                kwargs["today"] = TODAY
            effect_complete_onboarding(
                first_conn, thread_id="app-1", business_key=outcome, **kwargs
            )
        except Exception as exc:  # surfaced in the main test thread below
            first_error.append(exc)

    worker = threading.Thread(target=run_first)
    worker.start()
    assert entered_history_read.wait(timeout=5)
    second_outcome = "abandoned" if first_outcome == "hired" else "hired"
    second_kwargs = {"actor_username": "hr-user", "today": TODAY, "reason": "个人原因"}
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        effect_complete_onboarding(
            competing_conn,
            thread_id="app-1",
            business_key=second_outcome,
            **second_kwargs,
        )
    release_first.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert first_error == []

    monkeypatch.setattr(onboarding_nodes, "terminal_history_rows", original_history_rows)
    with pytest.raises(OnboardingTerminalRejected, match="已有终态"):
        effect_complete_onboarding(
            competing_conn,
            thread_id="app-1",
            business_key=second_outcome,
            **second_kwargs,
        )
    assert len(terminal_history_rows(competing_conn, "app-1")) == 1
    assert competing_conn.execute(
        "SELECT status FROM application WHERE id='app-1'"
    ).fetchone()[0] == ("hired" if first_outcome == "hired" else "refused")
    assert competing_conn.execute(
        "SELECT status FROM offer WHERE application_id='app-1'"
    ).fetchone()[0] == ("accepted" if first_outcome == "hired" else "abandoned")
    assert competing_conn.execute(
        "SELECT action FROM application_stage_history WHERE application_id='app-1' "
        "AND action IN ('onboarded', 'abandoned')"
    ).fetchone()[0] == ("onboarded" if first_outcome == "hired" else "abandoned")
    logs = competing_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_complete_onboarding' "
        "AND thread_id='app-1'"
    ).fetchone()[0]
    assert logs == 1
    queue_rows = competing_conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0]
    queue_logs = competing_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_enqueue_disposition' "
        "AND thread_id='app-1'"
    ).fetchone()[0]
    assert (queue_rows, queue_logs) == (
        (6, 6) if first_outcome == "hired" else (0, 0)
    )


# ── Task 5：effect_complete_onboarding(abandon) ────────────────────────


def test_abandon_writes_terminal_facts(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    history_id = _abandon(conn, reason="接受其他公司")
    assert history_id is not None
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor, action, detail_json "
        "FROM application_stage_history WHERE id = ?", (history_id,),
    ).fetchone()
    assert row[0] == "hired" and row[1] == "hired"
    assert row[2] == "human" and row[3] == "hr-user"
    assert row[4] == "abandoned"
    assert json.loads(row[5])["reason"] == "接受其他公司"
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("refused",)
    assert conn.execute(
        "SELECT status, updated_by FROM offer WHERE application_id='app-1'"
    ).fetchone() == ("abandoned", "hr-user")
    assert conn.execute(
        "SELECT status, closed_reason FROM onboarding_checklist WHERE id = ?", (checklist_id,)
    ).fetchone() == ("closed", "abandoned")


def test_abandon_preserves_custom_hired_stage_and_history_semantics(conn):
    stage_id = "hired-custom"
    _seed_hired_application(conn, current_stage_id=stage_id)
    checklist_id = _instantiated(conn)

    history_id = _abandon(conn, reason="接受其他公司")

    assert conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id='app-1'"
    ).fetchone() == ("refused", stage_id)
    row = conn.execute(
        "SELECT h.from_stage_id, h.to_stage_id, h.action, s_from.stage_type, s_to.stage_type "
        "FROM application_stage_history h "
        "JOIN stage s_from ON s_from.id = h.from_stage_id "
        "JOIN stage s_to ON s_to.id = h.to_stage_id WHERE h.id=?",
        (history_id,),
    ).fetchone()
    assert row == (stage_id, stage_id, "abandoned", "hired", "hired")


def test_abandon_writes_no_rejection_record(conn):
    """候选人自愿退出不是我方淘汰，不写拒绝记录。"""
    _seed_hired_application(conn)
    _instantiated(conn)
    before = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    _abandon(conn, reason="接受其他公司")
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == before == 0


def test_abandon_registers_no_disposition(conn):
    _seed_hired_application(conn)
    _instantiated(conn)
    _abandon(conn, reason="接受其他公司")
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 0


def test_abandon_requires_reason(conn):
    _seed_hired_application(conn)
    _instantiated(conn)
    with pytest.raises(OnboardingTerminalRejected):
        _abandon(conn, reason="   ")
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


def test_abandon_after_hired_confirmation_is_rejected(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    _confirm(conn)
    with pytest.raises(OnboardingTerminalRejected):
        _abandon(conn, reason="反悔")
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action IN ('onboarded','abandoned')"
    ).fetchone()[0] == 1


def test_abandon_without_checklist_is_allowed(conn):
    _seed_hired_application(conn)
    history_id = _abandon(conn, reason="入职前一周告知不来了")
    assert history_id is not None
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("refused",)
    assert conn.execute("SELECT COUNT(*) FROM onboarding_checklist").fetchone()[0] == 0


def test_abandon_rejects_unsigned_offer(conn):
    _seed_hired_application(conn)
    _instantiated(conn)
    conn.execute("UPDATE offer SET status='exported' WHERE application_id='app-1'")
    conn.commit()
    with pytest.raises(OnboardingTerminalRejected):
        _abandon(conn, reason="不来了")


def test_abandon_rerun_is_idempotent(conn):
    _seed_hired_application(conn)
    _instantiated(conn)
    first = _abandon(conn, reason="接受其他公司")
    second = _abandon(conn, reason="接受其他公司")
    assert first is not None and second is None
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='abandoned'"
    ).fetchone()[0] == 1
