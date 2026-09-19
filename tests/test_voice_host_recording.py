"""voice_host/recording.py：全程录制生命周期（interview-recording-retention
spec「语音主机不留副本」；live-voice-interview-session spec「全程录制与
turn 对齐」「录制中断」）。"""
import hashlib

import pytest

from voice_host import queue_store
from voice_host.recording import FakeRecordingAdapter, RecordingStartFailed, finalize_recording


@pytest.fixture
def conn(tmp_path):
    connection = queue_store.get_connection(str(tmp_path / "queue.db"))
    queue_store.init_schema(connection)
    queue_store.open_session(connection, session_id="s1", bundle_json="{}")
    return connection


def test_recorder_stop_returns_readable_file(tmp_path):
    recorder = FakeRecordingAdapter(data_dir=tmp_path)
    recorder.start(session_id="s1")
    path = recorder.stop(session_id="s1")
    assert path.exists()
    assert recorder.started == ["s1"]


def test_recorder_fail_on_start_raises(tmp_path):
    recorder = FakeRecordingAdapter(data_dir=tmp_path, fail_on_start=True)
    with pytest.raises(RecordingStartFailed):
        recorder.start(session_id="s1")


def test_finalize_recording_registers_sha256_in_queue_store(conn, tmp_path):
    recorder = FakeRecordingAdapter(data_dir=tmp_path)
    recorder.start(session_id="s1")
    path = recorder.stop(session_id="s1")

    sha256 = finalize_recording(conn, session_id="s1", path=path)

    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha256 == expected
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info["sha256"] == expected
    assert info["path"] == str(path)
