"""`docs/openers/commit-launcher.sh` ＋ `scripts/commit_request.py` —— Cowork 文档提交请求通道（0917X）。

Cowork 的 bash 在隔离 VM 里，碰 `.git/` 会留删不掉的锁，所以它只能写文件、不能 commit；
写好的文档得等「下一条泳道顺带提交」，已多次造成未提交态滞留与 ff 合并被挡（09-17 `0917K`）。
本通道仿 lane-launcher 的「请求文件 → launchd → 脚本」路：Cowork 用 Write 工具写一个
JSON 请求，launchd 以 Shao Peishen 本人身份调脚本 add/commit/push。

也正因为「谁都能写、写完就以本人身份执行」，这里的每条用例都是一道闸：
合法请求只带列出的路径；白名单外／越界路径一个都不放过；index.lock 不删；push=false 不推。
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_SH = ROOT / "docs" / "openers" / "commit-launcher.sh"

from scripts.commit_request import (  # noqa: E402
    OUTCOME_DEFERRED,
    OUTCOME_DONE,
    OUTCOME_REJECTED,
    validate_path,
)
from scripts.install_commit_launcher import LABEL, build_plist  # noqa: E402


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一个有初始提交的最小仓库：docs/ 与 openspec/changes/x/ 各带一个已跟踪文件。"""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "t")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "commit.gpgsign", "false")
    (r / "docs").mkdir()
    (r / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (r / "openspec" / "changes" / "x").mkdir(parents=True)
    (r / "openspec" / "changes" / "x" / "tasks.md").write_text("- [ ] t\n", encoding="utf-8")
    (r / "app").mkdir()
    (r / "app" / "m.py").write_text("x = 1\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    (r / ".claude" / "handoff" / "commit").mkdir(parents=True)
    return r


def write_request(repo: Path, name: str, body: dict | str) -> Path:
    req = repo / ".claude" / "handoff" / "commit" / f"{name}.request"
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    req.write_text(text, encoding="utf-8")
    return req


def run_launcher(repo: Path, **env_extra: str) -> subprocess.CompletedProcess:
    """走真正的薄壳 `commit-launcher.sh`，与 launchd 调用形态一致。"""
    env = dict(os.environ)
    env["COMMIT_LAUNCHER_REPO"] = str(repo)
    # 锁等待缩到几乎为 0：真实值 5 秒 × 5 次，测试没必要等 25 秒。
    env["COMMIT_LAUNCHER_LOCK_WAIT_SECONDS"] = "0.01"
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(LAUNCHER_SH)], env=env, capture_output=True, text=True, timeout=120
    )


def outcome_files(repo: Path, name: str) -> dict[str, Path]:
    d = repo / ".claude" / "handoff" / "commit"
    return {suffix: d / f"{name}.{suffix}" for suffix in ("request", "claiming", "done", "rejected", "deferred")}


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def commit_files(repo: Path, rev: str = "HEAD") -> list[str]:
    out = git(repo, "show", "--name-only", "--format=", rev).stdout
    return sorted(line for line in out.splitlines() if line)


# ─────────────────────────────────────────────────────────────────────────────
# 端到端：请求文件 → 薄壳 → Python → git
# ─────────────────────────────────────────────────────────────────────────────


