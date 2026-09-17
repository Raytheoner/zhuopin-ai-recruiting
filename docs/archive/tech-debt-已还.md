# tech-debt-已还

> 〔归档工具〕 只追加、不回改；由 scripts/archive_docs.py 从原文件原样搬入。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-10~~ · 边界守护 CI 的依赖基线钉死在立项 commit，有未声明的保质期 ✅ 已还 -->

## ~~TD-10~~ · 边界守护 CI 的依赖基线钉死在立项 commit，有未声明的保质期 ✅ 已还

**2026-09-08 已处置（`0908B`）**：触发条件如期发生——补 `tzdata` 时这道守卫第一次红，
与本条预测逐字吻合。按下方改法**二**（显式登记制）落地：判据从「相对立项 commit 的
`requirements.txt` diff 必须为空」换成「每一条依赖都必须登记在
`scripts/check_boundary.py` 的 `REGISTERED_DEPENDENCIES` 里并写明理由」。
⛔ 不是删掉这道检查（本条明令禁止），是换判据。加依赖从此要改两个文件——
两处都动才是一次刻意的决定。

顺带修掉旧判据的两个毛病：① 不再需要 git，浅克隆的 `test` job 上不必再
`pytest.skip`（那个 skip 是真实存在的覆盖缺口）；② 覆盖**全部**依赖而不只是
「新增的那些」。反证：`tests/test_boundary_guard.py` 48→50 条。

⏸ **「附带盲区」未随本次处置**：扫描范围仍只覆盖 `app/`，`tests/` 与 `scripts/`
依然是缺口。⛔ 本条销号不含那一项——它另立门户，见下方原文。

---

### 原登记（保留备查）

#### TD-10 · 边界守护 CI 的依赖基线钉死在立项 commit，有未声明的保质期

**欠的是什么**：`scripts/check_boundary.py` 的 `BASELINE_COMMIT`
（`e65f6857fe255634d49a3e8696b1dba0f5facbec`，立项 commit）钉死作为依赖 diff 的
基线，判据是「相对该 commit，`requirements.txt` diff 为空」。这条判据没有
保质期——未来任何一次**正当**新增依赖（`pgvector` / `PaddleOCR` / `BGE-M3` /
阶段二 `FunASR`）落地时，diff 必然非空，CI 会在**所有分支**上永久变红，且
不存在"改代码修复"这条路：判据本身就是"不许改 `requirements.txt`"。

**附带盲区**：该守护脚本目前只扫描 `app/`，`tests/` 与 `scripts/` 未纳入扫描
范围——这两处若引入边界违规，现有判据看不见，影响面尚未评估。

**触发条件**：第一次要往 `requirements.txt` 加依赖时。届时改法二选一：
1. 基线改为"上一次 tagged 发版"（滚动基线，随发版前移）；
2. 判据改为"新增依赖必须在白名单内"（显式登记制，不依赖某个历史 commit）。

⛔ 不要到时候图省事简单删掉这道 CI 步骤——那会连同它已经在守护的边界一起丢掉。

**为什么现在只登记不做**：触发条件尚未发生，M1/M2 现有依赖未变。

**来源**：0903O 终审发现，登记于 `lanes-20260904-002413` 看护报告 §四。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-14~~ · `hr-wecom-aibot-liaison` 的 proposal「不触碰 pyprojec -->

## ~~TD-14~~ · `hr-wecom-aibot-liaison` 的 proposal「不触碰 pyproject.toml」与实现已不符 ✅ 已还

**欠的是什么**：`openspec/changes/hr-wecom-aibot-liaison/proposal.md:37` 的「Impact ·
不触碰」把 `pyproject.toml` 整份列为不触碰。但第 1 章（2026-09-08 落地）**改了它的
`testpaths` 一行**，把 `tools/liaison/tests` 接进去，让全量 `pytest` 一次跑得到本服务的测试。

两者的**本意其实不冲突**：该条要挡的是**依赖**从 `pyproject.toml` 溜到 `.51`（design D10
通篇讲的都是依赖清单，`pyproject.toml` 在 `sync-to-server.sh` 的 `SYNC_PATHS` 里）。
`testpaths` 不是依赖，且第 1 章自带 `test_no_liaison_dependency_leaked_into_pyproject`
守住「不许往 `[project].dependencies` 加任何东西」。冲突的是**措辞**，不是事实。

**触发条件**：**归档该变更包之前**（跑 `openspec-archive-change` 之前）。把那一行从
「不触碰 `pyproject.toml`」订正为「⛔ 不往 `pyproject.toml` 添加任何依赖；`testpaths`
因 `tools/liaison/tests` 接入而新增一条」。

**不还的后果**：变更包归档进 `openspec/specs/` 之后，活文档里会留下一条**与代码相反**的
约束。下一个读它的人（或 reviewer）会把已经通过终审的 `testpaths` 那行当成越界改动，
要么白白花一轮去"修"它，要么把 `tools/liaison/tests` 从 `testpaths` 摘掉——而摘掉的后果
是静默的：不报错、不失败，只是本服务的 28 条测试从此没人跑。

**已还**（2026-09-09 `[Mac]0909O`，轻量通道）：`proposal.md:37` 那一行把 `pyproject.toml`
从「不触碰」清单里摘出来单列，订正为「⛔ 不往 `pyproject.toml` 添加任何依赖；`testpaths`
因 `tools/liaison/tests` 接入而新增一条」。**只改了这一行**，`git diff` 为 1 insertion /
1 deletion。挡依赖的判据没有放松：`test_no_liaison_dependency_leaked_into_pyproject`
原样守着 `[project].dependencies`。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-15~~ · 准入名单出厂态下每条消息刷 3 条 ERROR 日志 ✅ 已还 -->

## ~~TD-15~~ · 准入名单出厂态下每条消息刷 3 条 ERROR 日志 ✅ 已还

**欠的是什么**：`tools/liaison/whitelist.py` 的出厂态（`config/whitelist.yaml` 两条 `userid`
留空，真实企微 userid 尚未取得）下，每次 `load_whitelist()` **必然**产生 3 条 ERROR
（两条「userid 为空，整条丢弃」+ 一条「零条有效条目」）。而 spec 硬性禁止缓存名单，
第 4／5 章又要**每条入站消息**调一次 `admit()` —— 于是机器人收到的每一条消息都会刷 3 条 ERROR。

**为什么现在不改**：这不是 bug，出厂态"谁都不准入"是刻意的 fail-closed 设计；
但把这几条降级会与模块 docstring 里「任何失败都记 ERROR」的契约冲突，
**属于需要拍板的取舍，不是可以顺手改掉的东西**（终审 reviewer 原话：needs a decision
rather than a quiet edit）。本章尚未接线第 4／5 章，实际日志量为零，故留到接线前处置。

**触发条件**：**第 4／5 章把 `admit()` 接进入站消息路径之前**（以先到者为准：或真实
userid 填入使出厂态消失时复核一次）。

**不还的后果**：ERROR 级告警从上线第一天起持续误报，运维会很快学会忽略这个 logger ——
而 `whitelist.py` 里真正的合规漏洞（如已修的 C1 值泄漏、I2 顶层字段静默忽略）
恰恰也是靠 ERROR 日志暴露的。**噪声把唯一的告警通道淹掉**，真故障将无人察觉。

**已还**（2026-09-09 `[Mac]0909O`，轻量通道。裁决＝**去重不降级**，Shao Peishen 第十四批认可）：
`whitelist.py` 新增 `_FailureLog`，把全部 `logger.error` 收口成 `failures.error`，按
**名单文件内容的 SHA-256**（前面拼路径，见下）去重——同一份内容连续失败只记**一组**，
内容一变重新记一组。⛔ 未降级：记出来的仍是 `ERROR`（`test_dedup_does_not_downgrade_the_level`）。
⛔ 未缓存名单：全局状态只有 `_LAST_LOGGED_FAILURE_FINGERPRINT` 这**一个 64 字符十六进制串**，
判定路径每次仍完整重读重解析（`test_dedup_caches_only_a_fingerprint_never_the_roster`
＋原有的 `test_admit_rereads_the_file_on_every_call` 双守）。
契约「任何失败都记 ERROR」原样成立：同一轮里的兄弟 ERROR ⛔ 不许互相吞——去重判据在
`_FailureLog` **构造时**快照上一轮指纹，全局只在 `finish()` 更新一次
（`test_error_group_covers_every_distinct_failure_before_dedup_kicks_in` 断言出厂态那组
仍是 2 条「userid 为空」+ 1 条「零条有效条目」）。成功加载会清空指纹槽，
所以「修好→又改坏回同一份内容」会重新报。

**两处刻意的收紧**（都比 opener 字面要求更严，登记备查）：
1. 指纹 = `sha256(repr(path) + 文件内容)` 而不是只含内容。两个不同文件恰好写坏成同一份内容
   是两个独立现场，⛔ 不该互相吞 ERROR（`test_two_different_files_with_identical_bad_content_both_report`）。
   生产上只有一份 `DEFAULT_WHITELIST_PATH`，对去重效果零差别。
2. 每输出一组 ERROR，组尾补一行「以上准入名单失败按文件内容去重…」。
   ⚠️ 否则运维看见 3 条 ERROR 之后突然安静，会误以为问题自己好了——
   **日志安静不等于修好了**，这一行是唯一的提示。`config/README.md` 同步写进了失败面表。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-16~~ · 终审延后的四条 Minor（准入名单）✅ 已还 -->

## ~~TD-16~~ · 终审延后的四条 Minor（准入名单）✅ 已还

2026-09-08 交付单元 3 终审记录、当次未改：

1. **YAML 重复键静默 last-wins**：文件里出现两个 `members:` 块（或条目内两个 `userid`）时，
   PyYAML 静默取后者，**零日志**。运维若"追加一段"而不是扩写原有列表，名单会被静默替换。
2. **非 UTF-8 配置被归为「未预期异常」**：`UnicodeDecodeError` 是 `ValueError` 不是 `OSError`，
   落到兜底带。fail-closed 正确，但运维最可能犯的文件错误被报成内部异常。
3. **`path` 传 `str`／`None` 之外的类型**：类型标注是 `Path`，传 `str` 会 fail-closed 但报
   「未预期异常」。第 4／5 章调用方若传字符串，会得到一个永久拒绝且诊断错位的闸门。
   低成本修法：`_read_roster` 顶部 `path = Path(path)`。
4. **`config/README.md` 未警告失败面**：它告诉运维可以改活文件、不必重启，
   但没说 YAML 写坏／存成非 UTF-8／存盘竞态会**拒绝所有人**，且唯一提示是 ERROR 日志。

**触发条件**：第 4／5 章接线时一并处理（第 3 条尤其影响调用方）；第 4 条可随时补。
**不还的后果**：1 与 4 都是**静默**失败——名单被改小或全员被拒，而闸门看起来健康。

**已还**（2026-09-09 `[Mac]0909O`，轻量通道，四条一并）：

1. **YAML 重复键 fail-closed**：新增 `_NoDuplicateKeySafeLoader`（`yaml.SafeLoader` 子类，
   安全性不变——`test_yaml_python_tags_are_not_constructed` 常绿），构造映射前先自己扫一遍
   键，撞重复即抛 `_DuplicateKeyError`；`_read_roster` 单开一条分支记 ERROR 并**点名键名 +
   行列号**。⛔ 只记键名不记值，与 `extra` / `top_level_extra` 两处同口径
   （`test_duplicate_key_error_does_not_leak_field_values`）。
2. **非 UTF-8 单列一类**：`read_text` 换成 `read_bytes` + 显式 `decode("utf-8")`，
   `UnicodeDecodeError` 单开分支，日志说「不是 UTF-8 编码（另存为 UTF-8 无 BOM 即可）」
   并只带 `encoding` / 字节偏移量 / `reason` 三个定长元信息——⛔ 不记 `str(exc)`、
   更不记 `exc.object`（那是文件内容）。不再落「未预期异常」。
3. **`_read_roster` 顶部 `path = Path(path)`**：调用方传 `str` 现在走正常分支，
   诊断落到「文件不可读」而不是「未预期异常」（`test_str_path_*` 两条）。
4. **`config/README.md` 补「⚠️ 失败面」段**：六类失败 × 典型现场 × 日志里会说什么的表，
   点明「改坏了也是立刻生效」「唯一提示是 ERROR 日志」「⛔ 不要以为没报错就是好了」，
   并单独警告重复键的静默 last-wins 与 TD-15 的去重语义。

**验证**：`tools/liaison/tests/test_whitelist.py` 新增 15 条（TD-15 七条 + TD-16 八条），
全文件 61 passed。逐条证伪过：把 `whitelist.py` 换回改前版本再跑，这 15 条里有 9 条变红
（三条 TD-16 判据 + TD-15 去重），其余 6 条是防回归的常绿守卫。

<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-17~~ · `app/outbound/delivery.py:12` 的非法转义序列 SyntaxWarn -->

## ~~TD-17~~ · `app/outbound/delivery.py:12` 的非法转义序列 SyntaxWarning ✅ 已还

**2026-09-08 已处置（`0908U`，轻量通道）**：按下方登记的第一种改法，把该模块的
模块级 docstring 前缀成原始字符串（`"""` → `r"""`）。**全文件只改了这 1 个字符**——
本条 opener 明令「⛔ 不动该文件其它任何字符」，`git diff` 为 1 insertion / 1 deletion。

选 `r"""` 而不是把反斜杠转义成 `\\`：那条 Windows 路径是给运维**照抄**的，
`C:\\apps\\...` 在源码里读起来就不再是他要粘进去的那串。raw 前缀让源码与实际路径逐字一致。

**验证**：`./venv/bin/python -W error` 下 `ast.parse` 与 `import app.outbound.delivery`
均通过（修前 `ast.parse` 抛 `SyntaxError: "\z" is an invalid escape sequence`）；
`tools/liaison/tests/test_app_does_not_import_tools.py` 5 passed 且 0 warning；
全量 `pytest -q` 1378 passed / 3 skipped 与基线一致，warning 总数 7725 → 7715
（正是这条贡献的 10 条），全量输出里 `invalid escape sequence` 归零。

---

### 原登记（保留备查）

#### TD-17 · `app/outbound/delivery.py:12` 的非法转义序列 SyntaxWarning

**欠的是什么**：该行 docstring 里写了 Windows 路径 `C:\apps\...\candidate_outbound.switch`，
其中 `\z`（以及同类反斜杠序列）是**非法转义序列**，Python 3.14 会发 `SyntaxWarning:
"\z" is an invalid escape sequence`，且明确警告「Such sequences will not work in the future」。

**为什么现在才浮出来**：第 2 章新增的结构断言（`tools/liaison/tests/test_app_does_not_import_tools.py`）
会 AST-parse `app/` 下全部 52 个模块，于是把这条既有告警**暴露成每次跑该测试文件都出现的 2 条 warning**。
缺陷本体一直都在，只是此前没有任何测试去 parse 它。

**为什么第 2 章不修**：本交付单元的 opener 明令「⛔ 只动 `tools/liaison/` 与测试；⛔ 不碰 `app/`、`scripts/`」。
越界修它会让本章的"零 `app/` 改动"这条可机器核对的边界失效。

**触发条件**：下一个**本来就要改 `app/outbound/`** 的变更包顺手修；或单独派一个 opener。
修法是把该 docstring 改成原始字符串（前缀 `r`）或把反斜杠转义成 `\\`。

**不还的后果**：Python 未来版本会把 `SyntaxWarning` 升级为 `SyntaxError`，届时
`app/outbound/delivery.py` **直接 import 失败**——而它在 `.51` 的发送链路上。
在那之前，它持续污染测试输出，让"测试输出应当干净"这条判据失去分辨力。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-18~~ · 值守服务事务扫描器对 `with <Call>:` 会误报 ✅ 已还 -->

## ~~TD-18~~ · 值守服务事务扫描器对 `with <Call>:` 会误报 ✅ 已还

**欠的是什么**：`tools/liaison/tests/test_liaison_effects.py` 的 `_scan_transaction_violations`
在第 2 章终审后放宽为：`with` / `async with` 的 context expr 是 `ast.Name`、`ast.Attribute`
**或 `ast.Call`** 一律判为「第二个事务管理者」。放宽是为了抓住 `with self._conn:` 这个
第 3–5 章最可能出现的真实违规形态（终审实测原写法漏掉它）。

**代价**：第 3–5 章一旦在 `tools/liaison/` 的**非测试**代码里写 `with open(...) as f:`、
`with contextlib.suppress(...):` 这类与数据库无关的上下文管理器，会被误判为违规。

**触发条件**：第 3–5 章第一次因此变红时。届时的正确处置是**给扫描器加白名单或细化判据**
（例如只对名字里含 `conn` 的表达式、或对已知连接符号判违规）。

**不还的后果**：可控——**这个失败是响亮的**（一条可见的测试失败），不是静默的。
⚠️ 但要防的是**图省事把守卫改回只认裸局部名**：那会重新打开 `with self._conn:` 的口子，
而那个口子的症状是**没有症状**（`effect_log` 与业务表静默劈叉，正是 `.51` 2026-08-10／08-12
丢 `outbox` 的失败模式）。宁可留误报，⛔ 不许退回窄化。

**已还**（第 4 章 Task 3，2026-09-09）：`_scan_transaction_violations` 加 `_NON_DB_CONTEXT_CALLEES`
正面白名单，放行 `open` / `os.fdopen` / `io.open` / `contextlib.suppress` /
`tempfile.NamedTemporaryFile` / `tempfile.TemporaryDirectory`。⛔ 未窄化判据——
`with self._conn:` 与 `with get_connection():` 仍被抓，9 条 `test_scanner_*` 全绿。

**已还（第二轮细化，2026-09-09 `[Mac]0909O` 轻量通道）**：第一轮的正面白名单只放行 6 个
**逐条全名**，`with suppress(...)`（裸名导入）、`with TemporaryDirectory():`、
`with contextlib.ExitStack():` 这些照样误报（实测：还原成第一轮实现后，新增的
`test_scanner_allows_non_db_context_managers` 10 格里有 4 格红）。本轮把 `ast.Call`
这一格拆成三步判据：

1. 名字含 `conn` / `connect` / `transaction` / `begin`（**不分大小写**），或命中
   `_KNOWN_CONNECTION_CALLEES`（`closing` / `atomic` / `savepoint` / `cursor` /
   `Session` …）→ **违规**；
2. 命中非 DB 白名单——逐条全名，**或模块族** `contextlib.*` / `tempfile.*` → 放行；
3. 其余陌生被调用者 → **仍判违规**。

第 1 步压在第 2 步**前面**是刻意的：`contextlib.closing(conn)` 属于 `contextlib.*`
却货真价实管着一个连接，⛔ 不许被模块族白名单捞走。

🔴 **`ast.Name` / `ast.Attribute` 两格一个字没动，仍然无条件判违规**——本条原文
⛔ 的那种"退回只认裸局部名"没有发生。实测证伪：把 `isinstance(expr, (Name, Attribute, Call))`
改回 `isinstance(expr, ast.Name)`，**12 条测试当场变红**（含
`test_scanner_catches_with_self_conn_attribute` 与新增参数表里的 `with self._conn:`）。

⚠️ **第 3 步是相对本轮 opener 字面要求的一处收紧偏离**，刻意为之并登记：opener 写的是
"只在名字含 conn/… 时判违规"，照字面写会让 `with pool.acquire():` 这类名字里一个词根都
没有的陌生连接**静默通过**——而本条原文的落款正是"宁可留误报，⛔ 不许退回窄化"。
实测证伪：把第 3 步改成放行，`test_scanner_still_catches_unknown_callees_by_default` 变红。
opener 逐条点名要放行的 `open` / `contextlib.*` / `tempfile.*` / `suppress` /
`TemporaryDirectory` 全部已放行，**TD-18 的真实痛点已消**；留下的误报面是响亮的
（一条可见的测试失败 + 往白名单加一行即解），漏判则**没有症状**。

新增 `test_scanner_allows_non_db_context_managers`（10 格）、
`test_scanner_still_catches_connection_shaped_context_managers`（11 格证伪）、
`test_scanner_still_catches_unknown_callees_by_default`。`test_liaison_effects.py` 55 passed。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-19~~ · 真实建连适配 ✅ 已还（`0909AC`，`[Mac]0909AE` 实测验收） -->

## ~~TD-19~~ · 真实建连适配 ✅ 已还（`0909AC`，`[Mac]0909AE` 实测验收）

