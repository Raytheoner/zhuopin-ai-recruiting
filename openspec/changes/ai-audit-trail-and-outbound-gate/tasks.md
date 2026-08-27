**粒度约定**：一个 `##` 章节 = 一个 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的交付单元。章节的 checkbox 在该 plan 的 final review 通过后才勾。

**依赖顺序**：1 → 2 → 3，1 → 4 → 5，(3,5) → 6 → 7。章节 2 与 4 在章节 1 合并后可并行。

**每份 plan 的 Global Constraints 段**从 `CLAUDE.md`「工程铁律」逐字复制，并追加本变更的三条硬边界：不新增 `zhuopin_platform` 依赖、不跨仓库 import、不改 `effect_log` 与 `idempotent_effect`。

---

## 1. 数据层：三张新表与存储层约束

交付单元：`db.py` 的 `SCHEMA` 追加三张表，既有表一行不改。合并后现有 81 个测试必须全绿（本章不改任何行为）。

- [x] 1.1 建表 `analysis_run`：`id` / `application_id` / `job_id` / `configured_model` / `response_model` / `system_fingerprint`（可空）/ `prompt_version` / `temperature` / `input_hash` / `rubric_snapshot`（JSON）/ `raw_response` / `token_usage`（JSON）/ `latency_ms` / `created_at`。表注释写明「审计资产，禁止用作训练/调优输入」及其理由
- [x] 1.2 建表 `criterion_score`：`id` / `analysis_run_id`（外键）/ `criterion_key` / `score` / `evidence_ref` / `created_at`；加 `CHECK (evidence_ref IS NOT NULL AND trim(evidence_ref) != '')`（铁律 4 由存储层强制，不靠应用层）
- [x] 1.3 建表 `pending_approval`：`id` / `thread_id` / `message_type` / `recipient` / `payload_json` / `blocked_reason` / `status`（`pending`/`approved`/`abandoned`，加 `CHECK` 限值）/ `confirmed_by`（可空）/ `enqueued_at` / `resolved_at`（可空）；`content_hash` 加唯一索引（重复入队防线，见 5.3）
- [x] 1.4 加索引：`analysis_run(application_id)`、`criterion_score(analysis_run_id)`、`pending_approval(status)`
- [x] 1.5 测试：`CHECK` 约束生效——直接执行 `INSERT` 写空 `evidence_ref` 被数据库拒绝（绕过应用层同样被拒）；`status` 写非法值被拒；重复 `content_hash` 被唯一索引拒
- [x] 1.6 测试：`init_schema()` 对既有库幂等重跑不报错；`effect_log` 与 `outbox` 的结构与行数不受影响

### 1.x 落地偏离登记（U1 实施，2026-08-27，final review 通过）

本章按 `docs/superpowers/plans/2026-08-26-ai-audit-trail-unitU1-schema-and-config.md` 实施，落地时相对本文件字面有五条偏离。**前四条方向都是"更严"或"对齐下游粒度"，第五条是修计划自己的代码缺陷。** 分支实测 253 → 320 passed。

> **审批状态：2026-08-27 Shao Peishen 口头追认全部五条，纸面手续线下补办。**
> 记此一笔是为了不把口头追认误记成已完成正式签署——**手续未补齐前，本条按"已口头批准、待补书面"对待**。
> 依据 `CLAUDE.md`「决策代理」的留痕要求：代批/追认当次即须写明批准人、时间、事项。
> 批准人：Shao Peishen（本项目唯一决策人）｜时间：2026-08-27｜事项：本节偏离 1–5 全部。

