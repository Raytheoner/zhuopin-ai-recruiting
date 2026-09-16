# 拆件章程正本（liaison-unpack-charter，P2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为「回件打标即开班」自动起活的无头拆件会话，建立唯一一份行为章程正本（`.claude/skills/liaison-unpack/SKILL.md`），并提供把「事件驱动前言 + 章程全文」拼成起活 prompt 的纯函数，使章程只有一处正本、逐字进入每次会话、且不可能被服务代码悄悄复制一份。

**Architecture:** 单点常量 `unpack.charter.CHARTER_RELATIVE_PATH` 指向仓库内唯一的章程文件；`read_charter(repo_root)` 按该常量读取全文（缺失抛 `CharterMissing`）；纯函数 `compute_prompt(...)` 只做字符串拼接（前言在前、章程全文逐字在后，不改写章程一字）。章程本身是被动数据——真正的「不可越界」由 P0 的第九态起活逻辑与 P1 的 `dispatch.HEADLESS_ARGV_TEMPLATE` 权限白名单执行，本单元只保证内容正确、唯一、可机器核对。

**Tech Stack:** Python 3.14（仓库根 `venv`）、pytest 8.3.4、Markdown + YAML frontmatter（`.claude/skills/*/SKILL.md` 既有格式）。

**Spec:** `openspec/changes/liaison-reply-bridge-and-patrol/specs/liaison-unpack-charter/spec.md`（本计划的全部 Requirement 均出自此文件）；设计决策见同目录 `design.md` D4（`HEADLESS_ARGV_TEMPLATE`）、D9（第九态文案）、D12（信号 CLI 契约）、D13（章程落位与 `compute_prompt` 签名）、D15（§〇 ⑨ 逐字措辞）。

## Global Constraints

以下逐字取自 `CLAUDE.md`「合规红线」与本变更包 design D15、以及本批四条并行 plan 泳道的收口纪律；**必须逐字进入章程 §〇 与 §三**，不得改写措辞：

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **模型全部走境内**，简历数据不出境。
- **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。
- 候选人入口一律用一次性邀请链接，避免被认定「向境内公众提供」。
- 主观描述（「沟通能力强」）不得进入硬门槛规则，只能作为软技能关键词。
- **第 ⑨ 条（design D15，措辞逐字取 D15）**：归档件疑似含候选人个人信息（简历、身份证号、手机号等）⇒ ⛔ 不读入 prompt、不摘录，只登记并转「待人」栏。判据＝文件名或首段显示为候选人简历／名单／证件。
- **收口三条**（进章程 §三）：只 add 列出路径 ＋ commit **不 push**；`git status --porcelain` 越界自检；`.git/index.lock` **不删**。
- ⛔ 章程内不出现任何凭据、不出现绝对路径（仓库相对路径与 worktree 主工作区路径不同，绝对路径写死即错）。

本计划本身也遵守四条并发协议（并行同伴：`[Mac]0910G` 写 P0 计划、`[Mac]0910H` 写 P1 计划、`[Mac]0910J` 写 P3 计划，均为独立文件，互不touch）：只 `git add` 本计划涉及的路径、不 `git add -A`/`-a`/`stash`、push 被拒才 `pull --rebase --autostash` 重试 ≤3 次、`index.lock` 已存在等 5 秒重试 ≤5 次绝不删除。

---

### Task 1: `unpack` 包骨架 —— 章程正本单点常量与读取函数

**Files:**
- Create: `tools/liaison/unpack/__init__.py`
- Create: `tools/liaison/unpack/charter.py`
- Test: `tools/liaison/tests/test_unpack_charter.py`

**Interfaces:**
- Produces: `charter.CHARTER_RELATIVE_PATH: str`（值 `.claude/skills/liaison-unpack/SKILL.md`）、`charter.CharterMissing(Exception)`、`charter.read_charter(repo_root: Path) -> str`。后续 Task 2/3/4 与 P1 的 `dispatch.py`（design D13：读不到章程转 `failed(charter_missing)`）都靠这三个名字。

- [ ] **Step 1: 写空包与失败测试**

