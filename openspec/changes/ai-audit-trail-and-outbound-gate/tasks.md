**粒度约定**：一个 `##` 章节 = 一个 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的交付单元。章节的 checkbox 在该 plan 的 final review 通过后才勾。

**依赖顺序**：1 → 2 → 3，1 → 4 → 5，(3,5) → 6 → 7。章节 2 与 4 在章节 1 合并后可并行。

**每份 plan 的 Global Constraints 段**从 `CLAUDE.md`「工程铁律」逐字复制，并追加本变更的三条硬边界：不新增 `zhuopin_platform` 依赖、不跨仓库 import、不改 `effect_log` 与 `idempotent_effect`。

**进度（2026-09-03，0903G）：51/53。未归档依据：第 7 章 7.1/7.2 未完（U7 的 CI 边界守护两项）。**

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

#### 遗留二：开关文件路径按进程 CWD 解析 → ✅ **已拍板（2026-08-28 Shao Peishen），取「部署脚本里锁定工作目录」**

`Settings.candidate_outbound_switch_file` 的默认值 `data/candidate_outbound.switch` 是**相对路径，按进程工作目录解析**，与 `db_path` 同一约定。`.51` 由 Windows 计划任务拉起，**若计划任务的工作目录与预期不符，热改通道会静默失效**（读不到文件 → 降级到环境变量/基线值 → fail-closed，不报错）。

~~2026-08-27 未单独裁决，留给 U5 接线时定~~ → **✅ 2026-08-28 Shao Peishen 拍板：取「在部署脚本里锁定工作目录」。**

> **批准人**：Shao Peishen｜**时间**：2026-08-28｜**事项**：第 1 章「遗留二」开关文件路径口径｜**依据**：本人指示「部署脚本里锁定工作目录」。落档见 `docs/findings/2026-08-28-Shao-Peishen-五条裁决落档.md` §4。

**结论与边界**：

- ⛔ **不改用绝对路径配置**——被否决的另一个选项，⛔ 不要在 U5 或后续单元里重开这个决定
- ⛔ **不动代码**。`Settings.candidate_outbound_switch_file` 的相对路径默认值与 `db_path` 的同一约定**保持原样**；这是 `.51` 生产运维事实，不是 U1 的代码缺陷
- **U5 接线时按此口径**：把「部署脚本必须锁定工作目录」写进接线处的说明与运维页，不在代码里做路径兜底（做兜底＝在合规开关上放松，属不可代项）
- 落地动作本身属 `.51` 生产运维，**归 `docs/audit-and-outbound-ops.md` 的运维口径页**（7.3 已落地）与部署脚本，不占 U5 的代码 diff

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

**⚠️ 已拍板（2026-08-28，Shao Peishen）**：`record()` 返回 `False` 承载两种含义——「这条 run
已经写过」（主键短路，`tests/test_audit_sinks_sqlite.py:155`）与「这类事件在这个 sink 里没有
真身」（`:207`）。**结论：不动 U2**，由 U5 的调用点按 `event.event_type` 自行分辨（调用点本来
就知道自己在写什么类型）。⛔ U5 不得从 `False` 反推原因。分析见核对报告 §五 残留 B。

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

- [x] 4.1 `contracts.py`：门禁所需字段的 Protocol（`message_type` / `requires_confirmation` / `severity` / `recipient` / `body`）+ 已登记消息类型清单（`rejection_letter`、`interview_invitation`）
- [x] 4.2 `gate.py`：`compute_outbound_gate(message, outbound_enabled) -> GateDecision` 纯函数。fail-closed 六条判定按 spec 实现；`GateDecision` 携带 `allowed` / `reason` / `evidence`（判定所依据字段的**原始取值**，含空值），留痕直接消费 `evidence` 不重新求值
- [x] 4.3 门禁内部异常按拦截处理（判定失败绝不放行）
- [x] 4.4 AI 生成标识校验：拒信/邀约缺标识按拦截处理。**复用** `app/agents/jd_agent.py` 现有的 `AI_LABEL_TEMPLATE` 机制判定，不另写一套标识逻辑
- [x] 4.5 配置项 `CANDIDATE_OUTBOUND_ENABLED`，默认**关闭**；总开关每次外发现求值，不启动时缓存（支持传 callable）
- [x] 4.6 **（2026-08-28 D-6 取 (b) 后订正）** 测试**五条消息畸形**各一个用例：未登记类型、确认标志读不出布尔、风险等级不在词表、缺 AI 标识、收件对象读不出非空字符串——全部拦截，且 **`confirmed_by` 也清不掉**（终局）。另测**两类已知高风险**（确认标志显式为真、风险等级为已登记最高级）：无确认人时各自报出自己的原因，⛔ 不折成「等待人工确认」。
  > 订正前的旧口径把「`requires_confirmation` 为真」「`severity` 最高级」也算作六条 fail-closed 之一的**终局拦截**，与代码和 `specs/outbound-approval-gate/spec.md` 都对不上——照旧口径实现会让 `queue.approve()` 带确认人重走门禁仍被拦，待审批队列里的信件永远发不出去。依据见下方 4.x 的 D-6 节（commit `121713f` / `bcc41a1`）。