| # | 本文件字面 | 实际落地 | 判据（哪条测试咬住它） |
|---|---|---|---|
| 1 | 1.2 的 `trim(evidence_ref)` | `trim(evidence_ref, ' ' \|\| char(9) \|\| char(10) \|\| char(13))` | SQLite 单参 `trim()` **只剥空格**，一个纯制表符的 `evidence_ref` 会通过字面版 `CHECK`，铁律 4 就有静默缺口。`test_criterion_score_rejects_blank_evidence_at_storage_layer[tab/newline/mixed-whitespace]` |
| 2 | 1.3 的 `content_hash` 单列唯一索引 | `(thread_id, content_hash)` 两列唯一索引 | 与 5.3 幂等键 `{thread_id}:effect_enqueue_pending_approval:{content_hash}` 同粒度。单列全局唯一会让两个不同 thread 的同内容草稿在入队时撞 `IntegrityError`，把"拦下来排队"变成异常穿透。`test_pending_approval_allows_same_content_in_different_threads` |
| 3 | 1.3 未写 `message_type` / `recipient` 可空性 | 两列均可空 | 草稿被拦下的常见原因**正是**这两个字段缺失。设成 NOT NULL 会把"拦下一条畸形消息"变成 `IntegrityError`，异常穿透到调用方后一个 `except` 就是 fail-open。`test_pending_approval_accepts_malformed_draft_with_unknown_type_and_recipient` |
| 4 | `delivery-units.md` §4 约定 1 说加**两个**配置键 | 加了**三个**（多一个 `candidate_outbound_switch_file`） | 只有环境变量的话，`.51` 上改开关仍需重启进程，§3.5（三）"允许热改、不重启生效"在生产里等于零。约定的目的（U3/U4 只读不写 `config.py`）未被破坏 |
| 5 | **计划正文 Task 4 Step 3 给出的 `is_candidate_outbound_enabled()` 代码** | 见下方专条 | —— |

#### 偏离 5：外发总开关 fail-closed 加固（合规红线，需 Shao Peishen 追认）

**原计划代码为什么不成立。** 计划 Task 4 Step 3 的实现先调 `get_settings()`、再读开关文件。审查阶段实测出三个 **fail-open** 路径——异常从闸门里逃逸出去，调用方任何一个 `except Exception` 兜底就变成"放行"：

1. `CANDIDATE_OUTBOUND_ENABLED` 取值 pydantic 无法解析成 `bool`（`""`、拼错的 `"ture"`）→ `get_settings()` 抛 `ValidationError`。**且此时开关文件哪怕写着 `false` 也拦不住**，因为异常发生在读文件之前——热改这道闸形同虚设。
2. `LLM_MODEL=latest` → `validate_model_version()` 抛 `ValueError`。**一个跟外发毫无关系的配置错误，把外发闸门一起带走。**
3. `Path.exists()` 对 ENOTDIR / ELOOP（某级路径段是普通文件、符号链接自环）同样返回 `False`，于是**结构损坏的开关文件路径被当成"没配"**，降级去读环境变量。实测：`.env` 写 `true` 时返回 `True`。计划里 `exists()` vs `is_file()` 那条注释只堵住了"目录占位"一种。

这与计划自己声明的约束直接冲突——「任何一层读不出明确的'开'，结果都是 `False`：未知即拦截……出错的方向只能是更保守的那一侧」。**冲突时以真值为准，不迁就计划正文。**

**改成了什么。**
- `_read_switch_file()` 取代 `Path.exists()`：只有 `FileNotFoundError`（确实没这个文件）才返回 `None` 并降级去看环境变量；其余 `OSError` / `UnicodeDecodeError` / `ValueError` 一律抛内部哨兵 `_SwitchFileBroken` → 判关。**"没配"与"配坏了"必须分开**，`exists()` 的布尔值做不到这个区分。
- `get_settings()` 构造失败 → 立刻返回 `False`，连开关文件都不看。配置坏了就是全拦。
- `is_candidate_outbound_enabled()` 变成一层 `try/except Exception: return False` 的薄壳，包住私有的 `_evaluate_candidate_outbound_switch()`。**契约由结构保证，不靠枚举异常类型**——第一轮枚举了 `OSError`/`UnicodeDecodeError`，第二轮仍被 NUL 字节路径抛的裸 `ValueError` 逃出去（`dotenv` 不像 `os.environ` 那样拒绝 NUL；`.51` 上 PowerShell 写的 UTF-16 `.env` 解码后就会带 NUL）。枚举法已经失败两次。

