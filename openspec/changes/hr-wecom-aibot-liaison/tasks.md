> **进度**：59/66（第 1-7 章已完成并合回 main，第 8 章 2/9。第 1/2/3 章 2026-09-08，第 4 章「消息归档」、第 5 章「值守任务队列」、第 6 章「群通知外发」与第 7 章「连接生命周期与中断告警」2026-09-09。各 6/6、6/6、6/6、10/10、10/10、10/10、9/9。第 8 章已勾 8.3「launchd 守护配置与安装脚本」、8.5「结构性守护测试」2026-09-09——⚠️ 两条都只到「配置与测试就位」，launchd **尚未实际安装**，装与灰度在 8.6。⚠️ 第 6 章同样只到「给了 URL 就能发」：真实群 webhook 尚未写进 `.env`，**未做过任何一次真实投递**，灰度真发同在 8.6 由 Shao Peishen 本人操作）
>
> **粒度约定**（CLAUDE.md「粒度映射」）：本文件的**一个 `##` 章节 = 一个 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的交付单元**。章节的 checkbox 在该 plan 的 final review 通过后才勾。
>
> **落点约定**（design.md D10）：全部代码落 `tools/liaison/`，依赖落 `tools/liaison/requirements.txt`。⛔ 不落 `app/`、⛔ 不落 `scripts/`（二者都在 `sync-to-server.sh` 的 `SYNC_PATHS` 里，会被推到 `.51`）、⛔ 不进根 `requirements.txt`。允许 `tools/liaison` 单向 `import app.storage.idempotency`，⛔ `app/` 不得反向 import。
>
> **幂等约定**（design.md D3）：有副作用的动作一律独占一个 `effect_*` 函数并带幂等键 `{thread_id}:{node_name}:{business_key}`，`thread_id` = 会话标识（私聊取 userid／群聊取 chatid），`business_key` = 企微 `msgid`。计算类一律 `compute_*` 纯函数。

## 1. 通道可行性与服务骨架

对应能力：`liaison-channel-session`（凭据 fail-closed 启动校验部分）。**本章是全部后续章节的前置**：SDK 在 Python 3.14 上是否可用未经验证，未验证前不写业务代码（design.md D8）。

- [x] 1.1 建 `tools/liaison/` 目录骨架与 `tools/liaison/requirements.txt`（只含本服务依赖），加 `tools/liaison/README.md` 写明"开发期值守工具、永不部署 .51、不是产品功能"三条边界
- [x] 1.2 **SDK 兼容性实测（阻塞项）**：在 Python 3.14 环境安装 `wecom-aibot-python-sdk`，记录可安装性与 import 结果；结论写入 `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`
- [x] 1.3 按 1.2 结论二选一并在 findings 文档里写明选了哪条：① SDK 可用 → 钉死具体版本号写进 `tools/liaison/requirements.txt`；② SDK 不可用 → 按 design.md D8 退路建最小 WS 客户端的模块骨架（只覆盖本服务用到的消息类型，不做通用 SDK）
- [x] 1.4 实现启动期凭据校验：`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` 缺失、空串、纯空白一律拒绝启动并指明缺失项，进程不驻留
- [x] 1.5 `.env.example` 追加 `HR_LIAISON_*` 占位（只写变量名与注释，⛔ 不写任何真实值）；加测试断言受版本管理的文件中不含真实密钥形态的取值
- [x] 1.6 单测覆盖 1.4／1.5：三种缺失形态各一条、版本管理无凭据一条

**验收**：`liaison-channel-session` 中「凭据缺失时拒绝启动」一条要求的全部场景通过；SDK 路线已定且有 findings 落档。

