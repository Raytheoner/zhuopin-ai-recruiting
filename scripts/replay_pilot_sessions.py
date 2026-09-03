"""
第 8 章 8.1 / 8.5：拿 .51 上三段真实试点会话，用当前 intake 流程重跑一遍，
和修复前的同名指标做对比。

⚠️ 读这里产出的任何数字之前，先读完 app/agents/intake_question.py:30-63。
   那段注释说明了 question_id 降级计数（total / null_field / unknown_field）
   跨交付单元 E **不可直接比较**，连比例都不可比。因此本脚本**不采集也不输出**
   这三个数——不是漏了，是刻意不采。

输入：.51 data/demo.db 的一致快照副本（sqlite backup API 出的，见 8.2）。
      ⛔ 只读，本脚本一个字节都不写回副本。
输出：回放落进一个**临时本地库**（默认 data/replay/replay-run.db），
      ⛔ 绝不写 data/demo.db。

用法（真回放要联网调 LLM，必须显式给 --live）：

    ./venv/bin/python -m scripts.replay_pilot_sessions \\
        --snapshot data/replay/demo-51-20260903.db \\
        --out data/replay/replay-run.db --live

不给 --live 时只从副本里算「修复前」那半张表，不发一个请求。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# 三段试点会话的 thread_id 前缀（.51 现网，2026-08-13 ~ 08-18）。
# 用前缀而不是全 id：登记在 opener 与 findings 里的就是这 8 位，写全反而对不上。
PILOT_THREAD_PREFIXES: tuple[tuple[str, str], ...] = (
    ("a478499c", "反馈 1 · 供应链/采购（08-13）"),
    ("19b6ec6d", "反馈 2 · 软件（08-18）"),
    ("2494103e", "反馈 3 · 采购（08-18）"),
)

# 画像里以下划线开头的键不是业务字段，是流程产物（_jd_text / _jd_needs_manual）。
# 算「本轮新增了哪些字段」时必须排掉：JD 生成发生在收尾轮，把它算成新增内容会
# 让最后一轮永远显得「有产出」。
_NON_BUSINESS_PREFIX = "_"

# 空值口径：None / 空串 / 空列表 / 空字典都算「这个字段没填」。
_EMPTY_VALUES: tuple[object, ...] = (None, "", [], {})


# ──────────────────────────────────────────────────────────────────────
# 一、解析（纯函数，tests 全部只测这一段）
# ──────────────────────────────────────────────────────────────────────


def extract_user_turns(history_json: str) -> list[str]:
    """从 conversation.history_json 抽出**用户输入序列**，按原顺序。

    history 是 user / assistant 交替的完整记录；回放只需要 user 那一半，
    assistant 那半要由当前流程重新生成——照抄回去就不是回放了。
    """
    history = json.loads(history_json)
    return [
        turn["content"]
        for turn in history
        if turn.get("role") == "user" and turn.get("content")
    ]


def business_keys(profile_json: str) -> set[str]:
    """画像里**已填**的业务字段名。流程产物（下划线开头）与空值都不算。"""
    profile = json.loads(profile_json)
    return {
        key
        for key, value in profile.items()
        if not key.startswith(_NON_BUSINESS_PREFIX) and value not in _EMPTY_VALUES
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(frozen=True)
class SessionMetrics:
    """8.1 的四个数 + 8.5 的延迟。

    `idle_rounds_is_proxy=True` 表示空转轮数是**推导的**而不是实测的——
    修复前那一侧只能这么算（见 prefix_metrics 的说明）。
    """

    label: str
    total_rounds: int
    idle_rounds: int
    idle_rounds_is_proxy: bool
    final_unspecified_count: int | None
    final_unspecified_source: str
    latency_mean_ms: float | None
    latency_max_ms: float | None

    @property
    def latency_available(self) -> bool:
        return self.latency_mean_ms is not None


@dataclass
class GroundingCounts:
    """按 llm_response_model 分组的溯源计数——8.7 编造率的分子与分母。

    ⛔ 本脚本只出原始计数，不算比率、不下「编造率是 X%」的结论（那是 0903I 的事）。
    """

    rounds: int = 0
    ungrounded_total: int = 0
    written_total: int = 0
    ungrounded_field_names: dict[str, int] = field(default_factory=dict)


def _rows_for_thread(conn: sqlite3.Connection, prefix: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            "SELECT version, status, profile_json, unspecified_fields,"
            " derived_unspecified_fields, ungrounded_fields, written_fields,"
            " llm_response_model, llm_latency_ms, is_productive"
            " FROM job_profile WHERE job_id LIKE ? ORDER BY version ASC",
            (prefix + "%",),
        )
    )


def prefix_metrics(rows: list[sqlite3.Row], label: str) -> SessionMetrics:
    """修复前指标，全部从副本里的历史行推导。

    三处口径必须写清楚，否则对比表会被误读：

    1. **空转轮是推导值，不是实测值。** 历史行的 is_productive 是加列时的常量
       默认 1（app/storage/db.py 的 _ADDED_COLUMNS），不是当时判出来的，直接读
       会得到「空转轮恒为 0」这个纯属迁移产物的假象。这里改用「本轮相对上一轮
       没有新增业务字段」当代理。代理**偏严**：修复后的判定是「有新字段 **或**
       有新问题」，代理少了后半个条件，因此代理算出的空转轮 ≥ 真实空转轮。
    2. **最终未指定字段数来自 unspecified_fields（模型自称）**，修复后那一侧
       来自 derived_unspecified_fields（系统按字段表推导，第 6 章起才是真源）。
       两者定义不同，属「不可直接比」。
    3. **延迟无修复前基线**：llm_latency_ms 同样是加列后才有的，历史行恒为 NULL。
    """
    previous: set[str] = set()
    idle = 0
    for row in rows:
        keys = business_keys(row["profile_json"])
        if not keys - previous:
            idle += 1
        previous = keys

    final_unspecified = None
    if rows:
        final_unspecified = len(json.loads(rows[-1]["unspecified_fields"] or "[]"))

    latencies = [row["llm_latency_ms"] for row in rows if row["llm_latency_ms"] is not None]
    return SessionMetrics(
        label=label,
        total_rounds=len(rows),
        idle_rounds=idle,
        idle_rounds_is_proxy=True,
        final_unspecified_count=final_unspecified,
        final_unspecified_source="unspecified_fields（模型自称）",
        latency_mean_ms=_mean(latencies),
        latency_max_ms=max(latencies) if latencies else None,
    )


def replay_metrics(rows: list[sqlite3.Row], label: str) -> SessionMetrics:
    """修复后指标，全部实测：is_productive 与 llm_latency_ms 都是本次回放写的。"""
    idle = sum(1 for row in rows if not row["is_productive"])
    final_unspecified = None
    if rows:
        final_unspecified = len(json.loads(rows[-1]["derived_unspecified_fields"] or "[]"))
    latencies = [row["llm_latency_ms"] for row in rows if row["llm_latency_ms"] is not None]
    return SessionMetrics(
        label=label,
        total_rounds=len(rows),
        idle_rounds=idle,
        idle_rounds_is_proxy=False,
        final_unspecified_count=final_unspecified,
        final_unspecified_source="derived_unspecified_fields（系统推导，真源）",
        latency_mean_ms=_mean(latencies),
        latency_max_ms=max(latencies) if latencies else None,
    )


def grounding_counts_by_model(rows: list[sqlite3.Row]) -> dict[str, GroundingCounts]:
    """按 llm_response_model 分组数 ungrounded_fields / written_fields。

    模型标识缺失（历史行、或供应商没回 model 字段）归到 "(unknown)" 桶，
    ⛔ 不丢弃——丢掉等于让分母悄悄变小。
    """
    grouped: dict[str, GroundingCounts] = defaultdict(GroundingCounts)
    for row in rows:
        bucket = grouped[row["llm_response_model"] or "(unknown)"]
        bucket.rounds += 1
        ungrounded = json.loads(row["ungrounded_fields"] or "[]")
        bucket.ungrounded_total += len(ungrounded)
        bucket.written_total += len(json.loads(row["written_fields"] or "[]"))
        for name in ungrounded:
            bucket.ungrounded_field_names[name] = bucket.ungrounded_field_names.get(name, 0) + 1
    return dict(grouped)


def latency_verdict(before: SessionMetrics, after: SessionMetrics) -> str:
    """8.5 判定：均值没有明显变差（劣化 > 30% 记红）。

    修复前没有基线时返回「不可比」——⛔ 不把「没有基线」折成「通过」。
    """
    if not before.latency_available or not after.latency_available:
        return "不可比（修复前无 llm_latency_ms 基线，该列是加列后才有的）"
    delta = (after.latency_mean_ms - before.latency_mean_ms) / before.latency_mean_ms
    flag = "🔴 记红" if delta > 0.30 else "✅ 通过"
    return f"{flag}（均值变化 {delta:+.1%}）"


# ──────────────────────────────────────────────────────────────────────
# 二、真回放（联网调 LLM，只在 --live 时执行）
# ──────────────────────────────────────────────────────────────────────


def replay_live(user_turns: list[str], *, out_db_path: str) -> str:
    """把一段用户输入序列逐轮喂给**当前 intake 流程**，返回新建的 job_id。

    走 app 现有入口（create_app 暴露的 POST /api/jobs 与 /api/jobs/{id}/reply），
    ⛔ 不另写一套图调用——另写一套就只能证明「另写的那套」怎么跑。

    temperature 由 LLMGateway.TEMPERATURE 恒定为 0（工程铁律 5），这里不覆盖。
    """
    from fastapi.testclient import TestClient

    from app.audit import AuditRecorder, JsonlChainSink, RecorderAuditHook, SqliteSink
    from app.config import get_settings
    from app.llm.gateway import LLMGateway
    from app.storage.db import get_connection
    from app.web.server import create_app

    settings = get_settings()
    Path(out_db_path).parent.mkdir(parents=True, exist_ok=True)

    # 审计走专属连接、且落到**回放库**，与 app/main.py 同形（那里的注释解释了
    # 为什么不能复用 create_app 里的共享连接）。JSONL 镜像也改到回放目录，
    # ⛔ 不污染 data/audit/decisions.jsonl 那条真链。
    audit_conn = get_connection(out_db_path)
    audit_recorder = AuditRecorder(
        SqliteSink(audit_conn),
        JsonlChainSink(str(Path(out_db_path).with_suffix(".audit.jsonl"))),
    )
    audit_hook = RecorderAuditHook(audit_recorder, audit_conn)

    def gateway_factory() -> LLMGateway:
        return LLMGateway(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            supports_json_schema=settings.llm_supports_json_schema,
            audit_hook=audit_hook,
        )

    app = create_app(db_path=out_db_path, gateway_factory=gateway_factory, root_path="")
    with TestClient(app) as client:
        first, *rest = user_turns
        response = client.post("/api/jobs", json={"message": first})
        response.raise_for_status()
        job_id = response.json()["job_id"]
        for message in rest:
            reply = client.post(f"/api/jobs/{job_id}/reply", json={"message": message})
            reply.raise_for_status()
    audit_conn.close()
    return job_id


# ──────────────────────────────────────────────────────────────────────
# 三、CLI
# ──────────────────────────────────────────────────────────────────────


def _format_metrics(before: SessionMetrics, after: SessionMetrics | None) -> list[str]:
    lines = [f"### {before.label}", ""]
    lines.append("| 指标 | 修复前 | 修复后（回放） | 可比性 |")
    lines.append("|---|---|---|---|")
    idle_after = "—" if after is None else str(after.idle_rounds)
    unspec_after = "—" if after is None else str(after.final_unspecified_count)
    total_after = "—" if after is None else str(after.total_rounds)
    lines.append(
        f"| 空转轮数 | {before.idle_rounds}（推导代理，偏严） | {idle_after}（实测 is_productive=0） | ⚠️ 口径不同 |"
    )
    lines.append(
        f"| 最终未指定字段数 | {before.final_unspecified_count} | {unspec_after} | ⛔ 不可直接比 |"
    )
    lines.append(f"| 总轮数 | {before.total_rounds} | {total_after} | ⛔ 不可直接比 |")
    if after is not None and after.latency_available:
        lines.append(
            f"| 单轮 LLM 延迟 均值/最大 (ms) | 无基线 | "
            f"{after.latency_mean_ms:.0f} / {after.latency_max_ms:.0f} | "
            f"{latency_verdict(before, after)} |"
        )
    else:
        lines.append("| 单轮 LLM 延迟 均值/最大 (ms) | 无基线 | — | 不可比 |")
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="第 8 章 8.1/8.5 试点会话回放")
    parser.add_argument("--snapshot", required=True, help=".51 demo.db 的一致快照副本（只读）")
    parser.add_argument("--out", default="data/replay/replay-run.db", help="回放落库路径（临时本地库）")
    parser.add_argument("--live", action="store_true", help="真回放（联网调 LLM）；不给则只算修复前")
    args = parser.parse_args(argv)

    snapshot = sqlite3.connect(f"file:{args.snapshot}?mode=ro", uri=True)
    snapshot.row_factory = sqlite3.Row

    replay_conn = None
    if args.live:
        out = Path(args.out)
        if out.exists():
            out.unlink()
        replay_conn = sqlite3.connect(args.out)
        replay_conn.row_factory = sqlite3.Row

    report: list[str] = []
    for prefix, label in PILOT_THREAD_PREFIXES:
        before_rows = _rows_for_thread(snapshot, prefix)
        before = prefix_metrics(before_rows, label)

        after = None
        after_rows: list[sqlite3.Row] = []
        if args.live:
            row = snapshot.execute(
                "SELECT history_json FROM conversation WHERE thread_id LIKE ?", (prefix + "%",)
            ).fetchone()
            turns = extract_user_turns(row["history_json"])
            print(f"[replay] {label}: {len(turns)} 轮用户输入 …", flush=True)
            job_id = replay_live(turns, out_db_path=args.out)
            after_rows = _rows_for_thread(replay_conn, job_id[:8])
            after = replay_metrics(after_rows, label)

        report.extend(_format_metrics(before, after))
        if after_rows:
            report.append("按 llm_response_model 分组（8.7 的分子分母原始数，⛔ 本条不算比率）：")
            report.append("")
            report.append("| llm_response_model | 轮数 | ungrounded_fields 计数 | written_fields 计数 |")
            report.append("|---|---|---|---|")
            for model, counts in sorted(grounding_counts_by_model(after_rows).items()):
                report.append(
                    f"| `{model}` | {counts.rounds} | {counts.ungrounded_total} | {counts.written_total} |"
                )
            report.append("")

    print("\n".join(report))
    snapshot.close()
    if replay_conn is not None:
        replay_conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