**没有改变的东西**（拍板结论原样保留）：代码默认 `False`；优先级 开关文件 > 环境变量 > 基线值；每次调用求值、无 `@lru_cache`；`get_settings()` 自身的 `@lru_cache` 保留。

**判据。** 两条 Critical 各有专属回归测试钉住，均经变异验证会单独变红：

| 失效场景 | 钉住它的测试 |
|---|---|
| 垃圾值 `CANDIDATE_OUTBOUND_ENABLED`（`""` / `"ture"`） | `test_empty_env_value_is_closed_not_an_exception`、`test_typo_env_value_is_closed_not_an_exception` |
| 无关配置错误 `LLM_MODEL=latest` | `test_unrelated_config_error_does_not_break_gate` |
| 路径结构损坏（ENOTDIR / ELOOP） | `test_switch_path_with_file_as_parent_component_is_closed`、`test_switch_path_self_referencing_symlink_is_closed` |
| NUL 字节路径抛裸 `ValueError` | `test_read_switch_file_classifies_nul_byte_as_broken_not_bare_value_error` |
| 任何未枚举的异常类型 | `test_unenumerated_exception_type_still_closes_the_gate` |
| 没配（ENOENT）被误判成配坏了 | `test_absent_switch_file_still_falls_through_to_env_var` |

**默认值必须参与求值**（上一轮单元 D 的教训：合规默认值被改成 `True` 却无人发现）。把 `candidate_outbound_enabled: bool = False` 改成 `True` 后，`test_candidate_outbound_is_closed_by_default`、`test_switch_file_removal_falls_back_to_baseline`、`test_env_var_is_read_every_call_not_cached_at_startup` 三条变红——两次结构重排之后都复验过，基线分支没有被短路绕过。

#### 遗留一：开关文件的编码写法 → ✅ **已拍板（2026-08-27 Shao Peishen），取方案 (b)，由 U7 承接**

**现象。** `_read_switch_file()` 不剥 UTF-8 BOM，也不认 UTF-16。`.51` 是 Windows，PowerShell 的 `Out-File` / `>` 默认写 **UTF-16LE**，记事本的"UTF-8"**带 BOM**——两种写法下文件内容都不会被识别成 `true`。**后果：运维照着文档写一个 `true` 进去，开关不会打开，而且不报错。** 方向是 fail-closed（拦住了，没有放行），所以不阻塞 U1 合并；但那条热改通道在真机上等于打不开。

**结论：取 (b) —— `_read_switch_file()` 保持原样、不改代码，改为把运维写法规定死。** 被否掉的 (a)（改代码剥 BOM + 尝试 UTF-16 解码）属**在合规开关上放松**，是 `CLAUDE.md` 决策代理表的不可代项；他本人选择不放松代码。

**规定的写法（唯一允许）：**

```powershell
[System.IO.File]::WriteAllText($path, 'true')
```

**⛔ 禁止使用**：PowerShell 的 `Out-File`、`>`、`>>`（默认 UTF-16LE）；记事本的"UTF-8"另存（带 BOM）。用错写法的症状是**开关静默不生效、且无任何报错**——排查时人会先怀疑代码而不是怀疑文件编码，这正是必须写进运维文档的理由。

**承接单元：U7 的 7.3**（该条已就地加注）。⚠️ **U5 接线前必须确认 7.3 已落地**，否则总开关在生产上不具备可操作性。

#### 遗留二：开关文件路径按进程 CWD 解析 → 留给 U5 落地时定（未裁决）

`Settings.candidate_outbound_switch_file` 的默认值 `data/candidate_outbound.switch` 是**相对路径，按进程工作目录解析**，与 `db_path` 同一约定。`.51` 由 Windows 计划任务拉起，**若计划任务的工作目录与预期不符，热改通道会静默失效**（读不到文件 → 降级到环境变量/基线值 → fail-closed，不报错）。

