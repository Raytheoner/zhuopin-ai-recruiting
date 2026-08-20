# 交付单元拆分（第 3-8 章）

> 单元 A（第 1、2 章，15 项）已于 2026-08-19 合并（`5f43da2`），测试 169 全绿。
> 本文件只拆剩下的第 3-8 章，**不含实现计划**——每个单元的 plan 由 `spec-to-plan` 单独出。
>
> 粒度约定沿用 CLAUDE.md：**一个交付单元 = 一份 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的东西**。
> 字母 = **交付顺序**，不是章节号（B=第3章、C=第4章、D=第6章、E=第5章、F=第7章、G=第8章）。

## 一、单元划分表

| 单元 | 覆盖章节 | tasks | 主要触碰文件 | 依赖 | 被谁依赖 | 规模（openspec tasks → 预估 plan Task） |
|---|---|---|---|---|---|---|
| **B** 兜底档位与预算口径 | 第 3 章 + 两个安置项 | 3.1–3.11 ＋ minor 项 ＋ Coverage Gap 决策 | `ecu_knowledge.py`｜`intake_agent.py`｜`intake_question.py`｜`graph/state.py`｜`graph/nodes.py`｜`web/server.py` | 单元 A（`IntakeQuestion`、`derive_question_id`、`is_productive` 列） | D（文件）、E（台账口径）、G（8.1 的空转轮指标） | 13 → **6-7** |
| **C** 前端可点选选项 | 第 4 章 | 4.1–4.5 | `web/static/index.html` **仅此一个** | 单元 A 的 payload 契约 | F（7.4 点选来源例外） | 5 → **2-3** |
| **D** 未指定字段推导与确认前警示 | 第 6 章 | 6.1–6.10 | `intake_agent.py`｜`schemas/job_profile.py`｜`graph/nodes.py`｜`web/server.py`｜`index.html` | 单元 A（`derived_unspecified_fields` 列）；与 B 只是文件重叠 | E（5.5 的"计入未指定字段"） | 10 → **5-6** |
| **E** 已问未答追踪与诚实重问 | 第 5 章 | 5.1–5.8 | `intake_agent.py`｜`intake_question.py`｜`graph/state.py`｜`graph/nodes.py`｜`index.html` | B（台账载体 + `MAX_TOTAL_ROUNDS`）、D（`derive_unspecified_fields`） | G | 8 → **4-5** |
| **F** 字段溯源与编造率度量 | 第 7 章 | 7.1–7.11 | `intake_agent.py`｜`graph/nodes.py`｜`graph/state.py`｜`docs/` | 单元 A（两列 + `meta.response_model`）、C（7.4） | G（8.7 的首个编造率数字） | 11 → **5-6** |
| **G** 回放验证与上线 | 第 8 章 | 8.1–8.9 | `tests/`（新回放用例）｜`docs/`｜发版 | 前面全部 | —— | 9 → **不走 plan**，见 §2.5 |

合计剩余 54 项 openspec task。

## 二、各单元详情

### B · 兜底档位与预算口径（第 3 章）

**为什么它是第一个**：pilot 三场里有两场直接卡死在这里（`19b6ec6d` 第 4 轮"这些我不太了解，你有什么建议"、`a478499c` 第 5 轮"一般材料是什么，你都不知道吗"），两轮的 `profile_json` 与上一版逐字节相同。这是本变更包里唯一一个"修完立刻能在对话里看见"的单元。

**依赖（具体到符号）**：
- `app.agents.intake_question.IntakeQuestion.options` / `.field` / `.question_id` —— 3.4 往问题上补档位、3.9 按 question_id 判定都建在这个对象上（A 已交付）
- `app.agents.intake_question.derive_question_id()` —— 3.9 的"未问过的 question_id"以它为口径
- `job_profile.is_productive` 列 —— 1.1 已加、默认 1，B 是第一个真正写值和读值的单元
- `app.agents.intake_agent.MAX_ROUNDS` / `MAX_QUESTIONS_PER_ROUND` / `suggested_followups()`（返回类型随 3.1 从 `list[str]` 变）

**被依赖**：
- E 的 5.1 台账 —— 3.9 判定 `is_productive` 时必须持有"已问过的 question_id 集合"，那就是 5.1 台账的雏形。**B 必须把这份台账的落库载体定下来**（见下方决策点），E 在其上扩"已答 / 重问次数"
- G 的 8.1 —— "修复前后空转轮数"这个对比指标的数据源就是 B 写进 `is_productive` 的值
- D 只是文件重叠，逻辑上不依赖 B

