# 准入名单（hr-wecom-aibot-liaison 交付单元 3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 HR 企微值守服务落一道 **fail-closed 准入闸**——谁的消息能变成一条值守任务由一份受版本管理的 YAML 决定；这份文件缺失、不可读、解析失败、或有效条目为零时，一律折成"空名单、全部拒绝"，⛔ 绝不放行全部、⛔ 绝不沿用上一次成功加载的名单。同时让"名单里只许有 userid／中文姓名／角色说明"这条合规要求**在运行时自我执行**（多一个字段整条丢弃），而不是只靠人在 review 时看一眼。

**Architecture:**

```
tools/liaison/                      ← ⛔ 不在 sync-to-server.sh 的 SYNC_PATHS 里，结构上到不了 .51（design D10）
├── __init__.py                     ← 第 1 章产物；本章 Task 1 缺则补建
├── requirements.txt                ← 第 1 章产物；本章 Task 1 追加 PyYAML 一行
├── whitelist.py                    ← 本章唯一的实现文件
│   ├── compute_admission(sender, whitelist) -> bool     纯函数（铁律 2 的形状）
│   ├── load_whitelist(path) -> frozenset[str]           I/O 边界，fail-closed，记 ERROR
│   └── admit(sender, path) -> bool                      调用缝，第 4／5 章从这里接线
├── config/
│   ├── whitelist.yaml              ← D2 结论名单，字段只有三个
│   └── README.md                   ← 填写与变更流程（"改名单不改 .py"的承诺落在这里）
└── tests/
    └── test_whitelist.py           ← 39 条用例
```

三条支撑这套结构的判断，改动前先读：

**1. `compute_admission` 与文件读取必须拆开，否则"纯函数"是句空话。**
`tasks.md` 3.3 写的是"实现 `compute_admission`：纯函数，输入发送人标识，输出是否命中"，同一条又要求它处理"文件缺失／不可读／解析失败"。这两半直接矛盾——**读文件就不是纯函数**。本计划按铁律 2 的分层解开：`load_whitelist` 承担全部 I/O 与全部 ERROR 日志，`compute_admission` 只拿一个已加载好的 `frozenset` 做判定（不读文件、不读环境变量、不调网络、**连日志都不记**），`admit()` 把两者接起来供第 4／5 章调用。Task 3 用 AST 把纯度钉成断言：`compute_admission` 体内允许出现的调用名只有 `isinstance` 与 `strip`，谁加一行 `logger.error` 断言当场红。
⛔ 不要为了"贴合 tasks.md 3.3 的字面签名"把文件读回 `compute_admission` 里——那是把铁律 2 换成一句注释。

**2. 进程内不缓存名单。每次判定重新读文件。**
这不是性能疏忽，是 spec 的硬要求：`liaison-inbound-whitelist` 原文「MUST NOT 降级为放行全部或**放行上一次成功加载的名单**」。缓存一旦存在，"文件后来损坏了但内存里还留着旧名单"就是这条要求的直接违反，而且它是**静默**的——名单坏了，闸门却照常放人过。本服务每分钟处理个位数消息，读一次几百字节 YAML 的代价可以忽略。
附带收益：spec 的「名单变更不需要改动代码」只要求"改配置 + 重启"，无缓存实现连重启都不需要就已生效，重启这条自然满足。**本计划就此把 3.6 的二选一写死为「每次判定重读文件，重启与不重启都即时生效」**，⛔ 不实现任何缓存 + 热重载信号的方案（多一个可能与 fail-closed 冲突的状态机，收益为零）。
Task 5 的 `test_corrupting_the_file_never_reuses_the_previous_roster` 与 `test_admit_rereads_the_file_on_every_call` 专门守这条——已实测：加一个"加载失败就回退到上次成功结果"的缓存，这两条立刻失败。

**3. 合规约束靠加载器执行，不靠人眼。**
「名单不得含手机号／邮箱／身份证号」如果只写成注释和一条扫文件的测试，那么一个从别处拷来的、带 `phone:` 字段的条目仍然会被正常准入——测试是在 CI 里红，闸门在生产里照放。本实现改成**白名单式字段校验**：条目的键集合不等于 `{userid, name, role}` 就**整条丢弃**并记 ERROR。多写一个手机号的直接后果是"这个人进不来"，是一个立刻可见、方向安全的失败。
⛔ 日志只写字段**名**、绝不写字段**值**——多余字段的值恰恰可能就是不该被采集的个人信息，写进日志等于把它换个地方留存。Task 4 有断言：`"phone" in caplog.text` 且 `"13800138000" not in caplog.text`。

**Tech Stack:** Python 3.14（根 `pyproject.toml` 钉死 `>=3.14,<3.15`；本机实测 3.14.6）· 标准库（`logging` / `pathlib` / `collections.abc` / `ast` / `inspect` / `hashlib` / `re` / `os` / `textwrap`）· PyYAML 6.0.3（`yaml.safe_load`，⛔ 绝不用 `yaml.load`）· pytest 8.3.4

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」与本交付单元 opener 的五条范围约束。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *（本章不产生任何副作用，因此本章不应出现任何 `effect_*` 函数——见下方「本章的四条不做」。）*
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
   *（准入名单是**通道准入**，与候选人淘汰无关。⛔ 本章的任何拒绝逻辑都不得被复用到候选人处置路径上。）*
4. **模型全部走境内**，简历数据不出境。
5. **候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。**
6. **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。
7. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。
8. **部署约束 4**：**目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务 + 防火墙规则 + scp 推送。不要引入容器。

### 来自本交付单元 opener（逐字，五条）

1. **D2 结论逐字：白名单 = {汤丽萍, 邵培申}；聂鑫／王寒月／陈承不入；⛔ 实现阶段不重开**
2. **3.1 名单文件字段只含企微 userid、中文姓名、角色说明；⛔ 手机号／邮箱／身份证号（合规：不采集多余个人信息）**
3. **fail-closed 逐字：文件缺失／不可读／解析失败／名单为空一律折成空名单全拒；⛔ 不沿用任何硬编码默认名单**
4. **compute_admission 是纯函数（铁律 2 的形状），⛔ 不读环境变量、不调网络**
5. **3.6：名单变更不改 .py（配置热重载或重启即生效，plan 二选一并写死）**
   → **本计划写死为：每次判定重新读文件，⛔ 不做任何缓存。** 重启即生效，且不重启也已生效。理由见 Architecture 第 2 条。

