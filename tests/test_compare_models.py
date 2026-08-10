# tests/test_compare_models.py
import pytest

from scripts.compare_models import ComparisonResult, ProviderConfig, run_comparison, summarize


def test_summarize_picks_provider_with_all_schema_valid_and_lowest_latency():
    results = [
        ComparisonResult(
            provider_name="a", schema_valid=True, latency_ms=800, raw_output="{}", error=None
        ),
        ComparisonResult(
            provider_name="b", schema_valid=True, latency_ms=300, raw_output="{}", error=None
        ),
        ComparisonResult(
            provider_name="c", schema_valid=False, latency_ms=100, raw_output="bad", error="invalid json"
        ),
    ]

    summary = summarize(results)

    assert summary.recommended_provider == "b"
    assert summary.disqualified == ["c"]
    assert summary.skipped == []


def test_summarize_raises_when_no_provider_passes_schema():
    results = [
        ComparisonResult(
            provider_name="a", schema_valid=False, latency_ms=100, raw_output="x", error="bad"
        ),
    ]

    with pytest.raises(ValueError, match="没有供应商通过"):
        summarize(results)


def test_summarize_excludes_skipped_from_disqualified_and_does_not_raise():
    results = [
        ComparisonResult(
            provider_name="a", schema_valid=True, latency_ms=500, raw_output="{}", error=None
        ),
        ComparisonResult(
            provider_name="b",
            schema_valid=False,
            latency_ms=0,
            raw_output="",
            error="跳过：环境变量 ARK_API_KEY 未设置",
            skipped=True,
        ),
    ]

    summary = summarize(results)

    assert summary.recommended_provider == "a"
    assert summary.disqualified == []
    assert summary.skipped == ["b"]


def test_run_comparison_skips_provider_with_missing_api_key(monkeypatch):
    monkeypatch.delenv("NO_SUCH_TEST_API_KEY", raising=False)
    provider = ProviderConfig(
        name="doubao",
        api_key_env="NO_SUCH_TEST_API_KEY",
        base_url="https://example.invalid/v1",
        model="doubao-seed-2-1-turbo-241215",
        supports_json_schema=True,
    )

    results = run_comparison("sample requirement", [provider])

    assert len(results) == 1
    assert results[0].provider_name == "doubao"
    assert results[0].skipped is True
    assert results[0].schema_valid is False
    assert "NO_SUCH_TEST_API_KEY" in results[0].error
