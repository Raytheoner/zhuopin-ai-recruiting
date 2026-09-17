"""
U0 依赖可装性／体积探针（tasks 1.1）。在 Mac 与 `.51` 同款 Windows venv 各跑一次，
把输出贴进 docs/m2-model-comparison.md「环境」节。只 import、不下载模型、不调用。

用法：python -m scripts.smoke_m2_deps --json data/eval/m2-pilot/smoke-<平台>.json
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import json
import platform
import sys
import time
from pathlib import Path

DEPS: list[tuple[str, str]] = [
    ("numpy", "numpy"),
    ("pypdf", "pypdf"),
    ("python-docx", "docx"),
    ("reportlab", "reportlab"),
    ("pillow", "PIL"),
    ("pymupdf", "pymupdf"),
    ("paddlepaddle", "paddle"),
    ("paddleocr", "paddleocr"),
    ("torch", "torch"),
    ("FlagEmbedding", "FlagEmbedding"),
]


def _dist_size_mb(dist_name: str) -> float | None:
    try:
        files = md.files(dist_name)
    except md.PackageNotFoundError:
        return None
    if not files:
        return None
    total = 0
    for f in files:
        try:
            total += f.locate().stat().st_size
        except OSError:
            continue
    return round(total / 1e6, 1)


def _version(dist_name: str, module: object) -> str | None:
    try:
        return md.version(dist_name)
    except md.PackageNotFoundError:
        return getattr(module, "__version__", None)


def probe(dist_name: str, module_name: str) -> dict:
    started = time.monotonic()
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # ImportError、DLL 加载错、初始化异常都要记下来
        return {"dist": dist_name, "module": module_name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "dist": dist_name,
        "module": module_name,
        "ok": True,
        "version": _version(dist_name, module),
        "size_mb": _dist_size_mb(dist_name),
        "import_ms": round((time.monotonic() - started) * 1000),
    }


def probe_all(deps: list[tuple[str, str]]) -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "results": [probe(d, m) for d, m in deps],
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"平台：`{report['platform']}` ｜ Python {report['python']}",
        "",
        "| 包 | 可导入 | 版本 | 体积 MB | import 耗时 ms | 错误 |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        if r["ok"]:
            lines.append(f"| {r['dist']} | ✅ | {r['version']} | {r['size_mb']} | {r['import_ms']} | |")
        else:
            lines.append(f"| {r['dist']} | ❌ | | | | {r['error']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="U0 依赖探针")
    parser.add_argument("--json", type=Path, default=None, help="同时把结果写成 JSON")
    args = parser.parse_args(argv)
    report = probe_all(DEPS)
    print(render_markdown(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
