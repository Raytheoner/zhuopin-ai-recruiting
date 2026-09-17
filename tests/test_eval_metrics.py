import math

import pytest

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


def test_spearman_dense_reranks_subset_when_system_rank_covers_fewer_ids():
    # 20 人工名次 1..20；系统只对其中 10 份给出排名（compare_models_m2.evaluate_model 的 rank_ok 子集）。
    # F1：闭式公式必须先对交集做稠密重排，⛔ 不能直接套用原始（跨全量的）名次，否则完美一致也会算出负值。
    ids = [f"S{i}" for i in range(20)]
    human = {sid: i + 1 for i, sid in enumerate(ids)}
    subset = ids[1::2]  # 10 个 id，人工名次 2,4,...,20，与人工顺序一致

    perfect_system = {sid: rank for rank, sid in enumerate(subset, start=1)}
    assert spearman(perfect_system, human) == 1.0

    reversed_system = {sid: len(subset) - idx for idx, sid in enumerate(subset)}
    assert math.isclose(spearman(reversed_system, human), -1.0)


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


def test_top_k_recall_raises_on_k_less_than_one():
    human = ["a", "b", "c"]
    with pytest.raises(ValueError, match="k 必须 ≥ 1"):
        top_k_recall(["a"], human, k=0)


def test_field_correct_raises_on_str_skills():
    with pytest.raises(TypeError, match="skills 期望 list"):
        field_correct("skills", "CAN", ["CAN"])


def test_field_correct_raises_on_str_companies():
    with pytest.raises(TypeError, match="companies 期望 list"):
        field_correct("companies", ["A"], "A")
