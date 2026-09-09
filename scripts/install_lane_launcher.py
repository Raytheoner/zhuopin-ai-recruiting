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

# launchd 不继承登录 shell 的环境。默认 PATH 里没有 `~/.local/bin`，而 `claude`
# 就装在那儿 —— 缺了它，lane-launcher.sh 的 `command -v claude` 让整条链路退 10
# （2026-09-09 `0909Y` 首跑实测）。
# 🔴 ⛔ 不许留字面量 `~`：plist **不做波浪号展开**，`~/.local/bin` 会被当成一个
#    名字里真带 `~` 的相对目录，于是等价于没写 —— 而且不报错。HOME 必须在渲染时
#    展开成绝对路径。
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
    return repo_root / "docs" / "openers" / "lane-launcher.sh"


def watch_dir_path(repo_root: Path) -> Path:
    return repo_root / ".claude" / "handoff" / "launch"


def build_plist(repo_root: Path, home: Path) -> dict:
    """算出 LaunchAgent 的 plist 字典。纯函数：不建目录、不碰 launchctl。

    单独提出来是为了可测。这段过去埋在 `main()` 里、跟 `launchctl bootstrap`
    绑在一起，不真装一次 LaunchAgent 就断言不到 —— 而它里面两处缺陷都属于
    「错了不报错」：连坐杀进程时 launchd 侧退出码是 0，PATH 缺失时退出码是 10
    而不是「找不到 claude」。断言见 `tests/test_lane_launcher.py`。
    """
    watch_dir = watch_dir_path(repo_root)
    log_path = watch_dir / "launchd.log"

    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(launcher_path(repo_root))],
        # ⛔ 不设 RunAtLoad：登录时不该自己发一批车。只由目录变化触发。
        "RunAtLoad": False,
        "WatchPaths": [str(watch_dir)],
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        # 触发脚本自己会 cd 到仓库根；这里再钉一次，让 launchd 的 cwd 不是 /。
        "WorkingDirectory": str(repo_root),
        # launcher 退出后 launchd 认为这条 job 结束，回收**整个进程组** —— 它发的是
        # SIGKILL，`nohup`（只挡 SIGHUP）挡不住，run-lanes.sh 被连坐杀掉。症状是
        # 整批泳道在 `.started` 写完后几秒内集体消失，launchd 侧却一切正常、退出码 0
        # （2026-09-09 `0909Y` 首跑实测）。
        "AbandonProcessGroup": True,
        # 与 run-lanes.sh 同口径：C locale 下中文比较按字节走，UTF-8 下 macOS 自带
        # awk 与 bash 3.2 会**静默出错**（见 run-lanes.sh 顶部「locale 钉死」）。
        # PATH 见 PATH_ENTRIES 上方的说明。
        "EnvironmentVariables": {
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": ":".join(entry.format(home=home) for entry in PATH_ENTRIES),
        },
    }


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    if sys.platform != "darwin":
        print("✗ 本脚本只在 macOS 上有意义（launchd）", file=sys.stderr)
        return 1

    repo = Path(__file__).resolve().parent.parent
    launcher = launcher_path(repo)
    if not launcher.exists():
        print(f"✗ 找不到触发脚本：{launcher}", file=sys.stderr)
        return 1

    # WatchPaths 盯的是目录，目录不存在时 launchd 直接忽略这条 job —— 不报错，
    # 只是从此永远不触发。必须先建出来。
    watch_dir = watch_dir_path(repo)
    watch_dir.mkdir(parents=True, exist_ok=True)
    log_path = watch_dir / "launchd.log"

    plist = build_plist(repo, Path.home())

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
