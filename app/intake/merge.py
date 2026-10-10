"""候选人挂接与合并（channel-resume-intake U2 tasks 2.2/2.5/2.6）。

三个独占的副作用执行单元，各自带幂等键、经 @idempotent_effect 与 effect_log
同事务提交（工程铁律 1）。关联变更只动 application.candidate_id——resume 上
没有 candidate_id 列（M2 注释明确），candidate 与 resume 的关联唯一经 application。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.intake.duplicates import normalize_name
from app.storage.idempotency import idempotent_effect


class MergeValidationError(ValueError):
    """合并/撤销的前置校验失败（候选人不存在、已合并、缺保留选择等）。"""


def _find_candidate(conn: sqlite3.Connection, name_norm: str, phone_hash: str | None) -> str | None:
    """按 (规范化姓名, 手机号哈希) 查未合并候选人。无手机号 ⇒ 恒不命中（spec
    「无手机号 → 新建候选人」：NULL 不参与匹配，宁留重复也不错误合并）。"""
    if phone_hash is None:
        return None
    row = conn.execute(
        "SELECT id FROM candidate WHERE name = ? AND phone_hash = ? AND merged_into IS NULL",
        (name_norm, phone_hash),
    ).fetchone()
    return row[0] if row else None


def _create_application(
    conn: sqlite3.Connection, *, candidate_id: str, job_id: str, resume_id: str
) -> str:
    application_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (application_id, candidate_id, job_id, resume_id),
    )
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type) "
        "VALUES (?, ?, NULL, 'initial', 'agent')",
        (str(uuid.uuid4()), application_id),
    )
    return application_id


@idempotent_effect("effect_attach_resume_to_candidate")
def effect_attach_resume_to_candidate(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    resume_id: str,
    job_id: str,
    name: str,
    phone_hash: str | None,
) -> dict:
    """把一份已解析简历挂到候选人：按 (规范化姓名, 哈希) 查未合并候选人，命中复用、
    不命中新建；创建 application + 初始流转事实。幂等键 {resume_id}:effect_attach_resume_to_candidate:once。"""
    existing = conn.execute(
        "SELECT id, candidate_id FROM application WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    if existing is not None:
        return {"application_id": existing[0], "candidate_id": existing[1], "candidate_created": False}

    name_norm = normalize_name(name) or "姓名待校对"
    candidate_id = _find_candidate(conn, name_norm, phone_hash)
    candidate_created = candidate_id is None
    if candidate_created:
        candidate_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO candidate (id, name, phone_hash) VALUES (?, ?, ?)",
            (candidate_id, name_norm, phone_hash),
        )
    application_id = _create_application(
        conn, candidate_id=candidate_id, job_id=job_id, resume_id=resume_id
    )
    return {"application_id": application_id, "candidate_id": candidate_id, "candidate_created": candidate_created}
