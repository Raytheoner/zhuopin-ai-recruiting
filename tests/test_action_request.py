"""`scripts/action_request.py` —— Cowork 动作请求通道的行为断言（0917AK，补 Cowork·0917D 直写时欠的测试）。

这条通道与 lane-launcher / commit-launcher 同构：Cowork 用 Write 工具往
`.claude/handoff/commit/` 写一个 `<时间戳>.action`（JSON），launchd 以 Shao Peishen 本人
身份调 `commit-launcher.sh`，它先调本脚本处理动作、再照旧处理 `.request`。

也正因为「谁都能写、写完就以本人身份执行」，每条用例都是一道闸：
白名单外的动作一个不放；发信必须有台账「🆕 待发」的授权留痕；路径闸连 `..` 都不放；
输出里 `key=` 后面打码；处理完不留 `.action-claiming` 残留；外部调用（launchctl、
tools.liaison CLI、安装脚本）只断言**调用形状**，⛔ 不真起进程。

⚠️ 跑用例时本机**很可能真有 run-lanes.sh 在跑**。凡涉及「有无 run-lanes」的判据，
一律注入一个匹配不到任何进程的 pgrep 模式，否则结果取决于跑测试的时机。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from scripts import action_request as ar

ROOT = Path(__file__).resolve().parent.parent
COMMIT_LAUNCHER_SH = ROOT / "docs" / "openers" / "commit-launcher.sh"

NEVER_MATCHES = f"zzz-no-such-process-{uuid.uuid4().hex}"

LETTER = "# HR#3 · 第三封跟进信\n\n正文。\n"
LEDGER_PENDING = "| 日期 | 编号 | 主题 | 发送状态 |\n|---|---|---|---|\n| 2026-09-17 | `HR#3` | 第三封 | 🆕 待发 |\n"
LEDGER_SENT = LEDGER_PENDING.replace("🆕 待发", "✅ 已发 09-17")
QUEUE_HEAD = (
    "# 定夺队列\n\n## 一、待答\n\n| 编号 | 场景 | 阻塞类型 | 问题 | 选项与代价 | 推荐 | 来源 | 阻塞的任务 id | 状态 | 答复 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
)
G4_ROW = "| Q-05 | M2 | 决策（G4 闸门） | 【G4 发信】`HR#3`：起草完 ｜ 产出 `docs/跟进信/HR-3.md` | (a) | 无默认 | s | — | {status} | {reply} |\n"
G4_RELEASED = QUEUE_HEAD + G4_ROW.format(status="已答", reply="审核通过·发")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """最小仓库：handoff/commit、跟进信目录＋台账、一个合法安装脚本。模块路径全部指过来。"""
    r = tmp_path / "repo"
    (r / ".claude" / "handoff" / "commit").mkdir(parents=True)
    (r / "docs" / "跟进信").mkdir(parents=True)
    (r / "docs" / "跟进信" / "HR-3.md").write_text(LETTER, encoding="utf-8")
    (r / "docs" / "跟进信" / "HR-3.docx").write_bytes(b"PK")
    (r / "docs" / "跟进信" / "README-跟进信清单.md").write_text(LEDGER_PENDING, encoding="utf-8")
    (r / "docs" / "roadmap").mkdir(parents=True)
    (r / "docs" / "roadmap" / "定夺队列.md").write_text(G4_RELEASED, encoding="utf-8")  # 0917AO：G4 已放行是「能发」的第二个前提
    (r / "scripts").mkdir()
    (r / "scripts" / "install_demo_agent.py").write_text("print('hi')\n", encoding="utf-8")
    ar.configure(r)
    monkeypatch.setattr(ar, "PGREP_PATTERN", NEVER_MATCHES)
    monkeypatch.setattr(ar, "DRAIN_WAIT_SECONDS", 0)
    yield r
    ar.configure(ar.DEFAULT_REPO)


class Recorder:
    """`subprocess.run` 替身：记下每次调用的形状，回一个可配置的结果。"""

    def __init__(self, returncode: int = 0, stdout: str = "ok\n"):
        self.calls: list[dict] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": [str(c) for c in cmd], **{k: v for k, v in kw.items() if k in ("cwd", "env")}})
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr="")


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(ar.subprocess, "run", rec)
    return rec


def write_action(repo: Path, name: str, body: dict | str) -> Path:
    p = repo / ".claude" / "handoff" / "commit" / f"{name}.action"
    p.write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


def outcomes(repo: Path, name: str) -> dict[str, dict | None]:
    d = repo / ".claude" / "handoff" / "commit"
    out: dict[str, dict | None] = {}
    for suffix in ("done", "failed", "rejected"):
        f = d / f"{name}.{suffix}"
        out[suffix] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    return out


def assert_no_residue(repo: Path, name: str) -> None:
    d = repo / ".claude" / "handoff" / "commit"
    assert not (d / f"{name}.action").exists()
    assert not (d / f"{name}.action-claiming").exists(), "处理完不许留 claiming 残留"
    assert (d / f"{name}.processed").exists(), "认领件应改名 .processed 留痕"


# ─────────────────────────────────────────────────────────────────────────────
# 白名单与残留
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"action": "rm-rf"},
        {"action": "send-followup; rm -rf /"},
        {"no_action": 1},
        ["send-followup"],
        "not json at all",
    ],
)
def test_unknown_or_malformed_action_is_rejected_and_nothing_runs(repo: Path, recorder: Recorder, body) -> None:
    write_action(repo, "20260917-170000", body)
    assert ar.main() == 0
    o = outcomes(repo, "20260917-170000")
    assert o["done"] is None
    assert o["rejected"] is not None or o["failed"] is not None, o
    assert recorder.calls == [], "白名单外的动作不许起任何进程"
    assert_no_residue(repo, "20260917-170000")


def test_claiming_file_is_renamed_to_processed_after_success(repo: Path, recorder: Recorder) -> None:
    """09-17 真机现场留着 `*.action-claiming`（与 .done 并存）——完成后必须收成 .processed。"""
    write_action(repo, "20260917-170100", {"action": "kickstart-liaison"})
    assert ar.main() == 0
    assert outcomes(repo, "20260917-170100")["done"] is not None
    assert_no_residue(repo, "20260917-170100")


def test_actions_are_processed_in_name_order_and_all_in_one_pass(repo: Path, recorder: Recorder) -> None:
    write_action(repo, "20260917-170300", {"action": "kickstart-liaison"})
    write_action(repo, "20260917-170200", {"action": "kickstart-liaison"})
    assert ar.main() == 0
    assert len(recorder.calls) == 2
    for name in ("20260917-170200", "20260917-170300"):
        assert outcomes(repo, name)["done"] is not None
        assert_no_residue(repo, name)


# ─────────────────────────────────────────────────────────────────────────────
# 打码与路径闸
# ─────────────────────────────────────────────────────────────────────────────


def test_mask_hides_everything_after_key_equals() -> None:
    text = "GET https://x/api?key=sk-live-123456&corpid=abc  key='zzz' key=\"qqq\""
    masked = ar.mask(text)
    assert "sk-live-123456" not in masked and "zzz" not in masked and "qqq" not in masked
    assert "key=***" in masked
    assert "corpid=abc" in masked, "只打 key=，别的参数保留可读"


@pytest.mark.parametrize(
    "path",
    [
        "docs/跟进信/../跟进信/HR-3.md",
        "docs/跟进信/../../etc/passwd",
        "docs/HR-3.md",
        "/etc/passwd",
        "docs/跟进信/sub/HR-3.md",
        "docs/跟进信/HR-3.txt",
        "docs/跟进信/不存在.md",
        None,
        42,
    ],
)
def test_letter_path_gate_rejects_traversal_and_out_of_dir(repo: Path, path) -> None:
    assert ar.check_letter_path(path) is None


def test_letter_path_gate_accepts_existing_letter(repo: Path) -> None:
    assert ar.check_letter_path("docs/跟进信/HR-3.md") == repo / "docs/跟进信/HR-3.md"


# ─────────────────────────────────────────────────────────────────────────────
# send-followup：待发闸 ＋ 调用形状
# ─────────────────────────────────────────────────────────────────────────────


def test_send_followup_is_rejected_unless_ledger_says_pending(repo: Path, recorder: Recorder) -> None:
    """台账那一行不含「🆕 待发」⇒ 拒绝且一次都不调 CLI。这是 Shao Peishen 说「发」的唯一留痕。"""
    (repo / "docs" / "跟进信" / "README-跟进信清单.md").write_text(LEDGER_SENT, encoding="utf-8")
    write_action(repo, "20260917-171000", {"action": "send-followup", "md": "docs/跟进信/HR-3.md"})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-171000")
    assert o["rejected"] is not None and "🆕 待发" in o["rejected"]["reason"]
    assert recorder.calls == []
    assert_no_residue(repo, "20260917-171000")


@pytest.mark.parametrize(
    "queue_text,expect",
    [
        (None, "缺行"),  # 定夺队列文件不存在
        (QUEUE_HEAD, "缺行"),  # 有文件无 G4 行
        (QUEUE_HEAD + G4_ROW.format(status="待答", reply=""), "待答"),
        (QUEUE_HEAD + G4_ROW.format(status="已答", reply="审核通过"), "已答·未放行"),  # 只审核通过、没说「发」
        (QUEUE_HEAD + G4_ROW.format(status="已答", reply="不发"), "已答·未放行"),
        (QUEUE_HEAD + G4_ROW.format(status="作废", reply=""), "作废"),
        (QUEUE_HEAD + G4_ROW.format(status="已答", reply="发").replace("`HR#3`", "`HR#30`"), "缺行"),  # 别的信的放行不带开本信
    ],
)
def test_send_followup_is_rejected_without_g4_release_even_if_ledger_pending(repo: Path, recorder: Recorder, queue_text, expect) -> None:
    """0917AO 在环闸门 G4：台账「🆕 待发」之外，定夺队列须有含本信编号的 G4 行且已答含「发」；否则拒绝、不调 CLI。"""
    q = repo / "docs" / "roadmap" / "定夺队列.md"
    if queue_text is None:
        q.unlink()
    else:
        q.write_text(queue_text, encoding="utf-8")
    write_action(repo, "20260917-171050", {"action": "send-followup", "md": "docs/跟进信/HR-3.md"})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-171050")
    assert o["rejected"] is not None, o
    assert "缺 G4 放行" in o["rejected"]["reason"] and expect in o["rejected"]["reason"]
    assert recorder.calls == []
    assert_no_residue(repo, "20260917-171050")


def test_send_followup_g4_release_alone_is_not_enough_without_ledger_pending(repo: Path, recorder: Recorder) -> None:
    """两闸独立：G4 放行了但台账不是「🆕 待发」照样拒。"""
    (repo / "docs" / "跟进信" / "README-跟进信清单.md").write_text(LEDGER_SENT, encoding="utf-8")
    write_action(repo, "20260917-171060", {"action": "send-followup", "md": "docs/跟进信/HR-3.md"})
    assert ar.main() == 0
    assert outcomes(repo, "20260917-171060")["rejected"] is not None
    assert recorder.calls == []


def test_send_followup_is_rejected_when_ledger_row_is_ambiguous(repo: Path, recorder: Recorder) -> None:
    (repo / "docs" / "跟进信" / "README-跟进信清单.md").write_text(
        LEDGER_PENDING + "| 2026-09-18 | `HR#3` | 重复行 | 🆕 待发 |\n", encoding="utf-8"
    )
    write_action(repo, "20260917-171100", {"action": "send-followup", "md": "docs/跟进信/HR-3.md"})
    assert ar.main() == 0
    assert outcomes(repo, "20260917-171100")["rejected"] is not None
    assert recorder.calls == []


@pytest.mark.parametrize(
    "req",
    [
        {"action": "send-followup", "md": "docs/跟进信/../跟进信/HR-3.md"},
        {"action": "send-followup", "md": "docs/跟进信/HR-3.docx"},
        {"action": "send-followup", "md": "docs/跟进信/HR-3.md", "docx": "docs/跟进信/HR-3.md"},
        {"action": "send-followup", "md": "docs/跟进信/HR-3.md", "docx": "../x.docx"},
        {"action": "send-followup"},
    ],
)
def test_send_followup_path_gate(repo: Path, recorder: Recorder, req: dict) -> None:
    write_action(repo, "20260917-171200", req)
    assert ar.main() == 0
    assert outcomes(repo, "20260917-171200")["rejected"] is not None
    assert recorder.calls == []


def test_send_followup_calls_the_liaison_cli_with_send_flag_and_masks_output(repo: Path, recorder: Recorder) -> None:
    recorder.stdout = "已发送 key=SECRET-TOKEN 完成\n"
    write_action(
        repo, "20260917-171300",
        {"action": "send-followup", "md": "docs/跟进信/HR-3.md", "docx": "docs/跟进信/HR-3.docx"},
    )
    assert ar.main() == 0
    o = outcomes(repo, "20260917-171300")
    assert o["done"] is not None, o
    assert o["done"]["number"] == "HR#3"
    (call,) = recorder.calls
    assert call["cmd"] == [
        str(repo / "tools/liaison/.venv/bin/python"), "-m", "tools.liaison", "send-followup",
        "--md", "docs/跟进信/HR-3.md", "--docx", "docs/跟进信/HR-3.docx", "--send",
    ]
    assert Path(call["cwd"]) == repo
    dumped = json.dumps(o["done"], ensure_ascii=False)
    assert "SECRET-TOKEN" not in dumped and "key=***" in dumped


def test_send_followup_nonzero_rc_is_failed_not_done(repo: Path, recorder: Recorder) -> None:
    recorder.returncode = 3
    write_action(repo, "20260917-171400", {"action": "send-followup", "md": "docs/跟进信/HR-3.md"})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-171400")
    assert o["done"] is None and o["failed"] is not None and o["failed"]["rc"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# kickstart-liaison：只断言 launchctl 调用形状
# ─────────────────────────────────────────────────────────────────────────────


def test_kickstart_calls_launchctl_kickstart_on_the_liaison_label(repo: Path, recorder: Recorder) -> None:
    write_action(repo, "20260917-172000", {"action": "kickstart-liaison", "extra": "ignored"})
    assert ar.main() == 0
    (call,) = recorder.calls
    assert call["cmd"] == ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.zhuopin.hr.liaison"]
    assert outcomes(repo, "20260917-172000")["done"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# install-agent：脚本名闸 ＋ 调用形状
# ─────────────────────────────────────────────────────────────────────────────


def test_install_agent_runs_whitelisted_existing_script_with_repo_venv(repo: Path, recorder: Recorder) -> None:
    write_action(repo, "20260917-173000", {"action": "install-agent", "script": "scripts/install_demo_agent.py"})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-173000")
    assert o["done"] is not None, o
    (call,) = recorder.calls
    assert call["cmd"] == [str(repo / "venv/bin/python"), "scripts/install_demo_agent.py"]
    assert Path(call["cwd"]) == repo
    assert_no_residue(repo, "20260917-173000")


@pytest.mark.parametrize(
    "script",
    [
        "scripts/install_missing.py",            # 不存在
        "scripts/action_request.py",             # 不是 install_ 前缀
        "scripts/install_Demo.py",               # 大写不在 [a-z_]
        "scripts/install_demo_agent.py; rm -rf /",
        "scripts/../scripts/install_demo_agent.py",
        "/abs/scripts/install_demo_agent.py",
        "scripts/install_demo_agent.pyc",
        "scripts/sub/install_demo_agent.py",
        None,
        ["scripts/install_demo_agent.py"],
    ],
)
def test_install_agent_rejects_anything_outside_the_pattern(repo: Path, recorder: Recorder, script) -> None:
    write_action(repo, "20260917-173100", {"action": "install-agent", "script": script})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-173100")
    assert o["done"] is None and o["rejected"] is not None, o
    assert recorder.calls == []


# ─────────────────────────────────────────────────────────────────────────────
# launch-lanes：参数走 lane-launcher 同一白名单，写 launch/ 或 queue/
# ─────────────────────────────────────────────────────────────────────────────


def _hold_fake_process(tmp_path: Path) -> tuple[subprocess.Popen, str]:
    busy = tmp_path / f"fake-busy-{uuid.uuid4().hex}.sh"
    busy.write_text("#!/usr/bin/env bash\nsleep 20\n", encoding="utf-8")
    busy.chmod(0o755)
    holder = subprocess.Popen(["bash", str(busy)])
    for _ in range(100):
        if subprocess.run(["pgrep", "-f", busy.name], capture_output=True).returncode == 0:
            return holder, busy.name
        time.sleep(0.05)
    holder.kill()
    holder.wait()
    pytest.fail("假占位进程没能被 pgrep 看到")


def test_launch_lanes_writes_a_launch_request_when_idle(repo: Path) -> None:
    args = "--full-auto --yes --only 0917AK,0917AL --max-parallel 2 --stagger 30 --budget 25.00"
    write_action(repo, "20260917-174000", {"action": "launch-lanes", "args": args})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-174000")
    assert o["done"] is not None, o
    req = repo / ".claude" / "handoff" / "launch" / "20260917-174000.request"
    assert req.read_text(encoding="utf-8") == args + "\n"
    assert o["done"]["written"] == str(req)
    assert not (repo / ".claude" / "handoff" / "launch" / "queue").exists()


def test_launch_lanes_goes_to_queue_when_run_lanes_is_busy(repo: Path, monkeypatch, tmp_path: Path) -> None:
    holder, pattern = _hold_fake_process(tmp_path)
    monkeypatch.setattr(ar, "PGREP_PATTERN", pattern)
    try:
        write_action(repo, "20260917-174100", {"action": "launch-lanes", "args": "--full-auto --yes"})
        assert ar.main() == 0
    finally:
        holder.kill()
        holder.wait()
    o = outcomes(repo, "20260917-174100")
    assert o["done"] is not None, o
    queued = repo / ".claude" / "handoff" / "launch" / "queue" / "20260917-174100.request"
    assert queued.read_text(encoding="utf-8") == "--full-auto --yes\n"
    assert o["done"]["written"] == str(queued)
    assert not (repo / ".claude" / "handoff" / "launch" / "20260917-174100.request").exists()


@pytest.mark.parametrize(
    "args",
    [
        "--full-auto --yes; rm -rf /",
        "--full-auto --yes --model claude-opus-5",
        "--full-auto --chain",
        "--dry-run",
        "--only 0909C; touch /tmp/pwned",
        "--only",
        "--max-parallel abc",
        "--budget",
        "--plan /etc/passwd",
        "",
        "   ",
        None,
        ["--full-auto", "--yes"],
        "--full-auto\n--dry-run",
    ],
)
def test_launch_lanes_rejects_args_outside_the_launcher_whitelist(repo: Path, args) -> None:
    write_action(repo, "20260917-174200", {"action": "launch-lanes", "args": args})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-174200")
    assert o["done"] is None and o["rejected"] is not None, o
    launch = repo / ".claude" / "handoff" / "launch"
    assert not launch.exists() or list(launch.rglob("*.request")) == []


# ─────────────────────────────────────────────────────────────────────────────
# launch-queue-drain：无 run-lanes 时把最早一条移回 launch/
# ─────────────────────────────────────────────────────────────────────────────


def _queue(repo: Path, name: str, line: str = "--full-auto --yes\n") -> Path:
    q = repo / ".claude" / "handoff" / "launch" / "queue"
    q.mkdir(parents=True, exist_ok=True)
    p = q / f"{name}.request"
    p.write_text(line, encoding="utf-8")
    return p


def test_drain_moves_only_the_earliest_queued_request_back_to_launch(repo: Path) -> None:
    _queue(repo, "20260917-150000", "--full-auto --yes --only B\n")
    _queue(repo, "20260917-140000", "--full-auto --yes --only A\n")
    write_action(repo, "20260917-175000", {"action": "launch-queue-drain"})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-175000")
    assert o["done"] is not None, o
    launch = repo / ".claude" / "handoff" / "launch"
    moved = launch / "20260917-140000.request"
    assert moved.read_text(encoding="utf-8") == "--full-auto --yes --only A\n"
    assert o["done"]["drained"] == str(moved)
    assert (launch / "queue" / "20260917-150000.request").exists(), "一次只出一条，其余等下一个事件"
    assert not (launch / "queue" / "20260917-140000.request").exists()


def test_drain_does_nothing_while_run_lanes_is_busy(repo: Path, monkeypatch, tmp_path: Path) -> None:
    holder, pattern = _hold_fake_process(tmp_path)
    monkeypatch.setattr(ar, "PGREP_PATTERN", pattern)
    try:
        _queue(repo, "20260917-140000")
        write_action(repo, "20260917-175100", {"action": "launch-queue-drain"})
        assert ar.main() == 0
    finally:
        holder.kill()
        holder.wait()
    o = outcomes(repo, "20260917-175100")
    assert o["done"] is not None and o["done"]["drained"] is None, o
    assert (repo / ".claude" / "handoff" / "launch" / "queue" / "20260917-140000.request").exists()
    assert not (repo / ".claude" / "handoff" / "launch" / "20260917-140000.request").exists()


def test_drain_on_empty_queue_is_a_noop_done(repo: Path) -> None:
    write_action(repo, "20260917-175200", {"action": "launch-queue-drain"})
    assert ar.main() == 0
    o = outcomes(repo, "20260917-175200")
    assert o["done"] is not None and o["done"]["drained"] is None


def test_drain_is_callable_directly_for_the_scheduler(repo: Path) -> None:
    """R2 调度器处理 lanes-done-* 事件时直接调 `drain_launch_queue()`（乙泳道接），不必写 .action。"""
    _queue(repo, "20260917-140000")
    assert ar.drain_launch_queue() == repo / ".claude" / "handoff" / "launch" / "20260917-140000.request"
    assert ar.drain_launch_queue() is None


def test_drain_cli_subcommand(repo: Path) -> None:
    _queue(repo, "20260917-140000")
    env = dict(os.environ, COMMIT_LAUNCHER_REPO=str(repo), LANE_LAUNCHER_PGREP_PATTERN=NEVER_MATCHES,
               ACTION_REQUEST_DRAIN_WAIT="0")
    r = subprocess.run(
        [os.sys.executable, str(ROOT / "scripts" / "action_request.py"), "launch-queue-drain"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (repo / ".claude" / "handoff" / "launch" / "20260917-140000.request").exists()
    assert "20260917-140000.request" in r.stdout


# ─────────────────────────────────────────────────────────────────────────────
# 薄壳接线：commit-launcher.sh 先调本脚本（Cowork 直加的那一行——删掉即红）
# ─────────────────────────────────────────────────────────────────────────────


def test_commit_launcher_shell_processes_action_files(tmp_path: Path) -> None:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"], check=True)
    (r / ".claude" / "handoff" / "commit").mkdir(parents=True)
    write_action(r, "20260917-176000", {"action": "definitely-not-whitelisted"})
    env = dict(os.environ, COMMIT_LAUNCHER_REPO=str(r), LANE_LAUNCHER_PGREP_PATTERN=NEVER_MATCHES)
    proc = subprocess.run(["bash", str(COMMIT_LAUNCHER_SH)], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outcomes(r, "20260917-176000")["rejected"] is not None, proc.stdout + proc.stderr
    assert_no_residue(r, "20260917-176000")
