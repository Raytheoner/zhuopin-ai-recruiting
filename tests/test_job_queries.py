import ast
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.graph.nodes import DECISION_REVISION_REQUESTED, MAX_REVISIONS
from app.middleware.auth import UNKNOWN_REVIEWER
from app.storage import job_queries
from app.storage.db import init_schema


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "q.db"))
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


def _insert_job(conn, job_id, status="drafting", created_at="2026-09-01 10:00:00"):
    conn.execute(
        "INSERT INTO job (id, title, status, created_at) VALUES (?, '待确定', ?, ?)",
        (job_id, status, created_at),
    )


def _insert_version(conn, job_id, version, profile, *, status="drafting",
                    created_at="2026-09-01 10:01:00", derived=(), asked=(),
                    written=(), ungrounded=(), model="deepseek-chat-241226",
                    latency=1234.5, productive=1):
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, "
        "unspecified_fields, derived_unspecified_fields, created_at, is_productive, "
        "turn_started_at, llm_latency_ms, ungrounded_fields, written_fields, "
        "llm_response_model, asked_questions) "
        "VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-v{version}",
            job_id,
            version,
            status,
            json.dumps(profile, ensure_ascii=False),
            json.dumps(list(derived), ensure_ascii=False),
            created_at,
            productive,
            "2026-09-01 10:00:55",
            latency,
            json.dumps(list(ungrounded), ensure_ascii=False),
            json.dumps(list(written), ensure_ascii=False),
            model,
            json.dumps(list(asked), ensure_ascii=False),
        ),
    )


def _insert_review(conn, job_id, version, decision_type, *, reviewer="unknown:web-session",
                   feedback=None, decided_at="2026-09-01 11:00:00"):
    conn.execute(
        "INSERT INTO human_review (id, job_id, profile_version, decision_type, reviewer, "
        "feedback, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-{version}-{decision_type}",
            job_id,
            version,
            decision_type,
            reviewer,
            feedback,
            decided_at,
        ),
    )


def test_latest_profile_rows_returns_only_the_newest_version_per_job(conn):
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {"job_title": "旧标题"}, created_at="2026-09-01 10:01:00")
    _insert_version(conn, "j1", 2, {"job_title": "新标题"}, created_at="2026-09-01 10:05:00")

    rows = job_queries.latest_profile_rows(conn)

    assert len(rows) == 1
    assert rows[0]["latest_version"] == 2
    assert rows[0]["profile"]["job_title"] == "新标题"
    assert rows[0]["updated_at"] == "2026-09-01 10:05:00"
    # I-1：裸值原样保留给逻辑用，label 是转好的东八区展示文案。
    assert rows[0]["updated_at_label"] == job_queries.to_shanghai_label(rows[0]["updated_at"])
    assert rows[0]["created_at_label"] == job_queries.to_shanghai_label(rows[0]["created_at"])


def test_latest_profile_rows_keeps_jobs_that_have_no_profile_yet(conn):
    """第一轮就失败的 job 在库里是"有 job、没有任何 job_profile"。

    INNER JOIN 会让它从列表里彻底消失——而它恰恰是最需要被人看见的那一种。
    """
    _insert_job(conn, "empty", created_at="2026-09-02 09:00:00")

    rows = job_queries.latest_profile_rows(conn)

    assert [row["job_id"] for row in rows] == ["empty"]
    assert rows[0]["latest_version"] is None
    assert rows[0]["profile"] == {}
    assert rows[0]["updated_at"] == "2026-09-02 09:00:00"


def test_latest_profile_rows_sorts_most_recently_active_first(conn):
    _insert_job(conn, "old", created_at="2026-09-01 08:00:00")
    _insert_version(conn, "old", 1, {}, created_at="2026-09-01 08:01:00")
    _insert_job(conn, "new", created_at="2026-09-01 09:00:00")
    _insert_version(conn, "new", 1, {}, created_at="2026-09-03 15:00:00")

    assert [row["job_id"] for row in job_queries.latest_profile_rows(conn)] == ["new", "old"]