创建 `tools/liaison/unpack/__init__.py`（空文件，仅用于建包）。

```python
# tools/liaison/tests/test_unpack_charter.py
from __future__ import annotations

from pathlib import Path

import pytest


def test_read_charter_missing_raises(tmp_path: Path) -> None:
    from tools.liaison.unpack import charter

    with pytest.raises(charter.CharterMissing):
        charter.read_charter(tmp_path)


def test_read_charter_reads_relative_path(tmp_path: Path) -> None:
    from tools.liaison.unpack import charter

    charter_dir = tmp_path / ".claude" / "skills" / "liaison-unpack"
    charter_dir.mkdir(parents=True)
    (charter_dir / "SKILL.md").write_text("章程内容\n", encoding="utf-8")

    assert charter.read_charter(tmp_path) == "章程内容\n"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py -v`
Expected: `ModuleNotFoundError: No module named 'tools.liaison.unpack.charter'`（或收集错误）—— `charter.py` 尚不存在。

- [ ] **Step 3: 写最小实现**

```python
# tools/liaison/unpack/charter.py
"""拆件章程正本的单点定位与读取（design D13）。

章程正本只有一份，落在 `.claude/skills/liaison-unpack/SKILL.md`——CLAUDE.md
「规则真源在 `.claude/skills/`」，且人也能手动 `/liaison-unpack` 走同一流程。
本模块只做「找到它、读出它」，⛔ 不持有章程正文的任何副本（正文本身在
SKILL.md 里，见 Task 2）。
"""

from __future__ import annotations

from pathlib import Path


class CharterMissing(Exception):
    """章程正本文件缺失或不可读。"""


#: 章程正本相对仓库根的唯一路径。⛔ 这是本仓库里唯一一处定义这条路径的地方——
#: `dispatch.py`（P1）与全部测试都必须 import 这个常量，不得自己拼字符串。
CHARTER_RELATIVE_PATH = ".claude/skills/liaison-unpack/SKILL.md"


def read_charter(repo_root: Path) -> str:
    """读出章程正本全文。`repo_root` 缺该文件 ⇒ `CharterMissing`。"""

    path = Path(repo_root) / CHARTER_RELATIVE_PATH
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CharterMissing(f"章程正本缺失：{path}") from exc
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/__init__.py tools/liaison/unpack/charter.py tools/liaison/tests/test_unpack_charter.py
git commit -m "feat(liaison-unpack-charter): 章程正本单点常量与读取函数"
```

---

### Task 2: 章程正本全文 —— `.claude/skills/liaison-unpack/SKILL.md`

**Files:**
- Create: `.claude/skills/liaison-unpack/SKILL.md`
- Modify: `tools/liaison/tests/test_unpack_charter.py`（追加两个内容核对测试）

**Interfaces:**
- Consumes: `charter.CHARTER_RELATIVE_PATH`、`charter.read_charter`（Task 1）。
- Produces: 章程正本真实内容——Task 4（无副本扫描）、Task 5（allowedTools 交叉核对）都读取本文件的真实文本，不用假数据。

- [ ] **Step 1: 写红线九项与收口三条的核对测试（先写测试，此刻章程文件还不存在，测试应先失败）**

