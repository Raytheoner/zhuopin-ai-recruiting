"""M3 U4 延迟报表（voice-structured-interview tasks.md 5.10）。

按场次集合统计 interview_turn.latency_json 里各分段延迟与端到端延迟的
中位数/P95，写入 docs/m3-voice-probe.md「内部模拟批次」节（design.md
「验收门槛：端到端延迟中位 < 800ms」的度量来源）。

latency_json 的字段约定（voice_host/worker.py 写入，本计划「设计决策 6」）：
{"endpoint_detection_ms", "asr_ms", "follow_up_selection_ms"(仅追问轮),
 "tts_first_frame_ms", "end_to_end_ms"}。缺失某个分段键的 turn（如文本作答
turn）在该分段的统计里被跳过，不计入分母。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

SEGMENT_KEYS = (
    "endpoint_detection_ms", "asr_ms", "follow_up_selection_ms",
    "tts_first_frame_ms", "end_to_end_ms",
)

MARKER_START = "<!-- m3-latency-batch:{label} -->"
MARKER_END = "<!-- /m3-latency-batch:{label} -->"


def collect_latencies(conn, *, session_ids: list[str]) -> dict[str, list[float]]:
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"SELECT latency_json FROM interview_turn "
        f"WHERE session_id IN ({placeholders}) AND latency_json IS NOT NULL",
        session_ids,
    ).fetchall()
    values: dict[str, list[float]] = {key: [] for key in SEGMENT_KEYS}
    for (raw,) in rows:
        parsed = json.loads(raw)
        for key in SEGMENT_KEYS:
            if key in parsed and parsed[key] is not None:
                values[key].append(float(parsed[key]))
    return values


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[index]


def summarize(values: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for key, series in values.items():
        if not series:
            summary[key] = {"median": 0.0, "p95": 0.0, "n": 0}
            continue
        ordered = sorted(series)
        summary[key] = {
            "median": statistics.median(ordered), "p95": _percentile(ordered, 0.95), "n": len(ordered),
        }
    return summary


def render_section(label: str, summary: dict[str, dict[str, float]]) -> str:
    lines = [
        MARKER_START.format(label=label), f"### {label}", "",
        "| 分段 | 中位 ms | P95 ms | 样本数 |", "|---|---|---|---|",
    ]
    for key in SEGMENT_KEYS:
        s = summary.get(key, {"median": 0.0, "p95": 0.0, "n": 0})
        lines.append(f"| {key} | {s['median']:.1f} | {s['p95']:.1f} | {s['n']} |")
    lines.append(MARKER_END.format(label=label))
    return "\n".join(lines)


def upsert_batch_section(md_path: Path, label: str, summary: dict[str, dict[str, float]]) -> None:
    text = md_path.read_text(encoding="utf-8") if md_path.exists() else "# M3 语音探针结果（U0）\n"
    block = render_section(label, summary)
    start_marker = MARKER_START.format(label=label)
    end_marker = MARKER_END.format(label=label)
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)

    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        if "## 内部模拟批次" not in text:
            text = text.rstrip("\n") + "\n\n## 内部模拟批次\n\n"
        text = text.rstrip("\n") + "\n\n" + block + "\n"

    md_path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-ids", nargs="+", required=True)
    parser.add_argument("--batch-label", required=True)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--md-path", default="docs/m3-voice-probe.md")
    args = parser.parse_args()

    from app.config import get_settings
    from app.storage.db import get_connection, init_schema

    db_path = args.db_path or get_settings().db_path
    conn = get_connection(db_path)
    init_schema(conn)

    values = collect_latencies(conn, session_ids=args.session_ids)
    summary = summarize(values)
    upsert_batch_section(Path(args.md_path), args.batch_label, summary)
    print(f"批次 {args.batch_label} 已写入 {args.md_path}：{summary}")


if __name__ == "__main__":
    main()