**并行性**：与 **C 可真并行**（B 全在后端，C 只碰 `index.html`，零文件重叠）。与 D、E、F 全部串行——`intake_agent.py` 与 `graph/nodes.py` 被这四个单元连续改动，是本批最热的两个文件。

**进这个单元时要先决策（两项，见 §3.2 与 §4）**：
1. Coverage Gap「重试全部失败时记录累计耗时」走哪条路 —— **开工前必须有结论**，它直接决定 3.9/3.10 要不要多一个分支
2. 已问台账落哪儿：新列（走 1.1 已建立的 `init_schema` 幂等加列路径，决策 10）还是 `profile_json` 的下划线内部键（与 `_jd_text` 同位置，决策 8 的做法）。1.1 的加列清单里**没有**这一项，是新决定

**顺带必须做的**：`prompt_version` 从 `intake-v3` 升到 `intake-v4`（3.5 改 `SYSTEM_PROMPT`，铁律 5：提示词改了不升版本，`input_hash` 与历史评分就对不上）。

### C · 前端可点选选项（第 4 章）

**依赖**：只依赖单元 A 已交付的 payload 契约——`payload.questions[].options` / `.allow_free_text` / `.is_reask` / `payload.questions_text`，以及 `normalize_question_payload()` 对 .51 历史裸字符串行的兜底（历史行归一化后 `options` 恒为 `[]`，按 4.1 只渲染自由文本，不会崩）。

**不依赖 B 的代码**：B 没合之前 `options` 基本为空，C 的渲染分支自然退化成今天的纯文本；B 合并后同一段前端代码自动开始有档位可渲染。所以 C 与 B 可以同时开两条分支。

**被依赖**：F 的 7.4（由用户点选候选档位产生的字段以被选中的档位标识作为来源）。

**决策点（影响并行性，建议在 C 开工时定死）**：点选提交的形态。
- **推荐最简形态**：把选中的档位文本原样拼成该轮回复文本，POST 到既有的 `/api/jobs/{id}/reply`，**不动 API 契约**。这样 C 与后端零文件重叠、B ∥ C 成立；而且 7.4 的"点选来源例外"大概率不必单独实现——被选中的档位文本本来就逐字出现在该轮用户原话里，7.3 的子串判定天然命中
- 若改成请求体新增 `selected_options` 字段，就会碰 `app/web/server.py` 的 `ReplyRequest`，C 立刻与 B/D 变成串行

**这个单元的弱点，要提前说清**：本仓库没有 JS 测试运行器，`tests/test_static_frontend.py` 目前只能做字符串弱断言（A 的注释里已如实写明"真正的验证是手工跑通那一步"）。**C 是六个单元里"可独立测试"成色最弱的一个**，它的真实验收落在 8.4 的手工路径跑通上。这不是拆分方式的问题，是前端无构建这个既有形态的代价。

### D · 未指定字段推导与确认前警示（第 6 章）

**依赖（具体到符号）**：
- `app.agents.intake_agent._SYSTEM_MANAGED_FIELDS` —— 6.1 推导时的排除集
- `app.schemas.job_profile.JobProfile.model_json_schema()` 与字段定义本体 —— 6.1 遍历、6.4 中文名紧邻字段放
- `job_profile.derived_unspecified_fields` 列（1.1 已加，默认 `'[]'`）
- `app.observability.redaction.loggable_summary()` —— 6.2 的硬约束，见 §3.3
- 与 B 的关系**只是文件重叠**（`intake_agent.py` / `graph/nodes.py` / `web/server.py` / `index.html` 四处），第 6 章的逻辑不依赖第 3 章

**被依赖**：E 的 5.5「重问超限 → 目标字段计入未指定字段」。D 合并后这条**自动成立**——字段没值，`derive_unspecified_fields` 自然把它列进去，E 不需要再写一条平行的标记逻辑。**这是把 D 排在 E 前面的技术理由**（反过来排的话，E 要先往模型透传的那份 list 里写标记，D 落地时再拆掉）。

**触碰文件**：`intake_agent.py`（6.1/6.2）、`schemas/job_profile.py`（6.4）、`graph/nodes.py`（`effect_persist_draft` 写 `derived_unspecified_fields`、`effect_confirm_profile` 按 6.9 写 `profile_json` 内部键）、`web/server.py`（6.5/6.7/6.8：`confirm` 加 `acknowledged_gaps` 与 409）、`web/static/index.html`（6.6/6.7）。测试面：`test_job_profile_schema` / `test_intake_agent` / `test_web_api` / `test_static_frontend` / `test_log_redaction`。