- [x] 4.7 **（2026-08-28 D-6 取 (b) 后订正）** 测试放行的唯一路径：类型已登记 + 确认标志读得出布尔 + `severity` 在词表内 + 标识齐备 + 收件对象为非空字符串 + 带非空 `confirmed_by` + 总开关开启。
  > 订正前写作「`requires_confirmation` **显式为假** + `severity` **已知非最高级**」——那是取 (b) 前的旧口径。两类已知高风险现在由第一道闸的人以 `confirmed_by` **清关**，不再是放行的排除条件；两道闸仍串联（人签了字、总开关关着照样不发）。
- [x] 4.8 测试总开关优先级：带有效 `confirmed_by` 但总开关关闭 → 仍拦截，`reason` 为「外发总开关关闭」，与「等待人工确认」区分
- [x] 4.9 测试纯函数性：同一消息同一开关状态两次判定结果相同，且过程无任何持久化写入与消息投递

### 4.x 落地偏离登记（U4 实施，2026-08-28）

本章按 `docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU4-outbound-gate-pure-functions.md`
实施，落地时相对本文件 / `delivery-units.md` 字面有八条偏离。**方向全部是"更严"或"信息不丢"**，
没有一条放松。实测 `tests/test_outbound_gate.py tests/test_outbound_gate_structure.py` 124 passed，
全量 675 passed。落码 commit：`5e3fe79`（契约与词表）、`b3f8c46`（证据三态）、`32ccb14`（六条判定与
裸对象主防线）、`534d310`（两道闸）、`9475543`（异常按拦截与纯函数性）、`de0eb62`（review round 1
七条）、`b83f081` + `adcee86`（第七条 fail-closed 与 spec 同步）、`121713f` + `bcc41a1`（D-6 取 (b) 与 spec 同步）。

| # | 文件字面 | 实际落地 | 方向 / 理由 |
|---|---|---|---|
| 1 | 4.1 的 Protocol 字段是五个（`message_type` / `requires_confirmation` / `severity` / `recipient` / `body`） | 六个，多一个 `confirmed_by` | **保签名**。design D4 把签名定死为 `compute_outbound_gate(message, outbound_enabled)` 两参，而 4.7 要求判定"带 `confirmed_by`"。`confirmed_by` 只能挂在消息上。缺失方向是拦截，无 fail-open 风险 |
| 2 | 4.5「支持传 callable」 | **只**接受 callable，传 bool 判拦截 | **更严**。"支持"是允许，落地升级成强制——把 `delivery-units.md` §3.5 硬约束 1「禁止把它读成一个常量」从约定变成类型上做不到。`test_a_non_callable_switch_is_structural_misuse_and_blocks` |
| 3 | 4.2 的 `GateDecision` 是三字段（`allowed` / `reason` / `evidence`） | 五字段，多 `absent_fields` 与 `error` | **信息不丢**。U2 已落地的 `DecisionEvent.evidence` 是扁平 `dict[str, Any]`，absent 与 None 在扁平 dict 里必然同形；区别挪到 `absent_fields` 承载，`evidence` 保持 U5 可直接消费。`test_absent_attribute_is_distinguishable_from_an_explicit_none` |
| 4 | `delivery-units.md` §2.U4 只写了 `tests/test_outbound_gate.py` | 拆成行为面 + 结构面两个文件 | **可读性**。结构面读源码解析 AST，与行为用例不共享 fixture 也不共享失败信号；混在一起 `-k` 跑不开，reviewer 也难一眼看出结构防线还在不在 |
| 5 | spec 未规定判定顺序 | 六条 fail-closed 先判，两道闸最后判 | **口径**，见 D-3。总开关先判会在 U5 的观察期内把其余五条原因全部盖住。`test_awaiting_confirmation_wins_over_switch_off_so_the_observation_window_stays_readable` |
| 6 | spec「留痕记录判定所依据的各字段原始取值」 | `body` 不进 `evidence`，改记 `ai_label_present` 布尔 | **更保守**。拒信正文是候选人可识别内容；正文指纹由 U5 的 `content_hash` 承担。`test_evidence_never_carries_the_message_body` |
| 7 | spec 原本的六条拦截条件不含 `recipient` | **新增第七条拦截规则**：收件对象非空字符串才放行 | **更严**，见 D-2。✅ spec 已同步补第七条，`validate --strict` 通过，代码与 spec 一致。`test_unknown_recipient_is_blocked_per_the_2026_08_28_ruling`、`test_absent_recipient_attribute_is_blocked_too` |
| 8 | 4.4「复用 `AI_LABEL_TEMPLATE`」未规定匹配强度 | 取模板 `{generated_at}` 之前的**不变前缀全量匹配** | **最严的一侧**，见 D-1。`test_ai_label_prefix_is_pinned_verbatim`、`test_near_miss_labels_do_not_count_as_labelled` |

