"""来源改正的流转事实追加（channel-resume-intake U3 tasks 3.2）。

来源改正在流转事实表里留的是**追加**的一条 action='source_corrected' 事实，
source 记新值——⛔ 绝不更新投递创建时那条初始记录：本表是只追加的事实表，
改写会破坏"所有报表的基础"这个前提（design D5）。

幂等由调用方承担：POST /api/resumes/{resume_id}/source 的"同值早退"在到达本
函数之前就把重复提交挡住了（与 source_correction_log 同一口径，U1 已落地）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid


def append_source_correction(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    from_source: str | None,
    to_source: str,
    actor: str,
) -> str | None:
    """追加一条 source_corrected 流转事实，返回受影响的 application_id。

    该简历还没有投递（解析不出/解析失败，application 行不存在）时返回 None 且
    ⛔ 不写任何行——来源改正本身仍然成立（resume.source 与 source_correction_log
    由端点写），只是没有投递可挂事实（本计划架构决策 7）。

    from_stage_id 留 NULL、to_stage_id 取该投递当前阶段：来源改正**不改阶段**，
    这条事实不在阶段流里，语义靠 action 区分（架构决策 6）。
    detail_json 存原值/新值，让这条事实自足（与 source_correction_log 也能交叉
    复验）。
    """
    row = conn.execute(
        "SELECT id, current_stage_id FROM application WHERE resume_id = ?",
        (resume_id,),
    ).fetchone()
    if row is None:
        return None
    application_id, current_stage_id = row[0], row[1]
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, "
        " detail_json, source) "
        "VALUES (?, ?, NULL, ?, 'human', ?, 'source_corrected', ?, ?)",
        (
            str(uuid.uuid4()),
            application_id,
            current_stage_id,
            actor,
            json.dumps(
                {"from_source": from_source, "to_source": to_source},
                ensure_ascii=False,
            ),
            to_source,
        ),
    )
    return application_id
