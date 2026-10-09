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


def test_parse_plan_keeps_nested_headings_inside_constraints():
    """1001G 实测：约束段内 `### 工程铁律/合规红线…` 是正文，不能当边界（否则透镜只剩 56 字符）。"""
    plan = (
        "## Global Constraints\n\n"
        "> 从 CLAUDE.md 逐字复制与本单元相关的条目。\n\n"
        "### 工程铁律（不可违背）\n\n"
        "1. 幂等键 `{thread_id}:{node_name}:{business_key}`。\n\n"
        "### 合规红线（逐字）\n\n"
        "- AI 只做排序推荐，不做自动淘汰。\n\n"
        "## 1. File Structure\n\n"
        "app/storage/db.py\n\n"
        "### Task 1: 建表\n\n改 db.py。\n"
    )
    parsed = parse_plan(plan)
    assert "工程铁律" in parsed.constraints and "合规红线" in parsed.constraints
    assert "幂等键" in parsed.constraints
    assert "File Structure" not in parsed.constraints
    assert [n for n, _ in parsed.tasks] == [1]


def test_parse_plan_ends_constraints_at_bare_task_heading():
    """没有下一级 `##` 标题时，裸 `### Task N:` 仍是约束段的结束边界。"""
    plan = "## Global Constraints\n\n- 铁律一\n\n### Task 1: 建表\n\n改 db.py。\n"
    parsed = parse_plan(plan)
    assert parsed.constraints == "- 铁律一"
    assert [n for n, _ in parsed.tasks] == [1]


def test_progress_entries_appended_for_resumed_segments(tmp_path):
    """1001O seg2 实录：分段重跑时台账缺 Task 4-6 行 ⇒ 勾选静默落空，须补行。"""
    from scripts.codex_sdd_runner import _ensure_progress_entries

    p = tmp_path / "progress.md"
    _ensure_progress_entries(p, [(1, "a"), (2, "b"), (3, "c")])
    assert p.read_text(encoding="utf-8") == (
        "# 进度台账\n\n- [ ] Task 1\n- [ ] Task 2\n- [ ] Task 3\n\n"
    )
    # 模拟 Task 1 已勾选后再以 4-6 段重跑
    p.write_text(
        p.read_text(encoding="utf-8").replace("- [ ] Task 1", "- [x] Task 1"),
        encoding="utf-8",
    )
    _ensure_progress_entries(p, [(4, "d"), (5, "e"), (6, "f")])
    text = p.read_text(encoding="utf-8")
    assert "- [x] Task 1" in text
    assert "- [ ] Task 4" in text and "- [ ] Task 5" in text and "- [ ] Task 6" in text
    # 再跑一次不重复追加
    _ensure_progress_entries(p, [(4, "d"), (5, "e"), (6, "f")])
    assert p.read_text(encoding="utf-8").count("- [ ] Task 4") == 1


def test_build_codex_argv_shapes():
    run = build_codex_argv(model="deepseek-v4-pro", reason="high")
    assert run[0:4] == ["codex", "exec", "--json", "--sandbox"]
    assert "workspace-write" in run and "approval_policy=never" in run
    assert "forced_login_method=chatgpt" in run and "-" == run[-1]
    review = build_codex_argv(model="deepseek-v4-pro", reason="high", read_only=True)
    assert "read-only" in review and "workspace-write" not in review


def test_resolve_codex_bin_prefers_env_override_and_falls_back(tmp_path):
    from scripts.codex_sdd_runner import resolve_codex_bin

    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    assert resolve_codex_bin({"HR_CODEX_BIN": str(fake)}) == str(fake)
    # 不给覆盖时也必须返回一个非空字符串（PATH 或已知安装兜底，最差是默认名）
    assert resolve_codex_bin({}) != ""
    # 传进来的二进制必须进 argv[0]
    argv = build_codex_argv(model="m", reason="high", codex_bin="/tmp/x/codex")
    assert argv[0] == "/tmp/x/codex"


def test_git_commit_commits_worktree_changes(tmp_path):
    """runner 代提交（1001U）：有改动 ⇒ 真提交；无改动 ⇒ 返回 True 不产生空提交。"""
    import subprocess

    from scripts.codex_sdd_runner import _git_commit

    repo = tmp_path / "wt"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    assert _git_commit(repo, message="chore(sdd): Task 1 — t") is True
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "Task 1" in log
    # 无改动：再调一次仍 True，且不新增提交
    assert _git_commit(repo, message="chore(sdd): Task 1 — t") is True
    log2 = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert log2 == log


def test_prompts_carry_constraints_and_discipline():
    parsed = parse_plan(PLAN)
    task = parsed.tasks[0]
    tp = build_task_prompt(task, parsed.constraints)
    assert "先写测试" in tp and "git add -A" in tp and "第一铁律" in tp
    assert "SDD_PYTHON" in tp
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


def test_default_model_is_flash_per_user_instruction(tmp_path, capsys):
    """1001G（2026-10-08）：子任务一律 flash（Haiku 档）。⛔ 别把默认值改回 v4-pro 而不改这条断言。"""
    from scripts.codex_sdd_runner import DEFAULT_MODEL, DEFAULT_REASON

    assert DEFAULT_MODEL == "deepseek-flash"
    assert DEFAULT_REASON == "low"
    # dry-run 打印的 argv 也必须带 flash（钉住 main() → build_codex_argv 的接线）
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    assert main(["--plan", str(plan), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "-m deepseek-flash" in out and "-m deepseek-v4-pro" not in out
