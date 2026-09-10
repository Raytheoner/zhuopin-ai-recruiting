[Mac]0910B-SDK-message事件接线
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: worktree 自动生成（⛔ 不要指定分支名）｜ worktree: ☑ 勾选（改产品代码，与主工作区的真实进程/真实库隔离）｜ 工作区: worktree 内 ｜ 派发: [Mac]0910A

> 本文件是 `[Mac]0910B` 的正文（引用式 opener）。目标＝落地 `hr-wecom-aibot-liaison` 的
> **任务 8.5bis「SDK `message` 事件接线」**，即 `docs/session接力.md` 的 **AT-1a**。
> 依据：`openspec/changes/hr-wecom-aibot-liaison/tasks.md` 8.5bis（2026-09-10 追加）、
> `openspec/changes/liaison-reply-bridge-and-patrol/design.md` D6（接线判给本包）。
> Shao Peishen 2026-09-10 答 `1a` 授权拆条：本条只做 AT-1a。
> **流程口径**：本条走**单条直接 TDD**，⛔ 不走 `spec-to-plan` → `run-build`
> （Shao Peishen 2026-09-10 另答 `1a` 定）。依据＝`03-工具链协作规则.md`「轻量通道」第 4 条：
> 8.5bis 是**补已批准 spec 与代码之间的缺口**（两份 spec 本就要求入站进归档与队列），
> 需求侧无待定、⛔ 不动任何 spec 文本 ⇒ 按「未改行为」走。
> ⚠️ 若开工后发现**必须改 spec 文本**，立刻停下报，改走全流程，⛔ 不要自行扩大本条范围。
>
> 🔴 **本条只写代码 + 自动化测试，⛔ 不做真实入站验证。** 「`liaison_message` 有一条真实
> 企微消息行」归 **AT-1b**，由 `[Mac]0910A` 在主工作区跑（worktree 里没有 `.env`、
> 没有 `tools/liaison/.venv`、没有真实库，本条做不了）。理由见
> `docs/session接力.md`「⚠️ AT-1 的 worktree 矛盾」。
> ⛔ 不装 launchd、⛔ 不发任何消息（含群通知、私信、测试消息）、⛔ 不改 `.env`。

【为什么这条是前置】第 4 章「消息归档」6/6、第 5 章「值守任务队列」10/10 都已勾，
但**运行期入口从未接上**：`SUBSCRIBED_EVENTS` 只订三个连接事件 + `error`，
且 `test_this_chapter_wires_no_message_handling` 明令不接 ⇒ 两章只在单测层面成立，
现网 `liaison_message` **恒为 0 行**。于是「连接假死（TD-42）」与「根本没订阅 message」
两个成因产生**一模一样的症状**，分不清。本条接上之后，0 行才开始有诊断意义。

【一、开工前自检】任一条不过就**停下报，⛔ 不许绕过**。

1. `pwd` 确认在 worktree 内（路径含 `.claude/worktrees/`），`git branch --show-current`
   **不是** `main`／`master`。**报出实际分支名**，⛔ 不要指定或改名。
2. `grep -n '^- \[.\] 8.5bis' openspec/changes/hr-wecom-aibot-liaison/tasks.md` **必须命中**
   ——这就是「8.5bis 已上 main」的判据。未命中 → `git pull --rebase --autostash origin main`
   后重看；仍未命中 → 停下报（说明本条前提还没上 main）。
   ⚠️ **2026-09-10 订正**：本条原判据写的是「`git log --oneline -3` 里应能看到那条追加 8.5bis 的
   commit」，**已作废**。历史窗口会随时间漂移——那条 commit（`ba61a6d`）当天就被后续提交挤到
   第 16 位，前提明明成立、判据却假失败，照它跑的 session 会白 pull 一轮再「停下报」，
   报出的理由还是错的。前提判据一律落在**内容**上，⛔ 不落在历史位置上。
   判据真源见 `.claude/skills/kickoff/SKILL.md`「前置判据只落内容」。
3. `grep -n "8.5bis" openspec/changes/hr-wecom-aibot-liaison/tasks.md` 必须命中。
   **本条要做的七件事以该条目为准**，⛔ 不以本文件的复述为准（两者不一致时停下报）。
4. `grep -n "SUBSCRIBED_EVENTS = " tools/liaison/session_client.py` 应看到**四个**事件
   （`connected` / `disconnected` / `error` / `authenticated`）。已经有 `message` → 停下报
   （有人先改过，本条前提失效）。
