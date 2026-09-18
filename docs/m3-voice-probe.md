# M3 语音探针结果（U0）

## 探针结果

| 项 | 环境指纹 | 结论 | 关键指标 | 阻塞点 | 耗时 ms | 时间戳(UTC) |
|---|---|---|---|---|---|---|
| P1 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine&#124;company-wifi | 通过 | livekit_version=livekit-server version 1.13.7; turn_evaluated=False; two_client_data_channel=True |  | 2846 | 2026-09-18T02:21:30.522761+00:00 |
| P2 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine | 阻塞 |  | funasr 可导入（版本 1.4.15），但缺少 30s 中文样本音频（--audio-path data/m3-voice-probe/samples/sample-zh-30s.wav 不存在，需人工录制/提供，不入库） | 1693 | 2026-09-18T02:40:04.532941+00:00 |
| P3 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine | 阻塞 |  | cosyvoice 包不可导入: ModuleNotFoundError: No module named 'hyperpyyaml' | 23 | 2026-09-18T02:50:42.177882+00:00 |
| P4 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine | 阻塞 |  | Settings.llm_api_key 为空（.env 未配置 LLM_API_KEY），无法调用 DeepSeek | 9 | 2026-09-18T02:59:28.832816+00:00 |

