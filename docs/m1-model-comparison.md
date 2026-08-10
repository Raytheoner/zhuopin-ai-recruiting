# M1 模型对比实测结论

> 状态：**单供应商实测完成（2026-08-09），同日追加 deepseek-v4-flash vs deepseek-v4-pro 对比**。
> 当前只有 DeepSeek 的 API key，火山方舟（doubao）和阿里百炼（qwen）尚未注册账号，
> **这轮不是三方对比，只是 DeepSeek 单供应商验证**。doubao / qwen 两行的模型名和
> `json_schema` 支持情况仍是候选阶段的占位猜测，未经实测，不代表结论——两家 key 到位后
> 必须重跑本脚本，再回来更新本文件。

## 如何跑

```bash
export DEEPSEEK_API_KEY=...
# ARK_API_KEY / DASHSCOPE_API_KEY 暂缺，脚本会优雅跳过对应供应商（标记 skipped，不计入 disqualified）
python -m scripts.compare_models
```

## 模型名核实（工程铁律 5）

`GET https://api.deepseek.com/v1/models` 实测返回的可用模型列表：

```json
{"object":"list","data":[{"id":"deepseek-v4-flash","object":"model","owned_by":"deepseek"},{"id":"deepseek-v4-pro","object":"model","owned_by":"deepseek"}]}
```

