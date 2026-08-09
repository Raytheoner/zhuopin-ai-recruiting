# M1 模型对比实测结论

> 状态：**待实测**。跑完 `scripts/compare_models.py` 后由工程师填写本文件，不得编造数字。

## 如何跑

```bash
export DEEPSEEK_API_KEY=...
export ARK_API_KEY=...
export DASHSCOPE_API_KEY=...
python -m scripts.compare_models
```

## 对比结果（跑完后填）

| 供应商 | schema_valid | 延迟(ms) | json_schema 支持 | 备注 |
|---|---|---|---|---|
| deepseek | | | 否（json_object 降级） | |
| doubao | | | 是 | |
| qwen | | | 否（json_object 降级） | |

## 决策

- **选定供应商**：（待填）
- **理由**：（待填，至少覆盖 schema 遵循度、延迟、单价三项）
- **写回配置**：把结果填进 `.env` 的 `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_SUPPORTS_JSON_SCHEMA`
