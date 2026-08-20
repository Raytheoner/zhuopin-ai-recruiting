# M1 采集质量修复 · 交付单元 C（前端可点选选项）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让业务经理在 Web 通道里能**点**而不是只能**编**——把单元 A 已经端到端透传过来的 `payload.questions[].options` 渲染成可点选控件，点选结果原样拼成该轮回复文本提交给**既有**的 `POST /api/jobs/{id}/reply`。**不改任何 API 契约、不碰任何 Python 生产代码。**

**Architecture:** 单文件、无构建、原生 DOM。`renderMessage` 的 `question` 分支从"把 `questions_text` 整段贴进一个气泡"改为"按 `questions[]` 逐条渲染问题块"，每块 = 问题文本行 +（有档位时）AI 建议标识 + 一组 checkbox chip。发送时从**当前轮**的问题块里读出被勾选的档位，拼成多行文本，与自由文本框的内容合并成一条 `message` 走既有接口。整个改动是**纯客户端的**：服务端一个字节都不动，因此单元 C 与单元 B（全后端）零文件重叠、可真并行。

**Tech Stack:** 原生 DOM 单文件前端（`app/web/static/index.html`，无框架、无构建、无 npm）· 服务端侧只读不写：FastAPI 0.115.6 / pydantic 2.13.4 · 测试 Python 3.14.6（`./venv`）+ pytest 8.3.4

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-19 版）与 `openspec/changes/m1-intake-quality-fixes/delivery-units.md` §5 **逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这两条的关系是"不得触碰"**：C 不新增、不修改、不重命名任何 `effect_*` 节点，也不新增任何写库路径。点选提交复用既有的 `POST /reply` → `graph.invoke` → `effect_persist_draft` 这条已经带幂等键的链路，一个字节都不改。reviewer 判据：本单元的 diff 里**不出现** `app/graph/`、`app/storage/`、`app/agents/` 下的任何文件。

### 部署约束

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。

> 对应 tasks 4.4。本单元**不新增任何 fetch 调用点**（点选复用既有的 `api/jobs/${jobId}/reply`），但新增的 CSS 类名、选择器字符串、提示文案同样受 `test_index_html_has_no_absolute_paths` 的全字面量扫描约束——任何以 `/` 开头的新字符串字面量都会让它失败。

### 合规红线

- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。

> 对应 tasks 4.3、`proposal.md`「合规影响说明」第 2 条：**系统给出的档位选项是 AI 生成内容，UI 上 MUST 标明是"建议选项"而非既定要求。** reviewer 判据：标识与选项**在同一个代码分支里创建**，不存在"先渲染选项、后补标识"的中间态；`options` 为空时既不渲染选项控件、也不渲染孤立的标识。

- **AI 不得代替业务经理做决定**（`proposal.md`「合规影响说明」第 3 条逐字）：兜底档位只能作为候选项呈现，用户未明确选定前不得进入 `profile_patch`——这是"AI 只做推荐不做决定"红线在采集端的对应约束，且必须有测试覆盖。

> 对应 spec 的 `Requirement: 候选档位不得代替用户做决定`。**在前端的落地是一条可机械检查的硬规则：任何 checkbox 一律不得带 `checked` 默认值。** 预勾选等于系统替用户做了选择——用户直接点发送就把 AI 建议写进了画像。reviewer 判据：`index.html` 里不出现 `checked` 这个属性名，也不出现任何 `.checked = true` 的赋值。

### 跨单元接口约定（`delivery-units.md` §5，逐字）

2. **C 的点选提交不改 API 契约** —— 否则失去 B ∥ C 的并行，理由见 §2.C

> 落地形态：把选中的档位文本原样拼成该轮回复文本，POST 到既有的 `/api/jobs/{id}/reply`。**不许给 `ReplyRequest` 加 `selected_options` 字段**——那会碰 `app/web/server.py`，单元 C 与 B/D 立刻从并行变串行。本约定由 Task 2 的 `test_reply_api_contract_has_no_selected_options` 机械锁死。**该测试将来若失败，是一次设计对话，不是一个可以删掉的测试。**

5. **每个单元开工前必须 rebase 到最新 main** —— `app/agents/intake_agent.py` 与 `app/graph/nodes.py` 被 B/D/E/F 四个单元连续改动，是本批最热的两个文件

> 对 C 而言这两个热文件都不在触碰面内，但**测试要在最新 main 上跑**：单元 B 合并后 `options` 才真的有值，rebase 之后本单元的手工验证才是在真实数据上做的。

### 明确不适用（reviewer 不必在本单元追这几条）

- 铁律 3（AI 评分持久化）、铁律 4（`evidence_ref` 非空）：本单元不写 `criterion_score`，代码库中亦无该表。
- 铁律 5（`temperature=0` / 模型版本锁定）：本单元不发起任何 LLM 调用、不改 `SYSTEM_PROMPT`、不动 `prompt_version`（升 `intake-v4` 是单元 B 的事）。
- 铁律 6（企微回调先落库）、铁律 7（`langgraph >= 1.0.10`）：本单元不接企微通道、不动依赖版本。
- 合规红线「模型全部走境内」「禁止人脸/表情分析」「绝不用历史录用结果做监督信号」：本单元不涉及。
- 部署约束 2/3/4/5：本单元不改端口、不动鉴权中间件、不引入容器、不处理真实简历。

---

## 交付单元边界

