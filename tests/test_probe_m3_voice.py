from __future__ import annotations

import argparse
import io
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
from scripts.probe_m3_voice import _resolve_livekit_binary, _validate_network_label


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


def test_upsert_with_real_env_fingerprint_containing_pipes():
    """Test that real env_fingerprint() output (which contains |) is handled correctly.

    This is the critical regression test for the bug where fingerprints with |
    broke idempotent upsert. Using real env_fingerprint() ensures the composed
    functions work end-to-end.
    """
    real_fp = env_fingerprint(target="test-machine", extra="test-network")
    # real_fp will be something like "macOS-27.0-arm64-arm-64bit-Mach-O|py3.14.6|test-machine|test-network"
    assert "|" in real_fp, "Real fingerprint should contain | separators"

    # First upsert
    r1 = ProbeResult(item="P1", env_fingerprint=real_fp, conclusion="通过", duration_ms=1.0)
    doc = upsert_markdown_row("# 标题\n", r1)
    rows_after_first = [ln for ln in doc.splitlines() if ln.startswith("| P1 |")]
    assert len(rows_after_first) == 1, "First upsert should create exactly one row"

    # Second upsert with same key should overwrite, not append
    r2 = ProbeResult(item="P1", env_fingerprint=real_fp, conclusion="阻塞", blocking_reason="test", duration_ms=99.0)
    doc = upsert_markdown_row(doc, r2)
    rows_after_second = [ln for ln in doc.splitlines() if ln.startswith("| P1 |")]
    assert len(rows_after_second) == 1, "Second upsert should still have exactly one row (overwritten, not appended)"
    assert "阻塞" in rows_after_second[0], "Second upsert should have overwritten with new conclusion"
    assert "99" in rows_after_second[0], "Second upsert should have updated duration"


def test_upsert_with_metrics_containing_pipes():
    """Test that metric values containing | are escaped correctly."""
    result = ProbeResult(
        item="P1",
        env_fingerprint="fp-a",
        conclusion="通过",
        metrics={"config": "a|b|c", "status": "ok"}  # Value contains pipes
    )
    doc = upsert_markdown_row("# 标题\n", result)
    # The row should be created successfully without breaking table structure
    assert "| P1 |" in doc
    assert "## 探针结果" in doc

    # Verify we can upsert again with the same key
    result2 = ProbeResult(
        item="P1",
        env_fingerprint="fp-a",
        conclusion="阻塞",
        blocking_reason="config error",
        metrics={"config": "x|y|z"}
    )
    doc = upsert_markdown_row(doc, result2)
    rows = [ln for ln in doc.splitlines() if ln.startswith("| P1 |")]
    assert len(rows) == 1, "Should have exactly one row after second upsert (overwritten)"
    assert "阻塞" in rows[0]


def test_validate_network_label_accepts_known_values():
    assert _validate_network_label("company-wifi") == "company-wifi"
    assert _validate_network_label("phone-4g") == "phone-4g"


def test_validate_network_label_rejects_unknown():
    with pytest.raises(ValueError, match="network-label"):
        _validate_network_label("random-guess")


def test_resolve_livekit_binary_prefers_explicit_path(tmp_path: Path):
    fake_bin = tmp_path / "livekit-server"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    assert _resolve_livekit_binary(str(fake_bin)) == str(fake_bin)


def test_resolve_livekit_binary_falls_back_to_which():
    with patch("shutil.which", return_value="/opt/homebrew/bin/livekit-server"):
        assert _resolve_livekit_binary(None) == "/opt/homebrew/bin/livekit-server"


def test_resolve_livekit_binary_returns_none_when_missing():
    with patch("shutil.which", return_value=None):
        assert _resolve_livekit_binary(None) is None


def test_probe_p1_livekit_returns_blocking_result_on_connect_error():
    """A LiveKit SDK connect failure must become a 阻塞 ProbeResult, not an
    uncaught exception — otherwise main() never reaches write_result() and
    docs/m3-voice-probe.md is left stale while the operator sees a raw
    traceback instead of a recorded blocking reason. Reproduces the exact
    failure mode hit during real Step 6 execution
    (livekit.rtc.room.ConnectError: engine: signal failure: transport timed out),
    without spinning up a real livekit-server or making a real connection.
    """
    from scripts.probe_m3_voice import probe_p1_livekit

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # 装作 --dev 已正常起来、没有立即退出
    fake_proc.stdout = io.StringIO("")  # drain 线程读空即结束，不阻塞

    args = argparse.Namespace(
        target="test-machine",
        network_label="company-wifi",
        livekit_bin="/fake/livekit-server",  # 显式路径，_resolve_livekit_binary 直接返回，不落盘校验
        livekit_version_hint="test-version",
    )

    with (
        patch("scripts.probe_m3_voice.subprocess.Popen", return_value=fake_proc),
        patch("livekit.rtc.Room.connect", new=AsyncMock(side_effect=RuntimeError("boom: transport timed out"))),
    ):
        result = probe_p1_livekit(args)

    assert result.conclusion == "阻塞"
    assert result.blocking_reason is not None
    assert "boom" in result.blocking_reason
    fake_proc.terminate.assert_called_once()


from scripts.probe_m3_voice import _percentile


def test_percentile_median_and_p95():
    values = [float(i) for i in range(1, 101)]  # 1..100
    assert _percentile(values, 50) == pytest.approx(50.5, abs=1.0)
    assert _percentile(values, 95) == pytest.approx(95.5, abs=1.0)


def test_percentile_single_value():
    assert _percentile([42.0], 50) == 42.0
    assert _percentile([42.0], 95) == 42.0


def test_probe_p2_funasr_blocks_when_module_missing(tmp_path: Path):
    import sys
    from types import SimpleNamespace

    from scripts.probe_m3_voice import probe_p2_funasr

    real_import = __import__

    def _fake_import(name, *a, **kw):
        if name == "funasr":
            raise ModuleNotFoundError("No module named 'funasr'")
        return real_import(name, *a, **kw)

    import builtins

    monkey_target = builtins.__import__
    builtins.__import__ = _fake_import
    try:
        args = SimpleNamespace(target="dev-machine", audio_path=str(tmp_path / "missing.wav"))
        result = probe_p2_funasr(args)
    finally:
        builtins.__import__ = monkey_target

    assert result.conclusion == "阻塞"
    assert "funasr" in result.blocking_reason
