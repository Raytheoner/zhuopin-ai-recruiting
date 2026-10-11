"""onboarding-flow U3 tasks 3.4：终态不变式 ＋ ⛔ 无自动终态路径的反证。

本文件与 tests/test_onboarding_terminal_nodes.py 的分工：那份测"每个动作对不对"，
这份测"**任何顺序**驱动下都成立的那几条恒等式"——包括失败步骤（被拒的 abandon /
被拒的 confirm）夹在中间时。顺序是穷举的（见 SEQUENCES），每次驱动后复查全部不变式。
"""
import inspect
from pathlib import Path

import pytest

from app.graph import onboarding_nodes
from app.graph.onboarding_nodes import (
    OnboardingTerminalRejected,
    effect_complete_onboarding,
    effect_enqueue_disposition,
    effect_instantiate_checklist,
    terminal_history_rows,
)
from app.storage.db import get_connection, init_schema

TODAY = "2026-10-20"
START_DATE = "2026-10-20"
ROOT = Path(__file__).resolve().parents[1]

_TERMINAL_CONSISTENCY = {
    "onboarded": ("hired", "accepted"),
    "abandoned": ("refused", "abandoned"),
}


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_hired_application(conn, application_id="app-1", start_date=START_DATE):
    conn.execute(
        "INSERT INTO job (id, title, department, status) "
        "VALUES ('j1', '嵌入式工程师', '研发部', 'approved')"
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
        "VALUES (?, ?, 'j1', '研发部', ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, start_date),
    )
    conn.commit()


def _instantiated(conn, application_id="app-1"):
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


def _confirm(conn, note=None, application_id="app-1"):
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="hired",
        actor_username="hr-user", note=note, today=TODAY,
    )


def _abandon(conn, application_id="app-1"):
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="abandoned",
        actor_username="hr-user", reason="接受其他公司",
    )


def _assert_invariants(conn, application_id="app-1"):
    """任何一步之后都必须成立的恒等式。"""
    terminal = terminal_history_rows(conn, application_id)
    app_status = conn.execute(
        "SELECT status FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    offer_status = conn.execute(
        "SELECT status FROM offer WHERE application_id = ?", (application_id,)
    ).fetchone()[0]
    checklist = conn.execute(
        "SELECT id, status FROM onboarding_checklist WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    queue_count = conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id = ?",
        (application_id,),
    ).fetchone()[0]

    assert len(terminal) <= 1, f"同一投递出现 {len(terminal)} 条终态事实"
    assert conn.execute(
        "SELECT COUNT(*) FROM rejection_record r JOIN application a ON a.id = r.application_id "
        "WHERE a.status = 'refused'"
    ).fetchone()[0] == 0

    if not terminal:
        assert queue_count == 0
        assert app_status != "refused"
        return

    action = terminal[0][1]
    expected_app, expected_offer = _TERMINAL_CONSISTENCY[action]
    assert app_status == expected_app, f"{action} 后 application.status={app_status}"
    assert offer_status == expected_offer, f"{action} 后 offer.status={offer_status}"
    if checklist is not None:
        assert checklist[1] == "closed"
    assert queue_count == (6 if action == "onboarded" else 0)
    if app_status == "refused":
        assert [row[1] for row in terminal] == ["abandoned"]


_STEPS = {
    "confirm": lambda conn: _confirm(conn),
    "confirm_with_note": lambda conn: _confirm(conn, note="体检报告后补"),
    "abandon": lambda conn: _abandon(conn),
}

SEQUENCES = {
    "confirm": ("confirm",),
    "confirm_rerun": ("confirm", "confirm"),
    "confirm_then_abandon": ("confirm", "abandon"),
    "confirm_with_note": ("confirm_with_note",),
    "confirm_with_note_then_abandon": ("confirm_with_note", "abandon"),
    "abandon": ("abandon",),
    "abandon_rerun": ("abandon", "abandon"),
    "abandon_then_confirm": ("abandon", "confirm"),
}


@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_terminal_invariants_hold_across_every_sequence(conn, name):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    for step in SEQUENCES[name]:
        try:
            _STEPS[step](conn)
        except OnboardingTerminalRejected:
            pass
        _assert_invariants(conn)


def test_invariants_hold_when_abandoning_without_checklist(conn):
    _seed_hired_application(conn)
    _abandon(conn)
    _assert_invariants(conn)


def test_invariants_hold_when_confirm_is_rejected_before_start_date(conn):
    _seed_hired_application(conn, start_date="2099-01-01")
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)

    _assert_invariants(conn)


def test_enqueue_never_changes_terminal_facts(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    _confirm(conn)
    effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")
    _assert_invariants(conn)
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 6


_AUTO_TRIGGER_MARKERS = (
    "apscheduler", "BackgroundTasks", "add_job", "schedule.every",
    "threading.Timer", "asyncio.create_task",
)


def _app_sources() -> dict:
    return {
        path: path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "app").rglob("*.py"))
    }


def test_only_the_terminal_node_writes_the_onboarded_fact():
    hits = {
        path.relative_to(ROOT)
        for path, text in _app_sources().items()
        if "onboarded" in text
    }
    assert hits == {
        Path("app/graph/onboarding_nodes.py"),
        Path("app/storage/db.py"),
    }, f"终态事实的字面量出现在预期外的文件: {sorted(str(p) for p in hits)}"


def test_terminal_calls_are_limited_to_the_hr_surface():
    callers = {
        path.relative_to(ROOT)
        for path, text in _app_sources().items()
        if "effect_complete_onboarding(" in text
    }
    assert callers <= {
        Path("app/graph/onboarding_nodes.py"),
        Path("app/web/server.py"),
    }, f"终态节点被预期外的文件调用: {sorted(str(p) for p in callers)}"


def test_no_scheduling_construct_in_terminal_callers():
    for path, text in _app_sources().items():
        if "effect_complete_onboarding(" not in text:
            continue
        for marker in _AUTO_TRIGGER_MARKERS:
            assert marker not in text, f"{path} 出现调度构造 {marker}"


def test_terminal_node_requires_a_human_actor():
    parameters = inspect.signature(onboarding_nodes.effect_complete_onboarding).parameters
    assert "actor_username" in parameters
    assert parameters["actor_username"].default is inspect.Parameter.empty
    assert parameters["actor_username"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["business_key"].default is inspect.Parameter.empty
