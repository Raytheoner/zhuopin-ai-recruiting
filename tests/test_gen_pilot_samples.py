# tests/test_gen_pilot_samples.py
import json
import re

from app.audit.criteria import CRITERION_KEY_WHITELIST
from app.schemas.resume_fields import FIELD_NAMES
from scripts.gen_pilot_samples import RUBRIC, SEED, build_profiles, render_text, truth_fields, write_all


def test_write_all_records_actual_seed_not_module_constant(tmp_path):
    # M4：--seed 传入非默认值时，truth.json 里的 seed 必须是实际用的那个，⛔ 不是模块常量 SEED。
    custom_seed = SEED + 1
    profiles = build_profiles(3, seed=custom_seed)
    truth = write_all(tmp_path, profiles, font=None, seed=custom_seed)
    assert truth["seed"] == custom_seed


def test_build_profiles_is_deterministic_and_unique():
    a = build_profiles(20, seed=SEED)
    b = build_profiles(20, seed=SEED)
    assert [p.sample_id for p in a] == [f"S{i:02d}" for i in range(1, 21)]
    assert [truth_fields(p) for p in a] == [truth_fields(p) for p in b]
    assert len({p.name for p in a}) == 20


def test_human_rank_is_permutation_and_follows_designed_fit():
    profiles = build_profiles(20)
    ranks = sorted(p.human_rank for p in profiles)
    assert ranks == list(range(1, 21))
    ordered = sorted(profiles, key=lambda p: p.human_rank)
    fits = [p.fit for p in ordered]
    assert fits == sorted(fits, reverse=True)


def test_rubric_keys_in_whitelist():
    assert {c["key"] for c in RUBRIC["criteria"]} <= CRITERION_KEY_WHITELIST
    assert RUBRIC["profile_text"]


def test_render_text_contains_truth_and_no_phone_like_digits():
    for p in build_profiles(20):
        text = render_text(p)
        truth = truth_fields(p)
        assert truth["name"] in text
        for company in truth["companies"]:
            assert company in text
        assert re.search(r"\d{11}", text) is None
        assert "@" not in text
        if truth["expected_city"] is None:
            assert "期望工作城市" not in text
        else:
            assert f"期望工作城市：{truth['expected_city']}" in text


def test_truth_fields_has_six_keys_and_some_not_mentioned_cities():
    profiles = build_profiles(20)
    for p in profiles:
        assert set(truth_fields(p)) == set(FIELD_NAMES)
    assert any(truth_fields(p)["expected_city"] is None for p in profiles)
    assert any(p.explicit_years for p in profiles) and any(not p.explicit_years for p in profiles)


def test_write_all_produces_files_and_truth_json(tmp_path):
    profiles = build_profiles(3)
    truth = write_all(tmp_path, profiles, font=None)  # font=None ⇒ 不产扫描件
    data = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    assert data == truth
    assert data["sample_class"] == "synthetic"
    assert data["seed"] == SEED
    assert len(data["samples"]) == 3
    for row in data["samples"]:
        assert (tmp_path / row["files"]["txt"]).exists()
        assert (tmp_path / row["files"]["docx"]).exists()
        assert (tmp_path / row["files"]["pdf"]).exists()
        assert row["files"]["scan"] is None
        assert row["human_rank"] in {1, 2, 3}
