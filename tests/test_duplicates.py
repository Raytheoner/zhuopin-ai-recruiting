from __future__ import annotations

from app.intake.duplicates import CandidateRef, detect_suspected_duplicates, normalize_name


def test_normalize_name_folds_width_and_whitespace():
    assert normalize_name("张 三") == normalize_name("张三")
    assert normalize_name("张　三") == normalize_name("张三")
    assert normalize_name("ＡＢＣ") == normalize_name("ABC")


def test_detects_same_job_same_normalized_name():
    cand = CandidateRef("c1", "张 三")
    refs = [CandidateRef("c1", "张三"), CandidateRef("c2", "张　三"), CandidateRef("c3", "李四")]
    result = detect_suspected_duplicates(cand, refs)
    assert [c.id for c in result] == ["c2"]


def test_does_not_match_different_name():
    cand = CandidateRef("c1", "张三")
    assert detect_suspected_duplicates(cand, [CandidateRef("c2", "张伟")]) == []


def test_never_returns_candidate_itself():
    cand = CandidateRef("c1", "张三")
    assert detect_suspected_duplicates(cand, [CandidateRef("c1", "张三")]) == []
