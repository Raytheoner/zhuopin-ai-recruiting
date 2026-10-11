# 1011R 入职终态日期校验修复

既有 onboarding-flow U3 的真实入职日不得写入非日期字符串。父会话在纯内存合成夹具实证：
actual_start_date="0" 被当前节点接受并写入终态detail_json。这是现有契约的校验缺口，不改spec。
原U3 Tasks1–8完成后单独执行此修复计划，不重跑已完成Task。子Session全部gpt-6-luna。

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


### Task 1: 入职终态真实日期校验及无写入反证

允许修改：app/graph/onboarding_nodes.py、tests/test_onboarding_terminal_nodes.py、
tests/test_onboarding_terminal_pages.py、本计划。原终态事务/阶段保持/六类处置/鉴权不改变。

1. 先写失败测试：节点传入 "0"、"2026-02-30"、非规范ISO日期等非法实际日期时必须抛
OnboardingTerminalRejected；完整业务/effect快照无变化，解除后合法日期重试恰好一次。
API HR合法登录请求同样非法日期→422，业务表与日志快照不变。保留匿名401/非HR403既有测试。
2. 最小修复：hired 分支把 actual_start_date 或 offer.start_date 选出的实际日期校验成真实
YYYY-MM-DD日期（date.fromisoformat后isoformat一致；不允许空字符串悄悄fallback、非日期文本或不存在日期）。
如果actual_start_date为None仍用原offer日；测试注入today也作为真实日期比较，避免字符串排序
假装日期校验。错误映射到原OnboardingTerminalRejected，不新增异常泄漏500，不写新列。
保持合法过去/当天通过、未来拒绝语义及装饰器原子事务。API已有错误映射可复用，无需改server。
3. 先实跑新增测试红，再修到绿。最后实跑terminal_nodes、terminal_pages、invariant、
原onboarding_pages、test_effect_idempotency_suite、test_idempotency联合回归；真实命令/
输出/退出码写本计划，执行器证据文件由父runner写，子会话不能篡改。全量留父整合验收。
4. 不执行git add/commit，父runner在双review PASS后代提交。无生产/外发/真实数据操作。

## Task 1 实跑记录

- 红测（实现前）：`"$SDD_PYTHON" -m pytest -q tests/test_onboarding_terminal_nodes.py tests/test_onboarding_terminal_pages.py -k 'invalid_actual_date or invalid_injected_today'`；输出 `9 failed, 53 deselected in 0.65s`；退出码 `1`。节点接受非法日期，HR API 返回 200，非法 `today` 未被拒绝。
- 新增用例绿测：同一命令；输出 `9 passed, 53 deselected in 0.55s`；退出码 `0`。
- 指定联合回归：`"$SDD_PYTHON" -m pytest -q tests/test_onboarding_terminal_nodes.py tests/test_onboarding_terminal_pages.py tests/test_onboarding_terminal_invariant.py tests/test_onboarding_pages.py tests/test_effect_idempotency_suite.py tests/test_idempotency.py`；输出 `238 passed in 9.18s`；退出码 `0`。
