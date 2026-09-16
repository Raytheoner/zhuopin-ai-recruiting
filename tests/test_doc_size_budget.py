"""滚动文件体积闸（Token 治理 Phase 3，0916D）。

P0：`session接力.md` 118 KB、`OP-0820-全量编排.md` 379 KB，开场与改文档时读进上下文，之后每轮都付 cache read。
超限的处置只有一种：给已闭环的段落标题加「【已闭环】」（或划掉 `###` 标题），再跑
`python3 scripts/archive_docs.py --apply`。⛔ 不要为了过闸删内容，⛔ 不要调高下面的上限（调高须在路线图登记理由）。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIMITS_KB = {
    # 0916G 归档后 69→53 KB，剩余大段仍是真实在途工作（09-09/09-10 的悬置事项、约束清单）
    # ⇒ 按 §四.4 预案调至「当前 53 KB 向上取整 + 5」＝ 58，见路线图 Phase 3 状态行。
    "docs/session接力.md": 58,
    "docs/openers/OP-0820-全量编排.md": 60,
    "docs/openers/号池台账.md": 60,
    "docs/tech-debt.md": 50,   # 0916K 归档后基线
    "CLAUDE.md": 24,   # 0916H 瘦身后基线（22.5 KB 向上取整 23 ＋2＝25，封顶 24）
}


@pytest.mark.parametrize("rel,limit", LIMITS_KB.items())
def test_rolling_doc_within_budget(rel, limit):
    p = ROOT / rel
    if not p.exists():
        pytest.skip(f"{rel} 不存在")
    kb = p.stat().st_size / 1024
    assert kb <= limit, (
        f"{rel} = {kb:.0f} KB > {limit} KB。处置：给已闭环段落标题加「【已闭环】」后跑 "
        f"`python3 scripts/archive_docs.py --apply`（号池台账与编排文件无需标记，直接跑即可）。⛔ 不删内容、⛔ 不调上限。"
    )
