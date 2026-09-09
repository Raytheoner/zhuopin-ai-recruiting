#!/usr/bin/env python3
"""装 HR 值守通道服务的 launchd job（幂等）——tasks 8.3 / design D12。

**Claude ⛔ 不代跑本脚本。** 起 LaunchAgent 属安全配置变更，与改 settings.json 同类，
由 Shao Peishen 本人在 Terminal 跑一次（第 8 章灰度 8.6）。子 session 里能做的只有
`--dry-run`。

用法：
    tools/liaison/.venv/bin/python -m tools.liaison.scripts.install_launchd --dry-run
    tools/liaison/.venv/bin/python -m tools.liaison.scripts.install_launchd

卸载 / 回滚（design「回滚」段：停值守，⛔ 不销毁台账）：
    launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.liaison.plist

⛔ 本模块不 import `scripts/install_lane_launcher.py`，只参考它的写法：`tools/` 不在
`sync-to-server.sh` 的 SYNC_PATHS 白名单里而 `scripts/` 在，反向依赖会把本服务的代码
拽进同步范围（design D10）。重复的那几十行是刻意付的代价。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

LABEL = "com.zhuopin.hr.liaison"

#: tools/liaison/scripts/install_launchd.py → parents[0]=scripts, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "launchd" / f"{LABEL}.plist.template"
)


def agents_dir() -> Path:
    """LaunchAgents 目录。独立成函数是为了让用例能指到 tmp——⛔ 用例不写真实 ~/Library。"""
    return Path.home() / "Library" / "LaunchAgents"


def venv_python(repo_root: Path) -> Path:
    return repo_root / "tools" / "liaison" / ".venv" / "bin" / "python"


def log_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "liaison" / "logs"


def render_plist(repo_root: Path) -> str:
    """把模板里的占位符换成绝对路径。

    ⛔ 不接受相对路径：launchd 的 cwd 是 `/`，相对路径不报错，只是指到别处去。
    """
    repo_root = Path(repo_root).resolve()
    logs = log_dir(repo_root)
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    for placeholder, value in (
        ("{{PYTHON}}", str(venv_python(repo_root))),
        ("{{REPO_ROOT}}", str(repo_root)),
        ("{{STDOUT_LOG}}", str(logs / "launchd.out.log")),
        ("{{STDERR_LOG}}", str(logs / "launchd.err.log")),
    ):
        text = text.replace(placeholder, value)
    assert "{{" not in text, "模板里还有没被替换的占位符"
    return text


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="装 HR 值守通道服务的 launchd job（幂等）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只把渲染结果打出来，⛔ 不写文件、不调 launchctl",
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="仓库根（默认按本文件位置推算）")
    args = parser.parse_args(argv)

    repo = Path(args.repo_root).resolve()
    rendered = render_plist(repo)
    plist_path = agents_dir() / f"{LABEL}.plist"

    if args.dry_run:
        print(f"[dry-run] 仓库根：  {repo}")
        print(f"[dry-run] 解释器：  {venv_python(repo)}"
              f"{'' if venv_python(repo).exists() else '   ⚠️ 还不存在（见 README「依赖与环境」）'}")
        print(f"[dry-run] 日志目录：{log_dir(repo)}")
        print(f"[dry-run] 将写入：  {plist_path}")
        print("[dry-run] 渲染结果：")
        print(rendered)
        print("[dry-run] ⛔ 未写任何文件、未调用 launchctl。")
        return 0

    if sys.platform != "darwin":
        print("✗ 本脚本只在 macOS 上有意义（launchd）", file=sys.stderr)
        return 1

    # fail-closed：解释器不在，KeepAlive 会按 ThrottleInterval 无限重启一个必然失败的
    # 进程，而唯一症状是 err 日志里滚同一行。⛔ 宁可装不上，不装一个空转的 job。
    python = venv_python(repo)
    if not python.exists():
        print(
            f"✗ 找不到 venv 解释器：{python}\n"
            f"  先建：python3.14 -m venv tools/liaison/.venv && "
            f"tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt",
            file=sys.stderr,
        )
        return 1

    # 目录不存在时 launchd 不建也不报错，这条 job 直接起不来。
    log_dir(repo).mkdir(parents=True, exist_ok=True)
    agents_dir().mkdir(parents=True, exist_ok=True)

    existed = plist_path.exists()
    plist_path.write_text(rendered, encoding="utf-8")

    domain = f"gui/{os.getuid()}"
    # bootout 先于 bootstrap，让重复执行等价于「重装」。没装过时 bootout 返回非 0，
    # 那是正常的，⛔ 不要当失败。
    boot_out = _run(["launchctl", "bootout", domain, str(plist_path)])
    boot_in = _run(["launchctl", "bootstrap", domain, str(plist_path)])

    print(f"仓库：      {repo}")
    print(f"解释器：    {python}")
    print(f"日志：      {log_dir(repo)}/launchd.{{out,err}}.log")
    print(f"plist：     {plist_path} （{'覆盖写入' if existed else '新建'}）")
    print(f"bootout：   rc={boot_out.returncode} {boot_out.stderr.strip()}")
    print(f"bootstrap： rc={boot_in.returncode} {boot_in.stderr.strip()}")

    if boot_in.returncode != 0:
        print("✗ bootstrap 失败，值守未启动。上面那行 stderr 是原因。", file=sys.stderr)
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
    print("✅ 装好了。停 / 回滚：")
    print(f"   launchctl bootout {domain} {plist_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
