from __future__ import annotations

import hashlib
import json
import sqlite3

from app.agents.hard_requirement import (
    HardRequirement,
    assert_no_subjective_requirements,
    extract_hard_requirements,
)
from app.agents.intake_agent import run_intake_turn
from app.agents.intake_question import IntakeQuestion
from app.agents.jd_agent import JDGenerationResult, generate_jd
from app.audit.events import DecisionEvent
from app.audit.recorder import AuditRecorder
from app.channels.base import Channel, OutboundMessage
from app.graph.state import IntakeState
from app.llm.gateway import LLMGateway
from app.outbound import queue
from app.outbound.messages import CandidateOutboundMessage
from app.schemas.job_profile import JobProfile
from app.storage.idempotency import idempotent_effect

# 人工决策的三种类型（tasks 6.4 / 6.5 / 6.7）。
# ⛔ 三个字面量与 app/storage/db.py 的 human_review.decision_type CHECK、
# app/audit/assertions.py 断言四的 TERMINAL_STATUS_DECISIONS 逐字同源。改一处
# 必须同步改另两处——不同步的后果没有任何症状：不报错、不失败，只是留痕落在
# 一个断言查不到的取值上，审计那天才发现。
DECISION_APPROVED = "approved"
DECISION_REVISION_REQUESTED = "revision_requested"
DECISION_ABANDONED = "abandoned"


def _record_human_review(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    profile_version: int,
    decision_type: str,
    reviewer: str,
    feedback: str | None = None,
) -> None:
    """把一次人工决策写进 human_review（spec「决策留痕」）。

    ⛔ **本函数不 commit、不开事务**，只能在某个 effect_* 节点的函数体内被调用，
    与该节点的业务写落在同一个事务里、由 idempotent_effect 装饰器统一提交一次
    （工程铁律 1）。分开提交会出现"画像已确认但查不到谁确认的"，而更糟的是：
    幂等记录一旦先落，重试会被判定为"已执行"，那条留痕**永远不会补上**。

    ⛔ reviewer 不接受空白（表上有 CHECK 兜底）；**更不得写入任何自动判定的
    产物**——决策人只能是人（合规红线：AI 只做排序推荐，淘汰必须有人工确认
    节点并留痕）。

    id 取 `{job_id}-v{version}-{decision_type}`，与唯一索引
    (job_id, profile_version, decision_type) 和幂等键
    {job_id}:{node_name}:{version} **同粒度**。两道防线粒度不一致时，宽的那道
    形同虚设（与 pending_approval 的 (thread_id, content_hash) 同一理由）。
    """
    conn.execute(
        "INSERT INTO human_review "
        "(id, job_id, profile_version, decision_type, reviewer, feedback, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            f"{job_id}-v{profile_version}-{decision_type}",
            job_id,
            profile_version,
            decision_type,
            reviewer,
            feedback,
        ),
    )


