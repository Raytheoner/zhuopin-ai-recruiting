import numpy as np

from scripts.bench_bge_m3 import MODEL_ID, bench, render_markdown
from scripts.compare_models_m2 import Sample

KEYS = ("C", "AUTOSAR", "MCAL", "Java")


def fake_embed(texts: list[str]) -> np.ndarray:
    return np.array([[float(t.count(k)) for k in KEYS] for t in texts])


def _samples():
    return [
        Sample("S1", "C AUTOSAR MCAL", {}, 1),
        Sample("S2", "C MCAL", {}, 2),
        Sample("S3", "Java Java", {}, 3),
        Sample("S4", "AUTOSAR", {}, 4),
    ]


def test_bench_reports_dim_timing_and_recall():
    result = bench(fake_embed, _samples(), "C AUTOSAR MCAL", top_k=2)
    assert MODEL_ID == "BAAI/bge-m3"
    assert result["n"] == 4 and result["dim"] == 4 and result["top_k"] == 2
    assert result["per_doc_ms"] >= 0 and result["query_ms"] >= 0
    assert [r[0] for r in result["recalled"]] == ["S1", "S2"]
    assert result["recall_at_k"] == 1.0  # k_truth=top_k=2 ⇒ 人工前 2 = {S1,S2}，全部被召回


def test_render_markdown_mentions_backend_and_recall():
    result = bench(fake_embed, _samples(), "C AUTOSAR MCAL", top_k=2)
    md = render_markdown({**result, "backend": "fake"})
    assert "fake" in md and "recall@2" in md
