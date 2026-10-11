# 1011R 整合Final F1：邀约缓存前业务绑定修复

输入既有 spec：openspec/changes/interview-scheduling/specs/interview-invitation-drafting/spec.md。
整合Final发现五个邀约effect缓存命中可绕过对象/业务键校验。修现有契约，不改spec、不重跑
已完成排期Tasks1–12。当前本地整合分支全量4779passed/12skipped，F1修复后须重新验收。

## Global Constraints

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。

（由来与实证见 docs/CLAUDE-md-由来.md「工程铁律（不可违背）」）

### 部署约束（2026-08-09 定，详见 `04-部署与门户挂载.md`）

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
2. **过渡端口 8095**，登记技术债，触发条件 = 统一门户网关上线即迁移。
3. **鉴权中间件留空壳接入点**，签名对齐未来企微 OAuth SSO；将来只换实现不换调用方。
4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **模型全部走境内**，简历数据不出境。
- **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。
- 主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。


### Task 1: 五个邀约effect在缓存前绑定真实对象与约定业务键

本 Task 尚未完成：最近391项通过、Spec PASS，但质量FAIL：edit与backfill的fresh反证
只是换thread，不足以证明原thread下真实键参数错配。本次补齐edit与backfill，另外核对persist：
五个节点必须都有同thread、规范key、节点尚无成功缓存、实际键参数错配的首次调用反证。
edit原key对应正文A却传正文B；backfill原key对应sent却传confirmed；persist原key对应
run-A却传run-B。mark/sent已新增的3项fresh测试与五节点缓存命中测试全部保留。
每项拒绝且完整事实快照不变。现有实现已具备校验，测试即可，不重做旧修复。
重新跑联合验收，追加真实记录；保留历次测试与失败审查历史。必须逐项列出五节点fresh/cache
测试名称和所错配的键字段，禁止把wrong-thread或取消状态测试当作键参数错配矩阵。
审查时先读本轮task-1.pytest-evidence.json；原始事件逐行解析后只输出匹配pytest命令、
exit与最后摘要核验，禁止整打印大型events JSONL或其中的源码读取输出。

> 补充明确：缓存前绑定只查身份与键表达的参数，不能把“当前可操作状态”当身份。
> `_validate_slot_thread`只查slot→application归属，不做_assert_slot_invitable；原业务状态校验
> 保留在cache miss节点函数。不得新增“取消场次不能编辑/标记历史草稿”的限制（原函数没有）。
> sent的缓存前只校验key/显式body_digest参数、真实draft.slot归属，当前draft正文摘要校验留
> 原函数cache miss内。合法原send key在后续编辑草稿后重放仍None，历史发送事实不变。
> 补已有成功键→场次后来cancelled的合法原请求重放（各节点仍None、无写入），以及sent成功
> 后修改当前正文→原body_digest/键重放None；fresh persist/backfill/send的原取消限制仍测试。
> 这些反证避免修绑定时改变既有历史重放语义，不删错参数fresh/cache反证。

允许修改 app/storage/idempotency.py、app/graph/invitation_nodes.py；test_idempotency、
test_invitation_effect、test_effect_idempotency_suite及现存interview_invitation API/routes/storage/
review_page/e2e的测试；本计划。其它业务模块与spec不改。仅合成数据，无生产/外发/真实简历。

