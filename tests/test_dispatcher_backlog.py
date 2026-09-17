"""`scripts/dispatcher_backlog.py` 行为断言（任务驱动 workflow R3，0917AL）。

台账是调度器的输入：写错一个状态，调度器要么重跑已合入的活、要么把不可代项排进无头泳道。
所以钉死四件事：① 小样本仓库生成稳定且幂等（两次字节相同）；② 🔴 不可代项 ⇒ 阻塞类型「决策」且永不 ready；
③ 合并不覆盖——台账状态优先、真身不一致记 conflicts，只有 `--resolve-conflicts truth` 才改；
④ 单元有 plan ⇒ 拆段成为调度单元、无 plan ⇒ spec-to-plan 任务 ready。全部在临时目录里跑，⛔ 不碰真实 docs。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dispatcher_backlog.py"

ROADMAP = """# 路线图

## 二、业务路线图

| 阶段 | 内容 | 状态（真身） | 卡在哪 | 谁推 |
|---|---|---|---|---|
| **M0 通信底座** | 值守通道 | 63/67 | 核证 | 泳道 |
| **M2 简历解析** | 解析管线 | **已立包 0/69** | 三个硬前置 | Cowork |
| **M3 实时语音面试** | prep/live/post | 未启动 | 依赖 M2 真实数据 | M2 上线后 |

## 四、自动化缺口

| # | 缺口 | 后果 | 补法 | 状态 |
|---|---|---|---|---|
| **G1** | 附件不进归档 | 白发 | 接 `tools/liaison/bridge.py` | 🚀 `0917W` |
| **G4** | 无调度基础设施 | 提醒做不了 | 外部调度器 | ⏸ 待你定落点 |
| **G7** | 搁置包悬挂 | 进度表挂着 | 撤包 | ✅ 已完成 |
"""

TASKS_M2 = """**进度：1/6**

## 0. 前置门槛

- [ ] 0.2 🔴 **合规验收 #1 启动**（Shao Peishen 发起）。判据：三份文档落 `docs/compliance/`。阻塞 U1 的 2.3 开闸
- [x] 0.5 ✅ 已裁决

## 1. U0 模型对比定型

- [x] 1.1 冒烟安装，记录到 `docs/m2-model-comparison.md`
- [ ] 1.2 准备样本 ⏸ 留步：真实脱敏样本待 U0 计划待裁决 #2
- [ ] 1.7 🔴 **定型确认**（Shao Peishen 签认）。判据：文档末尾有「已确认 <日期>」

## 2. U1 数据模型

- [ ] 2.1 `app/storage/db.py` 新增 `candidate` 表
- [ ] 2.3 入库闸（前置：0.2 通过 + 2.1 绿）
- ~~2.9 bias 夹具~~ ⚰️ 已移出本包
"""

PLAN_U0 = """# U0 模型对比 实现计划

## 文件结构

- `scripts/compare_models_m2.py`
- `tests/test_compare_models_m2.py`

## 建议拆段点

- **第 1 条：Task 1–2**
- **第 2 条：Task 3–5**

### Task 1: schema
### Task 2: 分片
### Task 3: 指标
### Task 4: 抽取
### Task 5: 报告

## 待裁决（无人值守下不自行拍板）

1. **1.7 定型确认**——Shao Peishen 签认。
2. **真实脱敏样本来源**：由 Shao Peishen 指定。
"""

RELAY = """# 接力

## 五、【下一步】

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **R-1** | 已完成的事 | CC | ✅ **已完成** | — | — |
| **R-6** | 8.8 观察窗 09-24 到期收尾 | Cowork 派泳道 | ⏸ **待 09-24 到期后派发** | 观察结论落档 | 悬置 |
| **H-1** | `.51` 现网重建表 | **另起 session**（属发版决定·不可代） | ⏸ **待派发** | 表结构含 CHECK | 现网旧结构 |
| **H-4** | TD-29 剩两条 | 下一批泳道 | ⏸ **待派发** | `conftest` 收编完成 | 告警文案错 |

## 拆件会话

- 【谁做：Shao Peishen】【状态：待人】
  【判据：两份草稿要不要发给汤丽萍，请定】
  【不做会怎样：文件留在工作区】
"""

LEDGER = """# 号池台账

