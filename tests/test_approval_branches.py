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


# ── tasks 6.5 / 6.6：修改分支 ────────────────────────────────────────────


def test_revise_records_the_decision_and_reruns_the_turn(tmp_path):
    """修改 = 记一笔留痕 + 把修改意见当作用户这一轮的原话重跑一次采集。"""
    client, job_id, _payload = _start_job(
        tmp_path, extra_responses=[COMPLETE_PROFILE_RESPONSE]
    )
    resp = client.post(f"api/jobs/{job_id}/revise", json={"feedback": "人数改成 3 个"})
    assert resp.status_code == 200
    assert resp.json()["message"]["type"] == "confirmation_prompt"

    rows = _rows(
        tmp_path,
        "SELECT decision_type, feedback, profile_version FROM human_review WHERE job_id = ?",
        (job_id,),
    )
    assert rows == [("revision_requested", "人数改成 3 个", 1)]


def test_revise_keeps_every_draft_version(tmp_path):
    """tasks 6.5 后半：**保留每一版草案**（新 version，⛔ 不覆盖旧行）。"""
    client, job_id, _payload = _start_job(
        tmp_path, extra_responses=[COMPLETE_PROFILE_RESPONSE]
    )
    client.post(f"api/jobs/{job_id}/revise", json={"feedback": "人数改成 3 个"})

    versions = _rows(
        tmp_path, "SELECT version, status FROM job_profile WHERE job_id = ? ORDER BY version", (job_id,)
    )
    assert versions == [(1, "drafting"), (2, "drafting")]


def test_revise_sends_the_previous_profile_back_to_the_model(tmp_path):
    """tasks 6.5 前半：**基于原画像 + 修改意见**重新产出，不是从零重来。"""
    client, scripted = make_app_with_scripted_client(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, COMPLETE_PROFILE_RESPONSE]
    )
    job_id = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"}).json()["job_id"]
    client.post(f"api/jobs/{job_id}/revise", json={"feedback": "人数改成 3 个"})

    last_call = json.dumps(scripted.chat.completions.calls[-1], ensure_ascii=False)
    assert "底层软件工程师" in last_call, "上一版画像没有随 prompt 一起送进去"
    assert "人数改成 3 个" in last_call, "修改意见没有进 prompt"


def test_revise_rejects_blank_feedback(tmp_path):
    """"以自然语言描述要改什么"——没写内容就不是一次修改意见。"""
    client, job_id, _payload = _start_job(tmp_path)
    assert client.post(f"api/jobs/{job_id}/revise", json={"feedback": "   "}).status_code == 422


def test_revise_retried_at_the_same_version_records_one_review(tmp_path):
    """幂等键 = {job_id}:effect_request_revision:{version}。"""
    from app.graph.nodes import effect_request_revision
    from app.storage.db import get_connection, init_schema

    conn = get_connection(str(tmp_path / "idem.db"))
    init_schema(conn)
    for _ in range(3):
        effect_request_revision(
            conn, thread_id="j1", business_key="1", reviewer="tester", feedback="改一下"
        )

    assert conn.execute("SELECT COUNT(*) FROM human_review").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_request_revision'"
        ).fetchone()[0]
        == 1
    )


