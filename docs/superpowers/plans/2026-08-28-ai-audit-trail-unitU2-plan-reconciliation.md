# AI 留痕与外发门禁 · 交付单元 U2（`app/audit`）计划核对报告

> **For agentic workers:** REQUIRED SUB-SKILL: 本文件**不是实现计划**，正文只有一个 `### Task`（残留补档项）。若要执行它，用 superpowers:executing-plans。U2 的实现已在 main 上，⛔ 不要按 `2026-08-26-ai-audit-trail-unitU2-audit-module.md` 重新实施一遍。

**结论（先行）：U2 已于 2026-08-27 全量交付并合进 main，`tasks.md` 第 2 章 9/9 已勾。本次不出实现计划——出了就是把一个已完成的单元重做一遍。**

**Goal:** 核对 `docs/superpowers/plans/2026-08-26-ai-audit-trail-unitU2-audit-module.md`（commit fddc63d，6 Task，117KB）与 U1 落地真值、以及与 U2 实际落地成果的一致性；登记差异、残留项与需拍板项。

**Architecture:** 只读核对。证据一律取 git 真值与实跑输出，不取任何计划文档的自述。

**Tech Stack:** Python 3.14.6（`./venv`）· pytest 8.3.4 · git

---

## 零、核对起点与那条失效的前提

**任务书给的前提是「U1 刚合进 main，U2 依赖 U1 的表结构，现在可以开工」。这条前提已经过期。** 实测：

| 事实 | 证据 |
|---|---|
| U1（第 1 章）合并 | `d8f7043 2026-08-27 docs(openspec): 回勾第 1 章 6/6，登记 U1 五条落地偏离` |
| **U2（第 2 章）合并** | `c1ff33c` → `c48b212` 七笔，`0fa4d54 2026-08-27 docs(tasks): 勾选 ai-audit-trail 第2章 9/9（U2 app/audit 交付完成）` |
| 第 2 章 checkbox | `grep -c "^- \[x\] 2\." tasks.md` → **9/9** |
| 代码落地 | `app/audit/{__init__,events,sinks,recorder}.py` 共 767 行，`tests/test_audit_{events,sinks_sqlite,chain,recorder}.py` 共 1324 行 |
| U2 测试实跑 | `./venv/bin/python -m pytest tests/test_audit_*.py -q` → **98 passed in 0.19s** |
| 全量实跑 | `./venv/bin/python -m pytest tests -q` → **487 passed** |

U1 与 U2 之间隔着不到 5 小时，两者都在 2026-08-27 落地。任务书写这段话时看到的应该是 U1 刚合的那一刻的仓库状态。

**当前包内真实进度**（`tasks.md` 逐章 checkbox 实测）：

| 章 | 单元 | 状态 | plan 文件 |
|---|---|---|---|
| 1 | U1 数据层与配置位 | **6/6 ✅** | `2026-08-26-…unitU1-schema-and-config.md` |
| 2 | U2 `app/audit` | **9/9 ✅** | `2026-08-26-…unitU2-audit-module.md`（已执行完） |
| 3 | U3 留痕接线 | 0/7 | **无** |
| 4 | U4 `app/outbound` 纯函数 | 0/9 | **无**（另一条 session 正在出） |
| 5 | U5 队列与图节点接线 | 0/9 | 无 |
| 6 | U6 合规断言与 CI | 0/7 | 无 |
| 7 | U7 边界守护与文档 | 0/6 | 无 |

⚠️ **`app/outbound/` 目录尚不存在**（`ls app/outbound` → absent），所以 U4 确实未开工，那条并行 session 的前提成立。
⚠️ **真正"解锁且无人规划"的单元是 U3，不是 U2**——`delivery-units.md:24` 写 U3 依赖 U1、U2，两者现已齐备。当前可开的并行对是 **U3 ∥ U4**。见 §六。

---

## 一、核对结论一：表结构（旧计划 vs U1 实际 DDL）

**判据取 `app/storage/db.py:84-171` 的真实 DDL，不取任何计划的描述。**