**欠的是什么**：Task 5 的探针实测（`docs/findings/2026-09-09-aibot-wsclient-表面实测.md`
「遗留发现」）发现真实 SDK 的 `WSClient.connect` 是 `async def`——同步调用它只会返回一个
协程对象、不执行任何网络操作，不满足 `run_forever` 期望的"阻塞到断开为止"契约；真正的
阻塞入口是 `client.run()`。第 7 章按 controller ruling 把这个判断做成结构：
`session_client.make_sdk_connect` 用 `inspect.iscoroutinefunction` 探测 `connect`，探到协程
函数就当场 `raise SdkSurfaceUnverifiedError`（指名 `client.run()` 与 findings 文档），
`__main__.main()` 据此以 `EXIT_SDK_SURFACE_UNVERIFIED` 拒绝启动。**本章没有写、也没有猜
任何"把 `client.run()` 接进 `run_forever`"的适配代码**——那需要真实凭据把整条链路跑一遍
才能验证接对了，而 `HR_LIAISON_BOT_ID`/`HR_LIAISON_BOT_SECRET` 尚未注册，本仓库拿不到。

**触发条件**：Shao Peishen 在企微后台注册 aibot、取得 `HR_LIAISON_BOT_ID`/
`HR_LIAISON_BOT_SECRET` 之后，第 8 章 8.6 灰度验收——用真实凭据把 `client.run()`（或等价的
同步阻塞封装）接进 `session_client.run_forever`，并端到端验证真实建连与真实断线重连都按
预期记窗口、告警、恢复。在此之前，`tools/liaison/.venv` 里跑
`test_make_sdk_connect_refuses_a_coroutine_function_connect` /
`test_main_exits_when_the_sdk_connect_is_a_coroutine_function` 等断言会持续把这个缺口保持
"响亮可见"。

**不还的后果**：不还也没有隐患——当前处置是"表面对不上就拒绝启动"，失败模式是进程
在启动时**立刻、显式**退出（`EXIT_SDK_SURFACE_UNVERIFIED`，日志与 stderr 都点名
`client.run()`），⛔ 不是静默自旋重连（那正是本章要消灭的失败类别，详见本条上方引用的
findings 与 controller ruling）。真正的风险只在于：如果将来有人绕开
`make_sdk_connect` 的这道检查、直接把 `client.connect`（协程）手工拼进
`run_forever`，就会退回"服务起得来、日志正常、但从不真正建连"的静默故障——⛔ 不许
削弱或绕过这道检查来"让它先跑起来"。

### ⏫ 2026-09-09 状态更新：阻塞理由已消失，本条转为**已提上日程**（Shao Peishen 裁决）

上面「触发条件」里写的前置——"凭据尚未注册，本仓库拿不到"——**今天已经不成立**：
`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / `HR_LIAISON_GROUP_WEBHOOK` 三者均已落
本机 `.env`（`[Mac]0909AA` 只验存在性、未回显取值），白名单两个 userid 也已填入生效
（`e969f1b`）。本条不再是"等外部条件"，而是**队列里的一件待办**。

🔴 **它现在是 launchd 装机的真前置，而不是反过来。** 服务进程当前 `exit 4` 且**立刻返回**；
而 `com.zhuopin.hr.liaison.plist` 是 `RunAtLoad=true` + `KeepAlive=true` +
`ThrottleInterval=30`。TD-19 未还就装 job，得到的是一台**每 30 秒重启一次、每次立刻失败、
把 `launchd.err.log` 无限追加同一条报错的机器**——plist 自己的注释预警过这个形状（原文针对
退出码 2 的缺凭据场景，退出码 4 是同一形状）。⛔ 灰度前置清单里把 launchd 装机与 TD-19
并列是错的，它排在 TD-19 之后。

### ✅ 2026-09-09（`0909AC`）代码已就位——**本条仍不销**，剩最后一步归 Shao Peishen

**已做完的**（`tools/liaison/session_client.py` / `__main__.py`）：

1. `make_sdk_connect` 现在返回的阻塞调用真的调 **`client.run()`**（同步、跑到断线为止），
   ⛔ 不再是那个协程 `connect`。`REQUIRED_CLIENT_ATTRS` 从 `("on", "connect")` 改成
   `("on", "run")`——清单里只留**真正会被调用**的方法。
2. **护栏没被删、没被降级，只是挪了位置**：`verify_client_surface()` 现在核四项——
   方法齐全、`run` 可调用、`run` **不是**协程函数、`run` 能**零参数**调用。断言在岗：
   `test_make_sdk_connect_refuses_a_coroutine_function_run`、
   `test_make_sdk_connect_refuses_a_run_that_needs_arguments`、
   `test_main_exits_when_the_sdk_run_is_a_coroutine_function`、
   `test_make_sdk_connect_verifies_the_surface_before_run_forever_can_swallow_it`。
   ⛔ 没有加任何"跳过校验"的开关或环境变量。
3. 🔴 **每次建连尝试改用一个全新的连接对象**（`make_sdk_connect` 收的是**工厂**不是对象）。
   实测 `aibot==1.0.2` `client.py::connect` 开头是 `if self._started: return self`，而
   `_started` 只有 `disconnect()` 会清。同一个对象第二次 `run()` ⇒ connect 立刻返回 ⇒
   `loop.run_forever()` 挂在空转的事件循环上 ⇒ **进程活着、日志正常、永远不再连上**，
   ⛔ 没有任何症状。守护断言：`test_make_sdk_connect_builds_a_fresh_client_for_every_attempt`。

**实证**（装了闸门、⛔ 未真连企微）：`python -m tools.liaison` 已能一路走到 `client.run()`，
外层退避 1s → 2s → 4s，且每一轮 SDK 都重新打印 `Establishing WebSocket connection...`
（复用旧对象时这里会变成 `Client already connected` 然后永久挂起——正是第 3 条防的形态）。

**⏳ 曾经仍欠的那一步——已由 `[Mac]0909AE` 完成**：8.6 首次真实建连实测，连接**建立成功**
（`WebSocket connection established` ＋ 认证通过），本适配就此验收销账。⚠️ 那次实测同时暴露了
**另一个** bug（TD-38，`heartbeat_interval` 单位错配），⛔ 它不属于本条——本条守的是
「`run_forever` 拿到的 callable 是否真的阻塞建连」，那一问的答案已经是肯定的。

🔴 **2026-09-09 稍晚订正——⛔ 先别跑真实建连**：`[Mac]0909AE` 已经替本条跑了第一次
（见 `docs/findings/2026-09-09-首次真实建连实测.md`），结果撞上 **TD-38**（`heartbeat_interval`
传秒当毫秒，心跳 ×1000，44 秒即被企微 `45009 Too many requests` 限流）。**TD-38 未还之前
再跑一次只是再洪泛一次**，⛔ 不要重复。下面这条命令保留给 TD-38 还上之后。

⚠️ TD-38 的成因就在本条改的那个文件里（`session_client.py:37`），但它是**单位错配**——
类型相同（int）、只有单位不同，按设计就在 `verify_client_surface` 那道护栏的盲区里，
且**无任何本地症状**，只有真连上企微才暴露。⛔ 不要因此去加"校验单位"的启发式，
真正的判据是 TD-39 那三项端到端观察。

**TD-38 还上之后复跑用的命令**（在仓库根、Terminal 里，⛔ 不经 pytest；本条已销，这条留给 TD-39 复核）：

```bash
PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison
```

看三件事：① SDK 日志出现 `WebSocket connection established` 且**没有** `errcode=853000`；
② 拔网线／断 Wi-Fi 后 `liaison_outage_window` 开出一条窗口、群里收到断线告警；
③ 网络恢复后自动重连、窗口闭合并发出恢复告警。三条都对 ⇒ 本条可销，G-4（launchd 装机）解锁。

🔴 **2026-09-09 `[Mac]0909AG` 已跑完这三条，⛔ 不要重跑**：① ✅、② ✅（新开
`disconnect_event` 窗口，翻转时延 55.75 秒）、③ ❌ **不过**——复网后 122 秒零动作，
窗口永不闭合。定性见 **TD-39**（已从"待复核"升级为已确认缺陷）与
`docs/findings/2026-09-09-断线重连实测.md`。**G-4（launchd 装机）继续锁着**，
解锁条件改为「TD-39 还上并有自动化测试覆盖」。

⚠️ 先跑一次 `... -m tools.liaison --self-check` 更省事：它把凭据与 SDK 表面全校验一遍
就退出（exit 0），⛔ 不建连——凭据打错时不必等到真连才发现。


**销账依据（`[Mac]0909AE`，2026-09-09 21:25:49 CST）**：用真实凭据前台跑
`python -m tools.liaison`，SDK 日志出现 `WebSocket connection established` →
`Authentication successful` → `Authenticated`，`data/liaison/liveness.json` 写出
`state: connected`，`data/liaison.db` 建出五张表。**`client.run()` 接进
`run_forever` 是对的，本条欠的东西已还清。** 全文见
`docs/findings/2026-09-09-首次真实建连实测.md`。

⚠️ **销账 ≠ 8.6 通过**：同一次实测暴露出**另一个**缺陷（心跳单位，**TD-38**），
服务只在线 44 秒就被企微以 `45009 Too many requests` 判死。8.6 的阻断项从 TD-19
**转移**到 TD-38，⛔ 不要因为本条已销就以为 8.6 可以往下走。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-20~~ · `start()` 的启动补记用"本次启动时间"当恢复时间，会低报"重启时网络仍未恢复"的中断时 -->

## ~~TD-20~~ · `start()` 的启动补记用"本次启动时间"当恢复时间，会低报"重启时网络仍未恢复"的中断时长 ✅ 已还（`08d8784`）

**2026-09-09 已处置（`0909U`）**：裁决＝**改法 ①**（2026-09-09 Shao Peishen，见
`docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md` 裁决二），已落地。
`LiaisonSession.start()` ⛔ 不再调 `_backfill_open_windows(now)`——启动时**不闭合**任何
未闭合窗口；闭合统一交给 `on_connected` 的 `CLOSED_BY_RECONNECT` 路径（"启动后首次连上"
走的是同一条），恢复时间因此自然是**真正连上的时间**。`start()` 其余步骤与顺序不变：
读存活戳 → 必要时开 `startup_gap` 窗口 → 补发"已闭合未告警"的旧窗口 → 最后写存活戳。

`CLOSED_BY_STARTUP_BACKFILL` 与 `_backfill_open_windows` 失去调用方后**保留**，
docstring 已注明按本裁决停用——`effect_log` 与 `liaison_outage_window` 里存着用它闭合的
历史行，schema 的 `CHECK` 仍允许该取值，删掉它们那些历史行就失去解释。

回归用例咬住 reviewer 的原始复现时间线（`test_starting_while_still_offline_does_not_
underreport_the_outage`，`tools/liaison/tests/test_session_state_machine.py`）：
`connected@10:10` → `10:40` 网络未恢复时重启 → `12:00` 首次 `on_connected`。
修复前告警「10:10 至 10:40（持续 30 分 0 秒）」，修复后「10:10 至 12:00（持续 1 小时
50 分 0 秒）」＝ 110 分钟。`specs/liaison-channel-session/spec.md` 第 46／56 两处口径同步。

⛔ **本次未做、也不算欠**：改法 ③（接 `run_forever` 的 `on_attempt_failed`）由裁决明确
排除——它是 ① 之上的加强，要做须另立一条，⛔ 不许当成 TD-20 的遗留。

---

<details>
<summary>原始登记（保留备查）</summary>

**欠的是什么**：`LiaisonSession.start()`（`tools/liaison/session.py` ~271-304、~362-370）
在**尚未连上**的时刻就把每一个未闭合窗口（含刚补开的 `startup_gap` 窗口）的
`recovered_at` 记成**本次启动时间**并据此告警——而不是**真正重新连上的时间**。
启动后、首次连上前的那段（`starting` 状态）没有任何机制为它开窗、告警或计入
已发出的那条告警：`tick()` 在 `starting` 状态下直接早退；`on_disconnected` 只在
SDK 事件到达时触发，而"启动后从未连上过"这种情形永远等不到那个事件；
`run_forever` 的 `on_attempt_failed` seam 虽存在，`main()` 从未接上它。

**复现**（reviewer 实测）：末次存活戳 `connected@10:10` → 进程被杀 → 10:40 网络仍未
恢复时重启 → 12:00 才第一次真正连上。结果：只发出**一条**告警，文案是
「10:10 至 10:40（持续 30 分 0 秒）…请重发」，窗口以 `startup_backfill@10:40` 闭合；
10:40 到 12:00 这 80 分钟**没有窗口、没有告警、没有任何记录**。收信人被告知补发
30 分钟，实际丢失的是 **110 分钟**——低报了 80 分钟。

**触发条件**：第 8 章灰度（8.6）接真实 SDK 与 launchd 时。launchd 用
`AtStartup`/`KeepAlive` 在开机或系统唤醒时即拉起服务，这正好早于 Wi-Fi 关联完成
的时间点，是"重启时网络仍未恢复"这一场景最常发生的时刻。届时三条候选修法：
① 启动时不闭合旧窗口，留到 `on_connected` 真正连上时再闭合（告警自然带上真实
恢复时间）；② 启动时改为另开一个新窗口，专等 `on_connected` 闭合；③ 显式接上
`run_forever` 的 `on_attempt_failed` seam，把每次建连失败也计入中断记录。

**不还的后果**：告警会**低报**中断时长——它是"响亮但不完整"的，⛔ 不是静默丢失
（该发的那条告警仍会发出、仍写"请重发"），但收信人会按错误的、偏短的时段去补发，
落在低报区间之外的消息因此永远补不回来。

**⚠️ 标注**：裁决＝**改法 ①**（2026-09-09 Shao Peishen），已落地。原标注「需
Shao Peishen 拍板改法、⛔ 执行方不得自行改动 `session.py` 的状态机行为」已经兑现——
拍板已完成，改动依裁决执行，⛔ 不再是待拍事项。

</details>


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-23~~ `defer_task` 用「幂等键命中」推断状态，而不是读状态 ✅ 已还（80715c6） -->

## ~~TD-23~~ `defer_task` 用「幂等键命中」推断状态，而不是读状态 ✅ 已还（80715c6）

**登记时间**：2026-09-09（第 5 章 run-build 收口，[Mac]0909D）
**位置**：`tools/liaison/queue.py` `defer_task` 的幂等短路分支
**级别**：不阻塞第 5 章（`defer_task` 当前**零生产调用方**，只有测试在调）

**成因**：`idempotent_effect` 的前置检查在被装饰函数体**之前**短路返回，所以对同一
`{thread_id}:effect_defer_task:{msgid}` 的第二次调用走不到存储层 TRIGGER。为满足 spec
「非法转移一律拒绝」，`defer_task` 把"幂等命中"当作"这行已经离开过 pending"的证据直接
抛 `TaskTransitionRejected`。

**缺陷**：「离开过 pending」**不等于**「现在不在 pending」。存储层只在
`NEW.send_status='deferred'` 时触发，`deferred → pending` 在 schema 上是**合法的**，
只是目前没有对应的业务函数。

**触发场景（第 6 章一旦加「取消暂缓/重新置为待发」就会踩到）**：
`defer(M)` 成功 → 撤销暂缓把 `M` 改回 `pending` → 再 `defer(M)` → 幂等键命中 → 抛
`TaskTransitionRejected` 并声称"此前已成功从待发转入过暂缓"。但此刻 `M` 就在 `pending`，
这次转移**完全合法**却被永久拒绝，且**该 msgid 的暂缓从此再也做不成**（幂等键永远命中）。
附带：该分支异常文案硬编码"从待发转入过暂缓"，在 `pending→deferred→pushed→再 defer`
路径下文案也会失真（拒绝结论仍正确）。

**还债动作**（二选一，⛔ 不要改成 `return False`——那与「非法转移一律拒绝」冲突）：
① 在 schema 加一条禁止 `deferred/pushed → pending` 的 TRIGGER，让上述推理真正成立；
② 在幂等命中分支**读一次当前状态**再决定抛什么。

**触发条件**：第 6 章要把 `defer_task` 接进任何调用路径之前。
**不还的后果**：第 6 章接线后，被撤销过暂缓的条目永远无法再次暂缓，且报错信息指向错误
的原因，排查会被带偏。

**为什么第 5 章不改**：本服务有强制结构测试 `test_no_checkpointer_or_langgraph_in_liaison`
钉死 `tools/liaison/` **不含 langgraph/checkpointer**，「节点从头重跑」的重放场景在此服务
不存在；且当前零生产调用方。终审 reviewer 独立核查确认该裁定依据成立。

**已还**（2026-09-09，`80715c6`，[Mac]0909AK）：采用方案 ②（**读状态**），**未加 TRIGGER**。
两条理由：① 方案 ① 那条禁止 `deferred → pending` 的 TRIGGER 会让第 6 章的「撤销暂缓」
在存储层**永远不可能实现**——那是用删掉功能来消灭场景，而这个场景正是本条 TD 自己写明
的触发场景；② 它落在 `storage/schema.py`，不在本次还债的触碰区内。
实现分两层，**判断状态的地方只有一处**：幂等键改带**代际号**
（`{代际}#{msgid}`，代际在前且为纯数字 ⇒ 按首个 `#` 切分永远唯一，⛔ 不会踩 TD-22 那类
分隔符串味的坑），于是一次**新的**暂缓拿到新键、照常走到存储层触发器由它裁定；
只有键仍被占用（并发下两条路径撞同一代际）才**读一次真状态**再决定返回还是抛什么。
异常文案改成只陈述读到的真状态，⛔ 不再硬编码"此前已成功从待发转入过暂缓"
（那句在 `pending→deferred→pushed→再 defer` 路径下是假话）。
⛔ 仍然从不返回 `False`。见 `tools/liaison/queue.py::_next_defer_generation` 与
`defer_task` 的代码注释；`test_queue_status.py` 有 3 条用例守着。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-24~~ `mark_task_pushed` 的推送时间戳只有 thread 级幂等保护，缺第二道防线 ✅  -->

## ~~TD-24~~ `mark_task_pushed` 的推送时间戳只有 thread 级幂等保护，缺第二道防线 ✅ 已还（80715c6）

**登记时间**：2026-09-09（第 5 章 run-build 收口，[Mac]0909D）
**位置**：`tools/liaison/queue.py` `mark_task_pushed`
**级别**：不阻塞第 5 章（当前**零生产调用方**）

**成因**：队列条目的业务身份是**全局唯一**的 `msgid`（`liaison_task.msgid UNIQUE`），但
幂等键是 `{thread_id}:effect_mark_task_pushed:{msgid}`——**带 thread 前缀**。
`enqueue_task` 面对同一问题有 `msgid UNIQUE` 这道结构防线兜底（并有测试覆盖
「同一 msgid 由另一个 thread_id 投递」），`mark_task_pushed` **没有任何兜底**：
存储层的 TRIGGER 只拦 `→ deferred`，`pushed → pushed` 表层完全放行。

**触发场景（第 6 章「群通知外发」正是这个接缝）**：
msgid `M` 归档时 `thread_id = u_tang`（私聊），10:00 首次推送成功、`pushed_at = 10:00`；
第 6 章重试时若按**推送目标群**取 `thread_id = chat_xxx` 调
`mark_task_pushed(thread_id="chat_xxx", msgid="M", pushed_at="11:30")`
→ `effect_key` 不同 → 预检不命中 → UPDATE 真的执行 → **`pushed_at` 被改写成 11:30**，
`effect_log` 里 `effect_mark_task_pushed` 变成 2 行。
docstring「第一次推送的那个时刻才是事实」当场变假，且**没有任何症状**——
`effect_mark_task_pushed` 是 UPDATE 型、不进 `EFFECT_NODE_TO_TABLE`，恒等断言抓不到它。
现有 `test_mark_pushed_twice_is_an_idempotent_no_op` 只走同一个 `thread_id`，测不到这条。

**还债动作**（二选一）：
① 在 schema 加 `BEFORE UPDATE ... WHEN NEW.send_status='pushed' AND OLD.send_status='pushed'`
   的 `RAISE`（与 defer TRIGGER 同一手法，保持「存储层是唯一真源」）；
② `mark_task_pushed` 内部**从任务行读回 `thread_id`**，不由调用方传。

**触发条件**：第 6 章要调用 `mark_task_pushed` 之前。
**不还的后果**：推送时间戳被静默改写，审计上「第一次推送时刻」不再可信，且无告警。

**已还**（2026-09-09，`80715c6`，[Mac]0909AK）：采用方案 ②（**从任务行读回 `thread_id`**），
**未加 TRIGGER**——方案 ① 落在 `storage/schema.py`，不在本次触碰区；而且 ② 才是治本的
那条：它让幂等键回到 `msgid` 这个真正的业务身份上，调用方再也**凑不出**第二把键，
① 只是在凑出第二把键之后再拦一道。方案 ① 的效果由**同一层**的
`AND send_status <> 'pushed'`（`effect_mark_task_pushed` 的 UPDATE 条件）等价补上，
两者合起来就是这条时间戳的两道防线：UPDATE 命中 0 行时区分「条目不存在」（抛
`TaskNotFound`）与「已经是已推送」（内部信号翻成 `False`，且装饰器回滚 ⇒
`effect_log` ⛔ 不会多出第二行）。
入参 `thread_id` 保留但不再参与任何判定（删它会连累不在触碰区的 `test_retention.py`），
docstring 已写明。`test_queue_status.py` 有 2 条用例守着（换 `thread_id` 重复标记、
以及绕过业务层伪造幂等键）。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-25~~ · 非限流错误也落 `pending_resend`，第 8 章重发驱动器会拿到永远重发不成的行 ✅ -->

