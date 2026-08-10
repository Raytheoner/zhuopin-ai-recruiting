# 部署到 51 服务器（Windows venv + 计划任务，无 Docker）

依据 `04-部署与门户挂载.md` §6 决策记录：目标服务器 `192.168.100.51` 是 Windows，
没有 Docker，沿用现有 4 个服务同款的部署模式——venv + 计划任务，不引入容器运行时。

## 前置条件

- 服务器已装 Python 3.11+（与其他现有服务共用的解释器版本对齐，若不确定找 IT 确认）
- 本机（开发机）到服务器的 SSH 访问已配置（`sync-to-server.sh` 用 scp/ssh）
- 服务器能出公网访问 LLM 供应商 API（已实测：DeepSeek / 火山方舟 / 阿里百炼三家域名连通，见
  `04-部署与门户挂载.md` §4）

## 首次部署

1. 在服务器上创建部署目录（默认 `C:\apps\zhuopin-recruit-agent`）
2. 从开发机运行 `sync-to-server.sh` 把代码推过去（首次也可以手工 scp，效果一样）
3. RDP 登录服务器，以管理员身份打开 PowerShell，进入部署目录，创建 `.env`
   （**不要把 `.env` 提交进 git，也不要放在门户可访问的路径下**）：

```
LLM_PROVIDER=<按 docs/m1-model-comparison.md 的决策填>
LLM_API_KEY=<真实 key>
LLM_BASE_URL=<对应供应商 base_url>
LLM_MODEL=<锁定版本号，禁止 latest>
LLM_SUPPORTS_JSON_SCHEMA=<true|false>
DB_PATH=data/demo.db
ROOT_PATH=/hr/recruit-agent
```

4. 运行首次部署脚本：

```powershell
.\deploy-server.ps1
```

5. 验证：`curl.exe http://localhost:8095/hr/recruit-agent/` 应返回带「演示环境」横幅的页面；
   `Get-ScheduledTask -TaskName ZhuopinRecruitAgent` 应显示 `Ready`/`Running`
6. 请 Paul 在门户导航加一行外链，指向 `http://192.168.100.51:8095/hr/recruit-agent/`
   （板块名「HR·招聘智能体」，见 `04-部署与门户挂载.md` §2「门户导航挂法」）

## 日常发版

代码有更新后，从开发机运行：

```bash
./sync-to-server.sh
```

它会把代码 scp 推过去并重启计划任务。**依赖变更**（`requirements.txt` 改了）时，
额外 RDP 登录服务器重跑一次 `deploy-server.ps1`（对已存在的 venv 是幂等的，
只会重新 `pip install`，不会重建 venv）。

## 保活验证

- 重启服务器后，计划任务应在开机时自动拉起服务（`AtStartup` 触发器）：
  `Get-ScheduledTaskInfo -TaskName ZhuopinRecruitAgent` 查看 `LastRunTime`
- 手工 `Stop-Process` 掉 uvicorn 进程后，计划任务应在 1 分钟内自动重启
  （`-RestartCount 3 -RestartInterval 1分钟`，验证时最多等 3 次、共 3 分钟）

## 安全红线（照抄 `04-部署与门户挂载.md` §5，执行时逐条核对）

- [ ] `.env` 没有出现在门户可访问的任何路径下，也没有提交进 git
- [ ] LLM 凭据不在门户可访问的任何路径下
- [ ] 页面「演示环境，不进入正式招聘流程」标注清晰可见
- [ ] 访问日志沿用现有四服务同款做法（JSONL，不采集个人身份信息）

## 技术债提醒

- **过渡端口 8095 是临时的**：统一门户网关上线即迁移，届时只需网关加一条
  `proxy_pass` 指向 `root_path=/hr/recruit-agent`，本应用零改动（见计划开头 技术债 #8）
- **鉴权仍是门户共享口令**：M2 起处理真实简历前必须换成可识别到人的登录 + 访问留痕
  （见计划开头 技术债 #9，`04-部署与门户挂载.md` §4 风险二）
