# 2026-09-09 Shao Peishen 两条裁决落档：无备用 LLM 供应商 ＋ aibot 不与 Windows 共用

> 依据：Shao Peishen 2026-09-09 在 Cowork `HR业务线-接力0903B` 会话口述。本文只记结论与依据，⛔ 不含任何凭据取值。

## 1. 备用 LLM 供应商：不要了，只用 DeepSeek

- **结论**：`.51` 与本机 `.env` 的 `LLM_FALLBACK_API_KEY / LLM_FALLBACK_BASE_URL / LLM_FALLBACK_MODEL / LLM_FALLBACK_SUPPORTS_JSON_SCHEMA` **全部留空**。
- **代码侧无需改动**：`app/config.py` 四项默认空＝「不配就是没有备用」，`app/llm/gateway.py::_build_fallback` 三项全空时返回 `None`，网关行为与 WBS 2.3 落地前逐字一致；主供应商故障时不切换、直接抛 `LLMProviderUnavailable`，随后由 WBS 2.5 的「重试耗尽转 `needs_manual`」兜底。
- **代价（他知情）**：DeepSeek 抖动时没有第二家接力，受影响的会话进人工队列而不是自动续跑。09-03 回放实测单轮延迟均值 34–65 s、最大 132 s，本来就在人工可接受边界，接受。
- **作废项**：Cowork 09-08 推荐的「阿里云百炼 qwen-plus」采购建议作废；`docs/session接力.md`「等 Shao Peishen 的三件」之①销号。

## 2. 企微 aibot：⛔ 不与 Windows 侧共用，Mac 侧独立注册

- **他的提问**：机器人 ID 跟 Windows 共用一个是否可以？
- **结论**：不可以。design.md D1 的结论**由官方文档坐实**：企微《智能机器人长连接》文档「连接数量限制」一节明写——**每个智能机器人同一时间只能保持一个有效的长连接；同一机器人发起新连接并完成 `aibot_subscribe` 时，新连接会踢掉旧连接**，并建议「在业务层面避免同一机器人建立多个长连接」。
  来源：https://developer.work.weixin.qq.com/document/path/101463 （2026-09-09 取证）
- **共用的后果**：Mac 侧值守服务一上线就会顶掉 Windows 侧那套（现役服务质量/采购/财务三个部门），Windows 侧 launchd/计划任务重连后又把 Mac 顶掉——两边**乒乓式静默中断**，谁都收不全消息。D1 猜的失败模式「后连的顶掉先连的」正是官方行为。
- **处置**：Shao Peishen 在企微管理后台**新建一个** aibot 应用给 Mac 值守用，把新 BotID/Secret 自己写进本机 `.env`（`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET`）。09-09 贴进聊天的那一对是 Windows 侧现役凭据，⛔ Mac 侧任何文件不得使用。
- **顺带**：那对 Windows 侧凭据与群 webhook 地址已出现在聊天记录里（违反 09-08 定的「凭据不进聊天」口径）。Cowork 侧未复述、未落档、未写进任何文件；是否在企微后台重置 Secret / webhook key 由他决定（重置需同步改 Windows 侧配置）。

## 3. 群 webhook（人力AI保障群）

- 地址由他自己写进本机 `.env`：`HR_LIAISON_GROUP_WEBHOOK=<地址>`。变量名以 `.env.example` 为准（第十四批 `0909I` 会把这一行变量名加进 `.env.example`）。
- 真实投递验证放在 8.6 单机灰度（名单只有他自己时发第一条），⛔ 不在泳道里、不在验证 opener 里真发。