def _record_hard_requirements(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    profile_version: int,
    profile_dict: dict,
) -> list[HardRequirement]:
    """从画像提取硬门槛规则草案并落库（tasks 5.8）。

    ⛔ **本函数不 commit、不开事务**，只能在某个 effect_* 节点的函数体内被调用，
    与该节点的业务写落在同一个事务里、由 idempotent_effect 装饰器统一提交一次
    （工程铁律 1）。分开提交会出现"画像已冻结但规则草案缺席"，而更糟的是：
    幂等记录一旦先落，重试会被判定为"已执行"，那份草案**永远不会补上**。

    ⛔ **提取是确定性纯函数，这里不调模型**（工程铁律 2）。草案要能被人复核、
    被回放对比，一次模型调用就把它变成不可复算的东西。

    ⛔ **本函数只写规则，不执行规则**（合规红线：AI 只做排序推荐，不做自动淘汰）。
    blocking 列是给人看的标注，本变更包内没有任何代码读它去筛人。

    落库前过一道 assert_no_subjective_requirements()：它是合规红线「主观描述不得
    进入硬门槛规则」的最后一道机器判据。命中就让 SubjectiveRequirementError 穿透
    出去、整条确认事务回滚——宁可让业务经理看到一次失败，也不让一条"沟通能力强"
    的门槛落库。

    ⛔ INSERT 不加 OR IGNORE：extract 已经按天然键去重，主键冲突只可能是去重逻辑
    有洞或同一版被写了两次，两种都必须响，⛔ 不许静默吞掉。
    """
    rules = extract_hard_requirements(profile_dict)
    assert_no_subjective_requirements(rules)
    for rule in rules:
        conn.execute(
            "INSERT INTO hard_requirement "
            "(job_id, profile_version, field, operator, value, blocking, "
            "human_readable, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                job_id,
                profile_version,
                rule.field,
                rule.operator,
                rule.value,
                1 if rule.blocking else 0,
                rule.human_readable,
            ),
        )
    return rules


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
        # 第 5 章的已问台账（重问标注与重问上限）由它推导。compute_* 是纯函数，
        # 不自己查库——这份数据由 app/web/server.py 的 _run_turn 放进 state。
        asked_question_rounds=list(state.get("asked_question_rounds", [])),
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
        # 溯源三件套（第 7 章）。时序留痕不承担审计职责，所以模型标识不走
        # llm_latency_ms 那条线，而是随未溯源清单一起、按 intake-field-grounding
        # 的「编造信号可按模型版本归因」落库。
        "ungrounded_fields": result.ungrounded_fields,
        "written_fields": result.written_fields,
        "llm_response_model": result.llm_response_model,
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

    2026-08-27（第 7 章 tasks 7.5/7.9）：ungrounded_fields / written_fields /
    llm_response_model 三列写在**同一条 INSERT** 里，不新增 effect 节点、
    business_key 语义不变（仍是 round_count）。理由与上面的时序两列完全一致：
    intake-field-grounding 的「来源与画像同生共死」要求"画像有这一轮、来源没有
    这一轮"不可能出现，同一条 INSERT 是这条契约唯一自然成立的形态。
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
        "turn_started_at, llm_latency_ms, is_productive, asked_questions, "
        "ungrounded_fields, written_fields, llm_response_model) "
        "VALUES (?, ?, ?, 'drafting', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            json.dumps(state.get("ungrounded_fields", []), ensure_ascii=False),
            json.dumps(state.get("written_fields", []), ensure_ascii=False),
            state.get("llm_response_model"),
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
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    profile_dict: dict,
    reviewer: str,
) -> None:
    """
    effect_* 节点：把最新画像草案冻结为 approved，同步更新 job.status，并记一条
    human_review。business_key = 冻结的 version 号，防止同一版本被重复确认两次。

    不在这里 conn.commit() —— 理由同 effect_persist_draft：写入与 effect_log
    记录必须由 idempotent_effect 装饰器在同一个事务里一次性提交。

    2026-08-27（tasks 6.9）：profile_dict 这个入参此前只是收下不用，现在承载知情
    确认留痕（`_gap_acknowledgement`）。留痕与 status='approved' 必须落在同一条
    事务里（铁律 1）——分开写会出现"画像已确认但查不到确认时是否知情"，而这正是
    spec「使事后可以查明确认时业务经理是否知情」要杜绝的状态。

    2026-09-04（tasks 6.4）：human_review 那一条痕也进**同一个事务**。它回答的是
    另一个问题——"谁、在什么时候、确认了哪一版画像"（spec「决策留痕」）。
    ⛔ 不新增一个 effect_record_confirmation 节点去单独写它：多一个节点就多一个
    幂等键，而两个幂等键意味着"状态已 approved、留痕却缺席"是一个可达状态，
    合规上答不出话。

    profile_version 直接取 int(business_key)：调用方（app/web/server.py 的
    confirm）传的 business_key 就是被冻结的 version（与 effect_persist_draft
    用 int(business_key) 推 version 是同一条约定）。⛔ 不另加一个 version 参数
    ——同一个事实有两个入参，迟早会有一个调用点把它们传得不一致。

    2026-09-04（tasks 5.8/5.9）：硬门槛规则草案（`hard_requirement`）也进**同一个
    事务**，由 `_record_hard_requirements()` 写。提取本身是 `app/agents/
    hard_requirement.py` 里的确定性纯函数，⛔ 不调模型。草案只**存**规则、不
    **执行**规则——本变更包内没有任何代码读 `blocking` 去淘汰候选人（合规红线：
    AI 只做排序推荐，不做自动淘汰）。
    """
    conn.execute(
        "UPDATE job_profile SET status = 'approved', profile_json = ? "
        "WHERE job_id = ? AND version = (SELECT MAX(version) FROM job_profile WHERE job_id = ?)",
        (json.dumps(profile_dict, ensure_ascii=False), thread_id, thread_id),
    )
    conn.execute("UPDATE job SET status = 'approved' WHERE id = ?", (thread_id,))
    # 2026-09-04（tasks 5.8/5.9）：硬门槛规则草案也进**同一个事务**。它回答的是
    # "按这一版画像，哪些条件是可自动判定的门槛"（spec「硬门槛规则草案提取」）。
    # ⛔ 不新增一个 effect_extract_hard_requirements 节点：多一个节点就多一个
    # 幂等键，而两个幂等键意味着"画像已 approved、规则草案却缺席"是一个可达
    # 状态——将来筛简历时没人会发现草案缺了，只会以为这个岗位本来就没门槛。
    # ⛔ 放在 _record_human_review 之前：规则草案是这次确认的产物，人工留痕是
    # 这次确认的凭据，顺序调过来不会出错，但保持"先产物后凭据"与
    # effect_abandon_profile 的写法一致。
    _record_hard_requirements(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        profile_dict=profile_dict,
    )
    _record_human_review(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        decision_type=DECISION_APPROVED,
        reviewer=reviewer,
    )


