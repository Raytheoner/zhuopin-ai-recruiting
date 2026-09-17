# M2 模型对比实测（U0）

> 状态：**进行中**（2026-09-17 建骨架，同日完成本机实跑）。定型须 Shao Peishen 签认（tasks 1.7）：签认后在文末「已确认」填日期。
> 方法沿用 `docs/m1-model-comparison.md`：模型标识只认响应侧 `model` 字段，`system_fingerprint` 是漂移的唯一可信信号，`temperature=0`，只看首次不吃重试红利。
> 裁决前提（2026-09-17 Shao Peishen）：BGE-M3 在 `.51` 本地 CPU（design D7）；扫描件用 PaddleOCR（D14）；置信度阈值 0.7 起步由本文实测定终值（D5）。

## 如何跑

```bash
set -a; [ -f .env ] && source .env; set +a          # DEEPSEEK_API_KEY / ARK_API_KEY / DASHSCOPE_API_KEY
venv/bin/python -m scripts.gen_pilot_samples --out data/eval/m2-pilot          # 合成样本（真实脱敏样本到位后替换同目录）
venv/bin/python -m scripts.smoke_m2_deps --json data/eval/m2-pilot/smoke-$(uname).json
venv/bin/python -m scripts.compare_models_m2 --samples data/eval/m2-pilot --out data/eval/m2-pilot/compare-run.md --json data/eval/m2-pilot/compare-run.json
venv/bin/python -m scripts.compare_models_m2 --ocr-check                       # 扫描件 OCR 核对（需 PaddleOCR）
HF_ENDPOINT=https://hf-mirror.com venv/bin/python -m scripts.bench_bge_m3 --json data/eval/m2-pilot/bench-bge-m3.json
```

注（Mac，2026-09-17 实跑）：macOS 无 `timeout` 命令，本机改用后台 nohup 脚本跑长任务（`data/eval/m2-pilot/run-compare.sh`、`run-bench.sh`，已 `.gitignore`）；上面命令按计划原样保留，在 macOS 上跑时去掉 `timeout` 前缀或换等价包装即可。

## 环境（tasks 1.1）

### Mac（开发机）

平台：`macOS-27.0-arm64-arm-64bit-Mach-O` ｜ Python 3.14.6

| 包 | 可导入 | 版本 | 体积 MB | import 耗时 ms | 错误 |
|---|---|---|---|---|---|
| numpy | ✅ | 2.5.3 | 32.9 | 43 | |
| pypdf | ✅ | 6.19.0 | 4.0 | 36 | |
| python-docx | ✅ | 1.2.0 | 2.4 | 21 | |
| reportlab | ✅ | 5.0.1 | 8.5 | 4 | |
| pillow | ✅ | 12.3.0 | 14.9 | 3 | |
| pymupdf | ✅ | 1.28.2 | 60.8 | 40 | |
| paddlepaddle | ❌ | | | | ModuleNotFoundError: No module named 'paddle' |
| paddleocr | ❌ | | | | ModuleNotFoundError: No module named 'paddleocr' |
| torch | ✅ | 2.14.0 | 587.3 | 591 | |
| FlagEmbedding | ✅ | 1.4.2 | 2.1 | 2385 | |

`pip install paddlepaddle==3.4.0 paddleocr==3.7.0` 失败（`pip-paddle.log`）：

```
ERROR: Could not find a version that satisfies the requirement paddlepaddle==3.4.0 (from versions: none)
ERROR: No matching distribution found for paddlepaddle==3.4.0
exit=1
```

cp314（本机 Python 3.14）无 `paddlepaddle` wheel，与计划「待裁决」#6 实测一致。`FlagEmbedding==1.4.2` 安装成功（`pip-flagembedding.log`），一并装入 torch 2.14.0 等依赖。

全量 `pytest`：`1 failed, 2714 passed, 6 skipped`。唯一失败 `tools/liaison/tests/test_criteria_ledger_file.py::test_signed_off_row_has_no_evidence_yet`——主仓库 main 上同样红，是 liaison 泳道既有问题，与本单元无关，未修。

### `.51` 同款 Windows
⏸ 留步：需在 `.51` 同款 Windows venv 上实跑 `python -m scripts.smoke_m2_deps`，本节由该次运行填写。重依赖三项（paddlepaddle／paddleocr／FlagEmbedding）在 Python 3.14 + win_amd64 是否有 wheel 是第一问；paddle 装不上 ⇒ 走 D14 退路并登记技术债。

## 样本

- 来源类别：`synthetic`（`scripts/gen_pilot_samples.py`，seed 20260917，20 份，无任何真人信息）。⚠️ 合成样本上的指标只证明管线、schema 守字段率、evidence 可定位率与延迟成本；**D9 验收数字必须在真实脱敏样本上重算**（计划「待裁决」#2）。
- 文件形态：txt／docx／文本型 pdf（reportlab STSong-Light）／扫描 pdf（Pillow 渲染，Mac 有中文字体时生成）。

## 候选模型