2026-08-27 Shao Peishen 未单独裁决此条，按 U1 执行时的判断**留给 U5 接线时定**：届时要么在部署脚本里锁定工作目录，要么改用绝对路径配置。属 `.51` 生产运维事实，不是 U1 的代码缺陷，U1 不就地处理。

## 2. `app/audit`：事件、双 sink、统一入口

交付单元：留痕模块可独立测试，此时**尚未接线**，现有行为完全不变。

- [x] 2.1 `events.py`：`DecisionEvent` dataclass，字段按 D2 的招聘领域定义（`application_id` / `job_id` / `criterion_key` / `rubric_version` / `evidence_ref` 等为一等字段，不放自由字典）；`to_dict()` 剔除空 `error` 字段
- [x] 2.2 `sinks.py` 之一：`AuditSink` Protocol（`write` / `read_all`）+ `SqliteSink`，写 `analysis_run` 与 `criterion_score`。**副作用/幂等**：`SqliteSink.write` 不自行 `commit`，由调用方事务统一提交（与 `effect_persist_draft` 同一约定）；`analysis_run.id` 由调用方以 `{thread_id}:{node}:{input_hash}` 生成，主键冲突即视为已写入，短路返回
- [x] 2.3 `sinks.py` 之二：`JsonlChainSink` 写入侧——append 一行 JSON 并嵌 `prev_hash`；进程内按文件路径共享互斥锁；上一条哈希游标按路径共享（类级字典），缓存缺失时**从磁盘末行重算**而非当 genesis
- [x] 2.4 `sinks.py` 之三：`JsonlChainSink.verify_chain()`——对磁盘**原始字节**重算 SHA-256；第 2 行起缺 `prev_hash` 判断链；返回 `ok` / `total` / `broken_at` / `error`
- [x] 2.5 测试 `verify_chain()` 的四个攻击场景：完整链通过、中间行被改、中间行被删、**删光全部 `prev_hash` 字段后重写**（必须在第 2 行判断链，这条是平台侧修过的绕过）
- [x] 2.6 测试 `verify_chain()` 的序列化鲁棒性：含中文、换行转义、非 ASCII 的记录不误报断链（校验不做 JSON 重排序）
- [x] 2.7 测试并发写入：多线程并发 append 同一文件，行不穿插且事后 `verify_chain()` 通过；两个指向同一文件的 sink 实例交替写不断链
- [x] 2.8 `recorder.py`：`AuditRecorder`，`record()` 按 D1 顺序**先 SQLite 后 JSONL**；SQLite 写失败即抛异常（调用方不吞，评分视为不可用）；提供 `query_by(**filters)` 与 `verify_integrity()`
- [x] 2.9 测试双写故障语义：JSONL append 抛错时 SQLite 记录仍在且异常可见；对账能检出差集；补齐以链尾 `type=backfill` 事件形式追加（不插回原位）

## 3. 留痕接线：网关钩子 + 生产装配 + 评分项白名单

交付单元：合并后铁律 3、4 从"钩子留着"变成真实生效。

- [x] 3.1 `AuditHook` Protocol 扩参：新增可选 `audit_context`（承载 rubric 快照与 `application_id` / `job_id`），`LLMGateway.extract_structured()` 原样透传不解释内容；现有调用点不传也能跑
- [x] 3.2 实现 `RecorderAuditHook`（`AuditHook` → `AuditRecorder` 的适配），`NoopAuditHook` 保留并改注释定位为「测试专用」
- [x] 3.3 生产装配处（`create_app()`）注入 `RecorderAuditHook`；新增配置项：审计 JSONL 路径。**注入点只有一处**，回滚 = 换回一行
- [x] 3.4 `criterion_key` 白名单集中定义在一处；写入非白名单维度被拒。白名单显式排除声学情绪信号（语速/停顿/静默）与人脸/表情类维度
- [x] 3.5 测试：一次真实形状的评分调用后，`analysis_run` 落齐全部字段，且 `configured_model` 与 `response_model` 分两字段各自保存不互相覆盖
- [x] 3.6 测试：`system_fingerprint` 缺失时记空值且留痕照常写入（不让网关炸掉）
- [x] 3.7 测试：写入声学情绪维度、写入人脸/表情维度分别被拒；留痕中不含简历原文（只有 `input_hash`）