> **第 1 章落地偏离登记**（2026-09-08，run-build 收口时记）：
>
> - **1.3 选了路线 ①**：`wecom-aibot-python-sdk` 在 Python 3.14.6 上实测装得上（发行版本
>   `1.0.2`）、`import aibot` 成功、`WSClient` / `WSClientOptions` 的建连＋心跳＋重连三样参数齐备，
>   判据 A/B/C 全过。已在 `tools/liaison/requirements.txt` 钉死 `==1.0.2`。证据见
>   `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`。**路线 ② 的最小 WS 客户端骨架未建**
>   （分支未命中，⛔ 不是漏做）。
> - ⚠️ **发行名 ≠ import 名**：PyPI 是 `wecom-aibot-python-sdk`，顶层模块是 `aibot`。
>   且模块 `__version__` 报 `1.0.0` 与发行版本 `1.0.2` 对不上——**一律以
>   `importlib.metadata.version()` 为准**，任何拿 `__version__` 校验装对没有的写法都会误判。
> - 🔴 **第 7 章 7.6 的接线约束**：`WSClientOptions.max_reconnect_attempts` 默认值是 **10**，
>   与 spec「断线后自动恢复接收」要求的"重试直至成功、服务不退出"冲突。第 7 章必须显式传
>   `-1`（SDK 里 `-1` = 无限重连），⛔ 不许用默认值。
> - **超出原 1.5 范围的一处加固**：凭据扫描正则原本锚在行首不含缩进，终审实测发现**缩进的**
>   真实赋值可以整条逃过扫描（嵌套代码块／YAML 里粘一条即中）。已放行前导空白并补回归用例。
> - ⏸ **留步：真实建连未验**。需 Shao Peishen 在企业微信管理后台注册**新** aibot 应用取得
>   `BotID` / `Secret`（账号级操作，无法代劳）。本章只验 SDK 能力面是否齐备，真实建连归第 7 章。
> - **`tools/liaison/tests/` 已接进根 `pyproject.toml` 的 `testpaths`**，全量 `pytest` 一次跑到。
>   与 `proposal.md`「Impact · 不触碰 `pyproject.toml`」的口径冲突，已登记 TD-14，归档前订正。

## 2. 存储基座与幂等不变式

对应能力：跨全部能力的存储前提（design.md D3／D5）。

> **第 2 章落地偏离登记**（2026-09-08，run-build 收口时记）：
>
> - **2.6 的证伪改用进程内 monkeypatch**：实现计划 Task 6 Step 3 原写法是
>   `printf 'import tools.liaison...' > app/_tmp_violation.py` 再删除，即**往 `app/` 下真写一个文件**。
>   本交付单元 opener 明令「测试进程内 monkeypatch，⛔ 不改文件」且「⛔ 不碰 `app/`」，**opener 优先**。
>   落地为常驻测试 `test_scanner_catches_a_synthetic_violation_via_monkeypatched_app_modules`：
>   patch 掉 `test_no_app_module_imports_tools` **真正调用的那个** `_app_modules`，再直接驱动真实的
>   生产测试函数在 `pytest.raises` 下变红。全程零字节写入 `app/`；全分支 `git diff --name-only`
>   无任何 `app/`／`scripts/` 路径。
> - **计划缺陷（记在计划账上，非实现问题）**：Task 5 Step 3 的证伪 A「函数体内先写一行 `effect_log`
>   再抛异常」**不可能**造成它自己声称的「`COUNT(*) FROM effect_log == 0` 不成立」。两条独立原因：
>   ① `idempotent_effect` 的 `except Exception: conn.rollback()` 会把手工插入的那行一并撤销
>   （`get_connection` 用默认 `isolation_level`，那行就在同一个隐式事务里）；
>   ② 被 `-k` 选中的 `test_no_effect_log_when_business_write_raises` 装饰的是**测试内局部闭包**，
>   根本不调 `effects.py`。实现者如实报告而非改断言迎合 brief。
>   **控制方追加的变异**（把幂等记录挪到事务外，`fn` 内 `conn.commit()`）已使判据真正变红：
>   `test_constraint_violation_in_business_write_leaves_no_trace` 失败于
>   `tools/liaison/tests/test_liaison_effects.py:658: AssertionError` / `assert 1 == 0`。
>   **教训带进第 3 章的 brief**：`-k` 选中的证伪目标必须是**会调用生产函数**的测试。
> - **终审实证发现的两处守卫漏洞（已在合并前修完）**：① 事务扫描器原先只认裸局部名，漏掉
>   `with self.conn:`（Attribute）、`with get_connection() as c:`（Call）、`async with conn:`
>   —— 而第 3–5 章把连接挂在服务对象上比裸局部名自然得多；② `EFFECT_NODE_TO_TABLE` 的映射
>   **正确性**从未被验证，终审把两个条目对调后恒等断言**依然通过**。二者均已补测试堵上
>   （后者做成仓库级 AST 扫描，一并堵上「`effect_*` 定义在别的模块会同时逃过守卫」的缺口）。
> - **`assert_effect_log_identity` 的前提已写进 docstring**：本断言成立的前提是**业务表只增不删**。
>   第 7 章 180 天留存期清理落地后它会**因正当理由变红**，届时唯二的合法应对是「同事务连带删除
>   对应 `effect_log` 行」或「把断言限定在未清理的 thread 范围」，⛔ **不许改成总数比较、不许削弱成约等于**
>   —— 它是铁律 1 唯一的机器守卫。

