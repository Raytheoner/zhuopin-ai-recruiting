"""受限会话投递中继（0918H）机器闸：

- `scripts/handoff_relay.py`：合法 commit/launch/event 各一条搬对目录且去前缀；
  非法文件名、白名单外 paths、坏 JSON、发车参数含 `;`/`$(`、非 0 字节事件件、
  mtime < 30 秒（本轮跳过不搬）各一条的行为符合 opener 校验表；`relay.log` 有对应拒收行。
- `scripts/install_handoff_relay.py`：plist 的 WatchPaths（仓库根 handoff-inbox/）、
  StartInterval 兜底、AbandonProcessGroup、PATH 无字面量 `~`；`--print` 不装。

⛔ 全部用 tmp_path 造假仓库根，不往真 `.claude/handoff/` 或 `handoff-inbox/` 里写测试件
（真身自检是 opener 第五节的另一件事，在主检出手工做）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from scripts import handoff_relay as relay
from scripts import install_handoff_relay as installer


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "handoff-inbox").mkdir(parents=True)
    return r


def drop(repo: Path, name: str, content: str | bytes = "", age_seconds: float = 40.0) -> Path:
    """在 handoff-inbox/ 下落一个投递件，mtime 设为 age_seconds 之前。"""
    path = repo / "handoff-inbox" / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    old = time.time() - age_seconds
    os.utime(path, (old, old))
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 合法搬运
# ─────────────────────────────────────────────────────────────────────────────


def test_commit_request_moved_and_prefix_stripped(repo: Path):
    drop(repo, "commit-20260918-093000-src.request", json.dumps({"message": "m", "paths": ["docs/x.md"]}))
    outcomes = relay.run(repo)
    assert outcomes == [("commit-20260918-093000-src.request", "moved")]
    dst = repo / ".claude/handoff/commit/20260918-093000-src.request"
    assert dst.is_file()
    assert not (repo / "handoff-inbox/commit-20260918-093000-src.request").exists()


def test_commit_action_without_paths_moved(repo: Path):
    """.action 请求（如 kickstart-liaison）通常没有 paths 字段——空集合校验应视为通过。"""
    drop(repo, "commit-20260918-093500.action", json.dumps({"action": "kickstart-liaison"}))
    outcomes = relay.run(repo)
    assert outcomes == [("commit-20260918-093500.action", "moved")]
    assert (repo / ".claude/handoff/commit/20260918-093500.action").is_file()


def test_launch_request_moved(repo: Path):
    drop(repo, "launch-20260918-094000-0918Z.request", "--full-auto --yes --only 0918Z\n")
    outcomes = relay.run(repo)
    assert outcomes == [("launch-20260918-094000-0918Z.request", "moved")]
    dst = repo / ".claude/handoff/launch/20260918-094000-0918Z.request"
    assert dst.read_text(encoding="utf-8") == "--full-auto --yes --only 0918Z\n"


def test_launch_request_multi_only_values_moved(repo: Path):
    drop(repo, "launch-20260918-094500.request", "--full-auto --yes --only 0918Z,0918Y")
    outcomes = relay.run(repo)
    assert outcomes == [("launch-20260918-094500.request", "moved")]


def test_event_moved(repo: Path):
    drop(repo, "event-lanes-done-20260918-100000", "")
    outcomes = relay.run(repo)
    assert outcomes == [("event-lanes-done-20260918-100000", "moved")]
    assert (repo / ".claude/handoff/events/lanes-done-20260918-100000").is_file()


# ─────────────────────────────────────────────────────────────────────────────
# 拒收
# ─────────────────────────────────────────────────────────────────────────────


def test_unknown_prefix_rejected(repo: Path):
    drop(repo, "foo-20260918-093000.request", "{}")
    outcomes = relay.run(repo)
    assert outcomes == [("foo-20260918-093000.request", "rejected")]
    assert (repo / "handoff-inbox/rejected/foo-20260918-093000.request").is_file()
    log_text = (repo / "handoff-inbox/relay.log").read_text(encoding="utf-8")
    assert "foo-20260918-093000.request" in log_text
    assert "REJECTED" in log_text


def test_bad_ts_format_rejected(repo: Path):
    drop(repo, "commit-2026-09-18.request", json.dumps({"message": "m", "paths": ["docs/x.md"]}))
    outcomes = relay.run(repo)
    assert outcomes == [("commit-2026-09-18.request", "rejected")]


def test_path_traversal_in_filename_rejected(repo: Path):
    drop(repo, "commit-20260918-093000-..-x.request", "{}")
    outcomes = relay.run(repo)
    assert outcomes == [("commit-20260918-093000-..-x.request", "rejected")]


def test_commit_path_outside_whitelist_rejected(repo: Path):
    drop(repo, "commit-20260918-093000.request", json.dumps({"message": "m", "paths": ["app/x.py"]}))
    outcomes = relay.run(repo)
    assert outcomes == [("commit-20260918-093000.request", "rejected")]
    log_text = (repo / "handoff-inbox/relay.log").read_text(encoding="utf-8")
    assert "app/x.py" in log_text or "不在白名单" in log_text


def test_commit_bad_json_rejected(repo: Path):
    drop(repo, "commit-20260918-093000.request", "{not json")
    outcomes = relay.run(repo)
    assert outcomes == [("commit-20260918-093000.request", "rejected")]


@pytest.mark.parametrize(
    "payload",
    [
        "--full-auto --yes --only 0918Z; rm -rf /",
        "--full-auto --yes --only 0918Z$(whoami)",
        "--full-auto --yes --only 0918Z && echo hi",
        "--full-auto --yes --only 0918Z --max-parallel 3",
        "--full-auto --yes",
        "--only 0918Z",
    ],
)
def test_launch_args_outside_whitelist_regex_rejected(repo: Path, payload: str):
    drop(repo, "launch-20260918-094000.request", payload)
    outcomes = relay.run(repo)
    assert outcomes == [("launch-20260918-094000.request", "rejected")]


def test_event_nonzero_bytes_rejected(repo: Path):
    drop(repo, "event-lanes-done-x", "not empty")
    outcomes = relay.run(repo)
    assert outcomes == [("event-lanes-done-x", "rejected")]


def test_event_bad_name_rejected(repo: Path):
    drop(repo, "event-", "")
    outcomes = relay.run(repo)
    assert outcomes == [("event-", "rejected")]


# ─────────────────────────────────────────────────────────────────────────────
# mtime 防半截文件
# ─────────────────────────────────────────────────────────────────────────────


def test_fresh_file_skipped_not_touched(repo: Path):
    path = drop(repo, "commit-20260918-093000.request", json.dumps({"message": "m", "paths": ["docs/x.md"]}), age_seconds=1.0)
    outcomes = relay.run(repo)  # 默认 min_age=30s
    assert outcomes == [("commit-20260918-093000.request", "skipped")]
    assert path.exists()  # 原地未动
    assert not (repo / ".claude/handoff/commit").exists() or not any((repo / ".claude/handoff/commit").iterdir())
    assert not (repo / "handoff-inbox/rejected/commit-20260918-093000.request").exists()


def test_fresh_file_processed_next_round_once_aged(repo: Path):
    drop(repo, "commit-20260918-093000.request", json.dumps({"message": "m", "paths": ["docs/x.md"]}), age_seconds=1.0)
    assert relay.run(repo) == [("commit-20260918-093000.request", "skipped")]
    # 下一轮：把 mtime 拨回 40 秒前，模拟「时间已过去」
    path = repo / "handoff-inbox/commit-20260918-093000.request"
    old = time.time() - 40.0
    os.utime(path, (old, old))
    assert relay.run(repo) == [("commit-20260918-093000.request", "moved")]


# ─────────────────────────────────────────────────────────────────────────────
# 并发：launchd WatchPaths 与 300s 兜底可能重叠触发
# ─────────────────────────────────────────────────────────────────────────────


def test_file_removed_mid_processing_is_claimed_elsewhere_not_crash(repo: Path):
    """模拟另一实例抢先把文件搬走：process_one 不应抛异常，应返回 claimed-elsewhere。"""
    entry = drop(repo, "commit-20260918-093000.request", json.dumps({"message": "m", "paths": ["docs/x.md"]}))
    entry.unlink()  # 另一实例已经把它搬走（这里简化为直接删除）
    outcome = relay.process_one(repo, entry, min_age_seconds=0)
    assert outcome == "claimed-elsewhere"


# ─────────────────────────────────────────────────────────────────────────────
# 基础设施：忽略自身产物、目录建出来
# ─────────────────────────────────────────────────────────────────────────────


def test_relay_log_and_gitkeep_ignored(repo: Path):
    (repo / "handoff-inbox" / ".gitkeep").write_text("", encoding="utf-8")
    (repo / "handoff-inbox" / "relay.log").write_text("stale\n", encoding="utf-8")
    outcomes = relay.run(repo)
    assert outcomes == []


def test_run_creates_inbox_and_rejected_dirs(tmp_path: Path):
    r = tmp_path / "bare-repo"
    assert not r.exists()
    outcomes = relay.run(r)
    assert outcomes == []
    assert (r / "handoff-inbox").is_dir()
    assert (r / "handoff-inbox/rejected").is_dir()


# ─────────────────────────────────────────────────────────────────────────────
# install_handoff_relay.py
# ─────────────────────────────────────────────────────────────────────────────


def test_build_plist_has_watchpaths_and_fallback_interval(tmp_path: Path):
    plist = installer.build_plist(tmp_path, tmp_path / "home")
    assert plist["WatchPaths"] == [str(tmp_path / "handoff-inbox")]
    assert plist["StartInterval"] == 300
    assert plist["AbandonProcessGroup"] is True
    assert plist["RunAtLoad"] is False
    assert "~" not in plist["EnvironmentVariables"]["PATH"]
    assert str(tmp_path / "home" / ".local" / "bin") in plist["EnvironmentVariables"]["PATH"]


def test_print_only_does_not_touch_launchctl(capsys):
    rc = installer.main(["--print"])
    assert rc == 0
    out = capsys.readouterr().out
    assert installer.LABEL in out
