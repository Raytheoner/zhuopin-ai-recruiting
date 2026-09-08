# 消息归档（hr-wecom-aibot-liaison 交付单元 4）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"材料收到了"这句话有一个可核对的落点——内部同事发来的每一条消息与其附件按 **`msgid` 为最细一级键**落进 `data/liaison/archive/`，**先落材料、后写台账**，附件全程按字节流处理、完整性只用字节长度 + SHA-256，重复投递不产生第二份归档；名单外的消息同样归档并收到一条礼貌回复，但 **⛔ 不产生任何值守队列条目**。

**Architecture:**

```
tools/liaison/
├── storage/
│   ├── schema.py            ← 第 2 章产物，⛔ 本章一个字符都不改（opener 约束 5）
│   ├── db.py                ← 第 2 章产物，⛔ 本章不改
│   └── effects.py           ← 第 2 章产物，⛔ 本章不改（见下方「三条不改」第 1 条）
├── whitelist.py             ← 第 3 章产物，本章只 import `admit()`，⛔ 不改
├── archive.py               ← 【新建】纯函数 + 落盘编排
│   ├── compute_safe_filename(raw, *, max_bytes) -> str        纯函数（4.2）
│   ├── compute_archive_path(...) -> pathlib.Path              纯函数（4.1）
│   ├── InboundAttachment(filename: str, payload: bytes)        入参 DTO
│   ├── ArchiveOutcome(newly_archived: bool, attachments: ...)  出参 DTO
│   └── archive_message(conn, ...) -> ArchiveOutcome            D3 顺序的编排点（4.5）
├── attachments.py           ← 【新建】字节流落盘与完整性校验（4.3 / 4.4）
│   ├── compute_digest(payload: bytes) -> tuple[int, str]
│   ├── store_attachment(payload, destination) -> StoredAttachment
│   └── verify_archived_file(path, *, byte_length, sha256) -> bool
├── consistency.py           ← 【新建】台账 ↔ 材料一致性核对器
│   └── verify_ledger_against_archive(conn, *, archive_root) -> list[str]
├── inbound.py               ← 【新建】名单内／外分支接线（4.10）
│   ├── compute_inbound_route(admitted: bool) -> InboundRoute   纯函数
│   └── handle_inbound_message(conn, ..., reply=None) -> InboundResult
└── tests/
    ├── test_archive_paths.py        ← Task 1 + Task 2
    ├── test_attachments.py          ← Task 3
    ├── test_archive_effect.py       ← Task 4
    ├── test_archive_consistency.py  ← Task 5
    └── test_inbound_routing.py      ← Task 6
```

支撑这套结构的六条判断，动手前逐条读完：

**1. 落盘不是 `effect_*`，它是 `effect_*` 的前置。**
文件系统不参与 SQL 事务，所以"附件落盘"不可能和台账行同事务提交。design D3 因此把顺序钉死：**先落材料，再在一个事务里写台账行 + `effect_log` 行**。本计划把这条顺序做成一个编排函数 `archive_message()`：它先调 `store_attachment()`（纯 I/O，无 DB），再调第 2 章已有的 `effect_archive_message()`（幂等 + 事务）。
⛔ **不要把落盘塞进 `effect_archive_message` 函数体里**——那会让一次文件 I/O 失败把幂等装饰器的事务处理路径也拖进来，而且 `rollback()` 回滚不了已经落盘的文件。Task 4 有一条 AST 断言把"`store_attachment` 的调用必须出现在 `effect_archive_message` 的调用之前"钉成机器判据。

**2. 本章 ⛔ 不新增任何 `@idempotent_effect` 函数，因此 `effects.py` / `schema.py` 一个字符都不用改。**
这不是"能不改就不改"的偏好，是一条硬约束：`tools/liaison/tests/test_liaison_effects.py::test_effect_node_to_table_matches_the_insert_target_repo_wide` 会**仓库级扫描** `tools/liaison/` 下所有被 `@idempotent_effect` 装饰的函数，要求每一个都在 `EFFECT_NODE_TO_TABLE` 里登记、且登记的表名等于函数体里真实 `INSERT INTO` 的表。新增一个"发礼貌回复"的 effect（它不 INSERT 任何表）会让这条测试**当场变红**，而正确的修法是给它配一张 outbox 表——那是加表，触发 opener 约束 5 的"登记偏离并停在该点"。
**本章的结论**：礼貌回复走**注入的 reply port**（一个可调用对象），不做成 `effect_*`；其 at-most-once 缺口登记成技术债，由第 6／7 章连同真实外发通道一起补成带 outbox 行的幂等 effect。见 Task 6 的「⚠️ 已知缺口」。

**3. `msgid` 与 `thread_id` 只校验、⛔ 不改写。**
它们要进路径，所以必须做路径安全。但**归一化会制造碰撞**：两个不同的 `msgid` 若被改写成同一个字符串，两条消息就会落到同一个路径上——这正是「归档覆盖」那个生产 bug 的另一种形态，只是成因从"键太粗"换成了"键被磨平"。本实现改成**校验式**：含路径分隔符／控制字符、或是 `.`／`..`／空白 的 `thread_id`／`msgid` 一律 `raise ArchivePathError`，⛔ 不做任何字符替换。
**原始文件名反过来**——它是人给的、必然含各种字符，且没有唯一性职责，所以它走归一化（`compute_safe_filename`）。两者的处置不同不是不一致，是职责不同：**键要唯一，文件名要安全**。

**4. `compute_safe_filename` 里的 `decode` 与 4.4 的"⛔ 禁止 UTF-8 解码"不冲突，但必须能被机器区分开。**
4.4 禁的是**对附件内容**做文本解码后据此判定损坏（生产 bug「二进制判误」的成因）。文件名本来就是 `str`，超长截断必须按**字节**算（文件系统的 255 限制是字节不是字符），而按字节切完要再拼回 `str`，这一步绕不开 `decode`。
两者靠**模块边界**分开，并由 Task 3 的扫描器把边界钉成断言：**`attachments.py`（碰附件字节的唯一模块）里 ⛔ 不许出现任何 `.decode(`、⛔ 不许出现任何非二进制模式的 `open()`**。`archive.py` 里的 `decode` 只作用于文件名，不碰 `payload`。
⛔ 不要为了"统一"把截断逻辑挪进 `attachments.py`——那等于把这道边界拆了。

**5. 日期只做分目录，且 ⛔ 不读本机时钟。**
`compute_archive_path` 是纯函数，`yyyymmdd` 从入参 `received_at` 里解析，**不做任何时区换算**（协议给的偏移就是发送人看到的那个时刻，换算只会让"我 8 号发的怎么在 7 号目录里"变成一个要解释的问题）。
⛔ 不许在纯函数里调 `datetime.now()` / `date.today()`——那会让同一条消息在不同时刻重跑落到不同路径上，重投时**两份都在**，而幂等装饰器还认为只处理了一次。

**6. 本章 ⛔ 不实现入队，但必须断言"队列条目数不变"。**
`tasks.md` 4.10 原文："此处只接线并加断言'队列条目数不变'"。`handle_inbound_message` 返回的 `InboundRoute.should_enqueue` 就是第 5 章的接线位——第 5 章把 `effect_enqueue_task` 挂在这个布尔值后面即可，⛔ 本章不写这一行。Task 6 有一条 AST 断言：`inbound.py` 源码里**不得出现** `effect_enqueue_task` 这个名字。

**Tech Stack:** Python 3.14（根 `pyproject.toml` 钉死 `>=3.14,<3.15`；本机实测 3.14.6）· 标准库（`hashlib` / `os` / `json` / `tempfile` / `pathlib` / `datetime` / `unicodedata` / `dataclasses` / `logging` / `ast` / `sqlite3`）· pytest 8.3.4 · **⛔ 本章不新增任何第三方依赖**（`tools/liaison/requirements.txt` 一个字符不改）

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」、本交付单元 opener 的六条范围约束、以及 `design.md` 的 D3／D4／D5／D10。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md「工程铁律」（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
   *（本章不调用任何模型、不产生任何评分，本条不产生实现动作；⛔ 但也不得因此引入任何"顺手给消息打个标签"的模型调用。）*
4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。*（同上，本章不适用。）*
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。*（同上，本章不适用。）*
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
   *（本服务走的是 aibot WebSocket 长连接协议，不存在 HTTP 回调端点——`06-企业AI转型资产借鉴清单.md` §三 已澄清该协议路径绕开了这一步。本条在本章的等价物是「先落材料再处理」的 D3 顺序。）*
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。*（design D6：本服务不引入 LangGraph，⛔ 本章也不得引入。）*

### 来自 CLAUDE.md「合规红线」（逐字，与本章相关的四条）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
  *（本章的"名单外只归档不入队"是**通道准入**，与候选人淘汰无关。⛔ 本章任何拒绝／不入队逻辑不得被复用到候选人处置路径上。）*
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
  *（本章的礼貌回复是**固定模板**，不是 AI 生成合成内容，不落入该办法的适用范围；⛔ 但仍必须带"自动发送"标识——收件人不能误以为那是 Shao Peishen 本人的回复。见 Task 6 的 `POLITE_NOTICE`。）*
- **模型全部走境内**，简历数据不出境。*（本章不外发任何数据到第三方，归档全部落本机磁盘。）*
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。
  *（本服务的对象全部是内部同事，⛔ MUST NOT 用于向候选人发送任何内容——design D7 已钉死。）*

### 来自 CLAUDE.md「部署约束」（逐字，与本章相关的两条）

4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
   *（本服务**永不部署 .51**，代码落 `tools/`，结构上不在 `sync-to-server.sh` 的白名单里。）*
5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。
   *（本章归档的是**内部同事**发来的工作材料，不是候选人简历。⛔ 本服务不得被用来接收或转存候选人简历——那会绕过 M2 门槛。）*

### 来自本交付单元 opener（逐字，六条）

1. **铁律 1 逐字 + D3 顺序**：材料先落盘（临时文件 → `fsync` → 原子 `rename`），台账行与 `effect_log` 行同一事务提交；幂等键 `{thread_id}:effect_archive_message:{msgid}`；目标路径含 `msgid`、已存在即视为已完成。
2. **4.2/4.4 逐字**：文件名只做路径安全，⛔ 不因非 ASCII 改写或拒收；校验只用字节长度 + SHA-256，⛔ 任何 UTF-8/文本解码。
3. **4.1**：最细一级键是 `msgid`，⛔ 按天/按人一个文件的粗粒度。
4. **4.10 名单外分支**：归档 + 礼貌回复，⛔ 不生成队列条目——本章只接线并断言"队列条目数不变"，⛔ 不实现入队（第 5 章）。
5. **D10 落点 `tools/liaison/`**；⛔ 不碰 `app/`、`scripts/`；⛔ 不改第 2 章的表结构（需要加列/加表则登记偏离并停在该点）。
6. **并行同伴**：连接泳道在改 `tools/liaison/session*`、`alerts*`、`__main__.py`——本章 ⛔ 不碰这三处。

### 来自 design.md（逐字，四条）

- **D3 的顺序**：1. 附件写临时文件 → `fsync` → **原子 `rename`** 到最终路径（路径含 `msgid`，天然幂等：目标已存在即视为已完成）；2. 然后在**一个事务**里写消息行 + `effect_log` 行并提交。
  ⛔ 顺序不许反。"文件已落、DB 行未写"是可收敛的中间态（重跑时 rename 幂等、DB 行补上）；"DB 行已写、文件缺失"是不可恢复的谎——台账说材料收到了，材料却不在。
- **D4**：**归档路径** `data/liaison/archive/<thread_id>/<yyyymmdd>/<msgid>__<原文件名>`。键的最细一级是 `msgid`。⛔ 禁止任何"按天一个文件"或"按人一个文件"的粗粒度落点。**附件一律按字节流读写**（`rb`/`wb`）。完整性校验只用**字节长度 + SHA-256**，⛔ **禁止任何 UTF-8 解码校验**。原始后缀原样保留，不按内容猜类型。**文件名归一化只处理路径安全**，⛔ 不因为"看起来像乱码"就改写或拒收。
- **D5 的单向依赖**：`tools/liaison` 可以 `import app.storage.idempotency`，⛔ `app/` 下任何模块不得 import `tools/`。
- **D10**：代码落 **`tools/liaison/`**。⛔ 不落 `app/`，⛔ 也不落 `scripts/`——两者都在 `sync-to-server.sh` 的 `SYNC_PATHS` 白名单里。依赖落 `tools/liaison/requirements.txt`，⛔ 不进根 `requirements.txt`。⛔ 不改 `sync-to-server.sh` / `deploy-server.ps1` 去"排除 tools"。

### 本章的五条"不做"

- ⛔ **不新增任何 `@idempotent_effect` 函数**，⛔ 不改 `storage/effects.py`、`storage/schema.py`、`storage/db.py` 的任何一个字符。理由见 Architecture 第 2 条（会直接打爆第 2 章的仓库级扫描断言）。
- ⛔ **不实现入队**（`effect_enqueue_task` 的调用属第 5 章）、⛔ 不实现队列渲染、⛔ 不实现状态流转。
- ⛔ **不实现留存期清理**（第 8 章）。本章的一致性核对器**是**清理的前置，但 ⛔ 不许顺手写一个 `delete_expired()`。
- ⛔ **不实现真实外发通道**（第 6／7 章）。礼貌回复只经由注入的 reply port 发出，本章不建连、不调 SDK。
- ⛔ **不碰 `tools/liaison/__main__.py`、`session*`、`alerts*`**（连接泳道正在并行改这三处）。

---

## 前置状态（⚠️ controller 开工前必读）

第 1／2／3 章已合入 `main`，本章直接在其上叠加：

| 已存在的东西 | 路径 | 本章怎么用 |
|---|---|---|
| `liaison_message` 表（主键 `msgid`，含 `attachments_json TEXT NOT NULL DEFAULT '[]'`） | `tools/liaison/storage/schema.py` | **只写不改**。`attachments_json` 第 2 章只建列不写入，本章开始写入 |
| `effect_archive_message(conn, *, thread_id, business_key, sender_userid, received_at, msgtype, content, attachments_json)` | `tools/liaison/storage/effects.py` | **只调不改**。首次应用返回 `business_key`，幂等命中返回 `None` |
| `get_connection(db_path)` / `init_schema(conn)` | `tools/liaison/storage/db.py` | 测试 fixture 直接用 |
| `admit(sender_userid, path=None) -> bool` | `tools/liaison/whitelist.py` | Task 6 的准入判定入口 |
| `assert_effect_log_identity(conn)` | `tools/liaison/tests/test_liaison_effects.py` | **本章每个写库场景的末尾都要调一次**（该函数 docstring 原文要求"第 4／5／7 章的测试应当直接 import 本函数"） |
| `EFFECT_NODE_TO_TABLE` | `tools/liaison/storage/effects.py` | ⛔ 不改。本章不新增 effect，所以它保持两条 |

**跑测试的命令**（根 venv，不需要 `tools/liaison/.venv`——本章零第三方依赖）：

```bash
cd /Users/paulshao/Projects/HumanResource
venv/bin/python -m pytest tools/liaison/tests/ -v
```

---

## Spec Requirement → Task 对照

