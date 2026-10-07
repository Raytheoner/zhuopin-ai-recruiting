"""根 `.env` 的键必须全是 `app.config.Settings` 认识的（防 extra=forbid 静默炸）。

**由来（2026-10-07 实证）**：2026-09-30 有人往**仓库根 `.env`** 加了一行 `HR_LIAISON_GROUP_WEBHOOK`
——那是 liaison 值守的键，该待的地方是 `tools/liaison/.env`（`tools/liaison/__main__.py` 自己的
CLI 也明写「⛔ 仓库根的 .env 不再被本服务读」）。而 `app/config.py::Settings` 用的是
`env_file=".env"` + pydantic-settings 默认的 **extra=forbid** ⇒ `Settings()` 直接抛
`ValidationError: Extra inputs are not permitted`，`tests/test_web_api.py` 46 条红。

**为什么必须单独钉一条**：泳道的机器判据在 **worktree** 里跑，worktree 不带 `.env`（gitignored）
⇒ 这个红**在泳道里永远看不见**，只有回到主工作区才炸。本用例把它变成"主工作区一跑就红"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def _effective_keys(env_file: Path) -> list[str]:
    keys: list[str] = []
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip().lower())
    return keys


def test_root_dotenv_keys_are_all_known_to_settings():
    env_file = ROOT / ".env"
    if not env_file.is_file():
        pytest.skip("主工作区没有 .env（worktree／CI 场景，本用例无对象）")

    keys = _effective_keys(env_file)
    unknown = [k for k in keys if k not in Settings.model_fields]
    assert not unknown, (
        f"根 .env 含 Settings 不认识的键：{unknown}。"
        "app 的 Settings 是 extra=forbid，带着这些键启动会直接抛 ValidationError；"
        "liaison 自己的键（如 HR_LIAISON_GROUP_WEBHOOK）请放 `tools/liaison/.env`。"
    )


def test_settings_can_be_constructed_with_the_real_root_dotenv():
    """正面判据：真的按主工作区现状构造一次 `Settings()`。

    `unknown` 那条是纯文本比对；这条是**行为**比对——pydantic-settings 的 env 解析规则
    （大小写、前缀、类型）改了或被 .env 的写法绕过时，只有真构造一次才发现。
    """
    env_file = ROOT / ".env"
    if not env_file.is_file():
        pytest.skip("主工作区没有 .env（worktree／CI 场景，本用例无对象）")

    Settings()  # 抛 ValidationError 即红
