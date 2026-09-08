"""端到端回归：POST /api/jobs/{job_id}/confirm 连续两次必须都是 2xx。

背景：plan 里"现状已经是 2xx"这条结论是**读代码**得出的（confirm 端点忽略
effect_confirm_profile / effect_generate_and_persist_jd 的返回值，统一从
_jd_payload() 读回持久化结果），没有测试守着。现网 bug 恰恰出在 confirm 这条
真实 HTTP 路径上——修完必须有一条端到端回归钉住它，否则将来有人给 confirm
加一句"effect 返回 None 就报错"，这个 500 会原地复活而没人发现。

装配沿用 tests/test_web_api.py 的 make_app()（TestClient + create_app()），
与 tests/test_jd_endpoints.py / tests/test_approval_branches.py 用的是同一套，
不另造第二套 app 装配。
"""

import json

from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE
from tests.test_web_api import make_app


def _db_path(tmp_path) -> str:
    return str(tmp_path / "web.db")


def _rows(tmp_path, sql, params=()):
    from app.storage.db import get_connection

    conn = get_connection(_db_path(tmp_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _to_confirmation_prompt(tmp_path):
    """把一个岗位推进到 confirmation_prompt 状态，返回 (client, job_id)。"""
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE])
    resp = client.post("/api/jobs", json={"message": "要个做 ECU 底层软件的"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["type"] == "confirmation_prompt"
    return client, body["job_id"]


def test_repeat_confirm_is_2xx_and_byte_identical(tmp_path):
    """核心回归：连续两次 confirm 都是 2xx，且响应体逐字相同。

    第二次调用命中 effect_confirm_profile / effect_generate_and_persist_jd
    的幂等短路（两者都返回 None），confirm 端点必须**不**把 None 当错误，
    而是统一从 _jd_payload() 读回持久化结果——这正是本次修复要保住的行为。
    """
    client, job_id = _to_confirmation_prompt(tmp_path)

    first = client.post(f"/api/jobs/{job_id}/confirm")
    assert 200 <= first.status_code < 300, first.text

    second = client.post(f"/api/jobs/{job_id}/confirm")
    assert 200 <= second.status_code < 300, second.text

    # 逐字相同：不仅 status_code 一致，响应体的原始字节也必须一致——第二次
    # 读回的是同一份持久化结果，不是重新生成的（可能不同的）另一份。
    assert first.content == second.content
    assert first.status_code == second.status_code


def test_repeat_confirm_leaves_exactly_one_approved_job_profile_row(tmp_path):
    """job_profile 该 job_id 的 approved 行恰一行，job.status == 'approved'。"""
    client, job_id = _to_confirmation_prompt(tmp_path)
    assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 200

    approved_rows = _rows(
        tmp_path,
        "SELECT COUNT(*) FROM job_profile WHERE job_id = ? AND status = 'approved'",
        (job_id,),
    )[0][0]
    assert approved_rows == 1

    job_status = _rows(tmp_path, "SELECT status FROM job WHERE id = ?", (job_id,))[0][0]
    assert job_status == "approved"


def test_repeat_confirm_leaves_exactly_one_human_review_row(tmp_path):
    """⛔ 重复确认不许留两条决策痕（合规红线：淘汰/决策必须有留痕，且留痕
    不能因为重放而重复——重复的留痕会让"谁在什么时候确认了这一版"变得歧义）。
    """
    client, job_id = _to_confirmation_prompt(tmp_path)
    assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 200

    review_rows = _rows(
        tmp_path, "SELECT COUNT(*) FROM human_review WHERE job_id = ?", (job_id,)
    )[0][0]
    assert review_rows == 1


def test_repeat_confirm_leaves_exactly_one_effect_log_row_per_node(tmp_path):
    """工程铁律 1 的 reviewer 判据：effect_log 条数与业务表行数按 thread 恒等，
    且这条不变式要有测试覆盖。这里覆盖两个 effect_* 节点各自的计数。
    """
    client, job_id = _to_confirmation_prompt(tmp_path)
    assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 200

    confirm_effects = _rows(
        tmp_path,
        "SELECT COUNT(*) FROM effect_log WHERE thread_id = ? AND node_name = ?",
        (job_id, "effect_confirm_profile"),
    )[0][0]
    jd_effects = _rows(
        tmp_path,
        "SELECT COUNT(*) FROM effect_log WHERE thread_id = ? AND node_name = ?",
        (job_id, "effect_generate_and_persist_jd"),
    )[0][0]
    assert confirm_effects == 1
    assert jd_effects == 1