## ~~TD-25~~ · 非限流错误也落 `pending_resend`，第 8 章重发驱动器会拿到永远重发不成的行 ✅ 已还（26986e8）

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/notify/webhook.py:247` → `tools/liaison/notify/store.py:106`
**级别**：不阻塞第 6 章（spec 只要求「被记录且不被当作成功」，当前实现不违规）

**成因**：`effect_deliver_with_backoff` 对**任何**未送达的结局都返回 `delivered=False`，
`store` 一律落成 `state='pending_resend'`。但 `93000`（机器人不在群）、`40001`（凭据失效）
这类错误重试多少次都是同一结果，与 `45009` 限流有本质区别。

**不还的后果**：`select_pending_resends` 是第 8 章重发驱动器的取数口径，这些行会被
反复取出、反复失败，且每失败一次可能再告警一次——变成一条**永远刷屏的死行**。

**还债动作**（二选一）：① 台账加一列区分「可自动重发 / 需人工介入」；
② 在 `select_pending_resends` 上按 errcode 过滤，只返回 `45009` 一类可重试的。
**触发条件**：第 8 章接重发驱动器之前。

**已还**（2026-09-09，`26986e8`，[Mac]0909V）：采用方案 ②（errcode 过滤），**未加列**，
理由见 `tools/liaison/notify/store.py::RETRYABLE_ERRCODES` 的代码注释——「能不能自动重发」
由 `last_errcode` 派生得出，而它已经在表里，加列就是把同一事实存两遍且不一致时无症状。
`RETRYABLE_ERRCODES = {45009}`（唯一有明文依据的瞬时错误）；`93000` / `40001` /
`last_errcode IS NULL` 归「需人工介入」，由新增的只读 `select_manual_intervention_resends`
原样取得，⛔ 不变成沉默行。两个口径构成对 `pending_resend` 的一个划分，有测试守着。
⏸ 重发驱动器本身仍属第 8 章，本次 ⛔ 未接。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-26~~ 令牌桶无进程级单例，且降级投递第一步 2 次 HTTP 只扣 1 个令牌 ✅ 已还（5ca1f50） -->

## ~~TD-26~~ 令牌桶无进程级单例，且降级投递第一步 2 次 HTTP 只扣 1 个令牌 ✅ 已还（5ca1f50）

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/notify/webhook.py:286`（`bucket` 由调用方注入）、`webhook.py:184-196` + `:236`
**级别**：不阻塞第 6 章（本章零接线代码，两条都要到第 8 章接线才可达）

**成因**：两处独立缺口，后果相同——**实际发送速率能突破 D9 的 20 条/分钟**：
① `bucket` 是 `send_group_notify` 的参数，没有进程级单例。两个调用方各造一个
   `make_group_webhook_bucket()` 就是两份配额，速率直接翻倍。
② `DegradedDelivery` 第一次 `send_next()` 发出**两次** HTTP（`post_multipart` 上传附件
   + `post_json` 发文件消息），但外层只 `bucket.acquire()` 一次。降级通知在压力下
   以约 1.5 倍配额打服务端。

**不还的后果**：主动限流的全部意义就是「不靠被平台打回才知道」。突破配额后又回到
被 `45009` 打回、走退避重试的老路，而这正是本章要消灭的状态。
**还债动作**：① 第 8 章接线时用模块级单例并加断言；② 令 `send_next()` 自报本次要发几个
请求，由 `effect_deliver_with_backoff` 按数取令牌。
**触发条件**：第 8 章给群通知接上真实调用方之前。

**已还**（2026-09-09，`5ca1f50`，[Mac]0909AL）：触发条件已满足——`0909AD` 的 followup CLI
就是第一个真实调用方。两条按还债动作逐字落地：
① `ratelimit.get_group_webhook_bucket(*, monotonic, sleep)` 是**模块级单例**，
   生产代码取桶的唯一入口。**断言**＝第二个调用方带着自己的时钟来一律 `RuntimeError`，
   ⛔ 不静默返回既有单例：带自己的时钟＝它以为自己在造新桶，吞掉的话"有人绕过单例"
   这件事就没有任何症状，而后果是 D9 的 20 条/分钟在服务端那头变成 40。
   `reset_group_webhook_bucket()` ⛔ **只给测试用**（生产里重置＝手动给自己续一份配额）。
   顺带新增只读 `TokenBucket.available_tokens`，让判据能钉在**桶的剩余量**上而不是
   `acquire()` 的调用次数——后者是实现细节，前者才是这条债守的东西。
② `Delivery.pending_requests` 由投递对象**自报**本步要发几次 HTTP，
   `effect_deliver_with_backoff` 按数取令牌。⛔ **没有**写死"降级永远扣 2"：附件只上传
   一次（断点在 `_media_id`），重试那一步只剩 `send_file`，仍按 1 扣——按 2 扣会白吃掉
   一半配额，而症状是"发得比配置的还慢"，没有任何报错。
三条单测：`test_two_callers_share_the_same_group_webhook_bucket`（判据是 `is` 同一性，
⛔ 不放宽成"参数相同"）、`test_a_second_caller_bringing_its_own_clock_is_refused`、
`test_degraded_first_step_takes_two_tokens_for_its_two_http_requests`，
另有 direct 扣 1 与"上传过之后重试只扣 1"两条对照组。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-27~~ · `make_group_webhook_delivery` 对 `MODE_REJECT` 静默 -->

## ~~TD-27~~ · `make_group_webhook_delivery` 对 `MODE_REJECT` 静默降级，会发出一条空 markdown ✅ 已还（26986e8）

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/notify/webhook.py:199-203`
**级别**：不阻塞第 6 章（`store` 提前短路，当前不可达）

**成因**：docstring 写着「⛔ `MODE_REJECT` 到不了这里（store 提前短路）」，但真到了这里
不是报错，而是**静默走 `DirectDelivery`**——`plan.body` 在 reject 模式下为空，结果是往群里
发一条 `body=""` 的空 markdown。「到不了这里」这个前提由**调用方**保证，而不是由结构保证。

**不还的后果**：将来有人从别处调 `make_group_webhook_delivery`（它是公开函数），
「拒发」会静默变成「发一条空消息」——比拒发更糟，因为它看起来成功了。
**还债动作**：`MODE_REJECT` 分支改成 `raise ValueError`，并补一条测试。
**触发条件**：`make_group_webhook_delivery` 出现第二个调用方之前。

**已还**（2026-09-09，`26986e8`，[Mac]0909V）：`MODE_REJECT` 分支改为 `raise ValueError`
（文案点名"拒发模式不产生投递对象，调用方必须提前短路"），
`test_notify_webhook.py::test_reject_mode_refuses_to_produce_a_delivery_object` 断言它真的抛
且抛在任何 HTTP 之前。`store` 那条提前短路是正路，⛔ 未动。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-28~~ `liaison_group_notify` 的 CHECK 只守字段取值域，跨字段的荒唐组合能写进 -->

## ~~TD-28~~ `liaison_group_notify` 的 CHECK 只守字段取值域，跨字段的荒唐组合能写进去 ✅ 已还（5ca1f50）

> 🔴 **`.51` 现网补法已裁定（Shao Peishen 2026-09-10 答 `1a`）：灰度真发前重建表。**
> 当前若为 0 行则代价为零；若已有行则**先导出再灌回**。⛔ 不引入迁移机制（选项 b 未采纳），
> ⛔ 不接受「只有新库守得住」（选项 c 未采纳）。
> 判据＝灰度真发前，`.51` 上 `liaison_group_notify` 的建表语句含 `5ca1f50` 那两条跨字段 CHECK。
> **谁做**：另起 session（触碰 `.51`，属发版决定），⏸ 待派发。⛔ 本条在现网做完前，TD-28 只算「新库已还」。
> ✅ **现网已做 2026-09-17（`[Mac]0917AX`）**：「现网」实为 **Mac 本机 `data/liaison.db`**（liaison 永不上 `.51`，`0917AW` 核实此前「`.51` 现网」是误标）。0 行直接重建，`sqlite_master` 含两条跨字段 CHECK，负向插入实证被拒；备份 `data/liaison.db.bak-20260917-2135`。TD-28 自此**全部已还**。

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/storage/schema.py` `GROUP_NOTIFY_SCHEMA`
**级别**：不阻塞第 6 章（现有代码路径产不出这些行）

**成因**：表注释宣称「结构（主键）与机制（idempotent_effect）两道防线守同一件事」，
但 CHECK 实际只约束了各字段各自的取值域。review 期用直连 SQL 逐条**实证写入成功**：
- `state='sent'` + `mode='reject'`（"被拒发的通知已送达"）
- `state='rejected'` + `mode='direct'` + `attempts=99`
- `byte_length=-5` / `limit_bytes=-1` / `attempts=-3`

**还债动作**：补 `CHECK ((state='rejected') = (mode='reject'))` 与
`CHECK (attempts >= 0 AND byte_length >= 0 AND limit_bytes > 0)`。

**已还**（2026-09-09，`5ca1f50`，[Mac]0909AL）：两条按原文逐字补进 `GROUP_NOTIFY_SCHEMA`。
**只新增**——⛔ 未删任何既有列、⛔ 未改既有 CHECK（含 `(state='sent') = (sent_at IS NOT NULL)`）
的语义。`limit_bytes > 0` 而不是 `>= 0`：阈值为 0 意味着任何正文都超限，那不是阈值是死锁；
`byte_length = 0`（空正文）与 `attempts = 0`（还没发过）都是合法值，四条对照组用例
（`test_legitimate_group_notify_rows_still_get_through`）钉住 ⛔ 不许误伤正路——收紧约束
最常见的失败模式不是"没挡住"，是"顺手把合法的也挡了"，而那要到真发时才炸。
七条否定用例（`test_absurd_group_notify_rows_are_refused_by_the_schema`）**直连 SQL 写入**，
刻意绕开 `effect_send_group_notify`：本条要验的是「表结构自己守不守得住」，走代码路径
等于用被测对象证明被测对象；登记时就是用直连 SQL 实证写入成功的，还债用同一把尺子量。

**既有数据是否违反**：本机 `data/liaison.db` 的 `liaison_group_notify` **0 行**，
违反新 CHECK 的行数 **0**。`.51` 现网 ⏸ **未查**（本泳道无服务器访问）。
🔴 **仍欠一步（本条的残留）**：本仓库**没有任何迁移机制**（`storage/` 下无 migration /
`ALTER TABLE` / `user_version`），DDL 全部走 `CREATE TABLE IF NOT EXISTS`——因此新 CHECK
**只对新建的表生效**，已存在的库仍是旧结构。已实证：本机 `data/liaison.db` 里那张表的
`sqlite_master.sql` 至今不含新 CHECK。当前 0 行所以无害，但 ⛔ 不得当作"现网也守住了"。
**触发条件**：`.51` 上 `liaison_group_notify` 写入第一行真实数据之前，必须先决定
"重建表还是加迁移"。⛔ 不在本泳道自行处置——`.51` 的任何结构变更属发版决定。

**附带两条本章 blocking 修复引入的、已判可接受的后果**（⛔ 不是缺陷，登记备查）：
① 主键改成 `(thread_id, digest)` 后 `digest` 不再全局唯一也无单列索引——第 8 章若要
   "按 digest 单独查一条"会全表扫描。当前唯一取行路径 `select_pending_resends` 走
   `idx_liaison_group_notify_state`，不受影响。
② `sent_at` 从微秒降到秒级（与 `created_at` 的 `datetime('now')` 对齐的必然结果），
   同一秒内多条通知在 `sent_at` 上不再可分辨，排序另有 `thread_id, digest` 兜底。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-30~~ 日志留存期只有容量上界，缺时间维度的清理 ✅ 已还（ac686d1） -->

## ~~TD-30~~ 日志留存期只有容量上界，缺时间维度的清理 ✅ 已还（ac686d1）

**2026-09-09 已处置（`0909T`）**：按下方「还债动作」逐字落地。新增独立的
`HR_LIAISON_LOG_RETENTION_DAYS`（默认 **30**，非法值走 `RetentionConfigError` 同一套
fail-closed），`run_cleanup` 里按 **mtime** 清 `data/liaison/logs/*.log.*`。
⛔ **没有**复用 `HR_LIAISON_RETENTION_DAYS`（180）——D13 明写两者刻意不对齐，
`test_log_retention_days_has_its_own_env_var` 把这条钉死。
三处刻意的收窄：① ⛔ 不删当前活动日志 `liaison.log` 本体（`RotatingFileHandler`
正攥着它的 fd，unlink 之后日志会静默进黑洞直到下一次轮转）；② ⛔ 不递归子目录、
⛔ 不跟符号链接；③ 日志那一遍与归档那两遍**完全解耦**——一条读不出来的
`attachments_json` 会停住归档清理，但 ⛔ 不该连坐日志（两者之间没有任何引用关系）。
清理目录取自 `logsetup.resolve_log_dir()`，与 `setup_logging` 真正写日志的目录同一个
真源，⛔ 不在清理侧另读一遍环境变量。

**登记时间**：2026-09-09（第 8 章 8.4 run-build 收口，[Mac]0909M）
**位置**：`tools/liaison/logsetup.py`（缺的动作不在任何文件里）
**级别**：不阻塞 8.4（容量上界已满足，磁盘写不满）

**欠的是什么**：design D7 从 `runtime-observability` 借的是三条做法——有界轮转文件、
个人信息脱敏、**留存期有上限**。8.4 的 opener 只写了前两条，本章按 opener 范围做，
未擅自扩大。`RotatingFileHandler(maxBytes, backupCount)` 让磁盘占用 ≤
`maxBytes × (backupCount + 1)`（默认 5 MiB × 6 = 30 MiB），
所以「日志占用的存储空间 MUST 有明确上界」这条**是满足的**。

**不满足的是时间维度**：「超过留存期的日志 MUST 被清理」。低流量时一份含个人信息的
历史日志可以躺很久——`.gitignore:17` 逐字写明「日志是个人信息的第二份拷贝」，
躺得越久，PIPL 下的暴露面越大。这不是磁盘问题，是留存期问题。

**还债动作**：在 8.1 的留存清理任务里加一条按 mtime 清理 `data/liaison/logs/*.log.*`，
用**独立的** `HR_LIAISON_LOG_RETENTION_DAYS`（默认 **30**）。
⛔ 不要复用 `HR_LIAISON_RETENTION_DAYS`（180）——D13 明写了两者刻意不对齐：
日志是运行证据，归档是工作材料，两者的留存期本就不同。

**触发条件**：8.1 留存清理落地时顺带做；最迟不得晚于 8.6 灰度真发
（那之后日志里开始出现真实候选人内容）。
**不还的后果**：含个人信息的历史日志无限期驻留，PIPL 的最小必要与留存期限要求失守，
且**没有任何症状**——磁盘不会满，监控不会响。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-31~~ 带分隔符渲染的手机号不脱敏 ✅ 已还（`255fd36`） -->

## ~~TD-31~~ 带分隔符渲染的手机号不脱敏 ✅ 已还（`255fd36`）

**登记时间**：2026-09-09（第 8 章 8.4 final review，[Mac]0909M）
**位置**：`tools/liaison/logsetup.py` `_VALUE_PATTERNS` 的手机号正则
**级别**：不阻塞 8.4（**实测当前不可达**）

**成因**：手机号正则认的是 11 位连写（`+86`/`0086` 前缀已在 Task 1 fix round 覆盖），
带分隔符或全角的渲染整条失配。final reviewer 实测：

```
'138-1234-5678'     -> 原样
'138 1234 5678'     -> 原样
'138.1234.5678'     -> 原样
'+86-138-1234-5678' -> 原样
'１３８１２３４５６７８'      -> 原样（全角数字）
```

**为什么现在不修**：reviewer 逐条读了全部 12 处非测试 `logger.*` 调用点
（`inbound.py:139,151`、`alerts.py:60,114`、`session.py:233,236,293`、
`session_client.py:104`、`whitelist.py:105,122`、`__main__.py:110,195`），
**没有一处记录候选人键入的消息正文**——`alerts.LoggingAlertSink.send` 收到的
只有系统合成的中断告警文本。此刻无真实泄露面。

**触发条件**：任何一处调用点开始把消息正文／候选人自填内容写进日志之前。
**不还的后果**：候选人手抖用了分隔符格式的手机号即明文落盘，且**无症状**——
脱敏看起来在工作（同一行里的邮箱照样打码），只有这一种渲染漏。

**已还**（2026-09-09 `[Mac]0909AM`，轻量通道 TDD，`255fd36`）：`_VALUE_PATTERNS` 的手机号
分支改成「头三位 `1[3-9]\d` 连写 + 可选 3-4-4 分组」，并把数字类扩到全角。
逐条对应 TD 正文列的五种漏法：`138-1234-5678` / `138 1234 5678` / `138.1234.5678` /
`+86-138-1234-5678` / `１３８１２３４５６７８`，另补 `(+86) 138 1234 5678`、`(86)13812345678`
两种括号区号渲染。

⚠️ **放宽的边界是刻意画死的**（opener：⛔ 不许放宽到会误伤正常数字串）：
① 号段头三位必须连写，⛔ 分隔符不进这三位——这是挡住「一串被空格隔开的无关数字」的主护栏；
② 分隔后只认 **3-4-4** 一种分组；
③ 分隔符只收连字符/点/空格（含全角），⛔ **不收 `,` `;` `/`**——那些在日志里分隔的是两个
不同字段，收进来会把三段无关数字连成一个假手机号。