def test_revision_counts_only_counts_revision_requests(conn):
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {})
    _insert_review(conn, "j1", 1, DECISION_REVISION_REQUESTED)
    _insert_review(conn, "j1", 2, DECISION_REVISION_REQUESTED, decided_at="2026-09-01 11:05:00")
    _insert_review(conn, "j1", 3, "approved", decided_at="2026-09-01 11:10:00")

    counts = job_queries.revision_counts(conn, revision_decision_type=DECISION_REVISION_REQUESTED)

    assert counts == {"j1": 2}


def test_latest_message_types_returns_last_row_per_thread(conn):
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES ('j1', 'question', '{}')"
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) "
        "VALUES ('j1', 'confirmation_prompt', '{}')"
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES ('j2', 'question', '{}')"
    )

    assert job_queries.latest_message_types(conn) == {
        "j1": "confirmation_prompt",
        "j2": "question",
    }


def test_to_shanghai_label_converts_utc_to_asia_shanghai(conn):
    """final review I-1：无锡看到的每一个时间戳都必须是东八区。⛔ 不写死一个
    魔数字符串——通用换算：期望值由裸 UTC 值 + 8 小时现算，不是提前算好的
    一个固定答案（那样测试和实现完全可能对着同一个错误的公式抄出一致的结果）。
    """
    utc = "2026-09-01 10:30:00"

    label = job_queries.to_shanghai_label(utc)

    expected = (
        datetime.strptime(utc, "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
    ).strftime("%Y-%m-%d %H:%M:%S")
    assert label == expected
    # 换算结果的小时数必须逐字等于 UTC 小时 + 8（对 24 取模，覆盖跨天进位）。
    utc_hour = datetime.strptime(utc, "%Y-%m-%d %H:%M:%S").hour
    label_hour = datetime.strptime(label, "%Y-%m-%d %H:%M:%S").hour
    assert label_hour == (utc_hour + 8) % 24


def test_to_shanghai_label_rolls_over_into_the_next_day(conn):
    """跨天进位是这条换算最容易被写错的地方：UTC 20:00 之后的任何时刻，
    东八区都已经是第二天。"""
    utc = "2026-09-01 20:15:30"

    label = job_queries.to_shanghai_label(utc)

    assert label == "2026-09-02 04:15:30"


def test_to_shanghai_label_passes_through_none_and_malformed_values(conn):
    """NULL 与格式异常的历史脏数据 ⛔ 不能让整页 500——原样返回好过白屏。"""
    assert job_queries.to_shanghai_label(None) is None
    assert job_queries.to_shanghai_label("") == ""
    assert job_queries.to_shanghai_label("not-a-timestamp") == "not-a-timestamp"


def test_display_title_prefers_profile_job_title_over_placeholder(conn):
    row = {"title": "待确定", "profile": {"job_title": "  嵌入式软件工程师  "}}

    assert job_queries.display_title(row) == "嵌入式软件工程师"


def test_display_title_falls_back_to_job_row_title_when_profile_has_none(conn):
    assert job_queries.display_title({"title": "待确定", "profile": {}}) == "待确定"
    assert job_queries.display_title({"title": "待确定", "profile": {"job_title": "   "}}) == "待确定"


def test_jd_state_reads_the_three_internal_keys():
    assert job_queries.jd_state({}) == {
        "generated": False,
        "needs_manual": False,
        "human_written": False,
    }
    assert job_queries.jd_state(
        {"_jd_text": "岗位职责…", "_jd_needs_manual": True, "_jd_authorship": {"marked_by": "x"}}
    ) == {"generated": True, "needs_manual": True, "human_written": True}


def test_needs_manual_reasons_empty_for_a_healthy_job():
    assert job_queries.derive_needs_manual_reasons(
        job_status="drafting", profile={}, revision_count=0, max_revisions=MAX_REVISIONS
    ) == []


def test_needs_manual_reason_from_job_status_column():
    """WBS 2.5 落地当天这一条自动生效，⛔ 不许因为"现在无人写入"而省掉。"""
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="needs_manual", profile={}, revision_count=0, max_revisions=MAX_REVISIONS
    )

    assert [r["code"] for r in reasons] == [job_queries.REASON_JOB_STATUS]