```python
# 追加进 tools/liaison/tests/test_unpack_charter.py
from tools.liaison.unpack import charter  # noqa: E402  (顶部已 import 过则忽略此行)

REPO_ROOT = Path(__file__).resolve().parents[3]

RED_LINE_PHRASES = [
    "send-followup",       # ① 对外发送
    "scripts/",            # ② 建造（连同 tools/、app/、tests/ 一起要求不改）
    "新下裁决",             # ③
    "openspec/",           # ④
    "liaison.db",          # ⑤
    "白名单",               # ⑥
    "git push",            # ⑦
    "CLAUDE.md",           # ⑧
    "候选人个人信息",         # ⑨（D15）
]

CLOSING_PHRASES = ["不 push", "只 `git add`", "index.lock"]


def test_章程红线九项可核对() -> None:
    text = charter.read_charter(REPO_ROOT)
    for phrase in RED_LINE_PHRASES:
        assert phrase in text, f"章程缺红线短语：{phrase}"


def test_章程收口三条可核对() -> None:
    text = charter.read_charter(REPO_ROOT)
    for phrase in CLOSING_PHRASES:
        assert phrase in text, f"章程 §三 缺收口短语：{phrase}"


def test_章程结构小节完整() -> None:
    text = charter.read_charter(REPO_ROOT)
    for heading in ["§〇", "§一", "§二", "§三", "§四"]:
        assert heading in text, f"章程缺小节：{heading}"
    # R4（信号探测与循环）与 R6（回灌与还原）是纯文本要求，用关键短语核对存在性
    for phrase in [
        "一律按「有信号」处理",
        "只清检查点之前的项",
        "按分隔符",
        "docs/跟进信/回件/",
    ]:
        assert phrase in text, f"章程缺规定短语：{phrase}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py -v`
Expected: `test_章程红线九项可核对` 等三条 FAIL，报 `CharterMissing`（`.claude/skills/liaison-unpack/SKILL.md` 尚不存在）。

- [ ] **Step 3: 写章程正本**