**本单元 = `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 4 章（4.1–4.5），共 5 项。**

对应 `specs/intake-guided-options/spec.md` 的 `Requirement: 结构化追问与可选项作答` 在 **Web 通道**的落地，以及 `Requirement: 候选档位不得代替用户做决定` 的前端一半。

### 触碰面（硬边界）

| 文件 | 性质 | 谁还会碰它 |
|---|---|---|
| `app/web/static/index.html` | 生产代码，**本单元唯一的生产文件** | D（6.6/6.7 警示块）、E（5.4 重问视觉区分）——**都排在 C 之后**，串行，无并发冲突 |
| `tests/test_static_frontend.py` | 测试，本单元独占 | 无。B 的测试面是 `test_ecu_knowledge` / `test_intake_agent` / `test_web_api` / `test_graph_nodes`，零重叠 |

**这两个文件之外一律不得出现在本单元的 diff 里。** 这不是洁癖：`delivery-units.md` §2.C 写明「B ∥ C 成立的全部前提」就是 C 只碰 `index.html`。碰一下 `app/web/server.py`，正在并行跑的单元 B 立刻变成串行等待。

### 不依赖单元 B 的代码

B 未合并时 `payload.questions[].options` **基本恒为空**（模型今天不会主动给档位，`normalize_question_payload()` 对 `.51` 历史裸字符串行归一化后 `options` 恒为 `[]`）。本单元的渲染分支必须**自然退化成今天的纯文本**：

- 不崩（不能对 `undefined.length` 取值）
- 不渲染空控件（不能出现一个空的选项行、或一条孤零零的"以下为 AI 建议选项"提示）
- 不改变今天的发送行为（没有勾选时，发出去的 `message` 与今天逐字相同）

B 合并后，**同一段前端代码自动开始有档位可渲染**，不需要再改 C。

### 本单元不做的事

| 不做 | 属于谁 |
|---|---|
| 判定 `is_reask`、重问的富视觉区分 | 单元 E（tasks 5.4）。C 只**读** `q.is_reask` 并保持 A 已有的文本前缀不回退 |
| 填充 `options` 的内容、模糊回复兜底 | 单元 B（tasks 3.1–3.8） |
| 中文字段名映射、确认前缺口警示块 | 单元 D（tasks 6.4–6.7） |
| 点选来源例外（`source_quote` 标记） | 单元 F（tasks 7.4）。见下方「给单元 F 的接口说明」 |
| 给 `ReplyRequest` 加字段、改任何 Python 生产代码 | **谁都不做**，§5 约定 2 |

### 给单元 F 的接口说明（7.4，写给以后的人）

本单元拼出的回复文本形如：

```
该岗位的功能安全等级要求是什么？：ASIL-D、无要求
我们暂时还没定量产时间
```

- **被选中的档位文本逐字出现在该轮用户原话里**，所以 7.3 的子串判定天然命中，**7.4 的"点选来源例外"大概率不必单独实现**（`delivery-units.md` §2.C 的判断在本形态下成立）。
- **但有一处残留风险，F 必须知道**：每行会带上**问题原文**作为前缀（否则一轮问了 2-3 个问题时，模型不知道 `ASIL-D` 是在答哪一个）。如果某个问题的文本自身逐字包含了某个档位值（例如问题写成"功能安全要到 ASIL-D 吗？"），那么即使用户**没有**勾选，该值也会出现在用户这一轮的原话里，从而被 7.3 误判为"有来源"。
- **F 若要消除这一点**，做法是把 7.3 的子串搜索范围限制在用户原话里**去掉问题前缀之后**的部分，而不是回头改 C 的拼装格式（改格式会牺牲多问题轮次的可归属性）。这条留给 F 决策，C 不预先实现。

---

## Requirement → Task 覆盖矩阵

`specs/intake-guided-options/spec.md` 的 4 条 `### Requirement:` 全部列出。**本单元覆盖的指到本计划的 Task；其余指到 `tasks.md` 的章节**（属单元 B）。

| Requirement | Scenario | 落点 | 本单元? |
|---|---|---|---|
| 结构化追问与可选项作答 | 追问带可点选选项 | **Task 1** | ✅ |
| 结构化追问与可选项作答 | 点选即可作答 | **Task 2**（前端拼装）+ Task 3 手工验证 | ✅ |
| 结构化追问与可选项作答 | 选项之外的答案 | **Task 2** | ✅ |
| 结构化追问与可选项作答 | 无法给出有意义选项的问题 | **Task 1**（`options` 为空只渲染自由文本） | ✅ |
| 候选档位不得代替用户做决定 | 未选定不入画像 | **Task 1**（禁止任何默认勾选）+ 第 3 章 3.7（后端一半） | ⚠️ 部分 |
| 候选档位不得代替用户做决定 | 选定后才入画像 | **Task 2** + Task 3 手工验证 | ⚠️ 部分 |
| 模糊回复与反问的兜底档位 | 全部 3 条 | 第 3 章（3.3–3.8），单元 B | ❌ |
| 零产出轮不消耗追问预算 | 全部 3 条 | 第 3 章（3.9–3.11），单元 B | ❌ |

**两处 ⚠️ 的准确含义**：「候选档位不得代替用户做决定」在前端的一半 = 不预勾选、不自动提交；在后端的一半 = 用户回"你决定吧"时不把档位写进 `profile_patch`，那是单元 B 的 3.7。归档 `m1-intake-quality-fixes` 前两半都必须到位。

### tasks.md 第 4 章逐项落点

| tasks | 内容 | Task |
|---|---|---|
| 4.1 | 按 `options` 渲染可点选控件（原生 DOM），保留自由文本；`options` 为空时不渲染空控件 | **Task 1** |
| 4.2 | 点选结果构造该轮回复文本并提交，不要求用户复制粘贴或改写问题文本 | **Task 2** |
| 4.3 | 选项区标明"以下为 AI 建议选项" | **Task 1** |
| 4.4 | 继续走相对路径，验证挂在 `root_path` 子路径下仍正常 | **Task 3** |
| 4.5 | 测试：点选提交（无文字）能推进；只写自由文本不点选也能推进 | **Task 2**（自动化能覆盖的部分）+ **Task 3**（手工路径，真实验收） |

---

## ⚠️ 本单元的验收弱点（如实写明，不粉饰）

**这是六个交付单元里"可独立测试"成色最弱的一个。** 必须在计划里说清，否则 review 会拿"测试全绿"当成"功能可用"。

### 弱在哪里

1. **本仓库没有 JS 测试运行器**，`tests/test_static_frontend.py` 目前只能对 `index.html` 的**文本内容**做字符串断言。它能证明"某段代码还在文件里"，**不能证明"点一下会发生什么"**。
2. 本单元的核心行为——DOM 事件、勾选状态读取、回复文本拼装——**全部发生在浏览器里，没有一行被自动化测试真正执行过**。
3. 因此："`pytest` 全绿"对本单元的含义仅仅是**没有把别的东西弄坏**，不是**新功能可用**。

### 本单元里哪些断言是真的强

不是所有测试都弱。下面三条是**真断言**，reviewer 应当据此判断，而不是据字符串断言：

