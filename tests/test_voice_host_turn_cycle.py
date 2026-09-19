"""voice_host/turn_cycle.py：播报/打断纯状态机（live-voice-interview-session
spec「打断处理」）。不做真实音频 I/O，只接收时刻事件。"""
from voice_host.turn_cycle import TurnCycleController


def test_no_interrupt_when_broadcast_finishes_naturally():
    controller = TurnCycleController()
    controller.start_broadcast(question_seq=1, at_ms=1000)
    controller.on_broadcast_finished_naturally(at_ms=1800)
    assert controller.was_interrupted is False
    assert controller.truncated_at_ms is None


def test_interrupt_records_truncation_offset():
    controller = TurnCycleController()
    controller.start_broadcast(question_seq=1, at_ms=1000)
    offset = controller.on_candidate_speech_started(at_ms=1220)
    assert offset == 220
    assert controller.was_interrupted is True
    assert controller.truncated_at_ms == 220


def test_second_speech_started_call_after_stop_is_a_noop():
    controller = TurnCycleController()
    controller.start_broadcast(question_seq=1, at_ms=1000)
    controller.on_candidate_speech_started(at_ms=1150)
    second = controller.on_candidate_speech_started(at_ms=1400)
    assert second is None
    assert controller.truncated_at_ms == 150  # 第一次记录的截断点不被覆盖


def test_speech_started_without_active_broadcast_is_a_noop():
    controller = TurnCycleController()
    offset = controller.on_candidate_speech_started(at_ms=500)
    assert offset is None
    assert controller.was_interrupted is False


def test_each_question_needs_a_fresh_controller_instance():
    # 设计决策：⛔ 不跨题复用同一个 controller（Task 10 docstring）。这个测试
    # 只是确认"新实例状态是干净的"这个前提成立，不测试 worker.py 的调用方式
    # （那部分在 Task 11 测试）。
    first = TurnCycleController()
    first.start_broadcast(question_seq=1, at_ms=0)
    first.on_candidate_speech_started(at_ms=100)

    second = TurnCycleController()
    assert second.was_interrupted is False
    assert second.truncated_at_ms is None
