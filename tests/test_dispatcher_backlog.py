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


G2_RELEASED = (
    "# 定夺队列\n\n## 一、待答\n\n| 编号 | 场景 | 阻塞类型 | 问题 | 选项与代价 | 推荐 | 来源 | 阻塞的任务 id | 状态 | 答复 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
    "| Q-01 | M2 | 决策（G2 闸门） | 【G2 design／spec 定稿】`m2-resume-parse-and-rank`：x ｜ 产出 `openspec/changes/m2-resume-parse-and-rank/design.md` | (a) | 无默认 | s | `m2-resume-parse-and-rank/U0/plan` | 已答 | 定 |\n"
)


def test_unit_without_plan_makes_spec_to_plan_ready_after_previous_unit(tmp_path):
    repo = make_repo(tmp_path, with_plan=False)
    # 0917AO 在环闸门：G2 未放行 ⇒ spec-to-plan 条目 阻塞／决策 带 闸门 字段，不 ready
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert t["m2-resume-parse-and-rank/U0/plan"]["状态"] == "阻塞"
    assert t["m2-resume-parse-and-rank/U0/plan"]["阻塞类型"] == "决策"
    assert t["m2-resume-parse-and-rank/U0/plan"]["闸门"] == "G2 m2-resume-parse-and-rank"
    assert t["m2-resume-parse-and-rank/U0/plan"]["产出判据"].startswith("【G2 闸门·缺行】")
    assert "m2-resume-parse-and-rank/U0/plan" not in doc["summary"]["ready"]
    assert any(s.startswith("m2-resume-parse-and-rank/U0/plan ← G2") for s in doc["summary"]["闸门待放行"])
    # 台账被手改成 待开 也放不过（放行只认定夺队列）
    (repo / "docs/roadmap/任务台账.yaml").write_text(
        (repo / "docs/roadmap/任务台账.yaml").read_text(encoding="utf-8").replace("状态: 阻塞\n  阻塞类型: 决策\n  产出判据: 【G2", "状态: 待开\n  阻塞类型: 无\n  产出判据: 【G2"),
        encoding="utf-8",
    )
    shown = run(repo, "--show", "ready").stdout
    assert "m2-resume-parse-and-rank/U0/plan" not in shown
    # G2 放行 ⇒ 待开、ready；台账原先的 阻塞 不记 conflicts（闸门轴以队列为准）
    (repo / "docs/roadmap/定夺队列.md").write_text(G2_RELEASED, encoding="utf-8")
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert t["m2-resume-parse-and-rank/U0/plan"]["阶段"] == "plan"
    assert t["m2-resume-parse-and-rank/U0/plan"]["状态"] == "待开"
    assert "conflicts" not in t["m2-resume-parse-and-rank/U0/plan"]
    assert "m2-resume-parse-and-rank/U0/plan" in doc["summary"]["ready"]
    assert doc["summary"]["闸门待放行"] == []
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


# ── ⑤ 「人事部可见」排序键（0917BA，路线图第七节）──

RULES_VISIBLE = """# rules

## 3. 能开尽开的上限与排序

```visible-rules
path: app/web/
id: ^m1-[^/]+/9\\.\\d+$
id: ^m2-resume-parse-and-rank/(3|9)\\.\\d+$
```
"""

TASKS_VISIBLE = """**进度：0/8**

## 1. U0 模型对比定型

- [x] 1.1 冒烟安装

## 2. U1 数据模型

- [x] 2.1 `app/storage/db.py` 新增 `candidate` 表

## 3. U2 上传与解析管线

- [ ] 3.1 上传接口 `POST /resumes/upload`

## 9. U2.5 可见薄片（插在 U2 之后、U3 之前）

- [ ] 9.1 上传入口页
- [ ] 9.2 校对页 evidence 高亮

## 4. U3 硬门槛引擎

- [ ] 4.1 `app/agents/hard_requirement_screening.py` 纯函数

## 5. U4 召回精排

- [ ] 5.1 embedding 适配器
"""

