"""`.claude/skills/requirement-grill/SKILL.md` 机器闸（`0917AN` 移植 Win 端需求 grill）。

守四件事：三条机制名在、受访者限制在、事实源清单里的仓库路径真实存在、
产出路径与 intent 必备小节字样在。任一失守 ⇒ skill 正本漂移或仓库结构变了
而 skill 没跟上——两种都该当场红，不该等下一次 grill 时才发现路径扑空。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "requirement-grill" / "SKILL.md"


@pytest.fixture(scope="module")
def text() -> str:
    assert SKILL.is_file(), f"缺 {SKILL.relative_to(ROOT)}"
    return SKILL.read_text(encoding="utf-8")


def test_frontmatter_name_matches_directory(text):
    head = text.split("---", 2)
    assert len(head) >= 3, "frontmatter 缺失"
    assert re.search(r"^name:\s*requirement-grill\s*$", head[1], re.M)
    assert re.search(r"^description:\s*\S", head[1], re.M)


@pytest.mark.parametrize(
    "mechanism",
    ["M1 · 设计树与前沿", "M2 · 找事实是你的活", "M3 · 前沿为空才算完"],
)
def test_three_mechanisms_named(text, mechanism):
    assert mechanism in text, f"三条机制之一缺失：{mechanism}"


def test_interviewee_restricted_to_owner(text):
    assert "受访者只有一个：Shao Peishen（或场景发起人）" in text
    assert "绝不能是部门专员" in text


def _fact_source_paths(text: str) -> list[str]:
    """取「HR 版事实源清单」表里「路径（仓库内）」列的反引号路径。"""
    m = re.search(r"### HR 版事实源清单\n(.*?)\n\*\*M3", text, re.S)
    assert m, "找不到「HR 版事实源清单」小节"
    paths: list[str] = []
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[1].startswith("---") or cells[1] == "路径（仓库内）":
            continue
        if cells[1].startswith("—"):
            continue  # git 历史／值守库：不是仓库路径，另有查法
        paths.extend(re.findall(r"`([^`]+)`", cells[1]))
    assert paths, "事实源清单里没有解析出任何路径"
    return paths


def test_fact_source_paths_exist_in_repo(text):
    missing = [p for p in _fact_source_paths(text) if not (ROOT / p.rstrip("/")).exists()]
    assert not missing, f"事实源清单里的路径在仓库不存在：{missing}"


@pytest.mark.parametrize(
    "expected",
    ["CLAUDE.md", "docs/session接力.md", "docs/roadmap/", "openspec/",
     "docs/findings/", "docs/tech-debt.md", "docs/跟进信/"],
)
def test_fact_source_list_covers_opener_required_sources(text, expected):
    assert expected in _fact_source_paths(text)


def test_non_path_sources_declared(text):
    assert "git 历史" in text
    assert "值守库" in text and "只读" in text


def test_output_path_and_intent_sections(text):
    assert "docs/roadmap/intents/<场景>-intent.md" in text
    for heading in ("## 目标", "## 决策", "## 建议交付单元", "## 不做", "## 不可代项", "## 待专员", "## 待答题"):
        assert heading in text, f"intent 必备小节字样缺失：{heading}"
    # 「## 待答题」是台账生成器判「有未答题」的唯一节名（scripts/dispatcher_backlog.py parse_intents）
    assert "## 待答题" in text and "待答题" in (ROOT / "scripts" / "dispatcher_backlog.py").read_text(encoding="utf-8")
    # 0917AO 在环闸门：落档状态字样是「草稿·待 G1」，已确认只在 G1 放行后
    assert "status: 草稿·待 G1" in text and "已确认" in text
    assert "status: 待确认" not in text


def test_exit_is_g1_gate_not_propose(text):
    """0917AO：intent 落档 ⇒ 定夺队列 G1，⛔ 不再「落档即交调度器进 propose」。"""
    assert "落档 ⇒ 定夺队列 G1" in text
    assert "scripts/gates.py request G1" in text
    assert "⛔ 越闸" in text
    assert "落档即交调度器进 propose" not in text.replace("⛔ 不再写「落档即交调度器进 propose」", "")
    assert "落档提交即产生事件交调度器" not in text


def test_intent_sections_align_with_m2_intent():
    """产出格式对齐既有 `docs/roadmap/M2-intent.md`：那份是历史件，闸只认它现有的小节。"""
    m2 = (ROOT / "docs" / "roadmap" / "M2-intent.md").read_text(encoding="utf-8")
    for heading in ("## 目标", "## 决策", "## 建议交付单元", "## 不做", "## 不可代项"):
        assert heading in m2


def test_answer_template_is_plain_fenced_block(text):
    """一行答复模板必须是无语言标签的 fenced 代码块（Desktop 一键复制，⛔ 不标 bash）。"""
    assert re.search(r"\n```\nQ1a，Q2a，Q3a\n```", text)


def test_no_timeout_default_and_diff_section(text):
    assert "不设「超时按默认生效」" in text
    assert "## 九、与 Win 端正本差异" in text
    assert "criteria --add" in text, "专员部分须接 HR 口径点台账 CLI"
