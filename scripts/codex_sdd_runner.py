#!/usr/bin/env python3
"""Codex SDD 执行器（2026-09-29）——替代 Claude 插件 `superpowers:subagent-driven-development`。

对 `spec-to-plan` 产出的计划（`### Task N:` 三级标题 + Global Constraints 段）逐 Task 执行：

  1. 解析计划 → (Global Constraints 全文, [Task 1..N])
  2. 每个 Task 起一次**独立** `codex exec`（`--sandbox workspace-write`，cwd＝当前 worktree）
     跑 TDD 五步并提交——Task 间上下文隔离，与 superpowers 的「每 Task 全新子代理」同目标
  3. 每个 Task 两次**只读** `codex exec` review：Spec 合规（evidence_ref／effect_* 幂等键／
     temperature=0）+ 代码质量
  4. 更新 `.superpowers/sdd/<计划名>/progress.md` 台账
  5. 全部完成后全分支 Final Review（读 `git cherry -v main <分支>` 核真身）

⛔ 本脚本只做编排与起进程，⛔ 不自己写代码；无头会话照 `run-lanes.sh` 的 HEADER 五条纪律执行。
`--dry-run` 只打印计划、任务划分与将要执行的 argv（验收／单测用，不起真进程）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.codex_jsonl_summary import summarize

TASK_RE = re.compile(r"^###\s+Task\s+(\d+)\s*[:：]", re.MULTILINE)
CONSTRAINTS_RE = re.compile(
    r"^#{2,3}\s*Global\s+Constraints\b", re.IGNORECASE | re.MULTILINE
)

CODEX_BIN = "codex"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_REASON = "high"


@dataclass(frozen=True)
class ParsedPlan:
    constraints: str
    tasks: list[tuple[int, str]]


def parse_plan(text: str) -> ParsedPlan:
    """把计划切成 Global Constraints 段 + 按 `### Task N:` 划分的任务列表。"""
    constr = ""
    cm = CONSTRAINTS_RE.search(text)
    if cm:
        tail = text[cm.end():]
        nm = re.search(r"^#{1,3}\s", tail, re.MULTILINE)
        constr = (tail[: nm.start()] if nm else tail).strip()

    tasks: list[tuple[int, str]] = []
    matches = list(TASK_RE.finditer(text))
    for i, m in enumerate(matches):
        n = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        tasks.append((n, text[start:end].strip()))
    return ParsedPlan(constraints=constr, tasks=tasks)


def build_codex_argv(*, model: str, reason: str, read_only: bool = False) -> list[str]:
    """无头 codex exec 的 argv。审批/认证旗标与本机实测口径一致（见 AGENTS.md §1）。"""
    sandbox = "read-only" if read_only else "workspace-write"
    return [
        CODEX_BIN,
        "exec",
        "--json",
        "--sandbox",
        sandbox,
        "-c",
        "approval_policy=never",
        "-c",
        "forced_login_method=chatgpt",
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={reason}",
        "-",
    ]


HEADLESS_RULES = """【无头执行引导】本会话由 scripts/codex_sdd_runner.py 无头启动，没有人在旁边：
① 无人在场，禁止提问；需 Shao Peishen 拍板的点登记后停在该点，绝不默认生效。
② 并发协议：只 git add 本任务明确列出的路径；⛔ git add -A / git add . / git commit -a / git stash；
   push 被拒才 git pull --rebase --autostash origin main 重试 ≤3 次；.git/index.lock 存在则等 5 秒重试 ≤5 次，绝不删锁。