def test_revision_limit_blocks_the_sixth_request(tmp_path):
    """tasks 6.6：修改次数达到 5 次 → 提示转人工，⛔ 不再受理第 6 次。"""
    from app.graph.nodes import MAX_REVISIONS
    from app.storage.db import get_connection

    assert MAX_REVISIONS == 5

    client, job_id, _payload = _start_job(tmp_path)
    conn = get_connection(_db_path(tmp_path))
    for version in range(1, MAX_REVISIONS + 1):
        conn.execute(
            "INSERT INTO human_review "
            "(id, job_id, profile_version, decision_type, reviewer) VALUES (?, ?, ?, ?, ?)",
            (f"{job_id}-v{version}-revision_requested", job_id, version,
             "revision_requested", "tester"),
        )
    conn.commit()
    conn.close()

    resp = client.post(f"api/jobs/{job_id}/revise", json={"feedback": "再改一次"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["revision_count"] == 5 and detail["max_revisions"] == 5
    assert "HR" in detail["message"], "超限提示必须说清下一步是转人工编辑"


def test_revision_limit_still_allows_confirming(tmp_path):
    """超限只关掉"再改一次"这条路，⛔ 不把人锁死在页面上。

    spec：「系统提示转人工，由 HR 直接编辑画像后提交确认」——确认这条路必须还在。
    """
    from app.graph.nodes import MAX_REVISIONS
    from app.storage.db import get_connection

    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    conn = get_connection(_db_path(tmp_path))
    for version in range(1, MAX_REVISIONS + 1):
        conn.execute(
            "INSERT INTO human_review "
            "(id, job_id, profile_version, decision_type, reviewer) VALUES (?, ?, ?, ?, ?)",
            (f"{job_id}-v{version}-revision_requested", job_id, version,
             "revision_requested", "tester"),
        )
    conn.commit()
    conn.close()

    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200


def test_cannot_revise_a_frozen_profile(tmp_path):
    """决策四：画像冻结后不可修改，如需变更必须创建新版本（＝新建岗位）。"""
    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    client.post(f"api/jobs/{job_id}/confirm")

    resp = client.post(f"api/jobs/{job_id}/revise", json={"feedback": "再改一下"})
    assert resp.status_code == 409


# ── 范围追加（控制器裁决）：/reply 补 approved 终态守卫 ──────────────────


def test_reply_rejects_an_approved_job_and_creates_no_new_draft(tmp_path):
    """spec 6.4「确认后冻结」：画像 approved 之后，/reply 不能再悄悄产出一版
    新草案。少了这道守卫，/reply 会绕过 confirm/revise 已有的终态守卫，把一个
    已冻结的岗位重新拖回 drafting 语义——human_review 里那条 approved 留痕
    会与后续被 /reply 改动的画像内容直接矛盾。"""
    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200

    versions_before = _rows(
        tmp_path,
        "SELECT version FROM job_profile WHERE job_id = ? ORDER BY version",
        (job_id,),
    )

    resp = client.post(f"api/jobs/{job_id}/reply", json={"message": "再改改"})
    assert resp.status_code == 409

    versions_after = _rows(
        tmp_path,
        "SELECT version FROM job_profile WHERE job_id = ? ORDER BY version",
        (job_id,),
    )
    assert versions_after == versions_before, "/reply 在 approved 状态下产生了新草案版本"


# ── tasks 6.7：放弃分支 ──────────────────────────────────────────────────


def test_abandon_sets_status_and_keeps_every_byte_of_content(tmp_path):
    """tasks 6.7 原话：置 abandoned，**保留内容**。

    放弃不是撤销——事后要能查明"当时放弃的是哪一版画像、内容长什么样"。
    """
    client, job_id, _payload = _start_job(tmp_path)
    before = _rows(
        tmp_path, "SELECT profile_json FROM job_profile WHERE job_id = ?", (job_id,)
    )

    resp = client.post(f"api/jobs/{job_id}/abandon", json={"reason": "岗位取消了"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "abandoned"

    assert (
        _rows(tmp_path, "SELECT profile_json FROM job_profile WHERE job_id = ?", (job_id,))
        == before
    ), "放弃动作动了画像内容"
    assert _rows(tmp_path, "SELECT status FROM job WHERE id = ?", (job_id,)) == [("abandoned",)]
    assert _rows(
        tmp_path, "SELECT status FROM job_profile WHERE job_id = ?", (job_id,)
    ) == [("abandoned",)]


def test_abandon_records_the_decision_with_the_reason(tmp_path):
    client, job_id, _payload = _start_job(tmp_path)
    client.post(f"api/jobs/{job_id}/abandon", json={"reason": "岗位取消了"})

    assert _rows(
        tmp_path,
        "SELECT decision_type, feedback, profile_version FROM human_review WHERE job_id = ?",
        (job_id,),
    ) == [("abandoned", "岗位取消了", 1)]


def test_abandon_without_a_reason_is_allowed(tmp_path):
    """⛔ 不强制填理由：强制填理由的表单会得到"1"和"。"。留痕的必填项是
    决策人、决策类型、时间、画像版本——理由是加分项，不是门槛。"""
    client, job_id, _payload = _start_job(tmp_path)
    assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200
    assert _rows(
        tmp_path, "SELECT feedback FROM human_review WHERE job_id = ?", (job_id,)
    ) == [(None,)]


def test_abandon_is_idempotent(tmp_path):
    """重复 POST（双击、客户端超时重发、反向代理重试）只留一条痕。"""
    client, job_id, _payload = _start_job(tmp_path)
    assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200
    assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200

    assert _rows(
        tmp_path, "SELECT COUNT(*) FROM human_review WHERE job_id = ?", (job_id,)
    ) == [(1,)]


def test_abandoned_job_cannot_be_replied_to_or_confirmed(tmp_path):
    """放弃是终态。少了这道守卫，"放弃"就只是一个影响显示的标签——
    岗位能被 /reply 复活、再被确认，而 human_review 里那条 abandoned 留痕
    会与最终 approved 的状态直接矛盾。"""
    client, job_id, _payload = _start_job(tmp_path)
    client.post(f"api/jobs/{job_id}/abandon")

    assert client.post(f"api/jobs/{job_id}/reply", json={"message": "再改改"}).status_code == 409
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 409
    assert client.post(f"api/jobs/{job_id}/revise", json={"feedback": "改"}).status_code == 409


def test_abandon_human_review_row_count_equals_effect_log_count_per_thread(tmp_path):
    """铁律 1 的 reviewer 判据，放弃分支那一份：**同一 thread、同一
    business_key 重放**，effect_abandon_profile 的 effect_log 条数必须与
    human_review 条数按 thread 恒等。只调一次 /abandon 测不出重放是否幂等——
    这里直接调用 effect_abandon_profile 三次，模拟 LangGraph 恢复时节点从头
    重跑。"""
    from app.graph.nodes import effect_abandon_profile
    from app.storage.db import get_connection, init_schema

    conn = get_connection(str(tmp_path / "idem.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'x', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('j1-v1', 'j1', 1, 'drafting', '{}')"
    )
    conn.commit()

    for _ in range(3):
        effect_abandon_profile(
            conn, thread_id="j1", business_key="1", reviewer="tester", feedback="重复放弃"
        )

    effects = conn.execute(
        "SELECT COUNT(*) FROM effect_log "
        "WHERE thread_id = ? AND node_name = 'effect_abandon_profile'",
        ("j1",),
    ).fetchone()[0]
    reviews = conn.execute(
        "SELECT COUNT(*) FROM human_review WHERE job_id = ? AND decision_type = 'abandoned'",
        ("j1",),
    ).fetchone()[0]
    assert effects == reviews == 1
