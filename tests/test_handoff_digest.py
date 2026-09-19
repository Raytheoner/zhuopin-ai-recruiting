"""`.claude/handoff` 近期协议摘要（`[Mac]0919Q`），只读工具。

钉住两条：① 历史目录/文件量大时输出仍保持在合理体量（不逐个列出） ② `--full` 不带
`--confirm-large-output` 时拒绝执行，避免被无脑全量调用（本轮实测触发 63KB 硬截断的场景）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from scripts import handoff_digest as HD

OUTPUT_BYTES_CEILING = 3 * 1024


def _make_handoff(tmp_path: Path, *, n_commit: int = 300, n_launch: int = 300, n_lanes: int = 150, n_logs: int = 40) -> Path:
    handoff = tmp_path / ".claude" / "handoff"
    for sub, n, suffixes in (
        ("commit", n_commit, (".request", ".done", ".rejected", ".deferred")),
        ("launch", n_launch, (".request", ".started", ".consumed")),
        ("events", 5, ("",)),
    ):
        d = handoff / sub
        d.mkdir(parents=True)
        for i in range(n):
            suf = suffixes[i % len(suffixes)]
            p = d / f"20260919-{i:06d}{suf}"
            p.write_text("x")
            os.utime(p, (time.time() - i, time.time() - i))
    for i in range(n_lanes):
        (handoff / f"lanes-2026091{i % 9}-{i:06d}").mkdir(parents=True)
    for i in range(n_logs):
        (handoff / f"old-{i}.log").write_text("log")
    return handoff


def test_recent_only_output_stays_small_with_many_historical_entries(tmp_path):
    handoff = _make_handoff(tmp_path)
    out = HD.render(handoff, n=10)
    assert len(out.encode("utf-8")) < OUTPUT_BYTES_CEILING
    assert "共 300 条" in out
    assert "lanes-* 目录 150 个" in out
    assert ".log 文件 40 个" in out
    # 不逐个列出历史 lanes-* 目录名
    assert out.count("lanes-2026091") <= 1


def test_recent_shows_only_n_most_recent_with_status(tmp_path):
    d = tmp_path / "commit"
    d.mkdir(parents=True)
    for i, suf in enumerate([".done", ".rejected", ".deferred", ".request"]):
        p = d / f"20260919-00000{i}{suf}"
        p.write_text("x")
        os.utime(p, (1000 + i, 1000 + i))
    out = HD.render(tmp_path, n=2)
    lines = [l for l in out.splitlines() if l.startswith("- ")]
    assert len(lines) == 2
    assert any(l.endswith("待处理") for l in lines)  # 最近两条里含 .request


def test_full_without_confirm_is_rejected(tmp_path, capsys):
    handoff = _make_handoff(tmp_path, n_commit=5, n_launch=5, n_lanes=2, n_logs=1)
    rc = HD.main(["--repo", str(tmp_path), "--full"])
    assert rc != 0
    captured = capsys.readouterr()
    assert "--confirm-large-output" in captured.err


def test_full_with_confirm_runs_and_lists_all(tmp_path, capsys):
    handoff = _make_handoff(tmp_path, n_commit=3, n_launch=3, n_lanes=2, n_logs=1)
    rc = HD.main(["--repo", str(tmp_path), "--full", "--confirm-large-output"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "全量" in out
    assert out.count("lanes-2026091") >= 2