③ 环境不可达时留步并登记，⛔ 不假装闭合。
④ 收工必做：列出新增/修改文件清单 + 实际 commit hash，并反查 git log/status 确认真的提交了。
"""


def build_task_prompt(task: tuple[int, str], constraints: str) -> str:
    return (
        f"{HEADLESS_RULES}\n"
        f"执行计划中的 Task {task[0]}，严格按计划文本 TDD 五步（先写测试→实现→跑测试→提交）。\n\n"
        f"## Global Constraints（逐字生效，reviewer 的注意力透镜）\n{constraints}\n\n"
        f"## Task {task[0]}\n{task[1]}\n"
    )


def build_review_prompt(task: tuple[int, str], constraints: str, role: str) -> str:
    return (
        f"你是只读 {role} reviewer，⛔ 不修改任何文件。\n"
        f"对照下面的 Global Constraints 审查 Task {task[0]} 刚提交的代码与测试：\n"
        f"- Spec 合规重点：每个 effect_* 节点独占且带幂等键 {{thread_id}}:{{node_name}}:{{business_key}}、"
        f"compute_* 无副作用、temperature=0、模型版本显式锁定、evidence_ref 非空。\n"
        f"- 代码质量重点：命名/接口一致、错误处理、可测性、无越界改动（git status 不该出现本 Task 之外的改动）。\n"
        f"最后输出一行 VERDICT: PASS 或 VERDICT: FAIL <原因>，并列出 findings。\n\n"
        f"## Global Constraints\n{constraints}\n\n## Task {task[0]}\n{task[1]}\n"
    )


def _run(prompt: str, argv: list[str], cwd: Path, log_path: Path) -> tuple[int, str]:
    """起一次 codex exec，prompt 走 stdin。返回 (rc, 最终文本)。"""
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    fields, final, _saw = summarize(proc.stdout.decode("utf-8", errors="replace"))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"argv={' '.join(argv)}\n{final or '(无最终文本)'}\nusage={'|'.join(fields)}\n")
    except OSError:
        pass
    return proc.returncode, final


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--tasks", help="范围 N-M 或单个 N；默认全部")
    ap.add_argument("--yes", action="store_true", help="跳过开工确认")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--reason", default=DEFAULT_REASON)
    ap.add_argument("--dry-run", action="store_true", help="只打印计划/任务/argv，不起进程")
    ap.add_argument("--cwd", default=".", help="worktree 目录（codex exec 的 cwd）")
    a = ap.parse_args(argv)

    plan_path = Path(a.plan)
    text = plan_path.read_text(encoding="utf-8")
    parsed = parse_plan(text)
    if not parsed.tasks:
        print(f"✗ 计划里没有 `### Task N:` 任务（{plan_path}）", file=sys.stderr)
        return 2
    if not parsed.constraints:
        print("✗ 计划缺 Global Constraints 段——先回去跑 spec-to-plan 补，⛔ 不许空跑", file=sys.stderr)
        return 3

    tasks = parsed.tasks
    if a.tasks:
        if "-" in a.tasks:
            lo, hi = (int(x) for x in a.tasks.split("-", 1))
            tasks = [t for t in tasks if lo <= t[0] <= hi]
        else:
            n = int(a.tasks)
            tasks = [t for t in tasks if t[0] == n]
        if not tasks:
            print(f"✗ --tasks {a.tasks} 没匹配到任何任务", file=sys.stderr)
            return 4

    cwd = Path(a.cwd).resolve()
    plan_name = plan_path.stem
    run_argv = build_codex_argv(model=a.model, reason=a.reason)
    review_argv = build_codex_argv(model=a.model, reason=a.reason, read_only=True)
    print(f"计划：{plan_path}｜Global Constraints {len(parsed.constraints)} 字符｜任务 {len(tasks)} 条（{', '.join(str(t[0]) for t in tasks)}）")
    print(f"cwd：{cwd}")
    print(f"执行 argv：{' '.join(run_argv)}")
    if a.dry_run:
        for n, body in tasks:
            print(f"  Task {n}：{len(body)} 字符（先执行→两次只读 review）")
        return 0

    if not a.yes:
        answer = input(f"将执行 {len(tasks)} 个 Task（会起 codex 会话与 git commit）。确认？[y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消")
            return 5

    progress_dir = cwd / ".superpowers" / "sdd" / plan_name
    progress_dir.mkdir(parents=True, exist_ok=True)
    progress = progress_dir / "progress.md"
    if not progress.exists():
        progress.write_text(
            "# 进度台账\n\n" + "".join(f"- [ ] Task {n}\n" for n, _ in tasks) + "\n", encoding="utf-8"
        )

    failed = 0
    for n, body in tasks:
        print(f"▶ Task {n} 执行")
        rc, final = _run(build_task_prompt((n, body), parsed.constraints), run_argv, cwd, progress_dir / f"task-{n}.log")
        if rc != 0:
            print(f"✗ Task {n} 执行 rc={rc}：{final[-500:]}")
            failed += 1
            break
        for role in ("Spec 合规", "代码质量"):
            print(f"  · {role} review")
            rrc, rfinal = _run(build_review_prompt((n, body), parsed.constraints, role), review_argv, cwd, progress_dir / f"review-{n}-{role}.log")
            if "VERDICT: FAIL" in rfinal:
                print(f"  ✗ Task {n} {role} review FAIL：{rfinal[-300:]}")
                failed += 1
                break
        else:
            done = progress.read_text(encoding="utf-8").replace(f"- [ ] Task {n}", f"- [x] Task {n}")
            progress.write_text(done, encoding="utf-8")
            continue
        break

    if failed == 0:
        print("▶ 全分支 Final Review（只读）：核 `git cherry -v main <分支>` 无 `+` 才算真合")
        frc, ffinal = _run(
            "你是只读 Final Reviewer。对照 Global Constraints 与全计划，核验所有 Task 已合入 main："
            "`git cherry -v main <当前分支>` 不允许出现 `+` 行；出现任何 `+` 即输出 VERDICT: FAIL。"
            f"最后输出 VERDICT: PASS 或 VERDICT: FAIL <原因>。\n\n{parsed.constraints}",
            review_argv, cwd, progress_dir / "final-review.log",
        )
        if "VERDICT: FAIL" in ffinal:
            print(f"✗ Final Review FAIL：{ffinal[-300:]}")
            return 1
    print(f"收工：{'全部通过' if failed == 0 else f'{failed} 处失败'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