### 来自 design.md（逐字，两条）

- **D10**：代码落 **`tools/liaison/`**。⛔ 不落 `app/`（产品交付物），⛔ 也不落 `scripts/`——`scripts` 与 `app` **都在** `sync-to-server.sh` 的 `SYNC_PATHS` 白名单里，放进去就会被推到 `.51`，而本服务明确永不部署 `.51`。依赖落 **`tools/liaison/requirements.txt`**，⛔ 不进根 `requirements.txt`。⛔ 不改 `sync-to-server.sh` / `deploy-server.ps1` 去"排除 tools"。
- **D5 的单向依赖**：`tools/liaison` 可以 `import app.*`，⛔ `app/` 下任何模块不得 import `tools/`。*（本章不 import `app/` 的任何东西，也不被 `app/` import。）*

### 本章的四条"不做"（避免越界写进第 4／5 章的地盘）

- ⛔ **不建数据库、不建表、不写任何 `effect_*` 函数。** 本章零副作用，只有读文件与判定。
- ⛔ **不实现归档、不实现入队、不实现礼貌回复。** spec `liaison-inbound-whitelist` 里「名单内成员的消息进入归档与队列」「名单外消息只归档并礼貌回复」两条 Requirement **不在本章验收范围内**（`tasks.md` 第 3 章验收原文已排除），它们分别属于第 5 章与第 4 章（4.10）。本章只交付被这两条调用的判定函数 `admit()`。
- ⛔ **不硬编码任何默认名单。** 参考的 Windows 侧实现用一个硬编码 `frozenset`（6 个 userid），`06-企业AI转型资产借鉴清单.md:125` 已把它标为"给的是形状不是取值"。本实现的名单只来自配置文件；代码里出现任何 userid 字面量（测试夹具除外）即违反本条。
- ⛔ **不放宽 `requires-python`**，⛔ 不把 PyYAML 加进根 `requirements.txt`。

---

## 前置状态（⚠️ controller 开工前必读）

**写本计划时（2026-09-08），第 1、2 章都还没合进 `main`**：`grep -c '^- \[x\]' openspec/changes/hr-wecom-aibot-liaison/tasks.md` = **0**（阈值 12），且 `tools/liaison/` 目录在 `main` 上**不存在**。

本计划因此被写成**不依赖第 1、2 章任何产物**：

- 本章不 import `tools.liaison` 的任何其他模块，不 import `app.storage.idempotency`，不碰 `data/liaison.db`。
- Task 1 用**幂等**的方式补建 `tools/liaison/__init__.py`、`tools/liaison/requirements.txt`、`tools/liaison/tests/__init__.py` 与根 `pyproject.toml` 的 `testpaths` 条目——**已存在就原样不动**。第 1 章先合进来也不冲突。
- ⚠️ **若第 1 章在本章执行期间被合并**，`tools/liaison/requirements.txt` 与根 `pyproject.toml` 这两个文件可能产生 git 冲突。冲突时的处置**已定**：保留第 1 章的版本，再把本章需要的 `PyYAML==6.0.3` 一行与 `tools/liaison/tests` 一条 `testpaths` 追加进去，⛔ 不要用整文件覆盖。

## ⏸ 留步：两个真实企微 userid 尚未取得

**仓库内没有任何地方记录汤丽萍与邵培申的企微 userid**（已全仓 grep，`docs/openers/0908A-*.md` 只有中文姓名）。

**处置（已定，⛔ 不需要任何人当场回答）**：出厂的 `config/whitelist.yaml` 按 D2 写全两条条目的**姓名与角色说明**，`userid` 一律留**空串**；加载器把"userid 为空或纯空白"的条目**整条丢弃**并记 ERROR。于是出厂态是**谁都不准入**——这正是 D2 那段代价不对称分析里"可逆的那一侧"：少放一个人，他的消息仍被归档、仍收到礼貌回复，补救成本 = 配置文件填一行 + 重启，且缺口**可见**。

⛔ **不要为了"先跑起来"填假 userid。** 假值让闸门看起来是配好的，实际谁都进不来，而这个失败**不可见**——没人会收到礼貌回复来提示你名单是坏的。

真实 userid 拿到后的填写流程写在 `tools/liaison/config/README.md`（Task 1 产出），**不需要改任何 `.py`**。Task 3 有一条 `test_shipped_config_admits_nobody_until_userids_are_filled_in` 把这个出厂态钉死；userid 填进去后**这条用例会失败，这是正确的信号**——届时把它改成断言两个 userid 均命中，那次改动本身就是"名单已生效"的证据。

---

## Spec Requirement → Task 对照

| `liaison-inbound-whitelist` 的 Requirement | 覆盖它的 Task |
|---|---|
| 准入判定 fail-closed（含三条 Scenario：文件不存在／格式损坏／名单为空） | Task 2、Task 4 |
| 名单配置不得含联系方式类个人信息（Scenario：配置字段受限） | Task 1（受版本管理文件的静态断言）、Task 4（运行时整条丢弃） |
| 名单变更不需要改动代码（Scenario：追加一名成员） | Task 5 |
| 名单内成员的消息进入归档与队列 | ⛔ **不在本章**（第 5 章）。本章只交付被它调用的 `admit()` —— Task 3 |
| 名单外消息只归档并礼貌回复 | ⛔ **不在本章**（第 4 章 4.10）。本章只交付被它调用的 `admit()` —— Task 3 |

`tasks.md` 第 3 章条目对照：3.1 → Task 1；3.2 → Task 1；3.3 → Task 2 + Task 3；3.4 → Task 2；3.5 → Task 1／2／3／4；3.6 → Task 5。

---

### Task 1: 名单配置文件与落点就绪

**Files:**
- Create: `tools/liaison/config/whitelist.yaml`
- Create: `tools/liaison/config/README.md`
- Create（缺则建，已存在则不动）: `tools/liaison/__init__.py`、`tools/liaison/tests/__init__.py`、`tools/liaison/requirements.txt`
- Modify（缺则加，已有则不动）: `pyproject.toml` 的 `[tool.pytest.ini_options] testpaths`
- Test: `tools/liaison/tests/test_whitelist.py`

**Interfaces:**
- Consumes: 无（本章第一个 Task）
- Produces: 配置文件路径 `tools/liaison/config/whitelist.yaml`；YAML 结构 = 顶层映射，唯一键 `members`，值为条目列表，每条恰含 `userid`（str）／`name`（str）／`role`（str）三个键。Task 2–5 全部依赖这个结构。

