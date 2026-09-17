import math

from app.eval.metrics import (
    MIN_SAMPLES_FOR_RANK_METRICS,
    THRESHOLDS,
    field_accuracy,
    field_correct,
    normalize_company,
    spearman,
    span_traceability,
    top_k_recall,
)
from app.parsing.spans import split_into_spans


def test_thresholds_match_design_d9():
    assert THRESHOLDS == {"field_accuracy": 0.90, "spearman": 0.70, "top_k_recall": 0.85, "span_traceability": 1.0}
    assert MIN_SAMPLES_FOR_RANK_METRICS == 10


def test_company_suffix_and_fullwidth_do_not_count_as_error():
    assert normalize_company("上海某某科技有限公司") == normalize_company("上海某某科技")
    assert field_correct("companies", ["上海某某科技有限公司"], ["上海 某某科技"])


def test_years_rounded_before_compare():
    assert field_correct("years_of_experience", 4.6, 5)
    assert not field_correct("years_of_experience", 3, 5)


def test_skills_jaccard_threshold_0_8():
    truth = ["C", "AUTOSAR", "CAN", "LIN", "UDS"]
    assert field_correct("skills", ["c", "AUTOSAR", "CAN", "LIN", "UDS"], truth)  # 5/5
    assert field_correct("skills", ["C", "AUTOSAR", "CAN", "LIN"], truth)  # 4/5 = 0.8
    assert not field_correct("skills", ["C", "AUTOSAR", "CAN"], truth)  # 3/5


def test_education_compares_degree_and_school():
    assert field_correct("education", {"degree": "本科", "school": "江南 大学"}, {"degree": "本科", "school": "江南大学"})
    assert not field_correct("education", {"degree": "硕士", "school": "江南大学"}, {"degree": "本科", "school": "江南大学"})


def test_not_mentioned_counts_correct_only_when_both_none():
    assert field_correct("expected_city", None, None)
    assert not field_correct("expected_city", "无锡", None)
    assert not field_correct("expected_city", None, "无锡")


def test_field_accuracy_overall_is_mean_of_six_fields():
    truths = {"S1": {"name": "张三", "years_of_experience": 5, "skills": ["C"], "companies": ["A公司"], "education": {"degree": "本科", "school": "X"}, "expected_city": None}}
    preds = {"S1": {**truths["S1"], "name": "李四"}}
    acc = field_accuracy(preds, truths)
    assert acc.n == 1
    assert acc.per_field["name"] == 0.0
    assert acc.per_field["skills"] == 1.0
    assert math.isclose(acc.overall, 5 / 6)


def test_field_accuracy_ignores_samples_without_prediction():
    truths = {"S1": {"name": "a"}, "S2": {"name": "b"}}
    acc = field_accuracy({"S1": {"name": "a"}}, truths)
    assert acc.n == 1


def test_spearman_perfect_and_reversed():
    ids = [f"S{i}" for i in range(10)]
    human = {s: i + 1 for i, s in enumerate(ids)}
    assert spearman(human, human) == 1.0
    reversed_rank = {s: 10 - i for i, s in enumerate(ids)}
    assert math.isclose(spearman(reversed_rank, human), -1.0)


def test_spearman_none_below_min_samples():
    ids = [f"S{i}" for i in range(9)]
    human = {s: i + 1 for i, s in enumerate(ids)}
    assert spearman(human, human) is None


def test_top_k_recall_uses_half_n_when_small():
    human = ["a", "b", "c", "d", "e", "f"]  # n=6 ⇒ k=3
    assert top_k_recall(["a", "b", "x", "c"], human) == 2 / 3
    assert top_k_recall(["a", "b", "c"], human) == 1.0


def test_top_k_recall_default_k_10():
    human = [f"h{i}" for i in range(20)]
    system = [f"h{i}" for i in range(5)] + [f"z{i}" for i in range(15)]
    assert top_k_recall(system, human) == 0.5


def test_span_traceability_requires_existing_span_and_in_bounds():
    spans = split_into_spans("abc\ndef")
    assert span_traceability([(1, 0, 3), (2, 4, 7)], spans) == 1.0
    assert span_traceability([(1, 0, 3), (2, 4, 9)], spans) == 0.5  # 越界
    assert span_traceability([(1, None, None)], spans) == 0.0  # 未反查到
    assert span_traceability([(9, 0, 1)], spans) == 0.0  # 分片不存在
    assert span_traceability([], spans) == 0.0
