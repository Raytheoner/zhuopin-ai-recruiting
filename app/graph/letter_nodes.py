"""Offer/拒信文书 L4 编排层（offer-generation U2 tasks 2.3/2.4/2.6）。

compute_letter_draft_for_application 只读查库组装 facts + 模板，调 L3 Agent
生成草稿（工程铁律 2：compute_* 可以查库做输入组装）；写库全部在
effect_persist_letter / effect_edit_letter / effect_mark_letter_human_written
三个 effect_* 节点里，每个都独占、幂等（工程铁律 1）。查看/导出留痕
record_letter_access 与 app/graph/resume_nodes.py::record_resume_access 同款：
写入失败不吞异常，让读取也失败。
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid

from app.agents.jd_agent import (
    UNKNOWN_GENERATED_AT,
    enforce_ai_label,
    extract_label_generated_at,
    strip_ai_label,
)
from app.agents.letter_drafter import LetterDraft, LetterFacts, compute_letter_draft
from app.storage.idempotency import idempotent_effect
from app.storage.letter_template import get_letter_template


class LetterNotFoundError(Exception):
    """letter_id 不存在。"""


class LetterTemplateMissingError(Exception):
    """该 kind 还没有模板。"""


class OfferNotFoundError(Exception):
    """该投递尚无 Offer 记录，不能生成 Offer 文书。"""


class OfferNotApprovedError(Exception):
    """Offer 尚未审批通过（offer.status != 'approved'），不能生成文书。"""


class RejectionRecordMissingError(Exception):
    """该投递尚无 rejection_record，不能生成拒信。"""


def _assert_offer_approved(conn: sqlite3.Connection, application_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM offer WHERE application_id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise OfferNotFoundError("该投递尚无 Offer 记录，不能生成 Offer 文书")
    if row[0] != "approved":
        raise OfferNotApprovedError("Offer 尚未审批通过，不能生成文书")


def _assert_rejection_record_exists(
    conn: sqlite3.Connection, application_id: str
) -> None:
    row = conn.execute(
        "SELECT 1 FROM rejection_record WHERE application_id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise RejectionRecordMissingError(
            "该投递尚无淘汰记录，请先在复核工作台完成批量确认"
        )


def next_letter_version(
    conn: sqlite3.Connection, application_id: str, kind: str
) -> int:
    row = conn.execute(
        "SELECT MAX(version) FROM candidate_letter WHERE application_id = ? AND kind = ?",
        (application_id, kind),
    ).fetchone()
    return (row[0] or 0) + 1


def load_letter_facts(
    conn: sqlite3.Connection, application_id: str, kind: str
) -> LetterFacts:
    row = conn.execute(
        "SELECT c.name, j.title FROM application a "
        "JOIN candidate c ON c.id = a.candidate_id "
        "JOIN job j ON j.id = a.job_id WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"application 不存在: {application_id!r}")
    candidate_name, job_title = row
    if kind == "offer":
        offer = conn.execute(
            "SELECT department, start_date, report_to FROM offer WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        if offer is None:
            raise OfferNotFoundError("该投递尚无 Offer 记录，不能生成 Offer 文书")
        return LetterFacts(
            candidate_name=candidate_name,
            job_title=job_title,
            department=offer[0],
            start_date=offer[1],
            report_to=offer[2],
        )
    return LetterFacts(candidate_name=candidate_name, job_title=job_title)


def compute_letter_draft_for_application(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    kind: str,
    gateway,
) -> tuple[LetterDraft, int]:
    """L4 compute_* 节点：先过前置校验（fail-fast，避免白烧一次 LLM 调用），
    再读模板 + facts，调 L3 纯函数。返回 (draft, 生成时绑定的模板版本)。"""
    if kind == "offer":
        _assert_offer_approved(conn, application_id)
    elif kind == "rejection":
        _assert_rejection_record_exists(conn, application_id)
    else:
        raise ValueError(f"kind 只能是 offer/rejection，收到: {kind!r}")

    template = get_letter_template(conn, kind)
    if template is None:
        raise LetterTemplateMissingError(f"缺少 {kind} 模板，请先在模板维护页创建")

    facts = load_letter_facts(conn, application_id, kind)
    job_id = conn.execute(
        "SELECT job_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    draft = compute_letter_draft(
        gateway,
        kind=kind,
        template_body=template["body"],
        template_version=template["version"],
        facts=facts,
        audit_context={
            "thread_id": f"{application_id}:letter",
            "node": "compute_letter_draft",
            "application_id": application_id,
            "job_id": job_id,
        },
    )
    return draft, template["version"]


@idempotent_effect("effect_persist_letter")
def effect_persist_letter(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    kind: str,
    version: int,
    template_version: int,
    draft: LetterDraft,
    created_by: str,
) -> str:
    """effect_* 节点：把一版草稿落成 candidate_letter（版本递增），独占、幂等。

    business_key = f"{kind}:{draft.run_id}"，幂等键 =
    {application_id}:effect_persist_letter:{kind}:{analysis_run_id}——同一次真实
    LLM 调用只落一版；再次生成（新 run_id）得到新版本。

    前置校验在事务内重做一遍（compute 与 persist 之间 offer 状态可能被改）：
    Offer 类前置 offer.status='approved'；拒信类前置存在 rejection_record。
    不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交（铁律 1）。
    """
    if kind == "offer":
        _assert_offer_approved(conn, application_id)
    else:
        _assert_rejection_record_exists(conn, application_id)
    if not created_by or not created_by.strip():
        raise ValueError("created_by 不能为空")
    letter_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO candidate_letter (id, application_id, kind, version, "
        "template_version, body, ai_generated, analysis_run_id, sent_status, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'none', ?)",
        (letter_id, application_id, kind, version, template_version, draft.body,
         draft.run_id, created_by),
    )
    return letter_id


def load_letter(conn: sqlite3.Connection, letter_id: str) -> dict:
    row = conn.execute(
        "SELECT id, application_id, kind, version, body, ai_generated FROM "
        "candidate_letter WHERE id = ?",
        (letter_id,),
    ).fetchone()
    if row is None:
        raise LetterNotFoundError(f"文书不存在: {letter_id!r}")
    return {
        "id": row[0],
        "application_id": row[1],
        "kind": row[2],
        "version": row[3],
        "body": row[4],
        "ai_generated": bool(row[5]),
    }


def letter_edit_business_key(letter_id: str, text: str) -> str:
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]
    return f"{letter_id}:{digest}"


@idempotent_effect("effect_edit_letter")
def effect_edit_letter(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    letter_id: str,
    edited_body: str,
) -> str:
    """effect_* 节点：把 HR 编辑后的正文写回，编辑不去 AI 标识（tasks 2.4）。

    标识保护与 app/graph/jd_nodes.py::effect_update_jd_text 同款：⛔ 不检查用户
    有没有删标识（检查就有绕过空间），无条件重贴。唯一例外是已「标记为人工撰写」
    的文书（ai_generated=0）：那份作者已经是人，只剥不贴。
    """
    letter = load_letter(conn, letter_id)
    if letter["ai_generated"]:
        generated_at = (
            extract_label_generated_at(letter["body"]) or UNKNOWN_GENERATED_AT
        )
        final_body = enforce_ai_label(edited_body, generated_at=generated_at)
    else:
        final_body = strip_ai_label(edited_body)
    conn.execute(
        "UPDATE candidate_letter SET body = ? WHERE id = ?", (final_body, letter_id)
    )
    return final_body


@idempotent_effect("effect_mark_letter_human_written")
def effect_mark_letter_human_written(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    letter_id: str,
    reviewer: str,
    marked_at: str,
) -> str:
    """effect_* 节点：显式「标记为人工撰写」去标识 + 留痕（tasks 2.4）。

    这是**唯一**能去掉 AI 标识的路径（candidate-letter-engine spec「编辑不去标，
    显式标记人工撰写才去标」）。去标识与留痕在同一次 UPDATE：body 剥掉标识、
    ai_generated=0、authorship_marked_by/at/from_version 一起落地，结构上不存在
    「标识没了但查不到谁去的」中间态。⛔ reviewer 不接受空白（决策人只能是人）。
    """
    if not str(reviewer).strip():
        raise ValueError(
            "标记为人工撰写必须记下是谁标的（合规红线：决策人只能是人）"
        )
    letter = load_letter(conn, letter_id)
    final_body = strip_ai_label(letter["body"])
    conn.execute(
        "UPDATE candidate_letter SET body = ?, ai_generated = 0, "
        "authorship_marked_by = ?, authorship_marked_at = ?, authorship_from_version = ? "
        "WHERE id = ?",
        (final_body, reviewer, marked_at, letter["version"], letter_id),
    )
    return final_body


def record_letter_access(
    conn: sqlite3.Connection,
    *,
    accessor: str,
    application_id: str,
    letter_id: str,
    access_type: str,
) -> None:
    """文书查看/导出留痕（candidate-letter-engine spec「导出 docx」「文书草稿的
    查看留痕」）。写入失败不吞任何异常——调用方（路由）不 catch 就是正确行为
    （FastAPI 未捕获异常 ⇒ 500，读取自然失败，不返回正文）。"""
    conn.execute(
        "INSERT INTO letter_access_log (id, accessor, application_id, letter_id, access_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), accessor, application_id, letter_id, access_type),
    )
    conn.commit()