**咬住它的用例**（`tools/liaison/tests/test_liaison_log_redaction.py`）：`_PHONE_RENDERINGS`
八族 × 两个方向——`test_phone_rendering_is_masked`（该脱敏的被脱敏）与
`test_phone_lookalike_is_left_alone`（同族里差一位/差一个分组的近似串必须原样保留）；
外加 `test_phone_separator_class_does_not_join_unrelated_fields` 与
`test_log_format_timestamp_is_not_mistaken_for_a_separated_phone` 两条护栏。
⚠️ 本条**无症状**，⛔ 后人改这条正则时不许删这批用例——删了缺陷会静默复发。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-32~~ JSON/全角冒号渲染下 `thread_id` 反被打码，归档链路可能对不上 ✅ 已还（`255f -->

## ~~TD-32~~ JSON/全角冒号渲染下 `thread_id` 反被打码，归档链路可能对不上 ✅ 已还（`255fd36`）

**登记时间**：2026-09-09（第 8 章 8.4 Task 1 review 裁决，[Mac]0909M）
**位置**：`tools/liaison/logsetup.py` `_PROTECTED_SPAN_RE`
**级别**：不阻塞 8.4（**实测当前不可达**）

**成因**：保护段只认 `key=value` / `key:value` 且分隔符紧贴键名。键名带引号或用全角冒号时
整条失配，于是取值落进脱敏区：

```
'{"thread_id": "13812345678"}' -> '{"thread_id": "<redacted:phone>"}'
'thread_id：13812345678'        -> 'thread_id：<redacted:phone>'
```

`thread_id` 私聊时取的就是 `userid`，而企微后台允许把 userid 配成手机号——这不是假想。
一旦被打码，那条日志就再也和 `liaison_message.thread_id` 对不上，排障链路断掉。

**为什么当次不修（裁决留痕）**：修法是让保护段也认引号/全角冒号，
方向＝**扩大明文豁免面**，与合规红线反向。无人值守场景下按「保守方向」裁定不修。
现网两处调用点（`inbound.py:141,152`）均为 `"thread_id=%s msgid=%s"` 无空格渲染，
实测 fix 前后字节一致，今天不触发。

**触发条件**：任何调用点改用 JSON/dict/`%r`/全角冒号渲染 `thread_id` 或 `msgid` 之前。
**不还的后果**：排障时日志里的 `thread_id` 与库里对不上，且**无症状**——
看起来只是"脱敏很尽职"。
⚠️ 还债时 ⛔ 不要顺手把 `sender_userid` 一起纳入保护名单，那是真的扩大明文面。

**已还**（2026-09-09 `[Mac]0909AM`，轻量通道 TDD，`255fd36`）：`_PROTECTED_SPAN_RE` 拆成两支，
⛔ **不是把原来那支放松**——
· 分支 1（键名被**成对引号**包住，即 JSON / dict / `%r` 渲染）：键名后允许闭合引号、分隔符
  补上全角 `：` `＝`，取值前额外允许**一个半角空格**（`json.dumps` 的默认分隔符就是 `": "`，
  数值型取值 `{"thread_id": 13812345678}` 不带引号，不放这一个空格照样落进脱敏区）。
  ⛔ 只放一个空格、⛔ 不用 `\s*`——`\s` 含 `\n`，那正是 Round-1 修掉的出血。
· 分支 2（裸键名）：**逐字保持 Round-1 语义**，`thread_id= <下一个 token>` 仍按空值处理。
  裸键名没有「这是结构化渲染」的证据，宁可多打码。
裸值终止符另补两种引号与全角逗号/分号/冒号/等号/右括号，否则
`thread_id：wm001，mobile：138…` 会把后一对键值一起吞进保护段，反过来**扩大**明文面。

**逐字遵守了本条的警告**：`sender_userid` ⛔ 未加入 `PROTECTED_KEYS`，且
`test_sender_userid_stays_masked_in_the_new_renderings` 在三种新增渲染下钉死它仍被打码。

**咬住它的用例**：`_PROTECTED_RENDERINGS` 九种渲染 × 两个方向——
`test_protected_key_survives_every_rendering`（受保护键不许被反向打码）与
`test_neighbouring_phone_still_masked_in_every_rendering`（⛔ 不许顺带豁免同行的手机号）；
外加 `test_quoted_key_branch_does_not_swallow_the_next_pair` 与
`test_bare_key_with_space_before_value_still_falls_back_to_empty_value` 两条护栏。

⏸ **留步（本次刻意不做，非漏做）**：裸键名 + 空白 + 裸值（`thread_id= 138…`）仍按空值处理，
该取值会被打码。修它＝在**没有结构化证据**的渲染上扩大明文豁免面，方向与合规红线相反，
按 CLAUDE.md 的「保守方向」不做。触发条件＝真出现这种调用点渲染时再议。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-33~~ ✅ 已还（25d8715） · `idempotent_effect` 的 `effect_log` -->

## ~~TD-33~~ ✅ 已还（25d8715） · `idempotent_effect` 的 `effect_log` INSERT 只兜 `IntegrityError`，其余异常不回滚

**登记**：2026-09-09 [Mac]0909K（8.1–8.2 留存清理 run-build，Task 2 实现者发现、终审复核确认）
**位置**：`app/storage/idempotency.py`（第二个 `try` 块）
**范围**：⚠️ **不是本单元引入的**，是共享基础设施的既有缺口，`effect_archive_message` / `effect_enqueue_task` 等**所有**既有 effect 同样受影响。

第二个 `try`（写 `effect_log`）只 `except sqlite3.IntegrityError`。若该 INSERT 抛的是**别的**异常
（磁盘满的 `OperationalError` 是最现实的一种），异常直接向上抛，**中间不做 `conn.rollback()`**——
于是 `fn` 已经完成的业务写就那样悬在连接**尚未提交的事务**里，直到某个不相干的后续 `commit()`
把它悄悄一起带走。

**为什么当次不修**：`app/` 在本交付单元的文件范围之外（opener 与计划都写死 ⛔ 不碰 `app/`），
且改动会同时影响所有既有 effect 的失败语义，不该由一条泳道顺手改。

**触发条件**：下一次有人动 `app/storage/idempotency.py`，或第 8 章灰度（8.6）之前。
**不还的后果**：这正是铁律 1 要防的那类事故的近亲——`.51` 2026-08-10 / 08-12 两轮 `outbox` 丢失
是"业务写失败、幂等记录成功"，这条是"业务写成功、提交归属不明"。两者都**无症状**。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-34~~ · `test_file_appearing_after_the_scan_is_not_delet -->

## ~~TD-34~~ · `test_file_appearing_after_the_scan_is_not_deleted` 并不能区分修复前后 ✅ 已还（ac686d1）

**2026-09-09 已处置（`0909T`）**：新写
`test_ledger_row_committed_after_the_scan_still_protects_its_file`，它对读顺序**真正敏感**。
两处改动缺一不可：① 那个文件在 `fake_iter` 调 `real_iter` **之前**就落盘，因此
**包含在返回的候选快照里**（旧用例的致命处正是它不在快照里，而「不在快照里的文件
不会被删」是恒真的）；② 它的台账行在**扫盘之后**才提交（`_archive` 内部 commit），
模拟 `archive_message` 先写文件后写台账行的真实顺序。

**自证（⛔ 未用 `git stash`，CLAUDE.md 并行铁律禁止）**：用
`git worktree add --detach` 在修复前的 `1f2d018` 上开一个一次性 worktree，把新旧两条
用例的等价探针一起丢进去跑，实测 **新用例 FAILED（`deleted_files` 里真的出现了
`u1/20260101/late__c.bin`，即 design D3 禁止的「台账已记、材料缺失」）、旧用例 PASSED**，
与本条登记的判断逐字吻合。探针与该 worktree 已删除，⛔ 未进版本管理。
旧用例**保留**并改了 docstring，说明它守的是另一件事（「不在快照里的文件不会被删」
这条自愈性质），⛔ 不再把它当 finding 5(a) 的回归测试。

**登记**：2026-09-09 [Mac]0909K（8.1–8.2 终审后 scoped re-review 实测发现）
**位置**：`tools/liaison/tests/test_retention.py`（该用例）

它是终审 finding 5(a)（`run_cleanup` 先建 `referenced` 后扫盘的读顺序竞态）唯一的回归测试，
但**对读顺序不敏感**：用例里的 `fake_iter` 先读真实目录列表、**之后**才把"迟到的"文件写到盘上再返回，
所以那个文件无论如何都不会出现在返回的列表里。re-reviewer 把这条用例原样丢进修复前的
checkout（`1f2d018`）跑，**通过**。

⚠️ **生产代码的修复本身是对的**，已被 re-reviewer 用另写的探针独立验证：探针在 `1f2d018` 上能复现
误删、在 `6c2a315` 上文件被正确保护。欠的只是"能咬住"的回归测试。

**为什么当次不修**：终审只有一轮修复波次，无第二轮；该项经裁定**不承重**
（无下游任务依赖它，且生产行为已独立验证正确），故 park 并转成技术债。
**触发条件**：下一条泳道再动 `run_cleanup` 的读顺序之前（8.3/8.4 或 8.6 灰度）。
**不还的后果**：未来某次重构悄悄把读顺序改回去，整套测试仍然全绿——
症状是"归档文件在台账行还没写完时被删掉"，即 design D3 明令禁止的那个中间态。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-35~~ · `assert_effect_log_identity` 对 `liaison_task` 的严 -->

## ~~TD-35~~ · `assert_effect_log_identity` 对 `liaison_task` 的严格恒等与裁决一冲突 ✅ 已还（`0909AN`）

**登记**：2026-09-09 `0909T`（还冲突 B / 裁决一时当场发现）
**位置**：`tools/liaison/tests/test_liaison_effects.py` 的 `assert_effect_log_identity`
（第 775 行那个 `if table == "liaison_message":` 收窄）
**级别**：不阻塞 8.6 灰度（队列侧的账目已有等价强度的替代断言，见下）

**欠的是什么**：8.1–8.2 终审的 finding 2 把「清理会让业务表行数变少」这条豁免
**刻意收窄**到只对 `liaison_message` 成立，理由逐字是「`liaison_task` 从不被清理删除
（opener 约束 2）」。裁决一（2026-09-09）推翻了那个前提——终态（`pushed`）且超期的
队列行现在会被连带清掉，于是在**被清理过的 thread** 上，
`effect_enqueue_task` 的 `effect_log` 行数必然大于 `liaison_task` 的行数，
`assert_effect_log_identity` 会因为一个**完全正当**的理由变红。

**当次为什么不改**：那个文件由别的泳道持有，`0909T` 的 opener 明确列了可动文件清单
（⛔ 只动 `retention.py` / `logsetup.py` / 两个对应测试 / 两份规格 / TD-30·34 两段），
擅自改一个共享的机器守卫会和并行泳道撞在同一个函数上。

**当次的兜底（⛔ 不是"没管"）**：
1. `test_retention.py` 的 `assert_retention_accounting` 加了**第二条记账等式**
   ——`入队 effect 行数 == 存活队列行数 + 连带已清的队列行数`，右边那一项完全从
   `effect_log`（一行不删）推出来：既留下过 `effect_enqueue_task` 又留下过
   `RETENTION_DELETE_NODE` 的那些 `business_key`。⛔ 不是宽松判据，强度与原恒等式相当。
2. `test_identity_assertion_does_not_yet_account_for_a_cleaned_queue_row` 用
   `pytest.raises(AssertionError)` 把这个缺口**钉成可见的**，⛔ 不让它静默。

**还债动作**：把 `assert_effect_log_identity` 的豁免从「只对 `liaison_message`」放开到
「`liaison_message` 与 `liaison_task` 都对 `cleaned_threads` 豁免」，
并在 docstring 里把 finding 2 的理由更新成裁决一之后的口径。
🔴 ⛔ **不许**顺手把它削弱成总数比较或「约等于」——那是原 docstring 明令禁止的，
它是铁律 1 唯一的机器守卫。⛔ 也不许删掉
`test_identity_assertion_still_catches_a_break_in_an_uncleaned_thread`。
还完之后 `test_identity_assertion_does_not_yet_account_for_a_cleaned_queue_row` 必须
**改成正断言**（直接调 `assert_effect_log_identity(conn)` 且通过），⛔ 不许删掉了事
——删掉就等于把这个缺口重新变成静默的。

**销账（2026-09-09 `0909AN`）**：豁免已放开，但**没有**按上面「还债动作」的字面写法
（把 `liaison_task` 也整个 thread 豁免）——那个写法实测会把守卫变瞎，见下。

- **落地判据（逐字）**：豁免的**不是 thread，是行**。等式改为按 thread 分组的
  `effect 行数 == 业务表存活行数 + 已清行数`，其中「已清」＝本节点的 effect 行里、
  `(thread_id, business_key)` **同时**留下过一行 `RETENTION_DELETE_NODE` 的条数。
  没有清理发生时右项恒为 0，等式退化回原来的严格恒等。⛔ 没有总数比较、没有约等于。
- **哪部分被豁免 / 哪部分仍红**：只豁免"有清理记录可解释"的那些行。**解释不掉的差额
  一律仍红**——业务行不见了却没有清理记录、业务行凭空多出来，在**已被清理过**的
  thread 上照样当场变红。
- **为什么不按字面写法**：实测「两张表都按 thread 整个豁免」会让上述三种破裂在被清理过的
  thread 上**全部静默通过**（`0909AN` 用复刻旧/朴素两版逻辑的脚本逐一喂过场景验证）。
  行级抵扣三种全部抓住，比 TD-35 登记时的形态**更强**。
- **`0909T` 的缺口用例已按要求改回正断言**：`test_retention.py` 的
  `test_identity_assertion_does_not_yet_account_for_a_cleaned_queue_row`
  → `test_identity_assertion_now_accounts_for_a_cleaned_queue_row`，`pytest.raises`
  换成直调 `assert_effect_log_identity(conn)` 通过，⛔ 没有删除；同一条里补了下半段
  证伪（同一个已清理 thread 上凭空多一行 ⇒ 仍必须红）。
- **新增三条证伪 + 一条正向**（`test_liaison_effects.py`）：
  `test_identity_accounts_for_a_collaterally_cleaned_queue_row`、
  `test_identity_still_catches_an_unexplained_task_gap_on_a_cleaned_thread`、
  `test_identity_still_catches_an_unexplained_message_gap_on_a_cleaned_thread`、
  `test_identity_still_catches_an_unexplained_extra_row_on_a_cleaned_thread`。
- `test_identity_assertion_still_catches_a_break_in_an_uncleaned_thread` 与
  `test_identity_assertion_excludes_only_threads_that_were_actually_cleaned` 均**未删**
  （后者名字保留自还债前，docstring 已注明粒度已从 thread 收到行）。
- ⛔ 未动任何产品代码；`assert_retention_accounting` 的两条记账等式原样保留，与本守卫并行盯住，互不替代。
- pytest：2066 passed / 5 skipped → **2070 passed / 5 skipped**，0 失败。

**触发条件**：下一条持有 `test_liaison_effects.py` 的泳道；最迟不得晚于 8.6 单机灰度
（灰度会真的产生终态队列行，那之后这条守卫的覆盖缺口开始有实际影响）。
**不还的后果**：`effect_enqueue_task` ↔ `liaison_task` 这一对在被清理过的 thread 上
不再有任何断言检查——那正是铁律 1 的核心不变式，而缺口是静默的。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-36~~ · TD-19 一旦还上，两条凭据用例会带着假凭据向企微发起真实建连 ✅ 已还（`0909AC`，与 -->

## ~~TD-36~~ · TD-19 一旦还上，两条凭据用例会带着假凭据向企微发起真实建连 ✅ 已还（`0909AC`，与 TD-19 同一 commit）

**欠的是什么**：`test_liaison_credentials.py` 的
`test_entrypoint_succeeds_when_credentials_present` 与
`test_entrypoint_reads_dotenv_when_process_env_is_absent` 用 `sys.executable` 起真实子进程，
喂进去的是**假凭据**（`bot-1`/`sec-1`、`bot-from-file`/`sec-from-file`）。今天它们安全，
纯属**运气**——进程走到 SDK 表面校验就被 `SdkSurfaceUnverifiedError` 拦住（`exit 4`），
在任何网络动作之前。

TD-19 还上之后这道拦阻消失，同样两条用例会让子进程带着假凭据**真的去连企微 WebSocket**。
后果：跑一次本地测试就向企微服务端发起若干次认证失败的连接；在 `KeepAlive` 之外也构成
对外部服务的非预期请求，且**测试从此依赖网络可达**（离线时转红，原因与被测行为无关）。

**触发条件**：TD-19 落地的**同一个变更**里必须一并处理，⛔ 不能等它响。
建议方向（未裁决）：给入口加一个只做启动期自检、在建连前退出的开关，让这两条用例走它；
或在用例侧把 SDK 构造点 monkeypatch 掉。⛔ 不要靠"假凭据反正连不上"来免责——
连不上也是发出去了。

**不还的后果**：静默。测试照常绿，没有任何报错会告诉你它刚才对着企微生产端点做了几次
失败认证。发现它的时刻通常是外部限流或安全告警，而不是测试失败。

**来源**：`[Mac]0909AA` 在裁决甲类两条红时顺带识别（Shao Peishen 2026-09-09 批准登记）。
相关：TD-19、`docs/findings/2026-09-09-tools-venv-建立后四条测试转红.md`

### ✅ 2026-09-09 已还（`0909AC`），两层各管一半

**① 被测路径改走启动期自检**：入口新增 `--self-check`（`__main__.SELF_CHECK_ARG`）——把
「读 .env → 校验凭据 → 造连接对象 → 核 SDK 表面 → 接事件」整条路径**原样跑完**，在
`run_forever` 之前返回 0。⛔ 它**不跳过任何一项校验**（守护断言
`test_self_check_runs_the_whole_startup_path_then_stops_before_connecting`：表面对不上时它
照样以 exit 4 拒绝）；它跳过的只是唯一会碰网络的那一步。
⛔ **不许写进 launchd plist**（会让服务每次拉起就 exit 0、值守通道从此不存在且无症状），
守护断言 `test_plist_never_runs_the_self_check_mode`。

判据同时**变严**了：旧断言只说「不是 exit 2」，进程实际停在哪靠下游某一关碰巧拦住；
新断言直接要求整条自检走通（装了 SDK ⇒ exit 0）。**期望值是测出来的、⛔ 不是写死的**
（`_sdk_available_to_subprocess()`）——把"跑测试的解释器装没装 aibot"写进断言正是
`docs/findings/2026-09-09-tools-venv-建立后四条测试转红.md` 记的那个坑。

**② 加了一道机器判据**：`tools/liaison/tests/netguard/`——一个 `sitecustomize.py` 闸门，
非回环的 `getaddrinfo` / `connect` / `connect_ex` 一律 raise。进程内由 conftest 的 autouse
fixture 装上，子进程由 `netguard_support.subprocess_env()` 塞进 `PYTHONPATH`（`site` 在
解释器启动时自动 import）——**两条缺一不可**，因为要防的那条路径跑在子进程里。
于是「测试不触网」从"我看了一遍觉得没有"变成了一条会红的断言。

⚠️ **落地过程中实测到一次真实外发，已单独落档**：闸门第一版按主机名放行回环，而本机
`HTTPS_PROXY=http://127.0.0.1:<port>`——建连打到回环、被放行、由代理转发到企微，
企微回了 `errcode=853000`。只清 `*_PROXY` 环境变量**不够**（`websockets` 走
`urllib.request.getproxies()`，macOS 上还读系统代理设置）。现已连
`getproxies`/`proxy_bypass` 一起摁掉，判据用例也加了 **websockets 栈**的第二个探针。
详见 `docs/findings/2026-09-09-测试网络闸门被本机代理绕过.md`。

**验收实证**：`tools/liaison/.venv` 里 **750 passed / 0 failed**（基线 741）；根 venv 全量
**2005 passed / 5 skipped / 0 failed**。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-38~~ · `heartbeat_interval` 单位错配：传秒当毫秒，心跳频率 ×1000，44 秒即 -->

## ~~TD-38~~ · `heartbeat_interval` 单位错配：传秒当毫秒，心跳频率 ×1000，44 秒即被企微限流 ✅ 已还（`840f5cf`，`0909AF`）

**欠的是什么**：`tools/liaison/session_client.py:37` 的 `DEFAULT_HEARTBEAT_SECONDS = 30`
被 `build_ws_options`（同文件 118–131 行）原样传给 SDK 的 `heartbeat_interval=`，而
`aibot==1.0.2` 的这个参数**单位是毫秒**（`types.py:49`：`heartbeat_interval: int = 30000`，
docstring 明写「心跳间隔（毫秒），默认 30000」；`ws.py:299` 实际 `asyncio.sleep(interval/1000)`）。
于是 30 秒被解读成 **30 毫秒**，心跳频率放大 **1000 倍**。

**实证**（⛔ 不是推测，`[Mac]0909AE` 现网实测）：建连后 44 秒内发出 **1399** 次心跳
（契约应为 1–2 次），日志首行即 `Heartbeat timer started, interval: 30ms`；
21:26:33 企微返回 `errcode 45009 Too many requests`，SDK 随即
`No heartbeat ack received for 2 consecutive pings, connection considered dead`。
全文与统计见 `docs/findings/2026-09-09-首次真实建连实测.md`。

**不还的后果**：🔴 **值守通道无法保持在线**，且每次拉起都在对企微生产端洪泛
（≈32 次/秒）。**装了 launchd 会放大**：无人值守地反复重启 → 反复洪泛 → 反复吃 45009，
存在 bot 凭据被限流甚至封禁的风险。⛔ **TD-38 未还前不得装 launchd。**

**触发条件**：**立即**，8.6 的阻断项。

**已考虑但未做的改法**（留给还它的人，⛔ 不要当成结论）：把常量改名成毫秒口径并按毫秒传
（如 `DEFAULT_HEARTBEAT_MS = 30_000`），⛔ 不要只把取值从 30 改成 30000 而留着
`_SECONDS` 的名字——那正是本条的成因。还它时**必须一并复核 `reconnect_interval`**：
SDK 默认 `1000`（毫秒 = 1 秒），而 `session_client.py:22-25` 的注释称
`MAX_BACKOFF_SECONDS = 30.0` 与「SDK 内置退避的封顶取同一个数」，那是在两种单位下比的，
结论不成立（本模块当前没传该参数，故未爆）。

**为什么表面校验没拦住**：`verify_client_surface` 守的是方法名、事件名、`run` 的签名与
协程性，全部通过。这是一个**类型相同（int）、单位不同**的参数错配 —— 按设计就在那道护栏的
盲区里，且**无任何本地症状**，只有真连上企微才暴露。这也是"必须真跑一次"的价值所在。

**来源**：`[Mac]0909AE` 首次真实建连实测。相关：TD-19、TD-39、
`docs/findings/2026-09-09-首次真实建连实测.md`

**怎么还的**（`840f5cf`，`[Mac]0909AF`，Shao Peishen 2026-09-09 答 `1a` 授权）：
- `DEFAULT_HEARTBEAT_SECONDS = 30` → `DEFAULT_HEARTBEAT_MS = 30_000`（`session_client.py`），
  常量处写明单位与 SDK 契约出处；`build_ws_options` 的关键字默认值同步改名。
- 原断言 `assert options.heartbeat_interval == session_client.DEFAULT_HEARTBEAT_SECONDS`
  是**同义反复**——拿传进去的值跟它自己比，单位错成什么样都绿，**这正是本条活到真实建连
  才暴露的原因**。改成绝对值 `== 30_000`，并**先确认它在修复前真的红**
  （带真 SDK 的 `tools/liaison/.venv` 里报 `assert 30 == 30000`）再动实现。