- [ ] **Step 1: 补建落点骨架（幂等，已存在的文件原样不动）**

```bash
mkdir -p tools/liaison/config tools/liaison/tests
[ -f tools/liaison/__init__.py ] || printf '' > tools/liaison/__init__.py
[ -f tools/liaison/tests/__init__.py ] || printf '' > tools/liaison/tests/__init__.py
[ -f tools/liaison/requirements.txt ] || cat > tools/liaison/requirements.txt <<'EOF'
# HR 企微值守服务的独立依赖清单。
# ⛔ 绝不合进根 requirements.txt——根清单会被 sync-to-server.sh 推到 .51 并由
#    deploy-server.ps1 在服务器上 pip install，而本服务明确永不部署 .51（design D10）。
EOF
grep -q '^PyYAML==' tools/liaison/requirements.txt || cat >> tools/liaison/requirements.txt <<'EOF'

# 准入名单用 yaml.safe_load 解析（第 3 章）。版本与根 venv 里已有的传递依赖对齐，
# 避免"根 venv 跑测试用 6.0.3、tools venv 跑服务用另一个版本"的版本偏斜。
PyYAML==6.0.3
EOF
```

- [ ] **Step 2: 把 `tools/liaison/tests` 接进根 `pyproject.toml` 的 testpaths（幂等）**

打开 `pyproject.toml`，把 `[tool.pytest.ini_options]` 下的

```toml
testpaths = ["tests"]
```

改成（**若已经是下面这样就什么都不做**，第 1 章可能已经改过）：

```toml
# tools/liaison/tests 也在这里，让全量 pytest 一次跑到值守工具的用例。
# ⛔ 不另起一套测试跑法。注：pyproject.toml 会被同步到 .51 而 tools/ 不会——
# 这是安全的，testpaths 条目按 glob 处理，不匹配任何路径时被静默跳过、退出码仍为 0。
testpaths = ["tests", "tools/liaison/tests"]
```

- [ ] **Step 3: 写 D2 结论名单配置文件**

Create `tools/liaison/config/whitelist.yaml`:

```yaml
# HR 企微值守服务·准入名单
#
# 名单结论来自 design.md D2，⛔ 实现阶段不重开：
#   ✅ 汤丽萍   —— HR 侧唯一已确认的 AI 专员，是材料交换的对手方
#   ✅ 邵培申   —— 工具主人，自测／发指令／验证链路都需要自己这条通道
#   ❌ 陈承     —— 已在 Windows 侧白名单（IT 域），同一人进两套系统会生成两条互不知情的任务行
#   ❌ 聂鑫     —— 群成员身份 ≠ 材料提交责任人
#   ❌ 王寒月   —— 同上
#
# 字段只允许 userid / name / role 三个。多一个字段该条**整条被丢弃**（不是忽略多余字段）。
# ⛔ 不写手机号、邮箱、身份证号或任何可直接联系到个人的信息。
#
# 改这个文件不需要改任何 .py。填写与变更流程见同目录 README.md。

members:
  # ⚠️ userid 留空 = 该条不生效（fail-closed）。真实企微 userid 尚未取得，
  #    取得后填在这里即可生效，⛔ 不要为了"先跑起来"填假值。
  - userid: ""
    name: 汤丽萍
    role: HR AI 专员，材料交换对手方

  - userid: ""
    name: 邵培申
    role: 工具主人，自测与指令验证通道
```

- [ ] **Step 4: 写名单变更流程说明**

Create `tools/liaison/config/README.md`:

```markdown
# 准入名单的填写与变更

## 改名单不需要改代码

`whitelist.yaml` 是本服务准入判定的**唯一**数据来源。加载器每次判定都重新读它，
因此改完**重启即生效，不重启也已生效**。⛔ 代码里没有任何硬编码的默认名单，
改 `.py` 不会、也不该改变谁能进。

## 字段只有三个

| 字段 | 含义 |
|---|---|
| `userid` | 企业微信内部用户标识 |
| `name` | 中文姓名（用于日志与人工核对） |
| `role` | 角色说明（用于回答"这个人为什么在名单里"） |

⛔ **不要加第四个字段**，尤其不要加手机号、邮箱、身份证号。
加载器是**白名单式**校验：条目的键集合不等于这三个，**整条丢弃**并记 ERROR ——
后果是这个人进不来，不是"多余字段被忽略"。这是刻意的：合规要求由加载器执行，不靠人眼。

## 追加一名成员

1. 在 `members` 下追加一条，三个字段齐全，`userid` 非空。
2. 提交。⛔ 不改任何 `.py`。
3. 重启服务（或什么都不做——下一条消息进来时就会读到新名单）。

⚠️ 追加成员本身是一次**范围扩大**：名单外的人只是收不到任务队列条目，
名单内的人可以往值守任务队列里写行。D2 的判据是"默认从紧"——
确有材料提交职责时按名录单条追加并留痕，⛔ 不要把群成员整批放进来。

## userid 为空怎么办

出厂配置的两条 `userid` 都是空串，因为写这份配置时**真实企微 userid 尚未取得**。
空 userid 的条目会被丢弃，所以出厂态是**谁都不准入**。
拿到真实 userid 后填进去即可；⛔ 不要填假值占位——假值让闸门看起来配好了、
实际谁都进不来，而这个失败不可见（没人会收到礼貌回复来提示你名单是坏的）。
```

- [ ] **Step 5: 写失败的测试（配置文件的静态契约）**

Create `tools/liaison/tests/test_whitelist.py`（本 Task 只写下面这一段；Task 2–5 各自往同一文件追加）:

```python
"""准入名单（tasks.md 第 3 章）的行为契约。"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CONFIG = PACKAGE_ROOT / "config" / "whitelist.yaml"

D2_ADMITTED = ["汤丽萍", "邵培申"]
D2_REJECTED = ["聂鑫", "王寒月", "陈承"]

CONTACT_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "邮箱": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
}


def test_shipped_config_exists():
    assert SHIPPED_CONFIG.is_file()


def test_shipped_config_carries_no_contact_information():
    text = SHIPPED_CONFIG.read_text(encoding="utf-8")
    for label, pattern in CONTACT_PATTERNS.items():
        assert pattern.search(text) is None, f"准入名单配置里出现了{label}"


def test_shipped_config_entries_have_exactly_three_fields():
    document = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    for entry in document["members"]:
        assert set(entry) == {"userid", "name", "role"}


def test_shipped_config_is_exactly_the_d2_roster():
    document = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    assert [entry["name"] for entry in document["members"]] == D2_ADMITTED


def test_shipped_config_excludes_the_three_d2_rejections():
    text = SHIPPED_CONFIG.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    names = {entry["name"] for entry in document["members"]}
    for rejected in D2_REJECTED:
        assert rejected not in names
```

