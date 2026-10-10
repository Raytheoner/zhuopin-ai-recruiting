"""冲突检查纯函数的单元测试（tasks 2.1 的三个 spec Scenario + 边界）。"""
from __future__ import annotations

from app.agents.conflict_check import (
    CONFLICT_CANDIDATE_BUSY,
    CONFLICT_INTERVIEWER_BUSY,
    CONFLICT_NO_AVAILABILITY,
    CandidateWindow,
    ExistingSlot,
    check,
)


def _cand(
    app="a1", start="2026-10-14 06:00", end="2026-10-14 07:00"
) -> CandidateWindow:
    return CandidateWindow(application_id=app, start_at=start, end_at=end)


def test_no_conflicts_when_available_and_free():
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=[],
    )
    assert conflicts == []


def test_missing_availability_reported():
    conflicts = check(
        candidate=_cand(), interviewer_ids=["iv1"],
        availability_by_interviewer={}, existing_slots=[],
    )
    assert [c.code for c in conflicts] == [CONFLICT_NO_AVAILABILITY]


def test_availability_must_fully_contain_window():
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:30", "2026-10-14 08:00")]},
        existing_slots=[],
    )
    assert [c.code for c in conflicts] == [CONFLICT_NO_AVAILABILITY]


def test_interviewer_busy_reported():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a2",
            start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
            interviewer_ids=["iv1"],
        )
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert [c.code for c in conflicts] == [CONFLICT_INTERVIEWER_BUSY]


def test_candidate_busy_reported():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a1",
            start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
            interviewer_ids=["iv2"],
        )
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert [c.code for c in conflicts] == [CONFLICT_CANDIDATE_BUSY]


def test_multiple_conflicts_all_reported():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a2",
            start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
            interviewer_ids=["iv1"],
        ),
        ExistingSlot(
            slot_id="s2", application_id="a1",
            start_at="2026-10-14 06:15", end_at="2026-10-14 06:30",
            interviewer_ids=["iv9"],
        ),
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert sorted(c.code for c in conflicts) == sorted(
        [CONFLICT_INTERVIEWER_BUSY, CONFLICT_CANDIDATE_BUSY]
    )


def test_adjacent_slots_do_not_overlap():
    slots = [
        ExistingSlot(
            slot_id="s1", application_id="a1",
            start_at="2026-10-14 05:00", end_at="2026-10-14 06:00",
            interviewer_ids=["iv1"],
        )
    ]
    conflicts = check(
        candidate=_cand(),
        interviewer_ids=["iv1"],
        availability_by_interviewer={"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]},
        existing_slots=slots,
    )
    assert conflicts == []
