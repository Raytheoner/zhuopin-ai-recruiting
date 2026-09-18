"""app/graph/invite_nodes.py：U3 邀约与同意流程的全部 compute_*/effect_*
节点（voice-structured-interview tasks 4.1-4.11）。数据库用真实 SQLite
（tmp_path），与 tests/test_interview_prep_nodes.py 同一风格。"""

from __future__ import annotations

import sqlite3

import pytest

from app.graph.invite_nodes import (
    ContactVaultUnavailableError,
    LiveInterviewNotEnabledError,
    assert_invite_issuance_allowed,
    compute_new_invite_token,
    compute_new_session,
    effect_create_interview_session,
    effect_issue_invite,
    load_invite_expiry_days,
)
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "test.db"))
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', 'ECU 工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash', 'tester')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'cand-1', 'j1', 'r1', 'initial')"
    )
    c.commit()
    return c


def _freeze_prep(conn, application_id="app1", version=1):
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES (?, 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')",
        (f"run-{application_id}-{version}",),
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES (?, ?, ?, 1, ?, 'frozen')",
        (f"snap-{application_id}-{version}", application_id, version, f"run-{application_id}-{version}"),
    )
    conn.commit()


class TestSessionCreation:
    def test_compute_new_session_defaults_pending_and_90_day_retention(self, conn):
        _freeze_prep(conn)
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        assert session["application_id"] == "app1"
        assert session["sample_class"] == "internal_sim"
        assert session["retention_policy_version"] == "v1-90d"
        assert session["id"]

    def test_effect_create_interview_session_persists_pending_status(self, conn):
        _freeze_prep(conn)
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        session_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        row = conn.execute(
            "SELECT status, sample_class FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        assert row == ("pending", "internal_sim")

    def test_effect_create_interview_session_idempotent_same_request_id(self, conn):
        _freeze_prep(conn)
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        first_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        second = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        assert second is None  # 幂等短路
        count = conn.execute(
            "SELECT COUNT(*) FROM interview_session WHERE application_id = 'app1'"
        ).fetchone()[0]
        assert count == 1


class TestIssuanceGate:
    def test_internal_sim_allowed_without_any_gate(self, conn):
        assert_invite_issuance_allowed(conn, sample_class="internal_sim")  # 不抛异常

    def test_live_rejected_when_live_interview_disabled(self, conn, monkeypatch):
        import app.graph.invite_nodes as module

        monkeypatch.setattr(module, "is_live_interview_enabled", lambda: False)
        with pytest.raises(LiveInterviewNotEnabledError):
            assert_invite_issuance_allowed(conn, sample_class="live")

    def test_live_rejected_when_vault_unavailable_even_if_gate_open(self, conn, monkeypatch):
        import app.graph.invite_nodes as module

        monkeypatch.setattr(module, "is_live_interview_enabled", lambda: True)
        monkeypatch.setattr(module, "is_contact_vault_available", lambda: False)
        with pytest.raises(ContactVaultUnavailableError):
            assert_invite_issuance_allowed(conn, sample_class="live")

    def test_live_allowed_when_both_gates_open(self, conn, monkeypatch):
        import app.graph.invite_nodes as module

        monkeypatch.setattr(module, "is_live_interview_enabled", lambda: True)
        monkeypatch.setattr(module, "is_contact_vault_available", lambda: True)
        assert_invite_issuance_allowed(conn, sample_class="live")  # 不抛异常


class TestInviteIssuance:
    def test_load_invite_expiry_days_defaults_to_7(self, conn):
        assert load_invite_expiry_days(conn, "j1") == 7

    def test_load_invite_expiry_days_reads_job_prep_config(self, conn):
        conn.execute(
            "INSERT INTO job_prep_config (job_id, invite_expiry_days) VALUES ('j1', 3)"
        )
        conn.commit()
        assert load_invite_expiry_days(conn, "j1") == 3

    def test_compute_new_invite_token_is_url_safe_and_hashable(self, conn):
        token, token_hash, expires_at = compute_new_invite_token(conn, job_id="j1")
        assert len(token) > 20
        assert len(token_hash) == 64  # sha256 hex
        assert expires_at  # ISO8601 字符串

    def test_effect_issue_invite_persists_hash_not_plaintext(self, conn):
        _freeze_prep(conn)
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        session_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        token, token_hash, expires_at = compute_new_invite_token(conn, job_id="j1")
        effect_issue_invite(
            conn, thread_id=session_id, business_key=token_hash,
            session_id=session_id, token_hash=token_hash, expires_at=expires_at,
        )
        row = conn.execute(
            "SELECT invite_token_hash, invite_expires_at FROM interview_session WHERE id = ?",
            (session_id,),
        ).fetchone()
        assert row[0] == token_hash
        assert token not in (row[0] or "")

    def test_effect_issue_invite_reissue_invalidates_old_token_and_logs(self, conn):
        _freeze_prep(conn)
        session = compute_new_session(
            conn, application_id="app1", prep_snapshot_version=1, sample_class="internal_sim"
        )
        session_id = effect_create_interview_session(
            conn, thread_id="app1", business_key="req1", session=session
        )
        _, hash1, exp1 = compute_new_invite_token(conn, job_id="j1")
        effect_issue_invite(
            conn, thread_id=session_id, business_key=hash1,
            session_id=session_id, token_hash=hash1, expires_at=exp1,
        )
        _, hash2, exp2 = compute_new_invite_token(conn, job_id="j1")
        effect_issue_invite(
            conn, thread_id=session_id, business_key=hash2,
            session_id=session_id, token_hash=hash2, expires_at=exp2,
        )
        row = conn.execute(
            "SELECT invite_token_hash FROM interview_session WHERE id = ?", (session_id,)
        ).fetchone()
        assert row[0] == hash2  # 旧哈希已被覆盖，旧令牌查不到

        events = conn.execute(
            "SELECT event_type FROM interview_invite_event WHERE session_id = ? ORDER BY at",
            (session_id,),
        ).fetchall()
        assert [e[0] for e in events] == ["issued", "reissued"]