> ⚠️ `test_shipped_config_excludes_the_three_d2_rejections` 断言的是**解析后的 `name` 集合**，
> 不是原始文本——配置文件的注释里**故意**写了这三个名字和不入名单的理由（那是资产，
> 不是违规）。⛔ 不要把它改成 `assert rejected not in text`，那会把注释一起禁掉。

- [ ] **Step 6: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -v`
Expected: 若按 Step 1–4 顺序执行，此时配置已就位、应当 **5 passed**。
若先跑 Step 5–6 再跑 Step 3–4，则 `test_shipped_config_exists` FAIL（`assert False`），其余 ERROR。
两种顺序都可以，但**必须亲眼见到过一次红**——把 `whitelist.yaml` 临时改名再跑一次即可：

```bash
mv tools/liaison/config/whitelist.yaml /tmp/wl.bak && venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q; mv /tmp/wl.bak tools/liaison/config/whitelist.yaml
```
Expected: 5 failed。恢复后重跑应 5 passed。

- [ ] **Step 7: 跑全量测试确认没打断既有用例**

Run: `venv/bin/python -m pytest -q`
Expected: 既有用例全绿，新增 5 条通过。`testpaths` 的改动让 `tools/liaison/tests` 被收集到。

- [ ] **Step 8: Commit**

```bash
git add tools/liaison/config/whitelist.yaml tools/liaison/config/README.md tools/liaison/tests/test_whitelist.py tools/liaison/__init__.py tools/liaison/tests/__init__.py tools/liaison/requirements.txt pyproject.toml
git commit -m "feat(liaison): 准入名单配置文件与 D2 结论名单（第 3 章 3.1/3.2）"
```

---

### Task 2: `load_whitelist` 的 fail-closed 加载

**Files:**
- Create: `tools/liaison/whitelist.py`
- Test: `tools/liaison/tests/test_whitelist.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `tools/liaison/config/whitelist.yaml` 与其 YAML 结构
- Produces:
  - `DEFAULT_WHITELIST_PATH: Final[Path]` —— `Path(__file__).resolve().parent / "config" / "whitelist.yaml"`
  - `ALLOWED_MEMBER_FIELDS: Final[frozenset[str]]` —— `frozenset({"userid", "name", "role"})`
  - `load_whitelist(path: Path = DEFAULT_WHITELIST_PATH) -> frozenset[str]` —— 任何失败返回 `frozenset()`，绝不抛异常
  - `_read_roster(path: Path) -> frozenset[str]` —— 私有，按类型分支捕获预期失败
  - `_validated_userid(entry, index, path) -> str | None` —— Task 4 填充其字段校验，本 Task 先写空壳外的 userid 校验
  - 模块级 `logger = logging.getLogger(__name__)` —— 日志名为 `tools.liaison.whitelist`，测试按 `"whitelist" in record.name` 过滤

- [ ] **Step 1: 写失败的测试（四种 fail-closed 形态 + 不沿用旧名单 + 不抛异常）**

追加到 `tools/liaison/tests/test_whitelist.py`。先把文件顶部的 import 段补齐成：

```python
"""准入名单（tasks.md 第 3 章）的行为契约。"""

from __future__ import annotations

import ast
import hashlib
import inspect
import logging
import os
import re
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.liaison import whitelist as whitelist_module
from tools.liaison.whitelist import admit, compute_admission, load_whitelist

PACKAGE_ROOT = Path(whitelist_module.__file__).resolve().parent
SHIPPED_CONFIG = PACKAGE_ROOT / "config" / "whitelist.yaml"
```

（`PACKAGE_ROOT` 从模块的 `__file__` 取而不是从测试文件的 `parents[1]` 取，是为了让 Task 5
的"全部 `.py` 指纹"与被测模块的真实落点绑定，而不是与测试文件的相对位置绑定。）

再追加这些辅助函数（Task 3–5 都会用到，放在常量之后、用例之前）：

```python
def write_roster(path: Path, members: list[dict[str, object]]) -> Path:
    """用 safe_dump 写名单，避免手写 YAML 的引号／缩进误差混进用例。"""
    path.write_text(
        yaml.safe_dump({"members": members}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def source_fingerprint() -> str:
    """tools/liaison 下全部 .py 的内容指纹，用于证明"名单变更没改代码"。"""
    digest = hashlib.sha256()
    for py in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        digest.update(py.relative_to(PACKAGE_ROOT).as_posix().encode("utf-8"))
        digest.update(py.read_bytes())
    return digest.hexdigest()


def called_names(func) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def error_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR and "whitelist" in r.name]
```

然后追加本 Task 的用例：

```python
def test_missing_file_yields_empty_whitelist(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(tmp_path / "absent.yaml") == frozenset()
    assert error_records(caplog)


def test_unparsable_file_yields_empty_whitelist(tmp_path, caplog):
    broken = tmp_path / "whitelist.yaml"
    broken.write_text("members:\n  - userid: [unclosed\n", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(broken) == frozenset()
    assert error_records(caplog)


@pytest.mark.parametrize(
    "content", ["", "members: []\n", "members:\n", "{}\n", "[]\n", "just a string\n"]
)
def test_empty_or_shapeless_roster_yields_empty_whitelist(tmp_path, caplog, content):
    path = tmp_path / "whitelist.yaml"
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert error_records(caplog)


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: -1)() == 0, reason="root 绕过文件权限，无法构造不可读文件"
)
def test_unreadable_file_yields_empty_whitelist(tmp_path, caplog):
    blocked = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    blocked.chmod(0o000)
    try:
        with caplog.at_level(logging.ERROR):
            assert load_whitelist(blocked) == frozenset()
        assert error_records(caplog)
    finally:
        blocked.chmod(0o600)


def test_corrupting_the_file_never_reuses_the_previous_roster(tmp_path):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    assert load_whitelist(path) == frozenset({"TangLiPing"})

    path.write_text("members:\n  - userid: [unclosed\n", encoding="utf-8")
    assert load_whitelist(path) == frozenset()
    assert admit("TangLiPing", path) is False


def test_unexpected_exception_is_swallowed_into_empty_whitelist(tmp_path, monkeypatch, caplog):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )

    def explode(_raw):
        raise RecursionError("PyYAML 在畸形输入上可能抛出非 YAMLError")

    monkeypatch.setattr(whitelist_module.yaml, "safe_load", explode)
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert error_records(caplog)


@pytest.mark.parametrize(
    "path_arg",
    [Path("/nonexistent/deeply/absent.yaml"), Path("/"), Path("/dev/null"), Path("")],
)
def test_admit_never_raises_on_hostile_paths(path_arg):
    assert admit("TangLiPing", path_arg) is False
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q`
Expected: collection ERROR —— `ModuleNotFoundError: No module named 'tools.liaison.whitelist'`