| 测试 | 强在哪 |
|---|---|
| `test_reply_api_contract_has_no_selected_options`（Task 2） | 直接内省 `ReplyRequest.model_fields`。§5 约定 2 被破坏时它必然失败，与前端怎么写无关 |
| `test_reask_prefix_stays_in_sync_with_backend`（Task 1） | 从 `app.agents.intake_question` 导入 `_REASK_PREFIX` 常量去比对 `index.html`。后端改了前缀而前端没跟，它必然失败——这把"跨语言常量重复"这个真实风险变成了可检查的不变式 |
| `test_served_html_under_root_path_keeps_option_rendering`（Task 3） | 走 `_render_index()` 真实渲染路径，断言子路径挂载后选项渲染代码与 `<base href>` 都在。这是部署约束 1 在本单元的实际覆盖 |

### 真实验收在哪里

**在 Task 3 的手工验证清单上**，对应 `tasks.md` 8.4「用一个新 job 跑通整条路径」。Task 3 给出一份**逐条可判定**的清单——点哪里、看到什么算通过、什么情况算失败——并要求执行者把结果逐条回填。**清单没有逐条回填结果，本单元不算完成。**

### 考虑过并明确否决的替代

- **引入 Node 测试运行器**（本机 `node v26.4.0` 可用，`node --test` 无需 npm 依赖）：否决。要跑 JS 测试就得先把脚本从单文件 HTML 里抽出来，等于给一个明确定位为"原生 DOM、无框架、无构建"的前端引入构建步骤；而且 `.51` 是 Windows 服务器、没有 Node，测试栈会与部署栈分叉。**这是一次工具链形态变更，属决策人事项，不是可以在单元 C 里顺手做掉的。** 若日后确实要做，应单开一个变更包提案，不要塞进本单元。
- **引入 Playwright / Selenium 做端到端**：同上，且量级更大（浏览器二进制、CI runner）。
- **把渲染逻辑搬到后端生成 HTML**：会碰 `app/web/server.py`，直接违反 §5 约定 2，B ∥ C 当场失效。

---

## File Structure

**修改（本单元全部触碰面，共 2 个文件）**

| 文件 | 改动 |
|---|---|
| `app/web/static/index.html` | `<style>` 加 5 条选项相关规则；`<script>` 新增 `appendNode` / `questionOptions` / `renderQuestionBlock` / `collectSelections`，改写 `renderMessage` 的 `question` 分支与 `send-btn` 的 click handler |
| `tests/test_static_frontend.py` | 新增 5 个用例；既有 2 个用例保持不变 |

**不新建任何文件。**

---

## 开工前置

- [ ] `git pull --rebase origin main`（§5 约定 5）
- [ ] `./venv/bin/python -m pytest -q` 全绿，记下基线用例数（开工时为 **170**）
- [ ] 确认 `git status` 干净；本单元只允许 `git add app/web/static/index.html tests/test_static_frontend.py`

---

### Task 1: 按 `options` 渲染可点选控件与 AI 建议标识（tasks 4.1 / 4.3）

**对应 spec Scenario**：「追问带可点选选项」「无法给出有意义选项的问题」「未选定不入画像」（前端一半）

本 Task 结束时的可观察状态：追问带 `options` 时界面上出现一排可勾选的档位 chip 与"AI 建议选项"标识；`options` 为空时界面与今天逐字相同。**发送行为本 Task 不改**（勾选还不会进入回复文本，那是 Task 2）——这是刻意的中间态，保证本 Task 可以独立 review。

#### Step 1.1 — `<style>` 增加选项控件样式

在 `app/web/static/index.html` 中，把：

```css
  .turn { margin-bottom: 12px; }
```

替换为：

```css
  /* pre-wrap：questions_text 兜底分支里是多行文本，不加这条换行会被折叠成一行
     （这是改动前就存在的显示缺陷，本单元按问题逐条渲染后顺带修掉）。 */
  .turn { margin-bottom: 12px; white-space: pre-wrap; }
  .qblock { margin-bottom: 10px; }
  .qblock + .qblock { padding-top: 10px; border-top: 1px dashed #e9ecef; }
  /* 标识文案的样式刻意做得可读而不是"小字免责声明"：《AI 生成合成内容标识
     办法》要的是让人看见，不是让人看不见。 */
  .ai-hint { font-size: 13px; color: #6c757d; margin: 6px 0 6px; }
  .opts { display: flex; flex-wrap: wrap; gap: 8px; }
  .opt { display: inline-flex; align-items: center; gap: 6px; border: 1px solid #adb5bd; border-radius: 16px; padding: 4px 12px; cursor: pointer; font-size: 14px; }
  .opt input { margin: 0; }
  .turn.done .opt { opacity: 0.45; cursor: default; }
```

#### Step 1.2 — 拆出 `appendNode`，让 `appendTurn` 复用它

把：

```js
    function appendTurn(role, text) {
      const chat = document.getElementById("chat");
      const div = document.createElement("div");
      div.className = "turn " + role;
      div.textContent = text;
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    }
```

替换为：

```js
    // 追问要往气泡里塞 DOM 节点（选项控件），不能再只走 textContent。
    // 拆成"建气泡"与"填文本"两步，纯文本的调用点行为逐字不变。
    function appendNode(role) {
      const chat = document.getElementById("chat");
      const div = document.createElement("div");
      div.className = "turn " + role;
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
      return div;
    }

    function appendTurn(role, text) {
      appendNode(role).textContent = text;
    }
```

#### Step 1.3 — 新增常量、`questionOptions()` 与 `renderQuestionBlock()`

把这一段（含注释）：

```js
    // payload.questions 现在是结构化问题对象数组（question_id / text / field /
    // options / allow_free_text / is_reask）。**本章只渲染文本**：可点选控件与
    //「以下为 AI 建议选项」标识属第 4 章（后者是《AI 生成合成内容标识办法》
    // 的要求，不能先渲染选项、后补标识）。
    // 兜一层裸字符串：升级前写进 outbox 的历史行里 questions 是字符串数组。
    function questionText(q) {
      return typeof q === "string" ? q : (q && q.text) || "";
    }
```

替换为：