TASKS_M1_VISIBLE = """**进度：0/2**

## 8. 部署

- [x] 8.1 已发版

## 9. 验收与交付

- [ ] 9.1 **画像质量验收**：10 个历史岗位重跑
"""

PLAN_U2 = """# U2 上传与解析 实现计划

## 文件结构

- `app/parsing/upload.py`
- `tests/test_upload.py`

### Task 1: 上传接口
### Task 2: 解析
"""

PLAN_U3 = """# U3 硬门槛 实现计划

## 文件结构

- `app/agents/hard_requirement_screening.py`

### Task 1: screen
"""

G2_RELEASED_ALL = G2_RELEASED.replace("`m2-resume-parse-and-rank/U0/plan`", "`m2-resume-parse-and-rank/U2/plan`")


def make_visible_repo(tmp: Path) -> Path:
    repo = make_repo(tmp, with_plan=False)
    (repo / ".claude/skills/task-dispatcher").mkdir(parents=True)
    (repo / ".claude/skills/task-dispatcher/rules.md").write_text(RULES_VISIBLE, encoding="utf-8")
    (repo / "openspec/changes/m2-resume-parse-and-rank/tasks.md").write_text(TASKS_VISIBLE, encoding="utf-8")
    (repo / "openspec/changes/m1-job-profile-intake").mkdir(parents=True)
    (repo / "openspec/changes/m1-job-profile-intake/tasks.md").write_text(TASKS_M1_VISIBLE, encoding="utf-8")
    (repo / "docs/roadmap/定夺队列.md").write_text(
        G2_RELEASED_ALL + "| Q-02 | M1 | 决策（G2 闸门） | 【G2 design／spec 定稿】`m1-job-profile-intake`：x ｜ 产出 `openspec/changes/m1-job-profile-intake/design.md` | (a) | 无默认 | s | `m1-job-profile-intake/U9/plan` | 已答 | 定 |\n",
        encoding="utf-8",
    )
    return repo


def test_every_entry_carries_visible_flag_from_rules_paths_ids_and_unit_propagation(tmp_path):
    repo = make_visible_repo(tmp_path)
    (repo / "docs/superpowers/plans/2026-09-18-m2-unit2-upload.md").write_text(PLAN_U2, encoding="utf-8")
    (repo / "docs/superpowers/plans/2026-09-18-m2-unit3-screening.md").write_text(PLAN_U3, encoding="utf-8")
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert all(isinstance(x.get("可见"), bool) for x in doc["tasks"]), "每条都要有布尔 可见 字段"
    # id 规则：M2 U2（3.x）与 U2.5（9.x）条目、M1 9.x 验收
    assert t["m2-resume-parse-and-rank/3.1"]["可见"] is True
    assert t["m2-resume-parse-and-rank/9.1"]["可见"] is True
    assert t["m1-job-profile-intake/9.1"]["可见"] is True
    # U2.5 是独立单元，不与 U2 撞 id；同章传播到 plan／单元级
    assert t["m2-resume-parse-and-rank/U2.5/plan"]["可见"] is True
    assert t["m2-resume-parse-and-rank/U2.5"]["可见"] is True
    assert t["m2-resume-parse-and-rank/U2/plan"]["可见"] is True
    assert t["m1-job-profile-intake/U9/plan"]["可见"] is True
    # 拆段随所属单元：U2 的 plan 段可见，U3 的不可见；U3 plan 只匹配 unit3，⛔ 不吃 unit2.5
    assert t["plan:2026-09-18-m2-unit2-upload/seg1"]["可见"] is True
    assert t["plan:2026-09-18-m2-unit3-screening/seg1"]["可见"] is False
    assert t["m2-resume-parse-and-rank/U3"]["依赖"].count("plan:2026-09-18-m2-unit2-upload/seg1") == 0
    assert t["m2-resume-parse-and-rank/4.1"]["可见"] is False
    assert t["m2-resume-parse-and-rank/U4/plan"]["可见"] is False
    # 顺序链按文件物理顺序：U3 排在 U2.5 之后
    assert t["m2-resume-parse-and-rank/U3/plan"]["依赖"] == ["m2-resume-parse-and-rank/U2.5"]
    assert t["m2-resume-parse-and-rank/U2.5/plan"]["依赖"] == ["m2-resume-parse-and-rank/U2"]


