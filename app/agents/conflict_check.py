"""面试排期冲突检查（interview-slot-scheduling spec「冲突检查」）。

纯计算、无副作用：⛔ 本模块不得出现任何 storage 写入——不导入数据库驱动或
存储层模块、不执行任何 SQL、不持有连接。三类冲突全部列出，⛔ 不短路只报第一条：

1. interviewer_no_availability  面试官在候选时段没有已登记的可用时段
2. interviewer_busy             面试官该时刻已被另一场未取消面试占用
3. candidate_busy               同一候选人在该时刻已有另一场未取消面试
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONFLICT_NO_AVAILABILITY = "interviewer_no_availability"
CONFLICT_INTERVIEWER_BUSY = "interviewer_busy"
CONFLICT_CANDIDATE_BUSY = "candidate_busy"


@dataclass(frozen=True)
class Conflict:
    code: str
    interviewer_id: str | None = None
    detail: str = ""


class ConflictError(ValueError):
    """携带全部冲突原因的异常；调用方把它渲染成 HTTP 409 detail。"""

    def __init__(self, conflicts: list[Conflict]):
        self.conflicts = conflicts
        super().__init__("; ".join(c.detail for c in conflicts))


@dataclass(frozen=True)
class CandidateWindow:
    application_id: str
    start_at: str
    end_at: str


@dataclass(frozen=True)
class ExistingSlot:
    slot_id: str
    application_id: str
    start_at: str
    end_at: str
    interviewer_ids: list[str] = field(default_factory=list)


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """半开区间：end == start 不算重叠（上一场 15:00 结束、下一场 15:00 开始合法）。"""
    return a_start < b_end and b_start < a_end


def _available_at(
    availability: list[tuple[str, str]], start_at: str, end_at: str
) -> bool:
    """候选时段必须整个落在某一段可用时段内（不跨段拼接）。"""
    for win_start, win_end in availability:
        if win_start <= start_at and end_at <= win_end:
            return True
    return False


def check(
    *,
    candidate: CandidateWindow,
    interviewer_ids: list[str],
    availability_by_interviewer: dict[str, list[tuple[str, str]]],
    existing_slots: list[ExistingSlot],
) -> list[Conflict]:
    conflicts: list[Conflict] = []

    for interviewer_id in interviewer_ids:
        windows = availability_by_interviewer.get(interviewer_id, [])
        if not _available_at(windows, candidate.start_at, candidate.end_at):
            conflicts.append(
                Conflict(
                    code=CONFLICT_NO_AVAILABILITY,
                    interviewer_id=interviewer_id,
                    detail=f"{interviewer_id} 在 {candidate.start_at}–{candidate.end_at} 无可用时段",
                )
            )
            continue
        for slot in existing_slots:
            if interviewer_id not in slot.interviewer_ids:
                continue
            if _overlaps(
                candidate.start_at, candidate.end_at, slot.start_at, slot.end_at
            ):
                conflicts.append(
                    Conflict(
                        code=CONFLICT_INTERVIEWER_BUSY,
                        interviewer_id=interviewer_id,
                        detail=f"{interviewer_id} 在 {slot.start_at}–{slot.end_at} 已有场次 {slot.slot_id}",
                    )
                )
                break

    for slot in existing_slots:
        if slot.application_id != candidate.application_id:
            continue
        if _overlaps(
            candidate.start_at, candidate.end_at, slot.start_at, slot.end_at
        ):
            conflicts.append(
                Conflict(
                    code=CONFLICT_CANDIDATE_BUSY,
                    detail=f"该候选人在 {slot.start_at}–{slot.end_at} 已有场次 {slot.slot_id}",
                )
            )

    return conflicts
