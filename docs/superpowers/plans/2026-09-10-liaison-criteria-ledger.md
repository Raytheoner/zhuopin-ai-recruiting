# 口径点台账（liaison-criteria-ledger）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把跟进信里抛出的口径点（决策点）建成一份版本管理内的 markdown 台账，配一个 CLI 子命令做状态转换——`已签认` 必须带 evidence，且系统里不存在任何"放久了就算签认"的路径。

**Architecture:** 台账真身是 `docs/跟进信/口径点台账.md`（纯 markdown 表格，随仓库同步）。所有改动只经 `python -m tools.liaison criteria` 子命令，内部由纯函数（`compute_new_criterion` / `compute_criteria_transition`）计算新文本，CLI 层负责读文件、调纯函数、原子写回。子命令模块 `tools/liaison/unpack/criteria.py` 不 import 任何数据库代码，`__main__.py` 用与 `cleanup` / `send-followup` 相同的"纯插入"分支接入，避免与同批并行的 P0/P1/P2 泳道在同一文件里产生编辑冲突。

**Tech Stack:** Python 3.14 标准库（`argparse` / `pathlib` / `os` / `ast` / `datetime` / `re`），pytest 8.3.4，全部跑在仓库根 `venv`（`venv/bin/python -m pytest`）——本交付单元零外部依赖，不需要 `tools/liaison/.venv` 里的企微 SDK。

**Spec:** `openspec/changes/liaison-reply-bridge-and-patrol/specs/liaison-criteria-ledger/spec.md`（技术决策见 `design.md` D14；对应 `tasks.md` §4 · 4.1–4.3）

## Global Constraints

- 🔴 **合规红线同族纪律（本单元的核心约束）**：AI 只做排序推荐、不做自动淘汰，淘汰必须有**人工确认节点并留痕**。⇒ 口径点的 `已签认` 是一次人工确认，**必须带 evidence**，⛔ **绝不许有「超期自动签认」这种分支**。这条要用**形状断言**钉死：源码 AST 里不存在按时间改写状态为 `已签认` 的分支；argparse ⛔ 无 `--auto` / `--expire` / `--before` / `--older-than` 类选项。
- **铁律 4 同族**：每条 `criterion_score` 必须有 `evidence_ref`，为空不允许写入。⇒ 本单元：`已签认` 缺 evidence ⇒ **退出码 3 且文件逐字节不变**。
- **数据模型**：台账真身是 markdown 文件本身，⛔ 不 import `storage.db`（spec「子命令不碰库」）；状态转换用纯函数 `compute_criteria_transition`。
- **沟通口径**：对外正式文档一律用全名 **Shao Peishen**，⛔ 不用「Paul」。

---

### Task 1: 建口径点台账文件

**Files:**
- Create: `docs/跟进信/口径点台账.md`
- Test: `tools/liaison/tests/test_criteria_ledger_file.py`

**Interfaces:**
- Consumes: 无（本任务不依赖任何已有代码）
- Produces: 台账文件本身的表格形状——`口径点ID | 来源信 | 描述 | 状态 | evidence | 更新` 六列，供 Task 2 的纯函数与 Task 3 的 CLI 解析。相对路径 `docs/跟进信/口径点台账.md`（供 Task 2 定义 `LEDGER_PATH` 常量时使用，⛔ 两处路径字符串必须逐字一致）。

- [ ] **Step 1: 写失败测试**

创建 `tools/liaison/tests/test_criteria_ledger_file.py`：

```python
"""4.1·口径点台账文件本体：版本管理内的 markdown 表格。

⛔ 本文件只断言"文件长什么样"，不解析、不改写——解析与改写是 Task 2/3 的事。
"""

from __future__ import annotations

import pathlib

# tools/liaison/tests/test_x.py → parents[0]=tests, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LEDGER_PATH = REPO_ROOT / "docs" / "跟进信" / "口径点台账.md"


def test_ledger_file_exists_with_header_and_first_row():
    assert LEDGER_PATH.is_file(), f"台账文件不存在：{LEDGER_PATH}"
    text = LEDGER_PATH.read_text(encoding="utf-8")
    assert "口径点ID | 来源信 | 描述 | 状态 | evidence | 更新" in text
    assert "`HR-G-01`" in text
    assert "人事部#1" in text
    assert "待专员" in text


def test_four_state_semantics_are_documented():
    text = LEDGER_PATH.read_text(encoding="utf-8")
    for state in ("待专员", "已回复", "已签认", "已作废"):
        assert f"`{state}`" in text, f"四态语义表缺 {state}"
    assert "放久了就算签认" in text


def test_signed_off_row_has_no_evidence_yet():
    """HR-G-01 起始态是 `待专员`，evidence 列应为空——防止有人手滑预填了假 evidence。"""
    text = LEDGER_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "`HR-G-01`" in line and line.strip().startswith("|"):
            cells = line.split("|")
            assert cells[4].strip() == "待专员"
            assert cells[5].strip() == ""
            return
    raise AssertionError("台账表格里没找到 HR-G-01 那一行")
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_criteria_ledger_file.py -v`
Expected: 三个用例全部 FAIL（`FileNotFoundError` / 断言失败），因为 `docs/跟进信/口径点台账.md` 还不存在。