```markdown
---
name: liaison-unpack
description: 拆件会话章程正本——回件打标即开班后自动起的无头 Claude 会话必须把本文件全文逐字拼进 prompt 结尾作为行为边界；人也可手动 `/liaison-unpack` 走一遍同一流程。
---

# 拆件会话章程

本章程是拆件会话（回件桥打标后自动起活的无头 Claude 会话）唯一的行为边界正本。
仓库内其它任何代码与文档 MUST NOT 持有本文件 §〇 的整句副本；本章程由
`tools/liaison/unpack/charter.py::CHARTER_RELATIVE_PATH` 单点指向本文件。

## §〇 红线九项（绝对不做）

① 对外发送——含 `send-followup` 任何模式，一律不做。
② 建造——不修改 `tools/`、`app/`、`scripts/`、`tests/` 下的任何文件。
③ 新下裁决——不自行做出需要 Shao Peishen 或代理人拍板的新判断，转「待人」栏（见 §四）。
④ 不修改 `openspec/` 下任何 spec、design、tasks 文件。
⑤ 不写 `data/liaison.db*`、不写任何 `.env`；归档目录（`data/liaison/archive/`）只读；信号只经 CLI（`python -m tools.liaison unpack-signal`）操作，不直接改信号文件。
⑥ 不修改白名单配置（`tools/liaison/config/whitelist.yaml` 等）。
⑦ 不 `git push`；不做任何针对生产服务器（`.51`）的动作。
⑧ 不修改 `CLAUDE.md` 与 `.claude/skills/` 下任何文件（含本文件自身）。
⑨ 归档件疑似含候选人个人信息（简历、身份证号、手机号等）⇒ ⛔ 不读入 prompt、不摘录，只登记并转「待人」栏。判据＝文件名或首段显示为候选人简历／名单／证件。

以上九项，凡遇到需要新下判断的事项，一律转「待人」栏登记（§四），MUST NOT 自行决定。

## §一 信号探测与循环

1. 开工先探测信号：`python -m tools.liaison unpack-signal --probe`。
2. 输出 `[NO-SIGNAL]` ⇒ 本次会话结束，不做任何后续步骤。
3. 输出 `[SIGNAL]` ⇒ 按 §二 处理其中一条 pending 项。
4. 探测命令返回非零、命令缺失、或输出既不是 `[SIGNAL]` 也不是 `[NO-SIGNAL]` ⇒ **一律按「有信号」处理**，不得当成「无信号」结束。
5. 清信号只在 §二 的回灌结论已落档并 `git commit` 完成之后执行：`python -m tools.liaison unpack-signal --clear --before <检查点时刻>`；只清检查点之前的项，检查点之后新落的信号项保留给下一轮。

## §二 拆件步骤

1. 读信号：`unpack-signal --probe` 给出的 pending 项含 `letter_number`、`msgid`、`archived_path`。
2. 读归档件：用 `Read` 工具打开 `archived_path`（仓库相对路径）。⚠️ 遇 §〇 ⑨ 情形立即停止本条、转「待人」，不得继续往下读。
3. 判实质/非实质：内容是否为该封信「决策点」的实际回应。寒暄、误发、与决策点无关 ⇒ 判「非实质回件」。
4. 回灌结论：用 `Write` 工具把结论写入 `docs/跟进信/回件/<信编号>-<日期>.md`（目录不存在则新建）。
5. 台账转态或还原：
   - **实质回件** ⇒ 用 `Edit` 工具把 `docs/跟进信/README-跟进信清单.md` 中该信编号所在行，从第九态标记
     （`📨 回件已到，待拆件 …`）转为闭环四态之一（`📥 已回件并回灌 <日期>` / `✅ 无需回复` /
     `📨 已确认闭环 <日期>` / `❌ 已作废`，按内容判定其一）。
   - **非实质回件** ⇒ 把该行**按分隔符 `━━━ 原状态 ━━━` 之后的原状态原文**整行还原，
     并在 `docs/session接力.md` 登记还原原因。
6. 口径点台账：涉及口径点确认时，用 `python -m tools.liaison criteria --id HR-G-NN --to 已回复|已签认|已作废 [--evidence …]` 转态。
7. 登记接力文档：在 `docs/session接力.md` 追加一行，含【谁做：本次拆件会话（自动）】【状态：已闭环/待人】
   【判据：<本条怎样算完>】【不做会怎样：<下一条回件是否受阻>】四列。
8. 回到 §一 步骤 5 清信号，再回步骤 1 探测下一条 pending 项。

## §三 收口

- 会话在**主工作区**运行，不建 worktree、不建分支。
- 只 `git add` 本轮明确写入的路径（`docs/跟进信/回件/…`、`docs/跟进信/README-跟进信清单.md`、
  `docs/跟进信/口径点台账.md`、`docs/session接力.md`）；⛔ 不 `git add -A`、不 `git add .`、
  不 `git commit -a`、不 `git stash`。
- 提交前用 `git diff --cached` 自查暂存内容只含上述路径；用 `git status --porcelain` 做越界自检——
  发现列表外路径被改动 ⇒ 该路径不 add，在 `docs/session接力.md` 登记「自检发现越界编辑 <路径>，
  未提交、待人处理」。
- `git status` 里出现他人改动是正常的，不停、不问、不顺手提交。
- `git commit` 之后**不 push**（红线 ⑦）。
- 提交完用 `git log --oneline -5` 反查一次，确认刚才的提交真的落在当前分支上。
- 遇 `.git/index.lock` 已存在 ⇒ 等待重试，⛔ 绝不删除该锁。

## §四 待人栏

以下情形一律停止当前条目的自动处理，在 `docs/session接力.md` 追加一行【谁做：Shao Peishen】
【状态：待人】【判据：<本条何时算处理完>】【不做会怎样：<该条 pending 信号项保留，下一轮仍会
再探到，不会丢>】，然后跳过本条继续探测/处理其它信号项（若有）：

- §〇 ⑨ 命中：归档件疑似含候选人个人信息。
- 判「实质/非实质」出现真正的歧义（内容既不像决策点回应也不像纯寒暄）。
- 台账该信编号找不到对应行，或该行已不处于第九态（并发被别的流程改写）。
- 任何本章程未列出、需要新下判断的事项（红线③）。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/liaison-unpack/SKILL.md tools/liaison/tests/test_unpack_charter.py
git commit -m "feat(liaison-unpack-charter): 章程正本全文（红线九项/信号循环/拆件步骤/收口/待人栏）"
```

---

### Task 3: `compute_prompt` 纯函数 —— 前言在前、章程逐字在后

**Files:**
- Modify: `tools/liaison/unpack/charter.py`
- Modify: `tools/liaison/tests/test_unpack_charter.py`

**Interfaces:**
- Consumes: 无（不读文件，纯字符串拼接）。
- Produces: `charter.compute_prompt(*, letter_number: str, msgid: str, signal_relpath: str, checkpoint_iso: str, charter_text: str) -> str`。P1 的 `dispatch.dispatch_headless_unpack` 拿这个函数的返回值当 stdin 传给 `claude -p`。

