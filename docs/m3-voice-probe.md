# M3 语音探针结果（U0）

## 探针结果

| 项 | 环境指纹 | 结论 | 关键指标 | 阻塞点 | 耗时 ms | 时间戳(UTC) |
|---|---|---|---|---|---|---|
| P1 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine&#124;company-wifi | 通过 | livekit_version=livekit-server version 1.13.7; turn_evaluated=False; two_client_data_channel=True |  | 2846 | 2026-09-18T02:21:30.522761+00:00 |
| P2 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine | 阻塞 |  | funasr 可导入（版本 1.4.15），但缺少 30s 中文样本音频（--audio-path data/m3-voice-probe/samples/sample-zh-30s.wav 不存在，需人工录制/提供，不入库） | 1693 | 2026-09-18T02:40:04.532941+00:00 |
| P3 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine | 阻塞 |  | cosyvoice 包不可导入: ModuleNotFoundError: No module named 'hyperpyyaml' | 23 | 2026-09-18T02:50:42.177882+00:00 |
| P4 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine | 阻塞 |  | Settings.llm_api_key 为空（.env 未配置 LLM_API_KEY），无法调用 DeepSeek | 9 | 2026-09-18T02:59:28.832816+00:00 |
| P5 | macOS-27.0-arm64-arm-64bit-Mach-O&#124;py3.14.6&#124;dev-machine | 通过 | recommended_python=py314; reports={'py314': {'pip_ok': True, 'pip_error': None, 'import_ok': True, 'import_error': None}, 'py312': {'pip_ok': True, 'pip_error': None, 'import_ok': True, 'import_error': None}} |  | 87544 | 2026-09-18T06:12:53.127139+00:00 |

## 结论

> 由 Task 7 依据上面「探针结果」表的真实数据下判断。

- **live 段（U4）**：留步。依据 tasks.md 0.2 的开闸判据（P2/P3 任一阻塞 ⇒ U4 留步），当前 P2、P3 两行（`env_fingerprint=...|dev-machine`）均为「阻塞」，尚未有一次通过结果，U4 暂不发车；不影响其余单元（U1/U2/U3/U5/U6/U7 不依赖本结论）。
- **P1 LiveKit**：通过（`dev-machine|company-wifi`）。`livekit_version=livekit-server version 1.13.7`，两个浏览器客户端数据通道建连成功（`two_client_data_channel=True`）；`turn_evaluated=False`——开发机无公网 IP，做不了真实 NAT 穿越测试，TURN 结论留待目标机复测（与 design.md 既定预期一致）。
- **P2 FunASR**：阻塞（`dev-machine`）。阻塞原因是**测试数据缺失**，不是 D15 的兜底条件（不可装/延迟超预算）：`funasr` 本身可正常导入（版本 1.4.15），但本机没有 30 秒中文语音样本（`data/m3-voice-probe/samples/sample-zh-30s.wav` 不存在），未能跑到首字延迟测量这一步。补一份样本音频即可重跑拿到真实延迟数据。
- **P3 CosyVoice**：阻塞（`dev-machine`）。同样不是 D15 兜底条件触发：`cosyvoice` 包因缺少依赖 `hyperpyyaml` 而无法导入（`ModuleNotFoundError`），尚未进入模型加载与推理计时阶段。补装该依赖（及其余 CosyVoice 依赖链）后可重跑。
- **P4 追问选择 TTFT**：只有 `dev-machine` 一行，阻塞——`Settings.llm_api_key` 为空（`.env` 未配置 `LLM_API_KEY`），无法调用 DeepSeek。`.51` 一行 ⏸ 留步：本次执行环境无 `.51` 访问权限与 API key，design.md 要求的"经现网关对 DeepSeek 跑 20 次"实测需在 `.51` 上补跑。
- **P5 livekit-agents SDK**：通过（`dev-machine`）。`recommended_python=py314`，且 3.14／3.12 两个解释器均 `pip_ok=True`、`import_ok=True`，与 `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md` 的既有结论一致；语音主机可选用 3.14 或 3.12，优先 3.14。
- **目标机规格建议（CPU/GPU、内存、带宽）**：暂无法给出——P2、P3 两项探针都在进入耗时/资源测量前就阻塞（P2 缺样本音频、P3 缺依赖包），没有产出可用于定容的实测数据（无 CPU/内存占用记录）；P5 的 87544 ms 耗时是「创建两个 venv + pip 装包」的一次性安装耗时，不代表运行时资源占用，不能用于定容。此项需在补齐 P2 样本音频与 P3 依赖后重跑，拿到真实延迟与资源占用数据，才能作为 0.4 采购的输入；带宽方面本轮未测（P1 只验证了本机内建连，无跨网实测数据）。

