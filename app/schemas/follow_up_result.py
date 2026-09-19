"""追问选择（app/agents/follow_up_selector.py::select，U4 tasks 5.4，design
D17）的 LLM 输出 schema。

live-voice-interview-session spec「追问只在预埋集合内选择」：输出只能是
"预埋追问集合中的某一条"或"进入下一题"，不允许模型自由生成新文本——这条
约束体现在 schema 本身只携带一个索引，不携带任何自由文本字段。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FollowUpChoiceOut(BaseModel):
    decision: Literal["follow_up", "next_question"]
    follow_up_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _follow_up_index_required_when_following_up(self):
        if self.decision == "follow_up" and self.follow_up_index is None:
            raise ValueError("decision=follow_up 时 follow_up_index 不能为空")
        return self