#### 五项口径 —— ✅ 已拍板（2026-08-28 Shao Peishen）

> **批准人：Shao Peishen（本项目唯一决策人）｜时间：2026-08-28｜事项：本节 D-1 至 D-5 全部**
> **依据：本人指示「五项拍板都按最保险落地确认」。** 留痕格式按 `CLAUDE.md`「决策代理」的要求。
> 其中 D-2 改变了已落地的行为，当次即改代码并补了回归测试；其余四项**当前落地本身就是最保险的一侧**，不改一行。

| 项 | 结论 | 是否改动代码 |
|---|---|---|
| **D-1** AI 标识判定强度 | **(a) 保持**：匹配 `AI_LABEL_TEMPLATE` 中 `{generated_at}` 之前的完整不变前缀。三个选项里最严的一侧 | 否 |
| **D-2** 空 `recipient` 是否拦截 | **(b) 改判**：新增**第七条** fail-closed 规则，收件对象读不出非空字符串即拦截；非字符串（dict/list）同样判未知 | **是** |
| **D-3** 拦截原因归属顺序 | **(a) 保持**：消息自身的畸形先判，两道闸最后判。「最保险」在这一项上不构成区分——两个选项下放行/拦截行为完全一致，差别只在留痕记哪一条 `reason`；判据是可观测性 | 否 |
| **D-4** 风险等级词表 | **(a) 保持** `("low","medium","high")`，最高级 `"high"`，实际过闸的只有 low / medium。**加 `critical` 反而更松**（`"high"` 会变成非最高级而放行），三档才是更严的一侧 | 否 |
| **D-5** 只接受 callable | **保持并追认**：传 bool 判拦截。把 §3.5 硬约束 1「禁止读成常量」从约定变成类型上做不到 | 否 |

#### D-6 —— ✅ 已拍板取 (b)（2026-08-28 Shao Peishen）

> **批准人：Shao Peishen｜时间：2026-08-28｜事项：D-6 放行路径口径｜依据：本人指示「b」**

**背景**：本文件 4.6/4.7 的原措辞与 spec 的三条原文互相矛盾——前者说六条 fail-closed 是彼此独立的**终局拦截**，后者（「这两类 MUST 一律判为高风险」+「高风险消息 SHALL 仅在携带 `confirmed_by` 时才被放行外发」+ Scenario「人工放行」）说它们是**风险分级的输入**、`confirmed_by` 是**清关**。取 (b) 前实现照本文件字面，后果是 `queue.approve()` 带确认人重走门禁仍被拦，**待审批队列里的候选人信件永远发不出去**，本变更包立项要建的人工放行能力从未生效。4.6/4.7 的措辞已于 2026-08-28 随本次回勾一并订正。

| 类别 | 哪几条 | 处置 |
|---|---|---|
| **消息畸形** | 未登记类型、确认标志读不出布尔、风险等级不在词表、缺 AI 标识、收件对象读不出非空字符串 | **终局拦截，⛔ 人也清不掉**。签字的前提是知道自己在签什么；允许 `confirmed_by` 清掉畸形，人工确认就成了「随便谁点一下就能发任何东西」的橡皮图章 |
| **已知的高风险** | 确认标志显式为真、风险等级为已登记的最高级 | **风险分级，不是终局**。由第一道闸的人清关，与 spec 三条原文一致 |

**没有被放松的东西**：两道闸仍**串联**（人签了字、总开关关着照样不发）；总开关仍每次求值；`confirmed_by` 仍要求非空字符串。

**判据（锁定用例）**：