- [ ] **Step 3: 写实现**

Create `tools/liaison/whitelist.py`:

```python
"""HR 企微值守服务的准入名单：fail-closed 判定。

三条结构性约定，改动前先读：

1. **判定路径不抛异常给调用方。** 任何失败——文件缺失、不可读、解析失败、结构不符、
   名单为零——都折成"空名单"并让全部发送人未命中，同时记 ERROR 级日志。
   一个准入闸在配置损坏时放行，比它根本不存在更危险。
2. **进程内不缓存名单。** 每次判定重新读文件。这不是性能疏忽，是 spec 的硬要求
   （"MUST NOT 降级为放行上一次成功加载的名单"）——缓存一旦存在，文件损坏后
   沿用旧名单就是这条要求的直接违反。本服务每分钟只处理个位数消息，读一次文件的
   代价可以忽略。附带收益：名单变更重启即生效，且不需要重启也已生效。
3. **`compute_admission` 是纯函数**（工程铁律 2 的形状）：不读文件、不读环境变量、
   不调网络、不记日志。所有 I/O 与日志都在 `load_whitelist` 一侧。
   `admit()` 是把两者接起来的调用缝，第 4／5 章从这里接线。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import yaml

logger = logging.getLogger(__name__)

DEFAULT_WHITELIST_PATH: Final[Path] = (
    Path(__file__).resolve().parent / "config" / "whitelist.yaml"
)

# 名单条目只允许这三个字段。多一个字段整条丢弃，不是忽略多余字段——
# 让"⛔ 不含手机号／邮箱／身份证号"这条合规要求在运行时自我执行，
# 而不是只靠人在 review 时看一眼配置文件。
ALLOWED_MEMBER_FIELDS: Final[frozenset[str]] = frozenset({"userid", "name", "role"})


def compute_admission(sender_userid: Any, whitelist: frozenset[str]) -> bool:
    """纯函数：发送人标识是否命中已加载的名单。

    ⛔ 本函数不得新增任何 I/O、环境变量读取、网络调用或日志——
    `test_compute_admission_is_pure` 用 AST 把这条钉成断言。
    """
    if not isinstance(sender_userid, str):
        return False
    candidate = sender_userid.strip()
    if not candidate:
        return False
    return candidate in whitelist


def load_whitelist(path: Path = DEFAULT_WHITELIST_PATH) -> frozenset[str]:
    """读名单文件，返回可准入的 userid 集合。任何失败一律返回空集合。"""
    try:
        return _read_roster(path)
    except Exception:  # noqa: BLE001
        # 兜底带：_read_roster 已按类型分支捕获了预期失败。这里接住的是未预期的
        # 异常（例如 PyYAML 在畸形输入上抛出的非 YAMLError）。⛔ 不允许它逃到
        # 调用方——spec 要求"任何判定路径上的失败结果都必须是未命中"。
        # exc_info=True 保证它不会变成一次静默吞异常。
        logger.error("准入名单加载出现未预期异常，按空名单全拒：path=%s", path, exc_info=True)
        return frozenset()


def admit(sender_userid: Any, path: Path = DEFAULT_WHITELIST_PATH) -> bool:
    """调用缝：加载名单 + 判定。每次调用重新读文件（见模块 docstring 第 2 条）。"""
    return compute_admission(sender_userid, load_whitelist(path))


def _read_roster(path: Path) -> frozenset[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("准入名单不可读，按空名单全拒：path=%s err=%s", path, exc)
        return frozenset()

    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.error(
            "准入名单解析失败，按空名单全拒（⛔ 不沿用任何此前加载过的名单）：path=%s err=%s",
            path,
            exc,
        )
        return frozenset()

    if not isinstance(document, Mapping):
        logger.error("准入名单顶层不是映射，按空名单全拒：path=%s", path)
        return frozenset()

    members = document.get("members")
    if not isinstance(members, list):
        logger.error("准入名单缺 members 列表或类型不对，按空名单全拒：path=%s", path)
        return frozenset()

    admitted = {
        userid
        for index, entry in enumerate(members)
        if (userid := _validated_userid(entry, index, path)) is not None
    }

    if not admitted:
        logger.error("准入名单为零条有效条目，全部发送人判为未命中：path=%s", path)

    return frozenset(admitted)


def _validated_userid(entry: Any, index: int, path: Path) -> str | None:
    """校验单条名单条目。不合格返回 None（整条丢弃），并记 ERROR。

    ⛔ 日志只写字段**名**，绝不写字段**值**——多余字段的值恰恰可能就是
    手机号／邮箱这类不该被采集的个人信息，写进日志等于把它换个地方留存。
    """
    if not isinstance(entry, Mapping):
        logger.error("准入名单第 %d 条不是映射，整条丢弃：path=%s", index, path)
        return None

    userid = entry.get("userid")
    if not isinstance(userid, str) or not userid.strip():
        logger.error(
            "准入名单第 %d 条 userid 为空或非字符串，整条丢弃（该成员不会被准入）：path=%s",
            index,
            path,
        )
        return None

    return userid.strip()
```

> ⚠️ `_validated_userid` 在本 Task 里**只做 userid 校验**，字段白名单／必填校验由 Task 4 补上。
> ⛔ 不要在本 Task 提前写 Task 4 的分支——那会让 Task 4 的测试一开始就绿，TDD 的红失效。