**硬约束**：6.2 的 debug 日志必须走 `loggable_summary()`，详见 §3.3。

### E · 已问未答追踪与诚实重问（第 5 章）

**依赖（具体到符号）**：
- B 定下的已问台账落库载体 + 3.9 的"已问过的 question_id 集合"口径
- B 的 `MAX_TOTAL_ROUNDS` —— 5.5「超限即不再消耗追问轮次」必须和 3.10 的两个计数口径对齐，否则会出现"这个子问题不再问了，但轮次照扣"
- D 的 `derive_unspecified_fields()` —— 5.5 的"计入未指定字段"
- 单元 A 的 `IntakeQuestion.is_reask`、`render_questions_text()` 里的 `_REASK_PREFIX`、`derive_question_id()`（5.6 要断言"换措辞不改 id"）
- `app.agents.intake_agent._repeats_earlier_assistant_turn` —— 5.8 要对它的去留给结论；A 已在该函数 docstring 里显式把这个待办挂给 5.8，是留给本单元的明确交接

**触碰文件**：`intake_agent.py`、`intake_question.py`、`graph/state.py`、`graph/nodes.py`、`index.html`（5.4 要求重问条目与新问题在界面上可区分，光靠文本前缀不够）。与 B、D、C 全线重叠 → 串行。

### F · 字段溯源与编造率度量（第 7 章）

**依赖（具体到符号）**：
- `job_profile.ungrounded_fields` / `job_profile.llm_response_model` 两列（1.1 已加，至今无人写值）
- `LLMGateway.extract_structured_with_meta()` 返回的 `meta.response_model`，以及已经带到 `IntakeTurnResult.llm_response_model` 上但尚未落库的那个值（1.3 已透出，A 的注释明确写了"落库属第 7 章"）
- C 的点选回传（7.4）

**跨单元接口风险，必须写进 F 的 plan 的 Global Constraints**：7.1 把 `profile_patch` 的字段从裸值升级为 `{value, source_quote, source_turn}`。**合进 `profile_patch_accumulated` 与 `job_profile.profile_json` 的必须仍然是裸值**，结构升级只允许存在于"模型返回 → 校验"这一段，落库前拍平。不拍平会同时炸三处：
1. D 的 `derive_unspecified_fields` 会把 `{"value": null, ...}` 当成"这个字段有值"，漏报回到今天的故障
2. `POST /confirm` 里的 `JobProfile.model_validate` 直接 422（`headcount` 收到一个 dict）
3. `effect_generate_and_persist_jd` / `jd_agent` 全部按裸值读 `profile_dict`

**顺带必须做的**：7.2 再改 `SYSTEM_PROMPT` → `prompt_version` 升到 `intake-v5`（铁律 5）。

**业务可见性：零。** 按决策 12，本章只观测不拦截，三位业务经理在界面上看不到任何变化。它服务的是另一个问题，见 §3.1。

### G · 真实会话回放验证与上线（第 8 章）

**性质与前五个单元不同**，不建议当成一份 TDD plan 跑 `run-build`。建议拆成四段按清单执行：
1. **8.1 回放测试** —— 这段是写代码，进 `tests/`（新文件），可以走 plan
2. **8.2–8.5 发版** —— 照 `05-发布运行手册.md`。8.3（备份 `.51` 的 `demo.db` 含 `-wal`/`-shm`）与 8.4（生产验证）属 CLAUDE.md 决策代理表的**不可代**项：生产服务器 `.51` 的发版决定必须 Shao Peishen 本人拍板
3. **8.6–8.8 文档回填** —— 含 8.7 的首个真实编造率数字（依赖 F）、8.8 只改 `docs/m1-demo-manager-guide.md` 不改代码
4. **8.9 归档** —— `m1-job-profile-intake` 先归档、本包后归档；且归档被 Coverage Gap 阻塞，见 §3.2

## 三、拆分时必须回答的三件事

### 3.1 业务优先级：哪个单元让三位业务经理最快感受到改善

**排序（按 pilot 三场会话的抱怨强度与命中场次）**：

