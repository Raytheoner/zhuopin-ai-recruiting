"""
留痕事件的领域模型。

design D2：按招聘领域自建字段，**不套**平台的 scenario / automation_level /
oem_context / override_reason。关键理由：套平台字段表就得把招聘语义塞进一个
payload 自由字典，而字典里的键**没法加数据库约束**——`evidence_ref` 为空必须
拒写这条就落不了地，工程铁律 4 直接失效。字段是一等公民还是字典键，决定了
约束能不能由存储层强制。

⚠️ **审计资产：本模块承载的数据禁止用作任何模型的训练、微调或调优输入。**
理由：历史评分与录用结果携带既有偏见，拿它当监督信号会把偏见放大并固化
（Amazon 2018 教训，见 CLAUDE.md 合规红线「绝不用历史录用结果做监督信号」）。
留痕只服务两件事：PIPL 第 24 条说明权（"这条评分是哪个模型、哪个版本、按哪份
rubric 打的，依据是简历里哪一段"）与 CI 里的合规断言。

⚠️ **本模块 MUST NOT 承载简历原文。** 输入只以 `input_hash` 形式记录，原文留在
简历主存储中按其自身访问控制管理（spec「AI 调用的可复现留痕」）。加字段前先想
一遍：它会不会把原文带进来。

⚠️ `confirmed_by` 现阶段**不可信**：鉴权是空壳（`AuthContext.user_id` 恒为
`None`），值只能由调用方传入（design D7）。SSO 落地后同一字段变可信，结构不改。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

# ── 事件类型白名单 ──────────────────────────────────────────────────────
# 未登记的类型在构造时就抛，不留给下游 sink 去猜。与门禁的 fail-closed 同一
# 口径：未知就是错，不是默认值。
AI_ANALYSIS = "ai_analysis"
OUTBOUND_BLOCKED = "outbound_blocked"
OUTBOUND_DELIVERED = "outbound_delivered"
BACKFILL = "backfill"

EVENT_TYPES = frozenset({AI_ANALYSIS, OUTBOUND_BLOCKED, OUTBOUND_DELIVERED, BACKFILL})


@dataclass(frozen=True)
class CriterionScore:
    """
    一条逐项评分。

    `evidence_ref` 是工程铁律 4 的落点，格式为"材料标识 + 位置区间"
    （如 `resume-1#120-180`），使人可以据此定位到简历原文或面试记录的具体片段。
    为空由数据库 `CHECK` 拒写（U1 已落），本层**不做**重复校验也**不做**兜底——
    应用层多一道"友好提示"就多一个把 IntegrityError 吞掉的地方。

    `id` 留空时由 `SqliteSink` 按 `{analysis_run_id}:{criterion_key}` 生成：
    确定性 id 让重放撞主键而不是插出第二行。
    """

    criterion_key: str
    score: float
    evidence_ref: str
    id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "criterion_key": self.criterion_key,
            "score": self.score,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class DecisionEvent:
    """
    一条可留痕的决策事实。AI 评分与外发门禁共用这一个形状——
    `specs/outbound-approval-gate` 要求"留痕 MUST 使用与 AI 评分留痕相同的机制，
    落入同一份可校验的记录中"，两套事件模型就等于两条链。

    字段按用途分四组，跨组字段为 None 是常态而不是异常：一次 AI 评分不会有
    `message_type`，一次拦截不会有 `raw_response`。
    """

    # ── 通用 ──
    # id 由调用方按 `{thread_id}:{node_name}:{business_key}` 生成（tasks 2.2）,
    # 与幂等键同源，所以 thread_id 不另设数据库列。
    id: str
    event_type: str
    thread_id: str | None = None
    created_at: str | None = None  # 留空由数据库 datetime('now') 填
    application_id: str | None = None
    job_id: str | None = None

    # ── AI 评分侧（铁律 3 的逐字兑现）──
    configured_model: str | None = None  # 配置里写的名字
    response_model: str | None = None  # API 响应实际返回的名字，铁律 5：响应返回的才算
    system_fingerprint: str | None = None  # 供应商不返回时记空值，留痕照常写入
    prompt_version: str | None = None
    temperature: float | None = None
    input_hash: str | None = None  # ⚠️ 只存哈希，绝不存原文
    rubric_version: str | None = None
    rubric_snapshot: dict[str, Any] | None = None
    raw_response: str | None = None
    token_usage: dict[str, Any] | None = None
    latency_ms: float | None = None
    scores: tuple[CriterionScore, ...] = ()

    # ── 外发门禁侧（U4/U5 消费，U2 只提供形状）──
    message_type: str | None = None
    recipient: str | None = None
    content_hash: str | None = None
    blocked_reason: str | None = None
    confirmed_by: str | None = None  # ⚠️ 现阶段不可信，见模块 docstring
    evidence: dict[str, Any] | None = None  # 判定所依据字段的原始取值，含空值

    # ── 补录（design D1：镜像缺行走链尾补录，不插回原位）──
    backfill_of: str | None = None

    # ── 失败标注 ──
    error: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"未登记的事件类型: {self.event_type!r}；已登记: {sorted(EVENT_TYPES)}"
            )
        # frozen dataclass 上挂一个可变列表是个陷阱：调用方后续 append 会静默
        # 改掉一个"不可变"对象。统一折成 tuple。
        object.__setattr__(self, "scores", tuple(self.scores))

    def to_dict(self) -> dict[str, Any]:
        """
        折成可 JSON 序列化的字典。**空 error 字段被剔除**（tasks 2.1）：正常事件
        里挂一个 `"error": null` 会让镜像里每一行都带着一个永远为空的键，读的人
        以为这里曾经出过错。

        ⚠️ 只剔 error。其余 None 一律保留——spec「供应商不返回部署指纹」要求该
        字段"记为空值、留痕照常写入"，一并剔掉就分不清"这次没拿到"和"这版代码
        还没这个概念"。
        """
        payload: dict[str, Any] = {
            field.name: getattr(self, field.name) for field in fields(self)
        }
        payload["scores"] = [score.to_dict() for score in self.scores]
        if not (self.error or "").strip():
            payload.pop("error")
        return payload
