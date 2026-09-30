#!/usr/bin/env python3
"""泳道产物执行器代收口（0930D 起；0930F 增环境级 PARTIAL 自动收口）。

背景：Codex `workspace-write` 沙箱把写权限锁在泳道 worktree，而 worktree 的 git 元数据
（`.git/worktrees/<name>`）在主工作区 `.git` 下——泳道内的 `git add/commit/merge/push`
实测 `Operation not permitted`（0930A/B/C 三条因此 PARTIAL 滞留 worktree）。本脚本由
执行器 `run-lanes.sh` 调用（launchd 以用户态起，不受会话沙箱限制），分两阶段代收口：

  stage1（每条泳道退出时）：worktree 里有改动 ⇒ `git add -A`（worktree 隔离，改动全属本
  泳道）＋ commit 到泳道分支，并把 (lane,id,branch,sha,status,gate,worktree,env_only)
  记进 `<logdir>/collect.tsv`。仅 codex 引擎生效；claude 引擎泳道自提交，⛔ 不代劳。
  `env_only` 由 `is_sandbox_git_only_partial` 判：status=PARTIAL 且留步**仅为**环境级
  沙箱只读阻断（git/.codex 只读、index.lock、Operation not permitted）时为 1。

  stage2（一轮收敛后）：合并判据分三类——
    * status=OK：保持现有行为（gate 取自 run-lanes 的 gates.tsv，PASS／无判据 ⇒ 合并）；
    * env_only=1：在**该泳道 worktree 内**按与 run-lanes 同一提取逻辑取
      `docs/openers/<id>-*.md` 的 `## 机器判据` bash 块，同一黑名单安全预检＋同一超时；
      PASS／无判据块 ⇒ 合并（`--ff-only`，失败改 `--no-edit`）；未过或 UNSAFE ⇒ 不合并，
      记 NEEDS-MANUAL-GATE；
    * 其余 PARTIAL / GATE-*：不合并（只留分支）。
  合完 `git push origin main`（被拒 ⇒ `git pull --rebase --autostash origin main` 后重试
  ≤3 次）。合并冲突 ⇒ abort 该条并记 NEEDS-MANUAL-CONFLICT。⛔ 不改写任何泳道产物内容。

用法：
  python3 scripts/lane_collect.py stage1 --repo <主工作区> --logdir <批次目录> \
      --lane <泳道名> --id <编号> --status <OK|PARTIAL|...> \
      --worktree <worktree 路径> --branch <分支> --engine codex
  python3 scripts/lane_collect.py stage2 --repo <主工作区> --logdir <批次目录> [--no-push]
  两个子命令都支持 --dry-run（只打印将要做什么，不落任何写操作）。
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

COLLECT = "collect.tsv"

# 沙箱只读签名（0930F）：environment-only 留步的判据要素。沙箱侧与只读侧各命中其一即可。
_SANDBOX_RE = re.compile(r"沙箱|Seatbelt|workspace-write", re.IGNORECASE)
_READONLY_RE = re.compile(
    r"只读|read.{0,3}only|Operation not permitted|index\.lock",
    re.IGNORECASE,
)
# 业务留步签名（0930F）：等某人回件/答复/确认等人工依赖，才判为业务留步。
_BUSINESS_BLOCK_RE = re.compile(
    r"等\s*[^\s，。、（）()]{1,12}(?:回件|回复|答复|确认|拍板|定夺|回来|上线|窗口)"
)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        errors="replace",
    )


def is_sandbox_git_only_partial(log_text: str) -> bool:
    """判「OPENER_PARTIAL 的留步是否仅为环境级沙箱只读阻断」。

    判据要素：① 存在 `OPENER_PARTIAL:` 行；② 其理由命中沙箱只读签名；③ 日志里的
    「⏸ 留步」逐条都属该签名（任何一条业务留步 ⇒ False）。无「⏸ 留步」块时只认
    ①②（理由本身即留步）。判不准的一律返回 False（保守方向：不自动合并）。
    """
    if not isinstance(log_text, str) or "OPENER_PARTIAL:" not in log_text:
        return False
    reason = ""
    for line in log_text.splitlines():
        if line.lstrip().startswith("OPENER_PARTIAL:"):
            reason = line.split("OPENER_PARTIAL:", 1)[1].strip()
            break
    if not reason:
        return False
    if not (_SANDBOX_RE.search(reason) and _READONLY_RE.search(reason)):
        return False

    marker = "⏸ 留步"
    if marker not in log_text:
        return True

    # 「⏸ 留步」块：从首个标记起，到代码围栏 / 下一个二级标题 / 文件清单 / 后续动作为止，
    # 逐条收集非空内容行；命中沙箱/只读签名的属环境级，命中业务留步签名 ⇒ False，
    # 其余（叙述性文字）跳过——宁可留一条待办，不把没做的事标成做完。
    block_start = log_text.index(marker)
    block = log_text[block_start:]
    items: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("##") or s.startswith("```"):
            break
        if s.startswith("新增/修改文件清单") or s.startswith("已登记的后续动作"):
            break
        if s and not s.startswith("OPENER_PARTIAL:") and not s.startswith("⏸"):
            items.append(s)
    for it in items:
        if _SANDBOX_RE.search(it) or _READONLY_RE.search(it):
            continue
        if _BUSINESS_BLOCK_RE.search(it):
            return False
    return True


def _gate_for(logdir: Path, id_: str) -> str:
    gates = logdir / "gates.tsv"
    if not gates.is_file():
        return ""
    for line in gates.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] == id_:
            return parts[1]
    return ""


def _lane_log(logdir: Path, lane: str, id_: str) -> Path:
    return logdir / f"{lane}-{id_}.log"


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

    env_only = "0"
    if args.status == "PARTIAL":
        log = _lane_log(Path(args.logdir), args.lane, args.id_)
        if log.is_file():
            text = log.read_text(encoding="utf-8", errors="replace")
            if is_sandbox_git_only_partial(text):
                env_only = "1"

    row = "\t".join(
        [args.lane, args.id_, args.branch, sha, args.status, gate, str(wt), env_only]
    )
    with (Path(args.logdir) / COLLECT).open("a", encoding="utf-8") as f:
        f.write(row + "\n")
    print(
        f"collect: {args.id_} 已代提交 {sha[:8]}"
        f"（branch={args.branch}, status={args.status}, env_only={env_only}）"
    )
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
        # 列序：lane id branch sha status gate worktree env_only [result]
        # 0930D 旧行只有前 6 列 ⇒ worktree/env_only 按空处理（env_only 空=0）。
        while len(parts) < 8:
            parts.append("")
        rows.append(parts[:8])
    return rows


def _gate_extract_worktree(worktree: Path, id_: str) -> str:
    """按 run-lanes.sh 同一提取逻辑取 worktree 内 `docs/openers/<id>-*.md` 的判据 bash 块。"""
    files = sorted(glob.glob(str(worktree / "docs" / "openers" / f"{id_}-*.md")))
    if len(files) != 1:
        return ""
    lines = Path(files[0]).read_text(encoding="utf-8", errors="replace").split("\n")
    sec = next(
        (
            i
            for i, l in enumerate(lines)
            if re.match(r"^##+\s*([一二三四五六七八九十\d]+[、.．]\s*)?机器判据", l)
        ),
        None,
    )
    if sec is None:
        return ""
    start = None
    for i in range(sec + 1, len(lines)):
        l = lines[i]
        if start is None:
            if re.match(r"^#{1,2}\s", l):
                break
            if re.match(r"^```bash\s*$", l):
                start = i + 1
        elif re.match(r"^```\s*$", l):
            return "\n".join(lines[start:i])
    return ""


def _gate_unsafe_hit(text: str) -> str:
    """与 run-lanes.sh gate_unsafe 同黑名单；命中返回命中片段，未命中返回空串。"""
    m = re.search(
        r"git\s+(push|commit|reset|clean)|rm\s+-[A-Za-z-]*[rf]|"
        r"(^|[^0-9A-Za-z_])(sudo|curl|wget|ssh|scp|launchctl|mkfs)(\s|$)|"
        r">\s*/dev/sd|\$\([^)]*rm\s+|`[^`]*rm\s+",
        text,
    )
    return m.group(0) if m else ""


def _gate_run_worktree(script_text: str, worktree: Path, timeout_s: int) -> int:
    """在泳道 worktree 内跑判据块，返回退出码（超时 = 124）。"""
    p = subprocess.Popen(
        ["bash"],
        cwd=str(worktree),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        out, _ = p.communicate(input=script_text, timeout=float(timeout_s))
        rc = p.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        out, _ = p.communicate()
        rc = 124
    _ = out
    return rc


def _merge_branch(repo: Path, branch: str) -> bool:
    """ff-only 优先，失败改 --no-edit；仍失败 abort 后返回 False（冲突留人工）。"""
    r = _run(["git", "-C", str(repo), "merge", "--ff-only", branch])
    if r.returncode == 0:
        return True
    r = _run(["git", "-C", str(repo), "merge", "--no-edit", branch])
    if r.returncode == 0:
        return True
    _run(["git", "-C", str(repo), "merge", "--abort"])
    return False


def stage2(args: argparse.Namespace) -> int:
    logdir = Path(args.logdir)
    repo = Path(args.repo)
    rows = _read_rows(logdir)
    if not rows:
        return 0

    merged_ok: list[str] = []
    merged_env: list[str] = []
    manual: list[str] = []
    branch_only: list[str] = []

    for row in rows:
        lane, id_, branch, _sha, status, gate, worktree, env_only = row
        if not branch:
            row.append("NO-BRANCH")
            continue

        if status == "OK" and gate in ("", "PASS"):
            if args.dry_run:
                row.append("WOULD-MERGE")
                continue
            if _merge_branch(repo, branch):
                row.append("MERGED")
                merged_ok.append(id_)
            else:
                row.append("NEEDS-MANUAL-CONFLICT")
                manual.append(id_)
            continue

        if status == "PARTIAL" and env_only == "1" and worktree:
            wt = Path(worktree)
            block = _gate_extract_worktree(wt, id_) if wt.is_dir() else ""
            if block:
                hit = _gate_unsafe_hit(block)
                if hit:
                    row.append(f"NEEDS-MANUAL-GATE-UNSAFE-{hit.strip()[:24]}")
                    manual.append(id_)
                    continue
                timeout_s = int(os.environ.get("HR_LANE_GATE_TIMEOUT", "60") or "60")
                rc = _gate_run_worktree(block, wt, timeout_s) if not args.dry_run else 0
                if rc != 0:
                    row.append("NEEDS-MANUAL-GATE")
                    manual.append(id_)
                    continue
            if args.dry_run:
                row.append("WOULD-MERGE-ENV")
                continue
            if _merge_branch(repo, branch):
                row.append("MERGED-ENV")
                merged_env.append(id_)
            else:
                row.append("NEEDS-MANUAL-CONFLICT")
                manual.append(id_)
            continue

        if status == "OK":
            row.append(f"NEEDS-MANUAL-GATE-{gate or 'UNKNOWN'}")
            manual.append(id_)
        else:
            row.append("BRANCH-ONLY")
            branch_only.append(id_)

    push_state = "-"
    merged = merged_ok + merged_env
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
        (logdir / "collect.done").write_text("\n".join(merged) + "\n", encoding="utf-8")

    print(
        f"collect: stage2 {'[dry-run] ' if args.dry_run else ''}"
        f"自动合并（OK）{len(merged_ok)} 条"
        f"{'（' + ', '.join(merged_ok) + '）' if merged_ok else ''}"
        f"；自动合并（环境级 PARTIAL）{len(merged_env)} 条"
        f"{'（' + ', '.join(merged_env) + '）' if merged_env else ''}"
        f"；留人工 {len(manual)} 条"
        f"{'（' + ', '.join(manual) + '）' if manual else ''}"
        f"；留分支 {len(branch_only)} 条"
        f"{'（' + ', '.join(branch_only) + '）' if branch_only else ''}"
        f"；push={push_state if not args.dry_run else '-'}"
    )
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="泳道产物执行器代收口（0930D/0930F）")
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

    p2 = sub.add_parser("stage2", help="一轮收敛后合并 OK/环境级 PARTIAL 泳道并推送")
    p2.add_argument("--repo", required=True)
    p2.add_argument("--logdir", required=True)
    p2.add_argument("--no-push", dest="push", action="store_false")
    p2.add_argument("--dry-run", action="store_true")
    p2.set_defaults(func=stage2, push=True)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