### 3.x 落地偏离登记（U3 实施，2026-08-28，两轮 review 通过）

本章按 `docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU3-recorder-wiring.md`
实施，落地时相对本文件字面有六条偏离。**方向全部是"补齐 spec 要求但字面漏写的东西"
或"更严"**，没有一条放松。分支实测 654 → 675 passed（含 U4 并行分支的增量）。

| # | 本文件字面 | 实际落地 | 判据（哪条测试咬住它） |
|---|---|---|---|
| 1 | 3.1 只说「新增可选 `audit_context`」 | 另加 `temperature` 与 `attempt` 两个参数 | `analysis_run.temperature` 是 **NOT NULL**（`app/storage/db.py:102`）而旧签名里没有它，不补第一条真实写入就撞 NOT NULL；钩子在重试循环内每次尝试各调一次，多次尝试 `input_hash` 完全相同，不带 `attempt` 会撞 2.2 的确定性主键、第 2 次起被 U2 短路成 `False` 静默丢掉。`test_recorded_temperature_is_the_temperature_actually_sent`、`test_attempt_number_is_one_based_and_increments_per_retry` |
| 2 | 3.3 写「生产装配处（`create_app()`）注入」 | 注入点在 **`app/main.py:_gateway_factory()`**，`create_app` 签名一字未动 | 实际构造 `LLMGateway` 的不是 `create_app`（它只接收一个 `gateway_factory: Callable`，`app/web/server.py:54`）。改它的签名会立刻与 M1 的 B/D 单元串行。以 `delivery-units.md` §2.U3 为准。`test_create_app_signature_is_untouched`、`test_server_module_is_not_touched_by_this_unit` |
| 3 | 3.3 的「新增配置项：审计 JSONL 路径」 | **U1 已加齐**（`app/config.py:35`），U3 只读不写 `config.py` | `delivery-units.md` §4 约定 1：两个配置键在 U1 一次加齐，否则 U3 与 U4 共写 `config.py`、并行作废 |
| 4 | 3.4 只说「白名单集中定义在一处」，没说强制点在哪 | 定义在 `app/audit/criteria.py`，**强制点在 `CriterionScore.__post_init__`（构造期）** | 写入期强制只罩得住走那一个 sink 的路径；构造期强制让所有写入方（U5 的 queue、U6 的断言、M2 的评分器）连一个非法对象都造不出来。`test_rejection_happens_at_construction_not_at_write_time` |
| 5 | 本文件未规定 `criterion_key` 的口径 | **口径 A：存七个评分维度，不存 rubric 具体条目**（Shao Peishen 2026-08-28 拍板） | 仓库原有三处取值全是具体技能名（`autosar` / `can_bus`），而 spec 与 design 用的词是「维度」、且 design Risks 说「加维度是一行改动 + 一次 review」——只有维度是个位数时那句才成立。**对 M2 有约束**：评分器 MUST 把具体技能写进 `rubric_snapshot`，写进 `criterion_key` 会在构造期抛 `ForbiddenCriterionKey`。`tests/test_audit_criteria.py` 全部 |
| 6 | 本文件与 `delivery-units.md` **都没写**钩子拿不到事务连接这件事 | **审计走专属 SQLite 连接、自己提交**（需 Shao Peishen 追认，已于 2026-08-28 追认） | 钩子触发点在 `LLMGateway` 内部，那里没有 `conn`；复用全应用共享的那条会踩 `app/storage/idempotency.py:41-68`——被装饰函数抛异常时装饰器 `conn.rollback()`，留痕行被一起回滚，而那次 LLM 调用真的发生过、真的花了钱。铁律 1 禁止的是同一条连接上有第二个事务管理者，专属连接上只有适配器一个。三个方案的取舍见计划 §「一处必须自己定的架构决定」 |