- [ ] **Step 4: 跑测试确认它通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q`
Expected: **20 passed**（Task 1 的 5 条 + 本 Task 的 15 条 —— 6 条独立用例，其中 `test_empty_or_shapeless_roster_yields_empty_whitelist` 展开 6 个变体、`test_admit_never_raises_on_hostile_paths` 展开 4 个变体）

- [ ] **Step 5: 变异校验——确认 fail-closed 断言不是空的**

临时给 `load_whitelist` 加一个"失败就回退到上次成功结果"的缓存，确认测试变红：

```bash
cp tools/liaison/whitelist.py /tmp/whitelist.bak.py
python - <<'EOF'
import pathlib
p = pathlib.Path('tools/liaison/whitelist.py'); s = p.read_text(encoding='utf-8')
s = s.replace('        return _read_roster(path)',
              '        r = _read_roster(path)\n'
              '        if r: _MUTANT_CACHE[path] = r\n'
              '        return r or _MUTANT_CACHE.get(path, frozenset())', 1)
s = s.replace('def load_whitelist(', '_MUTANT_CACHE = {}\n\n\ndef load_whitelist(', 1)
p.write_text(s, encoding='utf-8')
EOF
venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q
cp /tmp/whitelist.bak.py tools/liaison/whitelist.py && rm /tmp/whitelist.bak.py
```
Expected: 变异版 **`test_corrupting_the_file_never_reuses_the_previous_roster` FAILED**（计划编写期已实测）。恢复后重跑全绿。
⚠️ 这一步是必做的，不是可选的：fail-closed 的测试最容易写成"永远是空集合所以永远通过"。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/whitelist.py tools/liaison/tests/test_whitelist.py
git commit -m "feat(liaison): 准入名单 fail-closed 加载，四种失败形态一律折成空名单（第 3 章 3.3/3.4）"
```

---

### Task 3: `compute_admission` 纯函数与调用缝

**Files:**
- Modify: `tools/liaison/whitelist.py`（本 Task 无需改动实现，`compute_admission` / `admit` 已在 Task 2 落地）
- Test: `tools/liaison/tests/test_whitelist.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `compute_admission(sender_userid, whitelist) -> bool`、`load_whitelist(path) -> frozenset[str]`、`admit(sender_userid, path) -> bool`
- Produces: `admit()` 被确立为第 4／5 章唯一的调用入口。第 4 章 4.10（名单外只归档）与第 5 章（名单内入队）都调 `admit(sender_userid)`，⛔ 不直接调 `compute_admission`（那样每个调用点都要自己管加载与失败处理，fail-closed 会被拆散到 N 处）。

- [ ] **Step 1: 写失败的测试（命中／未命中／畸形输入／纯度／出厂态）**

追加到 `tools/liaison/tests/test_whitelist.py`:

```python
def test_compute_admission_hits_a_member():
    assert compute_admission("TangLiPing", frozenset({"TangLiPing", "ShaoPeishen"})) is True


def test_compute_admission_misses_a_non_member():
    assert compute_admission("NieXin", frozenset({"TangLiPing"})) is False


@pytest.mark.parametrize("sender", [None, "", "   ", 123, b"TangLiPing", ["TangLiPing"]])
def test_compute_admission_rejects_malformed_sender(sender):
    assert compute_admission(sender, frozenset({"TangLiPing"})) is False


def test_compute_admission_is_pure():
    """铁律 2：compute_* 是无副作用纯函数。新增 I/O 或日志会让这条断言失败。"""
    assert called_names(compute_admission) <= {"isinstance", "strip"}


def test_compute_admission_ignores_environment(monkeypatch):
    monkeypatch.setenv("HR_LIAISON_WHITELIST", "NieXin")
    monkeypatch.setenv("HR_LIAISON_WHITELIST_PATH", "/tmp/anything.yaml")
    assert compute_admission("NieXin", frozenset({"TangLiPing"})) is False


def test_admit_hits_a_member_from_file(tmp_path):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    assert admit("TangLiPing", path) is True
    assert admit("ShaoPeishen", path) is True
    assert admit("NieXin", path) is False


def test_blank_userid_entry_is_dropped(tmp_path, caplog):
    """出厂配置的两条 userid 为空 ⇒ 谁都不准入，这是刻意的 fail-closed 出厂态。"""
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "", "name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "   ", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert error_records(caplog)


def test_shipped_config_admits_nobody_until_userids_are_filled_in():
    """⏸ 真实企微 userid 尚未取得，出厂态谁都不准入。

    userid 填进去之后这条会失败——**这是正确的信号**，届时把它改成
    断言两个 userid 均命中，那次改动本身就是"名单已生效"的证据。
    """
    assert load_whitelist(SHIPPED_CONFIG) == frozenset()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -k "compute_admission or admit_hits or blank_userid or admits_nobody" -q`
Expected: 若 Task 2 的实现已就位，多数会直接通过。**必须亲眼见到一次红**——把 `compute_admission` 的 `return candidate in whitelist` 临时改成 `return True` 再跑：

```bash
cp tools/liaison/whitelist.py /tmp/whitelist.bak.py
sed -i '' 's/    return candidate in whitelist/    return True/' tools/liaison/whitelist.py
venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q
cp /tmp/whitelist.bak.py tools/liaison/whitelist.py && rm /tmp/whitelist.bak.py
```
Expected: `test_compute_admission_misses_a_non_member`、`test_compute_admission_rejects_malformed_sender`（部分变体）、`test_compute_admission_ignores_environment`、`test_admit_hits_a_member_from_file`、`test_shipped_config_admits_nobody_until_userids_are_filled_in` 等多条 FAILED。恢复后全绿。

- [ ] **Step 3: 变异校验——确认纯度断言不是空的**

```bash
cp tools/liaison/whitelist.py /tmp/whitelist.bak.py
python - <<'EOF'
import pathlib
p = pathlib.Path('tools/liaison/whitelist.py'); s = p.read_text(encoding='utf-8')
s = s.replace('    if not isinstance(sender_userid, str):\n        return False\n',
              '    logger.error("mutant")\n    if not isinstance(sender_userid, str):\n        return False\n', 1)
