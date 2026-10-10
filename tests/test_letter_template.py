"""letter_template 版本化读写与占位符校验（U2 tasks 2.1）。"""
from __future__ import annotations

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.letter_template import (
    ForbiddenPlaceholderError,
    get_letter_template,
    put_letter_template,
    validate_template_body,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "t.db"))
    init_schema(c)
    return c


def test_seed_creates_placeholder_v1_for_both_kinds(conn):
    offer = get_letter_template(conn, "offer")
    rejection = get_letter_template(conn, "rejection")
    assert offer is not None and offer["version"] == 1
    assert rejection is not None and rejection["version"] == 1
    assert offer["updated_by"] == "system"


def test_put_creates_new_version_and_keeps_old(conn):
    put_letter_template(conn, kind="offer", body="{candidate_name}：您好v2", updated_by="hr")
    latest = get_letter_template(conn, "offer")
    assert latest["version"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_template WHERE kind = 'offer'"
    ).fetchone()[0] == 2


def test_put_identical_body_is_noop(conn):
    body = get_letter_template(conn, "offer")["body"]
    result = put_letter_template(conn, kind="offer", body=body, updated_by="hr")
    assert result["unchanged"] is True
    assert result["version"] == 1


def test_offer_rejects_score_rank_and_salary_placeholders():
    for bad in ("{total_score}", "{排名}", "{salary}", "{薪资}"):
        with pytest.raises(ForbiddenPlaceholderError):
            validate_template_body(kind="offer", body=f"{bad} 您好")


def test_rejection_rejects_score_rank_but_not_salary():
    with pytest.raises(ForbiddenPlaceholderError):
        validate_template_body(kind="rejection", body="{排名} 您好")
    # 拒信白名单只有 candidate_name/job_title；{department} 这类越界占位符同样被拒。
    with pytest.raises(ForbiddenPlaceholderError):
        validate_template_body(kind="rejection", body="{department} 您好")


def test_allowed_offer_placeholders_pass():
    validate_template_body(
        kind="offer",
        body="{candidate_name} 拟任 {job_title}，部门 {department}，入职 {start_date}，"
        "汇报 {report_to}",
    )
