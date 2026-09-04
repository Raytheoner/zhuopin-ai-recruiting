"""确认 / 修改 / 放弃三个分支的行为与留痕（tasks 6.1 / 6.4 / 6.5 / 6.6 / 6.7）。

⚠️ 三个分支**各自独占一个 effect_* 节点**，⛔ 不合并成一个"处理确认"节点。
理由是工程铁律 1 的直接推论：三条路径的对外后果完全不同（冻结并触发一次真实的
LLM 调用 / 什么都不冻结只记一笔 / 终止），合成一个节点后，"恢复时节点从头整个
重跑"会带着一个分支参数走进另一条路径，而幂等键只有一个。
"""

import json

import pytest

from tests.test_web_api import make_app, make_app_with_scripted_client

COMPLETE_PROFILE_RESPONSE = json.dumps(
    {
        "is_job_related": True,
        "is_complete": True,
        "questions": [],
        "profile_patch": {
            "job_title": "底层软件工程师",
            "department": "电子电器研发部",
            "headcount": 2,
            "education_requirement": "本科及以上",
            "experience_years": "3-5年",
            "core_skills": [{"name": "CAN 驱动开发", "required": True}],
            "project_experience_requirement": "至少一个量产 ECU 项目",
            "soft_skill_keywords": ["沟通"],
            "autosar_experience": ["CP"],
            "functional_safety": "ASIL-B",
            "mcu_family": ["TC3xx"],
            "diag_stack": ["UDS"],
            "sop_projects": [
                {
                    "vehicle_model": "A05 纯电",
                    "sop_date": "2024-06",
                    "role": "BSW 负责人",
                    "is_mass_production": True,
                }
            ],
            "toolchain": ["Vector DaVinci"],
        },
        "unspecified_fields": [],
    },
    ensure_ascii=False,
)

JD_RESPONSE = json.dumps({"body": "岗位职责：负责 ECU 底层软件开发。"}, ensure_ascii=False)


def _db_path(tmp_path) -> str:
    return str(tmp_path / "web.db")


def _rows(tmp_path, sql, params=()):
    from app.storage.db import get_connection

    conn = get_connection(_db_path(tmp_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _start_job(tmp_path, extra_responses=()):
    """跑到 confirmation_prompt 为止，返回 (client, job_id, payload)。"""
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE, *extra_responses])
    resp = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["type"] == "confirmation_prompt"
    return client, body["job_id"], body["message"]["payload"]


# ── tasks 6.1：确认页上必须看得见画像 ────────────────────────────────────


def test_confirmation_payload_carries_the_profile_summary(tmp_path):
    """现网真实缺陷的回归测试。

    修复前：payload 里 profile_patch_accumulated 有值，但没有任何**可渲染的**
    形态，前端从头到尾没读过它——业务经理在看不见画像的情况下点了确认。
    """
    _client, _job_id, payload = _start_job(tmp_path)
    summary = payload["profile_summary"]
    assert summary, "confirmation_prompt 必须带上画像摘要"
    labels = [item["label"] for item in summary]
    assert "岗位名称" in labels and "核心技能" in labels
    assert {"label": "招聘人数", "value": "2"} in summary


def test_confirmation_payload_has_no_english_field_identifier_in_the_summary(tmp_path):
    """⛔ 摘要里不许出现英文字段标识（index.html:162 那条既有约束的同一条）。"""
    from app.schemas.job_profile import FIELD_LABELS

    _client, _job_id, payload = _start_job(tmp_path)
    blob = json.dumps(payload["profile_summary"], ensure_ascii=False)
    for name in FIELD_LABELS:
        assert name not in blob


def test_profile_patch_accumulated_is_still_in_the_payload(tmp_path):
    """⛔ 新增 profile_summary，**不删** profile_patch_accumulated。

    它是 GET /api/jobs/{id} 读回历史行时唯一的原始数据，删掉会让 .51 上
    17 个真实 job 的历史 confirmation_prompt 行少一块内容。
    """
    _client, _job_id, payload = _start_job(tmp_path)
    assert payload["profile_patch_accumulated"]["job_title"] == "底层软件工程师"


