#!/usr/bin/env python3
"""Claude Code `PreToolUse` hook —— worktree 泳道不许动主工作区（2026-09-16）。

起因（Win 端 AI 赋能项目实录）：#596、#599 两条泳道的开工单都写了「先建 worktree」，泳道跳过这步，
直接在主工作区（master）上改了编辑锁工具与巡逻调度脚本，靠放锁时的登记检查才拦下。
「开工单里写要求」防不住跳步 ⇒ 两道机制：
  ① `run-lanes.sh` 对【设置】写 worktree ✅ 的条目，自己先建 worktree、在里面启动 claude；
  ② 本 hook：只在 run-lanes 导出 HR_LANE_ISOLATE=1 时生效，拦
     - Edit / Write / NotebookEdit 的目标文件落在主工作区（HR_LANE_MAIN）内、却不在任何 `.claude/worktrees/` 下
     - Bash 里在主工作区执行会改动工作区或提交的 git 命令（add / commit / stash / reset / checkout / restore / rm / mv / apply / am / cherry-pick / revert）
  放行：主工作区里的只读 git、`git merge --ff-only`、`git push`、`git pull`、`git worktree`（单元末条合回 main 要用）；
       主工作区外的路径（/tmp 等）；环境变量缺失时一律放行（非泳道会话不受影响）；任何异常放行（fail-open）。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

WRITE_GIT = {"add", "commit", "stash", "reset", "checkout", "restore", "rm", "mv", "apply", "am", "cherry-pick", "revert", "switch"}


def under(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def in_main_not_worktree(path, main):
    if not under(path, main):
        return False
    return not under(path, os.path.join(main, ".claude", "worktrees"))


def deny(msg):
    print(msg, file=sys.stderr)
    return 2


def check_bash(cmd, cwd, main):
    for part in re.split(r"&&|\|\||;|\n|\|", cmd):
        part = part.strip()
        if not part:
            continue
        try:
            t = shlex.split(part)
        except ValueError:
            continue
        if not t:
            continue
        if t[0] == "cd" and len(t) > 1:
            nxt = t[1] if os.path.isabs(t[1]) else os.path.join(cwd, t[1])
            cwd = nxt
            continue
        if os.path.basename(t[0]) != "git":
            continue
        d, i = cwd, 1
        while i < len(t) and t[i].startswith("-"):
            if t[i] == "-C" and i + 1 < len(t):
                d = t[i + 1] if os.path.isabs(t[i + 1]) else os.path.join(d, t[i + 1]); i += 2
            elif t[i] in ("-c",) and i + 1 < len(t):
                i += 2
            else:
                i += 1
        sub = t[i] if i < len(t) else ""
        if sub in WRITE_GIT and in_main_not_worktree(d, main):
            return f"git {sub}（在 {d}）"
    return None


def main():
    try:
        if os.environ.get("HR_LANE_ISOLATE") != "1":
            return 0
        main_dir = os.environ.get("HR_LANE_MAIN")
        wt = os.environ.get("HR_LANE_WORKTREE", "")
        if not main_dir:
            return 0
        data = json.load(sys.stdin)
        tool = data.get("tool_name")
        inp = data.get("tool_input") or {}
        cwd = data.get("cwd") or os.getcwd()
        hint = (f"本泳道声明了 worktree 隔离，只能在 `{wt}` 里改文件和提交。"
                f"改法：`cd {wt}` 后再操作；合回 main 用 `git -C <主工作区> merge --ff-only <分支>` 与 push（这两个放行）。"
                "若确属必须改主工作区的文件，输出 OPENER_PARTIAL 说明原因并停。（scripts/hooks/worktree-guard.py）")
        if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            fp = inp.get("file_path") or inp.get("notebook_path") or ""
            if fp:
                fp = fp if os.path.isabs(fp) else os.path.join(cwd, fp)
                if in_main_not_worktree(fp, main_dir):
                    return deny(f"⛔ 已拦截：{tool} 要改主工作区文件 `{fp}`。{hint}")
        elif tool == "Bash":
            hit = check_bash(str(inp.get("command", "")), cwd, main_dir)
            if hit:
                return deny(f"⛔ 已拦截：{hit} 会改动主工作区或直接提交到 main。{hint}")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
