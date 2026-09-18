# CosyVoice 安装记录（P3 探针前置，tasks 1.4）

官方仓库无 PyPI 发行版；PyPI 上的 `cosyvoice==0.0.8` 是另一个无关的小项目，⛔ 不要 `pip install cosyvoice`。

## 安装步骤（在此记录每次实跑的真实结果，覆盖式更新本文件，不新开日期文件）

```bash
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git data/m3-voice-probe/CosyVoice
cd data/m3-voice-probe/CosyVoice && git submodule update --init --recursive
python3.10 -m venv .venv-cosyvoice   # 若本机无 python3.10，记录实际用的版本号
.venv-cosyvoice/bin/pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
.venv-cosyvoice/bin/python -c "
from modelscope import snapshot_download
snapshot_download('iic/CosyVoice-300M-SFT', local_dir='pretrained_models/CosyVoice-300M-SFT')
"
```

## 执行记录

2026-09-18（Task 4 Step 6 实跑，开发机 macOS，`data/m3-voice-probe/` 已 gitignore，不入库）：

- **解释器**：本机无 `python3.10`（`which python3.10` 未命中）。可用 `/opt/homebrew/bin/python3.12`（同 `python3`）。官方文档用 conda + Python 3.10；本机改用 `python3.12 -m venv` 达到同等隔离，实际版本 `Python 3.12.13`。
- **网络**：`curl -sI https://github.com` 可达（HTTP/2 200），非无网络环境。
- **克隆**：`git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git` 含子模块 `third_party/Matcha-TTS`，**29 秒完成**，未触发预算问题（此前一次估计"子模块检出会很慢"是错的，实测很快）。
- **核对 `example.py`（关键发现——印证了本任务背景段落的提醒："官方示例代码随版本演进，写死的类名可能已改变"）**：
  当前版本 `example.py` 的推荐入口已改为顶层便捷函数 `from cosyvoice.cli.cosyvoice import AutoModel`（`AutoModel(model_dir=...)` 会按 `cosyvoice.yaml` 自动分派到 `CosyVoice`/`CosyVoice2`/`CosyVoice3` 三个具体类之一），不再是 Step 3 假设的直接 `from cosyvoice.cli.cosyvoice import CosyVoice`。
  但逐行核对 `cosyvoice/cli/cosyvoice.py` 源码后确认：**`CosyVoice` 类本身仍然存在**（`class CosyVoice:` 定义在第 27 行，`CosyVoice2`/`CosyVoice3` 继承自它），构造签名为 `__init__(self, model_dir, load_jit=False, load_trt=False, fp16=False, trt_concurrent=1)`——与 Step 3 代码 `CosyVoice(str(model_dir))` 的位置参数假设**一致**；`inference_sft` 签名为 `inference_sft(self, tts_text, spk_id, stream=False, speed=1.0, text_frontend=True)`——与 Step 3 代码 `model.inference_sft(sample_text, "中文女", stream=True)` 的调用方式**一致**。结论：**Step 3 假设的类名与调用签名对这个版本仍然成立，无需改代码**；只是不再是文档首推的调用路径（新版更推荐 `AutoModel`），留作已知信息供后续维护参考。
- **`pip install -r requirements.txt`（阿里云镜像）**：Collecting 阶段正常（`diffusers`、`fastapi`、`gdown`、`gradio` 等均下载成功），在构建 `grpcio==1.57.0`（该仓库 2023 年钉死的版本，需从源码构建 wheel）时**失败**：
  ```
  Getting requirements to build wheel: finished with status 'error'
  ModuleNotFoundError: No module named 'pkg_resources'
  ERROR: Failed to build 'grpcio' when getting requirements to build wheel
  ```
  根因：pip 的构建隔离环境不再自动带 `pkg_resources`（新版 `setuptools` 已将其剥离/延后安装），而 `grpcio==1.57.0` 的 `setup.py` 在构建期硬依赖 `pkg_resources`。这是**钉死版本依赖与当前工具链（Python 3.12 + 现代 pip/setuptools）不兼容**的真实安装阻塞，不是网络或环境缺失问题。`pip install` 到此中止，torch/torchaudio 及其后依赖均未安装，未继续跑 ModelScope 模型下载。
- **探针 CLI 实跑**（`PYTHONPATH=. venv/bin/python -m scripts.probe_m3_voice p3-cosyvoice --target dev-machine --cosyvoice-repo-path data/m3-voice-probe/CosyVoice --model-dir data/m3-voice-probe/CosyVoice/pretrained_models/CosyVoice-300M-SFT`，用的是项目自身 `venv`，不是 `.venv-cosyvoice`——这是探针设计本身的前提：调用方要把已装好依赖的 `cosyvoice_repo_path` 传进来，而本次依赖未装全，如实复现了这一点）：
  ```json
  {
    "conclusion": "阻塞",
    "blocking_reason": "cosyvoice 包不可导入: ModuleNotFoundError: No module named 'hyperpyyaml'",
    "duration_ms": 23.03,
    "item": "P3"
  }
  ```
  已写入 `docs/m3-voice-probe.md` 的 P3 行。
- **未覆盖到的后续步骤**：ModelScope 模型下载（`snapshot_download`）未跑到——因为 `pip install` 已在更早的 `grpcio` 构建阶段失败，模型权重目录 `pretrained_models/CosyVoice-300M-SFT` 未创建。留待后续会话解决 `grpcio` 版本钉死问题（例如尝试 `pip install --no-build-isolation` 配合预装旧版 `setuptools`，或手动升级 `grpcio` 到与 `pkg_resources` 无关的更新版本，需评估是否影响 CosyVoice 其余组件的兼容性）后补跑。