def test_visible_path_rule_marks_web_touch_area(tmp_path):
    repo = make_visible_repo(tmp_path)
    tasks = repo / "openspec/changes/m2-resume-parse-and-rank/tasks.md"
    tasks.write_text(tasks.read_text(encoding="utf-8") + "- [ ] 5.2 列表页改 `app/web/server.py`\n", encoding="utf-8")
    run(repo)
    t = by_id(load(repo))
    assert t["m2-resume-parse-and-rank/5.2"]["可见"] is True
    assert t["m2-resume-parse-and-rank/U4/plan"]["可见"] is True  # 同章传播


def test_ready_orders_visible_first_but_never_crosses_gate_or_dependency(tmp_path):
    repo = make_visible_repo(tmp_path)
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    ready = doc["summary"]["ready"]
    # 就绪集里可见的排最前（U2/plan 可见 > U0 已完成不在集内；M1 9 章可见且场景更高 ⇒ 第一）
    assert ready[:2] == ["m1-job-profile-intake/U9/plan", "m2-resume-parse-and-rank/U2/plan"]
    # 不越依赖：U2.5/plan 可见但 U2 未完成 ⇒ 不 ready
    assert "m2-resume-parse-and-rank/U2.5/plan" not in ready
    # 不越闸门：把 M1 的 G2 放行行删掉 ⇒ M1 9 章即便可见也被闸住
    q = repo / "docs/roadmap/定夺队列.md"
    q.write_text("\n".join(l for l in q.read_text(encoding="utf-8").splitlines() if not l.startswith("| Q-02")) + "\n", encoding="utf-8")
    run(repo)
    doc = load(repo)
    assert doc["summary"]["ready"][0] == "m2-resume-parse-and-rank/U2/plan"
    assert "m1-job-profile-intake/U9/plan" not in doc["summary"]["ready"]
    shown = run(repo, "--show", "ready").stdout
    assert shown.splitlines()[0].startswith("m2-resume-parse-and-rank/U2/plan") and "可见=✓" in shown.splitlines()[0]


def test_compute_ready_visible_key_sits_above_scene_priority_only_inside_ready_set():
    sys.path.insert(0, str(SCRIPT.parent))
    import dispatcher_backlog as db  # noqa: E402

    base = {"状态": "待开", "阻塞类型": "无", "依赖": [], "阶段": "build"}
    tasks = [
        dict(base, id="b-m2-visible", 场景="M2", 可见=True),
        dict(base, id="a-m0-hidden", 场景="M0", 可见=False),
        dict(base, id="c-m1-visible", 场景="M1", 可见=True),
        dict(base, id="d-m0-visible-blocked", 场景="M0", 可见=True, 依赖=["nope"]),
    ]
    db.set_queue_repo(Path("/nonexistent"))
    ready, unknown = db.compute_ready(tasks)
    assert ready == ["c-m1-visible", "b-m2-visible", "a-m0-hidden"]
    assert unknown == ["nope"]


def test_visible_rules_fallback_to_builtin_when_rules_file_absent(tmp_path):
    repo = make_visible_repo(tmp_path)
    (repo / ".claude/skills/task-dispatcher/rules.md").unlink()
    run(repo)
    t = by_id(load(repo))
    assert t["m2-resume-parse-and-rank/3.1"]["可见"] is True  # 内置默认清单与 rules.md 一致
    assert t["m2-resume-parse-and-rank/4.1"]["可见"] is False


def test_real_rules_file_names_visible_key_and_carries_the_block():
    text = (REPO / ".claude/skills/task-dispatcher/rules.md").read_text(encoding="utf-8")
    assert "人事部可见" in text and "```visible-rules" in text
    sys.path.insert(0, str(SCRIPT.parent))
    import dispatcher_backlog as db  # noqa: E402

    rules = db.load_visible_rules(REPO)
    assert rules == db.load_visible_rules(REPO / "nonexistent-dir"), "rules.md 清单与内置默认必须一致（改一处要同步另一处）"


