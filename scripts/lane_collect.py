#!/usr/bin/env python3
"""泳道产物执行器代收口（0930D，2026-09-30）。

背景：Codex `workspace-write` 沙箱把写权限锁在泳道 worktree，而 worktree 的 git 元数据
（`.git/worktrees/<name>`）在主工作区 `.git` 下——泳道内的 `git add/commit/merge/push`
实测 `Operation not permitted`（0930A/B/C 三条因此 PARTIAL 滞留 worktree）。本脚本由
执行器 `run-lanes.sh` 调用（launchd 以用户态起，不受会话沙箱限制），分两阶段代收口：

  stage1（每条泳道退出时）：worktree 里有改动 ⇒ `git add -A`（worktree 隔离，改动全属本
  泳道）＋ commit 到泳道分支，并把 (lane,id,branch,sha,status,gate) 记进
  `<logdir>/collect.tsv`。仅 codex 引擎生效；claude 引擎泳道自提交，⛔ 不代劳。

  stage2（一轮收敛后）：只把「status=OK 且机器判据 PASS／无判据块」的泳道合并回 main，
  合完 `git push origin main`（被拒 ⇒ `git pull --rebase --autostash origin main` 后重试
  ≤3 次）。合并冲突 ⇒ abort 该条并记 NEEDS-MANUAL-CONFLICT；PARTIAL / GATE-* ⇒ 只留
  分支不合并、汇总点名。⛔ 不改写任何泳道产物内容。

用法：
  python3 scripts/lane_collect.py stage1 --repo <主工作区> --logdir <批次目录> \
      --lane <泳道名> --id <编号> --status <OK|PARTIAL|...> \
      --worktree <worktree 路径> --branch <分支> --engine codex
  python3 scripts/lane_collect.py stage2 --repo <主工作区> --logdir <批次目录> [--no-push]
  两个子命令都支持 --dry-run（只打印将要做什么，不落任何写操作）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

COLLECT = "collect.tsv"


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        errors="replace",
    )


def _gate_for(logdir: Path, id_: str) -> str:
    gates = logdir / "gates.tsv"
    if not gates.is_file():
        return ""
    for line in gates.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] == id_:
            return parts[1]
    return ""


def stage1(args: argparse.Namespace) -> int:
    if args.engine != "codex":
        return 0
    wt = Path(args.worktree)
    if not wt.is_dir() or not (wt / ".git").exists():
        return 0
    st = _run(["git", "-C", str(wt), "status", "--porcelain"])
    if st.returncode != 0:
        print(f"collect: stage1 跳过 {args.id_}（git status rc={st.returncode}: {st.stderr.strip()}）")
        return 0
    if not st.stdout.strip():
        return 0
    if args.dry_run:
        print(f"collect: [dry-run] {args.id_} 有 {len(st.stdout.splitlines())} 处改动，将代提交到 {args.branch}")
        return 0
    add = _run(["git", "-C", str(wt), "add", "-A"])
    if add.returncode != 0:
        print(f"collect: stage1 跳过 {args.id_}（git add rc={add.returncode}: {add.stderr.strip()}）")
        return 0
    msg = f"chore(lane/{args.id_}): 执行器代提交泳道产物（{args.lane}，{args.status}）"
    commit = _run(["git", "-C", str(wt), "commit", "-m", msg])
    if commit.returncode != 0:
        print(f"collect: stage1 跳过 {args.id_}（git commit rc={commit.returncode}: {commit.stderr.strip()}）")
        return 0
    sha = _run(["git", "-C", str(wt), "rev-parse", "HEAD"]).stdout.strip()
    gate = _gate_for(Path(args.logdir), args.id_)
    row = "\t".join([args.lane, args.id_, args.branch, sha, args.status, gate])
    with (Path(args.logdir) / COLLECT).open("a", encoding="utf-8") as f:
        f.write(row + "\n")
    print(f"collect: {args.id_} 已代提交 {sha[:8]}（branch={args.branch}, status={args.status}）")
    return 0


def _read_rows(logdir: Path) -> list[list[str]]:
    path = logdir / COLLECT
    if not path.is_file():
        return []
    rows: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        while len(parts) < 7:
            parts.append("")
        rows.append(parts[:7])
    return rows


def stage2(args: argparse.Namespace) -> int:
    logdir = Path(args.logdir)
    repo = Path(args.repo)
    rows = _read_rows(logdir)
    if not rows:
        return 0

    merged: list[str] = []
    branch_only: list[str] = []
    manual: list[str] = []
    for row in rows:
        lane, id_, branch, sha, status, gate, result = row
        if not branch:
            row[6] = "NO-BRANCH"
            continue
        if status == "OK" and gate in ("", "PASS"):
            if args.dry_run:
                row[6] = "WOULD-MERGE"
                continue
            r = _run(["git", "-C", str(repo), "merge", "--no-edit", branch])
            if r.returncode == 0:
                row[6] = "MERGED"
                merged.append(id_)
            else:
                _run(["git", "-C", str(repo), "merge", "--abort"])
                row[6] = "NEEDS-MANUAL-CONFLICT"
                manual.append(id_)
        elif status == "OK":
            row[6] = f"NEEDS-MANUAL-GATE-{gate or 'UNKNOWN'}"
            manual.append(id_)
        else:
            row[6] = "BRANCH-ONLY"
            branch_only.append(id_)

    push_state = "-"
    if merged and args.push and not args.dry_run:
        push_state = "FAILED"
        for _ in range(3):
            p = _run(["git", "-C", str(repo), "push", "origin", "main"])
            if p.returncode == 0:
                push_state = "PUSHED"
                break
            _run(["git", "-C", str(repo), "pull", "--rebase", "--autostash", "origin", "main"])

    if not args.dry_run:
        with (logdir / COLLECT).open("w", encoding="utf-8") as f:
            for row in rows:
                f.write("\t".join(row) + "\n")
        # 只保留已合并条目的 sha 供人工回溯；其余行原样保留在文件里。
        (logdir / "collect.done").write_text("\n".join(merged) + "\n", encoding="utf-8")

    print(f"collect: stage2 {'[dry-run] ' if args.dry_run else ''}合并 {len(merged)} 条"
          f"{'（' + ', '.join(merged) + '）' if merged else ''}"
          f"；留分支 {len(branch_only)} 条"
          f"{'（' + ', '.join(branch_only) + '）' if branch_only else ''}"
          f"；待人工 {len(manual)} 条"
          f"{'（' + ', '.join(manual) + '）' if manual else ''}"
          f"；push={push_state if not args.dry_run else '-'}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="泳道产物执行器代收口（0930D）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("stage1", help="泳道退出时代提交到泳道分支")
    p1.add_argument("--repo", required=True)
    p1.add_argument("--logdir", required=True)
    p1.add_argument("--lane", required=True)
    p1.add_argument("--id", dest="id_", required=True)
    p1.add_argument("--status", required=True)
    p1.add_argument("--worktree", required=True)
    p1.add_argument("--branch", required=True)
    p1.add_argument("--engine", required=True)
    p1.add_argument("--dry-run", action="store_true")
    p1.set_defaults(func=stage1)

    p2 = sub.add_parser("stage2", help="一轮收敛后合并 OK 泳道并推送")
    p2.add_argument("--repo", required=True)
    p2.add_argument("--logdir", required=True)
    p2.add_argument("--no-push", dest="push", action="store_false")
    p2.add_argument("--dry-run", action="store_true")
    p2.set_defaults(func=stage2, push=True)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
