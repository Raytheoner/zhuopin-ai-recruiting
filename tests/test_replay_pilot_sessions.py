"""
scripts/replay_pilot_sessions.py 的解析与统计测试。

⛔ 本文件**不调真 LLM、不联网**：默认 `pytest` 必须能在断网机器上跑完。
   真回放那条路径只有一个用例覆盖，且由环境变量 REPLAY_LIVE 显式开启。
"""

import json
import os
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema
from scripts.replay_pilot_sessions import (
    _rows_for_thread,
    business_keys,
    extract_user_turns,
    grounding_counts_by_model,
    latency_verdict,
    prefix_metrics,
    replay_metrics,
)


def _seed(conn: sqlite3.Connection, job_id: str, rows: list[dict]) -> None:
    """按 job_profile 的真实 schema 灌几行。走 init_schema 建表而不是自己写
    DDL：列名一旦漂移，这里要跟着炸，而不是悄悄测一张过时的表。"""
    conn.execute("INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,))
    for index, row in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO job_profile (id, job_id, version, status, profile_json,"
            " unspecified_fields, derived_unspecified_fields, ungrounded_fields,"
            " written_fields, llm_response_model, llm_latency_ms, is_productive)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"{job_id}-{index}",
                job_id,
                index,
                row.get("status", "drafting"),
                json.dumps(row.get("profile", {})),
                json.dumps(row.get("unspecified", [])),
                json.dumps(row.get("derived_unspecified", [])),
                json.dumps(row.get("ungrounded", [])),
                json.dumps(row.get("written", [])),
                row.get("model"),
                row.get("latency"),
                row.get("is_productive", 1),
            ),
        )
    conn.commit()


@pytest.fixture()
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "t.db"))
    init_schema(connection)
    yield connection
    connection.close()


# ── extract_user_turns ────────────────────────────────────────────────


def test_extract_user_turns_keeps_only_user_side_in_order():
    history = json.dumps(
        [
            {"role": "user", "content": "一般材料采购"},
            {"role": "assistant", "content": "需要几人？"},
            {"role": "user", "content": "1人，统招本科"},
            {"role": "assistant", "content": "几年经验？"},
            {"role": "user", "content": "2年以上"},
        ]
    )
    assert extract_user_turns(history) == ["一般材料采购", "1人，统招本科", "2年以上"]


def test_extract_user_turns_drops_empty_content():
    """空 content 不能当成一轮输入喂回去——那会凭空多打一次模型，把总轮数抬高。"""
    history = json.dumps(
        [
            {"role": "user", "content": "采购部"},
            {"role": "user", "content": ""},
            {"role": "user"},
        ]
    )
    assert extract_user_turns(history) == ["采购部"]


# ── business_keys ─────────────────────────────────────────────────────


def test_business_keys_excludes_flow_artifacts_and_empty_values():
    profile = json.dumps(
        {
            "job_title": "采购工程师",
            "headcount": 1,
            "department": None,
            "toolchain": [],
            "core_skills": "",
            "_jd_text": "……",
            "_jd_needs_manual": True,
        }
    )
    assert business_keys(profile) == {"job_title", "headcount"}


# ── prefix_metrics（修复前）────────────────────────────────────────────


def test_prefix_metrics_derives_idle_rounds_from_field_growth(conn):
    """历史行的 is_productive 恒为迁移默认值 1，直接读会得到「空转轮恒为 0」这个
    假象。修复前那一侧必须改用「本轮没有新增业务字段」推导。"""
    _seed(
        conn,
        "aaaaaaaa-0000-0000-0000-000000000000",
        [
            {"profile": {"job_title": "采购"}},
            {"profile": {"job_title": "采购", "headcount": 1}},
            {"profile": {"job_title": "采购", "headcount": 1}},  # 空转
            {"profile": {"job_title": "采购", "headcount": 1, "_jd_text": "x"}},  # 只加流程产物 → 仍是空转
            {"profile": {"job_title": "采购", "headcount": 1, "department": "采购部", "_jd_text": "x"}},
        ],
    )
    rows = _rows_for_thread(conn, "aaaaaaaa")
    metrics = prefix_metrics(rows, "样例")

    assert metrics.total_rounds == 5
    assert metrics.idle_rounds == 2
    assert metrics.idle_rounds_is_proxy is True


def test_prefix_metrics_reads_final_unspecified_from_model_claimed_column(conn):
    _seed(
        conn,
        "bbbbbbbb-0000-0000-0000-000000000000",
        [
            {"profile": {"job_title": "软件"}, "unspecified": ["a", "b", "c"]},
            {
                "profile": {"job_title": "软件", "headcount": 2},
                "unspecified": ["headcount", "toolchain"],
                # 修复后那一列在历史行上是默认空数组，⛔ 不能被误当成修复前的取值
                "derived_unspecified": [],
            },
        ],
    )
    metrics = prefix_metrics(_rows_for_thread(conn, "bbbbbbbb"), "样例")

    assert metrics.final_unspecified_count == 2
    assert "模型自称" in metrics.final_unspecified_source


def test_prefix_metrics_reports_no_latency_baseline_when_column_is_null(conn):
    _seed(
        conn,
        "cccccccc-0000-0000-0000-000000000000",
        [{"profile": {"job_title": "x"}, "latency": None}] * 3,
    )
    metrics = prefix_metrics(_rows_for_thread(conn, "cccccccc"), "样例")

    assert metrics.latency_mean_ms is None
    assert metrics.latency_max_ms is None
    assert metrics.latency_available is False


