"""6.6 —— 合规断言接入测试套件与 CI。

CI 侧的接法：既有 test job 里加一个**可归因**的步骤（不另起一套 CI）。
本文件测的是 CLI 的退出码契约——CI 靠退出码判红绿，`.51` 上机巡检也靠它。

⚠️ 退出码 2（库不存在）**不能折成 0**。一个指错路径的巡检命令若安静地返回
0，读的人会以为"三条红线都守住了"，而实际上它一行数据都没查过——这跟空表
恒真是同一种谎，只是更隐蔽。
"""

import json

import pytest

from app.audit.assertions import AssertionResult, format_report, main
from app.audit.events import AI_ANALYSIS, CriterionScore, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema
from tests.test_audit_assertions import insert_run, insert_score

pytestmark = pytest.mark.compliance


@pytest.fixture(autouse=True)
def _clear_chain_class_state():
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "demo.db"
    conn = get_connection(str(path))
    init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def mirror_path(tmp_path):
    path = tmp_path / "audit" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_exit_zero_when_everything_is_clean(db_path, mirror_path, capsys):
    code = main(["--db", str(db_path), "--mirror", str(mirror_path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "全部通过" in out


def test_exit_one_when_a_red_line_is_broken(db_path, mirror_path, capsys):
    conn = get_connection(str(db_path))
    run_id = insert_run(conn)
    insert_score(
        conn, run_id=run_id, score_id="s-face",
        criterion_key="facial_expression", evidence_ref="video-1#0-10",
    )
    conn.close()

    code = main(["--db", str(db_path), "--mirror", str(mirror_path)])

    assert code == 1
    out = capsys.readouterr().out
    # spec：任一条不成立时判定为失败**并指出违例记录**。
    assert "facial_expression" in out
    assert "s-face" in out


def test_exit_two_when_db_missing(tmp_path, mirror_path, capsys):
    """⛔ 不许折成 0。指错路径的巡检安静返回 0 = 一行没查过却报"红线守住了"。"""
    code = main(["--db", str(tmp_path / "nope.db"), "--mirror", str(mirror_path)])

    assert code == 2
    assert "不存在" in capsys.readouterr().err


def test_exit_two_when_mirror_missing(db_path, tmp_path, capsys):
    code = main(["--db", str(db_path), "--mirror", str(tmp_path / "nope.jsonl")])

    assert code == 2
    assert "不存在" in capsys.readouterr().err


def test_exit_one_when_chain_is_broken(db_path, mirror_path, capsys):
    """链校验也接进 CLI（tasks 6.6：三条断言 + 链校验）。"""
    conn = get_connection(str(db_path))
    recorder = AuditRecorder(
        store=SqliteSink(conn), mirror_sink=JsonlChainSink(mirror_path)
    )
    for run_id in ("run-1", "run-2"):
        event = DecisionEvent(
            id=run_id, event_type=AI_ANALYSIS, thread_id="t-1",
            configured_model="deepseek-chat", prompt_version="score-v1",
            temperature=0.0, input_hash=f"sha256:{run_id}", raw_response="{}",
            scores=(CriterionScore("skill_match", 3.0, "resume-1#1-9"),),
        )
        recorder.record(conn, event)
        conn.commit()
        recorder.mirror(event)
    conn.close()

    lines = mirror_path.read_bytes().split(b"\n")
    record = json.loads(lines[0].decode("utf-8"))
    record["raw_response"] = "被改过"
    lines[0] = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    mirror_path.write_bytes(b"\n".join(lines))

    code = main(["--db", str(db_path), "--mirror", str(mirror_path)])

    assert code == 1
    assert "哈希链" in capsys.readouterr().out


def test_report_lists_violations_not_just_counts():
    """报告必须带违例记录本身。只报数字的话，CI 红了还得有人本地重跑一遍才知道红在哪。"""
    results = [
        AssertionResult(name="甲", ok=True),
        AssertionResult(
            name="乙", ok=False,
            violations=({"id": "s-1", "criterion_key": "face_match"},),
            detail="红线维度",
        ),
    ]

    report = format_report(results)

    assert "甲" in report and "乙" in report
    assert "s-1" in report
    assert "face_match" in report
    assert "红线维度" in report


def test_report_says_all_passed_when_nothing_is_broken():
    report = format_report([AssertionResult(name="甲", ok=True)])

    assert "全部通过" in report
