from __future__ import annotations

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from app.graph.nodes import compute_intake_turn, effect_deliver_message, effect_persist_draft, message_business_key
from app.graph.state import IntakeState


def build_intake_graph(db_path: str, *, gateway, conn, channel):
    """
    单轮采集流程：compute_intake_turn → effect_persist_draft → effect_deliver_message → END。
    每次 HTTP 请求 invoke 一次；跨请求的对话历史由 SqliteSaver 按 thread_id 持久化恢复。
    """
    graph = StateGraph(IntakeState)

    def _compute_node(state: IntakeState) -> IntakeState:
        return compute_intake_turn(state, gateway=gateway)

    def _persist_node(state: IntakeState) -> IntakeState:
        effect_persist_draft(
            conn,
            thread_id=state["job_id"],
            business_key=str(state["round_count"] - 1),
            state=state,
        )
        return state

    def _deliver_node(state: IntakeState) -> IntakeState:
        from app.channels.base import OutboundMessage

        if state.get("is_complete"):
            payload = {
                "type": "confirmation_prompt",
                "profile_patch_accumulated": state.get("profile_patch_accumulated", {}),
                "unspecified_fields": state.get("unspecified_fields", []),
            }
            message = OutboundMessage(type="confirmation_prompt", payload=payload)
        else:
            payload = {"questions": state.get("pending_questions", [])}
            message = OutboundMessage(type="question", payload=payload)

        # business_key 前缀带上 round_count：message_business_key() 本身只是
        # 内容哈希，如果两轮问出的问题恰好完全相同（例如用户没回答，LLM 在下一轮
        # 原样重问），纯内容哈希会让第二轮的合法投递被误判成第一轮的重放而静默
        # 跳过。带上 round_count 后，同一轮内的真实重放（round_count 相同）仍然
        # 会命中同一个 business_key、正确去重；不同轮次即使内容相同也会得到不同
        # 的 business_key，不会被误杀。
        effect_deliver_message(
            conn,
            thread_id=state["job_id"],
            business_key=f"{state['round_count']}:{message_business_key(payload)}",
            channel=channel,
            message=message,
        )
        return state

    graph.add_node("compute_intake_turn", _compute_node)
    graph.add_node("effect_persist_draft", _persist_node)
    graph.add_node("effect_deliver_message", _deliver_node)

    graph.set_entry_point("compute_intake_turn")
    graph.add_edge("compute_intake_turn", "effect_persist_draft")
    graph.add_edge("effect_persist_draft", "effect_deliver_message")
    graph.add_edge("effect_deliver_message", END)

    # 修复 CI 抓到的事务归属冲突（docs/findings/2026-08-13-sqlite-事务归属冲突.md）：
    # checkpointer 与 effect 层（app/storage/idempotency.py 的 idempotent_effect）
    # 曾经共用调用方传入的这一个 conn，导致同一个连接上有两个互相不知情的事务
    # 管理者各自 commit/rollback。方向 A（design.md 已选定并评估过 WAL 代价）：
    # 让 checkpointer 拿一个指向同一个数据库文件、但完全独立的连接。
    #
    # 不用 SqliteSaver.from_conn_string(db_path)：它返回的是一个 context
    # manager，不是 ready checkpointer——不经 `with` 直接传给 graph.compile()
    # 会报 "Invalid checkpointer provided"（本函数上一版这条注释踩过的坑），
    # 而这里需要连接活到编译出的图对象的生命周期结束，不能在本函数返回前就
    # 提前退出 `with` 块。直接复用 app.storage.db.get_connection() 再开一个
    # 连接，拿到的是一个可以自由持有、随时通过 `.conn` 属性访问、显式关闭的
    # sqlite3.Connection，语义更直接。
    from app.storage.db import get_connection

    checkpointer_conn = get_connection(db_path)
    checkpointer = SqliteSaver(checkpointer_conn)
    return graph.compile(checkpointer=checkpointer)
