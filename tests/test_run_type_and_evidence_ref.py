"""analysis_run.run_type 语义约定 ＋ criterion_score.evidence_ref 的 JSON 回指约定（tasks 2.6）。

两者都不改表结构：run_type 不加列，用 prompt_version 前缀区分（design.md
「analysis_run 增加 run_type 语义约定...⛔ 不加列」）；evidence_ref 列本身
仍是自由文本（M1 intake 场景继续可写自由格式字符串），这里只新增一种可选的
JSON 编码约定，服务 M2 精排把 evidence 存成 {span_id,start,end} 的场景。
"""
import json

import pytest

from app.audit.evidence_ref import EvidenceRef, format_evidence_ref, parse_evidence_ref
from app.audit.run_type import RUN_TYPE_PARSE, RUN_TYPE_RANK, run_type_of


def test_run_type_of_parse_prefix():
    assert run_type_of("parse-v1") == RUN_TYPE_PARSE
    assert run_type_of("parse-v2") == RUN_TYPE_PARSE


def test_run_type_of_rank_prefix():
    assert run_type_of("rank-v1") == RUN_TYPE_RANK


def test_run_type_of_unregistered_prefix_raises():
    with pytest.raises(ValueError, match="未登记"):
        run_type_of("score-v1")


def test_format_evidence_ref_produces_expected_json():
    raw = format_evidence_ref(span_id=6, start=120, end=180)
    assert json.loads(raw) == {"span_id": 6, "start": 120, "end": 180}


def test_parse_evidence_ref_round_trips():
    raw = format_evidence_ref(span_id=6, start=120, end=180)
    ref = parse_evidence_ref(raw)
    assert ref == EvidenceRef(span_id=6, start=120, end=180)


def test_parse_evidence_ref_rejects_non_json_string():
    """M1 intake 的自由格式回指（如 "resume-1#120-180"）不是本约定的合法输入，
    调用方必须先判断是否是 JSON 再调用本函数——本函数不做静默兜底。"""
    with pytest.raises(json.JSONDecodeError):
        parse_evidence_ref("resume-1#120-180")


def test_parse_evidence_ref_rejects_missing_keys():
    with pytest.raises(KeyError):
        parse_evidence_ref(json.dumps({"span_id": 6, "start": 120}))