spec 文件：`openspec/changes/hr-wecom-aibot-liaison/specs/liaison-message-archive/spec.md`（主）
＋ `specs/liaison-inbound-whitelist/spec.md`「名单外消息只归档并礼貌回复」一条。

| spec Requirement | Scenario | Task | tasks.md |
|---|---|---|---|
| 归档键细到单条消息 | 同人同天多条消息 / 同人同天同名文件内容不同 | Task 2、Task 4 | 4.1、4.6 |
| 附件按字节流处理，完整性校验不依赖文本可解码性 | 二进制附件不被误判损坏 / 完整性校验方式 | Task 3 | 4.3、4.4、4.7 |
| 文件名归一化只处理路径安全 | 中文文件名 / 含路径分隔符 / 超长 | Task 1、Task 2 | 4.2、4.9 |
| 重复投递不产生第二份归档 | 同一消息被投递两次 / 重连后重投历史消息 | Task 4、Task 6 | 4.8 |
| 台账中存在的消息其材料必然可取回 | 处理中途被强制终止 / 一致性可核对 | Task 4、Task 5 | 4.5、4.8 |
| 归档数据的留存期有上限 | — | **⛔ 不在本章**（第 8 章） | — |
| （whitelist spec）名单外消息只归档并礼貌回复 | 名单外成员发消息 / 名单外的群消息 | Task 6 | 4.10 |
| （whitelist spec）名单内成员的消息进入归档与队列 | — | **归档部分**在 Task 6；**入队部分 ⛔ 不在本章**（第 5 章） | 4.10 |

---

### Task 1: `compute_safe_filename`——文件名只做路径安全

**Files:**
- Create: `tools/liaison/archive.py`
- Test: `tools/liaison/tests/test_archive_paths.py`

**Interfaces:**
- Consumes: 无（本 Task 是本章的第一块，只用标准库）
- Produces:
  - `tools.liaison.archive.compute_safe_filename(raw_name: str, *, max_bytes: int = DEFAULT_MAX_FILENAME_BYTES) -> str`
  - `tools.liaison.archive.DEFAULT_MAX_FILENAME_BYTES: int = 200`
  - `tools.liaison.archive.FALLBACK_FILENAME: str = "unnamed"`
  - `tools.liaison.archive.ArchivePathError(ValueError)`
  Task 2 用 `compute_safe_filename` 拼路径；Task 4／6 通过 Task 2 间接用到。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_archive_paths.py`：

```python
"""归档路径的两个纯函数：文件名路径安全（4.2/4.9）与路径拼装（4.1）。

⛔ 这两个函数里不许出现任何 I/O、时钟读取、环境变量读取——它们是
`compute_*`（工程铁律 2 的形状），本文件末尾有 AST 断言把这条钉死。
"""

from __future__ import annotations

import ast
import inspect

import pytest

from tools.liaison.archive import (
    DEFAULT_MAX_FILENAME_BYTES,
    FALLBACK_FILENAME,
    compute_safe_filename,
)


# ─────────────────────────────────────────────────────────────────────────
# 4.2 / spec「文件名归一化只处理路径安全」
# ─────────────────────────────────────────────────────────────────────────


def test_chinese_filename_is_preserved_verbatim():
    """spec Scenario「中文文件名」：原样保留，⛔ 不转拼音、⛔ 不替换、⛔ 不拒收。

    这是生产 bug「二进制判误」的同源思路在文件名侧的翻版——"看着像乱码就处理掉"
    对中文文件名是纯粹的破坏。本条一旦变红，⛔ 不许改断言去迁就实现。
    """
    assert compute_safe_filename("岗位要求确认反馈表v3.xlsx") == "岗位要求确认反馈表v3.xlsx"


def test_non_ascii_of_other_scripts_is_preserved_too():
    """不是"给中文开个特例"，是根本不看字符属于哪个书写系统。"""
    for name in ("履歴書.pdf", "Lebenslauf–2026.docx", "résumé.txt", "emoji✅表.xlsx"):
        assert compute_safe_filename(name) == name


def test_path_separators_are_removed():
    """spec Scenario「文件名含路径分隔符」：分隔符被移除，文件落在预期目录内。"""
    assert compute_safe_filename("a/b/c.xlsx") == "abc.xlsx"
    assert compute_safe_filename("a\\b\\c.xlsx") == "abc.xlsx"


def test_traversal_attempt_cannot_escape_the_directory():
    """`../../etc/passwd` 被压成**一个**普通名字，不含任何分隔符。

    判据是"结果里没有分隔符"而不是"结果等于某个具体串"——后者会把断言
    绑死在归一化的实现细节上。
    """
    result = compute_safe_filename("../../etc/passwd")
    assert "/" not in result and "\\" not in result
    assert result not in (".", "..", "")


def test_control_characters_are_removed():
    """控制字符（含 NUL）会截断 C 层的路径字符串，必须清掉。"""
    assert compute_safe_filename("re\x00port\x1f.pdf") == "report.pdf"
    assert compute_safe_filename("line\nbreak.txt") == "linebreak.txt"


def test_leading_and_trailing_whitespace_is_stripped():
    assert compute_safe_filename("  报告.docx  ") == "报告.docx"
    assert compute_safe_filename("\t报告.docx\n") == "报告.docx"


def test_names_that_reduce_to_nothing_fall_back_to_a_placeholder():
    """全被清空、或只剩点号的名字，折成占位名。

    ⛔ 不允许返回空串或 "." / ".."——它们拼进路径会变成"目录本身"，
    最终 `os.replace` 会去覆盖一个目录，报的是 IsADirectoryError 这类
    看不出根因的错。
    """
    for hostile in ("", "   ", "///", "\x00", ".", "..", "  ..  "):
        assert compute_safe_filename(hostile) == FALLBACK_FILENAME


def test_non_string_input_falls_back_instead_of_raising():
    """协议给的字段可能是 None。判定路径不抛异常，折成占位名。"""
    assert compute_safe_filename(None) == FALLBACK_FILENAME
    assert compute_safe_filename(12345) == FALLBACK_FILENAME


# ─────────────────────────────────────────────────────────────────────────
# 4.9 超长截断
# ─────────────────────────────────────────────────────────────────────────


def test_overlong_name_is_truncated_and_keeps_the_extension():
    """spec Scenario「文件名超长」：截断且扩展名保留。"""
    name = "报" * 500 + ".xlsx"
    result = compute_safe_filename(name)
    assert result.endswith(".xlsx")
    assert len(result.encode("utf-8")) <= DEFAULT_MAX_FILENAME_BYTES


def test_truncation_counts_bytes_not_characters():
    """文件系统的 255 上限是**字节**。一个中文字 3 字节——按字符数算会溢出。

    ⛔ 不许把这条改成 `len(result) <= max_bytes`：那在纯 ASCII 下恰好也绿，
    是最典型的"测试写得比实现还宽"。
    """
    result = compute_safe_filename("字" * 200 + ".pdf", max_bytes=64)
    assert len(result.encode("utf-8")) <= 64
    assert result.endswith(".pdf")


def test_truncation_never_produces_broken_utf8():
    """按字节切会切在多字节字符中间，必须丢掉那个残缺字符而不是留下坏字节。"""
    result = compute_safe_filename("字" * 100 + ".pdf", max_bytes=10)
    # 能无损往返编解码 ⇒ 没有残缺序列
    assert result.encode("utf-8").decode("utf-8") == result


def test_extension_longer_than_the_budget_still_yields_a_bounded_name():
    """扩展名本身就超预算的病态输入：保不住扩展名，但**绝不能溢出**。"""
    result = compute_safe_filename("a." + "x" * 300, max_bytes=32)
    assert len(result.encode("utf-8")) <= 32
    assert result != ""


def test_dotfile_is_treated_as_a_whole_name_not_as_an_extension():
    """`.gitignore` 的"扩展名"是整个名字。⛔ 不许把它切成空 stem + 长后缀。"""
    result = compute_safe_filename(".gitignore", max_bytes=200)
    assert result == ".gitignore"


def test_name_within_budget_is_returned_unchanged():
    """没超预算就一个字符都不动——截断只在真的超了才发生。"""
    assert compute_safe_filename("正常文件.xlsx", max_bytes=200) == "正常文件.xlsx"


# ─────────────────────────────────────────────────────────────────────────
# 铁律 2：`compute_*` 是纯函数
# ─────────────────────────────────────────────────────────────────────────

_FORBIDDEN_IN_PURE_FUNCTIONS = {
    "open",
    "now",
    "today",
    "getenv",
    "urlopen",
    "print",
}


def test_compute_safe_filename_is_pure():
    """AST 判据：函数体里不许出现 I/O、时钟、环境变量、网络调用。

    ⛔ 变红时不许"加个 noqa"——纯函数是铁律 2 的形状，不是风格偏好。
    """
    tree = ast.parse(inspect.getsource(compute_safe_filename))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    leaked = called & _FORBIDDEN_IN_PURE_FUNCTIONS
    assert not leaked, f"compute_safe_filename 不再是纯函数，出现了 {sorted(leaked)}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_paths.py -v`
Expected: 全部 collection error —— `ModuleNotFoundError: No module named 'tools.liaison.archive'`

- [ ] **Step 3: 写最小实现**

创建 `tools/liaison/archive.py`：

```python
"""归档路径的计算与消息归档的编排。

**本模块分两层，⛔ 不许混在一起：**

- `compute_safe_filename` / `compute_archive_path` 是 `compute_*` 纯函数
  （工程铁律 2 的形状）：不读文件、不读时钟、不读环境变量、不记日志。
  测试用 AST 把这条钉成断言。
- `archive_message` 是编排点：它按 design D3 钉死的顺序，**先**调
  `attachments.store_attachment` 落材料，**后**调第 2 章的
  `effect_archive_message` 写台账。⛔ 顺序不许反。

⛔ **本模块里的 `decode` 只作用于文件名，绝不作用于附件内容。**
4.4 禁止的是"对附件内容做文本解码后据此判定损坏"（生产 bug「二进制判误」
的成因）；文件名本来就是 `str`，而按**字节**截断（文件系统的 255 是字节不是
字符）必须切完再拼回 `str`，这一步绕不开 decode。碰附件字节的模块是
`attachments.py`，那里 ⛔ 一个 `decode` 都不许出现，由扫描器守着。
"""

from __future__ import annotations

import unicodedata
from typing import Any, Final

#: 文件名的默认字节预算。留出余量给 `<msgid>__` 前缀——最终路径组件的上限
#: 是 255 字节（APFS/ext4 的单个 name component 限制），200 给文件名、
#: 剩下的给前缀，`compute_archive_path` 会按实际 msgid 长度再算一次精确预算。
DEFAULT_MAX_FILENAME_BYTES: Final[int] = 200

#: 名字被清空后的占位名。⛔ 不许返回 ""/"."/".."——它们拼进路径会指向目录本身，
#: 最终 `os.replace` 会去覆盖一个目录，报出与根因无关的 IsADirectoryError。
FALLBACK_FILENAME: Final[str] = "unnamed"

#: 路径分隔符。只收 `/` 与 `\`。
#: ⛔ 刻意**不**把 `:` 算进来：POSIX 上它不是分隔符，删掉它是对文件名的
#: 改写而不是路径安全，违反 4.2「⛔ 不因……改写」。
_PATH_SEPARATORS: Final[frozenset[str]] = frozenset({"/", "\\"})


class ArchivePathError(ValueError):
    """归档路径无法安全地算出来。

    只在 `thread_id`／`msgid` 这类**键**不可用时抛（见 `compute_archive_path`）。
    文件名不抛——它走归一化，任何输入都能折成一个安全的名字。
    """


def compute_safe_filename(
    raw_name: Any, *, max_bytes: int = DEFAULT_MAX_FILENAME_BYTES
) -> str:
    """把原始文件名压成一个安全的**单个**路径组件。

    只做四件事（4.2 逐字）：移除路径分隔符与控制字符、去除首尾空白、
    超长时按字节截断且保留扩展名。

    ⛔ **不因为非 ASCII 或"看着像乱码"改写或拒收。** 中文名原样保留。
    ⛔ 不做大小写归一、不做拼音转写、不按内容猜扩展名。
    """
    if not isinstance(raw_name, str):
        # 协议字段可能是 None 或数字。判定路径不抛异常给调用方——
        # 一个坏文件名不该让整条消息归档不了。
        return FALLBACK_FILENAME

    cleaned = "".join(
        char
        for char in raw_name
        if char not in _PATH_SEPARATORS and unicodedata.category(char) != "Cc"
    ).strip()

    # 只剩点号（"" / "." / ".." / "..."）的名字指向目录本身，折成占位名。
    if not cleaned.strip("."):
        return FALLBACK_FILENAME

    return _truncate_preserving_extension(cleaned, max_bytes)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """按字节截断，丢掉被切碎的那个字符。

    `errors="ignore"` 在这里是**正确**的用法而不是掩盖问题：被丢掉的只可能是
    我们自己刚切碎的那一个多字节字符的残段。⛔ 不要把这个模式搬去处理附件字节。
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _truncate_preserving_extension(name: str, max_bytes: int) -> str:
    if len(name.encode("utf-8")) <= max_bytes:
        return name

    stem, dot, extension = name.rpartition(".")
    # `stem` 为空 ⇒ 形如 ".gitignore"，整个名字就是名字，没有扩展名可保。
    if not dot or not stem:
        return _truncate_utf8(name, max_bytes) or FALLBACK_FILENAME

    suffix = "." + extension
    suffix_bytes = len(suffix.encode("utf-8"))
    if suffix_bytes >= max_bytes:
        # 病态输入：扩展名本身就吃掉了全部预算。保不住扩展名，
        # 但**绝不能溢出**——溢出会在 open() 时报 ENAMETOOLONG，
        # 而那时候材料已经收到了却落不了盘。
        return _truncate_utf8(name, max_bytes) or FALLBACK_FILENAME

    truncated_stem = _truncate_utf8(stem, max_bytes - suffix_bytes)
    if not truncated_stem:
        return _truncate_utf8(name, max_bytes) or FALLBACK_FILENAME
    return truncated_stem + suffix
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_paths.py -v`
Expected: PASS（15 条）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/archive.py tools/liaison/tests/test_archive_paths.py
git commit -m "feat(liaison): compute_safe_filename——文件名只做路径安全，中文原样保留（4.2/4.9）"
```

---

### Task 2: `compute_archive_path`——`msgid` 是最细一级键

**Files:**
- Modify: `tools/liaison/archive.py`（在 Task 1 的内容之后追加）
- Test: `tools/liaison/tests/test_archive_paths.py`（追加）

**Interfaces:**
- Consumes: `compute_safe_filename`、`ArchivePathError`、`FALLBACK_FILENAME`（Task 1）
- Produces:
  - `tools.liaison.archive.compute_archive_path(*, thread_id: str, msgid: str, received_at: str, filename: Any, archive_root: pathlib.Path) -> pathlib.Path`
  - `tools.liaison.archive.DEFAULT_ARCHIVE_ROOT: pathlib.Path`（= `<仓库根>/data/liaison/archive`）
  - `tools.liaison.archive.MAX_PATH_COMPONENT_BYTES: int = 255`
  Task 3 拿它的返回值当落盘目的地；Task 4／5／6 通过 `archive_message` 间接用到。

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_archive_paths.py` 末尾：