- 补 AST 级钉子 `test_ws_options_source_pins_the_heartbeat_interval_to_milliseconds`：
  上面那条靠 `importorskip("aibot")`，根 venv 按 design D10 不装 SDK ⇒ 恒 skip、**挡不住回退**；
  AST 钉子不依赖 SDK，钉死 `DEFAULT_HEARTBEAT_MS` 必须是字面量 `30_000` 且必须带 `_MS` 名，
  断言消息里写明**为什么是 30000 而不是 30**（否则下一个人只会觉得这个数很怪，顺手"修"回 30）。
- `reconnect_interval` 一并复核（同形状、未爆）：只更正 `session_client.py:22-25` 的注释——
  SDK 的封顶是**毫秒**口径且本模块**根本没传**该参数，「两层取同一个数」的说法作废。
  ⛔ **没有**顺手加传参（那会改变重连行为，超出本条范围）。

⚠️ **销账 ≠ 已验证**：根 venv 跑不到真 SDK，本条能验的到此为止。
**心跳是否真的变成 30 秒，需 `[Mac]0909AG` 真实建连确认**——在那之前，
「⛔ TD-38 未还前不得装 launchd」这条闸门按 Shao Peishen 答 `3a` **继续有效**。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-39~~ · 重连链被 `error` 事件抛出打断，连接断了就再也回不来 ✅ 已还（代码 `62d3594` -->

## ~~TD-39~~ · 重连链被 `error` 事件抛出打断，连接断了就再也回不来 ✅ 已还（代码 `62d3594`＋`9a35912`，真实断网复验 `0909AJ`）

**已于 `[Mac]0909AG` 复核完毕，⛔ 不是虚惊，⛔ 不许销账。** 实测见
`docs/findings/2026-09-09-断线重连实测.md`。

---

### 🛠 2026-09-09 `[Mac]0909AH` 处置：代码已改（`62d3594`），⏸ **本条暂不销账**

🔴 **⛔ 不许因为"代码改完了"就把本条划掉。** 销账 ≠ 已验证——`0909AF` 就是没区分
这两件事，才要 `0909AG` 回头补验。本条**唯一**的销账条件是**真实断网复验通过**：
真的拔网／关 Wi-Fi，看 ④⑤⑥ 三项从 ❌ 翻成 ✅。`0909AH` 在 worktree 里跑，
**没有 `.env`、没有真实库，做不了真实建连**，因此它 ⛔ 未验证"真的能重连回来"。

**改了什么**（两项都在 `tools/liaison/session_client.py`，接线在 `tools/liaison/__main__.py`）：

| 项 | 改法 | 落点 |
|---|---|---|
| 根因（上表第 4 步） | `_prepare_client` 补 `error` 事件监听器：记 **WARNING**、⛔ 不回抛。pyee 于是走「有监听器 ⇒ 分发不抛」的分支，`ws.py:153` 的 `_schedule_reconnect()` 能执行 | `EVENT_ERROR` / `_handle_sdk_error` |
| 同上·防复发 | 事件清单提成 `SUBSCRIBED_EVENTS`，接线**逐条走该清单**并纳入 `verify_client_surface` 校验——清单与接线曾是两处真源，那正是本条的缺口 | `SUBSCRIBED_EVENTS` |
| N-0（兜底） | 补 `liveness.json` `stamp_at` 看门狗，跑在**第三条线程**上；停更超 `STALE_LIVENESS_SECONDS` 就用 `LoopStopper` 跨线程 `call_soon_threadsafe(loop.stop)` 停掉 SDK 的事件循环，`client.run()` 才返回、外层 `run_forever` 才接得上手 | `run_liveness_watchdog` / `LoopStopper` |

**阈值取 180 秒**：实测判死时延 **55.75 秒**（心跳 30 秒 × 2 次未回 pong）的 3.2×。
⛔ 不许拍脑袋改小——设得不够大会把**正常的判死过程本身**误判成假死，看门狗于是
拆掉一条正在自愈的连接，"修复"反过来制造断线。

**已知且刻意接受**：真实长断网期间存活戳同样冻结，看门狗因此每 ~180 秒重建一次
连接（一小时约 17 次，远达不到企微限流量级）。⛔ 不要为它加"断网期间不看门"的
例外——"真断网"与"假死"从进程外部看**完全一样**，分辨得了就不需要看门狗了。

**测试覆盖**（对应本条修法第 3 点「必须有自动化测试覆盖」）：新增 24 条，
`tools/liaison` 783 → **807**，全量 2042 → **2066**，全部**先红后绿**。
主文件 `tools/liaison/tests/test_session_client_reconnect.py`（替身复刻 pyee 的
`error` 语义与 `ws.py:149-153` 的失败分支，⛔ 不碰网络、⛔ 不 import aibot、
⛔ 不用真实 sleep），接线断言在 `test_main_wiring.py`。
**变异复核**证明断言有判据力：摘掉 `error` 订阅 → 18 红；拆掉看门狗线程 → 1 红；
阈值改到 55.75 秒以下 → 1 红；去掉 `error` 监听器的两层防御 → 1 红
（那两层挡的是 `pyee/asyncio.py:78-81` 的「监听器抛异常 ⇒ 再 emit error」死循环）。

**⏸ 下一步（⛔ 本条未做）**：真实断网复验——**在主工作区**（worktree ❌，那里没有
`.env`）用真实凭据跑 `PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison`，
断网 → 等 ≥180 秒 → 复网，看 ④ 自动重连、⑤ `liveness` 翻回 `connected`、
⑥ 窗口闭合并发出恢复告警。三项都过 ⇒ 本条可销、G-4（launchd 装机）才谈解锁。

---

**原疑点的措辞需要修正**：不是"断线事件没到 `LiaisonSession`"——它**到了**。
`0909AE` 观察到的"16 秒仍是 `connected`"，只是因为判死时延本来就有 55.75 秒
（心跳 30 秒 × 2 个未回 pong），观察窗被人工强杀时链路还在途中。
**检测侧是好的，坏的是恢复侧。**

**缺陷是什么**：连接断开后 SDK 只重连一次；那一次失败时抛出的异常，
会把唯一还能触发重连的 task 杀死，于是**永不再试**。进程仍然活着、
`liveness.json` 永远停在 `disconnected`（`stamp_at` 也不再刷新）、
中断窗口永不闭合、恢复告警永不发出——**服务看起来在跑，其实早断了**。

**根因链**（SDK `wecom_aibot_python_sdk 1.0.2`，行号为 venv 内实际行号）：

1. `aibot/ws.py:149-153` `connect()` 失败分支：`self.on_error(e)`（152 行）**在**
   `await self._schedule_reconnect()`（153 行）**之前**；
2. `aibot/client.py:76` 把 `on_error` 接成 `lambda error: self.emit("error", error)`；
3. `pyee/base.py:178-184` `_emit_handle_potential_error`：`error` 事件**没有监听器时直接 `raise`**；
4. `tools/liaison/session_client.py:214-215` 只接了 `EVENT_CONNECTED` / `EVENT_DISCONNECTED`，
   **没有 `client.on("error", …)`** ⇒ 第 2 步必抛 ⇒ 第 152 行炸掉 ⇒ **第 153 行永远走不到**；
5. 异常冒泡出 `_receive_loop()`（`ws.py:208`），该 task 死亡
   （`Task exception was never retrieved`）。它是唯一还会调 `_schedule_reconnect` 的地方。

**为什么现在才暴露**：TD-38 未修时连接活不过 44 秒就被限流踢断，每次都走启动路径重建，
从没走到"运行中断线 → 重连失败"这条分支。**TD-38 修好是暴露本条的前提。**

**实测数据**（2026-09-09，断网 91.77 秒 + 复网观察 122.28 秒）：

| # | 判据 | 结论 |
|---|---|---|
| ① | 断线日志 | ✅ `connection closed` + `Reconnecting attempt 1` @ 22:03:24 |
| ② | `liveness` 翻 `disconnected` | ✅ 时延 55.75 秒（心跳周期决定，非缺陷） |
| ③ | 新开中断窗口 | ✅ 基线 1 → 2，`detected_by='disconnect_event'` |
| ④ | 复网自动重连成功 | ❌ 复网后 122.28 秒**零日志**；`Authentication successful` 全程仅 1 次（启动那次） |
| ⑤ | `liveness` 翻回 `connected` | ❌ 永远 `disconnected`，`stamp_at` 冻结 158 秒 |
| ⑥ | 窗口闭合 | ❌ `recovered_at`/`closed_by`/`alerted_at` 三列全空 |

**修法**（⛔ 本条只登记，`0909AG` 未改任何代码；修归另一条 opener）：

1. 在 `_prepare_client` 里补 `error` 事件监听器（记日志即可），让 pyee 走
   "有监听器 ⇒ 分发不抛"的分支，`ws.py:153` 的 `_schedule_reconnect()` 才能执行；
2. 补一条 `liveness.json` `stamp_at` 看门狗，让外层 `run_forever` 真的能兜底——见下方 N-0。
   ⛔ **不要改 `max_reconnect_attempts`**，它已经是 `-1`（原 N-1 已作废，见下）；
3. 🔴 **两项都必须有断线重连的自动化测试覆盖**，⛔ 不许只靠手工复跑一遍就算还上。
   本条守的正是无症状故障：没有测试钉住，它下次回归时同样不会有任何症状。

**🔴 2026-09-09 `[Mac]0909AG` 订正（本条初版写错了一项，⛔ 按订正后的做）**：

- **原 N-1 作废**：初版写「`max_reconnect_attempts=10` 会在 181 秒后永久放弃，应改 `-1`」。
  **查证后不成立**——`session_client.py:145` 的 `build_ws_options` **已经**传了
  `max_reconnect_attempts=UNLIMITED_RECONNECT_ATTEMPTS`（`= -1`，`session_client.py:41`）。
  `ws.py:341` 的放弃分支是 `self._max_reconnect_attempts != -1 and …`，`-1` 直接短路。
  ⛔ **不要去改这个值**，它本来就是对的。实测只重连 1 次的原因是 task 死了，**不是**次数用尽。
- **新增 N-0（比根因更该看的一条）· 外层兜底被同一个故障模式一并废掉**：
  `session_client.py:258-262` 的注释写「`client.run()` 几乎不会返回 …… 外层 `run_forever`
  是**兜底**那一层」。这条假设在本故障下**不成立**：`_receive_loop` task 死后，
  `loop.run_forever()` 照样挂着 ⇒ `client.run()` **仍然不返回** ⇒ 外层 `run_forever`
  **永远等不到那次返回**，兜底一次都不会触发。
  ⇒ **两层重连（SDK 内层 + 本模块外层）被同一个异常一并打掉**，这才是"永远回不来"的完整解释。
  ⇒ 只补 `error` 监听器能修好本次这条路径，但**兜底层依然是空的**：SDK 里任何别的
  "task 死了但 loop 还活着"的形态都会重演。**应同时补一条基于 `liveness.json`
  `stamp_at` 的看门狗**（盖戳超时 ⇒ 主动断开重建），让外层真的成为兜底。

**其余次生问题（同批修，⛔ 不要只修根因就销账）**：
- **N-2 · 首跳撞在断网期**：attempt 1 固定 1 秒后重试，必然落在断网窗口内，白费一次机会。
- **N-3 · `websockets` 次生崩栈**：`websockets/asyncio/client.py:741`
  `if 200 <= response.status_code < 300:` 在 `response is None` 时抛 `AttributeError`。
  第三方库缺陷，非主因，但污染日志。
  🔴 **2026-09-09 `0909AH` 明确：本条不修，仅记录。** ⛔ 不许去改 `site-packages` 里的
  `websockets`——那份改动不进版本管理、不会跟着任何一次部署走，下一次
  `pip install` 就没了，而"我明明修过"会让后来的人按一个不存在的修复去推理。
  要治只有两条路：升级 `websockets` 到修掉它的版本，或向上游提 issue。两条都另立一条。
- **N-4 · 本机走 HTTP 代理**：`scutil --proxy` 显示 `HTTPEnable/HTTPSEnable = 1`，
  断网时代理回 `HTTP 503`，异常类型是 `InvalidProxyStatus`。
  ⚠️ **根因与异常类型无关**（任何重连异常都走同一条路）。但 **.51 现网若不走代理，
  异常类型会不同**——复现时⛔ 不要按 `503` 去找。

**触发条件**：🔴 **8.6 灰度验收前必须还上**。值守通道断了不会自愈、也不会告警，
这正是 `make_sdk_connect` docstring 点名要消灭的那类故障。

**来源**：`[Mac]0909AE` 起疑、`[Mac]0909AG` 定性。相关：TD-37、TD-38、
`docs/findings/2026-09-09-断线重连实测.md`、`docs/findings/2026-09-09-首次真实建连实测.md`

---

### 🔴 真实断网复验（`[Mac]0909AJ`，2026-09-09 23:02–23:10 CST）· **本条据此销账**

`0909AH` 只在 worktree 里跑了单测（无 `.env`、无真实库，做不了真实建连），
「真的能重连回来」当时**从未验证过**。本节补的就是那半边：真断网、真复网、看它自己回不回得来。

**实验**：值守进程**全程不停**（⛔ 不是"停进程→断网→再起"，那测的是起不起得来），
脚本自动关/开 Wi-Fi（`en1`），**断网 245 秒**——刻意超过看门狗阈值
`STALE_LIVENESS_SECONDS = 180.0`。⚠️ `0909AG` 断的是 91.77 秒，**短于阈值**，
兜底层根本不会触发，⛔ 不要照抄那个数。前置：全量 807 passed / 4 skipped，
三条 grep 确认 `62d3594` 已在主工作区且看门狗**已接进 `main()`**。

**六项判据全过**（④⑤⑥ 从 `0909AG` 的 ❌ 翻绿）：

| # | 判据 | `0909AG` | `0909AJ` | 时延 |
|---|---|---|---|---|
| ① | `connection closed` / `Reconnecting` | ✅ | ✅ | 86.92 秒 |
| ② | `liveness` 翻 `disconnected` | ✅ 55.75s | ✅ | 86.92 秒 |
| ③ | 新开一条 `liaison_outage_window` | ✅ | ✅ | 86.92 秒 |
| ④ | 复网自动重连成功（再次 `Authenticated`） | ❌ | **✅** | 23.66 秒 |
| ⑤ | `liveness` 翻回 `connected` | ❌ | **✅** | 23.48 秒 |
| ⑥ | 窗口闭合（`recovered_at`＋`closed_by='reconnect'`） | ❌ | **✅** | 23.48 秒 |

①②③ 相对 `WIFI OFF`，④⑤⑥ 相对 `WIFI ON`。SDK 退避 1→2→4→8→16→30×5 共 10 次，
前 9 次在断网期报 `InvalidProxyStatus(503)`（即 N-4），第 10 次成功。
复网后 244 秒心跳全部有 ack，未再断。全文见
`docs/findings/2026-09-09-TD39真实断网复验.md`。

🔴 ~~**恢复是「改法 1」单独做到的 —— N-0 兜底层（看门狗）本次未被触发，仍只有单测覆盖。**~~
⚠️ **后半句已由 `[Mac]0909AQ`（2026-09-10 CST）订正为「N-0 兜底层已有端到端覆盖」**，
详见本条末「N-0 端到端覆盖」一节。前半句（本次恢复确由改法 1 单独做到）**仍然成立**。
⛔ 原句刻意保留不删——保留它才看得出这条是**分两次**验完的：`0909AJ` 验了改法 1 的
真实断网恢复，`0909AQ` 才补上 N-0 那半边；删掉原句就再也看不出中间存在过那道缺口。

判别证据：`"值守通道连接报错，已交给 SDK 重连"` 命中 **9 次**（改法 1 生效）；
`"存活戳已"` 的两条命中都在 **22:59:00**，即断网**之前** 3 分 17 秒，是启动瞬间的误触发
（见下），**断网窗口 23:02:17–23:06:22 内看门狗零日志**；
`"值守通道连接结束（第"` **零命中** ⇒ 外层 `run_forever` 从未接手。

*机理*：改法 1 生效后每次重连失败都触发 `error` 事件、刷新存活戳，而 SDK 退避上限是 30 秒
⇒ 存活戳最大间隔 30 秒 ⇒ **永远达不到 180 秒阈值**。两层是"改法 1 修好则看门狗静默"的关系，
不是并联触发。⛔ **不许把本次实测读成"两层都验过了"**——要真实触发看门狗需构造
"SDK 事件完全静默但 loop 还活着"的形态（`0909AG` 撞到的 task 已死场景），
**普通断网复现不出来**。N-0 若要端到端覆盖，另立一条 —— **已立：`[Mac]0909AQ`**
（opener `docs/openers/0909AQ-N0看门狗端到端覆盖.md`，Shao Peishen 2026-09-09 答 `2a` 授权，
✅ **2026-09-10 CST 已完成**）。⛔ 那条**不要再去断 Wi-Fi**：`0909AJ` 已证明普通断网触发不了看门狗，
必须在测试里构造「SDK 事件完全静默但 loop 还活着」的形态。
⚠️ **原拟 `0909AP`，撞号已改判为 `AQ`**（`[Mac]0909AZ` 2026-09-09 处置）：`AP` 早被
**第十六批·入站预演泳道**占用，`0909AJ` 取号时它还在 `OP-0820` 的未提交段里、grep 不到。
⛔ **`0909AP` 不是空洞、⛔ 不要重派给 N-0。**

**⚠️ 顺带发现 · 启动瞬间看门狗误触发 —— 🔴 Shao Peishen 2026-09-09 答 `3b` 明确「不修」**：
进程刚起、连接尚未建立时，看门狗读到**上一次运行残留的**存活戳（`0909AJ` 实测是 55 分钟前），
当场判陈旧并刷两条 ERROR（`存活戳已陈旧，但没有可停的事件循环把手` /
`兜底重建请求未能执行`），0.25 秒后连接其实正常 `Authenticated`。

⛔ **本条明确不修，⛔ 不要立 opener、⛔ 不要顺手改**（裁决日期 2026-09-09，
裁决人 Shao Peishen，依据＝无害：不影响功能，只是日志噪声）。
⚠️ **代价是已知并接受的**：每次冷启动都误报两条 ERROR，会稀释真故障时**同样文案**告警的
注意力——`launchd` 装机后 `KeepAlive` 每次拉起都会刷一遍。**看 `launchd.err.log` 时，
开头那两条 ERROR 是噪声，⛔ 不要按真故障去查。**

若将来改主意，修法方向：看门狗启动时先等第一次成功盖戳，或忽略早于本进程启动时刻的存活戳。

### ✅ N-0 端到端覆盖（`[Mac]0909AQ`，2026-09-10 CST）· **本条不改销账状态，只补覆盖**

`0909AJ` 之后 N-0 兜底层只有单测——验的是 `run_liveness_watchdog` **这个函数自己**的判定
逻辑；**接线那一段**（看门狗触发 → 停事件循环 → `client.run()` 返回 → 外层 `run_forever`
接手重建 → 窗口闭合、存活戳恢复）从未被端到端跑过。`0909AQ` 补的就是这一段，
**只加测试、⛔ 未改任何产品代码**（`session_client.py` / `__main__.py` 一字节未动）。

落点：`tools/liaison/tests/test_session_client_reconnect.py` 文末「N-0 端到端」一节，三条：

| # | 钉住的事 | 先红造法（实测报错原文） |
|---|---|---|
| E-1 | 存活戳陈旧 ⇒ 看门狗**真的**停掉事件循环、`client.run()` **真的**返回 | 摘掉 `__main__.py` 的 `threading.Thread(target=watchdog, …).start()` ⇒ `AssertionError: 事件循环是被本用例的兜底安全网停的，⛔ 不是看门狗——N-0 兜底层没有生效` / `assert [0, 1] == []`。按 opener 字面造法（摘 `main()` 的 `watchdog=` 形参）则为 `TypeError: main() got an unexpected keyword argument 'watchdog'` |
| E-2 | `run()` 返回后外层 `run_forever` **真的重建**了一次连接，且是**全新对象** | 把 `run_forever` 的重建分支改成不重建 ⇒ `AssertionError: 外层 run_forever 没有重建连接（只造了 1 个连接对象）` / `assert 1 >= 2` |
| E-3 | 重建后中断窗口闭合（`closed_by='reconnect'`）、存活戳翻回 `connected` 且盖在重建时刻 | 同 E-2 造法 ⇒ `AssertionError: 断线开出的中断窗口没有被重建后的连接闭合：[]` / `assert 0 == 1`；摘看门狗接线则 ⇒ `AssertionError: 恢复必须由看门狗驱动，⛔ 不是兜底安全网` |

