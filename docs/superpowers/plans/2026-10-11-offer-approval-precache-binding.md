# 1011R Offer 审批缓存参数绑定补修

既有offer-record-and-approval契约。父会话内存合成数据实证：已成功1:1:mgr:approved缓存key，
实际传入level=2、approver=gm仍返回None，二级事实未写入。当前键校验在函数体内，缓存命中会跳过。
此修复在邀约F1共享validate_before_cache hook完成之后执行；不重跑Offer Tasks1–9。

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


### Task 1: 审批参数在缓存前绑定键，并保留合法跨轮旧请求重放

Task未完成：首轮251项通过、Spec PASS，但质量FAIL指出合法returned键缓存重放可以传空comment。
本次优先写returned成功后同thread/key传comment=None/空串/全空白的红灯反证，完整快照不变；
保留fresh必填意见测试。缓存前validator应保留纯请求校验：decision有效、approver非空、
returned必须非空意见；这些请求有效性独立于数据库当前状态，不得随cache命中跳过。
comment内容无需纳入键，合法非空意见重放仍None；跨轮旧合法请求仍None，业务状态不变。
本次追加真实执行记录，保留首轮历史。审查原始事件请逐行解析后只输出命令/exit/最后摘要，
不要打印包含巨大源码读取输出的整个JSONL。

授权 app/graph/offer_nodes.py、tests/test_offer_nodes_approval.py、本计划；若共享hook小修确需，
允许 idempotency 及其测试并说明理由，禁止改其它业务模块/spec/旧letter tests。
1. 先写真实红灯：成功一级审批后保留thread/key，改变实际level/approver/decision之一，缓存命中
路径必须拒绝，Offer/approval/history/effect快照完全不变；加fresh错配负例。不要把改thread当缓存命中。
fresh与cache都保持原thread，key=1:1:mgr:approved，只改level、approver或decision，
至少覆盖2×3参数矩阵；fresh时该审批节点无成功缓存。逐项记录测试名与所错配参数。
2. 复用已在邀约F1引入的 validate_before_cache hook，pure validator只校验节点传入参数与键。
键格式仍round:level:approver:decision；从business_key取原轮次部分，不与当前数据库round强行比较。
这样合法原round1请求在Offer已revise到round2后仍允许命中旧缓存并返回None，新轮事实不变。
fresh旧round key仍由原锁内业务判定拒绝。保持thread_arg=offer_id与begin_immediate=True。
用户名可能含冒号，不要用固定四段split误判；按首轮次/级别与末decision解析或构造稳定比较。
增加含冒号approver的合法fresh与同键重放测试，配置夹具链到该approver，不用真实账户。
既有错误映射/无需意见字段纳入幂等键；不改变原不合法decision/returned必须意见业务前置。
3. 原旧轮重放测试原样保留，新增键参数错配反证、完整快照/caller事务守卫；合法同键重放不
新增approval/日志，different request keys/修订状态规则保留。验证hook失败释放自己事务且不覆盖原异常。
4. 最后实跑offer_nodes_approval/create/todo、offer_flow_endpoints、offer_unit3_e2e、idempotency、
effect_idempotency_suite联合回归。写实际命令/输出/exit到本计划，原始事件/证据由父runner生成。
不改执行器证据，不gitadd/commit。双review后父代提交，父会话重跑整合全量与Final。
全部合成数据/本地验证；无外发、真实数据、生产操作。子Session全部Luna。

## Task 1 执行记录（2026-10-11）

解释器：`$SDD_PYTHON=/Users/paulshao/Projects/HumanResource/venv/bin/python`。

- TDD 红灯：`"$SDD_PYTHON" -m pytest tests/test_offer_nodes_approval.py -q -k 'approval_key_rejects_parameter_mismatch or approval_key_with_colon_in_approver'` → `3 failed, 4 passed, 35 deselected in 1.46s`，exit_code=1。失败均为旧实现缓存命中接受错配：
  - `test_approval_key_rejects_parameter_mismatch_without_changing_facts[cached-level-overrides0]`：key `1:1:mgr:approved`，实际 `level=2`。
  - `test_approval_key_rejects_parameter_mismatch_without_changing_facts[cached-approver-overrides1]`：同 key，实际 `approver=gm`。
  - `test_approval_key_rejects_parameter_mismatch_without_changing_facts[cached-decision-overrides2]`：同 key，实际 `decision=returned`（提供合法意见）。
  fresh 对应的 level、approver、decision 三例均按预期拒绝；含冒号审批人合法首次请求与重放通过。
- 实现后 `"$SDD_PYTHON" -m pytest tests/test_offer_nodes_approval.py -q` → `42 passed in 7.99s`，exit_code=0。
- 指定联合回归：`"$SDD_PYTHON" -m pytest tests/test_offer_nodes_approval.py tests/test_offer_nodes_create.py tests/test_offer_nodes_todo.py tests/test_offer_flow_endpoints.py tests/test_offer_unit3_e2e.py tests/test_idempotency.py tests/test_effect_idempotency_suite.py -q` → `251 passed in 19.49s`，exit_code=0。

改动范围：`app/graph/offer_nodes.py`、`tests/test_offer_nodes_approval.py`、本计划。未改共享幂等实现或其他业务模块。

## Task 1 追加执行记录（2026-10-11）

- 补齐缓存前请求校验的红灯：`"$SDD_PYTHON" -m pytest tests/test_offer_nodes_approval.py -q -k 'cached_returned_approval_still_requires_non_empty_comment or cached_approval_still_validates_decision_and_approver'` → `6 failed, 43 deselected in 1.25s`，exit_code=1。4 个 `test_cached_returned_approval_still_requires_non_empty_comment` 参数（`comment=None`、`""`、`"   "`、`" \t\n"`）均被原实现缓存短路、未拒绝；`test_cached_approval_still_validates_decision_and_approver` 的无效 `decision="unknown"`、空 `approver="  "` 也未保留原请求校验语义。
- 实现后审批文件：`"$SDD_PYTHON" -m pytest tests/test_offer_nodes_approval.py -q` → `49 passed in 9.30s`，exit_code=0。
- 指定联合回归：`"$SDD_PYTHON" -m pytest tests/test_offer_nodes_approval.py tests/test_offer_nodes_create.py tests/test_offer_nodes_todo.py tests/test_offer_flow_endpoints.py tests/test_offer_unit3_e2e.py tests/test_idempotency.py tests/test_effect_idempotency_suite.py -q` → `257 passed in 20.60s`，exit_code=0。覆盖 caller transaction 守卫及 validator 失败时回滚自有锁、保留原异常的共享幂等测试。
- 本轮键错配矩阵逐项：`test_approval_key_rejects_parameter_mismatch_without_changing_facts[fresh-level-overrides0]` / `[cached-level-overrides0]`（实际 `level=2`）；`...[fresh-approver-overrides1]` / `[cached-approver-overrides1]`（实际 `approver=gm`）；`...[fresh-decision-overrides2]` / `[cached-decision-overrides2]`（实际 `decision=returned`，提供合法意见）。均保持 `thread_id=o1` 与 `key=1:1:mgr:approved`，完整业务与 effect 快照不变；fresh 时无成功缓存。
- 追加 `test_approval_key_with_colon_in_approver_supports_fresh_and_replay` 覆盖用户名 `team:lead` 的首次审批与同键重放。validator 只校验请求字段和键绑定，保留键内原轮次且不查询当前数据库轮次；既有跨轮旧请求重放测试未改，fresh 旧轮请求仍由锁内业务判定拒绝。
