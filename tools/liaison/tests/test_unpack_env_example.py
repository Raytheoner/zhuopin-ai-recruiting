""".env.example 只写变量名与说明，⛔ 不写任何取值（Global Constraints 逐字要求）。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / "tools" / "liaison" / ".env.example"


def test_env_example_mentions_claude_bin_and_budget_vars():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "HR_LIAISON_CLAUDE_BIN=" in text
    assert "HR_LIAISON_UNPACK_BUDGET_USD=" in text


def test_env_example_new_vars_carry_no_credential_values():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("HR_LIAISON_CLAUDE_BIN=") or line.startswith("HR_LIAISON_UNPACK_BUDGET_USD="):
            assert line.rstrip() in ("HR_LIAISON_CLAUDE_BIN=", "HR_LIAISON_UNPACK_BUDGET_USD="), (
                f"{line!r} 携带了取值，Global Constraints 要求只写变量名"
            )


def test_env_example_documents_default_budget_of_five():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "默认" in text and "5" in text
