# tests/test_compare_models.py
from scripts.compare_models import ComparisonResult, ProviderConfig, summarize


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


def test_summarize_raises_when_no_provider_passes_schema():
    results = [
        ComparisonResult(
            provider_name="a", schema_valid=False, latency_ms=100, raw_output="x", error="bad"
        ),
    ]

    import pytest

    with pytest.raises(ValueError, match="没有供应商通过"):
        summarize(results)