def test_needs_manual_reason_from_jd_discrimination_flag():
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="approved",
        profile={"_jd_needs_manual": True},
        revision_count=0,
        max_revisions=MAX_REVISIONS,
    )

    assert [r["code"] for r in reasons] == [job_queries.REASON_JD_DISCRIMINATION]


def test_needs_manual_reason_from_revision_limit_and_label_carries_the_number():
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="drafting", profile={}, revision_count=5, max_revisions=5
    )

    assert [r["code"] for r in reasons] == [job_queries.REASON_REVISION_LIMIT]
    # 上限数字不能在文案里写死：写死之后改 MAX_REVISIONS 界面上不会跟着变，
    # 而且不报错——业务经理看到的上限和系统实际执行的上限会悄悄不一致。
    assert "5" in reasons[0]["label"]


def test_needs_manual_reason_from_revision_limit_is_suppressed_once_approved():
    """final review I-2：approved 岗位一旦撞过修改上限就是**一件已经做完的事**
    ——revise() 对 approved 直接 409，revision_counts 也不会再变。继续产出
    这条理由会让这类岗位永久钉在转人工队列里、没有任何路径能清掉。"""
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="approved", profile={}, revision_count=5, max_revisions=5
    )

    assert reasons == []


def test_needs_manual_reason_from_jd_discrimination_still_fires_when_approved():
    """反证，防止上一条的修法写过头：⛔ 不能整体过滤 approved——
    `_jd_needs_manual` 恰恰只出现在 approved 岗位上，那是今天队列里唯一
    真实存在的写入方，整体过滤会得到一个恒空队列。"""
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="approved",
        profile={"_jd_needs_manual": True},
        revision_count=5,
        max_revisions=5,
    )

    assert [r["code"] for r in reasons] == [job_queries.REASON_JD_DISCRIMINATION]


def test_needs_manual_reasons_can_stack():
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="needs_manual",
        profile={"_jd_needs_manual": True},
        revision_count=9,
        max_revisions=5,
    )

    assert [r["code"] for r in reasons] == [
        job_queries.REASON_JOB_STATUS,
        job_queries.REASON_JD_DISCRIMINATION,
        job_queries.REASON_REVISION_LIMIT,
    ]


def test_stage_label_covers_every_reachable_state():
    healthy_jd = {"generated": False, "needs_manual": False, "human_written": False}
    generated_jd = {"generated": True, "needs_manual": False, "human_written": False}

    assert job_queries.stage_label(
        job_status="abandoned", latest_version=2, latest_message_type=None, jd=healthy_jd
    ) == "已放弃"
    assert job_queries.stage_label(
        job_status="needs_manual", latest_version=2, latest_message_type=None, jd=healthy_jd
    ) == "待人工处理"
    assert job_queries.stage_label(
        job_status="approved", latest_version=2, latest_message_type=None, jd=generated_jd
    ) == "已确认 · JD 已生成"
    assert job_queries.stage_label(
        job_status="approved", latest_version=2, latest_message_type=None, jd=healthy_jd
    ) == "已确认"
    assert job_queries.stage_label(
        job_status="drafting", latest_version=None, latest_message_type=None, jd=healthy_jd
    ) == "刚发起，还没有画像"
    assert job_queries.stage_label(
        job_status="drafting", latest_version=1,
        latest_message_type="confirmation_prompt", jd=healthy_jd
    ) == "等你确认"
    assert job_queries.stage_label(
        job_status="drafting", latest_version=1, latest_message_type="question", jd=healthy_jd
    ) == "追问中"