#### 两轮 review 各自改掉的东西（全部有回归钉子 + 变异验证）

**round 1（两条真缺陷）**：

1. **共享连接 + 并发线程**。适配器是模块级单例，被 FastAPI 工作线程池共用；一条 SQLite 连接只有一个事务，A 的 `rollback()` 会抹掉 B 已执行未提交的 INSERT。**实测 20 线程并发：SQLite 只剩 12 行、JSONL 17 行、3 个 `InterfaceError`**——按 spec 那 3 个异常各打挂一个真实请求，另外 8 条是真实付费调用的留痕被静默丢掉。加 `_write_lock` 后 20/20/0。`test_concurrent_calls_do_not_lose_rows_or_diverge`
2. **SQLite 主键短路时镜像照样 append**。实测 SQLite 1 行、JSONL 2 行，而 `reconcile()` 比的是**集合**差集，`ok` 仍为 `True`——偏差对唯一的检出手段完全隐形。改成用 `record()` 的返回值短路。`test_a_deduped_write_does_not_append_a_second_mirror_line`

**round 2（三条，另驳回一条）**：

3. **镜像行的 `created_at` 是 `null`**。留 `None` 让数据库 `datetime('now')` 填，只有 SQLite 那侧有时刻——而镜像才是防篡改的那份独立证据，说不出"这次调用发生在什么时候"的证据基本不成立，`reconcile()` 只比 id 也发现不了。改成显式 `sqlite_utc_now()`，两侧同一时刻。`test_mirror_line_carries_the_call_timestamp`
4. **未登记的 `audit_context` 键抛在留痕之前**。那次 API 调用已经付过钱、已经发生了，此刻抛会让它一条记录都不剩。改成按已登记的键先记完再抛，且 `error` **只记键名不记值**（未登记的键正是可能藏简历原文的那些）。`test_a_rejected_context_key_still_leaves_a_trail_without_leaking_its_value`
5. **去重丢弃打 DEBUG 而 `log_level` 默认 INFO**——丢掉一次真实付费调用的留痕却零可观测痕迹。提到 WARNING。
6. **驳回**：round 2 称 `test_none_raw_response` 只断言镜像侧，实测不成立——该测试同时断言了 SQLite 侧的 `raw_response == ""`。不改。

#### ⚠️ 交给下游的三条硬约束

1. **接 `audit_context` 到业务侧的那个单元**：`app/agents/jd_agent.py:69` 的 `generate_jd()` 循环最多两次，两次 prompt **逐字相同**、gateway 的 `attempt` 都是 1——接上 `audit_context` 后第二次（"上一次生成了歧视性表述所以重试"的那次，最该留痕的一次）会被确定性 id 直接去重掉。**必须先解决**，判据是那条 WARNING 日志有没有出现。
2. **U5**：写 `pending_approval` 用的是业务连接，与审计的专属连接是两条，⛔ 不要合并。另，`recorder.record()` 返回 `False` 时调用点⛔ 不反推原因（2026-08-28 对残留 B 的拍板）。
3. **M2 的评分器**：具体技能/rubric 条目写 `rubric_snapshot`，`criterion_key` 只放七个维度之一（偏离 5）。

#### 本章新登记的两条技术债（均在 `docs/tech-debt.md`）

- **TD-4**：模型返回空响应体时网关 `json.loads(None)` 抛 `TypeError`，不在 `except` 元组里、直接穿透且不消耗重试。**非 U3 引入**，是写空响应兜底时照出来的。
- **TD-5**：`raw_response` 逐字存（铁律 3 明令），而 spec 只约束**输入**不得存原文——评分模型把简历片段引回响应里，原文就进了 append-only 的镜像。**M1 不触发**（没有评分调用），⚠️ 需 Shao Peishen 本人拍板，属合规红线相关的不可代项。

## 4. `app/outbound`：门禁纯函数与消息契约

交付单元：门禁判定可独立测试，此时**尚未插入外发路径**。