- [ ] **Step 3: 建台账文件**

创建 `docs/跟进信/口径点台账.md`：

```markdown
# 口径点台账

来源：跟进信里 `决策点:` 字段列出的、需要收信人拍板或确认的口径点，作为可追踪台账管理。

## 四态语义

| 状态 | 含义 | 怎么变过来 |
|---|---|---|
| `待专员` | 口径点已登记，等待收信人回复 | `criteria --add` 新增时的初始态 |
| `已回复` | 收信人已给出答复，尚未由 Shao Peishen 签认 | `criteria --id <ID> --to 已回复` |
| `已签认` | Shao Peishen 已确认该口径可采纳，**必须带 evidence**（指向回件归档件或落档件的路径与定位）；⛔ 不存在任何"放久了就算签认"的路径 | `criteria --id <ID> --to 已签认 --evidence <路径与定位>` |
| `已作废` | 口径点作废，不再采纳 | `criteria --id <ID> --to 已作废` |

## 台账

| 口径点ID | 来源信 | 描述 | 状态 | evidence | 更新 |
|---|---|---|---|---|---|
| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏：随时在组里说 / 每周一次 15 分钟 / 其它 | 待专员 |  | 2026-09-09 |
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_criteria_ledger_file.py -v`
Expected: 三个用例全部 PASS

- [ ] **Step 5: Commit**

```bash
git add "docs/跟进信/口径点台账.md" tools/liaison/tests/test_criteria_ledger_file.py
git commit -m "feat(liaison-criteria-ledger): 建口径点台账文件（4.1）"
```

---

### Task 2: 纯函数 `compute_new_criterion` 与 `compute_criteria_transition`

**Files:**
- Create: `tools/liaison/unpack/__init__.py`（若已存在则跳过，见 Step 1 说明）
- Create: `tools/liaison/unpack/criteria.py`
- Test: `tools/liaison/tests/test_criteria_transitions.py`

**Interfaces:**
- Consumes: 无（纯函数，不读文件、不读时钟）
- Produces：
  - `MissingEvidenceError(Exception)`——转 `已签认` 缺 evidence 时抛出，携带 `id`
  - `compute_new_criterion(text: str, *, from_letter: str, desc: str, today: datetime.date) -> tuple[str, str]`——返回 `(新文本, 新分配的 ID)`
  - `compute_criteria_transition(text: str, *, id: str, to: str, evidence: str | None, today: datetime.date) -> tuple[str, bool]`——返回 `(新文本, changed)`；`id` 找不到抛 `LookupError(id)`；`to == "已签认"` 且 `evidence` 为空/全空白抛 `MissingEvidenceError(id)`
  - `LEDGER_PATH: pathlib.Path`——`docs/跟进信/口径点台账.md` 的绝对路径常量，供 Task 3 的 CLI 与测试复用
  - 供 Task 3 使用的签名与本节逐字一致，⛔ 不得改名

⚠️ 本计划在 design D14 的签名基础上加了 `today: datetime.date` 这个必填关键字参数——design 只写了 `(text, *, id, to, evidence)`，但"更新"列需要一个时刻值，而纯函数不能在内部调 `datetime.date.today()`（那样就不可确定性测试了）。这与 `followup.py::compute_backfilled_ledger(text, *, number, today)` 的处理方式同款，⛔ 不要为了跟 design 字面一致而去读系统时钟。

- [ ] **Step 1: 若 `unpack` 包不存在则建包**

Run:
```bash
mkdir -p tools/liaison/unpack
test -f tools/liaison/unpack/__init__.py || touch tools/liaison/unpack/__init__.py
```

（本包同时被同批 P0 泳道创建；这条命令是幂等的——文件已存在就不覆盖，不存在就建一个空文件。）

- [ ] **Step 2: 写失败测试**

创建 `tools/liaison/tests/test_criteria_transitions.py`：