```js
    // 与后端 app/agents/intake_question.py 的 _REASK_PREFIX 逐字一致。
    // 这是一处刻意的跨语言常量重复（前端无构建、拿不到后端常量），
    // 由 tests/test_static_frontend.py 的 test_reask_prefix_stays_in_sync_with_backend
    // 机械锁死：后端改了这个前缀而这里没跟，测试必然失败。不要就地改字面量。
    const REASK_PREFIX = "（这个你刚才没答）";

    // 《AI 生成合成内容标识办法》（2025-09-01 施行）+ proposal.md「合规影响说明」：
    // 档位是 AI 生成内容，UI 上必须标明是"建议选项"而非既定要求。
    const AI_OPTIONS_HINT = "以下为 AI 建议选项，不是既定要求；可直接点选，也可自行填写";

    // payload.questions 是结构化问题对象数组（question_id / text / field /
    // options / allow_free_text / is_reask）。
    // 兜一层裸字符串：升级前写进 outbox 的历史行里 questions 是字符串数组。
    function questionText(q) {
      return typeof q === "string" ? q : (q && q.text) || "";
    }

    // 三种情况一律返回 []，渲染分支自然退化成今天的纯文本：
    //   1) 历史裸字符串（.51 现网 2026-08-18 之前写的 outbox 行）
    //   2) 单元 B 合并前——模型基本不会主动给档位
    //   3) 目标字段没有可枚举档位（spec:「无法给出有意义选项的问题」）
    // 顺带过滤掉空串与非字符串项：渲染一个没有文字的 chip 比不渲染更糟。
    function questionOptions(q) {
      if (!q || typeof q === "string") return [];
      const options = q.options;
      if (!Array.isArray(options)) return [];
      return options.filter((o) => typeof o === "string" && o.trim() !== "");
    }

    function renderQuestionBlock(container, q) {
      const block = document.createElement("div");
      block.className = "qblock";
      // 拼回复文本时要用问题原文，且不带重问前缀——前缀是系统给用户看的提示，
      // 不该跑进用户这一轮的原话里（会污染第 7 章的来源子串判定）。
      block.dataset.qtext = questionText(q);

      const line = document.createElement("div");
      // 重问前缀与后端 render_questions_text() 保持逐字一致：用户看到的问题文本
      // 必须与写进 conversation history 的那一份相同。判定 is_reask 属第 5 章
      // （tasks 5.4），这里只读不写，也不做更强的视觉区分。
      line.textContent = (q && q.is_reask ? REASK_PREFIX : "") + questionText(q);
      block.appendChild(line);

      const options = questionOptions(q);
      if (options.length > 0) {
        // 标识与选项在同一个分支里创建：不存在"先渲染选项、后补标识"的中间态。
        const hint = document.createElement("div");
        hint.className = "ai-hint";
        hint.textContent = AI_OPTIONS_HINT;
        block.appendChild(hint);

        const group = document.createElement("div");
        group.className = "opts";
        options.forEach((opt) => {
          const label = document.createElement("label");
          label.className = "opt";
          const box = document.createElement("input");
          box.type = "checkbox";
          box.value = opt;
          // ⛔ 绝不设 box.checked = true。预勾选 = 系统替业务经理做了选择，
          // 用户直接点发送就把 AI 建议写进了画像——正是「候选档位不得代替
          // 用户做决定」这条红线要挡的事。
          // checkbox 而非单选：spec 的「点选即可作答」明确要求"点选了两个
          // 选项并提交"这条路径成立。
          label.appendChild(box);
          label.appendChild(document.createTextNode(opt));
          group.appendChild(label);
        });
        block.appendChild(group);
      }
      // options 为空时到此为止：不渲染空的选项控件，也不渲染孤立的 AI 标识
      // （tasks 4.1 + spec:「界面只渲染自由文本输入，不渲染空的选项控件」）。
      // 自由文本输入是页面上那个常驻的 textarea，任何分支下都在。

      container.appendChild(block);
      return block;
    }
```

#### Step 1.4 — 改写 `renderMessage` 的 `question` 分支

把：

```js
      if (message.type === "question") {
        const questions = message.payload.questions || [];
        const text =
          message.payload.questions_text || questions.map(questionText).join("\n");
        appendTurn("assistant", text);
        document.getElementById("confirm-btn").style.display = "none";
      } else if (message.type === "confirmation_prompt") {
```

替换为：

```js
      if (message.type === "question") {
        const questions = message.payload.questions || [];
        if (questions.length === 0) {
          // 极端降级：payload 里连问题数组都没有时退回整段文本，
          // 绝不渲染一个空白气泡。
          appendTurn("assistant", message.payload.questions_text || "");
          activeQuestions = null;
        } else {
          const turn = appendNode("assistant");
          questions.forEach((q) => renderQuestionBlock(turn, q));
          // 记住"本轮"的问题块。只有它里面的勾选会被 Task 2 的发送逻辑读取——
          // 上一轮的块在提交后置灰，否则用户点了两轮之前的档位，拼出来的
          // 回复文本会对不上当前问题。
          activeQuestions = turn;
        }
        document.getElementById("confirm-btn").style.display = "none";
      } else if (message.type === "confirmation_prompt") {
```

同时把顶部的：

```js
    let jobId = null;
```

替换为：

```js
    let jobId = null;
    // 当前这一轮的问题块容器。Task 2 的发送逻辑从这里读勾选结果。
    let activeQuestions = null;
```

#### Step 1.5 — 测试

在 `tests/test_static_frontend.py` **末尾追加**：

