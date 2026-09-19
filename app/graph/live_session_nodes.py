"""U4 live 子图 L4 编排层（voice-structured-interview tasks 5.9，design
D20）。

`compute_session_bundle` 只读查库组装 `SessionBundle`，不写库（工程铁律 2，
与 app/graph/interview_scoring_nodes.py::compute_align 同一先例）。四个
`effect_*` 节点独占写库，全部用 `@idempotent_effect` 装饰，字面幂等键与
design D20 给出的公式一致（`effect_close_session` 的 `business_key` 常量
"close" 是本计划「设计决策 7」的裁定）。`run_live_session_sync` 是普通函数
串联的编排入口（本计划「设计决策 1」），供 scripts/run_interview_live_sync.py
（Task 6）每轮调用。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from pathlib import Path

from app.schemas.live_turn_event import LiveTurnEvent
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


def _load_follow_up_limit(conn: sqlite3.Connection, job_id: str) -> int:
    row = conn.execute(
        "SELECT follow_up_limit FROM job_prep_config WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return 2
    return row[0]


def compute_session_bundle(conn: sqlite3.Connection, *, session_id: str) -> SessionBundle:
    """L4 compute_* 节点：组装下发快照（live-voice-interview-session spec
    「简历数据不进语音主机」；本计划「设计决策 4」）。只读，不写库。"""
    row = conn.execute(
        "SELECT s.application_id, s.prep_snapshot_version, a.job_id, jpc.prep_curve "
        "FROM interview_session s JOIN application a ON a.id = s.application_id "
        "LEFT JOIN job_prep_config jpc ON jpc.job_id = a.job_id "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    application_id, snapshot_version, job_id, prep_curve = row
    prep_curve = prep_curve or "easy_to_hard"

    snap_row = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, snapshot_version),
    ).fetchone()
    if snap_row is None:
        raise ValueError(f"prep_snapshot 不存在: application_id={application_id!r} version={snapshot_version!r}")
    snapshot_id = snap_row[0]

    question_rows = conn.execute(
        "SELECT id, seq, text, follow_ups_json FROM prep_question WHERE snapshot_id = ? ORDER BY seq",
        (snapshot_id,),
    ).fetchall()
    questions = [
        SessionBundleQuestion(question_id=qid, seq=seq, text=text, follow_ups=json.loads(follow_ups_json))
        for qid, seq, text, follow_ups_json in question_rows
    ]

    return SessionBundle(
        session_id=session_id, prep_curve=prep_curve,
        follow_up_limit=_load_follow_up_limit(conn, job_id), questions=questions,
    )


@idempotent_effect("effect_open_session")
def effect_open_session(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, bundle: SessionBundle, client,
) -> None:
    """business_key = str(snapshot_version)（design D20 字面公式
    `{session_id}:effect_open_session:{snapshot_version}`）。重复调用（每轮
    轮询都会无条件调用这个函数）在第一次成功后被幂等短路，不会重复通知语音
    主机。"""
    client.create_session(bundle)
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type) VALUES (?, ?, 'opened')",
        (str(uuid.uuid4()), session_id),
    )


@idempotent_effect("effect_persist_turn")
def effect_persist_turn(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, event: LiveTurnEvent,
) -> None:
    """business_key = str(event.seq)（design D20 字面公式
    `{session_id}:effect_persist_turn:{seq}`）。`idx_interview_turn_session_seq`
    的 UNIQUE(session_id, seq) 是第二层幂等保护（reviewer 判据：即便
    effect_log 判定失误，唯一索引也会挡住重复行）。"""
    follow_up_of_id = None
    if event.follow_up_of_seq is not None:
        row = conn.execute(
            "SELECT id FROM interview_turn WHERE session_id = ? AND seq = ?",
            (session_id, event.follow_up_of_seq),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"follow_up_of_seq={event.follow_up_of_seq} 在场次 {session_id!r} 中找不到对应 turn"
            )
        follow_up_of_id = row[0]

    conn.execute(
        "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, "
        "answer_text, answer_mode, audio_start_ms, audio_end_ms, latency_json, "
        "follow_up_of, interrupted_at_ms, asr_confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), session_id, event.seq, event.question_id, event.question_text,
            event.answer_text, event.answer_mode, event.audio_start_ms, event.audio_end_ms,
            json.dumps(event.latency, ensure_ascii=False), follow_up_of_id,
            event.interrupted_at_ms, event.asr_confidence,
        ),
    )
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type, detail) VALUES (?, ?, 'turn_persisted', ?)",
        (str(uuid.uuid4()), session_id, str(event.seq)),
    )


@idempotent_effect("effect_close_session")
def effect_close_session(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, final_status: str,
) -> None:
    """business_key 常量 "close"（本计划「设计决策 7」，同场次只关闭一次，
    与 app/graph/invite_nodes.py::effect_open_invite 的 business_key="open"
    同一先例）。"""
    if final_status not in ("completed", "interrupted"):
        raise ValueError(f"非法的 final_status: {final_status!r}")
    conn.execute(
        "UPDATE interview_session SET status = ? WHERE id = ? AND status = 'in_progress'",
        (final_status, session_id),
    )
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type, detail) VALUES (?, ?, 'closed', ?)",
        (str(uuid.uuid4()), session_id, final_status),
    )


@idempotent_effect("effect_fetch_recording")
def effect_fetch_recording(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str,
    content: bytes, recording_sha256: str, recording_dir: str, client,
) -> None:
    """business_key = recording_sha256，由调用方（run_live_session_sync）在
    下载完成后算好再传入（本计划「设计决策 8」：网络 GET 本身可安全重复，
    幂等保护只覆盖"落盘 + 更新 DB + 通知语音主机删副本"这几个真正的写副作用）。
    """
    path = Path(recording_dir) / f"{session_id}.rec"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    conn.execute(
        "UPDATE interview_session SET recording_uri = ?, recording_sha256 = ? WHERE id = ?",
        (str(path), recording_sha256, session_id),
    )
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type, detail) VALUES (?, ?, 'recording_fetched', ?)",
        (str(uuid.uuid4()), session_id, recording_sha256),
    )
    client.notify_delete_artifacts(session_id)


def run_live_session_sync(conn: sqlite3.Connection, *, session_id: str, client, recording_dir: str) -> str:
    """一轮轮询的完整编排（live-voice-interview-session spec「开场前置条件」
    「场次副作用的幂等」）：
    1. 题目快照必须仍是 frozen（Scenario「题目快照被撤回」）——不满足直接
       跳过，不调用语音主机、不改动任何状态。
    2. `effect_open_session` 无条件调用（幂等短路保证只在首轮真正生效）。
    3. 按已落库的 `MAX(seq)` 作为 `since` 游标轮询新 turn 事件（"中断可续入
       且不重复出题"与 U3 4.3 `open_resume` 的 `next_seq` 同一口径）。
    4. `session_status` 到达终态才收尾：`effect_close_session` → 下载录音 →
       校验哈希 → `effect_fetch_recording`。
    """
    row = conn.execute(
        "SELECT s.prep_snapshot_version, s.status, ps.status "
        "FROM interview_session s "
        "JOIN application a ON a.id = s.application_id "
        "JOIN prep_snapshot ps ON ps.application_id = a.id AND ps.version = s.prep_snapshot_version "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在或找不到对应 prep_snapshot: {session_id!r}")
    snapshot_version, session_status, snapshot_status = row

    if snapshot_status != "frozen":
        logger.warning("场次 %s 的题目快照已不是 frozen（%s），本轮跳过", session_id, snapshot_status)
        return "blocked_snapshot_not_frozen"
    if session_status != "in_progress":
        return f"skipped_status_{session_status}"

    bundle = compute_session_bundle(conn, session_id=session_id)
    effect_open_session(
        conn, thread_id=session_id, business_key=str(snapshot_version),
        session_id=session_id, bundle=bundle, client=client,
    )

    since = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM interview_turn WHERE session_id = ?", (session_id,)
    ).fetchone()[0]

    poll = client.poll_events(session_id, since=since)
    for event in poll.events:
        effect_persist_turn(
            conn, thread_id=session_id, business_key=str(event.seq),
            session_id=session_id, event=event,
        )

    if poll.session_status not in ("completed", "interrupted"):
        return "in_progress"

    effect_close_session(
        conn, thread_id=session_id, business_key="close",
        session_id=session_id, final_status=poll.session_status,
    )

    content, recording_sha256 = client.fetch_recording(session_id)
    if hashlib.sha256(content).hexdigest() != recording_sha256:
        raise ValueError(f"场次 {session_id!r} 录音回传哈希校验失败")
    effect_fetch_recording(
        conn, thread_id=session_id, business_key=recording_sha256,
        session_id=session_id, content=content, recording_sha256=recording_sha256,
        recording_dir=recording_dir, client=client,
    )
    return poll.session_status
