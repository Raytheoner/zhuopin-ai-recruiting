"""JD 的读取 / 编辑 / 标记人工撰写三个端点（tasks 7.3 / 7.5）。"""

import json

import pytest

from app.agents.jd_agent import AI_LABEL_PREFIX
from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE
from tests.test_web_api import make_app


def _confirmed_job(tmp_path, jd_response: str = JD_RESPONSE, root_path: str = ""):
    """跑到"画像已确认、JD 已生成"这一步，返回 (client, job_id, confirm 回执)。"""
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE, jd_response], root_path=root_path)
    prefix = root_path
    job_id = client.post(f"{prefix}/api/jobs", json={"message": "要个做 ECU 底层的"}).json()[
        "job_id"
    ]
    resp = client.post(
        f"{prefix}/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True}
    )
    assert resp.status_code == 200, resp.text
    return client, job_id, resp.json()


def test_confirm_returns_the_unified_jd_payload(tmp_path):
    _, _, body = _confirmed_job(tmp_path)
    assert set(body) >= {
        "job_id",
        "version",
        "jd_text",
        "needs_manual",
        "human_written",
        "authorship",
        "ungrounded_terms",
    }
    assert body["human_written"] is False
    assert body["authorship"] is None


def test_get_jd_returns_the_same_payload_as_confirm(tmp_path):
    client, job_id, confirmed = _confirmed_job(tmp_path)
    fetched = client.get(f"/api/jobs/{job_id}/jd").json()
    assert fetched == confirmed


def test_ungrounded_terms_are_reported_not_blocked(tmp_path):
    """决策 12：只观测不拦截。编造了术语的 JD 照样 200、照样落库、照样能拿到。"""
    fabricated = json.dumps(
        {"body": "任职要求：熟悉 FlexRay 与 Lauterbach 调试。"}, ensure_ascii=False
    )
    _, _, body = _confirmed_job(tmp_path, jd_response=fabricated)
    assert body["ungrounded_terms"] == ["FlexRay", "Lauterbach"]
    assert "FlexRay" in body["jd_text"]  # ⛔ 没被拦下、没被删掉


def test_ungrounded_terms_empty_when_everything_traces_back(tmp_path):
    grounded = json.dumps(
        {"body": "岗位职责：基于 AUTOSAR CP 开发 CAN 驱动，满足 ASIL-B。"},
        ensure_ascii=False,
    )
    _, _, body = _confirmed_job(tmp_path, jd_response=grounded)
    assert body["ungrounded_terms"] == []


@pytest.mark.compliance
def test_editing_cannot_delete_the_ai_label(tmp_path):
    """7.5 的红线断言：前端把标识删光提交上来，回执与落库结果照样带标识。"""
    client, job_id, _ = _confirmed_job(tmp_path)
    resp = client.post(
        f"/api/jobs/{job_id}/jd", json={"text": "我把标识删了，只留正文。"}
    )
    assert resp.status_code == 200
    assert AI_LABEL_PREFIX in resp.json()["jd_text"]
    assert AI_LABEL_PREFIX in client.get(f"/api/jobs/{job_id}/jd").json()["jd_text"]


def test_edit_is_idempotent_across_retries(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    first = client.post(f"/api/jobs/{job_id}/jd", json={"text": "改一版"}).json()
    second = client.post(f"/api/jobs/{job_id}/jd", json={"text": "改一版"}).json()
    assert first == second


def test_edit_rejects_empty_text(tmp_path):
    """空正文不是一次编辑，是一次误操作。⛔ 不许把一份 JD 清成只剩标识。"""
    client, job_id, _ = _confirmed_job(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/jd", json={"text": "   "})
    assert resp.status_code == 422


def test_mark_human_written_drops_the_label_and_records_the_reviewer(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    body = client.post(f"/api/jobs/{job_id}/jd/human-written").json()
    assert AI_LABEL_PREFIX not in body["jd_text"]
    assert body["human_written"] is True
    assert body["authorship"]["marked_by"]  # 鉴权空壳返回 UNKNOWN_REVIEWER，非空
    assert body["authorship"]["at"]


def test_mark_human_written_is_idempotent(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    first = client.post(f"/api/jobs/{job_id}/jd/human-written").json()
    second = client.post(f"/api/jobs/{job_id}/jd/human-written").json()
    assert first == second


def test_jd_endpoints_404_on_unknown_job(tmp_path):
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    assert client.get("/api/jobs/nope/jd").status_code == 404
    assert client.post("/api/jobs/nope/jd", json={"text": "x"}).status_code == 404
    assert client.post("/api/jobs/nope/jd/human-written").status_code == 404


def test_jd_endpoints_409_before_the_profile_is_confirmed(tmp_path):
    """还没确认画像就来编辑 JD：说清楚"先确认画像"，⛔ 不要 500。"""
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    job_id = client.post("/api/jobs", json={"message": "要个做 ECU 底层的"}).json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}/jd").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/jd", json={"text": "x"}).status_code == 409
    assert client.post(f"/api/jobs/{job_id}/jd/human-written").status_code == 409


def test_jd_endpoints_reject_an_abandoned_job(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    client.post(f"/api/jobs/{job_id}/abandon", json={"reason": ""})
    assert client.post(f"/api/jobs/{job_id}/jd", json={"text": "x"}).status_code == 409
    assert client.post(f"/api/jobs/{job_id}/jd/human-written").status_code == 409


def test_jd_endpoints_work_under_a_mount_prefix(tmp_path):
    """部署约束 1：挂到 /hr/recruit-agent 下必须照常工作。"""
    client, job_id, _ = _confirmed_job(tmp_path, root_path="/hr/recruit-agent")
    resp = client.get(f"/hr/recruit-agent/api/jobs/{job_id}/jd")
    assert resp.status_code == 200
    assert resp.json()["jd_text"]