| 核对点 | 旧 U2 计划的假设 | U1 实际 DDL | 结论 |
|---|---|---|---|
| `analysis_run` 列集 | `id/application_id/job_id/configured_model/response_model/system_fingerprint/prompt_version/temperature/input_hash/rubric_snapshot/raw_response/token_usage/latency_ms/created_at` 十四列 | 同上，逐列一致（`db.py:95-108`） | ✅ 成立 |
| `analysis_run` 可空性 | 业务关联列与 rubric 列全部可空 | `application_id`/`job_id`/`response_model`/`system_fingerprint`/`rubric_snapshot`/`token_usage`/`latency_ms` 均无 NOT NULL（`db.py:96-107`） | ✅ 成立 |
| **`analysis_run` 无 `rubric_version` 列** | 旧计划**已预见**并登记为偏离 2 | DDL 确实没有该列 | ✅ 预见正确，见 §二 |
| `criterion_score.evidence_ref` CHECK | 计划正文写 `trim(evidence_ref)`？ | 实际是 `trim(evidence_ref, ' ' \|\| char(9) \|\| char(10) \|\| char(13))`（`db.py:126-129`） | ✅ **对 U2 无影响**——U2 的职责是"不吞 IntegrityError"，收紧 CHECK 只让守护更容易变红，不改 U2 任何一行代码 |
| `criterion_score` 外键 | `analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id)` | 一致（`db.py:123`） | ✅ 成立 |
| `pending_approval` 唯一索引 | 旧 U2 计划**不涉及**该表 | `(thread_id, content_hash)` 两列（`db.py:167-168`，U1 偏离 2） | ➖ 与 U2 无关，是 U5 的事 |

**结论：U1 的五条落地偏离，没有一条落在 U2 的接触面上。** 偏离 1（`trim` 三参）与偏离 2、3（`pending_approval` 可空性与索引粒度）方向都是"更严"或"改 U2 不碰的表"；偏离 4（多加一个 `candidate_outbound_switch_file` 配置键）与偏离 5（开关 fail-closed 加固）都在 `app/config.py`，而 **U2 明令不 import `app.config`**（`app/audit/__init__.py:5-7`、`sinks.py:8`），有 AST 守护 `test_audit_module_imports_no_config_or_graph` 钉住。

---

## 二、核对结论二：`CANDIDATE_OUTBOUND_ENABLED` 与 U2 的关系 = 零

任务书要求核对开关的最终形态（默认 False、优先级「开关文件 > 环境变量 > 基线值」、每次调用求值无缓存、四类 fail-closed 加固）。**这四项全部核对属实**——依据是 `tasks.md:20` 起的「1.x 落地偏离登记」偏离 5 专条，其中「没有改变的东西」一段逐字保留了拍板结论，且列出六条专属回归测试名。

**但这一整块与 U2 的交集是空集**，理由是结构性的而非巧合：

1. `app/audit/` 全包禁止 import `app.config`（`__init__.py:5-7` 的模块 docstring 写明理由：绑死配置会让 U3 的注入点不再唯一）。
2. 该禁令有机器守护：`tests/test_audit_recorder.py:446 test_audit_module_imports_no_config_or_graph`，走 AST 扫真正的 import 语句，且带阳性对照 `test_import_detector_actually_detects`（`:466`）。
3. JSONL 路径由调用方传入，不由 sink 读配置（旧计划「已登记的边界」第 4 条，落地属实）。

**所以：外发总开关的形态无论怎么变，U2 都不需要改一行、也不需要改一条测试。** 这条不是"看起来不相关"，是有守护钉住的结构隔离。

> ⚠️ 任务书里那条「合规开关默认值必须有一条测试逼默认值真正参与求值」的硬要求（要求 4）——**在 U2 里无对应物，因为 U2 没有任何配置默认值**。它已在 U1 兑现：`test_candidate_outbound_is_closed_by_default` / `test_switch_file_removal_falls_back_to_baseline` / `test_env_var_is_read_every_call_not_cached_at_startup` 三条，且 U1 的偏离登记记载「把 `candidate_outbound_enabled: bool = False` 改成 `True` 后三条变红，两次结构重排后都复验过」。**这条记载我没有独立复验**（改配置默认值会动到另一条 session 正在读的 `app/config.py`），它的证据强度是"U1 实施者的自述 + 六条具名测试存在"，不是我实测的确定性。要升格成确定性，得在 U3 开工时跑一次变异验证。

---

## 三、核对结论三：旧计划 vs 实际落地的四条差异

前三条是旧计划**自己在正文里预先登记**的（`…unitU2-audit-module.md:2468-2472`），落地时按登记原样实施；第四条是 review 期新发现的，**目前只存在于 commit message 里，没有进任何登记表**。