- [x] 2.1 建 `data/liaison.db` 的 schema 初始化：`effect_log` 表与 `app/storage/db.py:59-67` **同构**（同列、同主键、同唯一索引），`CREATE TABLE IF NOT EXISTS` 幂等建表
- [x] 2.2 建 `liaison_message`（消息台账）与 `liaison_task`（队列真身）两张表；`liaison_task.send_status` 用 `CHECK` 约束钉死三态枚举值（存枚举，⛔ 不存 emoji）
- [x] 2.3 接入 `app.storage.idempotency.idempotent_effect`（单向 import），确认装饰器在本服务的单连接模型下工作；写一条测试断言该连接上不存在第二个事务管理者
- [x] 2.4 写「恒等不变式」测试脚手架：给定任意一批消息，每个 `effect_*` 的 `effect_log` 条数与其业务表行数按 `thread_id` 恒等。⚠️ 幂等策略：本章不产生对外副作用，只建表与验证机制
- [x] 2.5 单测：业务写抛异常时 `effect_log` 不留记录、重跑会重新尝试（对应 `liaison-task-queue`「业务写失败时不留下幂等记录」场景）
- [x] 2.6 加断言测试：`app/` 下无任何模块 import `tools/`（结构性单向约束，design.md D5）

**验收**：`liaison-task-queue` 中「入队幂等且与幂等记录原子提交」一条要求的两个场景可在脚手架上跑通（队列写入逻辑本身在第 5 章）。

## 3. 准入名单

对应能力：`liaison-inbound-whitelist`。

- [x] 3.1 定名单配置文件格式（`tools/liaison/config/whitelist.yaml`）：字段只含企微 userid、中文姓名、角色说明；⛔ 不含手机号／邮箱／身份证号
- [x] 3.2 写入 design.md D2 的结论名单：汤丽萍、邵培申两人。⛔ 聂鑫／王寒月／陈承不入（理由已在 D2 表格，⛔ 不在实现阶段重开此结论）
- [x] 3.3 实现 `compute_admission`：纯函数，输入发送人标识，输出是否命中。文件缺失／不可读／解析失败／名单为空一律折成空名单全拒；⛔ 不沿用任何此前加载过的名单
- [x] 3.4 判定路径不抛异常给调用方：任何失败结果都是"未命中"，并记录 ERROR 级日志
- [x] 3.5 单测覆盖四种 fail-closed 形态（不存在／格式坏／空名单／权限不可读）+ 命中一条 + 名单配置字段受限一条
- [x] 3.6 单测：追加一名成员并重载后该成员命中，且未修改任何 `.py` 文件（对应「名单变更不需要改动代码」场景）

**验收**：`liaison-inbound-whitelist` 除「名单内／名单外消息的归档与入队行为」两条（依赖第 4／5 章）外全部场景通过。