5. `grep -n "test_this_chapter_wires_no_message_handling" tools/liaison/tests/test_main_wiring.py`
   必须命中。已经没有 → 停下报。
6. ⚠️ **worktree 里没有 `venv/`，也没有 `tools/liaison/.venv/`**（都被 gitignore 挡着，
   git 不带过去）。⛔ 不要在 worktree 里新建 venv。跑测试一律用主工作区的解释器指向当前 worktree：
   `PYTHONPATH=. /Users/paulshao/Projects/HumanResource/venv/bin/python -m pytest <路径> -q`
7. ⚠️ **SDK（`aibot`）只装在 `tools/liaison/.venv`，根 venv 没有**。本条测试**一律用替身**，
   ⛔ 不许 `import aibot`（会让全量 pytest 在 collect 阶段整个红掉，见 `build_ws_options` 的 docstring）。

【二、帧字段映射——本条最大的未知，⛔ 不许猜】

`handle_inbound_message` 的签名是已知的（`tools/liaison/inbound.py:79`）：
`conn, *, thread_id, msgid, sender_userid, received_at, msgtype, content="", attachment=None, …`。

**未知的是 SDK 的 `message` 帧长什么样**——哪个字段是 `msgid`、发送人 userid 叫什么、
群聊的 chatid 在哪、附件是句柄还是 URL。处置：

- 先用**只读方式**确认 SDK 表面：读 `tools/liaison/.venv` 里 `aibot` 的 `message` 事件
  payload 定义／类型标注（⛔ 不改 site-packages、⛔ 不真实建连）。
- 确认得到 → 映射按**实际字段**写，并在代码里留一行注释写明依据（文件:行）。
- 确认不到 → **按「表面未验即拒绝启动」处置**（TD-19 同一处置）：`verify_client_surface`
  加一条校验，缺字段就 `SdkSurfaceUnverifiedError`，⛔ **不许先写个猜的映射让它静默跑错**。
  这种情况下本条照样可以完成（接线 + 校验 + 测试齐全），在报告里标明「映射待真实帧确认，
  已 fail-closed」，交 `[Mac]0910A` 的 AT-1b 用真实帧收口。

【三、要改什么（TDD：先让测试红，再改实现）】

按 tasks.md 8.5bis 的 ①–⑦。要点与**为什么**：

1. **① 常量与清单**：`session_client.py` 加 `EVENT_MESSAGE = "message"`，并入 `SUBSCRIBED_EVENTS`。
2. **② 表面校验同步**：照 `session_client.py:303-307`（`EVENT_ERROR` 那段）的形状，给 `message`
   加一条「不在清单里就 `SdkSurfaceUnverifiedError` 拒绝启动」。
   *为什么*：⚠️ **漏订阅不报错**，只是静默收不到——`session_client.py:300-307` 的注释已立此判据
   （`error` 漏订阅正是 TD-39 能发生的成因之一）。
3. **③ 回调只放帧**：SDK 回调里**只**把帧（`msgid` / `sender_userid` / `msgtype` / `content` /
   附件句柄 / 到达时刻）放进线程安全队列，⛔ **不在回调里碰 DB、不碰文件、不发消息**。
   *为什么*（🔴 工程铁律 1）：回调跑在事件循环线程，而铁律 1 要求**幂等记录与业务写在同一连接、
   同一 `BEGIN`**，那个连接归值守线程。跨线程共用 sqlite 连接会把事务归属搅乱——
   `docs/findings/2026-08-13-sqlite-事务归属冲突.md` 那一类（现网已因此丢过两轮 `outbox`）。
4. **④ 值守线程消费**：值守线程取帧 → 调 `inbound.handle_inbound_message(conn, …)`。
   `thread_id` 按 design D3 取（私聊 userid／群聊 chatid）。
5. **⑤ 映射**：见上方第二节。
6. **⑥ 删禁令测试并换正向断言**：删
   `tools/liaison/tests/test_main_wiring.py::test_this_chapter_wires_no_message_handling`，
   换上正向断言——`__main__.py` 确实 import `inbound` 且调到 `handle_inbound_message`；
   `SUBSCRIBED_EVENTS` 含 `message`。
   🔴 ⛔ **只删不换＝这块回到无判据状态**，下一轮谁把接线拆掉都不会变红。
7. **⑦ 接线层幂等测试**：同 `msgid` 重投 ⇒ 归档不出第二份、队列不增第二行、回复不重发。
   **幂等策略**：本条 ⛔ **不新增** `effect_*`——`handle_inbound_message` 内的 `archive_message`
   已按 `{thread_id}:…:{msgid}` 幂等，本条只加接线层的恒等断言。