| # | 差异 | 落地形态（真值） | 钉住它的测试 | 登记状态 |
|---|---|---|---|---|
| 1 | `AuditRecorder` 两段式，⛔ 不提供打包方法（tasks 2.8 字面是一个 `record()` 先 SQLite 后 JSONL） | `record(conn,event)` (`recorder.py:74`) / `mirror(event)` (`recorder.py:95`) 分离 | `test_record_writes_sqlite_only`、`test_mirror_writes_jsonl_only`、`test_recorder_exposes_no_packed_method` + 阳性对照 `test_packed_method_detector_actually_detects` | 旧计划已登记 |
| 2 | `rubric_version` 与 `rubric_snapshot` 合并落进 `rubric_snapshot` 一列 | `_pack_rubric()` (`sinks.py:172-184`) 存 `{"version":…,"snapshot":…}`，`read_all()` 无损拆回 | `test_rubric_version_and_snapshot_round_trip` | 旧计划已登记 |
| 3 | `AuditSink.write` 返回 `bool`；`SqliteSink` 对非 `ai_analysis` 事件返回 `False` | `SUPPORTED_EVENT_TYPES = {AI_ANALYSIS}` (`sinks.py:84`)，`sinks.py:110-115` 短路 | `test_non_analysis_events_have_no_body_in_this_sink`（参数化 OUTBOUND_BLOCKED / BACKFILL） | 旧计划已登记 |
| 4 | **`criterion_score` 的 INSERT 循环挪进主键短路的同一个 `try` 块** | `sinks.py:118-170`：两条 INSERT 同在一个 try，`except sqlite3.IntegrityError` 在 `:156` | `test_empty_evidence_ref_is_not_swallowed`（`tests/test_audit_sinks_sqlite.py:162`） | ⛔ **只在 `f6eb9b2` 的 commit message 里，未进 `tasks.md`，也未回灌旧计划** |

### 差异 4 值得单独说：旧计划里有一条自我实现的测试，是 review 抓出来的

`f6eb9b2` 的 commit message 逐字记载（**这是实施者的记述，我未独立重跑当时的失效版本**）：

> Review round 1 Important finding: `test_empty_evidence_ref_is_not_swallowed` 之前测不到自己声称守护的回归——`criterion_score` 的 CHECK 失败发生在 try/except 之外，把 except 写宽成 `except sqlite3.IntegrityError: return False` 该测试依然全绿。

我复核了旧计划正文的 Task 2 代码（`…unitU2-audit-module.md:1025-1071`），**确认 `for score in event.scores:` 循环确实写在 `except` 之后、try 块之外**。而同一份计划的 Global Constraints 又明文声称：

> Task 2 的 `test_check_constraint_failure_is_not_swallowed` 是这条的守护——把 `except sqlite3.IntegrityError: return False` 写宽一格，铁律 4 当场从"数据库强制"退回"静默放过"。

**这两句在旧计划里同时成立不了。** 断言的性质（"把 except 写宽会变红"）与代码结构（CHECK 失败根本不经过那个 except）互相矛盾——测试咬不住它声称咬住的东西。落地时由 review 抓出并修好了，可观察行为不变。

> **这是本次核对唯一发现的旧计划实质缺陷，也正是任务书要求 2 点名的那类"自我实现的测试"。它没有造成生产后果**（review 拦住了），**但它证明了一件事：计划正文里"某某测试守护某某回归"这句话本身需要被验证，而验证手段是变异——把被守护的代码改坏，看那条测试是不是真的单独变红。** 旧计划的「提取验证记录」跑了 90 passed，全绿恰恰是这个缺陷的伪装：一条咬不住的断言在正常路径上永远是绿的。U3–U7 出计划时，凡写「这条测试会在 X 回归时变红」，都必须在计划里附上变异验证的具体做法，而不是只写一句声称。

---

## 四、核对结论四：落地质量抽查（旧计划的三条分水岭判据）

旧计划把三条判据定为 reviewer 的机械判据。我逐条对着落地代码与测试核了一遍：

