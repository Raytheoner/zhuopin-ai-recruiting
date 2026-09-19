"""全程录制（voice-structured-interview U4 tasks 5.6，interview-recording-
retention spec「语音主机不留副本」，live-voice-interview-session spec「全程
录制与 turn 对齐」「录制中断」）。

真实实现用 LiveKit 的 room composite egress（服务端合成录制，不是 worker
进程自己拼音频字节）——`LiveKitEgressRecordingAdapter` 惰性 import
`livekit.api`，理由与 voice_host/adapters.py 一致。`FakeRecordingAdapter`
供测试与 Task 15 内部模拟使用，把"录制"模拟成往本地文件写一段占位字节。

中间文件 24h 过期清理见 voice_host/queue_store.py::sweep_expired_
intermediate_files（Task 8 已交付，本文件只负责录制开始/结束与"录制失败 ⇒
interrupted"这段判定逻辑，不重复实现清理）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class RecordingStartFailed(Exception):
    """录制未能开始（spec「录制失败 MUST 使场次进入中断待续」）。"""


class RecordingAdapter(Protocol):
    def start(self, *, session_id: str) -> None: ...

    def stop(self, *, session_id: str) -> Path:
        """返回本地录制文件路径；文件必须已经落盘完毕（同步等待，不是
        fire-and-forget）。"""
        ...

    def abort(self, *, session_id: str) -> None: ...


@dataclass
class FakeRecordingAdapter:
    data_dir: Path
    fail_on_start: bool = False
    started: list[str] = field(default_factory=list)

    def start(self, *, session_id: str) -> None:
        if self.fail_on_start:
            raise RecordingStartFailed(f"模拟录制启动失败: {session_id!r}")
        self.started.append(session_id)

    def stop(self, *, session_id: str) -> Path:
        path = Path(self.data_dir) / f"{session_id}.rec"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fake-recording:{session_id}".encode("utf-8"))
        return path

    def abort(self, *, session_id: str) -> None:
        return None


def finalize_recording(conn, *, session_id: str, path: Path) -> str:
    """录制文件落盘后计算哈希、登记进 queue_store（供 GET /sessions/{id}/
    recording 与 DELETE /sessions/{id}/artifacts 使用），返回 sha256
    十六进制串。"""
    from voice_host import queue_store  # 延迟导入避免循环依赖

    content = path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    queue_store.record_recording_file(conn, session_id=session_id, path=str(path), sha256=sha256)
    return sha256
