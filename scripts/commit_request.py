#!/usr/bin/env python3
"""Cowork 文档提交请求通道（2026-09-17，0917X）—— 请求文件 → launchd → 本脚本 add/commit/push。

为什么存在：
    Cowork 的 bash 在隔离 VM 里，碰 `.git/` 只能写不能删，连只读的 `git status` 都会留下
    删不掉的 `index.lock`（CLAUDE.md「git 相关只能在 CC」）。于是它写的文档必须等「下一条
    泳道顺带提交」，已多次造成未提交态滞留与 ff 合并被挡（09-17 `0917K`）。
    发车已有一条不经分类器的路：`.claude/handoff/launch/<ts>.request` → launchd WatchPaths →
    `docs/openers/lane-launcher.sh`。本通道照样做一遍，只是动作换成 git 提交。

为什么是 Python ＋ 薄壳（而不是纯 bash）：
    请求是 JSON、要做路径白名单与逐条校验、要解析 `git status -z` 的 NUL 分隔输出，
    这些在 macOS 自带的 bash 3.2 里没有 jq 都得手写解析器，且 UTF-8 locale 下静默出错
    （见 run-lanes.sh「locale 钉死」）。Python 标准库全都有，且闸门函数可以直接 import
    进 pytest 断言。薄壳 `docs/openers/commit-launcher.sh` 只做三件事：找 python3、cd 仓库根、
    exec 本脚本 —— 与 lane-launcher 一样由 launchd 用 /bin/bash 起。

协议（Cowork 侧）：
    ① 用 Write 工具写 `.claude/handoff/commit/<时间戳>.request`，内容是一个 JSON 对象：
         {"message": "docs(x): …", "paths": ["docs/…", "openspec/changes/<名>/tasks.md"], "push": true}
       `paths` 只能是**文件**（不是目录、不带通配），相对仓库根；`push` 省略即 false。
       `emit_event` 省略即 true：提交含 `docs/roadmap/定夺队列.md` 时写 `.claude/handoff/events/decision-<ts>`
       唤醒调度器（0917AM）；调度器自己的提交带 `"emit_event": false`，免得自己唤醒自己。
    ② 等 `<同名>.done`（含 commit hash）／`.rejected`（含原因）／`.deferred`（index.lock 在用）。
       `.deferred` **不会自动重试**：要重发就写一个新的 `.request`。

流程：
    取最早的一个 `*.request` → 原子认领（改名 `.claiming`）→ 解析 JSON → 逐条过白名单 →
    逐条核对确实在工作区 diff 里 → 跑 `tests/test_doc_size_budget.py`（有 venv/ 用 venv/）→
    等 index.lock（5 秒 × 5，⛔ 不删）→ `git add -- <路径…>` → `git commit --only -m <msg> -- <路径…>`
    → 可选 `git push`（被拒 ⇒ `pull --rebase --autostash` 后重试 ≤ 3）→ 写 `.done`。

路径白名单（机器闸，⛔ 不在这里放宽；要放宽先改 CLAUDE.md 的并发协议再议）：
    允许  docs/**（排除 docs/openers/run-lanes.sh、lane-launcher.sh、commit-launcher.sh）
          openspec/changes/<名>/tasks.md
    拒绝  tools/ app/ scripts/ tests/ .claude/ CLAUDE.md data/ .env*、含 `..`、绝对路径、
          `-A`／`.`／任何以 `-` 开头的 token、目录、通配符、以及不在工作区 diff 里的路径。
    列表里混一个非法的就整条拒绝，⛔ 不做「跳过这个继续」。

`git commit --only -- <路径>` 而不是裸 `git commit`：别的 session 可能已经往暂存区放了东西，
--only 保证提交里只有列出的路径、暂存区里别人的内容原样留着。

⛔ 本脚本不 source .env、不读密钥、不 eval 请求文件里的任何内容；push 只推当前分支到 origin。

单测注入口（生产由 launchd 直接调用、不设）：
    COMMIT_LAUNCHER_REPO               仓库根（默认 /Users/paulshao/Projects/HumanResource）
    COMMIT_LAUNCHER_LOCK_WAIT_SECONDS  index.lock 每次等待秒数（默认 5）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

DEFAULT_REPO = "/Users/paulshao/Projects/HumanResource"
HANDOFF_SUBDIR = Path(".claude") / "handoff" / "commit"

OUTCOME_DONE = "done"
OUTCOME_REJECTED = "rejected"
OUTCOME_DEFERRED = "deferred"

LOCK_RETRIES = 5
PUSH_RETRIES = 3

# 白名单里明确挖掉的三个文件：它们在跑／在用，且本身就是这条通道的执行器。
DOCS_EXCLUDED = frozenset({
    "docs/openers/run-lanes.sh",
    "docs/openers/lane-launcher.sh",
    "docs/openers/commit-launcher.sh",
    "docs/openers/handoff-relay.sh",
})
DOC_SIZE_TEST = Path("tests") / "test_doc_size_budget.py"

# 定夺答复事件（R2 调度器唤醒，0917AM）：一次提交里含定夺队列 ⇒ 写一个事件文件，launchd WatchPaths
# 由此起调度器去解阻塞。只在 .done 之后写：commit 没成就没有「答复落档」这件事。
DECISION_QUEUE_PATH = "docs/roadmap/定夺队列.md"
EVENTS_SUBDIR = Path(".claude") / "handoff" / "events"


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：白名单
# ─────────────────────────────────────────────────────────────────────────────


def validate_path(raw: str) -> str | None:
    """路径合法返回 None，否则返回拒绝原因（含原路径）。只看字符串，不碰文件系统。"""
    if not isinstance(raw, str) or raw == "":
        return f"路径为空或不是字符串：{raw!r}"
    if raw.startswith("-"):
        return f"路径以 - 开头（像参数）：{raw}"
    if raw.startswith("/") or raw.startswith("~"):
        return f"绝对路径不允许：{raw}"
    if ".." in raw.split("/"):
        return f"路径含 ..：{raw}"
    if any(ch in raw for ch in "*?[]") or "\0" in raw or "\n" in raw:
        return f"路径含通配或控制字符：{raw}"
    if raw.endswith("/") or raw == ".":
        return f"只接受文件路径，不接受目录：{raw}"
    normalized = str(PurePosixPath(raw))
    if normalized != raw:
        return f"路径未规范化（有 ./ 或重复斜杠）：{raw}"

    parts = normalized.split("/")
    if parts[0] == "docs" and len(parts) >= 2:
        if normalized in DOCS_EXCLUDED:
            return f"执行器脚本不经本通道提交：{raw}"
        return None
    if (
        parts[0] == "openspec"
        and len(parts) == 4
        and parts[1] == "changes"
        and parts[3] == "tasks.md"
    ):
        return None
    return f"不在白名单（docs/** 或 openspec/changes/<名>/tasks.md）内：{raw}"


def parse_request(text: str) -> tuple[dict | None, str | None]:
    """解析并做结构校验。返回 (request, None) 或 (None, 原因)。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"请求不是合法 JSON：{exc}"
    if not isinstance(data, dict):
        return None, "请求顶层不是 JSON 对象"
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return None, "message 缺失或为空"
    paths = data.get("paths")
    if not isinstance(paths, list) or not paths:
        return None, "paths 缺失、为空或不是列表"
    push = data.get("push", False)
    if not isinstance(push, bool):
        return None, f"push 必须是布尔值，收到 {push!r}"
    emit_event = data.get("emit_event", True)
    if not isinstance(emit_event, bool):
        return None, f"emit_event 必须是布尔值，收到 {emit_event!r}"
    for p in paths:
        reason = validate_path(p)
        if reason:
            return None, reason
    if len(set(paths)) != len(paths):
        return None, "paths 里有重复项"
    return {"message": message, "paths": list(paths), "push": push, "emit_event": emit_event}, None


