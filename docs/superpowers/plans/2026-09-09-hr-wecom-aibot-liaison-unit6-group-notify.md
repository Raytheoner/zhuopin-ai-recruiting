# 群通知外发（hr-wecom-aibot-liaison 交付单元 6）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"我已经往值守群通知过了"这句话可信——发之前先自己限流（不靠被平台打回才知道），内容超长按 **UTF-8 字节** 判定并降级成"提要 + 附件"（提要仍超限或没有附件承载就整条拒发并告警，⛔ 绝不静默截断），被限流就按 1s→2s→4s→8s 退避重试，重试耗尽则落成待重发记录 + 告警（⛔ 不静默丢弃），且这条通道**在结构上**没有任何以候选人为收件对象的入口。

**Architecture:**

```
tools/liaison/
├── config.py                       ← 【本章追加 1 个常量 + 1 个函数】load_group_webhook()
│                                      ⛔ 不动 REQUIRED_CREDENTIAL_ENV_NAMES（理由见下 6）
├── alerts.py                       ← 【本章追加 1 个函数】effect_emit_alert()，第 7 章的
│                                      effect_emit_outage_alert 改为委托它，行为逐字不变
├── storage/schema.py               ← 【本章只追加一张表】GROUP_NOTIFY_SCHEMA + 拼进 SCHEMA
│                                      ⛔ 不改 liaison_message / liaison_task / liaison_outage_window
├── notify/                         ← 本章主体（design D10：落 tools/liaison/，⛔ 不落 app/ 与 scripts/）
│   ├── __init__.py                 ← 只做再导出，⛔ 不放逻辑
│   ├── guard.py                    ← 纯函数：长度守卫、降级决策、内容摘要
│   │   ├── AIBOT_CHANNEL_LIMIT_BYTES = 20480          两个独立常量，
│   │   ├── GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES = 4096   ⛔ 结构上不可能共用（见下 2）
│   │   ├── compute_byte_length(text) -> int
│   │   ├── compute_length_guard(text, *, limit_bytes) -> LengthVerdict   limit_bytes ⛔ 无默认值
│   │   ├── compute_notify_digest(text) -> str          SHA-256 前 16 位十六进制
│   │   └── compute_notify_plan(text, *, limit_bytes, attachment_supported) -> NotifyPlan
│   ├── ratelimit.py                ← 令牌桶（容量 20、20/60s 补充）+ 退避序列
│   │   ├── compute_refill_tokens(elapsed, *, refill_tokens, refill_seconds) -> float   纯
│   │   ├── compute_backoff_delay(retry_index) -> float                                 纯
│   │   └── class TokenBucket       monotonic / sleep 全部构造时注入，⛔ 不 import time
│   ├── transport.py                ← 本服务**唯一**真发 HTTP 的地方（标准库 urllib）
│   │   ├── class Transport(Protocol)   post_json / post_multipart
│   │   ├── class UrllibTransport       超时必传，⛔ 报错文本里不许出现 URL
│   │   └── WebhookTransportError       网络层/协议层失败
│   ├── webhook.py                  ← 发送封装 + 断点续发的投递序列
│   │   ├── compute_upload_url(webhook_url) -> str      纯函数
│   │   ├── compute_markdown_payload / compute_file_payload    纯函数
│   │   ├── class GroupWebhookSender    ⛔ 构造与方法里都没有"发给谁"这个参数（6.7）
│   │   ├── class DirectDelivery / class DegradedDelivery      带断点，重试 ⛔ 不重发已送达的那条
│   │   └── effect_deliver_with_backoff(delivery, *, bucket, sleep) -> DeliveryOutcome
│   └── store.py                    ← 唯一写库的地方
│       ├── @idempotent_effect("effect_send_group_notify")
│       │   effect_send_group_notify(conn, *, thread_id, business_key, ...)  唯一 INSERT
│       ├── send_group_notify(...)              门面：算 plan → 调上面那个 effect
│       └── select_pending_resends(conn)        只读，给第 8 章的重发驱动留的把手
└── tests/
    ├── test_notify_guard.py        6.2 / 6.3 / 6.8 判定与降级决策
    ├── test_notify_ratelimit.py    6.4 / 6.5 令牌桶与退避序列
    ├── test_notify_transport.py    6.1 / 6.10 传输封装与凭据缺失
    ├── test_notify_store.py        6.6 / 6.9 幂等、恒等、待重发、告警
    ├── test_notify_webhook.py      6.3 / 6.5 / 6.9 通道装配与退避重试（端到端）
    └── test_notify_boundaries.py   6.7 结构性断言 + 阈值不共用 + 无静默截断
.env.example                        ← 【只加一行】HR_LIAISON_GROUP_WEBHOOK=（空值，⛔ 不写真实地址）
tools/liaison/tests/test_liaison_no_secrets_in_vcs.py
                                    ← 【只扩扫描面】把新变量名加进 CREDENTIAL_ENV_NAMES 与占位参数化
```

开工前必须先读懂的**八条判断**，改动前逐条对照：

**1. 🚨 `tools/liaison/` 下的非测试代码禁止写 `with <名字|属性|调用>:`。**
第 2 章的 `tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source` 扫 `tools/liaison/**/*.py`（`tests` 目录除外），把 `with X:` 判为"隐式提交事务边界"违规。
⚠️ **判据已被 TD-18 细化过（`8bae001`，2026-09-09），细化的只有 `ast.Call` 这一格**：被调用者命中 `open` / `os.fdopen` / `io.open` / `suppress` / `NamedTemporaryFile` / `TemporaryDirectory`（及 `contextlib.*` / `tempfile.*` 两个模块族）才放行；名字里带 `conn` / `connect` / `transaction` / `begin` 词根的**先**判违规；**其余陌生被调用者仍判违规**。`urllib.request.urlopen` 属"陌生被调用者"，⛔ 照旧会红。
⚠️ **本章 opener 里写的"先赋值再 with 变量"这条规避法是错的，⛔ 不要照做**——已核对扫描器源码（`test_liaison_effects.py:185-206`）：`ast.Name`（`with resp:`）与 `ast.Attribute`（`with self._x:`）同样在判违规之列，赋值一步躲不掉。
本章因此**一律不写 `with`**：`urlopen` 的响应用 `resp = ...` + `try/finally: resp.close()`。测试代码里可以照常用 `with`（扫描器跳过 `tests` 目录）。
⛔ **不要为了写 `with` 去放宽扫描器或往白名单里加本章文件**——技术债泳道正在改那个文件（TD-18），而它守的是工程铁律 1 唯一的静态判据。

**2. 两条通道的阈值必须结构性地不可共用，光写两个常量不够。**
`compute_length_guard(text, *, limit_bytes)` 的 `limit_bytes` 是**必传关键字参数，⛔ 没有默认值**——于是"忘了传、用了别的通道的默认值"这个错误在语法层就写不出来（调用点会当场 `TypeError`）。两个常量各自独立声明、由 `ChannelLimit` 各包一份，Task 6 用一条断言钉住"两者取值不相等且不是同一个名字的别名"。
取值来自 design D9 逐字：aibot 通道 **20480 字节**（Windows 侧 2026-08-28 实测）；群 webhook markdown **官方口径约 4096**。
⚠️ **群 webhook 取 4096 字节而不是"4096 字符换算出的 12288 字节"**——两种读法都可能对，选小的那个：判过严只会多降级一次（看得见、可回退），判过松会被服务端静默打回（看不见）。这条判断写进 `guard.py` 的常量注释里，供 reviewer 核对。

**3. 幂等键含内容摘要，且"先发后记"——失败模式是可能重复，⛔ 不是静默丢失。**
幂等键 = `{thread_id}:effect_send_group_notify:{digest}`，`digest` = 正文 UTF-8 编码后 SHA-256 的**前 16 位十六进制**（tasks 6.6 逐字要求"通知内容摘要"）。
发送动作**必须在 `@idempotent_effect` 装饰的函数体内部**执行：装饰器的 `effect_log` 预检是"这条通知发没发过"的唯一权威，把发送放到装饰器外面等于让预检形同虚设。
顺序钉死为 **先发送、后写台账行**（写行与 `effect_log` 由装饰器在同一个 `BEGIN` 里提交）。反过来（先记后发）会让"记录说发了、其实没发"成为可能，而那正是本章要消灭的那类谎。代价是"已发出但落库前进程被杀"⇒ 下次可能重复通知一次——重复看得见（群里多一条），丢失看不见。这个方向与第 7 章告警的选择一致，是刻意的。
⚠️ 装饰器的预检 `SELECT` 在 legacy transaction control 下**不开启写事务**，写事务从函数体最后那条 `INSERT` 才开始——所以 HTTP 期间**不持有 SQLite 写锁**。⛔ 不要把 `INSERT` 挪到发送之前"图省事"。

**4. 一次通知恰好写一行，三种终局共用同一张表。**
`liaison_group_notify` 用一个 `state` 列区分 `sent` / `pending_resend` / `rejected`，⛔ **不要拆成"成功表 + 待重发表"**：拆开之后"`effect_log` 条数 == 业务表行数"这条铁律 1 的恒等式就跨了两张表，而恒等式一旦需要求和才成立，下一次有人加第三种终局时它会因为一个正当理由变红，最顺手的"修法"就是削弱它。

**5. `EFFECT_NODE_TO_TABLE` 本章一个字都不加，⛔ 不碰 `storage/effects.py`。**
沿用第 7 章已验证过的判断（`docs/superpowers/plans/2026-09-09-...unit7-connection-lifecycle.md` 第 3 条），已重新核对第 2 章两条守卫测试的实际判据，确认本章新增的 effect **不会**让它们变红：
- `test_effect_node_to_table_matches_reality` 比对的是 `vars(tools.liaison.storage.effects)` 里的 `effect_*` 集合——只看 `effects.py` 一个模块，本章的 effect 定义在 `notify/store.py`，不在取值范围内。
- `test_effect_node_to_table_matches_the_insert_target_repo_wide` 虽然全仓扫 `@idempotent_effect`，但 `_validate_node_to_table_mapping` **只遍历 mapping 的键**（`test_liaison_effects.py:559`）；扫到了却没登记的 effect 不计入违规。
代价是本章的 effect 不被 `assert_effect_log_identity` 覆盖。这个缺口本章自己补：Task 4 在 `test_notify_store.py` 里自带 `assert_group_notify_identity(conn)`，并在每条写库用例末尾调它。

**6. `HR_LIAISON_GROUP_WEBHOOK` ⛔ 不进 `REQUIRED_CREDENTIAL_ENV_NAMES`。**
那个元组是**启动期** fail-closed 的清单（第 1 章：任一项缺失即拒绝启动）。把群 webhook 加进去，等于"没配群通知就整个值守通道起不来"——而收消息与发群通知是两件独立的事。
本章改用一个独立函数 `load_group_webhook(env)`：**发送时**校验，缺失即 `raise MissingCredentialsError([GROUP_WEBHOOK_ENV])`，报的是**变量名不是取值**（复用第 1 章 `errors.py` 已有的这条纪律）。6.10 要的"拒发并告知缺失项、⛔ 不静默跳过后报成功"由此成立。

**7. 收件对象在结构上不存在，靠断言把它钉住（6.7）。**
`GroupWebhookSender` 构造时只吃一个 webhook 地址；`send_markdown` / `send_file` 都**没有**"发给谁"的参数——地址即收件对象，而地址只可能来自 `HR_LIAISON_GROUP_WEBHOOK`（内部值守群）。
Task 6 加两条 AST 断言守着：① `tools/liaison/notify/` 下任何函数的形参名不得匹配 `touser|to_user|toparty|totag|candidate|recipient|openid|external_?userid`；② 任何字典字面量的键不得出现 `touser` / `toparty` / `totag`（企微"发给指定人"的三个参数名）。
⛔ 断言只看形参与字典键，不看注释与文档字符串——注释里写"⛔ 不发候选人"是应该的，写成一个参数才是问题。

**8. 真实投递不在本章，遇到就留步登记。**
真实 URL 写进 `.env`、灰度真发是 **8.6**，由 Shao Peishen 亲自做（对外通道开关属"不可代"项）。本章代码只到"给了 URL 就能发"为止：全部测试用 fake transport，⛔ 不发任何真网络、⛔ 不 sleep 真时间。
同理，"把 `pending_resend` 的行真的重发出去"的驱动器**不在本章范围**——本章只提供 `select_pending_resends(conn)` 这个只读把手，并在收工报告里登记 `⏸ 留步：待重发驱动器属第 8 章`。

**Tech Stack:** Python 3.14（根 `pyproject.toml` 钉死 `>=3.14,<3.15`）· 纯标准库（`urllib.request` / `json` / `hashlib` / `sqlite3` / `dataclasses` / `typing.Protocol` / `ast` / `logging`）· `app.storage.idempotency.idempotent_effect`（唯一放行的 `app.*` 导入）· pytest 8.3.4

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」、`design.md` D9/D3/D10 与本交付单元 opener。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md 工程铁律（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *（本章：`effect_send_group_notify` 的 `effect_log` 行与 `liaison_group_notify` 行由 `@idempotent_effect` 在同一事务提交；`business_key` = 内容摘要（SHA-256 前 16 位十六进制）。恒等判据由本章自带的 `assert_group_notify_identity` 覆盖——见 Architecture 第 5 条为什么不登记进 `EFFECT_NODE_TO_TABLE`。）*
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
   *（本章纯函数：`compute_byte_length` / `compute_length_guard` / `compute_notify_digest` / `compute_notify_plan` / `compute_refill_tokens` / `compute_backoff_delay` / `compute_upload_url` / `compute_markdown_payload` / `compute_file_payload`。副作用：`effect_deliver_with_backoff`（发 HTTP，不写库）与 `effect_send_group_notify`（唯一写库，挂装饰器）。⛔ 不许出现既算又写的混合函数。）*
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   *（本章不调任何模型。这条在这里的等价物是：限流参数、退避序列、两条通道阈值全部是**显式常量**，⛔ 不许写成"从响应里学"或"自适应"。）*
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
   *（本章是**出站**方向，不新建任何入站端点。这条在这里的等价物是出站超时：`WEBHOOK_TIMEOUT_SECONDS = 5.0`，⛔ 不许不带超时地调 `urlopen`。）*

### 来自 CLAUDE.md 合规红线（逐字，与本章相关的）

- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
  *（本通道只发**内部**值守通知，不发 JD/拒信/邀约。本章 ⛔ 不新增任何面向候选人的外发路径——这正是 6.7 的结构性约束。）*
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。
  *（本通道**没有**候选人入口。6.7：不存在以候选人标识为收件对象的参数或调用路径，由 AST 断言守着。）*
- **模型全部走境内**，简历数据不出境。
  *（本章只往企微群 webhook 发内部通知，不出境。⛔ 不许把通知内容送去任何第三方摘要服务——摘要是本地纯函数。）*

### 来自 CLAUDE.md 部署约束（逐字，与本章相关的）

4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
   *（design D10：`tools/` 不在 `sync-to-server.sh` 的 `SYNC_PATHS` 白名单里，本服务结构上到不了 .51。⛔ 不改部署脚本去"排除 tools"。依赖若有新增只进 `tools/liaison/requirements.txt`，⛔ 不进根 `requirements.txt`——本章全用标准库，实际不该有新增。）*

### 来自 design.md D9（逐字）

