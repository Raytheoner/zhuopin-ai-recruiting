"""`docs/openers/run-lanes.sh` 的上下文续棒（0920H B 半）。

A 半（context-guard.py 的 PostToolUse 挂点）让无头泳道越线后打
`OPENER_PARTIAL: 上下文转场 | 续棒: 分支=… worktree=… 上一条commit=… 已完成=… 待续=…`；
B 半让 run-lanes.sh 认这条哨兵：状态记 CTX-RELAY（⛔ 不 mark_done），抓齐五字段，在**同泳道队尾**追加
`<原id>续<n>` 续棒条继续跑；上限 HR_LANE_RELAY_MAX（默认 3），超上限记 RELAY-EXHAUSTED 并停本泳道。
钉死：
  ① 含「上下文转场」的 PARTIAL ⇒ CTX-RELAY 且抓齐五字段，续棒条跑 OK 后原条才摘标注
  ② 普通 PARTIAL ⇒ 仍是 PARTIAL、不触发续棒
  ③ 第 4 次越线 ⇒ RELAY-EXHAUSTED，本泳道后续条不跑，原条标注不摘
隔离方式同 test_run_lanes_model.py：脚本拷到临时目录、REPO 指向临时仓库、PATH 前插假 claude
（每次调用把 stdin 正文存盘、按调用序决定输出哨兵）。⛔ 不为验证起真泳道、不碰真实仓库。
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
[Mac]0101A-建造
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: wave-t ｜ worktree: ✅ 勾（写代码）｜ 工作区: .claude/worktrees/wave-t ｜ 派发: run-lanes.sh
正文见 `docs/openers/0101A-建造.md`，逐节照做。
🔴 红线：不碰 .51。
```

> 泳道：甲
> 无头豁免注明
```
[Mac]0101B-后续
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（文档）｜ 工作区: 仓库根 ｜ 派发: run-lanes.sh
写文档
```
"""

RELAY_SENTINEL = ("OPENER_PARTIAL: 上下文转场 | 续棒: 分支=wave-t worktree=.claude/worktrees/wave-t "
                  "上一条commit=abc1234 已完成=A半四条测试全绿已提交 待续=B半从解析函数开始")


