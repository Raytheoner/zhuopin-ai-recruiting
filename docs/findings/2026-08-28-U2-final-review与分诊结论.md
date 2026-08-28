# U2（`app/audit`）补跑全分支 final review 与 11 条 deferred minor 分诊结论

- **日期**：2026-08-28
- **执行**：opener `[Mac]0828D-U2补跑final review与minor分诊`（无头，run-lanes.sh 起）
- **审查对象**：交付单元 U2 的 8 个新文件（合并区间 `42fd90f..0fa4d54`，其中 Task 2 起的分支 diff 为 `c1ff33c..0fa4d54`）
- **输入材料**：`docs/findings/2026-08-26-U2-deferred-minors-与final-review缺口.md`
- **审查透镜**：逐字取自 `docs/superpowers/plans/2026-08-26-ai-audit-trail-unitU2-audit-module.md` 的 Global Constraints 段与 `CLAUDE.md`「工程铁律」
- **本文件是建议，不是处置。** 不改一行 `app/`、`tests/`、`openspec/`；不勾任何 checkbox

## ⚠️ 执行方式登记（如实）

**`superpowers:subagent-driven-development` 在本 session 里调不到**——无头 session 取不到项目作用域插件（2026-08-27 已实测，见 `.claude/handoff/lanes-20260827-160205-看护报告.md` §五-①），且本机 `~/.claude/plugins/marketplaces/` 下只有 `claude-plugins-official`，磁盘上**也没有** superpowers 的 SKILL.md 可照抄。

因此本轮**按 plan 的 Global Constraints 段与 `CLAUDE.md` 工程铁律手工执行同一套协议**：先立机械判据，再对每条判据跑变异（mutation）确认它是"load-bearing 的断言"而不是"恒真的绿"，最后才下结论。⛔ 没有假装调过 skill。

**变异全部在沙箱副本里跑**（`scratchpad/sandbox/`，`cp -R app tests pyproject.toml`），⛔ 仓库工作区一次都没有被改动过——本仓库此刻有并行泳道，在真工作区里改 `app/audit/sinks.py` 哪怕十秒，都可能让别的泳道的 pytest 撞上一个变异过的实现。沙箱基线 `80 passed`，五轮变异后复原回归仍 `80 passed`。

---

## 一、Final review 结论

### 判定：**通过（Approved）**。无 Critical、无 Important。

U2 的四条头号约束**全部落地，且每一条都有一条真正会变红的测试守着**。下表每一行都是实跑出来的变异证据，不是读代码得出的印象。

| # | 约束（透镜） | 变异 | 结果 | 判定 |
|---|---|---|---|---|
| M1 | 铁律 1 ① `SqliteSink.write` ⛔ 不自行 commit | 在 `sinks.py:169 return True` 前插入 `self.conn.commit()` | `3 failed, 77 passed` — `test_record_does_not_commit`、`test_write_does_not_commit`、`test_rollback_undoes_the_whole_event` | ✅ load-bearing |
| M2 | 铁律 1 ② `record()` 断言同一连接 | 把 `recorder.py:86` 的条件改成 `if False:` | `1 failed, 79 passed` — `test_record_rejects_a_foreign_connection` | ✅ load-bearing |
| M3 | design D3 分水岭：缺 `prev_hash` 仅第 1 行豁免 | 把 `sinks.py:395` 的 `if index > 1:` 改成 `if index > 0:` | `2 failed, 78 passed` — `test_all_prev_hash_fields_stripped_breaks_at_line_two`（**位置断言**变红，不是 `ok` 变红）、`test_line_one_may_omit_prev_hash` | ✅ load-bearing，且**边界两侧都锁住了** |
| M4 | 铁律 4 `evidence_ref` 为空必须原样抛 | 把 `sinks.py:165-167` 换成裸 `return False` | `8 failed, 72 passed` — 含 `test_empty_evidence_ref_is_not_swallowed[\t]`、`test_failed_score_leaves_no_orphan_run` | ✅ load-bearing |
| M5 | design D1 `AuditRecorder` ⛔ 无打包方法 | 给 `recorder.py` 加一个 `record_all()` 同时触碰两个 sink | `1 failed, 79 passed` — `test_recorder_exposes_no_packed_method`（AST 守护） | ✅ load-bearing |

