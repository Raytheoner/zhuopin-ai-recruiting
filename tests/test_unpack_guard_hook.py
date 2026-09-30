"""0930A：拆件受限会话的 Codex PreToolUse 路径级 deny hook 契约测试。

真源是 `scripts/hooks/codex_unpack_guard.py`，本文件以子进程喂 stdin 事件、断言
stdout 拦截 JSON 的方式核它，⛔ 不 import 该脚本正文（它是可执行入口，行为靠
stdin/stdout 契约，不是可复用库）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "codex_unpack_guard.py"


def _run_hook(event: dict, *, unpack: bool) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if unpack:
        env["HR_LIAISON_UNPACK"] = "1"
    else:
        env.pop("HR_LIAISON_UNPACK", None)
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=env,
    )


def test_白名单文件放行():
    r = _run_hook(
        {"tool_name": "Edit", "tool_input": {"file_path": "docs/跟进信/回件/x.md"}},
        unpack=True,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_越界文件deny():
    r = _run_hook(
        {"tool_name": "Edit", "tool_input": {"file_path": "app/main.py"}},
        unpack=True,
    )
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "app/main.py" in data["hookSpecificOutput"]["permissionDecisionReason"]


def test_未置位放行():
    r = _run_hook(
        {"tool_name": "Edit", "tool_input": {"file_path": "app/main.py"}},
        unpack=False,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""


@pytest.mark.parametrize(
    "command",
    ["git add -A", "git add .", "git commit -a", "git stash", "git push origin main"],
)
def test_整体性git命令deny(command):
    r = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": command}},
        unpack=True,
    )
    assert r.returncode == 0
    assert '"deny"' in r.stdout


def test_apply_patch解析越界路径deny():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: docs/跟进信/回件/x.md\n"
        "+hello\n"
        "*** Update File: tools/x.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "*** End Patch\n"
    )
    r = _run_hook(
        {"tool_name": "apply_patch", "tool_input": {"command": patch}},
        unpack=True,
    )
    assert r.returncode == 0
    assert '"deny"' in r.stdout
    assert "tools/x.py" in r.stdout


def test_apply_patch全白名单放行():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: docs/跟进信/回件/x.md\n"
        "+hello\n"
        "*** Update File: docs/跟进信/README-跟进信清单.md\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "*** End Patch\n"
    )
    r = _run_hook(
        {"tool_name": "apply_patch", "tool_input": {"command": patch}},
        unpack=True,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""