```python
def test_options_render_with_ai_disclosure_and_degrade_to_plain_text():
    """
    tasks 4.1 / 4.3。**弱断言**——本仓库没有 JS 测试运行器，这里只能证明
    "这几段代码还在文件里"，证明不了"点一下会发生什么"。真正的验收是
    Task 3 的手工验证清单（对应 tasks 8.4）。

    尽管如此，这几条仍然值得写：它们锁住的是"被人顺手改回去"这一类回退，
    而这类回退在单文件前端里既容易发生、又不会有任何其它信号。
    """
    # 有档位 → 渲染 checkbox 控件
    assert 'box.type = "checkbox"' in INDEX_HTML
    # 档位为空 → 走不进渲染分支（既不渲染控件也不渲染孤立标识）
    assert "if (options.length > 0)" in INDEX_HTML
    # 三种"没有 options"的输入都要归一到 []，不能对 undefined 取 .length
    assert "function questionOptions" in INDEX_HTML
    assert "if (!Array.isArray(options)) return [];" in INDEX_HTML
    # 历史 outbox 行里 questions 是裸字符串，前端也要兜一层
    assert 'typeof q === "string"' in INDEX_HTML


def test_ai_generated_options_carry_disclosure_label():
    """
    《AI 生成合成内容标识办法》（2025-09-01 施行）+ proposal.md「合规影响说明」：
    档位是 AI 生成内容，UI 上 MUST 标明是"建议选项"而非既定要求。

    断言标识文案存在，且它与选项控件在同一个分支里创建——标识出现在
    `if (options.length > 0)` 之后、`.opts` 容器创建之前，不存在"先渲染选项、
    后补标识"的中间态。
    """
    assert "AI_OPTIONS_HINT" in INDEX_HTML
    assert "建议选项" in INDEX_HTML
    assert "不是既定要求" in INDEX_HTML

    branch = INDEX_HTML.split("if (options.length > 0)", 1)
    assert len(branch) == 2, "选项渲染的条件分支不见了，标识与选项的绑定关系失效"
    body = branch[1]
    hint_at = body.find("AI_OPTIONS_HINT")
    opts_at = body.find('"opts"')
    assert hint_at != -1 and opts_at != -1
    assert hint_at < opts_at, "AI 建议标识必须先于选项控件创建，不允许后补"


def test_no_option_is_pre_selected():
    """
    合规红线「AI 不得代替业务经理做决定」在前端的机械判据：
    任何档位都不得默认勾选。预勾选等于系统替用户做了选择——用户直接点发送，
    AI 的建议就进了画像。

    对应 spec 的 Requirement:「候选档位不得代替用户做决定」/ Scenario:「未选定不入画像」。
    """
    assert ".checked = true" not in INDEX_HTML
    assert ".checked=true" not in INDEX_HTML
    assert "checked=" not in INDEX_HTML  # HTML 属性形式的预勾选


def test_reask_prefix_stays_in_sync_with_backend():
    """
    **强断言**（区别于本文件里其它几条字符串弱断言）。

    重问前缀在前后端各有一份字面量：后端 app/agents/intake_question.py 的
    _REASK_PREFIX 负责写进 conversation history，前端负责显示给用户。前端无构建、
    拿不到后端常量，重复不可避免——但"重复了就会漂移"是可以被机械挡住的。

    后端改了前缀而前端没跟上时，用户看到的问题文本会与系统记下的那一份不一致，
    而这个不一致**没有任何其它信号**（不报错、不失败，只是悄悄对不上）。
    """
    from app.agents.intake_question import _REASK_PREFIX

    assert _REASK_PREFIX in INDEX_HTML, (
        f"后端重问前缀是 {_REASK_PREFIX!r}，index.html 里没有这个字面量——"
        "两边已经漂移。改前端的 REASK_PREFIX 常量与后端对齐，不要改本测试。"
    )
```

#### Step 1.6 — 验证

```bash
./venv/bin/python -m pytest tests/test_static_frontend.py -q
```

预期输出末行：`6 passed`（原有 2 条 + 新增 4 条）。

```bash
./venv/bin/python -m pytest -q
```

预期输出末行：`174 passed`（基线 170 + 4）。**出现任何 failed 即本 Task 未完成**，尤其注意 `test_index_html_has_no_absolute_paths`——它会扫描本 Task 新增的每一个字符串字面量，任何以 `/` 开头的新字面量都会让它失败（部署约束 1）。

---

### Task 2: 点选结果拼成回复文本并走既有接口提交（tasks 4.2 / 4.5）

**对应 spec Scenario**：「点选即可作答」「选项之外的答案」「选定后才入画像」

本 Task 的**唯一硬约束**：`delivery-units.md` §5 约定 2 —— **不改 API 契约**。POST 出去的仍然只有 `{message}`，被选中的档位以逐字原文出现在用户这一轮的原话里。

#### Step 2.1 — 新增 `collectSelections()` 与 `freezeActiveQuestions()`

在 `app/web/static/index.html` 的 `renderMessage` 函数**之后**、`send-btn` 的事件监听**之前**插入：

```js
    // 把本轮点选拼成该轮回复文本。**刻意不改 API 契约**（delivery-units.md
    // §5 约定 2）：POST 出去的仍然只有 {message}，被选中的档位以逐字原文出现在
    // 用户这一轮的原话里。给 ReplyRequest 加 selected_options 会碰到
    // app/web/server.py，单元 C 与并行进行的单元 B/D 立刻从并行变串行。
    //
    // 附带好处：档位文本逐字出现在用户原话里，第 7 章 7.3 的来源子串判定天然
    // 命中，7.4 的"点选来源例外"大概率不必单独实现。
    function collectSelections() {
      if (!activeQuestions) return [];
      const lines = [];
      activeQuestions.querySelectorAll(".qblock").forEach((block) => {
        const picked = Array.from(block.querySelectorAll("input[type=checkbox]"))
          .filter((box) => box.checked)
          .map((box) => box.value);
        if (picked.length > 0) {
          // 带上问题原文：一轮问 2-3 个问题时，光丢回一个 "ASIL-D" 模型不知道
          // 在答哪一条。这个选择的代价（问题文本自身逐字含某个档位值时，会让
          // 该值即使未被勾选也出现在用户原话里，污染第 7 章的子串判定）已写进
          // 本计划的「给单元 F 的接口说明」，由 F 决定是否收窄搜索范围。
          lines.push(block.dataset.qtext + "：" + picked.join("、"));
        }
      });
      return lines;
    }

    // 提交后置灰上一轮的选项：再点一次拼出来的文本会对不上当前问题，
    // 而用户看不出这个错位——他只会觉得"我明明选了，系统怎么答非所问"。
    // done 这个类挂在 assistant 气泡（.turn）上，对应样式 .turn.done .opt。
    function freezeActiveQuestions() {
      if (!activeQuestions) return;
      activeQuestions.classList.add("done");
      activeQuestions.querySelectorAll("input[type=checkbox]").forEach((box) => {
        box.disabled = true;
      });
      activeQuestions = null;
    }
```