```python
# ─────────────────────────────────────────────────────────────────────────
# 4.1 / spec「归档键细到单条消息」
# ─────────────────────────────────────────────────────────────────────────

import pathlib  # noqa: E402  （追加段落，保持与上文同一文件）

from tools.liaison.archive import (  # noqa: E402
    DEFAULT_ARCHIVE_ROOT,
    ArchivePathError,
    compute_archive_path,
)

ROOT = pathlib.Path("/tmp/archive-root-for-tests")


def _path(**overrides):
    kwargs = {
        "thread_id": "tanglp",
        "msgid": "msg-0001",
        "received_at": "2026-09-09T10:30:00+08:00",
        "filename": "反馈表.xlsx",
        "archive_root": ROOT,
    }
    kwargs.update(overrides)
    return compute_archive_path(**kwargs)


def test_path_layout_matches_design_d4_verbatim():
    """D4 逐字：`<archive_root>/<thread_id>/<yyyymmdd>/<msgid>__<原文件名>`。"""
    assert _path() == ROOT / "tanglp" / "20260909" / "msg-0001__反馈表.xlsx"


def test_same_sender_same_day_three_messages_get_three_distinct_paths():
    """spec Scenario「同人同天多条消息」——三个**同名**附件，三条不同路径。

    这条直接对应参考服务的生产 bug「归档覆盖」：它把日期当成了键的最细一级，
    同人同天的后一条覆盖前一条。本实现里日期只是分目录。
    """
    paths = {
        _path(msgid=f"msg-{n}", filename="反馈表.xlsx") for n in ("a", "b", "c")
    }
    assert len(paths) == 3


def test_same_name_different_content_are_distinguished_by_msgid():
    """spec Scenario「同人同天同名文件内容不同」：靠 msgid 区分，两份都能取回。"""
    assert _path(msgid="m1") != _path(msgid="m2")


def test_date_only_partitions_directories_never_the_leaf():
    """判据写成"叶子名里不含日期段"——防止有人把 yyyymmdd 塞进文件名当键。"""
    path = _path()
    assert path.parent.name == "20260909"
    assert "20260909" not in path.name
    assert path.name.startswith("msg-0001__")


def test_date_comes_from_received_at_never_from_the_clock():
    """纯函数：同一条消息在任何时刻算出的路径必须一模一样。

    ⛔ 实现里出现 `datetime.now()` 会让重投时算出**第二个**路径——
    两份都在，而幂等装饰器还认为只处理了一次。
    """
    assert _path(received_at="2020-01-02T03:04:05+08:00").parent.name == "20200102"


def test_received_at_offset_is_not_converted():
    """协议给的偏移就是发送人看到的那个时刻，⛔ 不做时区换算。"""
    assert _path(received_at="2026-09-09T00:30:00+08:00").parent.name == "20260909"
    assert _path(received_at="2026-09-09T23:30:00+08:00").parent.name == "20260909"


def test_naive_received_at_is_accepted():
    """没有偏移的时间戳同样能用——日期取字面值。"""
    assert _path(received_at="2026-09-09 10:30:00").parent.name == "20260909"


def test_unparseable_received_at_raises():
    """⛔ 不许兜底成"今天"——那会把一个坏时间戳变成一条落错目录的记录。"""
    with pytest.raises(ArchivePathError):
        _path(received_at="昨天下午")


@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "a/b", "a\\b", "x\x00y", None, 7])
def test_keys_are_validated_never_rewritten(bad):
    """`thread_id`／`msgid` 只校验、⛔ 不改写。

    改写会制造碰撞：两个不同的 msgid 被磨成同一个字符串 ⇒ 两条消息落同一路径
    ⇒ 「归档覆盖」换个成因又回来了。所以这里的正确行为是**抛**，不是清洗。
    """
    with pytest.raises(ArchivePathError):
        _path(thread_id=bad)
    with pytest.raises(ArchivePathError):
        _path(msgid=bad)


def test_group_chat_thread_id_is_accepted():
    """群聊的 thread_id 取 chatid（design D3），形态与 userid 不同但同样合法。"""
    assert _path(thread_id="wrkSHat_chatid_001").parent.parent.name == "wrkSHat_chatid_001"


def test_leaf_component_never_exceeds_the_filesystem_limit():
    """`<msgid>__<文件名>` 合起来才是那个 255 字节的路径组件。

    ⛔ 只给文件名设预算不够——msgid 也占字节。这条用一个长 msgid + 长中文名
    把两者一起顶到上限。
    """
    path = _path(msgid="m" * 120, filename="档" * 300 + ".xlsx")
    assert len(path.name.encode("utf-8")) <= 255
    assert path.name.endswith(".xlsx")


def test_absurdly_long_msgid_raises_instead_of_silently_overflowing():
    """msgid 长到连一个字符的文件名都放不下 ⇒ 抛，⛔ 不静默截断 msgid（那是改写键）。"""
    with pytest.raises(ArchivePathError):
        _path(msgid="m" * 260)


def test_hostile_filename_cannot_escape_the_archive_root():
    """最终判据不是"字符串里没有 ..%"，而是**解析后的路径确实在根目录下面**。"""
    path = _path(filename="../../../../etc/passwd")
    assert ROOT in path.parents


def test_missing_filename_falls_back_but_still_archives():
    """无附件名（None）不该让整条消息归档不了——折成占位名。"""
    assert _path(filename=None).name == "msg-0001__unnamed"


def test_default_archive_root_is_under_data_liaison():
    """D4 的落点。⛔ 不许落到 data/ 之外——`data/` 已被 .gitignore 覆盖。"""
    assert DEFAULT_ARCHIVE_ROOT.parts[-3:] == ("data", "liaison", "archive")


def test_compute_archive_path_is_pure():
    tree = ast.parse(inspect.getsource(compute_archive_path))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    leaked = called & _FORBIDDEN_IN_PURE_FUNCTIONS
    assert not leaked, f"compute_archive_path 不再是纯函数，出现了 {sorted(leaked)}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_paths.py -v`
Expected: `ImportError: cannot import name 'compute_archive_path'`

- [ ] **Step 3: 写最小实现**

在 `tools/liaison/archive.py` 的 import 段补上 `import datetime`、`import pathlib`，并在 `_truncate_preserving_extension` 之后追加：

```python
#: 单个路径组件的字节上限（APFS / ext4 的 name component 限制）。
#: `<msgid>__<文件名>` 合起来受这一条约束，⛔ 不是只约束文件名。
MAX_PATH_COMPONENT_BYTES: Final[int] = 255

#: `<msgid>` 与文件名之间的分隔符（design D4 逐字）。
_MSGID_SEPARATOR: Final[str] = "__"

# tools/liaison/archive.py → parents[0]=liaison, [1]=tools, [2]=仓库根
_REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]

#: 归档根目录（design D4）。`data/` 已被 .gitignore:11 覆盖，归档不会误入版本管理。
DEFAULT_ARCHIVE_ROOT: Final[pathlib.Path] = _REPO_ROOT / "data" / "liaison" / "archive"


def _validated_key(value: Any, field_name: str) -> str:
    """校验一个要进路径的**键**（`thread_id` / `msgid`）。

    ⛔ **只校验、不改写。** 归一化会把两个不同的键磨成同一个字符串，
    两条消息就落到同一个路径上——「归档覆盖」那个生产 bug 换了个成因又回来了。
    键来自企微协议、本该是安全的标识符；不安全就说明上游给的东西有问题，
    这时候正确的方向是**响亮地失败**，不是安静地清洗。
    """
    if not isinstance(value, str):
        raise ArchivePathError(f"{field_name} 必须是字符串，实际是 {type(value).__name__}")
    if value != value.strip() or not value:
        raise ArchivePathError(f"{field_name} 为空或含首尾空白，⛔ 不接受也不清洗")
    if value in (".", ".."):
        raise ArchivePathError(f"{field_name} 是 '.' 或 '..'，会指向目录本身")
    for char in value:
        if char in _PATH_SEPARATORS or unicodedata.category(char) == "Cc":
            raise ArchivePathError(f"{field_name} 含路径分隔符或控制字符，⛔ 不清洗，直接拒绝")
    return value


def _yyyymmdd(received_at: Any) -> str:
    """从 `received_at` 取出 `yyyymmdd`，**只用于分目录**。

    ⛔ 不做时区换算——协议给的偏移就是发送人看到的那个时刻。
    ⛔ 不兜底成"今天"——那会把一个坏时间戳变成一条落错目录的记录，
    而且不报错、事后无从发现。
    """
    if not isinstance(received_at, str):
        raise ArchivePathError(
            f"received_at 必须是 ISO-8601 字符串，实际是 {type(received_at).__name__}"
        )
    try:
        moment = datetime.datetime.fromisoformat(received_at)
    except ValueError as exc:
        raise ArchivePathError(f"received_at 不是可解析的 ISO-8601 时间戳：{received_at!r}") from exc
    return f"{moment.year:04d}{moment.month:02d}{moment.day:02d}"


def compute_archive_path(
    *,
    thread_id: Any,
    msgid: Any,
    received_at: Any,
    filename: Any,
    archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT,
) -> pathlib.Path:
    """算出一份附件的归档落点（design D4 逐字）：

        <archive_root>/<thread_id>/<yyyymmdd>/<msgid>__<归一化文件名>

    **键的最细一级是 `msgid`。** ⛔ 禁止任何"按天一个文件"或"按人一个文件"
    的粗粒度落点——那正是参考服务生产 bug「归档覆盖」的成因。日期在这条路径里
    只是分目录。

    纯函数：不读文件、不读时钟、不读环境变量。同一条消息在任何时刻算出的路径
    必须逐字相同——否则重投时会落出第二份，而幂等装饰器仍认为只处理了一次。
    """
    safe_thread_id = _validated_key(thread_id, "thread_id")
    safe_msgid = _validated_key(msgid, "msgid")
    day = _yyyymmdd(received_at)

    prefix = safe_msgid + _MSGID_SEPARATOR
    budget = MAX_PATH_COMPONENT_BYTES - len(prefix.encode("utf-8"))
    if budget < len(FALLBACK_FILENAME.encode("utf-8")):
        # msgid 长到连占位名都放不下。⛔ 不许截断 msgid——那是改写键。
        raise ArchivePathError(
            f"msgid 过长（{len(prefix.encode('utf-8'))} 字节），"
            f"路径组件放不下文件名；⛔ 不截断 msgid"
        )

    safe_name = compute_safe_filename(filename, max_bytes=budget)
    return archive_root / safe_thread_id / day / (prefix + safe_name)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_paths.py -v`
