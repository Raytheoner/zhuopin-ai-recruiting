"""`scripts/hooks/context-guard.py` 行为断言（Token 治理 Phase 6，0916I）。

它挂在每一条用户消息上：误报只是多一句提醒，漏报是回到老路；**报错绝不能挡住用户消息**。
钉死：阈值下不出声、过 150k/250k 各只提醒一次、子代理（sidechain）行不算、无 transcript／坏输入静默放行。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "context-guard.py"


def transcript(tmp_path, ctxs, sidechain_last=None):
    p = tmp_path / "t.jsonl"
    rows = [{"type": "user", "message": {"content": "hi"}}]
    for i, c in enumerate(ctxs):
        rows.append({"type": "assistant", "message": {"id": f"m{i}", "usage": {
            "input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": c - 10}}})
    if sidechain_last:
        rows.append({"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": sidechain_last}}})
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def run(tmp_path, path, sid="s1", extra_env=None):
    payload = {"session_id": sid, "transcript_path": str(path) if path else None, "prompt": "x"}
    env = {"CC_CONTEXT_GUARD_DIR": str(tmp_path), "PATH": "/usr/bin:/bin", **(extra_env or {})}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=20)


def test_quiet_below_threshold(tmp_path):
    r = run(tmp_path, transcript(tmp_path, [40_000, 149_000]))
    assert r.returncode == 0 and r.stdout == ""


def test_warns_once_per_band(tmp_path):
    t = transcript(tmp_path, [80_000, 160_000])
    r1 = run(tmp_path, t)
    assert r1.returncode == 0 and "160k" in r1.stdout and "新开 session" in r1.stdout
    assert run(tmp_path, t).stdout == ""                       # 同档不重复
    t2 = transcript(tmp_path, [160_000, 260_000])
    assert "260k" in run(tmp_path, t2).stdout                  # 进 250k 档再提醒一次
    assert run(tmp_path, t2).stdout == ""


def test_other_session_has_own_marks(tmp_path):
    t = transcript(tmp_path, [170_000])
    assert run(tmp_path, t, sid="a").stdout
    assert run(tmp_path, t, sid="b").stdout


def test_sidechain_rows_ignored(tmp_path):
    t = transcript(tmp_path, [60_000], sidechain_last=300_000)
    assert run(tmp_path, t).stdout == ""


def test_missing_or_bad_input_is_silent(tmp_path):
    assert run(tmp_path, None).stdout == ""
    assert run(tmp_path, tmp_path / "nope.jsonl").stdout == ""
    r = subprocess.run([sys.executable, str(HOOK)], input="garbage", capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and r.stdout == ""


def test_headless_lane_never_warns(tmp_path):
    t = transcript(tmp_path, [300_000])
    assert run(tmp_path, t, extra_env={"HR_HEADLESS_LANE": "1"}).stdout == ""


def test_warning_forbids_early_wrapup(tmp_path):
    out = run(tmp_path, transcript(tmp_path, [170_000])).stdout
    assert "不许因为上下文大而提前收尾" in out
