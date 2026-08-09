from __future__ import annotations

from typing import TypedDict


class IntakeState(TypedDict, total=False):
    job_id: str
    history: list[dict]
    round_count: int
    profile_patch_accumulated: dict
    pending_questions: list[str]
    is_complete: bool
    is_job_related: bool
    unspecified_fields: list[str]