| 判据 | 旧计划要求 | 落地实况 | 结论 |
|---|---|---|---|
| 两段式的 AST 守护必须带阳性对照 | 「没有阳性对照，这条断言在"检查函数根本没生效"时同样是绿的」 | `test_packed_method_detector_actually_detects`（`tests/test_audit_recorder.py:103`）喂一个故意写错的源码串，断言 `== ["record_all"]` | ✅ 成立 |
| `effect_*` 禁 append JSONL 的守护必须带阳性对照，且三分支各一条 | 同上；今天恒真，阳性对照是唯一区别 | `test_effect_mirror_detector_actually_detects` 参数化三分支：`.mirror(`、`.backfill(`、裸名 `JsonlChainSink`（`:210-221`，commit `463c231` 补齐） | ✅ 成立 |
| 链的分水岭断言必须是 `broken_at == 2`，**不是** `ok is False` | 「只断言 `ok is False` 的话，一个"任何 prev_hash 缺失都算断链（含第 1 行）"的实现也会绿」 | `test_all_prev_hash_fields_stripped_breaks_at_line_two`（`tests/test_audit_chain.py:230-255`）断言 `ok is False` **且** `broken_at == 2` **且** error 含 "prev_hash"；配套 `test_line_one_may_omit_prev_hash` 与 `test_line_one_prev_hash_value_is_not_validated` 锁住豁免边界的另一侧 | ✅ 成立 |
| U2 零文件外溢（`db.py`/`config.py`/`graph/` diff 为空） | 完成判据第 3 条 | `git diff --stat c1ff33c~1 c48b212` → 8 个文件，全部是 `app/audit/*` 与 `tests/test_audit_*`，2091 行纯新增 | ✅ 成立 |

**抽查范围的边界：我核的是"旧计划自己点名的判据是否兑现"，不是"U2 是否符合 spec"。** 后者由当时的 run-build 两阶段 review 负责，本次未重做。

---

## 五、发现的两项残留（一项补档、一项待拍板）

### 残留 A（可执行）：U2 的四条落地偏离没有进 `tasks.md`

U1 在 `tasks.md:20` 建立了「1.x 落地偏离登记」这个体例，把偏离、真值、判据测试名放在**变更包内**、跟着 spec 走。U2 没有对应的「2.x 落地偏离登记」——四条差异分散在旧计划正文（前三条）与一条 commit message（第四条）里。

**为什么这不是形式主义**：U3/U5 的实施者读的是 `tasks.md`，不是三天前某份 117KB 的 plan 的第 2470 行。差异 3（`record()` 返回 `False` 有两种含义）**直接影响 U5 的接线判断**，见残留 B。

⛔ **本次未执行**：任务书明令不改 `tasks.md`（另一条 session 正在写）。落成 §七 的 Task 1。

### 残留 B（需拍板）：`record()` 返回 `False` 承载了两种不同的含义

实测两条测试断言的是同一个返回值：

- `tests/test_audit_sinks_sqlite.py:155` — `assert sink.write(_event()) is False`，含义是**"这条 run 已经写过了"**（主键冲突短路，tasks 2.2 要求的幂等）
- `tests/test_audit_sinks_sqlite.py:207` — `assert sink.write(_event(event_type=event_type)) is False`，含义是**"这类事件在这个 sink 里没有真身"**（外发事件的真身是 `pending_approval`）

两者都合理，但**折成同一个 `False` 之后，U5 的调用点分辨不出来**。U5 在 `effect_*` 里对一条 `outbound_blocked` 事件调 `recorder.record()`，拿到 `False`，无从判断是"已幂等短路"还是"这里根本不落它"。旧计划的偏离登记 3 只解释了为什么需要一个 bool，没有讨论两种 False 的合流。

**这不是 U2 的 bug**（两条路径都有测试、行为是设计出来的），**是一个会在 U5 接线时显形的接口债**。三个选项：

- **(a) 不动 U2**，在 U5 的调用点按 `event.event_type` 自行分辨（U5 本来就知道自己在写什么类型）。成本最低，代价是这条知识散在调用点。
- **(b) 让 `SqliteSink.write` 对不支持的事件类型抛异常**而不是返回 False。方向更严，但会改一条已合并单元的对外行为，且要改 `test_non_analysis_events_have_no_body_in_this_sink`。
- **(c) 返回值升格成三态枚举**（`WROTE` / `ALREADY` / `NOT_MINE`）。最清楚，改动面最大，且 U3 尚未接线，现在改比 U5 之后改便宜。

**⚠️ 需 Shao Peishen 拍板。** 我的倾向是 **(a)**——U2 已合并，改一个已交付单元的对外契约需要理由，而"调用点自己知道事件类型"这件事在 U5 里是天然成立的；但如果 U6 的对账断言要区分这两种 False，(c) 就变成必要的。**这条判断依赖 U6 6.4 的断言形状，而 6.4 尚未展开，所以我给不出确定性结论，只给倾向。**

---

## 六、下一步建议（不属本次交付，供决策）

