import numpy as np

from app.eval.recall import cosine_top_k, recall_at_k


def test_cosine_top_k_orders_by_similarity():
    query = np.array([1.0, 0.0])
    docs = np.array([[0.0, 1.0], [1.0, 0.0], [0.7, 0.7]])
    top = cosine_top_k(query, docs, ["a", "b", "c"], k=2)
    assert [t[0] for t in top] == ["b", "c"]
    assert abs(top[0][1] - 1.0) < 1e-9


def test_cosine_top_k_handles_k_larger_than_docs_and_zero_vectors():
    query = np.array([1.0, 0.0])
    docs = np.array([[0.0, 0.0], [1.0, 0.0]])
    top = cosine_top_k(query, docs, ["zero", "b"], k=5)
    assert [t[0] for t in top] == ["b", "zero"]
    assert top[1][1] == 0.0


def test_recall_at_k_against_human_top():
    human = [f"h{i}" for i in range(12)]
    recalled = ["h0", "h1", "h2", "h3", "h4", "z"]
    assert recall_at_k(recalled, human) == 0.5  # 人工前 10 中 5 个被召回
    assert recall_at_k([], human) == 0.0
