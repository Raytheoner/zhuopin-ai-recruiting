# M1 模型对比实测结论

> 状态：**单供应商实测完成（2026-08-09）**。当前只有 DeepSeek 的 API key，火山方舟（doubao）
> 和阿里百炼（qwen）尚未注册账号，**这轮不是三方对比，只是 DeepSeek 单供应商验证**。
> doubao / qwen 两行的模型名和 `json_schema` 支持情况仍是候选阶段的占位猜测，未经实测，
> 不代表结论——两家 key 到位后必须重跑本脚本，再回来更新本文件。

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
已把命名体系换成 `deepseek-v4-flash` / `deepseek-v4-pro`。本轮选用 `deepseek-v4-pro`（旗舰款，结构化抽取更看重准确率而非速度）作为实测与配置对象；`deepseek-v4-flash` 未测，如后续要评估更低延迟/成本的选项需另行实测。

调用响应里的 `model` 字段实测原样回显请求值：请求 `deepseek-v4-pro`，响应 `model` 字段也是
`"deepseek-v4-pro"`（未发生供应商静默升级/别名漂移）。

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
| deepseek | true | ~9400（单次实测，波动较大，另一次同请求测得约 17300） | 否（json_object 降级，已实测确认） | 模型：`deepseek-v4-pro`；响应 `model` 字段与请求一致 |
| doubao | — | — | 未实测（无 API key） | 脚本运行时该行 `skipped`，模型名/`json_schema` 仍是候选占位值 |
| qwen | — | — | 未实测（无 API key） | 同上 |

## 决策

- **本轮结论的性质**：单供应商验证，不是"对比后选优"。因为只有 DeepSeek 一家的 key，doubao/qwen
  完全没有跑过，谈不上"比较后选中 deepseek"——只是当前唯一可用的选项先接上，让后续开发（PaddleOCR/
  MinerU 抽取链路等）能继续推进。
- **选定供应商（临时）**：deepseek，模型 `deepseek-v4-pro`。
- **理由**：唯一持有 API key 的供应商；实测 schema 遵循度通过（json_object + prompt 内嵌 schema 路径）；
  延迟量级在个位数秒到十几秒之间，对招聘场景（异步抽取，非用户实时等待）可接受；单价未纳入本轮对比
  （无其他供应商价格做参照，比较没有意义）。
- **待办**：注册火山方舟（doubao）和阿里百炼（qwen）账号拿到 key 后，重跑
  `python -m scripts.compare_models`，把本文件的 doubao/qwen 两行换成真实实测数据，再重新评估是否
  换供应商（尤其 doubao 若 `json_schema` 真支持，抽取质量可能优于 json_object 降级路径）。
- **写回配置**：`.env` 的 `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` /
  `LLM_SUPPORTS_JSON_SCHEMA` 五项按本次实测结果更新（`LLM_API_KEY` 只在本地 `.env` 里填，不进版本库）：
  - `LLM_PROVIDER=deepseek`
  - `LLM_API_KEY=`（本地 `.env` 中已有的真实 key，不在此文档或 git 历史中出现）
  - `LLM_BASE_URL=https://api.deepseek.com/v1`
  - `LLM_MODEL=deepseek-v4-pro`
  - `LLM_SUPPORTS_JSON_SCHEMA=false`
