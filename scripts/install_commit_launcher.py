#!/usr/bin/env python3
"""装 launchd 提交请求触发器（幂等）：让 Cowork 写的文档不用等「下一条泳道顺带提交」。

与 install_lane_launcher.py 同构：launchd 盯住 `.claude/handoff/commit/`，Cowork 用 Write
工具往里写一个 JSON 请求文件，launchd 就以 Shao Peishen 本人的身份调用
`docs/openers/commit-launcher.sh` → `scripts/commit_request.py` 做 add/commit/push。
设计、协议、白名单见 scripts/commit_request.py 头注释。

装 LaunchAgent 属安全配置变更 —— ⛔ Claude 不代做，只由本人在 Terminal 跑一次：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_commit_launcher.py

只看不装（打印将要写入的 plist XML）：
    python3 /Users/paulshao/Projects/HumanResource/scripts/install_commit_launcher.py --print

卸载：
    launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.commit-launcher.plist
    rm ~/Library/LaunchAgents/com.zhuopin.hr.commit-launcher.plist
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.zhuopin.hr.commit-launcher"

# launchd 不继承登录 shell 的环境；PATH 必须显式给、且不能留字面量 `~`（plist 不做
# 波浪号展开，09-09 lane-launcher 实证）。python3 在 /opt/homebrew/bin，git 在 /usr/bin。
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
    return repo_root / "docs" / "openers" / "commit-launcher.sh"


def watch_dir_path(repo_root: Path) -> Path:
    return repo_root / ".claude" / "handoff" / "commit"


def build_plist(repo_root: Path, home: Path) -> dict:
    """算出 LaunchAgent 的 plist 字典。纯函数：不建目录、不碰 launchctl（可测）。"""
    watch_dir = watch_dir_path(repo_root)
    log_path = watch_dir / "launchd.log"
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(launcher_path(repo_root))],
        # ⛔ 不设 RunAtLoad：只由目录变化触发。
        "RunAtLoad": False,
        "WatchPaths": [str(watch_dir)],
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "WorkingDirectory": str(repo_root),
        # 09-09 `0909Y` 实证：缺这项时 launcher 一退出，launchd 就 SIGKILL 整个进程组。
        # 本通道的 git push 是同步跑完才退出的，连坐风险比 lane-launcher 小，但 git
        # 自己会 fork 子进程（pack、credential helper），照样带上。
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
    print("✅ 装好了。此后 Cowork 只要写一个 JSON 文件就能提交：")
    print(f"   {watch_dir}/<时间戳>.request")
    print('   {"message": "docs(x): …", "paths": ["docs/…"], "push": true}')
    print(f"   结果：<同名>.done / .rejected / .deferred；日志：{watch_dir / 'launchd.log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
