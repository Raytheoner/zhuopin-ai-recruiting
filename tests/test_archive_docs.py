"""`scripts/archive_docs.py` 行为断言（Token 治理 Phase 3，0916D）。

它会移动编排文件、号池台账与接力文件里的大段内容，错了的代价是「待执行的泳道被搬走」或「历史内容丢失」，
两者都不报错。所以钉死：待执行段不动、只搬已闭环、号池按月轮转、重复运行幂等、守恒（只增指针行不丢行）、
接力文件标记核对只放行「加【已闭环】」。全部在临时目录里跑，⛔ 不碰真实 docs。
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "archive_docs.py"

PLAN = """# OP-0820 · 全量 Opener 编排

> 头部说明

## 🔢 号池台账 —— 出号前必查

| 日期 | 号 | 主题 | 去向 |
|---|---|---|---|
| 08-27 | `[Mac]0827A` | 八月的号 | — |
| 09-15 | `[Mac]0915A` | 九月的号 | — |

## 二、并发协议（备查）

只 add 明确路径

## 第十批（已跑完）

> ✅ 已完成 2026-09-04（OK）· 原泳道 甲
```
[Mac]0904G-旧活
正文
```

## 第十八批 · 当前待执行

> 泳道：乙
```
[Mac]0916Q-待跑的活
正文
```
"""

RELAY = """# 接力

## 开场词（复制即用）

读本文件

## 二、下一步

### ~~① 旧事~~ ✅ 已跑完

旧事细节

### ② 进行中

还没做完

## 🆕 某事件【已闭环】（2026-09-10）

事件细节
### 事件子节
子节细节

## 五、约束

永远保留
"""


def run(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--today", "2026-09-16", *args],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "docs" / "openers").mkdir(parents=True)
    (tmp_path / "docs" / "openers" / "OP-0820-全量编排.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "docs" / "session接力.md").write_text(RELAY, encoding="utf-8")
    return tmp_path


def all_lines(root):
    return Counter(l for p in (root / "docs").rglob("*.md") for l in p.read_text(encoding="utf-8").split("\n"))


def txt(root, rel):
    return (root / rel).read_text(encoding="utf-8")


def test_dry_run_writes_nothing(root):
    before = all_lines(root)
    r = run(root, "--init-ledger")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry-run" in r.stdout and all_lines(root) == before


def test_apply_moves_only_closed_and_conserves_lines(root):
    before = all_lines(root)
    r = run(root, "--init-ledger", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    plan = txt(root, "docs/openers/OP-0820-全量编排.md")
    assert "[Mac]0916Q-待跑的活" in plan and "> 泳道：乙" in plan          # 待执行段不动
    assert "并发协议" in plan and "## 待执行区" in plan
    assert "[Mac]0904G" not in plan
    assert "[Mac]0904G-旧活" in txt(root, "docs/openers/归档/OP-0820-历史批次.md")
    led = txt(root, "docs/openers/号池台账.md")
    assert "[Mac]0915A" in led and "[Mac]0827A" not in led                # 按月轮转
    assert "[Mac]0827A" in txt(root, "docs/openers/归档/号池台账-归档.md")
    relay = txt(root, "docs/session接力.md")
    assert "进行中" in relay and "永远保留" in relay and "开场词" in relay
    assert "旧事细节" not in relay and "事件细节" not in relay and "子节细节" not in relay
    arch = txt(root, "docs/archive/session接力-归档.md")
    assert "旧事细节" in arch and "事件细节" in arch and "子节细节" in arch
    after = all_lines(root)
    lost = before - after
    assert not lost, f"丢行：{list(lost)[:3]}"                          # 只增不减
    extra = after - before
    assert all("〔归档工具〕" in l or l.strip() in ("", "|---|---|---|---|", "| 日期 | 号 | 主题 | 去向 |") or l.startswith("# ") or l.startswith("## ")
               for l in extra), [l for l in extra if "〔归档工具〕" not in l][:5]


def test_idempotent(root):
    assert run(root, "--init-ledger", "--apply").returncode == 0
    snap = all_lines(root)
    r = run(root, "--init-ledger", "--apply")
    assert r.returncode == 0 and all_lines(root) == snap


def test_all_ids_still_greppable(root):
    import re
    pat = re.compile(r"\[Mac\]\d{4}[A-Z]{1,2}")
    ids = lambda: sorted(set(m for p in (root / "docs").rglob("*.md") for m in pat.findall(p.read_text(encoding="utf-8"))))
    before = ids()
    run(root, "--init-ledger", "--apply")
    assert ids() == before


def test_verify_tags_rejects_other_edits(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    p = root / "docs" / "session接力.md"
    p.write_text(RELAY.replace("### ② 进行中", "### ② 进行中【已闭环】"), encoding="utf-8")
    assert run(root, "--verify-tags").returncode == 0
    p.write_text(RELAY.replace("还没做完", "改了正文"), encoding="utf-8")
    assert run(root, "--verify-tags").returncode != 0


def test_scope_plan_leaves_relay_untouched(root):
    relay_before = txt(root, "docs/session接力.md")
    r = run(root, "--init-ledger", "--scope", "plan", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert txt(root, "docs/session接力.md") == relay_before
    assert "[Mac]0904G" not in txt(root, "docs/openers/OP-0820-全量编排.md")
    r = run(root, "--scope", "relay", "--apply")
    assert r.returncode == 0 and "旧事细节" not in txt(root, "docs/session接力.md")
