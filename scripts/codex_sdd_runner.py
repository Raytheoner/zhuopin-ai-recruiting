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
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.codex_jsonl_summary import summarize

TASK_RE = re.compile(r"^###\s+Task\s+(\d+)\s*[:：]", re.MULTILINE)
CONSTRAINTS_RE = re.compile(
    r"^#{2,3}\s*Global\s+Constraints\b", re.IGNORECASE | re.MULTILINE
)

#: Global Constraints 段的结束边界＝下一个**同级或更高级**标题，或裸 `### Task N:`。
#: ⛔ 段内的 `###` 子标题（工程铁律/合规红线/部署约束…）属于约束正文，不能当边界——
#: 2026-10-08 `1001G` 实测：按任意 `#{1,3}` 截断会把四份 U1 计划的约束透镜截成 56 字符
#: （只剩一句 blockquote 引言），子会话与 reviewer 都拿不到铁律。

#: 默认二进制名（PATH 可用时等价）。真正执行前用 `resolve_codex_bin()` 解析，
#: 因为本机 `codex` 常常不在 PATH（只随 ChatGPT/Codex 应用分发，见 AGENTS.md §1）。
CODEX_BIN = "codex"

#: 已知安装兜底（与 `tools/liaison/unpack/dispatch.resolve_codex_bin` 同精神，⛔ 不抄它那份实现）。
_CODEX_BIN_FALLBACKS = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
)


def resolve_codex_bin(env: Mapping[str, str] | None = None) -> str:
    """三级解析：`HR_CODEX_BIN` → PATH 查找 → 已知安装路径兜底；都没有 ⇒ 返回默认名。

    2026-10-08 `1001U`：泳道里 `codex --version` 报 `command not found` 的直接修法——
    runner 自己解析二进制，不依赖调用方把它放上 PATH。
    """
    env = os.environ if env is None else env
    override = (env.get("HR_CODEX_BIN") or "").strip()
    if override and Path(override).exists():
        return override
    found = shutil.which(CODEX_BIN)
    if found:
        return found
    for candidate in _CODEX_BIN_FALLBACKS:
        if Path(candidate).exists():
            return candidate
    return CODEX_BIN
#: 默认模型＝Haiku 档映射（flash/low）。2026-10-08 `1001G` Shao Peishen 指令：
#: 本项目**子任务一律 flash**（含 review 会话）；要单次覆盖用 `--model` / `--reason`。
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_REASON = "low"


@dataclass(frozen=True)
class ParsedPlan:
    constraints: str
    tasks: list[tuple[int, str]]


def parse_plan(text: str) -> ParsedPlan:
    """把计划切成 Global Constraints 段 + 按 `### Task N:` 划分的任务列表。"""
    constr = ""
    cm = CONSTRAINTS_RE.search(text)
    if cm:
        level = len(cm.group(0).split()[0])  # `##` → 2；`###` → 3
        end_re = re.compile(
            rf"^(?:#{{1,{level}}}\s|###\s+Task\s+\d+\s*[:：])", re.MULTILINE
        )
        tail = text[cm.end():]
        nm = end_re.search(tail)
        constr = (tail[: nm.start()] if nm else tail).strip()

    tasks: list[tuple[int, str]] = []
    matches = list(TASK_RE.finditer(text))
    for i, m in enumerate(matches):
        n = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        tasks.append((n, text[start:end].strip()))
    return ParsedPlan(constraints=constr, tasks=tasks)


