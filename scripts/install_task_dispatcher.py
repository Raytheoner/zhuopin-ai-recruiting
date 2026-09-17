#!/usr/bin/env python3
"""装任务调度器（R2）的 LaunchAgent（幂等；2026-09-17，0917AM）。

触发两路，都由 launchd 保证、不靠任何常驻进程：
    WatchPaths            .claude/handoff/events/   —— run-lanes 收敛写 lanes-done-*、提交通道写 decision-*
    StartCalendarInterval 每日 09:00                —— 兜底：事件丢了（机器睡眠、launchd 合并触发）也至多晚一天
两路都只是起 `scripts/dispatcher_event.sh`；该不该真起一个 claude 会话由壳按「有未处理事件 / 当日兜底戳」判。

安装口（⛔ 不需要人开终端）：Cowork 写动作请求
    {"action": "install-agent", "script": "scripts/install_task_dispatcher.py"}
动作通道以 venv/bin/python 跑本脚本；本脚本再 bootout+bootstrap。手工跑也行：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_task_dispatcher.py
    python3 …/install_task_dispatcher.py --print      # 只打印 plist，不写文件、不碰 launchctl

卸载：
    launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.task-dispatcher.plist
    rm ~/Library/LaunchAgents/com.zhuopin.hr.task-dispatcher.plist
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.zhuopin.hr.task-dispatcher"

#: 每日兜底时点（本机本地时间）。09:00 ＝ 上班前把昨夜收敛的批次与未处理事件扫一遍。
CALENDAR_SLOTS: tuple[tuple[int, int], ...] = ((9, 0),)

# launchd 不继承登录 shell 的环境；`claude` 装在 ~/.local/bin。
# 🔴 ⛔ 不许留字面量 `~`：plist **不做波浪号展开**，HOME 在渲染时展开成绝对路径
#    （scripts/install_lane_launcher.py 同源）。
PATH_ENTRIES = (
    "{home}/.local/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)


def repo_root() -> Path:
    """独立成函数是为了让用例能指到 tmp。"""
    return Path(__file__).resolve().parent.parent


def agents_dir() -> Path:
    """LaunchAgents 目录。独立成函数是为了让用例能指到 tmp——⛔ 用例不写真实 ~/Library。"""
    return Path.home() / "Library" / "LaunchAgents"


def shell_path(root: Path) -> Path:
    return root / "scripts" / "dispatcher_event.sh"


def events_dir(root: Path) -> Path:
    return root / ".claude" / "handoff" / "events"


def log_dir(root: Path) -> Path:
    return root / ".claude" / "handoff" / "dispatcher"


def build_plist(root: Path, home: Path) -> dict:
    """算出 LaunchAgent 的 plist 字典。纯函数：不建目录、不碰 launchctl。

    两个必带触发键（WatchPaths / StartCalendarInterval）与 AbandonProcessGroup、PATH 缺了都
    「不报错只不工作」，断言见 tests/test_task_dispatcher.py。
    """
    logs = log_dir(root)
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(shell_path(root))],
        # ⛔ 不设 RunAtLoad：登录时不该自己起一个会话花钱；由事件或 09:00 兜底触发。
        "RunAtLoad": False,
        "WatchPaths": [str(events_dir(root))],
        "StartCalendarInterval": [{"Hour": hour, "Minute": minute} for hour, minute in CALENDAR_SLOTS],
        "WorkingDirectory": str(root),
        # 壳退出后 launchd 回收整个进程组（SIGKILL）——壳自己是同步等 claude 的，但 skill 经动作通道
        # 触发的 launchctl 子进程不该被连坐（0909Y 实测症状：子进程无声消失、launchd 侧退出码 0）。
        "AbandonProcessGroup": True,
        "StandardOutPath": str(logs / "launchd.log"),
        "StandardErrorPath": str(logs / "launchd.log"),
        # ⛔ 不放任何凭据键：调度器不需要 .env 里的东西，plist 也不受 .gitignore 保护。
        "EnvironmentVariables": {
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": ":".join(entry.format(home=home) for entry in PATH_ENTRIES),
            "PYTHONUTF8": "1",
        },
    }


def _render_slots() -> str:
    return "、".join(f"{hour:02d}:{minute:02d}" for hour, minute in CALENDAR_SLOTS)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_task_dispatcher.py",
        description="装／重装任务调度器（R2）的 LaunchAgent：WatchPaths .claude/handoff/events/ ＋ 每日 09:00 兜底。",
    )
    parser.add_argument("--print", action="store_true", help="只打印将写入的 plist，不写文件、不碰 launchctl")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    root = repo_root()
    plist = build_plist(root, Path.home())

    if args.print:
        sys.stdout.write(plistlib.dumps(plist).decode("utf-8"))
        return 0

    if sys.platform != "darwin":
        print("✗ 本脚本只在 macOS 上有意义（launchd）", file=sys.stderr)
        return 1

    # 无害预检：壳不在就退回并说明，⛔ 不写一份必然起不来的 plist。
    shell = shell_path(root)
    if not shell.is_file():
        print(f"✗ 找不到事件壳：{shell}", file=sys.stderr)
        return 1
    claude = Path.home() / ".local" / "bin" / "claude"
    if not claude.exists():
        print(f"✗ 找不到 claude CLI：{claude}（壳靠 PATH 里的 ~/.local/bin 找它）", file=sys.stderr)
        return 1

    # WatchPaths 盯的目录不存在时 launchd 直接忽略这条 job——不报错，只是永远不触发。必须先建。
    events_dir(root).mkdir(parents=True, exist_ok=True)
    (events_dir(root) / "processed").mkdir(parents=True, exist_ok=True)
    log_dir(root).mkdir(parents=True, exist_ok=True)

    agents = agents_dir()
    agents.mkdir(parents=True, exist_ok=True)
    plist_path = agents / f"{LABEL}.plist"
    existed = plist_path.exists()
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)

    domain = f"gui/{os.getuid()}"
    # bootout 先于 bootstrap，让重复执行等价于「重装」。已卸载时 bootout 返回非 0，那是正常的。
    boot_out = _run(["launchctl", "bootout", domain, str(plist_path)])
    boot_in = _run(["launchctl", "bootstrap", domain, str(plist_path)])

    print(f"仓库：      {root}")
    print(f"事件壳：    {shell}")
    print(f"监视目录：  {events_dir(root)}")
    print(f"plist：     {plist_path} （{'覆盖写入' if existed else '新建'}）")
    print(f"兜底时点：  每日 {_render_slots()}")
    print(f"bootout：   rc={boot_out.returncode} {boot_out.stderr.strip()}")
    print(f"bootstrap： rc={boot_in.returncode} {boot_in.stderr.strip()}")

    if boot_in.returncode != 0:
        print("✗ bootstrap 失败，调度器未生效。上面那行 stderr 是原因。", file=sys.stderr)
        return 1

    status = _run(["launchctl", "print", f"{domain}/{LABEL}"])
    if status.returncode != 0:
        print(f"✗ launchctl print 取不到状态：{status.stderr.strip()}", file=sys.stderr)
        return 1
    print("状态（launchctl print 摘要）：")
    for line in status.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "path =", "program =", "last exit code =")):
            print(f"  {stripped}")

    print()
    print(f"✅ 装好了。{events_dir(root)} 下出现事件文件、或每日 {_render_slots()}，即起一次调度器会话。")
    print(f"   日志：{log_dir(root)}/<ts>.log（会话）  /  {log_dir(root)}/launchd.log（壳与 launchd）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