**U2 无工可开。当前包内解锁且无人规划的单元是 U3（第 3 章，留痕接线，0/7）。**

依据：`delivery-units.md:24` 记 U3 依赖 U1、U2，两者均已合并；`delivery-units.md:232-234` 的排期图把 U3 排在 U1 之后与 U2 并列的下一环。另一条 session 在出 U4 的 plan，**U3 ∥ U4 与原方案里的 U2 ∥ U4 具备同样的并行性**——U3 碰 `app/llm/gateway.py` / `app/main.py`，U4 建全新的 `app/outbound/`，零文件重叠（`delivery-units.md:132-137` 的文件冲突表）。

⚠️ U3 的一条已知风险，出 plan 时必须正面处理（依据 `delivery-units.md:38-42` 与 `db.py:90-94` 的表注释）：**`RecorderAuditHook` 一接上 `_gateway_factory()`，M1 现有的岗位画像采集调用会立刻开始写 `analysis_run`**，而采集期没有投递、没有 rubric。U1 已把相关列全部做成可空来接这一下，但 U3 的 plan 需要有一条测试证明"采集路径写进去不炸"。

**我没有擅自去出 U3 的 plan**——任务书指定的是 U2，换标的属于改变交付物，该由你定。

---

## Global Constraints

以下条目对 §七 的 Task 1 生效，从任务书与 `CLAUDE.md` 逐字复制。

1. ⛔ **不修改 `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`**（另一条 session 正在写）——Task 1 的执行前提是那条 session 已收工且其改动已落地，执行前必须先 `git status` 确认该文件无未提交改动。
2. ⛔ **不碰** `app/graph/nodes.py`、`app/agents/intake_agent.py`（单元 F 热点）、`docs/openers/run-batch.sh`、`docs/openers/run-lanes.sh`。
3. ⛔ **不改 `app/audit/` 下任何一行代码。** U2 已合并且 98 条测试全绿；补档是文档动作，不得夹带代码改动。reviewer 判据：Task 1 的 diff 只出现 `tasks.md` 一个文件。
4. 遇到 `.git/index.lock`：先等 5 秒重试，最多 5 次；仍不行才看孤儿锁三项判据，**判据 3 用 `pgrep -x git`，⛔ 不要用 `-f`**（`-f` 会匹配到自己这条命令行）。
5. 引用事实必须标出处（commit sha / `file:line` / 实跑输出），⛔ 不许把弱证据升格成确定性——实施者自述记作自述，实测记作实测。
6. ⛔ 不 push、不开 run-build、不建 worktree。

---

## 七、残留项

### Task 1: 把 U2 的四条落地偏离补进 `tasks.md` 的「2.x 落地偏离登记」

**Files:**
- Modify: `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`（在第 2 章 9 条 checkbox 之后、`## 3.` 之前插入一节）

**Interfaces:**
- Consumes: 本报告 §三 的四条差异表；U1 的体例见 `tasks.md:20` 起的「1.x 落地偏离登记」
- Produces: `tasks.md` 中一个 `### 2.x 落地偏离登记（U2 实施，2026-08-27）` 小节，供 U3/U5/U6 的 plan 作者引用

- [ ] **Step 1: 确认另一条 session 已收工**

```bash
git status --short openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md
```

预期：**无输出**（该文件无未提交改动）。若有输出，⛔ 停止，等那条 session 收工——两条 session 同写一个文件必然丢改动。

- [ ] **Step 2: 确认插入位置**

```bash
grep -n "^- \[x\] 2\.9\|^## 3\." openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md
```

预期：两行输出，`2.9` 的行号小于 `## 3.` 的行号。新小节插在这两行之间的空行处。

- [ ] **Step 3: 插入登记小节**

在 Step 2 定位的位置插入以下内容（逐字，⛔ 不要改写措辞——判据列的测试名是机器可核的）：

