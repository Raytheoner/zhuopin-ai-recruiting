"""`unpack-signal` / `unpack-dispatch` 两条 CLI 子命令（spec「信号只由 CLI 清除」
「子命令不碰库」）。

⛔ 本模块不 import `tools.liaison.storage.db`——两条子命令都不需要值守数据库：
`unpack-signal` 只操作信号文件；`unpack-dispatch` 只操作锁文件与起进程。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tools.liaison.unpack.dispatch import (
    BUDGET_ENV,
    DEFAULT_BUDGET_USD,
    build_headless_argv,
    dispatch_headless_unpack,
    resolve_claude_bin,
)
from tools.liaison.unpack.signal import clear_signal_before, probe_signal

#: 三个落位默认值(design D12)。⚠️ 与 `dispatch_wiring.py` 的
#: `DEFAULT_SIGNAL_ROOT`/`DEFAULT_LOG_DIR`/`DEFAULT_LOCK_PATH` 是**同一份路径
#: 字面量的独立副本**——本模块刻意不 import `dispatch_wiring`（那个模块 import
#: 了 `storage.effects`，间接可能拉库依赖），两处路径值必须逐字相同，
#: 改一处务必同步改另一处（Task 7 的 `.env.example` 注释里会提醒）。
_DATA_ROOT = Path("data/liaison")
DEFAULT_SIGNAL_PATH = _DATA_ROOT / "unpack-signal.json"
DEFAULT_LOG_DIR = _DATA_ROOT / "logs" / "unpack-headless"
DEFAULT_LOCK_PATH = _DATA_ROOT / "unpack-session.lock"

#: 测试专用逃生口：指一个不同的信号文件路径，免得单测互相踩生产路径。
#: ⛔ 不写进 `.env.example`——生产环境不该调它。
SIGNAL_PATH_ENV = "HR_LIAISON_SIGNAL_PATH"

EXIT_SIGNAL_PRESENT = 0
EXIT_NO_SIGNAL = 1
EXIT_BAD_ARGS = 2


def _resolve_signal_path() -> Path:
    override = os.environ.get(SIGNAL_PATH_ENV)
    return Path(override) if override else DEFAULT_SIGNAL_PATH


def build_unpack_signal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison unpack-signal",
        description="探测/清除拆件信号文件。⛔ 不打开值守数据库。",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="有信号 → exit 0 且打印 [SIGNAL]；没有 → exit 1 且打印 [NO-SIGNAL]")
    mode.add_argument("--clear", action="store_true", help="按 --before 清除")
    parser.add_argument("--before", help="ISO8601 检查点，--clear 时必填，只清此刻之前的项")
    return parser


def unpack_signal_main(argv: list[str]) -> int:
    args = build_unpack_signal_parser().parse_args(argv)
    signal_path = _resolve_signal_path()

    if args.probe:
        if probe_signal(signal_path):
            print("[SIGNAL]")
            return EXIT_SIGNAL_PRESENT
        print("[NO-SIGNAL]")
        return EXIT_NO_SIGNAL

    if not args.before:
        print("--clear 必须搭配 --before <ISO时刻>", file=sys.stderr)
        return EXIT_BAD_ARGS
    clear_signal_before(signal_path, args.before)
    print(f"已清除 {args.before} 之前的信号项：{signal_path}")
    return 0


def build_unpack_dispatch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison unpack-dispatch",
        description="非阻塞起一个拆件会话。⛔ 不打开值守数据库。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只打印 argv 与解析到的 claude 路径，不起")
    mode.add_argument("--force", action="store_true", help="跳过并发守卫，仅供验收（tasks §5）")
    return parser


def unpack_dispatch_main(argv: list[str]) -> int:
    """⚠️ 本命令的 `--force`/正常两条路径都不接 `bridge_dispatch`（Task 5）——
    它们直接调 Task 4 的 `dispatch_headless_unpack`，且**没有章程**（验收用，
    传一个最小占位 prompt 即可，spec 对这条命令行为的唯一要求是"起活"本身可控）。
    """
    from datetime import datetime, timezone

    args = build_unpack_dispatch_parser().parse_args(argv)
    env = os.environ
    claude_bin = resolve_claude_bin(env)

    if args.dry_run:
        budget = (env.get(BUDGET_ENV) or DEFAULT_BUDGET_USD).strip()
        if claude_bin is None:
            print("找不到 claude 二进制（HR_LIAISON_CLAUDE_BIN / PATH / ~/.local/bin/claude 均未命中）")
            return 0
        print(f"claude 路径：{claude_bin}")
        print("argv：" + " ".join(build_headless_argv(claude_bin, budget)))
        return 0

    now = datetime.now(timezone.utc)
    if args.force and DEFAULT_LOCK_PATH.exists():
        # 仅验收用：直接移走旧锁，⛔ 不是生产路径的一部分（生产路径靠 pid 判活，
        # 不需要人工清锁）。
        DEFAULT_LOCK_PATH.unlink()

    outcome = dispatch_headless_unpack(
        charter_text="[unpack-dispatch --force] 验收占位：本次调用不携带真实章程",
        prompt="[unpack-dispatch --force] 这是一次验收起活，不代表真实拆件任务。",
        log_dir=DEFAULT_LOG_DIR,
        lock_path=DEFAULT_LOCK_PATH,
        env=env,
        now=now,
    )
    print(f"status={outcome.status} reason={outcome.reason} pid={outcome.pid} log={outcome.log_path}")
    return 0 if outcome.status == "started" else 1