🔴 **工程铁律 2**：`compute_*` 纯函数／`effect_*` 副作用的命名与分层不得破坏。
帧→参数的映射是纯函数（`compute_inbound_frame(...)` 之类），⛔ 不要在里面记日志或读时钟。

【四、验收】

1. 新测试**先红后绿**（报告里写明：红的时候报错信息原文是什么）。
   至少这两条要先红：② 的表面校验（把 `message` 从清单里拿掉 ⇒ 必须红）、
   ⑥ 的正向断言（把接线注释掉 ⇒ 必须红）。
2. `PYTHONPATH=. /Users/paulshao/Projects/HumanResource/venv/bin/python -m pytest tools/liaison/tests -q`
   全绿，**总数比改前多**（报出改前/改后两个数字）。
3. 全量 `pytest -q` 不回归（报出总数）。
4. `openspec validate hr-wecom-aibot-liaison --strict` 通过。
5. ⛔ **不做真实入站验证**——本条无法验证「真的能收到汤丽萍的消息」，那归 AT-1b。
   报告里要**明说这一点**，⛔ 不许写成「入站链路已验证」。

【五、落档】

- `openspec/changes/hr-wecom-aibot-liaison/tasks.md`：勾 8.5bis，并把进度头
  `62/67` 改成 `63/67`、第 8 章 `5/10` 改成 `6/10`。
  ⛔ **不要顺手勾 8.6**——8.6 是灰度，前置是 Shao Peishen 本人先私信一次机器人。
- `docs/session接力.md`：AT-1 行状态改为「AT-1a ✅ 已落地（`<commit>`）／AT-1b ⏸ 待
  `[Mac]0910A` 在主工作区验」。⛔ 不要把整条 AT-1 标成完成——真实入站那半没做。
- 映射若走了 fail-closed 支：在 `docs/tech-debt.md` 登记一条 TD（「`message` 帧字段映射未经真实帧确认，
  现为 fail-closed」），销账条件＝AT-1b 用真实帧确认。

【六、并发协议】其它泳道可能同时在跑，逐条照做：

1. 只 `git add` 本条明确列出的路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a` /
   `git stash`（含 `-u`）
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交
3. push 被拒 → `git pull --rebase --autostash origin main` 后重试，最多 3 次。
   ⛔ 不要在 commit 前 pull
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 默认绝不删除该锁

【七、提交与合并】只 add：`tools/liaison/session_client.py`、`tools/liaison/__main__.py`、
帧映射新文件（若新建，逐个列出）、`tools/liaison/tests/`（新增/改动的测试文件，逐个列出）、
`openspec/changes/hr-wecom-aibot-liaison/tasks.md`、`docs/session接力.md`、
`docs/tech-debt.md`（仅走 fail-closed 支时）、本文件 `docs/openers/0910B-SDK-message事件接线.md`。
⛔ 绝不 add `.env`、`data/`、`tools/liaison/.venv/`（核 `git status` 里不出现它们）。
commit 后**合并回 `main` 并 push**（⛔ 不留分支）——AT-1b 要在主工作区跑，必须先拿到这份代码。

【八、不要做的】

- ⛔ **不做真实建连、不等真实入站、不发任何消息**（worktree 里没有 `.env`、没有真实库）
- ⛔ **不装 launchd**，⛔ 不碰 `launchctl`
- ⛔ 不改 site-packages 里的 SDK
- ⛔ 不删/不改 `verify_client_surface` 的严格性（只许加校验，不许放宽）
- ⛔ 不在 SDK 回调线程里做任何 DB / 文件 / 网络写
- ⛔ 不改 `.env` / `whitelist.yaml` / `data/`
- ⛔ 不碰 `openspec/changes/liaison-reply-bridge-and-patrol/`（那是另一个包，D6 已把接线判给本包）
- ⛔ 不勾 8.6／8.7／8.8／8.9

【九、收工】逐个列出本次新增/修改/删除的文件（仓库相对路径，⛔ 不写"等若干文件"），报告含：
七件事各自的最终写法、帧映射走的是**实际字段**还是 **fail-closed**（必须明说是哪一种）、
两条先红后绿测试的红时报错原文、pytest 改前/改后数量、`openspec validate --strict` 结果、
合并回 main 的 commit hash，
并单列一行「⏸ 下一步：AT-1b 真实入站落库一条（主工作区、worktree ❌，归 `[Mac]0910A`）」。
