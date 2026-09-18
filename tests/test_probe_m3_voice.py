from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.probe_m3_voice import (
    PROBES,
    ProbeResult,
    env_fingerprint,
    register,
    render_json,
    upsert_markdown_row,
    write_result,
)


def test_env_fingerprint_contains_platform_python_and_target():
    fp = env_fingerprint(target="dev-machine")
    assert "|dev-machine" in fp
    assert "|py" in fp


def test_env_fingerprint_appends_extra_label():
    fp = env_fingerprint(target="dev-machine", extra="company-wifi")
    assert fp.endswith("|company-wifi")


def _read(tmp_path: Path, name: str = "doc.md") -> str:
    return (tmp_path / name).read_text(encoding="utf-8")


def test_upsert_creates_table_when_absent():
    result = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=120.0)
    out = upsert_markdown_row("# 标题\n\n正文\n", result)
    assert "| P1 | fp-a | 通过 |" in out
    assert "## 探针结果" in out


def test_upsert_overwrites_same_item_and_fingerprint():
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="阻塞", blocking_reason="缺二进制", duration_ms=1.0)
    r2 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=99.0)
    doc = upsert_markdown_row("# 标题\n", r1)
    doc = upsert_markdown_row(doc, r2)
    rows = [ln for ln in doc.splitlines() if ln.startswith("| P1 |")]
    assert len(rows) == 1
    assert "通过" in rows[0]
    assert "99" in rows[0]


def test_upsert_appends_new_row_for_different_fingerprint():
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    r2 = ProbeResult(item="P1", env_fingerprint="fp-b", conclusion="通过", duration_ms=2.0)
    doc = upsert_markdown_row("# 标题\n", r1)
    doc = upsert_markdown_row(doc, r2)
    rows = [ln for ln in doc.splitlines() if ln.startswith("| P1 |")]
    assert len(rows) == 2


def test_upsert_appends_new_row_for_different_item_same_fingerprint():
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    r2 = ProbeResult(item="P2", env_fingerprint="fp-a", conclusion="通过", duration_ms=2.0)
    doc = upsert_markdown_row("# 标题\n", r1)
    doc = upsert_markdown_row(doc, r2)
    assert any(ln.startswith("| P1 |") for ln in doc.splitlines())
    assert any(ln.startswith("| P2 |") for ln in doc.splitlines())


def test_upsert_preserves_content_outside_table():
    result = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    doc = upsert_markdown_row("# 标题\n\n## 结论\n\n待补\n", result)
    assert "## 结论" in doc
    assert "待补" in doc


def test_write_result_creates_and_is_idempotent(tmp_path: Path):
    doc_path = tmp_path / "m3-voice-probe.md"
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    write_result(r1, doc_path=doc_path)
    assert doc_path.exists()
    r2 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="阻塞", blocking_reason="x", duration_ms=2.0)
    write_result(r2, doc_path=doc_path)
    rows = [ln for ln in _read(tmp_path, doc_path.name).splitlines() if ln.startswith("| P1 |")]
    assert len(rows) == 1
    assert "阻塞" in rows[0]


def test_cli_help_lists_registered_probes(capsys):
    import pytest as _pytest

    with _pytest.raises(SystemExit) as exc_info:
        from scripts.probe_m3_voice import main as cli_main

        cli_main(["--help"])
    assert exc_info.value.code == 0
