"""`docs/openers/run-lanes.sh` 的机器判据闸（0920K）。

泳道自报 `OPENER_DONE` 不算数：收口时脚本按条目 id 找 `docs/openers/<id>-*.md`，取 `## 机器判据` 节后
第一个 ```bash 块，先过安全预检（黑名单命中 ⇒ GATE-UNSAFE、⛔ 不执行），再在仓库根跑（默认 60s 超时）；
退出码非 0 或超时 ⇒ GATE-FAIL。GATE-FAIL／GATE-UNSAFE 与 FAIL 同处置：不 mark_done、停本泳道。
钉死：
  ① 块 exit 0 ⇒ 状态维持 OK
  ② 块 exit 1 ⇒ GATE-FAIL，本泳道后续条不跑、原条标注不摘
  ③ 无「## 机器判据」节／glob 匹配数 ≠ 1 ⇒ 维持原判，不拦（向后兼容）
  ④ 块内含 git push ⇒ GATE-UNSAFE，且块**没被执行**
  ⑤ 块 sleep 超过时限 ⇒ GATE-FAIL（HR_LANE_GATE_TIMEOUT 调小，⛔ 不真等 60 秒）
隔离方式同 test_run_lanes_relay.py：脚本拷到临时目录、REPO 指向临时仓库、PATH 前插假 claude（恒输出 OPENER_DONE）。
⛔ 不为验证起真泳道、不碰真实仓库。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "docs" / "openers" / "run-lanes.sh"

PLAN = """# 测试编排

> 泳道：甲
> 无头豁免注明
```
[Mac]0101A-建造
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（测试）｜ 工作区: 仓库根 ｜ 派发: run-lanes.sh
正文见 `docs/openers/0101A-建造.md`，逐节照做。
```

