"""`scripts/opener_split_check.py`（Token 治理 P7·A 路）行为断言。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "opener_split_check.py"


def make(tmp_path, block_body, tasks=6, opener_file=None):
    (tmp_path / "docs/superpowers/plans").mkdir(parents=True)
    (tmp_path / "docs/openers").mkdir(parents=True)
    (tmp_path / "docs/superpowers/plans/p.md").write_text("".join(f"### Task {i}: x\n" for i in range(1, tasks + 1)), encoding="utf-8")
    if opener_file:
        (tmp_path / "docs/openers/0917B-正文.md").write_text(opener_file, encoding="utf-8")
    plan = tmp_path / "docs/openers/OP-0820-全量编排.md"
    plan.write_text("# 编排\n\n## 待执行区\n\n> 泳道：甲\n> 无头豁免注明\n\n```\n" + block_body + "\n```\n", encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path)], capture_output=True, text=True, timeout=20)


def test_long_build_without_range_blocked(tmp_path):
    r = make(tmp_path, "[Mac]0917A-建造\n调 run-build 执行 docs/superpowers/plans/p.md")
    assert r.returncode == 1 and "0917A" in r.stdout and "2 条" in r.stdout


def test_declared_range_passes(tmp_path):
    assert make(tmp_path, "[Mac]0917A-建造\n调 run-build 执行 docs/superpowers/plans/p.md 的 Task 1–3").returncode == 0


def test_exemption_passes(tmp_path):
    assert make(tmp_path, "[Mac]0917A-建造\n调 run-build 执行 docs/superpowers/plans/p.md\n拆分豁免：Task 间共享进程状态").returncode == 0


def test_short_plan_passes(tmp_path):
    assert make(tmp_path, "[Mac]0917A-建造\n调 run-build 执行 docs/superpowers/plans/p.md", tasks=4).returncode == 0


def test_non_build_passes(tmp_path):
    assert make(tmp_path, "[Mac]0917A-计划\n调 spec-to-plan 产出 docs/superpowers/plans/p.md").returncode == 0


def test_reference_style_opener_file_is_read(tmp_path):
    r = make(tmp_path, "[Mac]0917B-建造\n读 docs/openers/0917B-正文.md 全文并逐节执行",
             opener_file="调 superpowers:subagent-driven-development 执行 docs/superpowers/plans/p.md")
    assert r.returncode == 1 and "0917B" in r.stdout


def test_plan_opener_mentioning_next_build_passes(tmp_path):
    body = "[Mac]0917A-计划\n调 `Skill(spec-to-plan)` 出 docs/superpowers/plans/p.md\n⏸ 下一步：按 run-build 执行（波次 2 另出）"
    assert make(tmp_path, body).returncode == 0
