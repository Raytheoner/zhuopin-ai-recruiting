"""`compute_lane_digest`：run-lanes.sh 收敛结果 → 私信本人的摘要（纯函数）。"""

from __future__ import annotations

import ast
import inspect

from tools.liaison.lane_digest import DIGEST_MAX_CHARS, compute_lane_digest

RESULTS = (
    "G3\t0917Y\tOK\t12\t/Users/x/.claude/handoff/lanes-20260917-101500/0917Y.log\tsonnet\n"
    "G3\t0917Z\tPARTIAL\t7\t/Users/x/.claude/handoff/lanes-20260917-101500/0917Z.log\topus\n"
    "G1\t0917V\tFAIL(1)\t3\t/Users/x/.claude/handoff/lanes-20260917-101500/0917V.log\tsonnet\n"
    "G2\t0917W\tNO-SENTINEL\t40\t/Users/x/.claude/handoff/lanes-20260917-101500/0917W.log\tsonnet\n"
)
SUMMARY = (
    "泳道         编号           状态           分钟\n"
    "G1           0917V          FAIL(1)        3\n"
)


def test_digest_lists_every_lane_with_id_status_and_minutes():
    text = compute_lane_digest(RESULTS, SUMMARY, batch_label="lanes-20260917-101500")
    assert "lanes-20260917-101500" in text
    for needle in ("0917Y", "OK", "12", "0917Z", "PARTIAL", "7", "0917V", "FAIL(1)", "0917W"):
        assert needle in text, needle


def test_digest_calls_out_partial_and_failed_ids_separately():
    text = compute_lane_digest(RESULTS, SUMMARY)
    partial_line = next(line for line in text.splitlines() if line.startswith("⏸"))
    fail_line = next(line for line in text.splitlines() if line.startswith("✗"))
    assert "0917Z" in partial_line and "0917Y" not in partial_line
    assert "0917V" in fail_line and "0917W" in fail_line and "0917Y" not in fail_line


def test_digest_carries_no_log_paths_or_log_text():
    text = compute_lane_digest(RESULTS, SUMMARY)
    assert ".log" not in text and "/Users/" not in text


def test_digest_all_ok_says_so():
    text = compute_lane_digest("G3\t0917Y\tOK\t12\t/l\tsonnet\n", "")
    assert "无留步" in text or "全部 OK" in text
    assert "⏸" not in text and "✗" not in text


def test_digest_is_bounded_even_for_huge_batches():
    rows = "".join(f"G{i % 5}\t0917{i:03d}\tFAIL(1)\t{i}\t/l/{i}.log\tsonnet\n" for i in range(400))
    text = compute_lane_digest(rows, "")
    assert len(text) <= DIGEST_MAX_CHARS
    assert "…" in text


def test_digest_handles_empty_or_malformed_input_without_raising():
    assert "0 条" in compute_lane_digest("", "")
    text = compute_lane_digest("garbage-without-tabs\n\n", "")
    assert "0 条" in text


def test_compute_lane_digest_is_pure():
    tree = ast.parse(inspect.getsource(compute_lane_digest))
    calls = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "?"))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not calls & {"open", "read_text", "now", "today", "getenv", "print"}