> **第 3 章还债后补登记**（2026-09-09 `[Mac]0909O`，轻量通道还 TD-15／TD-16，⛔ 未改任何验收判据）：
>
> - **D1（TD-15 去重）**：3.4「任何失败结果都记 ERROR」的实现改为按**名单文件内容 SHA-256** 去重——同一份内容连续失败只记一组，内容一变重新记一组。⛔ **未降级**（仍是 ERROR），⛔ **未缓存名单**（全局只存一个 64 字符指纹串，3.3 的「⛔ 不沿用任何此前加载过的名单」原样成立且有双守测试）。契约仍成立：每个**不同的**失败态都被 ERROR 记录过。裁决＝去重不降级，Shao Peishen 第十四批认可。
> - **D2（TD-16 ①）**：YAML 重复键从 PyYAML 原生的静默 last-wins 改为 **fail-closed + ERROR 点名键名**。这是一处**行为收紧**：此前"再追加一段 `members:`"会静默替换整份名单，现在整份拒绝。⛔ 只记键名不记值。
> - **D3（TD-16 ②③）**：`UnicodeDecodeError` 单列成「配置文件非 UTF-8」分类；`_read_roster` 顶部 `path = Path(path)`，调用方传 `str` 不再落「未预期异常」。⚠️ 第 4 章 `inbound.py:102` 的 `admit(sender_userid, whitelist_path)` 是这条的直接受益方。
> - **D4（TD-16 ④）**：`tools/liaison/config/README.md` 补「⚠️ 失败面」段。
> - **D5（TD-14）**：`proposal.md` 的「Impact · 不触碰」把 `pyproject.toml` 那一行订正为「⛔ 不往 `pyproject.toml` 添加任何依赖；`testpaths` 因 `tools/liaison/tests` 接入而新增一条」。⛔ 只改了那一行。
> - **D6（TD-18 第二轮）**：`test_liaison_effects.py` 的 `_scan_transaction_violations` 对 `with <Call>:` 细化判据（名字词根 + 已知连接符号判违规、`contextlib.*`／`tempfile.*` 模块族放行、**陌生被调用者仍判违规**）。🔴 `ast.Name`／`ast.Attribute` 两格未动，`with self._conn:` 仍无条件必红。⚠️ 「陌生被调用者仍判违规」是相对 opener 字面要求的**收紧偏离**，理由与证伪实测见 `docs/tech-debt.md` TD-18。

## 4. 消息归档

对应能力：`liaison-message-archive`。

- [x] 4.1 实现 `compute_archive_path`：纯函数，输出 `data/liaison/archive/<thread_id>/<yyyymmdd>/<msgid>__<归一化文件名>`。日期只做分目录，`msgid` 是最细一级键（⛔ 禁止按天／按人粗粒度落点）
- [x] 4.2 实现 `compute_safe_filename`：只做路径安全（去路径分隔符与控制字符、去首尾空白、超长截断保留扩展名）；⛔ 不因非 ASCII 或"看着像乱码"改写或拒收，中文名原样保留
- [x] 4.3 实现附件落盘：`rb`/`wb` 字节流 + 写临时文件 → `fsync` → 原子 `rename` 到最终路径。⚠️ 幂等策略：目标路径含 `msgid`，已存在即视为已完成（rename 幂等）
- [x] 4.4 实现完整性校验：只用字节长度 + SHA-256。⛔ 禁止任何 UTF-8／文本解码校验，⛔ 不因内容不可解码判定损坏
- [x] 4.5 实现 `effect_archive_message`：**先落材料、后写台账**，台账行与 `effect_log` 行在同一事务提交。⚠️ 幂等策略：幂等键 `{thread_id}:effect_archive_message:{msgid}`；中间态只允许"材料已在、台账未记"，⛔ 不允许"台账已记、材料缺失"
- [x] 4.6 单测：同人同天 3 个同名附件各自完整、同名不同内容两份可分别取回（对应生产 bug「归档覆盖」）
- [x] 4.7 单测：真实 xlsx／pdf 字节样本进出逐字节相同、校验不走解码路径（对应生产 bug「二进制判误」）
- [x] 4.8 单测：同一 `msgid` 投递两次只有一份归档与一条台账；模拟落盘后被强制终止再重跑，台账与材料一致
- [x] 4.9 单测：文件名含路径分隔符不逃出目录、超长截断保留扩展名、中文名保留
- [x] 4.10 实现名单外分支：只归档 + 礼貌回复，⛔ 不生成队列条目（队列条目生成在第 5 章，此处只接线并加断言"队列条目数不变"）

