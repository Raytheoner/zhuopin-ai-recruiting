"""泳道批次看护正文的机器闸（Token 治理 Phase 1，0916B）。

P0 账本（docs/token治理/P0-对账.md）：看护会话 11 个、837 次调用、$60，0904Z 峰值上下文 352k。
根因是正文模板写「每 3–5 分钟查一次」，每查一次就是模型一轮。改法已落进 lane-dispatch skill，
但看护正文每批是照上一批手抄的——光改 skill 压不住抄旧件，所以加这道闸：
新的 `*泳道批次看护*.md` 必须用 `wait-lanes.sh` 等待，且不得再出现「每 N 分钟查一次」式轮询。
看护者开跑前的 pytest 基线会跑到本文件——闸不过即「有失败项 → 停下报告，不发车」。

旧件冻结在 LEGACY 里豁免（历史记录，不回改）。⛔ 不要往 LEGACY 里加新文件来绕过。
"""

from __future__ import annotations

import re
from pathlib import Path

OPENERS = Path(__file__).resolve().parent.parent / "docs" / "openers"

LEGACY = {
    "0903Y-泳道批次看护.md", "0904Y-泳道批次看护.md", "0904Z-泳道批次看护.md",
    "0908Y-泳道批次看护.md", "0908Z-泳道批次看护.md", "0909AZ-泳道批次看护.md",
    "0909X-泳道批次看护.md", "0909Y-泳道批次看护.md", "0909Z-泳道批次看护.md",
}

POLLING = re.compile(r"每\s*\d+\s*(?:[-–～~]\s*\d+\s*)?分钟\s*(?:查|看|轮询|检查)")


def caretaker_files():
    return sorted(p for p in OPENERS.glob("*泳道批次看护*.md") if p.name not in LEGACY)


def test_new_caretakers_use_wait_lanes():
    bad = [p.name for p in caretaker_files() if "wait-lanes.sh" not in p.read_text(encoding="utf-8")]
    assert not bad, f"这些看护正文没用 docs/openers/wait-lanes.sh 等待：{bad}"


def test_new_caretakers_have_no_minute_polling():
    bad = [p.name for p in caretaker_files() if POLLING.search(p.read_text(encoding="utf-8"))]
    assert not bad, f"这些看护正文仍写着「每 N 分钟查一次」式轮询：{bad}"


def test_polling_regex_catches_legacy_shape():
    assert POLLING.search("【六、轮询盯守】每 3-5 分钟查一次，直到泳道收敛")
    assert not POLLING.search("循环调 wait-lanes.sh，HEARTBEAT 即再调")