**"守护恒真"这个本单元栽过两次的坑，这一轮没有再出现。** 三条结构守护（`test_recorder_exposes_no_packed_method`、`test_no_effect_function_appends_jsonl`、`test_audit_module_imports_no_config_or_graph`）各自带阳性对照，且对照与真断言**共用同一个检查函数**（`_functions_touching_both_sinks` / `_effect_functions_touching_the_mirror` / `_modules_importing_config_or_graph`），所以对照不会与它保护的那条断言漂开。M5 是对这一点的独立复核：一个真实的打包方法确实被抓住了。

### 逐条透镜复核

- **铁律 1**：`app/audit/sinks.py` 与 `app/audit/recorder.py` 全文无 `commit()`（`grep` 实测）。`record(conn, event)` 那个"功能上冗余"的 `conn` 参数确实兑现了它存在的唯一理由——M2 证明拿掉断言会有测试变红。
  ⚠️ 一处**口径已被 U3 打破，但不是缺陷**：plan 的机械判据写的是「`app/audit/` 下不得出现新增的 `conn.commit()` 调用点」，而 U3 的 `app/audit/hook.py:248` 有一处。它带三条完整理由（钩子触发点没有 conn／复用共享连接会被 `idempotent_effect` 的 rollback 连坐／专属连接上只有一个事务管理者）并经 Shao Peishen 2026-08-28 追认。**该订正的是 plan 判据的措辞（限定到 `sinks.py`/`recorder.py`），不是代码。**
- **铁律 3**：`DecisionEvent` 逐字覆盖模型标识／版本／prompt 版本／temperature／输入哈希／rubric 快照／原始响应，`configured_model` 与 `response_model` 分列不互相覆盖。`test_missing_reproducibility_column_raises[prompt_version|temperature|input_hash]` 在 M4 下一并变红，说明这三列的落盘是被断言盯住的。
- **铁律 4**：本层不做重复校验、不做兜底，只负责不吞 `IntegrityError`——M4 证明这道"不吞"是唯一且有效的防线。`CriterionScore.evidence_ref` 由 U1 的 DB `CHECK` 强制。
- **design D1 两段式**：`record()` 只碰 `self._store`，`mirror()` 只碰 `self._mirror`，无打包方法（M5）。U3 的 `RecorderAuditHook.record()` 在**调用点**顺序调用两段，但 `mirror()` 发生在 `self._conn.commit()` **之后**（`hook.py:245-249` → `187-193`），且 mirror 失败只记日志不抛——正是 plan 规定的落地形态与"允许的单向偏差"。**D1 未被 U3 削弱。**

### 本次 final review 新查出的 5 条（不在原 11 条里）

编号 F-A ~ F-E，与下面的分诊表分开列，避免和"11 条"混淆。

