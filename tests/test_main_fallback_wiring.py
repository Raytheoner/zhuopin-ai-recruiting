"""WBS 2.3 的装配守护：备用供应商四项必须从 Settings 接到 _gateway_factory()。

AST 扫源码 + 一个真实子进程装配，两条路互补（与 tests/test_main_wiring.py 同一
形状）：AST 便宜、钉住"写在哪儿"；子进程贵、能证明"真的跑得起来"。
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")

FALLBACK_KEYWORDS = {
    "fallback_api_key": "llm_fallback_api_key",
    "fallback_base_url": "llm_fallback_base_url",
    "fallback_model": "llm_fallback_model",
    "fallback_supports_json_schema": "llm_fallback_supports_json_schema",
}


def _gateway_call() -> ast.Call:
    for node in ast.walk(ast.parse(MAIN_SOURCE)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "LLMGateway":
            return node
    raise AssertionError("app/main.py 里找不到 LLMGateway(...) 的构造调用")


def test_all_four_fallback_keywords_come_from_settings():
    """⛔ 不许在这里写死任何供应商常量：备用供应商只能来自 Settings。"""
    keywords = {kw.arg: kw.value for kw in _gateway_call().keywords}
    for arg, settings_field in FALLBACK_KEYWORDS.items():
        assert arg in keywords, f"_gateway_factory() 漏传了 {arg}"
        value = keywords[arg]
        assert isinstance(value, ast.Attribute) and value.attr == settings_field, (
            f"{arg} 必须取 settings.{settings_field}，⛔ 不许写死字面量"
        )


def test_importing_app_main_without_fallback_env_yields_no_fallback(tmp_path):
    """.51 上不改 .env 就是「无备用」——行为与今天逐字一致（2.3 的默认值要求）。"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LLM_FALLBACK_")
    }
    env["DB_PATH"] = str(tmp_path / "wiring.db")
    env["AUDIT_JSONL_PATH"] = str(tmp_path / "audit.jsonl")
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.main as m; print(m._gateway_factory()._fallback)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None"