- **限流**：企微群机器人官方限流 **20 条/分钟**（超限返回 `errcode 45009`）。本实现用令牌桶（容量 20、按 20/60s 补充）主动节流；真的收到 `45009` 时按指数退避重试（1s→2s→4s→8s，最多 4 次）；4 次仍失败即写入待重发记录并告警，⛔ 不静默丢弃。
- **长度守卫按字节计，且两条通道各自独立配置**：aibot 通道服务端限额 **20480 字节**（Windows 侧 2026-08-28 实测）；群机器人 webhook 的 markdown 上限约 **4096 字符**（企微官方限制）。⛔ 不共用一个常量——两个数字来源不同、单位不同（字节 vs 字符），共用等于给其中一条通道埋一个静默截断。
- **超限降级**：超限时降级为"提要 + 附件"；提要本身仍超限、或没有附件通道可用时**整条拒发并告警**，⛔ 不静默截断丢内容。
- **凭据分离**：群 webhook 真实 URL 只落 `.env`；版本管理里的配置只写**环境变量名**。

### 来自本交付单元 opener（逐字）

1. 铁律 1 逐字：effect_send_group_notify 的 effect_log 行与通知台账/待重发行同一事务提交；幂等键含内容摘要（SHA-256 取前 16 位十六进制），重试与恢复重跑不产生第二条通知
2. compute_* / effect_* 拆分：长度判定、降级决策、令牌桶取令牌、退避序列全是纯函数或可注入时钟的对象；HTTP 只在 effect 里发；传输层与时钟以参数注入，单测全 fake，⛔ 不 sleep 真时间、⛔ 不发真网络
3. D9 逐字：两条通道阈值是两个配置项、两个常量，⛔ 不共用；超限先降级"提要 + 附件"，提要仍超限或该通道无附件承载 → 拒发 + 告警；⛔ 任何形式的静默截断
4. 6.7 结构性：发送 API 的收件对象只能取"内部值守群"或名单内成员，⛔ 不存在以候选人标识为收件对象的参数或调用路径；用断言测试守护
5. 6.10：HR_LIAISON_GROUP_WEBHOOK 未配置 → 拒发并报告缺失的变量名，⛔ 不静默跳过后报成功；版本管理里只出现变量名，⛔ 不出现任何真实 URL（测试用 https://example.invalid/… 占位）
6. 告警走第 7 章的 alerts*；alerts 通道自身失败只记日志、不中止
7. 🔴 真实投递（真实 URL 写进 .env、灰度真发）不在本章——那是 8.6，由 Shao Peishen 亲自做；本章代码只到"给了 URL 就能发"为止
8. D10 落点 tools/liaison/（建议 tools/liaison/notify/）；⛔ 不碰 app/、scripts/；⛔ 不改 whitelist.py、__main__.py、tools/liaison/README.md（本批其它泳道在改）；需要加表 → 只在 storage/schema.py 追加 CREATE TABLE IF NOT EXISTS，⛔ 不改已有表
9. TD-18：事务扫描器对 `with <Call>:` 会误报——本章代码避开该写法（先赋值再 with 变量），⛔ 不改扫描器（技术债泳道在改）

> ⚠️ **对 opener 第 9 条的一处更正**（不是放宽，是收紧）：已核对扫描器源码，`with <Name>:` 与 `with <Attribute>:` 同样判违规，"先赋值再 with 变量"躲不掉。本章的规避法是**完全不写 `with`**（`try/finally` + 显式 `close()`）。⛔ 仍然不改扫描器。

### spec Requirement → Task 对照

| spec Requirement（`specs/liaison-group-notify/spec.md`） | tasks.md | Task |
|---|---|---|
| 发送速率受主动限流约束 | 6.4 | Task 2、Task 5 |
| 限流响应触发退避重试，重试耗尽不静默丢弃 | 6.5 / 6.6 / 6.9 | Task 2、Task 4、Task 5 |
| 长度守卫按字节计，且各通道阈值独立配置 | 6.2 / 6.8 | Task 1、Task 6 |
| 超限内容降级为提要加附件，不可降级即拒发 | 6.3 / 6.9 | Task 1、Task 5 |
| 外发对象仅限内部同事 | 6.7 | Task 3、Task 6 |
| 发送地址与凭据不入版本管理 | 6.1 / 6.10 | Task 3 |

---

### Task 1: 长度守卫与降级决策——两条通道的阈值在结构上不可共用

**Files:**
- Create: `tools/liaison/notify/__init__.py`
- Create: `tools/liaison/notify/guard.py`
- Test: `tools/liaison/tests/test_notify_guard.py`

**Interfaces:**
- Consumes: 无（本任务是本章的地基，只依赖标准库）
- Produces:
  - `AIBOT_CHANNEL_LIMIT_BYTES: int = 20480`、`GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES: int = 4096`
  - `ChannelLimit(name: str, limit_bytes: int)`；实例 `AIBOT_CHANNEL`、`GROUP_WEBHOOK_CHANNEL`
  - `compute_byte_length(text: str) -> int`
  - `LengthVerdict(byte_length: int, limit_bytes: int, over_limit: bool)`
  - `compute_length_guard(text: str, *, limit_bytes: int) -> LengthVerdict`（`limit_bytes` ⛔ 无默认值）
  - `compute_notify_digest(text: str) -> str`（16 位十六进制）
  - `compute_text_prefix_within_bytes(text: str, budget_bytes: int) -> str`
  - `NotifyPlan(mode, digest, body, full_text, attachment_filename, attachment_content, reject_reason, byte_length, limit_bytes)`
  - `compute_notify_plan(text: str, *, limit_bytes: int, attachment_supported: bool) -> NotifyPlan`
  - 常量 `MODE_DIRECT="direct"` / `MODE_DEGRADED="degraded"` / `MODE_REJECT="reject"`

- [ ] **Step 1: 建包并写失败的测试**

创建空包文件 `tools/liaison/notify/__init__.py`：

```python
"""群通知外发（tasks.md 第 6 章 / spec `liaison-group-notify`）。

⛔ 本包只做**内部值守群**的外发。不存在、也永远不许出现以候选人为收件对象的
参数或调用路径（6.7，由 tests/test_notify_boundaries.py 的 AST 断言守着）。
"""
```

创建 `tools/liaison/tests/test_notify_guard.py`：

```python
"""第 6 章·长度守卫与降级决策（6.2 / 6.3 / 6.8 的判定部分）。

本文件**全部是纯函数测试**：不建库、不起网络、不 sleep。
"""

from __future__ import annotations

import inspect

import pytest

from tools.liaison.notify import guard


def test_byte_length_counts_utf8_bytes_not_characters():
    """6.2 逐字：按 UTF-8 **字节数**判定，⛔ 不按字符数。"""
    assert guard.compute_byte_length("abc") == 3
    assert guard.compute_byte_length("中文") == 6
    assert len("中文") == 2  # 反面对照：字符数会给出完全不同的答案


def test_char_count_under_limit_but_bytes_over_is_judged_over_limit():
    """spec 场景「中文内容按字节判定」：字符数不超、字节数超 → 判超限。"""
    text = "中" * 2000  # 2000 字符 < 4096；6000 字节 > 4096
    assert len(text) < guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    verdict = guard.compute_length_guard(
        text, limit_bytes=guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    )
    assert verdict.byte_length == 6000
    assert verdict.over_limit is True


def test_two_channels_judge_the_same_text_independently():
    """spec 场景「两个通道阈值不同」：同一段内容按各自阈值独立判定。"""
    text = "中" * 2000  # 6000 字节：aibot(20480) 不超，群 webhook(4096) 超
    aibot = guard.compute_length_guard(text, limit_bytes=guard.AIBOT_CHANNEL.limit_bytes)
    group = guard.compute_length_guard(
        text, limit_bytes=guard.GROUP_WEBHOOK_CHANNEL.limit_bytes
    )
    assert aibot.over_limit is False
    assert group.over_limit is True


def test_the_two_channel_limits_are_two_different_configured_values():
    """D9 逐字：⛔ 不共用同一个阈值常量。

    这条断言的意义不在于"4096 != 20480"这个算术事实，而在于：谁要是把两条通道
    合并成一个常量，这里会当场变红。
    """
    assert guard.AIBOT_CHANNEL_LIMIT_BYTES == 20480
    assert guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES == 4096
    assert guard.AIBOT_CHANNEL_LIMIT_BYTES != guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    assert guard.AIBOT_CHANNEL.limit_bytes != guard.GROUP_WEBHOOK_CHANNEL.limit_bytes


def test_length_guard_has_no_default_limit():
    """结构性：`limit_bytes` 必传且无默认值 ⇒ "忘了传就用上别的通道的数"写不出来。"""
    parameter = inspect.signature(guard.compute_length_guard).parameters["limit_bytes"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        guard.compute_length_guard("x")  # type: ignore[call-arg]


def test_digest_is_sixteen_hex_chars_and_stable():
    """6.6：幂等键的 business_key 分量 = 内容摘要（SHA-256 前 16 位十六进制）。"""
    digest = guard.compute_notify_digest("值守通知")
    assert len(digest) == 16
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert digest == guard.compute_notify_digest("值守通知")
    assert digest != guard.compute_notify_digest("值守通知 ")


def test_prefix_never_splits_a_multibyte_character():
    """按字节预算取前缀时 ⛔ 不切碎多字节字符。"""
    # "中" 占 3 字节，预算 4 字节只能放下一个字
    assert guard.compute_text_prefix_within_bytes("中中中", 4) == "中"
    assert guard.compute_text_prefix_within_bytes("中中中", 6) == "中中"
    assert guard.compute_text_prefix_within_bytes("中中中", 2) == ""


def test_under_limit_content_is_sent_as_is():
    plan = guard.compute_notify_plan("短消息", limit_bytes=4096, attachment_supported=True)
    assert plan.mode == guard.MODE_DIRECT
    assert plan.body == "短消息"
    assert plan.attachment_filename is None
    assert plan.reject_reason is None


def test_over_limit_content_degrades_to_summary_plus_attachment():
    """spec 场景「超长内容降级成功」：群里收到提要与附件，完整内容可从附件取回。"""
    text = "中" * 2000
    plan = guard.compute_notify_plan(text, limit_bytes=4096, attachment_supported=True)
    assert plan.mode == guard.MODE_DEGRADED
    # 提要必须自己不超限
    assert guard.compute_byte_length(plan.body) <= 4096
    # 完整内容一字不少地进了附件
    assert plan.attachment_content == text
    assert plan.attachment_filename is not None
    assert plan.digest in plan.attachment_filename


def test_degraded_summary_declares_the_truncation_and_names_the_attachment():
    """spec 场景「不静默截断」：被裁掉的部分必须在提要里被**声明**出来。"""
    text = "中" * 2000
    plan = guard.compute_notify_plan(text, limit_bytes=4096, attachment_supported=True)
    assert "降级" in plan.body
    assert plan.attachment_filename in plan.body
    assert str(plan.byte_length) in plan.body
    # 反面：提要 ⛔ 不许是原文的一段纯前缀（那就是没声明的截断）
    assert not text.startswith(plan.body)


def test_reject_when_the_channel_has_no_attachment_carrier():
    """spec 场景「无法降级时拒发」之一：该通道没有附件承载方式。"""
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=False)
    assert plan.mode == guard.MODE_REJECT
    assert plan.body == ""
    assert plan.reject_reason == guard.REJECT_NO_ATTACHMENT_CHANNEL


def test_reject_when_the_summary_itself_would_still_be_over_limit():
    """spec 场景「无法降级时拒发」之二：提要本身仍超限。

    通道上限小到连"这是降级提要"这句声明都装不下时，⛔ 不许把声明砍掉硬发——
    砍掉声明发出去的就是一条静默截断的通知。
    """
    plan = guard.compute_notify_plan("中" * 100, limit_bytes=32, attachment_supported=True)
    assert plan.mode == guard.MODE_REJECT
    assert plan.reject_reason == guard.REJECT_SUMMARY_STILL_OVER


@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_limit_is_a_programming_error(limit):
    with pytest.raises(ValueError):
        guard.compute_length_guard("x", limit_bytes=limit)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_notify_guard.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.notify.guard'`

- [ ] **Step 3: 写实现**

创建 `tools/liaison/notify/guard.py`：