1. 先补真实红灯测试：五节点都覆盖fresh与已有成功缓存key，保留原thread/key但更换真实场次、
草稿或键字段（run_id/编辑正文/draft_id/status/body_digest）。必须拒绝且业务/effect完整快照
不变。不要用更换thread的假缓存未命中测试替代真实缓存命中错配。另补错thread归属，以及
同一投递两个场次复用同一个run_id的persist缓存跨场次错绑负例。
2. 可给共享装饰器增加默认None validate_before_cache纯校验hook，接收conn及节点参数，
每次调用（含缓存命中）都跑。需要DB只读查真实归属；不能写库/commit/调用effect。保留既有
thread_arg/business_key_args/require_no_transaction及所有默认行为。启用begin_immediate的节点
在自有写锁内、SELECT effect_log之前调用hook，失败回滚自己的事务、保留原错误；无写锁模式
hook失败不得回滚调用方事务。调用方事务拒绝在清理之外，哨兵不变。添加通用hook顺序/缓存
及失败/caller事务守卫测试，避免校验与缓存检查间的跨连接竞态。
3. 五节点推荐begin_immediate=True，提前锁定真实对象与键；sent节点移除内部BEGIN，使用
装饰器统一管理。使用私有只读validator（不命名effect_*），保留直接装饰规范effect节点。
thread=slot_application_id(conn, slot_id)，edit/mark先由真实draft.slot_id找归属；sent还要
核draft属于传入slot。校验只绑定键所表达的字段，不额外把actor、reviewer、marked_at、channel
等未进入既定键的字段强行加键。合法原键重放仍None，绝不新增事实。
键：persist=draft.run_id；edit=draft_edit_business_key(draft_id,edited_body)；mark=draft_id；
backfill=slot_id:status；sent=slot_id:draft_id:body_digest。persist若已有该run_id草稿，还需验证
其真实slot_id等于传入slot_id（同application另一slot也拒绝），不能只认application_id。
4. 保留所有业务前置/取消状态/AI标识/结果不降级/门禁默认关闭/物理发送精确一次不变式。
现有测试/恢复配方若历史上用非规范任意键，改成约定的真实键，不删除原断言或放宽异常/
恢复判据；若为准备业务状态写了未提交fixture，明确commit夹具准备，不改哨兵事务反证。
5. 先红→修→绿；最终跑invitation_effect、interview_invitation API/routes/storage/review_page、
idempotency、effect_idempotency_suite，并加Offer节点和onboarding终态的共享装饰器回归。
记录真实命令/计数/退出码到本计划；执行器日志由父生成不能手改。全量由父整合会话重新跑。
不执行git add/commit，双review PASS后父runner代提交。不能将当前4779旧基线当修复后证据。

#### Task 1 首次执行记录（2026-10-11，Spec FAIL，不能作为完成证据）

- 红灯命令：`"$SDD_PYTHON" -m pytest -q tests/test_idempotency.py tests/test_invitation_effect.py -k 'validate_before_cache or cache_hit_rejects or existing_run_id_cannot_bind or rejects_wrong_thread or sent_cache_hit'` → **13 failed, 85 deselected**，退出码 **1**。失败分别暴露装饰器缺少 hook、缓存命中绕过真实对象/业务键校验。
- 修复后同一聚焦命令 → **13 passed, 85 deselected**，退出码 **0**。
- 最终验收：`"$SDD_PYTHON" -m pytest -q tests/test_invitation_effect.py tests/test_interview_invitation_api.py tests/test_interview_invitation_routes.py tests/test_interview_invitation_storage.py tests/test_invitation_review_page.py tests/test_idempotency.py tests/test_effect_idempotency_suite.py tests/test_offer_nodes_create.py tests/test_offer_nodes_approval.py tests/test_onboarding_terminal_nodes.py tests/test_onboarding_terminal_invariant.py` → **386 passed**，退出码 **0**。
- 差异格式检查：`git diff --check` → 退出码 **0**。
- 全量测试由父整合会话重跑；本执行者未运行全量，也未执行 git add/commit。

#### Task 1 本次续执行记录（2026-10-11，补齐 Spec review 回归）

- 新增红灯命令：`"$SDD_PYTHON" -m pytest -q tests/test_invitation_effect.py -k 'successful_invitation_effect_keys_replay_after_slot_is_cancelled or sent_success_key_replays_after_current_draft_body_is_edited'` → **2 failed, 67 deselected**，退出码 **1**。失败分别复现取消场次后原成功键重放被拒，以及发送后编辑当前正文导致原发送键重放被拒。
- 修复后同一聚焦命令 → **2 passed, 67 deselected**，退出码 **0**。
- 最终联合验收：`"$SDD_PYTHON" -m pytest -q tests/test_invitation_effect.py tests/test_interview_invitation_api.py tests/test_interview_invitation_routes.py tests/test_interview_invitation_storage.py tests/test_invitation_review_page.py tests/test_idempotency.py tests/test_effect_idempotency_suite.py tests/test_offer_nodes_create.py tests/test_offer_nodes_approval.py tests/test_onboarding_terminal_nodes.py tests/test_onboarding_terminal_invariant.py` → **388 passed**，退出码 **0**。
- 规则修正：缓存前场次校验只确认 slot→application 归属；sent 缓存前只校验约定键、draft→slot 和 slot→application，不重验当前场次状态或正文摘要。backfill 缓存前不再把未进入键的 actor/channel/reason 当作绑定字段；这些业务有效性校验仍在 cache miss 节点函数。
- 差异格式检查：`git diff --check` → 退出码 **0**。
- 本执行者未运行全量测试，也未执行 git add/commit；提交仍由父 runner 代做。

#### Task 1 本次续执行记录（2026-10-11，补齐 fresh 参数错配反证）

