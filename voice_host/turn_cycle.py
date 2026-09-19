"""语音主机播报/打断状态机（voice-structured-interview U4 tasks 5.5，
live-voice-interview-session spec「打断处理」）。

纯状态机：不做真实音频 I/O，只接收 worker.py 从 LiveKit Agents SDK 的
VAD/端点检测回调转译出的时刻事件（候选人开始说话/播报自然结束），据此判定
播报是否被打断、截断点记在哪。真实音频 I/O 的接线在 voice_host/worker.py
（Task 11），本文件在没有安装 livekit-agents/FunASR/CosyVoice 的环境下也能
被完整单测。
"""
from __future__ import annotations

from dataclasses import dataclass

INTERRUPT_STOP_BUDGET_MS = 300
"""spec 承诺的打断响应上限（300ms 内停播报）。真实的停止耗时由
voice_host/adapters.py 的 TTS 适配器真实实现负责在这个预算内完成；本状态机
只记录候选人开口发生的时刻与截断点，不测量真实停止耗时——那是集成层面的
时序，不是纯状态机能观测到的。"""


@dataclass
class _BroadcastState:
    question_seq: int
    started_at_ms: int
    is_follow_up: bool
    stopped_at_ms: int | None = None
    interrupted: bool = False


class TurnCycleController:
    """一题的播报-作答周期。每题构造一个新实例（worker.py 按题序创建），
    ⛔ 不跨题复用。"""

    def __init__(self) -> None:
        self._state: _BroadcastState | None = None

    def start_broadcast(self, *, question_seq: int, at_ms: int, is_follow_up: bool = False) -> None:
        self._state = _BroadcastState(question_seq=question_seq, started_at_ms=at_ms, is_follow_up=is_follow_up)

    def on_candidate_speech_started(self, *, at_ms: int) -> int | None:
        """候选人开口。播报中 ⇒ 记截断点、标记打断、返回截断毫秒（相对播报
        开始的偏移，供 worker.py 写 `interview_turn.interrupted_at_ms`）；
        没有活跃播报，或播报已经因为这次调用而停止过一次 ⇒ 返回 None，不是
        一次新的"打断"（同一次打断只记一次截断点）。"""
        if self._state is None or self._state.stopped_at_ms is not None:
            return None
        offset = at_ms - self._state.started_at_ms
        self._state.stopped_at_ms = at_ms
        self._state.interrupted = True
        return offset

    def on_broadcast_finished_naturally(self, *, at_ms: int) -> None:
        if self._state is not None and self._state.stopped_at_ms is None:
            self._state.stopped_at_ms = at_ms

    @property
    def was_interrupted(self) -> bool:
        return self._state is not None and self._state.interrupted

    @property
    def truncated_at_ms(self) -> int | None:
        if self._state is None or not self._state.interrupted:
            return None
        return self._state.stopped_at_ms - self._state.started_at_ms