```python
"""长度守卫与降级决策——**本模块全是纯函数**（工程铁律 2）。

⛔ 不读环境、不碰网络、不碰数据库、不写日志。它只回答两个问题：
「这段文字对**这条**通道超限了吗」与「超限了该怎么办」。

⛔ 本目录（`tools/liaison/`，测试除外）禁止写 `with X:`：第 2 章的事务扫描器
把 `with <名字|属性>:` 无条件判为隐式提交违规，`with <调用>:` 只放行一份正面
白名单。本模块用不到 `with`，写在这里是给后来改动的人看的。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: aibot 通道的服务端限额，**字节**（design D9：Windows 侧 2026-08-28 实测）。
AIBOT_CHANNEL_LIMIT_BYTES = 20480

#: 群机器人 webhook 的 markdown 限额，**字节**。
#:
#: design D9 的官方口径是"约 4096 字符"。⚠️ 这里刻意取 **4096 字节**，而不是
#: "4096 字符换算出的 12288 字节"——两种读法都可能对，选小的那个：判过严只会多
#: 降级一次（群里看得见提要 + 附件，可回退）；判过松会被服务端静默打回，而
#: "我已经通知过了"当场变成假话。
#:
#: ⛔ **这两个常量永远是两个数、两个来源，⛔ 不许合并成一个"通用上限"。**
#: 单位与出处都不同（一个是实测字节数，一个是官方文档的字符口径），
#: 合并等于给其中一条通道埋一个静默截断（D9 逐字）。
GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES = 4096


@dataclass(frozen=True)
class ChannelLimit:
    """一条外发通道的长度配置。**每条通道各自一份。**"""

    name: str
    limit_bytes: int


AIBOT_CHANNEL = ChannelLimit(name="aibot", limit_bytes=AIBOT_CHANNEL_LIMIT_BYTES)
GROUP_WEBHOOK_CHANNEL = ChannelLimit(
    name="group_webhook", limit_bytes=GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
)

#: 摘要取 SHA-256 的前多少位十六进制（tasks 6.6：幂等键含"通知内容摘要"）。
DIGEST_HEX_LENGTH = 16

MODE_DIRECT = "direct"
MODE_DEGRADED = "degraded"
MODE_REJECT = "reject"

REJECT_NO_ATTACHMENT_CHANNEL = "该通道没有可用的附件承载方式，超限内容无法降级"
REJECT_SUMMARY_STILL_OVER = "提要本身仍超过通道上限，无法降级"

ATTACHMENT_FILENAME_TEMPLATE = "liaison-notify-{digest}.md"

#: 提要开头的声明。**这句话是"不静默截断"的可执行形式**：正文被裁过这件事
#: 必须写在发出去的内容里，⛔ 不许只写在日志里——收信人看不见日志。
SUMMARY_NOTICE_TEMPLATE = (
    "【内容超长已降级】完整正文见附件 {filename}（共 {byte_length} 字节）。"
    "以下为开头节选：\n\n"
)


@dataclass(frozen=True)
class LengthVerdict:
    byte_length: int
    limit_bytes: int
    over_limit: bool


@dataclass(frozen=True)
class NotifyPlan:
    """一条通知"怎么发"的完整决定。**纯数据，不含任何通道对象。**"""

    mode: str
    digest: str
    body: str
    full_text: str
    attachment_filename: str | None
    attachment_content: str | None
    reject_reason: str | None
    byte_length: int
    limit_bytes: int

    @property
    def is_send(self) -> bool:
        return self.mode in (MODE_DIRECT, MODE_DEGRADED)


def compute_byte_length(text: str) -> int:
    """UTF-8 编码后的**字节数**。⛔ 不是 `len(text)`。"""
    return len(text.encode("utf-8"))


def compute_length_guard(text: str, *, limit_bytes: int) -> LengthVerdict:
    """判定这段文字对**这条**通道是否超限。

    `limit_bytes` 是**必传关键字参数、⛔ 没有默认值**——这是"两条通道不共用阈值"
    的结构形态：忘了传当场 `TypeError`，⛔ 不会静默套用另一条通道的数。
    ⛔ 不要"为了方便"给它加默认值，那一行就是 D9 明令禁止的共用常量。
    """
    if limit_bytes <= 0:
        raise ValueError(f"通道上限必须为正：{limit_bytes}")
    byte_length = compute_byte_length(text)
    return LengthVerdict(
        byte_length=byte_length,
        limit_bytes=limit_bytes,
        over_limit=byte_length > limit_bytes,
    )


def compute_notify_digest(text: str) -> str:
    """通知内容摘要，幂等键 `{thread_id}:effect_send_group_notify:{digest}` 的第三段。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_HEX_LENGTH]


def compute_text_prefix_within_bytes(text: str, budget_bytes: int) -> str:
    """按 UTF-8 字节预算取前缀，⛔ 不切碎多字节字符。

    ⛔ 不用 `text.encode()[:n].decode(errors="ignore")`：那是"先切碎再把碎片掩盖
    掉"，掩盖手段（`ignore`）本身就是一种静默。这里逐字符累加，装不下就停。
    """
    if budget_bytes < 0:
        raise ValueError(f"字节预算不可能是负数：{budget_bytes}")
    chunks: list[str] = []
    used = 0
    for ch in text:
        size = len(ch.encode("utf-8"))
        if used + size > budget_bytes:
            break
        chunks.append(ch)
        used += size
    return "".join(chunks)


def _reject(text: str, *, digest: str, verdict: LengthVerdict, reason: str) -> NotifyPlan:
    return NotifyPlan(
        mode=MODE_REJECT,
        digest=digest,
        body="",
        full_text=text,
        attachment_filename=None,
        attachment_content=None,
        reject_reason=reason,
        byte_length=verdict.byte_length,
        limit_bytes=verdict.limit_bytes,
    )


def compute_notify_plan(
    text: str, *, limit_bytes: int, attachment_supported: bool
) -> NotifyPlan:
    """决定这条通知直接发、降级发、还是拒发。**纯函数，不发任何东西。**

    三条出口，⛔ 没有第四条（尤其没有"截断后照发"）：
    - 不超限 → `MODE_DIRECT`，原文一字不改地发；
    - 超限且有附件承载 → `MODE_DEGRADED`，提要（含降级声明）+ 附件（完整原文）；
    - 超限但没有附件承载、或连降级声明都装不下 → `MODE_REJECT` + 拒发原因。
    """
    digest = compute_notify_digest(text)
    verdict = compute_length_guard(text, limit_bytes=limit_bytes)
    if not verdict.over_limit:
        return NotifyPlan(
            mode=MODE_DIRECT,
            digest=digest,
            body=text,
            full_text=text,
            attachment_filename=None,
            attachment_content=None,
            reject_reason=None,
            byte_length=verdict.byte_length,
            limit_bytes=limit_bytes,
        )
    if not attachment_supported:
        return _reject(text, digest=digest, verdict=verdict, reason=REJECT_NO_ATTACHMENT_CHANNEL)

    filename = ATTACHMENT_FILENAME_TEMPLATE.format(digest=digest)
    notice = SUMMARY_NOTICE_TEMPLATE.format(filename=filename, byte_length=verdict.byte_length)
    budget = limit_bytes - compute_byte_length(notice)
    if budget <= 0:
        return _reject(text, digest=digest, verdict=verdict, reason=REJECT_SUMMARY_STILL_OVER)

    summary = notice + compute_text_prefix_within_bytes(text, budget)
    if compute_byte_length(summary) > limit_bytes:
        # 到不了这里（预算是按字节算好的）。留着是因为这条不变式一旦破掉，
        # 后果是"发出一条超限内容"而不是"报个错"，⛔ 不许删。
        return _reject(text, digest=digest, verdict=verdict, reason=REJECT_SUMMARY_STILL_OVER)

    return NotifyPlan(
        mode=MODE_DEGRADED,
        digest=digest,
        body=summary,
        full_text=text,
        attachment_filename=filename,
        attachment_content=text,
        reject_reason=None,
        byte_length=verdict.byte_length,
        limit_bytes=limit_bytes,
    )
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `python -m pytest tools/liaison/tests/test_notify_guard.py -q`
Expected: PASS（15 passed）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/notify/__init__.py tools/liaison/notify/guard.py tools/liaison/tests/test_notify_guard.py
git commit -m "feat(liaison): 群通知长度守卫与降级决策，两条通道阈值结构性不共用（6.2/6.3/6.8）"
```

---

### Task 2: 令牌桶主动节流与退避序列——时钟全部注入，测试 ⛔ 不 sleep 真时间

**Files:**
- Create: `tools/liaison/notify/ratelimit.py`
- Test: `tools/liaison/tests/test_notify_ratelimit.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - 常量 `GROUP_WEBHOOK_BUCKET_CAPACITY=20`、`GROUP_WEBHOOK_REFILL_TOKENS=20`、`GROUP_WEBHOOK_REFILL_SECONDS=60.0`
  - 常量 `RATE_LIMIT_ERRCODE=45009`、`BACKOFF_DELAYS_SECONDS=(1.0, 2.0, 4.0, 8.0)`、`MAX_RETRIES=4`
  - `compute_refill_tokens(elapsed_seconds: float, *, refill_tokens: int, refill_seconds: float) -> float`
  - `compute_backoff_delay(retry_index: int) -> float`（`retry_index` 从 1 起）
  - `TokenBucket(*, capacity, refill_tokens, refill_seconds, monotonic, sleep)`，方法 `acquire() -> float`
  - `make_group_webhook_bucket(*, monotonic, sleep) -> TokenBucket`

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_notify_ratelimit.py`：

```python
"""第 6 章·主动节流与退避序列（6.4 / 6.5 的时序部分）。

**本文件不 sleep 真时间。** 假时钟自己往前走——这不是为了跑得快，是为了让
"20 条/分钟"这条判据**能被验**：靠真等一分钟的断言最终都会被人跳过。
"""

from __future__ import annotations

import pytest

from tools.liaison.notify import ratelimit


class FakeClock:
    """单调钟 + sleep 的假体。`sleep` 直接把钟推到未来，⛔ 不真等。"""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, f"⛔ 不许睡负数：{seconds}"
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def bucket(clock):
    return ratelimit.make_group_webhook_bucket(monotonic=clock.monotonic, sleep=clock.sleep)


def test_backoff_sequence_is_exactly_1_2_4_8(clock):
    """D9 逐字：1s→2s→4s→8s，最多 4 次。"""
    assert [ratelimit.compute_backoff_delay(i) for i in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]
    assert ratelimit.MAX_RETRIES == 4


@pytest.mark.parametrize("bad", [0, -1, 5])
def test_backoff_index_outside_the_sequence_is_an_error(bad):
    """⛔ 不许"越界就返回最后一档"——那会把"最多 4 次"悄悄变成无限次。"""
    with pytest.raises(ValueError):
        ratelimit.compute_backoff_delay(bad)


def test_normal_rate_is_not_delayed(bucket, clock):
    """spec 场景「正常速率不受影响」：远低于上限时立即发送，无额外延迟。"""
    waits = [bucket.acquire() for _ in range(5)]
    assert waits == [0.0] * 5
    assert clock.slept == []


def test_a_full_bucket_lets_the_capacity_through_without_waiting(bucket):
    """容量 20（D9 逐字）：桶满时前 20 条不等待。"""
    waits = [bucket.acquire() for _ in range(ratelimit.GROUP_WEBHOOK_BUCKET_CAPACITY)]
    assert waits == [0.0] * 20


def test_the_twenty_first_send_waits_for_a_refilled_token(bucket, clock):
    """spec 场景「短时间内大量通知」：超出部分被**延后**，⛔ 不是被拒。"""
    for _ in range(ratelimit.GROUP_WEBHOOK_BUCKET_CAPACITY):
        bucket.acquire()
    waited = bucket.acquire()
    assert waited == pytest.approx(3.0)  # 60s / 20 个令牌
    assert clock.slept == [pytest.approx(3.0)]


def test_sustained_rate_never_exceeds_twenty_per_minute(bucket, clock):
    """稳态速率判据：突发窗口之后，相邻两条的间隔恒 ≥ 3 秒（= 20 条/分钟）。

    ⚠️ 口径说明：令牌桶**容量 20** 是 D9 逐字要求的，因此第一个窗口允许一次
    20 条的突发。这条断言因此从第 21 条开始验——⛔ 不要把它改成"任意 60 秒窗口
    内不超过 20 条"，那条更严的性质本实现按设计就不成立，写上去只会让人为了
    让它变绿去改容量，而容量是 design 定的。
    """
    sent_at: list[float] = []
    for _ in range(40):
        bucket.acquire()
        sent_at.append(clock.now)
    tail = sent_at[ratelimit.GROUP_WEBHOOK_BUCKET_CAPACITY :]
    gaps = [b - a for a, b in zip(tail, tail[1:])]
    assert gaps, "样本不足，断言没验到东西"
    assert all(gap >= 3.0 - 1e-9 for gap in gaps), gaps


def test_tokens_refill_over_time(clock):
    """闲一会儿就把令牌补回来，⛔ 不会补过容量上限。"""
    b = ratelimit.make_group_webhook_bucket(monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(20):
        b.acquire()
    clock.now += 30.0  # 30 秒补 10 个
    assert [b.acquire() for _ in range(10)] == [0.0] * 10
    assert b.acquire() > 0.0

    clock.now += 3600.0  # 闲很久
    assert [b.acquire() for _ in range(20)] == [0.0] * 20  # 最多补满 20 个
    assert b.acquire() > 0.0


def test_refill_math_is_a_pure_function():
    assert ratelimit.compute_refill_tokens(60.0, refill_tokens=20, refill_seconds=60.0) == 20.0
    assert ratelimit.compute_refill_tokens(3.0, refill_tokens=20, refill_seconds=60.0) == 1.0
    with pytest.raises(ValueError):
        ratelimit.compute_refill_tokens(-1.0, refill_tokens=20, refill_seconds=60.0)


def test_ratelimit_module_does_not_import_time():
    """结构性：时钟必须注入。模块自己 import time ⇒ "不 sleep 真时间"守不住。"""
    import ast
    import pathlib

    source = pathlib.Path(ratelimit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "time" not in imported, "⛔ ratelimit.py 不许 import time，时钟只能注入"
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_notify_ratelimit.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.notify.ratelimit'`

- [ ] **Step 3: 写实现**

创建 `tools/liaison/notify/ratelimit.py`：

```python
"""主动节流（令牌桶）与限流退避序列。

⛔ **本模块不 import `time`。** `monotonic` 与 `sleep` 全部构造时注入——理由不是
"方便测试"，是**测试必须不 sleep 真时间**（opener 约束 2）。真时钟一旦写死在模块
里，"20 条/分钟"这条判据就只能靠真等一分钟来验，于是它会被跳过，于是它形同虚设。

D9 逐字：令牌桶容量 20、按 20/60s 补充；收到 `errcode 45009` 时退避
1s→2s→4s→8s，最多 4 次。

⛔ 本目录禁止写 `with X:`（第 2 章事务扫描器），本模块用不到。
"""

from __future__ import annotations

from collections.abc import Callable

#: D9 逐字：企微群机器人 20 条/分钟。容量与补充速率是**两个**参数，
#: ⛔ 不要合并成"每 3 秒一个"——容量决定允许多大的突发，那是另一件事。
GROUP_WEBHOOK_BUCKET_CAPACITY = 20
GROUP_WEBHOOK_REFILL_TOKENS = 20
GROUP_WEBHOOK_REFILL_SECONDS = 60.0

#: 企微在超限时返回的 errcode（D9 逐字）。**只有这一个码触发退避重试**——
#: 其余错误码一律按"非限流错误"处理：记录、告警、⛔ 不重试、⛔ 不当作成功。
RATE_LIMIT_ERRCODE = 45009

#: D9 逐字的退避序列。⛔ 不许改成"指数计算出来"的形式——写死四个数，
#: 是为了让"最多 4 次"这件事在类型上就成立（序列长度即上限）。
BACKOFF_DELAYS_SECONDS = (1.0, 2.0, 4.0, 8.0)
MAX_RETRIES = len(BACKOFF_DELAYS_SECONDS)


def compute_backoff_delay(retry_index: int) -> float:
    """第 `retry_index` 次重试之前要等多久。`retry_index` **从 1 起**。

    ⚠️ 口径钉死：**首发不算重试**。"最多 4 次"指首发之后最多再发 4 次，退避序列
    1s→2s→4s→8s 各用一次，因此一条通知最多被发 5 次。
    ⛔ 不要改成"总共 4 次"——那会让 8s 这一档永远用不上，等于悄悄把退避上限砍掉一半。
    """
    if retry_index < 1 or retry_index > MAX_RETRIES:
        raise ValueError(f"重试序号超出 1..{MAX_RETRIES} 的范围：{retry_index}")
    return BACKOFF_DELAYS_SECONDS[retry_index - 1]


def compute_refill_tokens(
    elapsed_seconds: float, *, refill_tokens: int, refill_seconds: float
) -> float:
    """经过 `elapsed_seconds` 秒应当补充多少令牌。纯函数（铁律 2）。"""
    if elapsed_seconds < 0:
        raise ValueError(f"经过的时间不可能是负数：{elapsed_seconds}")
    if refill_seconds <= 0:
        raise ValueError(f"补充周期必须为正：{refill_seconds}")
    return elapsed_seconds * refill_tokens / refill_seconds


class TokenBucket:
    """发送前生效的主动节流。

    spec 逐字：节流 SHALL 在发送前生效，MUST NOT 依赖"先发出去、被拒了再说"。
    因此 `acquire()` 是**阻塞**语义（拿不到就等），⛔ 不是"拿不到就返回 False 让
    调用方决定"——把决定权交出去，第一个图省事的调用方就会直接发。
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_tokens: int,
        refill_seconds: float,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"桶容量必须为正：{capacity}")
        self._capacity = float(capacity)
        self._refill_tokens = refill_tokens
        self._refill_seconds = refill_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._tokens = float(capacity)
        self._updated_at = monotonic()

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = now - self._updated_at
        if elapsed <= 0:
            # 单调钟不倒流；相等时无事可做。⛔ 不要在这里 raise——
            # 某些平台上连续两次 monotonic() 返回同一个值是合法的。
            return
        gained = compute_refill_tokens(
            elapsed,
            # gitleaks:allow —— `<含 token 的关键字>=<值>` 命中 gitleaks 的
            # generic-api-key 规则（熵 3.52，阈值 3.5），这里是个关键字实参，
            # ⛔ 不是凭据。写这条豁免而不是把参数改名，是因为 `refill_tokens`
            # 是 D9 里"按 20/60s 补充"的直译，为了绕扫描器改名会让代码与 design 对不上。
            refill_tokens=self._refill_tokens,
            refill_seconds=self._refill_seconds,
        )
        self._tokens = min(self._capacity, self._tokens + gained)
        self._updated_at = now

    def acquire(self) -> float:
        """取一个令牌，返回**实际等待的秒数**（不需要等就是 0.0）。"""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        deficit = 1.0 - self._tokens
        wait = deficit * self._refill_seconds / self._refill_tokens
        self._sleep(wait)
        self._refill()
        # 睡够了就一定扣得动。⛔ 不要写成 `while self._tokens < 1: sleep(...)`：
        # 注入的假时钟如果不前进，那种写法会死循环，而死循环在无人值守的
        # 泳道里表现为"这一批永远不结束"，比一次断言失败难查得多。
        self._tokens = max(0.0, self._tokens - 1.0)
        return wait


def make_group_webhook_bucket(
    *, monotonic: Callable[[], float], sleep: Callable[[float], None]
) -> TokenBucket:
    """按 D9 的群 webhook 参数造一个桶。⛔ 参数写死在这里，不做成可配。"""
    return TokenBucket(
        capacity=GROUP_WEBHOOK_BUCKET_CAPACITY,
        refill_tokens=GROUP_WEBHOOK_REFILL_TOKENS,
        refill_seconds=GROUP_WEBHOOK_REFILL_SECONDS,
        monotonic=monotonic,
        sleep=sleep,
    )
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `python -m pytest tools/liaison/tests/test_notify_ratelimit.py -q`
Expected: PASS（12 passed）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/notify/ratelimit.py tools/liaison/tests/test_notify_ratelimit.py
git commit -m "feat(liaison): 群通知令牌桶主动节流与 1/2/4/8 退避序列，时钟全注入（6.4/6.5）"
```