# ── replay_metrics（修复后）────────────────────────────────────────────


def test_replay_metrics_counts_idle_rounds_from_measured_is_productive(conn):
    _seed(
        conn,
        "dddddddd-0000-0000-0000-000000000000",
        [
            {"profile": {"job_title": "x"}, "is_productive": 1, "latency": 1000.0},
            {"profile": {"job_title": "x"}, "is_productive": 0, "latency": 3000.0},
            {
                "profile": {"job_title": "x", "headcount": 1},
                "is_productive": 1,
                "latency": 2000.0,
                "derived_unspecified": ["toolchain", "mcu_family"],
            },
        ],
    )
    metrics = replay_metrics(_rows_for_thread(conn, "dddddddd"), "样例")

    assert metrics.total_rounds == 3
    assert metrics.idle_rounds == 1
    assert metrics.idle_rounds_is_proxy is False
    assert metrics.final_unspecified_count == 2
    assert metrics.latency_mean_ms == pytest.approx(2000.0)
    assert metrics.latency_max_ms == pytest.approx(3000.0)


def test_replay_metrics_reads_derived_column_not_model_claimed_one(conn):
    """第 6 章起真源是 derived_unspecified_fields。读错列的症状是「警示块少列几个
    字段」——不报错、没人会发现，所以这条必须有测试钉住。"""
    _seed(
        conn,
        "eeeeeeee-0000-0000-0000-000000000000",
        [
            {
                "profile": {"job_title": "x"},
                "unspecified": ["只是模型自称"],
                "derived_unspecified": ["a", "b", "c", "d"],
            }
        ],
    )
    metrics = replay_metrics(_rows_for_thread(conn, "eeeeeeee"), "样例")

    assert metrics.final_unspecified_count == 4


# ── grounding_counts_by_model（8.7 的分子分母）──────────────────────────


def test_grounding_counts_group_by_response_model(conn):
    _seed(
        conn,
        "ffffffff-0000-0000-0000-000000000000",
        [
            {
                "profile": {},
                "model": "deepseek-v4-flash",
                "ungrounded": ["mcu_family"],
                "written": ["job_title", "mcu_family"],
            },
            {
                "profile": {},
                "model": "deepseek-v4-flash",
                "ungrounded": ["mcu_family", "toolchain"],
                "written": ["mcu_family", "toolchain", "headcount"],
            },
            {"profile": {}, "model": "deepseek-chat", "ungrounded": [], "written": ["department"]},
        ],
    )
    counts = grounding_counts_by_model(_rows_for_thread(conn, "ffffffff"))

    assert counts["deepseek-v4-flash"].rounds == 2
    assert counts["deepseek-v4-flash"].ungrounded_total == 3
    assert counts["deepseek-v4-flash"].written_total == 5
    assert counts["deepseek-v4-flash"].ungrounded_field_names == {"mcu_family": 2, "toolchain": 1}
    assert counts["deepseek-chat"].ungrounded_total == 0
    assert counts["deepseek-chat"].written_total == 1


def test_grounding_counts_keep_null_model_rows_in_an_unknown_bucket(conn):
    """模型标识为空的行**不能丢**——丢掉等于让 8.7 的分母悄悄变小、编造率偏大。"""
    _seed(
        conn,
        "99999999-0000-0000-0000-000000000000",
        [{"profile": {}, "model": None, "ungrounded": ["x"], "written": ["x", "y"]}],
    )
    counts = grounding_counts_by_model(_rows_for_thread(conn, "99999999"))

    assert counts["(unknown)"].rounds == 1
    assert counts["(unknown)"].written_total == 2


# ── latency_verdict（8.5）─────────────────────────────────────────────


def _metrics(mean, maximum, *, proxy=False):
    from scripts.replay_pilot_sessions import SessionMetrics

    return SessionMetrics(
        label="样例",
        total_rounds=3,
        idle_rounds=0,
        idle_rounds_is_proxy=proxy,
        final_unspecified_count=0,
        final_unspecified_source="x",
        latency_mean_ms=mean,
        latency_max_ms=maximum,
    )


def test_latency_verdict_flags_regression_over_thirty_percent():
    assert "🔴" in latency_verdict(_metrics(1000.0, 1200.0), _metrics(1400.0, 1600.0))


def test_latency_verdict_passes_within_threshold():
    assert "✅" in latency_verdict(_metrics(1000.0, 1200.0), _metrics(1250.0, 1400.0))


def test_latency_verdict_says_incomparable_rather_than_pass_when_baseline_missing():
    """没有基线时**不得**折成「通过」——那会把「没测」写成「测过了没问题」。"""
    verdict = latency_verdict(_metrics(None, None, proxy=True), _metrics(1500.0, 2000.0))

    assert "不可比" in verdict
    assert "✅" not in verdict


# ── 真回放（默认跳过）─────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("REPLAY_LIVE"),
    reason="真回放要联网调 LLM，默认 pytest 必须不联网；设 REPLAY_LIVE=1 才跑",
)
def test_replay_live_drives_the_app_entry_and_writes_one_row_per_turn(tmp_path):
    from scripts.replay_pilot_sessions import replay_live

    out = str(tmp_path / "replay.db")
    job_id = replay_live(["嵌入式软件工程师", "2人，本科，3年以上"], out_db_path=out)

    check = sqlite3.connect(out)
    assert check.execute("SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)).fetchone()[0] == 2
    check.close()
