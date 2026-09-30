"""`docs/openers/run-lanes.sh` codex 引擎的模型分级与 argv 形状断言（2026-09-29 迁移）。

与 tests/test_run_lanes_model.py 同构，只是桩从假 `claude` 换成假 `codex`、引擎默认走
codex（不设 HR_AGENT_ENGINE，验「默认即 codex」这一条）。钉死：
  ① 默认档（sonnet）→ `-m deepseek-v4-pro -c model_reasoning_effort=high`
  ② 「模型: Opus」→ `-m deepseek-v4-pro -c model_reasoning_effort=max`
  ③ 沙箱与审批：`--sandbox workspace-write`；`--full-auto` ⇒ `-c approval_policy=never`，
     非全自动 ⇒ `on-request`；`-c forced_login_method=chatgpt`（本机 auth.json 对齐）
  ④ 子代理经 `-c agents.default_subagent_model=deepseek-v4-pro`
  ⑤ JSONL 收敛：哨兵（含 **OPENER_DONE** 形态）与 7 列用量进 results.tsv；
     非 JSON 输出回退成纯文本（哨兵仍判到，用量列全 "-"）
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "docs" / "openers" / "run-lanes.sh"

PLAN = """# 测试编排（codex 引擎）

> 泳道：甲
> 无头豁免注明
```
[Mac]0101A-无模型条目
【设置】执行环境: Codex ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（测试）｜ 工作区: 仓库根 ｜ 派发: run-lanes.sh
做点事
```