def test_stage_label_never_leaks_english_status():
    """合规不相干，但界面纪律相干：⛔ 英文 status 不得出现在业务经理眼前。"""
    for status in ("drafting", "approved", "abandoned", "needs_manual"):
        label = job_queries.stage_label(
            job_status=status,
            latest_version=1,
            latest_message_type="question",
            jd={"generated": False, "needs_manual": False, "human_written": False},
        )
        assert status not in label


def test_profile_versions_returns_every_version_ascending_with_snapshot(conn):
    _insert_job(conn, "j1")
    _insert_version(
        conn, "j1", 1, {"job_title": "嵌入式工程师", "department": "研发部"},
        derived=["experience_years"], asked=[{"question_id": "q1"}, {"question_id": "q2"}],
        written=["job_title"], ungrounded=["mcu_family"],
        model="deepseek-chat-241226", latency=2100.0,
    )
    _insert_version(
        conn, "j1", 2, {"job_title": "嵌入式工程师", "_jd_text": "岗位职责…"},
        status="approved", created_at="2026-09-01 10:20:00", productive=0,
    )

    versions = job_queries.profile_versions(conn, "j1")

    assert [v["version"] for v in versions] == [1, 2]
    first = versions[0]
    assert first["status_label"] == "草案"
    assert first["is_productive"] is True
    assert first["unspecified_fields"] == ["experience_years"]
    # 界面只认中文名（Global Constraints 第 13 条）。
    assert first["unspecified_field_labels"] and "experience_years" not in first["unspecified_field_labels"][0]
    assert first["asked_question_count"] == 2
    assert first["snapshot"]["llm_response_model"] == "deepseek-chat-241226"
    assert first["snapshot"]["llm_latency_ms"] == 2100.0
    assert first["snapshot"]["ungrounded_fields"] == ["mcu_family"]
    assert first["snapshot"]["written_fields"] == ["job_title"]
    assert first["jd"]["generated"] is False
    assert any(item["value"] == "嵌入式工程师" for item in first["summary"])

    second = versions[1]
    assert second["status_label"] == "已确认"
    assert second["is_productive"] is False
    assert second["jd"]["generated"] is True


def test_profile_versions_never_leaks_internal_underscore_keys_into_summary(conn):
    """`_jd_text` / `_gap_acknowledgement` 这类内部键 ⛔ 不得出现在摘要里。"""
    _insert_job(conn, "j1")
    _insert_version(
        conn, "j1", 1,
        {"job_title": "工程师", "_jd_text": "这是 JD 正文", "_gap_acknowledgement": {"acknowledged": True}},
    )

    summary = job_queries.profile_versions(conn, "j1")[0]["summary"]

    rendered = json.dumps(summary, ensure_ascii=False)
    assert "_jd_text" not in rendered
    assert "这是 JD 正文" not in rendered
    assert "_gap_acknowledgement" not in rendered


def test_profile_versions_tolerates_legacy_rows_with_null_snapshot_columns(conn):
    """.51 上 2026-08-19 之前写的行这些列是 NULL / '[]'，⛔ 不能因此抛异常。"""
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {"job_title": "工程师"}, model=None, latency=None)

    snapshot = job_queries.profile_versions(conn, "j1")[0]["snapshot"]

    assert snapshot["llm_response_model"] is None
    assert snapshot["llm_latency_ms"] is None


def test_profile_versions_translates_ungrounded_fields_to_chinese_labels(conn):
    """二审 Critical finding C1：`ungrounded_fields` 是内部英文 snake_case
    业务字段名（app/graph/state.py:83-85），⛔ 不得直接给前端渲染。

    修法与它的姐妹字段 unspecified_field_labels 完全同构：这里补
    ungrounded_field_labels，用同一个 field_labels() 翻译，⛔ 不新造一份
    映射表——两份译法迟早会在某个字段上不一致，而不一致没有任何症状。
    这条测试钉住这个新键存在、顺序与原始英文列表一一对应、且是中文。
    """
    _insert_job(conn, "j1")
    _insert_version(
        conn, "j1", 1, {"job_title": "工程师"},
        ungrounded=["mcu_family", "functional_safety"],
    )

    version = job_queries.profile_versions(conn, "j1")[0]

    # 原始英文列表（给逻辑用）依旧原样保留，本单元只读不改既有键。
    assert version["snapshot"]["ungrounded_fields"] == ["mcu_family", "functional_safety"]
    labels = version["snapshot"]["ungrounded_field_labels"]
    assert labels == ["MCU 平台", "功能安全等级"]
    # 界面只认中文名（Global Constraints 第 13 条）：翻译结果里不能再带
    # 英文原始字段名的任何一个子串。
    for raw, label in zip(version["snapshot"]["ungrounded_fields"], labels):
        assert raw not in label


