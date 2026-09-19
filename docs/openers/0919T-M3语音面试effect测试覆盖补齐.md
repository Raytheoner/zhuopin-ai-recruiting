# 0919T：M3 语音面试包合入后 effect 节点测试覆盖补齐（4 条全量 pytest 失败清零）

## 零、为什么

`0919O` 发版前置全量 pytest 重跑（`182383d`）发现 4 条失败，逐条比对 `Q-49` 授权引用的
"已知无关"名单，**没有一条在名单里**——按无人值守预案不得自行判定无关，已登记留步
（见 `docs/session接力.md` 09-19 `0919O` 条目）。task-dispatcher 复核后确认：四条根因全部
是 **M3 语音面试包（U2/U3/U4）合入 main 带来的新 effect 节点，测试覆盖没跟上**，不是新
增的生产代码 bug——production 代码本身（`SCHEMA`、`@idempotent_effect` 装饰）大半已经
是对的，缺口集中在测试文件。四条独立无依赖，任意顺序做完即可全部转绿。

⚠️ 铁律 1（`CLAUDE.md`）要求每个 `effect_*` 节点必须有强制中断验证；这不是"补测试凑数"，
是让新节点补上铁律 1 要求的覆盖。

## 一、Task 1（1 行）：`_ADDED_COLUMNS` 允许表集合放宽

`tests/test_db_m2_schema.py:766`：
```python
assert tables_in_added_columns == {"job_profile", "job", "resume"}
```
`app/storage/db.py` 的 `_ADDED_COLUMNS`（约 950-980 行）现在确实包含 `job_prep_config`／
`interview_session` 两张表的加列条目（`job_prep_config` 在 U2 建表、`interview_session`
在 M3 U1 建表，均已有 `CREATE TABLE IF NOT EXISTS`——`app/storage/db.py:713` 与 `:841` 可
核）。这条护栏本意是"进这个集合的表必须在 `SCHEMA` 已有 `CREATE TABLE IF NOT EXISTS`"，
两张新表都满足，护栏该放宽而不是拦。改成：
```python
assert tables_in_added_columns == {"job_profile", "job", "resume", "job_prep_config", "interview_session"}
```

## 二、Task 2（加两行）：迁移测试补 shell 表

`tests/test_db_m2_u2_schema.py::test_old_job_table_gains_parse_confidence_threshold_via_migration`
（约 30-51 行）手工建最小库（`job`／`job_profile`／`resume`），跑 `apply_column_migrations`
时因 `_ADDED_COLUMNS` 遍历到 `job_prep_config`／`interview_session` 的条目而报
`no such table: job_prep_config`。文件里已有同款注释解释过 `resume` 为什么要建成空壳
（"该表必须存在（即便只是空壳），否则会在遍历到…时先炸 no such table，与本测试要验证
的 job 迁移无关"）——照抄同一手法，在 `conn.execute("CREATE TABLE resume ...")` 之后加：
```python
conn.execute("CREATE TABLE job_prep_config (id TEXT PRIMARY KEY)")
conn.execute("CREATE TABLE interview_session (id TEXT PRIMARY KEY)")
```
（跟 `resume` 一样是无关空壳，本测试只验 `job` 表的迁移结果，不需要真实字段。）

## 三、Task 3（1 行）：`effect_send_verification_code` 补装饰器

`app/graph/invite_nodes.py:573`：
```python
def effect_send_verification_code(*args, **kwargs):
    """...OQ-10 未决，本单元不启用...raise NotImplementedError..."""
    raise NotImplementedError(...)
```
这是短信通道未接线的占位桩（design D13，门禁口径 OQ-10 待 Shao Peishen 定，⛔ 与本任务
无关、不要碰其余逻辑）。铁律 1 的机械检查不区分"占位桩"与"真实现"，只认函数名前缀。
直接在函数上方加 `@idempotent_effect("effect_send_verification_code")`：装饰器要求
`(conn, *, thread_id, business_key, **kwargs)` 调用签名，被装饰的桩仍是 `*args, **kwargs`
（兼容任意调用形状），内部照旧立刻 `raise NotImplementedError`——装饰器在 `fn` 抛异常时
直接向上抛（见 `app/storage/idempotency.py` 的 `try/except` 块），不会写 `effect_log`，
行为与现状完全一致，只是补齐了清单要的装饰器字面量。**⛔ 不改函数签名、不改函数体、
不删 `NotImplementedError`、不动 OQ-10 相关文案**——这条纯是补一行装饰器满足静态扫描。

## 四、Task 4（主体）：补 18 个新节点的 `EFFECT_NODE_MANIFEST` + `build_recipes()`

