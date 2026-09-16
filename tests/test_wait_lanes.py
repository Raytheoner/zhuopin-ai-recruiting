"""`docs/openers/wait-lanes.sh` —— 看护者阻塞等待脚本的行为断言（Token 治理 Phase 1，0916B）。

看护者靠它决定「什么时候醒来判断」。它若漏报，看护者会一直睡过 FAIL；若误报，
看护者又退回每几分钟醒一次的老路。所以四种返回各钉一条：新增结果 / 新一轮目录 /
进程退出 / 超时心跳，外加缺 --pid 的用法错误。全部用临时目录与一个 sleep 进程模拟，
⛔ 不碰真实 .claude/handoff，本机正在跑的批次不影响结果。
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "docs" / "openers" / "wait-lanes.sh"


@pytest.fixture
def env(tmp_path: Path):
    handoff = tmp_path / "handoff"
    logdir = handoff / "lanes-20260101-000000"
    logdir.mkdir(parents=True)
    (logdir / "manifest.tsv").write_text("a\tX1\tt1\na\tX2\tt2\n", encoding="utf-8")
    (logdir / "results.tsv").write_text("", encoding="utf-8")
    (logdir / "a-X1.log").write_text("hi\n", encoding="utf-8")
    sleeper = subprocess.Popen(["sleep", "60"])
    yield {"tmp": tmp_path, "handoff": handoff, "logdir": logdir, "pid": sleeper.pid, "proc": sleeper}
    sleeper.kill()
    sleeper.wait()


def run(env, *extra, max_s="15"):
    return subprocess.run(
        ["bash", str(SCRIPT), "--pid", str(env["pid"]), "--handoff", str(env["handoff"]),
         "--repo", str(env["tmp"]), "--interval", "1", "--max", max_s, *extra],
        capture_output=True, text=True, timeout=60,
    )


def later(seconds, fn):
    t = threading.Timer(seconds, fn)
    t.start()
    return t


def test_new_result_line_returns_change(env):
    def append():
        with open(env["logdir"] / "results.tsv", "a", encoding="utf-8") as fh:
            fh.write(f"a\tX1\tFAIL(1)\t7\t{env['logdir']}/a-X1.log\n")
    later(2, append)
    r = run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CHANGE" in r.stdout and "X1 FAIL(1)" in r.stdout
    assert "已完成 1/2" in r.stdout


def test_new_round_dir_returns_round(env):
    def new_round():
        d = env["handoff"] / "lanes-20260101-010000"
        d.mkdir()
        (d / "results.tsv").write_text("", encoding="utf-8")
    later(2, new_round)
    r = run(env, "--logdir", str(env["logdir"]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ROUND" in r.stdout and "lanes-20260101-010000" in r.stdout


def test_process_exit_returns_exited_with_summary(env):
    (env["logdir"] / "summary.txt").write_text("a X1 OK 5\n", encoding="utf-8")
    env["proc"].kill()
    env["proc"].wait()
    r = run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "EXITED" in r.stdout and "a X1 OK 5" in r.stdout


def test_timeout_without_change_is_heartbeat(env):
    start = time.monotonic()
    r = run(env, max_s="2")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "HEARTBEAT" in r.stdout and "已完成 0/2" in r.stdout
    assert time.monotonic() - start < 15
    assert len(r.stdout.strip().splitlines()) <= 3  # 心跳必须短，否则又把上下文喂胖


def test_warns_on_529(env):
    (env["logdir"] / "b-X2.log").write_text("API Error: 529 Overloaded\n", encoding="utf-8")
    r = run(env, max_s="1")
    assert "WARN 529" in r.stdout


def test_missing_pid_is_usage_error(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 2 and "USAGE" in r.stdout