p.write_text(s, encoding='utf-8')
EOF
venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py::test_compute_admission_is_pure -q
cp /tmp/whitelist.bak.py tools/liaison/whitelist.py && rm /tmp/whitelist.bak.py
```
Expected: **FAILED**（计划编写期已实测：`called_names` 会收到 `{"error", "isinstance", "strip"}`，不是 `{"isinstance", "strip"}` 的子集）。恢复后重跑全绿。

- [ ] **Step 4: 跑全量确认没打断既有用例**

Run: `venv/bin/python -m pytest -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add tools/liaison/tests/test_whitelist.py
git commit -m "test(liaison): compute_admission 纯度与命中判定断言（第 3 章 3.3/3.5）"
```

---

### Task 4: 字段白名单校验——多余字段整条丢弃

**Files:**
- Modify: `tools/liaison/whitelist.py`（补全 `_validated_userid` 的字段校验分支）
- Test: `tools/liaison/tests/test_whitelist.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `_validated_userid(entry, index, path) -> str | None`、`ALLOWED_MEMBER_FIELDS`
- Produces: 条目级契约——键集合必须**恰好**等于 `{"userid", "name", "role"}`。多一个键或少一个键都整条丢弃。这条契约是「名单配置不得含联系方式类个人信息」的运行时执行点。

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_whitelist.py`:

```python
def test_entry_with_a_forbidden_field_is_dropped_entirely(tmp_path, caplog):
    """多余字段 ⇒ 整条丢弃，不是"忽略多余字段"。

    同时断言日志只写字段**名**、不写字段**值**——多余字段的值恰恰可能就是
    不该被采集的个人信息，写进日志等于把它换个地方留存。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {
                "userid": "TangLiPing",
                "name": "汤丽萍",
                "role": "HR AI 专员",
                "phone": "13800138000",
            },
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"ShaoPeishen"})
    assert "phone" in caplog.text
    assert "13800138000" not in caplog.text


def test_entry_missing_a_required_field_is_dropped(tmp_path, caplog):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "TangLiPing", "name": "汤丽萍"},
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"ShaoPeishen"})


def test_non_mapping_entry_is_dropped(tmp_path, caplog):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        ["TangLiPing", {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"}],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"ShaoPeishen"})


def test_yaml_python_tags_are_not_constructed(tmp_path):
    """⛔ 绝不用 yaml.load。名单文件受版本管理，但它是"配置"这一类的输入，
    用能构造任意 Python 对象的加载器是无谓的暴露面。"""
    path = tmp_path / "whitelist.yaml"
    path.write_text("members: !!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
    assert load_whitelist(path) == frozenset()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -k "forbidden_field or missing_a_required" -q`
Expected: FAILED —— `test_entry_with_a_forbidden_field_is_dropped_entirely` 得到 `frozenset({'TangLiPing', 'ShaoPeishen'})`（Task 2 的 `_validated_userid` 还没有字段校验，带 `phone` 的那条会被正常准入）；`test_entry_missing_a_required_field_is_dropped` 同理。

- [ ] **Step 3: 写实现——在 `_validated_userid` 里插入字段白名单校验**

Modify `tools/liaison/whitelist.py`：把 `_validated_userid` 里"不是映射"检查之后、"取 userid"之前的位置，插入下面两段（其余部分不动，`entry.get("userid")` 相应改成 `entry["userid"]`，因为必填校验已经保证了键存在）：

```python
    keys = set(entry.keys())
    extra = keys - ALLOWED_MEMBER_FIELDS
    if extra:
        logger.error(
            "准入名单第 %d 条含不允许的字段 %s，整条丢弃（只允许 %s；⛔ 不采集手机号／邮箱／身份证号）：path=%s",
            index,
            sorted(str(key) for key in extra),
            sorted(ALLOWED_MEMBER_FIELDS),
            path,
        )
        return None

    missing = ALLOWED_MEMBER_FIELDS - keys
    if missing:
        logger.error(
            "准入名单第 %d 条缺字段 %s，整条丢弃：path=%s", index, sorted(missing), path
        )
        return None

    userid = entry["userid"]
```

改完后 `_validated_userid` 的完整形态应当是：

```python
def _validated_userid(entry: Any, index: int, path: Path) -> str | None:
    """校验单条名单条目。不合格返回 None（整条丢弃），并记 ERROR。

    ⛔ 日志只写字段**名**，绝不写字段**值**——多余字段的值恰恰可能就是
    手机号／邮箱这类不该被采集的个人信息，写进日志等于把它换个地方留存。
    """
    if not isinstance(entry, Mapping):
        logger.error("准入名单第 %d 条不是映射，整条丢弃：path=%s", index, path)
        return None

    keys = set(entry.keys())
    extra = keys - ALLOWED_MEMBER_FIELDS
    if extra:
        logger.error(
            "准入名单第 %d 条含不允许的字段 %s，整条丢弃（只允许 %s；⛔ 不采集手机号／邮箱／身份证号）：path=%s",
            index,
            sorted(str(key) for key in extra),
            sorted(ALLOWED_MEMBER_FIELDS),
            path,
        )
        return None

    missing = ALLOWED_MEMBER_FIELDS - keys
    if missing:
        logger.error(
            "准入名单第 %d 条缺字段 %s，整条丢弃：path=%s", index, sorted(missing), path
        )
        return None

    userid = entry["userid"]
    if not isinstance(userid, str) or not userid.strip():
        logger.error(
            "准入名单第 %d 条 userid 为空或非字符串，整条丢弃（该成员不会被准入）：path=%s",
            index,
            path,
        )
        return None

    return userid.strip()
```

- [ ] **Step 4: 跑测试确认它通过**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q`
Expected: 全绿（计划编写期在 `/tmp` 提取验证：**39 passed**）。

- [ ] **Step 5: 确认 `safe_load` 那条断言不是空的**

Run:
```bash
venv/bin/python -c "
import yaml
try:
    yaml.safe_load(\"members: !!python/object/apply:os.system ['echo pwned']\")
    print('NO ERROR —— 断言是空的，停下来查')
except yaml.YAMLError as e:
    print('YAMLError OK:', type(e).__name__)
"
```
Expected: `YAMLError OK: ConstructorError`（计划编写期已实测）。若打印的是"NO ERROR"，说明 `safe_load` 行为与预期不符，**停下来登记**，⛔ 不要把这条测试删掉了事。

- [ ] **Step 6: Commit**

```bash
git add tools/liaison/whitelist.py tools/liaison/tests/test_whitelist.py
git commit -m "feat(liaison): 名单条目字段白名单校验，多余字段整条丢弃（第 3 章 3.1 合规执行点）"
```

---

### Task 5: 名单变更不改代码

**Files:**
- Test: `tools/liaison/tests/test_whitelist.py`（追加）
- Modify: 无实现改动。本 Task 交付的是**不变式**，不是新功能。

**Interfaces:**
- Consumes: Task 2 的 `admit(sender_userid, path)`、Task 2 Step 1 的 `write_roster` / `source_fingerprint`
- Produces: 两条守护 Architecture 第 2 条（不缓存）与 spec「名单变更不需要改动代码」的不变式。第 4／5／7 章若日后为性能加缓存，这两条会挡住。

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_whitelist.py`:

```python
def test_appending_a_member_takes_effect_without_touching_any_py_file(tmp_path):
    """spec Scenario「追加一名成员」：新成员开始命中，且未修改任何源代码文件。

    ⚠️ 这里用的是虚构的测试标识，⛔ 不要改成聂鑫／王寒月／陈承——
    那三位不入名单是 design.md D2 的结论，测试里出现会误导 reviewer。
    """
    base = [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}]
    path = write_roster(tmp_path / "whitelist.yaml", base)

    fingerprint_before = source_fingerprint()
    assert admit("TestOnlyAppendedMember", path) is False

    write_roster(
        path,
        base
        + [
            {
                "userid": "TestOnlyAppendedMember",
                "name": "测试用追加条目",
                "role": "仅本用例使用，⛔ 不是 D2 名单成员",
            }
        ],
    )

    assert admit("TestOnlyAppendedMember", path) is True
    assert source_fingerprint() == fingerprint_before


def test_admit_rereads_the_file_on_every_call(tmp_path):
    """无缓存不变式：名单文件消失后，上一次命中的人立刻不再命中。

    ⛔ 不要为了性能给 load_whitelist 加缓存——spec 明文禁止
    "放行上一次成功加载的名单"，缓存与 fail-closed 直接冲突。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    assert admit("TangLiPing", path) is True
    path.unlink()
    assert admit("TangLiPing", path) is False