---

### Task 3: 传输封装与凭据——地址只从环境变量来，缺了就拒发并报变量名

**Files:**
- Create: `tools/liaison/notify/transport.py`
- Modify: `tools/liaison/config.py`（**只追加**一个常量与一个函数，⛔ 不改 `REQUIRED_CREDENTIAL_ENV_NAMES`）
- Modify: `.env.example`（**只加一行** `HR_LIAISON_GROUP_WEBHOOK=`，⛔ 不写值）
- Modify: `tools/liaison/tests/test_liaison_no_secrets_in_vcs.py`（只扩扫描面）
- Test: `tools/liaison/tests/test_notify_transport.py`

**Interfaces:**
- Consumes: 无（本任务不依赖 Task 1/2）
- Produces:
  - `WEBHOOK_TIMEOUT_SECONDS: float = 5.0`
  - `WebhookTransportError(RuntimeError)`
  - `WebhookResponse(errcode: int, errmsg: str, payload: dict)`，属性 `ok -> bool`
  - `parse_webhook_response(raw: bytes, *, status: int) -> WebhookResponse`（纯函数）
  - `compute_multipart_body(*, boundary: str, filename: str, content: str) -> bytes`（纯函数）
  - `Transport(Protocol)`：`post_json(url, payload, *, timeout)` / `post_multipart(url, *, filename, content, timeout)`
  - `UrllibTransport`（实现上述 Protocol，本服务唯一真发 HTTP 的类）
  - `tools.liaison.config.GROUP_WEBHOOK_ENV = "HR_LIAISON_GROUP_WEBHOOK"`
  - `tools.liaison.config.load_group_webhook(env: Mapping[str, str] | None = None) -> str`

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_notify_transport.py`：

```python
"""第 6 章·传输封装与凭据（6.1 / 6.10）。

**不发任何真网络**：`urlopen` 一律 monkeypatch 掉。真实投递是 8.6，由
Shao Peishen 亲自做（对外通道开关属"不可代"项）。

测试里的地址一律用 `https://example.invalid/...` 占位——`.invalid` 是 RFC 2606
保留的顶级域，就算哪天真被误发也一定解析失败。
"""

from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request

import pytest

from tools.liaison import config
from tools.liaison.errors import MissingCredentialsError
from tools.liaison.notify import transport

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

FAKE_WEBHOOK = "https://example.invalid/cgi-bin/webhook/send?key=fake-key-for-tests"


class FakeResponse:
    """`urlopen` 的返回值假体：只提供本实现真正用到的三样东西。"""

    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


# ── parse_webhook_response：纯函数，errcode 检查（6.1） ──────────────────


def test_errcode_zero_is_success():
    resp = transport.parse_webhook_response(b'{"errcode":0,"errmsg":"ok"}', status=200)
    assert resp.ok is True
    assert resp.errcode == 0


def test_rate_limit_errcode_is_returned_not_raised():
    """45009 是"要退避重试"的信号，⛔ 不是传输层异常——退避决策在上层。"""
    resp = transport.parse_webhook_response(b'{"errcode":45009,"errmsg":"limit"}', status=200)
    assert resp.ok is False
    assert resp.errcode == 45009


def test_missing_errcode_is_never_treated_as_success():
    """spec 场景「非限流错误」的底座：HTTP 200 但没有 errcode ⛔ 不许算成功。"""
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b'{"ok":true}', status=200)


def test_non_200_status_is_an_error():
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b"oops", status=502)


def test_non_json_body_is_an_error():
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b"<html>502</html>", status=200)


def test_non_integer_errcode_is_an_error():
    with pytest.raises(transport.WebhookTransportError):
        transport.parse_webhook_response(b'{"errcode":"0"}', status=200)


# ── UrllibTransport：唯一真发 HTTP 的类（这里把 urlopen 换掉） ──────────


def test_post_json_sends_utf8_json_with_a_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        captured["content_type"] = request.get_header("Content-type")
        return FakeResponse(b'{"errcode":0,"errmsg":"ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    resp = transport.UrllibTransport().post_json(
        FAKE_WEBHOOK, {"msgtype": "markdown", "markdown": {"content": "中文"}}
    )
    assert resp.ok is True
    assert captured["url"] == FAKE_WEBHOOK
    assert captured["timeout"] == transport.WEBHOOK_TIMEOUT_SECONDS
    assert "application/json" in captured["content_type"]
    # ensure_ascii=False：中文原样进 body，⛔ 不转义成 \uXXXX（省一半字节，
    # 而字节数正是长度守卫判定的对象）
    assert "中文".encode("utf-8") in captured["data"]
    assert json.loads(captured["data"].decode("utf-8"))["msgtype"] == "markdown"


def test_response_is_always_closed_even_when_parsing_fails(monkeypatch):
    """⛔ 本目录不许写 `with urlopen(...)`，所以关闭必须靠 try/finally——

    这条断言就是那个 finally 的判据。
    """
    holder = {}

    def fake_urlopen(request, timeout=None):
        holder["response"] = FakeResponse(b"not json")
        return holder["response"]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(transport.WebhookTransportError):
        transport.UrllibTransport().post_json(FAKE_WEBHOOK, {})
    assert holder["response"].closed is True


@pytest.mark.parametrize(
    "raised",
    [
        urllib.error.HTTPError(FAKE_WEBHOOK, 500, "boom", {}, None),
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
    ],
)
def test_transport_errors_never_leak_the_webhook_url(monkeypatch, raised):
    """群 webhook 的 URL **本身就是凭据**（`?key=` 那一段）。

    报错会被贴进聊天、日志与 issue——排障路径恰恰是最不设防的那条。
    """

    def fake_urlopen(request, timeout=None):
        raise raised

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(transport.WebhookTransportError) as excinfo:
        transport.UrllibTransport().post_json(FAKE_WEBHOOK, {})
    assert "fake-key-for-tests" not in str(excinfo.value)
    assert FAKE_WEBHOOK not in str(excinfo.value)
    # 链上也不许挂着带 URL 的原异常
    assert excinfo.value.__cause__ is None


def test_multipart_body_carries_the_filename_and_the_content():
    body = transport.compute_multipart_body(
        boundary="BOUND", filename="liaison-notify-abc.md", content="完整正文"
    )
    text = body.decode("utf-8")
    assert "--BOUND" in text
    assert 'name="media"' in text
    assert 'filename="liaison-notify-abc.md"' in text
    assert "完整正文" in text
    assert text.endswith("--BOUND--\r\n")


def test_post_multipart_uses_the_multipart_content_type(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["content_type"] = request.get_header("Content-type")
        captured["data"] = request.data
        return FakeResponse(b'{"errcode":0,"errmsg":"ok","media_id":"MID"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    resp = transport.UrllibTransport().post_multipart(
        FAKE_WEBHOOK, filename="a.md", content="x"
    )
    assert resp.payload["media_id"] == "MID"
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    boundary = captured["content_type"].split("boundary=", 1)[1]
    assert boundary.encode("ascii") in captured["data"]


# ── 凭据：只从环境读，缺了就拒发并报变量名（6.10） ──────────────────────


def test_group_webhook_is_read_from_the_environment():
    assert config.load_group_webhook({config.GROUP_WEBHOOK_ENV: f"  {FAKE_WEBHOOK}  "}) == FAKE_WEBHOOK


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_missing_or_blank_webhook_is_refused_by_variable_name(value):
    """6.10 逐字：未配置 → 拒发并报告缺失的变量名，⛔ 不静默跳过后报成功。"""
    env = {} if value is None else {config.GROUP_WEBHOOK_ENV: value}
    with pytest.raises(MissingCredentialsError) as excinfo:
        config.load_group_webhook(env)
    assert config.GROUP_WEBHOOK_ENV in str(excinfo.value)
    assert excinfo.value.missing_names == (config.GROUP_WEBHOOK_ENV,)


def test_error_message_reports_the_name_never_the_value():
    with pytest.raises(MissingCredentialsError) as excinfo:
        config.load_group_webhook({config.GROUP_WEBHOOK_ENV: "   "})
    assert "   " not in str(excinfo.value).replace(config.GROUP_WEBHOOK_ENV, "")


def test_group_webhook_is_not_part_of_startup_fail_closed():
    """结构性：⛔ 群 webhook 不进启动期必备清单。

    收消息与发群通知是两件独立的事。把它加进 REQUIRED_CREDENTIAL_ENV_NAMES，
    等于"没配群通知就整个值守通道起不来"——那不是 fail-closed，那是连坐。
    """
    assert config.GROUP_WEBHOOK_ENV not in config.REQUIRED_CREDENTIAL_ENV_NAMES


def test_env_example_declares_only_the_variable_name():
    """spec 场景「配置只记变量名」：受版本管理的配置里只有变量名，没有真实地址。"""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"{config.GROUP_WEBHOOK_ENV}=\n" in text or text.rstrip().endswith(
        f"{config.GROUP_WEBHOOK_ENV}="
    )
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_notify_transport.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.notify.transport'`

- [ ] **Step 3: 写传输层实现**

创建 `tools/liaison/notify/transport.py`：

```python
"""HTTP 传输层——**本服务唯一真发网络的地方**。

⛔ **本目录禁止写 `with X:`。** 第 2 章的事务扫描器把 `with <名字|属性>:` 无条件
判为隐式提交违规；`with <调用>:` 只对一份正面白名单（`open` / `os.fdopen` /
`io.open` / `suppress` / `NamedTemporaryFile` / `TemporaryDirectory` 与
`contextlib.*` / `tempfile.*` 两个模块族）放行，**陌生被调用者一律判违规**——
`urllib.request.urlopen` 正是陌生的那种。响应因此用 `resp = ...` +
`try/finally: resp.close()`，⛔ 不写 `with urllib.request.urlopen(...) as resp:`，
也 ⛔ 不写"先赋值再 `with 变量`"——`ast.Name` 同样在判违规之列。

⛔ **报错文本里绝不许出现 URL。** 群 webhook 的 URL 的 `?key=` 那一段**本身就是
凭据**，而报错会被贴进聊天、日志与 issue——排障路径恰恰是最不设防的那条。
`urllib` 的 `HTTPError` 把 URL 挂在自己身上，所以异常一律 `raise ... from None`
（⛔ 不是 `from exc`），只把类型名与 reason 转述出来。
tests/test_notify_transport.py::test_transport_errors_never_leak_the_webhook_url 守着这条。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Protocol

#: 出站超时。⛔ 不许不带超时地调 `urlopen`——没有超时的 HTTP 调用会让整条值守
#: 通道挂在一个永远不回的连接上，而"卡住"不报错、没有症状。
WEBHOOK_TIMEOUT_SECONDS = 5.0

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


class WebhookTransportError(RuntimeError):
    """网络层或协议层失败：连不上、超时、非 200、响应不是合法 JSON、没有 errcode。

    ⛔ 构造消息时不许把 URL 拼进去（见模块 docstring）。
    """


@dataclass(frozen=True)
class WebhookResponse:
    """一次 webhook 调用的结果。**`errcode` 是唯一的成败判据**。"""

    errcode: int
    errmsg: str
    payload: dict

    @property
    def ok(self) -> bool:
        return self.errcode == 0


class Transport(Protocol):
    """传输层的形状。**以参数注入**，单测全部用 fake（opener 约束 2）。"""

    def post_json(
        self, url: str, payload: dict, *, timeout: float = WEBHOOK_TIMEOUT_SECONDS
    ) -> WebhookResponse: ...

    def post_multipart(
        self,
        url: str,
        *,
        filename: str,
        content: str,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    ) -> WebhookResponse: ...


def parse_webhook_response(raw: bytes, *, status: int) -> WebhookResponse:
    """把原始响应变成 `WebhookResponse`。**纯函数**（铁律 2）。

    ⛔ **HTTP 200 但没有 `errcode` 的响应绝不许算成功。** 企微的错误是靠 body 里的
    `errcode` 表达的，只看 HTTP 状态码等于把所有业务错误当成功——spec 的「非限流
    错误不被当作成功」在这一层就是这一条。
    """
    if status != 200:
        raise WebhookTransportError(f"群通知 webhook 返回 HTTP {status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookTransportError(
            f"群通知 webhook 的响应不是合法 JSON（{exc.__class__.__name__}），⛔ 不当作成功"
        ) from None
    if not isinstance(payload, dict) or "errcode" not in payload:
        raise WebhookTransportError("群通知 webhook 的响应里没有 errcode 字段，⛔ 不当作成功")
    errcode = payload["errcode"]
    # bool 是 int 的子类：`{"errcode": true}` 会被 isinstance(_, int) 放行，而那
    # 显然不是一个错误码。两条分开写，⛔ 不要合并成一个表达式。
    if isinstance(errcode, bool) or not isinstance(errcode, int):
        raise WebhookTransportError(
            f"群通知 webhook 的 errcode 不是整数（{type(errcode).__name__}），⛔ 不当作成功"
        )
    return WebhookResponse(
        errcode=errcode, errmsg=str(payload.get("errmsg", "")), payload=payload
    )


def compute_multipart_body(*, boundary: str, filename: str, content: str) -> bytes:
    """拼一个只含单个文件字段的 multipart/form-data body。**纯函数**。

    ⛔ 不引入 `requests` / `urllib3` 之类的第三方库：design D10 的依赖隔离要求
    `tools/liaison/` 的依赖清单自己承担约束，而这段拼装只有二十行。
    """
    lines = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n"
        "\r\n"
    )
    tail = f"\r\n--{boundary}--\r\n"
    return lines.encode("utf-8") + content.encode("utf-8") + tail.encode("utf-8")


class UrllibTransport:
    """标准库实现。**本服务唯一真发 HTTP 的类。**"""

    def post_json(
        self, url: str, payload: dict, *, timeout: float = WEBHOOK_TIMEOUT_SECONDS
    ) -> WebhookResponse:
        # ensure_ascii=False：中文原样进 body，⛔ 不转义成 \uXXXX。
        # 不是审美问题——转义后字节数翻倍，而字节数正是长度守卫判定的对象。
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": _JSON_CONTENT_TYPE}
        )
        return self._read(request, timeout=timeout)

    def post_multipart(
        self,
        url: str,
        *,
        filename: str,
        content: str,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    ) -> WebhookResponse:
        boundary = f"----liaison{uuid.uuid4().hex}"
        body = compute_multipart_body(boundary=boundary, filename=filename, content=content)
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return self._read(request, timeout=timeout)

    def _read(self, request: urllib.request.Request, *, timeout: float) -> WebhookResponse:
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # ⛔ from None：HTTPError 把 URL 挂在 .url / .filename 上，
            # 而 URL 里的 key 就是凭据。只转述状态码。
            raise WebhookTransportError(f"群通知 webhook 返回 HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise WebhookTransportError(f"群通知 webhook 连接失败：{exc.reason}") from None
        except TimeoutError:
            raise WebhookTransportError(f"群通知 webhook 超时（{timeout} 秒）") from None
        try:
            status = response.status
            raw = response.read()
        finally:
            # ⛔ 不写 `with`（见模块 docstring）。这个 finally 就是它的替代品，
            # 由 test_response_is_always_closed_even_when_parsing_fails 守着。
            response.close()
        return parse_webhook_response(raw, status=status)
```

- [ ] **Step 4: 追加凭据读取（`config.py`）**

在 `tools/liaison/config.py` 末尾追加（⛔ 不改文件里已有的任何一行，尤其 ⛔ 不动 `REQUIRED_CREDENTIAL_ENV_NAMES`）：

```python
#: 群通知的发送地址（第 6 章）。**URL 本身就是凭据**——`?key=` 那一段即身份。
GROUP_WEBHOOK_ENV = "HR_LIAISON_GROUP_WEBHOOK"


def load_group_webhook(env: Mapping[str, str] | None = None) -> str:
    """读并校验群通知的发送地址。缺失／空串／纯空白一律 raise。

    ⛔ **刻意不加进 `REQUIRED_CREDENTIAL_ENV_NAMES`。** 那个元组是**启动期**
    fail-closed 的清单；收消息与发群通知是两件独立的事，把它加进去等于
    "没配群通知就整个值守通道起不来"——那不是 fail-closed，那是连坐。
    本函数在**发送时**校验：6.10 逐字要求"未配置 → 拒发并报告缺失的变量名，
    ⛔ 不静默跳过后报成功"，拒发的前提是先真的走到发送这一步。

    复用 `MissingCredentialsError`：它的消息里 ⛔ 只出现变量名、不出现取值。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    if _is_blank(source, GROUP_WEBHOOK_ENV):
        raise MissingCredentialsError([GROUP_WEBHOOK_ENV])
    return source[GROUP_WEBHOOK_ENV].strip()
```

- [ ] **Step 5: 追加 `.env.example` 的变量名占位（⛔ 不写值）**

在 `.env.example` 末尾（`HR_LIAISON_BOT_SECRET=` 那一行之后）追加：

```
# 群通知的发送地址（第 6 章 `liaison-group-notify`）。
# ⛔ 这里只写变量名，**绝不写任何真实地址**——URL 里的 key 段本身就是凭据，
#    而本文件会被 sync-to-server.sh 推到 .51。真实地址只落仓库根的 .env。
#
# 与上面两项不同，本项**不参与启动期 fail-closed**：没配它服务照常起、照常收
# 消息，只是发群通知时当场拒发并报出这个变量名（6.10）。
#
# 真实地址的填写与灰度真发是 8.6，属"候选人对外通道/生产发版"之外的内部通道，
# 但仍由 Shao Peishen 本人操作——本章代码只到"给了 URL 就能发"为止。
HR_LIAISON_GROUP_WEBHOOK=
```

- [ ] **Step 6: 把新变量名纳入秘密扫描面**

修改 `tools/liaison/tests/test_liaison_no_secrets_in_vcs.py`：

把 `CREDENTIAL_ENV_NAMES` 改成：

```python
CREDENTIAL_ENV_NAMES = (
    "HR_LIAISON_BOT_ID",
    "HR_LIAISON_BOT_SECRET",
    # 第 6 章（群通知外发）真正落地时定的变量名是 HR_LIAISON_GROUP_WEBHOOK。
    # ⚠️ 带 _URL 后缀的旧名同时留在扫描面里，⛔ 不要"顺手清掉"——第 2 章预留时
    # 用的是它，草稿与历史文档里可能已经写下过那个名字，删掉等于把那些行放生。
    # ⚠️ 顺序有讲究：长名在前。正则是最左匹配，短名在前时
    # `HR_LIAISON_GROUP_WEBHOOK_URL=x` 要靠回溯才匹得上——匹得上，但读起来像 bug。
    "HR_LIAISON_GROUP_WEBHOOK_URL",
    "HR_LIAISON_GROUP_WEBHOOK",
)
```

把文件末尾那条参数化改成：

```python
@pytest.mark.parametrize(
    "name",
    ("HR_LIAISON_BOT_ID", "HR_LIAISON_BOT_SECRET", "HR_LIAISON_GROUP_WEBHOOK"),
)
def test_env_example_declares_the_placeholder_with_empty_value(name):
```

⛔ **不要把 `HR_LIAISON_GROUP_WEBHOOK_URL` 也加进这条参数化**：`.env.example` 里没有它，也不该有它——它只是扫描面上的一个历史别名。

- [ ] **Step 7: 跑测试确认全绿**

Run: `python -m pytest tools/liaison/tests/test_notify_transport.py tools/liaison/tests/test_liaison_no_secrets_in_vcs.py tools/liaison/tests/test_liaison_credentials.py -q`
Expected: PASS（全绿；`test_liaison_credentials.py` 必须**一条都不能变红**——它守的是第 1 章的启动期 fail-closed，本任务只追加不修改）

- [ ] **Step 8: 提交**

```bash
git add tools/liaison/notify/transport.py tools/liaison/config.py .env.example \
        tools/liaison/tests/test_notify_transport.py tools/liaison/tests/test_liaison_no_secrets_in_vcs.py
git commit -m "feat(liaison): 群通知 webhook 传输封装与凭据读取，地址只从环境变量来（6.1/6.10）"
```

---

### Task 4: 通知台账表与幂等落库——一次通知恰好一行，三种终局同一张表

**Files:**
- Modify: `tools/liaison/storage/schema.py`（**只追加** `GROUP_NOTIFY_SCHEMA` 并拼进 `SCHEMA`，⛔ 不改已有表）
- Modify: `tools/liaison/alerts.py`（**只追加** `effect_emit_alert`，并让 `effect_emit_outage_alert` 委托它）
- Create: `tools/liaison/notify/store.py`（本任务只写"记"的部分，"发"在 Task 5 接上）
- Test: `tools/liaison/tests/test_notify_store.py`

**Interfaces:**
- Consumes: Task 1 的 `NotifyPlan` / `MODE_*`
- Produces:
  - `schema.GROUP_NOTIFY_SCHEMA`（表 `liaison_group_notify`）
  - `alerts.effect_emit_alert(sink: AlertSink, text: str) -> bool`（⛔ 永不抛异常）
  - `store.GROUP_NOTIFY_THREAD_ID = "__liaison_group_notify__"`
  - `store.STATE_SENT="sent"` / `STATE_PENDING_RESEND="pending_resend"` / `STATE_REJECTED="rejected"`
  - `store.NotifyRecord(state, attempts, last_errcode, last_error)`
  - `store.effect_send_group_notify(conn, *, thread_id, business_key, plan, deliver, alert_sink, ...)`（Task 5 补齐 `deliver`；本任务先以可注入的 `deliver` 桩打通落库路径）
  - `store.select_pending_resends(conn) -> list[sqlite3.Row]`
  - `store.compute_alert_text(plan, record) -> str`（纯函数）

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_notify_store.py`：

```python
"""第 6 章·通知台账与幂等落库（6.6 / 6.9 的落库部分）。

这个 effect 不在 EFFECT_NODE_TO_TABLE 里（本章 ⛔ 不碰 storage/effects.py，理由见
本章计划 Architecture 第 5 条），因此第 2 章的 assert_effect_log_identity 覆盖不到
它。恒等判据由本文件的 assert_group_notify_identity 自带——⛔ 不要因为"别处已经
有一条了"就省掉它。
"""

from __future__ import annotations

import sqlite3

import pytest

from tools.liaison import alerts
from tools.liaison.notify import guard, store
from tools.liaison.storage import db as liaison_db


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class BoomSink:
    def send(self, text: str) -> None:
        raise RuntimeError("群通知本身也挂了")


def assert_group_notify_identity(conn: sqlite3.Connection) -> None:
    """铁律 1 的恒等判据，本章版本。

    `effect_send_group_notify` 是本章唯一的 INSERT 型 effect，因此这条等式只比它。
    ⛔ 不许改成总数比较、⛔ 不许约等于——等式一旦松动，"发了没记 / 记了没发"
    这两种状态就再也没有机器判据。
    """
    effect_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM effect_log WHERE node_name = ? GROUP BY thread_id",
            ("effect_send_group_notify",),
        ).fetchall()
    )
    business_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM liaison_group_notify GROUP BY thread_id"
        ).fetchall()
    )
    assert effect_counts == business_counts, (
        f"群通知恒等不变式破裂：effect_log={effect_counts} 表={business_counts}"
    )


