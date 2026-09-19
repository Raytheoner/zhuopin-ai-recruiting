"""语音主机下发快照 schema（voice-structured-interview U4 tasks 5.3，design
D19）。

live-voice-interview-session spec「简历数据不进语音主机」：语音主机只接收
题目文本／题序／预埋追问／追问次数上限／场次标识；MUST NOT 接收简历内容、
候选人姓名、联系方式或评分数据。`model_config = ConfigDict(extra="forbid")`
与 app/schemas/interview_ai_input.py 同一手法——调用方传错键在构造对象那
一刻直接失败。字段范围裁定见本计划「设计决策 4」：额外排除
dimension/difficulty/rubric_json（评分相关），比 D19 字面要求更严格。

本文件是 `.51` 与语音主机共享的契约唯一源码。部署时 `sync-to-voice-host.sh`
（Task 14）把这个文件原样拷贝进语音主机部署目录，语音主机侧
`voice_host/api.py` 用同一个类解析下发的 JSON——两侧永远读同一份源码，不会
出现字段定义漂移（设计决策 2）。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SessionBundleQuestion(BaseModel):
    """冻结题目快照里的一题，语音主机只拿到播报与追问选择需要的字段。"""
    model_config = ConfigDict(extra="forbid")

    question_id: str
    seq: int = Field(ge=1)
    text: str = Field(min_length=1)
    follow_ups: list[str] = Field(default_factory=list)


class SessionBundle(BaseModel):
    """POST /sessions 的请求体。`app/live_voice/client.py::VoiceHostClient.
    create_session`（Task 4）序列化这个模型；`voice_host/api.py`（Task 9）
    反序列化同一个模型的拷贝。"""
    model_config = ConfigDict(extra="forbid")

    session_id: str
    prep_curve: str
    follow_up_limit: int = Field(ge=0)
    questions: list[SessionBundleQuestion] = Field(min_length=1)