- `test_confirmed_by_clears_a_known_high_risk_block_per_d6_option_b` —— (b) 的放行那一半
- `test_confirmed_by_cannot_clear_a_malformed_message`（5 条参数化）—— **(b) 更重要的那一半**，变异验证：让 `confirmed_by` 能清掉畸形后 15 条变红
- `test_a_plain_letter_without_a_confirmer_reports_exactly_the_spec_wording` —— spec 原文那句
- `test_a_cleared_high_risk_message_is_still_stopped_by_the_master_switch` —— 两道闸串联不变

**同步情况**：`specs/outbound-approval-gate/spec.md` 已补两类划分、清关范围限定、两个新 Scenario（`bcc41a1`），`openspec validate --strict` 通过。

#### ⚠️ 交给 U5 的一条硬约束

⛔ **U5 不得改 `app/outbound/gate.py` 与 `tests/test_outbound_gate.py`**。D-6 已于 2026-08-28 落码并同步 spec，翻转上面四条锁定用例＝把 D-6 修掉的那个 bug 装回去。U5 计划 `:29` 与 File Structure 表的 stale 描述已于同日订正。

## 5. 待审批队列与图节点接线

交付单元：门禁真正插入外发路径。**合并时 `CANDIDATE_OUTBOUND_ENABLED` 保持默认关闭**（全拦），观察拦截留痕符合预期后再由运维开启。

> **本章的两条前置（2026-08-28 Shao Peishen 裁决，落档见 `docs/findings/2026-08-28-Shao-Peishen-五条裁决落档.md` §4/§5）**：
>
> 1. **7.3 已从 U7 的尾部排期中提出，改为本章的前置项**——原先「U7 排最后」与 7.3 那句「U5 接线前必须确认本条已落地」互相打架，本次裁决解掉。7.3/7.4 已于 2026-08-28 落地并回勾（`db6596e`），产出 `docs/audit-and-outbound-ops.md`。⚠️ 该页第五节仍有三项 `.51` 上机留步，**其中第 3 项（在 `.51` 上按 4.1 实际创建一次开关文件）仍是本章开工前的未闭合前置**，属不可代项。
> 2. **开关文件路径按进程 CWD 解析：已拍板取「部署脚本里锁定工作目录」**（第 1 章「遗留二」）。本章接线时按此口径写说明，⛔ 不在代码里做路径兜底、⛔ 不重开该决定。

- [x] 5.1 `queue.py`：`pending_approval` 的读写与状态机（`pending` → `approved` / `abandoned`）；放行不 `DELETE` 而是改状态并记 `confirmed_by` 与 `resolved_at`；查询只返回 `pending`
- [x] 5.2 `queue.approve(id, confirmed_by)`：带 `confirmed_by` 重走门禁。**死锁防线（平台侧踩过）**：仅首道拦截（无 `confirmed_by`）才入队；放行复发被总开关拦下时不重复入队、状态保持 `pending`，可在开关开启后再次放行
- [x] 5.3 `effect_enqueue_pending_approval`：沿用现有 `idempotent_effect` 装饰器（**不改装饰器、不改 `effect_log`**）。**幂等策略**：`business_key` = 草稿内容哈希（复用 `message_business_key()` 的做法），幂等键 `{thread_id}:effect_enqueue_pending_approval:{content_hash}`；叠加 1.3 的 `content_hash` 唯一索引作第二道防线。函数体内不 `commit`，由装饰器统一提交
- [x] 5.4 `effect_record_outbound_audit`：沿用 `idempotent_effect`。**幂等策略**：`business_key` = `{content_hash}:{allowed}`，同一草稿的"拦截"与"放行"各留一条痕、重放不重复留痕
- [x] 5.5 外发路径接线：`compute_outbound_gate` 判定 → 按结果分流到 `effect_enqueue_pending_approval` 或既有 `effect_deliver_message`，两条路径都走 `effect_record_outbound_audit`。**不改 `effect_deliver_message` 内部逻辑、不改 `Channel` Protocol**
- [x] 5.6 测试端到端拦截：一封无 `confirmed_by` 的拒信 → 未投递、入队为 `pending`、留痕含拦截原因与判定字段原始取值
- [x] 5.7 测试端到端放行：队列 `approve` + 总开关开启 → 投递发生、队列转 `approved`、留痕动作类型为「已发送」且含 `confirmed_by`
- [x] 5.8 测试重放安全：外发相关节点被从头重跑 → 已外发不重复外发、已入队不重复入队（`effect_log` 命中短路）
- [x] 5.9 测试内部通知不受影响：岗位画像确认卡片不经候选人门禁，M1 现有投递行为与本变更前一致（回归）