Expected: PASS（39 条——Task 1 的 15 条 + 本 Task 的 16 条，其中 `test_keys_are_validated_never_rewritten` 展开成 9 个参数）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/archive.py tools/liaison/tests/test_archive_paths.py
git commit -m "feat(liaison): compute_archive_path——msgid 是最细一级键，日期只分目录（4.1）"
```

---

### Task 3: 附件字节流落盘与完整性校验

**Files:**
- Create: `tools/liaison/attachments.py`
- Test: `tools/liaison/tests/test_attachments.py`
- Modify: `tools/liaison/tests/test_liaison_effects.py`（还 TD-18，见下方 Step 1／2）
- Modify: `docs/tech-debt.md`（TD-18 标 ✅ 已还）

**Interfaces:**
- Consumes: `ArchivePathError` 不涉及；本 Task 只需要一个 `pathlib.Path` 目的地（由 Task 2 的 `compute_archive_path` 提供）
- Produces:
  - `tools.liaison.attachments.StoredAttachment`（frozen dataclass：`filename: str`、`relative_path: str`、`byte_length: int`、`sha256: str`）
  - `tools.liaison.attachments.compute_digest(payload: bytes) -> tuple[int, str]`
  - `tools.liaison.attachments.store_attachment(payload: bytes, destination: pathlib.Path, *, archive_root: pathlib.Path) -> StoredAttachment`
  - `tools.liaison.attachments.verify_archived_file(path: pathlib.Path, *, byte_length: int, sha256: str) -> bool`
  - `tools.liaison.attachments.AttachmentIntegrityError(RuntimeError)`
  Task 4 调 `store_attachment`；Task 5 调 `verify_archived_file`。

**🔴 本 Task 必须先还 TD-18，否则它的实现代码一落地就把第 2 章的守卫打红。**
`attachments.py` 必然要写 `with open(path, "rb")` 与 `with os.fdopen(fd, "wb")`。第 2 章的
`_scan_transaction_violations` 把**任何** `with <Call>:` 判为"第二个事务管理者"——这正是
`docs/tech-debt.md` 的 **TD-18** 登记在案的误报，其「触发条件」原文就是"第 3–5 章第一次因此
变红时"。**计划编写期的提取验证已实测触发**，报的是：

```
attachments.py::verify_archived_file 用 `with open(path, 'rb'):` 隐式提交
attachments.py::store_attachment 用 `with os.fdopen(handle_fd, 'wb'):` 隐式提交
```

TD-18 同时钉死了正确的还法与一条 ⛔：**给扫描器加白名单或细化判据**，⛔ **不许退回"只认裸局部名"
的窄化**——那会重新打开 `with self._conn:` 的口子，而那个口子**没有症状**（`.51` 2026-08-10／08-12
丢 `outbox` 的失败模式）。所以本计划用**正面白名单**：只放行逐条列出的已知非数据库上下文管理器，
其余 `with <Call>:` 一律照旧判违规。
⛔ **不许换成"绕开 `with`、改用 try/finally 手工关文件"**——那是让生产代码去迁就一个已知误报，
而且每一个将来碰文件的模块都得再绕一次。

- [ ] **Step 1: 给事务扫描器加正面白名单（还 TD-18）**

修改 `tools/liaison/tests/test_liaison_effects.py`：在 `def _scan_transaction_violations(` 之前插入：

```python
#: 已知与数据库无关的上下文管理器（TD-18 的还债形态）。
#: ⚠️ 这是一份**正面白名单**：只放行这里逐条列出的被调用者，其余 `with <Call>:`
#: 一律照旧判违规。⛔ 绝不许退化成"只对名字里含 conn 的表达式判违规"——
#: 那会重新打开 `with self._conn:` 的口子，而那个口子**没有症状**。
#: ⛔ 往这份名单里加东西之前先确认：它绝不可能是一个 sqlite3 连接。
_NON_DB_CONTEXT_CALLEES = frozenset(
    {
        "open",
        "os.fdopen",
        "io.open",
        "contextlib.suppress",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryDirectory",
    }
)


def _is_known_non_db_context(func: ast.AST) -> bool:
    # `with <callee>(...)` 的 callee 是否在正面白名单里。
    # 用 `ast.unparse` 取点号全名（`os.fdopen` 而不是只看 `fdopen`），
    # 这样 `with fdopen(...)`（来路不明的裸名）不会被误放行。
    try:
        return ast.unparse(func) in _NON_DB_CONTEXT_CALLEES
    except Exception:
        return False
```

并在 `visit()` 里 `with` 分支的判定之前加一次跳过（⛔ 只加这两行，不动下面原有的判定）：

```python
                if isinstance(expr, ast.Call) and _is_known_non_db_context(expr.func):
                    continue
                if isinstance(expr, (ast.Name, ast.Attribute, ast.Call)):
                    scope = scope_name(func_stack)
```

- [ ] **Step 2: 跑第 2 章全套，确认守卫没有被削弱**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: PASS（33 条）。**逐条确认这 9 条 `test_scanner_*` 全绿**——特别是
`test_scanner_catches_with_call_expression`（`with get_connection() as c:` 仍必须被抓，
因为 `get_connection` 不在白名单里）与 `test_scanner_catches_with_self_conn_attribute`
（`with self._conn:` 走 `ast.Attribute` 分支，本次改动完全没碰它）。
⛔ 这两条只要有一条变成"不再抓"，就说明白名单加错了地方，回退重做。

然后把 `docs/tech-debt.md` 的 `## TD-18 · …` 标题改成 `## ~~TD-18~~ · … ✅ 已还`，并在条目末尾追加：

```markdown
**已还**（第 4 章 Task 3，2026-09-09）：`_scan_transaction_violations` 加 `_NON_DB_CONTEXT_CALLEES`
正面白名单，放行 `open` / `os.fdopen` / `io.open` / `contextlib.suppress` /
`tempfile.NamedTemporaryFile` / `tempfile.TemporaryDirectory`。⛔ 未窄化判据——
`with self._conn:` 与 `with get_connection():` 仍被抓，9 条 `test_scanner_*` 全绿。
```

- [ ] **Step 3: 写失败的测试**

创建 `tools/liaison/tests/test_attachments.py`：

```python
"""附件落盘（4.3）与完整性校验（4.4/4.7）。

本文件最重要的不是"能存能取"，是两条结构性断言：
- `attachments.py` 里 ⛔ 一个 `.decode(` 都不许有（生产 bug「二进制判误」）
- `attachments.py` 里 ⛔ 不许有非二进制模式的 `open()`
两条都带证伪用例——先证明扫描器真的抓得到，再拿它扫真实源码。
"""

from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import zlib

import pytest

from tools.liaison.attachments import (
    AttachmentIntegrityError,
    StoredAttachment,
    compute_digest,
    store_attachment,
    verify_archived_file,
)

MODULE_PATH = pathlib.Path(store_attachment.__globals__["__file__"]).resolve()


# ─────────────────────────────────────────────────────────────────────────
# 4.4 完整性校验只用字节长度 + SHA-256
# ─────────────────────────────────────────────────────────────────────────


def test_compute_digest_returns_byte_length_and_sha256():
    payload = b"\x89PNG\r\n\x1a\n\x00\x01\x02"
    assert compute_digest(payload) == (len(payload), hashlib.sha256(payload).hexdigest())


def test_compute_digest_rejects_str_instead_of_silently_encoding_it():
    """传 `str` 进来 ⇒ 上游某处已经把字节解码过了 ⇒ 必须响亮地失败。

    ⛔ 不许在这里 `payload.encode()` 兜底：那正是"把二进制当文本"的入口，
    生产 bug「二进制判误」就是从这类善意兜底开始的。
    """
    with pytest.raises(TypeError):
        compute_digest("这不是字节")


def test_digest_is_stable_across_calls():
    payload = os.urandom(4096)
    assert compute_digest(payload) == compute_digest(payload)


# ─────────────────────────────────────────────────────────────────────────
# 4.3 落盘：字节流 + 临时文件 + fsync + 原子 rename
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


def test_store_writes_bytes_verbatim(root):
    payload = os.urandom(8192)
    destination = root / "u1" / "20260909" / "m1__样本.bin"

    stored = store_attachment(payload, destination, archive_root=root)

    assert destination.read_bytes() == payload
    assert isinstance(stored, StoredAttachment)
    assert stored.byte_length == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.filename == "m1__样本.bin"
    assert stored.relative_path == "u1/20260909/m1__样本.bin"


def test_store_creates_missing_parent_directories(root):
    destination = root / "深" / "20260909" / "m1__a.txt"
    store_attachment(b"x", destination, archive_root=root)
    assert destination.is_file()


def test_store_leaves_no_temporary_files_behind(root):
    destination = root / "u1" / "20260909" / "m1__a.bin"
    store_attachment(os.urandom(1024), destination, archive_root=root)
    leftovers = [p.name for p in destination.parent.iterdir() if p.name != destination.name]
    assert leftovers == [], f"临时文件没清干净：{leftovers}"


def test_store_is_idempotent_when_destination_already_holds_the_same_bytes(root):
    """D3 的幂等策略：目标路径含 msgid，已存在即视为已完成。

    判据是 **inode 不变**——重跑必须**不重写**，而不是"重写出一样的内容"。
    重写会让一个正在被读取的文件在中途被替换掉。
    """
    payload = os.urandom(2048)
    destination = root / "u1" / "20260909" / "m1__a.bin"

    first = store_attachment(payload, destination, archive_root=root)
    inode_before = destination.stat().st_ino
    second = store_attachment(payload, destination, archive_root=root)

    assert first == second
    assert destination.stat().st_ino == inode_before


def test_store_refuses_to_overwrite_a_different_payload_at_the_same_path(root):
    """同一个 msgid + 同一个文件名却是不同的字节 ⇒ 协议层出了怪事。

    ⛔ 绝不静默覆盖（那是「归档覆盖」）、⛔ 也不静默跳过（那会让台账指向
    一份不是它描述的材料）。抛出来，由调用方记 ERROR 并**不写台账行**——
    留在"材料在、台账没有"这个可收敛、可见的中间态。
    """
    destination = root / "u1" / "20260909" / "m1__a.bin"
    store_attachment(b"first", destination, archive_root=root)

    with pytest.raises(AttachmentIntegrityError):
        store_attachment(b"second", destination, archive_root=root)

    assert destination.read_bytes() == b"first", "冲突时既有材料不许被动过"


def test_store_rejects_str_payload(root):
    with pytest.raises(TypeError):
        store_attachment("文本", root / "u1" / "20260909" / "m1__a.txt", archive_root=root)


def test_store_calls_fsync_on_the_file_and_the_directory(root, monkeypatch):
    """D3 要求 fsync。没有它，"材料已落"这句话在断电后不成立。

    目录也要 fsync——只 fsync 文件的话，rename 本身可能还没落盘。
    """
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])

    store_attachment(b"payload", root / "u1" / "20260909" / "m1__a.bin", archive_root=root)

    assert len(synced) >= 2, f"fsync 只被调了 {len(synced)} 次，文件与目录都要 fsync"


def test_store_uses_atomic_rename_not_a_direct_write(monkeypatch, root):
    """判据：目的地路径**从不**被直接 open 写入，只被 os.replace 落位。

    直接写目的地会让崩溃留下一个半截文件——而半截文件在路径上"存在"，
    下次重跑就被幂等短路当成"已完成"，材料从此永久残缺。
    """
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(
        os, "replace", lambda src, dst: (replaced.append((str(src), str(dst))), real_replace(src, dst))[1]
    )
    destination = root / "u1" / "20260909" / "m1__a.bin"

    store_attachment(b"payload", destination, archive_root=root)

    assert [dst for _, dst in replaced] == [str(destination)]


def test_partial_write_failure_leaves_no_file_at_the_destination(root, monkeypatch):
    """写到一半炸了 ⇒ 目的地必须仍然不存在（原子性的可观测形式）。"""
    destination = root / "u1" / "20260909" / "m1__a.bin"

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        store_attachment(b"payload", destination, archive_root=root)

    assert not destination.exists()


# ─────────────────────────────────────────────────────────────────────────
# 4.7 二进制附件不被误判损坏（生产 bug「二进制判误」）
# ─────────────────────────────────────────────────────────────────────────


def _minimal_xlsx_bytes() -> bytes:
    """一份真实的 zip 容器（xlsx 就是 zip）。⛔ 不用 `b"fake xlsx"` 冒充——
    那种"样本"恰好是 UTF-8 可解码的，会让"二进制判误"这条测试假绿。
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    return buffer.getvalue()


def _minimal_pdf_bytes() -> bytes:
    """最小 PDF：头是 ASCII，但流内容是压缩后的字节，UTF-8 解不开。

    ⛔ 这里刻意**不用** `b"..." % len(body)` 拼接——PDF 的文件头就是 `%PDF`，
    而 `%P` 不是合法的字节格式化占位符，`%` 运算符会当场 `ValueError`
    （计划编写期的提取验证实测踩到）。用拼接，不用格式化。
    """
    body = zlib.compress(os.urandom(512))
    header = b"%PDF-1.7\n1 0 obj\n<< /Length " + str(len(body)).encode("ascii") + b" >>\nstream\n"
    return header + body + b"\nendstream\nendobj\n%%EOF"


@pytest.mark.parametrize(
    "sample,name",
    [(_minimal_xlsx_bytes(), "反馈表.xlsx"), (_minimal_pdf_bytes(), "说明.pdf")],
)
def test_real_binary_samples_round_trip_byte_for_byte(root, sample, name):
    """spec Scenario「二进制附件不被误判损坏」：取回的字节与原文件逐字节相同。"""
    with pytest.raises(UnicodeDecodeError):
        sample.decode("utf-8")  # 前提：这份样本确实不是 UTF-8 可解码的

    destination = root / "u1" / "20260909" / f"m1__{name}"
    stored = store_attachment(sample, destination, archive_root=root)

    assert destination.read_bytes() == sample
    assert verify_archived_file(destination, byte_length=stored.byte_length, sha256=stored.sha256)


def test_verify_detects_truncation_and_corruption(root):
    destination = root / "u1" / "20260909" / "m1__a.bin"
    payload = os.urandom(4096)
    stored = store_attachment(payload, destination, archive_root=root)

    destination.write_bytes(payload[:-1])  # 截断
    assert not verify_archived_file(destination, byte_length=stored.byte_length, sha256=stored.sha256)

    flipped = bytearray(payload)
    flipped[0] ^= 0xFF
    destination.write_bytes(bytes(flipped))  # 等长但内容变了
    assert not verify_archived_file(destination, byte_length=stored.byte_length, sha256=stored.sha256)


def test_verify_returns_false_for_a_missing_file(root):
    assert not verify_archived_file(root / "nope.bin", byte_length=0, sha256="0" * 64)


# ─────────────────────────────────────────────────────────────────────────
# 结构性断言：碰附件字节的模块里 ⛔ 不许有解码，也不许有文本模式的 open
# ─────────────────────────────────────────────────────────────────────────


def scan_text_handling_violations(source: str, filename: str) -> list[str]:
    """扫一份源码，找出两类违规：`.decode(` 调用、非二进制模式的 `open()`。

    做成独立函数是为了能证伪——先喂一份**故意写坏**的源码证明它抓得到，
    再拿它扫真实模块。⛔ 不要把它内联进测试里，那样就没法证伪了。
    """
    violations: list[str] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "decode":
            violations.append(f"{filename}:{node.lineno} 出现 .decode()——⛔ 附件字节不许解码")
        if isinstance(func, ast.Name) and func.id == "open":
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if not isinstance(mode, str) or "b" not in mode:
                violations.append(f"{filename}:{node.lineno} open() 不是二进制模式（mode={mode!r}）")
    return violations


def test_scanner_catches_a_deliberately_broken_module():
    """证伪：扫描器必须抓到这两种写法，否则下面那条对真实源码的断言不成立。"""
    bad = (
        "def load(path):\n"
        "    with open(path) as handle:\n"
        "        return handle.read().decode('utf-8')\n"
    )
    violations = scan_text_handling_violations(bad, "bad.py")
    assert len(violations) == 2, violations
    assert any("decode" in v for v in violations)
    assert any("二进制模式" in v for v in violations)


def test_scanner_allows_binary_mode_open():
    good = "def load(path):\n    with open(path, 'rb') as handle:\n        return handle.read()\n"
    assert scan_text_handling_violations(good, "good.py") == []


def test_attachments_module_never_decodes_and_never_opens_in_text_mode():
    """真实源码上的断言（4.4 逐字：⛔ 任何 UTF-8/文本解码）。

    ⛔ 变红时不许给违规行加豁免——生产 bug「二进制判误」的修法是把解码删掉，
    不是把检查删掉。文件名的字节截断需要 decode，那段代码在 `archive.py`，
    ⛔ 不许搬进本模块。
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert scan_text_handling_violations(source, str(MODULE_PATH)) == []
```

- [ ] **Step 4: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_attachments.py -v`
Expected: collection error —— `ModuleNotFoundError: No module named 'tools.liaison.attachments'`

- [ ] **Step 5: 写最小实现**

创建 `tools/liaison/attachments.py`：

```python
"""附件的字节流落盘与完整性校验（design D4、tasks 4.3/4.4）。

🔴 **本模块是本服务里唯一碰附件字节的地方，它有两条 ⛔ 由扫描器守着的禁令：**

1. ⛔ **一个 `.decode(` 都不许出现。** 完整性校验只用字节长度 + SHA-256。
   参考服务的生产 bug「二进制判误」正是把合法的 xlsx/pdf 拿去做 UTF-8 解码后
   误判为损坏——那不是一次手滑，是"文件内容可以当文本看"这个假设的必然结果。
2. ⛔ **不许有非二进制模式的 `open()`。** 文本模式会做换行转换，
   在 Windows 上把 `\\n` 写成 `\\r\\n`，附件当场变成另一份文件。

守卫在 tests/test_attachments.py::test_attachments_module_never_decodes_and_never_opens_in_text_mode，
带证伪用例。⛔ 变红时的正确修法是删掉违规代码，不是给它加豁免。

**本模块不碰数据库。** 它是 `effect_archive_message` 的**前置**而不是它的一部分
——文件系统不参与 SQL 事务，硬凑只会让一次 I/O 失败把幂等装饰器的事务处理
路径也拖进来，而 `rollback()` 回滚不了已经落盘的文件（design D3）。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile
from dataclasses import dataclass

#: 临时文件的前缀。以 `.` 开头 ⇒ 不会被普通的目录浏览看到；
#: 第 8 章的留存清理按这个前缀顺手扫掉崩溃残留的孤儿临时文件。
_TEMP_PREFIX = ".tmp-"
_TEMP_SUFFIX = ".part"


class AttachmentIntegrityError(RuntimeError):
    """同一个归档路径上已经有一份**不同**的材料。

    路径里含 `msgid`，所以这意味着同一条协议消息带来了两份不同的字节——
    协议层出了怪事。⛔ 不静默覆盖（那是「归档覆盖」），⛔ 也不静默跳过
    （那会让台账指向一份不是它描述的材料）。
    """


@dataclass(frozen=True)
class StoredAttachment:
    """一份已落盘附件的可核对元数据。会被序列化进 `liaison_message.attachments_json`。

    `relative_path` 存的是**相对归档根**的路径：归档根将来可能整体搬家
    （换盘、换机器），存绝对路径会让台账里所有的行一起失效。
    """

    filename: str
    relative_path: str
    byte_length: int
    sha256: str

    def as_dict(self) -> dict:
        return {
            "filename": self.filename,
            "relative_path": self.relative_path,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
        }


def compute_digest(payload: bytes) -> tuple[int, str]:
    """完整性凭据 = （字节长度, SHA-256 十六进制摘要）。

    ⛔ 只有这两样。任何"顺便看看是不是文本""按内容猜类型"的分支都不许加。
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        # ⛔ 不在这里 `payload.encode()` 兜底：那是"把二进制当文本"的入口。
        # 传进来一个 str 说明上游某处已经解码过了，那才是要修的地方。
        raise TypeError(f"附件必须是字节，实际是 {type(payload).__name__}；⛔ 本函数不做隐式编码")
    data = bytes(payload)
    return len(data), hashlib.sha256(data).hexdigest()


def verify_archived_file(path: pathlib.Path, *, byte_length: int, sha256: str) -> bool:
    """核对一份已归档材料。文件不在、长度不符、摘要不符 ⇒ False。

    ⛔ 全程 `rb`，⛔ 不解码。文件不存在返回 False 而不是抛——调用方
    （Task 5 的核对器）要把"哪些不一致"收集成一张清单，不是遇到第一条就停。
    """
    try:
        with open(path, "rb") as handle:
            actual = handle.read()
    except OSError:
        return False
    return (len(actual), hashlib.sha256(actual).hexdigest()) == (byte_length, sha256)


def store_attachment(
    payload: bytes,
    destination: pathlib.Path,
    *,
    archive_root: pathlib.Path,
) -> StoredAttachment:
    """把一份附件原样落到 `destination`，返回可核对的元数据。

    顺序（design D3 逐字）：**临时文件 → `fsync` → 原子 `rename`**。
    ⛔ 不许直接写目的地——崩溃会留下一个半截文件，而半截文件在路径上"存在"，
    下次重跑就被下面的幂等短路当成"已完成"，材料从此永久残缺。

    幂等：目标路径含 `msgid`，已存在**且字节一致**即视为已完成，直接返回，
    ⛔ 不重写（重写会把一个正被读取的文件在中途换掉）。已存在但字节不一致
    ⇒ `AttachmentIntegrityError`。
    """
    byte_length, digest = compute_digest(payload)
    data = bytes(payload)

    if destination.exists():
        if verify_archived_file(destination, byte_length=byte_length, sha256=digest):
            return _stored(destination, archive_root, byte_length, digest)
        raise AttachmentIntegrityError(
            f"归档路径 {destination} 已存在一份不同的材料（同一 msgid 带来了两份不同字节）；"
            f"⛔ 不覆盖、⛔ 不跳过"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        dir=destination.parent, prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX
    )
    temp_path = pathlib.Path(temp_name)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    except BaseException:
        # 清临时文件是尽力而为：清不掉也不能盖住原始异常。留下的孤儿临时文件
        # 以 `.tmp-` 开头，第 8 章的留存清理会扫掉。
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # 目录也要 fsync：只 fsync 文件的话，`rename` 这个目录项的变更本身
    # 可能还没落盘——断电后文件内容在、名字不在，等于材料没落。
    dir_fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    return _stored(destination, archive_root, byte_length, digest)


def _stored(
    destination: pathlib.Path, archive_root: pathlib.Path, byte_length: int, digest: str
) -> StoredAttachment:
    return StoredAttachment(
        filename=destination.name,
        relative_path=destination.relative_to(archive_root).as_posix(),
        byte_length=byte_length,
        sha256=digest,
    )
```

- [ ] **Step 6: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_attachments.py -v`
Expected: PASS（19 条；`test_real_binary_samples_round_trip_byte_for_byte` 展开成 2 个参数）

- [ ] **Step 7: 提交**

```bash
git add tools/liaison/attachments.py tools/liaison/tests/test_attachments.py \
        tools/liaison/tests/test_liaison_effects.py docs/tech-debt.md
git commit -m "feat(liaison): 附件字节流落盘（临时文件+fsync+原子rename）与字节长度+SHA-256 校验（4.3/4.4/4.7）；还 TD-18 事务扫描器误报"
```

---

### Task 4: `archive_message`——先落材料、后写台账

**Files:**
- Modify: `tools/liaison/archive.py`（在 Task 2 的内容之后追加）
- Test: `tools/liaison/tests/test_archive_effect.py`

**Interfaces:**
- Consumes: `compute_archive_path`（Task 2）、`store_attachment` / `StoredAttachment` / `AttachmentIntegrityError`（Task 3）、第 2 章的 `effect_archive_message`
- Produces:
  - `tools.liaison.archive.InboundAttachment`（frozen dataclass：`filename: str`、`payload: bytes`）
  - `tools.liaison.archive.ArchiveOutcome`（frozen dataclass：`msgid: str`、`newly_archived: bool`、`attachments: tuple[StoredAttachment, ...]`）
  - `tools.liaison.archive.archive_message(conn, *, thread_id, msgid, sender_userid, received_at, msgtype, content="", attachment=None, archive_root=DEFAULT_ARCHIVE_ROOT) -> ArchiveOutcome`
  Task 5 读它写进 `attachments_json` 的结构；Task 6 调 `archive_message` 并看 `newly_archived`。

**⚠️ 一条消息最多一个附件。** 企微 aibot 协议里每条消息只有一个 `msgtype`，媒体类消息携带**一个**媒体项。`attachment` 因此是 `InboundAttachment | None` 而不是列表。`attachments_json` 仍存**数组**（表默认值就是 `'[]'`，且给将来留口子），长度 0 或 1。
⛔ **不要现在就实现多附件**——那需要给路径加一级"消息内序号"，而 D4 的路径形态 `<msgid>__<原文件名>` 是逐字钉死的。真出现多附件的消息类型时，正确动作是登记偏离、改 design D4，不是在这里预留一个没人验证过的分支（YAGNI）。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_archive_effect.py`：

```python
"""归档编排（4.5）：先落材料、后写台账，重复投递不产生第二份（4.6/4.8）。

本文件的核心不是"能写进去"，是**顺序**与**中间态方向**：
"文件已落、DB 行未写"可收敛；"DB 行已写、文件缺失"是不可恢复的谎。
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from tools.liaison.archive import (
    ArchiveOutcome,
    InboundAttachment,
    archive_message,
)
from tools.liaison.attachments import AttachmentIntegrityError
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:30:00+08:00"


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


def _archive(conn, root, *, msgid="m1", thread_id="tanglp", filename=None, payload=None, content="收到"):
    attachment = (
        InboundAttachment(filename=filename, payload=payload) if payload is not None else None
    )
    return archive_message(
        conn,
        thread_id=thread_id,
        msgid=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        msgtype="file" if attachment else "text",
        content=content,
        attachment=attachment,
        archive_root=root,
    )


def _rows(conn):
    return conn.execute(
        "SELECT msgid, thread_id, attachments_json FROM liaison_message ORDER BY msgid"
    ).fetchall()


# ─────────────────────────────────────────────────────────────────────────
# 4.5 顺序：材料先落、台账后写
# ─────────────────────────────────────────────────────────────────────────


def test_text_message_without_attachment_is_archived(conn, root):
    outcome = _archive(conn, root, content="明天面试改到下午三点")

    assert isinstance(outcome, ArchiveOutcome)
    assert outcome.newly_archived is True
    assert outcome.attachments == ()
    assert _rows(conn) == [("m1", "tanglp", "[]")]
    assert_effect_log_identity(conn)


def test_attachment_lands_on_disk_and_is_recorded_in_the_ledger(conn, root):
    payload = b"\x50\x4b\x03\x04binary-ish\x00\xff"
    outcome = _archive(conn, root, filename="岗位要求确认反馈表v3.xlsx", payload=payload)

    stored = outcome.attachments[0]
    assert (root / stored.relative_path).read_bytes() == payload

    recorded = json.loads(_rows(conn)[0][2])
    assert recorded == [stored.as_dict()]
    assert recorded[0]["filename"] == "m1__岗位要求确认反馈表v3.xlsx"
    assert_effect_log_identity(conn)


def test_attachments_json_keeps_chinese_readable(conn, root):
    """`ensure_ascii=False`：库里存的是可读的中文，不是 `\\uXXXX` 转义。

    这不是审美——排障时要靠 `sqlite3 data/liaison.db 'select ...'` 直接看，
    一串转义码会让"这条到底是哪个文件"变成一次额外的解码。
    """
    _archive(conn, root, filename="反馈表.xlsx", payload=b"x")
    assert "反馈表" in _rows(conn)[0][2]


def test_store_is_called_before_the_ledger_write(conn, root, monkeypatch):
    """运行期判据：调用顺序必须是 落盘 → 写台账。"""
    calls = []
    import tools.liaison.archive as archive_module

    real_store = archive_module.store_attachment
    real_effect = archive_module.effect_archive_message
    monkeypatch.setattr(
        archive_module, "store_attachment",
        lambda *a, **k: (calls.append("store"), real_store(*a, **k))[1],
    )
    monkeypatch.setattr(
        archive_module, "effect_archive_message",
        lambda *a, **k: (calls.append("ledger"), real_effect(*a, **k))[1],
    )

    _archive(conn, root, filename="a.bin", payload=b"x")

    assert calls == ["store", "ledger"], f"D3 的顺序被反了：{calls}"


def test_source_order_puts_store_attachment_before_the_effect_call():
    """静态判据：源码里 `store_attachment` 的调用行号必须小于 `effect_archive_message` 的。

    运行期那条测试可以被一个"先算后写"的重构绕过去（比如把落盘挪进 effect
    的参数求值里）。这条盯的是源码形态，⛔ 两条都要，不许二选一。
    """
    tree = ast.parse(inspect.getsource(archive_message))
    lines = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in ("store_attachment", "effect_archive_message"):
                lines.setdefault(name, node.lineno)
    assert "store_attachment" in lines and "effect_archive_message" in lines, lines
    assert lines["store_attachment"] < lines["effect_archive_message"], (
        f"⛔ D3 顺序在源码里被反了：{lines}"
    )


def test_ledger_row_is_never_written_when_the_attachment_fails_to_land(conn, root, monkeypatch):
    """spec「MUST NOT 出现'台账已记但材料缺失'」的直接断言。

    落盘炸了 ⇒ 台账一行都不能有。⛔ 不许"先记上等下次补文件"——
    那句台账就是一个不可恢复的谎。
    """
    import tools.liaison.archive as archive_module

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(archive_module, "store_attachment", boom)

    with pytest.raises(OSError):
        _archive(conn, root, filename="a.bin", payload=b"x")

    assert _rows(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_conflicting_bytes_at_the_same_path_abort_before_the_ledger_write(conn, root):
    """同一 msgid 带来两份不同字节 ⇒ 抛，且台账保持原状（既有那一行不变）。"""
    _archive(conn, root, filename="a.bin", payload=b"first")
    assert len(_rows(conn)) == 1

    with pytest.raises(AttachmentIntegrityError):
        _archive(conn, root, msgid="m1", filename="a.bin", payload=b"second")

    assert len(_rows(conn)) == 1
    assert_effect_log_identity(conn)


def test_archive_message_never_commits_by_itself():
    """⛔ 本模块不许出现 `conn.commit()` / `conn.rollback()`。

    提交由 `idempotent_effect` 独占——那是"业务写与幂等记录同一个 BEGIN"
    成立的结构前提（`docs/findings/2026-08-13-sqlite-事务归属冲突.md`）。
    第 2 章已有一条仓库级的 AST 扫描器守这条；这里再钉一次本模块的源码，
    让违规在本章的测试里就红，不必等跑到 test_liaison_effects.py。
    """
    import pathlib

    import tools.liaison.archive as archive_module

    source = pathlib.Path(archive_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback")
    ]
    assert offenders == [], f"archive.py 里出现了 commit/rollback，行号 {offenders}"


# ─────────────────────────────────────────────────────────────────────────
# 4.6 同人同天多条消息互不覆盖（生产 bug「归档覆盖」）
# ─────────────────────────────────────────────────────────────────────────


def test_three_same_named_attachments_on_the_same_day_all_survive(conn, root):
    """spec Scenario「同人同天多条消息」：3 份内容各自完整，没有一份被覆盖。"""
    payloads = {f"m{n}": bytes([n]) * 1024 for n in (1, 2, 3)}
    outcomes = {
        msgid: _archive(conn, root, msgid=msgid, filename="反馈表.xlsx", payload=payload)
        for msgid, payload in payloads.items()
    }

    for msgid, payload in payloads.items():
        stored = outcomes[msgid].attachments[0]
        assert (root / stored.relative_path).read_bytes() == payload

    assert len({o.attachments[0].relative_path for o in outcomes.values()}) == 3
    assert len(_rows(conn)) == 3
    assert_effect_log_identity(conn)


def test_same_name_different_content_can_both_be_retrieved(conn, root):
    """spec Scenario「同人同天同名文件内容不同」：两份都能分别取回。"""
    first = _archive(conn, root, msgid="m1", filename="表.xlsx", payload=b"AAAA")
    second = _archive(conn, root, msgid="m2", filename="表.xlsx", payload=b"BBBB")

    assert (root / first.attachments[0].relative_path).read_bytes() == b"AAAA"
    assert (root / second.attachments[0].relative_path).read_bytes() == b"BBBB"


def test_two_senders_do_not_share_a_directory(conn, root):
    a = _archive(conn, root, thread_id="tanglp", msgid="m1", filename="表.xlsx", payload=b"A")
    b = _archive(conn, root, thread_id="shaops", msgid="m2", filename="表.xlsx", payload=b"B")
    assert a.attachments[0].relative_path.split("/")[0] == "tanglp"
    assert b.attachments[0].relative_path.split("/")[0] == "shaops"


# ─────────────────────────────────────────────────────────────────────────
# 4.8 重复投递 / 崩溃后重跑
# ─────────────────────────────────────────────────────────────────────────


def test_delivering_the_same_message_twice_yields_one_archive_and_one_row(conn, root):
    """spec Scenario「同一消息被投递两次」。"""
    payload = b"once"
    first = _archive(conn, root, msgid="m1", filename="a.bin", payload=payload)
    second = _archive(conn, root, msgid="m1", filename="a.bin", payload=payload)

    assert first.newly_archived is True
    assert second.newly_archived is False, "第二次必须是幂等命中"
    assert len(_rows(conn)) == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1
    assert second.attachments[0] == first.attachments[0]
    assert_effect_log_identity(conn)


def test_replaying_a_whole_batch_adds_nothing(conn, root):
    """spec Scenario「重连后重投历史消息」：归档与队列均无新增，恒等仍成立。"""
    batch = [("tanglp", "m1"), ("tanglp", "m2"), ("wrkSHat_chat1", "m3")]
    for thread_id, msgid in batch:
        _archive(conn, root, thread_id=thread_id, msgid=msgid, filename="a.bin", payload=msgid.encode())

    files_before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    for thread_id, msgid in batch:
        outcome = _archive(conn, root, thread_id=thread_id, msgid=msgid, filename="a.bin", payload=msgid.encode())
        assert outcome.newly_archived is False

    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == files_before
    assert len(_rows(conn)) == 3
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_crash_between_landing_the_file_and_writing_the_ledger_converges(conn, root):
    """spec Scenario「处理中途被强制终止」。

    模拟：材料已落盘，台账行还没写，进程被 kill。重启后重新处理同一条消息 ⇒
    台账与材料一致，且**没有**第二份材料。
    """
    import tools.liaison.archive as archive_module

    def die(*args, **kwargs):
        raise KeyboardInterrupt("kill -9 的替身")

    monkeypatched = pytest.MonkeyPatch()
    monkeypatched.setattr(archive_module, "effect_archive_message", die)
    with pytest.raises(KeyboardInterrupt):
        _archive(conn, root, msgid="m1", filename="a.bin", payload=b"payload")
    monkeypatched.undo()

    # 中间态：材料在、台账没有——这是**允许**的方向
    landed = sorted(p for p in root.rglob("*") if p.is_file())
    assert len(landed) == 1
    assert _rows(conn) == []

    # 重跑：收敛
    outcome = _archive(conn, root, msgid="m1", filename="a.bin", payload=b"payload")
    assert outcome.newly_archived is True
    assert len(_rows(conn)) == 1
    assert sorted(p for p in root.rglob("*") if p.is_file()) == landed, "⛔ 不许落出第二份材料"
    assert_effect_log_identity(conn)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_effect.py -v`
Expected: `ImportError: cannot import name 'archive_message'`

- [ ] **Step 3: 写最小实现**

在 `tools/liaison/archive.py` 顶部的 import 段追加：

```python
import json
from dataclasses import dataclass

from tools.liaison.attachments import StoredAttachment, store_attachment
from tools.liaison.storage.effects import effect_archive_message
```

并在文件末尾追加：

```python
@dataclass(frozen=True)
class InboundAttachment:
    """通道层递进来的一份附件：原始文件名 + 原始字节。

    ⛔ `payload` 必须是 `bytes`。通道适配层（第 7 章）负责把 SDK 给的东西
    转成字节，⛔ 不许在这里做任何"如果是 str 就 encode 一下"的兜底。
    """

    filename: Any
    payload: bytes


@dataclass(frozen=True)
class ArchiveOutcome:
    """一次归档的结果。

    `newly_archived` 区分"这次真的写了台账"与"幂等命中、之前就写过了"——
    第 5 章据此决定要不要入队，Task 6 据此决定要不要发礼貌回复。
    ⛔ 不要用"台账里有没有这一行"去替代它：那在并发下是另一次查询、另一个时刻。
    """

    msgid: str
    newly_archived: bool
    attachments: tuple[StoredAttachment, ...]


def archive_message(
    conn,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    received_at: str,
    msgtype: str,
    content: str = "",
    attachment: InboundAttachment | None = None,
    archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT,
) -> ArchiveOutcome:
    """归档一条消息：**先落材料、后写台账**（design D3 的顺序，⛔ 不许反）。

    1. 有附件就先算路径、写临时文件、`fsync`、原子 `rename` 到最终路径；
    2. 然后调第 2 章的 `effect_archive_message`——台账行与 `effect_log` 行
       在**同一个事务**里提交，幂等键 `{thread_id}:effect_archive_message:{msgid}`。

    ⛔ 本函数不 `commit()` 也不 `rollback()`：提交由 `idempotent_effect` 独占，
    那是"业务写与幂等记录同一个 BEGIN"成立的结构前提。

    两个中间态的方向（spec 原文）：
    - **允许**"材料已在、台账未记"——重跑时 rename 幂等、台账行补上，收敛；
    - ⛔ **禁止**"台账已记、材料缺失"——台账说材料收到了，材料却不在，
      这是不可恢复的谎。所以落盘失败时本函数直接向上抛，一行台账都不写。

    ⚠️ **一条消息最多一个附件**（aibot 协议：每条消息一个 msgtype、一个媒体项）。
    `attachments_json` 仍是数组，长度 0 或 1。真出现多附件的消息类型时，
    正确动作是**登记偏离、改 design D4 的路径形态**，⛔ 不是在这里加一个
    没人验证过的分支。
    """
    stored: tuple[StoredAttachment, ...] = ()
    if attachment is not None:
        destination = compute_archive_path(
            thread_id=thread_id,
            msgid=msgid,
            received_at=received_at,
            filename=attachment.filename,
            archive_root=archive_root,
        )
        stored = (store_attachment(attachment.payload, destination, archive_root=archive_root),)

    # ⛔ 这一行必须在上面那段之后。顺序倒过来就是 design D3 明令禁止的那种谎。
    applied = effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=sender_userid,
        received_at=received_at,
        msgtype=msgtype,
        content=content,
        # ensure_ascii=False：中文文件名在库里保持可读，排障时 sqlite3 直接看得懂。
        attachments_json=json.dumps([item.as_dict() for item in stored], ensure_ascii=False),
    )

    return ArchiveOutcome(msgid=msgid, newly_archived=applied is not None, attachments=stored)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_effect.py -v`
Expected: PASS（14 条）

再跑一次第 2 章的全套，确认没有把它的断言碰红：

Run: `venv/bin/python -m pytest tools/liaison/tests/ -v`
Expected: PASS（第 2 章的 `test_effect_node_to_table_matches_the_insert_target_repo_wide` 必须仍绿——本章没有新增 `@idempotent_effect` 函数）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/archive.py tools/liaison/tests/test_archive_effect.py
git commit -m "feat(liaison): archive_message——先落材料后写台账，重复投递不产生第二份（4.5/4.6/4.8）"
```

---

### Task 5: 台账 ↔ 材料一致性核对器

**Files:**
- Create: `tools/liaison/consistency.py`
- Test: `tools/liaison/tests/test_archive_consistency.py`

**Interfaces:**
- Consumes: `verify_archived_file`（Task 3）、`archive_message`（Task 4，测试里用来造数据）、`DEFAULT_ARCHIVE_ROOT`（Task 2）
- Produces:
  - `tools.liaison.consistency.LedgerInconsistency`（frozen dataclass：`msgid: str`、`relative_path: str`、`problem: str`）
  - `tools.liaison.consistency.verify_ledger_against_archive(conn, *, archive_root=DEFAULT_ARCHIVE_ROOT) -> list[LedgerInconsistency]`
  第 8 章的留存清理会在清理前后各调一次它。

**为什么这个 Task 单独存在**：spec 的「台账中存在的消息其材料必然可取回」有两个 Scenario，第二个是「**遍历**消息台账中每条带附件的记录 → 每条记录对应的材料均可读取且摘要匹配」。那是一条**可反复执行的核对**，不是一次写入路径上的断言——它得能在任何时刻对着一个真实的 `liaison.db` 跑。做成一个函数，第 8 章的留存清理才有前后对照的基准。
⛔ **不许顺手在这里写 `delete_expired()`**——留存清理是第 8 章。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_archive_consistency.py`：

```python
"""spec「台账中存在的消息其材料必然可取回」的可反复执行版本。

这个核对器只回答一个方向的问题：**台账说有的，磁盘上是不是真的有且没变。**
⛔ 反方向（磁盘上有、台账没有）不是不一致——那正是 design D3 允许的中间态。
"""

from __future__ import annotations

import json

import pytest

from tools.liaison.archive import InboundAttachment, archive_message
from tools.liaison.consistency import (
    LedgerInconsistency,
    verify_ledger_against_archive,
)
from tools.liaison.storage import db as liaison_db

RECEIVED_AT = "2026-09-09T10:30:00+08:00"


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


def _archive(conn, root, msgid, payload=None, filename="表.xlsx"):
    return archive_message(
        conn,
        thread_id="tanglp",
        msgid=msgid,
        sender_userid="tanglp",
        received_at=RECEIVED_AT,
        msgtype="file" if payload is not None else "text",
        content="",
        attachment=InboundAttachment(filename=filename, payload=payload) if payload is not None else None,
        archive_root=root,
    )


def test_a_healthy_archive_reports_no_inconsistency(conn, root):
    """spec Scenario「台账与材料的一致性可核对」的正路。"""
    for n in range(5):
        _archive(conn, root, f"m{n}", payload=f"payload-{n}".encode())

    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_empty_ledger_is_consistent(conn, root):
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_text_only_messages_are_skipped(conn, root):
    """没有附件的消息不参与核对——`attachments_json` 是 `[]`，没有材料可查。"""
    _archive(conn, root, "m1")
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_a_missing_file_behind_the_ledger_is_reported(conn, root):
    """"台账已记但材料缺失"——这正是 spec 禁止出现的那种状态，核对器必须看得见。"""
    outcome = _archive(conn, root, "m1", payload=b"payload")
    (root / outcome.attachments[0].relative_path).unlink()

    problems = verify_ledger_against_archive(conn, archive_root=root)

    assert len(problems) == 1
    assert isinstance(problems[0], LedgerInconsistency)
    assert problems[0].msgid == "m1"
    assert problems[0].relative_path == outcome.attachments[0].relative_path


def test_a_corrupted_file_is_reported(conn, root):
    """长度相同但内容变了 ⇒ SHA-256 抓得到。"""
    outcome = _archive(conn, root, "m1", payload=b"AAAA")
    (root / outcome.attachments[0].relative_path).write_bytes(b"BBBB")

    problems = verify_ledger_against_archive(conn, archive_root=root)
    assert [p.msgid for p in problems] == ["m1"]


def test_a_truncated_file_is_reported(conn, root):
    outcome = _archive(conn, root, "m1", payload=b"AAAABBBB")
    (root / outcome.attachments[0].relative_path).write_bytes(b"AAAA")

    assert [p.msgid for p in verify_ledger_against_archive(conn, archive_root=root)] == ["m1"]


def test_extra_files_on_disk_are_not_inconsistencies(conn, root):
    """磁盘上有、台账没有 ⇒ design D3 明确允许的中间态，⛔ 不许报成不一致。

    报了会让崩溃恢复期的每一次核对都刷出一堆假警报，真问题就淹了。
    """
    _archive(conn, root, "m1", payload=b"payload")
    stray = root / "tanglp" / "20260909" / "m9__孤儿.bin"
    stray.write_bytes(b"orphan")

    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_malformed_attachments_json_is_reported_not_raised(conn, root):
    """台账里存了坏 JSON ⇒ 报一条不一致，⛔ 不抛——核对器要能跑完整张表。"""
    _archive(conn, root, "m1", payload=b"payload")
    conn.execute("UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?", ("{坏", "m1"))
    conn.commit()

    problems = verify_ledger_against_archive(conn, archive_root=root)
    assert len(problems) == 1
    assert "attachments_json" in problems[0].problem


def test_all_rows_are_visited_even_after_the_first_failure(conn, root):
    """遇到第一条坏的不能停——spec 要求的是"遍历每条记录"。"""
    outcomes = {m: _archive(conn, root, m, payload=m.encode()) for m in ("m1", "m2", "m3")}
    for msgid in ("m1", "m3"):
        (root / outcomes[msgid].attachments[0].relative_path).unlink()

    assert sorted(p.msgid for p in verify_ledger_against_archive(conn, archive_root=root)) == ["m1", "m3"]


def test_checker_reads_bytes_never_text(conn, root):
    """核对走的是 `verify_archived_file`（`rb` + SHA-256），二进制样本必须过。"""
    payload = bytes(range(256)) * 8
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")

    _archive(conn, root, "m1", payload=payload)
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_ledger_records_enough_to_re_verify_without_the_original(conn, root):
    """台账里存的元数据必须自足：路径 + 字节长度 + SHA-256，三样齐了才核对得了。"""
    _archive(conn, root, "m1", payload=b"payload")
    recorded = json.loads(
        conn.execute("SELECT attachments_json FROM liaison_message WHERE msgid = 'm1'").fetchone()[0]
    )
    assert set(recorded[0]) == {"filename", "relative_path", "byte_length", "sha256"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_consistency.py -v`
Expected: `ModuleNotFoundError: No module named 'tools.liaison.consistency'`

- [ ] **Step 3: 写最小实现**

创建 `tools/liaison/consistency.py`：

```python
"""台账 ↔ 材料的一致性核对（spec「台账中存在的消息其材料必然可取回」）。

**只核对一个方向**：台账说有的，磁盘上是不是真的有、且字节没变。
⛔ 反方向（磁盘上有、台账没有）**不是**不一致——那正是 design D3 允许的、
可由重跑收敛的中间态。把它报成问题，会让崩溃恢复期的每次核对刷出一堆假
警报，真问题就淹在里面了。

⛔ **本模块不删任何东西。** 留存期清理是第 8 章。本模块是清理的**前置**
（清理前后各跑一次，用来证明清理没有把台账和材料弄成两张皮），不是清理本身。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from tools.liaison.archive import DEFAULT_ARCHIVE_ROOT
from tools.liaison.attachments import verify_archived_file


@dataclass(frozen=True)
class LedgerInconsistency:
    """一条"台账说有、实际对不上"的记录。

    `relative_path` 在"连 JSON 都解析不了"的情况下是空串——那时候根本
    读不出路径。⛔ 不要因此把它做成 `str | None`：调用方多一个分支，
    换来的信息量是零。
    """

    msgid: str
    relative_path: str
    problem: str


def verify_ledger_against_archive(
    conn, *, archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT
) -> list[LedgerInconsistency]:
    """遍历消息台账里每条带附件的记录，核对材料可读且摘要匹配。

    返回不一致清单；空列表 = 一致。**遇到坏记录继续往下跑**，⛔ 不抛异常、
    ⛔ 不提前返回——spec 要求的是"遍历每条记录"，跑一半停下等于没核对。
    """
    problems: list[LedgerInconsistency] = []

    for msgid, attachments_json in conn.execute(
        "SELECT msgid, attachments_json FROM liaison_message ORDER BY msgid"
    ):
        try:
            entries = json.loads(attachments_json)
        except (TypeError, ValueError):
            problems.append(
                LedgerInconsistency(msgid, "", "attachments_json 不是合法 JSON，无法核对")
            )
            continue

        if not isinstance(entries, list):
            problems.append(
                LedgerInconsistency(msgid, "", "attachments_json 不是数组，无法核对")
            )
            continue

        for entry in entries:
            problems.extend(_verify_entry(msgid, entry, archive_root))

    return problems


def _verify_entry(msgid: str, entry, archive_root: pathlib.Path) -> list[LedgerInconsistency]:
    if not isinstance(entry, dict):
        return [LedgerInconsistency(msgid, "", "attachments_json 条目不是对象")]

    relative_path = entry.get("relative_path")
    byte_length = entry.get("byte_length")
    sha256 = entry.get("sha256")
    if not isinstance(relative_path, str) or not isinstance(byte_length, int) or not isinstance(sha256, str):
        return [
            LedgerInconsistency(
                msgid,
                relative_path if isinstance(relative_path, str) else "",
                "attachments_json 条目缺 relative_path / byte_length / sha256",
            )
        ]

    if not verify_archived_file(
        archive_root / relative_path, byte_length=byte_length, sha256=sha256
    ):
        return [
            LedgerInconsistency(
                msgid, relative_path, "材料缺失或字节长度/SHA-256 与台账不符"
            )
        ]

    return []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_archive_consistency.py -v`
Expected: PASS（11 条）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/consistency.py tools/liaison/tests/test_archive_consistency.py
git commit -m "feat(liaison): 台账↔材料一致性核对器——遍历每条带附件记录核对长度与 SHA-256"
```

---

### Task 6: 名单外分支接线——只归档 + 礼貌回复，⛔ 不入队

**Files:**
- Create: `tools/liaison/inbound.py`
- Test: `tools/liaison/tests/test_inbound_routing.py`
- Modify: `openspec/changes/hr-wecom-aibot-liaison/tasks.md`（回勾 4.1–4.10 + 落地偏离登记；**final review 通过后才做**）
- Modify: `docs/tech-debt.md`（登记礼貌回复的 at-most-once 缺口）

**Interfaces:**
- Consumes: `admit`（第 3 章 `whitelist.py`）、`archive_message` / `ArchiveOutcome` / `InboundAttachment` / `DEFAULT_ARCHIVE_ROOT`（Task 2、Task 4）
- Produces:
  - `tools.liaison.inbound.InboundRoute`（frozen dataclass：`admitted: bool`、`should_enqueue: bool`、`reply_text: str | None`）
  - `tools.liaison.inbound.POLITE_NOTICE: str`
  - `tools.liaison.inbound.compute_inbound_route(admitted: bool) -> InboundRoute`
  - `tools.liaison.inbound.InboundResult`（frozen dataclass：`route: InboundRoute`、`outcome: ArchiveOutcome`、`replied: bool`）
  - `tools.liaison.inbound.handle_inbound_message(conn, *, thread_id, msgid, sender_userid, received_at, msgtype, content="", attachment=None, archive_root=DEFAULT_ARCHIVE_ROOT, whitelist_path=None, reply=None) -> InboundResult`
  **第 5 章的接线位** = `InboundRoute.should_enqueue`：第 5 章在 `handle_inbound_message` 里把 `effect_enqueue_task` 挂在这个布尔值后面。⛔ 本章不写那一行。

**⚠️ 已知缺口（本 Task 必须登记，⛔ 不许当它不存在）**：礼貌回复走**注入的 reply port**，不是 `effect_*`，所以它是 **at-most-once** 的——台账已提交、回复还没发出去时进程被杀，这条回复永久丢失（对方不会收到任何东西，而重投时归档已幂等命中、不会再触发回复）。
**为什么现在不修**：修法是给回复配一张 outbox 表 + 一个 `effect_reply_*` 幂等 effect。加表触发 opener 约束 5 的"登记偏离并停在该点"；而真实外发通道本来就在第 6／7 章。**结论：本章按 at-most-once 实现并登记技术债，第 6／7 章连同真实通道一起补成带 outbox 行的幂等 effect。**
**影响面**：丢的是一条"您不在受理名单内"的告知，不丢材料（归档已落）、不丢待办（名单外本来就不入队）。方向安全。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_inbound_routing.py`：

```python
"""名单内／外的分支接线（4.10）。

本章只做三件事：**归档两条分支都做**、**名单外回一条礼貌说明**、
**⛔ 队列条目数不变**。入队是第 5 章的事，本文件有一条 AST 断言把这条钉死。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import tools.liaison.inbound as inbound_module
from tools.liaison.archive import InboundAttachment
from tools.liaison.inbound import (
    POLITE_NOTICE,
    compute_inbound_route,
    handle_inbound_message,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:30:00+08:00"
ADMITTED_USERID = "tanglp"
OUTSIDER_USERID = "someone-else"


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


@pytest.fixture
def roster(tmp_path):
    """一份只含 ADMITTED_USERID 的名单文件。

    ⛔ 不依赖仓库里那份真实的 `config/whitelist.yaml`——它的内容会随 D2 的
    名单变更而变，把测试绑在上面等于让"改名单"顺手打红一批用例。
    """
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        f"  - userid: {ADMITTED_USERID}\n"
        "    name: 汤丽萍\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    return path


class ReplySpy:
    """记账用的 reply port。真实通道在第 6／7 章，本章只认这个可调用契约。"""

    def __init__(self):
        self.calls = []

    def __call__(self, thread_id: str, text: str) -> None:
        self.calls.append((thread_id, text))


def _handle(conn, root, roster, *, sender, msgid="m1", thread_id=None, reply=None, payload=None):
    return handle_inbound_message(
        conn,
        thread_id=thread_id or sender,
        msgid=msgid,
        sender_userid=sender,
        received_at=RECEIVED_AT,
        msgtype="file" if payload is not None else "text",
        content="材料在这里",
        attachment=InboundAttachment(filename="表.xlsx", payload=payload) if payload is not None else None,
        archive_root=root,
        whitelist_path=roster,
        reply=reply,
    )


def _task_count(conn):
    return conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0]


def _message_count(conn):
    return conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────
# 纯函数：路由决策
# ─────────────────────────────────────────────────────────────────────────


def test_route_for_an_admitted_sender():
    route = compute_inbound_route(True)
    assert (route.admitted, route.should_enqueue, route.reply_text) == (True, True, None)


def test_route_for_an_outsider():
    route = compute_inbound_route(False)
    assert route.admitted is False
    assert route.should_enqueue is False, "⛔ 名单外不得生成队列条目"
    assert route.reply_text == POLITE_NOTICE


def test_polite_notice_is_marked_as_automated_and_leaks_no_roster():
    """回复必须让人看出这是自动发的（⛔ 不能被误以为是 Shao Peishen 本人回的），
    且 ⛔ 不得透露名单里有谁（那是不必要的个人信息披露）。
    """
    assert "自动" in POLITE_NOTICE
    assert "Shao Peishen" in POLITE_NOTICE
    assert "汤丽萍" not in POLITE_NOTICE and ADMITTED_USERID not in POLITE_NOTICE


def test_compute_inbound_route_is_pure():
    import inspect

    tree = ast.parse(inspect.getsource(compute_inbound_route))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not (called & {"open", "now", "today", "getenv", "admit", "print"}), called


# ─────────────────────────────────────────────────────────────────────────
# 4.10 / whitelist spec「名单外消息只归档并礼貌回复」
# ─────────────────────────────────────────────────────────────────────────


def test_outsider_message_is_archived_replied_and_never_enqueued(conn, root, roster):
    """spec Scenario「名单外成员发消息」：归档 + 礼貌回复 + ⛔ 零队列条目。"""
    spy = ReplySpy()
    before = _task_count(conn)

    result = _handle(conn, root, roster, sender=OUTSIDER_USERID, reply=spy)

    assert result.route.admitted is False
    assert _message_count(conn) == 1, "名单外的消息**仍然要归档**（事后可查谁发过什么）"
    assert spy.calls == [(OUTSIDER_USERID, POLITE_NOTICE)]
    assert _task_count(conn) == before == 0, "⛔ 队列条目数必须不变"
    assert result.replied is True
    assert_effect_log_identity(conn)


def test_outsider_group_message_behaves_the_same(conn, root, roster):
    """spec Scenario「名单外的群消息」：行为与私聊一致——只归档、不入队。

    群聊的 thread_id 取 chatid（design D3），发送人仍是 userid。
    """
    spy = ReplySpy()
    result = _handle(
        conn, root, roster, sender=OUTSIDER_USERID, thread_id="wrkSHat_chat_001", reply=spy
    )

    assert result.route.should_enqueue is False
    assert _message_count(conn) == 1
    assert _task_count(conn) == 0
    assert spy.calls == [("wrkSHat_chat_001", POLITE_NOTICE)]
    assert_effect_log_identity(conn)


def test_outsider_attachment_is_still_archived_and_retrievable(conn, root, roster):
    """名单外也归档材料——"谁在什么时候发过什么"要可查。"""
    payload = b"\x00\x01\x02outsider"
    result = _handle(conn, root, roster, sender=OUTSIDER_USERID, payload=payload, reply=ReplySpy())

    stored = result.outcome.attachments[0]
    assert (root / stored.relative_path).read_bytes() == payload
    assert _task_count(conn) == 0


def test_admitted_message_is_archived_without_a_reply(conn, root, roster):
    """名单内不发礼貌回复——那条文案的意思是"你不在名单里"，发错人是骚扰。"""
    spy = ReplySpy()
    result = _handle(conn, root, roster, sender=ADMITTED_USERID, reply=spy)

    assert result.route.admitted is True
    assert result.route.should_enqueue is True, "第 5 章据此入队"
    assert spy.calls == []
    assert _message_count(conn) == 1
    assert_effect_log_identity(conn)


def test_this_chapter_never_enqueues_even_for_admitted_senders(conn, root, roster):
    """4.10 逐字："此处只接线并加断言'队列条目数不变'"。

    名单内的入队是第 5 章的事。本章跑完，队列必须还是空的——
    ⛔ 这条变红说明有人提前把第 5 章写进来了。
    """
    for n in range(3):
        _handle(conn, root, roster, sender=ADMITTED_USERID, msgid=f"m{n}", reply=ReplySpy())

    assert _message_count(conn) == 3
    assert _task_count(conn) == 0
    assert_effect_log_identity(conn)


def test_replaying_an_outsider_message_does_not_reply_twice(conn, root, roster):
    """重投同一 msgid ⇒ 归档幂等命中 ⇒ ⛔ 不重复发回复。

    判据挂在 `newly_archived` 上，而不是"查一下台账里有没有"——后者是另一次
    查询、另一个时刻，并发下会两条都发出去。
    """
    spy = ReplySpy()
    first = _handle(conn, root, roster, sender=OUTSIDER_USERID, msgid="m1", reply=spy)
    second = _handle(conn, root, roster, sender=OUTSIDER_USERID, msgid="m1", reply=spy)

    assert first.replied is True
    assert second.replied is False
    assert len(spy.calls) == 1
    assert _message_count(conn) == 1
    assert _task_count(conn) == 0
    assert_effect_log_identity(conn)


def test_missing_reply_port_is_logged_not_silently_dropped(conn, root, roster, caplog):
    """没接通道时（第 7 章之前）必须留下痕迹，⛔ 不许静默吞掉。"""
    import logging

    with caplog.at_level(logging.WARNING, logger=inbound_module.__name__):
        result = _handle(conn, root, roster, sender=OUTSIDER_USERID, reply=None)

    assert result.replied is False
    assert _message_count(conn) == 1, "回复发不出去 ⛔ 不影响归档"
    assert any("礼貌回复" in record.message for record in caplog.records)


def test_a_failing_reply_port_does_not_undo_the_archive(conn, root, roster, caplog):
    """回复通道炸了 ⇒ 记 ERROR 继续，⛔ 不许把已经归档的材料回滚掉。"""
    import logging

    def boom(thread_id, text):
        raise RuntimeError("通道断了")

    with caplog.at_level(logging.ERROR, logger=inbound_module.__name__):
        result = _handle(conn, root, roster, sender=OUTSIDER_USERID, reply=boom)

    assert result.replied is False
    assert _message_count(conn) == 1
    assert _task_count(conn) == 0
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_broken_roster_file_falls_closed_to_outsider(conn, root, tmp_path):
    """名单文件坏了 ⇒ 全部判为名单外 ⇒ 只归档不入队（第 3 章的 fail-closed 贯通到本章）。"""
    broken = tmp_path / "broken.yaml"
    broken.write_text("members: [unclosed", encoding="utf-8")
    spy = ReplySpy()

    result = _handle(conn, root, broken, sender=ADMITTED_USERID, reply=spy)

    assert result.route.admitted is False
    assert _task_count(conn) == 0
    assert len(spy.calls) == 1


# ─────────────────────────────────────────────────────────────────────────
# 结构性断言：本章 ⛔ 不实现入队
# ─────────────────────────────────────────────────────────────────────────


def test_inbound_module_never_references_the_enqueue_effect():
    """4.10 的 ⛔ 逐字："⛔ 不生成队列条目——本章只接线"。

    源码里出现 `effect_enqueue_task` 这个名字就说明第 5 章被提前写进来了。
    第 5 章要做的是在 `should_enqueue` 后面加一行，那时候把这条断言删掉，
    ⛔ 但**不是现在**。
    """
    source = pathlib.Path(inbound_module.__file__).read_text(encoding="utf-8")
    assert "effect_enqueue_task" not in source


def test_inbound_module_never_commits_by_itself():
    """提交由 `idempotent_effect` 独占（第 2 章的单一事务管理者约束）。"""
    source = pathlib.Path(inbound_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback")
    ]
    assert offenders == [], f"inbound.py 里出现了 commit/rollback，行号 {offenders}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_inbound_routing.py -v`
Expected: `ModuleNotFoundError: No module named 'tools.liaison.inbound'`

- [ ] **Step 3: 写最小实现**

创建 `tools/liaison/inbound.py`：

```python
"""一条入站消息的分支接线（tasks 4.10）。

本章只接三件事：
1. 用第 3 章的 `admit()` 判准入；
2. **两条分支都归档**——spec 原文：名单外的消息"SHALL 仍然归档
   （以便事后可查'谁在什么时候发过什么'）"；
3. 名单外回一条礼貌说明，且 ⛔ **MUST NOT 生成任何值守任务队列条目**。

⛔ **本模块不入队。** `InboundRoute.should_enqueue` 是留给第 5 章的接线位——
那一章在这个布尔值后面加一行 `effect_enqueue_task` 即可。
tests/test_inbound_routing.py::test_inbound_module_never_references_the_enqueue_effect
把"现在还没写"钉成断言。

⚠️ **礼貌回复是 at-most-once，这是一个已登记的缺口**（见 docs/tech-debt.md）：
它走注入的 reply port，不是 `effect_*`。台账已提交、回复还没发出去时进程被杀，
这条回复永久丢失（重投时归档幂等命中，不会再触发回复）。
⛔ 不要在本模块里"顺手"补一个 `effect_reply_*`——那需要一张 outbox 表，
加表是 design 层的偏离，且真实外发通道本来就在第 6／7 章。
丢的是一条告知，不丢材料、不丢待办，方向安全。
"""

from __future__ import annotations

import logging
import pathlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.liaison.archive import (
    DEFAULT_ARCHIVE_ROOT,
    ArchiveOutcome,
    InboundAttachment,
    archive_message,
)
from tools.liaison.whitelist import admit

logger = logging.getLogger(__name__)

#: 名单外发送人收到的固定回复。
#:
#: **⛔ 不透露名单里有谁**——那是不必要的个人信息披露，且会让这个内部工具
#: 变成一个可以拿来探测"谁有权限"的接口。
#: **必须带"自动发送"标识**：这条不是 AI 生成合成内容（固定模板，不落入
#: 《AI 生成合成内容标识办法》的适用范围），但收件人不能误以为它是
#: Shao Peishen 本人的回复。
POLITE_NOTICE = (
    "您好，这是 Shao Peishen 的 HR 值守助手自动发送的回复。"
    "您的消息与材料已收到并留档，但当前未在本助手的受理范围内，不会转成待办事项。"
    "如需处理，请直接联系 Shao Peishen。"
)


@dataclass(frozen=True)
class InboundRoute:
    """一条消息该怎么处置。纯数据，由 `compute_inbound_route` 算出。"""

    admitted: bool
    should_enqueue: bool
    reply_text: str | None


@dataclass(frozen=True)
class InboundResult:
    route: InboundRoute
    outcome: ArchiveOutcome
    replied: bool


def compute_inbound_route(admitted: bool) -> InboundRoute:
    """纯函数（工程铁律 2 的形状）：只由"准入与否"决定处置。

    ⛔ 不读名单文件（那是 `admit()` 的事）、不读时钟、不记日志。
    """
    if admitted:
        return InboundRoute(admitted=True, should_enqueue=True, reply_text=None)
    return InboundRoute(admitted=False, should_enqueue=False, reply_text=POLITE_NOTICE)


def handle_inbound_message(
    conn,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    received_at: str,
    msgtype: str,
    content: str = "",
    attachment: InboundAttachment | None = None,
    archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT,
    whitelist_path: pathlib.Path | None = None,
    reply: Callable[[str, str], Any] | None = None,
) -> InboundResult:
    """归档 → （名单外）礼貌回复。⛔ 本章不入队。

    顺序是**先归档、后回复**：材料落定了才告知对方"收到了但不受理"。
    反过来会出现"回复已发、材料没留住"，事后无从查证。

    回复只在 `newly_archived` 为真时发出——重投同一 `msgid` 时归档会幂等命中，
    这时候再发一遍就是骚扰。⛔ 不要改成"查台账里有没有这一行"来判断：
    那是另一次查询、另一个时刻。
    """
    route = compute_inbound_route(admit(sender_userid, whitelist_path))

    outcome = archive_message(
        conn,
        thread_id=thread_id,
        msgid=msgid,
        sender_userid=sender_userid,
        received_at=received_at,
        msgtype=msgtype,
        content=content,
        attachment=attachment,
        archive_root=archive_root,
    )

    replied = False
    if route.reply_text is not None and outcome.newly_archived:
        if reply is None:
            # 第 7 章接通道之前会走到这里。⛔ 不静默吞——"没人回我"和
            # "系统压根没打算回"是两件事，排障时必须能分辨。
            logger.warning(
                "礼貌回复未能发出：没有接入 reply 通道（第 6／7 章补）。"
                "thread_id=%s msgid=%s",
                thread_id,
                msgid,
            )
        else:
            try:
                reply(thread_id, route.reply_text)
                replied = True
            except Exception:  # noqa: BLE001
                # 回复失败 ⛔ 不许回滚归档——材料已经落定，那是本章更重要的产出。
                logger.error(
                    "礼貌回复发送失败，材料已归档、不回滚。thread_id=%s msgid=%s",
                    thread_id,
                    msgid,
                    exc_info=True,
                )

    return InboundResult(route=route, outcome=outcome, replied=replied)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_inbound_routing.py -v`
Expected: PASS（15 条）

- [ ] **Step 5: 跑全量，确认没碰红前面章节**

```bash
venv/bin/python -m pytest tools/liaison/tests/ -v
venv/bin/python -m pytest -q
```

Expected: 全绿。特别确认这四条仍绿：
`test_effect_node_to_table_matches_reality`、`test_effect_node_to_table_matches_the_insert_target_repo_wide`、`test_no_second_transaction_manager_in_source`、`test_app_does_not_import_tools`。

- [ ] **Step 6: 登记技术债（编号 commit 前一刻现取）**

在 `docs/tech-debt.md` 追加一条：

```markdown
## TD-<现取> · 值守服务的礼貌回复是 at-most-once，崩溃即丢

**现象**：`tools/liaison/inbound.py` 的礼貌回复走注入的 reply port，不是
`effect_*`、没有 outbox 行。台账已提交、回复还没发出去时进程被杀，这条回复
永久丢失——重投同一 `msgid` 时归档幂等命中，不会再触发回复。

**为什么当时这么做**：做成幂等 effect 需要一张 outbox 表；第 4 章 opener
约束 5 明确"⛔ 不改第 2 章的表结构（需要加列/加表则登记偏离并停在该点）"，
而真实外发通道本来就在第 6／7 章。

**影响面**：丢的是一条"您不在受理名单内"的告知。⛔ 不丢材料（归档已落）、
⛔ 不丢待办（名单外本来就不入队）。方向安全。

**还债动作**：第 6／7 章落真实外发通道时，把回复改成带 outbox 行的
`effect_*`（幂等键 `{thread_id}:effect_reply_notice:{msgid}`），并在
`EFFECT_NODE_TO_TABLE` 登记。届时删掉本条。
```

🔴 **编号在 `git commit` 前一刻才去 `docs/tech-debt.md` 顶部现取**，⛔ 不要提前写死——并行泳道可能同时在加 TD，预分配必撞号。撞了就取下一个可用号，⛔ 不覆盖别人那条。

- [ ] **Step 7: 回勾 WBS 与落地偏离登记**

在 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 4 章：把 4.1–4.10 十条的 `- [ ]` 改成 `- [x]`，并在该章「**验收**」行之前插入：

```markdown
> **第 4 章落地偏离登记**（run-build 收口时记）：
> - **P1（plan 决定）**：`compute_safe_filename` 与文件读写拆成 `archive.py` / `attachments.py` 两个模块。4.4 的"⛔ 禁止 UTF-8 解码"由一条只扫 `attachments.py` 的 AST 断言执行；文件名的字节截断必须 decode，那段在 `archive.py`。⛔ 不许把截断逻辑搬进 `attachments.py`——那会把这道边界拆了。
> - **P2（plan 决定）**：`thread_id` / `msgid` **只校验不改写**（不安全即 `ArchivePathError`）。归一化会把两个不同的键磨成同一个，制造与「归档覆盖」同源的碰撞。
> - **P3（plan 决定）**：一条消息**最多一个附件**（aibot 协议：一条消息一个 msgtype、一个媒体项）。`attachments_json` 仍是数组、长度 0 或 1。真出现多附件消息类型时须改 design D4 的路径形态，⛔ 不在本章预留分支。
> - **P4（已登记技术债 TD-<现取>）**：礼貌回复是 at-most-once（走注入 reply port，非 `effect_*`）。做成幂等 effect 需要加 outbox 表，触发 opener 约束 5 的"停在该点"，且真实通道在第 6／7 章。丢的是告知，不丢材料、不丢待办。
> - **P5（范围）**：`liaison-message-archive` 的「归档数据的留存期有上限」一条 **⛔ 不在本章**（第 8 章）。`tools/liaison/consistency.py` 是它的前置核对器，本章 ⛔ 不实现任何删除。
> - **P6（范围）**：`liaison-inbound-whitelist`「名单内成员的消息进入归档与队列」只完成**归档**一半；**入队**在第 5 章，接线位 = `InboundRoute.should_enqueue`。
```

- [ ] **Step 8: 提交**

```bash
git add tools/liaison/inbound.py tools/liaison/tests/test_inbound_routing.py \
        docs/tech-debt.md openspec/changes/hr-wecom-aibot-liaison/tasks.md
git commit -m "feat(liaison): 名单外只归档+礼貌回复、⛔ 不入队；回勾第 4 章 4.1-4.10 与落地偏离登记（4.10）"
```

⚠️ **并发协议**（本仓库同时有别的泳道在跑）：只 `git add` 上面逐条列出的路径，⛔ 禁止 `git add -A` / `git add .` / `git commit -a`。`git status` 里出现别人的改动是正常的，不要停、不要顺手提交。push 被拒 → `git pull --rebase --autostash origin main` 后重试，最多 3 次；⛔ 不要在 commit 前 pull。

---

## 计划编写期的提取验证结果（2026-09-08，Asia/Shanghai）

按 `spec-to-plan` SKILL.md 步骤 6 做的端到端提取验证：把本计划里**全部** `python` 代码块原样提取到 `/tmp/liaison-verify/`（`app/` + `tools/` + `pyproject.toml` 的隔离副本），用仓库根 venv（Python 3.14.6 / pytest 8.3.4）跑全量。⛔ 本章零第三方依赖，不需要另建 venv。

**基线**：`131 passed`（本章新增 98 条 + 第 2 章 `test_liaison_effects.py` 33 条）。

| 文件 | 条数 |
|---|---|
| `test_archive_paths.py` | 39 |
| `test_attachments.py` | 19 |
| `test_archive_effect.py` | 14 |
| `test_archive_consistency.py` | 11 |
| `test_inbound_routing.py` | 15 |
| `test_liaison_effects.py`（第 2 章，未新增用例） | 33 |

### 揪出并已在本计划里修掉的 2 个真实缺陷

1. **`_minimal_pdf_bytes()` 的 `b"%PDF..." % len(body)` 直接 `ValueError`。**
   PDF 的文件头就是 `%PDF`，而 `%P` 不是合法的字节格式化占位符——**整个 `test_attachments.py` 在 collect 阶段就炸**，不是一条用例红，是一整个文件收集失败。已改成字节拼接，并在 docstring 里写明为什么不用 `%`。
2. **🔴 `attachments.py` 一落地就打红第 2 章的 `test_no_second_transaction_manager_in_source`。**
   实测报文：`attachments.py::verify_archived_file 用 with open(path, 'rb'): 隐式提交` + `attachments.py::store_attachment 用 with os.fdopen(handle_fd, 'wb'): 隐式提交`。这正是 **TD-18** 预言的触发点。已按 TD-18 钉死的还法（正面白名单，⛔ 不窄化判据）折进 **Task 3 的 Step 1／2**，并实测：改完 33 条全绿，**9 条 `test_scanner_*` 全部仍绿**——`with self._conn:`、`with get_connection() as c:` 都仍然被抓。
   ⚠️ **不做这一步的后果不是"某条红了再说"**：`run-build` 的 reviewer 会看到一条与本章逻辑无关的红，最顺手的"修法"是把 `with open` 改写成 try/finally 去迁就误报——那正是 TD-18 明令要避免的方向。

### 变异验证（证明守卫真的会咬，不是摆设）

提取验证只证明"代码能跑通"。又逐个植入 5 个**方向性错误**，确认每一个都被抓：

| 植入的错误 | 结果 |
|---|---|
| 把 D3 顺序反过来（先写台账、后落材料） | **10 条红**（含 `test_store_is_called_before_the_ledger_write`、`test_source_order_puts_store_attachment_before_the_effect_call`、`test_ledger_row_is_never_written_when_the_attachment_fails_to_land`） |
| 直接写目的地、不走临时文件 + 原子 `rename` | **1 条红**（`test_store_uses_atomic_rename_not_a_direct_write`） |
| 把字节截断改成字符截断（`text[:max_bytes]`） | **3 条红**（含 `test_truncation_counts_bytes_not_characters`） |
| 在 `inbound.py` 里 import `effect_enqueue_task` | **1 条红**（`test_inbound_module_never_references_the_enqueue_effect`） |
| 在 `attachments.py` 里加一次 `data.decode("utf-8")` | **1 条红**（`test_attachments_module_never_decodes_and_never_opens_in_text_mode`） |

全部还原后回到 `131 passed`。

### 这次验证**不**能证明什么

测试与被测代码出自同一份文档、同一个作者，全绿只证明**代码可执行且内部自洽**，⛔ 不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责，这一步不是它的替代品。
另外三件事本次验证覆盖不到，`run-build` 时要留意：
- **`fsync` 的真实持久化效果**测不出来（测试只断言它被调用过、且文件与目录各一次）。真正的断电验证不在本项目的能力范围内。
- **并发写**没测。本服务是单进程单连接（design D5／D12），`store_attachment` 的"先 exists 再写"存在 TOCTOU，但在单进程下不可达。⛔ 不要因此加锁——那是给一个不存在的场景加复杂度；真要多进程了，先改 D5。
- **真实企微 `msgid` 的字符集**未知（本计划按"不含分隔符与控制字符的非空字符串"处理）。若第 7 章接通道后发现真实 `msgid` 含 `/`，`_validated_key` 会**抛异常拒绝整条消息**——这是刻意的保守方向，届时要改的是 D4 的路径形态（例如加一层编码），⛔ 不是把 `_validated_key` 放松成清洗。

---

## Self-Review

按 `writing-plans` SKILL.md 的三项自查，逐项过完：

**1. Spec 覆盖**：`liaison-message-archive` 的 6 条 Requirement——5 条有对应 Task（见上方对照表），第 6 条「归档数据的留存期有上限」**明确不在本章**（`tasks.md` 第 4 章验收原文已排除，属第 8 章）。`liaison-inbound-whitelist` 的「名单外消息只归档并礼貌回复」全部 Scenario 落在 Task 6；「名单内成员的消息进入归档与队列」只完成归档一半，入队属第 5 章。**无遗漏。**

**2. 占位符扫描**：全文无 `TBD` / `TODO` / "适当处理错误" / "类似 Task N" / "为上面写测试"。每个代码步骤都给了完整可运行的代码块，每条命令都给了预期输出。**已实跑 `grep -nE 'TBD|TODO|适当处理|类似 Task |同上，参照' <本文件>`，唯一命中的就是本行自己（本行在列举要扫的模式），生产/测试代码块与步骤正文里一条都没有。**

**3. 类型一致性**：跨 Task 的名字逐一核对过（提取验证跑通本身就是最强的机器判据）——
`compute_safe_filename(raw_name, *, max_bytes)` · `compute_archive_path(*, thread_id, msgid, received_at, filename, archive_root)` · `StoredAttachment(filename, relative_path, byte_length, sha256)` + `.as_dict()` · `store_attachment(payload, destination, *, archive_root)` · `verify_archived_file(path, *, byte_length, sha256)` · `InboundAttachment(filename, payload)` · `ArchiveOutcome(msgid, newly_archived, attachments)` · `archive_message(conn, *, thread_id, msgid, sender_userid, received_at, msgtype, content, attachment, archive_root)` · `InboundRoute(admitted, should_enqueue, reply_text)` · `InboundResult(route, outcome, replied)` · `handle_inbound_message(...)`。前后一致。

**4. 铁律 1 专项**（本项目额外要求）：本章**不新增**任何 `effect_*`，唯一的副作用写库动作是第 2 章已有的 `effect_archive_message`，幂等键 `{thread_id}:effect_archive_message:{msgid}`，台账行与 `effect_log` 行由 `idempotent_effect` 在同一事务提交。恒等不变式由 `assert_effect_log_identity(conn)` 覆盖——本章 **13 个写库场景**（`test_archive_effect.py` 8 处 + `test_inbound_routing.py` 5 处）末尾都调了它。`archive.py` / `inbound.py` 均有"⛔ 无 commit/rollback"的 AST 断言。

---

## 收尾：归档时限与还没做完的事

- `tasks.md` 第 4 章十条全部勾上**不等于**变更包可归档——第 5–8 章还没做。`openspec-archive-change` 要等整个 `hr-wecom-aibot-liaison` 的 `tasks.md` 全勾之后才跑。
- 本章交给第 5 章的**唯一接口**是 `InboundRoute.should_enqueue`。第 5 章要做的是在 `handle_inbound_message` 里这个布尔值后面加一行 `effect_enqueue_task`，并**同时删掉** `test_inbound_module_never_references_the_enqueue_effect` 这条断言（它守的是"现在还没写"，第 5 章之后就该退休）。⛔ 别的地方都不用改。
- 本章交给第 8 章的是 `consistency.verify_ledger_against_archive()`（留存清理前后各跑一次）+ `.tmp-` 前缀的孤儿临时文件需要被清理扫掉，以及 `assert_effect_log_identity` docstring 里那条**留存清理会让恒等断言因正当理由变红**的警告——那里已经写死了只允许的两种应对，⛔ 不许削弱断言。

---

## Execution Handoff

计划已存到 `docs/superpowers/plans/2026-09-09-hr-wecom-aibot-liaison-unit4-message-archive.md`。

**下一步用本项目的 `run-build` 技能执行**（它封装了 `superpowers:subagent-driven-development` + Global Constraints 检查 + WBS 回勾）：新开 CC session、**勾 worktree**（写代码）、分支名不指定。

⚠️ **`superpowers:*` 技能在本项目取不到**（本次已实测 `Unknown skill: superpowers:writing-plans`）。`run-build` 同样会取不到 `subagent-driven-development`——那时的处置是**读磁盘上的 SKILL.md 手工走协议**（`~/.claude/plugins/cache/claude-plugins-official/superpowers/6.2.0/skills/`），⛔ 不判失败。SDD 台账随 worktree 删除而消失，**收口前必须把台账内容转写进 `tasks.md` 的落地偏离登记**。
