[Mac]0909AA-凭据验证与liaison-venv
【设置】执行环境: CC Desktop ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（建 venv、跑只读探针与测试，不写业务代码）｜ 工作区: 仓库根 ｜ 派发: Cowork·HR业务线-接力0909Q

> 本文件是 `[Mac]0909AA` 的正文（引用式 opener）。三个凭据已于 2026-09-09 由 Cowork 写进本机 `.env`（BotID/Secret 为 Mac 端**新建**的 aibot，群 webhook 为「人力AI保障群」）。
> 🔴 本 session ⛔ 不读取、不回显、不复述任何凭据取值；只判「非空 / len=N」。
> 🔴 本 session ⛔ 不发任何真实消息、⛔ 不往「人力AI保障群」推任何东西、⛔ 不往 `whitelist.yaml` 加人。
> ℹ️ 口径订正（以脚本自身 docstring 为准）：`probe_ws_surface.py` **只做内省，不建连、不需要凭据**。接力文档里「真实建连一次」的说法是错的，⛔ 不要据此改脚本或加凭据参数。

【一、验凭据存在性与三条守卫】⛔ 只报 len，不回显取值：
  for k in HR_LIAISON_BOT_ID HR_LIAISON_BOT_SECRET HR_LIAISON_GROUP_WEBHOOK; do v=$(grep -E "^$k=" .env | head -1 | cut -d= -f2-); if [ -n "$v" ]; then echo "$k 非空 len=${#v}"; else echo "$k 为空"; fi; done
三个都应非空（预期 35 / 43 / 89）。任一为空 → 停下报，⛔ 不自己编值、不去别处找。
守卫三条，任一不过就停下报（⛔ 不自己改 .gitignore、不 force add）：
  ls -l .env | awk '{print $1}'            # 应 -rw-------
  git check-ignore -v .env                  # 应命中 .gitignore:2
  git grep -nE "qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=" -- ':!*.md' | wc -l   # 应 0（tests 里的 example.invalid 假值不匹配真实域名）

【二、建 tools venv】🔴 先验解释器版本，⛔ 不放宽：
  python3.14 -V
没有 3.14 → 停下报，⛔ 不用 3.12/3.13 代替（`requirements.txt` 注释与 design D8 都写死「实测过的那份与装上的那份必须同一份」）。
  python3.14 -m venv tools/liaison/.venv
  tools/liaison/.venv/bin/python -m pip install -U pip
  tools/liaison/.venv/bin/python -m pip install -r tools/liaison/requirements.txt
装完报三行版本：`pip list` 里 `wecom-aibot-python-sdk`（应 1.0.2）、`pytest`（8.3.4）、`PyYAML`（6.0.3）。装不上 → 停下报原始错误全文，⛔ 不换版本、⛔ 不改 `requirements.txt`、⛔ 不往根 `requirements.txt` / `pyproject.toml` 加任何东西。

【三、在 tools venv 里首次真跑 SDK 相关测试】
  PYTHONPATH=. tools/liaison/.venv/bin/python -m pytest -q tools/liaison/tests
这是 `test_liaison_sdk_smoke.py` 第一次在装了 SDK 的环境里真跑（此前一直 skip）。
- 全绿 → 在报告里写明「SDK smoke 首次真跑通过，版本 1.0.2」
- 有红 → 停下报，把失败用例与原始输出全文贴出来，⛔ 不改测试去迁就实现、⛔ 不改 `session_client.py` 的常量来"让它过"

【四、重跑表面探针，核与既有 findings 是否一致】
  PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_ws_surface
把 JSON 原始输出全文抄进报告，并与 `docs/findings/2026-09-09-aibot-wsclient-表面实测.md` 逐项比对：
- 一致（`connect` 仍是协程函数、事件名仍是 connected/disconnected）→ 报告写「表面无变化」。🔴 **TD-19 仍留步，⛔ 本 session 不销它**——销账要等 8.6 用真实凭据把 `client.run()` 端到端跑通，那是另一条路
- 不一致 → 🔴 只落一份新 findings 记实测，⛔ 不动 `session_client.py` 的 `REQUIRED_CLIENT_ATTRS` / `EVENT_*` 常量（改它要 Shao Peishen 拍，因为 `SdkSurfaceUnverifiedError` 拒启动正是这条链唯一的护栏）

【五、登记号池】`docs/openers/OP-0820-全量编排.md` 的「🔢 号池台账」加两行：
1. 本号：`| 09-09 | `[Mac]0909AA` | 凭据存在性验证 + tools venv 建立 + SDK smoke 首跑 + 表面探针复核 | `docs/openers/0909AA-凭据验证与liaison-venv.md`（引用式） |`
2. 规则行（加在两条硬规则那一段末尾）：「🔴 **单字母用尽后续双字母**（`AA`、`AB`…）。2026-09-09 `0909` 的 A–Z 当天全部用尽，这是第一次发生。查重 grep 要同时认一字母与两字母：`grep -rhoE "\[Mac\]MMDD[A-Z]{1,2}"`。」

【六、提交】只 add 这些：`docs/openers/OP-0820-全量编排.md`、`docs/openers/0909AA-凭据验证与liaison-venv.md`、`docs/findings/`（若本轮新增）、`docs/session接力.md`（若回填）。
🔴 ⛔ **绝不 add**：`.env`、`tools/liaison/.venv`。提交前核 `git status --short` 里这两者一条都不出现（`.venv` 应被 `.gitignore` 挡住；若它冒出来 → 停下报，说明 ignore 规则有洞）。
⛔ 禁止 `git add -A` / `git add .` / `commit -a`。commit 后 push；被拒则 `git pull --rebase --autostash origin main` 重试最多 3 次。

【七、收工报告】含：三个变量的 len 三行、三条守卫结论、三个依赖版本、`tools/liaison/tests` 的 pytest 输出尾 3 行、探针 JSON 全文、与既有 findings 的逐项比对结论、TD-19 状态（预期＝仍留步）、commit hash。
并单列「灰度前置还差什么」：① 汤丽萍与邵培申的 `userid`（通讯录「账号」，留空＝fail-closed）② `tools/liaison/scripts/install_launchd.py` 由 Shao Peishen 在 Terminal 跑一次 ③ TD-20 改法已裁决、由第十五批 `0909U` 落地。