### 5.x 落地偏离登记（U5 实施，2026-08-28 起 / 2026-08-30 续跑，全分支终审通过）

本章按 `docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU5-queue-and-wiring.md` 实施。
分支 `worktree-audit-u5-queue-and-wiring`，10 笔提交，全量实测 **720 passed**
（main 侧 675 → 本分支 720，增量 45 条）。

✅ **本章 9/9 已回勾（2026-09-03，0903E）**：回勾判据「final review 通过**且已合并**」两项均已满足——
`.51` §5-3 前置已闭合（`docs/audit-and-outbound-ops.md` §五第 2、3 项 2026-09-03 三次实跑标 ✅ 已闭合），
分支 `worktree-audit-u5-queue-and-wiring` 已合并回 `main`（merge commit `06a55d2c`，`--no-ff`；
`git rev-list --count main..worktree-audit-u5-queue-and-wiring` = 0，确认无残留提交）。
合并后全量 `pytest` **720 passed**、0 failed（合并前 main 基线 675，本章增量 45 条如数并入）。
分支已随合并以 `git branch -d` 删除。

| # | 本文件字面 | 实际落地 | 判据（哪条测试咬住它） |
|---|---|---|---|
| 1 | `delivery-units.md:26` 给 U5 列的触碰文件是 `queue.py`｜`nodes.py`｜`build.py` | 新增 `app/outbound/messages.py` 与 `app/outbound/delivery.py` 两个文件 | 门禁要的六个字段在既有 `OutboundMessage`（只有 `type`/`payload`）上不存在，必须有具体形状承载；编排逻辑放 `queue.py` 会形成 `queue → nodes → queue` 循环 import。`tests/test_outbound_gate_structure.py::test_gate_purity_scope_excludes_only_the_registered_non_gate_modules` |
| 2 | `delivery-units.md:26` 把 `app/graph/build.py` 列进 U5 的触碰文件 | **⛔ 一行没改** | `build.py` 里是**采集图**，它投递的 `question`/`confirmation_prompt` 是发给业务经理的内部通知，spec「内部通知不受影响」明令不走候选人门禁。候选人外发是独立入口 `deliver_candidate_message`。方向是更严不是更松。`test_the_intake_graph_cannot_reach_the_candidate_gate`（结构，带阳性对照）+ `test_internal_notifications_still_deliver_unconditionally`（行为） |
| 3 | 本文件与 `delivery-units.md` 都没规定 `severity`/`requires_confirmation` 的默认值 | `severity` 默认最高级、`requires_confirmation` 默认 `True` | spec「门禁覆盖范围」逐字「拒信与邀约这两类 MUST **一律**判为高风险」。默认值写反的话，一封忘记显式设置的拒信会走"低风险"路径直接发出去——默认值必须站在红线这一侧 |
| 4 | 5.8 计划原文的重放测试只断言 `effect_log` 条数 | 改为断言 **JSONL 镜像行数**（同一 `event.id` 恰好 1 行） | 🔴 只断言 `effect_log` 是**假绿**：`idempotent_effect` 重放时返回 `None`、函数体根本没跑，`effect_log` 由幂等键天然恒为 1 条，对"镜像被写重了没有"零分辨力。而外发事件在 `SqliteSink` 里没有真身（`SUPPORTED_EVENT_TYPES` 只收 `ai_analysis`），JSONL 那一行是唯一留痕，`reconcile()` 比的是 id **集合**差集，同 id 出现三次对它完全隐形。变异验证：撤销 `70de7b2` 的重放守卫后本条单独转红（实测「同一个 event.id 在 JSONL 里出现了 3 次」），其余四条仍绿 |
| 5 | 5.5 未规定开关文件路径口径的落点 | 口径（部署脚本锁定工作目录，Shao Peishen 2026-08-28 拍板）写进 `app/outbound/delivery.py` 模块 docstring | 运维页那一半 7.3/7.4 已写（`docs/audit-and-outbound-ops.md` §1.1 表格 + §3.1），本次只补接线处说明。⛔ 代码里不做路径兜底——兜底＝在合规开关上放松：从错误目录拉起的进程本该读不到开关文件而**全拦**，兜底会让它反而读到别处的开关并**放行** |

