"""`docs/openers/lane-launcher.sh` —— launchd 发车触发器的行为断言（0909G）。

这个脚本是**发车链路上唯一不经过 Claude 的一环**：看护者只写一个请求文件，
launchd 看到目录变化就以 Shao Peishen 本人的身份调用它。也正因为如此，请求文件
是一个「谁都能写、写完就以本人身份执行」的入口 —— 参数白名单一旦漏，它就是一条
本地提权通道。这里的四条用例分别钉死：合法请求真发车、非法 token 一个都不放过、
已有 run-lanes 在跑时不并发、`--dry-run` 不许从这条路走。

⚠️ 用例跑的时候本机**很可能真有 run-lanes.sh 在跑**（泳道批次）。所以除了并发
用例，其余一律把 `LANE_LAUNCHER_PGREP_PATTERN` 注入成一个匹配不到任何进程的哨兵
串 —— 否则真实泳道会把本该发车的用例判成 deferred，测试结果取决于跑测试的时机。
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "docs" / "openers" / "lane-launcher.sh"

# 匹配不到任何进程的 pgrep 模式。带 uuid 是为了防止哪天真有个同名进程。
NEVER_MATCHES = f"zzz-no-such-process-{uuid.uuid4().hex}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一个只有 launch 目录与假执行器的最小仓库。"""
    launch = tmp_path / ".claude" / "handoff" / "launch"
    launch.mkdir(parents=True)

    # 假 run-lanes.sh：把收到的参数**逐字**写进 args.txt，再 sleep 1 模拟长任务。
    # 逐字比对是这组用例里最要紧的断言 —— 参数被吞掉一个（比如 --only 的取值）
    # 不会报错，只会静默地把整批泳道都跑一遍。
    fake = tmp_path / "fake-run-lanes.sh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" > "$(dirname "$0")/args.txt"\n'
        "sleep 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return tmp_path