1. **B + C 合起来** —— 这两个是同一个抱怨的两面。三场里两场卡死在"我不知道 / 你有什么建议"，经理原话是"复制粘贴编辑有点麻烦，最好直接选择项加，文字描述"。
   - B 单独上线：用户开始拿到具体档位（文本形式），空转轮不再烧预算 —— **已经能感受到**
   - C 单独上线：几乎无感（没有 B，`options` 基本是空的）
   - **两个一起进 demo，是本批可感改善最大的一次投放**
2. **D（第 6 章）** —— 两位经理都点了确认、事后才在 JD 里发现缺口。改善发生在"确认那一刻"而不是对话里，感受强度次于 B/C，但确定性高：它是纯代码推导，不依赖模型行为
3. **E（第 5 章）** —— 修"问过了怎么又问"。只有 `2494103e` 一场明确命中；而且 B 落地后空转轮减少，重问的发生频率本身会下降，边际收益被 B 吃掉一部分
4. **F（第 7 章）** —— **对业务经理零可见**

**技术依赖顺序**：B →（D）→ E；C 与 B 并行；F 在 C 之后；G 最后。

**两处不一致，需要 Shao Peishen 拍板**：

- **F 排最后，与 proposal 把编造列为"比字段准确率更要紧"矛盾。** 技术上 F 可以早做（只依赖已合并的 A 的两列 + C 的点选）。它一行用户可见的改善都不产生，但 8.7 的"首个真实编造率数字"要等真实会话跑起来才有，决策 12 的解锁条件又是"累计 ≥ 20 场真实采集会话"—— **F 越早合，编造率的时钟越早开始走**。按业务感受排，F 最后；按"尽早攒够 20 场样本"排，F 应紧跟 B/C、排在 D 和 E 之前。两种排法都自洽，取舍点是先让经理们舒服，还是先让那个至今未知的数字开始积累。
- **E 排在 D 之后**，是我按业务价值 + 5.5 的技术便利做的调整。若 Shao Peishen 认为"重复问同一件事"的体感更刺痛（姚祖怡那条是三份反馈里最尖锐的），E 与 D 对调也成立，代价是 5.5 要先自己写一遍"计入未指定字段"，D 落地后再删。

**另外要纠正 tasks.md 一处**：章节头写的「第 6 章可与第 3-5 章并行」「第 7 章可与第 3-6 章并行」，**按本次的判据（触碰文件重叠即须串行）不成立**。第 6 章要改 `intake_agent.py` / `graph/nodes.py` / `web/server.py` / `index.html`，与第 3、4、5 章四个文件全线重叠；第 7 章同样要改 `intake_agent.py` / `graph/nodes.py`。真正能并行的只有 **B ∥ C**（后端 vs 纯前端），前提是 C 采用"点选文本原样提交、不改 API 契约"的最简形态。

### 3.2 那条半实现的 SHALL → 挂在 **B**，且是"进单元前先决策"

`intake-turn-observability`「LLM 调用失败或重试时，留痕 SHALL 记录直到本轮结束为止的累计耗时」中"全部重试都失败"这一半，**挂到单元 B，作为 B 开工前的第一个决策点**，不能推到实现中途。Shao Peishen 已定"先挂着、等第 3 章做到 `is_productive` 时再定"——那个时点就是 B 的开工时点。

**为什么必须在 B 的开头定**（两条路都直接改 B 的核心判定）：
- **路 A（实现它）**：整轮失败时也要写一行承载耗时的 `job_profile` 行。那一行的 `is_productive` 取什么值 → 3.9 的判定多一个分支；它算不算进 `MAX_TOTAL_ROUNDS` → 3.10 的预算口径多一条规则；而且这行会让 `business_key`（= 总行数）跟着 +1 —— 幂等语义不变，但"这一轮到底算不算发生过"第一次有了答案
- **路 B（把 spec 那句窄化为只覆盖"重试后成功"）**：改 spec 原文，属契约变更，**不是实现者能替代做的**（design.md「Coverage Gaps」已写明 narrowing 是决策者的判断）。窄化后 B 不需要多任何分支

**供决策参考的判据（不代替决策）**：路 A 每次全失败都多写一行草案，会把"系统炸了"的轮次伪装成"用户交互过的一轮"，而 8.1 的回放对比正是按 `job_profile` 行数算轮数的——这会污染本批唯一的效果度量。路 B 保留了"整轮失败 = 不存在这一轮"的干净语义，代价是那句 SHALL 的一半永远不被满足、必须改 spec 原文。**倾向路 B**；按 CLAUDE.md 决策代理表，代理人未指定期间一律挂起等本人。