> **第 4 章落地偏离登记**（run-build 收口时记，2026-09-09 [Mac]0909B）：
> - **P1（plan 决定）**：`compute_safe_filename` 与文件读写拆成 `archive.py` / `attachments.py` 两个模块。4.4 的"⛔ 禁止 UTF-8 解码"由一条只扫 `attachments.py` 的 AST 断言执行；文件名的字节截断必须 decode，那段在 `archive.py`。⛔ 不许把截断逻辑搬进 `attachments.py`——那会把这道边界拆了。
> - **P2（plan 决定）**：`thread_id` / `msgid` **只校验不改写**（不安全即 `ArchivePathError`）。归一化会把两个不同的键磨成同一个，制造与「归档覆盖」同源的碰撞。
> - **P3（plan 决定）**：一条消息**最多一个附件**（aibot 协议：一条消息一个 msgtype、一个媒体项）。`attachments_json` 仍是数组、长度 0 或 1。真出现多附件消息类型时须改 design D4 的路径形态，⛔ 不在本章预留分支。
> - **P4（已登记技术债 TD-19）**：礼貌回复是 at-most-once（走注入 reply port，非 `effect_*`）。做成幂等 effect 需要加 outbox 表，触发 opener 约束 5 的"停在该点"，且真实通道在第 6／7 章。丢的是告知，不丢材料、不丢待办。
> - **P5（范围）**：`liaison-message-archive` 的「归档数据的留存期有上限」一条 **⛔ 不在本章**（第 8 章）。`tools/liaison/consistency.py` 是它的前置核对器，本章 ⛔ 不实现任何删除，并有一条源码扫描断言守着（`test_consistency_module_never_deletes_or_writes`）。
> - **P6（范围）**：`liaison-inbound-whitelist`「名单内成员的消息进入归档与队列」只完成**归档**一半；**入队**在第 5 章，接线位 = `InboundRoute.should_enqueue`。第 5 章接上入队时须**同时删掉** `test_inbound_module_never_references_the_enqueue_effect`——它守的是"现在还没写"，届时就该退休。
> - **P7（review 发现，plan 原文有缺陷；已登记技术债 TD-20）**：plan 给的 leaf 形态 `<msgid>__<原文件名>` **不是无碰撞编码**——`msgid` 与文件名都可含下划线时，两组不同的 `(msgid, 文件名)` 会落到同一路径（实测：`msgid="ms_"`+`"filename"` 与 `msgid="ms"`+`"_filename"` 都得 `ms___filename`），即「归档覆盖」生产 bug 的又一种形态。**处置**：保持 D4 路径形态不变，改为 `_validated_key` 拒收任何含 `_` 的 `msgid`（此时 leaf 中第一个 `_` 必在 `len(msgid)` 处，编码可逆）。方向与 plan 自陈的保守立场一致（「⛔ 不是把 `_validated_key` 放松成清洗」）。⚠️ 真实企微 msgid 若为 base64url（字母表含 `_`）则**带附件的消息会全数被拒、纯文本消息却正常**——第 7 章接通道前须先打日志核对字符集，见 TD-20。

**验收**：`liaison-message-archive` 除「留存期」一条（第 8 章）外全部场景通过；`liaison-inbound-whitelist`「名单外消息只归档并礼貌回复」全部场景通过。

## 5. 值守任务队列

对应能力：`liaison-task-queue`。