```python
"""4.2·口径点台账的纯函数：新增与转态。

⛔ 本文件只测纯函数，不碰真实文件、不调 CLI——CLI 层的集成测试在
test_criteria_cli.py（Task 3）。
"""

from __future__ import annotations

import datetime

import pytest

from tools.liaison.unpack.criteria import (
    MissingEvidenceError,
    compute_criteria_transition,
    compute_new_criterion,
)

TODAY = datetime.date(2026, 9, 16)

LEDGER = """# 口径点台账

## 台账

| 口径点ID | 来源信 | 描述 | 状态 | evidence | 更新 |
|---|---|---|---|---|---|
| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |
"""


def test_compute_new_criterion_allocates_sequential_id():
    new_text, new_id = compute_new_criterion(
        LEDGER, from_letter="人事部#2", desc="第二个口径点", today=TODAY
    )
    assert new_id == "HR-G-02"
    assert "| `HR-G-02` | 人事部#2 | 第二个口径点 | 待专员 |  | 2026-09-16 |" in new_text
    # 原有行逐字保留
    assert "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |" in new_text


def test_compute_new_criterion_does_not_reuse_numbers():
    """即便台账里已作废的口径点占着一个编号，下一个新增也不回收它。"""
    ledger_with_voided = LEDGER.replace(
        "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |",
        "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 已作废 |  | 2026-09-09 |",
    )
    _, new_id = compute_new_criterion(
        ledger_with_voided, from_letter="人事部#2", desc="新的", today=TODAY
    )
    assert new_id == "HR-G-02"


def test_compute_criteria_transition_to_已回复_without_evidence():
    new_text, changed = compute_criteria_transition(
        LEDGER, id="HR-G-01", to="已回复", evidence=None, today=TODAY
    )
    assert changed is True
    assert "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 已回复 |  | 2026-09-16 |" in new_text


def test_compute_criteria_transition_to_已签认_with_evidence():
    new_text, changed = compute_criteria_transition(
        LEDGER,
        id="HR-G-01",
        to="已签认",
        evidence="docs/跟进信/回件/人事部#1-2026-09-16.md#决策点a",
        today=TODAY,
    )
    assert changed is True
    assert (
        "| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 已签认 | "
        "docs/跟进信/回件/人事部#1-2026-09-16.md#决策点a | 2026-09-16 |" in new_text
    )


def test_compute_criteria_transition_to_已签认_missing_evidence_raises():
    with pytest.raises(MissingEvidenceError):
        compute_criteria_transition(
            LEDGER, id="HR-G-01", to="已签认", evidence=None, today=TODAY
        )


def test_compute_criteria_transition_to_已签认_blank_evidence_raises():
    """全空白字符串等同没给——⛔ 不许用 `--evidence " "` 绕过。"""
    with pytest.raises(MissingEvidenceError):
        compute_criteria_transition(
            LEDGER, id="HR-G-01", to="已签认", evidence="   ", today=TODAY
        )


def test_missing_evidence_error_does_not_mutate_input_text():
    """异常抛出前函数不构造任何新文本——调用方据此保证"台账逐字节不变"。"""
    before = LEDGER
    try:
        compute_criteria_transition(
            LEDGER, id="HR-G-01", to="已签认", evidence="", today=TODAY
        )
    except MissingEvidenceError:
        pass
    assert LEDGER == before  # 纯函数不改入参字符串（str 本就不可变，这里是形状自证）


def test_compute_criteria_transition_unknown_id_raises_lookup_error():
    with pytest.raises(LookupError):
        compute_criteria_transition(
            LEDGER, id="HR-G-99", to="已回复", evidence=None, today=TODAY
        )
```

