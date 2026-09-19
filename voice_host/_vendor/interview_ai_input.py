"""M3 面试域 AI 调用输入 schema：prep 出题／追问选择／post 评分三处 LLM 调用
的输入契约（design D1/D2/D17/D19，tasks.md 2.7）。

字段白名单是结构性的，不是运行时校验补丁：三个模型物理上不存在候选人身份
字段（姓名/手机号）、身份核验字段（核验结果/尝试次数/核验时刻）、声学情绪
字段（语速/停顿/静默）；`model_config = ConfigDict(extra="forbid")` 让调用方
传错键在构造对象那一刻直接失败，而不是被悄悄透传进最终拼给 LLM 网关的
prompt。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResumeScoreItem(BaseModel):
    """简历评分摘录（M2 candidate-ranking 产出的逐维得分+证据摘录）。"""
    model_config = ConfigDict(extra="forbid")

    criterion_key: str
    score: float
    evidence_excerpt: str


class PrepInput(BaseModel):
    """prep 出题（app/agents/interview_prep.py::generate，U2 tasks 3.1）的输入。

    interview-prep-question-engine spec「按画像与简历弱点生成题目」：
    输入 MUST 只包含冻结画像与 rubric、该投递的简历评分结果；
    MUST NOT 包含候选人姓名、联系方式等身份字段。
    """
    model_config = ConfigDict(extra="forbid")

    profile: dict
    rubric_dimensions: list[str] = Field(min_length=1)
    resume_scores: list[ResumeScoreItem] = Field(default_factory=list)


class FollowUpInput(BaseModel):
    """追问选择（app/agents/follow_up_selector.py::select，design D17，
    U4 tasks 5.4）的输入。

    live-voice-interview-session spec「追问只在预埋集合内选择」：追问选择
    是无副作用纯函数，输入只有当前题目、其预埋追问集合、候选人本轮转写。
    """
    model_config = ConfigDict(extra="forbid")

    question_text: str
    follow_ups: list[str] = Field(min_length=1)
    transcript: str


class ScoreInputTurn(BaseModel):
    """post 评分的单条 turn 输入。⛔ 刻意不包含 interview_turn 表里的
    asr_confidence/acoustic_ref/audio_start_ms/audio_end_ms——合规红线
    「声学信号只展示不计分」的结构性保证。"""
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    seq: int
    question_text: str
    answer_text: str


class ScoreInput(BaseModel):
    """post 评分（app/agents/interview_scoring.py::score，U5 tasks 6.2）的
    输入。

    interview-scorecard spec「逐维评分带 turn 回指」「声学信号只展示不计分」：
    评分输入 MUST NOT 包含身份核验数据、声学情绪信号或候选人身份字段。
    """
    model_config = ConfigDict(extra="forbid")

    rubric_dimensions: list[str] = Field(min_length=1)
    turns: list[ScoreInputTurn] = Field(min_length=1)