- [x] 5.1 实现 `effect_enqueue_task`：写一条 `liaison_task` 行，携带发送人标识、会话标识、来源 `msgid`、接收时间。⚠️ 幂等策略：幂等键 `{thread_id}:effect_enqueue_task:{msgid}`，队列行与 `effect_log` 行同一事务提交
- [x] 5.2 存储层拒绝缺少来源 `msgid` 的队列行（`NOT NULL` + 单测）
- [x] 5.3 实现状态转移校验：三态枚举 + 「暂缓」只能来自「待发」+ 「已推送」必带推送时间戳；非法取值与非法转移一律拒绝
- [x] 5.4 实现 `render_queue_markdown`：从 `liaison_task` 渲染只读 Markdown 视图，内容中的竖线／换行做转义。⛔ 单向：不从该文件读任何状态、不回写
- [x] 5.5 实现"从队列条目回指材料"的查询路径（条目 → 来源消息 → 全部附件）
- [x] 5.6 单测：内容含竖线／换行／控制字符／超长文本时字段不错位、不产生额外条目（对应生产 bug「队列越界写入」）
- [x] 5.7 单测：短时间内多条消息并发入队各自成行、无覆盖丢失（对应生产 bug「队列追加并发覆盖」）
- [x] 5.8 单测：手改导出 Markdown 不影响真身、下次渲染覆盖手改内容
- [x] 5.9 单测：非法状态取值被拒、非法转移被拒、已推送带时间戳
- [x] 5.10 单测：跑第 2 章的恒等不变式脚手架，确认队列条目数与幂等记录数按会话恒等

> **第 5 章落地偏离登记**（run-build 收口时记，2026-09-09 [Mac]0909D）：
>
> - **P1 计划参考实现有 bug，已偏离**：plan Task 3 给的 `defer_task` 参考实现依赖
>   `effect_defer_task` 走到存储层 TRIGGER，但 `idempotent_effect` 的前置检查在函数体
>   **之前**短路返回——对已 `deferred` 的行第二次 defer 根本走不到 TRIGGER，会**静默
>   no-op 而不是拒绝**。改为在翻译层把"幂等命中"当作"已离开过 pending"直接抛
>   `TaskTransitionRejected`。依据：spec 要求「非法转移一律拒绝」，plan 自带测试亦
>   逐字禁止吞掉该异常。**副作用见 TD-23**。
> - **P2 第 4 章钉子测试已翻转**：`test_this_chapter_never_enqueues_even_for_admitted_senders`
>   是 4.10 留下的「防止提前实现入队」临时守卫（其 docstring 自陈），第 5 章实现入队后
>   与目标矛盾，就地翻转为 `test_distinct_admitted_messages_each_enqueue_exactly_once`。
>   **名单外只归档不入队的 7 条守卫（含 fail-closed 一条）全部存活**，未减覆盖。
> - **P3 转义方案两轮返工**：`escape_cell` 原用 `f"\\x{ord(ch):02x}"`，`02x` 只保证
>   **最小**宽度 2，码点 > `0xFF` 时产生变长十六进制（U+2028 → `\x2028`），而截断的
>   token 正则只吃 2 位 → 切出 `\x20` 这种**语法完整但语义错误**的静默失真。已改为
>   定长 `\uXXXX`（BMP 内）/ `\UXXXXXXXX`（BMP 外）。已验证转义在长度 ≤3 的 6174 个
>   串上是**单射**。
> - **P4 终审修掉一处静默吞错**：`enqueue_task` 原用 `"UNIQUE" in detail` 文案匹配判幂等
>   命中，会把 `idempotent_effect` 在**回滚失败**时刻意抛出的 `IntegrityError` 吞成
>   "幂等成功"——那正是铁律 1 恒等式破裂且无症状的路径。已改为语义判据（查 `effect_log`
>   的 `(node_name, business_key)`，⛔ 不查 `liaison_task`：同连接会看到自己刚写的
>   未提交行，反而把"这次刚写坏的"误判成"早就提交好的"）。
> - **P5 未尽项已登记 TD-23 / TD-24**：均只影响第 6 章接线时的调用方，当前
>   `defer_task` / `mark_task_pushed` **零生产调用方**，不阻塞本章。
> - **P6 `superpowers` 技能在本机取不到**（`Unknown skill`），按磁盘 `SKILL.md` 手工走完
>   SDD 协议：6 个 Task 各派独立实现者 + 独立 reviewer，2 轮 Task 级 fix loop，
>   终审全分支 review（opus）+ 1 轮修复。SDD 台账随 worktree 删除消失，关键结论已转写进本节。