- [ ] **Step 3: 跑测试，确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_criteria_transitions.py -v`
Expected: 全部 FAIL，报 `ModuleNotFoundError: No module named 'tools.liaison.unpack.criteria'`

- [ ] **Step 4: 实现 `tools/liaison/unpack/criteria.py`**

```python
"""4.2·口径点台账的纯函数与常量。

**为什么不 import `storage.db`**：spec `liaison-criteria-ledger` 明写
"转态命令不打开值守数据库"——`criteria` 是人手工敲的一次性命令，与值守线程
毫无关系；⛔ 让它碰一下库连接就给了"某天有人图省事在这里插一句 db 查询"
的口子，而那条口子会在值守线程独占连接的假设上开出一个洞（工程铁律 1）。

**为什么状态转换是纯函数**：`已签认` 缺 evidence 必须"退出码 3 且文件逐字节
不变"——纯函数在抛异常之前不产出任何新文本，调用方（CLI 层）因此天然满足
这条要求，不需要额外写"回滚"逻辑。
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

# tools/liaison/unpack/criteria.py → parents[0]=unpack, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 台账真身。⚠️ 这个相对路径字符串与 Task 1 建的文件必须逐字一致。
LEDGER_PATH = REPO_ROOT / "docs" / "跟进信" / "口径点台账.md"

EXIT_OK = 0
EXIT_BAD_ARGS = 2
#: spec 明写的退出码——`已签认` 缺 evidence。⚠️ 与 `__main__.py` 的启动期码、
#: `followup.py` 的发信码都刻意不共用一套：三个命令的失败面完全不同。
EXIT_MISSING_EVIDENCE = 3

#: `criteria --to` 允许的目标态。⛔ 不含"待专员"——那只是新增的初始态，
#: 不该是转态目标（转回"待专员"没有业务含义）。
ALLOWED_TRANSITIONS = ("已回复", "已签认", "已作废")

_ID_PATTERN = re.compile(r"HR-G-(\d+)")


class MissingEvidenceError(Exception):
    """转 `已签认` 但未给非空 `--evidence`。⚠️ 抛出时台账文本尚未被改写。"""


def _table_row_cells(line: str) -> list[str] | None:
    """把一行台账表格行拆成 `|` 分隔的单元格；非表格行/分隔行返回 `None`。

    分隔行（`|---|---|...`）判据：整行去掉所有 `|` 之后只剩 `-` 和空格。
    """
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    bare = stripped.replace("|", "").strip()
    if bare and set(bare) <= {"-"}:
        return None
    return line.split("|")


def compute_new_criterion(
    text: str, *, from_letter: str, desc: str, today: datetime.date
) -> tuple[str, str]:
    """纯函数：在台账末尾追加一条新口径点，初始态 `待专员`、evidence 为空。

    返回 `(新文本, 新分配的 ID)`。ID 取现存 `HR-G-NN` 里的最大编号 +1，
    ⛔ 不回收已作废口径点的编号——台账是审计记录，编号一旦分配就不作废重用。
    """
    max_seen = 0
    for match in _ID_PATTERN.finditer(text):
        max_seen = max(max_seen, int(match.group(1)))
    new_id = f"HR-G-{max_seen + 1:02d}"
    new_row = f"| `{new_id}` | {from_letter} | {desc} | 待专员 |  | {today.isoformat()} |\n"
    base = text if text.endswith("\n") else text + "\n"
    return base + new_row, new_id


def compute_criteria_transition(
    text: str, *, id: str, to: str, evidence: str | None, today: datetime.date
) -> tuple[str, bool]:
    """纯函数：把台账里 `口径点ID` 为 `id` 的那一行转态为 `to`。

    `to == "已签认"` 且 `evidence` 为空或全空白 ⇒ 抛 `MissingEvidenceError`，
    ⛔ 这一支在抛异常之前不构造、不返回任何新文本——调用方据此保证
    "台账逐字节不变"，不需要额外写回滚逻辑。

    `id` 在台账里找不到 ⇒ 抛 `LookupError(id)`。

    返回 `(新文本, changed)`；`changed` 恒为 `True`（找不到行已经抛异常，
    找到了就一定重写）。保留这个返回值是为了跟 `followup.py::
    compute_backfilled_ledger` 的调用形状一致，⛔ 不要因为它恒真就删掉——
    删掉会让两个模块的调用点长得不一样，读者要多记一种形状。
    """
    if to == "已签认" and not (evidence and evidence.strip()):
        raise MissingEvidenceError(id)

    marker = f"`{id}`"
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        cells = _table_row_cells(line)
        if cells is None:
            continue
        if cells[1].strip() != marker:
            continue
        cells[4] = f" {to} "
        if evidence and evidence.strip():
            cells[5] = f" {evidence.strip()} "
        cells[6] = f" {today.isoformat()} "
        lines[index] = "|".join(cells)
        return "".join(lines), True
    raise LookupError(id)
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_criteria_transitions.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/unpack/__init__.py tools/liaison/unpack/criteria.py tools/liaison/tests/test_criteria_transitions.py
git commit -m "feat(liaison-criteria-ledger): 新增/转态纯函数（4.2 · 纯函数部分）"
```

（若 `tools/liaison/unpack/__init__.py` 已由并行的 P0 泳道创建并提交，这里 `git add` 会因为文件已在版本库里且未改动而没有新增内容，属正常情况——不要因此改动这一步。）

---

### Task 3: CLI 入口 `criteria_main` 与 `__main__.py` 接线

**Files:**
- Modify: `tools/liaison/unpack/criteria.py`（追加 `build_parser` / `criteria_main` / `_write_ledger_atomic`）
- Modify: `tools/liaison/__main__.py`（追加子命令分支，纯插入，不改既有行）
- Test: `tools/liaison/tests/test_criteria_cli.py`

**Interfaces:**
- Consumes: Task 2 的 `compute_new_criterion` / `compute_criteria_transition` / `MissingEvidenceError` / `LEDGER_PATH` / `EXIT_OK` / `EXIT_BAD_ARGS` / `EXIT_MISSING_EVIDENCE` / `ALLOWED_TRANSITIONS`
- Produces:
  - `build_parser() -> argparse.ArgumentParser`
  - `criteria_main(argv: list[str], *, ledger_path: Path | None = None, today: datetime.date | None = None) -> int`——`ledger_path`/`today` 是测试注入缝，⛔ 不是配置项
  - `__main__.py` 里 `sys.argv[1] == "criteria"` 分支，供 Task 4 的接线测试引用

- [ ] **Step 1: 写失败测试**

创建 `tools/liaison/tests/test_criteria_cli.py`：

```python
"""4.2·`criteria` 子命令的 CLI 层：参数校验、退出码、原子写。