def test_legal_request_commits_only_listed_paths(repo: Path) -> None:
    """① 合法请求 ⇒ 出现一个 commit，且**只**含列出的路径；.done 里有 hash。

    工作区里另有一个未列出的改动（docs/other.md）与一个别人暂存的改动（app/m.py），
    两者都不许被卷进来 —— 这是并行泳道成立的唯一前提（CLAUDE.md「只 git add 本条列出的路径」）。
    """
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    (repo / "docs" / "new.md").write_text("new\n", encoding="utf-8")  # 未跟踪的新文件
    (repo / "docs" / "other.md").write_text("other\n", encoding="utf-8")  # 未列出
    (repo / "app" / "m.py").write_text("x = 2\n", encoding="utf-8")
    git(repo, "add", "app/m.py")  # 别的 session 暂存了别的东西
    before = head(repo)

    write_request(repo, "20260917-120000", {
        "message": "docs: 测试提交",
        "paths": ["docs/a.md", "docs/new.md"],
        "push": False,
    })
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    files = outcome_files(repo, "20260917-120000")
    assert files["done"].exists(), proc.stdout + proc.stderr
    assert not files["request"].exists() and not files["claiming"].exists()

    after = head(repo)
    assert after != before
    assert commit_files(repo) == ["docs/a.md", "docs/new.md"]
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "docs: 测试提交"

    done = json.loads(files["done"].read_text(encoding="utf-8"))
    assert done["outcome"] == OUTCOME_DONE
    assert done["commit"] == after
    assert done["pushed"] is False

    # 别人暂存的 app/m.py 仍在暂存区、未被提交；docs/other.md 仍是未跟踪
    assert "app/m.py" in git(repo, "diff", "--cached", "--name-only").stdout
    assert "?? docs/other.md" in git(repo, "status", "--porcelain").stdout


def test_tasks_md_under_openspec_changes_is_allowed(repo: Path) -> None:
    (repo / "openspec" / "changes" / "x" / "tasks.md").write_text("- [x] t\n", encoding="utf-8")
    write_request(repo, "20260917-120100", {
        "message": "chore: 回勾", "paths": ["openspec/changes/x/tasks.md"], "push": False,
    })
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outcome_files(repo, "20260917-120100")["done"].exists(), proc.stdout + proc.stderr
    assert commit_files(repo) == ["openspec/changes/x/tasks.md"]


@pytest.mark.parametrize(
    "path",
    [
        "app/m.py",
        "scripts/x.py",
        "tests/test_x.py",
        "tools/x.py",
        ".claude/settings.json",
        "CLAUDE.md",
        "data/x.csv",
        ".env",
        ".env.local",
        "docs/openers/run-lanes.sh",
        "docs/openers/lane-launcher.sh",
        "docs/openers/commit-launcher.sh",
        "openspec/changes/x/design.md",
        "docs/../app/m.py",
        "/etc/passwd",
        "-A",
        ".",
        "docs/",
        "docs/*.md",
    ],
)
def test_out_of_whitelist_paths_are_rejected_without_commit(repo: Path, path: str) -> None:
    """② 白名单外／`..`／绝对路径／`-A`／`.`／目录／通配 ⇒ .rejected，且没有 commit。

    路径列表里**混一个**非法的就整条拒绝，⛔ 不做「跳过这个继续」—— 半截提交比不提交更难收拾。
    """
    # 让工作区里真有这些路径的改动（能建的都建出来），证明拒绝的原因是白名单而不是「未改动」
    target = repo / path
    if not path.startswith(("/", "-")) and ".." not in path and "*" not in path and not path.endswith("/") and path != ".":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n", encoding="utf-8")
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    before = head(repo)

    write_request(repo, "20260917-130000", {
        "message": "x", "paths": ["docs/a.md", path], "push": False,
    })
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    files = outcome_files(repo, "20260917-130000")
    assert files["rejected"].exists(), proc.stdout + proc.stderr
    assert not files["done"].exists()
    assert head(repo) == before
    body = json.loads(files["rejected"].read_text(encoding="utf-8"))
    assert body["outcome"] == OUTCOME_REJECTED
    assert path in body["reason"]
    # 连合法的那条也没被暂存
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_unchanged_path_is_rejected(repo: Path) -> None:
    """③ 列了一个工作区里没改动的路径 ⇒ rejected。

    「没改动」多半是 Cowork 写错了文件名；静默跳过会让它以为已提交。
    """
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    before = head(repo)
    write_request(repo, "20260917-140000", {
        "message": "x", "paths": ["docs/a.md", "docs/missing.md"], "push": False,
    })
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    files = outcome_files(repo, "20260917-140000")
    assert files["rejected"].exists(), proc.stdout + proc.stderr
    assert "docs/missing.md" in files["rejected"].read_text(encoding="utf-8")
    assert head(repo) == before


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps({"paths": ["docs/a.md"]}),                   # 缺 message
        json.dumps({"message": "", "paths": ["docs/a.md"]}),    # 空 message
        json.dumps({"message": "x"}),                            # 缺 paths
        json.dumps({"message": "x", "paths": []}),               # 空 paths
        json.dumps({"message": "x", "paths": "docs/a.md"}),      # paths 不是列表
        json.dumps({"message": "x", "paths": ["docs/a.md"], "push": "yes"}),  # push 不是布尔
    ],
)
def test_malformed_request_is_rejected(repo: Path, body: str) -> None:
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    before = head(repo)
    write_request(repo, "20260917-150000", body)
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    files = outcome_files(repo, "20260917-150000")
    assert files["rejected"].exists(), proc.stdout + proc.stderr
    assert head(repo) == before


