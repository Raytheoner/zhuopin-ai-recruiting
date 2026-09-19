"""M3 U4 live 段 `.51` 出站轮询同步入口（voice-structured-interview tasks.md
5.9）。持续轮询处于 in_progress 且已通过开场前置条件（live-voice-interview-
session spec「开场前置条件」）的场次，调用
app.graph.live_session_nodes.run_live_session_sync 拉 turn 事件、场次收尾、
拉取并回传删除录音副本。

真正的 Windows 计划任务安装（SYSTEM 账户/AtStartup/失败重启 3 次）是
tasks.md 8.6 的范围；本脚本自身内建 `--interval` 轮询循环（design.md Risks
「轮询间隔 2s」），计划任务只需要保证进程存活，不需要每 2 秒重新拉起一次。
"""
from __future__ import annotations

import argparse
import logging
import time

from app.config import get_settings
from app.graph.live_session_nodes import run_live_session_sync
from app.live_voice.client import VoiceHostClient
from app.storage.db import get_connection, init_schema

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 2.0


def _sessions_ready_for_live_sync(conn) -> list[str]:
    """live-voice-interview-session spec「开场前置条件」的存储层落点：题目
    快照已冻结（由 run_live_session_sync 自己再查一次 prep_snapshot.status
    把关，本查询只挡 status/手机号/双同意三项，是更便宜的粗筛）、双同意均
    通过、手机号核验通过、场次处于 in_progress。"""
    rows = conn.execute(
        "SELECT s.id FROM interview_session s "
        "WHERE s.status = 'in_progress' AND s.phone_verified_at IS NOT NULL "
        "AND (SELECT COUNT(*) FROM interview_consent c WHERE c.session_id = s.id "
        "AND c.result = 'accepted') = 2 "
        "ORDER BY s.created_at"
    ).fetchall()
    return [row[0] for row in rows]


def run_once(conn, *, client: VoiceHostClient, recording_dir: str) -> int:
    """返回本轮处理的场次数。单场次异常不得中断整批（与
    scripts/run_interview_post_scoring.py::run_batch 同一处理方式）。"""
    processed = 0
    for session_id in _sessions_ready_for_live_sync(conn):
        try:
            result = run_live_session_sync(
                conn, session_id=session_id, client=client, recording_dir=recording_dir,
            )
            logger.info("场次 %s 本轮同步结果: %s", session_id, result)
        except Exception:
            logger.exception("场次 %s 本轮同步出现未预期异常，跳过继续处理其余场次", session_id)
        processed += 1
    return processed


def run_loop(
    conn, *, client: VoiceHostClient, recording_dir: str,
    interval: float, max_iterations: int | None = None,
) -> None:
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        run_once(conn, client=client, recording_dir=recording_dir)
        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            time.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出（供测试/手动排障用）")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    args = parser.parse_args()

    settings = get_settings()
    conn = get_connection(settings.db_path)
    init_schema(conn)
    client = VoiceHostClient(
        base_url=settings.voice_host_base_url,
        shared_secret=settings.voice_host_shared_secret,
        timeout=settings.voice_host_request_timeout_seconds,
    )

    if args.once:
        run_once(conn, client=client, recording_dir=settings.interview_recording_dir)
    else:
        run_loop(conn, client=client, recording_dir=settings.interview_recording_dir, interval=args.interval)


if __name__ == "__main__":
    main()