# ── ⑤ 拆段识别（0918C）：「## 建议拆段点」按段生成 seg1..segN，段间串行、段内条目只依赖本段 ──

TASKS_U1_SEGMENTED = """**进度：3/8**

## 2. U1 数据模型

- [x] 2.1 `app/storage/db.py` 新增 `candidate` 表
- [x] 2.2 新增 `application`
- [x] 2.3 新增 `rejection_record`
- [ ] 2.4 新增 `resume_access_log`
- [ ] 2.5 新增 `resume_embedding`
- [ ] 2.6 `analysis_run` 增加 `run_type` 语义约定
- [ ] 2.7 `tests/test_db_m2_schema.py` 老库升级回归
- [ ] 2.8 `scripts/create_hr_account.py`
"""

# 措辞刻意用「Segment A：」而不是「第 1 条：」——真实计划 `2026-09-17-m2-resume-parse-and-rank-unit1-data-model.md`
# 就是这种写法，旧正则 `第\\s*(\\d+)\\s*条` 不命中，8 个 Task 被压成单条 seg1（主航道停摆根因）。
PLAN_U1_SEGMENTED = """# U1 数据模型 实现计划

## 文件结构

- `app/storage/db.py`
- `tests/test_db_m2_schema.py`

## 建议拆段点（`lane-dispatch`「长 run-build 拆段」，本计划 8 个 Task）

- **Segment A：Task 1–3**（候选人/投递域）
- **Segment B：Task 4–6**（留痕/校对/标记域）
- **Segment C：Task 7–8**（跨库回归测试 + 建账号脚本）

拆段交接只靠分支与 commit hash。

## 前置

### Task 1: `candidate`（tasks 2.1）
### Task 2: `application`（tasks 2.2）
### Task 3: `rejection_record` 建表（tasks 2.3 ＋ U7 任务 8.1，同一交付单元合并）
### Task 4: `resume_access_log`（tasks 2.4）
### Task 5: `resume_embedding`（tasks 2.5）
### Task 6: `run_type` 约定（tasks 2.6）
### Task 7: 老库升级回归（tasks 2.7 剩余部分）
### Task 8: `scripts/create_hr_account.py`（tasks 2.8）
"""

U1_STEM = "2026-09-17-m2-resume-parse-and-rank-unit1-data-model"


def make_segmented_repo(tmp: Path, tasks: str = TASKS_U1_SEGMENTED) -> Path:
    repo = make_repo(tmp, with_plan=False)
    (repo / "openspec/changes/m2-resume-parse-and-rank/tasks.md").write_text(tasks, encoding="utf-8")
    (repo / f"docs/superpowers/plans/{U1_STEM}.md").write_text(PLAN_U1_SEGMENTED, encoding="utf-8")
    return repo


def test_segment_points_in_any_wording_yield_one_entry_per_segment_chained(tmp_path):
    repo = make_segmented_repo(tmp_path)
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    seg = [f"plan:{U1_STEM}/seg{k}" for k in (1, 2, 3)]
    assert all(s in t for s in seg) and f"plan:{U1_STEM}/seg4" not in t
    assert t[seg[0]]["标题"].endswith("Task 1–3") and t[seg[1]]["标题"].endswith("Task 4–6") and t[seg[2]]["标题"].endswith("Task 7–8")
    # 段间依赖：segN 依赖 segN-1
    assert t[seg[0]]["依赖"] == [] and t[seg[1]]["依赖"] == [seg[0]] and t[seg[2]]["依赖"] == [seg[1]]
    # 段内条目只依赖本段（按 `### Task N: …（tasks 2.x）` 的映射），⛔ 不再依赖整份 plan 的全部拆段
    for no, s in (("2.4", seg[1]), ("2.5", seg[1]), ("2.6", seg[1]), ("2.7", seg[2]), ("2.8", seg[2])):
        deps = t[f"m2-resume-parse-and-rank/{no}"]["依赖"]
        assert s in deps and not any(x in deps for x in seg if x != s), (no, deps)
    # 段内条目全勾 ⇒ 该段真身「完成」；下一段进 ready、再下一段不进
    assert t[seg[0]]["状态"] == "完成" and t[seg[1]]["状态"] == "待开" and t[seg[2]]["状态"] == "待开"
    assert seg[1] in doc["summary"]["ready"] and seg[2] not in doc["summary"]["ready"]
    assert seg[0] not in doc["summary"]["ready"]
    # 单元级聚合条目依赖三段
    assert set(seg) <= set(t["m2-resume-parse-and-rank/U1"]["依赖"])