# 同一岗位的画像修改次数上限（tasks 6.6 / spec「修改次数上限」）。
# 超限不是失败，是**换一条路**：提示转人工，由 HR 直接编辑画像后提交确认。
# ⚠️ 上限的意义是护住业务经理的耐心（design.md 决策六：一个追问到第 8 轮的
# 机器人，下次就没人用了），⛔ 不要因为"多改几次也没坏处"把它调大。
MAX_REVISIONS = 5


@idempotent_effect("effect_request_revision")
def effect_request_revision(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    reviewer: str,
    feedback: str,
) -> None:
    """
    effect_* 节点：记一次「修改」决策，独占、幂等。
    business_key = 被打回的那一版画像 version。

    ⛔ **本节点不改画像、不改任何状态。** 修改意见的落地是下一轮采集：
    app/web/server.py 的 revise 把 feedback 当作用户这一轮的原话交给 _run_turn，
    原画像作为 profile_patch_accumulated 一起送进 prompt，产出的是 job_profile
    的**新一版**（design.md 决策四：画像改动走新版本，每一版草案都保留）。
    ⛔ 绝不 UPDATE 已有的 job_profile 行——那会让"当时按什么标准筛的"答不上来。

    ⛔ 不与 effect_confirm_profile / effect_abandon_profile 合并成一个"处理确认"
    节点。三条路径的对外后果完全不同（冻结并触发一次真实的 LLM 调用 / 什么都不
    冻结只记一笔 / 终止），合成一个节点后，"恢复时节点从头整个重跑"会带着一个
    分支参数走进另一条路径，而幂等键只有一个（工程铁律 1、2）。

    不在这里 conn.commit() —— 理由同 effect_persist_draft。
    """
    _record_human_review(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        decision_type=DECISION_REVISION_REQUESTED,
        reviewer=reviewer,
        feedback=feedback,
    )


def revision_count(conn: sqlite3.Connection, job_id: str) -> int:
    """已提出的修改次数。**真源 = human_review 行数**，⛔ 不另存计数列。

    另存一列计数器就多一个会漂移的真源，而漂移**没有任何症状**：不报错、
    不失败，只是上限悄悄算错——业务经理要么在第 3 次就被拦下，要么改到第 8 轮
    还没人拦。这与 app/graph/state.py 里"预算计数器不放进 state 自增"是同一条
    理由。
    """
    return conn.execute(
        "SELECT COUNT(*) FROM human_review WHERE job_id = ? AND decision_type = ?",
        (job_id, DECISION_REVISION_REQUESTED),
    ).fetchone()[0]


@idempotent_effect("effect_abandon_profile")
def effect_abandon_profile(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    reviewer: str,
    feedback: str | None = None,
) -> None:
    """
    effect_* 节点：把岗位与当前画像版本置为 abandoned，独占、幂等。
    business_key = 被放弃的那一版画像 version。

    ⛔ **内容一字不改**（tasks 6.7 原话"置 abandoned，保留内容"）：不删
    job_profile 行、不清 profile_json、不动 conversation。放弃不是撤销——
    事后要能查明"当时放弃的是哪一版画像、内容长什么样"。

    ⛔ 不与 effect_confirm_profile / effect_request_revision 合并（理由见
    effect_request_revision 的 docstring）。

    不在这里 conn.commit() —— 理由同 effect_persist_draft：两条 UPDATE 与
    human_review 留痕、effect_log 记录必须由 idempotent_effect 装饰器在同一个
    事务里一次性提交（工程铁律 1）。
    """
    conn.execute(
        "UPDATE job_profile SET status = 'abandoned' WHERE job_id = ? AND version = ?",
        (thread_id, int(business_key)),
    )
    conn.execute("UPDATE job SET status = 'abandoned' WHERE id = ?", (thread_id,))
    _record_human_review(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        decision_type=DECISION_ABANDONED,
        reviewer=reviewer,
        feedback=feedback,
    )


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


