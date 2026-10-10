"""邀约文案模板的版本化读写与保存校验（interview-scheduling U3 tasks 3.1）。

版本化：每次 PUT 产生新版本（'v1' → 'v2' → …），绝不覆盖旧版（interview-invitation-
drafting spec「文案模板的来源与版本」）。保存校验：⛔ 候选人评分／排名／淘汰理由类
占位符一律禁（spec 逐字：「模板 MUST NOT 含候选人评分、排名或淘汰理由的占位符」）。

⛔ 校验用**白名单**不是黑名单：只放行 ALLOWED_INVITATION_PLACEHOLDERS 里的九个，
其余（拼写错误、未登记字段、任何评分／排名词）一律拒。黑名单枚举不完（"总分"、
"被淘汰"、"match_score" 这类写法总在往外冒），白名单漏不了。

禁词表的**真源**是 app/storage/letter_template.py 的 FORBIDDEN_COMMON_KEYWORDS
（offer-generation 已为「模板不得含评分／排名／硬门槛占位符」立了同一份词表）。本模块
import **同一个常量对象**，不另抄一份——两份词表迟早分叉，而分叉的失败是静默的
（某一边放宽后没有任何错误浮出来）。tests/test_invitation_template.py 用 `is` 钉住。

版本号是字符串标签 'v<数字>'（U1 真身 `invitation_template.version` 是 TEXT 主键）。
⚠️ 取最新版⛔ 不能用 `ORDER BY version DESC`——字典序下 'v10' < 'v2'，第十版会静默
退回第二版。一律按 `_version_number()` 解析出的整数比大小。
"""
from __future__ import annotations

import re
import sqlite3

from app.storage.letter_template import FORBIDDEN_COMMON_KEYWORDS

# 占位符语法：{name}。名字可含中文（{排名} 也要能被扫到并拒掉）。
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

# 邀约模板的占位符白名单（spec「按场次生成邀约文案」的内容清单：岗位、轮次、起止时刻、
# 形式（地址或会议链接）、面试官称谓、联系人；candidate_name 是收信人姓名，属普通事实
# 字段，与评分／排名无关，一并放行——见偏离登记 D-U3-1）。
ALLOWED_INVITATION_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "candidate_name",
        "job_title",
        "round",
        "start_at",
        "end_at",
        "mode",
        "location_or_link",
        "interviewer_names",
        "contact",
    }
)


class ForbiddenPlaceholderError(ValueError):
    """模板里出现被禁或未登记的占位符。message 带出具体占位符，供接口回给 HR。"""


def _reject_reason(name: str) -> str:
    low = name.lower()
    if any(k in low or k in name for k in FORBIDDEN_COMMON_KEYWORDS):
        return "评分/排名/淘汰理由占位符"
    return "未登记的占位符"


def validate_invitation_template_body(body: str) -> None:
    """保存校验：正文非空、占位符全部落在白名单内。"""
    if not body or not body.strip():
        raise ValueError("模板正文不能为空")
    offenders = []
    for token in _PLACEHOLDER_RE.findall(body):
        name = token.strip()
        if name in ALLOWED_INVITATION_PLACEHOLDERS:
            continue
        offenders.append(f"{name}（{_reject_reason(name)}）")
    if offenders:
        raise ForbiddenPlaceholderError(
            f"模板含被禁止的占位符: {sorted(set(offenders))}"
        )


def _version_number(label: str) -> int:
    if not label.startswith("v") or not label[1:].isdigit():
        raise ValueError(f"模板版本标签非法（应为 'v<数字>'）: {label!r}")
    return int(label[1:])


def _row_to_dict(row) -> dict:
    version, body, updated_by, updated_at = row
    return {
        "version": version,
        "body": body,
        "updated_by": updated_by,
        "updated_at": updated_at,
    }


def get_invitation_template(conn: sqlite3.Connection) -> dict | None:
    """返回最新版模板；从未写入返回 None。"""
    rows = conn.execute(
        "SELECT version, body, updated_by, updated_at FROM invitation_template"
    ).fetchall()
    if not rows:
        return None
    return _row_to_dict(max(rows, key=lambda r: _version_number(r[0])))


def put_invitation_template(
    conn: sqlite3.Connection, *, body: str, updated_by: str
) -> dict:
    """新增一个版本，绝不覆盖旧版；同内容重复 PUT 是幂等 no-op。"""
    validate_invitation_template_body(body)
    if not updated_by or not updated_by.strip():
        raise ValueError("updated_by 不能为空")

    latest = get_invitation_template(conn)
    if latest is not None and latest["body"] == body:
        return {**latest, "unchanged": True}

    next_number = (_version_number(latest["version"]) + 1) if latest else 1
    conn.execute(
        "INSERT INTO invitation_template (version, body, updated_by) VALUES (?, ?, ?)",
        (f"v{next_number}", body, updated_by),
    )
    conn.commit()
    result = get_invitation_template(conn)
    assert result is not None
    return {**result, "unchanged": False}
