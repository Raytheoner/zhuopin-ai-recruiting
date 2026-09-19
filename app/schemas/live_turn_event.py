"""语音主机上报的 turn 事件 schema（voice-structured-interview U4 tasks 5.2/
5.9）。`GET /sessions/{id}/events?since=` 的响应体。

与 app/schemas/session_bundle.py 同一部署形态（设计决策 2）：`.51` 侧
`app/live_voice/client.py`（Task 4）解析响应体，语音主机侧 `voice_host/api.py`
（Task 9）构造响应体，两侧 import 的是同一份被拷贝的源码。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LiveTurnEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    question_id: str
    question_text: str
    answer_text: str
    answer_mode: Literal["voice", "text"]
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None
    asr_confidence: float | None = None
    follow_up_of_seq: int | None = None
    interrupted_at_ms: int | None = None
    latency: dict = Field(default_factory=dict)


class LiveEventsPollResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[LiveTurnEvent]
    session_status: Literal["in_progress", "completed", "interrupted"]
