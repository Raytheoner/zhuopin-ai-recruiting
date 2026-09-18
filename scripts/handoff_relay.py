#!/usr/bin/env python3
"""受限会话投递中继（2026-09-18，0918H）—— `handoff-inbox/` → `.claude/handoff/**`。

为什么存在：
    2026-09-18 Cowork·`0918G` 实核：远端文件工具对 `.claude/**` **只读**——
    `device_commit_files` 写被直接拒，错误原文 `Writing to .claude is not permitted
    via remote tools.`。这不是缺 `device_bash` 才有的问题：`.claude/` 是 Claude
    自身数据目录，受限会话一律写不进去。而提交／发车／事件三条通道的投递口全在
    `.claude/` 下 ⇒ 没有本机 shell 的会话一件也投不进来，只能靠人工粘贴破局。
    本脚本把投递口搬到仓库根 `handoff-inbox/`（受限会话的 Write 工具能碰），
    由 launchd 盯着它，搬进真正的通道目录，去掉文件名前缀、沿用各通道既有命名。

红线：**只搬运、不放行**。不解析 G1–G5、不改任务台账／定夺队列状态；commit 类
    请求复用 `scripts/commit_request.py` 的路径白名单（同一份实现，不另写一套）；
    发车参数只认 `^--full-auto --yes --only <编号>(,<编号>)*$`——除该白名单外的任何
    字符（分号、反引号、`$(`、重定向、额外参数）一律拒收，因为它会被 shell 执行。

投递件名 → 落点（去掉前缀，沿用各通道既有命名）：
    commit-<ts>[-<来源>].request  → .claude/handoff/commit/<ts>[-<来源>].request
    commit-<ts>[-<来源>].action   → .claude/handoff/commit/<ts>[-<来源>].action
    launch-<ts>[-<来源>].request  → .claude/handoff/launch/<ts>[-<来源>].request
    event-<事件名>                 → .claude/handoff/events/<事件名>

跳过（不当投递件、不移入 rejected、不写日志——`relay.log` 自身、`*.log`、`.gitkeep`、
    `.DS_Store`、任何目录）：中继自身产物或环境杂物，防止把自己的 launchd 日志当投递件
    反复拒收（0918Q D1）。

校验（任一条不过 ⇒ 移入 `handoff-inbox/rejected/`，`relay.log` 追加一行）：
    1. 文件名只含 `[A-Za-z0-9_.-]`，⛔ 含 `/`、`..` 一律拒；前缀须是 commit-/launch-/event- 之一。
    2. mtime 距今 < 5 秒 ⇒ 当场两次采样确认（隔 1.5 秒读一次 `(size, mtime)`）：两次相同即
       视为写入已完成，本轮直接搬；仍在变化则跳过，留到下一轮，⛔ 不算拒收、不记日志
       （0918Q D2：避免恒等到下一个 300 秒兜底点才搬）。
    3. commit 类：内容须是合法 JSON 对象；若含 `paths`，逐条过
       `scripts.commit_request.validate_path`（与提交通道同一份实现）。
    4. launch 类：整行（去掉尾部换行）须精确匹配 `^--full-auto --yes --only [0-9A-Za-z]+(,[0-9A-Za-z]+)*$`。
    5. event 类：文件须是 0 字节。

单测注入口：`HANDOFF_RELAY_REPO`（仓库根）、`HANDOFF_RELAY_MIN_AGE_SECONDS`（默认 5）、
`HANDOFF_RELAY_SAMPLE_INTERVAL_SECONDS`（默认 1.5）。生产由 launchd 直接调用，三者都不设
——路径一律用 `Path(__file__).resolve().parents[1]`（脚本自身位置的上一级），⛔ 不用
`os.getcwd()`、⛔ 不用 `git rev-parse --show-toplevel`：装成 launchd 任务后必须指向
**主检出**，而不是任何 worktree。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from scripts.commit_request import validate_path
except ImportError:  # 以文件路径直跑本脚本时 scripts/ 自己在 sys.path
    from commit_request import validate_path  # type: ignore[no-redef]

INBOX_DIRNAME = "handoff-inbox"
REJECTED_DIRNAME = "rejected"
LOG_FILENAME = "relay.log"
IGNORED_NAMES = frozenset({LOG_FILENAME, ".gitkeep", ".DS_Store"})

CHANNEL_SUBDIR = {
    "commit": Path(".claude") / "handoff" / "commit",
    "launch": Path(".claude") / "handoff" / "launch",
    "event": Path(".claude") / "handoff" / "events",
}

_TS = r"\d{8}-\d{6}"
_SOURCE = r"[A-Za-z0-9_-]+"
COMMIT_NAME_RE = re.compile(rf"^{_TS}(?:-{_SOURCE})?\.(request|action)$")
LAUNCH_NAME_RE = re.compile(rf"^{_TS}(?:-{_SOURCE})?\.request$")
EVENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
LAUNCH_ARGS_RE = re.compile(r"^--full-auto --yes --only [0-9A-Za-z]+(,[0-9A-Za-z]+)*$")

DEFAULT_MIN_AGE_SECONDS = 5.0
DEFAULT_SAMPLE_INTERVAL_SECONDS = 1.5


@dataclass(frozen=True)
class RelayResult:
    ok: bool
    channel: str | None = None
    target_name: str | None = None
    reason: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：分类与校验（不碰文件系统之外只读内容）
# ─────────────────────────────────────────────────────────────────────────────


def _validate_commit_payload(text: str) -> str | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"内容不是合法 JSON：{exc}"
    if not isinstance(data, dict):
        return "内容顶层不是 JSON 对象"
    paths = data.get("paths")
    if paths is None:
        return None
    if not isinstance(paths, list):
        return "paths 不是列表"
    for p in paths:
        reason = validate_path(p)
        if reason:
            return reason
    return None


def _validate_launch_payload(text: str) -> str | None:
    stripped = text.rstrip("\n")
    if not LAUNCH_ARGS_RE.fullmatch(stripped):
        return f"发车参数不匹配白名单正则：{stripped!r}"
    return None


def _validate_event_payload(size: int) -> str | None:
    if size != 0:
        return f"事件文件必须是 0 字节，实际 {size} 字节"
    return None


def classify_and_validate(name: str, read_text, stat_size) -> RelayResult:
    """纯逻辑：`read_text()`／`stat_size()` 是惰性取值的回调，校验不需要时不调用。"""
    if "/" in name or ".." in name or name.startswith("."):
        return RelayResult(False, reason=f"文件名含 / 或 ..，或以 . 开头：{name}")

    for prefix, channel in (("commit-", "commit"), ("launch-", "launch"), ("event-", "event")):
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if channel == "commit":
            if not COMMIT_NAME_RE.match(rest):
                return RelayResult(False, reason=f"commit 文件名格式不对（须 <ts>[-来源].request/.action）：{name}")
            reason = _validate_commit_payload(read_text())
        elif channel == "launch":
            if not LAUNCH_NAME_RE.match(rest):
                return RelayResult(False, reason=f"launch 文件名格式不对（须 <ts>[-来源].request）：{name}")
            reason = _validate_launch_payload(read_text())
        else:
            if not EVENT_NAME_RE.match(rest) or rest == "":
                return RelayResult(False, reason=f"event 文件名格式不对：{name}")
            reason = _validate_event_payload(stat_size())
        if reason:
            return RelayResult(False, reason=reason)
        return RelayResult(True, channel=channel, target_name=rest)

    return RelayResult(False, reason=f"不认得的前缀（须 commit-/launch-/event-）：{name}")


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def inbox_dir(repo: Path) -> Path:
    return repo / INBOX_DIRNAME


def rejected_dir(repo: Path) -> Path:
    return inbox_dir(repo) / REJECTED_DIRNAME


def log(repo: Path, line: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with (inbox_dir(repo) / LOG_FILENAME).open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {line}\n")


def _is_ignored(entry: Path) -> bool:
    """跳过中继自身产物与目录——不当投递件看，也不写 REJECTED 日志（0918Q D1）。"""
    if entry.is_dir():
        return True
    name = entry.name
    return name in IGNORED_NAMES or name.endswith(".log")


def _candidates(inbox: Path) -> list[Path]:
    return sorted(
        p for p in inbox.iterdir()
        if p.is_file() and not _is_ignored(p)
    )


def _is_stable(entry: Path, sample_interval_seconds: float, sleep) -> bool:
    """两次采样 `(size, mtime)` 相隔 `sample_interval_seconds` 若相同即视为写入已完成。

    `FileNotFoundError`（文件在采样间隙被搬走）不在此处捕获，交给调用方统一按
    「抢输」处理。
    """
    before = entry.stat()
    sleep(sample_interval_seconds)
    after = entry.stat()
    return (before.st_size, before.st_mtime) == (after.st_size, after.st_mtime)


def process_one(
    repo: Path,
    entry: Path,
    min_age_seconds: float,
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    sleep=time.sleep,
) -> str:
    """返回 "skipped" / "moved" / "rejected" / "claimed-elsewhere"（供单测断言）。

    launchd 的 WatchPaths 与 300 秒 `StartInterval` 兜底可能前后脚重叠触发；本函数不做
    独占认领（不像 commit_request.py 有 `.claiming` 改名），只在文件已被另一实例先一步
    `os.replace` 掉时把 `FileNotFoundError` 当作正常的「抢输」处理，⛔ 不重试、不报错退出。

    防半截文件不再靠固定静默期硬等——mtime 距今 < `min_age_seconds` 时，当场做一次
    两次采样确认（`sample_interval_seconds` 间隔），采样期间文件仍在变化才跳过、
    留到下一轮；采样稳定即视为写入已完成，本轮直接搬（0918Q D2：避免恒等到下一个
    300 秒兜底点才搬）。
    """
    try:
        age = time.time() - entry.stat().st_mtime
        if age < min_age_seconds and not _is_stable(entry, sample_interval_seconds, sleep):
            return "skipped"

        result = classify_and_validate(
            entry.name,
            read_text=lambda: entry.read_text(encoding="utf-8", errors="replace"),
            stat_size=lambda: entry.stat().st_size,
        )
        if not result.ok:
            dst = rejected_dir(repo) / entry.name
            os.replace(entry, dst)
            log(repo, f"REJECTED {entry.name}: {result.reason}")
            return "rejected"

        target_dir = repo / CHANNEL_SUBDIR[result.channel]
        target_dir.mkdir(parents=True, exist_ok=True)
        dst = target_dir / result.target_name
        os.replace(entry, dst)
        log(repo, f"MOVED {entry.name} -> {result.channel}/{result.target_name}")
        return "moved"
    except FileNotFoundError:
        return "claimed-elsewhere"


def run(
    repo: Path,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> list[tuple[str, str]]:
    """扫一遍 `handoff-inbox/`，返回 [(文件名, 结果), ...]（供单测断言）。"""
    inbox = inbox_dir(repo)
    inbox.mkdir(parents=True, exist_ok=True)
    rejected_dir(repo).mkdir(parents=True, exist_ok=True)
    outcomes: list[tuple[str, str]] = []
    for entry in _candidates(inbox):
        outcomes.append((entry.name, process_one(repo, entry, min_age_seconds, sample_interval_seconds)))
    return outcomes


def main() -> int:
    repo = Path(os.environ.get("HANDOFF_RELAY_REPO", str(default_repo_root())))
    min_age = float(os.environ.get("HANDOFF_RELAY_MIN_AGE_SECONDS", str(DEFAULT_MIN_AGE_SECONDS)))
    sample_interval = float(
        os.environ.get("HANDOFF_RELAY_SAMPLE_INTERVAL_SECONDS", str(DEFAULT_SAMPLE_INTERVAL_SECONDS))
    )
    run(repo, min_age, sample_interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
