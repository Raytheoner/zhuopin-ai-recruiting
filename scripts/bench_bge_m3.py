"""
BGE-M3 本地 CPU 召回实测（tasks 1.5；design D7 已裁决本地 CPU，⛔ 不测托管 API）。

模型权重约 2.2 GB，首次运行会下载；境内机器先 `export HF_ENDPOINT=https://hf-mirror.com`。
单测只测 bench() 的纯计算部分（注入假 embedder），⛔ 不加载模型。

用法：python -m scripts.bench_bge_m3 --samples data/eval/m2-pilot --backend flag --top-k 10 --json data/eval/m2-pilot/bench-bge-m3.json
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.eval.recall import cosine_top_k, recall_at_k
from scripts.compare_models_m2 import Sample, load_samples

MODEL_ID = "BAAI/bge-m3"


def load_embedder(backend: str) -> Callable[[list[str]], np.ndarray]:
    if backend == "flag":
        from FlagEmbedding import BGEM3FlagModel

        try:
            model = BGEM3FlagModel(MODEL_ID, use_fp16=False, devices=["cpu"])
        except TypeError:  # FlagEmbedding < 1.3 的参数名是 device
            model = BGEM3FlagModel(MODEL_ID, use_fp16=False, device="cpu")

        def embed(texts: list[str]) -> np.ndarray:
            return np.asarray(model.encode(texts, batch_size=4, max_length=4096)["dense_vecs"], dtype=np.float32)

        return embed
    if backend == "st":
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL_ID, device="cpu")
        return lambda texts: np.asarray(model.encode(texts, normalize_embeddings=True, batch_size=4), dtype=np.float32)
    raise ValueError(f"未知 backend: {backend}（可选 flag / st）")


def bench(embed: Callable[[list[str]], np.ndarray], samples: list[Sample], rubric_text: str, *, top_k: int = 10) -> dict:
    started = time.perf_counter()
    query = embed([rubric_text])[0]
    query_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    docs = embed([s.text for s in samples])
    docs_ms = (time.perf_counter() - started) * 1000
    ids = [s.sample_id for s in samples]
    recalled = cosine_top_k(query, docs, ids, top_k)
    human_order = [s.sample_id for s in sorted(samples, key=lambda s: s.human_rank)]
    return {
        "model": MODEL_ID,
        "platform": platform.platform(),
        "n": len(samples),
        "dim": int(docs.shape[1]) if len(samples) else 0,
        "query_ms": round(query_ms, 1),
        "docs_total_ms": round(docs_ms, 1),
        "per_doc_ms": round(docs_ms / len(samples), 1) if samples else 0.0,
        "top_k": top_k,
        "recall_at_k": recall_at_k([sid for sid, _ in recalled], human_order, k_truth=top_k),
        "recalled": [(sid, round(score, 4)) for sid, score in recalled],
    }


def render_markdown(result: dict) -> str:
    return "\n".join(
        [
            f"模型：`{result['model']}`（backend={result.get('backend', '—')}）｜ 平台：`{result['platform']}`",
            f"样本 {result['n']} 份 ｜ 维度 {result['dim']} ｜ 画像向量 {result['query_ms']} ms ｜ 单份简历 {result['per_doc_ms']} ms（合计 {result['docs_total_ms']} ms）",
            f"recall@{result['top_k']}（以人工排序前 {result['top_k']} 为真值）＝ {result['recall_at_k'] * 100:.1f}%",
            "",
            "| 名次 | 样本 | cosine |",
            "|---|---|---|",
        ]
        + [f"| {i + 1} | {sid} | {score} |" for i, (sid, score) in enumerate(result["recalled"])]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BGE-M3 本地 CPU 召回 bench")
    parser.add_argument("--samples", type=Path, default=Path("data/eval/m2-pilot"))
    parser.add_argument("--backend", choices=["flag", "st"], default="flag")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    samples, rubric = load_samples(args.samples)
    result = {**bench(load_embedder(args.backend), samples, rubric["profile_text"], top_k=args.top_k), "backend": args.backend}
    print(render_markdown(result))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