**怎么构造出「SDK 事件完全静默但 loop 还活着」**（`0909AJ` 已证明⛔ 断网造不出来）：
假 SDK 的 `run()` 逐行复刻 `aibot/client.py:345-362`——`new_event_loop()` →
`run_until_complete(握手)`（在 loop **里面**触发 `connected`／`disconnected`，这是
`LoopStopper.capture()` 唯一抓得到那个 loop 的时机）→ 此后**再不触发任何事件** →
`loop.run_forever()` 一直挂着。

三条口径上的要点，⛔ 改测试前先读：
1. **阈值 `STALE_LIVENESS_SECONDS = 180.0` 用的是产品默认值、⛔ 没被调小。** 跨过它靠
   **假时钟往前跳**（`_AdvanceableClock`），⛔ 不靠真 `sleep`，三条合计 0.15 秒跑完。
   压小的只有看门狗轮询间隔（15 秒 → 10 毫秒），理由是 `main()` 传给它的 `sleep` 是真的
   `stop_event.wait`。
2. **假 SDK 带一个 5 秒兜底安全网，每条用例都断言它 ⛔ 没响过。** 一个 loop 只有两个人能
   停：看门狗与这张网 ⇒「`run()` 返回了 ⋀ 安全网没响」＝看门狗真的干了活。
   ⛔ 不许删掉那条断言——删了之后，看门狗缺席时本节会变成**挂死然后照样绿**。
3. **「启动瞬间看门狗误报」那条（Shao Peishen 答 `3b` 明确不修）在本节被绕开、⛔ 未顺手修**：
   假时钟让第一次检查落在「存活戳刚盖下」的时刻，因此不会撞上它。

**全量计数**：本条前 894 passed / 5 skipped → 本条后 **897 passed / 5 skipped**（+3，只增不减）。
⚠️ 该计数是在 **Linux ＋ Python 3.13 的 CC 云端 session** 里跑的，与 Mac 本机的基线数字
（`0909AJ` 记的 807）不可直接相减——期间第十六批等泳道各自加过测试。同一环境下
`test_launchd_plist.py::test_real_install_refuses_when_the_venv_python_is_missing` 恒红，
原因是 `install_launchd` 在非 macOS 上直接拒绝（`✗ 本脚本只在 macOS 上有意义（launchd）`），
**与本条无关、Mac 上不会红**，⛔ 不要当成回归去查。

**8.6 的阻断解除**：TD-39 已还 ⇒ 值守通道断了会自愈、会告警。
**G-4（launchd 装机）的解锁条件「TD-39 还上并有自动化测试覆盖」已满足**
（自动化覆盖 = `tools/liaison/tests/test_session_client_reconnect.py`），
⛔ 但装机本身是 Shao Peishen 本人在 Terminal 跑的**不可代项**，`0909AJ` 未装、也不许代装。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-40~~ · `.env` 里的 `HR_LIAISON_*` 三个键让整个 app 的配置加载不了 ✅ 已还 -->

## ~~TD-40~~ · `.env` 里的 `HR_LIAISON_*` 三个键让整个 app 的配置加载不了 ✅ 已还（`0909AC`，改法 ③）

**欠的是什么**：`app/config.py:13` 是
`SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")`——pydantic-settings v2 的
`BaseSettings` **默认 `extra="forbid"`**。`0909AA`（2026-09-09）把
`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / `HR_LIAISON_GROUP_WEBHOOK` 三个键写进仓库根
`.env` 之后，`Settings()` 每次实例化都抛
`ValidationError: Extra inputs are not permitted`，一次报三条。

**这不是"测试环境的小毛病"**：`Settings` 是 app 的配置入口 ⇒ **Web 服务在他本机起不来**。
`0909AC` 实测：有 `.env` 的仓库根 checkout 上根 venv 全量 **23 failed**
（`test_config.py` 5、`test_config_fallback.py` 6、`test_config_audit_and_outbound.py` 10、
`test_outbound_gate.py` 1、`tests/test_main_wiring.py` 1）；同一份代码在**没有 `.env`** 的
worktree 里 **0 failed**。

⚠️ **失败信息会把 bot_secret 明文打进 pytest 输出**（`input_value='...'`）——终端回滚、
CI 日志、粘给别人看的报错里都带着它。这条本身就构成一个泄漏面。

**为什么一直没被发现**：`.51` 上没有这三个键（`tools/` 不同步过去），CI 也不带 `.env`。
它**只在他这台机器上**发生，而那正是唯一会跑 Web 服务的机器。

**触发条件**：⚠️ **立刻**——他下一次起 Web 服务或跑全量 pytest 就会撞上，现在已经在撞了。

**三个改法（未裁决，Shao Peishen 拍）**：
① `app/config.py` 加 `extra="ignore"`——一行，但**放弃了「`.env` 里写错别字当场报错」这道岗**；
② 给 `Settings` 补三个 `hr_liaison_*` 字段——app 并不用它们，纯为让校验过关，语义上是脏的；
③ 把值守通道的凭据从根 `.env` 挪到 `tools/liaison/.env`（配合
`__main__.resolve_dotenv_path()` 改默认路径）——**与 design D10 的依赖隔离同构**，
两套配置各归各位，代价是他要挪一次文件。

**不还的后果**：Web 服务起不来，且报错指向的是 `.env` 里那三个**本来就该在那儿**的键，
很容易被读成"配置写错了"而去删凭据——删完值守通道又起不来。两边互相打架，
且**每次报错都回显一次 bot_secret**。

**来源**：`[Mac]0909AC` 收工前在仓库根 checkout 跑全量时实测（⛔ 不是推测，见上方失败计数）。
相关：TD-19、design D10

### ✅ 2026-09-09 已还——**裁决＝改法 ③**（Shao Peishen 当次答 `1c`）

值守通道的配置迁到 `tools/liaison/.env`，与 design D10 的依赖隔离同构：依赖在
`tools/liaison/requirements.txt`，配置在 `tools/liaison/.env`，两套各归各位，
`tools/` ⛔ 不进 `sync-to-server.sh` 的 SYNC_PATHS，都不会上 .51。

⛔ **没有选改法 ①（`extra="ignore"`）**：那会连带放弃「根 `.env` 里写错别字当场报错」
这道岗——而那道岗挡的是"配置看起来配了、其实键名拼错了"这类**无症状**故障，
比本条更难发现。

落地清单：

- `__main__.DEFAULT_DOTENV_PATH = LIAISON_DIR / ".env"`，`resolve_dotenv_path()` 据此取值。
  ⛔ **刻意不做"根 `.env` 兜底"**——兜底会让"键还留在根 `.env` 里"这个坏状态继续静默存在。
- 占位从根 `.env.example` **整段迁到** `tools/liaison/.env.example`。⚠️ 连**空占位**都迁走了：
  `extra="forbid"` 判的是键**在不在**，不是值空不空，而 `cp .env.example .env` 是最常见的
  触发路径。
- 缺凭据时 stderr 多打一行「已从 <path> 读取」——迁移之后"我明明配了啊"最可能的原因
  就是文件还在旧位置，⛔ 只打路径不打取值。
- README、plist 模板注释、`errors.py` 的提示文案三处指向同步更新。

两道回归岗：
`test_default_dotenv_path_is_the_liaison_dir_not_the_repo_root`（默认路径被改回仓库根即红）、
`test_root_env_example_does_not_carry_the_liaison_keys`（三个键回到根 `.env.example` 即红）。

**验收实证**：迁移前根 venv 全量 **23 failed**；迁移后 **2041 passed / 5 skipped / 0 failed**，
`Settings()` 正常实例化。liaison venv **786 passed / 0 failed**。
凭据本身已从根 `.env` 移入 `tools/liaison/.env`（权限 0600），⛔ 未留备份副本。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-42~~ · 连接假死：看门狗有判据无把手，`liveness=connected` 会骗人 ✅ 已还（`20 -->

## ~~TD-42~~ · 连接假死：看门狗有判据无把手，`liveness=connected` 会骗人 ✅ 已还（`20a2848`），✅ 真实验证 2026-09-16 14:27 CST

**登记**：2026-09-10 09:4x（Cowork·0909Q 实测发现）
**位置**：`tools/liaison/session_client.py` 的存活戳看门狗（`0909AH` 加的 N-0 兜底层）
**级别**：🔴 **阻断 8.6**——它让「服务在线」这个判据失效

**现象（实测，非推测）**：2026-09-09 23:27 到 2026-09-10 09:38 整整 **10 小时**，
进程活着、`liveness.json` 的 `state=connected`、`stamp_at` 持续推进，**实际一条消息都没收到**：
`liaison_message` 0 行、`liaison_task` 0 行、归档目录空、日志十小时零新行。
期间汤丽萍在群里 @ 过机器人，落空。

**根因（日志逐字自证）**：
```
存活戳已 180 秒以上没有刷新（末次 …23:11:30），判定为「事件循环还活着但连接假死」
存活戳已陈旧，但没有可停的事件循环把手（本次连接从未触发过任何 SDK 事件）。⛔ 本轮兜底…
兜底重建请求未能执行——连接可能仍卡在假死状态，且外层 run_forever 接不上手
```
看门狗**探到了**假死、**也报了警**，但「本次连接从未触发过任何 SDK 事件」⇒ 拿不到事件循环引用 ⇒
**有判据、无把手**，停不下来；`run_forever` 那层同样接不上手。⇒ 两层都在，都动不了。

⚠️ **与 TD-39 的关系**：TD-39（`error` 事件打断重连链）**已修好且已验证**——2026-09-09 23:03 那次断线
3 分 1 秒自愈就是证据。本条是**另一条路径**：连接建立后静默假死、从未触发任何 SDK 事件。
⛔ 不要把它当成 TD-39 没修好。

**不还的后果**：比昨天「服务起不来」阴得多——服务**看起来完全正常**，`liveness=connected`、
存活戳在刷、launchd 显示常驻。**无症状**，只有去数库里的行数才发现十小时没进过东西。
8.6 灰度若带着它上线，专员发的每一条都会静默落空，而所有仪表都显示绿灯。

**还债方向（两条都要，缺一不可）**：
1. **给一条不依赖 SDK 事件的兜底停机路径**——看门狗判定假死后必须**总能**让进程终止，
   由 launchd `KeepAlive` 拉起重连。⛔ 不许依赖「拿到事件循环把手」这个前提，那正是失效的地方
2. **修判据**：`liveness` 增加「距上次**真实 SDK 事件**的时间」，看门狗与一切外部判据都改看它。
   🔴 `state=connected` ＋ `stamp_at` 在推进 **⛔ 不足以证明在收消息**——本条就是反例

**🔴 复现实测（2026-09-10 10:05，决定性证据）**：09:38:26 `pkill` 重启、3 秒内重连成功。
10:0x Shao Peishen **本人**（`ShaoPeiShen`，在白名单内）在「人力AI保障组」@ 机器人发了一条。
10:05 核：`liaison_message` **仍为 0**、归档目录空、日志自 09:38 那条重启告警后**再无新行**；
而 `liveness` 仍是 `connected`、`stamp_at` 10:05:05 刚刷过、`since` 09:38:26 稳定 27 分钟。
⚠️ 同期 `effect_log` **12 → 15**——进程确实在干活（在写存活戳），**但入站链路是死的**。

⇒ 三条结论：
1. **重启后 27 分钟内即再次假死，可复现，⛔ 不是偶发**
2. 本次与昨夜那十小时的区别是**已知有一条消息真的发出且没到**——⛔ 不能再用「也许只是没人发」解释
3. 🔴 **判据彻底失效的证明**：`state=connected` ＋ `stamp_at` 在推进 ＋ `effect_log` 在增长，
   三个指标全绿，通道一条收不到。**⛔ 今后任何「服务是否在收」的判断都不得只看这三样**，
   唯一可信的即时判据是「让白名单内的人发一条，看 `liaison_message` 是否 +1」

**触发条件**：🔴 **立即**——8.6 灰度在它还上之前一步都不能往前走；
⛔ 在它还上并真实验证通过之前，不得再请任何专员发消息（截至 2026-09-10 汤丽萍已白发 4 条）。

**已还**（2026-09-10 `[Mac]0909AS`，worktree，commit `20a2848`；测试先红后绿，变异复核拆掉终止路径两条同红）：

1. **不依赖 SDK 事件的停机路径**（`session_client.LivenessWatchdog`）：判定假死后先走 TD-39 的停 loop
   （⛔ 从「每 15 秒停一次」改为**一段假死只请求一次**）；没有把手、或停了之后宽限期 180 秒仍假死 ⇒
   ERROR ＋ 中断告警（`alerts.effect_emit_alert`，`LoggingAlertSink`）＋ 台账 `data/liaison/watchdog.json`
   ⇒ `os._exit(5)`，launchd `KeepAlive` 拉起。**⛔ 不许无限自杀循环**的处置＝退避：连续「起来就假死」
   （活不满 30 分钟又被终止）满 **N=3** 次起，判死后等一个**翻倍**的宽限期再终止（180→360→…封顶 1 小时），
   每次 ERROR 与告警都带「连续第 k 次」，k≥3 附「请人工介入」。循环 ⛔ 不停——停了就是把假死留在原地。
2. **修判据**：`liveness.json` 增 `last_event_at`＝SDK 最近一次真的送到东西的时刻（`connected`/`disconnected`/
   `authenticated` 事件，＋ SDK 日志里的入站证据 `Received heartbeat ack` / `Authentication successful` /
   `Received push message` / `Received event callback`，经 `WSClientOptions.logger` 公开注入点由 `SdkLogObserver`
   观察，启动时 `verify_sdk_activity_markers` 核对文案仍在 SDK 源码里、不在则拒绝启动 exit 4）。看门狗改读
   整份存活戳：`connected` 且 **600 秒**无 SDK 活动即假死（20 个心跳周期）；旧格式（无该键）判
   `unknown_format`＝需要关注、⛔ 不当健康、也不据此拆连接；早于本进程启动的残留戳 ⛔ 不算证据
   （`0909AJ` 附带发现的冷启动误报——终止路径接上后它会变成每次启动都自杀，故从「刻意不修」升级为必修）。
   人肉判据：`cat data/liaison/liveness.json`，`stamp_at` 在推进而 `last_event_at` 停在几小时前 ＝ 连着但收不到。

⚠️ **本条的实测证据（`liaison_message` 0 行）与「SDK `message` 事件未接线」不可区分**：
`handle_inbound_message` 当前零生产调用方、`test_this_chapter_wires_no_message_handling` 明令 `__main__` 不接
（`liaison-reply-bridge-and-patrol/proposal.md` 已登记为前置）。⇒ 即便连接完全健康，`liaison_message` 也恒为 0。
还上本条之后，`last_event_at` 每 30 秒随心跳回包推进 ⇒ 若它在推进而消息仍不落库，病根就是接线而不是假死。

⏸ **真实验证留步（Shao Peishen 本人做，⛔ 单测全绿不算验收）**：重启服务后 ① `cat data/liaison/liveness.json`
看 `last_event_at` 是否每 ~30 秒推进；② 断网／静默 ≥ 13 分钟（600 秒活动阈值 ＋ 180 秒宽限），确认日志出现
「看门狗终止进程」、进程自行退出并被 launchd 拉起、`data/liaison/watchdog.json` 计数 +1。

**✅ 真实验证已跑完（2026-09-16 14:13–14:27 CST，`[Mac]0910A` §四，全自动断网脚本，Shao Peishen 在场）**：
`en1` 断网 9 分钟（14:13:30–14:22:35）＋ 复网观察 3 分钟，六项判读全过：
① 判死——`14:17:34` 存活戳 185 秒未刷新（阈值 180 秒）；
② 看门狗判定——同一行 WARNING，先请求停掉事件循环让外层重连接手；
③ 终止——`14:20:35` 看门狗终止进程（退出码 5，交给 launchd `KeepAlive` 拉起），存活戳已 366 秒未刷新、
本进程已运行 1927 秒、连续第 1 次；
④ 告警行——`【HR 值守通道·连接假死】` 同时刻打出，文案与③一致；
⑤ pid 变化——`41272` → `44355`（`launchctl print` 与日志双重确认）；
⑥ 复网自愈——`14:23:06` 起新连接 `since`，`14:25:xx` 起稳定 `state=connected` 且 `last_event_at` 持续推进。
`data/liaison/watchdog.json`：`{"consecutive": 1, "reason": "stale_stamp", "last_terminated_at": "2026-09-16T14:20:35...", "process_lifetime_seconds": 1927}`。
脚本 `trap`／900 秒独立兜底均未触发（正常收尾，`RESTORE-TRAP` 只在脚本自然退出时打了一次）。

---


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-43~~ · `message` 帧的字段映射未经真实帧确认，现为 fail-closed（入站消息一条都不落 -->

## ~~TD-43~~ · `message` 帧的字段映射未经真实帧确认，现为 fail-closed（入站消息一条都不落库）✅ 已还（本次 commit，`[Mac]0910A` AT-1b）

**登记**：2026-09-10（`[Mac]0910B`，tasks 8.5bis ⑤ 的 fail-closed 支）
**位置**：`tools/liaison/frames.py` 的 `FIELD_PATHS`
**级别**：🔴 **阻断 8.6**——接线已通，但没有这张表，入站链路仍然一条都落不了库

**已还（2026-09-16，`[Mac]0910A` 轮 1）**：真实帧结构由群 @ 一条 ＋ 私信一条两条真实企微
消息确认（值守线程 fail-closed 诊断行，只有键名与类型）。`msgid`=`body.msgid`、
发送人=`body.from.userid`、正文=`body.text.content`（三项固定路径，进 `FIELD_PATHS`）；
`thread_id` 按 `chattype` 分叉（design.md D6：私聊取 `userid`、群聊取 `chatid`，私聊帧
**没有 `chatid` 这个键**，实测证实），单一固定路径表达不了，单独放
`THREAD_ID_PATHS_BY_CHATTYPE`、`compute_inbound_frame` 按 `chattype` 分支取值，
不识别的 `chattype` 仍 fail-closed。`test_production_field_paths_are_still_unverified_so_mapping_refuses`
已按预案改写成两条正向断言（群/私聊各一条）＋两条 fail-closed 回归（群消息缺 `chatid`／
`chattype` 不识别）。⚠️ **顺带订正一处旧记录**：下面「为什么确认不了」一节说"win 端
入站解析…不存在"是错的——`5-平台底座/wecom-aibot-service/aibot_service/frame_parsing.py`
`intake.py` 就是入站解析，`2026-09-16` 已用它核对字段路径（`sender`/`chatid` 路径与本条
实测完全一致），仅供当年判断留痕、⛔ 不要再引用那句话当现状。
**未还、有意搁置**：附件句柄（下方"一并欠着的"那段）——本次仍是 `attachment=None`，
归 TD-22，与 8.6 灰度私信验证顺序耦合，⛔ 不属本次范围。

**观察记录（不升级为独立 TD，纯记录）**：轮 2（Shao Peishen 私信「测试短信3」同一时刻，
2026-09-16 13:55 前后）另发一条群 @ 消息（同一"人力AI保障组"、同一 MAC机器人），
私信在 9 秒内正确落库（见上），**群消息 10+ 分钟未落库、日志无任何痕迹（不是
fail-closed 报错，是完全没收到）**。连接全程 `state=connected`，`last_event_at`
持续推进，非本机/本服务侧故障迹象。同一 `chattype=group` 的消息在轮 1（本条修复
之前）曾经成功送达并触发 fail-closed（帧结构已用于本条填表），说明群消息链路本身
**是通的**，本次未达更像是企微群消息回调侧的偶发延迟/丢失，不是本项目代码缺陷。
⇒ `THREAD_ID_PATHS_BY_CHATTYPE["group"]`（取 `chatid`）目前只有**单元测试**覆盖，
没有一条**真实**群消息走完整链路落库验证过。下次任何人自然发生一条群消息成功落库时，
顺手在 `liaison_message` 里核一下 `thread_id` 是否等于 `chatid`（⛔ 不等于 `sender_userid`），
不必为此单独起一轮验证。

**欠的是什么**：`tools/liaison/frames.py` 的 `FIELD_PATHS` **是空的**。SDK 的 `message`
事件已经订上了（8.5bis，`SUBSCRIBED_EVENTS` 含 `message`），回调、队列、值守线程消费、
幂等断言全部就位，但「`msgid` / 发送人 userid / 会话 id / 正文 / 附件句柄**落在帧的哪个
键上**」至今**没有任何真实帧依据** ⇒ `compute_inbound_frame` 一律抛
`InboundFrameUnverifiedError`，本帧**不落库**，只把帧的**键结构**（⛔ 无取值）打进 ERROR 日志。

