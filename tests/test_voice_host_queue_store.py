"""voice_host/queue_store.py：语音主机本地事件队列（design D19"worker 的输出
是 turn 事件队列（本地 SQLite 队列，被 .51 拉走后标记）与录音文件"）。全部
用 stdlib sqlite3，不依赖 app.storage（语音主机结构上不可能访问 `.51` 的
数据库）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voice_host import queue_store


@pytest.fixture
def conn(tmp_path):
    connection = queue_store.get_connection(str(tmp_path / "queue.db"))
    queue_store.init_schema(connection)
    return connection


def test_open_session_is_idempotent(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json='{"session_id": "s1"}')
    queue_store.open_session(conn, session_id="s1", bundle_json='{"session_id": "s1"}')
    assert queue_store.get_session_status(conn, session_id="s1") == "in_progress"


def test_append_turn_event_and_events_since(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.append_turn_event(
        conn, session_id="s1", seq=1, question_id="q1", question_text="t", answer_text="a",
        answer_mode="voice", audio_start_ms=0, audio_end_ms=3000, asr_confidence=0.9,
        follow_up_of_seq=None, interrupted_at_ms=None, latency={"end_to_end_ms": 500.0},
    )
    queue_store.append_turn_event(
        conn, session_id="s1", seq=2, question_id="q1", question_text="t2", answer_text="a2",
        answer_mode="voice", audio_start_ms=3200, audio_end_ms=6000, asr_confidence=0.8,
        follow_up_of_seq=1, interrupted_at_ms=None, latency={"end_to_end_ms": 420.0},
    )
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["follow_up_of_seq"] == 1

    only_new = queue_store.events_since(conn, session_id="s1", since_seq=1)
    assert [e["seq"] for e in only_new] == [2]


def test_append_turn_event_is_idempotent_on_repeat_seq(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    kwargs = dict(
        session_id="s1", seq=1, question_id="q1", question_text="t", answer_text="a",
        answer_mode="text", audio_start_ms=None, audio_end_ms=None, asr_confidence=None,
        follow_up_of_seq=None, interrupted_at_ms=None, latency={},
    )
    queue_store.append_turn_event(conn, **kwargs)
    queue_store.append_turn_event(conn, **kwargs)
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert len(events) == 1


def test_close_session_updates_status(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.close_session(conn, session_id="s1", status="completed")
    assert queue_store.get_session_status(conn, session_id="s1") == "completed"


def test_record_and_get_recording_file(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="s1", path=str(tmp_path / "s1.rec"), sha256="abc123")
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info == {"path": str(tmp_path / "s1.rec"), "sha256": "abc123", "transferred": False}

    queue_store.mark_recording_transferred(conn, session_id="s1")
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info["transferred"] is True


def test_sweep_expired_intermediate_files_only_deletes_old_untransferred(conn, tmp_path):
    import time

    old_path = tmp_path / "old.rec"
    old_path.write_bytes(b"old")
    fresh_path = tmp_path / "fresh.rec"
    fresh_path.write_bytes(b"fresh")
    transferred_path = tmp_path / "transferred.rec"
    transferred_path.write_bytes(b"transferred")

    queue_store.open_session(conn, session_id="old", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="old", path=str(old_path), sha256="h1")
    queue_store.open_session(conn, session_id="fresh", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="fresh", path=str(fresh_path), sha256="h2")
    queue_store.open_session(conn, session_id="xfer", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="xfer", path=str(transferred_path), sha256="h3")
    queue_store.mark_recording_transferred(conn, session_id="xfer")

    now = time.time()
    twenty_five_hours_ago = now - 25 * 3600
    conn.execute("UPDATE recording_file SET recorded_at = ? WHERE session_id = 'old'", (twenty_five_hours_ago,))
    conn.commit()

    deleted = queue_store.sweep_expired_intermediate_files(conn, now=now, max_age_hours=24)

    assert deleted == [str(old_path)]
    assert not old_path.exists()
    assert fresh_path.exists()
    assert transferred_path.exists()


def test_record_voice_host_event(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.record_voice_host_event(conn, session_id="s1", event_type="mode_switched_manual", detail="seq=3")
    row = conn.execute(
        "SELECT event_type, detail FROM voice_host_event WHERE session_id = 's1'"
    ).fetchone()
    assert row == ("mode_switched_manual", "seq=3")