原脚本里写死的 `deepseek-chat-241226`（以及 `.env.example` 里的 `deepseek-chat`）**不存在**——DeepSeek
已把命名体系换成 `deepseek-v4-flash` / `deepseek-v4-pro`。本轮选用 `deepseek-v4-pro`（旗舰款，结构化抽取更看重准确率而非速度）作为首轮实测与配置对象；`deepseek-v4-flash` 已在同日追加对比，见下方
[「flash vs pro 对比」](#flash-vs-pro-对比同步等待场景2026-08-09)。

调用响应里的 `model` 字段实测原样回显请求值：请求 `deepseek-v4-pro`，响应 `model` 字段也是
`"deepseek-v4-pro"`。**这句话不能证明"没有静默漂移"——回显的是你请求时传入的别名，
DeepSeek 换掉 `deepseek-v4-pro` 别名底下的实际模型时，`model` 字段照样原样回显
`"deepseek-v4-pro"`，这是别名机制本身决定的，不是探测漂移的信号。见下一节。

## system_fingerprint（版本漂移的唯一可信信号）

实测确认 DeepSeek 的响应体里带 OpenAI 兼容 API 常见的 `system_fingerprint` 字段，
本轮抓到的真实值形如 `fp_9954b31ca7_prod0820_fp8_kvcache_20260402`——这个字段随底层
模型/部署变化，`model` 字段不会。

处理方式：[gateway.py](../app/llm/gateway.py) 的 `extract_structured` 已经从响应里取
`system_fingerprint`（`getattr(response, "system_fingerprint", None)`，供应商不返回时
老实记 `None`，不报错），和 `model` / 响应 `model` 字段一起传给 `AuditHook.record()`
持久化（见 [tests/test_llm_gateway.py](../tests/test_llm_gateway.py) 的
`test_audit_hook_records_system_fingerprint_when_present` /
`test_audit_hook_records_none_system_fingerprint_when_absent`）。

**这不代表铁律 5 的"版本可复现"问题已解决**：`AuditHook` 目前仍只有 `NoopAuditHook`
（只打日志不落库，是既有技术债，见计划开头），`system_fingerprint` 现在只是"接口已经
打通、字段已经能拿到"，等真正接数据库的 `AuditHook` 实现落地后，才谈得上"能查某条
评分对应哪个 `system_fingerprint`"。另外 `system_fingerprint` 本身也只是启发式信号
（同一实际模型在不同请求间是否 100% 稳定尚未经过跨天验证），不是供应商给出的正式版本号
承诺——比无更好，但不是万能药。

## json_schema 支持实测（本次最重要产出）

对 `deepseek-v4-pro` 分别测试两种 `response_format`：

| response_format | 结果 |
|---|---|
| `{"type":"json_schema", ...}` | **不支持**。API 返回 `400 Bad Request`：`"This response_format type is unavailable now"` |
| `{"type":"json_object"}`（对照） | 支持，调用成功，返回内容通过 `JobProfile` pydantic 校验 |

结论：`LLM_SUPPORTS_JSON_SCHEMA=false`，网关按 [gateway.py](../app/llm/gateway.py) 的降级路径走
`json_object` + 把 Schema 写进 system prompt。

## 对比结果

| 供应商 | schema_valid | 延迟(ms) | json_schema 支持 | 备注 |
|---|---|---|---|---|
| deepseek-pro | true | 三次中位数 13527（明细见下节） | 否（json_object 降级，已实测确认） | 模型：`deepseek-v4-pro`；响应 `model` 字段与请求一致 |
| deepseek-flash | true | 三次中位数 11595（明细见下节） | 否（沿用 pro 的实测结论，未独立重测） | 模型：`deepseek-v4-flash` |
| doubao | — | — | 未实测（无 API key） | 脚本运行时该行 `skipped`，模型名/`json_schema` 仍是候选占位值 |
| qwen | — | — | 未实测（无 API key） | 同上 |

## flash vs pro 对比（同步等待场景，2026-08-09）

**为什么要重新对比**：上一轮"延迟量级在个位数秒到十几秒之间，对招聘场景（异步抽取，非用户
实时等待）可接受"这个判断用错了前提——M1 demo 的真实使用方式是业务经理在浏览器里**同步等待**：
提需求 → 等一轮追问 → 回答 → 再等一轮，最多 5 轮加 JD 生成，接近一分钟盯着转圈。延迟不是
"能不能接受"的问题，是能不能过 demo 这一关的问题。

**跑法**：`PROVIDER_CANDIDATES` 里的 `deepseek-pro` / `deepseek-flash` 两项，用同一个
`SAMPLE_REQUIREMENT`、`max_retries=0`，每个模型各跑 3 次（`run_comparison` 每次只测一个供应商，
取 `schema_valid=true` 的延迟中位数）。

### 延迟

| 模型 | 3 次延迟(ms) | 中位数(ms) |
|---|---|---|
| deepseek-v4-pro | 14241 / 13527 / 9918 | **13527** |
| deepseek-v4-flash | 11595 / 7376 / 26033 | **11595** |

flash 中位数比 pro 低约 14%，但**不构成"延迟明显更低"**：flash 自身三次的波动幅度
（7376～26033ms，约 3.5 倍）比 pro 的波动幅度（9918～14241ms）大得多，flash 最慢的一次
（26033ms）比 pro 最慢的一次（14241ms）还要慢将近一倍。3 次样本量下，两者的延迟区间高度
重叠，不能下"flash 更快"这个结论——需要更大样本量才能判断是真实差异还是噪声。

### 字段质量（人工比对 ECU 特化字段）

两个模型各 3 次输出里，`autosar_experience` 全部正确识别为 `["CP"]`、`functional_safety`
全部正确留空为 `"无"`（输入原文只提到"最好懂 AUTOSAR"，没说 CP/AP 或功能安全等级，两个模型
都做了合理默认，没有编造更具体的信息）。差异出现在 `mcu_family` / `toolchain` 这类输入完全
没提到的字段：

- **pro**：3 次里 1 次（run 1）**编出**了 `mcu_family: ["ARM Cortex-M", "Infineon TriCore"]`
  和 `toolchain: ["Keil", "IAR", "Eclipse-based IDE"]`——这些是输入原文完全没有的具体型号/工具名，
  另外 2 次两个字段都是空。同一模型 3 次输出在"是否编造未提及的具体信息"上不一致。
  （`unspecified_fields` 字段设计上应该用来标记这类"未指定，用默认值填充"的字段，但 6 次输出
  没有一次用到它——这是 prompt/schema 层面的既有问题，与 flash/pro 选型无关，记一笔待跟进。）
- **flash**：3 次 `mcu_family` / `toolchain` 全部老实留空，没有编造具体型号；run 3 额外生成了
  `project_experience_requirement`（复述"有嵌入式驱动开发经验，熟悉 AUTOSAR 者优先"），这是
  对输入的合理转述而非编造具体项目/公司信息，可接受。

字段质量结论：两者可接受，**flash 在"未提及字段不编造具体值"上比 pro 更稳一些**（pro 出现过
一次编造，flash 没有），质量不是 flash 的短板。

### 结论：flash 够不够用？

判据是"字段质量可接受 且 延迟明显更低"——字段质量这条 flash 满足（甚至略优于 pro），但
**延迟这条不满足**：14% 的中位数差异被 3.5 倍的波动幅度盖过，不能算"明显更低"。

**更重要的发现**：无论选 flash 还是 pro，单次抽取都在 7～26 秒区间，5 轮追问 + JD 生成的同步
等待场景下，总等待时间大概率落在 40～70+ 秒——这已经不是"选哪个模型"能解决的问题，而是当前
"每轮同步调用一次 LLM、前端转圈等结果"这个交互模式在 demo 场景下站不住脚。是否需要换更快的
供应商、减少轮次、或者改成流式/异步 UX，是比 flash/pro 选型更大的问题，本轮未展开，留给决策者
定夺。

## 决策

- **本轮结论的性质**：单供应商验证 + 同供应商内 flash/pro 对比，仍不是"跨供应商对比后选优"。
  doubao/qwen 完全没跑过，谈不上"比较后选中 deepseek"——只是当前唯一可用的选项先接上，让后续
  开发（PaddleOCR/MinerU 抽取链路等）能继续推进。
- **flash vs pro 的选型**：数据不支持"flash 明显更快"，两者延迟区间重叠，字段质量都可接受
  （flash 编造倾向略低）。**留给决策者定夺**，本文件不替决策者下结论。
- **flash vs pro 的最终结论**：flash 与 pro 的延迟差异在 n=3 时不显著，不构成选型理由。但实测中
  pro 有 1/3 概率（3 次里 1 次）抽出输入未提及的 MCU 型号与工具链，flash 3 次均留空。对本项目而言
  编造倾向比延迟更关键——画像里多出未提及的硬性要求会导致寻源方向偏，且业务经理看到一份专业画像
  时不会逐字核对哪些是自己说的，很难被发现。**结论：demo 阶段用 flash，待 pilot 反馈后再评估。**
- **理由（选 DeepSeek 而非其他供应商）**：唯一持有 API key 的供应商；实测 schema 遵循度通过
  （json_object + prompt 内嵌 schema 路径）；单价未纳入本轮对比（无其他供应商价格做参照，比较
  没有意义）。
- **待办**：
  1. 注册火山方舟（doubao）和阿里百炼（qwen）账号拿到 key 后，重跑
     `python -m scripts.compare_models`，把本文件的 doubao/qwen 两行换成真实实测数据，再重新
     评估是否换供应商（尤其 doubao 若 `json_schema` 真支持，抽取质量可能优于 json_object 降级
     路径）。
  2. flash vs pro 的延迟对比只有 3 个样本、波动很大，若要下更确定的结论需要更大样本量重跑。
  3. `unspecified_fields` 字段在 6 次实测输出里从未被使用——prompt 没有有效引导模型区分
     "有依据的默认值"和"该标记为未指定"，需要单独排查（与本轮 flash/pro 选型无关）。
  4. 无论最终选 flash 还是 pro，7～26 秒的单次延迟对"5 轮同步追问 + JD 生成"这个 demo 场景
     都偏慢，需要评估是否要换交互模式（减少轮次/流式反馈/异步通知）而不只是换模型。
- **写回配置**：`.env` 的 `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` /
  `LLM_SUPPORTS_JSON_SCHEMA` 五项待决策者选定 flash/pro 后再更新（`LLM_API_KEY` 只在本地 `.env`
  里填，不进版本库）。**决策已定（见上）**，`.env` 与 `.env.example` 的 `LLM_MODEL` 已同步改为
  `deepseek-v4-flash`。
