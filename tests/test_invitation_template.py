"""邀约模板版本化与占位符白名单（U3 tasks 3.1）。

反证两条 spec 硬约束：⛔ 模板不得含评分／排名／淘汰理由占位符；模板带版本号、
升版不覆盖旧版。"""
from __future__ import annotations

import pytest

from app.storage import letter_template as letter_template_module
from app.storage.db import get_connection, init_schema
from app.storage.invitation_template import (
    ALLOWED_INVITATION_PLACEHOLDERS,
    ForbiddenPlaceholderError,
    get_invitation_template,
    put_invitation_template,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "invitation_template.db"))
    init_schema(c)
    return c


def test_seed_v1_exists_after_init_schema(conn):
    template = get_invitation_template(conn)
    assert template["version"] == "v1"
    assert template["updated_by"] == "system"


def test_allowed_placeholder_set_is_nine(conn):
    assert ALLOWED_INVITATION_PLACEHOLDERS == {
        "candidate_name", "job_title", "round", "start_at", "end_at",
        "mode", "location_or_link", "interviewer_names", "contact",
    }


def test_forbidden_keywords_are_the_letter_template_constant():
    """禁词表是**同一个对象**，不是抄一份。两份词表分叉是静默故障。"""
    from app.storage import invitation_template as invitation_template_module

    assert (
        invitation_template_module.FORBIDDEN_COMMON_KEYWORDS
        is letter_template_module.FORBIDDEN_COMMON_KEYWORDS
    )


def test_put_creates_next_version_and_keeps_old(conn):
    put_invitation_template(conn, body="第一版 {job_title}", updated_by="hr-1")
    result = put_invitation_template(conn, body="第二版 {job_title}", updated_by="hr-1")
    # v1 是 init_schema 的占位种子；两次 PUT 依次落到 v2、v3，且 v1/v2 都还在。
    assert result["version"] == "v3"
    assert result["unchanged"] is False
    versions = [
        row[0]
        for row in conn.execute("SELECT version FROM invitation_template ORDER BY version")
    ]
    assert versions == ["v1", "v2", "v3"]


def test_put_same_body_is_unchanged_noop(conn):
    first = put_invitation_template(conn, body="同一版 {job_title}", updated_by="hr-1")
    second = put_invitation_template(conn, body="同一版 {job_title}", updated_by="hr-1")
    assert second["version"] == first["version"]
    assert second["unchanged"] is True
    count = conn.execute("SELECT COUNT(*) FROM invitation_template").fetchone()[0]
    assert count == 2  # v1 占位种子 + 这一次


@pytest.mark.parametrize(
    "placeholder",
    ["{total_score}", "{排名}", "{reject_reason}", "{score}", "{建议录用}"],
)
def test_put_rejects_forbidden_placeholders(conn, placeholder):
    with pytest.raises(ForbiddenPlaceholderError):
        put_invitation_template(conn, body=f"您好 {placeholder}", updated_by="hr-1")


def test_put_rejects_unregistered_placeholder(conn):
    with pytest.raises(ForbiddenPlaceholderError):
        put_invitation_template(conn, body="您好 {interviewer_phone}", updated_by="hr-1")


def test_put_accepts_all_whitelisted_placeholders(conn):
    body = " ".join(f"{{{name}}}" for name in sorted(ALLOWED_INVITATION_PLACEHOLDERS))
    assert put_invitation_template(conn, body=body, updated_by="hr-1")["version"] == "v2"


def test_put_rejects_empty_body(conn):
    with pytest.raises(ValueError):
        put_invitation_template(conn, body="   ", updated_by="hr-1")


def test_put_requires_updated_by(conn):
    with pytest.raises(ValueError):
        put_invitation_template(conn, body="您好 {job_title}", updated_by="  ")


def test_put_writes_no_row_when_validation_fails(conn):
    before = conn.execute("SELECT COUNT(*) FROM invitation_template").fetchone()[0]
    with pytest.raises(ForbiddenPlaceholderError):
        put_invitation_template(conn, body="{rank}", updated_by="hr-1")
    after = conn.execute("SELECT COUNT(*) FROM invitation_template").fetchone()[0]
    assert after == before