```

- [ ] **Step 2: 跑测试确认它通过，并用变异确认它不是空的**

Run: `venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -k "appending_a_member or rereads_the_file" -q`
Expected: 2 passed。

变异校验（复用 Task 2 Step 5 的缓存变异脚本）：
```bash
cp tools/liaison/whitelist.py /tmp/whitelist.bak.py
python - <<'EOF'
import pathlib
p = pathlib.Path('tools/liaison/whitelist.py'); s = p.read_text(encoding='utf-8')
s = s.replace('        return _read_roster(path)',
              '        r = _read_roster(path)\n'
              '        if r: _MUTANT_CACHE[path] = r\n'
              '        return r or _MUTANT_CACHE.get(path, frozenset())', 1)
s = s.replace('def load_whitelist(', '_MUTANT_CACHE = {}\n\n\ndef load_whitelist(', 1)
p.write_text(s, encoding='utf-8')
EOF
venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q
cp /tmp/whitelist.bak.py tools/liaison/whitelist.py && rm /tmp/whitelist.bak.py
```
Expected: **`test_admit_rereads_the_file_on_every_call` 与 `test_corrupting_the_file_never_reuses_the_previous_roster` 双双 FAILED**（计划编写期已实测：2 failed, 37 passed）。恢复后全绿。

- [ ] **Step 3: 跑全量**

Run: `venv/bin/python -m pytest -q`
Expected: 全绿。

- [ ] **Step 4: 自查——本章验收判据**

```bash
# ① 用例数
venv/bin/python -m pytest tools/liaison/tests/test_whitelist.py -q 2>&1 | tail -2
# ② 代码里没有任何硬编码 userid 字面量（测试夹具不算，只查实现文件）
grep -nE '"[A-Za-z]{4,}"' tools/liaison/whitelist.py | grep -viE 'userid|name|role|utf-8|members|whitelist|config|yaml'
# ③ 没有 effect_ 函数（本章零副作用）
grep -c 'def effect_' tools/liaison/whitelist.py
# ④ 没有 yaml.load（只许 safe_load）
grep -n 'yaml\.load' tools/liaison/whitelist.py
# ⑤ 依赖没有漏进根 requirements.txt
grep -in 'yaml' requirements.txt
```
Expected: ① 39 passed；② 无输出；③ `0`；④ 无输出；⑤ 无输出。

- [ ] **Step 5: 回勾 `tasks.md` 第 3 章并 Commit**

把 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 3 章的 3.1–3.6 六个 checkbox 勾上（`- [ ]` → `- [x]`）。
⛔ **只勾第 3 章的六条**，其他章节一个都不要动——并发下别的泳道可能正在改同一文件。

```bash
git add tools/liaison/tests/test_whitelist.py openspec/changes/hr-wecom-aibot-liaison/tasks.md
git commit -m "test(liaison): 名单变更不改代码与无缓存不变式，回勾第 3 章 WBS（3.6）"
```

---

## 计划编写期的提取验证结果（2026-09-08）

按 `spec-to-plan` skill 第 6 步做了端到端提取验证，**不是**在本仓库跑的（本章代码尚未落地），而是把本计划里的全部代码块原样提取到 `/tmp/wl3/` 后用本仓库的 `venv/bin/python`（Python 3.14.6、pytest 8.3.4、PyYAML 6.0.3）执行：

| 项 | 结果 |
|---|---|
| 提取后首次全量 | **39 passed in 0.05s**，零修复 |
| 变异 A：`yaml.safe_load` 是否真的挡住 `!!python/object/apply` | ✅ 抛 `ConstructorError`（`YAMLError` 子类），断言非空 |
| 变异 B：给 `compute_admission` 加一行 `logger.error` | ✅ `test_compute_admission_is_pure` FAILED |
| 变异 C：给 `load_whitelist` 加"失败回退到上次成功结果"的缓存 | ✅ `test_corrupting_the_file_never_reuses_the_previous_roster` 与 `test_admit_rereads_the_file_on_every_call` 双双 FAILED（2 failed, 37 passed） |
| 恢复后复跑 | **39 passed** |

**边界（skill 原文）**：测试与被测代码出自同一份文档、同一个作者，全通只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责。上面三个变异校验是为了排除"断言是空的"这一类自洽陷阱，**不能替代 review**。

⚠️ 提取验证是在 `/tmp/wl3` 这个扁平目录里跑的，`tools/liaison/tests/__init__.py` 与根 `pyproject.toml` 的 `testpaths` 接线**没有**在那里验证到（Task 1 Step 2、Step 7 负责在真仓库里验证）。

## 收尾：归档时限

`tasks.md` 第 3 章六个 checkbox 全部勾上**不等于**变更包可归档——本变更包还有第 1、2、4–8 章。`openspec-archive-change` 要等**整包**勾满再跑（`CLAUDE.md`「归档时限」的判据是变更包 `tasks.md` **全部**勾选）。⛔ 本章执行完不要触发归档。