def _build_sandbox(tmp_path: Path, relay_times: int, partial_plain: bool = False):
    repo = tmp_path / "repo"
    (repo / ".claude" / "handoff").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    script = tmp_path / "run-lanes.sh"
    text = SRC.read_text(encoding="utf-8")
    text, n = re.subn(r'^REPO="[^"]*"$', f'REPO="{repo}"', text, count=1, flags=re.M)
    assert n == 1
    script.write_text(text, encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    bodies = tmp_path / "bodies"
    bodies.mkdir()
    if partial_plain:
        first = "OPENER_PARTIAL: 留步：等 .51 窗口"
    else:
        first = RELAY_SENTINEL
    fake = bindir / "claude"
    # 第 1..relay_times 次调用输出转场哨兵（或普通 PARTIAL），之后输出 OPENER_DONE。
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'n=$(( $(wc -l < "{calls}" 2>/dev/null || echo 0) + 1 ))\n'
        f'echo "ARGS $* LANE=${{HR_HEADLESS_LANE:-unset}} PWD=$PWD" >> "{calls}"\n'
        f'cat > "{bodies}/$n.txt"\n'
        f'if [[ $n -le {relay_times} ]]; then r="{first}"; else r="OPENER_DONE"; fi\n'
        'python3 -c "import json,sys; print(json.dumps({\\"total_cost_usd\\":0.01,\\"num_turns\\":2,'
        '\\"usage\\":{\\"input_tokens\\":10,\\"output_tokens\\":20,\\"cache_read_input_tokens\\":30,'
        '\\"cache_creation_input_tokens\\":40,\\"iterations\\":[{\\"input_tokens\\":1000,'
        '\\"cache_read_input_tokens\\":151000}]},\\"result\\":sys.argv[1]}))" "$r"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("HR_LANE_")}
    env = dict(base_env, RUN_LANES_COPY="1", PATH=f"{bindir}:{os.environ['PATH']}")
    return {"repo": repo, "script": script, "plan": plan, "calls": calls, "bodies": bodies, "env": env}


def run(sb):
    return subprocess.run(
        ["bash", str(sb["script"]), "--plan", str(sb["plan"]), "--stagger", "0", "--yes", "--full-auto"],
        env=sb["env"], capture_output=True, text=True, timeout=180,
    )


def results(sb):
    f = next((sb["repo"] / ".claude" / "handoff").glob("lanes-*/results.tsv"))
    rows = [l.split("\t") for l in f.read_text(encoding="utf-8").splitlines()]
    return {r[1]: r for r in rows}, rows, f.parent


def call_ids(sb):
    return [re.search(r"\[Mac\]([^ ]+?)-", l).group(1) for l in sb["calls"].read_text(encoding="utf-8").splitlines()]


def test_ctx_relay_spawns_successor_in_same_lane_and_marks_root_done_after(tmp_path):
    sb = _build_sandbox(tmp_path, relay_times=1)
    r = run(sb)
    assert r.returncode == 0, r.stdout + r.stderr
    rows, all_rows, logdir = results(sb)
    assert rows["0101A"][2] == "CTX-RELAY"
    assert rows["0101A续1"][2] == "OK"
    assert rows["0101B"][2] == "OK"
    assert all(len(x) == 13 for x in all_rows)                       # 续棒条也占一行、13 列、upeak 照记
    assert rows["0101A续1"][12] == "152000"
    # 队尾顺序：原条 → 续棒条 → 泳道内后续条
    assert call_ids(sb) == ["0101A", "0101A续1", "0101B"]
    # 五字段抓齐并落日志
    log = (logdir / "甲-0101A.log").read_text(encoding="utf-8")
    assert "relay: 分支=wave-t\tworktree=.claude/worktrees/wave-t\t上一条commit=abc1234\t已完成=A半四条测试全绿已提交\t待续=B半从解析函数开始" in log
    # 续棒条正文：带前置三字段、待续、不重做已完成、原 opener 正文引用与红线指针；沿用原【设置】行（同 worktree）
    body = (sb["bodies"] / "2.txt").read_text(encoding="utf-8")
    assert body.splitlines()[0].endswith("[Mac]0101A续1-建造") or "[Mac]0101A续1-建造" in body
    assert "分支=wave-t" in body and "worktree=.claude/worktrees/wave-t" in body and "上一条commit=abc1234" in body
    assert "待续" in body and "B半从解析函数开始" in body and "不重做" in body and "A半四条测试全绿已提交" in body
    assert "docs/openers/0101A-建造.md" in body and "红线" in body
    assert body.count("【设置】") == 1 and ".claude/worktrees/wave-t" in body
    # 续棒条在同一 worktree 里起
    calls = sb["calls"].read_text(encoding="utf-8").splitlines()
    assert calls[0].endswith("wave-t") and calls[1].endswith("wave-t")
    # 原条标注只在续棒条跑 OK 之后才摘；摘的是原条 0101A
    plan = sb["plan"].read_text(encoding="utf-8")
    assert "✅ 已完成" in plan.split("[Mac]0101A-建造")[0]
    assert "续棒1" in plan.split("[Mac]0101A-建造")[0]
    assert "🔁 本批自动续棒 1 次：0101A → 0101A续1" in r.stdout


def test_plain_partial_does_not_relay(tmp_path):
    sb = _build_sandbox(tmp_path, relay_times=1, partial_plain=True)
    r = run(sb)
    assert r.returncode == 0, r.stdout + r.stderr
    rows, _, _ = results(sb)
    assert rows["0101A"][2] == "PARTIAL"
    assert "0101A续1" not in rows
    assert call_ids(sb) == ["0101A", "0101B"]
    assert "🔁 本批无上下文续棒" in r.stdout


def test_fourth_crossing_is_relay_exhausted_and_stops_lane(tmp_path):
    sb = _build_sandbox(tmp_path, relay_times=99)
    r = run(sb)
    assert r.returncode != 0
    rows, _, _ = results(sb)
    assert [rows[i][2] for i in ("0101A", "0101A续1", "0101A续2")] == ["CTX-RELAY"] * 3
    assert rows["0101A续3"][2] == "RELAY-EXHAUSTED"
    assert "0101A续4" not in rows and "0101B" not in rows          # ⛔ 不无限续；本泳道后续条不跑
    assert call_ids(sb) == ["0101A", "0101A续1", "0101A续2", "0101A续3"]
    assert "> 泳道：甲" in sb["plan"].read_text(encoding="utf-8").split("[Mac]0101A-建造")[0]   # 原条标注不摘
    assert "RELAY-EXHAUSTED" in r.stdout and "0101A续3" in r.stdout
    assert "🔁 本批自动续棒 3 次：0101A → 0101A续1 → 0101A续2 → 0101A续3" in r.stdout


def test_relay_max_from_env(tmp_path):
    sb = _build_sandbox(tmp_path, relay_times=99)
    sb["env"]["HR_LANE_RELAY_MAX"] = "1"
    r = run(sb)
    rows, _, _ = results(sb)
    assert rows["0101A"][2] == "CTX-RELAY" and rows["0101A续1"][2] == "RELAY-EXHAUSTED"
    assert "0101A续2" not in rows and r.returncode != 0
