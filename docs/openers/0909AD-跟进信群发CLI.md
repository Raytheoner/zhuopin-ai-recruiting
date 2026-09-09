[Mac]0909AD-跟进信群发CLI
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: 不指定（建 worktree 后 git 自己生成）｜ worktree: ✅ 勾选（写实现代码）｜ 工作区: worktree ｜ 派发: Cowork·HR业务线-接力0909Q

> 本文件是 `[Mac]0909AD` 的正文（引用式 opener）。目标＝**根治"跟进信只能手工发"**。
> 依据：win 端 `5-平台底座/wecom-aibot-service/scripts/push_followup_letter.py` 的形态——
> 发信的是**跑在本机原生环境的脚本**，AI 会话只是调它。Mac 侧的对等物是 **CC 的 Bash**
> （macOS 原生、有网）；⛔ **不是** Cowork 的 shell（隔离 VM，`qyapi.weixin.qq.com` 无 DNS，
> 2026-09-09 两次实测；云容器经代理亦 403）。所以这条 CLI 只能由 CC 或本机 Terminal 跑。
> 🔴 **本 session ⛔ 不真发任何消息**（对外发送红线）。交付到"可发送态"即止，首次真发由 Shao Peishen 在 CC 里跑那一条命令，他跑＝授权留痕。

【一、任务】轻量通道 TDD，⛔ 不走 spec-to-plan / run-build。开工先 git pull --rebase origin main，`./venv/bin/python -m pytest -q` 记基线，并读三份依据：`tools/liaison/notify/webhook.py`（`GroupWebhookSender` / `compute_upload_url` / `publish_attachment`）、`tools/liaison/notify/transport.py`（`compute_multipart_body` / `UrllibTransport`）、`docs/跟进信/README-跟进信清单.md`（台账真身与八态语义）。

1. **二进制附件支持**（当前缺口：`publish_attachment(*, filename, content: str)` 与 `compute_multipart_body(..., content: str)` 只收文本，docx 发不了）
   - 让这两处同时接受 `str` 与 `bytes`：`str` 按 UTF-8 编码后走同一条路，`bytes` 原样进 multipart body
   - `Content-Type` 用 `application/octet-stream`；filename 按 RFC 2231/UTF-8 处理中文名（本项目的文件名全是中文，⛔ 不许 latin-1 编码后炸）
   - 🔴 单测必须覆盖：中文文件名的 multipart body 逐字节断言、bytes 与 str 两条路产出一致、超过企微 20MB 上限时**提前拒绝并报明**（⛔ 不发出去再看错误码）

2. **新增子命令 `send-followup`**（`tools/liaison/__main__.py` 末尾照 `cleanup` 的写法加一个分支，⛔ 不动 `main()` 首行的 `setup_logging()` 接线）
   - 用法：`python -m tools.liaison send-followup --md <正文.md> [--docx <附件.docx>] [--dry-run]`
   - 顺序：读 md → 去掉 frontmatter（⛔ frontmatter 绝不进群消息）→ `send_markdown(正文)` → 若给了 `--docx`：`publish_attachment` 拿 media_id → `send_file(media_id)`
   - 🔴 **`--dry-run` 必须真的什么都不发**：只打印将要发送的 markdown 正文、附件名与字节数、目标 webhook 的**域名**（⛔ 不打印 key）。默认即 `--dry-run`；**真发必须显式传 `--send`**（多一道闸，防手滑）
   - 凭据只从 `HR_LIAISON_GROUP_WEBHOOK` 读，⛔ 不加命令行传 webhook 的参数、⛔ 任何日志与异常都不得回显 key（`transport.py` 已有「异常不泄露 webhook url」的守护测试，新代码同样受它约束——确认那条测试覆盖到新路径）
   - 退出码：0 成功；2 参数错；3 凭据缺失；4 发送失败（错误码与响应体进日志，⛔ 不吞）

3. **台账回填**（发送成功后，`--send` 路径才做；`--dry-run` ⛔ 不碰文件）
   - 改 `docs/跟进信/README-跟进信清单.md` 对应行：`⏳ 待你审`／`🆕 待发` → `✅ 已推送 <YYYY-MM-DD>`，并把「编号」列的 `（待发，暂不占号）`／`（待你审，暂不占号）` 括注去掉
   - 定位那一行只用**编号**做键（如 `人事部#1`），⛔ 不用标题模糊匹配
   - 幂等：已经是 `✅ 已推送` 的行再跑一次 ⇒ 打印"已是终态，不重复回填"并退 0，⛔ 不重复发消息也⛔ 不改日期

4. **单测**（进 `tools/liaison/tests/test_notify_webhook.py` 与新建 `test_send_followup_cli.py`）：全部离线，用假 transport，🔴 **⛔ 不得有任何真实网络调用**（`test_liaison_boundaries.py` 那两道门禁的同族要求）
   - `--dry-run` 一条消息都不发、一个文件都不改
   - `--send` 时 markdown 与 file 各发一次、顺序正确、frontmatter 未进正文
   - 台账回填的幂等用例（跑两次只改一次）
   - 缺 `HR_LIAISON_GROUP_WEBHOOK` 时退 3 且**不发**

【二、守住】⛔ 只动 `tools/liaison/notify/webhook.py`、`tools/liaison/notify/transport.py`、`tools/liaison/__main__.py`（末尾加一个子命令分支）、`tools/liaison/tests/`、`docs/跟进信/README-跟进信清单.md`（只在真发路径下由 CLI 改，本 session ⛔ 不手工改它）。
⛔ 不碰 `session*`（第 7 章长连接是另一条路，本条与它零依赖）、`retention*`、`logsetup*`、`queue.py`、`storage/*`、`whitelist.py`、`app/`、`scripts/`。
⛔ **不做私信**：群 webhook 绑定的就是「人力AI保障组」这一个群，发不了单聊。私信要走 aibot 的 chatid 发送（第 7 章），等 `0909AC` 与 8.6 之后另立。

【三、收口】合回 main（先 rebase）→ `git rev-list --count main..<分支>` 与 `git cherry -v main <分支>` 都判真合 0；pytest ≥ 基线且 0 失败；⛔ 不归档。

【四、收工】报告含：`--dry-run` 对 `人事部#1` 那封信的**完整输出原文**（这是唯一能让 Shao Peishen 在真发前看清"到底会发出去什么"的东西）、中文文件名 multipart 的逐字节断言输出、四组单测红→绿、pytest 前后、commit hash。
末尾单列**给他的两条命令**（他在 CC 里跑，⛔ 本 session 不跑）：

    ./venv/bin/python -m tools.liaison send-followup --md "docs/跟进信/人事部-汤丽萍-跟进-2026-09-09-AI招聘值守机制启用与配合方式.md" --docx "docs/跟进信/人事部-汤丽萍-跟进-2026-09-09-AI招聘值守机制启用与配合方式.docx" --dry-run

    ./venv/bin/python -m tools.liaison send-followup --md "docs/跟进信/人事部-汤丽萍-跟进-2026-09-09-AI招聘值守机制启用与配合方式.md" --docx "docs/跟进信/人事部-汤丽萍-跟进-2026-09-09-AI招聘值守机制启用与配合方式.docx" --send

⚠️ 用哪个解释器要在报告里写死：`tools/liaison/.venv/bin/python` 装了 SDK 但本条不需要 SDK；根 `./venv` 够用。以实际跑通的那个为准，⛔ 不要两个都写。