def make_plan(text="值守通知", *, limit=4096, attachment=True):
    return guard.compute_notify_plan(text, limit_bytes=limit, attachment_supported=attachment)


def stub_deliver(outcome_state, *, attempts=1, errcode=None, error=None):
    """把"发"这一步换成一个桩：本任务只验落库，真投递在 Task 5。"""

    def deliver(plan):
        return store.NotifyRecord(
            state=outcome_state, attempts=attempts, last_errcode=errcode, last_error=error
        )

    return deliver


def test_a_sent_notify_writes_exactly_one_row_and_one_effect_log(conn):
    plan = make_plan()
    sink = RecordingSink()
    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_SENT),
        alert_sink=sink,
    )
    assert state == store.STATE_SENT
    rows = conn.execute(
        "SELECT digest, state, mode, attempts, sent_at IS NOT NULL FROM liaison_group_notify"
    ).fetchall()
    assert rows == [(plan.digest, store.STATE_SENT, guard.MODE_DIRECT, 1, 1)]
    assert sink.texts == []  # 成功不告警
    assert_group_notify_identity(conn)


def test_the_same_content_is_never_sent_twice(conn):
    """6.6 逐字：幂等键含内容摘要，重试与恢复重跑 ⛔ 不产生第二条通知。"""
    plan = make_plan()
    sent_count = {"n": 0}

    def counting_deliver(p):
        sent_count["n"] += 1
        return store.NotifyRecord(state=store.STATE_SENT, attempts=1, last_errcode=None, last_error=None)

    kwargs = dict(
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=counting_deliver,
        alert_sink=RecordingSink(),
    )
    assert store.effect_send_group_notify(conn, **kwargs) == store.STATE_SENT
    assert store.effect_send_group_notify(conn, **kwargs) is None  # 幂等命中：装饰器返回 None
    assert sent_count["n"] == 1, "第二次调用 ⛔ 不许再发一遍"
    assert conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 1
    assert_group_notify_identity(conn)


def test_effect_key_is_thread_node_digest(conn):
    """幂等键形态逐字：{thread_id}:effect_send_group_notify:{内容摘要}。"""
    plan = make_plan()
    store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_SENT),
        alert_sink=RecordingSink(),
    )
    key = conn.execute("SELECT effect_key FROM effect_log").fetchone()[0]
    assert key == f"{store.GROUP_NOTIFY_THREAD_ID}:effect_send_group_notify:{plan.digest}"


def test_exhausted_retries_persist_as_pending_resend_and_alert(conn):
    """spec 场景「重试耗尽」：持久化为待重发记录 **且** 发出告警，⛔ 不静默丢弃。"""
    plan = make_plan()
    sink = RecordingSink()
    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_PENDING_RESEND, attempts=5, errcode=45009, error="limit"),
        alert_sink=sink,
    )
    assert state == store.STATE_PENDING_RESEND
    row = conn.execute(
        "SELECT state, attempts, last_errcode, sent_at, body FROM liaison_group_notify"
    ).fetchone()
    assert row[0] == store.STATE_PENDING_RESEND
    assert row[1] == 5
    assert row[2] == 45009
    assert row[3] is None
    assert row[4] == plan.full_text, "待重发行必须存**完整原文**，⛔ 不是提要"
    assert len(sink.texts) == 1
    assert "45009" in sink.texts[0]
    assert_group_notify_identity(conn)


def test_rejected_notify_is_recorded_and_alerted_and_never_sent(conn):
    """spec 场景「无法降级时拒发」：不发送 + 告警说明拒发原因。"""
    plan = make_plan("中" * 2000, limit=4096, attachment=False)
    assert plan.mode == guard.MODE_REJECT
    sink = RecordingSink()
    attempted = {"n": 0}

    def never_called(p):
        attempted["n"] += 1
        raise AssertionError("拒发的通知 ⛔ 不许进投递路径")

    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=never_called,
        alert_sink=sink,
    )
    assert state == store.STATE_REJECTED
    assert attempted["n"] == 0
    assert len(sink.texts) == 1
    assert guard.REJECT_NO_ATTACHMENT_CHANNEL in sink.texts[0]
    assert_group_notify_identity(conn)


def test_pending_resends_are_listable(conn):
    """给第 8 章的重发驱动器留的把手。本章 ⏸ 不实现驱动器本身。"""
    for text, state in (("a", store.STATE_SENT), ("b", store.STATE_PENDING_RESEND)):
        plan = make_plan(text)
        store.effect_send_group_notify(
            conn,
            thread_id=store.GROUP_NOTIFY_THREAD_ID,
            business_key=plan.digest,
            plan=plan,
            deliver=stub_deliver(state),
            alert_sink=RecordingSink(),
        )
    rows = store.select_pending_resends(conn)
    assert [r["body"] for r in rows] == ["b"]


def test_alert_channel_failure_does_not_abort_the_record(conn, caplog):
    """opener 约束 6：alerts 通道自身失败只记日志、⛔ 不中止。"""
    plan = make_plan()
    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_PENDING_RESEND, attempts=5, errcode=45009),
        alert_sink=BoomSink(),
    )
    assert state == store.STATE_PENDING_RESEND
    assert conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 1
    assert_group_notify_identity(conn)


def test_state_enum_is_enforced_by_the_table(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_group_notify "
            "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, body) "
            "VALUES ('d','t','group_webhook','半成功','direct',1,4096,'x')"
        )


def test_sent_requires_a_timestamp_and_others_must_not_have_one(conn):
    """「已送达必带时间戳」与「未送达必不带」由一条表约束同时钉住。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_group_notify "
            "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, body, sent_at) "
            "VALUES ('d1','t','group_webhook','sent','direct',1,4096,'x',NULL)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_group_notify "
            "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, body, sent_at) "
            "VALUES ('d2','t','group_webhook','rejected','reject',1,4096,'x','2026-09-09')"
        )


def test_generic_alert_emitter_never_raises():
    """`effect_emit_alert` ⛔ 永不抛异常，返回是否送成功。"""
    assert alerts.effect_emit_alert(RecordingSink(), "hello") is True
    assert alerts.effect_emit_alert(BoomSink(), "hello") is False
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_notify_store.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.notify.store'`

- [ ] **Step 3: 追加建表 DDL**

在 `tools/liaison/storage/schema.py` 里，`OUTAGE_WINDOW_SCHEMA` 之后追加（⛔ 不改上面任何一张表）：

```python
#: 第 6 章·群通知台账。**一次通知恰好一行**，三种终局共用同一张表。
#:
#: ⛔ 不要拆成"成功表 + 待重发表"：拆开之后铁律 1 的恒等式
#: 「`effect_log` 条数 == 业务表行数」就跨了两张表，需要求和才成立；等式一旦需要
#: 求和，下一次有人加第四种终局时它会因为一个完全正当的理由变红，而那时最顺手的
#: "修法"就是削弱等式本身。
#:
#: 主键是**内容摘要**（SHA-256 前 16 位十六进制），同时是幂等键的 business_key
#: ——与第 7 章 liaison_outage_window 用窗口起始时间做主键同一手法：
#: 结构（主键）与机制（idempotent_effect）两道防线守同一件事。
GROUP_NOTIFY_SCHEMA = """
CREATE TABLE IF NOT EXISTS liaison_group_notify (
    digest TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    -- 通道名。两条通道阈值独立（D9），台账里也要能分得出这行是哪条通道发的。
    channel TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('sent', 'pending_resend', 'rejected')),
    mode TEXT NOT NULL
        CHECK (mode IN ('direct', 'degraded', 'reject')),
    byte_length INTEGER NOT NULL,
    limit_bytes INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_errcode INTEGER,
    last_error TEXT,
    -- **完整原文**。⛔ 存的绝不是提要——待重发靠这一列重发，存提要等于把
    -- "降级"变成"丢内容"，而那正是本章要消灭的东西。
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT,
    -- 「已送达必带时间戳」与「未送达必不带时间戳」一条等式同时钉住。
    -- 写成等式而不是两条 CHECK：两个方向必须同时成立，拆开写容易只加一半。
    CHECK ((state = 'sent') = (sent_at IS NOT NULL))
);