| 候选 | 配置侧模型 | json_schema | 定价（元/百万 token，入/出，抄录日期） | key 状态 |
|---|---|---|---|---|
| deepseek-pro | deepseek-v4-pro | ✗（M1 实测） | 未抄录 | ✅ 本机已配置 |
| deepseek-flash | deepseek-v4-flash | ✗ | 未抄录 | ✅ 本机已配置 |
| doubao | doubao-seed-2-1-turbo-241215（未核实） | ？（未跑） | 未抄录 | ❌ 缺 `ARK_API_KEY` |
| qwen | qwen3.7-plus-241226（未核实） | ？（未跑） | 未抄录 | ❌ 缺 `DASHSCOPE_API_KEY` |

## 对比结果（tasks 1.3／1.4）

生成时间：2026-09-17T08:08:27.304921+00:00 ｜ 样本数：20 ｜ prompt 版本：parse-u0-v1 / rank-u0-v1 ｜ temperature=0

| 候选 | 模型标识（响应侧） | fingerprint | 解析成功 | 字段准确率 | Spearman | Top-K 召回 | evidence 可定位率 | 精排成功 | 解析 P50/P95 ms | 精排 P50/P95 ms | tokens 入/出 | 成本（元） | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 门槛（D9） | | | | ≥90% | ≥0.70 | ≥85% | 100% | | | | | | |
| deepseek-pro | deepseek-v4-pro | a307abda487cd1b463329ccb945ce396 | 20/20 | 100.0% | 0.24 | 60.0% | 100.0% | 20/20 | 43915/89704 | 64352/145143 | 51376/133880 | 未填价格 |  |
| deepseek-flash | deepseek-flash | aeb56401ca74e127821c4f9126dcb669 | 20/20 | 100.0% | 0.26 | 70.0% | 100.0% | 20/20 | 1835/2811 | 8207/16238 | 49336/45182 | 未填价格 |  |
| doubao | （未跑） | | | | | | | | | | | | 跳过：环境变量 ARK_API_KEY 未设置 |
| qwen | （未跑） | | | | | | | | | | | | 跳过：环境变量 DASHSCOPE_API_KEY 未设置 |

逐字段准确率：

| 候选 | 姓名 | 工作年限 | 技能列表 | 公司经历 | 教育（最高学历与院校） | 期望城市 |
|---|---|---|---|---|---|---|
| deepseek-pro | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| deepseek-flash | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

注：Spearman 为 — 表示可计算样本 < 10（D9：不出结论）；成本按 `ModelCandidate` 里抄录的定价计算，本次未抄录价格，成本一律「未填价格」。

⚠️ **响应侧模型名与配置不一致**：deepseek-flash 请求侧配置为 `deepseek-v4-flash`，但响应体 `model` 字段实际返回 `deepseek-flash`（fingerprint `aeb56401ca74e127821c4f9126dcb669`）。按铁律 5，响应侧取回的值才算数，已按 `deepseek-flash` 落「候选模型」与本表。deepseek-pro 响应侧与配置一致（`deepseek-v4-pro`，fingerprint `a307abda487cd1b463329ccb945ce396`）。

审计留痕 JSONL（铁律 3，每次调用一行，不进版本库）：`data/eval/m2-pilot/runs/deepseek-pro/20260917T072137Z.jsonl`（40 行）、`data/eval/m2-pilot/runs/deepseek-flash/20260917T080446Z.jsonl`（40 行）。

doubao／qwen 本机跳过：`ARK_API_KEY` / `DASHSCOPE_API_KEY` 均未设置。

## BGE-M3 本地 CPU 召回（tasks 1.5）

模型：`BAAI/bge-m3`（backend=`flag`，FlagEmbedding 1.4.2 + torch 2.14.0 CPU）｜ 平台：`macOS-27.0-arm64-arm-64bit-Mach-O`

样本 20 份 ｜ 维度 1024 ｜ 画像向量 ~205 ms（207.7 / 202.5 ms 两次实跑）｜ 单份简历 ~58 ms（59.3 / 57.6 ms）

- top-10（真值 = 人工排序前 10）：recall@10 = 40.0%
- top-20（真值 = 人工排序前 20，即全部 20 份）：recall@20 = 100%——n=20 时该数字必然平凡（真值集合等于全体样本），**计划 1.5 要求的 top-30 召回率在本合成样本上无意义**（样本总数仅 20 份，top-30 恒为 100%），需在真实脱敏样本（≥20 份且候选池更大）上重算。

| 名次 | 样本 | cosine |
|---|---|---|
| 1 | S20 | 0.6762 |
| 2 | S08 | 0.6755 |
| 3 | S14 | 0.6622 |
| 4 | S15 | 0.6544 |
| 5 | S03 | 0.6451 |
| 6 | S16 | 0.645 |
| 7 | S19 | 0.6436 |
| 8 | S01 | 0.6425 |
| 9 | S04 | 0.642 |
| 10 | S07 | 0.6306 |

模型权重下载（BAAI/bge-m3，约 30 个仓库文件含多套重复权重格式）：`HF_ENDPOINT=https://hf-mirror.com` 镜像在本机（美区网络）下载卡在 107 MB / 10 分钟后放弃，改直连 `huggingface.co` 用时 17 分钟拉全；本机 HF 缓存落盘 4.3 GB。备注（非决策，登记供生产参考）：`snapshot_download` 默认拉取仓库全部文件，生产部署应通过 `allow_patterns` 限制到实际推理所需的权重格式，估算可压到约 2.3 GB。

