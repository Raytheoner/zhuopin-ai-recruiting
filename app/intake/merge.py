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
    # channel-resume-intake U3 task 3.1：投递创建的初始流转事实带上来源
    # （resume.source）。来源改正走"追加"而不是"改写"（task 3.2，见
    # app/intake/stage_history.py），所以这条初始行此后永不更新。
    # 未标来源的简历（U1 的单文件上传路径会留 NULL）原样落 NULL——与
    # candidate_source() 的「NULL 视为 unknown」同一口径，⛔ 不回填 'unknown'。
    source_row = conn.execute(
        "SELECT source FROM resume WHERE id = ?", (resume_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, source) "
        "VALUES (?, ?, NULL, 'initial', 'agent', ?)",
        (str(uuid.uuid4()), application_id, source_row[0] if source_row else None),
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


def _snapshot_secondary(conn: sqlite3.Connection, secondary_id: str) -> dict:
    apps = conn.execute(
        "SELECT id, job_id, resume_id, current_stage_id, status, kanban_state "
        "FROM application WHERE candidate_id = ? ORDER BY created_at, id",
        (secondary_id,),
    ).fetchall()
    return {
        "secondary_id": secondary_id,
        "applications": [
            {
                "id": a[0], "job_id": a[1], "resume_id": a[2],
                "current_stage_id": a[3], "status": a[4], "kanban_state": a[5],
            }
            for a in apps
        ],
    }


def _write_closed_by_merge(conn: sqlite3.Connection, application_id: str, merged_by: str) -> None:
    row = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    stage_id = row[0]
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action) "
        "VALUES (?, ?, ?, ?, 'human', ?, 'closed_by_merge')",
        (str(uuid.uuid4()), application_id, stage_id, stage_id, merged_by),
    )


@idempotent_effect("effect_merge_candidates")
def effect_merge_candidates(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    primary_id: str,
    secondary_id: str,
    reason: str,
    keep_application_per_job: dict[str, str],
    merged_by: str,
) -> dict:
    """把 secondary 并入 primary：写 secondary 快照 → secondary 的 application 全部
    改 candidate_id 到 primary → 同岗位双投递按 HR 选择保留一份、另一份写
    action='closed_by_merge' 流转事实不删 → secondary.merged_into = primary。
    幂等键 {primary_id}:effect_merge_candidates:{secondary_id}:{request_id}。"""
    if primary_id == secondary_id:
        raise MergeValidationError("主候选人不能等于被合并候选人")
    primary = conn.execute(
        "SELECT id, merged_into FROM candidate WHERE id = ?", (primary_id,)
    ).fetchone()
    secondary = conn.execute(
        "SELECT id, merged_into FROM candidate WHERE id = ?", (secondary_id,)
    ).fetchone()
    if primary is None or secondary is None:
        raise MergeValidationError("候选人不存在")
    if primary[1] is not None:
        raise MergeValidationError("主候选人已被合并")
    if secondary[1] is not None:
        raise MergeValidationError("被合并候选人已被合并")

    secondary_apps = conn.execute(
        "SELECT id, job_id FROM application WHERE candidate_id = ? ORDER BY created_at, id",
        (secondary_id,),
    ).fetchall()

    # 2026-10-11 修正（1001O seg2 Spec review F1）：**改挂前**冻结主方既有投递快照，
    # 校验与写关闭共用同一份——⛔ 不在改挂循环里现查：那会读到自己刚改挂的行，被合并方
    # 同岗位多份、主方没有时会凭空写 closed_by_merge（HR 未被提示、产生错误审计事实）。
    primary_apps_by_job: dict[str, str] = {}
    for row in conn.execute(
        "SELECT id, job_id FROM application WHERE candidate_id = ? ORDER BY created_at, id",
        (primary_id,),
    ).fetchall():
        primary_apps_by_job.setdefault(row[1], row[0])

    # 同岗位双投递：合并前必须由 HR 选保留哪份（spec「同岗位双投递」）。
    for application_id, job_id in secondary_apps:
        primary_open = primary_apps_by_job.get(job_id)
        if primary_open is None:
            continue
        keep_id = keep_application_per_job.get(job_id)
        if keep_id not in (application_id, primary_open):
            raise MergeValidationError(f"岗位 {job_id} 存在双投递，必须指定保留哪份投递")

    snapshot = _snapshot_secondary(conn, secondary_id)
    merge_log_id = str(uuid.uuid4())

    for application_id, job_id in secondary_apps:
        primary_open = primary_apps_by_job.get(job_id)
        conn.execute(
            "UPDATE application SET candidate_id = ? WHERE id = ?", (primary_id, application_id)
        )
        if primary_open is not None:
            keep_id = keep_application_per_job.get(job_id)
            loser_id = primary_open if keep_id == application_id else application_id
            _write_closed_by_merge(conn, loser_id, merged_by)

    conn.execute(
        "INSERT INTO candidate_merge_log "
        "(id, primary_id, secondary_id, reason, secondary_snapshot, merged_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (merge_log_id, primary_id, secondary_id, reason,
         json.dumps(snapshot, ensure_ascii=False), merged_by),
    )
    conn.execute(
        "UPDATE candidate SET merged_into = ? WHERE id = ?", (primary_id, secondary_id)
    )
    return {"merge_log_id": merge_log_id, "primary_id": primary_id, "secondary_id": secondary_id}


@idempotent_effect("effect_unmerge_candidates")
def effect_unmerge_candidates(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    merge_log_id: str,
    unmerged_by: str,
) -> dict:
    """按快照恢复 secondary 原有 application 归属；合并期间 primary 新增记录保留在
    primary；写 unmerged_by/at 并清 secondary.merged_into。
    幂等键 {merge_log_id}:effect_unmerge_candidates:undo。"""
    row = conn.execute(
        "SELECT primary_id, secondary_id, secondary_snapshot, unmerged_at "
        "FROM candidate_merge_log WHERE id = ?",
        (merge_log_id,),
    ).fetchone()
    if row is None:
        raise MergeValidationError("合并留痕不存在")
    if row[3] is not None:
        raise MergeValidationError("该合并已被撤销")

    primary_id, secondary_id, snapshot_json = row[0], row[1], row[2]
    # 只按快照还原：快照之外的 application（合并后 primary 新增的投递）⛔ 不动，
    # 它们本来就挂在 primary 上。
    snapshot = json.loads(snapshot_json)
    for app in snapshot["applications"]:
        conn.execute(
            "UPDATE application SET candidate_id = ?, current_stage_id = ?, "
            "status = ?, kanban_state = ? WHERE id = ?",
            (
                secondary_id,
                app["current_stage_id"],
                app["status"],
                app["kanban_state"],
                app["id"],
            ),
        )

    conn.execute("UPDATE candidate SET merged_into = NULL WHERE id = ?", (secondary_id,))
    conn.execute(
        "UPDATE candidate_merge_log SET unmerged_by = ?, unmerged_at = datetime('now') "
        "WHERE id = ?",
        (unmerged_by, merge_log_id),
    )
    return {"merge_log_id": merge_log_id, "primary_id": primary_id, "secondary_id": secondary_id}
