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


# ─────────────────────────────────────────────────────────────────────────────
# `scripts/install_lane_launcher.py` 生成的 plist —— 两处缺陷的回归钉（0909S）
#
# 09-09 首跑（0909Y）实测：监听 → 参数白名单 → `.started` 六秒内全通，run-lanes
# 却被 launchd 连坐杀掉，`command -v claude` 另外退 10。两处根因都在 plist 字典里，
# 而 plist 生成过去埋在 `main()` 里、跟 launchctl 调用绑在一起 —— 没装 LaunchAgent
# 就测不到。所以先把字典构造提成纯函数 `build_plist`，再由这组用例钉住。
#
# 这两条都属于「错了不报错」的形态：连坐杀掉时 launchd 自己一切正常，
# PATH 缺失时退出码是 10 而不是「找不到 claude」，所以只能靠断言守。
# ─────────────────────────────────────────────────────────────────────────────

from scripts.install_lane_launcher import LABEL, build_plist  # noqa: E402

FAKE_HOME = Path("/Users/fake-home-0909S")


@pytest.fixture
def plist(tmp_path: Path) -> dict:
    return build_plist(tmp_path, FAKE_HOME)


def test_build_plist_abandons_process_group(plist: dict) -> None:
    """① AbandonProcessGroup 必须为 True，否则 run-lanes 被连坐杀掉。

    lane-launcher.sh 退出后，launchd 认为这条 job 已经结束，回收整个进程组 ——
    它发的是 SIGKILL，`nohup`（只挡 SIGHUP）挡不住。症状是整批泳道在
    `.started` 写完后几秒内集体消失，而 launchd 侧一切正常、退出码 0。
    """
    assert plist["AbandonProcessGroup"] is True


def test_build_plist_path_is_absolute_and_covers_local_bin(plist: dict) -> None:
    """② PATH 必须显式给，且 `~/.local/bin` 要写成绝对路径。

    launchd 不继承登录 shell 的环境，默认 PATH 里没有 `~/.local/bin`；
    `claude` 装在那儿，于是 `command -v claude` 让 launcher 退 10。
    plist 也**不做波浪号展开** —— 留一个 `~` 等于留一个永远不存在的目录。
    """
    path = plist["EnvironmentVariables"]["PATH"]
    entries = path.split(":")

    assert "~" not in path, path
    assert path.startswith("/"), path
    assert all(entry.startswith("/") for entry in entries), entries
    assert any(entry.endswith("/.local/bin") for entry in entries), entries
    # HOME 按渲染时的绝对路径写死，且优先于系统目录 —— claude 就在这一条里。
    assert entries[0] == f"{FAKE_HOME}/.local/bin", entries


def test_build_plist_keeps_locale_pinned(plist: dict) -> None:
    """③ 加 PATH 不许把 locale 挤掉。

    run-lanes.sh 顶部「locale 钉死」：UTF-8 下 macOS 自带 awk 与 bash 3.2 处理
    中文会**静默出错**。这两个变量掉了不会报错，只会让编排结果悄悄不对。
    """
    env = plist["EnvironmentVariables"]
    assert env["LC_ALL"] == "C"
    assert env["LANG"] == "C"


def test_build_plist_keeps_trigger_contract(tmp_path: Path, plist: dict) -> None:
    """本次修的是环境，触发契约一个字都不许动。

    RunAtLoad 一旦为真，登录就自己发一批车；WatchPaths 指错目录则从此永不触发
    且**不报错**（launchd 静默忽略）。两条都用断言钉住，防后续改动顺手带走。
    """
    assert plist["Label"] == LABEL
    assert plist["RunAtLoad"] is False
    watch_dir = tmp_path / ".claude" / "handoff" / "launch"
    assert plist["WatchPaths"] == [str(watch_dir)]
    assert plist["ProgramArguments"] == [
        "/bin/bash",
        str(tmp_path / "docs" / "openers" / "lane-launcher.sh"),
    ]
    assert plist["WorkingDirectory"] == str(tmp_path)


def test_build_plist_is_pure(tmp_path: Path) -> None:
    """build_plist 只算不写：建目录、跑 launchctl 都留在 main() 里。

    不然这组用例本身就会在跑测试的机器上留下 `.claude/handoff/launch/`，
    甚至换掉真的 LaunchAgent。
    """
    before = sorted(p.name for p in tmp_path.iterdir())
    build_plist(tmp_path, FAKE_HOME)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