-- 第 8 章的重发驱动器要找 state='pending_resend' 的行。
CREATE INDEX IF NOT EXISTS idx_liaison_group_notify_state
    ON liaison_group_notify (state, created_at);
"""
```

并把文件最后一行的 `SCHEMA` 改成：

```python
#: 本服务的全量 DDL。
SCHEMA = (
    EFFECT_LOG_SCHEMA + MESSAGE_AND_TASK_SCHEMA + OUTAGE_WINDOW_SCHEMA + GROUP_NOTIFY_SCHEMA
)
```

- [ ] **Step 4: 把告警出口泛化（`alerts.py`）**

在 `tools/liaison/alerts.py` 里追加 `effect_emit_alert`，并把已有的 `effect_emit_outage_alert` 改成**委托**它（函数名、签名、返回值、"永不抛异常"的语义**逐字不变**，第 7 章的测试因此一条都不会变红）：

```python
def effect_emit_alert(sink: AlertSink, text: str) -> bool:
    """把一条告警送出去。**⛔ 永不抛异常**，返回是否送成功。

    第 6 章的群通知复用同一个出口（opener 约束 6：告警走第 7 章的 alerts*；
    alerts 通道自身失败只记日志、不中止）。

    ⛔ 捕获 `Exception` 而不是 `BaseException`：`KeyboardInterrupt` 与 `SystemExit`
    必须能停下服务。
    返回 False ⇒ 调用方 ⛔ 不许把这条告警标记成"已告警"。
    """
    try:
        sink.send(text)
    except Exception:
        logger.error(
            "告警发送失败，本次 ⛔ 不标记已告警；⛔ 不因此中止调用方的流程。原文：%s",
            text,
            exc_info=True,
        )
        return False
    return True


def effect_emit_outage_alert(sink: AlertSink, text: str) -> bool:
    """中断告警的出口。语义与 `effect_emit_alert` 逐字相同，⛔ 永不抛异常。

    保留这个名字是刻意的：第 7 章的调用点与测试都指着它，改名的收益是零、
    风险是把一章已经验收过的行为一起动了。
    """
    return effect_emit_alert(sink, text)
```

- [ ] **Step 5: 写落库实现**

创建 `tools/liaison/notify/store.py`：

```python
"""群通知的**唯一写库路径**。

⛔ 本模块不许出现 `conn.commit()` / `conn.rollback()` / `conn.executescript()`：
提交由 `app.storage.idempotency.idempotent_effect` 独占——它在同一个隐式事务里写
业务行与 `effect_log` 行然后一次性提交（工程铁律 1）。
`tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source` 守着这条。
⛔ 同理不许写 `with X:`。

**顺序钉死：先发送、后写台账行。** 发送在 `@idempotent_effect` 的**函数体内部**
执行——装饰器的 `effect_log` 预检是"这条通知发没发过"的唯一权威，把发送挪到装饰器
外面，预检就形同虚设。反过来（先记后发）会让"记录说发了、其实没发"成为可能，
而那正是本章要消灭的那类谎。代价是"已发出但落库前进程被杀"⇒ 下次可能重复通知
一次：重复看得见（群里多一条），丢失看不见。这个方向与第 7 章告警的选择一致。

⚠️ 装饰器的预检 `SELECT` 在 LEGACY_TRANSACTION_CONTROL 下**不开启写事务**，
写事务从函数体最后那条 `INSERT` 才开始——所以 HTTP 期间**不持有 SQLite 写锁**。
⛔ 不要把 `INSERT` 挪到发送之前"图省事"。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.storage.idempotency import idempotent_effect
from tools.liaison.alerts import AlertSink, effect_emit_alert
from tools.liaison.notify import guard

#: 群通知不挂在任何一条会话上，取固定哨兵值——与第 7 章连接窗口用
#: `__liaison_connection__` 同一手法。调用方若能指出通知源自哪条会话，
#: 传那条会话的 thread_id 更好（恒等式按 thread_id 分组仍然成立）。
GROUP_NOTIFY_THREAD_ID = "__liaison_group_notify__"

STATE_SENT = "sent"
STATE_PENDING_RESEND = "pending_resend"
STATE_REJECTED = "rejected"


@dataclass(frozen=True)
class NotifyRecord:
    """一次投递尝试的结果。**纯数据**，由 Task 5 的投递器产出。"""

    state: str
    attempts: int
    last_errcode: int | None = None
    last_error: str | None = None


def compute_alert_text(plan: guard.NotifyPlan, record: NotifyRecord) -> str:
    """拒发／重试耗尽时告警说什么。**纯函数**（铁律 2）。

    ⛔ 文本里不许出现"已由幂等保障""无需重发"这类说法——沿用 `alerts.py` 的判断：
    幂等防的是重复，补不回没发出去的东西；一句让人安心的话会让收信人不去补发。
    """
    if plan.mode == guard.MODE_REJECT:
        return (
            "【HR 值守通道·群通知拒发】"
            f"内容 {plan.byte_length} 字节超过本通道上限 {plan.limit_bytes} 字节，"
            f"且无法降级：{plan.reject_reason}。"
            f"该通知未发送（摘要 {plan.digest}），请人工处理。"
        )
    return (
        "【HR 值守通道·群通知未送达】"
        f"已尝试 {record.attempts} 次仍未成功"
        f"（errcode={record.last_errcode}，{record.last_error}）。"
        f"该通知已落为待重发记录（摘要 {plan.digest}），请人工确认后重发。"
    )


def _now_text() -> str:
    """送达时间戳。用 UTC 的 ISO8601 字面量，与库里其它 `datetime('now')` 同口径。"""
    return datetime.now(tz=timezone.utc).isoformat(timespec="microseconds")


@idempotent_effect("effect_send_group_notify")
def effect_send_group_notify(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    plan: guard.NotifyPlan,
    deliver: Callable[[guard.NotifyPlan], NotifyRecord],
    alert_sink: AlertSink,
    channel: str = guard.GROUP_WEBHOOK_CHANNEL.name,
) -> str:
    """发一条群通知并把结果落成**恰好一行**。返回终局状态。

    `business_key` 必须是 `plan.digest`（内容摘要）——幂等键因此是
    `{thread_id}:effect_send_group_notify:{摘要}`（tasks 6.6 逐字）。

    `deliver` 是投递器，**以参数注入**（Task 5 提供真实实现，单测用桩）：
    ⛔ 本模块不认识 HTTP、不认识令牌桶、不认识退避——它只负责"把结果记下来"。
    """
    if business_key != plan.digest:
        raise ValueError(
            f"business_key 必须是内容摘要：business_key={business_key} plan.digest={plan.digest}"
        )

    if plan.mode == guard.MODE_REJECT:
        record = NotifyRecord(state=STATE_REJECTED, attempts=0)
    else:
        record = deliver(plan)

    sent_at = _now_text() if record.state == STATE_SENT else None
    conn.execute(
        "INSERT INTO liaison_group_notify "
        "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, "
        " attempts, last_errcode, last_error, body, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            plan.digest,
            thread_id,
            channel,
            record.state,
            plan.mode,
            plan.byte_length,
            plan.limit_bytes,
            record.attempts,
            record.last_errcode,
            record.last_error,
            # **完整原文**，⛔ 不是提要：待重发靠这一列重发。
            plan.full_text,
            sent_at,
        ),
    )

    if record.state != STATE_SENT:
        # ⛔ 告警失败不许中止：effect_emit_alert 永不抛异常（opener 约束 6）。
        # 台账行必须照落——"没告警成功"和"没记下来"是两码事，后者才是丢失。
        effect_emit_alert(alert_sink, compute_alert_text(plan, record))

    return record.state


def select_pending_resends(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """列出待重发的通知。**只读**。

    ⏸ **本章不提供重发驱动器**——把 `pending_resend` 真的重发出去属第 8 章。
    这个函数是留给它的把手，也是"重试耗尽的通知没有被静默丢弃"的可查证据。
    """
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM liaison_group_notify WHERE state = ? ORDER BY created_at, digest",
        (STATE_PENDING_RESEND,),
    ).fetchall()
```

- [ ] **Step 6: 跑测试确认全绿**

Run: `python -m pytest tools/liaison/tests/test_notify_store.py tools/liaison/tests/test_alerts.py tools/liaison/tests/test_liaison_schema.py tools/liaison/tests/test_liaison_effects.py -q`
Expected: PASS（`test_alerts.py` 与 `test_liaison_effects.py` 必须**一条都不变红**——前者验的是委托没有改变第 7 章的行为，后者验的是没有引入第二个事务管理者）

- [ ] **Step 7: 提交**

```bash
git add tools/liaison/storage/schema.py tools/liaison/alerts.py tools/liaison/notify/store.py \
        tools/liaison/tests/test_notify_store.py
git commit -m "feat(liaison): 群通知台账表与幂等落库，一次通知恰好一行（6.6）"
```

---

### Task 5: 通道装配——节流 → 守卫 → 降级 → 退避重试 → 耗尽落待重发

**Files:**
- Create: `tools/liaison/notify/webhook.py`
- Modify: `tools/liaison/notify/__init__.py`（只做再导出）
- Test: `tools/liaison/tests/test_notify_webhook.py`

**Interfaces:**
- Consumes: Task 1 的 `compute_notify_plan` / `NotifyPlan` / `GROUP_WEBHOOK_CHANNEL`；Task 2 的 `TokenBucket` / `compute_backoff_delay` / `RATE_LIMIT_ERRCODE` / `MAX_RETRIES`；Task 3 的 `Transport` / `UrllibTransport` / `WebhookResponse` / `WebhookTransportError` / `config.load_group_webhook`；Task 4 的 `effect_send_group_notify` / `NotifyRecord` / `STATE_*` / `GROUP_NOTIFY_THREAD_ID`
- Produces:
  - `compute_upload_url(webhook_url: str) -> str`（纯函数）
  - `compute_markdown_payload(body: str) -> dict` / `compute_file_payload(media_id: str) -> dict`（纯函数）
  - `GroupWebhookSender(*, webhook_url, transport, attachment_supported=True, timeout=...)`
    方法 `send_markdown(body)` / `send_file(media_id)` / `publish_attachment(*, filename, content) -> str`，属性 `attachment_supported`
  - `Delivery(Protocol)`：属性 `done`、方法 `send_next() -> WebhookResponse`
  - `DirectDelivery(sender, plan)` / `DegradedDelivery(sender, plan)`
  - `make_group_webhook_delivery(sender, plan) -> Delivery`
  - `DeliveryOutcome(delivered: bool, attempts: int, last_errcode: int | None, last_error: str | None)`
  - `effect_deliver_with_backoff(delivery, *, bucket, sleep) -> DeliveryOutcome`
  - `build_group_webhook_sender(*, env=None, transport=None) -> GroupWebhookSender`
  - `send_group_notify(conn, *, text, sender, bucket, alert_sink, sleep, thread_id=GROUP_NOTIFY_THREAD_ID) -> str | None`

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_notify_webhook.py`：

