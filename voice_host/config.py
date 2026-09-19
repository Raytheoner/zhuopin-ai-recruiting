"""语音主机配置（voice-structured-interview U4 tasks 5.1，design D12）。
全部走环境变量，⛔ 不依赖 `.51` 的 app.config.Settings——两台机器完全独立
部署，语音主机没有、也不应该有 `.51` 的 .env。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceHostSettings:
    shared_secret: str
    data_dir: str
    port: int
    cosyvoice_venv_python: str
    cosyvoice_worker_script: str


def load_settings() -> VoiceHostSettings:
    return VoiceHostSettings(
        shared_secret=os.environ.get("VOICE_HOST_SHARED_SECRET", ""),
        data_dir=os.environ.get("VOICE_HOST_DATA_DIR", "voice_host_data"),
        port=int(os.environ.get("VOICE_HOST_PORT", "8090")),
        cosyvoice_venv_python=os.environ.get(
            "VOICE_HOST_COSYVOICE_PYTHON", "voice_host/.venv-cosyvoice/bin/python"
        ),
        cosyvoice_worker_script=os.environ.get(
            "VOICE_HOST_COSYVOICE_WORKER", "voice_host/tts_cosyvoice_worker.py"
        ),
    )