# ── tasks 6.4：确认分支写 human_review ───────────────────────────────────


def test_confirm_records_one_human_review(tmp_path):
    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200

    rows = _rows(
        tmp_path,
        "SELECT decision_type, reviewer, profile_version, feedback, batch_id "
        "FROM human_review WHERE job_id = ?",
        (job_id,),
    )
    assert len(rows) == 1
    decision_type, reviewer, profile_version, feedback, batch_id = rows[0]
    assert decision_type == "approved"
    assert reviewer == "unknown:web-session"  # 鉴权是空壳，见 UNKNOWN_REVIEWER
    assert profile_version == 1
    assert feedback is None and batch_id is None


def test_human_review_row_count_equals_effect_log_count_per_thread(tmp_path):
    """铁律 1 的 reviewer 判据：**每个 effect_* 节点的 effect_log 条数与其业务表
    行数按 thread 恒等**，且这条不变式有测试覆盖。这就是那个覆盖。

    ⚠️ 恒等式必须在**重放**之后仍然成立，不只是首次调用之后——"同一 thread
    同一 business_key 重跑，effect_log 与业务表行数恒等"才是铁律 1 完整的
    reviewer 判据。只调一次 confirm 的话，即便 effect_confirm_profile 在重放
    时重复写 human_review（幂等失效），这条断言也测不出来。所以这里对同一个
    job 调用两次 /confirm（同一 thread_id、同一 business_key = version），
    在第二次调用之后再取数比对。
    """
    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200

    effects = _rows(
        tmp_path,
        "SELECT COUNT(*) FROM effect_log "
        "WHERE thread_id = ? AND node_name = 'effect_confirm_profile'",
        (job_id,),
    )[0][0]
    reviews = _rows(
        tmp_path,
        "SELECT COUNT(*) FROM human_review WHERE job_id = ? AND decision_type = 'approved'",
        (job_id,),
    )[0][0]
    assert effects == reviews == 1


def test_confirm_retried_does_not_duplicate_human_review(tmp_path):
    """副作用幂等 · 回调重复到达：Web 通道下等价于 POST 被重试。"""
    client, scripted = make_app_with_scripted_client(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE]
    )
    job_id = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"}).json()["job_id"]
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200
    calls_after_first = scripted.chat.completions.call_count
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200

    assert scripted.chat.completions.call_count == calls_after_first, "第二次确认又调了一次 LLM"
    assert (
        _rows(tmp_path, "SELECT COUNT(*) FROM human_review WHERE job_id = ?", (job_id,))[0][0]
        == 1
    )


def test_review_and_status_share_one_transaction(tmp_path):
    """铁律 1：**幂等记录与业务写必须在同一个事务里提交**。

    造法：让留痕这一半失败（reviewer 空串撞上表上的 CHECK），断言业务写那一半
    也没有留下——status 仍是 drafting，effect_log 里没有这一条。

    ⚠️ 拆开事务的后果不是"少一条痕"，是**永久丢失**：幂等记录一旦先落，重试会
    被判定为"已执行"，那条留痕再也不会补上（`.51` 2026-08-10 / 08-12 两次丢
    outbox 就是这个形状）。
    """
    import sqlite3

    from app.graph.nodes import effect_confirm_profile
    from app.storage.db import get_connection, init_schema

    conn = get_connection(str(tmp_path / "tx.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'x', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('j1-v1', 'j1', 1, 'drafting', '{}')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        effect_confirm_profile(
            conn, thread_id="j1", business_key="1", profile_dict={}, reviewer="   "
        )

    assert conn.execute("SELECT status FROM job_profile").fetchone()[0] == "drafting"
    assert conn.execute("SELECT status FROM job").fetchone()[0] == "drafting"
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM human_review").fetchone()[0] == 0