| # | 发现 | 位置 | 证据 | 严重度 | 建议 |
|---|---|---|---|---|---|
| F-A | **整段尾部被删除，`verify_chain()` 仍返回 `ok=True`。** docstring 只声明"检不出最后一行被**修改**"，实际"删掉尾部 N 条"同样检不出 | `app/audit/sinks.py:362-363`（docstring）、`345-418` | 写 5 条 → `ok=True, total=5`；截到只剩前 2 条 → **`ok=True, total=2`**，无任何报告 | **中** | **本批修**（改 docstring 措辞 + 落一条技术债），见下 |
| F-B | `query()` 带过滤时仍 `SELECT * FROM criterion_score` **全表扫**，把整张评分表读进内存再分组 | `app/audit/sinks.py:222-224` | `set_trace_callback` 实抓两条 SQL：`SELECT * FROM analysis_run WHERE application_id = 'app-1' …` / `SELECT * FROM criterion_score ORDER BY criterion_key`（**无 WHERE**） | 低 | 挂技术债（触发条件＝`criterion_score` 上万行，或 M2 迁 Postgres） |
| F-C | `rubric_snapshot` 列里若不是 JSON **对象**，`query()` 抛 `AttributeError` 而不是报告坏数据 | `app/audit/sinks.py:230-232` | `UPDATE analysis_run SET rubric_snapshot='["legacy"]'` → `AttributeError: 'list' object has no attribute 'get'` | 低 | 挂技术债（触发条件＝出现非 `SqliteSink` 的 `analysis_run` 写入方）。当前 `analysis_run` 是 U1 新建表、唯一写入方就是本 sink |
| F-D | `record()` 的事务归属断言在 store **没有 `conn` 属性**时静默跳过（`getattr(..., None)`） | `app/audit/recorder.py:85-86` | 用一个无 `conn` 属性的假 sink + 一条毫不相干的连接调 `record()` → 返回 `True`，**未抛** `TransactionOwnershipError` | 低 | **判定不成立可关闭**：`AuditSink` Protocol 本就不含 `conn`，生产装配恒为 `SqliteSink`，断言恒生效；影响面仅限测试替身 |
| F-E | plan 机械判据「`app/audit/` 下不得新增 `conn.commit()`」已被 `hook.py:248` 打破 | `app/audit/hook.py:248` | `grep -rn "commit()" app/audit/` | 低（口径，非缺陷） | 订正 plan 判据措辞，⛔ 不改代码 |

**F-A 值得单独说一句，它是这批里唯一够得上"中"的新发现。** 补偿控制存在但**不完整**：`reconcile()` 比对 `analysis_run.id` 集合，所以被删掉的 `ai_analysis` 留痕会落进 `missing_in_mirror`；但 `sinks.py:110-114` 明写「补录事件只存在于镜像链上」、外发事件的真身在 `pending_approval`——**这两类记录被从链尾删掉，两侧都无从发现**。这不改变 U2 的合格判定（哈希链检不出尾部截断是这类结构的固有性质，plan 也没要求锚定），但 docstring 现在的措辞会让读者以为敞口只有"最后一行"那么大。

---

## 二、11 条 deferred minor 分诊表

⚠️ 每一行的「现在还成立吗」都是**跑去看过源码 / 实跑复现**得出的，不是照抄清单描述。行号为 2026-08-28 当前 HEAD (`db6596e`) 的实际行号。