def build_codex_argv(
    *, model: str, reason: str, read_only: bool = False, codex_bin: str | None = None
) -> list[str]:
    """无头 codex exec 的 argv。审批/认证旗标与本机实测口径一致（见 AGENTS.md §1）。

    `codex_bin` 省略时用默认名（保持测试/`--dry-run` 的稳定打印）；实跑由 `main()` 传解析结果。
    """
    sandbox = "read-only" if read_only else "workspace-write"
    return [
        codex_bin or CODEX_BIN,
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
④ 收工必做：列出新增/修改文件清单（commit 由 runner 代做，见⑥）。
⑤ 本 worktree **没有 venv**：跑测试一律用环境变量 SDD_PYTHON 指的解释器（"$SDD_PYTHON" -m pytest …），
   ⛔ 不要用 ./venv/bin/python（worktree 里不存在）；计划里的裸 `python3`（含 `python3 -c …` 验收命令）
   一律先换成 "$SDD_PYTHON" 再跑——本机 python3＝3.9，导入 app 即失败（1001G 实测）。
⑥ **提交由 runner 代做**（worktree 的 git 元数据在主仓 .git/worktrees/<名> 下，本会话沙箱内 git add/commit
   必失败——0930D 同源）：⛔ 不要尝试 git add/commit；改动留在 worktree 即可，runner 会在两轮 review 通过后代提交。
"""


def build_task_prompt(task: tuple[int, str], constraints: str) -> str:
    return (
        f"{HEADLESS_RULES}\n"
        f"执行计划中的 Task {task[0]}，严格按计划文本 TDD（先写测试→实现→跑测试）；"
        f"⛔ 不要 git add/commit（runner 代提交，见⑥）。\n\n"
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
        f"- 跑测试用 \"$SDD_PYTHON\" -m pytest …（本 worktree 没有 venv，⛔ 不要用 ./venv/bin/python）。\n"
        f"最后输出一行 VERDICT: PASS 或 VERDICT: FAIL <原因>，并列出 findings。\n\n"
        f"## Global Constraints\n{constraints}\n\n## Task {task[0]}\n{task[1]}\n"
    )


def _run(prompt: str, argv: list[str], cwd: Path, log_path: Path) -> tuple[int, str]:
    """起一次 codex exec，prompt 走 stdin。返回 (rc, 最终文本)。"""
    env = {**os.environ, "SDD_PYTHON": sys.executable}
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    fields, final, _saw = summarize(proc.stdout.decode("utf-8", errors="replace"))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"argv={' '.join(argv)}\n{final or '(无最终文本)'}\nusage={'|'.join(fields)}\n")
    except OSError:
        pass
    return proc.returncode, final


def _git_commit(cwd: Path, *, message: str) -> bool:
    """由 runner（父进程，有 git 写权限）代提交本 Task 的改动。

    2026-10-08 `1001U`：worktree 的 git 元数据在主仓 `.git/worktrees/<名>` 下，
    **Task 会话（workspace-write 沙箱，cwd=worktree）自己 `git add/commit` 必失败**
    （0930D 同源）。runner 在非沙箱父进程里跑，代提交才可行；提交失败 ⇒ 返回 False，
    调用方**不勾进度、直接收工**（fail-closed，绝不把"没提交的任务"记成完成）。
    """
    add = subprocess.run(
        ["git", "add", "-A"], cwd=str(cwd), capture_output=True, text=True
    )
    if add.returncode != 0:
        print(f"  ✗ 代提交失败（git add）：{add.stderr.strip()[-300:]}", file=sys.stderr)
        return False
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(cwd))
    if diff.returncode == 0:
        return True  # 无改动 ⇒ 视为已落定
    commit = subprocess.run(
        ["git", "commit", "-m", message], cwd=str(cwd), capture_output=True, text=True
    )
    if commit.returncode != 0:
        print(f"  ✗ 代提交失败（git commit）：{commit.stderr.strip()[-300:]}", file=sys.stderr)
        return False
    return True


def _ensure_progress_entries(progress: Path, tasks: list[tuple[int, str]]) -> None:
    """确保台账存在且覆盖本次任务范围（分段重跑时补行）。

    2026-10-09 `1001O` seg2 实录：台账只在首次派发时建行，`--tasks 4-6` 重跑时
    `- [ ] Task N` 替换全部落空 ⇒ 勾选静默丢失、台账停在旧范围。补行后替换才有效。
    """
    if not progress.exists():
        progress.write_text(
            "# 进度台账\n\n"
            + "".join(f"- [ ] Task {n}\n" for n, _ in tasks)
            + "\n",
            encoding="utf-8",
        )
        return
    text = progress.read_text(encoding="utf-8")
    missing = [
        n
        for n, _ in tasks
        if f"- [ ] Task {n}" not in text and f"- [x] Task {n}" not in text
    ]
    if missing:
        if not text.endswith("\n"):
            text += "\n"
        text += "".join(f"- [ ] Task {n}\n" for n in missing)
        progress.write_text(text, encoding="utf-8")


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
    codex_bin = resolve_codex_bin()
    run_argv = build_codex_argv(model=a.model, reason=a.reason, codex_bin=codex_bin)
    review_argv = build_codex_argv(
        model=a.model, reason=a.reason, read_only=True, codex_bin=codex_bin
    )
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
    _ensure_progress_entries(progress, tasks)

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
            # fail-closed：没有明确 PASS（含空输出/rc≠0）都算没通过——空 review 不许当绿灯。
            if rrc != 0 or "VERDICT: PASS" not in rfinal:
                print(f"  ✗ Task {n} {role} review 未通过（rc={rrc}）：{rfinal[-300:]}")
                failed += 1
                break
        else:
            if not _git_commit(cwd, message=f"chore(sdd): Task {n} — {plan_name}"):
                print(f"  ✗ Task {n} 代提交失败，不勾进度、收工")
                failed += 1
                break
            done = progress.read_text(encoding="utf-8").replace(f"- [ ] Task {n}", f"- [x] Task {n}")
            progress.write_text(done, encoding="utf-8")
            continue
        break

    if failed == 0:
        print("▶ 全分支 Final Review（只读）：核本段任务是否全部提交、工作区是否干净、测试是否全绿")
        frc, ffinal = _run(
            "你是只读 Final Reviewer。对照 Global Constraints 与本次执行的 Task 清单，核验："
            "① 每个 Task 都有对应提交（`git log --oneline` 可见）；② `git status` 干净、无未提交改动；"
            "③ ⛔ 不在本会话跑 pytest（只读沙箱没有可用临时目录，pytest 的 tmp_path 初始化必失败——"
            "测试是否全绿以各 Task 提交时的记录与执行器的 `## 机器判据` 为准）。"
            "⛔ 不要要求本分支已合入 main——合并由执行器（lane_collect stage2）代做，不在本步判。"
            f"最后输出 VERDICT: PASS 或 VERDICT: FAIL <原因>。\n\n{parsed.constraints}",
            review_argv, cwd, progress_dir / "final-review.log",
        )
        if frc != 0 or "VERDICT: PASS" not in ffinal:
            print(f"✗ Final Review 未通过（rc={frc}）：{ffinal[-300:]}")
            return 1
    print(f"收工：{'全部通过' if failed == 0 else f'{failed} 处失败'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