def run_launcher(repo: Path, pgrep_pattern: str = NEVER_MATCHES) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LANE_LAUNCHER_REPO"] = str(repo)
    env["LANE_LAUNCHER_RUNNER"] = "fake-run-lanes.sh"
    env["LANE_LAUNCHER_PGREP_PATTERN"] = pgrep_pattern
    return subprocess.run(
        ["bash", str(LAUNCHER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def write_request(repo: Path, name: str, line: str) -> Path:
    req = repo / ".claude" / "handoff" / "launch" / f"{name}.request"
    req.write_text(line + "\n", encoding="utf-8")
    return req


def wait_for(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_legal_request_launches_runner_with_verbatim_args(repo: Path) -> None:
    """① 合法请求 → .started 含 PID、.consumed 存在、假执行器收到的参数逐字相同。"""
    args = "--full-auto --yes --only 0909C,0909D --max-parallel 2 --stagger 30 --budget 25.00"
    write_request(repo, "20260908-120000", args)

    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stderr

    launch = repo / ".claude" / "handoff" / "launch"
    started = launch / "20260908-120000.started"
    assert started.exists(), proc.stdout + proc.stderr

    body = started.read_text(encoding="utf-8")
    pid_line = next(line for line in body.splitlines() if line.startswith("pid="))
    pid = int(pid_line.split("=", 1)[1])
    assert pid > 0
    assert f"boot_log={launch / '20260908-120000.boot.log'}" in body

    assert (launch / "20260908-120000.consumed").exists()
    assert not (launch / "20260908-120000.request").exists()
    assert not (launch / "20260908-120000.claimed").exists()

    assert wait_for(repo / "args.txt"), "假执行器没有被调用"
    received = (repo / "args.txt").read_text(encoding="utf-8").splitlines()
    assert received == args.split(), received


@pytest.mark.parametrize(
    "line",
    [
        "--full-auto --yes; rm -rf /",
        "--full-auto --yes --model claude-opus-5",
        "--full-auto --chain",
        "--only 0909C; touch /tmp/pwned",
        "--max-parallel abc",
        "--budget",
        "",
    ],
)
def test_illegal_tokens_are_rejected_without_launching(repo: Path, line: str) -> None:
    """② 非法 token → .rejected，且假执行器一次都没被调用。

    白名单是这条链路唯一的防线：请求文件是普通文件，被注入的 Claude 也写得了。
    `; rm -rf /` 这类之所以进不来，不是因为没人写得出，而是因为脚本既不 eval
    也不放行未知 token。
    """
    write_request(repo, "20260908-130000", line)

    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stderr

    launch = repo / ".claude" / "handoff" / "launch"
    rejected = launch / "20260908-130000.rejected"
    assert rejected.exists(), proc.stdout + proc.stderr
    assert "拒绝原因" in rejected.read_text(encoding="utf-8")

    assert not (launch / "20260908-130000.started").exists()
    assert not (launch / "20260908-130000.consumed").exists()
    time.sleep(0.3)
    assert not (repo / "args.txt").exists(), "假执行器被调用了，白名单没拦住"


def test_concurrent_run_lanes_defers_the_request(repo: Path) -> None:
    """③ 已有 run-lanes 进程在跑 → .deferred，不发车。

    两条 run-lanes 同时跑会让两批泳道抢同一批 worktree 分支，产出互相覆盖且
    **不报错**（memory「同一 opener 重复派发会静默覆盖产出」）。
    """
    busy = repo / f"fake-busy-{uuid.uuid4().hex}.sh"
    busy.write_text("#!/usr/bin/env bash\nsleep 20\n", encoding="utf-8")
    busy.chmod(0o755)
    holder = subprocess.Popen(["bash", str(busy)])
    try:
        # 等 pgrep 真能看到它，再跑 launcher —— 否则这条用例会偶发地测成 ①。
        for _ in range(100):
            if subprocess.run(["pgrep", "-f", busy.name], capture_output=True).returncode == 0:
                break
            time.sleep(0.05)
        else:
            pytest.fail("假占位进程没能被 pgrep 看到")

        write_request(repo, "20260908-140000", "--full-auto --yes")
        proc = run_launcher(repo, pgrep_pattern=busy.name)
        assert proc.returncode == 0, proc.stderr
    finally:
        holder.kill()
        holder.wait()

    launch = repo / ".claude" / "handoff" / "launch"
    deferred = launch / "20260908-140000.deferred"
    assert deferred.exists(), proc.stdout + proc.stderr
    assert "推迟原因" in deferred.read_text(encoding="utf-8")
    assert not (launch / "20260908-140000.started").exists()
    time.sleep(0.3)
    assert not (repo / "args.txt").exists()


def test_dry_run_is_not_an_allowed_argument(repo: Path) -> None:
    """④ --dry-run 走这条路一律拒绝。

    launcher 只起真跑。dry-run 由看护者自己在 session 里跑 —— 那条命令不触发
    Auto Mode 分类器，本来就不需要绕到 launchd 这边来；放进白名单只会多一条
    「请求文件能让 launchd 起进程」的形态，收益为零。
    """
    write_request(repo, "20260908-150000", "--dry-run")

    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stderr

    launch = repo / ".claude" / "handoff" / "launch"
    rejected = launch / "20260908-150000.rejected"
    assert rejected.exists(), proc.stdout + proc.stderr
    assert "--dry-run" in rejected.read_text(encoding="utf-8")
    assert not (launch / "20260908-150000.started").exists()


def test_only_the_earliest_request_is_handled_per_invocation(repo: Path) -> None:
    """一次只处理最早的一个；其余留在原地等 WatchPaths 的下一次触发。"""
    write_request(repo, "20260908-160000", "--full-auto --yes")
    write_request(repo, "20260908-170000", "--full-auto --yes")

    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stderr

    launch = repo / ".claude" / "handoff" / "launch"
    assert (launch / "20260908-160000.consumed").exists()
    assert (launch / "20260908-170000.request").exists()
    assert not (launch / "20260908-170000.started").exists()


def test_no_request_is_a_silent_noop(repo: Path) -> None:
    """目录空（或只剩已处理过的文件）→ 退出 0，什么都不做。

    launchd 的 WatchPaths 会被本脚本自己写 .started/.consumed 再次触发，
    这条空转路径每批至少走一次，必须是无副作用的。
    """
    (repo / ".claude" / "handoff" / "launch" / "20260908-180000.consumed").write_text(
        "--full-auto --yes\n", encoding="utf-8"
    )

    proc = run_launcher(repo)
    assert proc.returncode == 0, proc.stderr
    assert not (repo / "args.txt").exists()
