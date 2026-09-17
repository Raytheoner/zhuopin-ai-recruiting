"""动作请求通道（2026-09-17，Cowork·0917D 应 Shao Peishen「凡能直接驱动的都不要让人开终端」；
0917AK 补测试并固化，见 tests/test_action_request.py）。

复用已安装的 commit-launcher（launchd WatchPaths 监视 `.claude/handoff/commit/`）：
Cowork 写 `<时间戳>.action`（JSON），`commit-launcher.sh` 先调本脚本处理动作，再照旧处理 `.request`。
⛔ 请求内容一律当数据，⛔ 不当命令；只执行下面白名单里写死的动作与参数形状。

白名单动作：
- `send-followup`：`{"action":"send-followup","md":"docs/跟进信/<文件>.md","docx":"docs/跟进信/<文件>.docx"}`
  闸：md/docx 必须在 `docs/跟进信/` 下且存在；信件抬头编号在台账那一行的发送状态必须含「🆕 待发」
  （Cowork 只在 Shao Peishen 明确回「发」之后才把状态改成 🆕 待发——这就是授权留痕）。
  执行：`tools/liaison/.venv/bin/python -m tools.liaison send-followup --md … --docx … --send`（已测 CLI，负责回填台账）
- `kickstart-liaison`：`{"action":"kickstart-liaison"}` → `launchctl kickstart -k gui/<uid>/com.zhuopin.hr.liaison`
- `install-agent`（0917AK）：`{"action":"install-agent","script":"scripts/install_<名>.py"}`
  闸：`script` 只允许 `scripts/install_[a-z_]+\\.py` 且文件存在。执行：`venv/bin/python <script>`（cwd=仓库根）。
  这是 R2 调度器等 LaunchAgent 的安装口——安装器自己再做 launchctl bootstrap，不需要人开终端。
- `launch-lanes`（0917AK）：`{"action":"launch-lanes","args":"--full-auto --yes --only 0917AK"}`
  闸：`args` 过 lane-launcher.sh **同一份**白名单（见 `validate_lane_args`）。无 run-lanes 在跑 ⇒ 写
  `.claude/handoff/launch/<同名>.request` 触发发车；在跑 ⇒ 直接写 `launch/queue/<同名>.request` 排队。
- `launch-queue-drain`（0917AK，R1 出队）：`{"action":"launch-queue-drain"}`，无参数。无 run-lanes 在跑 ⇒
  把 `launch/queue/` 里最早一条移回 `launch/`（WatchPaths 由此触发发车）；在跑 ⇒ 不动，`drained=null`。
  R2 调度器处理 `events/lanes-done-*` 时直接调 `drain_launch_queue()` 或 CLI `action_request.py launch-queue-drain`。

结果：`<同名>.done`（rc=0）或 `<同名>.failed`／`<同名>.rejected`，内含输出末 60 行（`key=` 后内容打码）。
认领件 `<同名>.action-claiming` 处理完改名 `<同名>.processed`（09-17 真机曾留着 claiming 与 .done 并存）。

单测注入口：`configure(repo)` 把所有路径指到临时仓库；`LANE_LAUNCHER_PGREP_PATTERN` 与
`ACTION_REQUEST_DRAIN_WAIT` 两个环境变量与 lane-launcher.sh 同名同义。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_REPO = Path(os.environ.get("COMMIT_LAUNCHER_REPO", "/Users/paulshao/Projects/HumanResource"))
# 与 lane-launcher.sh 同一并发判据。⛔ 不要用不带 .sh 的 `run-lanes`——会命中日志路径等无关进程。
PGREP_PATTERN = os.environ.get("LANE_LAUNCHER_PGREP_PATTERN", r"run-lanes.*\.sh")
# 出队前最多等 run-lanes 退出这么多秒：事件文件是它退出前几毫秒写的，紧跟着出队会看到
# 一个正在收尾的进程。--chain 的下一轮同 PID 续跑，等满仍在跑就留队等下个事件。
DRAIN_WAIT_SECONDS = float(os.environ.get("ACTION_REQUEST_DRAIN_WAIT", "20"))

REPO = HANDOFF = LEDGER = LAUNCH_DIR = QUEUE_DIR = Path()


def configure(repo: Path) -> None:
    """把所有路径钉到 `repo`。生产由 `COMMIT_LAUNCHER_REPO`／默认值决定；单测指到 tmp 仓库。"""
    global REPO, HANDOFF, LEDGER, LAUNCH_DIR, QUEUE_DIR
    REPO = Path(repo)
    HANDOFF = REPO / ".claude/handoff/commit"
    LEDGER = REPO / "docs/跟进信/README-跟进信清单.md"
    LAUNCH_DIR = REPO / ".claude/handoff/launch"
    QUEUE_DIR = LAUNCH_DIR / "queue"


configure(DEFAULT_REPO)

_LETTER_PATH = re.compile(r"^docs/跟进信/[^/]+\.(md|docx)$")
_NUMBER = re.compile(r"^#\s+(?P<n>[^\s#]+#\d+)\s*(?:·|$)", re.M)
_INSTALL_SCRIPT = re.compile(r"^scripts/install_[a-z_]+\.py$")
_ONLY_VALUE = re.compile(r"^[A-Za-z0-9_.,-]+$")
_NUMERIC = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] [action] {msg}"
    HANDOFF.mkdir(parents=True, exist_ok=True)
    with (HANDOFF / "launchd.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def mask(text: str) -> str:
    """`key=<取值>` ⇒ `key=***`，带单双引号的取值一并打掉（原版漏了引号形态）。"""
    return re.sub(r"(key=)[\"']?[^\s&\"']*[\"']?", r"\1***", text)


def write(base: Path, suffix: str, payload: dict) -> None:
    payload = {"at": datetime.now().isoformat(timespec="seconds"), **payload}
    (base.parent / f"{base.name}.{suffix}").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tail(text: str, n: int) -> list[str]:
    return mask(text).splitlines()[-n:]


# ── 并发判据 ─────────────────────────────────────────────────────────────────


def run_lanes_is_running() -> bool:
    return subprocess.run(["pgrep", "-f", PGREP_PATTERN], capture_output=True).returncode == 0


def _wait_until_run_lanes_exits(max_seconds: float) -> bool:
    """等到没有 run-lanes 在跑 ⇒ True；等满仍在跑 ⇒ False。"""
    deadline = time.monotonic() + max_seconds
    while True:
        if not run_lanes_is_running():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))


# ── send-followup ────────────────────────────────────────────────────────────


def check_letter_path(p: object) -> Path | None:
    if not isinstance(p, str) or ".." in p or not _LETTER_PATH.match(p):
        return None
    full = REPO / p
    return full if full.is_file() else None


def handle_send_followup(req: dict) -> tuple[str, dict]:
    md = check_letter_path(req.get("md"))
    if md is None or md.suffix != ".md":
        return "rejected", {"reason": "md 路径不合法或不存在（须 docs/跟进信/<文件>.md）"}
    cmd_docx: list[str] = []
    if req.get("docx") is not None:
        dx = check_letter_path(req.get("docx"))
        if dx is None or dx.suffix != ".docx":
            return "rejected", {"reason": "docx 路径不合法或不存在"}
        cmd_docx = ["--docx", str(dx.relative_to(REPO))]
    m = _NUMBER.search(md.read_text(encoding="utf-8"))
    if not m:
        return "rejected", {"reason": "信件抬头找不到编号"}
    number = m.group("n")
    rows = [l for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.lstrip().startswith("|") and f"`{number}" in l]
    if len(rows) != 1:
        return "rejected", {"reason": f"台账里 {number} 行数={len(rows)}（须恰好 1 行）"}
    if "🆕 待发" not in rows[0].split("|")[-2]:
        return "rejected", {"reason": f"{number} 发送状态不是「🆕 待发」（未获授权或已发）"}
    py = REPO / "tools/liaison/.venv/bin/python"
    cmd = [str(py), "-m", "tools.liaison", "send-followup", "--md", str(md.relative_to(REPO)), *cmd_docx, "--send"]
    env = {**os.environ, "PYTHONPATH": "."}
    r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=180)
    return ("done" if r.returncode == 0 else "failed"), {"number": number, "rc": r.returncode, "output_tail": _tail(r.stdout + r.stderr, 60)}


# ── kickstart-liaison ────────────────────────────────────────────────────────


def handle_kickstart(_req: dict) -> tuple[str, dict]:
    r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.zhuopin.hr.liaison"], capture_output=True, text=True, timeout=60)
    return ("done" if r.returncode == 0 else "failed"), {"rc": r.returncode, "output_tail": _tail(r.stdout + r.stderr, 20)}


# ── install-agent ────────────────────────────────────────────────────────────


def check_install_script(p: object) -> Path | None:
    if not isinstance(p, str) or ".." in p or not _INSTALL_SCRIPT.match(p):
        return None
    full = REPO / p
    return full if full.is_file() else None


def handle_install_agent(req: dict) -> tuple[str, dict]:
    script = check_install_script(req.get("script"))
    if script is None:
        return "rejected", {"reason": "script 不合法或不存在（须 scripts/install_[a-z_]+.py）"}
    rel = str(script.relative_to(REPO))
    cmd = [str(REPO / "venv/bin/python"), rel]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=300)
    return ("done" if r.returncode == 0 else "failed"), {"script": rel, "rc": r.returncode, "output_tail": _tail(r.stdout + r.stderr, 60)}


# ── launch-lanes ─────────────────────────────────────────────────────────────


def validate_lane_args(line: object) -> tuple[str | None, str | None]:
    """lane-launcher.sh 的参数白名单，逐字对齐：(规范化后的一行, None) 或 (None, 拒绝原因)。

    只允许 --full-auto | --yes | --only <编号> | --max-parallel N | --stagger N | --budget N。
    ⛔ --dry-run / --model / --chain / --plan 一律不放——理由见 lane-launcher.sh 同段注释。
    半截参数整条拒绝，⛔ 不做「跳过这个继续」。
    """
    if not isinstance(line, str):
        return None, "args 必须是一行字符串"
    if "\n" in line or "\r" in line:
        return None, "args 只允许一行"
    toks = line.split()
    if not toks:
        return None, "args 为空，没有参数可传"
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok in ("--full-auto", "--yes"):
            i += 1
        elif tok == "--only":
            val = toks[i + 1] if i + 1 < len(toks) else ""
            if not val:
                return None, "--only 缺少取值"
            if not _ONLY_VALUE.match(val):
                return None, f"--only 取值含非法字符：{val}"
            i += 2
        elif tok in ("--max-parallel", "--stagger", "--budget"):
            val = toks[i + 1] if i + 1 < len(toks) else ""
            if not val:
                return None, f"{tok} 缺少取值"
            if not _NUMERIC.match(val):
                return None, f"{tok} 取值不是数字：{val}"
            i += 2
        else:
            return None, f"不在白名单里的 token：{tok}"
    return " ".join(toks), None


def handle_launch_lanes(req: dict, base: Path) -> tuple[str, dict]:
    line, why = validate_lane_args(req.get("args"))
    if line is None:
        return "rejected", {"reason": why}
    target_dir = QUEUE_DIR if run_lanes_is_running() else LAUNCH_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{base.name}.request"
    target.write_text(line + "\n", encoding="utf-8")
    return "done", {"written": str(target), "queued": target_dir == QUEUE_DIR, "args": line}


# ── launch-queue-drain ───────────────────────────────────────────────────────


def drain_launch_queue() -> Path | None:
    """无 run-lanes 在跑时把 queue/ 里最早一条移回 launch/，返回新路径；没动返回 None。

    移回去的那一下会触发 WatchPaths → lane-launcher.sh 重新走一遍白名单与并发判据，
    所以这里⛔ 不做参数校验、不起进程——只搬文件。一次只搬一条：launcher 一次也只发一条，
    多搬只会让第二条又被它排回队里。
    """
    if not QUEUE_DIR.is_dir():
        return None
    queued = sorted(QUEUE_DIR.glob("*.request"))
    if not queued:
        return None
    if not _wait_until_run_lanes_exits(DRAIN_WAIT_SECONDS):
        return None
    src = queued[0]
    dst = LAUNCH_DIR / src.name
    src.rename(dst)
    return dst


def handle_launch_queue_drain(_req: dict) -> tuple[str, dict]:
    remaining = sorted(p.name for p in QUEUE_DIR.glob("*.request")) if QUEUE_DIR.is_dir() else []
    moved = drain_launch_queue()
    if moved is None:
        reason = "队列为空" if not remaining else "run-lanes 仍在跑，留队等下个 lanes-done 事件"
        return "done", {"drained": None, "reason": reason, "queue": remaining}
    return "done", {"drained": str(moved), "queue": [n for n in remaining if n != moved.name]}


HANDLERS = {
    "send-followup": lambda req, base: handle_send_followup(req),
    "kickstart-liaison": lambda req, base: handle_kickstart(req),
    "install-agent": lambda req, base: handle_install_agent(req),
    "launch-lanes": handle_launch_lanes,
    "launch-queue-drain": lambda req, base: handle_launch_queue_drain(req),
}


def main() -> int:
    HANDOFF.mkdir(parents=True, exist_ok=True)
    for act in sorted(HANDOFF.glob("*.action")):
        base = act.with_suffix("")
        claiming = base.with_name(base.name + ".action-claiming")
        try:
            act.rename(claiming)
        except FileNotFoundError:
            continue
        raw = claiming.read_text(encoding="utf-8", errors="replace")
        try:
            req = json.loads(raw)
            handler = HANDLERS.get(req.get("action")) if isinstance(req, dict) else None
            if handler is None:
                outcome, payload = "rejected", {"reason": "动作不在白名单"}
            else:
                outcome, payload = handler(req, base)
        except Exception as exc:  # noqa: BLE001 — 结果必须落文件，不能静默
            outcome, payload = "failed", {"reason": f"{type(exc).__name__}: {exc}"}
        write(base, outcome, {"request": raw, **payload})
        claiming.rename(base.with_name(base.name + ".processed"))
        log(f"{outcome}：{base.name} {payload.get('reason') or payload.get('rc') or payload.get('drained') or payload.get('written') or ''}")
    return 0


def _cli(argv: list[str]) -> int:
    if argv == ["launch-queue-drain"]:
        moved = drain_launch_queue()
        print(json.dumps({"drained": str(moved) if moved else None}, ensure_ascii=False))
        return 0
    if argv:
        print(f"用法：action_request.py [launch-queue-drain]（不带参数＝处理 {HANDOFF} 下的 *.action）", file=sys.stderr)
        return 64
    return main()


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
