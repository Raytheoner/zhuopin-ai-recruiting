"""U3 存储层 `app/storage/interview_invitation.py` 的行为测试。"""
from __future__ import annotations

import sys
import types

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.interview_invitation import (
    candidate_recipient_for_invitation,
    latest_draft,
    list_drafts,
    list_outcomes,
    next_draft_version,
    slot_application_id,
    slot_facts,
    slot_status,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "invitation_storage.db"))
    init_schema(c)
    _seed(c)
    return c


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, location_or_link) "
        "VALUES ('s1', 'app1', 2, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite', "
        "'无锡市新吴区××路 1 号')"
    )
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('ha1', 'tangliping', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('iv1', 'ha1', '汤丽萍', '人事部', '[]', 1)"
    )
    conn.execute(
        "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
        "VALUES ('s1', 'iv1')"
    )
    conn.commit()


def _insert_draft(conn, *, draft_id: str, version: int) -> None:
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "VALUES (?, 's1', ?, 'v1', '您好，邀请您参加面试。', 1)",
        (draft_id, version),
    )
    conn.commit()


def test_next_draft_version_starts_at_one(conn):
    assert next_draft_version(conn, "s1") == 1


def test_next_draft_version_after_insert(conn):
    _insert_draft(conn, draft_id="d1", version=1)
    _insert_draft(conn, draft_id="d2", version=2)
    assert next_draft_version(conn, "s1") == 3


def test_latest_draft_is_highest_version(conn):
    _insert_draft(conn, draft_id="d1", version=1)
    _insert_draft(conn, draft_id="d2", version=2)
    assert latest_draft(conn, "s1")["id"] == "d2"
    assert latest_draft(conn, "no-such-slot") is None


def test_list_drafts_is_ordered_and_keeps_old_versions(conn):
    _insert_draft(conn, draft_id="d2", version=2)
    _insert_draft(conn, draft_id="d1", version=1)
    drafts = list_drafts(conn, "s1")
    assert [d["version"] for d in drafts] == [1, 2]
    assert [d["template_version"] for d in drafts] == ["v1", "v1"]
    assert all(d["ai_generated"] is True for d in drafts)


def test_slot_facts_loads_candidate_job_and_interviewer_names(conn):
    facts = slot_facts(conn, "s1")
    assert facts.candidate_name == "张三"
    assert facts.job_title == "嵌入式软件工程师"
    assert facts.round == 2
    assert facts.mode == "onsite"
    assert facts.location_or_link == "无锡市新吴区××路 1 号"
    assert facts.interviewer_names == ["汤丽萍"]


def test_slot_facts_none_for_unknown_slot(conn):
    assert slot_facts(conn, "nope") is None


def test_slot_helpers_and_empty_outcomes(conn):
    assert slot_application_id(conn, "s1") == "app1"
    assert slot_status(conn, "s1") == "scheduled"
    assert list_outcomes(conn, "s1") == []


def test_recipient_is_none_when_vault_module_missing(conn, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", None)
    assert candidate_recipient_for_invitation(conn, application_id="app1") is None


def test_recipient_is_none_when_vault_flag_off(conn, monkeypatch):
    module = types.ModuleType("app.storage.contact_vault")
    module.is_candidate_contact_vault_enabled = lambda: False
    module.read_contact = lambda *a, **k: pytest.fail("开关关闭时不得读取 vault")
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", module)
    assert candidate_recipient_for_invitation(conn, application_id="app1") is None


def test_recipient_reads_vault_when_enabled(conn, monkeypatch):
    module = types.ModuleType("app.storage.contact_vault")
    module.is_candidate_contact_vault_enabled = lambda: True

    class _Contact:
        phone = "13800000000"
        email = None

    module.read_contact = lambda *a, **k: _Contact()
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", module)
    assert candidate_recipient_for_invitation(conn, application_id="app1") == "13800000000"


def test_recipient_is_none_when_vault_read_raises(conn, monkeypatch):
    module = types.ModuleType("app.storage.contact_vault")
    module.is_candidate_contact_vault_enabled = lambda: True

    def _boom(*a, **k):
        raise RuntimeError("解密失败")

    module.read_contact = _boom
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", module)
    assert candidate_recipient_for_invitation(conn, application_id="app1") is None