#### Step 2.2 — 改写发送逻辑

把：

```js
    document.getElementById("send-btn").addEventListener("click", async () => {
      const input = document.getElementById("input");
      const text = input.value.trim();
      if (!text) return;
      appendTurn("user", text);
      input.value = "";

      const url = jobId ? `api/jobs/${jobId}/reply` : "api/jobs";
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
```

替换为：

```js
    document.getElementById("send-btn").addEventListener("click", async () => {
      const input = document.getElementById("input");
      const typed = input.value.trim();
      // 三条路径都要成立（spec 的三条 Scenario）：
      //   点选、一个字没打        → 能提交（「点选即可作答」）
      //   只打字、一个都没点      → 能提交，不因"未点选"被拒（「选项之外的答案」）
      //   既没点也没打            → 什么都不做，与改动前 if (!text) return 一致
      // 用户**不需要**复制粘贴或改写系统给出的问题文本才能回答——这是三份
      // pilot 反馈里业务经理原话抱怨的那一点（tasks 4.2）。
      const parts = collectSelections();
      if (typed) parts.push(typed);
      const message = parts.join("\n");
      if (!message) return;

      input.value = "";
      freezeActiveQuestions();
      // 聊天区显示的，就是实际发出去的那一段文本，一字不差——用户要能核对
      // 系统替他拼了什么。
      appendTurn("user", message);

      const url = jobId ? `api/jobs/${jobId}/reply` : "api/jobs";
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // 请求体逐字不变：{message: "..."}。这是 B ∥ C 并行成立的全部前提。
        body: JSON.stringify({ message: message }),
      });
```

#### Step 2.3 — 测试

在 `tests/test_static_frontend.py` **末尾追加**：

```python
def test_reply_api_contract_has_no_selected_options():
    """
    **强断言**（本单元最有价值的一条自动化测试，与前端怎么写完全无关）。

    delivery-units.md §5 跨单元接口约定 2：「C 的点选提交不改 API 契约」。
    点选形态一旦改成请求体新增 selected_options 字段，就会碰 app/web/server.py 的
    ReplyRequest，单元 C 与并行进行的 B/D 从并行变串行——而这个代价在代码评审里
    看不出来（改动本身很小、很自然），只会表现为"另一条分支莫名其妙冲突了"。

    ⚠️ 这条测试将来若失败，是一次设计对话，不是一个可以删掉的测试。
    要给采集接口加字段，先回去看 delivery-units.md §2.C 与 §5，确认没有并行分支
    正在等这两个文件。
    """
    from app.web.server import CreateJobRequest, ReplyRequest

    assert set(ReplyRequest.model_fields) == {"message"}
    assert set(CreateJobRequest.model_fields) == {"message"}


def test_selection_and_free_text_compose_one_message():
    """
    tasks 4.2 / 4.5。**弱断言**——拼装逻辑在浏览器里跑，这里只能证明代码还在。
    真正的验收是 Task 3 手工验证清单的第 3、4、5 条。

    锁住三件事：
      1. 勾选结果与自由文本合并成**一条** message（而不是两次请求、或新字段）
      2. 空 + 空才 return，不再是改动前"文本框空就 return"（那会让纯点选提交失效）
      3. 请求体仍然是 {message: ...}
    """
    assert "function collectSelections" in INDEX_HTML
    assert "if (typed) parts.push(typed);" in INDEX_HTML
    assert 'const message = parts.join("\\n");' in INDEX_HTML
    assert "if (!message) return;" in INDEX_HTML
    assert "JSON.stringify({ message: message })" in INDEX_HTML
    # 改动前的短路条件必须已经消失，否则"只点选不打字"会被静默丢弃
    assert "if (!text) return;" not in INDEX_HTML
    # 提交后上一轮的选项要冻结，防止用户点到两轮之前的档位
    assert "function freezeActiveQuestions" in INDEX_HTML
    assert "box.disabled = true;" in INDEX_HTML
```

**刻意没有写的一条测试**：一个"多行、含 `：` 与 `、` 的 message 走 POST /reply"的服务端用例。理由——本单元一个字节都没改服务端，`message` 一直是 `str`，这条路径已被 `tests/test_web_api.py` 的既有用例覆盖；再写一条只会复制 `make_app_with_scripted_client` 那套 fixture 到本文件，制造和单元 B 的测试维护面重叠。**测试要覆盖本单元改了的东西，不是覆盖本单元没改的东西。**

#### Step 2.4 — 验证

```bash
./venv/bin/python -m pytest tests/test_static_frontend.py -q
```

预期输出末行：`8 passed`。

```bash
./venv/bin/python -m pytest -q
```

预期输出末行：`176 passed`（基线 170 + Task 1 的 4 + Task 2 的 2）。

---

### Task 3: 子路径挂载验证与手工路径跑通（tasks 4.4 / 4.5 的真实验收）

**这一步是本单元的真实验收，不是收尾仪式。** 前两个 Task 的自动化测试证明的是"代码还在、契约没破"，证明不了"点一下会发生什么"——那件事只有在浏览器里做过才算数。

#### Step 3.1 — 子路径挂载的自动化覆盖（tasks 4.4）

在 `tests/test_static_frontend.py` **末尾追加**：

```python
def test_served_html_under_root_path_keeps_option_rendering():
    """
    部署约束 1：挂到任意子路径下都能正常工作，且有测试覆盖。

    既有的 tests/test_web_api.py 已经覆盖了 <base href> 本身的取值。这里补的是
    另一半：**经过 _render_index() 之后，选项渲染那几段代码仍然在页面里**。
    占位符替换是一次字符串替换，理论上不会吃掉别的内容——但"理论上"正是
    root_path 这类问题最爱翻车的地方（改动前的旧断言就曾经是个永不失败的摆设，
    见本文件 test_index_html_has_no_absolute_paths 的 docstring）。

    直接调 _render_index() 而不是起一个 TestClient：本用例要验的是渲染这一步，
    起 app 会把 LLM gateway、graph、checkpointer 一并拖进来，还会与单元 B 的
    测试 fixture 维护面重叠。
    """
    from app.web.server import _render_index

    for root_path, expected_base in [
        ("", '<base href="/">'),
        ("/hr/recruit-agent", '<base href="/hr/recruit-agent/">'),
        ("/foo/bar", '<base href="/foo/bar/">'),
    ]:
        html = _render_index(root_path)
        assert expected_base in html
        assert "<!--BASE_HREF-->" not in html, "占位符没被替换，相对路径会解析到域根"
        # 选项渲染与 AI 标识必须一起活到渲染之后
        assert "AI_OPTIONS_HINT" in html
        assert 'box.type = "checkbox"' in html
        assert "function collectSelections" in html
        # 本单元没有新增 fetch 调用点，既有的两个仍然是相对路径
        assert "api/jobs" in html
        assert '"/api/jobs' not in html
```

