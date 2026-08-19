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

    round_count: int
    profile_patch_accumulated: dict

    # 每项是 IntakeQuestion.to_payload() 的结果（纯 dict），不是 IntakeQuestion
    # 实例：state 会被 SqliteSaver 序列化进 checkpoint，纯 dict 的往返语义是
    # 确定的，dataclass 则依赖序列化器实现细节——"重放后类型变了"是只在恢复
    # 路径上炸的故障。结构定义见 app/agents/intake_question.py。
    pending_questions: list[dict]

    is_complete: bool
    is_job_related: bool
    unspecified_fields: list[str]

    # 本轮开始时刻（HTTP 请求进入、还没调模型），由 app/web/server.py 的
    # _run_turn 写入，节点只透传。轮次**结束**时刻沿用 job_profile.created_at。
    turn_started_at: str

    # 本轮 LLM 累计耗时（含重试），由 compute_intake_turn 写入、
    # effect_persist_draft 与画像草案在同一条 INSERT 里落库。
    llm_latency_ms: float