```python
"""第 6 章·通道装配（6.1 / 6.3 / 6.5 / 6.9 的端到端部分）。

**不发真网络、不 sleep 真时间**：transport 与时钟都是 fake。
"""

from __future__ import annotations

import pytest

from tools.liaison.errors import MissingCredentialsError
from tools.liaison.notify import guard, ratelimit, store, transport, webhook
from tools.liaison.storage import db as liaison_db

FAKE_WEBHOOK = "https://example.invalid/cgi-bin/webhook/send?key=fake-key-for-tests"


class FakeTransport:
    """按剧本回应。`scripted` 是 errcode 序列，⛔ 用完即断言"发多了"。"""

    def __init__(self, scripted=None, *, upload_ok=True):
        self.scripted = list(scripted or [])
        self.json_calls: list[dict] = []
        self.multipart_calls: list[dict] = []
        self.upload_ok = upload_ok

    def _next(self) -> int:
        if not self.scripted:
            return 0
        return self.scripted.pop(0)

    def post_json(self, url, payload, *, timeout=transport.WEBHOOK_TIMEOUT_SECONDS):
        self.json_calls.append({"url": url, "payload": payload})
        errcode = self._next()
        if errcode == "boom":
            raise transport.WebhookTransportError("连接失败")
        return transport.WebhookResponse(errcode=errcode, errmsg="scripted", payload={"errcode": errcode})

    def post_multipart(self, url, *, filename, content, timeout=transport.WEBHOOK_TIMEOUT_SECONDS):
        self.multipart_calls.append({"url": url, "filename": filename, "content": content})
        if not self.upload_ok:
            return transport.WebhookResponse(errcode=40058, errmsg="bad media", payload={"errcode": 40058})
        return transport.WebhookResponse(
            errcode=0, errmsg="ok", payload={"errcode": 0, "media_id": "MEDIA-1"}
        )

    def sent_msgtypes(self) -> list[str]:
        return [c["payload"]["msgtype"] for c in self.json_calls]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def bucket(clock):
    return ratelimit.make_group_webhook_bucket(monotonic=clock.monotonic, sleep=clock.sleep)


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def make_sender(fake, *, attachment=True):
    return webhook.GroupWebhookSender(
        webhook_url=FAKE_WEBHOOK, transport=fake, attachment_supported=attachment
    )


# ── 纯函数 ──────────────────────────────────────────────────────────────


def test_upload_url_swaps_the_path_and_keeps_the_key():
    url = webhook.compute_upload_url(FAKE_WEBHOOK)
    assert "/upload_media?" in url
    assert "/send?" not in url
    assert "key=fake-key-for-tests" in url
    assert url.endswith("&type=file")


def test_upload_url_refuses_an_unexpected_path():
    """⛔ 猜不出来就报错，不硬拼——拼错的地址会把附件发去一个未知端点。"""
    with pytest.raises(ValueError):
        webhook.compute_upload_url("https://example.invalid/cgi-bin/webhook/other?key=k")


def test_payloads_carry_no_recipient_field():
    """6.7：负载里 ⛔ 不存在 touser / toparty / totag。"""
    md = webhook.compute_markdown_payload("hi")
    fl = webhook.compute_file_payload("MID")
    for payload in (md, fl):
        assert not ({"touser", "toparty", "totag"} & set(payload))
    assert md == {"msgtype": "markdown", "markdown": {"content": "hi"}}
    assert fl == {"msgtype": "file", "file": {"media_id": "MID"}}


# ── 退避重试（6.5 / spec「限流后重试成功」「重试耗尽」「非限流错误」） ────


def test_rate_limited_then_success_delivers_exactly_one_notification(bucket, clock):
    fake = FakeTransport([45009, 0])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(make_sender(fake), guard.compute_notify_plan(
            "短", limit_bytes=4096, attachment_supported=True)),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is True
    assert outcome.attempts == 2
    assert clock.slept == [1.0]
    # spec 逐字：「不产生重复的通知」——被限流那次**没有送达**，
    # 所以真正送达的仍然只有一条。
    assert len(fake.json_calls) == 2
    assert fake.scripted == []


def test_exhausted_retries_report_the_last_errcode(bucket, clock):
    """D9 逐字：1s→2s→4s→8s，最多 4 次 ⇒ 首发 + 4 次重试 = 最多 5 次发送。"""
    fake = FakeTransport([45009] * 6)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(make_sender(fake), guard.compute_notify_plan(
            "短", limit_bytes=4096, attachment_supported=True)),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is False
    assert outcome.attempts == 5
    assert outcome.last_errcode == 45009
    assert clock.slept == [1.0, 2.0, 4.0, 8.0]
    assert len(fake.json_calls) == 5


def test_non_rate_limit_error_is_not_retried_and_not_success(bucket, clock):
    """spec 场景「非限流错误」：被记录、⛔ 不当作成功、⛔ 不静默丢弃。"""
    fake = FakeTransport([93000])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(make_sender(fake), guard.compute_notify_plan(
            "短", limit_bytes=4096, attachment_supported=True)),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is False
    assert outcome.attempts == 1
    assert outcome.last_errcode == 93000
    assert clock.slept == [], "非限流错误 ⛔ 不许退避重试"


def test_transport_failure_is_not_retried_and_not_success(bucket, clock):
    fake = FakeTransport(["boom"])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(make_sender(fake), guard.compute_notify_plan(
            "短", limit_bytes=4096, attachment_supported=True)),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is False
    assert outcome.last_errcode is None
    assert "连接失败" in outcome.last_error


def test_every_attempt_takes_a_token_from_the_bucket(clock):
    """节流在**发送前**生效（spec 逐字），因此每一次尝试都要先取令牌。"""
    taken = {"n": 0}

    class CountingBucket:
        def acquire(self) -> float:
            taken["n"] += 1
            return 0.0

    fake = FakeTransport([45009, 45009, 0])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(make_sender(fake), guard.compute_notify_plan(
            "短", limit_bytes=4096, attachment_supported=True)),
        bucket=CountingBucket(),
        sleep=clock.sleep,
    )
    assert outcome.attempts == 3
    assert taken["n"] == 3


# ── 降级投递的断点续发（6.3 / 6.9） ─────────────────────────────────────


def test_degraded_delivery_sends_the_file_then_the_summary(bucket, clock):
    fake = FakeTransport([0, 0])
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=True)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DegradedDelivery(make_sender(fake), plan), bucket=bucket, sleep=clock.sleep
    )
    assert outcome.delivered is True
    # 顺序钉死：先附件、后提要。反过来会在群里留下一条声称"见附件"却没有附件的提要。
    assert fake.sent_msgtypes() == ["file", "markdown"]
    assert fake.multipart_calls[0]["content"] == "中" * 2000
    assert fake.multipart_calls[0]["filename"] == plan.attachment_filename


def test_retry_after_a_partial_degraded_delivery_never_resends_the_file(bucket, clock):
    """断点续发：提要被限流后重试，⛔ 不许把已经送达的附件再发一遍。"""
    fake = FakeTransport([0, 45009, 0])
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=True)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DegradedDelivery(make_sender(fake), plan), bucket=bucket, sleep=clock.sleep
    )
    assert outcome.delivered is True
    assert fake.sent_msgtypes() == ["file", "markdown", "markdown"]
    assert len(fake.multipart_calls) == 1, "附件 ⛔ 只上传一次"


def test_failed_upload_is_a_delivery_failure_not_a_silent_send(bucket, clock):
    fake = FakeTransport([0], upload_ok=False)
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=True)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DegradedDelivery(make_sender(fake), plan), bucket=bucket, sleep=clock.sleep
    )
    assert outcome.delivered is False
    assert fake.json_calls == [], "附件没传上去 ⇒ ⛔ 一条群消息都不许发"


# ── 端到端（守卫 → 降级 → 落库 → 告警） ────────────────────────────────


def test_short_notify_goes_out_directly_and_is_recorded(conn, bucket, clock):
    fake = FakeTransport([0])
    state = webhook.send_group_notify(
        conn, text="值守通知", sender=make_sender(fake), bucket=bucket,
        alert_sink=RecordingSink(), sleep=clock.sleep,
    )
    assert state == store.STATE_SENT
    assert fake.sent_msgtypes() == ["markdown"]
    row = conn.execute("SELECT state, mode, channel FROM liaison_group_notify").fetchone()
    assert row == (store.STATE_SENT, guard.MODE_DIRECT, guard.GROUP_WEBHOOK_CHANNEL.name)


def test_long_notify_is_degraded_and_the_full_text_is_retrievable(conn, bucket, clock):
    """spec 场景「超长内容降级成功」：群里收到提要与附件，完整内容可从附件取回。"""
    text = "中" * 2000
    fake = FakeTransport([0, 0])
    state = webhook.send_group_notify(
        conn, text=text, sender=make_sender(fake), bucket=bucket,
        alert_sink=RecordingSink(), sleep=clock.sleep,
    )
    assert state == store.STATE_SENT
    assert fake.multipart_calls[0]["content"] == text
    summary = fake.json_calls[1]["payload"]["markdown"]["content"]
    assert guard.compute_byte_length(summary) <= guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    assert "降级" in summary
    # 台账里存的是完整原文，⛔ 不是提要
    assert conn.execute("SELECT body FROM liaison_group_notify").fetchone()[0] == text


def test_undegradable_notify_is_rejected_alerted_and_never_sent(conn, bucket, clock):
    """spec 场景「无法降级时拒发」：不发送 + 告警说明拒发原因。"""
    fake = FakeTransport([0])
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn, text="中" * 2000, sender=make_sender(fake, attachment=False), bucket=bucket,
        alert_sink=sink, sleep=clock.sleep,
    )
    assert state == store.STATE_REJECTED
    assert fake.json_calls == [] and fake.multipart_calls == []
    assert len(sink.texts) == 1
    assert guard.REJECT_NO_ATTACHMENT_CHANNEL in sink.texts[0]


def test_exhausted_notify_lands_as_pending_resend_with_an_alert(conn, bucket, clock):
    fake = FakeTransport([45009] * 6)
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn, text="值守通知", sender=make_sender(fake), bucket=bucket,
        alert_sink=sink, sleep=clock.sleep,
    )
    assert state == store.STATE_PENDING_RESEND
    assert len(store.select_pending_resends(conn)) == 1
    assert len(sink.texts) == 1


def test_missing_webhook_env_refuses_to_build_a_sender():
    """6.10 端到端：环境变量未配置 → 拒发并告知缺失项，⛔ 不静默跳过后报成功。"""
    fake = FakeTransport([0])
    with pytest.raises(MissingCredentialsError) as excinfo:
        webhook.build_group_webhook_sender(env={}, transport=fake)
    assert "HR_LIAISON_GROUP_WEBHOOK" in str(excinfo.value)
    assert fake.json_calls == [], "⛔ 没有地址时一条消息都不许发出去"


def test_building_a_sender_from_the_environment_uses_the_configured_url():
    fake = FakeTransport([0])
    sender = webhook.build_group_webhook_sender(
        env={"HR_LIAISON_GROUP_WEBHOOK": FAKE_WEBHOOK}, transport=fake
    )
    sender.send_markdown("hi")
    assert fake.json_calls[0]["url"] == FAKE_WEBHOOK
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tools/liaison/tests/test_notify_webhook.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.notify.webhook'`

- [ ] **Step 3: 写实现**

创建 `tools/liaison/notify/webhook.py`：

```python
"""群通知通道的装配：节流 → 守卫 → 降级 → 发送 → 退避重试 → 落库。

⛔ 本目录禁止写 `with X:`（第 2 章事务扫描器）。⛔ 本模块不 import `time`：
`sleep` 与令牌桶都以参数注入。
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tools.liaison.alerts import AlertSink
from tools.liaison.config import load_group_webhook
from tools.liaison.notify.guard import (
    GROUP_WEBHOOK_CHANNEL,
    GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES,
    MODE_DEGRADED,
    NotifyPlan,
    compute_notify_plan,
)
from tools.liaison.notify.ratelimit import (
    MAX_RETRIES,
    RATE_LIMIT_ERRCODE,
    TokenBucket,
    compute_backoff_delay,
)
from tools.liaison.notify.store import (
    GROUP_NOTIFY_THREAD_ID,
    STATE_PENDING_RESEND,
    STATE_SENT,
    NotifyRecord,
    effect_send_group_notify,
)
from tools.liaison.notify.transport import (
    WEBHOOK_TIMEOUT_SECONDS,
    Transport,
    UrllibTransport,
    WebhookResponse,
    WebhookTransportError,
)

_SEND_PATH_SUFFIX = "/send"
_UPLOAD_PATH_SUFFIX = "/upload_media"


def compute_upload_url(webhook_url: str) -> str:
    """把发送地址换成附件上传地址。**纯函数**。

    企微群机器人的附件上传与发送共用同一个 `key`，只差路径与一个 `type` 参数。
    ⛔ 路径不是以 `/send` 结尾就报错、**不硬拼**——拼错的地址会把附件发去一个
    未知端点，而那是一次带着完整正文的外发。
    ⏸ 该端点本章**未实测**（真发是 8.6，由 Shao Peishen 亲自做）。本章代码只到
    "给了 URL 就能发"为止。
    """
    parsed = urllib.parse.urlsplit(webhook_url)
    if not parsed.path.endswith(_SEND_PATH_SUFFIX):
        raise ValueError(f"群 webhook 地址的路径不是以 {_SEND_PATH_SUFFIX} 结尾，⛔ 不猜")
    path = parsed.path[: -len(_SEND_PATH_SUFFIX)] + _UPLOAD_PATH_SUFFIX
    query = f"{parsed.query}&type=file" if parsed.query else "type=file"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))


def compute_markdown_payload(body: str) -> dict:
    """markdown 消息负载。**纯函数**。⛔ 里面没有、也永远不许有收件对象字段。"""
    return {"msgtype": "markdown", "markdown": {"content": body}}


def compute_file_payload(media_id: str) -> dict:
    """文件消息负载。**纯函数**。⛔ 里面没有、也永远不许有收件对象字段。"""
    return {"msgtype": "file", "file": {"media_id": media_id}}


class GroupWebhookSender:
    """本通道**唯一**的外发出口。

    🔴 **结构上没有"发给谁"这个参数。** 构造时只吃一个 webhook 地址，而地址只可能
    来自 `HR_LIAISON_GROUP_WEBHOOK`（内部值守群）；`send_markdown` / `send_file`
    都不接收收件对象。spec 的「不存在以候选人标识为收件对象的参数或调用路径」
    因此不是一句承诺，是一个签名事实——由
    tests/test_notify_boundaries.py 的 AST 断言持续守着。
    """

    def __init__(
        self,
        *,
        webhook_url: str,
        transport: Transport,
        attachment_supported: bool = True,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    ) -> None:
        self._webhook_url = webhook_url
        self._transport = transport
        self._timeout = timeout
        self._attachment_supported = attachment_supported
        self._upload_url = compute_upload_url(webhook_url) if attachment_supported else None

    @property
    def attachment_supported(self) -> bool:
        return self._attachment_supported

    def send_markdown(self, body: str) -> WebhookResponse:
        return self._transport.post_json(
            self._webhook_url, compute_markdown_payload(body), timeout=self._timeout
        )

    def send_file(self, media_id: str) -> WebhookResponse:
        return self._transport.post_json(
            self._webhook_url, compute_file_payload(media_id), timeout=self._timeout
        )

    def publish_attachment(self, *, filename: str, content: str) -> str:
        """把完整正文上传成一份附件，返回 `media_id`。

        ⛔ 上传失败一律抛 `WebhookTransportError`：上传没成功却继续发提要，
        群里就会出现一条声称"完整正文见附件"却没有附件的通知——那是一句谎。
        """
        if self._upload_url is None:
            raise WebhookTransportError("本通道没有配置附件承载方式")
        response = self._transport.post_multipart(
            self._upload_url, filename=filename, content=content, timeout=self._timeout
        )
        if not response.ok:
            raise WebhookTransportError(
                f"附件上传失败：errcode={response.errcode} {response.errmsg}"
            )
        media_id = response.payload.get("media_id")
        if not media_id:
            raise WebhookTransportError("附件上传的响应里没有 media_id，⛔ 不当作成功")
        return str(media_id)


class Delivery(Protocol):
    """一次通知要发的消息序列。**带断点**：`send_next` 只推进已送达的那一步。"""

    @property
    def done(self) -> bool: ...

    def send_next(self) -> WebhookResponse: ...


class DirectDelivery:
    """不超限的通知：一条 markdown，发完就完。"""

    def __init__(self, sender: GroupWebhookSender, plan: NotifyPlan) -> None:
        self._sender = sender
        self._plan = plan
        self._index = 0

    @property
    def done(self) -> bool:
        return self._index >= 1

    def send_next(self) -> WebhookResponse:
        response = self._sender.send_markdown(self._plan.body)
        if response.ok:
            self._index += 1
        return response


class DegradedDelivery:
    """超限的通知：先附件、后提要，**两条消息、一个断点**。

    🔴 **顺序钉死为「先附件、后提要」，⛔ 不许反。** 反过来一旦附件那条永久失败，
    群里就留下一条声称"完整正文见附件"却没有附件的提要——那是一句谎，而且看不出来。
    先发附件、提要失败，留下的是一份没有说明的文件：难看，但不骗人，且待重发记录
    加告警会把它接住。

    断点：`_index` 只在某一步真的送达之后才前进，因此被限流后的重试 ⛔ 不会把已经
    送达的那条再发一遍（spec 逐字：「不产生重复的通知」）。附件也只上传一次。
    """

    def __init__(self, sender: GroupWebhookSender, plan: NotifyPlan) -> None:
        self._sender = sender
        self._plan = plan
        self._index = 0
        self._media_id: str | None = None

    @property
    def done(self) -> bool:
        return self._index >= 2

    def send_next(self) -> WebhookResponse:
        if self._index == 0:
            if self._media_id is None:
                self._media_id = self._sender.publish_attachment(
                    filename=self._plan.attachment_filename or "",
                    content=self._plan.attachment_content or "",
                )
            response = self._sender.send_file(self._media_id)
        else:
            response = self._sender.send_markdown(self._plan.body)
        if response.ok:
            self._index += 1
        return response


def make_group_webhook_delivery(sender: GroupWebhookSender, plan: NotifyPlan) -> Delivery:
    """按 plan 的 mode 选投递形态。⛔ `MODE_REJECT` 到不了这里（store 提前短路）。"""
    if plan.mode == MODE_DEGRADED:
        return DegradedDelivery(sender, plan)
    return DirectDelivery(sender, plan)


@dataclass(frozen=True)
class DeliveryOutcome:
    delivered: bool
    attempts: int
    last_errcode: int | None = None
    last_error: str | None = None


def effect_deliver_with_backoff(
    delivery: Delivery,
    *,
    bucket: TokenBucket,
    sleep: Callable[[float], None],
    max_retries: int = MAX_RETRIES,
) -> DeliveryOutcome:
    """把一个投递序列发完，被限流就退避重试。

    三条出口：
    - 全部送达 → `delivered=True`；
    - 收到 `RATE_LIMIT_ERRCODE` 且重试次数已用满 → `delivered=False` + 最后的 errcode；
    - 收到**其它** errcode、或传输层抛异常 → `delivered=False`，⛔ **不重试**。

    ⛔ 非限流错误不许重试：那类错误（负载不合法、机器人被移出群、key 失效）重试
    多少次都是同一个结果，重试只会把一次可诊断的失败拖成一串噪音。
    ⚠️ 重试次数按**整条通知**计，不是每条消息各算一份——否则一条降级通知的
    退避上限会悄悄翻倍。
    """
    attempts = 0
    retries = 0
    while not delivery.done:
        bucket.acquire()  # 节流在**发送前**生效（spec 逐字）
        attempts += 1
        try:
            response = delivery.send_next()
        except WebhookTransportError as exc:
            return DeliveryOutcome(
                delivered=False, attempts=attempts, last_errcode=None, last_error=str(exc)
            )
        if response.ok:
            continue
        if response.errcode != RATE_LIMIT_ERRCODE:
            return DeliveryOutcome(
                delivered=False,
                attempts=attempts,
                last_errcode=response.errcode,
                last_error=response.errmsg,
            )
        if retries >= max_retries:
            return DeliveryOutcome(
                delivered=False,
                attempts=attempts,
                last_errcode=response.errcode,
                last_error=response.errmsg,
            )
        retries += 1
        sleep(compute_backoff_delay(retries))
    return DeliveryOutcome(delivered=True, attempts=attempts)


def build_group_webhook_sender(
    *, env=None, transport: Transport | None = None
) -> GroupWebhookSender:
    """从环境读地址造发送器。

    地址缺失 → `MissingCredentialsError`（6.10：拒发并报告缺失的变量名，
    ⛔ 不静默跳过后报成功）。⛔ 不要在这里 try/except 把它吞掉换成"降级到只写日志"
    ——那正是"静默跳过后报成功"。
    """
    return GroupWebhookSender(
        webhook_url=load_group_webhook(env),
        transport=transport if transport is not None else UrllibTransport(),
    )


def send_group_notify(
    conn: sqlite3.Connection,
    *,
    text: str,
    sender: GroupWebhookSender,
    bucket: TokenBucket,
    alert_sink: AlertSink,
    sleep: Callable[[float], None],
    thread_id: str = GROUP_NOTIFY_THREAD_ID,
) -> str | None:
    """本章的入口：算 plan → 发 → 记。返回终局状态；幂等命中时返回 `None`。

    ⛔ **本函数没有"发给谁"这个参数**，也 ⛔ 不许加——收件对象由 `sender` 的
    webhook 地址决定，而地址只能来自 `HR_LIAISON_GROUP_WEBHOOK`（6.7）。
    """
    plan = compute_notify_plan(
        text,
        limit_bytes=GROUP_WEBHOOK_CHANNEL.limit_bytes,
        attachment_supported=sender.attachment_supported,
    )

    def deliver(prepared: NotifyPlan) -> NotifyRecord:
        outcome = effect_deliver_with_backoff(
            make_group_webhook_delivery(sender, prepared), bucket=bucket, sleep=sleep
        )
        return NotifyRecord(
            state=STATE_SENT if outcome.delivered else STATE_PENDING_RESEND,
            attempts=outcome.attempts,
            last_errcode=outcome.last_errcode,
            last_error=outcome.last_error,
        )

    return effect_send_group_notify(
        conn,
        thread_id=thread_id,
        business_key=plan.digest,
        plan=plan,
        deliver=deliver,
        alert_sink=alert_sink,
        channel=GROUP_WEBHOOK_CHANNEL.name,
    )
```