验证：

```bash
./venv/bin/python -m pytest tests/test_static_frontend.py -q
```

预期输出末行：`9 passed`。

```bash
./venv/bin/python -m pytest -q
```

预期输出末行：`177 passed`（基线 170 + 4 + 2 + 1）。

#### Step 3.2 — 手工验证脚手架（临时文件，⛔ 不提交进仓库）

**为什么需要它**：单元 B 还没合并，模型不会主动给 `options`，直接跑真实 demo 在界面上**验不到任何点选控件**——手工验证会变成"看起来没坏"，而这正是本单元最需要证伪的地方。脚手架用脚本化的 LLM 响应确定性地造出"带档位的追问"，不依赖真实模型、不烧 token、可重复。

把下面的内容写到**仓库外**的临时目录（例如 `/tmp/unitc_manual_harness.py`）。**⛔ 不要写进仓库、不要 `git add`**——本单元的触碰面只有两个文件。

```python
"""单元 C 手工验证脚手架（临时文件，⛔ 不提交进仓库）。

跑法（在仓库根目录执行）：
    ./venv/bin/python /tmp/unitc_manual_harness.py
然后浏览器打开   http://127.0.0.1:8099/hr/recruit-agent/
"""
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# 从仓库根跑，把 cwd 放进 sys.path，才 import 得到 app 包
sys.path.insert(0, str(Path.cwd()))


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list
    usage: object = None


TURNS = [
    # 第 1 轮：两个问题，一个有档位、一个 options 为空
    # → 同一屏里同时验"有档位渲染 chip"和"无档位不渲染空控件"
    json.dumps(
        {
            "is_job_related": True,
            "questions": [
                {
                    "text": "该岗位的功能安全等级要求是什么？",
                    "field": "functional_safety",
                    "options": ["ASIL-B", "ASIL-D", "无要求"],
                    "allow_free_text": True,
                },
                {
                    "text": "具体对应哪个车型、量产时间大概什么时候？",
                    "field": "project_context",
                    "options": [],
                    "allow_free_text": True,
                },
            ],
            "profile_patch": {"job_title": "嵌入式软件工程师"},
        },
        ensure_ascii=False,
    ),
    # 第 2 轮：历史裸字符串形态（.51 现网 2026-08-18 之前的 outbox 行）
    json.dumps(
        {
            "is_job_related": True,
            "questions": ["学历与工作年限有什么要求？"],
            "profile_patch": {"department": "电子研发部"},
        },
        ensure_ascii=False,
    ),
    # 第 3 轮：收尾，进入 confirmation_prompt
    json.dumps(
        {
            "is_job_related": True,
            "questions": [],
            "profile_patch": {
                "headcount": 2,
                "education_requirement": "本科",
                "experience_years": "3-5 年",
            },
        },
        ensure_ascii=False,
    ),
]


class ScriptedCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        content = self._responses.pop(0) if self._responses else TURNS[-1]
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class ScriptedChat:
    def __init__(self, responses):
        self.completions = ScriptedCompletions(responses)


class ScriptedClient:
    def __init__(self, responses):
        self.chat = ScriptedChat(responses)


def main():
    import uvicorn

    from app.llm.gateway import LLMGateway
    from app.web.server import create_app

    db_path = str(Path(tempfile.mkdtemp(prefix="unitc-manual-")) / "manual.db")
    client = ScriptedClient(TURNS)

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.invalid",
            model="scripted-for-manual-check",
            supports_json_schema=False,
            client=client,
        )

    # root_path 与生产一致：手工验证顺带就把部署约束 1 走了一遍真路径
    app = create_app(
        db_path=db_path,
        gateway_factory=gateway_factory,
        root_path="/hr/recruit-agent",
    )
    print(f"临时库: {db_path}")
    print("打开 http://127.0.0.1:8099/hr/recruit-agent/")
    uvicorn.run(app, host="127.0.0.1", port=8099)


if __name__ == "__main__":
    main()
```

**本脚手架已在 spec-to-plan 阶段实跑验证过**（2026-08-19，用 `TestClient` 走完三轮）：第 1 轮响应体确认带 `options: ["ASIL-B","ASIL-D","无要求"]` 与一个 `options: []` 的问题，第 2 轮裸字符串经 `normalize_question_payload()` 归一化后 `options` 为 `[]`、`question_id` 为 `free:1863ebe5`，第 3 轮进入 `confirmation_prompt`。执行者拿到的是一个已知能跑的脚本，不是一段待调试的伪代码。

#### Step 3.3 — 手工验证清单（逐条回填，不得只写"手工验证通过"）

启动：

```bash
./venv/bin/python /tmp/unitc_manual_harness.py
```

浏览器打开 `http://127.0.0.1:8099/hr/recruit-agent/`，**F12 打开 Console 与 Network 面板全程开着**，然后逐条走：