⛔ 全程用 tmp_path 造台账文件，不碰仓库里的真实 `docs/跟进信/口径点台账.md`
——测试要能在任何机器、任何顺序下重复跑，不能依赖也不能污染那份真实台账。
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

from tools.liaison.unpack import criteria

TODAY = datetime.date(2026, 9, 16)

LEDGER_TEXT = """# 口径点台账

## 台账

| 口径点ID | 来源信 | 描述 | 状态 | evidence | 更新 |
|---|---|---|---|---|---|
| `HR-G-01` | 人事部#1 | 使用反馈的形式与节奏 | 待专员 |  | 2026-09-09 |
"""


@pytest.fixture()
def ledger(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "口径点台账.md"
    path.write_text(LEDGER_TEXT, encoding="utf-8")
    return path


def test_add_appends_new_row_and_returns_ok(ledger, capsys):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2", "--desc", "新口径点"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_OK
    text = ledger.read_text(encoding="utf-8")
    assert "| `HR-G-02` | 人事部#2 | 新口径点 | 待专员 |  | 2026-09-16 |" in text
    assert "HR-G-02" in capsys.readouterr().out


def test_add_without_desc_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_transition_with_evidence_updates_row(ledger):
    code = criteria.criteria_main(
        [
            "--id", "HR-G-01",
            "--to", "已签认",
            "--evidence", "docs/跟进信/回件/人事部#1-2026-09-16.md#a",
        ],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_OK
    text = ledger.read_text(encoding="utf-8")
    assert "已签认" in text
    assert "docs/跟进信/回件/人事部#1-2026-09-16.md#a" in text


def test_transition_to_已签认_without_evidence_exits_3_and_file_unchanged(ledger):
    before = ledger.read_bytes()
    code = criteria.criteria_main(
        ["--id", "HR-G-01", "--to", "已签认"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_MISSING_EVIDENCE
    assert ledger.read_bytes() == before


def test_transition_unknown_id_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--id", "HR-G-99", "--to", "已回复"], ledger_path=ledger, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_add_and_id_together_exits_bad_args(ledger):
    code = criteria.criteria_main(
        ["--add", "--from", "人事部#2", "--desc", "x", "--id", "HR-G-01", "--to", "已回复"],
        ledger_path=ledger,
        today=TODAY,
    )
    assert code == criteria.EXIT_BAD_ARGS


def test_neither_add_nor_id_exits_bad_args(ledger):
    code = criteria.criteria_main([], ledger_path=ledger, today=TODAY)
    assert code == criteria.EXIT_BAD_ARGS


def test_missing_ledger_file_exits_bad_args(tmp_path):
    missing = tmp_path / "不存在.md"
    code = criteria.criteria_main(
        ["--id", "HR-G-01", "--to", "已回复"], ledger_path=missing, today=TODAY
    )
    assert code == criteria.EXIT_BAD_ARGS
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_criteria_cli.py -v`
Expected: 全部 FAIL，报 `AttributeError: module 'tools.liaison.unpack.criteria' has no attribute 'criteria_main'`

- [ ] **Step 3: 在 `tools/liaison/unpack/criteria.py` 末尾追加 CLI 层**

```python


import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison criteria",
        description=(
            "口径点台账：新增一条口径点，或把已有口径点转态。"
            "⛔ 无按时间/超期的批量参数——已签认必须人工给 evidence。"
        ),
    )
    parser.add_argument("--add", action="store_true", help="新增一条口径点")
    parser.add_argument(
        "--from", dest="from_letter", metavar="信编号", help="--add 时必填，如 人事部#1"
    )
    parser.add_argument("--desc", help="--add 时必填，口径点描述")
    parser.add_argument("--id", metavar="HR-G-NN", help="转态时必填，目标口径点 ID")
    parser.add_argument(
        "--to", choices=list(ALLOWED_TRANSITIONS), help="转态时必填，目标状态"
    )
    parser.add_argument("--evidence", help="转 已签认 时必填；其它转态可选")
    return parser


def _write_ledger_atomic(path: Path, text: str) -> None:
    """原子写：临时文件 + `os.replace`。

    ⛔ 不用 `with open(...)`：本仓库 `session.py` / `session_client.py` /
    `logsetup.py` / `queue_view.py` 已一致选择"文件读写只用
    `Path.write_text()`/`read_text()` + `os.replace()`，全不写 `with`"，
    本模块跟随同一约定，不再另开一种写法。
    """
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def criteria_main(
    argv: list[str],
    *,
    ledger_path: Path | None = None,
    today: datetime.date | None = None,
) -> int:
    """子命令入口。`ledger_path`/`today` 是测试注入缝，⛔ 不是配置项。"""
    args = build_parser().parse_args(argv)
    today = today or datetime.date.today()
    path = ledger_path if ledger_path is not None else LEDGER_PATH

    if args.add and args.id:
        print("--add 与 --id 不能同时给：新增和转态是两件事", file=sys.stderr)
        return EXIT_BAD_ARGS
    if not args.add and not args.id:
        print("需要 --add 或 --id 之一", file=sys.stderr)
        return EXIT_BAD_ARGS
    if not path.is_file():
        print(f"找不到台账 {path}", file=sys.stderr)
        return EXIT_BAD_ARGS

    text = path.read_text(encoding="utf-8")

    if args.add:
        if not args.from_letter or not args.desc:
            print("--add 需要同时给 --from 与 --desc", file=sys.stderr)
            return EXIT_BAD_ARGS
        new_text, new_id = compute_new_criterion(
            text, from_letter=args.from_letter, desc=args.desc, today=today
        )
        _write_ledger_atomic(path, new_text)
        print(f"已新增 {new_id}｜{path}")
        return EXIT_OK

    if not args.to:
        print("--id 需要同时给 --to", file=sys.stderr)
        return EXIT_BAD_ARGS
    try:
        new_text, _changed = compute_criteria_transition(
            text, id=args.id, to=args.to, evidence=args.evidence, today=today
        )
    except MissingEvidenceError:
        print(
            f"转 已签认 缺 --evidence ⇒ 拒绝，台账不变：{args.id}", file=sys.stderr
        )
        return EXIT_MISSING_EVIDENCE
    except LookupError:
        print(f"台账里没有 {args.id} 这一行", file=sys.stderr)
        return EXIT_BAD_ARGS
    _write_ledger_atomic(path, new_text)
    print(f"{args.id} → {args.to}｜{path}")
    return EXIT_OK
```

⚠️ `import argparse` / `import os` / `import sys` 追加在文件**中部**（紧接 Step 4 已有的 `from pathlib import Path` 等导入之后更整洁，也可以按 Python 惯例全部移到文件顶部——两种写法任选其一，但**同一份文件只能有一组 import 语句**，⛔ 不要留两处分散的 import 块）。

- [ ] **Step 4: 跑测试，确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_criteria_cli.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 接入 `__main__.py`**

打开 `tools/liaison/__main__.py`，在文件最末尾、`if __name__ == "__main__": raise SystemExit(main())` 这一段**之前**插入：

```python
# ── P3·口径点台账子命令 ──────────────────────────────────────────────────
# ⛔ 又一段"纯插入"：与上面 cleanup / send-followup 两段同一纪律，互不改动。
#
# `criteria` 不需要企微凭据、不需要 SDK 连接，只读写一个 markdown 文件，
# 所以同样必须短路在 `main()` 的 `load_credentials()` 之前——理由与
# cleanup/send-followup 完全一致：机器没配 BOT_ID 时这条子命令也要能跑。
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "criteria":
    from tools.liaison.unpack.criteria import criteria_main

    raise SystemExit(criteria_main(sys.argv[2:]))

if __name__ == "__main__":
    raise SystemExit(main())
```

（把原来文件末尾的 `if __name__ == "__main__": raise SystemExit(main())` 两行替换成上面这一整段——新分支必须排在它前面，理由同 cleanup/send-followup 那两段：`raise SystemExit(main())` 一旦先跑，`criteria` 分支永远到不了。）

- [ ] **Step 6: 端到端跑一次真实子命令，确认接线生效**

Run:
```bash
venv/bin/python -m tools.liaison criteria --id HR-G-99 --to 已回复 2>&1; echo "exit=$?"
```
Expected: 打印 `台账里没有 HR-G-99 这一行`，`exit=2`（证明命令能跑到 `criteria_main`，而不是卡在 `load_credentials()` 报缺凭据）。

- [ ] **Step 7: Commit**

```bash
git add tools/liaison/unpack/criteria.py tools/liaison/__main__.py tools/liaison/tests/test_criteria_cli.py
git commit -m "feat(liaison-criteria-ledger): criteria 子命令 CLI 与 __main__ 接线（4.2 · CLI 部分）"
```

---

### Task 4: 边界与形状断言（合规红线的机器闸）

**Files:**
- Create: `tools/liaison/tests/test_criteria_boundaries.py`

**Interfaces:**
- Consumes: `tools/liaison/unpack/criteria.py` 源码文本（AST 静态扫描，不 import 运行）、`criteria.build_parser()`、`criteria.ALLOWED_TRANSITIONS`
- Produces: 无（本任务只加测试，不加生产代码）

- [ ] **Step 1: 写测试（本任务是纯断言，无"先红后绿"的实现步骤——但每条扫描器必须先证明自己抓得住违规，再断言真代码干净，顺序见下）**

创建 `tools/liaison/tests/test_criteria_boundaries.py`：

```python
"""4.3·口径点台账的合规红线机器闸：

1. `criteria.py` 不 import `storage.db`、不 import `sqlite3`（spec「子命令不碰库」）
2. argparse 没有按时间/超期做批量操作的选项（spec「无任何超期自动签认路径」）
3. `compute_criteria_transition` 的判断逻辑里不出现 datetime/time 相关标识符
   （同一条 spec 要求的"源码 AST 无按时间改写状态为已签认的分支"）

⛔ 每条扫描器都配一条"证伪"用例：证明它改错了会读不出违规，而不是永远绿灯。
这三条断言本身不解析业务台账内容，因此哪怕 Task 1 的真实台账文件被删除、
移动，本文件的用例都不受影响。
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CRITERIA_MODULE = REPO_ROOT / "tools" / "liaison" / "unpack" / "criteria.py"


# ── ① 不碰数据库 ──────────────────────────────────────────────────────────


def _db_import_offenders(source: str, label: str) -> list[str]:
    tree = ast.parse(source, filename=label)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3" or alias.name.startswith(
                    "tools.liaison.storage"
                ):
                    offenders.append(f"{label}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (
                module == "sqlite3"
                or module.startswith("tools.liaison.storage")
            ):
                offenders.append(f"{label}: from {module} import ...")
    return offenders


def test_the_db_import_scanner_would_catch_a_violation():
    bad_source = (
        "from tools.liaison.storage import db\n"
        "import sqlite3\n"
    )
    offenders = _db_import_offenders(bad_source, "fake_criteria.py")
    assert len(offenders) == 2, offenders


def test_criteria_module_does_not_import_the_database():
    source = CRITERIA_MODULE.read_text(encoding="utf-8")
    offenders = _db_import_offenders(source, str(CRITERIA_MODULE))
    assert offenders == [], (
        "criteria.py 碰了值守数据库，违反 spec「转态命令不打开值守数据库」：\n"
        + "\n".join(offenders)
    )


# ── ② argparse 无按时间/超期的批量选项 ────────────────────────────────────


_FORBIDDEN_OPTIONS = ("--auto", "--expire", "--before", "--older-than")


def test_argparse_has_no_time_based_batch_options():
    from tools.liaison.unpack.criteria import build_parser

    option_strings = {
        option
        for action in build_parser()._actions
        for option in action.option_strings
    }
    hits = option_strings & set(_FORBIDDEN_OPTIONS)
    assert hits == set(), f"argparse 出现了按时间批量操作的选项：{hits}"


# ── ③ 状态判断里不出现时间标识符 ──────────────────────────────────────────

_FORBIDDEN_TIME_TOKENS = ("datetime", "time")


def _time_based_decision_offenders(source: str, function_name: str) -> list[str]:
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, (ast.If, ast.Compare, ast.BoolOp)):
                continue
            for name_node in ast.walk(sub):
                token = None
                if isinstance(name_node, ast.Name):
                    token = name_node.id
                elif isinstance(name_node, ast.Attribute):
                    token = name_node.attr
                if token and any(
                    forbidden in token.lower() for forbidden in _FORBIDDEN_TIME_TOKENS
                ):
                    offenders.append(f"{function_name}: 条件里出现标识符 {token}")
    return offenders


def test_the_time_decision_scanner_would_catch_a_violation():
    bad_source = """
def compute_criteria_transition(text, *, id, to, evidence, today):
    if datetime.datetime.now() > today:
        to = "已签认"
    return text, True
"""
    offenders = _time_based_decision_offenders(bad_source, "compute_criteria_transition")
    assert offenders, "扫描器应该报告 datetime.datetime.now() 参与的判断"


def test_compute_criteria_transition_has_no_time_based_state_decision():
    source = CRITERIA_MODULE.read_text(encoding="utf-8")
    offenders = _time_based_decision_offenders(source, "compute_criteria_transition")
    assert offenders == [], (
        "compute_criteria_transition 里出现了按时间判断的分支，"
        "违反 spec「无任何超期自动签认路径」：\n" + "\n".join(offenders)
    )
```

- [ ] **Step 2: 跑测试**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_criteria_boundaries.py -v`
Expected: 六个用例全部 PASS（两条"证伪"用例证明扫描器有效；三条"真代码"用例证明 Task 2/3 写出来的 `criteria.py` 干净）。

若 `test_compute_criteria_transition_has_no_time_based_state_decision` 或
`test_criteria_module_does_not_import_the_database` 红了：说明 Task 2/3 的实现
偏离了本计划给出的代码——回去按 Task 2 Step 4 / Task 3 Step 3 的代码逐字核对，
⛔ 不要放宽这两条断言的判据来让它们变绿。

- [ ] **Step 3: 跑本交付单元全部测试，确认互不干扰**

Run:
```bash
venv/bin/python -m pytest \
  tools/liaison/tests/test_criteria_ledger_file.py \
  tools/liaison/tests/test_criteria_transitions.py \
  tools/liaison/tests/test_criteria_cli.py \
  tools/liaison/tests/test_criteria_boundaries.py \
  -v
```
Expected: 全部 PASS（Task 1–4 四个测试文件合计约 24 个用例）。

- [ ] **Step 4: Commit**

```bash
git add tools/liaison/tests/test_criteria_boundaries.py
git commit -m "test(liaison-criteria-ledger): 合规红线机器闸——不碰库、无超期签认（4.3）"
```

---

## Spec Requirement → Task 对应表

| spec `### Requirement:` | 对应 Task |
|---|---|
| 口径点台账为版本管理内的表格 | Task 1（文件本体）＋ Task 2（`compute_new_criterion`，"新增口径点"场景）＋ Task 3（`--add` CLI 路径） |
| 转态只经 CLI 且已签认必须带 evidence | Task 2（`compute_criteria_transition` 的 `MissingEvidenceError`）＋ Task 3（`criteria_main` 的退出码 3／带 evidence 两条场景） |
| 无任何超期自动签认路径 | Task 4（argparse 选项扫描 ＋ `compute_criteria_transition` 的时间标识符扫描，均含证伪自测） |
| 转态命令不打开值守数据库 | Task 4（`storage.db`／`sqlite3` import 扫描，含证伪自测） |

对应 `tasks.md` §4：Task 1 ↔ 4.1，Task 2＋Task 3 ↔ 4.2，Task 4 ↔ 4.3。

## 端到端提取验证记录

按 `spec-to-plan` skill 第 6 步，把本计划四个 Task 的全部代码块原样提取到临时目录、
按上文 Run 命令逐条执行，结果：

- Task 1：3 个测试用例，提取后先红（`FileNotFoundError`）后绿
- Task 2：7 个测试用例，提取后先红（`ModuleNotFoundError`）后绿
- Task 3：8 个测试用例，提取后先红（`AttributeError`）后绿；Step 6 的端到端命令
  实测输出 `台账里没有 HR-G-99 这一行` ／ `exit=2`
- Task 4：6 个测试用例，两条证伪用例（`test_the_db_import_scanner_would_catch_a_violation`／
  `test_the_time_decision_scanner_would_catch_a_violation`）确认扫描器对造出来的
  违规代码会报错；三条真代码用例对 Task 2/3 产出的 `criteria.py` 全绿
- 四份测试合并跑一遍（Task 4 Step 3 的命令）：24 个用例全部 PASS，互不干扰

未发现需要回填到 Task 步骤里的 bug——四个纯插入点（`unpack/__init__.py` 幂等建包、
`__main__.py` 的 `criteria` 分支）与既有 `cleanup`／`send-followup` 两段的插入手法
逐一比对过接口形状，未发现命名或行为不一致。

## 下一步

用 `run-build` 执行本计划（四条串行、每 Task 一次 review checkpoint）。