- [ ] 4.1 `contracts.py`：门禁所需字段的 Protocol（`message_type` / `requires_confirmation` / `severity` / `recipient` / `body`）+ 已登记消息类型清单（`rejection_letter`、`interview_invitation`）
- [ ] 4.2 `gate.py`：`compute_outbound_gate(message, outbound_enabled) -> GateDecision` 纯函数。fail-closed 六条判定按 spec 实现；`GateDecision` 携带 `allowed` / `reason` / `evidence`（判定所依据字段的**原始取值**，含空值），留痕直接消费 `evidence` 不重新求值
- [ ] 4.3 门禁内部异常按拦截处理（判定失败绝不放行）
- [ ] 4.4 AI 生成标识校验：拒信/邀约缺标识按拦截处理。**复用** `app/agents/jd_agent.py` 现有的 `AI_LABEL_TEMPLATE` 机制判定，不另写一套标识逻辑
- [ ] 4.5 配置项 `CANDIDATE_OUTBOUND_ENABLED`，默认**关闭**；总开关每次外发现求值，不启动时缓存（支持传 callable）
- [ ] 4.6 测试 fail-closed 六条判定各一个用例：未登记类型、缺 `requires_confirmation`、`requires_confirmation` 为真、`severity` 为空、`severity` 最高级、缺 AI 标识——全部拦截
- [ ] 4.7 测试放行的唯一路径：类型已登记 + `requires_confirmation` 显式为假 + `severity` 已知非最高级 + 标识齐备 + 带 `confirmed_by` + 总开关开启
- [ ] 4.8 测试总开关优先级：带有效 `confirmed_by` 但总开关关闭 → 仍拦截，`reason` 为「外发总开关关闭」，与「等待人工确认」区分
- [ ] 4.9 测试纯函数性：同一消息同一开关状态两次判定结果相同，且过程无任何持久化写入与消息投递

## 5. 待审批队列与图节点接线

交付单元：门禁真正插入外发路径。**合并时 `CANDIDATE_OUTBOUND_ENABLED` 保持默认关闭**（全拦），观察拦截留痕符合预期后再由运维开启。

- [ ] 5.1 `queue.py`：`pending_approval` 的读写与状态机（`pending` → `approved` / `abandoned`）；放行不 `DELETE` 而是改状态并记 `confirmed_by` 与 `resolved_at`；查询只返回 `pending`
- [ ] 5.2 `queue.approve(id, confirmed_by)`：带 `confirmed_by` 重走门禁。**死锁防线（平台侧踩过）**：仅首道拦截（无 `confirmed_by`）才入队；放行复发被总开关拦下时不重复入队、状态保持 `pending`，可在开关开启后再次放行
- [ ] 5.3 `effect_enqueue_pending_approval`：沿用现有 `idempotent_effect` 装饰器（**不改装饰器、不改 `effect_log`**）。**幂等策略**：`business_key` = 草稿内容哈希（复用 `message_business_key()` 的做法），幂等键 `{thread_id}:effect_enqueue_pending_approval:{content_hash}`；叠加 1.3 的 `content_hash` 唯一索引作第二道防线。函数体内不 `commit`，由装饰器统一提交
- [ ] 5.4 `effect_record_outbound_audit`：沿用 `idempotent_effect`。**幂等策略**：`business_key` = `{content_hash}:{allowed}`，同一草稿的"拦截"与"放行"各留一条痕、重放不重复留痕
- [ ] 5.5 外发路径接线：`compute_outbound_gate` 判定 → 按结果分流到 `effect_enqueue_pending_approval` 或既有 `effect_deliver_message`，两条路径都走 `effect_record_outbound_audit`。**不改 `effect_deliver_message` 内部逻辑、不改 `Channel` Protocol**
- [ ] 5.6 测试端到端拦截：一封无 `confirmed_by` 的拒信 → 未投递、入队为 `pending`、留痕含拦截原因与判定字段原始取值
- [ ] 5.7 测试端到端放行：队列 `approve` + 总开关开启 → 投递发生、队列转 `approved`、留痕动作类型为「已发送」且含 `confirmed_by`
- [ ] 5.8 测试重放安全：外发相关节点被从头重跑 → 已外发不重复外发、已入队不重复入队（`effect_log` 命中短路）
- [ ] 5.9 测试内部通知不受影响：岗位画像确认卡片不经候选人门禁，M1 现有投递行为与本变更前一致（回归）