文件：`tests/test_effect_idempotency_suite.py`。两处要加：
1. `EFFECT_NODE_MANIFEST`（约 92 行起的 `frozenset`）：加下面 18 个节点名
2. `build_recipes()`（约 523 行起）：每个节点加一条 `Recipe`（`seed`／`invoke`／
   `count_business_rows`／`rows_per_effect`），**照抄文件里已有配方的写法**——种子建最小
   闭环数据，`invoke` 调用真实的 `effect_*` 函数，`count_business_rows` 数"这个节点自己
   的那条业务事实"（INSERT/UPDATE 通常 `rows_per_effect=1`；若某节点是 DELETE，参考文件
   里已有的"全表唯一负值"配方注释，`rows_per_effect=-1`）。

18 个节点按文件分组（生产代码里已经全部带 `@idempotent_effect`，Task 4 不改 `app/` 任何
代码，只补测试）：

**`app/graph/invite_nodes.py`（11 个）**：
`effect_create_interview_session`（86 行）／`effect_issue_invite`（155 行）／
`effect_log_invite_access_denied`（193 行）／`effect_open_invite`（203 行）／
`effect_issue_resume_token`（279 行）／`effect_consume_resume_token`（295 行）／
`effect_deliver_invitation`（354 行）／`effect_record_consent`（394 行）／
`effect_issue_verification_code`（439 行）／`effect_verify_phone`（493 行）／
`effect_display_verification_code_to_hr`（560 行）

**`app/graph/live_session_nodes.py`（4 个）**：
`effect_open_session`（76 行）／`effect_persist_turn`（92 行）／
`effect_close_session`（130 行）／`effect_fetch_recording`（149 行）

**`app/graph/interview_scoring_nodes.py`（3 个）**：
`effect_write_acoustic_refs`（289 行）／`effect_persist_scorecard`（314 行）／
`effect_mark_scoring_failed`（367 行）

逐个打开对应文件读函数体（业务写的是哪张表、INSERT 还是 UPDATE、`business_key` 用什么），
再写配方；不要凭函数名猜。种子数据可能需要跨表前置链（如场次类节点大概率需要先有
`job`／`candidate`／`application`／`interview_session` 一条完整闭环，参考文件里 M3 相关
的既有 `_seed_*` 函数或自行按需新建，⛔ 不要为了偷懒简化种子而绕过外键/约束）。

## 五、红线

- ⛔ **Task 4 不改 `app/` 任何代码**（文件顶部说明已写死这条）；若发现某节点真的不幂等
  （中断测试测出业务写与 `effect_log` 不在同一事务），**不要在测试单元里顺手改被测代码**
  ——停下登记进 `docs/roadmap/定夺队列.md`（阻塞类型＝决策，因为这是铁律 1 的真实违反，
  修法可能影响调用方，需人判），不猜、不代拍
- ⛔ Task 3 不动 OQ-10、不改 `effect_send_verification_code` 的签名/行为，只加装饰器一行
- ⛔ 不碰 `Q-47`／`TAG4_RE` 等无关文件（本条与那批修复无关）
- 本条与其余在跑泳道（M0/M1/M2/M3 场景、`gap:G1`/`gap:G2`、`voice-structured-interview`
  U4 seg5、`answer:Q-14`）触碰区不重叠：只碰 `tests/test_db_m2_schema.py`、
  `tests/test_db_m2_u2_schema.py`、`tests/test_effect_idempotency_suite.py`、
  `app/graph/invite_nodes.py`（仅 Task 3 一行装饰器）

## 六、无头块铁律（四条逐字内嵌）

1. **只 `git add` 本条明确列出的路径。** ⛔ 禁止 `git add -A` / `git add .` /
   `git commit -a` / `git stash`（含 `-u`）
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交
3. push 被拒 → `git pull --rebase --autostash origin main` 后重试，最多 3 次。
   ⛔ 不要在 commit 前 pull
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 默认绝不删除该锁

## 七、验证与收口

1. TDD：Task 1/2 先跑现有失败用例确认红，改完转绿；Task 3 跑
   `tests/test_effect_idempotency_suite.py::test_every_effect_named_function_is_decorated_with_idempotent_effect`
   转绿；Task 4 每加完一批节点跑一次
   `tests/test_effect_idempotency_suite.py -k "<node_name>"` 单独确认，全部加完后跑整
   个 `tests/test_effect_idempotency_suite.py`
2. 全量收口：`venv/bin/python -m pytest -q` 应 **0 failed**（当前基线 main HEAD 之上
   3662 passed／4 failed／10 skipped，改完预期 3666 passed／0 failed／10 skipped——
   skipped 数不变，本条不装 M3 语音主机相关依赖）
3. Final review 通过后 `git merge --no-ff` 回 main（⛔ 不 `--ff-only`，参考 `0919R`／
   `0919S` 教训：main 上并行泳道随时会先推进）、`git push origin main`
4. 收口后：在 `docs/session接力.md` 追加一行登记「四条测试已转绿」，并在
   `docs/roadmap/定夺队列.md` 给 `Q-49` 补一行——新的失败名单（本条改动前的 4 条）已
   转绿、重新核实通过，供 Shao Peishen 针对**新的**全量 pytest 结果（如果本条之后又有
   别的泳道引入新失败）再答一次「发」（⛔ 不要代签这行，只登记待答）
