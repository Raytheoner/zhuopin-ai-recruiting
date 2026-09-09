# HR 企微值守通道服务

**这是什么**：Shao Peishen 在 HumanResource 开发期用的 24 小时值守工具，通过企业微信
"智能机器人"长连接与 HR 专员（汤丽萍）收材料、传信息，并把材料归档、把待办入队。

## 三条边界（评审与改动时先看这里）

1. **开发期值守工具**，不是 HumanResource 的产品功能。它的对手方是内部同事，
   ⛔ 不碰候选人、不碰简历、不做任何 AI 评分。
2. **永不部署 `.51`**（Shao Peishen 2026-08-13 拍板，与 Windows 侧同一口径）。
   落在 `tools/` 而不是 `app/` / `scripts/` 就是为了让这条约束由目录结构本身承担：
   `tools` 不在 `sync-to-server.sh` 的 `SYNC_PATHS` 白名单里，推不上去。
3. **不是产品功能**，因此 ⛔ 不受 `04-部署与门户挂载.md` 管辖：不挂 `/hr/recruit-agent`、
   不做路径前缀就绪、不进 FastAPI 应用、不走 8095 端口、不接鉴权中间件空壳。
   评审按 `openspec/changes/hr-wecom-aibot-liaison/proposal.md` 的验收标准判，
   ⛔ 不要套产品功能的验收标准。

## 依赖与环境

依赖清单是本目录下独立的 `requirements.txt`，⛔ 不进根 `requirements.txt`、
⛔ 不进 `pyproject.toml`——那两份都会被同步到 `.51` 并在那边 `pip install`。

```bash
python3.14 -m venv tools/liaison/.venv
tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt
```

`tools/liaison/.venv/` 被根 `.gitignore` 的 `.venv/` 规则覆盖（该规则不带前导斜杠，
匹配任意层级的 `.venv` 目录），不会入库。

## 凭据

`HR_LIAISON_BOT_ID` 与 `HR_LIAISON_BOT_SECRET` **只从进程环境读**，真实值落
**`tools/liaison/.env`**（`.gitignore:2` 的 `.env` 已挡住它）。两者缺失、为空串或只含
空白字符时，服务**拒绝启动**并指明缺哪一项，⛔ 不以"启动了但收不到消息"的状态驻留。
占位见同目录 `.env.example`：

```bash
cp tools/liaison/.env.example tools/liaison/.env   # 然后填真实值
```

🔴 **⛔ 不要放进仓库根的 `.env`**（2026-09-09 迁出，TD-40）。`app/config.py` 的
`Settings` 是 pydantic-settings 的 `BaseSettings`，默认 `extra="forbid"`——根 `.env` 里
多一个它不认识的键，`Settings()` 就整个抛 `ValidationError` ⇒ **Web 服务起不来**，
且报错把 `bot_secret` 明文打进输出。实测过一次：带这三个键时根 venv 全量 23 failed，
迁出后 0 failed。⚠️ 连**空占位**都不行——`extra="forbid"` 判的是键在不在，不是值空不空。
⛔ 也**没有**"根 `.env` 兜底"：兜底会让键留在根里这个坏状态继续静默存在。

⚠️ 这与 design D10 同构：依赖在 `tools/liaison/requirements.txt`，配置在
`tools/liaison/.env`，两套各归各位；`tools/` ⛔ 不进 `sync-to-server.sh`，都不会上 .51。

测试专用逃生口 `HR_LIAISON_DOTENV_PATH`：指定入口读哪个 `.env` 文件，缺省为
`tools/liaison/.env`。⛔ 它只服务于测试隔离（避免开发机上真实的 `.env` 让"凭据缺失"
用例变绿），⛔ 不要在生产用法里依赖它，因此它**不写进 `.env.example`**。

## 运行与守护

前台跑一次（调试用）：

```bash
tools/liaison/.venv/bin/python -m tools.liaison
```

**装成 launchd 常驻**（design D12：`KeepAlive` + `ThrottleInterval`，
⛔ 不移植 Windows 侧的三级退避重启脚本——那是因为计划任务没有 KeepAlive 语义才写的）：

```bash
# 先看会写什么，不落任何文件、不调 launchctl
tools/liaison/.venv/bin/python -m tools.liaison.scripts.install_launchd --dry-run
# 真装（幂等，重复跑等价于重装）
tools/liaison/.venv/bin/python -m tools.liaison.scripts.install_launchd
```

🔴 **装这一步只由 Shao Peishen 本人在 Terminal 跑**——起 LaunchAgent 属安全配置变更，
与改 `settings.json` 同类，⛔ Claude 不代做。子 session 里能做的只有 `--dry-run`。

安装脚本是 fail-closed 的：`tools/liaison/.venv/bin/python` 不存在时**拒绝安装**并退非 0。
理由是 `KeepAlive` 对退出码不作区分——装上一个解释器不存在的 job，launchd 会按 30s
一轮无限重启一个必然失败的进程，而唯一症状是 err 日志里滚同一行。

**停 / 回滚**（design「回滚」段：停的是值守，⛔ 不销毁台账——归档与 `liaison.db` 留在本机）：

```bash
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.liaison.plist
```

**看日志**：

```bash
tail -f data/liaison/logs/launchd.err.log   # 崩溃与缺凭据看这里
tail -f data/liaison/logs/launchd.out.log
launchctl print gui/$UID/com.zhuopin.hr.liaison | grep -E "state|last exit code"
```

⚠️ 日志目录不存在时 launchd **不建也不报错**，只是这条 job 起不来。安装脚本会先 `mkdir -p`。

⛔ plist 里不放任何凭据取值：`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` 由进程自己从
`tools/liaison/.env` 读。`~/Library/LaunchAgents/` 不受 `.gitignore` 保护、会被备份链原样带走，
凭据挪进 `EnvironmentVariables` 等于静默扩大泄漏面。守护断言见
`tests/test_liaison_boundaries.py::test_launchd_template_carries_variable_names_but_no_credential_values`。

## 当前进度

第 1 章（本目录的骨架、SDK 可行性结论、凭据 fail-closed 校验）。存储、白名单、
归档、队列、群通知、断线告警在第 2–8 章，见变更包 `tasks.md`。
