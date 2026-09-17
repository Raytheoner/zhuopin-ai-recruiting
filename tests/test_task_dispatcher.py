"""任务调度器（R2，0917AM）三件套的机器闸：

- `scripts/dispatcher_event.sh`：单实例锁互斥（pid 存活即退出、死 pid 覆盖）、事件文件处理完移入
  `events/processed/`（会话 rc≠0 则原地留着）、无事件且当日兜底已跑 ⇒ 不起会话、prompt 只含事件文件名。
- `scripts/install_task_dispatcher.py`：plist 两项触发键（WatchPaths ＋ StartCalendarInterval）必带、
  AbandonProcessGroup、PATH 无 `~`、`--print` 不装。
- `scripts/commit_request.py`：提交含 `docs/roadmap/定夺队列.md` 才写 `events/decision-*`。
- `.claude/skills/task-dispatcher/SKILL.md`：红线字样与九步关键字逐条在（机器闸）。

⛔ 用例不起真 claude（DISPATCHER_CLAUDE 指到假脚本）、不装 LaunchAgent、不写 ~/Library。
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHELL = ROOT / "scripts" / "dispatcher_event.sh"
SKILL = ROOT / ".claude" / "skills" / "task-dispatcher" / "SKILL.md"
RULES = ROOT / ".claude" / "skills" / "task-dispatcher" / "rules.md"

from scripts import install_task_dispatcher  # noqa: E402
from scripts.commit_request import write_decision_event  # noqa: E402
from scripts.install_task_dispatcher import LABEL, build_plist  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# dispatcher_event.sh
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".claude" / "handoff" / "events").mkdir(parents=True)
    return r


def fake_claude(repo: Path, rc: int = 0, body: str = "OPENER_DONE") -> Path:
    """假 claude：把 stdin（prompt）与参数落到 calls/ 目录，按 rc 退出。每次调用一个文件。"""
    calls = repo / "calls"
    calls.mkdir(exist_ok=True)
    script = repo / "fake-claude.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"n=$(ls '{calls}' | wc -l | tr -d ' ')\n"
        f'{{ echo "ARGS: $*"; echo "--- PROMPT ---"; cat; }} > "{calls}/call-$n.txt"\n'
        f"echo '{body}'\n"
        f"exit {rc}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def run_shell(repo: Path, claude: Path, **env_extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DISPATCHER_REPO"] = str(repo)
    env["DISPATCHER_CLAUDE"] = str(claude)
    env.update(env_extra)
    return subprocess.run(["bash", str(SHELL)], env=env, capture_output=True, text=True, timeout=60)


def calls(repo: Path) -> list[str]:
    d = repo / "calls"
    return [p.read_text(encoding="utf-8") for p in sorted(d.glob("call-*.txt"))] if d.is_dir() else []


def events_dir(repo: Path) -> Path:
    return repo / ".claude" / "handoff" / "events"


def test_event_files_are_moved_to_processed_after_a_clean_session(repo: Path) -> None:
    (events_dir(repo) / "lanes-done-20260917-010101").touch()
    (events_dir(repo) / "decision-20260917-020202").write_text("commit=abc\n", encoding="utf-8")
    proc = run_shell(repo, fake_claude(repo))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    remaining = sorted(p.name for p in events_dir(repo).iterdir() if p.is_file())
    assert remaining == [], f"事件文件应已移入 processed/：{remaining}"
    processed = sorted(p.name for p in (events_dir(repo) / "processed").iterdir())
    assert processed == ["decision-20260917-020202", "lanes-done-20260917-010101"]
    # prompt 里列了事件文件名、指明调 skill；⛔ 不把事件内容当命令拼进去
    assert len(calls(repo)) == 1
    prompt = calls(repo)[0]
    assert "lanes-done-20260917-010101" in prompt and "decision-20260917-020202" in prompt
    assert "task-dispatcher" in prompt and "OPENER_DONE" in prompt
    assert "commit=abc" not in prompt


def test_events_stay_when_session_exits_nonzero(repo: Path) -> None:
    (events_dir(repo) / "lanes-done-20260917-010101").touch()
    proc = run_shell(repo, fake_claude(repo, rc=1))
    assert proc.returncode == 1
    assert (events_dir(repo) / "lanes-done-20260917-010101").is_file(), "会话失败时事件必须原地留着等下次唤醒"
    assert not list((events_dir(repo) / "processed").iterdir())


def test_live_lock_holder_makes_second_instance_exit_without_calling_claude(repo: Path) -> None:
    (events_dir(repo) / "lanes-done-x").touch()
    holder = subprocess.Popen(["sleep", "30"])
    try:
        lock = repo / ".claude" / "handoff" / "dispatcher.lock"
        lock.write_text(f"{holder.pid}\n", encoding="utf-8")
        proc = run_shell(repo, fake_claude(repo))
        assert proc.returncode == 0
        assert calls(repo) == [], "锁持有者存活时⛔ 不得再起一个会话"
        assert (events_dir(repo) / "lanes-done-x").is_file()
        assert lock.read_text(encoding="utf-8").strip() == str(holder.pid), "⛔ 不得动别人的锁"
        launchd_log = repo / ".claude" / "handoff" / "dispatcher" / "launchd.log"
        assert "在跑" in launchd_log.read_text(encoding="utf-8")
    finally:
        holder.kill()
        holder.wait()


def test_dead_pid_lock_is_treated_as_orphan_and_released_on_exit(repo: Path) -> None:
    (events_dir(repo) / "lanes-done-x").touch()
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock = repo / ".claude" / "handoff" / "dispatcher.lock"
    lock.write_text(f"{dead.pid}\n", encoding="utf-8")
    proc = run_shell(repo, fake_claude(repo))
    assert proc.returncode == 0
    assert len(calls(repo)) == 1, "死 pid 的锁是孤儿锁，应覆盖并照跑"
    assert not lock.exists(), "退出时必须释放自己的锁"


def test_lock_is_held_for_the_whole_session(repo: Path) -> None:
    """会话进行中锁文件存在且内容是壳的 pid（假 claude 在跑时读锁）。"""
    (events_dir(repo) / "lanes-done-x").touch()
    lock = repo / ".claude" / "handoff" / "dispatcher.lock"
    probe = repo / "lock-seen.txt"
    script = repo / "fake-claude.sh"
    script.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        f"cat '{lock}' > '{probe}' 2>&1; echo \"env=$DISPATCHER_LOCK_HELD\" >> '{probe}'\n"
        "echo OPENER_DONE\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    proc = run_shell(repo, script)
    assert proc.returncode == 0, proc.stderr
    seen = probe.read_text(encoding="utf-8").splitlines()
    assert seen[0].strip().isdigit(), f"会话期间锁文件应含 pid：{seen}"
    assert seen[1] == "env=1", "壳必须导出 DISPATCHER_LOCK_HELD=1 让 skill ① 知道锁归壳管"


def test_no_events_runs_the_daily_fallback_once_then_stays_silent(repo: Path) -> None:
    claude = fake_claude(repo)
    first = run_shell(repo, claude)
    assert first.returncode == 0 and len(calls(repo)) == 1, "当日首次调用即使无事件也要兜底跑一次"
    assert "每日兜底" in calls(repo)[0]
    second = run_shell(repo, claude)
    assert second.returncode == 0
    assert len(calls(repo)) == 1, "无事件且当日兜底已跑 ⇒ 静默退出，⛔ 不再起会话（否则 processed/ 搬动触发 WatchPaths 就是死循环）"
    # 新事件到达 ⇒ 当日照样再起
    (events_dir(repo) / "lanes-done-y").touch()
    third = run_shell(repo, claude)
    assert third.returncode == 0 and len(calls(repo)) == 2


def test_events_arriving_during_a_session_are_consumed_in_the_same_invocation(repo: Path) -> None:
    (events_dir(repo) / "lanes-done-1").touch()
    calls_dir = repo / "calls"
    calls_dir.mkdir()
    script = repo / "fake-claude.sh"
    # 第一轮会话期间冒出第二个事件；第二轮不再制造。
    script.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        f"n=$(ls '{calls_dir}' | wc -l | tr -d ' ')\n: > \"{calls_dir}/call-$n.txt\"\n"
        f"[[ $n -eq 0 ]] && touch '{events_dir(repo)}/lanes-done-2'\n"
        "echo OPENER_DONE\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    proc = run_shell(repo, script)
    assert proc.returncode == 0, proc.stderr
    assert len(list(calls_dir.glob("call-*.txt"))) == 2
    assert sorted(p.name for p in (events_dir(repo) / "processed").iterdir()) == ["lanes-done-1", "lanes-done-2"]


def test_session_is_started_with_sonnet_budget_and_headless_flags(repo: Path) -> None:
    (events_dir(repo) / "lanes-done-1").touch()
    run_shell(repo, fake_claude(repo))
    args_line = calls(repo)[0].splitlines()[0]
    assert "--model sonnet" in args_line
    assert "--max-budget-usd 10" in args_line
    assert "-p " in args_line and "--dangerously-skip-permissions" in args_line
    assert "--strict-mcp-config" in args_line


def test_session_log_is_written_under_dispatcher_dir(repo: Path) -> None:
    (events_dir(repo) / "lanes-done-1").touch()
    run_shell(repo, fake_claude(repo, body="hello-from-fake"))
    logs = [p for p in (repo / ".claude" / "handoff" / "dispatcher").glob("*.log") if p.name != "launchd.log"]
    assert len(logs) == 1 and re.match(r"\d{8}-\d{6}\.log$", logs[0].name)
    assert "hello-from-fake" in logs[0].read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# install_task_dispatcher.py
# ─────────────────────────────────────────────────────────────────────────────

FAKE_HOME = Path("/Users/fake-home-0917AM")


@pytest.fixture
def plist(tmp_path: Path) -> dict:
    return build_plist(tmp_path, FAKE_HOME)


def test_plist_carries_both_trigger_keys(tmp_path: Path, plist: dict) -> None:
    """两项必带：WatchPaths（事件唤醒）＋ StartCalendarInterval（每日 09:00 兜底）。缺一都不报错只不触发。"""
    assert plist["WatchPaths"] == [str(tmp_path / ".claude" / "handoff" / "events")]
    assert plist["StartCalendarInterval"] == [{"Hour": 9, "Minute": 0}]
    assert plist["StartCalendarInterval"] == [
        {"Hour": h, "Minute": m} for h, m in install_task_dispatcher.CALENDAR_SLOTS
    ]
    assert "StartInterval" not in plist and "KeepAlive" not in plist
    assert plist["RunAtLoad"] is False, "登录时不该自己起一个会话花钱"


def test_plist_runs_the_shell_with_bash_from_repo_root(tmp_path: Path, plist: dict) -> None:
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == ["/bin/bash", str(tmp_path / "scripts" / "dispatcher_event.sh")]
    assert plist["WorkingDirectory"] == str(tmp_path)
    assert plist["AbandonProcessGroup"] is True


def test_plist_path_is_absolute_and_has_no_tilde(plist: dict) -> None:
    path = plist["EnvironmentVariables"]["PATH"]
    entries = path.split(":")
    assert all(e.startswith("/") for e in entries), entries
    assert "~" not in path
    assert str(FAKE_HOME / ".local" / "bin") in entries


def test_plist_never_carries_credentials(plist: dict) -> None:
    keys = {k.upper() for k in plist["EnvironmentVariables"]}
    assert not any("KEY" in k or "TOKEN" in k or "SECRET" in k or k.startswith("HR_") for k in keys)


def test_plist_is_pure(tmp_path: Path) -> None:
    build_plist(tmp_path, FAKE_HOME)
    assert not (tmp_path / ".claude").exists()


def test_installer_print_only_does_not_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(install_task_dispatcher, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(install_task_dispatcher, "agents_dir", lambda: tmp_path / "agents")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("--print 不得调 launchctl"))
    assert install_task_dispatcher.main(["--print"]) == 0
    out = capsys.readouterr().out
    parsed = plistlib.loads(out.encode("utf-8"))
    assert parsed["Label"] == LABEL and "WatchPaths" in parsed and "StartCalendarInterval" in parsed
    assert not (tmp_path / "agents").exists()


# ─────────────────────────────────────────────────────────────────────────────
# commit_request.py 的 decision 事件
# ─────────────────────────────────────────────────────────────────────────────


def test_commit_with_decision_queue_writes_a_decision_event(tmp_path: Path) -> None:
    ev = write_decision_event(tmp_path, ["docs/a.md", "docs/roadmap/定夺队列.md"], "deadbeef")
    assert ev is not None and ev.parent == tmp_path / ".claude" / "handoff" / "events"
    assert re.match(r"decision-\d{8}-\d{6}$", ev.name)
    assert ev.read_text(encoding="utf-8") == "commit=deadbeef\n"


def test_commit_without_decision_queue_writes_no_event(tmp_path: Path) -> None:
    assert write_decision_event(tmp_path, ["docs/a.md", "docs/roadmap/任务台账.yaml"], "deadbeef") is None
    assert not (tmp_path / ".claude" / "handoff" / "events").exists()


def test_end_to_end_commit_request_emits_event_only_for_decision_queue(tmp_path: Path) -> None:
    """走真正的 commit_request.main：两次请求，只有含定夺队列那次写事件。"""
    r = tmp_path / "repo"
    r.mkdir()

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.name", "t")
    git("config", "user.email", "t@example.com")
    git("config", "commit.gpgsign", "false")
    (r / "docs" / "roadmap").mkdir(parents=True)
    (r / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (r / "docs" / "roadmap" / "定夺队列.md").write_text("q\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    handoff = r / ".claude" / "handoff" / "commit"
    handoff.mkdir(parents=True)
    env = {**os.environ, "COMMIT_LAUNCHER_REPO": str(r), "COMMIT_LAUNCHER_LOCK_WAIT_SECONDS": "0.01"}

    def submit(name: str, paths: list[str]) -> dict:
        (handoff / f"{name}.request").write_text(json.dumps({"message": name, "paths": paths}), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "commit_request.py")], env=env, cwd=str(r), capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads((handoff / f"{name}.done").read_text(encoding="utf-8"))

    (r / "docs" / "a.md").write_text("a2\n", encoding="utf-8")
    d1 = submit("20260917-000001", ["docs/a.md"])
    assert "decision_event" not in d1
    assert not (r / ".claude" / "handoff" / "events").exists()

    (r / "docs" / "roadmap" / "定夺队列.md").write_text("q | 已答\n", encoding="utf-8")
    d2 = submit("20260917-000002", ["docs/roadmap/定夺队列.md"])
    assert d2["decision_event"].startswith(".claude/handoff/events/decision-")
    assert (r / d2["decision_event"]).read_text(encoding="utf-8") == f"commit={d2['commit']}\n"


# ─────────────────────────────────────────────────────────────────────────────
# SKILL.md 机器闸
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert SKILL.is_file(), f"缺 {SKILL.relative_to(ROOT)}"
    return SKILL.read_text(encoding="utf-8")


def test_skill_frontmatter(skill_text: str) -> None:
    head = skill_text.split("---", 2)
    assert len(head) >= 3, "frontmatter 缺失"
    assert re.search(r"^name:\s*task-dispatcher\s*$", head[1], re.M)
    assert re.search(r"^description:\s*\S", head[1], re.M)


@pytest.mark.parametrize(
    "red_line",
    [
        "⛔ 发版 `.51`",
        "⛔ 对外发信",
        "🆕 待发",
        "⛔ 改 `.claude/skills/` 其它 skill",
        "⛔ 改 CLAUDE.md",
        "⛔ 真实简历",
        "写定夺队列，不猜",
    ],
)
def test_skill_contains_every_red_line(skill_text: str, red_line: str) -> None:
    assert red_line in skill_text, f"红线字样缺失：{red_line}"


@pytest.mark.parametrize(
    "step_marker",
    [
        ".claude/handoff/dispatcher.lock",
        "pid 存活即退出",
        "scripts/dispatcher_backlog.py",
        "rules.md",
        "lane-dispatch",
        "号池",
        "run-lanes.sh --dry-run",
        "opener_split_check.py",
        "test_doc_size_budget.py",
        "launch-lanes",
        "docs/roadmap/定夺队列.md",
        "先 grep",
        ".request",
        "launch-queue-drain",
        "lanes-done-",
        "events/processed/",
    ],
)
def test_skill_names_every_step(skill_text: str, step_marker: str) -> None:
    assert step_marker in skill_text, f"九步关键字缺失：{step_marker}"


def test_skill_does_not_restate_rules_and_rules_file_exists(skill_text: str) -> None:
    assert RULES.is_file()
    assert "真源是同目录 `rules.md`" in skill_text
    # 四判据表是 rules.md §1 的正文，SKILL.md 不该复制一份（两处漂移就没有真源了）
    assert "| ① | **依赖全完成**" not in skill_text


def test_skill_refers_only_to_existing_repo_paths(skill_text: str) -> None:
    referenced = {
        "scripts/dispatcher_backlog.py",
        "scripts/dispatcher_event.sh",
        "scripts/install_task_dispatcher.py",
        "scripts/action_request.py",
        "scripts/opener_split_check.py",
        "scripts/archive_docs.py",
        "docs/openers/run-lanes.sh",
        "docs/openers/OP-0820-全量编排.md",
        "docs/openers/号池台账.md",
        "docs/roadmap/任务台账.yaml",
        "docs/roadmap/定夺队列.md",
        "docs/跟进信/README-跟进信清单.md",
        "tests/test_doc_size_budget.py",
    }
    for p in referenced:
        assert p in skill_text, f"SKILL.md 应引用 {p}"
        assert (ROOT / p).exists(), f"SKILL.md 引用的路径不存在：{p}"
