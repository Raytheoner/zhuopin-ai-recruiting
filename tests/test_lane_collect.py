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
REAL_LOGDIR = (
    Path("/Users/paulshao/Projects/HumanResource/.claude/handoff/lanes-20260930-072754")
)

sys.path.insert(0, str(REPO_ROOT))
from scripts.lane_collect import is_sandbox_git_only_partial  # noqa: E402

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


def collect_rows(logdir: Path) -> list[list[str]]:
    return [l.split("\t") for l in (logdir / "collect.tsv").read_text(encoding="utf-8").splitlines()]


def write_lane_log(logdir: Path, lane: str, id_: str, text: str) -> Path:
    p = logdir / f"{lane}-{id_}.log"
    p.write_text(text, encoding="utf-8")
    return p


def write_opener(wt: Path, id_: str, gate_block: str | None) -> None:
    d = wt / "docs" / "openers"
    d.mkdir(parents=True, exist_ok=True)
    text = f"# {id_}\n\n## 一、正文\n\n略。\n\n"
    if gate_block is not None:
        text += "## 机器判据\n\n```bash\n" + gate_block + "\n```\n"
    (d / f"{id_}-测试.md").write_text(text, encoding="utf-8")


SANDBOX_PARTIAL = (
    "OPENER_PARTIAL: Seatbelt 沙箱把 worktree 的 git 元数据判只读，"
    "git add/commit 无法执行，未能提交合回 main\n\n"
    "## ⏸ 留步\n\n- git 提交被沙箱只读拦（Operation not permitted）\n"
)


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


def test_真实0930abc三条日志全判环境级() -> None:
    logs = sorted(REAL_LOGDIR.glob("*.log"))
    assert len(logs) == 3
    hits = [
        p.name
        for p in logs
        if is_sandbox_git_only_partial(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert len(hits) == 3, hits


def test_业务留步判非环境级() -> None:
    assert not is_sandbox_git_only_partial("OPENER_PARTIAL: 等 .51 低峰窗口再发版")
    assert not is_sandbox_git_only_partial(
        "OPENER_PARTIAL: 沙箱挡 git\n\n## ⏸ 留步\n\n- 等汤丽萍回件\n"
    )
    assert not is_sandbox_git_only_partial("")  # 无哨兵


def test_stage1_环境级PARTIAL写env_only为1(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt = make_lane(sb, "lane-env", "one\nenv\n")
    write_lane_log(logdir, "lane-env", "0930F", SANDBOX_PARTIAL)
    run_collect(*stage1_args(sb, logdir, "lane-env", "0930F", "PARTIAL", wt))
    rows = collect_rows(logdir)
    assert len(rows) == 1
    row = rows[0]
    assert row[4] == "PARTIAL"
    assert row[7] == "1"
    assert row[6] == str(wt)


def test_stage1_业务PARTIAL写env_only为0(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt = make_lane(sb, "lane-biz", "one\nbiz\n")
    write_lane_log(logdir, "lane-biz", "0930G", "OPENER_PARTIAL: 等 .51 低峰窗口再发版")
    run_collect(*stage1_args(sb, logdir, "lane-biz", "0930G", "PARTIAL", wt))
    rows = collect_rows(logdir)
    assert rows[0][7] == "0"


def test_stage2_环境级PARTIAL判据PASS或无判据则合并(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt = make_lane(sb, "lane-env-pass", "one\nenvpass\n")
    write_lane_log(logdir, "lane-env-pass", "0930F", SANDBOX_PARTIAL)
    write_opener(wt, "0930F", "true\n")
    run_collect(*stage1_args(sb, logdir, "lane-env-pass", "0930F", "PARTIAL", wt))
    r = run_collect("stage2", "--repo", str(sb["repo"]), "--logdir", str(logdir), "--no-push")
    assert r.returncode == 0, r.stderr
    assert "envpass" in (sb["repo"] / "a.txt").read_text(encoding="utf-8")
    results = (logdir / "collect.tsv").read_text(encoding="utf-8")
    assert "MERGED-ENV" in results
    assert "自动合并（环境级 PARTIAL）" in r.stdout

    # 无判据块同样合并
    logdir2 = new_logdir(sb)
    wt2 = make_lane(sb, "lane-env-nogate", "one\nenvnogate\n")
    write_lane_log(logdir2, "lane-env-nogate", "0930F", SANDBOX_PARTIAL)
    write_opener(wt2, "0930F", None)
    run_collect(*stage1_args(sb, logdir2, "lane-env-nogate", "0930F", "PARTIAL", wt2))
    r2 = run_collect("stage2", "--repo", str(sb["repo"]), "--logdir", str(logdir2), "--no-push")
    assert r2.returncode == 0, r2.stderr
    assert "envnogate" in (sb["repo"] / "a.txt").read_text(encoding="utf-8")


def test_stage2_环境级PARTIAL判据FAIL不合并(sb: dict) -> None:
    logdir = new_logdir(sb)
    wt = make_lane(sb, "lane-env-fail", "one\nenvfail\n")
    write_lane_log(logdir, "lane-env-fail", "0930F", SANDBOX_PARTIAL)
    write_opener(wt, "0930F", "false\n")
    run_collect(*stage1_args(sb, logdir, "lane-env-fail", "0930F", "PARTIAL", wt))
    r = run_collect("stage2", "--repo", str(sb["repo"]), "--logdir", str(logdir), "--no-push")
    assert r.returncode == 0, r.stderr
    assert "envfail" not in (sb["repo"] / "a.txt").read_text(encoding="utf-8")
    results = (logdir / "collect.tsv").read_text(encoding="utf-8")
    assert "NEEDS-MANUAL-GATE" in results and "MERGED-ENV" not in results
