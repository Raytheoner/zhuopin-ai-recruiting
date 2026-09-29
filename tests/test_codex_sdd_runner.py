"""`scripts/codex_sdd_runner.py` 的行为断言（2026-09-29，Codex 迁移）。

只测纯函数与 dry-run：⛔ 不起真 codex 进程、不真 git commit。钉死四件事：
  ① 计划按 `### Task N:` 切任务、Global Constraints 独立成段
  ② codex argv 形状（workspace-write 执行 / read-only review；审批与认证覆盖键）
  ③ Task prompt 与 review prompt 携带约束与无头纪律
  ④ 缺 Global Constraints ⇒ exit 3 拒跑（正本 run-build 前置检查的机器版）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.codex_sdd_runner import (
    build_codex_argv,
    build_review_prompt,
    build_task_prompt,
    main,
    parse_plan,
)

PLAN = """# 测试计划

## Global Constraints

- 第一铁律：每个 effect_* 节点带幂等键。
- temperature=0，模型版本显式锁定。

## Task 列表

### Task 1: 建表

写 app/storage/db.py，命令 pytest tests/test_x.py。

### Task 2: 接线

改 app/graph/nodes.py。

### Task 3: 收口

跑全量 pytest。
"""


def test_parse_plan_splits_constraints_and_tasks():
    parsed = parse_plan(PLAN)
    assert "第一铁律" in parsed.constraints and "temperature=0" in parsed.constraints
    assert [n for n, _ in parsed.tasks] == [1, 2, 3]
    assert "app/storage/db.py" in parsed.tasks[0][1]
    assert "app/graph/nodes.py" in parsed.tasks[1][1]


def test_parse_plan_without_constraints_is_empty():
    parsed = parse_plan("### Task 1: x\n正文\n")
    assert parsed.constraints == ""
    assert [n for n, _ in parsed.tasks] == [1]


def test_build_codex_argv_shapes():
    run = build_codex_argv(model="deepseek-v4-pro", reason="high")
    assert run[0:4] == ["codex", "exec", "--json", "--sandbox"]
    assert "workspace-write" in run and "approval_policy=never" in run
    assert "forced_login_method=chatgpt" in run and "-" == run[-1]
    review = build_codex_argv(model="deepseek-v4-pro", reason="high", read_only=True)
    assert "read-only" in review and "workspace-write" not in review


def test_prompts_carry_constraints_and_discipline():
    parsed = parse_plan(PLAN)
    task = parsed.tasks[0]
    tp = build_task_prompt(task, parsed.constraints)
    assert "先写测试" in tp and "git add -A" in tp and "第一铁律" in tp
    assert "Task 1" in tp and "app/storage/db.py" in tp
    rp = build_review_prompt(task, parsed.constraints, "Spec 合规")
    assert "只读" in rp and "VERDICT: PASS" in rp and "evidence_ref" in rp
    assert "{thread_id}:{node_name}:{business_key}" in rp


def test_main_dry_run_prints_tasks_and_exits_zero(tmp_path, capsys):
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    assert main(["--plan", str(plan), "--dry-run", "--tasks", "1-2"]) == 0
    out = capsys.readouterr().out
    assert "Task 1" in out and "Task 2" in out and "Task 3" not in out


def test_main_rejects_plan_without_global_constraints(tmp_path):
    plan = tmp_path / "bad.md"
    plan.write_text("### Task 1: x\n正文\n", encoding="utf-8")
    assert main(["--plan", str(plan), "--dry-run"]) == 3