`.51` Windows CPU 的画像/单份耗时数字 ⏸ 留步（同 1.1，需在 `.51` 上实跑）。

## 扫描件 OCR（D14）

`--ocr-check`：20 行，全部返回

```
OcrUnavailable: PaddleOCR/PyMuPDF 未安装: No module named 'paddleocr'
```

（预期结果：`paddlepaddle`/`paddleocr` 在本机 cp314 上无法安装，见「环境」节 pip 报错。）按 D14 退路处理：扫描件解析失败进人工队列，登记技术债，待裁决 #6 保持开放。本次无法给出 OCR 输出与人工真值的相似度数据（20 份全部不可用）。

## 决策（tasks 1.6；每项必须有上面的数据支撑）

| 项 | 结论 | 数据支撑 |
|---|---|---|
| 抽取模型 | 待数据（合成样本上 deepseek-pro／deepseek-flash 字段准确率均 100%，无法区分优劣；真实脱敏样本复算后定。flash 在延迟/成本侧明显更快——解析 P50 1835 ms vs pro 43915 ms——仅作为参考信号，不作结论） | 见「对比结果」节字段准确率表与延迟列 |
| 精排模型 | 待数据（Spearman pro 0.24 / flash 0.26，Top-10 召回 pro 60.0% / flash 70.0%，均未达 D9 门槛 ≥0.70 / ≥85%；合成样本的「设计排序」与 LLM 精排口径存在差异，需在真实脱敏样本上复算） | 见「对比结果」节表首「门槛（D9）」行对照 |
| embedding 方案 | `.51` 本地 CPU BGE-M3（已裁决，D7）；装包体积：torch 587 MB + FlagEmbedding 2.1 MB（依赖链约 1 GB 量级）+ 权重缓存 4.3 GB（可用 `allow_patterns` 限至约 2.3 GB）；单份耗时：Mac M-series CPU 单份 ~58 ms、画像 ~205 ms；recall@10（合成样本，n=20）= 40.0%，仅记录不作结论 | 见「BGE-M3 本地 CPU 召回」节 |
| 置信度阈值起步值（Q3） | 0.7（推荐起步）；终值：待真实脱敏样本复算（合成样本 evidence 可定位率 100%、字段准确率 100%，区分度不足，无法给出比 0.7 更细的终值） | 见「对比结果」节 evidence 可定位率与逐字段准确率表 |
| 扫描件路径 | PaddleOCR（已裁决，D14）；可装性：❌ cp314 无 wheel（Mac 实测 `pip install paddlepaddle==3.4.0` 报 `No matching distribution found`）；相似度：无数据（20 份扫描件全部 `OcrUnavailable`）⇒ 按 D14 退路（不可读文件进人工队列 + 登记技术债），待裁决 #6 保持开放 | 见「扫描件 OCR」节 |

## 待裁决与留步

（同步计划「待裁决」节的现状，2026-09-17）

1. **1.7 定型确认**——本文档「决策」节须 Shao Peishen 签认，本计划只产数据、不替他定。
2. **真实脱敏样本未到位**：本次「对比结果」「BGE-M3 本地 CPU 召回」「扫描件 OCR」三节数字全部基于 `sample_class=synthetic`（合成，seed 20260917），只证明管线/schema/evidence/延迟/成本可跑通，**不作 D9 等验收结论**；待真实脱敏样本（计划「待裁决」#2）到位后重算。
3. **缺 key 候选**：`doubao`（缺 `ARK_API_KEY`）、`qwen`（缺 `DASHSCOPE_API_KEY`）本机均未跑，属「预算与外部采购」不可代项，待 Shao Peishen 采购。
4. **`paddlepaddle` 在 Python 3.14 装不上**：PyPI 与官方索引均无 cp314 wheel（2026-09-17 Mac 实测），按 D14 退路（一期扫描件进人工队列 + 技术债，等 Paddle 发 cp314 wheel 再接）；另一处置（`.51` 另建 3.13 sidecar venv）属技术方案审查，代理人未设，挂起等本人（计划「待裁决」#6）。
5. **`.51` Windows 冒烟未跑**（tasks 1.1）：无头 Mac 会话做不到，需在 `.51` 同款 Windows venv 上实跑 `scripts.smoke_m2_deps`；结果决定 PaddleOCR／BGE-M3 装包结论是否需要修正。
6. **Q3 置信度阈值终值待真实样本**：本文档暂定 0.7 起步，终值需真实脱敏样本复算后由 1.6/1.7 收口。
7. **D9 精排指标在合成样本上未达标**（Spearman 0.24/0.26 < 0.70，Top-10 召回 60.0%/70.0% < 85%）：非最终结论，需在真实脱敏样本上复算——合成样本的「设计排序」真值口径与 LLM 精排口径可能本身存在差异。

## 已确认

（Shao Peishen 签认后填：已确认 YYYY-MM-DD）
