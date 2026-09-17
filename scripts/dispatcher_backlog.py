#!/usr/bin/env python3
"""任务台账生成器（任务驱动 workflow · R3，[Mac]0917AL）。

为什么：调度器（R2）要「读台账＋仓库真身，挑无前置阻碍的任务能开尽开」，前提是台账**机器可读**。
今天待办散在五处（路线图、各 openspec tasks.md、superpowers plans、session接力、号池台账），
每处格式不同，只能靠人读。本脚本把它们**合并**成一份 `docs/roadmap/任务台账.yaml`。

三条硬性质（都有测试钉住，见 tests/test_dispatcher_backlog.py）：
  1. **合并不是覆盖**：已有条目的「状态」以台账为准；真身推出的状态与台账不同时写 `conflicts:`，
     ⛔ 不静默改状态（台账状态是机器判据，写错方向会让该发生的事再也不发生——09-09 跟进信教训）。
     只有显式 `--resolve-conflicts truth` 才用真身覆盖。
  2. **幂等**：同一仓库连跑两次，字节相同（正文不含时间戳；顺序全部确定）。
  3. **不可代项 ⇒ 阻塞类型「决策」**：🔴／不可代／Shao Peishen／待裁决 等标记一律判「决策」，
     调度器永远不会把它排进泳道。
  4. **在环闸门（0917AO，`scripts/gates.py`）**：propose（G1）／plan（G2）／release（G3）条目在定夺队列
     对应闸门行未「已答＋放行字样」前一律 `阻塞／决策` 并带 `闸门:` 字段；`compute_ready` 再按定夺队列复核一遍，
     台账状态被手改成「待开」也放不过（口径：放行只认定夺队列，⛔ 不认台账状态自改）。
     intent 落档（`docs/roadmap/intents/<场景>-intent.md`）⇒ 生成 `propose:<场景>` 条目；有未答题 ⇒ 阻塞／决策（先清题）。
  5. **定夺队列覆盖文本启发式（0917AQ，`scripts/dispatcher_answers.py`）**：待答／远期行所列任务 ⇒ 阻塞（类型取队列列）；
     已答行按 `ANSWER_MAP`（＋rules.md §7）解阻塞并生成 `answer:Q-xx` 任务；无映射 ⇒ 保持阻塞并入 `summary.缺任务映射`
     （`--register-unmapped` 登记进队列）；作废 ⇒ 完成。队列决定的 待开↔阻塞 轴写 `队列:` 字段，merge 不记 conflicts。

条目字段（设计 §四 R3）：id／场景／阶段／依赖／触碰区／状态／阻塞类型／产出判据／来源（＋标题、单元）。
  阶段 ∈ {intent, grill, propose, plan, build, merge, release, acceptance, archive, gate, gap}
  状态 ∈ {待开, 在跑, 完成, 阻塞}；阻塞类型 ∈ {决策, 外部, 无}

用法：
  python3 scripts/dispatcher_backlog.py                 # 生成/合并并写盘，打印摘要
  python3 scripts/dispatcher_backlog.py --dry-run       # 只打印摘要，不写盘
  python3 scripts/dispatcher_backlog.py --repo <path> --out <yaml>
  python3 scripts/dispatcher_backlog.py --resolve-conflicts truth   # 用真身状态覆盖台账状态
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:
    from scripts import dispatcher_answers as answers
    from scripts import gates
except ImportError:  # `python3 scripts/dispatcher_backlog.py` 直跑时 scripts/ 自己在 sys.path
    import dispatcher_answers as answers  # type: ignore[no-redef]
    import gates  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
UNMATCHED_PLANS: list[str] = []  # 本次运行未归属任何单元的 plan（只进摘要，不进台账）
ANSWER_REPORT = answers.Report()  # 本次运行定夺队列 ⇒ 台账的结果（生成／解阻塞／缺映射…，只进摘要）

REL = {
    "roadmap": "docs/roadmap/HR项目实施路线图与构建自动化流程.md",
    "relay": "docs/session接力.md",
    "ledger": "docs/openers/号池台账.md",
    "changes": "openspec/changes",
    "plans": "docs/superpowers/plans",
    "intents": "docs/roadmap/intents",
    "queue": gates.QUEUE_REL,
    "rules": ".claude/skills/task-dispatcher/rules.md",
    "out": "docs/roadmap/任务台账.yaml",
}

STATUS = ("待开", "在跑", "完成", "阻塞")
BLOCK = ("决策", "外部", "无")

# 场景优先级（opener：M0>M1>M2>M3；M0′ 归 M0）
SCENE_PRIORITY = {"M0": 0, "M0′": 0, "M1": 1, "M2": 2, "M3": 3}

# 「人事部可见」清单（0917BA，路线图第七节）：条目产出会改变 `.51` 页面上人事部可见／可用的内容。
# 真源是 rules.md §3 的 ```visible-rules 块（path: 触碰区前缀 ／ id: 条目 id 正则）；本常量只是文件缺失时的兜底，
# 两处必须一致（tests/test_dispatcher_backlog.py 有断言）。同一章（单元）任一条目可见 ⇒ 该章 plan／单元／拆段全部可见。
VISIBLE_DEFAULT = (
    "path: app/web/",
    "id: ^m1-[^/]+/9\\.\\d+$",
    "id: ^m2-resume-parse-and-rank/(3|6|9)\\.\\d+$",
)
VISIBLE_BLOCK_RE = re.compile(r"^```visible-rules[^\n]*\n(.*?)^```", re.M | re.S)


@dataclass(frozen=True)
class VisibleRules:
    paths: tuple[str, ...]
    ids: tuple[str, ...]

    def matches(self, task_id: str, paths: list[str]) -> bool:
        if any(re.search(pat, task_id) for pat in self.ids):
            return True
        return any(p.startswith(prefix) for p in paths for prefix in self.paths)


def parse_visible_rules(lines: list[str]) -> VisibleRules:
    paths: list[str] = []
    ids: list[str] = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if line.startswith("path:"):
            paths.append(line[len("path:"):].strip())
        elif line.startswith("id:"):
            ids.append(line[len("id:"):].strip())
    return VisibleRules(paths=tuple(paths), ids=tuple(ids))


def load_visible_rules(repo: Path) -> VisibleRules:
    """读 rules.md 的 ```visible-rules 块；文件或块缺失 ⇒ 内置默认（与 rules.md 同内容）。"""
    f = repo / REL["rules"]
    if f.is_file():
        m = VISIBLE_BLOCK_RE.search(f.read_text(encoding="utf-8"))
        if m:
            return parse_visible_rules(m.group(1).splitlines())
    return parse_visible_rules(list(VISIBLE_DEFAULT))


def mark_visible(entries: list["Entry"], rules: VisibleRules) -> None:
    """① 按 id 正则／触碰区前缀打底；② 同一变更包同一章（`单元`）传播；③ 单元可见 ⇒ 其依赖里的拆段 `plan:*/seg*` 可见。"""
    byid = {e.id: e for e in entries}
    for e in entries:
        e.可见 = rules.matches(e.id, e.触碰区)
    groups: dict[tuple[str, str], list[Entry]] = {}
    for e in entries:
        if e.单元:
            groups.setdefault((e.id.split("/", 1)[0], e.单元), []).append(e)
    for members in groups.values():
        if any(m.可见 for m in members):
            for m in members:
                m.可见 = True
    for e in entries:
        if e.可见 and e.单元:
            for dep in e.依赖:
                if dep.startswith("plan:") and "/seg" in dep and dep in byid:
                    byid[dep].可见 = True

# 决策关键词：命中即「决策」（不可代项，调度器永不排进泳道）
DECISION_KEYS = ("🔴", "不可代", "Shao Peishen", "待你", "待裁决", "本人签认", "本人拍板", "本人配置", "预算", "采购")
# 外部关键词：命中即「外部」（等别人／等环境／等时间）
EXTERNAL_KEYS = ("汤丽萍", "专员", "业务经理", "HR 出", "观察窗", "到期", "留步", "外部", "API key", "API_KEY", ".51")

PATH_RE = re.compile(r"(?<![\w/])(?:[\w.\-]+/)+[\w.\-]+\.(?:py|md|sh|yaml|yml|txt|json|html|js|css|toml|ini|plist)\b")
BACKTICK_RE = re.compile(r"`([^`]+)`")


@dataclass
class Entry:
    id: str
    场景: str
    阶段: str
    标题: str
    来源: str
    单元: str = ""
    依赖: list[str] = field(default_factory=list)
    触碰区: list[str] = field(default_factory=list)
    状态: str = "待开"
    阻塞类型: str = "无"
    产出判据: str = ""
    truth_known: bool = True  # 真身能否独立判定状态；False ⇒ 台账状态无条件优先、不记 conflicts
    闸门: str = ""  # "G2 <subject>"：到闸条目；状态的 待开↔阻塞 轴由定夺队列决定（gates.py），台账不记冲突
    队列: str = ""  # "Q-02 已答 a"／"Q-01 待答"／"Q-11 作废"：定夺队列决定了本条的 待开↔阻塞 轴（dispatcher_answers），台账不记冲突
    可见: bool = False  # 「人事部可见」：产出会改变 `.51` 页面上人事部可见／可用的内容（rules.md ```visible-rules），就绪集内最高排序键

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "场景": self.场景,
            "阶段": self.阶段,
            "标题": self.标题,
        }
        if self.单元:
            d["单元"] = self.单元
        d.update(
            {
                "依赖": sorted(set(self.依赖)),
                "触碰区": sorted(set(self.触碰区)),
                "状态": self.状态,
                "阻塞类型": self.阻塞类型,
                "产出判据": self.产出判据,
                "来源": self.来源,
                "可见": bool(self.可见),
            }
        )
        if self.闸门:
            d["闸门"] = self.闸门
        if self.队列:
            d["队列"] = self.队列
        return d


# ───────────────────────── 通用判定 ─────────────────────────


def infer_block(text: str) -> str:
    if any(k in text for k in DECISION_KEYS):
        return "决策"
    if any(k in text for k in EXTERNAL_KEYS):
        return "外部"
    return "无"


def status_from_marks(text: str, default: str = "待开") -> str:
    """从状态列文字推状态：✅/已闭环/已完成 ⇒ 完成；🚀/在跑 ⇒ 在跑；⏸/待人/🔴 ⇒ 阻塞；否则默认。"""
    if "✅" in text or "已闭环" in text or "已完成" in text or "已归档" in text:
        return "完成"
    if "🚀" in text or "在跑" in text or "运行中" in text:
        return "在跑"
    if "⏸" in text or "待人" in text or "🔴" in text or "待你" in text:
        return "阻塞"
    return default


def strip_md(s: str) -> str:
    s = re.sub(r"\*\*|~~|__", "", s)
    return s.strip()


def touch_paths(text: str, repo: Path) -> list[str]:
    """抽取文本里的文件路径（反引号内优先；只收像路径的 token）。"""
    found: set[str] = set()
    for m in BACKTICK_RE.findall(text):
        for p in PATH_RE.findall(m):
            found.add(p)
        if PATH_RE.fullmatch(m.strip()):
            found.add(m.strip())
    for p in PATH_RE.findall(text):
        found.add(p)
    return sorted(p for p in found if not p.startswith(("http", "C:")))


def split_table_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", line.strip()))


def judge_from(text: str) -> str:
    """「判据：…」子句；没有就取正文本身（去 markdown 粗体）。"""
    m = re.search(r"判据[：:]\s*(.+?)(?:。|$)", text)
    return strip_md(m.group(1)) if m else strip_md(text)[:200]


def scene_of_change(change: str) -> str:
    m = re.match(r"m(\d)", change)
    if m:
        return f"M{m.group(1)}"
    if change.startswith("hr-wecom"):
        return "M0"
    if change.startswith("liaison-"):
        return "M0′"
    return change


# ───────────────────────── 路线图 ─────────────────────────


def parse_roadmap(text: str, rel: str) -> list[Entry]:
    entries: list[Entry] = []
    section = ""
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        if line.startswith("## "):
            section = line
            continue
        if not line.startswith("|") or is_separator(line):
            continue
        cells = split_table_row(line)
        if len(cells) < 4:
            continue
        head = strip_md(cells[0])
        # 业务路线图：| **M2 简历解析…** | 内容 | 状态 | 卡在哪 | 谁推 |
        m = re.match(r"(M\d′?)\s*(.*)", head)
        if "业务路线图" in section and m and len(cells) >= 5:
            code, name = m.group(1), m.group(2).strip()
            status_txt = cells[2]
            if "未启动" in status_txt:
                status = "待开"
            elif re.search(r"✅\s*已归档", status_txt) or _fraction_done(status_txt):
                status = "完成"
            else:
                status = "在跑"
            deps = sorted(
                {f"scene:{c}" for c in re.findall(r"\bM\d′?", " ".join(cells[3:])) if c != code}
            )
            block = "无"
            if status == "待开" and deps:
                block = "无"
            entries.append(
                Entry(
                    id=f"scene:{code}",
                    场景=code,
                    阶段="intent" if status == "待开" else "build",
                    标题=name or code,
                    来源=f"{rel}:{i}",
                    依赖=deps,
                    状态=status,
                    阻塞类型=block,
                    产出判据=strip_md(cells[3])[:200] if status != "完成" else "已归档",
                )
            )
            continue
        # 缺口表：| **G1** | **缺口** | 后果 | 补法 | 状态 |
        g = re.fullmatch(r"G\d+", head)
        if g and len(cells) >= 5:
            status_txt = cells[4]
            status = status_from_marks(status_txt, default="待开")
            block = "无"
            if status == "阻塞":
                block = infer_block(status_txt)
                if block == "无":
                    block = "外部"
            entries.append(
                Entry(
                    id=f"gap:{head}",
                    场景="构建自动化",
                    阶段="gap",
                    标题=strip_md(cells[1])[:120],
                    来源=f"{rel}:{i}",
                    触碰区=touch_paths(cells[3], ROOT),
                    状态=status,
                    阻塞类型=block,
                    产出判据=strip_md(cells[3])[:200],
                )
            )
    return entries


def _fraction_done(s: str) -> bool:
    m = re.search(r"(\d+)/(\d+)", s)
    return bool(m) and m.group(1) == m.group(2)


# ───────────────────────── openspec tasks.md ─────────────────────────

ITEM_RE = re.compile(r"^\s*- \[( |x|X)\]\s*(~~)?(\d+\.\d+[a-z]?(?:bis)?)\s*(.*)$")
CHAPTER_RE = re.compile(r"^## (\d+)\.\s*(.+)$")


def parse_tasks(change: str, text: str, rel: str, plans: dict[str, "PlanInfo"], unit_status: dict[str, str]) -> list[Entry]:
    scene = scene_of_change(change)
    chapters: list[dict] = []
    cur: dict | None = None
    for i, line in enumerate(text.splitlines(), 1):
        cm = CHAPTER_RE.match(line)
        if line.startswith("## "):
            if cm:
                num = int(cm.group(1))
                title = cm.group(2).strip()
                um = re.search(r"\bU(\d+(?:\.\d+)?)(?!\.?\d)", title)
                unit_no = um.group(1) if um else str(num)  # 允许 U2.5 这类插入单元（0917BA 可见薄片）
                cur = {"num": num, "title": title, "unit_no": unit_no, "items": []}
                chapters.append(cur)
            else:
                cur = None
            continue
        if cur is None:
            continue
        im = ITEM_RE.match(line)
        if not im:
            continue
        checked = im.group(1).lower() == "x"
        tomb = bool(im.group(2))
        body = im.group(4)
        moved = "已移出" in body or tomb
        cur["items"].append(
            {"no": im.group(3), "checked": checked, "moved": moved, "body": body, "line": i}
        )

    entries: list[Entry] = []
    unit_ids: list[str] = []
    gate_blocks: dict[str, list[str]] = {}  # unit id -> [gate item ids]
    prev_unit: str | None = None
    for ch in chapters:
        is_gate = ch["num"] == 0
        unit_id = f"{change}/U{ch['unit_no']}"
        plan_id = f"{unit_id}/plan"
        live = [it for it in ch["items"] if not it["moved"]]
        done = all(it["checked"] for it in live)  # 全部移出的章节视为完成（没有活可做）
        matched = [] if is_gate else _match_plans(plans, change, ch["unit_no"])
        seg_ids = [sid for p in matched for sid in p.segment_ids]
        checked_nos = {it["no"] for it in ch["items"] if it["checked"]}
        for p in matched:
            # 文件名 unit<N>-<k> 指向单条 N.k 的 plan：该项已勾即 plan 已执行；整单元 plan 看单元是否全勾
            sub = re.search(rf"unit{re.escape(ch['unit_no'])}-(\d+)(?!\d)", p.stem)
            sub_done = bool(sub) and f"{ch['num']}.{sub.group(1)}" in checked_nos
            unit_status[p.stem] = "完成" if (done or sub_done) else "待开"
        # 条目级：只是进度跟踪，不是调度单元——依赖它所属单元的 plan／拆段，避免被当成 ready
        item_deps_base: list[str] = [] if is_gate else (seg_ids if matched else [plan_id])
        if not is_gate and prev_unit:
            item_deps_base = item_deps_base + [prev_unit]
        for it in live:
            if it["checked"]:
                continue
            item_id = f"{change}/{it['no']}"
            body = it["body"]
            block = infer_block(body)
            status = "阻塞" if block != "无" else "待开"
            deps: list[str] = list(item_deps_base)
            pm = re.search(r"前置[：:（(]\s*([^）)。]+)", body)
            if pm:
                deps += [f"{change}/{n}" for n in re.findall(r"\d+\.\d+", pm.group(1))]
            for u in re.findall(r"阻塞 U(\d+(?:\.\d+)?)", body):
                gate_blocks.setdefault(f"{change}/U{u}", []).append(item_id)
            entries.append(
                Entry(
                    id=item_id,
                    场景=scene,
                    阶段="gate" if is_gate else "build",
                    标题=strip_md(re.sub(r"[⏸🔴✅]\s*", "", body.split("。")[0]))[:120],
                    来源=f"{rel}:{it['line']}",
                    单元=ch["title"],
                    依赖=deps,
                    触碰区=touch_paths(body, ROOT),
                    状态=status,
                    阻塞类型=block,
                    产出判据=judge_from(body),
                )
            )
        if is_gate:
            continue
        unit_paths = sorted({p for it in live for p in touch_paths(it["body"], ROOT)})
        # 单元的 plan 任务：无 plan ⇒ 待开（spec-to-plan）；有 plan ⇒ 完成
        entries.append(
            Entry(
                id=plan_id,
                场景=scene,
                阶段="plan",
                标题=f"spec-to-plan：{ch['num']}. {ch['title']}",
                来源=rel,
                单元=ch["title"],
                依赖=[prev_unit] if prev_unit else [],
                触碰区=[f"docs/superpowers/plans/*-{change}-unit{ch['unit_no']}-*.md"],
                状态="完成" if (matched or done) else "待开",
                阻塞类型="无",
                产出判据=f"docs/superpowers/plans/ 下存在文件名含 unit{ch['unit_no']} 且含 {change}（或其前缀）的 plan，`grep -c '^### Task '` ≥ 1",
            )
        )
        # 单元级：全部 checkbox 勾选 ＋ 各拆段合 main
        entries.append(
            Entry(
                id=unit_id,
                场景=scene,
                阶段="build",
                标题=f"{ch['num']}. {ch['title']}",
                来源=rel,
                单元=ch["title"],
                依赖=[plan_id] + seg_ids + ([prev_unit] if prev_unit else [])
                + [f"{change}/{it['no']}" for it in live if not it["checked"]],  # 聚合条目：从不 ready，只在全勾时完成
                触碰区=unit_paths,
                状态="完成" if done else "待开",
                阻塞类型="无",
                产出判据=f"章节 {ch['num']} 全部 checkbox 勾选且各拆段合 main（`git cherry -v main <分支>` 无 `+`）",
            )
        )
        unit_ids.append(unit_id)
        prev_unit = unit_id
    for e in entries:
        if e.id in gate_blocks:
            e.依赖 = sorted(set(e.依赖) | set(gate_blocks[e.id]))
    all_done = all(it["checked"] for ch in chapters for it in ch["items"] if not it["moved"])
    entries.append(
        Entry(
            id=f"change:{change}",
            场景=scene,
            阶段="archive",
            标题=f"变更包 {change}",
            来源=rel,
            依赖=unit_ids,
            状态="待开" if not all_done else "完成",
            阻塞类型="无",
            产出判据="tasks.md 全部勾选 ⇒ 当场 openspec-archive-change（CLAUDE.md 归档时限）",
        )
    )
    return entries


# ───────────────────────── superpowers plans ─────────────────────────


@dataclass
class PlanInfo:
    stem: str
    rel: str
    task_count: int
    segments: list[tuple[int, int]]
    pending: list[str]
    paths: list[str]

    @property
    def segment_ids(self) -> list[str]:
        return [f"plan:{self.stem}/seg{k}" for k in range(1, len(self.segments) + 1)]


def parse_plan(path: Path, rel: str) -> PlanInfo:
    text = path.read_text(encoding="utf-8")
    task_count = len(re.findall(r"^### Task \d+", text, flags=re.M))
    segments: list[tuple[int, int]] = []
    for m in re.finditer(r"第\s*(\d+)\s*条[：:]\s*Task\s*(\d+)\s*[–—-]\s*(\d+)", text):
        segments.append((int(m.group(2)), int(m.group(3))))
    if not segments and task_count:
        segments = [(1, task_count)]
    pending: list[str] = []
    sec = re.search(r"^## 待裁决[^\n]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if sec:
        for m in re.finditer(r"^\d+\.\s+\**(.+?)\**(?:——|—|：|:|$)", sec.group(1), flags=re.M):
            pending.append(strip_md(m.group(1))[:120])
    paths: list[str] = []
    fs = re.search(r"^## 文件结构[^\n]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if fs:
        paths = touch_paths(fs.group(1), ROOT)
    return PlanInfo(stem=path.stem, rel=rel, task_count=task_count, segments=segments, pending=pending, paths=paths)


def _match_plans(plans: dict[str, PlanInfo], change: str, unit_no: str) -> list[PlanInfo]:
    """文件名含 unit<N>（后面不跟数字或小数点——`unit2` ⛔ 不吃 `unit2.5`）且含变更包全名或其首段前缀（如 m2-unit0）的 plan，按名排序。"""
    prefix = change.split("-")[0]
    u = re.escape(str(unit_no))
    out = []
    for stem in sorted(plans):
        if not re.search(rf"unit{u}(?!\.?\d)", stem):
            continue
        if change in stem or re.search(rf"(?<![\w]){re.escape(prefix)}-unit{u}(?!\.?\d)", stem):
            out.append(plans[stem])
    return out


def plan_entries(plans: dict[str, PlanInfo], unit_status: dict[str, str]) -> list[Entry]:
    """每份 plan 一条 + 每个拆段一条 + 每条待裁决一条。plan 的场景按文件名前缀推。"""
    out: list[Entry] = []
    for stem in sorted(plans):
        p = plans[stem]
        if p.task_count == 0 or stem not in unit_status:
            continue  # 未归属任何在册单元的 plan（历史／已归档）不入台账，避免把已交付的活当 ready
        m = re.search(r"\d{4}-\d{2}-\d{2}-(.+)", stem)
        name = m.group(1) if m else stem
        scene = scene_of_change(name)
        if scene == name:
            scene = "构建自动化" if "liaison" not in name and "audit" not in name else "M0"
        done = unit_status.get(stem) == "完成"
        prev: str | None = None
        for k, (a, b) in enumerate(p.segments, 1):
            sid = f"plan:{stem}/seg{k}"
            out.append(
                Entry(
                    id=sid,
                    场景=scene,
                    阶段="build",
                    标题=f"{name} Task {a}–{b}",
                    来源=p.rel,
                    依赖=[prev] if prev else [],
                    触碰区=p.paths,
                    状态="完成" if done else "待开",
                    阻塞类型="无",
                    产出判据=f"Task {a}–{b} 各自 TDD 通过、spec/code review 两阶段通过；分支合 main 后 `git cherry -v main <分支>` 无 `+`",
                    truth_known=False,
                )
            )
            prev = sid
        for n, q in enumerate(p.pending, 1):
            block = infer_block(q)
            if block == "无":
                block = "决策"
            out.append(
                Entry(
                    id=f"plan:{stem}/待裁决#{n}",
                    场景=scene,
                    阶段="build",
                    标题=q,
                    来源=p.rel,
                    状态="完成" if done else "阻塞",
                    阻塞类型=block,
                    产出判据="定夺队列该条状态＝已答，且答复已回写计划「待裁决」节",
                    truth_known=False,
                )
            )
    return out


# ───────────────────────── session接力 ─────────────────────────

TAG4_RE = re.compile(r"【谁做[：:]\s*(.*?)】\s*【状态[：:]\s*(.*?)】\s*【判据[：:]\s*(.*?)】\s*【不做会怎样[：:]\s*(.*?)】", re.S)


def parse_relay(text: str, rel: str) -> list[Entry]:
    entries: list[Entry] = []
    seen: dict[str, int] = {}
    section = ""
    lines = text.splitlines()
    in_table = False
    for i, line in enumerate(lines, 1):
        if line.startswith("#"):
            section = strip_md(line.lstrip("#").strip())
            in_table = False
            continue
        if line.startswith("|"):
            cells = split_table_row(line)
            if cells and cells[0] == "#" and len(cells) >= 4 and "谁做" in cells[2]:
                in_table = True
                continue
            if is_separator(line):
                continue
            if in_table and len(cells) >= 4:
                key = strip_md(cells[0])
                if not key:
                    continue
                who, status_txt = cells[2], cells[3]
                judge = cells[4] if len(cells) > 4 else ""
                status = status_from_marks(status_txt)
                if status == "完成":
                    continue
                if infer_block(who) == "决策":
                    status, block = "阻塞", "决策"
                elif "待派发" in status_txt:
                    status, block = "待开", "无"
                else:
                    if status == "待开" and infer_block(who + status_txt) != "无":
                        status = "阻塞"
                    block = infer_block(who + " " + status_txt) if status == "阻塞" else "无"
                    if status == "阻塞" and block == "无":
                        block = "外部"
                base = f"relay:{key}"
                eid = base
                if base in seen:
                    eid = f"{base}@{_slug(section)}"
                seen[base] = i
                entries.append(
                    Entry(
                        id=eid,
                        场景=_scene_from_text(cells[1] + section),
                        阶段="acceptance" if "验收" in cells[1] else "build",
                        标题=strip_md(cells[1])[:120],
                        来源=f"{rel}:{i}",
                        触碰区=touch_paths(cells[1] + judge, ROOT),
                        状态=status,
                        阻塞类型=block,
                        产出判据=strip_md(judge)[:200],
                    )
                )
            continue
        in_table = False
    # 四标签行式待办
    n = 0
    for m in TAG4_RE.finditer(text):
        n += 1
        who, status_txt, judge, _ = (re.sub(r"\s+", " ", strip_md(x)) for x in m.groups())
        status = status_from_marks(status_txt)
        if status == "完成":
            continue
        block = infer_block(who + " " + status_txt)
        if status in ("待开", "阻塞") and block != "无":
            status = "阻塞"
        if status == "阻塞" and block == "无":
            block = "外部"
        line_no = text[: m.start()].count("\n") + 1
        entries.append(
            Entry(
                id=f"relay:tag#{hashlib.sha1(judge[:80].encode()).hexdigest()[:8]}",
                场景=_scene_from_text(judge),
                阶段="build",
                标题=judge[:120],
                来源=f"{rel}:{line_no}",
                触碰区=touch_paths(judge, ROOT),
                状态=status,
                阻塞类型=block,
                产出判据=judge[:200],
            )
        )
    return entries


def _slug(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:6]


def _scene_from_text(s: str) -> str:
    m = re.search(r"\bM(\d)\b", s)
    if m:
        return f"M{m.group(1)}"
    if "liaison" in s or "值守" in s or "回件" in s or "跟进信" in s:
        return "M0"
    if "m1-" in s or "intake" in s:
        return "M1"
    if "m2-" in s or "简历" in s:
        return "M2"
    return "构建自动化"


# ───────────────────────── 号池台账 ─────────────────────────


def parse_ledger(text: str, rel: str) -> list[Entry]:
    rows: dict[str, list[tuple[int, str, str]]] = {}
    for i, line in enumerate(text.splitlines(), 1):
        if not line.startswith("|") or is_separator(line):
            continue
        cells = split_table_row(line)
        if len(cells) < 4:
            continue
        m = re.search(r"\[Mac\]\d{4}[A-Z]{1,2}", cells[1])
        if not m:
            continue
        rows.setdefault(m.group(0), []).append((i, cells[2], cells[3]))
    out: list[Entry] = []
    closed = {c for c, rs in rows.items() if any(k in rs[-1][2] for k in ("✅", "已完成", "已闭合", "⚰️"))}
    for code in sorted(rows):
        i, topic, dest = rows[code][-1]
        alltxt = " ".join(d for _, _, d in rows[code])
        if code in closed:
            continue
        deps = [f"opener:[Mac]{c}" for c in re.findall(r"待 (\d{4}[A-Z]{1,2}) 收敛", dest) if f"[Mac]{c}" not in closed]
        if "⏸" in dest:
            status = "阻塞"
            block = infer_block(dest)
            if block == "无":
                block = "外部"
        elif re.search(r"待[^|]{0,30}发车|待派发", dest):
            status, block = "待开", "无"
        elif "🚀" in dest or "在跑" in dest or "运行中" in dest:
            status, block = "在跑", "无"
        else:
            continue  # 去向无明确开放态标记的行不入台账：「已发车」不等于在跑，很多号跑完从不补结果行；在跑与否由调度器查 results.tsv
        out.append(
            Entry(
                id=f"opener:{code}",
                场景=_scene_from_text(topic),
                阶段="build",
                标题=strip_md(topic)[:120],
                来源=f"{rel}:{i}",
                依赖=deps,
                触碰区=touch_paths(topic + " " + alltxt, ROOT),
                状态=status,
                阻塞类型=block,
                产出判据=strip_md(dest)[:200],
            )
        )
    return out


# ───────────────────────── intent 草稿 → propose 条目 ─────────────────────────

FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.S)
UNANSWERED_RE = re.compile(r"^\s*(?:- \[ \]|- (?!\[x\])(?!\[X\]))\s*\S", re.M)


def _frontmatter(text: str) -> dict[str, str]:
    m = FRONT_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.split("#", 1)[0].strip()
    return out


def _section(text: str, title: str) -> str:
    m = re.search(rf"^##\s*{re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def parse_intents(repo: Path, rel_dir: str, changes: set[str]) -> list[Entry]:
    """`docs/roadmap/intents/<场景>-intent.md` ⇒ `propose:<场景>`。
    有未答题（「## 待答题」节非空且有未勾条目）⇒ 阻塞／决策（grill 要他逐题答）；该场景已有 openspec 变更包
    （包存在本身就是 propose 的真身，⛔ 不等 `status` 改字）⇒ 完成；否则待开——到不到 G1 由 apply_gates 按定夺队列判。"""
    out: list[Entry] = []
    d = repo / rel_dir
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        text = p.read_text(encoding="utf-8")
        fm = _frontmatter(text)
        stem = p.stem[:-7] if p.stem.endswith("-intent") else p.stem
        scene_name = fm.get("场景") or stem
        m = re.search(r"\bM\d′?", scene_name) or re.search(r"\bM\d′?", stem)
        scene = m.group(0) if m else scene_name
        rel = f"{rel_dir}/{p.name}"
        pending = _section(text, "待答题")
        unanswered = bool(UNANSWERED_RE.search(pending))
        has_pkg = any(scene_of_change(c) == scene for c in changes)
        if has_pkg:
            status, block = "完成", "无"
        elif unanswered:
            status, block = "阻塞", "决策"
        else:
            status, block = "待开", "无"
        out.append(
            Entry(
                id=f"propose:{scene}",
                场景=scene,
                阶段="propose",
                标题=f"openspec-propose：{scene_name}（intent 定稿后）",
                来源=f"{rel}:1",
                触碰区=[f"openspec/changes/"],
                状态=status,
                阻塞类型=block,
                产出判据=(
                    "G1 放行（定夺队列该行已答「定」）后：openspec/changes/<pkg>/ 齐 proposal／specs／design／tasks，"
                    "`openspec validate <pkg> --strict` 过"
                    + ("；当前 intent 有未答题，先由 requirement-grill 在本线清题" if unanswered else "")
                ),
            )
        )
    return out


def apply_gates(entries: list[Entry], queue_text: str) -> None:
    """到闸条目：定夺队列未放行 ⇒ 阻塞／决策 并写 `闸门`；放行 ⇒ 待开／无（只动 待开↔阻塞 轴）。"""
    for e in entries:
        gs = gates.gate_for_task({"id": e.id, "阶段": e.阶段, "场景": e.场景})
        if gs is None:
            continue
        gate, subject = gs
        e.闸门 = f"{gate} {subject}"
        if e.状态 != "待开":
            continue  # 真身生成的条目只有 待开 会被闸拦；本就阻塞（如 intent 有未答题）或已完成的不动
        state = gates.gate_state(gate, subject, text=queue_text)
        if state != "已放行":
            e.状态, e.阻塞类型 = "阻塞", "决策"
            e.产出判据 = f"【{gate} 闸门·{state}】" + e.产出判据


# ───────────────────────── 生成、合并、ready ─────────────────────────


def generate(repo: Path) -> list[Entry]:
    entries: list[Entry] = []
    rm = repo / REL["roadmap"]
    if rm.exists():
        entries += parse_roadmap(rm.read_text(encoding="utf-8"), REL["roadmap"])
    plans: dict[str, PlanInfo] = {}
    pdir = repo / REL["plans"]
    if pdir.is_dir():
        for p in sorted(pdir.glob("*.md")):
            plans[p.stem] = parse_plan(p, f"{REL['plans']}/{p.name}")
    unit_status: dict[str, str] = {}
    cdir = repo / REL["changes"]
    if cdir.is_dir():
        for t in sorted(cdir.glob("*/tasks.md")):
            change = t.parent.name
            if change == "archive":
                continue
            entries += parse_tasks(change, t.read_text(encoding="utf-8"), f"{REL['changes']}/{change}/tasks.md", plans, unit_status)
    entries += plan_entries(plans, unit_status)
    UNMATCHED_PLANS[:] = sorted(st for st, p in plans.items() if p.task_count and st not in unit_status)
    changes = {t.parent.name for t in cdir.glob("*/tasks.md") if t.parent.name != "archive"} if cdir.is_dir() else set()
    entries += parse_intents(repo, REL["intents"], changes)
    relay = repo / REL["relay"]
    if relay.exists():
        entries += parse_relay(relay.read_text(encoding="utf-8"), REL["relay"])
    ledger = repo / REL["ledger"]
    if ledger.exists():
        entries += parse_ledger(ledger.read_text(encoding="utf-8"), REL["ledger"])
    # id 去重：同 id 后者并入前者的依赖/触碰区，保守取「阻塞」状态
    byid: dict[str, Entry] = {}
    for e in entries:
        if e.id in byid:
            o = byid[e.id]
            o.依赖 = sorted(set(o.依赖) | set(e.依赖))
            o.触碰区 = sorted(set(o.触碰区) | set(e.触碰区))
            if e.状态 == "阻塞":
                o.状态, o.阻塞类型 = e.状态, e.阻塞类型
        else:
            byid[e.id] = e
    merged = list(byid.values())
    queue_text = _queue_text(repo)
    global ANSWER_REPORT
    ANSWER_REPORT = answers.apply_answers(merged, queue_text, new_entry=Entry)  # 先按队列定 待开↔阻塞，再过闸门
    apply_gates(merged, queue_text)
    mark_visible(merged, load_visible_rules(repo))
    return merged


def _queue_text(repo: Path) -> str:
    q = repo / REL["queue"]
    return q.read_text(encoding="utf-8") if q.is_file() else ""


def load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {t["id"]: t for t in data.get("tasks", []) if isinstance(t, dict) and "id" in t}


def merge(existing: dict[str, dict], generated: list[Entry], resolve: str = "ledger") -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for e in generated:
        d = e.to_dict()
        seen.add(e.id)
        old = existing.get(e.id)
        if old:
            truth_status = d["状态"]
            ledger_status = old.get("状态", truth_status)
            if ledger_status != truth_status and (e.闸门 or e.队列) and {ledger_status, truth_status} <= {"待开", "阻塞"}:
                pass  # 到闸／在队列里的条目，其 待开↔阻塞 由定夺队列决定（gates.py／dispatcher_answers），台账手改不算数、不记冲突
            elif ledger_status != truth_status and not e.truth_known:
                d["状态"] = ledger_status  # 真身判不了（如拆段是否已跑），台账说了算，不记冲突
            elif ledger_status != truth_status:
                if resolve == "truth":
                    d["状态"] = truth_status
                else:
                    d["状态"] = ledger_status
                    d["conflicts"] = {"状态": {"台账": ledger_status, "真身": truth_status}}
            for k in ("备注", "泳道", "opener"):
                if k in old:
                    d[k] = old[k]
        out.append(d)
    # 台账里有、真身不再生成的条目：原样保留，标记真身消失（很可能已完成或来源被归档）
    for tid in sorted(existing):
        if tid in seen:
            continue
        old = dict(existing[tid])
        if old.get("状态") != "完成":
            if resolve == "truth":
                old["状态"] = "完成"
                old.pop("conflicts", None)
            else:
                old["conflicts"] = {"状态": {"台账": old.get("状态"), "真身": "来源已消失或已勾选"}}
        out.append(old)
    return out


_QUEUE_TEXT: str | None = None  # main/show 按 --repo 装入；None ⇒ 读 ROOT 下的定夺队列


def set_queue_repo(repo: Path) -> None:
    global _QUEUE_TEXT
    _QUEUE_TEXT = _queue_text(repo)


def gate_held(tasks: list[dict], ready: list[str]) -> list[tuple[str, str, str, str]]:
    """ready 候选里到闸未放行的 (id, 闸, subject, 状态名)——按定夺队列现查，⛔ 不认台账状态。"""
    text = _QUEUE_TEXT if _QUEUE_TEXT is not None else _queue_text(ROOT)
    return gates.filter_ready(tasks, ready, text)[1]


def compute_ready(tasks: list[dict]) -> tuple[list[str], list[str]]:
    """ready ＝ 待开 ∧ 阻塞类型 无 ∧ 依赖全完成 ∧ 闸门已放行（gates.py 只读定夺队列）。返回 (ready ids, 未知依赖 ids)。
    排序（rules.md §3）：就绪集内「人事部可见」最先，其次场景优先级，再 id 字典序——可见只改就绪集内的次序，⛔ 不越依赖、不越闸门。"""
    byid = {t["id"]: t for t in tasks}
    ready: list[str] = []
    unknown: set[str] = set()
    for t in tasks:
        if t.get("状态") != "待开" or t.get("阻塞类型") != "无":
            continue
        ok = True
        for dep in t.get("依赖", []):
            d = byid.get(dep)
            if d is None:
                unknown.add(dep)
                ok = False
            elif d.get("状态") != "完成":
                ok = False
        if ok:
            ready.append(t["id"])
    ready.sort(key=lambda i: (not byid[i].get("可见", False), SCENE_PRIORITY.get(byid[i]["场景"], 9), i))
    held = {h[0] for h in gate_held(tasks, ready)}
    return [r for r in ready if r not in held], sorted(unknown)


def summarize(tasks: list[dict]) -> dict:
    ready, unknown = compute_ready(tasks)
    return {
        "条目数": len(tasks),
        "按状态": {s: sum(1 for t in tasks if t.get("状态") == s) for s in STATUS},
        "阻塞·决策": sum(1 for t in tasks if t.get("状态") == "阻塞" and t.get("阻塞类型") == "决策"),
        "阻塞·外部": sum(1 for t in tasks if t.get("状态") == "阻塞" and t.get("阻塞类型") == "外部"),
        "conflicts": sorted(t["id"] for t in tasks if "conflicts" in t),
        "ready": ready,
        "闸门待放行": [f"{tid} ← {g} `{sub}`（{st}）" for tid, g, sub, st in gates.at_gate(tasks, _QUEUE_TEXT if _QUEUE_TEXT is not None else _queue_text(ROOT)) if st != "已放行"],
        "未知依赖": unknown,
        "未归属plans": list(UNMATCHED_PLANS),
        "缺任务映射": [key for _no, key, _reply in ANSWER_REPORT.缺映射],
    }


def render(tasks: list[dict]) -> str:
    doc = {
        "version": 1,
        "说明": (
            "由 scripts/dispatcher_backlog.py 从路线图／openspec tasks.md／superpowers plans／session接力／号池台账"
            "合并生成。状态以本文件为准；与真身不一致的条目带 conflicts:。⛔ 不要手工改 id。"
        ),
        "summary": summarize(tasks),
        "tasks": tasks,
    }
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=160)


def show(path: Path, what: str) -> int:
    tasks = list(load_existing(path).values())
    ready, _ = compute_ready(tasks)
    if what == "ready":
        picked = [t for t in tasks if t["id"] in set(ready)]
        picked.sort(key=lambda t: ready.index(t["id"]))
    elif what == "blocked":
        picked = [t for t in tasks if t.get("状态") == "阻塞"]
    elif what == "running":
        picked = [t for t in tasks if t.get("状态") == "在跑"]
    elif what == "gated":
        held = {h[0]: h for h in gates.at_gate(tasks, _QUEUE_TEXT or "") if h[3] != "已放行"}
        picked = [dict(t, 闸门状态=f"{held[t['id']][1]} `{held[t['id']][2]}` {held[t['id']][3]}") for t in tasks if t["id"] in held]
    else:
        picked = [t for t in tasks if "conflicts" in t]
    for t in picked:
        extra = f" 阻塞类型={t.get('阻塞类型')}" if what == "blocked" else ""
        extra += f" conflicts={t['conflicts']}" if what == "conflicts" else ""
        extra += f" 闸门={t['闸门状态']}" if what == "gated" else ""
        extra += " 可见=✓" if t.get("可见") else ""
        print(f"{t['id']}\t{t.get('场景')}\t{t.get('阶段')}\t{t.get('状态')}{extra}\t{t.get('标题', '')[:80]}\t触碰区={','.join(t.get('触碰区', [])) or '-'}")
    print(f"# {what}: {len(picked)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resolve-conflicts", choices=("ledger", "truth"), default="ledger")
    ap.add_argument("--show", choices=("ready", "blocked", "running", "conflicts", "gated"), default=None,
                    help="只读：按类打印现有台账里的条目（一行一条），不生成、不写盘；台账 > 40 KB，⛔ 不要整读")
    ap.add_argument("--register-unmapped", action="store_true",
                    help="已答行无 ANSWER_MAP 映射 ⇒ 追加定夺队列「【答复→任务映射缺失】Q-xx」行（去重），不静默")
    args = ap.parse_args(argv)
    set_queue_repo(args.repo.resolve())
    if args.show:
        return show(args.out or ((args.repo.resolve()) / REL["out"]), args.show)
    repo = args.repo.resolve()
    out = args.out or (repo / REL["out"])
    generated = generate(repo)
    existing = load_existing(out)
    tasks = merge(existing, generated, resolve=args.resolve_conflicts)
    text = render(tasks)
    s = summarize(tasks)
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(f"条目数={s['条目数']} 待开={s['按状态']['待开']} 在跑={s['按状态']['在跑']} 完成={s['按状态']['完成']} 阻塞={s['按状态']['阻塞']}")
    print(f"阻塞·决策={s['阻塞·决策']} 阻塞·外部={s['阻塞·外部']} conflicts={len(s['conflicts'])} ready={len(s['ready'])}")
    for r in s["ready"]:
        print(f"  ready: {r}")
    if s["未知依赖"]:
        print(f"  未知依赖: {', '.join(s['未知依赖'])}")
    r = ANSWER_REPORT
    fresh = [tid for tid in r.生成 if tid not in existing]
    print(f"定夺队列⇒台账: 映射任务={len(r.生成)}（本次新入台账={len(fresh)}） 解阻塞={len(r.解阻塞)} 保持阻塞={len(r.保持阻塞)} 作废={len(r.作废)} 待答压阻塞={len(r.待答阻塞)} 缺映射={len(r.缺映射)}")
    for tid in r.生成:
        print(f"  映射任务: {tid}{'' if tid in existing else '（新）'}")
    for no, key, reply in r.缺映射:
        print(f"  缺任务映射: {key}（{reply[:40]}）")
    if args.register_unmapped and r.缺映射 and not args.dry_run:
        for number in answers.register_unmapped(repo / REL["queue"], r.缺映射):
            print(f"  已登记定夺队列: {number}")
    print(f"{'(dry-run) ' if args.dry_run else ''}→ {out.relative_to(repo) if out.is_relative_to(repo) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