**⚠️ 终审发现一条真缺陷，已登记 TD-9，⛔ 本单元不修**：
**同一草稿的第二次及以后的拦截永远不留痕。** 实测（2026-08-30）：首次拦截 ✅ 留痕、
最终成功放行 ✅ 留痕，但中间"人点了放行、被总开关拦下"这次尝试**零留痕**，
违反 spec「外发与拦截动作强制留痕」的 `系统 SHALL 对每一次外发尝试留痕，无论结果是
放行还是拦截`。两条成因**叠加**，缺一条都不足以解释：
① `queue.approve()` 在 `decision.allowed` 为假时**早返回**，`deliver_candidate_message`
   根本没被调用，自然没有留痕；
② 就算改成无条件调用它也仍然无效——`business_key` = `{content_hash}:{allowed}`
   （本文件 5.4 **字面规定**）只区分"拦截 vs 放行"，不区分**是哪一条拦截**，
   于是第二次拦截撞上首次拦截的 `effect_log` 行、装饰器返回 `None`、镜像被跳过。

**为什么只登记不修**：修它要同时改本文件 5.4 字面规定的幂等键公式（拟改为
`{content_hash}:{allowed}:{reason}`，仍满足 5.4「重放不重复留痕」的原意）**并**给
已过审的 Task 2 的 `queue.approve()` 加审计依赖（签名变更）。这是计划/契约层的变更，
且触碰合规路径上的留痕语义，⛔ 不在无人值守的续跑 session 里自行拍板重设计。
**影响面已界定：闸门本身完好，没有任何不该发的消息会被发出去，丢的只是可观测性**；
且 TD-8 已登记本单元在生产里没有调用方，现网不受影响。

## 6. 合规断言、对账与 CI

交付单元：红线被破坏时 CI 直接红。

- [x] 6.1 `assertions.py` 断言一：以 AI 评分为理由的拒绝记录数恒为 0（`rejection_record.reason_type='ai_score'`；该表尚不存在时断言以「表不存在即通过、表存在则必须为 0」的形式实现，M2 建表后自动生效）
- [x] 6.2 `assertions.py` 断言二：`criterion_score` 中 `evidence_ref` 为空的记录数恒为 0（`CHECK` 之上的纵深防御）
- [x] 6.3 `assertions.py` 断言三：`criterion_score.criterion_key` 不存在白名单外的取值
- [x] 6.4 `assertions.py` 对账查询：按 `analysis_run.id` 比对 SQLite 与 JSONL 两侧记录集合，差集非空即报告（D1 的检出手段）
- [x] 6.5 `assertions.py` 拦截统计查询：按 `message_type` 与拦截原因统计次数，使「某类消息一直在被拦」可被发现（fail-closed 误拦的兜底观测）
- [x] 6.6 三条断言 + 链校验（`verify_chain()`）接入测试套件与 CI；任一条不成立即判失败并指出违例记录
- [x] 6.7 测试断言本身有效：故意插入一条以 AI 评分为理由的拒绝记录 / 一条白名单外的 `criterion_key` → 对应断言必须失败（防止断言写成恒真）

### 6.x 落地偏离登记（U6 实施，2026-09-03，全分支终审 + 一轮 fix wave 通过）

本章按 `docs/superpowers/plans/2026-09-03-ai-audit-trail-unitU6-assertions-and-ci.md` 实施，
合并 commit `e5e8e33`（分支 `claude/audit-u6-assertions-dev-0903g`，5 个实现 commit + 2 个终审修复 commit）。
落地相对本文件与既有注释字面有六条偏离，**方向都是「更严」或「更准」**。

