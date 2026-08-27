from __future__ import annotations

import hashlib
import json
import sqlite3

from app.agents.intake_agent import run_intake_turn
from app.agents.intake_question import IntakeQuestion
from app.agents.jd_agent import JDGenerationResult, generate_jd
from app.channels.base import Channel, OutboundMessage
from app.graph.state import IntakeState
from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile
from app.storage.idempotency import idempotent_effect


def compute_intake_turn(state: IntakeState, *, gateway: LLMGateway) -> IntakeState:
    """compute_* 节点：纯函数，只调用 LLM 与做数据转换，不写库、不发消息。"""
    history = list(state.get("history", []))
    accumulated_before = dict(state.get("profile_patch_accumulated", {}))
    round_count = state.get("round_count", 0)
    previous_questions = [
        IntakeQuestion.from_payload(item) for item in state.get("previous_questions", [])
    ]

    result = run_intake_turn(
        gateway,
        history=history,
        round_count=round_count,
        # 已累积的字段必须一起送进 prompt：SYSTEM_PROMPT 要求"不要重复历史已有
        # 字段"，模型看不见这份内容就无从遵守（review Critical 发现1）。
        profile_patch_accumulated=accumulated_before,
        # 预算的两个口径与已问台账都从 state 透传，真源是数据库（_run_turn 查
        # 出来放进 state），compute 节点自己不查库——它是 compute_*，纯函数。
        productive_round_count=state.get("productive_round_count", round_count),
        asked_question_ids_before=list(state.get("asked_question_ids_before", [])),
        previous_questions=previous_questions,
    )

    accumulated = {**accumulated_before, **result.profile_patch}

    # 把本轮助手说的话也记进历史，让下一轮的 prompt 是一段真正的对话，而不是
    # 一串没有上下文的用户独白——否则模型不知道上一轮已经问过什么。
    #
    # 文本直接用 result.questions_text，不在这里自己 join：渲染入口必须唯一
    # （app/agents/intake_question.render_questions_text），否则 history 里的
    # 文本和下发给通道的文本会分叉，_repeats_earlier_assistant_turn 就在比对
    # 一个从未真正下发过的字符串（design.md 决策 1「代价」）。
    assistant_turn = {
        "role": "assistant",
        "content": result.questions_text
        if result.questions
        else "（信息已收集完整，等待用人部门确认画像）",
    }

    return {
        **state,
        "history": [*history, assistant_turn],
        "is_job_related": result.is_job_related,
        "pending_questions": [question.to_payload() for question in result.questions],
        "profile_patch_accumulated": accumulated,
        "is_complete": result.is_complete,
        "round_count": round_count + 1,
        "unspecified_fields": result.unspecified_fields,
        "model_claimed_unspecified_fields": result.model_claimed_unspecified_fields,
        # 零产出轮判定与本轮台账增量，由 effect_persist_draft 与画像草案写在
        # 同一条 INSERT 里。
        "is_productive": result.is_productive,
        "asked_questions": [question.to_payload() for question in result.asked_questions],
        # 时序：只放耗时，不放模型标识——intake-turn-observability 要求时序
        # 留痕不承担审计职责。模型标识由第 7 章按 intake-field-grounding 落库。
        "llm_latency_ms": result.llm_latency_ms,
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

    同时把本轮结束时的完整对话记录写进 conversation 表。对话历史必须和画像草案
    一样落在持久层：修复前它只存在于 LangGraph checkpoint 里，而 IntakeState.history
    没有 reducer，每次 invoke 的输入会静默覆盖上一轮的值，第二轮起模型就只看得到
    最新一句话（review Critical 发现1）。放在同一个 effect 里写，保证"这一轮的画像"
    和"这一轮的对话"要么一起落盘、要么一起不落盘。

    2026-08-19（m1-intake-quality-fixes tasks 1.5）：本轮时序（turn_started_at /
    llm_latency_ms）写在**同一条 INSERT** 里。intake-turn-observability 要求
    "画像有这一轮、时序没有这一轮"不可能出现，所以不新增 effect 节点、不另起
    一次写入——多一次写入就多一个能失败的地方，而这两份数据必须同生共死。
    business_key 语义不变（仍是 round_count），幂等键不受影响。

    2026-08-19（第 3 章）：is_productive 与 asked_questions 也进同一条 INSERT。
    它们和画像草案是同一轮的三份事实，分开写就会出现"这一轮的画像在、这一轮
    问过什么不在"——而追问预算正是按这两列取数的。

    2026-08-27（第 6 章 tasks 6.2/6.5）：derived_unspecified_fields 开始写值——
    系统推导的那份进这一列（真源），模型自称的那份留在 unspecified_fields
    （对照）。⛔ 两列不许写同一个值，否则 8.1 的回放对比失去对照组。
    ungrounded_fields / llm_response_model 两列仍然不写值（第 7 章接上）。
    """
    profile_json = json.dumps(state.get("profile_patch_accumulated", {}), ensure_ascii=False)
    # 两列分工见 app/storage/db.py 的建表注释：derived_* 是系统推导的真源，
    # 裸 unspecified_fields 是模型自称的对照。⛔ 不许两列写同一个值——那会让
    # 8.1 的"修复前 vs 修复后"对比失去对照组。
    derived_json = json.dumps(state.get("unspecified_fields", []), ensure_ascii=False)
    model_claimed_json = json.dumps(
        state.get("model_claimed_unspecified_fields", []), ensure_ascii=False
    )
    version = int(business_key) + 1
    asked_questions_json = json.dumps(state.get("asked_questions", []), ensure_ascii=False)

    conn.execute(
        "INSERT INTO job_profile "
        "(id, job_id, version, status, profile_json, unspecified_fields, "
        "derived_unspecified_fields, "
        "turn_started_at, llm_latency_ms, is_productive, asked_questions) "
        "VALUES (?, ?, ?, 'drafting', ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{thread_id}-v{version}",
            thread_id,
            version,
            profile_json,
            model_claimed_json,
            derived_json,
            state.get("turn_started_at"),
            state.get("llm_latency_ms"),
            # 默认 True：判定没接上时按"有产出"算，与列默认值和历史行一致。
            1 if state.get("is_productive", True) else 0,
            asked_questions_json,
        ),
    )
    conn.execute(
        "INSERT INTO conversation (thread_id, history_json, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(thread_id) DO UPDATE SET "
        "history_json = excluded.history_json, updated_at = excluded.updated_at",
        (thread_id, json.dumps(state.get("history", []), ensure_ascii=False)),
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


@idempotent_effect("effect_generate_and_persist_jd")
def effect_generate_and_persist_jd(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    gateway: LLMGateway,
    profile: JobProfile,
    profile_dict: dict,
    version: int,
) -> JDGenerationResult:
    """
    effect_* 节点：调用 LLM 生成 JD 并把结果持久化回 job_profile，独占、幂等。
    business_key = 被确认的画像 version（与 effect_confirm_profile 用同一个
    version，两者 node_name 不同，effect_key 天然不会互相冲突）。

    这里补的是 review 发现的 Critical 缺口：generate_jd() 是一次真实、有成本的
    LLM 调用，在修复前直接在 HTTP handler 里裸调用，完全没有走 idempotent_effect
    保护——POST /api/jobs/{id}/confirm 被重试（双击、客户端超时重发、反向代理
    重试，这些在浏览器 demo 里都是会真实发生的场景）时，会重复触发一次 LLM 调用，
    并且第二次生成的（可能不同的）JD 文本会静默覆盖第一次的结果，这正是工程铁律1
    要求"每个有副作用的动作必须独占一个节点、带幂等键"要防止的情形。加上这层
    包装后，同一个 version 的第二次调用会在 idempotent_effect 内部命中已有的
    effect_log 记录直接短路返回 None——generate_jd() 根本不会被再次调用，
    profile_json 也不会被覆盖。

    不在这里 conn.commit() —— 理由同 effect_persist_draft / effect_confirm_profile：
    写入必须与 effect_log 记录由 idempotent_effect 装饰器在同一个事务里一次性提交。

    返回值只在"本次是真的执行了函数体"时有意义（idempotent_effect 命中重放会
    直接返回 None）；调用方如果需要在重放路径上也拿到 JD 文本，应该在调用后
    重新从 job_profile.profile_json 里读，而不是依赖这里的返回值。
    """
    jd_result = generate_jd(gateway, profile)
    conn.execute(
        "UPDATE job_profile SET profile_json = ? WHERE job_id = ? AND version = ?",
        (
            json.dumps(
                {
                    **profile_dict,
                    "_jd_text": jd_result.text,
                    "_jd_needs_manual": jd_result.needs_manual,
                },
                ensure_ascii=False,
            ),
            thread_id,
            version,
        ),
    )
    return jd_result


def message_business_key(payload: dict) -> str:
    """给 effect_deliver_message 生成稳定的 business_key，同一轮同一内容只投递一次。"""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
