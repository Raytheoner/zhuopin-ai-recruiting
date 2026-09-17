"""在环闸门 G1–G5（`scripts/gates.py`，[Mac]0917AO）。

钉四件事：① `gate_open` 各闸真值表——缺行／待答／已答非放行字样／已答放行／作废；② 否定形态（不发、暂不定）不算放行；
③ `gate_request` 追加去重、编号递增、落在「一、待答」表末；④ 台账接线：propose／plan／release 条目到闸未放行 ⇒
不进 ready（台账状态被手改成待开也放不过），放行后进 ready。全部在临时目录里跑，⛔ 不碰真实 docs。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import gates

ROOT = Path(__file__).resolve().parent.parent
BACKLOG = ROOT / "scripts" / "dispatcher_backlog.py"

HEADER = (
    "# 定夺队列（R5）\n\n## 一、待答\n\n"
    "| 编号 | 场景 | 阻塞类型 | 问题 | 选项与代价 | 推荐 | 来源 | 阻塞的任务 id | 状态 | 答复 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
)
TAIL = "\n## 二、远期定夺（前置未齐）\n\n| 编号 | 场景 | 阻塞类型 | 问题 | 前置 | 来源 | 阻塞的任务 id |\n|---|---|---|---|---|---|---|\n| Q-F1 | M2 | 决策 | 远期 | x | y | `z` |\n"


def row(gate: str, subject: str, status: str, reply: str, number: str = "Q-07") -> str:
    return f"| {number} | M3 | 决策 | {gates.gate_marker(gate, subject)}：到闸 ｜ 产出 `p` | (a)/(b) | 无默认 | src | `t` | {status} | {reply} |\n"


@pytest.fixture
def queue(tmp_path: Path) -> Path:
    q = tmp_path / "repo" / "docs" / "roadmap" / "定夺队列.md"
    q.parent.mkdir(parents=True)
    gates.configure(tmp_path / "repo")
    yield q
    gates.configure(gates.ROOT)


# ── ① 真值表 ──


@pytest.mark.parametrize("gate", list(gates.GATES))
def test_missing_file_or_row_is_closed(gate: str, queue: Path) -> None:
    assert gates.gate_state(gate, "X") == "缺行"
    assert gates.gate_open(gate, "X") is False
    queue.write_text(HEADER + row("G1", "OTHER", "已答", "定") + TAIL, encoding="utf-8")
    assert gates.gate_state(gate, "X") == "缺行"


@pytest.mark.parametrize(
    "gate,status,reply,expected",
    [
        ("G1", "待答", "", "待答"),
        ("G1", "待答", "定", "待答"),  # 答复列有字但状态没改「已答」——不算，放行只认状态＝已答
        ("G1", "已答", "1a", "已答·未放行"),
        ("G1", "已答", "定", "已放行"),
        ("G1", "已答", "逐条改后「定」", "已放行"),
        ("G1", "已答", "暂不定，先补 D3", "已答·未放行"),
        ("G1", "作废", "", "作废"),
        ("G1", "已答", "作废", "作废"),
        ("G2", "已答", "定", "已放行"),
        ("G2", "已答", "发", "已答·未放行"),  # 字样要对闸：G2 认「定」不认「发」
        ("G3", "已答", "发", "已放行"),
        ("G3", "已答", "不发，等窗口", "已答·未放行"),
        ("G4", "已答", "审核通过·发", "已放行"),
        ("G4", "已答", "审核通过，发。", "已放行"),
        ("G4", "已答", "审核通过「发」", "已放行"),
        ("G4", "已答", "审核通过", "已答·未放行"),
        ("G4", "已答", "发", "已答·未放行"),  # G4 须先「审核通过」再「发」
        ("G4", "已答", "改：第二段删掉再发", "已答·未放行"),  # (b) 改稿答复，final review 🔴 #1
        ("G4", "已答", "审核通过，改：第二段删掉再发", "已答·未放行"),
        ("G4", "已答", "发现日期错了，改", "已答·未放行"),
        ("G4", "已答", "不发", "已答·未放行"),
        ("G4", "已答", "审核通过，不「发」", "已答·未放行"),
        ("G4", "已答", "驳回，改后再发", "已答·未放行"),
        ("G1", "已答", "待定", "已答·未放行"),
        ("G1", "已答", "改：D3 的定义有误", "已答·未放行"),
        ("G1", "已答", "改后再「定」", "已答·未放行"),
        ("G1", "已答", "D3 改成 offset 区间，定。", "已放行"),
        ("G3", "已答", "等窗口定了再发", "已答·未放行"),
        ("G3", "已答", "已发", "已放行"),
        ("G5", "已答", "签", "已放行"),
        ("G5", "已答", "签名栏漏了", "已答·未放行"),
        ("G5", "已答", "别签，口径有误", "已答·未放行"),
        ("G5", "已答", "定", "已答·未放行"),
    ],
)
def test_truth_table(gate: str, status: str, reply: str, expected: str, queue: Path) -> None:
    queue.write_text(HEADER + row(gate, "subj", status, reply) + TAIL, encoding="utf-8")
    assert gates.gate_state(gate, "subj") == expected
    assert gates.gate_open(gate, "subj") is (expected == "已放行")


def test_subject_match_is_exact_backticked(queue: Path) -> None:
    """`人事部#2` 放行不能带开 `人事部#21`。"""
    queue.write_text(HEADER + row("G4", "人事部#2", "已答", "审核通过·发") + TAIL, encoding="utf-8")
    assert gates.gate_open("G4", "人事部#2")
    assert not gates.gate_open("G4", "人事部#21")
    assert not gates.gate_open("G4", "人事部")


def test_row_in_answered_section_without_status_column_counts_as_answered(queue: Path) -> None:
    text = HEADER + TAIL + "\n## 三、已答 / 作废\n\n| 编号 | 问题 | 答复 | 落档 |\n|---|---|---|---|\n" + f"| Q-09 | {gates.gate_marker('G2', 'pkg')}：x | 定 | tasks.md |\n"
    queue.write_text(text, encoding="utf-8")
    assert gates.gate_open("G2", "pkg")


def test_unknown_gate_raises(queue: Path) -> None:
    with pytest.raises(ValueError):
        gates.gate_open("G9", "x")


# ── ③ 追加与去重 ──


def test_request_appends_once_with_next_number_inside_pending_table(queue: Path) -> None:
    queue.write_text(HEADER + "| Q-03 | M2 | 决策 | 别的 | (a) | a | s | `t` | 待答 | |\n" + TAIL, encoding="utf-8")
    assert gates.gate_request("G1", "M3", scene="M3", artifact="docs/roadmap/intents/M3-intent.md", task_ids=["propose:M3"]) is True
    assert gates.gate_request("G1", "M3", scene="M3", artifact="docs/roadmap/intents/M3-intent.md", task_ids=["propose:M3"]) is False
    text = queue.read_text(encoding="utf-8")
    assert text.count("【G1 intent 定稿】`M3`") == 1
    lines = text.splitlines()
    i = next(k for k, l in enumerate(lines) if "【G1 intent 定稿】`M3`" in l)
    assert lines[i].startswith("| Q-04 |")
    assert lines[i - 1].startswith("| Q-03 |"), "落在「一、待答」表末"
    assert lines[i + 1] == "", "不能串进「二、远期」表"
    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
    assert len(cells) == 10
    assert cells[2].startswith("决策") and "G1" in cells[2]
    assert "docs/roadmap/intents/M3-intent.md" in cells[3], "字段含产出路径"
    assert cells[7] == "`propose:M3`" and cells[8] == "待答" and cells[9] == ""
    assert gates.gate_state("G1", "M3") == "待答"
    # 远期表原样
    assert "| Q-F1 | M2 |" in text


def test_request_reopens_after_a_revise_reply_but_not_after_pending_or_release(queue: Path) -> None:
    """他答「改：…」（已答·未放行）⇒ 改稿后允许再追加一行回闸，新行成判据；待答／已放行／作废 ⇒ 去重。"""
    queue.write_text(HEADER + row("G4", "HR#3", "已答", "改：第二段删掉再发", number="Q-05") + TAIL, encoding="utf-8")
    assert gates.gate_state("G4", "HR#3") == "已答·未放行"
    assert gates.gate_request("G4", "HR#3", scene="M2", artifact="docs/跟进信/HR-3.md") is True
    text = queue.read_text(encoding="utf-8")
    assert text.count("【G4 发信】`HR#3`") == 2 and "| Q-06 |" in text
    assert gates.gate_state("G4", "HR#3") == "待答", "新行成为判据"
    assert gates.gate_request("G4", "HR#3", scene="M2", artifact="docs/跟进信/HR-3.md") is False
    queue.write_text(text.replace("| Q-06 |", "| Q-06 |").replace("| 待答 | |", "| 已答 | 审核通过·发 |"), encoding="utf-8")
    assert gates.gate_state("G4", "HR#3") == "已放行"
    assert gates.gate_request("G4", "HR#3", scene="M2", artifact="docs/跟进信/HR-3.md") is False
    queue.write_text(HEADER + row("G4", "HR#3", "作废", "") + TAIL, encoding="utf-8")
    assert gates.gate_request("G4", "HR#3", scene="M2", artifact="docs/跟进信/HR-3.md") is False


def test_request_creates_file_and_section_when_missing(queue: Path) -> None:
    assert gates.gate_request("G4", "人事部#3", scene="M2", artifact="docs/跟进信/x.md") is True
    assert gates.gate_state("G4", "人事部#3") == "待答"
    assert "## 一、待答" in queue.read_text(encoding="utf-8")


def test_cli_state_open_request(queue: Path) -> None:
    repo = queue.parent.parent.parent
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts/gates.py"), "--repo", str(repo), *a], capture_output=True, text=True)
    assert run("state", "G1", "M3").stdout.strip() == "缺行"
    assert run("open", "G1", "M3").returncode == 1
    assert run("request", "G1", "M3", "--scene", "M3", "--artifact", "a.md", "--tasks", "propose:M3").stdout.strip() == "已追加"
    assert run("open", "G1", "M3").returncode == 1
    queue.write_text(queue.read_text(encoding="utf-8").replace("| 待答 | |", "| 已答 | 定 |"), encoding="utf-8")
    r = run("open", "G1", "M3")
    assert r.returncode == 0 and r.stdout.strip() == "已放行"


# ── ④ 台账接线 ──


def ledger(tasks: list[dict]) -> str:
    return yaml.safe_dump({"version": 1, "tasks": tasks}, allow_unicode=True, sort_keys=False)


def base_task(tid: str, stage: str, scene: str = "M3", **kw) -> dict:
    d = {"id": tid, "场景": scene, "阶段": stage, "标题": tid, "依赖": [], "触碰区": [], "状态": "待开", "阻塞类型": "无", "产出判据": "", "来源": f"docs/roadmap/intents/{scene}-intent.md:1"}
    d.update(kw)
    return d


def test_gate_for_task_mapping() -> None:
    assert gates.gate_for_task(base_task("propose:M3", "propose")) == ("G1", "M3")
    assert gates.gate_for_task(base_task("m2-resume-parse-and-rank/U1/plan", "plan", "M2")) == ("G2", "m2-resume-parse-and-rank")
    assert gates.gate_for_task(base_task("release:M2", "release", "M2")) == ("G3", "M2")
    assert gates.gate_for_task(base_task("plan:x/seg1", "build")) is None
    assert gates.gate_for_task(base_task("relay:x", "acceptance")) is None  # 起草允许闸前，发信由动作通道按 G4 拦


def test_filter_ready_holds_gated_tasks_even_if_ledger_says_ready(queue: Path) -> None:
    tasks = [base_task("propose:M3", "propose"), base_task("relay:H-4", "build")]
    ready, held = gates.filter_ready(tasks, ["propose:M3", "relay:H-4"], "")
    assert ready == ["relay:H-4"]
    assert held == [("propose:M3", "G1", "M3", "缺行")]
    queue.write_text(HEADER + row("G1", "M3", "已答", "定") + TAIL, encoding="utf-8")
    ready, held = gates.filter_ready(tasks, ["propose:M3", "relay:H-4"], queue.read_text(encoding="utf-8"))
    assert ready == ["propose:M3", "relay:H-4"] and held == []


def test_show_ready_excludes_propose_until_g1_released(queue: Path) -> None:
    """调度器规则：intent 草稿存在（propose 条目待开、依赖齐）但 G1 未放行 ⇒ `--show ready` 不含 propose。"""
    repo = queue.parent.parent.parent
    out = repo / "docs/roadmap/任务台账.yaml"
    out.write_text(ledger([base_task("propose:M3", "propose"), base_task("relay:H-4", "build", "构建自动化")]), encoding="utf-8")
    show = lambda: subprocess.run([sys.executable, str(BACKLOG), "--repo", str(repo), "--show", "ready"], capture_output=True, text=True, check=True).stdout
    assert "propose:M3" not in show() and "relay:H-4" in show()
    gates.gate_request("G1", "M3", scene="M3", artifact="docs/roadmap/intents/M3-intent.md", task_ids=["propose:M3"])
    assert "propose:M3" not in show()
    show_gated = lambda: subprocess.run([sys.executable, str(BACKLOG), "--repo", str(repo), "--show", "gated"], capture_output=True, text=True, check=True).stdout
    g = show_gated()
    assert "propose:M3" in g and "闸门=G1 `M3` 待答" in g and "# gated: 1" in g
    queue.write_text(queue.read_text(encoding="utf-8").replace("| 待答 | |", "| 已答 | a |"), encoding="utf-8")
    assert "propose:M3" not in show(), "答复列填字母不算放行"
    assert "已答·未放行" in show_gated()
    queue.write_text(queue.read_text(encoding="utf-8").replace("| 已答 | a |", "| 已答 | 定 |"), encoding="utf-8")
    assert "propose:M3" in show()
    assert "# gated: 0" in show_gated()


def test_sweep_reports_and_only_appends_with_apply(queue: Path) -> None:
    repo = queue.parent.parent.parent
    (repo / "docs/roadmap/任务台账.yaml").write_text(
        ledger([
            base_task("propose:M3", "propose"),
            base_task("m2-x/U1/plan", "plan", "M2", 依赖=["m2-x/U0"]),
            base_task("m2-x/U0", "merge", "M2", 状态="完成"),
            base_task("m2-y/U1/plan", "plan", "M2", 依赖=["m2-y/U0"]),  # 依赖未完成 ⇒ 未到闸，不入队
            base_task("m2-y/U0", "merge", "M2"),
        ]),
        encoding="utf-8",
    )
    rows = gates.sweep(repo)
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [("propose:M3", "G1", "M3", "缺行"), ("m2-x/U1/plan", "G2", "m2-x", "缺行")]
    assert not queue.exists(), "干跑不写文件"
    rows = gates.sweep(repo, apply=True)
    assert [r[4] for r in rows] == ["已追加", "已追加"]
    text = queue.read_text(encoding="utf-8")
    assert "【G1 intent 定稿】`M3`" in text and "【G2 design／spec 定稿】`m2-x`" in text and "`openspec/changes/m2-x/design.md`" in text
    assert "m2-y" not in text
    again = gates.sweep(repo, apply=True)
    assert [(r[3], r[4]) for r in again] == [("待答", "—"), ("待答", "—")], "已有行不重复追加"
    assert queue.read_text(encoding="utf-8") == text


# frontmatter `场景` ＝ 场景码（requirement-grill 约定，与文件名 stem 一致），生成器取原文作 id／闸门 subject，
# ⛔ 不再从描述里用正则猜（0918C：曾把 `S-M3` 猜成 `M3`，重复生成 G1 行）
INTENT = "---\nstatus: 草稿·待 G1\n场景: M3\n---\n# M3 实时语音面试 · intent\n\n## 目标\nx\n\n## 决策\n- D1\n\n## 待答题\n{pending}\n"


@pytest.mark.parametrize(
    "pending,status,block",
    [
        ("", "阻塞", "决策"),  # 无未答题 ⇒ 到闸；G1 缺行 ⇒ 阻塞／决策（闸门）
        ("- [ ] Q3 岗位范围？", "阻塞", "决策"),  # 有未答题 ⇒ 先清题，同样不 ready
    ],
)
def test_intent_draft_yields_propose_entry_held_by_g1(queue: Path, pending: str, status: str, block: str) -> None:
    repo = queue.parent.parent.parent
    (repo / "docs/roadmap/intents").mkdir(parents=True)
    (repo / "docs/roadmap/intents/M3-intent.md").write_text(INTENT.format(pending=pending), encoding="utf-8")
    subprocess.run([sys.executable, str(BACKLOG), "--repo", str(repo)], capture_output=True, text=True, check=True)
    doc = yaml.safe_load((repo / "docs/roadmap/任务台账.yaml").read_text(encoding="utf-8"))
    t = {x["id"]: x for x in doc["tasks"]}["propose:M3"]
    assert t["阶段"] == "propose" and t["场景"] == "M3" and t["状态"] == status and t["阻塞类型"] == block
    assert t["闸门"] == "G1 M3"
    assert "propose:M3" not in doc["summary"]["ready"]
    if not pending:
        assert t["产出判据"].startswith("【G1 闸门·缺行】")
        assert gates.sweep(repo, apply=True)[0][:4] == ("propose:M3", "G1", "M3", "待答")
        # 放行 ⇒ 待开、ready
        queue.write_text(queue.read_text(encoding="utf-8").replace("| 待答 | |", "| 已答 | 定 |"), encoding="utf-8")
        subprocess.run([sys.executable, str(BACKLOG), "--repo", str(repo)], capture_output=True, text=True, check=True)
        doc = yaml.safe_load((repo / "docs/roadmap/任务台账.yaml").read_text(encoding="utf-8"))
        assert "propose:M3" in doc["summary"]["ready"]
    else:
        assert "未答题" in t["产出判据"]
        assert gates.sweep(repo, apply=True) == [], "有未答题不到闸，不入队"
        assert not queue.exists()
