"""`docs/openers/run-lanes.sh` 模型分级的行为断言（Token 治理 Phase 2，0916C）。

P0 账本：无头泳道 95% 调用跑在 Opus——原脚本 MODEL="" 时不传 --model，沿用本机默认。
改后取值优先级：命令行 --model ＞ opener【设置】行「模型: X」＞ 默认 sonnet；子代理经
CLAUDE_CODE_SUBAGENT_MODEL 固定为 sonnet。这里钉死四件事：
  ① 没写模型的条目实跑时传 --model sonnet，且子代理环境变量到位
  ② 【设置】写「模型: Opus」的条目传 --model opus
  ③ 命令行 --model 整批覆盖
  ④ 【设置】写了不认识的模型名 → dry-run 预检拒跑（exit 13），⛔ 不许静默回落到默认
另外 results.tsv 第 6 列记模型——A/B 对比靠它。

隔离方式：把脚本拷到临时目录、把 REPO 常量改成临时仓库、设 RUN_LANES_COPY=1 跳过自拷贝，
PATH 前插一个假 claude（记录参数与环境变量、输出 OPENER_DONE）。⛔ 不碰真实仓库的 handoff 与编排文件。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "docs" / "openers" / "run-lanes.sh"

PLAN = """# 测试编排

> 泳道：甲
> 无头豁免注明
```
[Mac]0101A-无模型条目
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（测试）｜ 工作区: 仓库根 ｜ 派发: run-lanes.sh
做点事
```

> 泳道：乙
> 无头豁免注明
```
[Mac]0101B-声明Opus条目
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（测试）｜ 工作区: 仓库根 ｜ 派发: run-lanes.sh ｜ 模型: Opus
做点难事
```
"""

BAD = PLAN.replace("模型: Opus", "模型: GPT9")


@pytest.fixture
def sandbox(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "handoff").mkdir(parents=True)
    (repo / ".git").mkdir()
    script = tmp_path / "run-lanes.sh"
    text = SRC.read_text(encoding="utf-8")
    text, n = re.subn(r'^REPO="[^"]*"$', f'REPO="{repo}"', text, count=1, flags=re.M)
    assert n == 1
    script.write_text(text, encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    fake = bindir / "claude"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "ARGS $* SUB=${{CLAUDE_CODE_SUBAGENT_MODEL:-unset}}" >> "{calls}"\n'
        "cat >/dev/null\n"
        "echo OPENER_DONE\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    plan = tmp_path / "plan.md"
    env = dict(os.environ, RUN_LANES_COPY="1", PATH=f"{bindir}:{os.environ['PATH']}")
    return {"repo": repo, "script": script, "plan": plan, "calls": calls, "env": env}


def run(sb, *args, plan_text=PLAN):
    sb["plan"].write_text(plan_text, encoding="utf-8")
    return subprocess.run(
        ["bash", str(sb["script"]), "--plan", str(sb["plan"]), "--stagger", "0", *args],
        env=sb["env"], capture_output=True, text=True, timeout=120,
    )


def calls_for(sb):
    lines = sb["calls"].read_text(encoding="utf-8").splitlines()
    by_id = {}
    for ln in lines:
        m = re.search(r"\[Mac\](0101[AB])-", ln)
        assert m, ln
        by_id[m.group(1)] = ln
    return by_id


def test_dry_run_shows_resolved_models(sandbox):
    r = run(sandbox, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "模型 sonnet（默认）" in r.stdout
    assert "模型 opus（opener设置行）" in r.stdout
    assert not sandbox["calls"].exists()


def test_unknown_model_blocks_dry_run(sandbox):
    r = run(sandbox, "--dry-run", plan_text=BAD)
    assert r.returncode == 13, r.stdout + r.stderr
    assert "GPT9" in r.stdout or "gpt9" in r.stdout


def test_real_run_passes_models_and_subagent_env(sandbox):
    r = run(sandbox, "--yes", "--full-auto")
    assert r.returncode == 0, r.stdout + r.stderr
    c = calls_for(sandbox)
    assert "--model sonnet" in c["0101A"] and "SUB=sonnet" in c["0101A"]
    assert "--model opus" in c["0101B"] and "SUB=sonnet" in c["0101B"]
    assert "--strict-mcp-config" in c["0101A"] and "--strict-mcp-config" in c["0101B"]   # Phase 5：无头默认不载 MCP
    results = next((sandbox["repo"] / ".claude" / "handoff").glob("lanes-*/results.tsv"))
    rows = {l.split("\t")[1]: l.split("\t") for l in results.read_text(encoding="utf-8").splitlines()}
    assert rows["0101A"][2] == "OK" and rows["0101A"][5] == "sonnet"
    assert rows["0101B"][5] == "opus"


def test_cli_model_overrides_everything(sandbox):
    r = run(sandbox, "--yes", "--full-auto", "--model", "haiku")
    assert r.returncode == 0, r.stdout + r.stderr
    c = calls_for(sandbox)
    assert "--model haiku" in c["0101A"] and "--model haiku" in c["0101B"]


def test_mcp_on_opt_in_skips_strict_flag(sandbox):
    plan = PLAN.replace("｜ 模型: Opus", "｜ 模型: Opus ｜ MCP: on")
    r = run(sandbox, "--yes", "--full-auto", plan_text=plan)
    assert r.returncode == 0, r.stdout + r.stderr
    c = calls_for(sandbox)
    assert "--strict-mcp-config" in c["0101A"]
    assert "--strict-mcp-config" not in c["0101B"]