| # | 字面 | 实际落地 | 判据（哪条测试咬住它） |
|---|---|---|---|
| 1 | 6.5「按 `message_type` 与拦截原因统计」，`app/outbound/gate.py:50` 注释指向 `pending_approval.blocked_reason` | 数据源改为 **JSONL 镜像**，不查 `pending_approval` | 只查 `pending_approval` 会系统性漏掉两类：① 外发**放行**事件根本不入队；② 放行复发被拦时不入队（`app/outbound/delivery.py` 的 D5 死锁防线只对首道拦截入队）。而「某类消息是不是一直在被拦」恰恰要拿拦截数和放行数对照才答得出。`test_delivered_events_are_counted_separately` / `test_always_blocked_types_is_the_actionable_signal`。⛔ 未改 `gate.py` 那句注释（越界改 U4 的文件），只登记 |
| 2 | 6.1「表不存在即通过、表存在则计数必须为 0」 | 多一条分支：**表存在但缺 `reason_type` 列 → 判失败** | 字面只写了两条分支，第三种情况（M2 建表时列名与本仓库常量不一致）会落进「查不到违例」从而静默通过。fail-closed：验不了红线不算守住了红线。`test_ai_score_rejection_assertion_fails_when_reason_column_missing` |
| 3 | 6.6「三条断言 + 链校验接入测试套件与 CI」 | 除接入外，另加 `python -m app.audit.assertions` 巡检 CLI，退出码 0/1/**2** | CI 的库是空的，三条断言在那儿恒真——真正有数据可查的是 `.51`。退出码 2（路径不存在）单列是关键：指错路径的巡检若返回 0，读的人会以为红线守住了。`test_exit_two_when_db_missing` / `test_exit_two_when_mirror_missing`（终审已实跑真实 CLI 复核：全绿→0、`--db` 不存在→2、`--mirror` 不存在→2） |
| 4 | 6.7「故意插入违例 → 对应断言必须失败」 | 除计划规定的三次注入外，**终审又跑了 5 次独立变异，抓出 2 处幸存缺口并当场封堵** | 详见下方「6.7 反证的实测台账」。这两处正是 6.7 要防的那种恒真，却躲过了计划自带的三次注入——**说明「反证文件存在」本身不等于「反证有效」**，判据只能是变异实测 |
| 5 | 计划未提 | `pyproject.toml` 注册 `compliance` marker（3 行），超出 0903G opener 的 `git add` 白名单 | CI 的可归因步骤 `pytest -m compliance` 需要该 marker 注册，否则每次 CI 都刷 `PytestUnknownMarkWarning`。该文件不在并行泳道 0903H 的触碰面内，逐条显式 `git add`、⛔ 全程未用 `git add -A`。登记于此 |
| 6 | 6.5「使『某类消息一直在被拦』可被发现」 | **⚠️ 撞上 TD-9，本单元未修，统计存在已知盲区** | TD-9（`docs/tech-debt.md:225`）：同一草稿的**第二次拦截**走 `queue.approve()` 早返回，且幂等键 `{content_hash}:{allowed}` 不含 reason → 撞 `effect_log` 已有行 → `idempotent_effect` 返回 `None` → 镜像被跳过，JSONL 里**一行都没有**。因此 6.5 对「放行复发被拦」这一整类系统性缺席——**恰恰是最该被看见的那批**。TD-9 原文已预言此事。修它要同时改已过审的 `approve()` 签名与本文件 5.4 字面规定的幂等键公式，属计划/契约层变更且触碰合规留痕语义，**Shao Peishen 尚未裁决，⛔ 本单元只登记不修** |

**6.7 反证的实测台账**（本章的价值全在这张表——「0 命中」同时兼容「红线守住了」和「断言没生效」，只有变异能分开这两者）：

| 变异（把断言改成恒真） | 结果 | 谁跑的 |
|---|---|---|
| 断言一 `ok=not rows` → `ok=True` | 🔴 `test_ai_score_rejection_is_detected` 等变红 | implementer |
| 断言二 三参 `trim` → 单参 `trim()` | 🔴 `[tab]`/`[newline]`/`[carriage-return]`/`[mixed-whitespace]` 变红（`[empty]`/`[space]` 保持绿，符合 SQLite 单参语义） | implementer |
| 断言三 白名单塞进 `handwriting_style` | 🔴 `test_unknown_criterion_key_is_detected` 变红 | implementer |
| 断言三 比较条件改恒真 `WHERE 1 = 0 AND …` | 🔴 12 failed / 10 passed | controller（独立复核） |
| `chain_assertion` 强制 `ok=True` | 🔴 2 failed / 5 passed | task-3 reviewer |
| 拦截统计丢弃缺 `message_type` 的事件 | 🔴 `test_missing_type_and_reason_get_explicit_buckets` 变红 | task-4 reviewer |
| **`REJECTION_TABLE`→`"rejection_records"` + `AI_SCORE_REASON`→`"ai_score_v2"`** | ⚠️ **原本全绿（43 + 771 全过）＝ 红线一被静默停用** → 补 `test_rejection_constants_are_pinned_to_the_red_line_wording` 后 🔴 1 failed / 44 passed | 终审发现，controller 复核封堵 |
| **删掉 `criterion_key IS NULL OR` 守卫** | ⚠️ **原本全绿（45 全过）** → 补 `test_null_criterion_key_is_detected_by_the_real_assertion`（直接调生产函数）后 🔴 1 failed / 44 passed | 终审发现，controller 复核封堵 |

后两行是本单元最值钱的两条：反证文件**自己**曾在两个维度上恒真。第一处的成因是夹具表用**被测的那三个常量**建的，没有任何东西钉住 `CLAUDE.md` 里的字面量 `rejection_record` / `reason_type` / `ai_score`；第二处的成因是替代测试自建表并手抄 WHERE 子句，**从不调用生产函数**。两者都已改为直接绑定生产代码。

**终审 parked（真实但不承重，带裁定，⛔ 本单元不做）**：

1. `app/audit/assertions.py` 与 `.github/workflows/ci.yml` 都指向 `docs/audit-and-outbound-ops.md` 讲巡检口径，但该文档尚未写这件事。裁定：写运维文档超出本单元范围，且两处指针正上方都已内嵌完整命令行，操作者不会卡住。留作 7.3 的后续补写。
2. `--db` 指向一个存在但无 schema 的文件时，断言二/三抛 `sqlite3.OperationalError` → 退出码 1，与「发现违例」撞码。裁定：输出是 traceback，⛔ 不可能被读成「红线守住了」，与退出码 2 要防的「安静返回 0」不是一个量级的风险。

**⏸ 留步（本单元不闭合，需 `.51` 上机）**：巡检 CLI 从未对着 `.51` 的真实 `data/demo.db` 与
`data/audit/decisions.jsonl` 跑过。首次上机巡检属发版动作（生产服务器 `.51` 的发版决定为
**不可代项**），登记在此，待 Shao Peishen 安排。


## 7. 边界守护与文档

交付单元：本变更的三条硬边界变成机器可查，人为破坏会被 CI 挡下。

- [ ] 7.1 CI 检查：`app/` 下禁止出现 `from zhuopin_platform` / `import zhuopin_platform`；禁止 `sys.path` 指向 OneDrive 路径的注入
- [ ] 7.2 CI 检查：`requirements.txt` 与 `pyproject.toml` 不含 `zhuopin_platform`；本变更的依赖文件 diff 必须为空
- [x] 7.3 `docs/` 增一页说明留痕与门禁的运维口径：JSONL 路径与备份、链校验怎么手动跑、`CANDIDATE_OUTBOUND_ENABLED` 的开关流程与「不提供一键放行全部」的理由 → **`docs/audit-and-outbound-ops.md`**（2026-08-28 落地。编码约束在第四节，四种写法已用字节级实测逐条验证；链校验命令已在开发机实跑并贴输出。⏸ 三项留步待 `.51` 上机闭合，见该页第五节：备份任务是否已覆盖 `data/`、`.51` 上链校验首跑输出、`.51` 上按 4.1 实际创建一次开关文件——**U5 接线前需完成第 3 项**）
  - **⚠️ U1 发现、U7 承接（2026-08-27 Shao Peishen 拍板取方案 (b)，见第 1 章「遗留一」）：本页必须写入开关文件的编码约束。** 规定唯一允许的写法是 `[System.IO.File]::WriteAllText($path, 'true')`；**⛔ 禁止** PowerShell 的 `Out-File` / `>` / `>>`（默认 UTF-16LE）与记事本的"UTF-8"另存（带 BOM）。`_read_switch_file()` 不剥 BOM、不认 UTF-16，用错写法的症状是**开关静默不生效且不报错**（方向 fail-closed，拦住了但打不开）。他明确选择不改代码——改代码剥 BOM 属「在合规开关上放松」，是不可代项。**U5 接线前必须确认本条已落地**，否则总开关在 `.51` 上不具备可操作性
- [x] 7.4 `06-企业AI转型资产借鉴清单.md` 追加本次借鉴记录：借的四条做法、自建的对应模块、**明确未引入依赖未拷贝代码** → **§10「本次借鉴记录」**（2026-08-28 追加。四条编排做法逐条给了自建落点行号，留痕侧另记 §10.2；两条判据实跑输出已贴：依赖 diff 零行、`grep zhuopin_platform app/` 退出码 1 零命中）
- [x] 7.5 技术债登记：`operator_id` 现阶段不可信（鉴权空壳）；企微 OAuth SSO 待两侧共同决定，是 M2 处理真实简历前的阻塞项之一（另一半留痕已由本变更完成）→ **`docs/tech-debt.md` TD-6**（2026-08-28 登记，触发条件＝部署约束 5 的「M2 起处理真实简历前」）
- [x] 7.6 技术债登记：JSONL 写入侧仅进程内锁，假设单进程部署；M2 迁 Postgres 时需重新处理并发写与 JSONL 的关系 → **`docs/tech-debt.md` TD-7**（2026-08-28 登记，触发条件＝M2 迁 Postgres 或 `.51` 部署形态转多进程，孰先触发）
