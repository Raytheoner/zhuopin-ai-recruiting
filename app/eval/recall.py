"""
召回段的进程内向量比对（design D7「向量存储」：SQLite BLOB + numpy 全量 cosine，⛔ 不引入 pgvector/FAISS）。
纯函数；embedding 的产生在调用方。
"""
from __future__ import annotations

import numpy as np


def cosine_top_k(query: np.ndarray, docs: np.ndarray, ids: list[str], k: int) -> list[tuple[str, float]]:
    if len(ids) == 0:
        return []
    q = np.asarray(query, dtype=np.float64)
    d = np.asarray(docs, dtype=np.float64)
    if q.ndim != 1:
        raise ValueError(f"query 期望 1D 数组，收到 shape {q.shape}")
    if d.ndim != 2:
        raise ValueError(f"docs 期望 2D 数组，收到 shape {d.shape}")
    if d.shape[1] != q.shape[0]:
        raise ValueError(f"docs 嵌入维度 {d.shape[1]} 与 query {q.shape[0]} 不匹配")
    if d.shape[0] != len(ids):
        raise ValueError(f"docs 行数 {d.shape[0]} 与 ids 长度 {len(ids)} 不匹配")
    qn = np.linalg.norm(q)
    dn = np.linalg.norm(d, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = np.where((dn > 0) & (qn > 0), d @ q / (dn * qn), 0.0)
    order = np.argsort(-sims, kind="stable")[: max(k, 0)]
    return [(ids[i], float(sims[i])) for i in order]


def recall_at_k(recalled_ids: list[str], human_order: list[str], *, k_truth: int = 10) -> float:
    truth = set(human_order[:k_truth])
    if not truth:
        return 0.0
    return len(truth & set(recalled_ids)) / len(truth)