- [ ] **Step 1: 写失败测试**

```python
# 追加进 tools/liaison/tests/test_unpack_charter.py

def test_事件驱动前言不改写章程原文() -> None:
    charter_text = "§〇 红线\n①对外发送\n"
    prompt = charter.compute_prompt(
        letter_number="人事部#3",
        msgid="abc123",
        signal_relpath="data/liaison/unpack-signal.json",
        checkpoint_iso="2026-09-16T14:00:00+08:00",
        charter_text=charter_text,
    )
    assert prompt.endswith(charter_text)
    preamble = prompt[: -len(charter_text)]
    assert preamble.strip() != ""
    assert "人事部#3" in preamble
    assert "abc123" in preamble
    assert "data/liaison/unpack-signal.json" in preamble
    assert "2026-09-16T14:00:00+08:00" in preamble
    assert "[NO-SIGNAL]" in preamble
    # 「再探一次直到无信号」只在前言，不得混进章程原文
    assert "[NO-SIGNAL]" not in charter_text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py::test_事件驱动前言不改写章程原文 -v`
Expected: `AttributeError: module 'tools.liaison.unpack.charter' has no attribute 'compute_prompt'`

- [ ] **Step 3: 写最小实现**

```python
# 追加进 tools/liaison/unpack/charter.py

def compute_prompt(
    *,
    letter_number: str,
    msgid: str,
    signal_relpath: str,
    checkpoint_iso: str,
    charter_text: str,
) -> str:
    """拼出起活 prompt：事件驱动前言在前，章程全文逐字在后。

    前言携带本次触发的信件编号、消息标识、信号文件路径、检查点时刻，以及
    「走完一轮再探一次信号，仍有则再走一轮，直到无信号」这条循环规则——
    这条规则只属于前言，不得混进 ``charter_text``（spec 要求章程正文不可被
    前言改写，也不可反过来把只在前言里的规则塞进章程）。
    """

    preamble = (
        "# 拆件会话起活\n\n"
        f"- 信件编号：{letter_number}\n"
        f"- 消息标识（msgid）：{msgid}\n"
        f"- 信号文件：{signal_relpath}\n"
        f"- 检查点时刻：{checkpoint_iso}\n\n"
        "走完一轮拆件后，再探测一次信号（`python -m tools.liaison unpack-signal "
        "--probe`）；仍有信号则再走一轮，直到输出 `[NO-SIGNAL]` 为止。这条规则"
        "只在本前言里，不在下面的章程正文里。\n\n"
        "---\n\n"
    )
    return preamble + charter_text
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/unpack/charter.py tools/liaison/tests/test_unpack_charter.py
git commit -m "feat(liaison-unpack-charter): compute_prompt 纯函数——前言在前、章程逐字在后"
```

---

### Task 4: 代码里无章程副本——源码扫描测试

**Files:**
- Modify: `tools/liaison/tests/test_unpack_charter.py`

**Interfaces:**
- Consumes: `tools.liaison.tests._source_scan.is_vendored`（已有工具函数，见 `tools/liaison/tests/_source_scan.py`，用于排除 `.venv`/`site-packages`）、`charter.read_charter`。

- [ ] **Step 1: 写失败测试**

```python
# 追加进 tools/liaison/tests/test_unpack_charter.py
from tools.liaison.tests._source_scan import is_vendored


def test_代码里无章程副本() -> None:
    text = charter.read_charter(REPO_ROOT)
    red_line_sentences = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"))
    ]
    assert len(red_line_sentences) == 9, "章程 §〇 应恰好九条红线整句"

    package_root = Path(charter.__file__).resolve().parents[1]  # tools/liaison
    for py in sorted(package_root.rglob("*.py")):
        if "__pycache__" in py.parts or is_vendored(py):
            continue
        source = py.read_text(encoding="utf-8")
        for sentence in red_line_sentences:
            assert sentence not in source, f"{py} 含章程 §〇 整句副本：{sentence}"