> 泳道：甲
> 无头豁免注明
```
[Mac]0101B-后续
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（文档）｜ 工作区: 仓库根 ｜ 派发: run-lanes.sh
写文档
```
"""


def _opener(gate_block: str | None, extra_section: str = "") -> str:
    head = "# [Mac]0101A opener\n\n## 一、正文\n\n照做。\n\n"
    if gate_block is None:
        return head + extra_section
    return head + "## 机器判据\n\n```bash\n" + gate_block + "\n```\n\n## 六、并发协议\n\n略。\n" + extra_section


def _build_sandbox(tmp_path: Path, opener_text: str | None, extra_files: dict[str, str] | None = None):
    repo = tmp_path / "repo"
    (repo / ".claude" / "handoff").mkdir(parents=True)
    (repo / "docs" / "openers").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    if opener_text is not None:
        (repo / "docs" / "openers" / "0101A-建造.md").write_text(opener_text, encoding="utf-8")
    for name, text in (extra_files or {}).items():
        (repo / "docs" / "openers" / name).write_text(text, encoding="utf-8")
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
        f'echo "ARGS $* PWD=$PWD" >> "{calls}"\n'
        "cat > /dev/null\n"
        'python3 -c "import json; print(json.dumps({\\"total_cost_usd\\":0.01,\\"num_turns\\":2,'
        '\\"usage\\":{\\"input_tokens\\":10,\\"output_tokens\\":20,\\"cache_read_input_tokens\\":30,'
        '\\"cache_creation_input_tokens\\":40},\\"result\\":\\"OPENER_DONE\\"}))"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("HR_LANE_")}
    env = dict(base_env, RUN_LANES_COPY="1", PATH=f"{bindir}:{os.environ['PATH']}")
    return {"repo": repo, "script": script, "plan": plan, "calls": calls, "env": env}


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
    if not sb["calls"].exists():
        return []
    return [re.search(r"\[Mac\]([^ ]+?)-", l).group(1) for l in sb["calls"].read_text(encoding="utf-8").splitlines()]


def lane_tag_kept(sb) -> bool:
    return "> 泳道：甲" in sb["plan"].read_text(encoding="utf-8").split("[Mac]0101A-建造")[0]


def test_gate_exit_zero_keeps_ok_and_runs_in_repo_root(tmp_path):
    # 判据块用相对路径核验 ⇒ 只有在仓库根跑才成立
    sb = _build_sandbox(tmp_path, _opener('[ -f docs/openers/0101A-建造.md ] || { echo "opener 缺失"; exit 1; }\necho "✓ 全过"\nexit 0'))
    r = run(sb)
    assert r.returncode == 0, r.stdout + r.stderr
    rows, all_rows, logdir = results(sb)
    assert rows["0101A"][2] == "OK" and rows["0101B"][2] == "OK"
    assert all(len(x) == 13 for x in all_rows)
    assert call_ids(sb) == ["0101A", "0101B"]
    assert not lane_tag_kept(sb)                                      # 过闸 ⇒ 照常摘标注
    assert "🚦 机器判据：1 条过 / 0 条不过" in r.stdout
    assert (logdir / "gate-0101A.sh").exists()


def test_gate_exit_one_is_gate_fail_and_stops_lane(tmp_path):
    sb = _build_sandbox(tmp_path, _opener('echo "✗ 判据 X 实测 0"\nexit 1'))
    r = run(sb)
    assert r.returncode != 0
    rows, all_rows, logdir = results(sb)
    assert rows["0101A"][2] == "GATE-FAIL"
    assert "0101B" not in rows                                        # 本泳道后续条不跑
    assert call_ids(sb) == ["0101A"]
    assert all(len(x) == 13 for x in all_rows)
    assert lane_tag_kept(sb)                                          # 原条未被 mark_done
    log = (logdir / "甲-0101A.log").read_text(encoding="utf-8")
    assert "✗ 判据 X 实测 0" in log                                     # 判据块输出末 30 行追加进日志
    assert "GATE-FAIL" in r.stdout and "🚦 机器判据：0 条过 / 1 条不过（0101A）" in r.stdout


def test_missing_section_keeps_verdict(tmp_path):
    sb = _build_sandbox(tmp_path, _opener(None, extra_section="## 五、判据\n\n```bash\nexit 1\n```\n"))
    r = run(sb)
    assert r.returncode == 0, r.stdout + r.stderr
    rows, _, _ = results(sb)
    assert rows["0101A"][2] == "OK" and rows["0101B"][2] == "OK"
    assert "🚦 本批无机器判据块" in r.stdout


def test_ambiguous_glob_keeps_verdict(tmp_path):
    # 两个文件都匹配 0101A-*.md ⇒ 视为无判据，不拦（哪怕其中的块会 exit 1）
    sb = _build_sandbox(tmp_path, _opener("exit 1"), extra_files={"0101A-建造-旧版.md": _opener("exit 1")})
    r = run(sb)
    assert r.returncode == 0, r.stdout + r.stderr
    rows, _, _ = results(sb)
    assert rows["0101A"][2] == "OK" and rows["0101B"][2] == "OK"
    assert "🚦 本批无机器判据块" in r.stdout


def test_missing_opener_file_keeps_verdict(tmp_path):
    sb = _build_sandbox(tmp_path, None)
    r = run(sb)
    assert r.returncode == 0, r.stdout + r.stderr
    rows, _, _ = results(sb)
    assert rows["0101A"][2] == "OK" and rows["0101B"][2] == "OK"


def test_blacklisted_block_is_gate_unsafe_and_not_executed(tmp_path):
    marker = tmp_path / "GATE_RAN"
    sb = _build_sandbox(tmp_path, _opener(f'touch "{marker}"\ngit push origin main\nexit 0'))
    r = run(sb)
    assert r.returncode != 0
    rows, _, logdir = results(sb)
    assert rows["0101A"][2] == "GATE-UNSAFE"
    assert not marker.exists()                                        # 块没跑
    assert "0101B" not in rows and call_ids(sb) == ["0101A"]
    assert lane_tag_kept(sb)
    assert "git push" in (logdir / "甲-0101A.log").read_text(encoding="utf-8")   # 记账命中的模式
    assert "🚦 机器判据：0 条过 / 1 条不过（0101A）" in r.stdout


def test_gate_timeout_is_gate_fail(tmp_path):
    sb = _build_sandbox(tmp_path, _opener("sleep 90\nexit 0"))
    sb["env"]["HR_LANE_GATE_TIMEOUT"] = "2"
    t0 = time.monotonic()
    r = run(sb)
    assert time.monotonic() - t0 < 60
    assert r.returncode != 0
    rows, _, logdir = results(sb)
    assert rows["0101A"][2] == "GATE-FAIL"
    assert "0101B" not in rows
    assert lane_tag_kept(sb)
    assert "超时" in (logdir / "甲-0101A.log").read_text(encoding="utf-8")


def test_rm_inside_backticks_is_gate_unsafe(tmp_path):
    marker = tmp_path / "GATE_RAN"
    sb = _build_sandbox(tmp_path, _opener(f'touch "{marker}"\nx=`rm /tmp/never`\nexit 0'))
    r = run(sb)
    rows, _, _ = results(sb)
    assert rows["0101A"][2] == "GATE-UNSAFE" and not marker.exists() and r.returncode != 0


def test_numbered_heading_is_recognised(tmp_path):
    # 真实 opener 的节名带序号（如本条 0920K 的「## 五、机器判据」），必须同样被认出来
    text = _opener("exit 1").replace("## 机器判据", "## 五、机器判据")
    sb = _build_sandbox(tmp_path, text)
    r = run(sb)
    rows, _, _ = results(sb)
    assert rows["0101A"][2] == "GATE-FAIL" and r.returncode != 0
