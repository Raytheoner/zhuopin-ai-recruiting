"""voice_host/adapters.py：ASR/TTS Protocol 适配器与 Fake 实现。"""
from voice_host.adapters import FakeASRAdapter, FakeTTSAdapter, TranscriptResult


def test_fake_tts_records_played_text_and_consumes_scripted_latency():
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80, 60])
    first = tts.synthesize_and_play("题目一", at_ms=0)
    second = tts.synthesize_and_play("题目二", at_ms=1000)
    assert tts.played == ["题目一", "题目二"]
    assert (first, second) == (80, 60)


def test_fake_tts_repeats_last_value_after_sequence_exhausted():
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    assert tts.synthesize_and_play("a", at_ms=0) == 80
    assert tts.synthesize_and_play("b", at_ms=0) == 80


def test_fake_asr_returns_scripted_results_in_order():
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="回答一", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="回答二", confidence=0.8, endpoint_detection_ms=280, asr_ms=140, interrupted_offset_ms=220),
    ])
    first = asr.transcribe_turn()
    second = asr.transcribe_turn()
    assert first.text == "回答一"
    assert second.interrupted_offset_ms == 220
