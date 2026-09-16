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


from tools.liaison.unpack import charter  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

RED_LINE_PHRASES = [
    "send-followup",       # ① 对外发送
    "scripts/",            # ② 建造（连同 tools/、app/、tests/ 一起要求不改）
    "新下裁决",             # ③
    "openspec/",           # ④
    "liaison.db",          # ⑤
    "白名单",               # ⑥
    "git push",            # ⑦
    "CLAUDE.md",           # ⑧
    "候选人个人信息",         # ⑨（D15）
]

CLOSING_PHRASES = ["不 push", "只 `git add`", "index.lock"]


def test_章程红线九项可核对() -> None:
    text = charter.read_charter(REPO_ROOT)
    for phrase in RED_LINE_PHRASES:
        assert phrase in text, f"章程缺红线短语：{phrase}"


def test_章程收口三条可核对() -> None:
    text = charter.read_charter(REPO_ROOT)
    for phrase in CLOSING_PHRASES:
        assert phrase in text, f"章程 §三 缺收口短语：{phrase}"


def test_章程结构小节完整() -> None:
    text = charter.read_charter(REPO_ROOT)
    for heading in ["§〇", "§一", "§二", "§三", "§四"]:
        assert heading in text, f"章程缺小节：{heading}"
    # R4（信号探测与循环）与 R6（回灌与还原）是纯文本要求，用关键短语核对存在性
    for phrase in [
        "一律按「有信号」处理",
        "只清检查点之前的项",
        "按分隔符",
        "docs/跟进信/回件/",
    ]:
        assert phrase in text, f"章程缺规定短语：{phrase}"
