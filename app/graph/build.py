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

        effect_deliver_message(
            conn,
            thread_id=state["job_id"],
            business_key=message_business_key(payload),
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

    # SqliteSaver.from_conn_string(db_path) returns a context manager, not a
    # ready checkpointer — using it directly (without `with`) breaks
    # graph.compile() with "Invalid checkpointer provided". SqliteSaver(conn)
    # takes a raw sqlite3.Connection instead, so it reuses the connection this
    # function already received rather than opening a second one to the same
    # file.
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer)
