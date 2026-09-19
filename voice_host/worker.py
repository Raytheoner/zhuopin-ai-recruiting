"""语音主机 agents worker 核心编排（voice-structured-interview U4 tasks
5.4/5.5，design D17/D19/D20）。

按题序播报 → 端点检测＋流式转写 → 追问选择（D17 纯函数，越界/异常按下一题
处理）→ 追问或下一题；每轮把 turn 写入本地事件队列（voice_host/queue_store.
py，D19"worker 的输出是 turn 事件队列"）。⛔ 不写任何 `.51` 数据库——本文件
不 import `app.storage`。

打断处理委托给 voice_host/turn_cycle.py 的纯状态机；ASR/TTS 通过
voice_host/adapters.py 的 Protocol 注入。追问选择复用
app.agents.follow_up_selector（生产部署时是 voice_host/_vendor/ 下的物理
拷贝，Task 11 Step 1；本文件的 import 语句写的是 `.51` 仓库路径，因为测试
与生产在同一个 Python 包命名空间下都能解析到——`.51` 检出时解析到
app.agents 包本身，语音主机部署检出时 sync-to-voice-host.sh 只同步了
app/agents/follow_up_selector.py 这一个文件到对应相对路径，import 路径
字面一致）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app.agents.follow_up_selector import FollowUpChoice, select
from app.llm.gateway import LLMGateway, LLMProviderUnavailable, SchemaExtractionFailed
from app.schemas.interview_ai_input import FollowUpInput
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion

from voice_host import queue_store
from voice_host.adapters import ASRAdapter, TTSAdapter
from voice_host.turn_cycle import TurnCycleController
from voice_host.recording import RecordingAdapter, RecordingStartFailed


@dataclass
class WorkerTurnResult:
    seq: int
    question_id: str
    question_text: str
    answer_text: str
    answer_mode: str
    audio_start_ms: int | None
    audio_end_ms: int | None
    asr_confidence: float | None
    follow_up_of_seq: int | None
    interrupted_at_ms: int | None
    latency: dict = field(default_factory=dict)


def _clock_ms() -> int:
    return int(time.monotonic() * 1000)


MAX_REPLAY_ATTEMPTS = 3
"""重听不计入追问次数（live-voice-interview-session spec Scenario「候选人
请求重听」），但仍设一个防御性上限——候选人反复触发不应让这一轮问答永远
无法收敛（本计划「设计决策 15」）。"""


def _ask_one_turn(
    *, question_id: str, question_text: str, tts: TTSAdapter, asr: ASRAdapter,
    seq: int, follow_up_of_seq: int | None, is_follow_up: bool, answer_mode: str,
) -> WorkerTurnResult:
    if answer_mode == "text":
        transcript = asr.transcribe_turn()  # 这里的 asr 实际是 TextAnswerAdapter，满足同一 Protocol
        return WorkerTurnResult(
            seq=seq, question_id=question_id, question_text=question_text,
            answer_text=transcript.text, answer_mode="text",
            audio_start_ms=None, audio_end_ms=None,
            asr_confidence=transcript.confidence, follow_up_of_seq=follow_up_of_seq,
            interrupted_at_ms=None,
            latency={"end_to_end_ms": transcript.asr_ms},
        )

    turn_cycle = TurnCycleController()
    audio_start_ms = _clock_ms()
    turn_cycle.start_broadcast(question_seq=seq, at_ms=audio_start_ms, is_follow_up=is_follow_up)
    tts_first_frame_ms = tts.synthesize_and_play(question_text, at_ms=audio_start_ms)
    transcript = asr.transcribe_turn()

    replay_attempts = 0
    while transcript.replay_requested and replay_attempts < MAX_REPLAY_ATTEMPTS:
        replay_attempts += 1
        turn_cycle = TurnCycleController()
        audio_start_ms = _clock_ms()
        turn_cycle.start_broadcast(question_seq=seq, at_ms=audio_start_ms, is_follow_up=is_follow_up)
        tts_first_frame_ms = tts.synthesize_and_play(question_text, at_ms=audio_start_ms)
        transcript = asr.transcribe_turn()

    if transcript.interrupted_offset_ms is not None:
        turn_cycle.on_candidate_speech_started(at_ms=audio_start_ms + transcript.interrupted_offset_ms)

    audio_end_ms = audio_start_ms + int(transcript.endpoint_detection_ms + transcript.asr_ms)
    turn_cycle.on_broadcast_finished_naturally(at_ms=audio_end_ms)

    end_to_end_ms = transcript.endpoint_detection_ms + transcript.asr_ms + tts_first_frame_ms

    return WorkerTurnResult(
        seq=seq, question_id=question_id, question_text=question_text,
        answer_text=transcript.text, answer_mode="voice",
        audio_start_ms=audio_start_ms, audio_end_ms=audio_end_ms,
        asr_confidence=transcript.confidence, follow_up_of_seq=follow_up_of_seq,
        interrupted_at_ms=turn_cycle.truncated_at_ms,
        latency={
            "endpoint_detection_ms": transcript.endpoint_detection_ms,
            "asr_ms": transcript.asr_ms,
            "tts_first_frame_ms": tts_first_frame_ms,
            "end_to_end_ms": end_to_end_ms,
        },
    )


def _select_follow_up(gateway: LLMGateway, question: SessionBundleQuestion, transcript: str) -> FollowUpChoice:
    """追问选择的异常兜底（本计划「设计决策 11」）：结构化输出重试耗尽
    （`SchemaExtractionFailed`）或供应商不可用（`LLMProviderUnavailable`）
    一律按"进入下一题"处理——语音回路不能因为一次 LLM 调用失败卡死。"""
    follow_up_input = FollowUpInput(
        question_text=question.text, follow_ups=question.follow_ups, transcript=transcript,
    )
    try:
        return select(gateway, follow_up_input)
    except (SchemaExtractionFailed, LLMProviderUnavailable):
        return FollowUpChoice(
            is_follow_up=False, follow_up_index=None, out_of_range=False, run_id="", response_model=None,
        )


def _persist(conn, *, session_id: str, result: WorkerTurnResult) -> None:
    queue_store.append_turn_event(
        conn, session_id=session_id, seq=result.seq, question_id=result.question_id,
        question_text=result.question_text, answer_text=result.answer_text,
        answer_mode=result.answer_mode, audio_start_ms=result.audio_start_ms,
        audio_end_ms=result.audio_end_ms, asr_confidence=result.asr_confidence,
        follow_up_of_seq=result.follow_up_of_seq, interrupted_at_ms=result.interrupted_at_ms,
        latency=result.latency,
    )


def run_session(
    *, conn, bundle: SessionBundle, gateway: LLMGateway, tts: TTSAdapter, asr: ASRAdapter,
    recorder: RecordingAdapter, text_adapter: ASRAdapter | None = None,
) -> str:
    """跑完整场次：先启动录制（失败 ⇒ 直接 interrupted），再按
    `bundle.questions` 顺序逐题问答——每题开始前查一次
    `queue_store.get_current_answer_mode`（tasks 5.7"同一场次 MUST 允许
    混合"），语音模式用 `asr`、文本模式用 `text_adapter`（两者满足同一
    `ASRAdapter` Protocol，`_ask_one_turn` 不关心具体是哪一种）。"""
    import logging

    logger = logging.getLogger(__name__)

    try:
        recorder.start(session_id=bundle.session_id)
    except RecordingStartFailed:
        logger.exception("场次 %s 录制启动失败，场次进入 interrupted", bundle.session_id)
        queue_store.close_session(conn, session_id=bundle.session_id, status="interrupted")
        return "interrupted"

    seq = 0
    for question in bundle.questions:
        seq += 1
        mode = queue_store.get_current_answer_mode(conn, session_id=bundle.session_id)
        active_asr = text_adapter if (mode == "text" and text_adapter is not None) else asr
        result = _ask_one_turn(
            question_id=question.question_id, question_text=question.text,
            tts=tts, asr=active_asr, seq=seq, follow_up_of_seq=None, is_follow_up=False,
            answer_mode=mode if text_adapter is not None else "voice",
        )
        _persist(conn, session_id=bundle.session_id, result=result)

        last_turn_seq_for_question = seq
        follow_up_count = 0
        while follow_up_count < min(bundle.follow_up_limit, len(question.follow_ups)):
            selection_start_ms = _clock_ms()
            choice = _select_follow_up(gateway, question, result.answer_text)
            follow_up_selection_ms = _clock_ms() - selection_start_ms

            if not choice.is_follow_up:
                break

            follow_up_count += 1
            seq += 1
            follow_up_text = question.follow_ups[choice.follow_up_index]
            follow_up_mode = queue_store.get_current_answer_mode(conn, session_id=bundle.session_id)
            follow_up_asr = text_adapter if (follow_up_mode == "text" and text_adapter is not None) else asr
            follow_up_result = _ask_one_turn(
                question_id=question.question_id, question_text=follow_up_text,
                tts=tts, asr=follow_up_asr, seq=seq,
                follow_up_of_seq=last_turn_seq_for_question, is_follow_up=True,
                answer_mode=follow_up_mode if text_adapter is not None else "voice",
            )
            follow_up_result.latency["follow_up_selection_ms"] = follow_up_selection_ms
            follow_up_result.latency["end_to_end_ms"] += follow_up_selection_ms
            _persist(conn, session_id=bundle.session_id, result=follow_up_result)

            last_turn_seq_for_question = seq
            result = follow_up_result

    path = recorder.stop(session_id=bundle.session_id)
    from voice_host.recording import finalize_recording
    finalize_recording(conn, session_id=bundle.session_id, path=path)

    queue_store.close_session(conn, session_id=bundle.session_id, status="completed")
    return "completed"