## 6. 合规断言、对账与 CI

交付单元：红线被破坏时 CI 直接红。

- [ ] 6.1 `assertions.py` 断言一：以 AI 评分为理由的拒绝记录数恒为 0（`rejection_record.reason_type='ai_score'`；该表尚不存在时断言以「表不存在即通过、表存在则必须为 0」的形式实现，M2 建表后自动生效）
- [ ] 6.2 `assertions.py` 断言二：`criterion_score` 中 `evidence_ref` 为空的记录数恒为 0（`CHECK` 之上的纵深防御）
- [ ] 6.3 `assertions.py` 断言三：`criterion_score.criterion_key` 不存在白名单外的取值
- [ ] 6.4 `assertions.py` 对账查询：按 `analysis_run.id` 比对 SQLite 与 JSONL 两侧记录集合，差集非空即报告（D1 的检出手段）
- [ ] 6.5 `assertions.py` 拦截统计查询：按 `message_type` 与拦截原因统计次数，使「某类消息一直在被拦」可被发现（fail-closed 误拦的兜底观测）
- [ ] 6.6 三条断言 + 链校验（`verify_chain()`）接入测试套件与 CI；任一条不成立即判失败并指出违例记录
- [ ] 6.7 测试断言本身有效：故意插入一条以 AI 评分为理由的拒绝记录 / 一条白名单外的 `criterion_key` → 对应断言必须失败（防止断言写成恒真）

## 7. 边界守护与文档

交付单元：本变更的三条硬边界变成机器可查，人为破坏会被 CI 挡下。

- [ ] 7.1 CI 检查：`app/` 下禁止出现 `from zhuopin_platform` / `import zhuopin_platform`；禁止 `sys.path` 指向 OneDrive 路径的注入
- [ ] 7.2 CI 检查：`requirements.txt` 与 `pyproject.toml` 不含 `zhuopin_platform`；本变更的依赖文件 diff 必须为空
- [ ] 7.3 `docs/` 增一页说明留痕与门禁的运维口径：JSONL 路径与备份、链校验怎么手动跑、`CANDIDATE_OUTBOUND_ENABLED` 的开关流程与「不提供一键放行全部」的理由
  - **⚠️ U1 发现、U7 承接（2026-08-27 Shao Peishen 拍板取方案 (b)，见第 1 章「遗留一」）：本页必须写入开关文件的编码约束。** 规定唯一允许的写法是 `[System.IO.File]::WriteAllText($path, 'true')`；**⛔ 禁止** PowerShell 的 `Out-File` / `>` / `>>`（默认 UTF-16LE）与记事本的"UTF-8"另存（带 BOM）。`_read_switch_file()` 不剥 BOM、不认 UTF-16，用错写法的症状是**开关静默不生效且不报错**（方向 fail-closed，拦住了但打不开）。他明确选择不改代码——改代码剥 BOM 属「在合规开关上放松」，是不可代项。**U5 接线前必须确认本条已落地**，否则总开关在 `.51` 上不具备可操作性
- [ ] 7.4 `06-企业AI转型资产借鉴清单.md` 追加本次借鉴记录：借的四条做法、自建的对应模块、**明确未引入依赖未拷贝代码**
- [ ] 7.5 技术债登记：`operator_id` 现阶段不可信（鉴权空壳）；企微 OAuth SSO 待两侧共同决定，是 M2 处理真实简历前的阻塞项之一（另一半留痕已由本变更完成）
- [ ] 7.6 技术债登记：JSONL 写入侧仅进程内锁，假设单进程部署；M2 迁 Postgres 时需重新处理并发写与 JSONL 的关系