# ─────────────────────────────────────────────────────────────────────────────
# git 包装
# ─────────────────────────────────────────────────────────────────────────────


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
    )


def changed_paths(repo: Path) -> set[str]:
    """工作区里有改动（含未跟踪、已暂存、已删除）的文件集合。

    用 `-z` 拿 NUL 分隔的原始路径：不带 -z 时非 ASCII 文件名会被 git 加引号转义
    （`docs/session接力.md` 会变成 "docs/session\\346\\216\\245\\345\\212\\233.md"）。
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=str(repo), capture_output=True, check=True,
    ).stdout.decode("utf-8", errors="surrogateescape")
    fields = out.split("\0")
    result: set[str] = set()
    i = 0
    while i < len(fields):
        entry = fields[i]
        if not entry:
            i += 1
            continue
        xy, path = entry[:2], entry[3:]
        result.add(path)
        # 重命名／复制条目后面跟一个「原路径」字段，跳过它
        if xy[0] in "RC" or xy[1] in "RC":
            i += 1
        i += 1
    return result


def index_lock_path(repo: Path) -> Path:
    """worktree 下 `.git` 是文件，index 在真实 gitdir 里；让 git 自己算。"""
    out = git(repo, "rev-parse", "--git-path", "index.lock").stdout.strip()
    p = Path(out)
    return p if p.is_absolute() else repo / p


def wait_index_lock(repo: Path, wait_seconds: float) -> Path | None:
    """锁在 ⇒ 等 wait_seconds × LOCK_RETRIES；仍在 ⇒ 返回锁路径（调用方写 .deferred）。⛔ 绝不删。"""
    lock = index_lock_path(repo)
    for attempt in range(LOCK_RETRIES):
        if not lock.exists():
            return None
        if attempt < LOCK_RETRIES - 1:
            time.sleep(wait_seconds)
    return lock if lock.exists() else None


def find_venv_python(repo: Path) -> Path | None:
    """本仓库的 venv 叫 `venv/`（pre-commit 钩子也指它），`.venv/` 只是兜底。"""
    for name in ("venv", ".venv"):
        candidate = repo / name / "bin" / "python"
        if candidate.exists():
            return candidate
    return None


def run_doc_size_test(repo: Path) -> tuple[bool, str]:
    """跑体积闸。返回 (通过?, 说明)。没有测试文件或没有能跑 pytest 的解释器 ⇒ 跳过（算通过，写明）。"""
    test_file = repo / DOC_SIZE_TEST
    if not test_file.exists():
        return True, f"跳过：{DOC_SIZE_TEST} 不存在"
    venv_python = find_venv_python(repo)
    if venv_python is not None:
        interpreter = str(venv_python)
    else:
        probe = subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True)
        if probe.returncode != 0:
            return True, "跳过：没有 venv/，当前解释器也没有 pytest"
        interpreter = sys.executable
    proc = subprocess.run(
        [interpreter, "-m", "pytest", str(DOC_SIZE_TEST), "-q", "-p", "no:cacheprovider"],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
    )
    tail = "\n".join(proc.stdout.splitlines()[-15:])
    if proc.returncode != 0:
        return False, f"{DOC_SIZE_TEST} 红（rc={proc.returncode}）：\n{tail}\n{proc.stderr[-2000:]}"
    return True, f"通过（{interpreter}）：{proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ''}"


def push_with_retry(repo: Path, branch: str) -> tuple[bool, str]:
    log: list[str] = []
    for attempt in range(1, PUSH_RETRIES + 1):
        proc = git(repo, "push", "origin", f"HEAD:refs/heads/{branch}")
        if proc.returncode == 0:
            log.append(f"push 第 {attempt} 次成功")
            return True, "\n".join(log)
        log.append(f"push 第 {attempt} 次被拒：{proc.stderr.strip()[-500:]}")
        if attempt == PUSH_RETRIES:
            break
        # ⛔ 只在 push 被拒后才 pull；--autostash 带着别人的未提交改动一起 rebase
        pull = git(repo, "pull", "--rebase", "--autostash", "origin", branch)
        if pull.returncode != 0:
            log.append(f"pull --rebase --autostash 失败：{pull.stderr.strip()[-500:]}")
            # rebase 半途会留下 rebase 状态，必须立刻收回来，⛔ 不能把仓库留在 rebase 中
            git(repo, "rebase", "--abort")
            return False, "\n".join(log)
    return False, "\n".join(log)


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def write_decision_event(repo: Path, paths: list[str], commit_hash: str) -> Path | None:
    """提交里含 `docs/roadmap/定夺队列.md` ⇒ 写 `.claude/handoff/events/decision-<ts>`，返回其路径；否则 None。

    事件文件只用文件名触发（run-lanes 的 lanes-done-* 同款空文件），内容只记 commit hash 供人看，
    ⛔ 调度器不把它当命令。写失败不影响提交结果（每日 09:00 兜底会补处理）。
    """
    if DECISION_QUEUE_PATH not in paths:
        return None
    events = repo / EVENTS_SUBDIR
    try:
        events.mkdir(parents=True, exist_ok=True)
        event = events / f"decision-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        event.write_text(f"commit={commit_hash}\n", encoding="utf-8")
    except OSError as exc:
        log(f"⚠ 写定夺事件失败（不影响提交）：{exc}")
        return None
    return event


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


class Outcome:
    def __init__(self, base: Path, claiming: Path, raw_request: str):
        self.base = base
        self.claiming = claiming
        self.raw_request = raw_request

    def _write(self, suffix: str, payload: dict) -> None:
        payload = {"outcome": suffix, "at": now(), "request_raw": self.raw_request, **payload}
        target = self.base.with_name(self.base.name + f".{suffix}")
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.claiming.unlink(missing_ok=True)

    def reject(self, reason: str, **extra) -> int:
        log(f"✗ 拒绝：{reason.splitlines()[0]}")
        self._write(OUTCOME_REJECTED, {"reason": reason, **extra})
        return 0

    def defer(self, reason: str, **extra) -> int:
        log(f"⏸ 推迟：{reason}")
        self._write(OUTCOME_DEFERRED, {"reason": reason, **extra})
        return 0

    def done(self, **payload) -> int:
        log(f"✓ 完成：commit={payload.get('commit')} pushed={payload.get('pushed')}")
        self._write(OUTCOME_DONE, payload)
        return 0


def main() -> int:
    repo = Path(os.environ.get("COMMIT_LAUNCHER_REPO", DEFAULT_REPO))
    wait_seconds = float(os.environ.get("COMMIT_LAUNCHER_LOCK_WAIT_SECONDS", "5"))
    handoff = repo / HANDOFF_SUBDIR
    handoff.mkdir(parents=True, exist_ok=True)

    # 一次只处理最早的一个：自己写 .done 会再触发 WatchPaths，剩下的下一轮处理。
    requests = sorted(handoff.glob("*.request"))
    if not requests:
        return 0
    req = requests[0]
    base = req.with_suffix("")
    claiming = base.with_name(base.name + ".claiming")
    # 原子认领：rename 在源已被别的实例搬走时抛错，抢输的直接退出，⛔ 不重试
    try:
        req.rename(claiming)
    except FileNotFoundError:
        log(f"请求 {req.name} 已被另一个实例认领，退出")
        return 0
    raw = claiming.read_text(encoding="utf-8", errors="replace")
    outcome = Outcome(base, claiming, raw)

    request, reason = parse_request(raw)
    if request is None:
        return outcome.reject(reason or "未知原因")

    if git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return outcome.reject(f"{repo} 不是 git 工作区")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        return outcome.reject("仓库处于 detached HEAD，不知道提交到哪个分支")

    changed = changed_paths(repo)
    missing = [p for p in request["paths"] if p not in changed]
    if missing:
        return outcome.reject("以下路径在工作区里没有改动（写错了文件名？）：" + "、".join(missing))

    ok, note = run_doc_size_test(repo)
    if not ok:
        return outcome.reject(note)

    lock = wait_index_lock(repo, wait_seconds)
    if lock is not None:
        return outcome.defer(f"{lock} 仍存在（等了 {LOCK_RETRIES} × {wait_seconds}s），另一个 session 正在用；⛔ 未删")

    add = git(repo, "add", "--", *request["paths"])
    if add.returncode != 0:
        if "index.lock" in add.stderr:
            return outcome.defer(f"git add 撞上 index.lock：{add.stderr.strip()[-300:]}")
        return outcome.reject(f"git add 失败：{add.stderr.strip()[-500:]}")

    commit = git(repo, "commit", "--only", "-m", request["message"], "--", *request["paths"])
    if commit.returncode != 0:
        # 把刚暂存的撤回来，别给别的 session 留一份半截暂存
        git(repo, "reset", "-q", "--", *request["paths"])
        if "index.lock" in commit.stderr:
            return outcome.defer(f"git commit 撞上 index.lock：{commit.stderr.strip()[-300:]}")
        return outcome.reject(f"git commit 失败：{(commit.stderr + commit.stdout).strip()[-800:]}")
    commit_hash = git(repo, "rev-parse", "HEAD").stdout.strip()

    pushed = False
    push_note = "未要求 push"
    if request["push"]:
        pushed, push_note = push_with_retry(repo, branch)

    payload = {
        "commit": commit_hash,
        "branch": branch,
        "paths": request["paths"],
        "message": request["message"],
        "pushed": pushed,
        "push_note": push_note,
        "doc_size_test": note,
    }
    if request["push"] and not pushed:
        # 本地 commit 已经在了，这不是失败；但 push 没成要让 Cowork 看见
        payload["warning"] = "本地已提交但 push 未成功，等下一条泳道推或人工 push"
    event = write_decision_event(repo, request["paths"], commit_hash) if request["emit_event"] else None
    if event is not None:
        payload["decision_event"] = str(event.relative_to(repo))
    return outcome.done(**payload)


if __name__ == "__main__":
    sys.exit(main())