**归档阻塞照旧**：这条不解决，`m1-intake-quality-fixes` 不得归档。也就是说 G 的 8.9 之前，它必须已经落地成代码或落地成 spec 改动。

### 3.3 tasks 6.2 的 `loggable_summary()` 上岗点 → 挂在 **D**（第 6 章）

已作为硬约束写进 D：6.2 那条 debug 日志（模型给的 `unspecified_fields` 与系统推导结果对照）是本变更包里**第一次**把业务对象内容送进 logging，**必须走 `app.observability.redaction.loggable_summary()`**，不得 `logger.debug("...%s", parsed.unspecified_fields)` 直接打。

- **依据**：`docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.3.1 的更正段。原文把上岗点错挂在 `intake-turn-observability` 上——那条 spec 自带「时序留痕不承担审计职责」，明令其中"只有时间与耗时信息"，既不过 logging 也没有业务内容可脱敏，押在它身上等不到结果。通读全 8 章，唯一一处是 6.2
- **验收要求**：D 里要有一条测试断言这条日志路径**确实调用了** `loggable_summary()`。不能只测"日志里没泄漏"——§8.3.1 已经指出，在没有调用点的情况下"0 命中"同时兼容"脱敏有效"和"脱敏根本没上岗"两种解释，那不叫验证
- **附带的常设规则（不绑单元）**：任何把业务对象送进 logging 的代码一律走 `loggable_summary()`，绕过时由 `RedactionFilter` 告警。F 若要打未溯源字段的日志，同样适用

## 四、单元 A 终审 minor 项的安置 → **B**，不是 E

`derive_question_id` 未校验 `field` 是否属于 `JobProfile` schema、也没有 null-`field` 比例的监控指标（A 终审记录，标注"第 5 章之前修"）→ **落在 B**。

比"第 5 章之前"更强的一条理由：**B 的 3.9 是 `question_id` 第一次从"只用来渲染"变成"参与判定"**。`is_productive` 直接按"有没有问出未问过的 `question_id`"取值。模型给一个野 `field`（拼错、或幻觉出一个不存在的字段名）时，`derive_question_id` 今天原样接受，于是每轮都产出一个"新"的 question_id，**每一轮都会被判成有产出**——`MAX_ROUNDS` 的有产出轮计数当场失效，正是这一章要修的那个故障换了个形式回来。所以它在 B 里不是"顺手修"，是 B 的前置条件。

**落地形态建议**：`derive_question_id` 对不在 `JobProfile.model_fields` 里的 `field` 按"无 field"降级（走既有的 `free:` 哈希分支，不抛异常——降级而非报错是 A 已确立的基调），并把"降级次数"与"null-field 次数"打点，供 8.1 回放时看比例。

## 五、跨单元接口约定（各自 plan 的 Global Constraints 里逐条抄进去）

1. **F 的 `profile_patch` 结构升级不得穿透到 `profile_json`** —— 落库前拍平成裸值，理由见 §2.F
2. **C 的点选提交不改 API 契约** —— 否则失去 B ∥ C 的并行，理由见 §2.C
3. **B 与 F 都会改 `SYSTEM_PROMPT` → 各自升 `prompt_version`**（现为 `intake-v3`；B → `v4`，F → `v5`）。铁律 5
4. **B 若为已问台账新增列，走 1.1 已建立的 `init_schema` 幂等加列路径**，不另起迁移机制（决策 10）；所有新列必须可空或有默认值，既有 15 个 job 的历史行不回填
5. **每个单元开工前必须 rebase 到最新 main** —— `app/agents/intake_agent.py` 与 `app/graph/nodes.py` 被 B/D/E/F 四个单元连续改动，是本批最热的两个文件

## 六、推荐执行顺序

```
① B（第 3 章）  ∥  C（第 4 章）      ← 两条分支同时开，合并后一起进 demo
       ↓
② D（第 6 章）
       ↓
③ E（第 5 章）
       ↓
④ F（第 7 章）
       ↓
⑤ G（第 8 章）：回放 → 发版 → 文档回填 → 归档
```

**开工前必须先有的两个决定**（都属 Shao Peishen）：
1. Coverage Gap 走路 A 还是路 B（§3.2）—— 卡住 B 的开工
2. F 的排位：最后，还是紧跟 B/C 以尽早开始积累 20 场样本（§3.1）—— 不卡 B，但要在 ② 之前定
