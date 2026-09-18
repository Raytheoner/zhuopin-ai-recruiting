"""简历解析结果落库（resume-parsing spec「字段级置信度与人工校对队列」
「解析留痕与版本」，design D2/D5/D11/D13）。

⚠️ 幂等 thread_id 用 resume_id，不是 application_id——application 在首次解析
完成前并不存在（候选人姓名恰恰是解析要抽取的字段）。见本计划「架构决策」第 1
条。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from app.schemas.resume_fields import FIELD_NAMES, ResumeFields
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


def _lowest_field_confidence(fields: ResumeFields) -> float:
    """resume.parse_confidence 记录本次解析里最低的那个字段置信度——
    工作台按这一列排序"哪份简历最需要人工介入"最直观。未提及的字段不计入
    （它们没有"抽取把握"这回事）。"""
    values = [
        getattr(fields, name).confidence
        for name in FIELD_NAMES
        if not getattr(fields, name).not_mentioned
    ]
    return min(values) if values else 1.0


def _find_or_create_candidate(conn: sqlite3.Connection, *, name: str) -> str:
    """按姓名去重（design D11 简化版：本单元不采集手机号，phone_hash 恒为
    NULL，见本计划「架构决策」第 4 条——同名不同人会被误判为同一候选人，
    这是已登记的已知限制，M3 采集手机号后按 (name, phone_hash) 重新收紧）。
    """
    row = conn.execute(
        "SELECT id FROM candidate WHERE name = ? AND phone_hash IS NULL", (name,)
    ).fetchone()
    if row is not None:
        return row[0]
    candidate_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO candidate (id, name) VALUES (?, ?)", (candidate_id, name)
    )
    return candidate_id


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


def _upsert_review_queue_rows(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    fields: ResumeFields,
    confidence_threshold: float,
) -> None:
    for name in FIELD_NAMES:
        field = getattr(fields, name)
        if field.not_mentioned:
            continue
        if field.confidence >= confidence_threshold:
            continue
        machine_value = json.dumps(field.value if not hasattr(field.value, "model_dump")
                                    else field.value.model_dump(), ensure_ascii=False)
        conn.execute(
            "INSERT INTO field_review_queue (id, resume_id, field, machine_value, confidence) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(resume_id, field) WHERE status='pending' DO NOTHING",
            (str(uuid.uuid4()), resume_id, name, machine_value, field.confidence),
        )


@idempotent_effect("effect_persist_parse")
def effect_persist_parse(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    resume_id: str,
    job_id: str,
    fields: ResumeFields,
    parser_version: str,
    model_configured: str,
    model_response: str | None,
    prompt_version: str,
    confidence_threshold: float,
) -> str:
    """写 resume_parse_version（历史）+ resume 三列（最新版缓存）+
    field_review_queue（低置信度字段）；首次解析额外创建 candidate/application/
    application_stage_history。返回 application_id。

    ⛔ 不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交（工程铁律 1）。
    """
    fields_json = fields.model_dump_json()
    confidence = _lowest_field_confidence(fields)

    conn.execute(
        "INSERT INTO resume_parse_version "
        "(resume_id, parser_version, parsed_json, confidence, model_configured, "
        "model_response, prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (resume_id, parser_version, fields_json, confidence, model_configured,
         model_response, prompt_version),
    )
    conn.execute(
        "UPDATE resume SET status = 'parsed', parsed_json = ?, parse_confidence = ?, "
        "parser_version = ? WHERE id = ?",
        (fields_json, confidence, parser_version, resume_id),
    )

    existing = conn.execute(
        "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    if existing is None:
        candidate_name = (
            fields.name.value if not fields.name.not_mentioned else "姓名待校对"
        )
        candidate_id = _find_or_create_candidate(conn, name=candidate_name)
        application_id = _create_application(
            conn, candidate_id=candidate_id, job_id=job_id, resume_id=resume_id
        )
    else:
        application_id = existing[0]

    _upsert_review_queue_rows(
        conn, resume_id=resume_id, fields=fields, confidence_threshold=confidence_threshold
    )

    return application_id


def record_resume_access(
    conn: sqlite3.Connection, *, accessor: str, resume_id: str, access_type: str
) -> None:
    """resume-upload-and-gate spec「简历访问留痕」：写入失败必须让调用方的读取
    也失败——本函数不吞任何异常，调用方（路由）不 catch 就是正确行为
    （FastAPI 未捕获异常 ⇒ 500，读取自然失败，不返回简历内容）。
    """
    conn.execute(
        "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
        "VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), accessor, resume_id, access_type),
    )
    conn.commit()


def queue_reapplication_screening(resume_id: str) -> None:
    """U3 硬门槛引擎的接入点空壳（tasks 3.10「触发该投递重判」，与
    app/middleware/auth.py::AuthMiddleware 是同一种"空壳接入点"手法）。

    ⛔ 本单元不实现重判逻辑——U3 还没有 compute_screen/effect_persist_flags。
    这里只留一个签名稳定的调用点：字段校对提交后调它，U3 落地时只需要把
    函数体换成真实的重判触发，⛔ 不改调用方（本函数所在 app/web/server.py 的
    /resumes/{id}/fields/{field}/review 路由不用动）。
    """
    logger.info(
        "resume_id=%s 的字段校对已完成，等待 U3 接入重判逻辑（当前为空壳）",
        resume_id,
    )