| # | 条目（原文一句） | 位置 file:line | 现在还成立吗 | 严重度 | 建议 | 依据 |
|---|---|---|---|---|---|---|
| 1 | T1 `to_dict()` 返回 dict 值字段（`rubric_snapshot`/`token_usage`/`evidence`）**按引用**，未深拷贝 | `app/audit/events.py:149-155` | **仍成立**。实测：`d = ev.to_dict(); d["rubric_snapshot"]["k"]="TAMPERED"` 之后 `ev.rubric_snapshot == {'k':'TAMPERED'}`，`d[...] is ev.rubric_snapshot` 为 `True`——一个 frozen dataclass 被从返回值改掉了 | 低 | **挂技术债**（触发条件＝出现第一个持有并就地修改 `to_dict()` 返回值的消费者） | 当前唯一消费者是 `JsonlChainSink._append`（`sinks.py:287-296`），它 `body = dict(payload)` 后立刻 `json.dumps`，不持有引用；U3 的 `hook.py` 也只是把 dict 传进构造器。没有实际受害者，深拷贝的成本却落在每一次留痕写入上 |
| 2 | T1 `test_all_registered_event_types_construct` 用 AI 形状 fixture 造全部事件类型，只证构造、不证 per-type 字段形状 | `tests/test_audit_events.py:85-87` | **仍成立**。四种类型全走 `_analysis_event(event_type=…)`，`outbound_*` 的 `recipient`/`content_hash`/`blocked_reason` 一个都没断言 | 低 | **挂技术债**（触发条件＝U4/U5 开始真正构造 `outbound_blocked`/`outbound_delivered` 事件时，随那个单元补 per-type 形状断言） | U2 的职责就是"只提供形状"（plan「U4/U5 消费，U2 只提供形状」）。在还没有消费者的时候写 per-type 断言，锁的是猜出来的形状 |
| 3 | T2 `_is_analysis_run_pk_conflict` 对 SQLite 异常**文本做子串匹配**，措辞漂移即失效 | `app/audit/sinks.py:238-240` | **仍成立**，但**已被现有测试盯住**：措辞一变，判据返回 `False` → `raise` 而不是短路，`test_duplicate_primary_key_short_circuits`（`tests/test_audit_sinks_sqlite.py:158-165`）当场变红 | 低 | **判定不成立可关闭** | 两点：① 失效方向是 fail-loud（抛异常）而非静默放过，是安全的那一侧；② 已有回归测试覆盖，不需要额外技术债条目。sqlite3 不暴露结构化的约束名，子串匹配是标准库下唯一可行的判据 |
| 4 | T2 `score.id or f"{event.id}:{key}"` 把**显式设成空串**的 id 当作未设置 | `app/audit/sinks.py:145` | **仍成立**。实测 `CriterionScore(..., id="")` → 落库用的是 `evt:skill_match` | 低 | **判定不成立可关闭** | 空串不是合法主键值，回落到确定性 id 是**对的那一侧**——按"尊重调用方意图"写反而会插出一行 `id=''`，第二次调用就撞主键。真要改也只该是构造期拒绝空串，那属于 `CriterionScore` 的入参校验，不是这里 |
| 5 | T3 `task-3-report.md` 的自评声称跑过 no-op-lock 验证但**没粘贴失败输出** | 原件在 `.claude/worktrees/audit-module-u2/.superpowers/sdd/…/task-3-report.md`（git-ignored） | **成立但已失去意义**。该声称当时已被 reviewer 独立复现证实为真（真锁 3/3 pass、`_lock_for` monkeypatch 成 no-op 后 5/5 fail），且报告本体只存在于随时会被清掉的 worktree 里 | 无（流程留痕，非代码缺陷） | **判定不成立可关闭** | 报告严谨度问题，已由独立复现补足；对应的守护 `test_concurrent_appends_do_not_interleave`（`tests/test_audit_chain.py:126`）在版本库里，不随 worktree 消失 |
| 6 | T4 `broken_at` 与报错文案里的「第 N 行」是**记录序号不是物理行号** | `app/audit/sinks.py:365-416`，成因在 `_raw_lines()` `332-336`（`if line.strip()` 丢掉空白行） | **仍成立，且实测偏差可观**。构造 5 物理行（第 1 条后夹两个空行）、篡改**物理第 4 行**：报告 `broken_at=3`、文案「第 3 行的 prev_hash …」、`total=3`（文件实为 5 行）。审计员按报告去开第 3 行，看到的是一个空行 | **中** | **本批修** ★ | 这是执行者点名的两条之一。留痕的用途就是"出事时能查证"，一个把人指到错行的定位信息，在最需要它的那个场合失效。改法：`_raw_lines()` 返回 `(物理行号, bytes)`，报错文案同时给记录序号与物理行号；`total` 的语义在 docstring 里写明是记录数 |
| 7 | T4 `verify_chain()` **不取 `_lock_for` 写锁**，写方持锁时可能并发读 | `app/audit/sinks.py:345`（`_append` 在 `288` 取锁，`verify_chain`/`read_all`/`_raw_lines` 全不取） | **仍成立**（`grep` 确认 `_lock_for` 只在 `_append` 与其定义处出现）。撕裂读**仍未能复现**——`write`+`flush`+`fsync` 都在锁内，当时只有 2 MB 无缓冲写才拆开 | 低 | **本批修** ★ | 这是执行者点名的另一条。理由不是"复现出来了"，而是成本与收益极不对称：一行 `with self._lock_for(self._key):` 包住 `lines = self._raw_lines()`，零行为变化、零死锁风险（已确认 `_append` 内不调 `verify_chain`，无嵌套获取），换掉一个"没复现出来 ≠ 不存在"的一致性缺口。而 U6 的 CI 对账断言会**周期性**调用 `verify_chain()`，届时与写入并发是常态而不是意外。⚠️ 若采纳，`read_all()` 应同批处理，否则 `reconcile()` 仍是无锁读 |
| 8 | T4 `tail_hash` 在**每一处提前返回**上都是 `None` | `app/audit/sinks.py:372-414`（5 处 `return ChainVerification(ok=False, …)` 均不带 `tail_hash`） | **仍成立**。实测断链结果 `tail_hash=None` | 低 | **挂技术债**（触发条件＝U6 引入外部锚定，即把 `tail_hash` 定期落到独立介质时一并处理） | 链已断时的"尾哈希"本来就不可信，返回 `None` 是保守的那一侧；在没有锚定机制的今天，补上这个值也没有消费者 |
| 9 | T5 `tests/test_audit_recorder.py:84` `store.conn = conn` 是**死行**，注释声称的用途它并不承担 | `tests/test_audit_recorder.py:84`（注释写「满足事务归属断言」） | **仍成立**。该测试只调 `recorder.mirror()`；事务归属断言在 `recorder.record()` 里（`recorder.py:85`），`mirror()` 根本不读 `self._store`。`CountingSink`（`tests/test_audit_recorder.py:34-45`）本身也没有 `conn` 属性 | 极低 | **本批修**（顺手，删一行 + 删注释） | 代码无害，但注释在**撒谎**——它把"这条断言在 mirror 路径上也生效"写成了既成事实。下一个人照它推理就会推错。零风险、零行为变化 |
| 10 | T6 `backfill()` **不检查 `missing_id` 是否真的缺失**就写入；调用两次产生重复 `backfill` 事件，无守卫 | `app/audit/recorder.py:147-163` | **仍成立，且后果比清单描述的更重**。实测：对一个**两侧都齐全**的 `run-a` 连补两次 → 镜像多出两条 **id 完全相同**的 `backfill:run-a`，而 `reconcile().ok` **仍为 `True`**、`backfilled={'run-a'}`，链 `verify_chain().ok` 也仍为 `True`——凭空多出的两条"这条曾经缺过"的陈述，对账、链校验、id 唯一性三道关**一道都拦不住** | **中** | **本批修** ★ | 这是三条"本批修"里我认为最该先修的。其余两条是**定位信息**与**一致性**问题，这一条是**留痕内容真实性**问题：防篡改镜像里出现了一条与事实不符的记录，而这份镜像存在的全部意义就是"它说的话可信"。改法：`backfill()` 先跑 `reconcile()`，`missing_id` 不在 `unexplained_missing` 里就拒绝（抛，不是静默返回 `False`——静默会让误补者以为补上了）。⚠️ 注意 `reconcile()` 有 I/O 成本，若嫌重可退一步只做"同 id 不重复补录"的去重，但那拦不住"补一个根本没缺的 id" |
| 11 | T6 `test_reconcile_is_clean_when_both_sides_match` **没单独断言** `missing_in_store == frozenset()` | `tests/test_audit_recorder.py:328-339`（只断言 `result.ok is True` 与 `missing_in_mirror == frozenset()`） | **成立但不构成覆盖缺口**：`Reconciliation.ok`（`app/audit/recorder.py:61-62`）的定义就是 `not unexplained_missing and not missing_in_store`，`ok is True` **已蕴含** `missing_in_store` 为空 | 低 | **判定不成立可关闭** | 已有 `test_reconcile_detects_a_row_missing_from_the_store`（`tests/test_audit_recorder.py:315`）从正面锁住这一维。补一行显式断言可读性更好，但不是缺口——按"没有覆盖缺口就不新增断言"处理，⛔ 不把可读性偏好记成技术债 |