```

- [ ] **Step 2: 跑测试确认失败或通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py::test_代码里无章程副本 -v`
Expected: 此刻应已 `1 passed`（Task 1-3 写的 `charter.py` 不含章程正文任何整句，本步骤是**确认性**而非先红后绿——若红，说明 Task 1/3 的代码注释里意外抄了章程整句，需回改注释用词直到绿）。

- [ ] **Step 3: 若为红，修正 `charter.py` 注释措辞（不改变量名与逻辑），再跑一次确认绿**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter.py -v`
Expected: `7 passed`

- [ ] **Step 4: Commit**

```bash
git add tools/liaison/tests/test_unpack_charter.py
git commit -m "test(liaison-unpack-charter): 章程正文九条红线整句在代码里零副本"
```

---

### Task 5: 章程与 `allowedTools`（D4）交叉核对

**Files:**
- Create: `tools/liaison/tests/test_unpack_charter_allowlist.py`

**Interfaces:**
- Consumes: `tools.liaison.unpack.dispatch.HEADLESS_ARGV_TEMPLATE`（由 **P1**「信号与打标即开班」交付，design D4 单点常量）、`charter.read_charter`。
- ⚠️ **执行顺序依赖**：design.md「Migration Plan」§2 规定 P0→P1→P2 顺序合并；本 Task 假设执行本计划时 `tools/liaison/unpack/dispatch.py` 已由 P1 合入 main。若 `run-build` 执行到本 Task 时 `tools.liaison.unpack.dispatch` 仍不存在 ⇒ 停止本 Task、在收工报告登记「⏸ 留步：P1 尚未合并，`dispatch.HEADLESS_ARGV_TEMPLATE` 不可导入」，Task 1–4 的产出不受影响、照常提交。

- [ ] **Step 1: 写失败测试**

```python
# tools/liaison/tests/test_unpack_charter_allowlist.py
from __future__ import annotations

import re
from pathlib import Path

from tools.liaison.unpack import charter
from tools.liaison.unpack.dispatch import HEADLESS_ARGV_TEMPLATE

REPO_ROOT = Path(__file__).resolve().parents[3]

#: `HEADLESS_ARGV_TEMPLATE` 里 `Bash(<命令前缀>:*)` 形式的白名单项，取冒号前的
#: 命令前缀本体（如 `git add`、`git commit`、`python -m tools.liaison unpack-signal`）。
_BASH_PATTERN = re.compile(r'Bash\(([^:]+):\*\)')


def _bash_command_prefixes() -> list[str]:
    return [_BASH_PATTERN.match(tok).group(1) for tok in HEADLESS_ARGV_TEMPLATE if _BASH_PATTERN.match(tok)]


def test_章程使用的命令都在白名单里放行() -> None:
    text = charter.read_charter(REPO_ROOT)
    prefixes = _bash_command_prefixes()
    required_uses = [
        "python -m tools.liaison unpack-signal",
        "python -m tools.liaison criteria",
        "git add",
        "git commit",
        "git status",
        "git diff",
        "git log",
    ]
    for use in required_uses:
        assert any(use.startswith(p) or p.startswith(use) for p in prefixes), (
            f"章程要求执行的命令 `{use}` 未被 HEADLESS_ARGV_TEMPLATE 放行，会话会卡住而不报错：{use}"
        )
        assert use.split(" ")[0] in text or use in text, f"章程正文里找不到命令 `{use}` 的用途说明"


def test_白名单放行的每条命令章程里都有用途() -> None:
    text = charter.read_charter(REPO_ROOT)
    for prefix in _bash_command_prefixes():
        # 命令前缀里挑最具辨识度的最后一段关键词做子串核对（如 "unpack-signal"、"criteria"、"git add"）
        keyword = prefix.split(" ")[-1] if "tools.liaison" in prefix else prefix
        assert keyword in text, f"白名单放行的命令 `{prefix}` 在章程里找不到用途说明（关键词 `{keyword}` 缺失）"
