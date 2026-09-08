"""
2.5「重试耗尽 → 转 needs_manual」的两个 `effect_*` 节点。

放在**新文件**而不是 `app/graph/nodes.py`：本交付单元对 nodes.py 的改动被限定在
"提取异常 → state 信号"那一处（纯函数侧），有副作用的两个动作独占各自的节点、
各带幂等键（工程铁律 1/2）。顺带避开与并行泳道在 nodes.py 上的合并冲突。

⛔ 本模块不写 `human_review`：转人工是**系统判定**，不是人工决策。往
`human_review` 里塞一条 `reviewer='system'` 会让合规红线「淘汰必须有人工确认
节点并留痕」的留痕表里混进机器决策，审计那天分不出哪条是人做的。
"""

from __future__ import annotations

import logging
import sqlite3

from app.channels.base import Channel, OutboundMessage
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)

# 转人工的原因码。⛔ 两个字面量只在这里定义，nodes.py 与前端/队列都引用它，
# 各处自己写字符串就会出现"落库写 schema_exhausted、断言查 schema_failed"
# 这种不报错的不一致。
REASON_SCHEMA_EXHAUSTED = "schema_retry_exhausted"
REASON_PROVIDER_UNAVAILABLE = "provider_unavailable"

# 下发给业务经理的固定文案。**系统写死的字符串，不是模型产出**，所以
# ⛔ 不需要《AI 生成合成内容标识办法》要求的 AI 生成标识——那条红线管的是
# AI 生成的 JD / 拒信 / 邀约。
_REASON_TEXT = {
    REASON_SCHEMA_EXHAUSTED: (
        "系统连续几次都没能把这段需求解析成结构化画像，已转交 HR 人工处理。"
        "刚才这条消息没能被处理，请稍后重新发一次；此前已确认的画像内容不受影响。"
    ),
    REASON_PROVIDER_UNAVAILABLE: (
        "模型服务暂时不可用（主备两家都没能应答），本轮已转交 HR 人工处理。"
        "刚才这条消息没能被处理，请稍后重新发一次；此前已确认的画像内容不受影响。"
    ),
}

# ⛔ 终态不得被覆盖：已确认（approved）与已放弃（abandoned）都是终态，
# 一次模型故障不该把一个已经冻结的画像拽回"待人工处理"。
_TERMINAL_STATUSES = ("approved", "abandoned")


def manual_handoff_text(reason_code: str) -> str:
    """原因码 → 给业务经理看的中文。未登记的原因码退回通用文案，⛔ 不抛异常：
    这条路径本来就是"出事之后"的路径，在这里再炸一次只会把一次可恢复的转人工
    变成一个 500。"""
    return _REASON_TEXT.get(
        reason_code,
        "本轮采集没能完成，已转交 HR 人工处理。"
        "刚才这条消息没能被处理，请稍后重新发一次；此前已确认的画像内容不受影响。",
    )


@idempotent_effect("effect_mark_needs_manual")
def effect_mark_needs_manual(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, reason_code: str
) -> None:
    """
    把岗位置为 `needs_manual`，让 `derive_needs_manual_reasons` 的第 1 个来源
    （`job.status = 'needs_manual'`）真正有值——那个来源在
    `app/storage/job_queries.py:224` 写着"今天恒为空，2.5 落地当天自动生效"。

    ⛔ 不在这里 `conn.commit()`：业务写与 `effect_log` 必须由
    `idempotent_effect` 装饰器在同一个事务里提交一次（工程铁律 1）。

    ⛔ 不写 `job_profile`：2.5 要求"不产出半成品"——重试耗尽的这一轮
    **不落新版本画像**，所以这里只动 `job` 一张表。
    """
    logger.warning(
        "job_id=%s 转人工（原因: %s，business_key=%s）。⛔ 本轮不落新版本画像，"
        "已采集内容原样保留。",
        thread_id,
        reason_code,
        business_key,
    )
    conn.execute(
        "UPDATE job SET status = 'needs_manual' WHERE id = ? AND status NOT IN (?, ?)",
        (thread_id, *_TERMINAL_STATUSES),
    )


@idempotent_effect("effect_deliver_manual_handoff")
def effect_deliver_manual_handoff(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    channel: Channel,
    reason_code: str,
    round_count: int,
) -> None:
    """
    把"已转人工"这件事下发给通道。

    ⚠️ 这一步**不是可选的**：`app/web/server.py` 的 `_run_turn` 结尾无条件读
    `channel.latest(job_id)` 并取 `.type`，转人工路径不投递任何消息的话它会拿到
    None、当场 AttributeError——一次可恢复的转人工会变成一个 500，而业务经理
    只看到"服务器错误"。`OutboundMessage.type` 的取值表
    （`app/channels/base.py:9`）里本来就留了 "needs_manual" 这一项。

    ⛔ 投递独占一个节点，⛔ 不与 effect_mark_needs_manual 合并（工程铁律 1：
    每个有副作用的动作独占一个节点）。
    """
    channel.deliver(
        thread_id,
        OutboundMessage(
            type="needs_manual",
            payload={
                "reason_code": reason_code,
                "message": manual_handoff_text(reason_code),
                "round_count": round_count,
            },
        ),
    )