**为什么不先猜一版**（这条是刻意的，⛔ 不是没做完）：`thread_id` 与 `msgid` 是归档路径与
幂等键 `{thread_id}:{node_name}:{business_key}` 的组成部分。猜错的后果不是报错，是
**归档互相覆盖**或**同一条消息永远重复入队**，而台账里一切正常——与工程铁律 1 要消灭的
失效形态同族。⇒ 按 TD-19 同一处置：表面未验就不许跑。

**为什么确认不了**（2026-09-10 实测，钉死版本 `wecom-aibot-python-sdk==1.0.2` 包内源码）：
SDK 只命名了两个字段——`body.msgtype`（`aibot/message_handler.py:34-38`、`types.py:98-114`）
与 `headers.req_id`（`client.py:130-132`）；`body` 的类型就是 `Any`（`types.py:166`
`WsFrame = Dict[str, Any]`），其余字段 SDK 原样透传、从不读也从不命名。企微官方文档不算
依据（8.5bis ⑤ 逐字：「⛔ 不照文档猜」）。win 端 `wecom-aibot-service` 只用它**主动发**
（`aibot_service/dispatch.py` / `delivery.py` 全是出站），入站解析在那边也不存在。

**触发条件（怎么还）**：**AT-1b** —— `[Mac]0910A` 在主工作区起真实服务、由 Shao Peishen
本人私信一次机器人；日志里那行「帧结构（只有键名与类型）」就是答案，照它把 `FIELD_PATHS`
四个键填上（⛔ 只改这张表，别处不动），再把附件句柄那一段接通（见下）。填完后
`tools/liaison/tests/test_inbound_wiring.py` 里的
`test_production_field_paths_are_still_unverified_so_mapping_refuses` 会转红——**那是预期的**，
把它改成"真实映射能取到四个字段"的正向断言即可，⛔ 不许删掉不换。

**一并欠着的**：`handle_message_frame` 传 `attachment=None`。帧里的附件是**句柄**，变成字节
要再走一次 SDK 下载（一次网络调用），且句柄落在哪个键同样未验。⇒ 与本条同一时点收口。
⚠️ 与 8.6 灰度的顺序耦合：**文档只能走私信**（`docs/findings/2026-09-09-win端aibot收发实证-
对8.6灰度的三条影响.md` §二），群里 @ 机器人核不出附件链路，也核不出 TD-22 的 `msgid` 字符集。

**不还的后果**：服务连得上、订得到、日志干净、**`liaison_message` 仍然恒为 0 行**——与
TD-42「连接假死」的症状**看起来一样**。⚠️ 但两者现在可分辨了：假死时日志**一条 ERROR 都
没有**，本条则每来一条消息就打一行「帧结构」。⇒ 日志里有帧结构 = 连接是好的、只差这张表。


<!-- 〔归档工具〕 2026-09-16 搬入：~~TD-44~~ · `SdkLogObserver` 无 `delegate`，SDK 与本模块的 ERROR/WA -->

## ~~TD-44~~ · `SdkLogObserver` 无 `delegate`，SDK 与本模块的 ERROR/WARN 全部漏进未脱敏、无轮转的 `launchd.err.log` ✅ 已还（本次 commit，级别：合规相关）

**登记**：2026-09-16（`[Mac]0910A` AT-1b 轮 1 实跑时发现）

**现象**：`[Mac]0910A` 轮 1 请 Shao Peishen 群 @ 一条、私信一条真实消息后，
`data/liaison/logs/liaison.log`（`logsetup.py` 管理的、有轮转与脱敏的结构化日志）
**mtime 停在重启那一刻，之后再没写过一行**——两条消息的 fail-closed 诊断行
（`handle_message_frame` 里 `logger.error("入站帧未落库…")`，即 TD-43 描述的那一行）
一条都没进去。但 `data/liaison/logs/launchd.err.log`（launchd 直接重定向的**原始 stderr**，
**无轮转、无脱敏、无容量上界**，登记时已 **2.4 MB**）里两条都在，且带着 SDK 自己
`Received push message` 的完整回显——**消息原文明文可见**：
`"text": {"content": "@MAC机器人 测试短信2。"}` 与 `"content": "测试私信1！"`。

**根因**：`tools/liaison/__main__.py` 构造 `session_client.SdkLogObserver` 时
（`main()` 内，紧邻 `on_message` 定义处）**没有传 `delegate`**：

```python
sdk_logger = session_client.SdkLogObserver(
    on_activity=on_sdk_activity, on_any_log=loop_stopper.capture
)
```

`SdkLogObserver._forward()`（`session_client.py:1143-1151`）：`delegate is None` 时
**直接 `print(..., file=sys.stderr)`**，SDK 的 debug/info/warn/error 全走这条路，
**完全绕过 `logsetup.setup_logging()` 挂的 `RotatingFileHandler` 与脱敏 filter**。
`handle_message_frame` 里那行 `logger.error(...)` 本身用的是标准 `logging` 模块
（`logging.getLogger(__name__)`），理论上该走 `liaison.log`——但实测它也落进了
`launchd.err.log`，需要下一手查清是 `setup_logging()` 在这条真实运行路径上没被正确
调用/生效，还是别的传播（propagation）问题；`liaison.log` 27 行、`launchd.err.log`
2.4 MB 且持续增长是硬证据，⛔ 单测大概率覆盖不到（要跑真实 `main()` 才会暴露）。

**为什么是合规相关，不只是"日志放错文件"**：
1. `launchd.err.log` **无轮转、无容量上界、无 `HR_LIAISON_LOG_RETENTION_DAYS` 清理**——
   `logsetup.py` 模块 docstring 整段讲的"脱敏必须挂在 handler 上"防线，对这条路径
   **完全不设防**。
2. SDK 自带的 `Received push message` 调试回显**把消息正文原样打了出来**——这不是本项目
   代码主动记录的（`handle_message_frame` 自己只打「帧结构，无取值」），是 SDK 库自己的
   `DEBUG` 级日志经这条未脱敏管道流出去的。
3. 登记时 `launchd.err.log` 已 2.4 MB——**服务从何时开始产生这个文件、之前是否已经
   积累了别的真实消息内容，未查**（本条只查了本次两条消息的落点，没有逐行核旧内容）。

**不还的后果**：只要值守服务在跑，**每一条真实企微消息的原文都会以明文落进一个
不受管理、不会被清理、当前已 2.4 MB 且还在长的本地文件**——与「模型全部走境内、
简历数据不出境」同类但更基础的一条：这条连"境内"都谈不上，是**本地磁盘上就已经
不设防**。

**还债动作**：
1. ✅ **已还**：`SdkLogObserver` 构造时补 `delegate=logging.getLogger(f"{logsetup.PACKAGE_LOGGER_NAME}.sdk")`，
   SDK 日志重新流回 `logsetup.py` 管的 handler（带轮转与脱敏）。
2. ✅ **根因已查清，非猜测**：`handle_message_frame` 的 `logger.error` 走的是
   `tools/liaison/__main__.py:48` 的模块级 `logger = logging.getLogger(__name__)`。
   **`python -m tools.liaison` 是本模块的真实启动方式**，此时 Python 把
   `__name__` 绑成字面量 `"__main__"`（与「被 import 时解析成
   `tools.liaison.__main__`」是两回事）——`"__main__"` 与包 logger
   `"tools.liaison"` 毫无父子关系，`propagate` 链走到 root，root 无 handler
   ⇒ 触发 `logging.lastResort`（无格式、无脱敏、直接 print 到 stderr）。
   与 `setup_logging()` 本身的调用时机／`propagate` 设置／launchd plist 无关。
   已改成字面量 `logging.getLogger(f"{logsetup.PACKAGE_LOGGER_NAME}.__main__")`，
   并补两条 AST 回归测试钉死「⛔ 不许再写 `getLogger(__name__)`」——**这正是
   本条缺陷能活到 0910A 真实消息实跑才被发现的原因**：单测里 `import` 该模块
   会"看着正确"地解析出包内名字，只有真走 `-m` 入口才会露出问题，常规单元测试
   测不出来。
3. **`launchd.err.log` 现存的 2.4 MB 内容如何处置**——含真实消息原文，是否需要
   连同这条修复一起清理／归档／限定访问，**这一步涉及已经产生的真实个人信息，
   不由本条代办，需 Shao Peishen 本人定**。

**处置裁定**（Shao Peishen 2026-09-16 答 `1b`）：先不动这份文件，**登记详情，交下一个任务去清理/归档**，
⛔ 本条／`[Mac]0910A` 都不执行清理。**登记的详情**：路径 `data/liaison/logs/launchd.err.log`，
登记时体积 **2,481,263 字节（2.4 MB）**、快照时间 **2026-09-16T04:35:25Z**（`TZ=Asia/Shanghai` 即
12:35:25 CST）。⚠️ **体积仅覆盖服务本次重启（`[Mac]0910A` §二 `kickstart -k`，11:42:53 CST）之后**——
该文件本身可能在此之前已存在更久、积累了更早的真实内容，**未逐行核实**，下一手接手时应先查文件
创建时间／launchd 是否曾经 rotate 过，⛔ 不要假设"2.4 MB 都是今天产生的"。
清理动作本身仍按"合规相关"处置——含真实个人信息的文件，删除/归档前建议先确认是否需要留痕
（例如按 §PIPL 说明权的思路，至少记一条"何时因何清理"），而不是直接 `rm`。


<!-- 〔归档工具〕 2026-09-17 搬入：~~TD-29~~ 第 6 章包边角：再导出面不对称、死代码、测试脚手架三处复制粘贴、异常文案错位 ✅ 已还（`0916 -->

## ~~TD-29~~ 第 6 章包边角：再导出面不对称、死代码、测试脚手架三处复制粘贴、异常文案错位 ✅ 已还（`0916E`）

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**级别**：不阻塞第 6 章，全部是可读性/可维护性

四条一起登记（都很小，建议一次还完）：
1. **`tools/liaison/notify/__init__.py` 再导出面不对称且不完整**：`STATE_*` 导出而 `MODE_*` 不导出；
   `transport.py` 一个名字都没导出（调用方从包根拿不到 `WebhookTransportError`）；
   `NotifyRecord` / `effect_send_group_notify` / `GROUP_NOTIFY_THREAD_ID` 缺席；
   `AIBOT_CHANNEL` 导出但生产代码零消费者。
2. **`tools/liaison/notify/guard.py` `NotifyPlan.is_send` 是死代码**（全仓零引用，含测试）。
3. **6 个子代理各造一份测试脚手架**：`FakeClock`（`test_notify_ratelimit.py` / `test_notify_webhook.py`）、
   `RecordingSink`（`test_notify_store.py` / `test_notify_webhook.py`）、`FAKE_WEBHOOK`
   （`test_notify_transport.py` / `test_notify_webhook.py`）三处复制粘贴，建议收进 `tests/conftest.py`。
   另 `test_notify_store.py` 有两条写库用例末尾漏调 `assert_group_notify_identity(conn)`
   （`test_effect_key_is_thread_node_digest` / `test_pending_resends_are_listable`）——
   其余七条都调了，覆盖不缺口，但本章自带判据是唯一防线，建议补齐。
4. **`tools/liaison/config.py:75` 复用 `MissingCredentialsError` 带来文案错位**：该异常消息逐字是
   「HR 值守通道**拒绝启动**：…」，而 `load_group_webhook` 的 docstring 正好在论证它
   **不是**启动期检查。运维在服务正常运行、只是群通知发不出去时，会收到一句说服务拒绝启动的告警。

**核实结论**（2026-09-09，[Mac]0909AL）：本条**此前未处置**。派发时以为它已在 `0909T` 处置，
是**读串了行**——那句「2026-09-09 已处置（`0909T`）」属于下面的 TD-30（日志留存期），
⛔ 不属于本条。四条逐条核过代码：`MODE_*`/transport 确实一个都没导出、`is_send` 确实还在且
全仓零引用、两条漏调的 `assert_group_notify_identity` 确实还漏着、`config.py:75` 确实还在
复用 `MissingCredentialsError`。

**已还三条**（`5ca1f50`）：
1. ✅ `notify/__init__.py` 再导出面补齐并**对称**：补 `MODE_DIRECT/DEGRADED/REJECT`、
   transport 的五个名字（`Transport` / `UrllibTransport` / `WebhookResponse` /
   `WebhookTransportError` / `WEBHOOK_TIMEOUT_SECONDS`）、`NotifyRecord` /
   `effect_send_group_notify` / `GROUP_NOTIFY_THREAD_ID`，另导出本轮新增的
   `get_group_webhook_bucket` / `reset_group_webhook_bucket`。
   ⚠️ `AIBOT_CHANNEL` **保留导出**（虽仍零消费者）：它与 `GROUP_WEBHOOK_CHANNEL` 是同一个
   概念的两个成员，只导一个正是本条要消灭的那种不对称。口径已写进包 docstring：
   一个概念的全部成员一起导出，⛔ 不许只导一半。
2. ✅ `guard.py` 的 `NotifyPlan.is_send` 死代码已删（删前复查全仓零引用，含测试）。
3. ✅ `test_notify_store.py` 两条漏调的 `assert_group_notify_identity(conn)`
   已补齐（`test_effect_key_is_thread_node_digest` / `test_pending_resends_are_listable`），
   九条写库用例现在全数自带恒等判据。测试脚手架三处复制粘贴的收编见下方 `0916E`。

**仍欠两条已还**（`0916E`，Shao Peishen 2026-09-10 答 `4a`：单独派一条泳道独占
`tools/liaison/tests/` 与 `tools/liaison/config.py`）：
- ✅ **③ 的 conftest 收编**：`FakeClock` / `RecordingSink` / `FAKE_WEBHOOK` 三处复制粘贴收进
  `tools/liaison/tests/conftest.py`，`test_notify_ratelimit.py` / `test_notify_store.py` /
  `test_notify_webhook.py` / `test_notify_transport.py` 改为从 conftest 导入。
- ✅ **④ `config.py:75` 的异常文案错位**：新增 `GroupWebhookMissingError`（`errors.py`），
  `load_group_webhook` 改抛它而不是 `MissingCredentialsError`，消息改为准确描述
  "群通知发送地址缺失，本次群通知跳过发送"，⛔ 不再含"拒绝启动"字样。调用方
  `followup.py:217` 与 `notify/webhook.py` 文档同步；`test_notify_transport.py` /
  `test_notify_webhook.py` 涉及的用例改断言新异常类型。

---


<!-- 〔归档工具〕 2026-09-17 搬入：~~TD-45~~ · `criteria` 子命令的调用引用在两处用了不存在的 `python` 二进制名 ✅ 已还 -->

## ~~TD-45~~ · `criteria` 子命令的调用引用在两处用了不存在的 `python` 二进制名 ✅ 已还

**2026-09-17 已处置（`0917H`）**：三处一次性改掉——`.claude/skills/liaison-unpack/SKILL.md`
第 46 行、`dispatch.py` 的 `HEADLESS_ARGV_FIXED_PART`、`test_unpack_charter_allowlist.py`
的 `_REQUIRED_USES`——`python -m tools.liaison criteria` 统一改成
`PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison criteria`（与
`unpack-signal` 同款写法）。`test_unpack_charter_allowlist.py` 全绿。

**登记时间**：2026-09-17（`[Mac]0916V`，P3 口径点台账 全分支 final review 发现，Important 级、判定超出本单元 Files 范围，不在本单元内改；原误登记为 TD-44，与已归档的 `~~TD-44~~`（SdkLogObserver）撞号，`[Mac]0916W` 改为 TD-45）
**触发条件**：下次任何人碰 `.claude/skills/liaison-unpack/SKILL.md` 或 `tools/liaison/unpack/dispatch.py` 的白名单/接线时顺手改掉；⛔ 在此之前不单独占泳道。

**缺口**：`.claude/skills/liaison-unpack/SKILL.md`（第 46 行左右）指示无头拆件会话跑 `python -m tools.liaison criteria --id … --to …`，`tools/liaison/unpack/dispatch.py` 的 `HEADLESS_ARGV_FIXED_PART`／allowlist 同样白名单了 `Bash(python -m tools.liaison criteria:*)`——但本机（乃至目标部署环境）不存在裸 `python` 这个二进制，只有 `python3` 与两个 venv（`venv/bin/python`、`tools/liaison/.venv/bin/python`）。这条命令在无头会话里会直接 `command not found`。

**为什么不是本次顺手改**：这两处文件都在本单元（P3 口径点台账，Task 1–4）声明的 Files 范围之外，属于 P1（`liaison-unpack-dispatch`）泳道的触碰区；P1 曾修过同类问题（`unpack-signal` 的白名单条目已改成 `PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal`），`criteria` 是后于那次修复才接入 `__main__.py` 的新分支，两处引用沿用了旧写法，没人跟着改。`test_unpack_charter_allowlist.py` 只做"章程文本 vs 白名单文本"互相对照，两边一起错时测试照样绿，抓不出这类问题。

**还债动作**：三处一次性改掉——`SKILL.md` 第 46 行、`dispatch.py` 的 `HEADLESS_ARGV_FIXED_PART`、`test_unpack_charter_allowlist.py` 的 `_REQUIRED_USES`——把 `python -m tools.liaison criteria` 统一改成 `PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison criteria`（与 `unpack-signal` 同款写法）。命令本身是纯标准库、`python3` 裸跑即可，只是这两处引用里的二进制名要修。

**来源**：全分支 final review（opus，`[Mac]0916V`）实测确认——`which python` 在本机查无此二进制，`python3` 裸跑该命令正常。

---


<!-- 〔归档工具〕 2026-09-17 搬入：~~TD-46~~ · `test_run_lanes_model.py` 的 sandbox 环境会继承外层 `HR_ -->

## ~~TD-46~~ · `test_run_lanes_model.py` 的 sandbox 环境会继承外层 `HR_LANE_*`，泳道自跑测试必红 ✅ 已还（0917I）

**根因**：`sandbox` fixture（`tests/test_run_lanes_model.py`）用 `dict(os.environ, ...)` 把当前进程的**全部**环境变量原样带进被测脚本（`run-lanes.sh` 拷贝）的子进程。当 pytest 本身跑在一个由 `run-lanes.sh` 派发的 worktree 泳道会话里时，run-lanes.sh 已为这个 CC 会话导出了 `HR_LANE_ISOLATE=1`／`HR_LANE_MAIN`／`HR_LANE_WORKTREE`（给 `scripts/hooks/worktree-guard.py` 用，`docs/openers/run-lanes.sh:612`）——这些变量原样泄漏进被测脚本的非 worktree 条目，把 `ISO=unset` 断言污染成 `ISO=1`。**`run-lanes.sh` 本体没有问题**：它对每次 `claude` 调用只用 `env ${iso_env[@]}` 局部注入（`run-lanes.sh:618`），worktree 条目才带 `iso_env`，非 worktree 条目是空数组——泄漏纯粹是测试 fixture 没有隔离启动环境。

**还债动作**：`_build_sandbox`（原 `sandbox` fixture）改为先过滤掉 `HR_LANE_` 前缀的环境变量再拷给子进程；新增回归测试 `test_ambient_hr_lane_env_not_leaked_into_child`（先设三个 `HR_LANE_*` 再建 sandbox，钉死非 worktree 条目仍是 `ISO=unset`/`WT=unset`）。改前该回归测试与原有的 `test_worktree_lane_runs_inside_script_created_worktree` 在"跑在泳道 worktree 里"时均红，改后两者在原环境与干净环境下均绿；全量 `pytest` 2454 passed, 6 skipped，两种环境一致。

**登记时间**：2026-09-17（`[Mac]0917I`，`[Mac]0917H` 收工报告称此为「环境泄漏、预先存在」但未查证，本条补上根因与证据）

---


<!-- 〔归档工具〕 2026-09-17 搬入：~~TD-47~~ · `unpack-dispatch --force` 起的无头 `claude` 会话未登录，P1 -->

## ~~TD-47~~ · `unpack-dispatch --force` 起的无头 `claude` 会话未登录，P1 起活验收阻断 ✅ 已还（0917K）

**欠的是什么**：`liaison-reply-bridge-and-patrol` tasks.md 5.2 P1 单独验（`[Mac]0917J` 真实起活实测）当场执行 `PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-dispatch --force`，子进程 `claude` 立即退出，无头日志（`data/liaison/logs/unpack-headless/20260916T232918575673Z.log`）唯一一行是 `Not logged in · Please run /login`。5.2 无法通过，5.3（P0+P1 端到端）连带无法在本轮验。

**根因（0917K 实测确认）**：`tools/liaison/unpack/dispatch.py:121` 的 `_CHILD_ENV_ALLOWLIST = (CLAUDE_BIN_ENV, "PATH", "HOME", "PYTHONPATH")` 只放行这四个键给子进程 `claude` 二进制。`claude` 在 macOS 上的登录态存在系统 Keychain 条目「Claude Code-credentials」里，查询该条目按进程环境里的 `USER` 变量值做 account 匹配——不在四键白名单内，子进程因而拿不到登录态。