**验收**：`liaison-task-queue` 全部场景通过；`liaison-inbound-whitelist`「名单内成员的消息进入归档与队列」全部场景通过。

## 6. 群通知外发

对应能力：`liaison-group-notify`。

- [x] 6.1 实现 webhook 发送封装：标准库 HTTP 客户端 + JSON + `errcode` 检查 + 超时；真实地址只从环境变量读，受版本管理的配置只记变量名
- [x] 6.2 实现 `compute_length_guard`：按 UTF-8 **字节数**判定超限；⛔ 两条通道阈值独立配置（aibot 通道 20480 字节；群 webhook markdown 约 4096 字符对应的字节阈值），⛔ 不共用同一常量
- [x] 6.3 实现超限降级：降级为"提要 + 附件"；提要仍超限或该通道无附件承载方式 → 拒发 + 告警，⛔ 不静默截断
- [x] 6.4 实现令牌桶主动节流（容量 20、按 20/60s 补充），发送前生效
- [x] 6.5 实现限流退避重试：收到 `errcode 45009` 按 1s→2s→4s→8s 重试，最多 4 次
- [x] 6.6 实现重试耗尽的待重发持久化 + 告警。⚠️ 幂等策略：`effect_send_group_notify` 幂等键 `{thread_id}:effect_send_group_notify:{通知内容摘要}`，重试不产生重复通知
- [x] 6.7 结构性约束：收件对象只能是内部值守群或名单内成员；⛔ 不提供以候选人标识为收件对象的参数或调用路径。加断言测试守护（对应 `liaison-group-notify`「无候选人外发入口」场景）
- [x] 6.8 单测：字符数不超但字节数超 → 判超限；两通道阈值独立生效；非限流错误不被当作成功
- [x] 6.9 单测：降级成功一条、无法降级拒发一条、不静默截断一条；限流后重试成功不产生重复通知一条、重试耗尽落待重发 + 告警一条
- [x] 6.10 单测：环境变量未配置时拒发并告知缺失项，⛔ 不静默跳过后报告成功

**验收**：`liaison-group-notify` 全部场景通过；全部测试用 mock 端点，⛔ 不做任何真实发送。

## 7. 连接生命周期与中断告警

对应能力：`liaison-channel-session`（除第 1 章已完成的凭据校验外）。

- [x] 7.1 实现存活戳：连接健康但空闲时持续更新；⛔ 不把"一段时间无消息"判为断线（参考服务曾混淆此二者）
- [x] 7.2 实现中断窗口记录：起始时间 + 恢复时间，恢复后闭合
- [x] 7.3 实现未闭合窗口的启动期补记：上次未闭合窗口按"起始已知、恢复时间 = 本次启动时间"补记，⛔ 不丢弃。⚠️ 幂等策略：按窗口起始时间去重，重复启动不重复补记。⚠️ **落地偏离（TD-20）**：`recovered_at` 记的是**本次启动时刻**而非**真正重新连上的时刻**——「重启时网络仍未恢复」场景下中断时长被低报（复现见 `docs/tech-debt.md` TD-20）。本条按 spec 原文（"恢复时间 = 本次启动时间"）实现即勾，改法需 Shao Peishen 拍板，触发条件＝第 8 章灰度 8.6
- [x] 7.4 实现中断告警：内容必含窗口起止时间与"该时段消息可能未收到、请重发"的明确表述；⛔ 措辞中不得声称该缺口已被幂等机制覆盖
- [x] 7.5 告警通道失败时只记本地日志，⛔ 不中止消息接收
- [x] 7.6 接线自动重连（SDK 内置机制或 D8 退路的自建实现），确认重试带退避、⛔ 无紧密循环。⏸ **留步：真实建连未验**——`HR_LIAISON_BOT_ID`/`SECRET` 待 Shao Peishen 在企微后台注册 aibot 后才有；本章全程 fake 连接对象。⛔ **本勾不得当作"真实链路已验"的证据**，端到端验证留到第 8 章灰度（8.6）。另：实测 `WSClient.connect` 是 `async def`（`docs/findings/2026-09-09-aibot-wsclient-表面实测.md`），真正的阻塞入口是 `client.run()`；本章按"表面未验即拒绝启动"处理（`EXIT_SDK_SURFACE_UNVERIFIED`），⛔ 不发明未经验证的 async 适配器（TD-19）
- [x] 7.7 单测：空闲两小时不告警、真断线记窗口、进程被杀后重启补记窗口
- [x] 7.8 单测：告警文本含起止时间与重发请求；告警发送失败时接收不中止
- [x] 7.9 单测：重连间隔随失败次数增长、服务不退出