> 泳道：乙
> 无头豁免注明
```
[Mac]0101B-声明Opus条目
【设置】执行环境: Codex ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（测试）｜ 工作区: 仓库根 ｜ 派发: run-lanes.sh ｜ 模型: Opus
做点难事
```
"""


def _build_sandbox(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "handoff").mkdir(parents=True)
    (repo / ".git").mkdir()
    script = tmp_path / "run-lanes.sh"
    text = SRC.read_text(encoding="utf-8")
    text, n = re.subn(r'^REPO="[^"]*"$', f'REPO="{repo}"', text, count=1, flags=re.M)
    assert n == 1
    script.write_text(text, encoding="utf-8")
    # run-lanes.sh 的 codex 引擎引用 $REPO/scripts/codex_jsonl_summary.py（单源解析器），
    # 测试隔离仓库里没有它——把真身拷一份进 tmp 仓库，行为与生产一致。
    (repo / "scripts").mkdir()
    (repo / "scripts" / "codex_jsonl_summary.py").write_text(
        (SRC.parents[2] / "scripts" / "codex_jsonl_summary.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    fake = bindir / "codex"
    # codex exec --json 的 JSONL 假返回：一条 agent_message（哨兵）＋一条 turn.completed 用量。
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "ARGS $* LANE=${{HR_HEADLESS_LANE:-unset}}" >> "{calls}"\n'
        "cat >/dev/null\n"
        "cat <<'JSON'\n"
        '{"type":"thread.started","thread_id":"t1"}\n'
        '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"OPENER_DONE"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":20,'
        '"cached_input_tokens":30,"cache_write_input_tokens":40}}\n'
        "JSON\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    plan = tmp_path / "plan.md"
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("HR_LANE_")}
    # ⛔ 不设 HR_AGENT_ENGINE：验默认即 codex。
    env = dict(base_env, RUN_LANES_COPY="1", PATH=f"{bindir}:{os.environ['PATH']}")
    return {"repo": repo, "script": script, "plan": plan, "calls": calls, "env": env}


@pytest.fixture
def sandbox(tmp_path: Path):
    return _build_sandbox(tmp_path)


def run(sb, *args, plan_text=PLAN):
    sb["plan"].write_text(plan_text, encoding="utf-8")
    return subprocess.run(
        ["bash", str(sb["script"]), "--plan", str(sb["plan"]), "--stagger", "0", *args],
        env=sb["env"], capture_output=True, text=True, timeout=120,
    )


def calls_for(sb):
    lines = sb["calls"].read_text(encoding="utf-8").splitlines()
    # codex argv 没有 claude 的 `-n [Mac]id-title` 标题旗标，且两泳道并发、起跑顺序不定——
    # 按被测行为（reasoning 档位）区分：high＝默认 sonnet 档（0101A），max＝Opus 档（0101B）。
    out: dict[str, str] = {}
    for ln in lines:
        if "model_reasoning_effort=high" in ln:
            out["0101A"] = ln
        elif "model_reasoning_effort=max" in ln:
            out["0101B"] = ln
    return out


def test_dry_run_defaults_to_codex_engine(sandbox):
    r = run(sandbox, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "执行引擎：codex" in r.stdout
    assert "模型 sonnet（默认）" in r.stdout
    assert "模型 opus（opener设置行）" in r.stdout
    assert not sandbox["calls"].exists()


def test_real_run_passes_codex_argv_and_usage_columns(sandbox):
    r = run(sandbox, "--yes", "--full-auto")
    assert r.returncode == 0, r.stdout + r.stderr
    c = calls_for(sandbox)
    a, b = c["0101A"], c["0101B"]
    for ln in (a, b):
        assert "--sandbox workspace-write" in ln
        assert "-c approval_policy=never" in ln
        assert "-c forced_login_method=chatgpt" in ln
        assert "LANE=1" in ln
    assert "-m deepseek-v4-pro -c model_reasoning_effort=high" in a
    assert "-m deepseek-v4-pro -c model_reasoning_effort=max" in b
    assert "-c agents.default_subagent_model=deepseek-v4-pro" in a
    results = next((sandbox["repo"] / ".claude" / "handoff").glob("lanes-*/results.tsv"))
    rows = {l.split("\t")[1]: l.split("\t") for l in results.read_text(encoding="utf-8").splitlines()}
    assert rows["0101A"][2] == "OK" and rows["0101A"][5] == "sonnet"
    assert rows["0101B"][5] == "opus"
    # 列序同 claude 引擎：cost("-"), in, out, cache_read, cache_write, turns
    assert rows["0101A"][6:12] == ["-", "10", "20", "30", "40", "1"]
    # 第 13 列（upeak）：codex 的 JSONL 没有单轮上下文峰值可算 ⇒ 恒 "-"（0930I／[Mac]0930R）
    assert rows["0101A"][12] == "-" and rows["0101B"][12] == "-"


def test_codex_peak_column_stays_dash_and_no_150k_false_alarm(sandbox):
    """复刻 0930F 的累计 usage（input 6454677／cached 6394496）。

    旧实现把 `input + cache_read + cache_write` 当峰值 ⇒ 12,849,173 ≥150k，整批误报
    「越过 150k 转场线」；新口径下 codex 泳道该列恒 `-`、播报跳过它。⛔ 不许改回累计量充数。
    """
    fake = Path(sandbox["env"]["PATH"].split(":")[0]) / "codex"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "cat <<'JSON'\n"
        '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"OPENER_DONE"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":6454677,"output_tokens":52930,'
        '"cached_input_tokens":6394496,"cache_write_input_tokens":0}}\n'
        "JSON\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    r = run(sandbox, "--yes", "--full-auto")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "150k" not in r.stdout
    results = next((sandbox["repo"] / ".claude" / "handoff").glob("lanes-*/results.tsv"))
    rows = {l.split("\t")[1]: l.split("\t") for l in results.read_text(encoding="utf-8").splitlines()}
    assert rows["0101A"][7] == "6454677"  # 累计用量仍照记在第 8 列
    assert rows["0101A"][12] == "-"       # 峰值列留 "-"，不拿累计量充数


def test_non_full_auto_uses_on_request_approval(sandbox):
    r = run(sandbox, "--yes", "--only", "0101A")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-c approval_policy=on-request" in calls_for(sandbox)["0101A"]


def test_bold_sentinel_counts_as_done(sandbox):
    fake = sandbox["script"].parent / "bin" / "codex"
    fake.write_text(fake.read_text(encoding="utf-8").replace('"OPENER_DONE"', '"**OPENER_DONE**"'), encoding="utf-8")
    r = run(sandbox, "--yes", "--full-auto", "--only", "0101A")
    assert r.returncode == 0, r.stdout + r.stderr
    results = next((sandbox["repo"] / ".claude" / "handoff").glob("lanes-*/results.tsv"))
    assert results.read_text(encoding="utf-8").split("\t")[2] == "OK"


def test_non_json_stdout_falls_back_to_plain_text(sandbox):
    fake = sandbox["script"].parent / "bin" / "codex"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo OPENER_DONE\n",
        encoding="utf-8",
    )
    r = run(sandbox, "--yes", "--full-auto", "--only", "0101A")
    assert r.returncode == 0, r.stdout + r.stderr
    results = next((sandbox["repo"] / ".claude" / "handoff").glob("lanes-*/results.tsv"))
    rows = results.read_text(encoding="utf-8").split("\t")
    assert rows[2] == "OK"
    assert rows[6:12] == ["-", "-", "-", "-", "-", "-"]


def test_codex_resolved_via_hr_codex_bin_when_not_on_path(sandbox):
    """launchd 的 PATH 没有 codex（2026-09-29 实测）：HR_CODEX_BIN 绝对路径必须能兜住。"""
    fake = sandbox["script"].parent / "bin" / "codex"
    sandbox["env"]["PATH"] = "/usr/bin:/bin"
    sandbox["env"]["HR_CODEX_BIN"] = str(fake)
    r = run(sandbox, "--yes", "--full-auto", "--only", "0101A")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls_for(sandbox)["0101A"]


GATE_PLAN = """# 测试编排（worktree + 机器判据）