def test_index_lock_defers_and_is_not_deleted(repo: Path) -> None:
    """④ `.git/index.lock` 在 ⇒ .deferred，锁**原样留着**，没有 commit。

    另一个 session 正在用那把锁；删了会毁掉它的提交（CLAUDE.md 并发协议第 4 条）。
    """
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    lock = repo / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    before = head(repo)

    write_request(repo, "20260917-160000", {"message": "x", "paths": ["docs/a.md"], "push": False})
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    files = outcome_files(repo, "20260917-160000")
    assert files["deferred"].exists(), proc.stdout + proc.stderr
    assert json.loads(files["deferred"].read_text(encoding="utf-8"))["outcome"] == OUTCOME_DEFERRED
    assert lock.exists(), "锁被删了"
    assert head(repo) == before
    assert not files["done"].exists()


def test_push_false_does_not_push(repo: Path, tmp_path: Path) -> None:
    """⑤ push=false ⇒ 本地 commit 有、远端没动。"""
    remote = tmp_path / "remote.git"
    git(remote.parent, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    remote_before = git(remote, "rev-parse", "main").stdout.strip()

    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    write_request(repo, "20260917-170000", {"message": "x", "paths": ["docs/a.md"], "push": False})
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outcome_files(repo, "20260917-170000")["done"].exists(), proc.stdout + proc.stderr
    assert git(remote, "rev-parse", "main").stdout.strip() == remote_before
    assert head(repo) != remote_before


def test_push_true_pushes_to_origin(repo: Path, tmp_path: Path) -> None:
    """⑥ push=true ⇒ 远端 main 跟到本地新 commit；.done 里 pushed=true。"""
    remote = tmp_path / "remote.git"
    git(remote.parent, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")

    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    write_request(repo, "20260917-180000", {"message": "x", "paths": ["docs/a.md"], "push": True})
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    files = outcome_files(repo, "20260917-180000")
    assert files["done"].exists(), proc.stdout + proc.stderr
    done = json.loads(files["done"].read_text(encoding="utf-8"))
    assert done["pushed"] is True
    assert git(remote, "rev-parse", "main").stdout.strip() == head(repo) == done["commit"]


def test_red_doc_size_test_rejects_before_commit(repo: Path) -> None:
    """⑦ `tests/test_doc_size_budget.py` 红 ⇒ rejected，不 commit、不暂存。"""
    (repo / "tests").mkdir()
    (repo / "tests" / "test_doc_size_budget.py").write_text(
        "def test_red():\n    assert False, 'over budget'\n", encoding="utf-8"
    )
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    before = head(repo)
    write_request(repo, "20260917-190000", {"message": "x", "paths": ["docs/a.md"], "push": False})
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    files = outcome_files(repo, "20260917-190000")
    assert files["rejected"].exists(), proc.stdout + proc.stderr
    assert "test_doc_size_budget" in files["rejected"].read_text(encoding="utf-8")
    assert head(repo) == before
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_missing_doc_size_test_is_noted_in_done(repo: Path) -> None:
    """没有体积闸测试文件（或没有 venv）⇒ 照常提交，但 .done 里注明跳过。"""
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    write_request(repo, "20260917-190100", {"message": "x", "paths": ["docs/a.md"], "push": False})
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    done = json.loads(outcome_files(repo, "20260917-190100")["done"].read_text(encoding="utf-8"))
    assert "跳过" in done["doc_size_test"]


def test_only_the_earliest_request_is_handled_per_invocation(repo: Path) -> None:
    (repo / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    write_request(repo, "20260917-200000", {"message": "x", "paths": ["docs/a.md"], "push": False})
    write_request(repo, "20260917-210000", {"message": "y", "paths": ["docs/a.md"], "push": False})
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outcome_files(repo, "20260917-200000")["done"].exists()
    assert outcome_files(repo, "20260917-210000")["request"].exists()


def test_no_request_is_a_silent_noop(repo: Path) -> None:
    """自己写的 .done 会再触发一次 WatchPaths，这条空转路径必须无副作用。"""
    before = head(repo)
    (repo / ".claude" / "handoff" / "commit" / "20260917-220000.done").write_text("{}", encoding="utf-8")
    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert head(repo) == before


# ─────────────────────────────────────────────────────────────────────────────
# 白名单纯函数
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "docs/findings/x.md", "docs/openers/OP-0820-全量编排.md", "docs/session接力.md",
    "openspec/changes/foo-bar/tasks.md",
])
def test_validate_path_accepts_whitelisted(path: str) -> None:
    assert validate_path(path) is None


@pytest.mark.parametrize("path", [
    "docs/openers/run-lanes.sh", "docs/openers/lane-launcher.sh", "docs/openers/commit-launcher.sh",
    "openspec/changes/foo/design.md", "openspec/specs/x.md", "app/x.py", "scripts/x.py",
    "tests/x.py", "tools/x", ".claude/x", "CLAUDE.md", "data/x", ".env", ".env.example",
    "docs/../x", "../docs/x", "/docs/x", "-A", "--all", ".", "", "docs", "docs/", "docs/*.md",
    "docs/x?.md", "docs/[a].md", "./docs/x.md", "docs//x.md",
])
def test_validate_path_rejects_out_of_whitelist(path: str) -> None:
    assert validate_path(path) is not None


# ─────────────────────────────────────────────────────────────────────────────
# 安装器 plist：09-09 lane-launcher 实证缺这两项会被连坐杀／找不到命令
# ─────────────────────────────────────────────────────────────────────────────

FAKE_HOME = Path("/Users/fake-home-0917X")


@pytest.fixture
def plist(tmp_path: Path) -> dict:
    return build_plist(tmp_path, FAKE_HOME)


def test_build_plist_abandons_process_group_and_sets_path(plist: dict) -> None:
    assert plist["AbandonProcessGroup"] is True
    path = plist["EnvironmentVariables"]["PATH"]
    assert "~" not in path
    entries = path.split(":")
    assert all(e.startswith("/") for e in entries), entries
    assert entries[0] == f"{FAKE_HOME}/.local/bin"
    assert "/opt/homebrew/bin" in entries and "/usr/bin" in entries


def test_build_plist_trigger_contract(tmp_path: Path, plist: dict) -> None:
    assert plist["Label"] == LABEL == "com.zhuopin.hr.commit-launcher"
    assert plist["RunAtLoad"] is False
    assert plist["WatchPaths"] == [str(tmp_path / ".claude" / "handoff" / "commit")]
    assert plist["ProgramArguments"] == ["/bin/bash", str(tmp_path / "docs" / "openers" / "commit-launcher.sh")]
    assert plist["WorkingDirectory"] == str(tmp_path)


def test_build_plist_is_pure(tmp_path: Path) -> None:
    before = sorted(p.name for p in tmp_path.iterdir())
    build_plist(tmp_path, FAKE_HOME)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_installer_print_only_does_not_install(tmp_path: Path) -> None:
    """`--print` 只打印 plist XML，不写 ~/Library/LaunchAgents、不碰 launchctl。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = dict(os.environ, HOME=str(fake_home))
    proc = subprocess.run(
        ["python3", str(ROOT / "scripts" / "install_commit_launcher.py"), "--print"],
        env=env, capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "AbandonProcessGroup" in proc.stdout
    assert "<key>PATH</key>" in proc.stdout
    assert not (fake_home / "Library" / "LaunchAgents").exists()
