[Mac]1001G-PRD补全与UI升级
【设置】执行环境: Codex ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（文档与前端设计为主，主检出）｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）｜ 派发: [Mac]0930R

## 零、为什么（Shao Peishen 2026-10-07 转场定调）

上一场（`[Mac]0930R`）把泳道看护跑顺了，也把 `08-PRD.md` / `09-UI-Design.md` 的 **v1.0** 立了起来。
本场按他的新指示**调整节奏**：**具体业务先缓一缓**——

1. **先把「要建什么」写全**：按现有需求文档、已实现的 spec 与代码，补齐招聘智能体的**全部 PRD**；
   缺的必要内容由你补上（补出来的产品判断要标「🆕 建议」进待确认表，⛔ 不静默替他拍板）；
2. **再在完整 PRD 基础上做 UI 升级**：用 **@Product Design**（`product-design` 插件）把 HR 项目的界面
   从"能用"推到"产品级"；
3. 这两件做完、需求与 UI 框架都稳定了，**再回去跑业务泳道**（M1 9.1 人评、M2 三样输入那批）。

## 一、输入（开工先读，别凭记忆）

| 类别 | 位置 |
|---|---|
| 现有 PRD / UI 稿 | `08-PRD.md`（v1.0）、`09-UI-Design.md`（v1.0） |
| 需求与架构 | `02-系统架构与MVP范围.md`、`docs/roadmap/HR项目实施路线图与构建自动化流程.md`、`docs/roadmap/任务台账.yaml`、`openspec/changes/*/proposal.md｜design.md` |
| 已实现的契约（真源） | `openspec/specs/*`（13 个能力，含已归档包同步进来的） |
| 代码真身 | `app/**`（尤其是 `app/web/**`、`app/agents/**`、`app/graph/**`、`app/storage/**`）、`tools/liaison/**`、`scripts/**` |
| 决策与口径 | `docs/roadmap/定夺队列.md`、`docs/跟进信/口径点台账.md`、`docs/tech-debt.md`、`docs/compliance/` |
| 当前状态 | `docs/session接力.md` 的 🔴 节（2026-10-07 版） |

## 二、交付物 ①：完整 PRD（`08-PRD.md` → v2.0）

**目标**：把 PRD 从"阶段性记录"补成**可验收的全量产品需求**。

1. **功能需求全覆盖**（延续现有 `F` 编号体系，逐条给：编号｜优先级｜用户故事｜验收口径｜真身状态｜来源）：
   - M0 通信底座、M0′ 回件自动化（含 2026-09-30～10-07 的 `0930K` 群通知接线／`0930L` 队列回写／
     `1001A` 回推文案可读化 这三条增量）；
   - M1 需求解析与岗位画像（含 9.1 验收口径、AI 标识、硬门槛"只存不执行"）；
   - M2 简历解析与评分排序（六字段、低置信度隔离、硬门槛纯函数、BGE-M3＋rubric 精排＋evidence span、
     HR 工作台三页、批量确认幂等、四项指标口径、判例批改表）；
   - M3 实时语音面试（prep/live/post、身份核验隔离、声学信号不进评分）；
   - 二期四场景（渠道接入／面试排期／Offer／入职）——按 openspec 已立包的 proposal 与 design 写，标注"未开工"；
   - 构建自动化本身（泳道／调度器／闸门 G1–G5／提交与发车通道）——它是交付手段，PRD 里给出"它保证什么"的验收口径。
2. **两张缺口清单**（这是本条的硬交付，⛔ 不许省）：
   - 「**已实现、PRD 没写**」：从 `openspec/specs/*` 与 `app/**` 反查，逐条补进 PRD；
   - 「**PRD 写了、代码没实现**」：逐条给真身状态与阻塞原因（对齐 `docs/roadmap/任务台账.yaml`）。
3. **补缺**：凡是 PRD 必须写而现在没有的（例如：完整用户旅程与状态机、权限矩阵、异常与边界、
   指标口径与埋点、非功能指标的量化门槛、数据留存与删除的逐表口径），**由你补**；补的内容：
   - 与既有决策冲突时**以既有决策为准**，冲突处单列；
   - 属产品取向的新判断 ⇒ 标「🆕 建议：…」，并进 §10「待你拍板」表（每条写清"不确认会怎样"）。
4. 交付形态：`08-PRD.md` 升到 **v2.0**（头部写清 v1.0→v2.0 的变更摘要）；章节顺序可重排但要保留
   §2 非目标、§7 非功能、§10 待拍板、术语表；⛔ 全篇不许出现凭据/真实候选人信息。

## 三、交付物 ②：UI 升级（用 @Product Design）

**目标**：在 v2.0 PRD ＋ `09-UI-Design.md` v1.0（设计令牌／页面规格／6 条 UI 验收）基础上，
把现有 8 页推到产品级，并把 UI Design 文档升到 v2.0 回填实际。

1. **先审**：用 `product-design:audit` 对现有 8 个页面（`app/web/static/*.html`）出**截图取证 + 逐条结论**
   （信息架构、层级、可读性、状态覆盖、无障碍、与 v1.0 原则的偏差）。截图落 `data/eval/ui-audit/`
   （gitignored），结论落 `docs/findings/`。

   ⚠️ **先确认插件可达**：本场开头把 `product-design` 的 `index`/`audit` 技能名点一遍（`Skill(product-design:index)`）。
   若报未知技能/加载失败 ⇒ **停下报 Shao Peishen**，并登记「⏸ 留步：product-design 不可达」——
   ⛔ 不许退回"自己手写 CSS 硬做"，那会丢掉本条的审计取证与设计 QA 两层保证（同族教训：`superpowers` 不可达那条）。