def test_decision_records_are_chronological_and_labelled_in_chinese(conn):
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {})
    _insert_review(conn, "j1", 1, DECISION_REVISION_REQUESTED,
                   feedback="人数改成 3 个", decided_at="2026-09-01 11:00:00")
    _insert_review(conn, "j1", 2, "approved", decided_at="2026-09-01 12:00:00")

    records = job_queries.decision_records(conn, "j1", unknown_reviewer=UNKNOWN_REVIEWER)

    assert [r["profile_version"] for r in records] == [1, 2]
    assert records[0]["decision_label"] == "要求修改"
    assert records[0]["feedback"] == "人数改成 3 个"
    assert records[1]["decision_label"] == "确认"
    for record in records:
        assert record["decision_type"] not in record["decision_label"]
    # M1：决策人恒为 UNKNOWN_REVIEWER（鉴权空壳阶段），裸值直接展示是一串
    # 没有意义的英文（"unknown:web-session"）。reviewer_label 映射成中文，
    # 裸值原样保留给逻辑用。
    assert records[0]["reviewer"] == UNKNOWN_REVIEWER
    assert records[0]["reviewer_label"] == "未登录（演示环境）"
    assert records[0]["decided_at_label"]


def test_decision_records_shows_a_real_reviewer_identity_unchanged(conn):
    """SSO 落地后 reviewer 会是真实的企微 userid——这时 ⛔ 不能被误判成
    UNKNOWN_REVIEWER 而被替换成"未登录（演示环境）"这句话。"""
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {})
    _insert_review(conn, "j1", 1, "approved", reviewer="wxid_real_person")

    record = job_queries.decision_records(conn, "j1", unknown_reviewer=UNKNOWN_REVIEWER)[0]

    assert record["reviewer_label"] == "wxid_real_person"


def _non_docstring_literals(path: str) -> list[str]:
    """模块里所有**非 docstring** 的字符串字面量。

    ⛔ 不扫整份源码：本模块的注释里逐字写着"⛔ 一条 INSERT / UPDATE / DELETE
    都不许有"这类说明，扫全文会被自己的注释判违例——而一条永远红的断言等于
    没有断言，下一个人只会把它删掉。注释根本不进 AST，docstring 显式排除，
    剩下的字符串字面量正好就是 SQL 所在的地方。
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                docstring_nodes.add(id(node.body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]


def test_module_contains_no_write_statements():
    """本单元的硬边界（Global Constraints 第 1/7 条）：只读模块里出现任何一条
    写语句都是违例。这条断言是机器判据，⛔ 不要靠 review 眼力守。"""
    for literal in _non_docstring_literals("app/storage/job_queries.py"):
        upper = literal.upper()
        for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE", "DROP TABLE"):
            assert statement not in upper, f"只读查询层里不许出现 {statement}：{literal!r}"


def test_module_does_not_import_graph_or_agents_layer():
    """Global Constraints 第 10 条：storage → graph 是层次倒置。

    按 AST 里真实的 import 判，⛔ 不按文本包含判：模块注释里正写着"本模块不
    import app.graph"，文本判会把这句说明本身当成违例。
    """
    tree = ast.parse(Path("app/storage/job_queries.py").read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    for name in imported:
        assert not name.startswith("app.graph"), f"⛔ 层次倒置：{name}"
        assert not name.startswith("app.agents"), f"⛔ 层次倒置：{name}"