@idempotent_effect("effect_enqueue_pending_approval")
def effect_enqueue_pending_approval(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    message: CandidateOutboundMessage,
    blocked_reason: str,
) -> str:
    """
    effect_* 节点：把一条被门禁拦下的候选人草稿写进待审批队列，独占、幂等。

    `business_key` = 草稿内容哈希，于是幂等键是
    `{thread_id}:effect_enqueue_pending_approval:{content_hash}`——与 U1 的
    `(thread_id, content_hash)` 唯一索引**同粒度**（U1 偏离登记 2 就是为了这条
    才把单列索引改成两列的）。两道防线粒度不一致时，宽的那道形同虚设。

    不在这里 `conn.commit()` —— 理由同 `effect_persist_draft`：写入必须与
    `effect_log` 记录由 `idempotent_effect` 装饰器在同一个事务里一次性提交。

    ⛔ 本函数体内不 append JSONL（delivery-units.md §3.4 第 2 条）。留痕是
    `effect_record_outbound_audit` 的事，镜像 append 更是在事务提交之后才发生。

    ⚠️ **D5 死锁防线的另一半**（Task 2 交付了 `queue.approve()` 从不重新入队
    那一半）：携带 `confirmed_by` 的消息**拒绝入队**。`queue.enqueue()` 本身
    不检查这一列——它按 `(thread_id, content_hash)` 无条件 `ON CONFLICT DO
    NOTHING`（Task 1 design），`content_hash()` 又刻意不含 `confirmed_by`，
    所以底下这层完全看不出"这条已经签过名了"。签名消息本就是从队列里取出、
    带着签名重走门禁的那一条（`queue.approve()`），它不该再被送回这个入队
    节点——这道检查在 enqueue() 之前挡住它，不指望下面的表结构或唯一索引
    替它把关。
    """
    if message.confirmed_by is not None:
        raise ValueError(
            f"携带 confirmed_by={message.confirmed_by!r} 的消息不能入队"
            "（design D5 死锁防线：只有首次被门禁拦下、尚未签名的草稿才入队，"
            "签名消息走的是 queue.approve() 的放行路径）"
        )
    return queue.enqueue(
        conn, thread_id=thread_id, message=message, blocked_reason=blocked_reason
    )


@idempotent_effect("effect_record_outbound_audit")
def effect_record_outbound_audit(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    recorder: AuditRecorder,
    event: DecisionEvent,
) -> bool:
    """
    effect_* 节点：外发/拦截动作的留痕，独占、幂等。

    `business_key` = `{content_hash}:{allowed}:{reason}`（tasks 5.4，经变更包
    `outbound-retry-audit-trace` 订正；原为 `{content_hash}:{allowed}`）——同一草稿
    的"拦截"与"放行"各留一条痕，**不同原因的两次拦截也各留一条**。
    ⛔ 只用 content_hash 的话，放行那条会命中拦截那条的 `effect_log` 被短路，
    于是**投递发生了却没有留痕**；只到 `{allowed}` 的话，"首次被拦"与"放行时被
    总开关拦下"两次的键逐字相同（content_hash 不含 confirmed_by），第二次尝试
    **一条痕都不产生**——这正是 TD-9。

    ⚠️ 现在有**两个调用点**共用同一条公式：`app/outbound/delivery.py` 的
    `deliver_candidate_message()` 与 `app/outbound/queue.py` 的 `approve()`，
    两者都经由 `app.outbound.delivery.audit_business_key()` 求值，
    ⛔ 不许任何调用点自己再拼一遍这个字符串。

    返回值是 `recorder.record()` 的返回值，对外发事件**恒为 `False`**：外发事件
    在 `analysis_run` 里没有真身（它的真身是 `pending_approval`），
    `SqliteSink.SUPPORTED_EVENT_TYPES` 只收 `ai_analysis`。
    ⛔ **调用方不得把这个 `False` 当成"写失败"，更不得据此跳过镜像 append**——
    镜像里那一行是外发留痕唯一的载体。调用点按自己写的 `event_type` 决定行为，
    不从 `False` 反推原因（2026-08-28 对残留 B 的拍板）。

    ⛔ 函数体内不 append JSONL：`recorder.mirror()` 由调用点在本节点**返回之后**
    触发，那时装饰器已 `commit`（delivery-units.md §3.4 第 3 条）。
    """
    return recorder.record(conn, event)