- 新增 fresh 反证：mark 使用原 thread 与规范 draft_id 键但传入另一真实 draft_id；sent 使用原 thread 与规范 slot_id:draft_id:body_digest 键，分别传入错 draft_id 或错 body_digest。两节点调用前均无该节点成功缓存；拒绝后完整邀约事实快照不变，effect_log 无新增。
- 矩阵核对：persist、edit、backfill 已有 fresh/cache 错配或归属反证；mark、sent 补齐了本次缺失的 fresh 参数错配覆盖。历史测试均保留。
- 聚焦命令：`"$SDD_PYTHON" -m pytest -q tests/test_invitation_effect.py -k 'mark_fresh_key_rejects_mismatched_draft_id or sent_fresh_key_rejects_mismatched_parameters_without_fact_changes'` → **3 passed, 69 deselected**，退出码 **0**。现有校验逻辑已拒绝错配，本轮仅补测试，无需实现改动。
- 最终联合验收：`"$SDD_PYTHON" -m pytest -q tests/test_invitation_effect.py tests/test_interview_invitation_api.py tests/test_interview_invitation_routes.py tests/test_interview_invitation_storage.py tests/test_invitation_review_page.py tests/test_idempotency.py tests/test_effect_idempotency_suite.py tests/test_offer_nodes_create.py tests/test_offer_nodes_approval.py tests/test_onboarding_terminal_nodes.py tests/test_onboarding_terminal_invariant.py` → **391 passed**，退出码 **0**。
- 差异格式检查：`git diff --check` → 退出码 **0**。
- 保留前两次执行记录与审查历史；本执行者未运行全量测试，也未执行 git add/commit；提交仍由父 runner 代做。

#### Task 1 本次续执行记录（2026-10-11，补齐 persist/edit/backfill fresh 键参数错配）

- 新增三项 fresh 首次调用反证；保持 thread=`app1`，传入规范业务键所表达的原值，但给节点参数传入实际错配值。三项均在该节点无成功缓存时拒绝，调用前后完整邀约事实快照不变，且对应节点 effect_log 为 0。本轮只补测试，既有实现已满足校验。
- 聚焦命令：`"$SDD_PYTHON" -m pytest -q tests/test_invitation_effect.py -k 'persist_fresh_key_rejects_mismatched_run_id or edit_fresh_key_rejects_mismatched_body or backfill_fresh_key_rejects_mismatched_status'` → **3 passed, 72 deselected**，退出码 **0**。
- 五节点 fresh/cache 错配矩阵（此矩阵只列业务键参数错配；错 thread、取消状态等独立测试不计入）：

  | 节点 | fresh 首次调用测试 | fresh 键字段错配 | 已有成功缓存测试 | cache-hit 键字段错配 |
  |---|---|---|---|---|
  | persist | `test_persist_fresh_key_rejects_mismatched_run_id_without_fact_changes` | key=`run-A`，参数 `draft.run_id=run-B` | `test_persist_cache_hit_rejects_rebound_slot_and_preserves_facts` | 真实 `slot_id` 从原场次改为同投递另一场次，原 `run_id` 键不变 |
  | edit | `test_edit_fresh_key_rejects_mismatched_body_without_fact_changes` | key 表达正文 A，参数 `edited_body` 为正文 B | `test_edit_cache_hit_rejects_changed_text_with_original_business_key` | `edited_body` |
  | mark | `test_mark_fresh_key_rejects_mismatched_draft_id_without_fact_changes` | `draft_id` | `test_mark_cache_hit_rejects_rebound_draft_without_fact_changes` | `draft_id` |
  | backfill | `test_backfill_fresh_key_rejects_mismatched_status_without_fact_changes` | key 表达 `sent`，参数 `status=confirmed` | `test_backfill_cache_hit_rejects_changed_status_with_original_business_key` | `status` |
  | sent | `test_sent_fresh_key_rejects_mismatched_parameters_without_fact_changes` | 参数化覆盖 `draft_id`、`body_digest` | `test_sent_cache_hit_rejects_changed_digest_with_original_business_key` | `body_digest` |

- 最终联合验收：`"$SDD_PYTHON" -m pytest -q tests/test_invitation_effect.py tests/test_interview_invitation_api.py tests/test_interview_invitation_routes.py tests/test_interview_invitation_storage.py tests/test_invitation_review_page.py tests/test_idempotency.py tests/test_effect_idempotency_suite.py tests/test_offer_nodes_create.py tests/test_offer_nodes_approval.py tests/test_onboarding_terminal_nodes.py tests/test_onboarding_terminal_invariant.py` → **394 passed**，退出码 **0**。
- `git diff --check` → 退出码 **0**。
- 保留前三次执行记录、原始失败审查历史与所有既有测试；本执行者未运行全量测试，也未执行 git add/commit；提交仍由父 runner 代做。