**验收**：`liaison-channel-session` 全部场景通过。

## 8. 留存清理、进程守护与灰度验收

对应能力：`liaison-message-archive`（留存期一条）+ 整体可运行性。

- [ ] 8.1 实现留存期清理：`HR_LIAISON_RETENTION_DAYS` 默认 180，超期归档与消息台账清理；队列行不参与自动清理。⚠️ 幂等策略：按年龄判定，重复执行安全
- [ ] 8.2 清理失败时告警，⛔ 不静默跳过；单测覆盖超期被清理一条、清理失败告警一条
- [x] 8.3 写 launchd plist（`KeepAlive` + `ThrottleInterval`）与安装说明；⛔ 不移植 Windows 侧的三级退避重启脚本（design.md D12）。产出：`tools/liaison/launchd/com.zhuopin.hr.liaison.plist.template`（占位符渲染式，模板内 ⛔ 无任何凭据取值）＋ `tools/liaison/scripts/install_launchd.py`（幂等：渲染 → 写 `~/Library/LaunchAgents/` → bootout 忽略失败 → bootstrap → 打印 `launchctl print` 状态行；`--dry-run` 不写任何文件、不调 launchctl）＋ README「运行与守护」一节（装／停即 `bootout` 回滚／看日志）。⏸ **留步：本 session ⛔ 未执行安装**——起 LaunchAgent 属安全配置变更，由 Shao Peishen 在 Terminal 自己跑一次，时点＝灰度 8.6。⏸ **留步：`tools/liaison/.venv` 尚未创建**，安装脚本对此 fail-closed（解释器不存在即拒装并退非 0，理由＝`KeepAlive` 不区分退出码，装上会 30s 一轮无限重启一个必然失败的进程）。⛔ 本勾只表示配置与脚本已就位并被单测覆盖，**不得当作「守护已生效」的证据**
- [ ] 8.4 日志接入：轮转 + 有界容量 + 个人信息脱敏（借用 `runtime-observability` 的做法，不受其 HTTP 请求链路要求约束）
- [x] 8.5 加结构性守护测试：`tools/` 不在 `sync-to-server.sh` 的 `SYNC_PATHS` 里、SDK 依赖不在根 `requirements.txt` 里（design.md D10 的两道门禁）。已由 `tools/liaison/tests/test_liaison_boundaries.py` 两条覆盖（第 1 章落地：`test_tools_is_not_in_sync_paths`、`test_root_requirements_has_no_liaison_dependency`）＋ 本条补 plist 守护（`test_launchd_template_carries_variable_names_but_no_credential_values`：凭据名 ⛔ 不得以 plist 键值形式进模板，只准以 XML 注释形式出现——`~/Library/LaunchAgents/` 不受 `.gitignore` 保护且会被备份链带走，凭据挪进 `EnvironmentVariables` 会静默扩大泄漏面）
- [ ] 8.6 单机灰度：名单先只放邵培申自己，自测归档／入队／通报三条链路，核对恒等不变式
- [ ] 8.7 加入汤丽萍（配置加一行 + 重启），并告知她"照原样在群里发即可、不用改任何习惯"
- [ ] 8.8 一周观察窗口的观察项落档：漏消息告警是否误报、限流是否被触发、归档是否有重名冲突。⚠️ 观察结论写 `docs/findings/`，⛔ 不在观察期内改判据
- [ ] 8.9 全部章节勾完后**当场**跑 `openspec-archive-change`（CLAUDE.md「归档时限」：不得跨越一个工作 session）

**验收**：服务在本机常驻可用、三条链路端到端通、两道结构性门禁有测试守护、观察结论已落档。