```

- [ ] **Step 2: 跑测试确认状态**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter_allowlist.py -v`
Expected（P1 已合并时）: 先见证失败（若章程用词与白名单前缀措辞不完全对齐，例如 `git diff --cached` 与白名单 `git diff` 前缀不匹配），随后按 Step 3 调整章程或测试关键词直到绿。
Expected（P1 未合并时）: `ModuleNotFoundError: No module named 'tools.liaison.unpack.dispatch'` ⇒ 按本 Task 顶部的执行顺序依赖处理，登记留步，本 Task 到此为止。

- [ ] **Step 3: 若为语义未对齐（而非模块缺失），调整章程 §三 用词或测试关键词表，使双向核对一致**

只允许调整measurement的关键词提取逻辑或章程原文措辞本身（保持语义不变），⛔ 不允许为了让测试通过而删减红线或收口条目。

Run: `venv/bin/python -m pytest tools/liaison/tests/test_unpack_charter_allowlist.py -v`
Expected: `2 passed`

- [ ] **Step 4: Commit（仅当 P1 已合并、测试真正跑通时）**

```bash
git add tools/liaison/tests/test_unpack_charter_allowlist.py
# 若 Step 3 调整了章程原文，一并加入：
# git add .claude/skills/liaison-unpack/SKILL.md
git commit -m "test(liaison-unpack-charter): 章程用命与 D4 allowedTools 白名单双向核对"
```

---

## Requirement → Task 对应表（spec `liaison-unpack-charter/spec.md` 全 7 条）

| Spec Requirement | 覆盖 Task |
|---|---|
| 章程正本唯一且由单点常量解析 | Task 1（`CHARTER_RELATIVE_PATH` + `read_charter`）＋ Task 4（源码零副本扫描） |
| 章程原文逐字进入 prompt 且前言在前 | Task 3（`compute_prompt`） |
| 章程 §〇 红线八项照单 | Task 2（章程正文 §〇 ①–⑧，含 `test_章程红线九项可核对`） |
| 章程规定信号探测与循环 | Task 2（章程正文 §一，含 `test_章程结构小节完整` 对「有信号」「只清检查点之前」两条短语的核对） |
| 章程规定工作区与 git 收口 | Task 2（章程正文 §三，含 `test_章程收口三条可核对`） |
| 章程规定拆件的回灌与还原 | Task 2（章程正文 §二，含 `test_章程结构小节完整` 对「按分隔符」「docs/跟进信/回件/」两条短语的核对） |
| 章程规定候选人个人信息的处置 | Task 2（章程正文 §〇 ⑨，逐字取 design D15，含 `test_章程红线九项可核对`） |

## Self-Review（写计划人自查，非 subagent 复核）

1. **Spec coverage**：上表 7 条 Requirement 均有 Task 落地，无遗漏。
2. **Placeholder scan**：全文无 TBD/TODO/「适当处理」类占位；Task 5 的「留步」分支是显式的执行前置条件说明，不是占位——两条路径（P1 已合并 / 未合并）都给了确切的下一步动作。
3. **Type consistency**：`compute_prompt` 的关键字参数名（`letter_number`/`msgid`/`signal_relpath`/`checkpoint_iso`/`charter_text`）在 Task 3 定义后，Task 5 的测试文件与本表引用均未改名；`CHARTER_RELATIVE_PATH`/`CharterMissing`/`read_charter` 三个名字自 Task 1 定义后在 Task 2/4/5 保持不变。
4. **幂等/副作用**：本单元全部产出为纯函数（`compute_prompt`）与只读函数（`read_charter`），无 `effect_*` 节点、无 DB 写入，铁律 1（幂等 + 事务）与铁律 2（L3 纯函数）天然满足，不需要额外幂等键。
5. **端到端提取验证**（`scripts/task-brief`）：交付前对本文件跑

```bash
scripts/task-brief docs/superpowers/plans/2026-09-10-liaison-unpack-charter.md 1
```

预期能取出 Task 1 的非空正文（Files/Interfaces/五个 Step 齐全）。`grep -c '^### Task ' docs/superpowers/plans/2026-09-10-liaison-unpack-charter.md` 预期为 `5`，与本计划实际任务数一致。