def test_segment_is_ready_while_its_items_are_unchecked_and_next_segment_waits(tmp_path):
    repo = make_segmented_repo(tmp_path, TASKS_U1_SEGMENTED.replace("- [x]", "- [ ]").replace("进度：3/8", "进度：0/8"))
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    seg1, seg2 = f"plan:{U1_STEM}/seg1", f"plan:{U1_STEM}/seg2"
    assert t[seg1]["状态"] == "待开" and seg1 in doc["summary"]["ready"] and seg2 not in doc["summary"]["ready"]


def test_segment_truth_done_by_checkboxes_records_conflict_against_ledger_running(tmp_path):
    """段内条目全勾 ⇒ 真身「完成」可判；台账仍写「在跑」⇒ 记 conflicts、台账优先（合并不是覆盖）。
    而映射不到条目的段（真身判不了）仍按 truth_known=False 静默以台账为准。"""
    repo = make_segmented_repo(tmp_path)
    run(repo)
    path = repo / "docs/roadmap/任务台账.yaml"
    doc = load(repo)
    seg1 = f"plan:{U1_STEM}/seg1"
    for x in doc["tasks"]:
        if x["id"] == seg1:
            x["状态"] = "在跑"
            x["备注"] = "人工核实备注不得丢"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    run(repo)
    t = by_id(load(repo))
    assert t[seg1]["状态"] == "在跑" and t[seg1]["conflicts"]["状态"] == {"台账": "在跑", "真身": "完成"}
    assert t[seg1]["备注"] == "人工核实备注不得丢"
    assert t[seg1]["标题"].endswith("Task 1–3"), "已存在的 seg1 按新规则重算标题／依赖"


def test_segmented_generation_is_idempotent(tmp_path):
    repo = make_segmented_repo(tmp_path)
    run(repo)
    first = (repo / "docs/roadmap/任务台账.yaml").read_bytes()
    run(repo)
    assert first == (repo / "docs/roadmap/任务台账.yaml").read_bytes()


# ── ⑥ 场景名（0918C，Q-37）：intent 的场景取 frontmatter `场景` 原文，⛔ 不用 `\\bM\\d′?` 正则猜 ──

INTENT_S_M3 = "---\nstatus: 已确认（G1 Q-32）\n场景: S-M3\n---\n# S-M3 · intent\n\n## 目标\nx\n\n## 待答题\n"


def test_intent_scene_is_frontmatter_verbatim_not_regex_guess(tmp_path):
    repo = make_repo(tmp_path, with_plan=False)
    (repo / "docs/roadmap/intents").mkdir(parents=True)
    (repo / "docs/roadmap/intents/S-M3-intent.md").write_text(INTENT_S_M3, encoding="utf-8")
    # 无 frontmatter 场景字段 ⇒ 退到文件名 stem（去 -intent），同样不猜
    (repo / "docs/roadmap/intents/S-排期-intent.md").write_text("# 排期\n\n## 目标\nx\n", encoding="utf-8")
    run(repo)
    doc = load(repo)
    t = by_id(doc)
    assert "propose:S-M3" in t and "propose:M3" not in t
    assert t["propose:S-M3"]["场景"] == "S-M3" and t["propose:S-M3"]["闸门"] == "G1 S-M3"
    assert "propose:S-排期" in t and t["propose:S-排期"]["场景"] == "S-排期"
    first = (repo / "docs/roadmap/任务台账.yaml").read_bytes()
    run(repo)
    assert first == (repo / "docs/roadmap/任务台账.yaml").read_bytes()
