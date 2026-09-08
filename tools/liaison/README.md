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

`HR_LIAISON_BOT_ID` 与 `HR_LIAISON_BOT_SECRET` **只从进程环境读**，真实值只落仓库根的
`.env`（`.gitignore` 已排除）。两者缺失、为空串或只含空白字符时，服务**拒绝启动**并
指明缺哪一项，⛔ 不以"启动了但收不到消息"的状态驻留。占位见根 `.env.example`。

测试专用逃生口 `HR_LIAISON_DOTENV_PATH`：指定入口读哪个 `.env` 文件，缺省为仓库根的
`.env`。⛔ 它只服务于测试隔离（避免开发机上真实的 `.env` 让"凭据缺失"用例变绿），
⛔ 不要在生产用法里依赖它，因此它**不写进 `.env.example`**。

## 当前进度

第 1 章（本目录的骨架、SDK 可行性结论、凭据 fail-closed 校验）。存储、白名单、
归档、队列、群通知、断线告警在第 2–8 章，见变更包 `tasks.md`。
