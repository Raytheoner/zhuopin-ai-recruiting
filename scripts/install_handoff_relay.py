#!/usr/bin/env python3
"""装 launchd 投递中继触发器（幂等；2026-09-18，0918H）。

与 install_commit_launcher.py 同构：launchd 盯住仓库根 `handoff-inbox/`，受限会话
（`.claude/**` 只读、写不进提交/发车/事件三条通道）用 Write 工具往里写一个文件，
launchd 就以 Shao Peishen 本人的身份调用 `docs/openers/handoff-relay.sh` →
`scripts/handoff_relay.py` 把合格件搬进 `.claude/handoff/**`。设计、协议、白名单
见 scripts/handoff_relay.py 头注释。

外加 `StartInterval` 每 300 秒兜底：WatchPaths 在机器睡眠、或 launchd 合并触发时
可能漏事件；受限会话没有本机 shell 可以另起一次触发，只能靠定时轮询兜底。

🔴 **本脚本必须在主检出装，不能在 worktree 装**：worktree 里 `.claude/handoff/`
根本不存在，装在 worktree 上的 launchd 任务会盯错目录（本条 opener 第四节实证）。

装 LaunchAgent 属安全配置变更 —— ⛔ Claude 不代做，只由本人在 Terminal 跑一次：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_handoff_relay.py

只看不装（打印将要写入的 plist XML）：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_handoff_relay.py --print

卸载：
    launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.handoff-relay.plist
    rm ~/Library/LaunchAgents/com.zhuopin.hr.handoff-relay.plist
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.zhuopin.hr.handoff-relay"

# 兜底轮询间隔（秒）。WatchPaths 是主触发，这只是漏事件时的安全网。
FALLBACK_INTERVAL_SECONDS = 300

# launchd 不继承登录 shell 的环境；PATH 必须显式给、且不能留字面量 `~`（plist 不做
# 波浪号展开）。python3 在 /opt/homebrew/bin，git 在 /usr/bin。
PATH_ENTRIES = (
    "{home}/.local/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)


def launcher_path(repo_root: Path) -> Path:
    return repo_root / "docs" / "openers" / "handoff-relay.sh"


def watch_dir_path(repo_root: Path) -> Path:
    return repo_root / "handoff-inbox"


def log_path(repo_root: Path) -> Path:
    # ⛔ 不落 handoff-inbox/ 内部：扫描器会把自己的 launchd 日志当成投递件，
    # 每轮拒收又立刻被 launchd 重建，陷入无限循环（0918Q D1 实证）。
    return repo_root / "logs" / "handoff-relay.launchd.log"


def build_plist(repo_root: Path, home: Path) -> dict:
    """算出 LaunchAgent 的 plist 字典。纯函数：不建目录、不碰 launchctl（可测）。"""
    watch_dir = watch_dir_path(repo_root)
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(launcher_path(repo_root))],
        # ⛔ 不设 RunAtLoad：由目录变化或 StartInterval 触发，不需要登录时自起。
        "RunAtLoad": False,
        "WatchPaths": [str(watch_dir)],
        "StartInterval": FALLBACK_INTERVAL_SECONDS,
        "StandardOutPath": str(log_path(repo_root)),
        "StandardErrorPath": str(log_path(repo_root)),
        "WorkingDirectory": str(repo_root),
        # 缺这项时 launcher 一退出，launchd 就 SIGKILL 整个进程组（0909Y 实证同类问题）。
        "AbandonProcessGroup": True,
        "EnvironmentVariables": {
            "PYTHONUTF8": "1",
            "PATH": ":".join(entry.format(home=home) for entry in PATH_ENTRIES),
        },
    }


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    print_only = "--print" in argv

    repo = Path(__file__).resolve().parent.parent
    plist = build_plist(repo, Path.home())

    if print_only:
        sys.stdout.write(plistlib.dumps(plist).decode("utf-8"))
        return 0

    if sys.platform != "darwin":
        print("✗ 本脚本只在 macOS 上有意义（launchd）", file=sys.stderr)
        return 1
    launcher = launcher_path(repo)
    if not launcher.exists():
        print(f"✗ 找不到触发脚本：{launcher}", file=sys.stderr)
        return 1

    # WatchPaths 的目录不存在时 launchd 静默忽略这条 job，必须先建出来。
    watch_dir = watch_dir_path(repo)
    watch_dir.mkdir(parents=True, exist_ok=True)
    (watch_dir / "rejected").mkdir(parents=True, exist_ok=True)
    log_path(repo).parent.mkdir(parents=True, exist_ok=True)

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = agents_dir / f"{LABEL}.plist"
    existed = plist_path.exists()
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)

    domain = f"gui/{os.getuid()}"
    # bootout 先于 bootstrap ⇒ 重复执行等价于重装；未装时 bootout 非 0 是正常的。
    boot_out = run(["launchctl", "bootout", domain, str(plist_path)])
    boot_in = run(["launchctl", "bootstrap", domain, str(plist_path)])

    print(f"仓库：      {repo}")
    print(f"触发脚本：  {launcher}")
    print(f"监视目录：  {watch_dir}")
    print(f"兜底间隔：  每 {FALLBACK_INTERVAL_SECONDS} 秒")
    print(f"plist：     {plist_path} （{'覆盖写入' if existed else '新建'}）")
    print(f"bootout：   rc={boot_out.returncode} {boot_out.stderr.strip()}")
    print(f"bootstrap： rc={boot_in.returncode} {boot_in.stderr.strip()}")
    if boot_in.returncode != 0:
        print("✗ bootstrap 失败，触发器未生效。上面那行 stderr 是原因。", file=sys.stderr)
        return 1

    status = run(["launchctl", "print", f"{domain}/{LABEL}"])
    if status.returncode != 0:
        print(f"✗ launchctl print 取不到状态：{status.stderr.strip()}", file=sys.stderr)
        return 1
    print("状态（launchctl print 摘要）：")
    for line in status.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "path =", "program =", "last exit code =")):
            print(f"  {stripped}")
    print()
    print("✅ 装好了。此后受限会话只要往仓库根写一个文件就能投递：")
    print(f"   {watch_dir}/commit-<ts>[-<来源>].request  （JSON，格式同提交通道）")
    print(f"   {watch_dir}/launch-<ts>[-<来源>].request  （一行发车参数）")
    print(f"   {watch_dir}/event-<事件名>                （0 字节）")
    print(f"   合格件搬进 .claude/handoff/**；不合格进 {watch_dir}/rejected/，原因见 {watch_dir}/relay.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
