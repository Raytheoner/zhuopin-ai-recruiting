"""候选人外发门禁。

**判定在这里，副作用不在这里。** `compute_outbound_gate()` 是纯函数：
不写库、不发消息、不读配置文件，同一输入判多少次结果都一样
（spec「门禁判定与副作用分离」）。入队、投递、留痕三类副作用各自归
`app/graph/nodes.py` 的 `effect_*` 节点（交付单元 U5）。

这道闸存在的理由：`effect_deliver_message` 在本变更之前是**无条件投递**，
合规红线「AI 只做排序推荐，不做自动淘汰」全靠调用方自觉。
"""

from app.outbound.gate import GateDecision, compute_outbound_gate
from app.outbound.messages import CandidateOutboundMessage

__all__ = [
    "GateDecision",
    "compute_outbound_gate",
    "CandidateOutboundMessage",
    "deliver_candidate_message",
]


def __getattr__(name: str):
    """
    延迟导出 `deliver_candidate_message`（PEP 562）。

    ⚠️ **不能**在模块顶层 `from app.outbound.delivery import
    deliver_candidate_message`：`delivery.py` 反向 import 了
    `app.graph.nodes`，而 `app.graph.nodes` 在其模块顶部就有
    `from app.outbound import queue`——若本包的 `__init__.py` 在包初始化期间
    就急切执行到 `delivery.py`，会在 `app.graph.nodes` 尚未定义
    `effect_deliver_message` 等名字时被回头 import，触发
    `ImportError: cannot import name '...' from partially initialized module`
    （2026-08-28 实测：任何先 import `app.graph.nodes` 的测试文件，如
    `tests/test_graph_nodes.py`，会在收集阶段就炸）。延迟到首次属性访问才
    导入，可以让 `app.graph.nodes` 有机会先把自己的模块体跑完。
    """
    if name == "deliver_candidate_message":
        from app.outbound.delivery import deliver_candidate_message

        return deliver_candidate_message
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
