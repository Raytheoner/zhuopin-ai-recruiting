from __future__ import annotations

from typing import TypedDict


class IntakeState(TypedDict, total=False):
    job_id: str

    # 完整对话记录（user / assistant 交替），**每次 invoke 都必须传入完整的一份**。
    #
    # 这里刻意不加 Annotated[..., operator.add] 之类的 reducer：本图的真源是数据库
    # （round_count 从 job_profile 计数、profile_patch_accumulated 从 job_profile 读回、
    # history 从 conversation 表读回），checkpoint 只是执行过程的快照。没有 reducer 时
    # LangGraph 按 LastValue 处理，"输入覆盖 checkpoint"正是这套取数方式想要的语义，
    # 而且天然重放安全——工程铁律1 要求节点从头重跑不能产生额外副作用，用 operator.add
    # 的话，同一份输入被重放一次就会把用户消息在历史里追加两遍。
    #
    # 危险的不是"没有 reducer"，而是"没有 reducer 却只把本轮新消息当成完整 history 传
    # 进来"——那正是 review 发现的 Critical bug：第二轮起模型只看得到最新一句话。
    # 调用方（app/web/server.py 的 _run_turn）必须先从 conversation 表读回完整历史、
    # 追加本轮新消息，再整份传进来。
    history: list[dict]

    # job_profile 总行数。business_key 的口径，语义不变（design.md 决策 5）。
    round_count: int
    # is_productive=1 的行数。MAX_ROUNDS 按它计数，MAX_TOTAL_ROUNDS 按
    # round_count 计数，任一命中即收尾。两个都由 app/web/server.py 的 _run_turn
    # 从库里查出来传进来——预算计数器**不放进 state 自增**，那会引入第二个
    # 真源（design.md 决策 5 否决的替代方案）。
    productive_round_count: int
    profile_patch_accumulated: dict

    # 这个 job 此前所有轮次问过的 question_id 并集（已问台账），由 _run_turn
    # 从 job_profile.asked_questions 读出。compute_intake_turn 用它判定本轮
    # 有没有问出新问题。
    asked_question_ids_before: list[str]

    # 上一轮实际问出的问题（IntakeQuestion.to_payload() 的列表），同样由
    # _run_turn 从库里读。用途有二：反问判定要拿上一轮的问题文本当"线索"；
    # "候选档位不得代替用户做决定"要知道上一轮给过哪些档位。
    previous_questions: list[dict]

    # 每一轮问出的问题（`IntakeQuestion.to_payload()` 的列表），外层一项 = 一轮，
    # 按 version 升序。由 _run_turn 从 job_profile.asked_questions 读出，与
    # asked_question_ids_before 同源——后者是它拍平后的并集。
    #
    # 第 5 章的已问台账（问了几轮 / 答没答 / 重问几次）全部由它 + 画像现值
    # **推导**，不在 state 或库里另存一份状态：多存一份就多一个会漂移的真源，
    # 而漂移没有任何症状（不报错、不失败，只是重问次数悄悄算错）。这与本文件
    # 开头"真源是数据库、checkpoint 只是执行过程快照"是同一条理由。
    asked_question_rounds: list[list[dict]]

    # 每项是 IntakeQuestion.to_payload() 的结果（纯 dict），不是 IntakeQuestion
    # 实例：state 会被 SqliteSaver 序列化进 checkpoint，纯 dict 的往返语义是
    # 确定的，dataclass 则依赖序列化器实现细节——"重放后类型变了"是只在恢复
    # 路径上炸的故障。结构定义见 app/agents/intake_question.py。
    pending_questions: list[dict]

    is_complete: bool
    is_job_related: bool
    # 系统按字段表推导的未指定字段（tasks 6.1，真源）。
    unspecified_fields: list[str]

    # 模型自称的未指定字段（tasks 6.2，对照）。与上面那个键刻意分成两个键：
    # 混用一个键名就迟早会有人把对照值当真源用，而那个 bug 的表现是"警示块里
    # 少列了一个字段"——没人会注意到。
    model_claimed_unspecified_fields: list[str]

    # 本轮是否有产出，由 compute_intake_turn 判定、effect_persist_draft 落进
    # job_profile.is_productive。
    is_productive: bool
    # 本轮实际问出的问题（payload 列表），与 is_productive 同一条 INSERT 落库。
    asked_questions: list[dict]

    # 本轮开始时刻（HTTP 请求进入、还没调模型），由 app/web/server.py 的
    # _run_turn 写入，节点只透传。轮次**结束**时刻沿用 job_profile.created_at。
    turn_started_at: str

    # 本轮 LLM 累计耗时（含重试），由 compute_intake_turn 写入、
    # effect_persist_draft 与画像草案在同一条 INSERT 里落库。
    llm_latency_ms: float

    # 本轮未溯源的业务字段名（第 7 章 intake-field-grounding）。
    # 只观测不拦截：这几个键的存在不影响图的任何分支判断。
    ungrounded_fields: list[str]

    # 本轮写入的业务字段名，编造率的分母。
    written_fields: list[str]

    # API 响应里实际返回的模型标识（铁律 5）。与配置里的别名分开记录、
    # 不互相覆盖——配置里写的名字不算数，响应返回的才算。
    llm_response_model: str | None
