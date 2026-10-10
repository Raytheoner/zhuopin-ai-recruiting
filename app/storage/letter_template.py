"""Offer/拒信文书模板的版本化读写与保存校验（offer-generation U2 tasks 2.1）。

版本化：每次 PUT 产生新版本，绝不覆盖旧版（candidate-letter-engine spec
「文书模板由 HR 维护并版本化」）。保存校验：⛔ 评分/排名/硬门槛占位符（两类
模板一律禁）；Offer ⛔ 薪资类占位符——命中即拒并指出具体占位符（正则＋关键词表）。
"""
from __future__ import annotations

import re
import sqlite3

# 占位符语法：{name}。校验只扫花括号内的名字（名字可含中文，如 {排名} 也要命中）。
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

# 允许出现在模板里的占位符白名单（kind 决定）。超出白名单即拒——包括一切
# 评分/排名/硬门槛/薪资类占位符与拼写错误。
ALLOWED_OFFER_PLACEHOLDERS: frozenset[str] = frozenset(
    {"candidate_name", "job_title", "department", "start_date", "report_to"}
)
ALLOWED_REJECTION_PLACEHOLDERS: frozenset[str] = frozenset(
    {"candidate_name", "job_title"}
)

# ⛔ 评分/排名/硬门槛类占位符关键词（两类模板一律禁）。
FORBIDDEN_COMMON_KEYWORDS: tuple[str, ...] = (
    "score", "rank", "ranking", "total_score", "hard_requirement", "hard_rule",
    "recommend", "reject", "总分", "排名", "评分", "硬门槛", "建议",
)

# ⛔ 薪资类占位符关键词（仅 Offer 模板禁）。
FORBIDDEN_SALARY_KEYWORDS: tuple[str, ...] = (
    "salary", "pay", "compensation", "bonus", "allowance",
    "薪", "工资", "薪资", "报酬", "待遇",
)


class ForbiddenPlaceholderError(ValueError):
    """模板里出现被禁的占位符。message 带出具体占位符与类别，供接口直接回给 HR。"""


def _reject_reason(kind: str, name: str) -> str:
    low = name.lower()
    if kind == "offer" and any(
        k in low or k in name for k in FORBIDDEN_SALARY_KEYWORDS
    ):
        return "薪资类占位符"
    if any(k in low or k in name for k in FORBIDDEN_COMMON_KEYWORDS):
        return "评分/排名/硬门槛占位符"
    return "未登记的占位符"


def validate_template_body(*, kind: str, body: str) -> None:
    """保存校验：kind 合法、正文非空、占位符全部落在该 kind 的白名单内。"""
    if kind not in ("offer", "rejection"):
        raise ValueError(f"kind 只能是 offer/rejection，收到: {kind!r}")
    if not body or not body.strip():
        raise ValueError("模板正文不能为空")
    allowed = (
        ALLOWED_OFFER_PLACEHOLDERS if kind == "offer"
        else ALLOWED_REJECTION_PLACEHOLDERS
    )
    offenders = []
    for token in _PLACEHOLDER_RE.findall(body):
        name = token.strip()
        if name in allowed:
            continue
        offenders.append(f"{name}（{_reject_reason(kind, name)}）")
    if offenders:
        raise ForbiddenPlaceholderError(
            f"模板含被禁止的占位符: {sorted(set(offenders))}"
        )


def get_letter_template(conn: sqlite3.Connection, kind: str) -> dict | None:
    """返回该 kind 的最新版模板；从未写入返回 None。"""
    row = conn.execute(
        "SELECT kind, version, body, updated_by, updated_at FROM letter_template "
        "WHERE kind = ? ORDER BY version DESC LIMIT 1",
        (kind,),
    ).fetchone()
    if row is None:
        return None
    return {
        "kind": row[0],
        "version": row[1],
        "body": row[2],
        "updated_by": row[3],
        "updated_at": row[4],
    }


def put_letter_template(
    conn: sqlite3.Connection, *, kind: str, body: str, updated_by: str
) -> dict:
    """新增一个版本，绝不覆盖旧版；同内容重复 PUT 是幂等 no-op。"""
    validate_template_body(kind=kind, body=body)
    if not updated_by or not updated_by.strip():
        raise ValueError("updated_by 不能为空")

    latest = get_letter_template(conn, kind)
    if latest is not None and latest["body"] == body:
        return {**latest, "unchanged": True}

    new_version = (latest["version"] + 1) if latest else 1
    conn.execute(
        "INSERT INTO letter_template (kind, version, body, updated_by) "
        "VALUES (?, ?, ?, ?)",
        (kind, new_version, body, updated_by),
    )
    conn.commit()
    return {**get_letter_template(conn, kind), "unchanged": False}
