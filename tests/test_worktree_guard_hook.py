"""`scripts/hooks/worktree-guard.py` 行为断言（2026-09-16，Win 端 #596/#599 泳道跳过 worktree 直接改主工作区）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "worktree-guard.py"


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "repo"
    wt = main / ".claude" / "worktrees" / "wave2-x"
    wt.mkdir(parents=True)
    return main, wt


def run(tool, inp, cwd, main, wt, isolate=True):
    env = dict(os.environ)
    env.pop("HR_LANE_ISOLATE", None)
    if isolate:
        env.update(HR_LANE_ISOLATE="1", HR_LANE_MAIN=str(main), HR_LANE_WORKTREE=str(wt))
    payload = {"tool_name": tool, "tool_input": inp, "cwd": str(cwd)}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=20)


def test_edit_main_file_blocked(repo):
    main, wt = repo
    r = run("Edit", {"file_path": str(main / "tools" / "x.py")}, wt, main, wt)
    assert r.returncode == 2 and "worktree" in r.stderr


def test_write_relative_from_main_cwd_blocked(repo):
    main, wt = repo
    assert run("Write", {"file_path": "scripts/a.sh"}, main, main, wt).returncode == 2


@pytest.mark.parametrize("fp", ["tools/x.py", "/tmp/scratch.txt"])
def test_edit_inside_worktree_or_outside_repo_allowed(repo, fp):
    main, wt = repo
    path = fp if fp.startswith("/") else str(wt / fp)
    assert run("Edit", {"file_path": path}, wt, main, wt).returncode == 0


@pytest.mark.parametrize("cmd", [
    "git add tools/x.py",
    "git commit -m x",
    "cd {main} && git commit -am x",
    "git -C {main} add .",
    "git stash",
    "git checkout -- tools/x.py",
])
def test_git_writes_in_main_blocked(repo, cmd):
    main, wt = repo
    r = run("Bash", {"command": cmd.format(main=main)}, main, main, wt)
    assert r.returncode == 2, cmd


@pytest.mark.parametrize("cmd,cwd_is_main", [
    ("git add tools/x.py && git commit -m x", False),
    ("git -C {main} merge --ff-only wave2-x", False),
    ("git -C {main} push origin main", False),
    ("git status --short", True),
    ("git log --oneline -5", True),
    ("git -C {main} worktree remove {wt}", False),
    ("cd {wt} && git commit -m x", True),
    ("ls; cat README.md", True),
])
def test_allowed_bash(repo, cmd, cwd_is_main):
    main, wt = repo
    r = run("Bash", {"command": cmd.format(main=main, wt=wt)}, main if cwd_is_main else wt, main, wt)
    assert r.returncode == 0, (cmd, r.stderr)


def test_inactive_without_env(repo):
    main, wt = repo
    assert run("Edit", {"file_path": str(main / "tools" / "x.py")}, main, main, wt, isolate=False).returncode == 0


def test_garbage_input_fails_open():
    env = dict(os.environ, HR_LANE_ISOLATE="1", HR_LANE_MAIN="/x")
    r = subprocess.run([sys.executable, str(HOOK)], input="garbage", capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0
