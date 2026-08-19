## 1. 事务边界修复与回归测试（核心修复）

- [x] 1.1 写回归测试：确定性复现"单一事务边界所有权被破坏"这一机制本身（对应 `specs/effect-transaction-integrity/spec.md` 的"事务归属冲突可在任意平台确定性复现"），不依赖当前这几个具体失败用例的名字，不依赖特定平台的偶然时序；先在修复前的代码上跑一次，确认稳定失败（TDD 红灯基线）
- [x] 1.2 实现方向 A（design.md 已选定）：`build_intake_graph` 内部为 `SqliteSaver` 开一个与 effect 层分离的专用连接，通过 `SqliteSaver.from_conn_string(db_path)` 以 `with` 正确持有（实现改用 `get_connection(db_path)` 直接持有而非 `from_conn_string`+`with`——`from_conn_string` 返回 context manager，不 `with` 直接传给 `graph.compile()` 会报 "Invalid checkpointer provided"，而生命周期需要活到图对象生命周期结束；效果等价，已在 plan 与 code 注释中记录理由）
- [x] 1.3 为两个连接分别启用 `PRAGMA journal_mode=WAL` 与非零 `busy_timeout`（方向 A 的必要配套，缺了这一步会从"事务状态对不上"退化成"database is locked"）
- [x] 1.4 新连接的生命周期绑定到图对象/应用生命周期；补充验证：进程正常退出与异常退出都不遗留未关闭连接（对应 design.md「Risks / Trade-offs」的连接生命周期风险）
- [x] 1.5 修复 `app/storage/idempotency.py:36-46` 的异常掩盖问题：清理路径的 `conn.rollback()` 本身若抛出异常，不得替换原始异常向上传播，调用方最终看到的必须是触发失败的原始异常（对应 spec 的"异常路径不掩盖原始错误"）
- [x] 1.6 幂等专项测试：对带幂等保护的 effect 节点强制中断后重放，断言业务写入与 `effect_log` 记录恰好各一份，不多不少（对应工程铁律第 1、2 条审计断言与 spec 的"幂等键与业务写入原子提交在事务中断后仍然成立"）
- [x] 1.7 本地全量测试跑绿：`venv/bin/python -m pytest`，包含 1.1 与 1.6 新增的测试
- [x] 1.8 确认本章节代码改动的行为边界与 `specs/effect-transaction-integrity/spec.md` 一致，没有引入 spec 未覆盖的新行为

## 2. 跨平台验证与收尾（依赖第 1 章完成并合并）

- [x] 2.1 推送触发真实 CI（Windows），确认原始三个已知失败用例（`test_build_intake_graph_runs_end_to_end` / `test_app_works_when_mounted_at_arbitrary_subpath` / `test_second_turn_prompt_contains_first_turn_message_and_known_fields`）与本轮自检额外观察到的同根因用例（`test_create_job_returns_first_question` / `test_reply_and_confirm_then_generate_jd`）全部转绿——CI run 31763141141（commit `1a33322`）：`88 passed`，零失败
- [x] 2.2 确认 1.1 新增的回归测试在本地与 CI 上都稳定通过，且其失败/通过判定不依赖具体哪个业务用例中招——CI run 31763141141 与 31768797970（commit `d2801bd`）两次独立运行均 `88 passed`，`tests/test_transaction_ownership.py` 的确定性结构测试与特征测试均在其中，不依赖自然时序侥幸
- [x] 2.3 更新 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` 状态为「已修复」，记录实际采用的方案（方向 A + 异常掩盖修复）与跨平台验证结果，供未来 M2 Postgres 迁移参考