### 分诊统计

| 建议 | 条数 | 编号 |
|---|---|---|
| 本批修 | **4** | 6、7、9、10（★）＋ 新发现 F-A |
| 挂技术债 | **3** | 1、2、8（＋ 新发现 F-B、F-C） |
| 判定不成立可关闭 | **4** | 3、4、5、11（＋ 新发现 F-D） |
| 合计 | **11** | 与清单正文计数一致（⛔ 不是标题里那个 `(9)`） |

---

## 三、结论

1. **U2 的 final review 判定为通过。** 四条头号约束全部落地，五轮变异证明每一条都有真正会变红的测试守着；"守护恒真"这个本单元历史上栽过两次的坑没有第三次。**U5 建在 `AuditRecorder` 之上是安全的。**
2. **11 条 minor 里没有一条 Critical/Important**，最重的三条是「中」：`backfill()` 可以往防篡改镜像里写不实记录（#10）、断链定位会把审计员指到错行（#6）、尾部截断检不出且 docstring 低估了敞口（F-A）。
3. **这三条都不阻塞 U5**，但 #10 会随 U5 接线而进入真实使用路径（U5 一旦让 `AuditRecorder` 上线，`backfill()` 就成了运维手上的一个真按钮），**建议在 U5 接线之前修掉**。

