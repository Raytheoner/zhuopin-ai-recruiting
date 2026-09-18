"""复合流程测试——覆盖各任务测试结构上都覆盖不到的序列（final review 收尾项）：
上传时判定命中"待校对"字段 → 该字段人工校对修正 → 重判必须读到修正后的值并
产出新的一组标记 → 同一校对状态下重复触发重判必须是幂等的（不产生第三组）。

拆成三个 Critical/Important 修复：
- Fix 1（screening_nodes.py::screen_and_persist 的 business_key 加入
  review_generation 判别符）：没有它，第二次调用会被误判成"已经做过"而
  静默 no-op——本测试第 3 步之后如果标记数还停在 1 条就是这个问题复发。
- Fix 2（screening_nodes.py::compute_screen 里叠加 field_review_queue 的
  human_value）：没有它，第 3 步重判用的仍是原始机器值 2.0，规则依旧判
  fail/skipped，不会翻成 pass。
- 第 5 步复核 Fix 1 的幂等边界：同一 review_generation 下重复触发不应
  产生第三组标记。

夹具与调用方式沿用 tests/test_screening_trigger_on_upload.py（job/profile/rule
建法、screening_client 命名习惯、make_test_client 用法）与
tests/test_screening_trigger_on_field_review.py（queue_reapplication_screening
直接调用的用法）；tests/ 目录没有 __init__.py，模块间不能互相 import，
_docx_bytes/_fake_compute_parse 按惯例原样复制而非新设计。
"""
from __future__ import annotations

import io

import docx
import pytest

from app.graph.resume_nodes import queue_reapplication_screening
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


@pytest.fixture
def screening_client(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="hr1", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)

    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', 1, 'experience_years', 'gte', '5', 1, '工作年限要求：5 年及以上')"
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'j1', 1, 'approved', '{}')"
    )
    conn.commit()
    return client, conn


def _fake_compute_parse(gateway, *, spans, audit_context):
    """years_of_experience 置信度 0.5（低于 job 默认阈值 0.7）：既会落进
    field_review_queue 待校对，也会让上传时判定把绑在它上面的规则标成
    skipped("待校对")而不是 fail——step 2 要验的正是这个"待校对"态。原始
    机器值 2.0 不满足 gte 5，如果 Fix 2 失效（重判仍读机器值）会继续 fail
    而不是翻 pass，可借此暴露问题。"""
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, SpanRef, TextField,
    )

    fields = ResumeFields(
        name=TextField(value="张三", confidence=0.9, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=2.0, confidence=0.5, spans=[SpanRef(span_id=2, quote="2年", start=5, end=7)]
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )

    class _Meta:
        response_model = "test-model"

    return fields, _Meta()


def test_review_correction_produces_new_verdict_and_stays_idempotent(screening_client, monkeypatch):
    client, conn = screening_client
    monkeypatch.setattr("app.web.server.compute_parse", _fake_compute_parse)

    # 1. 上传一份 years_of_experience 落入待校对队列的简历。
    files = [
        ("files", ("a.docx", _docx_bytes(["张三，2年工作经验。" * 6]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
    ]
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=files,
    )
    assert resp.status_code == 200
    body = resp.json()["results"][0]
    assert body["screening_status"] == "ok"
    resume_id = body["resume_id"]
    application_id = body["application_id"]

    pending = conn.execute(
        "SELECT field FROM field_review_queue WHERE resume_id = ? AND status = 'pending'",
        (resume_id,),
    ).fetchall()
    assert ("years_of_experience",) in pending

    # 2. 上传时判定：命中待校对字段的规则必须是 skipped("待校对")，不是
    #    直接拿机器值 2.0 去判 fail。
    flags_after_upload = client.get(
        f"/api/applications/{application_id}/screening-flags"
    ).json()["flags"]
    assert len(flags_after_upload) == 1
    assert flags_after_upload[0]["verdict"] == "skipped"
    assert flags_after_upload[0]["reason"] == "待校对"

    # 3. 提交字段校对，人工修正值 6（满足 gte 5，原机器值 2 不满足）。
    review_resp = client.post(
        f"/api/resumes/{resume_id}/fields/years_of_experience/review",
        json={"human_value": "6"},
    )
    assert review_resp.status_code == 200
    assert review_resp.json() == {"ok": True, "already_reviewed": False, "screening_status": "ok"}

    # 4. 校对完成后必须产生新的一组标记（Fix 1：business_key 判别符生效，
    #    不会被上传时那组的 effect_key 短路掉），且判定用的是修正后的值
    #    （Fix 2：compute_screen 叠加了 human_value）——原本 fail 的规则
    #    应翻成 pass。
    flags_after_review = client.get(
        f"/api/applications/{application_id}/screening-flags"
    ).json()["flags"]
    assert len(flags_after_review) == 2
    assert flags_after_review[0]["verdict"] == "skipped"  # 上传时那组原样保留
    assert flags_after_review[1]["verdict"] == "pass"  # 校对后新增那组

    # 5. 同一校对状态下（没有新的字段被校对）再触发一次重判，不应产生
    #    第三组标记——复核 Fix 1 的幂等边界没有被判别符本身破坏。
    queue_reapplication_screening(conn, resume_id)
    flags_after_repeat = client.get(
        f"/api/applications/{application_id}/screening-flags"
    ).json()["flags"]
    assert len(flags_after_repeat) == 2


def test_review_with_non_numeric_value_defers_screening_instead_of_500(
    screening_client, monkeypatch
):
    """final review 修复项：field_review_queue.human_value 是自由文本
    （FieldReviewRequest.human_value 没有做数字校验），但
    _overlay_reviewed_fields 对 years_of_experience 无条件 float(human_value)。
    HR 直接调 API 传一个非纯数字的值（如"6年"）会让 queue_reapplication_screening
    在校对结果已经 commit 之后才抛出 ValueError——路由必须像上传/重解析路由
    一样 try/except 兜住，返回 200 + screening_status="deferred"，⛔ 不能让
    已经成功的校对提交因为重判失败而被 FastAPI 翻译成 500。"""
    client, conn = screening_client
    monkeypatch.setattr("app.web.server.compute_parse", _fake_compute_parse)

    files = [
        ("files", ("a.docx", _docx_bytes(["张三，2年工作经验。" * 6]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
    ]
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=files,
    )
    resume_id = resp.json()["results"][0]["resume_id"]

    review_resp = client.post(
        f"/api/resumes/{resume_id}/fields/years_of_experience/review",
        json={"human_value": "6年"},
    )
    assert review_resp.status_code == 200
    body = review_resp.json()
    assert body["ok"] is True
    assert body["already_reviewed"] is False
    assert body["screening_status"] == "deferred"

    # 校对结果本身必须已经落库——这是本修复要保护的东西：重判失败不能
    # 回滚/掩盖已经成功的人工校对提交。
    row = conn.execute(
        "SELECT status, human_value FROM field_review_queue "
        "WHERE resume_id = ? AND field = 'years_of_experience'",
        (resume_id,),
    ).fetchone()
    assert row == ("reviewed", "6年")