```markdown
### 2.x 落地偏离登记（U2 实施，2026-08-27）

本章按 `docs/superpowers/plans/2026-08-26-ai-audit-trail-unitU2-audit-module.md` 实施，
落地时相对本文件字面有四条偏离。**前三条是该计划正文已预先登记的**（计划 §偏离登记
1–3），第四条是 review round 1 新发现。分支实测 `tests/test_audit_*.py` 98 passed，
全量 487 passed。核对报告见
`docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU2-plan-reconciliation.md`。

| # | 本文件字面 | 实际落地 | 判据（哪条测试咬住它） |
|---|---|---|---|
| 1 | 2.8 的 `record()` 一次调用内"先 SQLite 后 JSONL" | **两段式** `record(conn, event)` / `mirror(event)`，⛔ 无打包方法 | 2.8 是**顺序**要求，两段式满足它；打包会在事务回滚时留下「JSONL 有、SQLite 无」，design D1 明令这是更糟的一侧。`test_record_writes_sqlite_only`、`test_mirror_writes_jsonl_only`、`test_recorder_exposes_no_packed_method`（AST，带阳性对照 `test_packed_method_detector_actually_detects`） |
| 2 | 2.1 把 `rubric_version` 列为一等字段 | 与 `rubric_snapshot` 合并落进 `analysis_run.rubric_snapshot` 一列，形态 `{"version":…,"snapshot":…}`，`read_all()` 无损拆回 | U1 的 `analysis_run` 没有 version 列，而 U2 ⛔ 不改 U1 的表（跨单元改 `db.py` 会作废并行、且让 U1 的老库回归守护失效）。spec 要"完整快照"，版本是快照的属性。`test_rubric_version_and_snapshot_round_trip` |
| 3 | 2.2 未写 `write` 的返回类型 | `AuditSink.write` 返回 `bool`；`SqliteSink` 对非 `ai_analysis` 事件返回 `False` 而不造表 | 主键冲突短路需要一个调用方与对账都观察得到的信号。外发事件的真身是 `pending_approval`（U5 写），补录事件只存在于镜像链上。`test_non_analysis_events_have_no_body_in_this_sink`（参数化 OUTBOUND_BLOCKED / BACKFILL） |
| 4 | 计划正文 Task 2 把 `criterion_score` 的 INSERT 写在 try/except **之外** | 挪进主键短路的**同一个 try 块**（`app/audit/sinks.py:118-170`） | review round 1 Important：CHECK 失败发生在 try 之外时，把 except 写宽成 `except sqlite3.IntegrityError: return False` 那条守护**依然全绿**——测试咬不住它声称守护的回归。挪进来后窄化判据 `_is_analysis_run_pk_conflict` 才同时罩住两条语句。可观察行为不变。`test_empty_evidence_ref_is_not_swallowed`（commit `f6eb9b2`） |

**⚠️ 遗留、需 Shao Peishen 拍板（U5 接线前解决）**：`record()` 返回 `False` 承载两种不同含义——
「这条 run 已经写过」（主键短路，`tests/test_audit_sinks_sqlite.py:155`）与「这类事件在这个 sink
里没有真身」（`:207`）。U5 的调用点分辨不出这两者。三个选项与倾向见核对报告 §五 残留 B。
```

- [ ] **Step 4: 机器核对——四条判据里的测试名必须都真实存在**

```bash
for t in test_record_writes_sqlite_only test_mirror_writes_jsonl_only test_recorder_exposes_no_packed_method test_packed_method_detector_actually_detects test_rubric_version_and_snapshot_round_trip test_non_analysis_events_have_no_body_in_this_sink test_empty_evidence_ref_is_not_swallowed; do printf '%s -> ' "$t"; grep -rl "def $t" tests/ || echo MISSING; done
```

预期：**七行，每行都指向一个 `tests/test_audit_*.py` 文件，没有一行是 `MISSING`。**
出现 `MISSING` 说明登记表里写了一条不存在的测试名——那就是把弱证据写成了确定性，⛔ 必须改到对为止再提交。

- [ ] **Step 5: 确认 diff 只有一个文件**

```bash
git status --short
```

预期：只有 ` M openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 一行（Global Constraints 第 3 条）。

- [ ] **Step 6: 提交**

```bash
git add openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md
git commit -m "docs(openspec): 登记 U2 四条落地偏离，与 U1 的 1.x 同体例"
```

---

## 八、交付前自查

- [x] 核对结论有实跑证据：`98 passed` / `487 passed` / `git diff --stat c1ff33c~1 c48b212` 八文件
- [x] 每条事实标了出处（commit sha、`file:line`、命令输出），实施者自述与我的实测分开标注（§二末、§三差异 4）
- [x] 没有为了显得有产出而重写一份 U2 实现计划
- [x] 未修改 `tasks.md`、未碰 `app/graph/nodes.py` / `app/agents/intake_agent.py` / `docs/openers/run-*.sh`
- [x] Task 用 `### Task N` 三级标题
- [x] 带 Global Constraints 段
- [x] 需拍板项集中在 §五 残留 B 与 §六
