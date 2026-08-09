from __future__ import annotations

import hashlib
import json
import sqlite3

from app.agents.intake_agent import run_intake_turn
from app.channels.base import Channel, OutboundMessage
from app.graph.state import IntakeState
from app.llm.gateway import LLMGateway
from app.storage.idempotency import idempotent_effect


def compute_intake_turn(state: IntakeState, *, gateway: LLMGateway) -> IntakeState:
    """compute_* 节点：纯函数，只调用 LLM 与做数据转换，不写库、不发消息。"""
    result = run_intake_turn(
        gateway, history=state["history"], round_count=state.get("round_count", 0)
    )

    accumulated = dict(state.get("profile_patch_accumulated", {}))
    accumulated.update(result.profile_patch)

    return {
        **state,
        "is_job_related": result.is_job_related,
        "pending_questions": result.questions,
        "profile_patch_accumulated": accumulated,
        "is_complete": result.is_complete,
        "round_count": state.get("round_count", 0) + 1,
        "unspecified_fields": result.unspecified_fields,
    }


@idempotent_effect("effect_persist_draft")
def effect_persist_draft(conn: sqlite3.Connection, *, thread_id: str, business_key: str, state: dict) -> None:
    """
    effect_* 节点：写 job_profile 草案行，独占、幂等。business_key = round_count。

    不在这里 conn.commit() —— idempotent_effect 装饰器要求被装饰函数的写入与它自己
    追加的 effect_log 行落在同一个事务里、由装饰器统一提交一次（见
    app/storage/idempotency.py）。如果这里先提交一次，一旦进程在“这次提交”和
    “装饰器提交 effect_log”之间崩溃，job_profile 行已经落盘但 effect_log 没有，
    LangGraph 重放时会用同一个 business_key 重新执行本函数，撞上已存在的主键
    （UNIQUE constraint failed），重试永久失败——这正是工程铁律1要避免的情形。
    """
    profile_json = json.dumps(state.get("profile_patch_accumulated", {}), ensure_ascii=False)
    unspecified_json = json.dumps(state.get("unspecified_fields", []), ensure_ascii=False)
    version = int(business_key) + 1

    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, unspecified_fields) "
        "VALUES (?, ?, ?, 'drafting', ?, ?)",
        (f"{thread_id}-v{version}", thread_id, version, profile_json, unspecified_json),
    )


@idempotent_effect("effect_deliver_message")
def effect_deliver_message(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    channel: Channel,
    message: OutboundMessage,
) -> None:
    """effect_* 节点：投递消息给通道，独占、幂等。business_key 由调用方传入（内容哈希）。"""
    channel.deliver(thread_id, message)


@idempotent_effect("effect_confirm_profile")
def effect_confirm_profile(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, profile_dict: dict
) -> None:
    """
    effect_* 节点：把最新画像草案冻结为 approved，同步更新 job.status。
    business_key = 冻结的 version 号，防止同一版本被重复确认两次。

    不在这里 conn.commit() —— 理由同 effect_persist_draft：写入与 effect_log
    记录必须由 idempotent_effect 装饰器在同一个事务里一次性提交。
    """
    conn.execute(
        "UPDATE job_profile SET status = 'approved' "
        "WHERE job_id = ? AND version = (SELECT MAX(version) FROM job_profile WHERE job_id = ?)",
        (thread_id, thread_id),
    )
    conn.execute("UPDATE job SET status = 'approved' WHERE id = ?", (thread_id,))


def message_business_key(payload: dict) -> str:
    """给 effect_deliver_message 生成稳定的 business_key，同一轮同一内容只投递一次。"""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
