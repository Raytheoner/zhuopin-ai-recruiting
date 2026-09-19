"""M3 U5 post 评分批处理入口（voice-structured-interview tasks.md 6.7）。

扫描 status='completed' 且尚未成功评分的场次，逐条跑 compute_align →
compute_score → effect_persist_scorecard（app/graph/interview_scoring_nodes.
py::run_post_scoring）。单条场次失败不得让整个批次中断。

真正的 Windows 计划任务安装（SYSTEM 账户/AtStartup/失败重启 3 次）是
tasks.md 8.6 的范围，本脚本只是那个计划任务将来调用的入口，本身不做任何
安装动作。
"""
from __future__ import annotations

import logging
import sys

from app.audit import AuditRecorder, JsonlChainSink, RecorderAuditHook, SqliteSink
from app.config import get_settings
from app.graph.interview_scoring_nodes import run_post_scoring
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema

logger = logging.getLogger(__name__)


def _due_sessions(conn) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM interview_session WHERE status = 'completed' "
        "AND post_scoring_status IN ('pending', 'failed_retry') ORDER BY created_at"
    ).fetchall()
    return [row[0] for row in rows]


def run_batch(db_path: str, gateway: LLMGateway) -> int:
    """返回进程退出码：0＝本轮全部场次都跑完（含判定为 failed_retry 的场次，
    那是预期内的可重试状态，不是批处理本身的故障）；1＝出现未预期异常。"""
    conn = get_connection(db_path)
    init_schema(conn)
    exit_code = 0
    for session_id in _due_sessions(conn):
        try:
            result = run_post_scoring(conn, session_id=session_id, gateway=gateway)
            logger.info("场次 %s 批处理结果: %s", session_id, result)
        except Exception:
            logger.exception(
                "场次 %s 批处理出现未预期异常，跳过本场次继续处理其余场次", session_id
            )
            exit_code = 1
    return exit_code


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    # 审计走专属连接，与 app/main.py::_audit_conn 同形（该文件注释解释了为什么
    # 不能复用共享连接：钩子在 LLMGateway 内部触发，那里没有 conn，复用共享
    # 连接会让留痕行被 idempotent_effect 的 rollback 一起撤销）。本脚本直接
    # 落到生产库 settings.db_path，不是隔离的回放库——批处理本来就是要写
    # 生产数据。
    audit_conn = get_connection(settings.db_path)
    audit_recorder = AuditRecorder(SqliteSink(audit_conn), JsonlChainSink(settings.audit_jsonl_path))
    audit_hook = RecorderAuditHook(audit_recorder, audit_conn)

    gateway = LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
        audit_hook=audit_hook,
        fallback_api_key=settings.llm_fallback_api_key,
        fallback_base_url=settings.llm_fallback_base_url,
        fallback_model=settings.llm_fallback_model,
        fallback_supports_json_schema=settings.llm_fallback_supports_json_schema,
    )

    sys.exit(run_batch(settings.db_path, gateway))


if __name__ == "__main__":
    main()