2. **再定方向**：按 audit 结论给**一版设计方向**（不是一堆选项；同一结果只给一种做法）＋页面级改造清单
   （哪页改什么、为什么、验收什么）。需要视觉探索时用 `product-design:ideate`，产出的图落 `data/`。
3. **再落地**：用 `product-design:image-to-code`（或插件内等价路径）把方案落到 `app/web/static/**`。
   **硬约束**（`09-UI-Design.md` v1.0 已定，⛔ 不要推翻）：
   - ⛔ 不引第三方库/CDN/字体；共用层仍是 `app/web/static/app.css`（可扩展令牌与组件，⛔ 不改既有令牌取值）；
   - 品牌色沿用 `#0d6efd`（等 VI 色值再换）；⛔ 不做暗色；
   - 候选人两页（`interview_consent.html`／`interview_invite_issue.html`）375px 必须无横向滚动、按钮全宽、字号 ≥16px；
   - 全站子路径无关（`<!--BASE_HREF-->`＋`static/app.css`；⛔ 不硬编码 `/static`、`/api`）；
   - AI 标识必须**显著**（正常字号＋提示底色，⛔ 不做小字免责声明），既有文案不许删；
   - 改前端 **⛔ 不改后端接口语义**；确需改接口 ⇒ 先停下来在报告里说明，别顺手改。
4. **再复核**：`product-design:design-qa`（或 audit 复跑）＋机器判据：
   - `node scripts/ui_narrow_check.mjs`（本机 Chrome 无头实测，rc=0）；
   - `./venv/bin/python -m pytest tests/test_static_frontend.py tests/test_candidate_pages_narrow.py tests/test_resume_list_page.py tests/test_resume_review_page.py tests/test_upload_page.py tests/test_invite_web_e2e.py -q`；
   - 页面上原有的 AI 标识句仍可 grep 到（`AI 自动抽取`／`AI 生成`）。
5. **回填**：`09-UI-Design.md` 升 **v2.0**（新增组件/页面写进 §5/§6，把 audit 结论与"仍未做的"写进 §12 风险与 §13）。

## 四、前置（开工第一屏就跑，⛔ 别跳）

```bash
cd /Users/paulshao/Projects/HumanResource
git pull --rebase origin main && git log --oneline -3 && git status --short
./venv/bin/python -m pytest tests/test_doc_size_budget.py tests/test_static_frontend.py tests/test_root_dotenv_keys.py -q
ls docs/session接力.md 08-PRD.md 09-UI-Design.md
```

- 工作区**不干净**（除 `??` 的无关历史遗留）⇒ 先报，不要硬上；
- ⛔ **不许动 `.env`**：根 `.env` 只放 app 配置（`tests/test_root_dotenv_keys.py` 守着这条），
  liaison 的键在 `tools/liaison/.env`；需要 webhook/密钥一律找 Shao Peishen 本人。
- 需要联网跑 LLM 的脚本可以直接跑（2026-10-07 起 `run-lanes.sh` 已给泳道开 `network_access`；本会话更不受限）。

## 五、完成判据（逐条可核，⛔ 不许"看着差不多"）

| # | 判据 |
|---|---|
| 1 | `08-PRD.md` 头部为 v2.0，含 v1.0→v2.0 变更摘要；功能需求覆盖 §二.1 六类，每条有验收口径与真身状态 |
| 2 | 两张缺口清单（已实现未写／已写未实现）在 PRD 内成节，条目数写进报告 |
| 3 | 新增/补齐内容里，凡属产品取向的都在 §10「待你拍板」列出，且每条写明"不确认会怎样" |
| 4 | `docs/findings/` 有本次 UI audit 记录（含截图路径与逐条结论），截图在 `data/eval/ui-audit/` |
| 5 | 8 页改造后：`node scripts/ui_narrow_check.mjs` rc=0，且 §三.4 的 pytest 子集全绿 |
| 6 | `09-UI-Design.md` 头部为 v2.0，新组件/页面已回填，未做项写明 |
| 7 | 报告里给出：改动的文件清单、每条判据的实测输出尾行、以及"下一步可发的业务泳道建议" |

## 六、红线

- ⛔ **不可代项**（合规红线七条、候选人对外通道、`.51` 发版、真实简历处理范围、预算采购）只登记不拍，
  一律进 `docs/roadmap/定夺队列.md`；
- ⛔ 不发任何对外消息（含企微、邮件）；
- ⛔ 不碰 `data/` 里的真实材料与 `data/liaison.db`／`data/demo.db`；截图与设计中间产物一律落 `data/`（gitignored）；
- ⛔ 不删既有文案与 AI 标识、⛔ 不引第三方前端依赖、⛔ 不动 `run-lanes.sh` 等执行器；
- 改前端代码要**先跑判据再提交**；提交走仓库既有通道（`docs/**` 用提交通道；`app/**`／`tests/**`
  需主会话直执＋用户批准，按 `CLAUDE.md` 并发协议：只 `git add` 本条明确列出的路径）。

## 七、收口

1. 两件交付物全部达成后，更新 `docs/session接力.md` 的 🔴 节（当前态＋下一步＋仍缺 Shao Peishen 的输入）；
2. 按 `CLAUDE.md`「会话末需你定夺」给**两栏**汇报（状态同步／需你定夺），需要他答的逐条给 `(a)/(b)` 与推荐；
3. 收工前把「业务泳道下一步」列出来（M1 9.1 人评回收、M2 三样输入），方便下一场直接接上。