> 泳道：甲
> 无头豁免注明
```
[Mac]0101G-带判据的worktree条
【设置】执行环境: Codex ｜ Session: 新开 ｜ 分支: lane-gate-test ｜ worktree: ✅ 勾（测试）｜ 工作区: .claude/worktrees/lane-gate-test ｜ 派发: run-lanes.sh
干活
```
"""


def test_codex_worktree_gate_runs_inside_worktree(sandbox):
    """0930G 回归：codex worktree 泳道的 `## 机器判据` 必须在**泳道 worktree 内**跑。

    判据块故意写成历史 opener 的形态——首行硬编码主仓绝对路径、检查只存在于泳道产物的文件：
    修复前它在 main 上跑（产物还没合回）⇒ 假阴 GATE-FAIL（0930E 实证，Q-63）；
    修复后执行器改写 `cd` 并在 worktree 内跑 ⇒ OK。
    """
    import shutil

    repo = sandbox["repo"]
    shutil.rmtree(repo / ".git")

    def g(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    (repo / "README.md").write_text("x", encoding="utf-8")
    opener = repo / "docs" / "openers" / "0101G-带判据的worktree条.md"
    opener.parent.mkdir(parents=True)
    opener.write_text(
        "## 机器判据\n\n```bash\ncd /Users/paulshao/Projects/HumanResource\ntest -f gate-marker.txt\n```\n",
        encoding="utf-8",
    )
    g("add", "README.md", "docs/openers/0101G-带判据的worktree条.md")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    # 假 codex：在当前目录（= 泳道 worktree）写 marker 后输出哨兵
    fake = sandbox["script"].parent / "bin" / "codex"
    fake.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\ntouch gate-marker.txt\necho OPENER_DONE\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    r = run(sandbox, "--yes", "--full-auto", "--only", "0101G", plan_text=GATE_PLAN)
    assert r.returncode == 0, r.stdout + r.stderr
    results = next((repo / ".claude" / "handoff").glob("lanes-*/results.tsv"))
    rows = {l.split("\t")[1]: l.split("\t") for l in results.read_text(encoding="utf-8").splitlines()}
    assert rows["0101G"][2] == "OK", rows["0101G"]
