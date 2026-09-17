#!/usr/bin/env python3
"""装 Mac 侧调度 tick 的 LaunchAgent（幂等）：每 5 分钟跑一次 `python -m tools.liaison tick`。

为什么是 launchd 而不是进程内定时（TD-11）：
    提醒必须是"进程重启也不丢"的调度。sleep 循环／后台线程活不过一次重启，等于把
    "没做"标成"做完了"。launchd `StartInterval` 由操作系统保证：tick 是一次性进程，
    跑完即退，下一次由系统再拉起；tick 自身幂等（三类扫描各带幂等键），多跑无害。

    装 LaunchAgent 属安全配置变更，和改 settings.json 同类——⛔ Claude 不代做，
    由 Shao Peishen 本人跑一次。

用法（在任意目录、任意终端跑一次即可，重复跑等价于重装）：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_liaison_tick.py

只看不装（打印将写入的 plist 原文，不写文件、不碰 launchctl）：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_liaison_tick.py --print

卸载：
    launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.liaison-tick.plist
    rm ~/Library/LaunchAgents/com.zhuopin.hr.liaison-tick.plist

⛔ 与值守服务本体（`com.zhuopin.hr.liaison`，`tools/liaison/scripts/install_launchd.py`）
是两条独立的 job：本体常驻、持企微长连接；tick 一次性、只写发件箱。tick 入队的东西
由本体私信本人——本体没起，tick 照样入队，等本体起来再发。
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.zhuopin.hr.liaison-tick"

#: 两次 tick 之间的间隔（秒）。5 分钟：跟进信／观察窗按天判，泳道批次收敛后 5 分钟内
#: 知道足够；再密只是白读文件。
START_INTERVAL_SECONDS = 300

# launchd 不继承登录 shell 的环境，默认 PATH 极简。tick 本身只用 venv 里的 python，
# 但 `compute_lane_digest` 之外将来若有子进程（git 等）就靠这份 PATH。
# 🔴 ⛔ 不许留字面量 `~`：plist **不做波浪号展开**，`~/.local/bin` 会被当成一个
#    名字里真带 `~` 的相对目录，等价于没写——而且不报错（scripts/install_lane_launcher.py 同源）。
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


def venv_python(root: Path) -> Path:
    # 与 tools/liaison/scripts/install_launchd.py::venv_python 同一路径：SDK 与依赖只装在这个 venv 里。
    return root / "tools" / "liaison" / ".venv" / "bin" / "python"


def log_dir(root: Path) -> Path:
    return root / "data" / "liaison" / "logs"


def build_plist(root: Path, home: Path) -> dict:
    """算出 LaunchAgent 的 plist 字典。纯函数：不建目录、不碰 launchctl。

    单独提出来是为了可测——四个必带键（StartInterval / AbandonProcessGroup /
    EnvironmentVariables.PATH / WorkingDirectory）缺了都"不报错只不工作"，
    断言见 tests/test_install_liaison_tick.py。
    """
    logs = log_dir(root)
    return {
        "Label": LABEL,
        "ProgramArguments": [str(venv_python(root)), "-m", "tools.liaison", "tick"],
        # 装好立刻跑一次，让安装当场自证；tick 幂等，多跑无害。
        "RunAtLoad": True,
        # 定时由 launchd 保证。⛔ 不设 KeepAlive：tick 正常退出后 KeepAlive 会立刻再拉起，等价于忙循环。
        "StartInterval": START_INTERVAL_SECONDS,
        # `python -m tools.liaison` 靠 cwd 进 sys.path。launchd 默认 cwd 是 `/`，不钉死就 import 不到 tools 包。
        "WorkingDirectory": str(root),
        # tick 退出后 launchd 认为这条 job 结束、回收**整个进程组**（SIGKILL）。tick 当前不开子进程，
        # 但这一键缺失的症状（子进程无声消失、launchd 侧退出码 0）在 0909Y 实测过一次，直接钉死。
        "AbandonProcessGroup": True,
        "StandardOutPath": str(logs / "tick.out.log"),
        "StandardErrorPath": str(logs / "tick.err.log"),
        # ⛔ 不放任何 HR_LIAISON_* 键：tick 不需要凭据（只写库），plist 也不受 .gitignore 保护。
        "EnvironmentVariables": {
            "PATH": ":".join(entry.format(home=home) for entry in PATH_ENTRIES),
            "PYTHONUTF8": "1",
        },
    }


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_liaison_tick.py",
        description="装／重装 Mac 侧调度 tick 的 LaunchAgent（每 300 秒跑一次 python -m tools.liaison tick）。",
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

    # 无害预检：解释器不在就退回人工并说明，⛔ 不写一份必然起不来的 plist
    # （那种 plist 的症状是 err 日志里每 5 分钟一条 "No such file"，而 launchctl 一切正常）。
    python = venv_python(root)
    if not python.exists():
        print(
            f"✗ 找不到值守通道 venv 的解释器：{python}\n"
            "  先按 tools/liaison/README.md「依赖与环境」建 tools/liaison/.venv，再重跑本脚本。",
            file=sys.stderr,
        )
        return 1

    # 日志目录不存在时 launchd 不建、也不报错，只是这条 job 起不来。
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
    print(f"解释器：    {python}")
    print(f"plist：     {plist_path} （{'覆盖写入' if existed else '新建'}）")
    print(f"间隔：      {START_INTERVAL_SECONDS} 秒")
    print(f"bootout：   rc={boot_out.returncode} {boot_out.stderr.strip()}")
    print(f"bootstrap： rc={boot_in.returncode} {boot_in.stderr.strip()}")

    if boot_in.returncode != 0:
        print("✗ bootstrap 失败，tick 未生效。上面那行 stderr 是原因。", file=sys.stderr)
        return 1

    status = _run(["launchctl", "print", f"{domain}/{LABEL}"])
    if status.returncode != 0:
        print(f"✗ launchctl print 取不到状态：{status.stderr.strip()}", file=sys.stderr)
        return 1
    print("状态（launchctl print 摘要）：")
    for line in status.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "path =", "program =", "last exit code =", "run interval =")):
            print(f"  {stripped}")

    print()
    print("✅ 装好了。此后每 5 分钟自动跑一次 tick，只入队；私信由值守服务本体发。")
    print(f"   日志：{log_dir(root) / 'tick.out.log'}  /  {log_dir(root) / 'tick.err.log'}")
    print(f"   手动跑一次：cd {root} && {python} -m tools.liaison tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
