"""泳道批次收敛摘要（0917Y）：`run-lanes.sh` 的 `results.tsv` ＋ `summary.txt` → 私信本人的正文。

**纯函数**（工程铁律 2 的形状）：不读文件、不看时钟、不记日志——文件由 CLI
（`owner_notify.owner_notify_main --lane-logdir`）读好了喂进来。

输入契约（`docs/openers/run-lanes.sh` 第 636 行逐字）：`results.tsv` 每行六列、tab 分隔
    lane \\t id \\t status \\t mins \\t log \\t model
`status` 取值：OK / PARTIAL / FAIL(<code>) / NO-SENTINEL / NO-BODY / WORKTREE-FAIL / BUDGET-HIT。
`summary.txt` 是同一份数据的人读排版，这里只当兜底（results.tsv 解析不出行时提示它存在），
⛔ 不从它二次解析。

⛔ **不含日志正文、不含日志路径**：私信里出现本机绝对路径既没用（手机上点不开）
也把目录结构外泄。摘要只带 编号／状态／分钟。
"""

from __future__ import annotations

#: 私信正文上限（字符）。企微 markdown 单条上限约 4096 字节，中文按 3 字节算
#: 800 字 ≈ 2.4 KB，留足 emoji 与换行的余量。超限从「各条」段截断并加省略号，
#: ⛔ 不截 PARTIAL/FAIL 列表——那两行才是他要看的。
DIGEST_MAX_CHARS = 800

_OK = "OK"
_PARTIAL = "PARTIAL"


def _parse_results(results_tsv_text: str) -> list[tuple[str, str, str, str]]:
    """每行 → (lane, id, status, mins)。列数不足四列的行丢弃（不是本脚本写的）。"""
    rows: list[tuple[str, str, str, str]] = []
    for line in results_tsv_text.splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        lane, lane_id, status, mins = (p.strip() for p in parts[:4])
        if not lane_id or not status:
            continue
        rows.append((lane, lane_id, status, mins))
    return rows


def _is_failed(status: str) -> bool:
    return status not in (_OK, _PARTIAL)


def compute_lane_digest(
    results_tsv_text: str,
    summary_text: str,
    *,
    batch_label: str = "",
) -> str:
    """结果表 → ≤ `DIGEST_MAX_CHARS` 字的 Markdown 摘要。

    `batch_label` 是批次标识（run-lanes.sh 的 `lanes-<STAMP>`，STAMP 即批次时间），
    由调用方从 LOGDIR 目录名取；不传就不写批次行。
    `summary_text` 只用于"results.tsv 一行都没解析出来"时的提示。
    """
    rows = _parse_results(results_tsv_text)
    ok = [r for r in rows if r[2] == _OK]
    partial = [r for r in rows if r[2] == _PARTIAL]
    failed = [r for r in rows if _is_failed(r[2])]

    head: list[str] = ["**泳道批次收敛**"]
    if batch_label:
        head.append(f"批次：{batch_label}")
    head.append(f"共 {len(rows)} 条：OK {len(ok)} ｜ PARTIAL {len(partial)} ｜ 失败 {len(failed)}")
    if not rows and summary_text.strip():
        head.append("（results.tsv 无可解析行，summary.txt 有内容，去日志目录看）")

    tail: list[str] = []
    if partial:
        tail.append("⏸ 留步：" + "、".join(r[1] for r in partial))
    if failed:
        tail.append("✗ 失败/无哨兵：" + "、".join(f"{r[1]}({r[2]})" for r in failed))
    if rows and not partial and not failed:
        tail.append("全部 OK，无留步、无失败")

    detail = [f"- {lane}/{lane_id} {status} {mins}m" for lane, lane_id, status, mins in rows]

    head_text = "\n".join(head)
    tail_text = "\n".join(tail)
    fixed = len(head_text) + len(tail_text) + 2  # 两个段落分隔的换行
    budget = DIGEST_MAX_CHARS - fixed
    kept: list[str] = []
    used = 0
    truncated = False
    for line in detail:
        if used + len(line) + 1 > budget - 2:  # 给省略号留位
            truncated = True
            break
        kept.append(line)
        used += len(line) + 1
    if truncated:
        kept.append("…")

    parts = [head_text]
    if kept:
        parts.append("\n".join(kept))
    if tail_text:
        parts.append(tail_text)
    return "\n".join(parts)[:DIGEST_MAX_CHARS]
