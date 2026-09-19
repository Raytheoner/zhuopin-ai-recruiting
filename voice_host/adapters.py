"""ASR/TTS 的适配器接口（voice-structured-interview U4 tasks 5.4，design
D15"ASR/TTS 只允许自托管"；本计划「设计决策 9/10/12」）。

真实实现（`FunASRStreamingAdapter`/`CosyVoiceSubprocessAdapter`）在函数体
内部才 import funasr/发起 subprocess——funasr 是重依赖，CosyVoice 走独立
子进程（设计决策 10：`grpcio` 版本冲突与 Python 3.10 要求，与主进程的
livekit-agents/FunASR 依赖树隔离）。顶层 import 会让本文件在没装这些包的
机器（含本仓库的开发/CI 环境）上直接 import 失败，连 Fake 实现都用不了。
惰性导入的写法与 scripts/probe_m3_voice.py 的既有先例一致。

`FakeTTSAdapter`/`FakeASRAdapter` 供 worker.py 的单测（Task 11）与 Task 15
的内部模拟 e2e 使用，不接触任何真实音频/网络。

`TranscriptResult.interrupted_offset_ms`：真实的 FunASR + VAD 流式识别管线
能在候选人开口的那一刻上报"打断发生在播报开始后第几毫秒"，这不是测试专用
字段，是真实语音管线的自然输出（本计划「设计决策 12」）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class TTSAdapter(Protocol):
    def synthesize_and_play(self, text: str, *, at_ms: int) -> int:
        """播报文本，返回首帧延迟 ms（`tts_first_frame_ms`）。真实实现里这
        个调用会阻塞到 TTS 首帧就绪、把音频推进 LiveKit 房间。"""
        ...


class ASRAdapter(Protocol):
    def transcribe_turn(self) -> "TranscriptResult":
        """阻塞直到端点检测判定候选人说完（或候选人打断了播报），返回这
        一轮的转写结果。"""
        ...


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    confidence: float
    endpoint_detection_ms: float
    asr_ms: float
    interrupted_offset_ms: int | None = None
    replay_requested: bool = False
    """候选人说"再说一遍"或点击重听（live-voice-interview-session spec
    Scenario「候选人请求重听」）。真实的 FunASR 管线通过关键词识别
    （"再说一遍"/"重听"）或候选人端按钮触发的 data channel 消息上报这个
    标记；`_ask_one_turn`（voice_host/worker.py）据此重新播报同一题，不计入
    追问次数、不产生新的 turn（本计划「设计决策 15」）。"""


@dataclass
class FakeTTSAdapter:
    """测试/内部模拟用：不真的合成音频，记录被要求播报过的文本，首帧延迟
    走脚本化序列（耗尽后复用最后一个值，与 FakeASRAdapter 刻意不同——TTS
    首帧延迟在真实场景里波动小，复用最后一个值是合理近似；ASR 转写内容
    每轮完全不同，耗尽就该报错提醒轮数算错，见 FakeASRAdapter 文档）。"""

    first_frame_ms_sequence: list[int] = field(default_factory=lambda: [80])
    played: list[str] = field(default_factory=list)

    def synthesize_and_play(self, text: str, *, at_ms: int) -> int:
        self.played.append(text)
        if len(self.first_frame_ms_sequence) > 1:
            return self.first_frame_ms_sequence.pop(0)
        return self.first_frame_ms_sequence[0]


@dataclass
class FakeASRAdapter:
    """脚本化转写序列：worker.py 每调用一次 `transcribe_turn` 消费一条。
    序列耗尽会让 list.pop(0) 抛 `IndexError`——刻意设计，用来在测试里及早
    暴露"轮数算错"的问题，而不是静默复用。"""

    results: list[TranscriptResult] = field(default_factory=list)

    def transcribe_turn(self) -> TranscriptResult:
        return self.results.pop(0)
