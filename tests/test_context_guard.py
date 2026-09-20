"""`scripts/hooks/context-guard.py` 的 `PostToolUse` 挂点（上下文硬闸，0920H）。

0920G 只把无头泳道的上下文峰值记进 results.tsv；触发与消费两头都没有——`UserPromptSubmit`
在无头泳道一辈子只有开场那一条（彼时上下文≈0），运行中永不触发。0920H 给同一份脚本加
`PostToolUse` 挂点：**只对无头泳道**（`HR_HEADLESS_LANE` 非空）生效，越过 `HR_LANE_CONTEXT_LIMIT`
（默认 150k）就注入「做完手上这个里程碑并提交，然后打 `OPENER_PARTIAL: 上下文转场 | 续棒: …`」，
续棒由 run-lanes.sh 自动排。

有人值守路径的旧行为由 `tests/test_context_guard_hook.py` 钉着，一字不动；这里钉的是：
  ① `UserPromptSubmit` ＋ 无头 ⇒ 零输出（0916K 的旧豁免不变）
  ② `PostToolUse` ＋ 有人值守（HR_HEADLESS_LANE 为空）⇒ 零输出（⛔ 不许每次工具调用后都被注入）
  ③ `PostToolUse` ＋ 无头 ＋ ctx=149_999 ⇒ 零输出；ctx=150_000 ⇒ 注入且含 `OPENER_PARTIAL: 上下文转场`
  ④ 同一 session 第二次越线 ⇒ 不重复注入
全部用假 transcript（jsonl 临时文件）＋ 假 stdin JSON，⛔ 不起任何真会话。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "context-guard.py"


def transcript(tmp_path, ctxs):
    p = tmp_path / "t.jsonl"
    rows = [{"type": "user", "message": {"content": "hi"}}]
    for i, c in enumerate(ctxs):
        rows.append({"type": "assistant", "message": {"id": f"m{i}", "usage": {
            "input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": c - 10}}})
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def run(tmp_path, path, event, sid="s1", headless=True, extra_env=None):
    payload = {"hook_event_name": event, "session_id": sid, "transcript_path": str(path),
               "tool_name": "Bash", "tool_input": {}, "tool_response": {}}
    env = {"CC_CONTEXT_GUARD_DIR": str(tmp_path), "PATH": "/usr/bin:/bin", **(extra_env or {})}
    if headless:
        env["HR_HEADLESS_LANE"] = "1"
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=20)


def injected_text(r):
    """PostToolUse 的纯 stdout 不进模型；注入必须走 hookSpecificOutput.additionalContext。"""
    assert r.returncode == 0
    out = json.loads(r.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    return hso["additionalContext"]


def test_user_prompt_submit_headless_still_exempt(tmp_path):
    r = run(tmp_path, transcript(tmp_path, [300_000]), "UserPromptSubmit")
    assert r.returncode == 0 and r.stdout == ""


def test_post_tool_use_attended_session_is_silent(tmp_path):
    r = run(tmp_path, transcript(tmp_path, [300_000]), "PostToolUse", headless=False)
    assert r.returncode == 0 and r.stdout == ""


def test_post_tool_use_headless_threshold_edge(tmp_path):
    assert run(tmp_path, transcript(tmp_path, [149_999]), "PostToolUse").stdout == ""
    r = run(tmp_path, transcript(tmp_path, [150_000]), "PostToolUse")
    text = injected_text(r)
    assert "OPENER_PARTIAL: 上下文转场" in text
    assert "150k" in text and "待续" in text and "不要开始新的里程碑" in text


def test_post_tool_use_limit_from_env(tmp_path):
    env = {"HR_LANE_CONTEXT_LIMIT": "100000"}
    assert run(tmp_path, transcript(tmp_path, [99_999]), "PostToolUse", extra_env=env).stdout == ""
    assert "上下文转场" in injected_text(run(tmp_path, transcript(tmp_path, [100_000]), "PostToolUse", extra_env=env))


def test_post_tool_use_injects_once_per_session(tmp_path):
    t = transcript(tmp_path, [160_000])
    assert injected_text(run(tmp_path, t, "PostToolUse"))
    assert run(tmp_path, t, "PostToolUse").stdout == ""                       # 第二次越线不重复
    assert run(tmp_path, transcript(tmp_path, [260_000]), "PostToolUse").stdout == ""  # 更高也不再注入
    assert injected_text(run(tmp_path, t, "PostToolUse", sid="other"))         # 别的 session 各管各的


def test_post_tool_use_bad_input_is_silent(tmp_path):
    r = subprocess.run([sys.executable, str(HOOK)], input='{"hook_event_name":"PostToolUse"}',
                       capture_output=True, text=True, timeout=20,
                       env={"HR_HEADLESS_LANE": "1", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout == ""