| 日期 | 号 | 主题 | 去向 |
|---|---|---|---|
| 09-17 | `[Mac]0917AJ` | U0 建造 Task 7–8 | 编排文件无头块，经 launchd 发车 |
| 09-17 | `[Mac]0917AJ` | U0 建造结果 | ✅ 已完成 |
| 09-17 | `[Mac]0917AK` | R1 发车排队 | 编排文件无头块，待 0917AJ 收敛后发车 |
| 09-17 | `[Mac]0917AL` | R3 任务台账 | 编排文件无头块，待 0917AZ 收敛后发车 |
| 09-16 | `[Mac]0916B` | 历史号 | 聊天里派发 |
| 09-03 | `[Mac]0903A` | `.51` 四步验证 | ⏸ 仍未闭合，发版决定不可代，Shao Peishen |
"""


def make_repo(tmp: Path, with_plan: bool = True) -> Path:
    repo = tmp / "repo"
    (repo / "docs/roadmap").mkdir(parents=True)
    (repo / "docs/openers").mkdir(parents=True)
    (repo / "docs/superpowers/plans").mkdir(parents=True)
    (repo / "openspec/changes/m2-resume-parse-and-rank").mkdir(parents=True)
    (repo / "docs/roadmap/HR项目实施路线图与构建自动化流程.md").write_text(ROADMAP, encoding="utf-8")
    (repo / "docs/session接力.md").write_text(RELAY, encoding="utf-8")
    (repo / "docs/openers/号池台账.md").write_text(LEDGER, encoding="utf-8")
    (repo / "openspec/changes/m2-resume-parse-and-rank/tasks.md").write_text(TASKS_M2, encoding="utf-8")
    if with_plan:
        (repo / "docs/superpowers/plans/2026-09-17-m2-unit0-model-comparison.md").write_text(PLAN_U0, encoding="utf-8")
    return repo


def run(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *extra],
        capture_output=True, text=True, check=True,
    )


def load(repo: Path) -> dict:
    return yaml.safe_load((repo / "docs/roadmap/任务台账.yaml").read_text(encoding="utf-8"))


def by_id(doc: dict) -> dict[str, dict]:
    return {t["id"]: t for t in doc["tasks"]}


# ── ① 稳定、幂等 ──


def test_generation_is_idempotent_byte_for_byte(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    first = (repo / "docs/roadmap/任务台账.yaml").read_bytes()
    run(repo)
    second = (repo / "docs/roadmap/任务台账.yaml").read_bytes()
    assert first == second
    doc = yaml.safe_load(first)
    assert doc["summary"]["条目数"] == len(doc["tasks"])
    assert doc["summary"]["conflicts"] == []


def test_dry_run_prints_summary_without_writing(tmp_path):
    repo = make_repo(tmp_path)
    out = run(repo, "--dry-run").stdout
    assert "条目数=" in out and "ready=" in out
    assert not (repo / "docs/roadmap/任务台账.yaml").exists()


# ── ② 不可代项 ⇒ 决策阻塞、永不 ready ──


def test_red_marked_items_are_decision_blocked_and_never_ready(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    for tid in ("m2-resume-parse-and-rank/0.2", "m2-resume-parse-and-rank/1.7"):
        assert t[tid]["状态"] == "阻塞"
        assert t[tid]["阻塞类型"] == "决策"
        assert tid not in doc["summary"]["ready"]
    assert t["m2-resume-parse-and-rank/1.7"]["产出判据"] == "文档末尾有「已确认 <日期>」"
    # 留步／待裁决：决策优先于外部
    assert t["m2-resume-parse-and-rank/1.2"]["阻塞类型"] == "决策"
    # plan 里的待裁决 ⇒ 决策
    assert t["plan:2026-09-17-m2-unit0-model-comparison/待裁决#1"]["阻塞类型"] == "决策"
    assert t["plan:2026-09-17-m2-unit0-model-comparison/待裁决#2"]["状态"] == "阻塞"
    # 号池里 ⏸ + Shao Peishen ⇒ 决策
    assert t["opener:[Mac]0903A"]["阻塞类型"] == "决策"
    # 接力 谁做 写「不可代」⇒ 即便「待派发」也是决策阻塞
    assert t["relay:H-1"]["状态"] == "阻塞" and t["relay:H-1"]["阻塞类型"] == "决策"
    assert t["relay:H-4"]["状态"] == "待开" and "relay:H-4" in doc["summary"]["ready"]
    # 决策类条目一律不出现在 ready
    for tid in doc["summary"]["ready"]:
        assert t[tid]["阻塞类型"] == "无" and t[tid]["状态"] == "待开"


# ── ③ 合并不是覆盖 ──


def test_merge_keeps_ledger_status_and_records_conflict(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    path = repo / "docs/roadmap/任务台账.yaml"
    doc = load(repo)
    for t in doc["tasks"]:
        if t["id"] == "m2-resume-parse-and-rank/2.1":
            t["状态"] = "在跑"
            t["泳道"] = "0917ZZ"
        if t["id"] == "m2-resume-parse-and-rank/1.7":
            t["状态"] = "完成"  # 台账说已签认，真身 checkbox 还没勾
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    run(repo)
    t = by_id(load(repo))
    assert t["m2-resume-parse-and-rank/2.1"]["状态"] == "在跑"
    assert t["m2-resume-parse-and-rank/2.1"]["泳道"] == "0917ZZ"
    assert t["m2-resume-parse-and-rank/2.1"]["conflicts"] == {"状态": {"台账": "在跑", "真身": "待开"}}
    assert t["m2-resume-parse-and-rank/1.7"]["状态"] == "完成"
    assert t["m2-resume-parse-and-rank/1.7"]["conflicts"]["状态"]["真身"] == "阻塞"
    assert sorted(load(repo)["summary"]["conflicts"]) == ["m2-resume-parse-and-rank/1.7", "m2-resume-parse-and-rank/2.1"]
    # 显式 truth 才用真身覆盖
    run(repo, "--resolve-conflicts", "truth")
    t = by_id(load(repo))
    assert t["m2-resume-parse-and-rank/2.1"]["状态"] == "待开"
    assert "conflicts" not in t["m2-resume-parse-and-rank/2.1"]


def test_segment_status_from_ledger_wins_silently_because_truth_cannot_tell(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    path = repo / "docs/roadmap/任务台账.yaml"
    doc = load(repo)
    for t in doc["tasks"]:
        if t["id"] == "plan:2026-09-17-m2-unit0-model-comparison/seg1":
            t["状态"] = "完成"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert t["plan:2026-09-17-m2-unit0-model-comparison/seg1"]["状态"] == "完成"
    assert "conflicts" not in t["plan:2026-09-17-m2-unit0-model-comparison/seg1"]
    # seg1 完成 ⇒ seg2 变 ready
    assert "plan:2026-09-17-m2-unit0-model-comparison/seg2" in doc["summary"]["ready"]


def test_entries_missing_from_truth_are_kept_with_conflict(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    path = repo / "docs/roadmap/任务台账.yaml"
    doc = load(repo)
    doc["tasks"].append({"id": "manual:x", "场景": "M2", "阶段": "build", "标题": "手工加的", "依赖": [], "触碰区": [],
                         "状态": "待开", "阻塞类型": "无", "产出判据": "x", "来源": "手工"})
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    run(repo)
    t = by_id(load(repo))
    assert t["manual:x"]["状态"] == "待开"
    assert t["manual:x"]["conflicts"]["状态"]["真身"] == "来源已消失或已勾选"


# ── ④ 阶段：plan 有无决定调度单元 ──


def test_unit_with_plan_yields_segments_and_completed_plan_task(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert t["m2-resume-parse-and-rank/U0/plan"]["状态"] == "完成"
    seg1 = t["plan:2026-09-17-m2-unit0-model-comparison/seg1"]
    seg2 = t["plan:2026-09-17-m2-unit0-model-comparison/seg2"]
    assert seg1["标题"].endswith("Task 1–2") and seg2["依赖"] == [seg1["id"]]
    assert seg1["触碰区"] == ["scripts/compare_models_m2.py", "tests/test_compare_models_m2.py"]
    assert seg1["id"] in doc["summary"]["ready"] and seg2["id"] not in doc["summary"]["ready"]
    # 条目级依赖拆段 ⇒ 不与拆段同时 ready
    assert "m2-resume-parse-and-rank/1.2" not in doc["summary"]["ready"]
    # 单元是聚合条目：依赖 plan、拆段与未勾条目，永不 ready
    u0 = t["m2-resume-parse-and-rank/U0"]
    assert "m2-resume-parse-and-rank/1.7" in u0["依赖"] and u0["id"] not in doc["summary"]["ready"]


def test_unit_without_plan_makes_spec_to_plan_ready_after_previous_unit(tmp_path):
    repo = make_repo(tmp_path, with_plan=False)
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert t["m2-resume-parse-and-rank/U0/plan"]["阶段"] == "plan"
    assert t["m2-resume-parse-and-rank/U0/plan"]["状态"] == "待开"
    assert "m2-resume-parse-and-rank/U0/plan" in doc["summary"]["ready"]
    # U1 的 plan 依赖 U0 完成 ⇒ 不 ready
    assert t["m2-resume-parse-and-rank/U1/plan"]["依赖"] == ["m2-resume-parse-and-rank/U0"]
    assert "m2-resume-parse-and-rank/U1/plan" not in doc["summary"]["ready"]
    assert doc["summary"]["未归属plans"] == []


def test_gate_items_block_downstream_units_and_explicit_prereqs(tmp_path):
    repo = make_repo(tmp_path)
    t = by_id((run(repo), load(repo))[1])
    assert "m2-resume-parse-and-rank/0.2" in t["m2-resume-parse-and-rank/U1"]["依赖"]
    assert {"m2-resume-parse-and-rank/0.2", "m2-resume-parse-and-rank/2.1"} <= set(t["m2-resume-parse-and-rank/2.3"]["依赖"])
    assert t["m2-resume-parse-and-rank/0.2"]["阶段"] == "gate"
    assert "m2-resume-parse-and-rank/2.9" not in t  # 墓碑不入台账
    assert t["change:m2-resume-parse-and-rank"]["阶段"] == "archive"


def test_roadmap_scenes_gaps_relay_and_ledger_rows(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert t["scene:M3"]["状态"] == "待开" and t["scene:M3"]["依赖"] == ["scene:M2"]
    assert t["scene:M2"]["状态"] == "在跑"
    assert "scene:M3" not in doc["summary"]["ready"]
    assert t["gap:G1"]["状态"] == "在跑" and t["gap:G1"]["触碰区"] == ["tools/liaison/bridge.py"]
    assert t["gap:G4"]["状态"] == "阻塞" and t["gap:G4"]["阻塞类型"] == "决策"
    assert t["gap:G7"]["状态"] == "完成"
    assert t["relay:R-6"]["状态"] == "阻塞" and t["relay:R-6"]["阻塞类型"] == "外部"
    assert "relay:R-1" not in t
    tag = [x for x in t if x.startswith("relay:tag#")]
    assert len(tag) == 1 and t[tag[0]]["阻塞类型"] == "决策"
    # 号池：✅ 的号不入；待 X 收敛 ⇒ 依赖 X（X 已 ✅ 则不挂依赖）；无标记历史行不入
    assert "opener:[Mac]0917AJ" not in t and "opener:[Mac]0916B" not in t
    assert t["opener:[Mac]0917AK"]["状态"] == "待开" and t["opener:[Mac]0917AK"]["依赖"] == []
    assert t["opener:[Mac]0917AL"]["依赖"] == ["opener:[Mac]0917AZ"]
    assert "opener:[Mac]0917AZ" in doc["summary"]["未知依赖"]
    assert "opener:[Mac]0917AL" not in doc["summary"]["ready"]


def test_every_entry_has_required_fields_and_valid_enums(tmp_path):
    repo = make_repo(tmp_path)
    run(repo)
    doc = load(repo)
    required = {"id", "场景", "阶段", "标题", "依赖", "触碰区", "状态", "阻塞类型", "产出判据", "来源"}
    for t in doc["tasks"]:
        assert required <= set(t), t["id"]
        assert t["状态"] in ("待开", "在跑", "完成", "阻塞"), t["id"]
        assert t["阻塞类型"] in ("决策", "外部", "无"), t["id"]
        assert (t["状态"] == "阻塞") == (t["阻塞类型"] != "无"), t["id"]


# ── ⑤ 真实仓库：定夺队列引用的任务 id 必须在台账里 ──

REPO = Path(__file__).resolve().parent.parent
ID_RE = __import__("re").compile(r"`([^`\s]+)`")


def test_decision_queue_task_ids_exist_in_real_ledger():
    ledger = REPO / "docs/roadmap/任务台账.yaml"
    queue = REPO / "docs/roadmap/定夺队列.md"
    if not ledger.exists() or not queue.exists():
        pytest.skip("首版台账／定夺队列尚未生成")
    ids = {t["id"] for t in yaml.safe_load(ledger.read_text(encoding="utf-8"))["tasks"]}
    missing = []
    for line in queue.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| Q-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # 「一、待答」10 列时任务 id 在第 8 列；「二、远期」7 列时在第 7 列
        col = cells[7] if len(cells) >= 10 else cells[-1]
        for ref in ID_RE.findall(col):
            if ref not in ids:
                missing.append((cells[0], ref))
    assert missing == [], f"定夺队列引用了台账里不存在的任务 id：{missing}"


def test_rules_file_exists_and_names_the_four_ready_criteria():
    rules = REPO / ".claude/skills/task-dispatcher/rules.md"
    text = rules.read_text(encoding="utf-8")
    for key in ("依赖全完成", "非阻塞", "触碰区不与在跑泳道重叠", "不属不可代项", "max-parallel 3", "M0 > M1 > M2 > M3", "新场景发现"):
        assert key in text, key
