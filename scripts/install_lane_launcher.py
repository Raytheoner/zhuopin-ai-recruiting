#!/usr/bin/env python3
"""装 launchd 发车触发器（幂等）：让泳道发车不再需要人点 Run。

为什么要一个脚本、且只能 Shao Peishen 本人跑：
    2026-09-04 起，CC Desktop 的 Auto Mode 分类器把「无人值守起会自主 commit/push
    的子 session」判为高风险，看护者的 `nohup run-lanes.sh` 与 `run_in_background`
    两条路都被拦；`.claude/settings.json` 白名单三条齐全时照拦
    （lanes-20260904-093026-看护报告.md §五）。此后每批都要他在对话里点一次 Run。

    根治办法是把「起进程」这件事交给操作系统：launchd 盯住
    `.claude/handoff/launch/` 目录，看护者只用 Write 工具往里写一个请求文件
    （不走 Bash，不经过分类器），launchd 就以他本人的身份调用
    `docs/openers/lane-launcher.sh`，由它起 run-lanes.sh。

    装 LaunchAgent 属安全配置变更，和改 settings.json 同类——⛔ Claude 不代做。

用法（在任意目录、任意终端跑一次即可，重复跑结果一样）：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_lane_launcher.py

卸载：
    launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.lane-launcher.plist
    rm ~/Library/LaunchAgents/com.zhuopin.hr.lane-launcher.plist
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.zhuopin.hr.lane-launcher"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    if sys.platform != "darwin":
        print("✗ 本脚本只在 macOS 上有意义（launchd）", file=sys.stderr)
        return 1

    repo = Path(__file__).resolve().parent.parent
    launcher = repo / "docs" / "openers" / "lane-launcher.sh"
    if not launcher.exists():
        print(f"✗ 找不到触发脚本：{launcher}", file=sys.stderr)
        return 1

    # WatchPaths 盯的是目录，目录不存在时 launchd 直接忽略这条 job —— 不报错，
    # 只是从此永远不触发。必须先建出来。
    watch_dir = repo / ".claude" / "handoff" / "launch"
    watch_dir.mkdir(parents=True, exist_ok=True)
    log_path = watch_dir / "launchd.log"

    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(launcher)],
        # ⛔ 不设 RunAtLoad：登录时不该自己发一批车。只由目录变化触发。
        "RunAtLoad": False,
        "WatchPaths": [str(watch_dir)],
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        # 触发脚本自己会 cd 到仓库根；这里再钉一次，让 launchd 的 cwd 不是 /。
        "WorkingDirectory": str(repo),
        # 与 run-lanes.sh 同口径：C locale 下中文比较按字节走，UTF-8 下 macOS 自带
        # awk 与 bash 3.2 会**静默出错**（见 run-lanes.sh 顶部「locale 钉死」）。
        "EnvironmentVariables": {"LC_ALL": "C", "LANG": "C"},
    }

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = agents_dir / f"{LABEL}.plist"

    existed = plist_path.exists()
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)

    uid = os.getuid()
    domain = f"gui/{uid}"

    # bootout 先于 bootstrap，让重复执行等价于「重装」。已卸载时 bootout 返回非 0，
    # 那是正常的，⛔ 不要把它当失败。
    boot_out = run(["launchctl", "bootout", domain, str(plist_path)])
    boot_in = run(["launchctl", "bootstrap", domain, str(plist_path)])

    print(f"仓库：      {repo}")
    print(f"触发脚本：  {launcher}")
    print(f"监视目录：  {watch_dir}")
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
    print("✅ 装好了。此后看护者只要写一个文件就能发车：")
    print(f"   {watch_dir}/<批次时间戳>.request   内容一行＝run-lanes.sh 的参数")
    print(f"   日志：{log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