---

## 四、需 Shao Peishen 定的

> 分诊属「两轮 review 之间的 Triage：bug 定级、返工与否」，`CLAUDE.md` 决策代理表把它列在**可代**——但**代理人至今未指定**，而「代理人未指定期间的默认：可代项同样一律挂起等本人」。所以下面全部**只提建议，不执行**。⛔ 无人值守 session 不替他拍。

### 要定的第一件：这 5 条要不要本批修

| 编号 | 一句话 | 改动面 | 我的建议 |
|---|---|---|---|
| **#10** | `backfill()` 可以往防篡改镜像里写"这条曾经缺过"的不实记录，三道关都拦不住 | `app/audit/recorder.py` 约 10 行 + 2 条新测试 | **修，且优先级最高**。留痕内容真实性 > 其余两条的定位与一致性 |
| **#6** | 断链报「第 3 行」而实际被改的是物理第 4 行 | `app/audit/sinks.py` `_raw_lines()` 改签名 + 5 处报错文案 + 1 条新测试 | **修**。留痕的用途就是出事时能查证 |
| **F-A** | 尾部整段被删检不出，且 docstring 措辞低估了敞口 | 只改 docstring + 落 1 条技术债（外部锚定归 U6） | **修 docstring 那部分**（零风险）；锚定本身不在本批 |
| **#7** | `verify_chain()` / `read_all()` 无锁读 | `app/audit/sinks.py` 2 行 | **修**。一行成本换掉一个说不清的缺口；U6 上线后并发读是常态 |
| **#9** | 测试里一行死代码 + 一句撒谎的注释 | `tests/test_audit_recorder.py` 删 1 行 | **修**（顺手） |

### 要定的第二件：修的话要不要单开一条 opener

**建议单开一条，我的理由有三条：**

1. **触碰文件与本条完全不同。** 本条只写 `docs/findings/`；修复要动 `app/audit/recorder.py`、`app/audit/sinks.py`、`tests/test_audit_recorder.py`、`tests/test_audit_chain.py`。混在一起会让"报告"和"改代码"共用一个提交边界，出问题时回滚粒度不对。
2. **#6 与 #7 都会动 `app/audit/sinks.py`，#10 动 `recorder.py`——彼此不重叠，但都与 U5（第 5 章，已出实现计划）触碰同一目录。** 需要按 `lane-dispatch` 的判据决定是串在 U5 之前还是并行，这个编排决定不该由本条自己做。
3. **改动要走 TDD + 两阶段 review**（每条修复都要有能变红的变异证据，标准与本轮 M1~M5 相同），那是 `run-build` 的形状，需要 worktree ✅ ——而本条明确 worktree ❌。

**建议的 opener 形状**（供参考，⛔ 本条不代发）：一条 `run-build` 形态的 opener，worktree ✅，范围锁死为上表五条，逐条要求粘贴变异证据，完成后回归 675 passed + 新增测试数。若只批一部分，建议的最小集是 **#10 + #6**（两条「中」），#7/#9/F-A 可并入同一条顺手做完。

### 不可代项

本轮**没有触碰**任何 `CLAUDE.md` 不可代项：无合规红线变更、无淘汰规则例外、无对外通道开关、无真实简历处理范围变更、无 `.51` 发版、无预算采购。

---

## 附：复现材料

变异脚本与探测脚本在本次 session 的 scratchpad（`…/scratchpad/probe.py`、`probe2.py`、`mutate.sh`、`sandbox/`），**不进版本库**——它们依赖沙箱副本，且上表已把每条的输入、操作与实得输出写全，照着重跑不需要原脚本。