| # | 操作 | ✅ 通过判据 | ❌ 失败判据 |
|---|---|---|---|
| 1 | 打开页面，在输入框里写「要个做嵌入式开发的」，点【发送】 | 出现两条问题；Console 无任何红色报错 | Console 有 `Cannot read properties of undefined` 之类的报错；或聊天区一片空白 |
| 2 | 看第一条问题「功能安全等级」 | 问题文本下方有一行「以下为 AI 建议选项，不是既定要求…」，再下方有 **3 个** 圆角 chip：`ASIL-B` / `ASIL-D` / `无要求` | 只有纯文本没有 chip；或有 chip 但**没有**那行 AI 标识（这一条直接违反《AI 生成合成内容标识办法》，必须打回） |
| 3 | 看第二条问题「车型与量产时间」 | 只有问题文本。**没有** chip，也**没有**那行 AI 标识 | 出现一个空的选项行、或一条孤零零的"以下为 AI 建议选项"却没有任何选项 |
| 4 | 什么都别点，检查 3 个 chip 的初始状态 | 三个 checkbox **全部未勾选** | 任何一个默认勾上了 → 违反「候选档位不得代替用户做决定」，必须打回 |
| 5 | **只勾选 `ASIL-D`，输入框一个字不打**，点【发送】 | 能提交；聊天区右侧出现用户气泡，内容逐字为 `该岗位的功能安全等级要求是什么？：ASIL-D` | 点了没反应（说明 `if (!text) return` 的旧短路还在）；或用户气泡内容与实际发出去的不一致 |
| 6 | Network 面板点开刚才那个 `reply` 请求，看 Request Payload | 请求体是 `{"message":"该岗位的功能安全等级要求是什么？：ASIL-D"}`，**只有 `message` 一个键** | 出现 `selected_options` 或任何新键 → 违反 §5 约定 2，必须打回 |
| 7 | 同一个请求，看 Request URL | URL 是 `http://127.0.0.1:8099/hr/recruit-agent/api/jobs/…/reply`，**带 `/hr/recruit-agent` 前缀** | URL 是 `http://127.0.0.1:8099/api/jobs/…`（丢了前缀）→ 违反部署约束 1 |
| 8 | 回头看第 1 轮那组 chip（已经提交过了） | 变灰、鼠标移上去不是手型、点不动 | 还能继续勾选 → 用户可能拿两轮前的档位拼出对不上当前问题的回复 |
| 9 | 第 2 轮出现的问题「学历与工作年限」（脚手架故意走裸字符串形态） | 正常显示纯文本，无 chip，Console 无报错 | 页面崩、气泡空白、或 Console 报错 → `.51` 现网 15 个 job 的历史行会在真实环境复现这个崩溃 |
| 10 | **不勾任何东西**（本来也没有），在输入框写「本科，3-5 年」，点【发送】 | 正常提交、正常推进 | 因"未点选"被拒或无反应 → 违反 spec 的「选项之外的答案」 |
| 11 | 到达"画像已收集完整，请确认"后，输入框清空、不勾任何东西，点【发送】 | **什么都不发生**，不产生空请求 | 发出了一个 `message` 为空串的请求 |
| 12 | 在 Console 里执行下面这段，模拟单元 E 将来会置的 `is_reask`（本单元只读不写）：<br>`renderMessage({type:"question",payload:{questions:[{text:"学历要求是什么？",field:"education_requirement",options:["大专","本科","硕士"],is_reask:true}]}})` | 新气泡里问题文本以 `（这个你刚才没答）` 开头，档位 chip 正常渲染 | 前缀不出现 → 单元 A 已有的重问文本提示被本单元回退掉了 |
| 13 | 在 Console 里执行下面三段畸形 payload，逐个看 Console：<br>`renderMessage({type:"question",payload:{questions:[{text:"A"}]}})`<br>`renderMessage({type:"question",payload:{questions:[{text:"B",options:null}]}})`<br>`renderMessage({type:"question",payload:{questions:["裸字符串问题"]}})` | 三次都只渲染纯文本，无 chip，**Console 无报错** | 任何一次报错 → 单元 B 未合并期间的降级路径不成立 |
| 14 | 点【确认画像，生成 JD】 | JD 面板正常出现（脚手架的 JD 文本来自最后一条脚本响应，内容不重要，**能出来**就行） | 500 / 白屏 → 本单元碰坏了确认流程（本不该发生，本单元没改那段代码） |

**回填要求**：把上表复制进本单元的 final review 记录，每行填 ✅ / ❌ + 一句实际观察。**14 行全部填完且无 ❌，本单元才算完成。**只写"手工验证通过"不算完成。

#### Step 3.4 — 收尾

- [ ] 手工验证清单 14 条全部回填、无 ❌
- [ ] 删除临时脚手架：`rm /tmp/unitc_manual_harness.py`
- [ ] `git status` 确认工作区里只有 `app/web/static/index.html` 与 `tests/test_static_frontend.py` 两处改动
- [ ] `./venv/bin/python -m pytest -q` → `177 passed`
- [ ] 回勾 `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 4 章 4.1–4.5 五个 checkbox（**在 final review 通过之后才勾**，CLAUDE.md 粒度约定）

⛔ 提交时只允许 `git add app/web/static/index.html tests/test_static_frontend.py openspec/changes/m1-intake-quality-fixes/tasks.md`。**禁止 `git add -A` / `git add .` / `git commit -a`**——单元 B 可能正在同一个仓库里并行推进。

---

## 完成判据（final review 用）

1. `./venv/bin/python -m pytest -q` 输出 `177 passed`，无 failed、无 error
2. 本单元 diff 只含 `app/web/static/index.html` 与 `tests/test_static_frontend.py`（回勾 `tasks.md` 另算）
3. `app/web/server.py` 的 `ReplyRequest` 逐字未变（由 `test_reply_api_contract_has_no_selected_options` 保证）
4. Task 3 的 14 条手工验证清单已逐条回填、无 ❌
5. `index.html` 里不出现任何形式的默认勾选（`checked=` / `.checked = true`）
6. `options` 非空的分支里，AI 建议标识**先于**选项控件创建
7. tasks.md 第 4 章 4.1–4.5 五个 checkbox 已勾

## 已知遗留（不在本单元修，写明去向）

| 遗留 | 去向 |
|---|---|
| 前端行为没有任何自动化执行覆盖 | 形态代价，见「验收弱点」一节。要改需单开变更包讨论 JS 测试栈，不在本单元 |
| 重问前缀在前后端各存一份字面量 | 由 `test_reask_prefix_stays_in_sync_with_backend` 锁死漂移；更彻底的做法（后端把前缀放进 payload）属单元 E 的 5.4 视觉区分一并考虑 |
| 回复文本带问题原文前缀，可能污染 7.3 的来源子串判定 | 见「给单元 F 的接口说明」，由 F 决定是否收窄搜索范围 |
| `options` 很多时 chip 会换行铺满，没有折叠 | 单元 B 的档位规格是「2 至 3 个」，当前不构成问题。真出现十几个档位时再说 |