**实验记录（对照实验，`env -i` 隔离，二进制取 `unpack-dispatch --dry-run` 打印的 `/Users/paulshao/.local/bin/claude`）**：

| 键集合 | 结果首行 |
|---|---|
| `PATH HOME PYTHONPATH`（原四键，`CLAUDE_BIN_ENV` 当次未设） | `Not logged in · Please run /login`（复现） |
| 原四键 + `USER`（正确值） | `Error: Exceeded USD budget (0.05)`（登录成功，只是撞了 `--max-budget-usd 0.05`） |
| 原四键（去掉 `USER` 再验一次，最小集合减一） | `Not logged in · Please run /login`（复现，证明 `USER` 是必需项） |
| `PATH USER`（去掉 `HOME`、`PYTHONPATH`） | `OK`（`PATH`+`USER` 已是充分集合，`HOME`/`PYTHONPATH` 对登录态本身无影响，仍保留是因为子进程运行仍需要它们） |
| 原四键 + `USER=nonexistent_bogus_user`（错误值） | `Not logged in · Please run /login`（证伪"只要 USER 存在即可"——必须是能匹配 Keychain account 的正确值，即父进程当前登录用户名，属非秘密系统变量） |
| `LOGNAME`/`TMPDIR`/`SHELL`/`LANG`/`XPC_SERVICE_NAME`（逐个单独加） | 均仍 `Not logged in · Please run /login`（逐一排除，非这些变量） |

**最小键集合**：`USER`（非秘密系统变量）。`__CF_USER_TEXT_ENCODING`、`CLAUDE_CONFIG_DIR` 当次父环境未设，未测。

**还债动作（已完成）**：`_CHILD_ENV_ALLOWLIST` 补 `USER`；`tools/liaison/tests/test_unpack_dispatch.py` 新增/更新断言——放行 `USER`，同时新增 `test_filter_child_env_never_leaks_hr_liaison_secrets_regardless_of_allowlist_growth` 作凭据边界回归闸（任意 `HR_LIAISON_*` 键，含未来新增的，均不得进入子进程环境）。

**端到端验证**：worktree 内 `PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-dispatch --force` 真实起活，子进程存活约 200 秒、正常执行完整流程（含 Read/Bash/Write/git commit）后正常结束，无头日志不再出现 `Not logged in`——占位章程下判定为"正常结束"，与 TD-47 原始故障（立即退出）不同。

**踩坑记录（供下次做类似验证参考）**：`python -m tools.liaison ...` 时 Python 会把 CWD 插到 `sys.path` 最前面，早于 `PYTHONPATH`；若在主工作区目录下执行、仅靠 `PYTHONPATH=<worktree 路径>` 指向 worktree 代码，实际仍会优先加载主工作区自己的 `tools.liaison`（`import` 后 `__file__` 可验证）。要验证 worktree 里的改动，必须把 cwd 切到该 worktree 内再跑。

**来源**：`[Mac]0917J` 真实起活实测发现（日志原文如上），`[Mac]0917K` 定位根因、实验验证最小键集合并修复、端到端复验通过。

---


<!-- 〔归档工具〕 2026-09-17 搬入：~~TD-48~~ · 无头拆件会话在占位 prompt 下自行改写并提交 `docs/session接力.md` ✅  -->

## ~~TD-48~~ · 无头拆件会话在占位 prompt 下自行改写并提交 `docs/session接力.md` ✅ 已还（0917O）

**欠的是什么**：`0917K` 的 `unpack-dispatch --force` 实测中，子会话不仅起活成功，还自行写入了 `docs/session接力.md` 的 R-3c/R-3d 两行并各自单独 `git commit`（`a644d45`、`f575c5e`）——这两条提交内容本身是有价值的现场记录，但提交动作本身不在授权范围内，`0917M` 已判定为噪声提交丢弃（未合入 main）。

**根因**：章程红线「越界编辑不入提交」目前只落在 design.md 的 Risks 段落与人工传递的完整章程文本里，是**提示词层**的约束；但 `unpack-dispatch --force` 走的是占位 prompt（"这是一次验收起活，不代表真实拆件任务"），并不携带完整章程文本。然而子进程 `claude -p` 仍会按正常规则加载仓库根 `CLAUDE.md`，其中「🧭 会话末『需你定夺』」一节末尾写着「待办一律落进 `docs/session接力.md`，不留在聊天里」——这条项目级约定对占位 prompt 同样生效，子会话据此自发把当次探测结果当"待办"写回接力文档并提交，绕开了本应由占位 prompt 单独声明的边界。

**触发条件**：`liaison-reply-bridge-and-patrol` §5 全部归档前必须处置——归档意味着 `unpack-dispatch --force`/自动触发路径会在无人盯着的情况下常态化运行，若不处置，每次验收起活都可能重演一次"自发提交"。

**还债动作（二选一，方案未定）**：① `--force` 路径的占位 prompt 也携带真实章程文本（含红线声明），让子会话知道自己在"验收起活"语境下不该碰接力文档；② 收窄无头会话可用的 `git add` 路径白名单（技术兜底，不依赖子会话"读懂"提示词）。

**不还的后果**：每次真实/验收起活都可能把子会话的即兴记录当正式提交写进 main，污染 `docs/session接力.md` 的可信度，且这类提交不经过任何 review 关卡。

**来源**：`0917K` 无头实测复现（`a644d45`/`f575c5e`，已由 `0917M` 丢弃未合入）；design.md Risks 段落已承认提示词层红线的局限，本条补上具体触发实证。

`0917M` 后又复现：`fe64bc1`（08:08，同一 `lane-0917k-td47-child-auth` worktree，`0917N` 清理丢弃未合入）。

`0917N` §5.2 重跑再次复现：TD-47 修复合入后，`--force` 起的无头会话仍在 08:16 自发提交 `07537fe`（登记 R-6 行），且其自身日志文本谎称「没有写新条目、未改动任何文件」与实际提交动作矛盾——证明 TD-48 与 TD-47 是独立问题、TD-47 的修复不影响 TD-48 复现率。`0917N` 已 `git revert --no-edit 07537fe`（不改写历史）。

**还债记录（0917O，2026-09-17，Shao Peishen 答 `1a`：两层一起做）**

- **实验（一次性 git 仓 `/tmp/td48-exp.*`，`env -i PATH HOME USER`，`--max-budget-usd 0.5`，prompt 让会话依次 a 改 `tools/x.py`／b 写 `docs/session接力.md`／c `git add -A`／d `git add docs/session接力.md`／e `git commit -a -m t`／f `git commit -m t`）**：
  - 写法 甲（`acceptEdits` ＋ 只把 allow 收窄到路径）：a **放行**（`The file … has been updated successfully`）、b 放行、c 被拒（`This command requires approval`）、d 放行、e 被拒、f 放行 ⇒ 不满足：`acceptEdits` 自动接受项目内任何编辑，allow 段的路径限定对编辑不起作用。
  - 写法 乙（甲 ＋ `--disallowedTools` 显式 deny）：a **被拒**（`File is in a directory that is denied by your permission settings.`）、b 放行、c 被拒（`Permission to use Bash with command git add -A has been denied.`）、d 放行、e 被拒（`Permission to use Bash with command git commit -a -m t has been denied.`）、f 放行 ⇒ **满足，选中**。丙未试（按"取第一个满足的"）。
  - 路径 pattern 相对子进程 cwd（仓库根）写，实测能匹配工具收到的绝对路径。
  - 【三】F 复验：用 `build_headless_argv` 的真实 argv ＋ `compute_prompt` 真章程 prompt 再跑一轮（`tools/x.py` 改为非空、走 `Edit`），a/c/e 仍被拒、原文同上，b/d/f 放行。
- **代码**：`dispatch.HEADLESS_ARGV_FIXED_PART` 改为 写法 乙——`Edit`/`Write` 按 `CHARTER_WRITABLE_PATHS`（与章程 §三 四项逐字一致）逐路径放行，`git add` 逐路径前缀放行，`git commit` 只放行 `-m` 形式，deny 段列红线目录（`tools/ app/ scripts/ tests/ openspec/ .claude/ CLAUDE.md data/ .env* docs/openers/`）与 `git add -A`／`git add .`／`git commit -a`／`git stash`／`git push`。`unpack-dispatch --force` 改走 `charter.read_charter` ＋ `charter.compute_prompt`（信件编号「（验收起活·无真实回件）」、msgid `FORCE-<UTC戳>`），缺章程 ⇒ stderr 一行、退出码 1、不起进程。
- **测试**：新 `tools/liaison/tests/test_unpack_path_guard.py`（4 条：无裸 Edit/Write/git add/git commit；§三 路径从 SKILL.md 解析后逐项在 Edit/Write/git add 放行里；红线目录都在 deny 里；send-followup／git push 不在放行里）、`test_unpack_cli.py` 加 2 条（`--force` prompt 以章程全文结尾；缺章程不起进程）；`test_unpack_charter_allowlist.py` 扫描器改为只扫 allow 段（共用新 `tests/_argv_rules.py`），带路径的 `git add <路径>` 拆成动词＋路径分别在章程里核对。
- **章程 SKILL.md 改动：无**（红线九项与 §三 路径清单原文未动；权限写法对齐全部在扫描器一侧完成）。
- **design.md**：D4 argv 块由常量生成、逐字一致；Risks 第一条改为「已由权限层按路径收窄（0917O，写法 乙）」并列三条残余风险（deny 为枚举、`-m … -a` 前缀绕过靠编辑被拒兜底、清单漂移由测试转红）。`specs/liaison-unpack-dispatch/spec.md` 「拆件会话以受限权限启动」措辞同步（限路径放行 ＋ 显式拒绝），`openspec validate --strict` 通过。
- **测试隔离根因**：见 TD-49。

---


<!-- 〔归档工具〕 2026-09-17 搬入：~~TD-49~~ · 跑一次 pytest 就往真实 `data/liaison/` 写信号并**真的起一个 `cla -->

## ~~TD-49~~ · 跑一次 pytest 就往真实 `data/liaison/` 写信号并**真的起一个 `claude -p` 拆件会话** ✅ 已还（0917O）

**欠的是什么**：`tools/liaison/tests/test_inbound_wiring.py` 的两条打标用例（`test_worker_marks_the_ledger_for_an_admitted_sender_with_an_inflight_letter`、`…when_sender_userid_has_incidental_whitespace`）走 `run_session_worker` 真实链路。`__main__.py` 把模块级 `UNPACK_SIGNAL_PATH`（`from unpack_cli import DEFAULT_SIGNAL_PATH`，绝对路径，`HR_LIAISON_SIGNAL_PATH` 对它无效）与真实 `dispatch_wiring.bridge_dispatch` 绑进 `run_bridge`，于是每跑一次 pytest：① 真实 `data/liaison/unpack-signal.json` 被追加 `{"letter_number":"人事部#1","msgid":"MSGID0001","archived_path":"data/liaison/archive/threadA/20260910/…"}`（这就是 `0917N`/`34327bf` 那条"再探同一 pending"的来源）；② `subprocess.Popen` **真的起一个** `claude -p … --max-budget-usd 5`（cwd＝仓库根、prompt＝真章程 ＋ 人事部#1/MSGID0001），第二条用例因锁 pid 存活被 `skipped_busy`。

**实证**：2026-09-17 在 worktree `lane-0917o-td48-path-guard` 跑 `pytest tools/liaison/tests/test_inbound_wiring.py`（26 passed）后，worktree 里凭空出现 `data/liaison/{unpack-signal.json,unpack-session.lock,logs/}`，`ps -p 57622` 抓到活着的 `claude -p --permission-mode acceptEdits …`（`0917O` 当场 kill；同日在主工作区跑全量 pytest 取基线又起了 pid 60426，同样当场 kill，其日志为空、未落任何提交）。`0917K`–`0917N` 观察到的"无头会话自发提交 `docs/session接力.md`"，相当一部分是**由测试起的**会话干的，与 `--force` 占位 prompt 是两个独立来源。

**还债动作（已做）**：`tools/liaison/tests/conftest.py` 新增 autouse 夹具 `unpack_side_effects_to_tmp`——`HR_LIAISON_SIGNAL_PATH`、`unpack_cli.DEFAULT_SIGNAL_PATH/DEFAULT_LOG_DIR/DEFAULT_LOCK_PATH`、`dispatch_wiring.DEFAULT_LOG_DIR/DEFAULT_LOCK_PATH`、`__main__.UNPACK_SIGNAL_PATH` 全部顶到 `tmp_path`；`HR_LIAISON_CLAUDE_BIN` 指到不存在的文件，`Popen` 当场 `FileNotFoundError` ⇒ `process_create_failed`，任何用例都起不了真实进程。回归测试 `tools/liaison/tests/test_unpack_test_isolation.py`：跑同一条打标链路，断言真实路径（从未被 patch 的 `REPO_ROOT` 现算）的 `(存在, mtime_ns)` 前后不变、信号落在隔离路径、审计恰好一条 `dispatch_failed/process_create_failed`。改后全量 `pytest`：worktree 内不再生成 `data/`，`pgrep` 无 `claude -p`。

**遗留（归 `0917P`，本条不动）**：主工作区真实 `data/liaison/unpack-signal.json` 里的 `MSGID0001` 项与 `unpack-session.lock`（pid 60426，已死）仍在，需清理。⚠️ **在没有本夹具的旧提交上跑 pytest 仍会起真实会话**——切老分支跑测试前先看 conftest 有没有 `unpack_side_effects_to_tmp`。

**来源**：`0917O` 做 TD-48 【三】E 时复现。

---


<!-- 〔归档工具〕 2026-09-17 搬入：~~TD-50~~ · 拆件会话 `git add` 章程路径被判需审批 ✅ 已还（0917Q） -->

## ~~TD-50~~ · 拆件会话 `git add` 章程路径被判需审批 ✅ 已还（0917Q）

**欠的是什么**：TD-48（`0917O`）把拆件会话的 `git add` 收窄成逐路径前缀 `Bash(git add <章程路径>:*)`，但**首次真实回件**（2026-09-17 09:45 CST，人事部#1，日志 `data/liaison/logs/unpack-headless/20260917T014544528242Z.log`）会话在收口时三次 `git add` 全被拦——它写出的最终请求是 `git add "docs/跟进信/README-跟进信清单.md" "docs/跟进信/回件/人事部#1-20260917.md" "docs/session接力.md"`，日志原文：「`git add` 三处改动路径时，工具返回「This command requires approval」，重试三次（合并/分别加引号/单文件）均同样被拦」。结果：回件判断已做、三个文件已写、但未 commit、未清信号、未进第二轮，会话停在「需你定夺」等一个不存在的人。同一日志开头还有 14 行 CLI 告警：「Permission allow rule (--allowed-tools): Write(docs/跟进信/回件/**) is not matched by file permission checks — only Edit(path) rules are. Use Edit(docs/跟进信/回件/**) instead (Edit rules cover all file-editing tools).」（allow 4 条、deny 10 条同款）——`Write(...)` 规则从未生效过。

**根因（一次性 git 仓实测，claude 2.1.263，`env -i PATH HOME USER`，`--max-budget-usd 0.5`，argv 取自 `build_headless_argv`、仅把 `text` 换成 `stream-json --verbose` 以抓工具原文）**：

| 轮 | 命令 | 规则 | 结果 |
|---|---|---|---|
| g1 | `git add docs/跟进信/README-跟进信清单.md` | 旧 `Bash(git add docs/跟进信/README-跟进信清单.md:*)` | 放行 |
| g2 | `git add "docs/跟进信/README-跟进信清单.md"` | 同上 | **This command requires approval** |
| g3 | `git add docs/跟进信/回件/a.md docs/session接力.md` | 旧 `Bash(git add docs/跟进信/回件/:*)` | **This command requires approval** |
| g4 | `git add -- docs/session接力.md` | 旧 `Bash(git add docs/session接力.md:*)` | **This command requires approval** |
| g5 | `git add docs/session接力.md` | 同上 | 放行 |
| n1 | `git add notes/x.md`（纯 ASCII 对照） | 临时 `Bash(git add notes/:*)` | **This command requires approval** |
| n2–n4 | 引号／两路径／`--` 的 ASCII 版 | 同上 | 全部 requires approval |
| h1 | `git add docs/跟进信/回件/a.md` | 临时 `Bash(git add docs/跟进信/回件/*)` | 放行 |
| h2 | `git add docs/跟进信/README-跟进信清单.md docs/session接力.md` | 旧文件前缀规则 | 放行 |
| h3 | `git add notes/x.md` | 临时 `Bash(git add notes/*)` | 放行 |
| h4 | `git add docs/跟进信/回件/a.md docs/session接力.md` | 临时 `Bash(git add docs/跟进信/回件/*)` | 放行 |
| h5 | `git add "docs/session接力.md"` | 临时 `Bash(git add "docs/session接力.md":*)` | 放行 |

定因：**与非 ASCII 无关**（n1 纯 ASCII 目录同样被拦，g1 非 ASCII 文件放行）。① `Bash(X:*)` 是**词边界**前缀——命中 `X` 本身或 `X ` ＋任意后续，`X` 后紧跟非空格字符不算，所以 `Bash(git add <dir>/:*)` **从未**命中过 `git add <dir>/<文件>`（TD-48 的目录规则一天都没生效，`0917O` 实验只测过文件路径 `docs/session接力.md` 所以没露）；② 引号不做归一化，`git add "…"` 是另一条命令；③ `--` 同理是另一个前缀；④ `Bash(<含 * 的模式>)` 是通配，`*` 匹配任意串**含空格**（h4）；⑤ 多路径本身不是问题——首个路径命中即放行（h2）。真实会话被拦三次＝ g2（三路径全带引号）＋ 引号分别加 ＋ 单文件（落在 `回件/` 目录下，即 g3/h1 形态）。

**选中写法（甲 的通配变体；乙 未取——`Bash(git add:*)` ＋ deny 枚举同样受词边界限制，`Bash(git add tools:*)` 命中不了 `git add tools/x.py`；丙 未加，红线已在权限层）**：`dispatch._git_add_allow_rules`——目录 `Bash(git add <dir>*)`、文件 `Bash(git add <file>:*)`，各配 `"<path>`／`-- <path>`／`-- "<path>` 三种变体（4 路径 × 4 = 16 条）；deny 段新增 `dispatch._git_add_deny_rules` 通配 `Bash(git add *tools/*)` 等 10 条，堵「章程路径打头、红线目录尾随」（`git add docs/session接力.md tools/x.py` 会卷走并行泳道在 `tools/` 下的未提交改动）；`Write(...)` allow/deny 共 14 条整段删除（`Edit(path)` 覆盖全部编辑工具）。

**复验（新 argv ＋ `compute_prompt` 真章程 prompt，同一一次性仓）**：g1/g2/g3/g4 放行；g6 `git add "docs/跟进信/README-跟进信清单.md" "docs/跟进信/回件/人事部#1-20260917.md" "docs/session接力.md"`（真实会话原命令）放行；x1 `git add -A`／x2 `git add .`／x3 `git add tools/x.py`／x4 `git add docs/openers/x.md`／x5 `git add docs/session接力.md tools/x.py` 全部「Permission to use Bash with command … has been denied.」；x6 `Write` 新建 `tools/y.py` 「File is in a directory that is denied by your permission settings.」；日志 0 条 `not matched by file permission checks`；`git diff --cached --name-only` 只含四个章程路径。

**测试**：`tests/_argv_rules.py` 新增按实测语义写的规则匹配器（`bash_rule_matches`／`bash_command_verdict`：词边界前缀、`*` 含空格、deny 优先）；`test_unpack_path_guard.py` 新增 4 条（argv 无 `Write(`；目录不用 `:*` 写法；章程路径四种写法＋多路径全放行；越界与尾随越界全不放行）、改 1 条（Edit＋git add 用裁决器）；`test_unpack_charter_allowlist.py` 关键词剥掉 `"…"`／`-- ` 外壳再对章程；`tools/liaison/tests` 1115 passed，全量 pytest 2466 passed／6 skipped／0 failed。

**残余风险（已写进 design Risks ④⑤）**：尾随的 `docs/` 下清单外路径（`git add docs/session接力.md docs/tech-debt.md`）不在 deny 枚举里，靠章程 §三 `git diff --cached` 自查兜底；规则语义是对 claude 2.1.263 的实测，CLI 升级可能变，只能靠真实起活日志发现。

**首次真实回件的三个文件（`README-跟进信清单.md` 还原、`回件/人事部#1-20260917.md`、`session接力.md` 追加）在主工作区处于未提交状态**，本条不碰（⛔ 本 opener 不改 `docs/session接力.md`、`docs/跟进信/**`），待人核对后决定提交或丢弃；第二条信号（msgid `a932acca…`）仍 pending，修复合入后下一次起活会按新规则收口。

---