把 `tools/liaison/notify/__init__.py` 补成（只做再导出，⛔ 不放逻辑）：

```python
"""群通知外发（tasks.md 第 6 章 / spec `liaison-group-notify`）。

⛔ 本包只做**内部值守群**的外发。不存在、也永远不许出现以候选人为收件对象的
参数或调用路径（6.7，由 tests/test_notify_boundaries.py 的 AST 断言守着）。
"""

from tools.liaison.notify.guard import (
    AIBOT_CHANNEL,
    GROUP_WEBHOOK_CHANNEL,
    NotifyPlan,
    compute_length_guard,
    compute_notify_digest,
    compute_notify_plan,
)
from tools.liaison.notify.ratelimit import TokenBucket, make_group_webhook_bucket
from tools.liaison.notify.store import (
    STATE_PENDING_RESEND,
    STATE_REJECTED,
    STATE_SENT,
    select_pending_resends,
)
from tools.liaison.notify.webhook import (
    GroupWebhookSender,
    build_group_webhook_sender,
    send_group_notify,
)

__all__ = [
    "AIBOT_CHANNEL",
    "GROUP_WEBHOOK_CHANNEL",
    "GroupWebhookSender",
    "NotifyPlan",
    "STATE_PENDING_RESEND",
    "STATE_REJECTED",
    "STATE_SENT",
    "TokenBucket",
    "build_group_webhook_sender",
    "compute_length_guard",
    "compute_notify_digest",
    "compute_notify_plan",
    "make_group_webhook_bucket",
    "select_pending_resends",
    "send_group_notify",
]
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `python -m pytest tools/liaison/tests/test_notify_webhook.py -q`
Expected: PASS（16 passed）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/notify/webhook.py tools/liaison/notify/__init__.py \
        tools/liaison/tests/test_notify_webhook.py
git commit -m "feat(liaison): 群通知通道装配，节流→守卫→降级→退避→待重发（6.3/6.5/6.9）"
```

---

### Task 6: 结构性守卫——收件对象、阈值不共用、静默截断，三条都用断言钉住

**Files:**
- Create: `tools/liaison/tests/test_notify_boundaries.py`
- Modify: `openspec/changes/hr-wecom-aibot-liaison/tasks.md`（回勾 6.1–6.10）

**Interfaces:**
- Consumes: Task 1–5 的全部产物
- Produces: 无新代码接口；产出的是**机器判据**

- [ ] **Step 1: 写断言测试**

创建 `tools/liaison/tests/test_notify_boundaries.py`：

```python
"""第 6 章·结构性约束（6.7 + D9 的"阈值不共用" + "不静默截断"）。

这些断言守的是**写不出来**，不是"跑起来对"。它们变红时的正确修法是删掉违规
代码，⛔ 不是给断言加豁免。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from tools.liaison.notify import guard

NOTIFY_ROOT = pathlib.Path(guard.__file__).resolve().parent

#: 企微"发给指定人/部门/标签"的三个参数名，以及候选人相关的收件对象叫法。
_RECIPIENT_PARAM = re.compile(
    r"^(touser|to_user|toparty|to_party|totag|to_tag|candidate|candidate_id|"
    r"recipient|recipients|openid|open_id|external_userid|external_user_id)$",
    re.IGNORECASE,
)
_RECIPIENT_KEY = {"touser", "toparty", "totag", "external_userid", "openid"}


def _notify_sources() -> list[pathlib.Path]:
    return sorted(NOTIFY_ROOT.rglob("*.py"))


def test_there_are_sources_to_scan():
    """自检：扫描面为空的断言永远绿，那比没有断言更糟。"""
    assert len(_notify_sources()) >= 5


@pytest.mark.compliance
def test_no_function_takes_a_recipient_parameter():
    """6.7：⛔ 不存在以候选人标识为收件对象的参数。

    收件对象由 webhook 地址决定，而地址只能来自 HR_LIAISON_GROUP_WEBHOOK
    （内部值守群）。任何"发给谁"的形参都是在这条通道上开一个对外的口子。
    """
    offenders = []
    for path in _notify_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = [
                a.arg
                for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
                if a is not None
            ]
            for extra in (args.vararg, args.kwarg):
                if extra is not None:
                    names.append(extra.arg)
            for name in names:
                if _RECIPIENT_PARAM.match(name):
                    offenders.append(f"{path.name}::{node.name}({name})")
    assert offenders == [], f"群通知通道出现了收件对象参数：{offenders}"


@pytest.mark.compliance
def test_no_payload_dict_carries_a_recipient_key():
    """6.7：⛔ 不存在以候选人标识为收件对象的调用路径。

    只看字典字面量的键，⛔ 不看注释与 docstring——注释里写"⛔ 不发候选人"是应该的，
    写成一个字段才是问题。
    """
    offenders = []
    for path in _notify_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value.lower() in _RECIPIENT_KEY:
                        offenders.append(f"{path.name}:{key.lineno} 键 {key.value!r}")
    assert offenders == [], f"群通知负载里出现了收件对象字段：{offenders}"


def test_recipient_scanners_actually_catch_a_violation():
    """证伪：断言本身得真的能抓到东西，否则它只是装饰。"""
    tree = ast.parse("def send(text, touser):\n    return {'touser': touser}\n")
    params = [
        a.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for a in node.args.args
    ]
    assert any(_RECIPIENT_PARAM.match(name) for name in params)
    keys = [
        k.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for k in node.keys
        if isinstance(k, ast.Constant)
    ]
    assert any(k.lower() in _RECIPIENT_KEY for k in keys)


def test_the_two_channel_limits_are_never_read_from_one_shared_name():
    """D9：⛔ 两条通道不共用同一个阈值常量。

    判据不是"两个数不相等"（那太容易靠改数糊弄），而是：`guard.py` 里 ⛔ 不存在
    一个既不叫 AIBOT_* 也不叫 GROUP_WEBHOOK_* 的"通用上限"常量。
    """
    source = (NOTIFY_ROOT / "guard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    limit_names = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_LIMIT_BYTES"):
                    limit_names.append(target.id)
    assert sorted(limit_names) == [
        "AIBOT_CHANNEL_LIMIT_BYTES",
        "GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES",
    ], f"guard.py 里的阈值常量集合变了：{limit_names}"


@pytest.mark.compliance
def test_no_silent_truncation_anywhere_in_the_notify_package():
    """D9 逐字：⛔ 任何形式的静默截断。

    判据：`notify/` 下 ⛔ 不存在对被发送文本的切片（`text[:n]`）。唯一被允许的
    "裁剪"是 `compute_text_prefix_within_bytes`，而它的产物永远和一句显式的降级
    声明拼在一起再发出去。
    """
    offenders = []
    for path in _notify_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
                source_name = getattr(node.value, "id", "") or getattr(node.value, "attr", "")
                if source_name in {"text", "body", "content", "full_text", "summary"}:
                    offenders.append(f"{path.name}:{node.lineno} 对 {source_name} 做了切片")
    assert offenders == [], f"群通知包里出现了对正文的切片（疑似静默截断）：{offenders}"


@pytest.mark.compliance
def test_degraded_body_always_declares_itself():
    """降级发出去的每一条提要，都必须自带"我被降级过"的声明。"""
    for size in (2000, 5000, 20000):
        plan = guard.compute_notify_plan(
            "中" * size,
            limit_bytes=guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES,
            attachment_supported=True,
        )
        assert plan.mode == guard.MODE_DEGRADED
        assert "降级" in plan.body
        assert plan.attachment_filename in plan.body
        assert guard.compute_byte_length(plan.body) <= guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES


def test_notify_package_never_imports_time_or_requests():
    """结构性：时钟与传输都必须注入；⛔ 不引入第三方 HTTP 库（design D10）。"""
    banned = {"time", "requests", "httpx", "urllib3", "aiohttp"}
    offenders = []
    for path in _notify_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in banned:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    offenders.append(f"{path.name}: from {node.module}")
    assert offenders == [], offenders
```

- [ ] **Step 2: 跑这一份，确认全绿**

Run: `python -m pytest tools/liaison/tests/test_notify_boundaries.py -q`
Expected: PASS（9 passed）

- [ ] **Step 3: 跑全量回归**

Run: `python -m pytest -q`
Expected: PASS——**已有用例一条都不许变红**。重点盯这四条：
- `tools/liaison/tests/test_liaison_effects.py`（没有第二个事务管理者、没有 `with X:`、`EFFECT_NODE_TO_TABLE` 仍与源码一致）
- `tools/liaison/tests/test_liaison_credentials.py`（第 1 章的启动期 fail-closed 未被本章连坐）
- `tools/liaison/tests/test_alerts.py`（`effect_emit_outage_alert` 委托后行为逐字不变）
- `tools/liaison/tests/test_liaison_no_secrets_in_vcs.py`（扫描面扩了，且没扫出东西）

Run: `python -m pytest -q -m compliance`
Expected: PASS（合规断言单独跑一遍可归因）

- [ ] **Step 4: 回勾 WBS**

把 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 6 章的 6.1–6.10 全部由 `- [ ]` 改为 `- [x]`。
⚠️ 并发提醒：`tasks.md` 是全仓最容易撞的文件。⛔ 只改第 6 章那十行，`git status` 里出现别人的改动是正常的，不要停下、不要顺手提交。

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/tests/test_notify_boundaries.py \
        openspec/changes/hr-wecom-aibot-liaison/tasks.md
git commit -m "test(liaison): 群通知结构性守卫（收件对象/阈值不共用/不静默截断），回勾 6.1-6.10"
```

---

## Self-Review

**1. spec coverage** —— `specs/liaison-group-notify/spec.md` 六条 Requirement 全部有任务承接，对照表见 Global Constraints 末尾。逐条点名：
- 「发送速率受主动限流约束」两个 Scenario → Task 2 的 `test_normal_rate_is_not_delayed` / `test_the_twenty_first_send_waits_for_a_refilled_token` / `test_sustained_rate_never_exceeds_twenty_per_minute`
- 「限流响应触发退避重试…」三个 Scenario → Task 5 的 `test_rate_limited_then_success_delivers_exactly_one_notification` / `test_exhausted_retries_report_the_last_errcode` / `test_non_rate_limit_error_is_not_retried_and_not_success`，落库侧 Task 4 的 `test_exhausted_retries_persist_as_pending_resend_and_alert`
- 「长度守卫按字节计…」两个 Scenario → Task 1 的 `test_char_count_under_limit_but_bytes_over_is_judged_over_limit` / `test_two_channels_judge_the_same_text_independently`，结构侧 Task 6 的 `test_the_two_channel_limits_are_never_read_from_one_shared_name`
- 「超限内容降级为提要加附件…」三个 Scenario → Task 1 的降级三条 + Task 5 的 `test_long_notify_is_degraded_and_the_full_text_is_retrievable` / `test_undegradable_notify_is_rejected_alerted_and_never_sent` + Task 6 的 `test_no_silent_truncation_anywhere_in_the_notify_package`
- 「外发对象仅限内部同事」两个 Scenario → Task 6 的两条 AST 断言 + 证伪用例
- 「发送地址与凭据不入版本管理」两个 Scenario → Task 3 的 `.env.example` 占位、扫描面扩展、`test_missing_or_blank_webhook_is_refused_by_variable_name`

**2. 占位符扫描** —— 全文无 TBD / TODO / "适当处理错误"。每个 Step 都带可运行的命令与预期输出。

**3. 类型一致性** —— 跨 Task 的名字逐个核过：`NotifyPlan.mode` 取值只有 `MODE_DIRECT/MODE_DEGRADED/MODE_REJECT`；`NotifyRecord.state` 取值只有 `STATE_SENT/STATE_PENDING_RESEND/STATE_REJECTED`，与表的 `CHECK` 逐字对应；`business_key` 处处等于 `plan.digest`；`Transport` 的两个方法签名在 Task 3 定义、Task 5 的 `FakeTransport` 与 `GroupWebhookSender` 逐字沿用。

**4. 本章刻意留下的三处缺口**（⛔ 不是遗漏，reviewer 请按这三条核对，不要"顺手补上"）：
- ⏸ **待重发驱动器不在本章**：`select_pending_resends` 只是把手，真正把 `pending_resend` 重发出去属第 8 章。
- ⏸ **附件上传端点未实测**：`compute_upload_url` 的路径推导按企微群机器人的公开约定写，真实连通性要到 8.6 才验得到。代码只到"给了 URL 就能发"为止。
- ⏸ **真实投递不在本章**：真实 URL 写进 `.env`、灰度真发由 Shao Peishen 亲自做（8.6）。本章全部测试用 fake transport，⛔ 不发真网络。

**5. 未做端到端提取验证** —— `spec-to-plan` 技能第 6 步的"把代码块提取到临时目录跑一遍"本次**没做**：本计划的代码依赖仓库内的 `app.storage.idempotency`、`tools.liaison.storage.db` 与第 2/7 章的守卫测试，脱离仓库提取出来跑不了，硬凑一个临时副本只会验证一份被改造过的代码。代之以：每个 Task 的 Step 2 都要求**先看着测试失败**，Step 4/6 在**真仓库里**跑真测试，Task 6 再跑一次全量回归——判据比临时副本更强。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-09-hr-wecom-aibot-liaison-unit6-group-notify.md`.

下一步用 `run-build`（Superpowers `subagent-driven-development`）逐任务执行：每个 Task 派一个全新子代理，两阶段 review 之间不共享上下文。⛔ 本次会话不进 run-build。
