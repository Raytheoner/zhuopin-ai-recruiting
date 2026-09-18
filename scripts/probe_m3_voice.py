"""
M3 语音链路技术探针（tasks.md 1.1–1.7，design.md OQ-5/X5）。
五项探针（P1 LiveKit／P2 FunASR／P3 CosyVoice／P4 追问选择 LLM TTFT／P5 livekit-agents
SDK 兼容性）各一个子命令，输出统一 JSON，结果幂等追加写 docs/m3-voice-probe.md。

用法：python -m scripts.probe_m3_voice <子命令> [选项]
子命令名单见 `python -m scripts.probe_m3_voice --help`（由已注册的探针动态生成）。
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DOC_PATH = Path("docs/m3-voice-probe.md")


@dataclass(frozen=True)
class ProbeResult:
    item: str
    env_fingerprint: str
    conclusion: str  # "通过" | "阻塞"
    metrics: dict[str, Any] = field(default_factory=dict)
    blocking_reason: str | None = None
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.conclusion not in ("通过", "阻塞"):
            raise ValueError(f"conclusion 只能是 '通过' 或 '阻塞'，收到: {self.conclusion!r}")
        if self.conclusion == "阻塞" and not self.blocking_reason:
            raise ValueError("结论为「阻塞」时必须给出 blocking_reason")


def env_fingerprint(*, target: str, extra: str | None = None) -> str:
    """环境指纹：平台 + Python 版本 + 目标机/开发机标签 (+ 可选附加标签，如网络环境)。
    与 `upsert_markdown_row` 的 (item, env_fingerprint) 复合键一起，决定同一条探针
    结果在不同环境下各占一行、同一环境下反复跑只覆盖最后一次。
    """
    fp = f"{platform.platform()}|py{sys.version.split()[0]}|{target}"
    if extra:
        fp = f"{fp}|{extra}"
    return fp


def render_json(result: ProbeResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True)


_TABLE_HEADER = "| 项 | 环境指纹 | 结论 | 关键指标 | 阻塞点 | 耗时 ms | 时间戳(UTC) |"
_TABLE_SEP = "|---|---|---|---|---|---|---|"


def _escape_cell(value: str) -> str:
    """Escape pipe characters in markdown table cells to preserve split("|") integrity.
    Uses HTML entity encoding (&#124;) so split("|") won't break on escaped pipes.
    """
    return value.replace("|", "&#124;")


def _unescape_cell(value: str) -> str:
    """Unescape pipe characters from markdown table cells."""
    return value.replace("&#124;", "|")


def _format_metrics(metrics: dict[str, Any]) -> str:
    formatted = "; ".join(f"{k}={v}" for k, v in sorted(metrics.items()))
    return _escape_cell(formatted)


def _format_row(result: ProbeResult) -> str:
    return (
        f"| {_escape_cell(result.item)} | {_escape_cell(result.env_fingerprint)} | {_escape_cell(result.conclusion)} | "
        f"{_format_metrics(result.metrics)} | {_escape_cell(result.blocking_reason or '')} | "
        f"{round(result.duration_ms)} | {_escape_cell(result.timestamp)} |"
    )


def upsert_markdown_row(doc_text: str, result: ProbeResult) -> str:
    """把 `result` 幂等写进 `doc_text` 里「## 探针结果」表格：同 (item, env_fingerprint)
    覆盖已有行，否则追加新行；表格不存在则先建。表格外的其余内容原样保留。
    """
    lines = doc_text.splitlines()
    new_row = _format_row(result)

    if _TABLE_HEADER not in lines:
        if lines and lines[-1] != "":
            lines.append("")
        lines += ["## 探针结果", "", _TABLE_HEADER, _TABLE_SEP, new_row, ""]
        return "\n".join(lines) + "\n"

    header_idx = lines.index(_TABLE_HEADER)
    row_start = header_idx + 2
    row_end = row_start
    while row_end < len(lines) and lines[row_end].startswith("|"):
        row_end += 1

    key = (result.item, result.env_fingerprint)
    for i in range(row_start, row_end):
        cells = [c.strip() for c in lines[i].split("|")]
        if len(cells) > 2 and (_unescape_cell(cells[1]), _unescape_cell(cells[2])) == key:
            lines[i] = new_row
            return "\n".join(lines) + "\n"

    lines.insert(row_end, new_row)
    return "\n".join(lines) + "\n"


def write_result(result: ProbeResult, *, doc_path: Path = DEFAULT_DOC_PATH) -> None:
    existing = (
        doc_path.read_text(encoding="utf-8")
        if doc_path.exists()
        else "# M3 语音探针结果（U0）\n"
    )
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(upsert_markdown_row(existing, result), encoding="utf-8")


PROBES: dict[str, Callable[[argparse.Namespace], ProbeResult]] = {}


def register(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], ProbeResult]) -> Callable:
        PROBES[name] = fn
        return fn

    return deco


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M3 语音链路技术探针")
    parser.add_argument("--json", type=Path, default=None, help="额外把结果写成 JSON 文件")
    parser.add_argument("--no-doc-write", action="store_true", help="跳过写 docs/m3-voice-probe.md（测试用）")
    parser.add_argument("--doc-path", type=Path, default=DEFAULT_DOC_PATH)
    subparsers = parser.add_subparsers(dest="probe", required=True)
    for name, fn in PROBES.items():
        sub = subparsers.add_parser(name)
        fn.__wrapped_add_arguments__(sub) if hasattr(fn, "__wrapped_add_arguments__") else None
        sub.set_defaults(_fn=fn)

    args = parser.parse_args(argv)
    result: ProbeResult = args._fn(args)
    print(render_json(result))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(render_json(result), encoding="utf-8")
    if not args.no_doc_write:
        write_result(result, doc_path=args.doc_path)
    return 0 if result.conclusion == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(main())
