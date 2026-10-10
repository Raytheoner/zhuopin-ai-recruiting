"""onboarding-flow U2 节点：effect_instantiate_checklist（2.1）与 effect_update_item（2.2）。"""
import json

import pytest

from app.graph.onboarding_nodes import (
    ChecklistInstantiateRejected,
    ChecklistTemplateMissing,
    effect_instantiate_checklist,
)
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_job_candidate_resume(conn, department="研发部"):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES ('j1', '嵌入式工程师', ?, 'approved')",
        (department,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.commit()


def _seed_hired_application(conn, application_id="app-1", department="研发部", start_date="2026-10-20"):
    _seed_job_candidate_resume(conn, department=department)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', 'hired')",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, department, start_date),
    )
    conn.commit()


def _seed_account(conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id))
    conn.commit()
    return account_id


# ── Task 2：effect_instantiate_checklist ────────────────────────────────


def test_instantiate_creates_checklist_and_items(conn):
    _seed_hired_application(conn)
    checklist_id = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    assert checklist_id is not None
    cl = conn.execute(
        "SELECT application_id, template_version, start_date, status FROM onboarding_checklist WHERE id=?",
        (checklist_id,),
    ).fetchone()
    assert cl == ("app-1", 1, "2026-10-20", "open")
    items = conn.execute(
        "SELECT name, owner_party, status FROM onboarding_item WHERE checklist_id=? ORDER BY rowid",
        (checklist_id,),
    ).fetchall()
    assert len(items) == 6
    assert all(status == "pending" for _, _, status in items)


def test_instantiate_rejects_non_hired_application(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'initial', 'active')"
    )
    conn.commit()
    with pytest.raises(ChecklistInstantiateRejected):
        effect_instantiate_checklist(
            conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
        )


def test_instantiate_rejects_hired_without_accepted_offer(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired', 'hired')"
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES ('offer-1', 'app-1', 'j1', '研发部', '2026-10-20', 'manager-1', 'pending_approval', 1, 'hr')"
    )
    conn.commit()
    with pytest.raises(ChecklistInstantiateRejected):
        effect_instantiate_checklist(
            conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
        )


def test_instantiate_rerun_returns_none_and_keeps_single_checklist(conn):
    _seed_hired_application(conn)
    first = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    second = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    assert first is not None
    assert second is None
    assert conn.execute("SELECT COUNT(*) FROM onboarding_checklist WHERE application_id='app-1'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM onboarding_item i JOIN onboarding_checklist c ON c.id=i.checklist_id "
        "WHERE c.application_id='app-1'"
    ).fetchone()[0] == 6


def test_instantiate_prefers_job_template_over_department(conn):
    _seed_hired_application(conn)
    conn.execute(
        "INSERT INTO onboarding_template (id, scope_type, scope_id, version, items, updated_by) "
        "VALUES ('job-tpl', 'job', 'j1', 1, ?, 'hr')",
        (json.dumps([{"name": "岗位专属条目", "owner_party": "hr", "due_offset_days": 0, "required": True}], ensure_ascii=False),),
    )
    conn.commit()
    checklist_id = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    items = conn.execute("SELECT name FROM onboarding_item WHERE checklist_id=?", (checklist_id,)).fetchall()
    assert items == [("岗位专属条目",)]


def test_instantiate_missing_template_rejected(conn):
    _seed_hired_application(conn)
    conn.execute("DELETE FROM onboarding_template")
    conn.commit()
    with pytest.raises(ChecklistTemplateMissing):
        effect_instantiate_checklist(
            conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
        )
