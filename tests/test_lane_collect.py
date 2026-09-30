"""0930D：泳道产物执行器代收口（`scripts/lane_collect.py`）行为测试。

真源是脚本的 CLI 契约：stage1 在泳道 worktree 里代提交并记账；stage2 只合并
「status=OK 且机器判据 PASS／无判据块」的泳道，冲突 abort 并点名，其余只留分支。
用临时 git 仓库＋真实 worktree 复现，⛔ 不碰本仓库。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "lane_collect.py"
RUN_LANES = REPO_ROOT / "docs" / "openers" / "run-lanes.sh"

ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=ENV
    )


def run_collect(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=ENV
    )


@pytest.fixture()
def sb(tmp_path: Path) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, env=ENV)
    git("remote", "add", "origin", str(bare), cwd=repo)
    git("push", "-u", "origin", "main", cwd=repo)
    return {"repo": repo, "bare": bare, "root": tmp_path}


def make_lane(sb: dict, name: str, content: str) -> Path:
    wt = sb["root"] / name
    git("worktree", "add", str(wt), "-b", name, "main", cwd=sb["repo"])
    (wt / "a.txt").write_text(content, encoding="utf-8")
    return wt


def new_logdir(sb: dict) -> Path:
    d = sb["root"] / "lanes-test"
    d.mkdir(exist_ok=True)
    return d


def stage1_args(sb: dict, logdir: Path, lane: str, id_: str, status: str, wt: Path, gate: str = "") -> list[str]:
    if gate:
        (logdir / "gates.tsv").write_text(f"{id_}\t{gate}\n", encoding="utf-8")
    return [
        "stage1", "--repo", str(sb["repo"]), "--logdir", str(logdir),
        "--lane", lane, "--id", id_, "--status", status,
        "--worktree", str(wt), "--branch", lane, "--engine", "codex",
    ]


def test_stage1_代提交并记账(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt = make_lane(sb, "lane-ok", "one\ntwo\n")
    r = run_collect(*stage1_args(sb, logdir, "lane-ok", "0930T", "OK", wt))
    assert r.returncode == 0, r.stderr
    assert git("status", "--porcelain", cwd=wt).stdout.strip() == ""
    subject = git("log", "-1", "--format=%s", cwd=wt).stdout
    assert "执行器代提交" in subject
    rows = (logdir / "collect.tsv").read_text(encoding="utf-8").strip().split("\n")
    assert len(rows) == 1 and rows[0].split("\t")[4] == "OK"
    # 主工作区不受影响
    assert (sb["repo"] / "a.txt").read_text(encoding="utf-8") == "one\n"


def test_stage1_干净树与非codex不代劳(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt = sb["root"] / "lane-clean"
    git("worktree", "add", str(wt), "-b", "lane-clean", "main", cwd=sb["repo"])
    run_collect(*stage1_args(sb, logdir, "lane-clean", "0930T", "OK", wt))
    assert not (logdir / "collect.tsv").exists()
    (wt / "a.txt").write_text("one\nx\n", encoding="utf-8")
    r = run_collect(
        "stage1", "--repo", str(sb["repo"]), "--logdir", str(logdir), "--lane", "lane-clean",
        "--id", "0930T", "--status", "OK", "--worktree", str(wt), "--branch", "lane-clean",
        "--engine", "claude",
    )
    assert r.returncode == 0
    assert not (logdir / "collect.tsv").exists()
    assert (wt / "a.txt").read_text(encoding="utf-8") == "one\nx\n"


def test_stage2_只合OK并推送(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt_ok = make_lane(sb, "lane-ok", "one\nok\n")
    wt_partial = make_lane(sb, "lane-partial", "one\npartial\n")
    run_collect(*stage1_args(sb, logdir, "lane-ok", "0930O", "OK", wt_ok, gate="PASS"))
    run_collect(*stage1_args(sb, logdir, "lane-partial", "0930P", "PARTIAL", wt_partial))
    r = run_collect("stage2", "--repo", str(sb["repo"]), "--logdir", str(logdir))
    assert r.returncode == 0, r.stderr
    assert "ok" in (sb["repo"] / "a.txt").read_text(encoding="utf-8")
    origin_show = git("--git-dir", str(sb["bare"]), "show", "main:a.txt", cwd=sb["root"]).stdout
    assert "ok" in origin_show
    results = (logdir / "collect.tsv").read_text(encoding="utf-8")
    assert "MERGED" in results and "BRANCH-ONLY" in results
    assert "partial" not in (sb["repo"] / "a.txt").read_text(encoding="utf-8")


def test_stage2_冲突abort并点名(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt = make_lane(sb, "lane-c1", "from-lane\n")
    run_collect(*stage1_args(sb, logdir, "lane-c1", "0930C1", "OK", wt, gate="PASS"))
    (sb["repo"] / "a.txt").write_text("from-main\n", encoding="utf-8")
    git("add", "a.txt", cwd=sb["repo"])
    git("commit", "-m", "main-side", cwd=sb["repo"])
    r = run_collect("stage2", "--repo", str(sb["repo"]), "--logdir", str(logdir), "--no-push")
    assert r.returncode == 0, r.stderr
    assert (sb["repo"] / "a.txt").read_text(encoding="utf-8") == "from-main\n"
    assert git("status", "--porcelain", cwd=sb["repo"]).stdout.strip() == ""
    assert "NEEDS-MANUAL-CONFLICT" in (logdir / "collect.tsv").read_text(encoding="utf-8")


def test_执行器接线存在() -> None:
    text = RUN_LANES.read_text(encoding="utf-8")
    assert "lane_collect.py\" stage1" in text
    assert "lane_collect.py\" stage2" in text
