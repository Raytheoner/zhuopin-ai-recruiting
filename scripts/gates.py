#!/usr/bin/env python3
"""在环闸门 G1–G5 的放行判据（2026-09-17 `[Mac]0917AO`，口径见 `docs/roadmap/任务驱动workflow设计.md`「在环闸门」）。

Shao Peishen 定的硬口径：自动化只推进两个闸门之间的活；到闸一律写 `docs/roadmap/定夺队列.md` 并停在该场景。
放行**只认**定夺队列对应行「状态＝已答」且答复含放行字样（定／发／签），⛔ 不认聊天记忆、不认台账状态自改。
所以本模块只读定夺队列——`gate_open()` 没有任何别的输入源，也不缓存。

| 闸 | 到闸 | 放行字样 | 放行后 |
|---|---|---|---|
| G1 intent 定稿 | grill 产出 intent 且无未答题 | 定 | propose |
| G2 design／spec 定稿 | 包 validate 过、无 Open Questions | 定 | spec-to-plan → build |
| G3 发布 | 场景全部单元合 main | 发 | 动作通道 `deploy-51`（后续）|
| G4 发信 | 跟进信 md＋docx 起草完、自检过 | 发（可与「审核通过」同条） | Cowork 经动作通道 `send-followup` |
| G5 口径签认 | 回件拆件判为口径点 | 签 | 口径点台账转已签认 |

闸门行的机器形状（`gate_request()` 写、`gate_state()` 读，两头共用 `gate_marker()`）：
「问题」列以 `【G<n> <名>】`<subject>`` 开头，subject 用反引号包住——G1／G2／G3 的 subject 是场景或变更包名，
G4 是信件编号（`人事部#2`），G5 是口径点 id。同一 (闸, subject) 只入队一次（去重靠同一标记）。

判定真值表（tests/test_gates.py 钉死）：
  缺行 → 关；状态≠已答 → 关；已答但答复**不是**放行字（「改：…再发」「待定」「不发」「暂不定」都不是）→ 关；
  已答且答复为放行字（整条就是它／「」引住／末字，G4 还须含「审核通过」）→ 开；状态或答复含「作废」→ 关（状态名「作废」）。
  答复列须由 Cowork 转写为**字面**放行字，⛔ 不填 `a`/`b`——填字母的行永远是「已答·未放行」。

调度器接线：`scripts/dispatcher_backlog.py` 生成台账时把到闸未放行的条目标 `阻塞／决策` 并写 `闸门:` 字段，
`compute_ready` 再按本模块复核一遍（台账状态被手改也放不过）；`scripts/action_request.py` 的 `send-followup`
在「🆕 待发」之外再要 G4 放行。

用法：
  python3 scripts/gates.py state G1 M3                 # 打印 缺行|待答|已答·未放行|已放行|作废
  python3 scripts/gates.py open  G4 '人事部#2'          # 退出码 0＝放行，1＝未放行
  python3 scripts/gates.py request G1 M3 --scene M3 --artifact docs/roadmap/intents/M3-intent.md --tasks propose:M3
  python3 scripts/gates.py sweep [--apply]             # 台账里到闸的条目逐条对队列；--apply 才追加缺的行
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_REL = "docs/roadmap/定夺队列.md"

GATES: dict[str, dict[str, str]] = {
    "G1": {"名": "intent 定稿", "字": "定", "到闸": "grill 产出 intent 且无未答题", "放行后": "propose"},
    "G2": {"名": "design／spec 定稿", "字": "定", "到闸": "包 `openspec validate` 过、无 Open Questions", "放行后": "spec-to-plan → build"},
    "G3": {"名": "发布", "字": "发", "到闸": "场景全部单元合 main", "放行后": "动作通道 `deploy-51`（后续）或无头 `.51` 发版"},
    "G4": {"名": "发信", "字": "发", "到闸": "跟进信 md＋docx 起草完、自检过", "放行后": "Cowork 经动作通道 `send-followup`"},
    "G5": {"名": "口径签认", "字": "签", "到闸": "回件拆件判为口径点", "放行后": "口径点台账转已签认"},
}

# 台账阶段 → 闸门；subject 由 gate_for_task 取。acceptance（起草）与拆件不在这里：起草允许闸前进行，
# 发信由动作通道按 G4 拦，口径签认由 Cowork 按 G5 走，调度器本就不碰这两个动作。
STAGE_GATE = {"propose": "G1", "plan": "G2", "release": "G3"}

STATES = ("缺行", "待答", "已答·未放行", "已放行", "作废")

# 放行字必须「独立成词」：整条答复就是它、或它被「」『』引号引住、或它是答复末字（如「审核通过·发」「已发」）。
# 单字包含不算——「改：第二段删掉再发」「待定」「签名栏漏了」都含字但不是放行（final review 0917AO 🔴 #1）。
_QUOTED = "[「『\"'“‘]{z}[」』\"'”’]"
_NEG_BEFORE = re.compile(r"(?:不|别|勿|未|暂不|先不|暂缓|不要|再)\s*$")
_HOLD_PREFIX = re.compile(r"^(?:改|待|不|别|勿|暂|先|等|未|再)")
_TRAIL_PUNCT = re.compile(r"[\s。．.!！~～、，,;；]+$")
_NUM = re.compile(r"^Q-(\d+)$")

QUEUE = ROOT / QUEUE_REL


def configure(repo: Path) -> None:
    """把定夺队列路径钉到 `repo`（单测指到临时仓库）。"""
    global QUEUE
    QUEUE = Path(repo) / QUEUE_REL


configure(Path(os.environ.get("COMMIT_LAUNCHER_REPO", ROOT)))


# ── 队列解析 ─────────────────────────────────────────────────────────────────


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep(line: str) -> bool:
    return bool(re.match(r"^\|\s*:?-{2,}", line.strip()))


def parse_queue(text: str) -> list[dict[str, str]]:
    """把文件里所有管道表按各自表头解析成 dict 行；附 `_section`（所在 `##` 标题）与 `_line`（1 起行号）。"""
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    header: list[str] | None = None
    section = ""
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section = line[3:].strip()
            header = None
            continue
        if not line.lstrip().startswith("|"):
            header = None
            continue
        if _is_sep(line):
            continue
        cells = _cells(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _is_sep(nxt):
            header = cells
            continue
        if header is None:
            continue
        row = {h: (cells[k] if k < len(cells) else "") for k, h in enumerate(header)}
        row["_section"] = section
        row["_line"] = str(i + 1)
        rows.append(row)
    return rows


def _read_queue(queue: Path | None) -> str:
    q = Path(queue) if queue else QUEUE
    return q.read_text(encoding="utf-8") if q.is_file() else ""


# ── 闸门行定位与判定 ─────────────────────────────────────────────────────────


def gate_marker(gate: str, subject: str) -> str:
    if gate not in GATES:
        raise ValueError(f"未知闸门：{gate}（只有 {', '.join(GATES)}）")
    return f"【{gate} {GATES[gate]['名']}】`{subject}`"


def find_gate_row(gate: str, subject: str, text: str) -> dict[str, str] | None:
    """「问题」列同时含 `【G<n>` 与 `` `subject` `` 的行；多行时取最后一行（后写的是最新答复）。"""
    if gate not in GATES:
        raise ValueError(f"未知闸门：{gate}")
    tag = f"【{gate} "
    key = f"`{subject}`"
    hit = None
    for row in parse_queue(text):
        q = row.get("问题", "")
        if tag in q and key in q:
            hit = row
    return hit


def reply_releases(gate: str, reply: str) -> bool:
    """答复是否**为**本闸的放行字样（不是"含该字"）。

    开：`定`／`发`／`签` 独立成词——整条就是它、被「」引住（「逐条改后「定」」）、或是末字（「审核通过·发」）。
    关：含「作废」「驳回」；以 改／待／不／别／暂／先／等／未／再 开头（「改：…再发」「待定」「等窗口定了再发」）；
        引号前紧跟否定或「再」（「不「发」」「改后再「发」」）；G4 还须同时含「审核通过」（先审核通过再发，可同条）。
    """
    reply = (reply or "").strip()
    if not reply or "作废" in reply or "驳回" in reply:
        return False
    z = GATES[gate]["字"]
    if gate == "G4" and "审核通过" not in reply:
        return False
    core = _TRAIL_PUNCT.sub("", reply)
    for m in re.finditer(_QUOTED.format(z=z), reply):
        if not _NEG_BEFORE.search(reply[: m.start()]):
            return True
    if core == z:
        return True
    if core.endswith(z) and not _HOLD_PREFIX.match(reply):
        before = core[:-1]
        return not _NEG_BEFORE.search(before)
    return False


def gate_state(gate: str, subject: str, queue: Path | None = None, text: str | None = None) -> str:
    """返回 STATES 之一。只读定夺队列；文件不存在＝缺行。"""
    text = _read_queue(queue) if text is None else text
    row = find_gate_row(gate, subject, text)
    if row is None:
        return "缺行"
    status = row.get("状态", "").strip()
    reply = row.get("答复", "").strip()
    if "作废" in status or reply.startswith("作废"):
        return "作废"
    if "状态" not in row:  # 「三、已答 / 作废」表没有状态列：有答复即视为已答
        status = "已答" if reply else "待答"
    if status != "已答":
        return "待答"
    return "已放行" if reply_releases(gate, reply) else "已答·未放行"


def gate_open(gate: str, subject: str, queue: Path | None = None, text: str | None = None) -> bool:
    """放行判据（只读定夺队列）。"""
    return gate_state(gate, subject, queue=queue, text=text) == "已放行"


# ── 追加闸门行（去重） ────────────────────────────────────────────────────────


def _next_number(text: str) -> str:
    nums = [int(m.group(1)) for line in text.splitlines() for m in [_NUM.match(_cells(line)[0])] if line.startswith("| Q-") and m]
    return f"Q-{(max(nums) + 1) if nums else 1:02d}"


def build_gate_row(gate: str, subject: str, *, scene: str, artifact: str, task_ids: list[str], number: str) -> str:
    g = GATES[gate]
    z = g["字"]
    if gate == "G4":
        options = f"(a) 答「审核通过·发」⇒ 放行，Cowork 经动作通道 `send-followup` 发出；(b) 答「改：…」⇒ 按批注改稿、自检后再追加一行回闸；(c) 答「作废」⇒ 该信作废"
    else:
        options = f"(a) 答「{z}」⇒ 放行，{g['放行后']}；(b) 答「改：…」⇒ 按批注改后再追加一行回闸，或逐条改后答「{z}」；(c) 答「作废」⇒ 该 subject 的下游条目作废"
    tasks = "、".join(f"`{t}`" for t in task_ids) if task_ids else "—"
    problem = f"{gate_marker(gate, subject)}：{g['到闸']} ｜ 产出 `{artifact}`"
    return (
        f"| {number} | {scene} | 决策（{gate} 闸门·{g['名']}，不可代） | {problem} | {options} "
        f"| 无默认（闸门须本人放行；答复列由 Cowork 转写为字面「{z}」，⛔ 不填字母 a/b） | `scripts/gates.py` ｜ `{artifact}` | {tasks} | 待答 | |"
    )


def gate_request(
    gate: str, subject: str, *, scene: str, artifact: str, task_ids: list[str] | None = None, queue: Path | None = None
) -> bool:
    """到闸 ⇒ 在「一、待答」表末尾追加一行；同 (闸, subject) 已有行且状态为 待答／已放行／作废 ⇒ 不重复，返回 False。
    最后一行是「已答·未放行」（他答了「改：…」，改稿后再回闸）⇒ 允许再追加一行，新行成为判定依据（find_gate_row 取最后一行）。"""
    q = Path(queue) if queue else QUEUE
    text = q.read_text(encoding="utf-8") if q.is_file() else "# 定夺队列（R5）\n\n## 一、待答\n\n| 编号 | 场景 | 阻塞类型 | 问题 | 选项与代价 | 推荐 | 来源 | 阻塞的任务 id | 状态 | 答复 |\n|---|---|---|---|---|---|---|---|---|---|\n"
    if find_gate_row(gate, subject, text) is not None and gate_state(gate, subject, text=text) != "已答·未放行":
        return False
    row = build_gate_row(gate, subject, scene=scene, artifact=artifact, task_ids=task_ids or [], number=_next_number(text))
    lines = text.splitlines()
    # 定位「一、待答」节里最后一条表行
    start = next((i for i, l in enumerate(lines) if l.startswith("## ") and "待答" in l), None)
    if start is None:
        lines += ["", "## 一、待答", "", "| 编号 | 场景 | 阻塞类型 | 问题 | 选项与代价 | 推荐 | 来源 | 阻塞的任务 id | 状态 | 答复 |", "|---|---|---|---|---|---|---|---|---|---|", row]
    else:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        last = max((j for j in range(start, end) if lines[j].lstrip().startswith("|")), default=None)
        if last is None:
            lines[end:end] = ["| 编号 | 场景 | 阻塞类型 | 问题 | 选项与代价 | 推荐 | 来源 | 阻塞的任务 id | 状态 | 答复 |", "|---|---|---|---|---|---|---|---|---|---|", row]
        else:
            lines.insert(last + 1, row)
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text("\n".join(lines) + ("\n" if text.endswith("\n") or not text else ""), encoding="utf-8")
    return True


# ── 台账接线 ─────────────────────────────────────────────────────────────────


def gate_for_task(task: dict) -> tuple[str, str] | None:
    """台账条目 → (闸, subject)；不到闸的阶段返回 None。"""
    stage = task.get("阶段")
    gate = STAGE_GATE.get(stage or "")
    if gate is None:
        return None
    tid = str(task.get("id", ""))
    if gate == "G2":
        subject = tid.split("/U", 1)[0] if "/U" in tid else str(task.get("场景", ""))
    else:
        subject = str(task.get("场景", ""))
    return gate, subject


def artifact_for_task(task: dict) -> str:
    gate_subject = gate_for_task(task)
    if gate_subject is None:
        return ""
    gate, subject = gate_subject
    if gate == "G1":
        src = str(task.get("来源", ""))
        return src.split(":", 1)[0] if src else f"docs/roadmap/intents/{subject}-intent.md"
    if gate == "G2":
        return f"openspec/changes/{subject}/design.md"
    return "docs/deploy-51-server.md"


def held_tasks(tasks: list[dict], text: str) -> list[tuple[str, str, str, str]]:
    """(id, 闸, subject, 状态名) —— 到闸而未放行的条目。只按阶段判，不看台账状态（台账自改不算数）。"""
    out = []
    for t in tasks:
        gs = gate_for_task(t)
        if gs is None:
            continue
        st = gate_state(gs[0], gs[1], text=text)
        if st != "已放行":
            out.append((t["id"], gs[0], gs[1], st))
    return out


def filter_ready(tasks: list[dict], ready: list[str], text: str) -> tuple[list[str], list[tuple[str, str, str, str]]]:
    held = {h[0]: h for h in held_tasks([t for t in tasks if t["id"] in set(ready)], text)}
    return [r for r in ready if r not in held], [held[r] for r in ready if r in held]


def at_gate(tasks: list[dict], text: str) -> list[tuple[str, str, str, str]]:
    """真正停在闸前的条目：阶段到闸 ∧ 依赖全完成 ∧ 非在跑/完成 ∧ 非外部阻塞，(id, 闸, subject, 状态名)，含已放行的。"""
    byid = {t["id"]: t for t in tasks}
    out = []
    for t in tasks:
        gs = gate_for_task(t)
        if gs is None or t.get("状态") in ("完成", "在跑") or t.get("阻塞类型") == "外部":
            continue
        if t.get("状态") == "阻塞" and not str(t.get("产出判据", "")).startswith("【G"):
            continue  # 因别的决策阻塞（如 intent 有未答题）：还没到闸，不入队
        if not all((byid.get(d) or {}).get("状态") == "完成" for d in t.get("依赖", [])):
            continue
        out.append((t["id"], gs[0], gs[1], gate_state(gs[0], gs[1], text=text)))
    return out


def sweep(repo: Path, apply: bool = False) -> list[tuple[str, str, str, str, str]]:
    """台账里依赖已齐、阶段到闸的条目逐条对队列。返回 (id, 闸, subject, 状态名, 动作)；`--apply` 才追加缺行。"""
    import yaml  # 延迟：gates 被 dispatcher_backlog 导入，那边已有 yaml

    ledger = Path(repo) / "docs/roadmap/任务台账.yaml"
    if not ledger.is_file():
        return []
    tasks = [t for t in (yaml.safe_load(ledger.read_text(encoding="utf-8")) or {}).get("tasks", []) if isinstance(t, dict)]
    byid = {t["id"]: t for t in tasks}
    queue = Path(repo) / QUEUE_REL
    text = queue.read_text(encoding="utf-8") if queue.is_file() else ""
    out = []
    for tid, gate, subject, st in at_gate(tasks, text):
        action = "—"
        if st == "缺行" and apply:
            t = byid[tid]
            added = gate_request(gate, subject, scene=str(t.get("场景", "")), artifact=artifact_for_task(t), task_ids=[tid], queue=queue)
            text = queue.read_text(encoding="utf-8")
            st = gate_state(gate, subject, text=text)
            action = "已追加" if added else "已有行"
        out.append((tid, gate, subject, st, action))
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=None, help="仓库根（默认本文件所在仓库）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("state")
    p.add_argument("gate", choices=tuple(GATES))
    p.add_argument("subject")
    p = sub.add_parser("open")
    p.add_argument("gate", choices=tuple(GATES))
    p.add_argument("subject")
    p = sub.add_parser("request")
    p.add_argument("gate", choices=tuple(GATES))
    p.add_argument("subject")
    p.add_argument("--scene", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--tasks", default="", help="阻塞的任务 id，逗号分隔")
    p = sub.add_parser("sweep")
    p.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    repo = (args.repo or ROOT).resolve()
    configure(repo)
    if args.cmd == "state":
        print(gate_state(args.gate, args.subject))
        return 0
    if args.cmd == "open":
        ok = gate_open(args.gate, args.subject)
        print("已放行" if ok else gate_state(args.gate, args.subject))
        return 0 if ok else 1
    if args.cmd == "request":
        tasks = [t for t in args.tasks.split(",") if t]
        added = gate_request(args.gate, args.subject, scene=args.scene, artifact=args.artifact, task_ids=tasks)
        print("已追加" if added else "已有行，不重复")
        return 0
    rows = sweep(repo, apply=args.apply)
    for tid, gate, subject, st, action in rows:
        print(f"{tid}\t{gate}\t{subject}\t{st}\t{action}")
    print(f"# 到闸条目: {len(rows)} ｜ 未放行: {sum(1 for r in rows if r[3] != '已放行')}" + ("" if args.apply else " ｜ 干跑（--apply 才追加缺行）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
