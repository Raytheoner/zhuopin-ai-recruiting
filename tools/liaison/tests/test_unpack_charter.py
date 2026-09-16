from __future__ import annotations

from pathlib import Path

import pytest


def test_read_charter_missing_raises(tmp_path: Path) -> None:
    from tools.liaison.unpack import charter

    with pytest.raises(charter.CharterMissing):
        charter.read_charter(tmp_path)


def test_read_charter_reads_relative_path(tmp_path: Path) -> None:
    from tools.liaison.unpack import charter

    charter_dir = tmp_path / ".claude" / "skills" / "liaison-unpack"
    charter_dir.mkdir(parents=True)
    (charter_dir / "SKILL.md").write_text("章程内容\n", encoding="utf-8")

    assert charter.read_charter(tmp_path) == "章程内容\n"
