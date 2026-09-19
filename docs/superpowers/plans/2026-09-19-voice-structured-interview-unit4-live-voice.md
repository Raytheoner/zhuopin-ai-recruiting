# U4 live 语音链路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立的语音主机上跑通候选人语音结构化面试的完整链路——`.51` 出站下发冻结题目快照、语音主机上的 agents worker 按题序播报／端点检测／流式转写／在预埋集合内选追问／处理打断／全程录制、候选人网络不佳时可降级为文本作答、`.51` 出站轮询把 turn 事件与录音拉回并让语音主机删除副本——全程语音主机不接触简历、候选人身份字段或评分数据，两机之间的每一次调用都签名防重放。

**Architecture:** 两台机器，`.51` 无公网入站、语音主机无入站连接 `.51`（D12）。`.51` 侧新增 `app/live_voice/`（HMAC 签名 + 出站客户端）、`app/agents/follow_up_selector.py`（L3 纯函数，D17）、`app/graph/live_session_nodes.py`（四个 `effect_*` 节点 + 轮询编排，D20）、两个批处理脚本。语音主机侧是全新的顶层包 `voice_host/`（独立部署，不 import `app.storage`），跑 FastAPI 服务端点 + LiveKit Agents worker + 本地 SQLite 事件队列；ASR/TTS/LiveKit 三个重依赖全部惰性 import 并通过 Protocol 适配器注入，测试与内部模拟用 Fake 适配器,不需要真实安装这些包。两侧共享的契约（签名算法、`SessionBundle`/`LiveTurnEvent` schema、`follow_up_selector.py`）在 `.51` 仓库里只有一份源码，`sync-to-voice-host.sh` 部署时原样拷贝一份到语音主机，防止字段定义漂移。

**Tech Stack:** Python、pytest、SQLite（`.51` 侧 `app/storage/db.py`；语音主机侧独立的 `voice_host/queue_store.py`）、FastAPI + httpx（两机接口）、LLM 调用经既有 `app/llm/gateway.py::LLMGateway`；语音主机侧 LiveKit Agents SDK / FunASR / CosyVoice（P5 通过，P2/P3 阻塞，D15：不可装或超预算 ⇒ 本单元的 build 阶段「⏸ 留步」，但代码与测试照常交付，真实安装验证是 tasks 5.1/5.11/8.7 的现场范围）。

**Spec:** `openspec/changes/voice-structured-interview/specs/live-voice-interview-session/spec.md`（本计划实现该 spec 全部 8 条 Requirement）与 `openspec/changes/voice-structured-interview/specs/interview-recording-retention/spec.md`（本计划实现其中「语音主机不留副本」1 条 Requirement；「留存期限在场次建立时固定」「到期自动删除并留痕」「访问留痕」「候选人撤回或终止」四条已由 U1/U3 交付或属 U7 范围，不在本计划）。对应 `openspec/changes/voice-structured-interview/tasks.md` 第 5 章 5.1–5.11。设计依据 `design.md` 决策 D12/D15/D17/D18/D19/D20。

**前置状态（如实记录，⛔ 不假装已就绪）：** `docs/m3-voice-probe.md` 显示 P2（FunASR 首字延迟）、P3（CosyVoice 首帧延迟）当前「阻塞」（分别缺 30s 中文样本音频、缺 `hyperpyyaml` 依赖），0.4「语音主机采购与预算」未完成。按 tasks.md 0.2 判据，本单元的**代码交付与自动化测试**不受此阻塞（P2/P3 阻塞只挡真实音频的现场联调，即 tasks 5.11/8.7），但**真实安装、真实语音链路联调、目标机部署**在 0.4 到位、P2/P3 重跑通过前 ⏸ 留步。本计划的自动化测试全程使用 Fake ASR/TTS/录制适配器验证编排逻辑，不依赖真实安装。

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」与「合规红线」两节逐字复制，每条相关条目都出现，不适用的写明理由，不省略。

**工程铁律：**

1. LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。**（适用）** `.51` 侧 `effect_open_session`/`effect_persist_turn`/`effect_close_session`/`effect_fetch_recording` 全部用 `@idempotent_effect` 装饰，幂等键字面匹配 D20 给出的四条公式（Task 5）。语音主机侧 `voice_host/queue_store.py` 不是 LangGraph 节点、不写 `.51` 的 `effect_log`——它是 D19 明确要求的"本地 SQLite 事件队列"，幂等性靠 `(session_id, seq)` 唯一约束（Task 8），与铁律 1 的立意（重放不产生重复业务行）一致但不是同一套机制，语音主机结构上不可能访问 `.51` 的 `effect_log` 表（无库访问，见铁律 2 的适用说明）。
2. L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。**（适用）** `app/agents/follow_up_selector.py::select()` 是 L3（Task 3），只调网关，不 `import app.storage`。`app/graph/live_session_nodes.py` 里 `compute_session_bundle` 只读不写，`effect_*` 前缀函数才写（Task 5）。语音主机侧 `voice_host/worker.py` 不是 L3/L4 的一部分（它跑在另一台机器、不接触 `.51` 的数据库），但同一原则延伸适用：`voice_host/turn_cycle.py`（Task 10）是纯状态机，`voice_host/queue_store.py` 的写操作独占在各自的 `append_*`/`close_*`/`record_*` 函数里（Task 8）。
3. 所有 AI 评分必须持久化：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。**（部分适用）** 追问选择不是"评分"，但同属受铁律 5 约束的 AI 调用，沿用 `LLMGateway.extract_structured_with_meta` 既有留痕机制（`prompt_version="interview-follow-up-v1"`，Task 3）。本单元不写 `criterion_score`（那是 U5 范围），`evidence_ref`（铁律 4）与本单元无关。
4. 每条 `criterion_score` 必须有 `evidence_ref`。**（不适用）** 本单元不产出任何 `criterion_score` 行。
5. `temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。**（适用）** `follow_up_selector.select()` 复用既有 `LLMGateway`（`TEMPERATURE=0` 已是网关常量），不新增供应商配置。
6. 企微回调先落库再处理：只推一次、5 秒无响应即丢弃。**（不适用）** 本单元不涉及企微回调。
7. `langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。**（不适用于本单元的具体改动）** 本单元不新增或改动 `requirements.txt` 里的 `langgraph` 版本，也不建真实 StateGraph——`run_live_session_sync`/`voice_host/worker.py::run_session` 都是普通 Python 函数串联（沿用 `app/graph/interview_prep_nodes.py`/`interview_scoring_nodes.py` 已确立的判例，本计划「设计决策 1」）。项目全局约束仍然有效，reviewer 需确认本单元未引入更低版本的间接依赖；`voice_host/requirements.txt` 是独立的依赖清单（语音主机不跑 LangGraph），不受这条约束管辖。

**合规红线：**

- AI 只做排序推荐，不做自动淘汰。淘汰必须有人工确认节点并留痕。**（适用）** 本单元的四个 `effect_*` 节点只写 `interview_turn`/`interview_session.status`/`interview_live_event`/录音元数据，不碰 `application.current_stage_id`、不写 `rejection_record`。Task 15 的 e2e 断言 `rejection_record` 与 `application.current_stage_id` 前后不变。
- 禁止人脸/表情分析；声学情绪信号只展示给面试官，不进 `criterion_score`。**（适用）** 语音主机只处理音频，无任何视频/图像采集代码路径；`interview_turn.acoustic_ref` 由 U5 的 `effect_write_acoustic_refs` 写，本单元的 `effect_persist_turn` 结构上不写这一列（Task 5 的 INSERT 语句字面不含 `acoustic_ref`）。
- AI 生成的 JD、拒信、邀约须带标识。**（适用）** 候选人答题端页面（Task 13）逐题展示 AI 生成标识（复用 `app.agents.jd_agent.AI_LABEL_TEMPLATE` 同款文案约定）。
- 模型全部走境内，简历数据不出境。**（适用）** ASR/TTS 强制自托管（D15，⛔ 不切境内云 API）；追问选择复用既有境内网关配置，不新增供应商。
- 绝不用历史录用结果做监督信号。**（不适用）** 本单元不涉及任何历史录用结果数据。
- 候选人入口一律用一次性邀请链接。**（适用）** 候选人答题端页面只能通过 U3 已签发、已验证的场次访问（`session_id` 来自 URL，语音主机对未知/未开场 `session_id` 一律拒绝，Task 9）；本单元不新增任何绕过邀请令牌的入口。
- 主观描述不得进入硬门槛规则。**（不适用）** 本单元不产出任何硬门槛判定。

---

## Spec Requirement → Task 映射

| Spec Requirement | 覆盖 Task |
|---|---|
| 开场前置条件 | Task 6（`_sessions_ready_for_live_sync` 的前置查询）、Task 15（e2e） |
| 语音回路与延迟观测 | Task 11（worker 分段延迟记录）、Task 7（延迟报表） |
| 追问只在预埋集合内选择 | Task 3（`follow_up_selector.select`）、Task 11（worker 接线越界归一） |
| 打断处理 | Task 10（`turn_cycle.py` 状态机）、Task 11（worker 接线） |
| 全程录制与 turn 对齐 | Task 12（`recording.py`）、Task 5（`effect_persist_turn` 落 `audio_start_ms`/`audio_end_ms`） |
| 文本作答降级 | Task 13（切换端点 + `_ask_one_turn` 文本分支） |
| 中断可续入且不重复出题 | Task 6（`run_live_session_sync` 的 `since` 游标复用既有 `MAX(seq)` 查询，与 U3 4.3 `open_resume` 同一口径） |
| 简历数据不进语音主机 | Task 1（`SessionBundle` 反射测试）、Task 9（`voice_host/` 无 `app.storage` 引用的结构性守卫） |
| 场次副作用的幂等 | Task 5（四个 `effect_*` 节点） |
| 语音主机不留副本（留存 spec） | Task 12（回传成功即删）、Task 8（24h 过期兜底清理） |

## 本计划补的技术决策（design.md/tasks.md 字面未写全，裁定如下，不留 TBD）

1. **不建真实 LangGraph StateGraph**：`.51` 侧 `run_live_session_sync()` 与语音主机侧 `voice_host/worker.py::run_session()` 都是普通函数串联，沿用 U2/U5 已确立的判例（2026-08-26，Shao Peishen 判定"行为等价"）。
2. **两机契约的单一源码 + 部署时物理拷贝**：`app/schemas/session_bundle.py`、`app/schemas/live_turn_event.py`、`app/live_voice/signing.py`、`app/agents/follow_up_selector.py`（及其依赖的 `app/schemas/interview_ai_input.py`/`app/schemas/follow_up_result.py`/`app/llm/gateway.py`）只在 `.51` 仓库维护一份；`sync-to-voice-host.sh`（Task 14）用 `rsync -avR` 把这份白名单原样拷贝进语音主机部署目录，语音主机侧代码直接 import 这份拷贝，不重新实现算法。两侧字段/算法永远同源，不会出现"`.51` 改了签名算法、语音主机没跟着改"的漂移。
3. **两机接口鉴权**：预共享密钥（`VOICE_HOST_SHARED_SECRET`，两侧各自的环境变量，不进仓库）+ HMAC-SHA256 签名（`method\npath\ntimestamp\nsha256(body)`）+ 时间戳 ±60s 防重放（design 原文字面要求）。Header 名 `X-ZP-Timestamp`/`X-ZP-Signature`。
4. **`SessionBundle` 字段范围**：`session_id`、`prep_curve`、`follow_up_limit`、`questions[]`（每题 `question_id`/`seq`/`text`/`follow_ups`）。刻意不含 `dimension`/`difficulty`/`rubric_json`（评分相关，D19"MUST NOT 接收…评分数据"的从严解读，不只是不含身份字段）。`question_id` 是不透明的 UUID 字符串，语音主机把它原样带回 `LiveTurnEvent`，`.51` 侧靠它落 `interview_turn.question_id` 外键，不需要额外的题面匹配逻辑。
5. **语音主机侧本地事件队列不是 `effect_log`**：`voice_host/queue_store.py` 是 D19 明确要求的"本地 SQLite 队列"，与 `.51` 的 `effect_log` 机制是两回事（见 Global Constraints 铁律 1 的适用说明）。`turn_event` 表 `UNIQUE(session_id, seq)` 是它自己的幂等边界。
6. **`interview_turn.latency_json` 字段约定**：`{"endpoint_detection_ms", "asr_ms", "follow_up_selection_ms"(可选,仅追问轮), "tts_first_frame_ms", "end_to_end_ms"}`。文本作答 turn 只有 `end_to_end_ms`（等于候选人提交耗时，见 Task 13）。`scripts/report_m3_latency.py`（Task 7）按这个约定统计。
7. **`effect_close_session` 的 `business_key`**：D20 只给出节点名没给字面公式，裁定用常量 `"close"`（同场次只关闭一次），与 `app/graph/invite_nodes.py::effect_open_invite` 的 `business_key="open"` 同一先例。
8. **`effect_fetch_recording` 的编排顺序**：网络 GET（读，可安全重复）在效果节点**外面**先做，拿到内容与 sha256 后才把 sha256 作为 `business_key` 传给效果节点（与 `effect_deliver_invitation` 的 `draft_id` 先由调用方算好再传入同一形态）——效果节点内部只做"落盘 + 更新 DB + 通知语音主机删副本"这几件真正需要幂等保护的写操作。
9. **ASR/TTS/录制/LiveKit 房间传输全部走 Protocol 适配器，重依赖惰性 import**：`voice_host/adapters.py`/`voice_host/recording.py` 定义 `TTSAdapter`/`ASRAdapter`/`RecordingAdapter` 三个 `typing.Protocol`；真实实现（跑 FunASR/CosyVoice/livekit-agents）在函数体内部才 `import`，与 `scripts/probe_m3_voice.py` 的既有写法一致。测试与 Task 15 的内部模拟 e2e 全部用 `Fake*Adapter`，不需要真实安装这些重依赖——这是本单元的测试套件能在没有 P2/P3 通过的情况下依然可执行、可验证编排逻辑正确性的结构性原因。
10. **CosyVoice 走独立子进程/独立 venv**：`docs/m3-voice-probe-cosyvoice-install.md` 记录的真实阻塞是 `requirements.txt` 里钉死的 `grpcio==1.57.0` 与现代 `pip`/`setuptools` 不兼容（缺 `pkg_resources`），且 CosyVoice 官方要求 Python 3.10（与 `livekit-agents` 推荐的 3.14/3.12 不同解释器）。裁定：`provision_voice_host.sh`（Task 14）为 CosyVoice 建独立 venv（`voice_host/.venv-cosyvoice`），真实 TTS 适配器通过 `subprocess` 调用该 venv 的解释器，不在主 worker 进程里直接 import cosyvoice——两套依赖树物理隔离，互不冲突。
11. **追问选择失败的兜底**：`SchemaExtractionFailed`/`LLMProviderUnavailable` 一律按"进入下一题"处理（不重试、不阻塞语音回路），因为默认安全回退本来就是"下一题"，重试对 <800ms 端到端延迟预算不划算。
12. **打断的截断点来自 ASR 适配器上报，不是 worker 主动轮询**：`TranscriptResult` 带一个可选字段 `interrupted_offset_ms`——真实的 FunASR+VAD 流式识别管线能在候选人开口的那一刻上报"打断发生在播报开始后第几毫秒"，这不是测试专用字段，是真实语音管线的自然输出。`turn_cycle.py` 只负责状态记录判定，不做音频层面的检测。
13. **文本作答适配器复用 `ASRAdapter` Protocol**：`TextAnswerAdapter.transcribe_turn()` 阻塞轮询本地队列直到候选人提交文字答案，返回的 `TranscriptResult` 里 `confidence=1.0`、`endpoint_detection_ms=0`、`asr_ms=` 提交耗时——`_ask_one_turn` 不需要为文本模式单独写一套问答循环，只需要按 `answer_mode` 决定要不要调用 `tts.synthesize_and_play`（语音模式跳过 TTS 就是文不对题，文本模式跳过 TTS 才是正确行为）与要不要记录 `audio_start_ms`/`audio_end_ms`（`interview_turn` 的 CHECK 约束要求文本 turn 这两列必须为空）。
14. **语音主机侧的场次级事件表**：`voice_host/queue_store.py` 用一张 `voice_host_event`（`session_id`/`event_type` CHECK 枚举/`detail`/`created_at`）记录"切换文本作答""网络质量提示"两类事件，不像 `.51` 侧那样为每类事件单独建表——语音主机侧数据量小（一个场次几十行），没有 `.51` 侧"增长速率不同不能合并"的顾虑。
15. **重听（候选人请求重听）不产生新 turn、不计入追问次数**：`TranscriptResult.replay_requested` 复用与打断同一份 ASR/VAD 上报机制（真实管线通过关键词识别或候选人端按钮触发）；`_ask_one_turn` 在同一次 turn 尝试内原地重播同一题，重播次数设防御性上限 `MAX_REPLAY_ATTEMPTS=3`（spec 未给出上限，超过后直接采纳当次转写结果，不无限阻塞该轮问答）。这条覆盖 live-voice-interview-session spec「打断处理」Requirement 下的 Scenario「候选人请求重听」，Task 11 交付。

---

### Task 1: `SessionBundle` 下发快照 schema

**Files:**
- Create: `app/schemas/session_bundle.py`
- Test: `tests/test_session_bundle_schema.py`

**Interfaces:**
- Produces：`SessionBundleQuestion(question_id, seq, text, follow_ups)`、`SessionBundle(session_id, prep_curve, follow_up_limit, questions)`，`model_config = ConfigDict(extra="forbid")`。后续 Task 4（客户端序列化）、Task 5（`compute_session_bundle` 构造）、Task 9（语音主机反序列化）直接用这两个类。

- [ ] **Step 1: 写反射测试与构造测试**

创建 `tests/test_session_bundle_schema.py`：

```python
"""app/schemas/session_bundle.py 的结构性测试：确保下发快照 schema 在字段
层面就不可能携带简历/候选人身份/评分字段（live-voice-interview-session spec
「简历数据不进语音主机」；design D19）。"""
import pytest
from pydantic import ValidationError

from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion

FORBIDDEN_SUBSTRINGS = ("resume", "candidate", "name", "phone", "dimension", "difficulty", "rubric")


def _all_field_names() -> set[str]:
    names = set(SessionBundle.model_fields.keys())
    names |= set(SessionBundleQuestion.model_fields.keys())
    return names


def test_no_forbidden_field_names_anywhere_in_schema():
    for field_name in _all_field_names():
        for forbidden in FORBIDDEN_SUBSTRINGS:
            assert forbidden not in field_name.lower(), (
                f"字段 {field_name!r} 命中禁止子串 {forbidden!r}"
            )


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        SessionBundle(
            session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
            questions=[{"question_id": "q1", "seq": 1, "text": "讲讲你的项目"}],
            candidate_name="张三",
        )


def test_valid_bundle_round_trips_through_json():
    bundle = SessionBundle(
        session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
        questions=[
            SessionBundleQuestion(question_id="q1", seq=1, text="讲讲你的项目", follow_ups=["能展开讲讲分层设计吗"]),
        ],
    )
    restored = SessionBundle.model_validate_json(bundle.model_dump_json())
    assert restored == bundle


def test_questions_must_not_be_empty():
    with pytest.raises(ValidationError):
        SessionBundle(session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2, questions=[])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_session_bundle_schema.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.schemas.session_bundle'`）

- [ ] **Step 3: 实现 schema**

创建 `app/schemas/session_bundle.py`：

```python
"""语音主机下发快照 schema（voice-structured-interview U4 tasks 5.3，design
D19）。

live-voice-interview-session spec「简历数据不进语音主机」：语音主机只接收
题目文本／题序／预埋追问／追问次数上限／场次标识；MUST NOT 接收简历内容、
候选人姓名、联系方式或评分数据。`model_config = ConfigDict(extra="forbid")`
与 app/schemas/interview_ai_input.py 同一手法——调用方传错键在构造对象那
一刻直接失败。字段范围裁定见本计划「设计决策 4」：额外排除
dimension/difficulty/rubric_json（评分相关），比 D19 字面要求更严格。

本文件是 `.51` 与语音主机共享的契约唯一源码。部署时 `sync-to-voice-host.sh`
（Task 14）把这个文件原样拷贝进语音主机部署目录，语音主机侧
`voice_host/api.py` 用同一个类解析下发的 JSON——两侧永远读同一份源码，不会
出现字段定义漂移（设计决策 2）。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SessionBundleQuestion(BaseModel):
    """冻结题目快照里的一题，语音主机只拿到播报与追问选择需要的字段。"""
    model_config = ConfigDict(extra="forbid")

    question_id: str
    seq: int = Field(ge=1)
    text: str = Field(min_length=1)
    follow_ups: list[str] = Field(default_factory=list)


class SessionBundle(BaseModel):
    """POST /sessions 的请求体。`app/live_voice/client.py::VoiceHostClient.
    create_session`（Task 4）序列化这个模型；`voice_host/api.py`（Task 9）
    反序列化同一个模型的拷贝。"""
    model_config = ConfigDict(extra="forbid")

    session_id: str
    prep_curve: str
    follow_up_limit: int = Field(ge=0)
    questions: list[SessionBundleQuestion] = Field(min_length=1)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_session_bundle_schema.py -v`
Expected: PASS（4 项）

- [ ] **Step 5: Commit**

```bash
git add app/schemas/session_bundle.py tests/test_session_bundle_schema.py
git commit -m "feat(voice-interview): U4 SessionBundle 下发快照 schema"
```

---

### Task 2: 两机接口 HMAC 签名与防重放

**Files:**
- Create: `app/live_voice/__init__.py`
- Create: `app/live_voice/signing.py`
- Test: `tests/test_live_voice_signing.py`

**Interfaces:**
- Produces：`HEADER_TIMESTAMP`、`HEADER_SIGNATURE`、`TIMESTAMP_SKEW_SECONDS=60`、`canonical_string(*, method, path, timestamp, body) -> str`、`sign(*, secret, method, path, timestamp, body) -> str`、`verify(*, secret, method, path, timestamp, body, signature, now=None) -> None`（不匹配抛 `SignatureInvalidError`）。Task 4（客户端签名）、Task 9（服务端验签）都依赖这些确切名字。

- [ ] **Step 1: 写签名/验签测试**

创建 `tests/test_live_voice_signing.py`：

```python
"""app/live_voice/signing.py：两机接口的 HMAC 签名与 ±60s 防重放窗口
（voice-structured-interview U4 tasks 5.2，本计划「设计决策 3」）。"""
import pytest

from app.live_voice.signing import (
    TIMESTAMP_SKEW_SECONDS,
    SignatureInvalidError,
    sign,
    verify,
)

SECRET = "test-shared-secret"


def test_valid_signature_verifies():
    ts = "1758240000.0"
    body = b'{"session_id": "s1"}'
    signature = sign(secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=body)
    verify(
        secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=body,
        signature=signature, now=1758240000.0,
    )


def test_tampered_body_fails():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=b"original")
    with pytest.raises(SignatureInvalidError):
        verify(
            secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=b"tampered",
            signature=signature, now=1758240000.0,
        )


def test_wrong_secret_fails():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="POST", path="/sessions", timestamp=ts, body=b"body")
    with pytest.raises(SignatureInvalidError):
        verify(
            secret="another-secret", method="POST", path="/sessions", timestamp=ts, body=b"body",
            signature=signature, now=1758240000.0,
        )


def test_stale_timestamp_outside_skew_window_fails():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"")
    stale_now = 1758240000.0 + TIMESTAMP_SKEW_SECONDS + 1
    with pytest.raises(SignatureInvalidError):
        verify(
            secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"",
            signature=signature, now=stale_now,
        )


def test_timestamp_at_exact_skew_boundary_verifies():
    ts = "1758240000.0"
    signature = sign(secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"")
    boundary_now = 1758240000.0 + TIMESTAMP_SKEW_SECONDS
    verify(
        secret=SECRET, method="GET", path="/sessions/s1/events", timestamp=ts, body=b"",
        signature=signature, now=boundary_now,
    )


def test_malformed_timestamp_fails():
    with pytest.raises(SignatureInvalidError):
        verify(
            secret=SECRET, method="GET", path="/sessions/s1/events", timestamp="not-a-number",
            body=b"", signature="whatever", now=1758240000.0,
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_live_voice_signing.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.live_voice'`）

- [ ] **Step 3: 实现签名模块**

创建 `app/live_voice/__init__.py`（空文件）。

创建 `app/live_voice/signing.py`：

```python
"""两机接口签名（voice-structured-interview U4 tasks 5.2，design D12；本计划
「设计决策 3」）。

`.51` 出站请求与语音主机的每一次交互都用预共享密钥 + HMAC-SHA256 签名 +
时间戳防重放（±60s）。本文件是签名算法的唯一源码——`.51` 侧
`app/live_voice/client.py`（Task 4）签名请求，语音主机侧 `voice_host/api.py`
（Task 9）验证请求，两边用的是同一份被 `sync-to-voice-host.sh`（Task 14）
物理拷贝过去的代码，不是"两边各自实现一遍"，不会出现算法漂移。
"""
from __future__ import annotations

import hashlib
import hmac
import time

HEADER_TIMESTAMP = "X-ZP-Timestamp"
HEADER_SIGNATURE = "X-ZP-Signature"
TIMESTAMP_SKEW_SECONDS = 60


def canonical_string(*, method: str, path: str, timestamp: str, body: bytes) -> str:
    body_sha256 = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{body_sha256}"


def sign(*, secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    message = canonical_string(method=method, path=path, timestamp=timestamp, body=body)
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


class SignatureInvalidError(Exception):
    """签名不匹配、时间戳格式非法、或时间戳超出 ±60s 窗口（防重放）。三种
    原因统一成一个异常类型——调用方（Task 9 的 FastAPI 依赖）对三者的处理
    完全一样：拒绝请求、不泄露具体是哪种原因（避免帮攻击者调试签名）。"""


def verify(
    *, secret: str, method: str, path: str, timestamp: str, body: bytes, signature: str,
    now: float | None = None,
) -> None:
    now_value = time.time() if now is None else now
    try:
        ts_value = float(timestamp)
    except ValueError as exc:
        raise SignatureInvalidError(f"时间戳格式非法: {timestamp!r}") from exc

    if abs(now_value - ts_value) > TIMESTAMP_SKEW_SECONDS:
        raise SignatureInvalidError(f"时间戳超出 ±{TIMESTAMP_SKEW_SECONDS}s 窗口: {timestamp!r}")

    expected = sign(secret=secret, method=method, path=path, timestamp=timestamp, body=body)
    if not hmac.compare_digest(expected, signature):
        raise SignatureInvalidError("签名不匹配")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_live_voice_signing.py -v`
Expected: PASS（6 项）

- [ ] **Step 5: Commit**

```bash
git add app/live_voice/__init__.py app/live_voice/signing.py tests/test_live_voice_signing.py
git commit -m "feat(voice-interview): U4 两机接口 HMAC 签名与防重放"
```

---

### Task 3: `follow_up_selector.py` L3 纯函数（追问选择）

**Files:**
- Create: `app/schemas/follow_up_result.py`
- Create: `app/agents/follow_up_selector.py`
- Test: `tests/test_follow_up_selector_agent.py`

**Interfaces:**
- Consumes：`app.schemas.interview_ai_input.FollowUpInput`（U1 已交付，字段 `question_text`/`follow_ups`/`transcript`）、`app.llm.gateway.LLMGateway`。
- Produces：`FollowUpChoice(is_follow_up, follow_up_index, out_of_range, run_id, response_model)`、`select(gateway, follow_up_input, *, audit_context=None) -> FollowUpChoice`、`FOLLOW_UP_PROMPT_VERSION`。Task 11（`voice_host/worker.py`）直接 import 这两个名字（经 `sync-to-voice-host.sh` 拷贝的副本）。

- [ ] **Step 1: 写输出 schema 测试**

创建 `tests/test_follow_up_result_schema.py`：

```python
"""app/schemas/follow_up_result.py：追问选择 LLM 输出 schema。"""
import pytest
from pydantic import ValidationError

from app.schemas.follow_up_result import FollowUpChoiceOut


def test_next_question_decision_does_not_require_index():
    parsed = FollowUpChoiceOut(decision="next_question")
    assert parsed.follow_up_index is None


def test_follow_up_decision_requires_index():
    with pytest.raises(ValidationError):
        FollowUpChoiceOut(decision="follow_up")


def test_follow_up_decision_with_index_is_valid():
    parsed = FollowUpChoiceOut(decision="follow_up", follow_up_index=1)
    assert parsed.follow_up_index == 1


def test_unknown_decision_literal_is_rejected():
    with pytest.raises(ValidationError):
        FollowUpChoiceOut(decision="something_else")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_follow_up_result_schema.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现输出 schema**

创建 `app/schemas/follow_up_result.py`：

```python
"""追问选择（app/agents/follow_up_selector.py::select，U4 tasks 5.4，design
D17）的 LLM 输出 schema。

live-voice-interview-session spec「追问只在预埋集合内选择」：输出只能是
"预埋追问集合中的某一条"或"进入下一题"，不允许模型自由生成新文本——这条
约束体现在 schema 本身只携带一个索引，不携带任何自由文本字段。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FollowUpChoiceOut(BaseModel):
    decision: Literal["follow_up", "next_question"]
    follow_up_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _follow_up_index_required_when_following_up(self):
        if self.decision == "follow_up" and self.follow_up_index is None:
            raise ValueError("decision=follow_up 时 follow_up_index 不能为空")
        return self
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_follow_up_result_schema.py -v`
Expected: PASS（4 项）

- [ ] **Step 5: 写 `select()` 的行为测试**

创建 `tests/test_follow_up_selector_agent.py`（`ScriptedClient`/`_gateway` 与 `tests/test_interview_scoring_agent.py` 同一形状，本文件独立不依赖它）：

```python
"""app/agents/follow_up_selector.py 的纯函数测试：不接触数据库，LLM 用脚本化
假客户端。"""
import json

import pytest

from app.agents.follow_up_selector import FOLLOW_UP_PROMPT_VERSION, select
from app.llm.gateway import LLMGateway, SchemaExtractionFailed
from app.schemas.interview_ai_input import FollowUpInput


class ScriptedClient:
    def __init__(self, bodies: list[str]):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 10

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


def _gateway(bodies):
    return LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient(bodies),
    )


def _follow_up_input():
    return FollowUpInput(
        question_text="讲讲你的 AUTOSAR 项目经验",
        follow_ups=["具体分层设计是怎么做的", "遇到过哪些踩坑经历"],
        transcript="做过三年，用的是分层架构",
    )


def test_in_range_follow_up_index_is_selected():
    body = json.dumps({"decision": "follow_up", "follow_up_index": 1}, ensure_ascii=False)
    choice = select(_gateway([body]), _follow_up_input())
    assert choice.is_follow_up is True
    assert choice.follow_up_index == 1
    assert choice.out_of_range is False


def test_next_question_decision_is_not_a_follow_up():
    body = json.dumps({"decision": "next_question"}, ensure_ascii=False)
    choice = select(_gateway([body]), _follow_up_input())
    assert choice.is_follow_up is False
    assert choice.follow_up_index is None
    assert choice.out_of_range is False


def test_out_of_range_index_is_normalized_to_next_question():
    body = json.dumps({"decision": "follow_up", "follow_up_index": 99}, ensure_ascii=False)
    choice = select(_gateway([body]), _follow_up_input())
    assert choice.is_follow_up is False
    assert choice.out_of_range is True


def test_prompt_version_constant_is_recorded():
    assert FOLLOW_UP_PROMPT_VERSION == "interview-follow-up-v1"


def test_schema_extraction_failure_propagates_to_caller():
    # 两次都返回非法 JSON，网关重试耗尽后抛 SchemaExtractionFailed——不在
    # select() 内部吞掉，由调用方（voice_host/worker.py，Task 11）决定兜底。
    with pytest.raises(SchemaExtractionFailed):
        select(_gateway(["not json", "still not json", "nope"]), _follow_up_input())
```

- [ ] **Step 6: 运行测试确认失败**

Run: `pytest tests/test_follow_up_selector_agent.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.agents.follow_up_selector'`）

- [ ] **Step 7: 实现 `select()`**

创建 `app/agents/follow_up_selector.py`：

```python
"""追问选择 L3 Agent（design D17，U4 tasks 5.4）。

无副作用纯函数：只调用 LLM 网关做一次结构化输出，不写库、不访问简历/身份
字段（输入类型 `FollowUpInput` 结构上不含这些字段，U1 已交付，见
app/schemas/interview_ai_input.py）。追问选择只在预埋集合内选：模型返回
`follow_up_index` 越界（不在 `0..len(follow_ups)-1`）按"进入下一题"处理并
标记 `out_of_range`——语音主机上的 `voice_host/worker.py`（经
sync-to-voice-host.sh 拷贝的这份文件）据此调用。本文件不知道自己跑在哪台
机器上，不 import 任何 livekit/funasr/cosyvoice，也不 import app.storage。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.llm.gateway import LLMGateway
from app.schemas.follow_up_result import FollowUpChoiceOut
from app.schemas.interview_ai_input import FollowUpInput

FOLLOW_UP_PROMPT_VERSION = "interview-follow-up-v1"

_SYSTEM_PROMPT_TEMPLATE = (
    "你是资深技术面试官助手。当前题目：{question_text}\n"
    "预埋追问集合（编号从 0 开始）：\n{follow_ups}\n"
    "候选人本轮转写：{transcript}\n"
    "判断候选人的回答是否遗漏了 rubric 关键点、需要追问。如果需要，从预埋"
    "追问集合中选出编号最贴切的一条（decision=follow_up，给出"
    "follow_up_index）；如果回答已经充分，或没有一条预埋追问贴切，返回"
    "decision=next_question。⛔ 不得输出预埋集合之外的新问题文本。输出"
    "JSON，字段：decision、follow_up_index（decision=next_question 时可省略）。"
)


@dataclass(frozen=True)
class FollowUpChoice:
    is_follow_up: bool
    follow_up_index: int | None
    out_of_range: bool
    run_id: str
    response_model: str | None


def _build_system_prompt(follow_up_input: FollowUpInput) -> str:
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(follow_up_input.follow_ups))
    return _SYSTEM_PROMPT_TEMPLATE.format(
        question_text=follow_up_input.question_text,
        follow_ups=numbered,
        transcript=follow_up_input.transcript,
    )


def select(
    gateway: LLMGateway, follow_up_input: FollowUpInput, *, audit_context: dict | None = None,
) -> FollowUpChoice:
    """越界／`next_question` 都归一为"进入下一题"（spec Scenario「模型输出不
    在集合内」）；只有 `decision=follow_up` 且 index 落在
    `0..len(follow_ups)-1` 内才是真正的追问。⛔ 不重试——追问选择的默认安全
    回退本来就是"下一题"，重试对时延预算（design D18 <800ms 端到端）不划算。
    `SchemaExtractionFailed`/`LLMProviderUnavailable` 不在这里捕获，原样上抛，
    由调用方决定兜底（本计划「设计决策 11」，见 Task 11）。"""
    system_prompt = _build_system_prompt(follow_up_input)
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=system_prompt,
        user_prompt=follow_up_input.model_dump_json(),
        schema=FollowUpChoiceOut,
        prompt_version=FOLLOW_UP_PROMPT_VERSION,
        audit_context=audit_context,
    )

    if parsed.decision != "follow_up":
        return FollowUpChoice(
            is_follow_up=False, follow_up_index=None, out_of_range=False,
            run_id=meta.run_id, response_model=meta.response_model,
        )

    index = parsed.follow_up_index
    if index is None or not (0 <= index < len(follow_up_input.follow_ups)):
        return FollowUpChoice(
            is_follow_up=False, follow_up_index=None, out_of_range=True,
            run_id=meta.run_id, response_model=meta.response_model,
        )

    return FollowUpChoice(
        is_follow_up=True, follow_up_index=index, out_of_range=False,
        run_id=meta.run_id, response_model=meta.response_model,
    )
```

- [ ] **Step 8: 运行测试确认通过**

Run: `pytest tests/test_follow_up_selector_agent.py tests/test_follow_up_result_schema.py -v`
Expected: PASS（9 项）

- [ ] **Step 9: Commit**

```bash
git add app/schemas/follow_up_result.py app/agents/follow_up_selector.py tests/test_follow_up_result_schema.py tests/test_follow_up_selector_agent.py
git commit -m "feat(voice-interview): U4 追问选择 L3 纯函数（design D17）"
```

---

### Task 4: `.51` 出站客户端 `VoiceHostClient` + `LiveTurnEvent` schema

**Files:**
- Create: `app/schemas/live_turn_event.py`
- Create: `app/live_voice/client.py`
- Modify: `app/config.py`（在 `resume_storage_dir` 字段之后追加三个新配置项，见 Step 5）
- Test: `tests/test_live_turn_event_schema.py`
- Test: `tests/test_live_voice_client.py`

**Interfaces:**
- Produces：`LiveTurnEvent`、`LiveEventsPollResponse`（`app/schemas/live_turn_event.py`）；`VoiceHostClient(base_url, shared_secret, *, timeout=5.0, http_client=None)`，方法 `create_session(bundle: SessionBundle) -> dict`、`poll_events(session_id: str, *, since: int) -> LiveEventsPollResponse`、`fetch_recording(session_id: str) -> tuple[bytes, str]`（返回内容与其 `X-Recording-SHA256` header 值）、`notify_delete_artifacts(session_id: str) -> None`；异常 `VoiceHostRequestFailed(status_code, body)`。Task 5/6 直接用这个类。

- [ ] **Step 1: 写 `LiveTurnEvent`/`LiveEventsPollResponse` 测试**

创建 `tests/test_live_turn_event_schema.py`：

```python
"""app/schemas/live_turn_event.py：GET /sessions/{id}/events 的响应体 schema。"""
import pytest
from pydantic import ValidationError

from app.schemas.live_turn_event import LiveEventsPollResponse, LiveTurnEvent


def test_minimal_voice_event_parses():
    event = LiveTurnEvent(
        seq=1, question_id="q1", question_text="讲讲你的项目", answer_text="做过三年",
        answer_mode="voice", audio_start_ms=0, audio_end_ms=4200, asr_confidence=0.92,
        latency={"endpoint_detection_ms": 300.0, "asr_ms": 150.0, "tts_first_frame_ms": 80.0, "end_to_end_ms": 530.0},
    )
    assert event.follow_up_of_seq is None
    assert event.interrupted_at_ms is None


def test_unknown_answer_mode_is_rejected():
    with pytest.raises(ValidationError):
        LiveTurnEvent(
            seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="video",
        )


def test_poll_response_requires_known_session_status():
    with pytest.raises(ValidationError):
        LiveEventsPollResponse(events=[], session_status="abandoned")


def test_poll_response_round_trips():
    payload = LiveEventsPollResponse(
        events=[
            LiveTurnEvent(seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="text"),
        ],
        session_status="in_progress",
    )
    restored = LiveEventsPollResponse.model_validate_json(payload.model_dump_json())
    assert restored == payload
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_live_turn_event_schema.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `LiveTurnEvent`/`LiveEventsPollResponse`**

创建 `app/schemas/live_turn_event.py`：

```python
"""语音主机上报的 turn 事件 schema（voice-structured-interview U4 tasks 5.2/
5.9）。`GET /sessions/{id}/events?since=` 的响应体。

与 app/schemas/session_bundle.py 同一部署形态（设计决策 2）：`.51` 侧
`app/live_voice/client.py`（Task 4）解析响应体，语音主机侧 `voice_host/api.py`
（Task 9）构造响应体，两侧 import 的是同一份被拷贝的源码。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LiveTurnEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    question_id: str
    question_text: str
    answer_text: str
    answer_mode: Literal["voice", "text"]
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None
    asr_confidence: float | None = None
    follow_up_of_seq: int | None = None
    interrupted_at_ms: int | None = None
    latency: dict = Field(default_factory=dict)


class LiveEventsPollResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[LiveTurnEvent]
    session_status: Literal["in_progress", "completed", "interrupted"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_live_turn_event_schema.py -v`
Expected: PASS（4 项）

- [ ] **Step 5: 在 `app/config.py` 追加语音主机相关配置**

在 `app/config.py` 第 81-83 行（`resume_storage_dir` 字段块）之后插入：

```python

    # 两机接口（U4 tasks 5.2，design D12）。语音主机的 base_url 与预共享密钥
    # 都是生产凭据，只在各自机器的 .env 里维护，不进仓库（与 llm_api_key
    # 同一约定）。
    voice_host_base_url: str = ""
    voice_host_shared_secret: str = ""
    voice_host_request_timeout_seconds: float = 5.0

    # 录音回传落盘目录（U4 tasks 5.9）。相对路径按进程工作目录解析，与
    # db_path/resume_storage_dir 同一约定。
    interview_recording_dir: str = "data/interview_recordings"
```

- [ ] **Step 6: 写 `VoiceHostClient` 测试**

创建 `tests/test_live_voice_client.py`（用 `httpx.MockTransport` 模拟语音主机响应，不起真实服务器）：

```python
"""app/live_voice/client.py：`.51` 出站客户端。用 httpx.MockTransport 模拟
语音主机响应——不依赖真实网络或真实语音主机进程。"""
import json

import httpx
import pytest

from app.live_voice import signing
from app.live_voice.client import VoiceHostClient, VoiceHostRequestFailed
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion

SECRET = "test-secret"


def _bundle():
    return SessionBundle(
        session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
        questions=[SessionBundleQuestion(question_id="q1", seq=1, text="讲讲你的项目", follow_ups=[])],
    )


def _client_with_handler(handler):
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://voice-host.invalid")
    return VoiceHostClient(base_url="https://voice-host.invalid", shared_secret=SECRET, http_client=http_client)


def _assert_signed(request: httpx.Request):
    ts = request.headers[signing.HEADER_TIMESTAMP]
    sig = request.headers[signing.HEADER_SIGNATURE]
    signing.verify(
        secret=SECRET, method=request.method, path=request.url.path, timestamp=ts,
        body=request.content, signature=sig,
    )


def test_create_session_sends_signed_request_and_parses_ack():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.method == "POST"
        assert request.url.path == "/sessions"
        body = json.loads(request.content)
        assert body["session_id"] == "s1"
        return httpx.Response(200, json={"accepted": True})

    client = _client_with_handler(handler)
    ack = client.create_session(_bundle())
    assert ack == {"accepted": True}


def test_poll_events_parses_response_into_typed_model():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.url.path == "/sessions/s1/events"
        assert request.url.params["since"] == "0"
        return httpx.Response(200, json={"events": [], "session_status": "in_progress"})

    client = _client_with_handler(handler)
    poll = client.poll_events("s1", since=0)
    assert poll.session_status == "in_progress"
    assert poll.events == []


def test_fetch_recording_returns_content_and_sha_header():
    content = b"fake-recording-bytes"
    import hashlib
    sha = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.url.path == "/sessions/s1/recording"
        return httpx.Response(200, content=content, headers={"X-Recording-SHA256": sha})

    client = _client_with_handler(handler)
    got_content, got_sha = client.fetch_recording("s1")
    assert got_content == content
    assert got_sha == sha


def test_notify_delete_artifacts_sends_delete():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed(request)
        assert request.method == "DELETE"
        assert request.url.path == "/sessions/s1/artifacts"
        return httpx.Response(204)

    client = _client_with_handler(handler)
    client.notify_delete_artifacts("s1")


def test_non_2xx_response_raises_with_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _client_with_handler(handler)
    with pytest.raises(VoiceHostRequestFailed) as exc_info:
        client.create_session(_bundle())
    assert exc_info.value.status_code == 500
```

- [ ] **Step 7: 运行测试确认失败**

Run: `pytest tests/test_live_voice_client.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.live_voice.client'`）

- [ ] **Step 8: 实现 `VoiceHostClient`**

创建 `app/live_voice/client.py`：

```python
"""`.51` 出站客户端（voice-structured-interview U4 tasks 5.2，design D12：
"`.51` 与语音主机之间只有两类交互，全部由 `.51` 主动发起"）。每次请求都用
app/live_voice/signing.py 签名——两侧共享同一份签名源码（设计决策 2/3）。

`http_client` 可注入（与 app/llm/gateway.py::LLMGateway 的 `client` 参数同
一手法），测试用 httpx.MockTransport，不起真实服务器；生产环境不传则用
真实 httpx.Client。
"""
from __future__ import annotations

import time

import httpx

from app.live_voice import signing
from app.schemas.live_turn_event import LiveEventsPollResponse
from app.schemas.session_bundle import SessionBundle


class VoiceHostRequestFailed(Exception):
    def __init__(self, *, status_code: int, body: str):
        super().__init__(f"语音主机请求失败: status={status_code} body={body[:500]!r}")
        self.status_code = status_code
        self.body = body


class VoiceHostClient:
    def __init__(
        self, *, base_url: str, shared_secret: str, timeout: float = 5.0,
        http_client: httpx.Client | None = None,
    ):
        self._secret = shared_secret
        self._client = http_client or httpx.Client(base_url=base_url, timeout=timeout)

    def _signed_headers(self, *, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = str(time.time())
        signature = signing.sign(
            secret=self._secret, method=method, path=path, timestamp=timestamp, body=body,
        )
        return {signing.HEADER_TIMESTAMP: timestamp, signing.HEADER_SIGNATURE: signature}

    def _request(self, method: str, path: str, *, content: bytes = b"", params: dict | None = None) -> httpx.Response:
        headers = self._signed_headers(method=method, path=path, body=content)
        response = self._client.request(method, path, content=content, headers=headers, params=params)
        if response.status_code >= 300:
            raise VoiceHostRequestFailed(status_code=response.status_code, body=response.text)
        return response

    def create_session(self, bundle: SessionBundle) -> dict:
        body = bundle.model_dump_json().encode("utf-8")
        response = self._request("POST", "/sessions", content=body)
        return response.json()

    def poll_events(self, session_id: str, *, since: int) -> LiveEventsPollResponse:
        response = self._request(
            "GET", f"/sessions/{session_id}/events", params={"since": str(since)},
        )
        return LiveEventsPollResponse.model_validate_json(response.content)

    def fetch_recording(self, session_id: str) -> tuple[bytes, str]:
        response = self._request("GET", f"/sessions/{session_id}/recording")
        return response.content, response.headers["X-Recording-SHA256"]

    def notify_delete_artifacts(self, session_id: str) -> None:
        self._request("DELETE", f"/sessions/{session_id}/artifacts")
```

- [ ] **Step 9: 运行测试确认通过**

Run: `pytest tests/test_live_voice_client.py -v`
Expected: PASS（5 项）

- [ ] **Step 10: Commit**

```bash
git add app/schemas/live_turn_event.py app/live_voice/client.py app/config.py tests/test_live_turn_event_schema.py tests/test_live_voice_client.py
git commit -m "feat(voice-interview): U4 .51 出站客户端 VoiceHostClient"
```

---

### Task 5: DB schema 新增 + `.51` 侧 live 子图四个 `effect_*` 节点

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 字符串追加 `interview_live_event` 建表块；`_ADDED_COLUMNS` 追加 `job_prep_config.follow_up_limit`/`interview_session.recording_sha256`）
- Create: `app/graph/live_session_nodes.py`
- Test: `tests/test_db_m3_schema.py`（追加）
- Test: `tests/test_live_session_nodes.py`

**Interfaces:**
- Produces：新表 `interview_live_event(id, session_id, event_type, detail, at)`；新列 `job_prep_config.follow_up_limit INTEGER`、`interview_session.recording_sha256 TEXT`。`compute_session_bundle(conn, *, session_id) -> SessionBundle`、`effect_open_session`/`effect_persist_turn`/`effect_close_session`/`effect_fetch_recording`（均 `@idempotent_effect`）、`run_live_session_sync(conn, *, session_id, client, recording_dir) -> str`。Task 6 直接调用 `run_live_session_sync`。

- [ ] **Step 1: 在 `app/storage/db.py` 的 `SCHEMA` 字符串里追加新表**

在 `app/storage/db.py` 第 897-898 行（`interview_scorecard_tip` 的索引语句之后、闭合 `"""` 之前）插入：

```sql

-- 场次级 live 段事件留痕（voice-structured-interview U4 tasks 5.9，design
-- D20）。⛔ 不与 interview_invite_event 合并：那张表的 event_type CHECK 枚举
-- 已经固定（'issued'/'reissued'/...），SQLite 的 CHECK 约束不能靠
-- ALTER TABLE ADD COLUMN 追加取值，往里塞新枚举值需要整表重建，风险不值得
-- ——新开一张表是更便宜的选择（与 interview_scorecard 的既有先例同一手法）。
CREATE TABLE IF NOT EXISTS interview_live_event (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES interview_session(id),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'opened', 'turn_persisted', 'closed', 'recording_fetched'
    )),
    detail TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interview_live_event_session
    ON interview_live_event (session_id);
```

- [ ] **Step 2: 在 `_ADDED_COLUMNS` 元组末尾追加两条新列**

在 `app/storage/db.py` 的 `_ADDED_COLUMNS` 元组内，`("interview_session", "post_scored_at", "TEXT"),` 一行之后、闭合 `)` 之前追加：

```python
    # U4 tasks 5.4：追问次数上限，岗位级配置，默认 2（tasks.md 字面值）。
    # job_prep_config 在 U2 建表，走加列迁移（与 low_confidence_threshold
    # 同一先例）。
    ("job_prep_config", "follow_up_limit", "INTEGER NOT NULL DEFAULT 2"),
    # U4 tasks 5.9：effect_fetch_recording 校验并记录回传录音的 sha256，供
    # 审计与重复拉取判重使用。
    ("interview_session", "recording_sha256", "TEXT"),
```

- [ ] **Step 3: 写新表与新列的结构测试**

在 `tests/test_db_m3_schema.py` 末尾追加：

```python


# ── interview_live_event（U4 tasks 5.9）─────────────────────────────


def test_interview_live_event_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "interview_live_event")
    assert _columns(conn, "interview_live_event") == {"id", "session_id", "event_type", "detail", "at"}


def test_interview_live_event_rejects_unknown_event_type(conn):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', 't')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', 'job-1', 'synthetic', 'a.pdf', 'hash', 'tester')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'cand-1', 'job-1', 'resume-1', 'initial')"
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES ('sess-1', 'app-1', 1, '2099-01-01', 'v1', 'internal_sim', 'in_progress')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_live_event (id, session_id, event_type) VALUES ('e1', 'sess-1', 'bogus')"
        )


def test_job_prep_config_follow_up_limit_column_added(conn):
    assert "follow_up_limit" in _columns(conn, "job_prep_config")


def test_interview_session_recording_sha256_column_added(conn):
    assert "recording_sha256" in _columns(conn, "interview_session")
```

- [ ] **Step 4: 运行 schema 测试确认通过**

Run: `pytest tests/test_db_m3_schema.py -v`
Expected: PASS（含新增 4 项）

- [ ] **Step 5: 写 `compute_session_bundle` 测试**

创建 `tests/test_live_session_nodes.py`：

```python
"""app/graph/live_session_nodes.py：U4 live 子图 L4 编排层。数据库用真实
SQLite（tmp_path），两机接口用手写 Fake 客户端（不依赖 httpx.MockTransport，
本文件只关心 live_session_nodes 自己怎么调用客户端，不重复 Task 4 已经测过
的签名/序列化细节）。"""
import json

import pytest

from app.graph.live_session_nodes import (
    compute_session_bundle,
    effect_close_session,
    effect_fetch_recording,
    effect_open_session,
    effect_persist_turn,
    run_live_session_sync,
)
from app.schemas.live_turn_event import LiveEventsPollResponse, LiveTurnEvent
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_ready_session(conn, *, job_id="job-1", application_id="app-1", session_id="sess-1"):
    conn.execute("INSERT INTO job (id, title) VALUES (?, '嵌入式软件工程师')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')", (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')", (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-prep', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', ?, 1, 1, 'run-prep', 'frozen')", (application_id,),
    )
    conn.execute(
        "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
        "rubric_json, follow_ups_json, rationale) VALUES "
        "('q1', 'snap-1', 1, 'AUTOSAR CP', 'easy', '讲讲你的项目', '{}', '[\"能展开讲讲分层设计吗\"]', 'r')"
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, phone_verified_at, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES (?, ?, 1, datetime('now'), '2099-01-01', 'v1', 'internal_sim', 'in_progress')",
        (session_id, application_id),
    )
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) VALUES "
        "(?, 'ai_interview', 'accepted', 'v1'), (?, 'identity_check', 'accepted', 'v1')",
        (session_id, session_id),
    )
    conn.commit()


def test_compute_session_bundle_excludes_scoring_fields(conn):
    _seed_ready_session(conn)
    bundle = compute_session_bundle(conn, session_id="sess-1")
    assert bundle.session_id == "sess-1"
    assert bundle.follow_up_limit == 2  # 默认值
    assert bundle.questions[0].question_id == "q1"
    assert bundle.questions[0].follow_ups == ["能展开讲讲分层设计吗"]
    assert "dimension" not in bundle.model_dump()["questions"][0]


class FakeVoiceHostClient:
    def __init__(self, *, poll_responses, recording_content=b"rec", recording_sha=None):
        import hashlib
        self.poll_responses = list(poll_responses)
        self.created_sessions = []
        self.deleted_artifacts = []
        self.recording_content = recording_content
        self.recording_sha = recording_sha or hashlib.sha256(recording_content).hexdigest()

    def create_session(self, bundle):
        self.created_sessions.append(bundle.session_id)
        return {"accepted": True}

    def poll_events(self, session_id, *, since):
        return self.poll_responses.pop(0)

    def fetch_recording(self, session_id):
        return self.recording_content, self.recording_sha

    def notify_delete_artifacts(self, session_id):
        self.deleted_artifacts.append(session_id)


def test_effect_open_session_is_idempotent_and_calls_client_once(conn):
    _seed_ready_session(conn)
    bundle = compute_session_bundle(conn, session_id="sess-1")
    client = FakeVoiceHostClient(poll_responses=[])

    effect_open_session(conn, thread_id="sess-1", business_key="1", session_id="sess-1", bundle=bundle, client=client)
    effect_open_session(conn, thread_id="sess-1", business_key="1", session_id="sess-1", bundle=bundle, client=client)

    assert client.created_sessions == ["sess-1"]  # 第二次调用被幂等短路
    count = conn.execute(
        "SELECT COUNT(*) FROM interview_live_event WHERE session_id = 'sess-1' AND event_type = 'opened'"
    ).fetchone()[0]
    assert count == 1


def test_effect_persist_turn_resolves_follow_up_of_seq_to_turn_id(conn):
    _seed_ready_session(conn)
    first = LiveTurnEvent(
        seq=1, question_id="q1", question_text="讲讲你的项目", answer_text="做过三年", answer_mode="voice",
        audio_start_ms=0, audio_end_ms=3000, asr_confidence=0.9,
    )
    effect_persist_turn(conn, thread_id="sess-1", business_key="1", session_id="sess-1", event=first)

    follow_up = LiveTurnEvent(
        seq=2, question_id="q1", question_text="能展开讲讲分层设计吗", answer_text="是这样分层的",
        answer_mode="voice", audio_start_ms=3200, audio_end_ms=6000, asr_confidence=0.85, follow_up_of_seq=1,
    )
    effect_persist_turn(conn, thread_id="sess-1", business_key="2", session_id="sess-1", event=follow_up)

    first_turn_id = conn.execute("SELECT id FROM interview_turn WHERE session_id='sess-1' AND seq=1").fetchone()[0]
    follow_up_of = conn.execute("SELECT follow_up_of FROM interview_turn WHERE session_id='sess-1' AND seq=2").fetchone()[0]
    assert follow_up_of == first_turn_id


def test_effect_persist_turn_is_idempotent_on_repeat_seq(conn):
    _seed_ready_session(conn)
    event = LiveTurnEvent(
        seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="text",
    )
    effect_persist_turn(conn, thread_id="sess-1", business_key="1", session_id="sess-1", event=event)
    effect_persist_turn(conn, thread_id="sess-1", business_key="1", session_id="sess-1", event=event)
    count = conn.execute("SELECT COUNT(*) FROM interview_turn WHERE session_id='sess-1' AND seq=1").fetchone()[0]
    assert count == 1


def test_effect_close_session_updates_status(conn):
    _seed_ready_session(conn)
    effect_close_session(conn, thread_id="sess-1", business_key="close", session_id="sess-1", final_status="completed")
    status = conn.execute("SELECT status FROM interview_session WHERE id='sess-1'").fetchone()[0]
    assert status == "completed"


def test_effect_fetch_recording_writes_file_and_notifies_delete(conn, tmp_path):
    _seed_ready_session(conn)
    client = FakeVoiceHostClient(poll_responses=[], recording_content=b"hello-recording")
    effect_fetch_recording(
        conn, thread_id="sess-1", business_key=client.recording_sha, session_id="sess-1",
        content=client.recording_content, recording_sha256=client.recording_sha,
        recording_dir=str(tmp_path / "recordings"), client=client,
    )
    row = conn.execute("SELECT recording_uri, recording_sha256 FROM interview_session WHERE id='sess-1'").fetchone()
    assert row[1] == client.recording_sha
    assert (tmp_path / "recordings" / "sess-1.rec").read_bytes() == b"hello-recording"
    assert client.deleted_artifacts == ["sess-1"]


def test_run_live_session_sync_full_cycle(conn, tmp_path):
    _seed_ready_session(conn)
    poll_first = LiveEventsPollResponse(
        events=[LiveTurnEvent(seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="text")],
        session_status="completed",
    )
    client = FakeVoiceHostClient(poll_responses=[poll_first])

    result = run_live_session_sync(
        conn, session_id="sess-1", client=client, recording_dir=str(tmp_path / "recordings"),
    )

    assert result == "completed"
    assert client.created_sessions == ["sess-1"]
    assert client.deleted_artifacts == ["sess-1"]
    turn_count = conn.execute("SELECT COUNT(*) FROM interview_turn WHERE session_id='sess-1'").fetchone()[0]
    assert turn_count == 1
    status = conn.execute("SELECT status FROM interview_session WHERE id='sess-1'").fetchone()[0]
    assert status == "completed"


def test_run_live_session_sync_skips_when_snapshot_expired(conn):
    _seed_ready_session(conn)
    conn.execute("UPDATE prep_snapshot SET status='expired' WHERE id='snap-1'")
    conn.commit()
    client = FakeVoiceHostClient(poll_responses=[])
    result = run_live_session_sync(conn, session_id="sess-1", client=client, recording_dir="unused")
    assert result == "blocked_snapshot_not_frozen"
    assert client.created_sessions == []
```

- [ ] **Step 6: 运行测试确认失败**

Run: `pytest tests/test_live_session_nodes.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.graph.live_session_nodes'`）

- [ ] **Step 7: 实现 `app/graph/live_session_nodes.py`**

创建 `app/graph/live_session_nodes.py`：

```python
"""U4 live 子图 L4 编排层（voice-structured-interview tasks 5.9，design
D20）。

`compute_session_bundle` 只读查库组装 `SessionBundle`，不写库（工程铁律 2，
与 app/graph/interview_scoring_nodes.py::compute_align 同一先例）。四个
`effect_*` 节点独占写库，全部用 `@idempotent_effect` 装饰，字面幂等键与
design D20 给出的公式一致（`effect_close_session` 的 `business_key` 常量
"close" 是本计划「设计决策 7」的裁定）。`run_live_session_sync` 是普通函数
串联的编排入口（本计划「设计决策 1」），供 scripts/run_interview_live_sync.py
（Task 6）每轮调用。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from pathlib import Path

from app.schemas.live_turn_event import LiveTurnEvent
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


def _load_follow_up_limit(conn: sqlite3.Connection, job_id: str) -> int:
    row = conn.execute(
        "SELECT follow_up_limit FROM job_prep_config WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return 2
    return row[0]


def compute_session_bundle(conn: sqlite3.Connection, *, session_id: str) -> SessionBundle:
    """L4 compute_* 节点：组装下发快照（live-voice-interview-session spec
    「简历数据不进语音主机」；本计划「设计决策 4」）。只读，不写库。"""
    row = conn.execute(
        "SELECT s.application_id, s.prep_snapshot_version, a.job_id, jpc.prep_curve "
        "FROM interview_session s JOIN application a ON a.id = s.application_id "
        "LEFT JOIN job_prep_config jpc ON jpc.job_id = a.job_id "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在: {session_id!r}")
    application_id, snapshot_version, job_id, prep_curve = row
    prep_curve = prep_curve or "easy_to_hard"

    snap_row = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, snapshot_version),
    ).fetchone()
    if snap_row is None:
        raise ValueError(f"prep_snapshot 不存在: application_id={application_id!r} version={snapshot_version!r}")
    snapshot_id = snap_row[0]

    question_rows = conn.execute(
        "SELECT id, seq, text, follow_ups_json FROM prep_question WHERE snapshot_id = ? ORDER BY seq",
        (snapshot_id,),
    ).fetchall()
    questions = [
        SessionBundleQuestion(question_id=qid, seq=seq, text=text, follow_ups=json.loads(follow_ups_json))
        for qid, seq, text, follow_ups_json in question_rows
    ]

    return SessionBundle(
        session_id=session_id, prep_curve=prep_curve,
        follow_up_limit=_load_follow_up_limit(conn, job_id), questions=questions,
    )


@idempotent_effect("effect_open_session")
def effect_open_session(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, bundle: SessionBundle, client,
) -> None:
    """business_key = str(snapshot_version)（design D20 字面公式
    `{session_id}:effect_open_session:{snapshot_version}`）。重复调用（每轮
    轮询都会无条件调用这个函数）在第一次成功后被幂等短路，不会重复通知语音
    主机。"""
    client.create_session(bundle)
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type) VALUES (?, ?, 'opened')",
        (str(uuid.uuid4()), session_id),
    )


@idempotent_effect("effect_persist_turn")
def effect_persist_turn(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, event: LiveTurnEvent,
) -> None:
    """business_key = str(event.seq)（design D20 字面公式
    `{session_id}:effect_persist_turn:{seq}`）。`idx_interview_turn_session_seq`
    的 UNIQUE(session_id, seq) 是第二层幂等保护（reviewer 判据：即便
    effect_log 判定失误，唯一索引也会挡住重复行）。"""
    follow_up_of_id = None
    if event.follow_up_of_seq is not None:
        row = conn.execute(
            "SELECT id FROM interview_turn WHERE session_id = ? AND seq = ?",
            (session_id, event.follow_up_of_seq),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"follow_up_of_seq={event.follow_up_of_seq} 在场次 {session_id!r} 中找不到对应 turn"
            )
        follow_up_of_id = row[0]

    conn.execute(
        "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, "
        "answer_text, answer_mode, audio_start_ms, audio_end_ms, latency_json, "
        "follow_up_of, interrupted_at_ms, asr_confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), session_id, event.seq, event.question_id, event.question_text,
            event.answer_text, event.answer_mode, event.audio_start_ms, event.audio_end_ms,
            json.dumps(event.latency, ensure_ascii=False), follow_up_of_id,
            event.interrupted_at_ms, event.asr_confidence,
        ),
    )
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type, detail) VALUES (?, ?, 'turn_persisted', ?)",
        (str(uuid.uuid4()), session_id, str(event.seq)),
    )


@idempotent_effect("effect_close_session")
def effect_close_session(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, final_status: str,
) -> None:
    """business_key 常量 "close"（本计划「设计决策 7」，同场次只关闭一次，
    与 app/graph/invite_nodes.py::effect_open_invite 的 business_key="open"
    同一先例）。"""
    if final_status not in ("completed", "interrupted"):
        raise ValueError(f"非法的 final_status: {final_status!r}")
    conn.execute(
        "UPDATE interview_session SET status = ? WHERE id = ? AND status = 'in_progress'",
        (final_status, session_id),
    )
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type, detail) VALUES (?, ?, 'closed', ?)",
        (str(uuid.uuid4()), session_id, final_status),
    )


@idempotent_effect("effect_fetch_recording")
def effect_fetch_recording(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str,
    content: bytes, recording_sha256: str, recording_dir: str, client,
) -> None:
    """business_key = recording_sha256，由调用方（run_live_session_sync）在
    下载完成后算好再传入（本计划「设计决策 8」：网络 GET 本身可安全重复，
    幂等保护只覆盖"落盘 + 更新 DB + 通知语音主机删副本"这几个真正的写副作用）。
    """
    path = Path(recording_dir) / f"{session_id}.rec"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    conn.execute(
        "UPDATE interview_session SET recording_uri = ?, recording_sha256 = ? WHERE id = ?",
        (str(path), recording_sha256, session_id),
    )
    conn.execute(
        "INSERT INTO interview_live_event (id, session_id, event_type, detail) VALUES (?, ?, 'recording_fetched', ?)",
        (str(uuid.uuid4()), session_id, recording_sha256),
    )
    client.notify_delete_artifacts(session_id)


def run_live_session_sync(conn: sqlite3.Connection, *, session_id: str, client, recording_dir: str) -> str:
    """一轮轮询的完整编排（live-voice-interview-session spec「开场前置条件」
    「场次副作用的幂等」）：
    1. 题目快照必须仍是 frozen（Scenario「题目快照被撤回」）——不满足直接
       跳过，不调用语音主机、不改动任何状态。
    2. `effect_open_session` 无条件调用（幂等短路保证只在首轮真正生效）。
    3. 按已落库的 `MAX(seq)` 作为 `since` 游标轮询新 turn 事件（"中断可续入
       且不重复出题"与 U3 4.3 `open_resume` 的 `next_seq` 同一口径）。
    4. `session_status` 到达终态才收尾：`effect_close_session` → 下载录音 →
       校验哈希 → `effect_fetch_recording`。
    """
    row = conn.execute(
        "SELECT s.prep_snapshot_version, s.status, ps.status "
        "FROM interview_session s "
        "JOIN application a ON a.id = s.application_id "
        "JOIN prep_snapshot ps ON ps.application_id = a.id AND ps.version = s.prep_snapshot_version "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"interview_session 不存在或找不到对应 prep_snapshot: {session_id!r}")
    snapshot_version, session_status, snapshot_status = row

    if snapshot_status != "frozen":
        logger.warning("场次 %s 的题目快照已不是 frozen（%s），本轮跳过", session_id, snapshot_status)
        return "blocked_snapshot_not_frozen"
    if session_status != "in_progress":
        return f"skipped_status_{session_status}"

    bundle = compute_session_bundle(conn, session_id=session_id)
    effect_open_session(
        conn, thread_id=session_id, business_key=str(snapshot_version),
        session_id=session_id, bundle=bundle, client=client,
    )

    since = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM interview_turn WHERE session_id = ?", (session_id,)
    ).fetchone()[0]

    poll = client.poll_events(session_id, since=since)
    for event in poll.events:
        effect_persist_turn(
            conn, thread_id=session_id, business_key=str(event.seq),
            session_id=session_id, event=event,
        )

    if poll.session_status not in ("completed", "interrupted"):
        return "in_progress"

    effect_close_session(
        conn, thread_id=session_id, business_key="close",
        session_id=session_id, final_status=poll.session_status,
    )

    content, recording_sha256 = client.fetch_recording(session_id)
    if hashlib.sha256(content).hexdigest() != recording_sha256:
        raise ValueError(f"场次 {session_id!r} 录音回传哈希校验失败")
    effect_fetch_recording(
        conn, thread_id=session_id, business_key=recording_sha256,
        session_id=session_id, content=content, recording_sha256=recording_sha256,
        recording_dir=recording_dir, client=client,
    )
    return poll.session_status
```

- [ ] **Step 8: 运行测试确认通过**

Run: `pytest tests/test_live_session_nodes.py -v`
Expected: PASS（8 项）

- [ ] **Step 9: 跑迁移漂移守卫测试确认未破坏既有行为**

Run: `pytest tests/test_db_migration.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add app/storage/db.py app/graph/live_session_nodes.py tests/test_db_m3_schema.py tests/test_live_session_nodes.py
git commit -m "feat(voice-interview): U4 live 子图 DB schema 与 effect_* 节点（design D20）"
```

---

### Task 6: `scripts/run_interview_live_sync.py` 轮询批处理入口

**Files:**
- Create: `scripts/run_interview_live_sync.py`
- Test: `tests/test_run_interview_live_sync_script.py`

**Interfaces:**
- Consumes：`app.graph.live_session_nodes.run_live_session_sync`、`app.live_voice.client.VoiceHostClient`。
- Produces：`_sessions_ready_for_live_sync(conn) -> list[str]`、`run_once(conn, *, client, recording_dir) -> int`、`run_loop(conn, *, client, recording_dir, interval, max_iterations=None) -> None`、`main()`。真正的 Windows 计划任务安装是 tasks.md 8.6 范围，本脚本只是它将来调用的入口。

- [ ] **Step 1: 写 `_sessions_ready_for_live_sync` 与 `run_once`/`run_loop` 测试**

创建 `tests/test_run_interview_live_sync_script.py`：

```python
"""scripts/run_interview_live_sync.py：轮询批处理入口。数据库用真实 SQLite
（tmp_path），两机客户端用手写 Fake（与 tests/test_live_session_nodes.py 的
FakeVoiceHostClient 同一形状，本文件独立不 import 它，避免测试间耦合）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_interview_live_sync import _sessions_ready_for_live_sync, run_loop, run_once
from app.schemas.live_turn_event import LiveEventsPollResponse
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_session(conn, *, session_id, status="in_progress", phone_verified=True, consents=2):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', 't') ON CONFLICT(id) DO NOTHING")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三') ON CONFLICT(id) DO NOTHING")
    conn.execute(
        "INSERT OR IGNORE INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', 'job-1', 'synthetic', 'a.pdf', 'hash', 'tester')"
    )
    conn.execute(
        f"INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        f"VALUES ('app-{session_id}', 'cand-1', 'job-1', 'resume-1', 'initial')"
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, input_hash, raw_response) "
        f"VALUES ('run-{session_id}', 'deepseek-chat', 'v1', 0, 'h', 'r')"
    )
    conn.execute(
        f"INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        f"VALUES ('snap-{session_id}', 'app-{session_id}', 1, 1, 'run-{session_id}', 'frozen')"
    )
    phone_verified_at = "datetime('now')" if phone_verified else "NULL"
    conn.execute(
        f"INSERT INTO interview_session (id, application_id, prep_snapshot_version, phone_verified_at, "
        f"retention_until, retention_policy_version, sample_class, status) "
        f"VALUES ('{session_id}', 'app-{session_id}', 1, {phone_verified_at}, "
        f"'2099-01-01', 'v1', 'internal_sim', '{status}')"
    )
    for i in range(consents):
        kind = "ai_interview" if i == 0 else "identity_check"
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) VALUES (?, ?, 'accepted', 'v1')",
            (session_id, kind),
        )
    conn.commit()


def test_ready_sessions_requires_status_phone_and_both_consents(conn):
    _seed_session(conn, session_id="ready")
    _seed_session(conn, session_id="not_verified", phone_verified=False)
    _seed_session(conn, session_id="one_consent", consents=1)
    assert _sessions_ready_for_live_sync(conn) == ["ready"]


class FakeClient:
    def __init__(self):
        self.created = []

    def create_session(self, bundle):
        self.created.append(bundle.session_id)
        return {"accepted": True}

    def poll_events(self, session_id, *, since):
        return LiveEventsPollResponse(events=[], session_status="in_progress")

    def fetch_recording(self, session_id):
        raise AssertionError("不应该在 in_progress 阶段拉录音")

    def notify_delete_artifacts(self, session_id):
        raise AssertionError("不应该在 in_progress 阶段通知删除")


def test_run_once_processes_all_ready_sessions(conn, tmp_path):
    _seed_session(conn, session_id="s1")
    _seed_session(conn, session_id="s2")
    client = FakeClient()

    processed = run_once(conn, client=client, recording_dir=str(tmp_path))

    assert processed == 2
    assert set(client.created) == {"s1", "s2"}


def test_run_once_swallows_per_session_exception_and_continues(conn, tmp_path, monkeypatch):
    _seed_session(conn, session_id="s1")
    _seed_session(conn, session_id="s2")

    import scripts.run_interview_live_sync as mod

    calls = []

    def _boom(*args, **kwargs):
        calls.append(kwargs.get("session_id"))
        if kwargs.get("session_id") == "s1":
            raise RuntimeError("模拟出站请求超时")
        return "in_progress"

    monkeypatch.setattr(mod, "run_live_session_sync", _boom)
    processed = run_once(conn, client=FakeClient(), recording_dir=str(tmp_path))

    assert processed == 2
    assert calls == ["s1", "s2"]


def test_run_loop_stops_after_max_iterations(conn, tmp_path, monkeypatch):
    import scripts.run_interview_live_sync as mod

    call_count = {"n": 0}

    def _fake_run_once(*args, **kwargs):
        call_count["n"] += 1
        return 0

    monkeypatch.setattr(mod, "run_once", _fake_run_once)
    run_loop(conn, client=FakeClient(), recording_dir=str(tmp_path), interval=0.0, max_iterations=3)

    assert call_count["n"] == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_run_interview_live_sync_script.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'scripts.run_interview_live_sync'`）

- [ ] **Step 3: 实现脚本**

创建 `scripts/run_interview_live_sync.py`：

```python
"""M3 U4 live 段 `.51` 出站轮询同步入口（voice-structured-interview tasks.md
5.9）。持续轮询处于 in_progress 且已通过开场前置条件（live-voice-interview-
session spec「开场前置条件」）的场次，调用
app.graph.live_session_nodes.run_live_session_sync 拉 turn 事件、场次收尾、
拉取并回传删除录音副本。

真正的 Windows 计划任务安装（SYSTEM 账户/AtStartup/失败重启 3 次）是
tasks.md 8.6 的范围；本脚本自身内建 `--interval` 轮询循环（design.md Risks
「轮询间隔 2s」），计划任务只需要保证进程存活，不需要每 2 秒重新拉起一次。
"""
from __future__ import annotations

import argparse
import logging
import time

from app.config import get_settings
from app.graph.live_session_nodes import run_live_session_sync
from app.live_voice.client import VoiceHostClient
from app.storage.db import get_connection, init_schema

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 2.0


def _sessions_ready_for_live_sync(conn) -> list[str]:
    """live-voice-interview-session spec「开场前置条件」的存储层落点：题目
    快照已冻结（由 run_live_session_sync 自己再查一次 prep_snapshot.status
    把关，本查询只挡 status/手机号/双同意三项，是更便宜的粗筛）、双同意均
    通过、手机号核验通过、场次处于 in_progress。"""
    rows = conn.execute(
        "SELECT s.id FROM interview_session s "
        "WHERE s.status = 'in_progress' AND s.phone_verified_at IS NOT NULL "
        "AND (SELECT COUNT(*) FROM interview_consent c WHERE c.session_id = s.id "
        "AND c.result = 'accepted') = 2 "
        "ORDER BY s.created_at"
    ).fetchall()
    return [row[0] for row in rows]


def run_once(conn, *, client: VoiceHostClient, recording_dir: str) -> int:
    """返回本轮处理的场次数。单场次异常不得中断整批（与
    scripts/run_interview_post_scoring.py::run_batch 同一处理方式）。"""
    processed = 0
    for session_id in _sessions_ready_for_live_sync(conn):
        try:
            result = run_live_session_sync(
                conn, session_id=session_id, client=client, recording_dir=recording_dir,
            )
            logger.info("场次 %s 本轮同步结果: %s", session_id, result)
        except Exception:
            logger.exception("场次 %s 本轮同步出现未预期异常，跳过继续处理其余场次", session_id)
        processed += 1
    return processed


def run_loop(
    conn, *, client: VoiceHostClient, recording_dir: str,
    interval: float, max_iterations: int | None = None,
) -> None:
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        run_once(conn, client=client, recording_dir=recording_dir)
        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            time.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出（供测试/手动排障用）")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    args = parser.parse_args()

    settings = get_settings()
    conn = get_connection(settings.db_path)
    init_schema(conn)
    client = VoiceHostClient(
        base_url=settings.voice_host_base_url,
        shared_secret=settings.voice_host_shared_secret,
        timeout=settings.voice_host_request_timeout_seconds,
    )

    if args.once:
        run_once(conn, client=client, recording_dir=settings.interview_recording_dir)
    else:
        run_loop(conn, client=client, recording_dir=settings.interview_recording_dir, interval=args.interval)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_run_interview_live_sync_script.py -v`
Expected: PASS（4 项）

- [ ] **Step 5: Commit**

```bash
git add scripts/run_interview_live_sync.py tests/test_run_interview_live_sync_script.py
git commit -m "feat(voice-interview): U4 live 段 .51 出站轮询同步批处理入口"
```

---

### Task 7: `scripts/report_m3_latency.py` 延迟报表

**Files:**
- Create: `scripts/report_m3_latency.py`
- Test: `tests/test_report_m3_latency.py`

**Interfaces:**
- Produces：`SEGMENT_KEYS`、`collect_latencies(conn, *, session_ids) -> dict[str, list[float]]`、`summarize(values) -> dict[str, dict[str, float]]`、`upsert_batch_section(md_path, label, summary) -> None`、`main()`。

- [ ] **Step 1: 写统计与幂等写入测试**

创建 `tests/test_report_m3_latency.py`：

```python
"""scripts/report_m3_latency.py：延迟统计与 docs/m3-voice-probe.md 幂等
写入（voice-structured-interview U4 tasks 5.10）。"""
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.report_m3_latency import collect_latencies, summarize, upsert_batch_section
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_turn(conn, *, session_id, seq, latency: dict):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', 't') ON CONFLICT(id) DO NOTHING")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三') ON CONFLICT(id) DO NOTHING")
    conn.execute(
        "INSERT OR IGNORE INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', 'job-1', 'synthetic', 'a.pdf', 'hash', 'tester')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        f"VALUES ('app-{session_id}', 'cand-1', 'job-1', 'resume-1', 'initial')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO interview_session (id, application_id, prep_snapshot_version, "
        f"retention_until, retention_policy_version, sample_class, status) "
        f"VALUES ('{session_id}', 'app-{session_id}', 1, '2099-01-01', 'v1', 'internal_sim', 'completed')"
    )
    conn.execute(
        "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, answer_text, "
        "answer_mode, latency_json) VALUES (?, ?, ?, 'q1', 't', 'a', 'voice', ?)",
        (f"{session_id}-{seq}", session_id, seq, json.dumps(latency, ensure_ascii=False)),
    )
    conn.commit()


def test_collect_latencies_gathers_values_per_segment(conn):
    _seed_turn(conn, session_id="s1", seq=1, latency={"endpoint_detection_ms": 300, "end_to_end_ms": 700})
    _seed_turn(conn, session_id="s1", seq=2, latency={"endpoint_detection_ms": 320, "end_to_end_ms": 750})
    values = collect_latencies(conn, session_ids=["s1"])
    assert values["endpoint_detection_ms"] == [300.0, 320.0]
    assert values["end_to_end_ms"] == [700.0, 750.0]
    assert values["asr_ms"] == []


def test_summarize_computes_median_and_p95():
    values = {"end_to_end_ms": [100.0, 200.0, 300.0, 400.0, 500.0]}
    summary = summarize(values)
    assert summary["end_to_end_ms"]["median"] == 300.0
    assert summary["end_to_end_ms"]["n"] == 5
    assert summary["end_to_end_ms"]["p95"] >= 400.0


def test_summarize_handles_empty_series():
    summary = summarize({"asr_ms": []})
    assert summary["asr_ms"] == {"median": 0.0, "p95": 0.0, "n": 0}


def test_upsert_batch_section_is_idempotent_and_overwrites_same_label(tmp_path):
    md_path = tmp_path / "m3-voice-probe.md"
    summary_v1 = {"end_to_end_ms": {"median": 700.0, "p95": 750.0, "n": 10}}
    summary_v2 = {"end_to_end_ms": {"median": 650.0, "p95": 720.0, "n": 12}}

    upsert_batch_section(md_path, "batch-1", summary_v1)
    upsert_batch_section(md_path, "batch-1", summary_v2)

    text = md_path.read_text(encoding="utf-8")
    assert text.count("batch-1") >= 1
    assert "650.0" in text
    assert "700.0" not in text  # 旧数据被覆盖，不是追加


def test_upsert_batch_section_keeps_different_labels_separate(tmp_path):
    md_path = tmp_path / "m3-voice-probe.md"
    upsert_batch_section(md_path, "batch-1", {"end_to_end_ms": {"median": 700.0, "p95": 750.0, "n": 10}})
    upsert_batch_section(md_path, "batch-2", {"end_to_end_ms": {"median": 600.0, "p95": 650.0, "n": 8}})
    text = md_path.read_text(encoding="utf-8")
    assert "batch-1" in text
    assert "batch-2" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_report_m3_latency.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现脚本**

创建 `scripts/report_m3_latency.py`：

```python
"""M3 U4 延迟报表（voice-structured-interview tasks.md 5.10）。

按场次集合统计 interview_turn.latency_json 里各分段延迟与端到端延迟的
中位数/P95，写入 docs/m3-voice-probe.md「内部模拟批次」节（design.md
「验收门槛：端到端延迟中位 < 800ms」的度量来源）。

latency_json 的字段约定（voice_host/worker.py 写入，本计划「设计决策 6」）：
{"endpoint_detection_ms", "asr_ms", "follow_up_selection_ms"(仅追问轮),
 "tts_first_frame_ms", "end_to_end_ms"}。缺失某个分段键的 turn（如文本作答
turn）在该分段的统计里被跳过，不计入分母。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

SEGMENT_KEYS = (
    "endpoint_detection_ms", "asr_ms", "follow_up_selection_ms",
    "tts_first_frame_ms", "end_to_end_ms",
)

MARKER_START = "<!-- m3-latency-batch:{label} -->"
MARKER_END = "<!-- /m3-latency-batch:{label} -->"


def collect_latencies(conn, *, session_ids: list[str]) -> dict[str, list[float]]:
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"SELECT latency_json FROM interview_turn "
        f"WHERE session_id IN ({placeholders}) AND latency_json IS NOT NULL",
        session_ids,
    ).fetchall()
    values: dict[str, list[float]] = {key: [] for key in SEGMENT_KEYS}
    for (raw,) in rows:
        parsed = json.loads(raw)
        for key in SEGMENT_KEYS:
            if key in parsed and parsed[key] is not None:
                values[key].append(float(parsed[key]))
    return values


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[index]


def summarize(values: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for key, series in values.items():
        if not series:
            summary[key] = {"median": 0.0, "p95": 0.0, "n": 0}
            continue
        ordered = sorted(series)
        summary[key] = {
            "median": statistics.median(ordered), "p95": _percentile(ordered, 0.95), "n": len(ordered),
        }
    return summary


def render_section(label: str, summary: dict[str, dict[str, float]]) -> str:
    lines = [
        MARKER_START.format(label=label), f"### {label}", "",
        "| 分段 | 中位 ms | P95 ms | 样本数 |", "|---|---|---|---|",
    ]
    for key in SEGMENT_KEYS:
        s = summary[key]
        lines.append(f"| {key} | {s['median']:.1f} | {s['p95']:.1f} | {s['n']} |")
    lines.append(MARKER_END.format(label=label))
    return "\n".join(lines)


def upsert_batch_section(md_path: Path, label: str, summary: dict[str, dict[str, float]]) -> None:
    text = md_path.read_text(encoding="utf-8") if md_path.exists() else "# M3 语音探针结果（U0）\n"
    block = render_section(label, summary)
    start_marker = MARKER_START.format(label=label)
    end_marker = MARKER_END.format(label=label)
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)

    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        if "## 内部模拟批次" not in text:
            text = text.rstrip("\n") + "\n\n## 内部模拟批次\n\n"
        text = text.rstrip("\n") + "\n\n" + block + "\n"

    md_path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-ids", nargs="+", required=True)
    parser.add_argument("--batch-label", required=True)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--md-path", default="docs/m3-voice-probe.md")
    args = parser.parse_args()

    from app.config import get_settings
    from app.storage.db import get_connection, init_schema

    db_path = args.db_path or get_settings().db_path
    conn = get_connection(db_path)
    init_schema(conn)

    values = collect_latencies(conn, session_ids=args.session_ids)
    summary = summarize(values)
    upsert_batch_section(Path(args.md_path), args.batch_label, summary)
    print(f"批次 {args.batch_label} 已写入 {args.md_path}：{summary}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_report_m3_latency.py -v`
Expected: PASS（5 项）

- [ ] **Step 5: Commit**

```bash
git add scripts/report_m3_latency.py tests/test_report_m3_latency.py
git commit -m "feat(voice-interview): U4 延迟报表脚本（design 端到端 <800ms 门槛）"
```

---

### Task 8: `voice_host/` 包骨架 + 本地 SQLite 事件队列

**Files:**
- Create: `voice_host/__init__.py`
- Create: `voice_host/config.py`
- Create: `voice_host/queue_store.py`
- Test: `tests/test_voice_host_queue_store.py`

**Interfaces:**
- Produces：`voice_host.config.VoiceHostSettings`（`shared_secret`/`data_dir`/`port`，从环境变量读取，`load_settings() -> VoiceHostSettings`）；`voice_host.queue_store` 的 `get_connection(db_path) -> sqlite3.Connection`、`init_schema(conn)`、`open_session(conn, *, session_id, bundle_json)`、`append_turn_event(conn, *, session_id, seq, question_id, question_text, answer_text, answer_mode, audio_start_ms, audio_end_ms, asr_confidence, follow_up_of_seq, interrupted_at_ms, latency)`、`events_since(conn, *, session_id, since_seq) -> list[dict]`、`get_session_status(conn, *, session_id) -> str | None`、`close_session(conn, *, session_id, status)`、`record_recording_file(conn, *, session_id, path, sha256)`、`get_recording_file(conn, *, session_id) -> dict | None`、`mark_recording_transferred(conn, *, session_id)`、`sweep_expired_intermediate_files(conn, *, now, max_age_hours=24) -> list[str]`、`record_voice_host_event(conn, *, session_id, event_type, detail=None)`。Task 9/11/12/13 都依赖这些确切名字。

**本任务测试全部落在项目主 `tests/` 目录、用项目主 venv 跑**（本计划「设计决策 9」：`voice_host/queue_store.py` 只用 stdlib `sqlite3`，不 import 任何重依赖，不需要额外安装任何东西）。

- [ ] **Step 1: 写 `queue_store.py` 测试**

创建 `tests/test_voice_host_queue_store.py`：

```python
"""voice_host/queue_store.py：语音主机本地事件队列（design D19"worker 的输出
是 turn 事件队列（本地 SQLite 队列，被 .51 拉走后标记）与录音文件"）。全部
用 stdlib sqlite3，不依赖 app.storage（语音主机结构上不可能访问 `.51` 的
数据库）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voice_host import queue_store


@pytest.fixture
def conn(tmp_path):
    connection = queue_store.get_connection(str(tmp_path / "queue.db"))
    queue_store.init_schema(connection)
    return connection


def test_open_session_is_idempotent(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json='{"session_id": "s1"}')
    queue_store.open_session(conn, session_id="s1", bundle_json='{"session_id": "s1"}')
    assert queue_store.get_session_status(conn, session_id="s1") == "in_progress"


def test_append_turn_event_and_events_since(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.append_turn_event(
        conn, session_id="s1", seq=1, question_id="q1", question_text="t", answer_text="a",
        answer_mode="voice", audio_start_ms=0, audio_end_ms=3000, asr_confidence=0.9,
        follow_up_of_seq=None, interrupted_at_ms=None, latency={"end_to_end_ms": 500.0},
    )
    queue_store.append_turn_event(
        conn, session_id="s1", seq=2, question_id="q1", question_text="t2", answer_text="a2",
        answer_mode="voice", audio_start_ms=3200, audio_end_ms=6000, asr_confidence=0.8,
        follow_up_of_seq=1, interrupted_at_ms=None, latency={"end_to_end_ms": 420.0},
    )
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["follow_up_of_seq"] == 1

    only_new = queue_store.events_since(conn, session_id="s1", since_seq=1)
    assert [e["seq"] for e in only_new] == [2]


def test_append_turn_event_is_idempotent_on_repeat_seq(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    kwargs = dict(
        session_id="s1", seq=1, question_id="q1", question_text="t", answer_text="a",
        answer_mode="text", audio_start_ms=None, audio_end_ms=None, asr_confidence=None,
        follow_up_of_seq=None, interrupted_at_ms=None, latency={},
    )
    queue_store.append_turn_event(conn, **kwargs)
    queue_store.append_turn_event(conn, **kwargs)
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert len(events) == 1


def test_close_session_updates_status(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.close_session(conn, session_id="s1", status="completed")
    assert queue_store.get_session_status(conn, session_id="s1") == "completed"


def test_record_and_get_recording_file(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="s1", path=str(tmp_path / "s1.rec"), sha256="abc123")
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info == {"path": str(tmp_path / "s1.rec"), "sha256": "abc123", "transferred": False}

    queue_store.mark_recording_transferred(conn, session_id="s1")
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info["transferred"] is True


def test_sweep_expired_intermediate_files_only_deletes_old_untransferred(conn, tmp_path):
    import time

    old_path = tmp_path / "old.rec"
    old_path.write_bytes(b"old")
    fresh_path = tmp_path / "fresh.rec"
    fresh_path.write_bytes(b"fresh")
    transferred_path = tmp_path / "transferred.rec"
    transferred_path.write_bytes(b"transferred")

    queue_store.open_session(conn, session_id="old", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="old", path=str(old_path), sha256="h1")
    queue_store.open_session(conn, session_id="fresh", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="fresh", path=str(fresh_path), sha256="h2")
    queue_store.open_session(conn, session_id="xfer", bundle_json="{}")
    queue_store.record_recording_file(conn, session_id="xfer", path=str(transferred_path), sha256="h3")
    queue_store.mark_recording_transferred(conn, session_id="xfer")

    now = time.time()
    twenty_five_hours_ago = now - 25 * 3600
    conn.execute("UPDATE recording_file SET recorded_at = ? WHERE session_id = 'old'", (twenty_five_hours_ago,))
    conn.commit()

    deleted = queue_store.sweep_expired_intermediate_files(conn, now=now, max_age_hours=24)

    assert deleted == [str(old_path)]
    assert not old_path.exists()
    assert fresh_path.exists()
    assert transferred_path.exists()


def test_record_voice_host_event(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.record_voice_host_event(conn, session_id="s1", event_type="mode_switched_manual", detail="seq=3")
    row = conn.execute(
        "SELECT event_type, detail FROM voice_host_event WHERE session_id = 's1'"
    ).fetchone()
    assert row == ("mode_switched_manual", "seq=3")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_voice_host_queue_store.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'voice_host'`）

- [ ] **Step 3: 实现包骨架与配置**

创建 `voice_host/__init__.py`（空文件）。

创建 `voice_host/config.py`：

```python
"""语音主机配置（voice-structured-interview U4 tasks 5.1，design D12）。
全部走环境变量，⛔ 不依赖 `.51` 的 app.config.Settings——两台机器完全独立
部署，语音主机没有、也不应该有 `.51` 的 .env。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceHostSettings:
    shared_secret: str
    data_dir: str
    port: int
    cosyvoice_venv_python: str
    cosyvoice_worker_script: str


def load_settings() -> VoiceHostSettings:
    return VoiceHostSettings(
        shared_secret=os.environ.get("VOICE_HOST_SHARED_SECRET", ""),
        data_dir=os.environ.get("VOICE_HOST_DATA_DIR", "voice_host_data"),
        port=int(os.environ.get("VOICE_HOST_PORT", "8090")),
        cosyvoice_venv_python=os.environ.get(
            "VOICE_HOST_COSYVOICE_PYTHON", "voice_host/.venv-cosyvoice/bin/python"
        ),
        cosyvoice_worker_script=os.environ.get(
            "VOICE_HOST_COSYVOICE_WORKER", "voice_host/tts_cosyvoice_worker.py"
        ),
    )
```

- [ ] **Step 4: 实现 `queue_store.py`**

创建 `voice_host/queue_store.py`：

```python
"""语音主机本地事件队列（voice-structured-interview U4 tasks 5.1-5.9，
design D19"worker 的输出是 turn 事件队列（本地 SQLite 队列，被 .51 拉走后
标记）与录音文件"）。

⛔ 不 import app.storage、不 import 任何 `.51` 专属模块——语音主机结构上
不可能访问 `.51` 的数据库（本计划「Global Constraints 铁律 1 的适用说明」）。
全部用 stdlib sqlite3，独立 schema、独立连接。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY NOT NULL,
    bundle_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'interrupted')),
    current_answer_mode TEXT NOT NULL DEFAULT 'voice' CHECK (current_answer_mode IN ('voice', 'text')),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_event (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES session(id),
    seq INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    answer_mode TEXT NOT NULL CHECK (answer_mode IN ('voice', 'text')),
    audio_start_ms INTEGER,
    audio_end_ms INTEGER,
    asr_confidence REAL,
    follow_up_of_seq INTEGER,
    interrupted_at_ms INTEGER,
    latency_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_event_session_seq ON turn_event (session_id, seq);

CREATE TABLE IF NOT EXISTS recording_file (
    session_id TEXT PRIMARY KEY NOT NULL REFERENCES session(id),
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    transferred_at REAL
);

CREATE TABLE IF NOT EXISTS voice_host_event (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES session(id),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'mode_switched_manual', 'mode_switched_after_prompt', 'network_quality_prompted'
    )),
    detail TEXT,
    created_at REAL NOT NULL
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def open_session(conn: sqlite3.Connection, *, session_id: str, bundle_json: str) -> None:
    """`INSERT OR IGNORE`：同一 `session_id` 重复 POST /sessions（`.51` 每轮
    轮询都会调用一次 create_session）天然幂等，不需要额外的幂等键机制——
    `session_id` 本身就是这一侧的天然幂等键。"""
    conn.execute(
        "INSERT OR IGNORE INTO session (id, bundle_json, created_at) VALUES (?, ?, ?)",
        (session_id, bundle_json, time.time()),
    )
    conn.commit()


def get_session_status(conn: sqlite3.Connection, *, session_id: str) -> str | None:
    row = conn.execute("SELECT status FROM session WHERE id = ?", (session_id,)).fetchone()
    return row[0] if row else None


def get_current_answer_mode(conn: sqlite3.Connection, *, session_id: str) -> str:
    row = conn.execute("SELECT current_answer_mode FROM session WHERE id = ?", (session_id,)).fetchone()
    return row[0] if row else "voice"


def set_current_answer_mode(conn: sqlite3.Connection, *, session_id: str, mode: str) -> None:
    conn.execute("UPDATE session SET current_answer_mode = ? WHERE id = ?", (mode, session_id))
    conn.commit()


def close_session(conn: sqlite3.Connection, *, session_id: str, status: str) -> None:
    conn.execute("UPDATE session SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()


def append_turn_event(
    conn: sqlite3.Connection, *, session_id: str, seq: int, question_id: str, question_text: str,
    answer_text: str, answer_mode: str, audio_start_ms: int | None, audio_end_ms: int | None,
    asr_confidence: float | None, follow_up_of_seq: int | None, interrupted_at_ms: int | None,
    latency: dict,
) -> None:
    """`(session_id, seq)` 唯一索引是幂等边界——worker.py 每题只调用一次，
    但重跑/重试时用 `INSERT OR IGNORE` 兜底，不因重复调用报错。"""
    conn.execute(
        "INSERT OR IGNORE INTO turn_event (id, session_id, seq, question_id, question_text, "
        "answer_text, answer_mode, audio_start_ms, audio_end_ms, asr_confidence, "
        "follow_up_of_seq, interrupted_at_ms, latency_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), session_id, seq, question_id, question_text, answer_text, answer_mode,
            audio_start_ms, audio_end_ms, asr_confidence, follow_up_of_seq, interrupted_at_ms,
            json.dumps(latency, ensure_ascii=False),
        ),
    )
    conn.commit()


def events_since(conn: sqlite3.Connection, *, session_id: str, since_seq: int) -> list[dict]:
    rows = conn.execute(
        "SELECT seq, question_id, question_text, answer_text, answer_mode, audio_start_ms, "
        "audio_end_ms, asr_confidence, follow_up_of_seq, interrupted_at_ms, latency_json "
        "FROM turn_event WHERE session_id = ? AND seq > ? ORDER BY seq",
        (session_id, since_seq),
    ).fetchall()
    return [
        {
            "seq": r[0], "question_id": r[1], "question_text": r[2], "answer_text": r[3],
            "answer_mode": r[4], "audio_start_ms": r[5], "audio_end_ms": r[6], "asr_confidence": r[7],
            "follow_up_of_seq": r[8], "interrupted_at_ms": r[9], "latency": json.loads(r[10]),
        }
        for r in rows
    ]


def record_recording_file(conn: sqlite3.Connection, *, session_id: str, path: str, sha256: str) -> None:
    conn.execute(
        "INSERT INTO recording_file (session_id, path, sha256, recorded_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET path = excluded.path, sha256 = excluded.sha256, "
        "recorded_at = excluded.recorded_at",
        (session_id, path, sha256, time.time()),
    )
    conn.commit()


def get_recording_file(conn: sqlite3.Connection, *, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT path, sha256, transferred_at FROM recording_file WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return {"path": row[0], "sha256": row[1], "transferred": row[2] is not None}


def mark_recording_transferred(conn: sqlite3.Connection, *, session_id: str) -> None:
    conn.execute(
        "UPDATE recording_file SET transferred_at = ? WHERE session_id = ?", (time.time(), session_id)
    )
    conn.commit()


def sweep_expired_intermediate_files(conn: sqlite3.Connection, *, now: float, max_age_hours: int = 24) -> list[str]:
    """回传前的中间文件过期兜底清理（interview-recording-retention spec
    「语音主机不留副本」：回传成功即删由 Task 12 的正常路径处理；本函数是
    "`.51` 从未成功回传"这种异常情况的独立安全网）。只删
    `transferred_at IS NULL` 且 `recorded_at` 早于 `max_age_hours` 之前的行，
    已转移的文件（`transferred_at` 非空）不受影响。"""
    cutoff = now - max_age_hours * 3600
    rows = conn.execute(
        "SELECT session_id, path FROM recording_file WHERE transferred_at IS NULL AND recorded_at < ?",
        (cutoff,),
    ).fetchall()
    deleted: list[str] = []
    for session_id, path in rows:
        Path(path).unlink(missing_ok=True)
        conn.execute("DELETE FROM recording_file WHERE session_id = ?", (session_id,))
        deleted.append(path)
    conn.commit()
    return deleted


def record_voice_host_event(
    conn: sqlite3.Connection, *, session_id: str, event_type: str, detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO voice_host_event (id, session_id, event_type, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), session_id, event_type, detail, time.time()),
    )
    conn.commit()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_voice_host_queue_store.py -v`
Expected: PASS（7 项）

- [ ] **Step 6: Commit**

```bash
git add voice_host/__init__.py voice_host/config.py voice_host/queue_store.py tests/test_voice_host_queue_store.py
git commit -m "feat(voice-interview): U4 语音主机本地事件队列（design D19）"
```

---

### Task 9: `voice_host/api.py`（FastAPI 服务端点 + HMAC 校验）+ 无入站结构守卫

**Files:**
- Create: `voice_host/_vendor/__init__.py`
- Create: `voice_host/_vendor/signing.py`（开发/测试期的手工种子拷贝，内容与 `app/live_voice/signing.py` 逐字一致；`sync-to-voice-host.sh` 部署时会用最新版本覆盖它，见 Task 14 Step 3 的漂移测试）
- Create: `voice_host/api.py`
- Test: `tests/test_voice_host_api.py`
- Test: `tests/test_voice_host_no_forbidden_imports.py`

**Interfaces:**
- Produces：`voice_host.api.create_app(*, queue_db_path, shared_secret, on_session_opened=None) -> FastAPI`。端点：`POST /sessions`、`GET /sessions/{id}/events?since=`、`GET /sessions/{id}/recording`、`DELETE /sessions/{id}/artifacts`。

- [ ] **Step 1: 建立签名模块的部署期种子拷贝**

创建 `voice_host/_vendor/__init__.py`（空文件）。

创建 `voice_host/_vendor/signing.py`：与 `app/live_voice/signing.py`（Task 2）逐字相同的内容（复制该文件全文，只改模块顶部注释第一段为下面这段，其余代码逐字不变）：

```python
"""两机接口签名——本文件是 app/live_voice/signing.py 的部署期拷贝（本计划
「设计决策 2」）。⛔ 不要在这个文件里手改逻辑：`sync-to-voice-host.sh`
（Task 14）每次部署都会用 `.51` 仓库里的最新版本覆盖它；`tests/test_voice_
host_no_forbidden_imports.py` 的漂移测试会在两份内容不一致时报警，提醒你
改的是错误的文件。"""
# ... 以下与 app/live_voice/signing.py Step 3 实现内容逐字相同（HEADER_TIMESTAMP
# / HEADER_SIGNATURE / TIMESTAMP_SKEW_SECONDS / canonical_string / sign /
# SignatureInvalidError / verify），此处不重复粘贴，执行者直接 cp 该文件内容。
```

（执行者动作：`cp app/live_voice/signing.py voice_host/_vendor/signing.py`，然后把上面这段新 docstring 替换掉原文件开头的 docstring，其余代码保持逐字一致。）

- [ ] **Step 2: 写漂移守卫测试（防止两份拷贝内容分叉）**

创建 `tests/test_voice_host_no_forbidden_imports.py`：

```python
"""结构性守卫：
1. voice_host/ 包不得出现对 `.51` 专属数据/存储/候选人联系方式的引用
   （design D19"无库访问、无简历"）。
2. voice_host/_vendor/signing.py 与 app/live_voice/signing.py 的核心签名
   算法逐字一致（本计划「设计决策 2」：两侧必须同源，不能各自实现）。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_TOKENS = ("app.storage", "app.outbound", "candidate_contact_vault", "resume_storage_dir")


def test_voice_host_package_has_no_forbidden_references():
    voice_host_dir = REPO_ROOT / "voice_host"
    offending = []
    for path in voice_host_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offending.append(f"{path.relative_to(REPO_ROOT)}: {token}")
    assert offending == [], f"voice_host/ 下出现禁止引用: {offending}"


def test_vendored_signing_module_matches_canonical_source():
    canonical = (REPO_ROOT / "app" / "live_voice" / "signing.py").read_text(encoding="utf-8")
    vendored = (REPO_ROOT / "voice_host" / "_vendor" / "signing.py").read_text(encoding="utf-8")

    def _body_after_docstring(text: str) -> str:
        # 跳过模块级 docstring（两份文件的 docstring 第一段允许不同，见
        # Task 9 Step 1 的说明），只比对代码主体。
        marker = '"""'
        first = text.index(marker)
        second = text.index(marker, first + 3)
        return text[second + 3 :]

    assert _body_after_docstring(canonical) == _body_after_docstring(vendored)
```

- [ ] **Step 3: 运行漂移测试确认通过（Step 1 已手工同步过内容）**

Run: `pytest tests/test_voice_host_no_forbidden_imports.py -v`
Expected: PASS（2 项）

- [ ] **Step 4: 写 `voice_host/api.py` 测试**

创建 `tests/test_voice_host_api.py`（用 `fastapi.testclient.TestClient`，签名走 `app.live_voice.signing`，不依赖真实 HTTP 网络）：

```python
"""voice_host/api.py：语音主机 FastAPI 服务端点。签名用 app.live_voice.
signing（.51 侧的源码，测试环境两侧代码同在一个仓库检出里，直接复用没有
问题；真实部署时语音主机跑的是 voice_host/_vendor/signing.py 的独立拷贝，
算法逐字一致，见 Task 9 Step 2 的漂移测试）。"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.live_voice import signing
from voice_host.api import create_app

SECRET = "test-secret"


@pytest.fixture
def client(tmp_path):
    opened = []
    app = create_app(
        queue_db_path=str(tmp_path / "queue.db"), shared_secret=SECRET,
        on_session_opened=lambda session_id, bundle_json: opened.append(session_id),
    )
    test_client = TestClient(app)
    test_client.opened = opened  # type: ignore[attr-defined]
    return test_client


def _signed_headers(*, method: str, path: str, body: bytes) -> dict:
    ts = str(time.time())
    sig = signing.sign(secret=SECRET, method=method, path=path, timestamp=ts, body=body)
    return {signing.HEADER_TIMESTAMP: ts, signing.HEADER_SIGNATURE: sig}


def _bundle_body(session_id="s1"):
    return json.dumps({
        "session_id": session_id, "prep_curve": "easy_to_hard", "follow_up_limit": 2,
        "questions": [{"question_id": "q1", "seq": 1, "text": "讲讲你的项目", "follow_ups": []}],
    }).encode("utf-8")


def test_post_sessions_accepts_valid_signature_and_registers(client):
    body = _bundle_body()
    headers = _signed_headers(method="POST", path="/sessions", body=body)
    response = client.post("/sessions", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert client.opened == ["s1"]


def test_post_sessions_rejects_bad_signature(client):
    body = _bundle_body()
    headers = _signed_headers(method="POST", path="/sessions", body=b"different-body")
    response = client.post("/sessions", content=body, headers=headers)
    assert response.status_code == 401


def test_post_sessions_rejects_missing_headers(client):
    response = client.post("/sessions", content=_bundle_body())
    assert response.status_code == 401


def test_get_events_returns_empty_before_any_turn(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))

    headers = _signed_headers(method="GET", path="/sessions/s1/events", body=b"")
    response = client.get("/sessions/s1/events", params={"since": 0}, headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"events": [], "session_status": "in_progress"}


def test_get_events_for_unknown_session_returns_404(client):
    headers = _signed_headers(method="GET", path="/sessions/unknown/events", body=b"")
    response = client.get("/sessions/unknown/events", params={"since": 0}, headers=headers)
    assert response.status_code == 404


def test_get_recording_before_available_returns_404(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))
    headers = _signed_headers(method="GET", path="/sessions/s1/recording", body=b"")
    response = client.get("/sessions/s1/recording", headers=headers)
    assert response.status_code == 404


def test_delete_artifacts_is_idempotent_even_without_recording(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))
    headers = _signed_headers(method="DELETE", path="/sessions/s1/artifacts", body=b"")
    first = client.delete("/sessions/s1/artifacts", headers=headers)
    second = client.delete("/sessions/s1/artifacts", headers=headers)
    assert first.status_code == 204
    assert second.status_code == 204
```

- [ ] **Step 5: 运行测试确认失败**

Run: `pytest tests/test_voice_host_api.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'voice_host.api'`）

- [ ] **Step 6: 实现 `voice_host/api.py`**

创建 `voice_host/api.py`：

```python
"""语音主机 FastAPI 服务端点（voice-structured-interview U4 tasks 5.2，
design D12："`.51` 与语音主机之间只有两类交互，全部由 `.51` 主动发起"——
本文件是被动接收方）。

签名校验用 voice_host/_vendor/signing.py（部署期从 `.51` 仓库拷贝的独立
副本，见 Task 9 Step 1/2）。`on_session_opened` 回调在真实部署时接上
`voice_host/worker.py::run_session`（Task 11），本文件自己不知道 ASR/TTS/
LiveKit 是什么，只负责"收请求、验签名、读写本地队列、回响应"。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response

from voice_host import queue_store
from voice_host._vendor import signing


def create_app(*, queue_db_path: str, shared_secret: str, on_session_opened=None) -> FastAPI:
    app = FastAPI()
    conn = queue_store.get_connection(queue_db_path)
    queue_store.init_schema(conn)

    async def _verify(request: Request) -> bytes:
        body = await request.body()
        timestamp = request.headers.get(signing.HEADER_TIMESTAMP)
        signature = request.headers.get(signing.HEADER_SIGNATURE)
        if timestamp is None or signature is None:
            raise HTTPException(status_code=401, detail="缺少签名头")
        try:
            signing.verify(
                secret=shared_secret, method=request.method, path=request.url.path,
                timestamp=timestamp, body=body, signature=signature,
            )
        except signing.SignatureInvalidError:
            raise HTTPException(status_code=401, detail="签名无效")
        return body

    @app.post("/sessions")
    async def post_sessions(request: Request):
        body = await _verify(request)
        import json
        payload = json.loads(body)
        session_id = payload["session_id"]
        queue_store.open_session(conn, session_id=session_id, bundle_json=body.decode("utf-8"))
        if on_session_opened is not None:
            on_session_opened(session_id, body.decode("utf-8"))
        return {"accepted": True}

    @app.get("/sessions/{session_id}/events")
    async def get_events(session_id: str, since: int, request: Request):
        await _verify(request)
        status = queue_store.get_session_status(conn, session_id=session_id)
        if status is None:
            raise HTTPException(status_code=404, detail="场次不存在")
        events = queue_store.events_since(conn, session_id=session_id, since_seq=since)
        return {"events": events, "session_status": status}

    @app.get("/sessions/{session_id}/recording")
    async def get_recording(session_id: str, request: Request):
        await _verify(request)
        info = queue_store.get_recording_file(conn, session_id=session_id)
        if info is None:
            raise HTTPException(status_code=404, detail="录音尚未就绪")
        with open(info["path"], "rb") as fh:
            content = fh.read()
        return Response(content=content, headers={"X-Recording-SHA256": info["sha256"]})

    @app.delete("/sessions/{session_id}/artifacts", status_code=204)
    async def delete_artifacts(session_id: str, request: Request):
        await _verify(request)
        info = queue_store.get_recording_file(conn, session_id=session_id)
        if info is not None:
            from pathlib import Path
            Path(info["path"]).unlink(missing_ok=True)
            queue_store.mark_recording_transferred(conn, session_id=session_id)
        return Response(status_code=204)

    return app
```

- [ ] **Step 7: 运行测试确认通过**

Run: `pytest tests/test_voice_host_api.py -v`
Expected: PASS（7 项）

- [ ] **Step 8: 补跑漂移与结构守卫测试**

Run: `pytest tests/test_voice_host_no_forbidden_imports.py -v`
Expected: PASS（2 项，`voice_host/api.py` 新增的引用不触发 `FORBIDDEN_TOKENS`）

- [ ] **Step 9: Commit**

```bash
git add voice_host/_vendor/__init__.py voice_host/_vendor/signing.py voice_host/api.py tests/test_voice_host_api.py tests/test_voice_host_no_forbidden_imports.py
git commit -m "feat(voice-interview): U4 语音主机 FastAPI 服务端点 + HMAC 校验"
```

---

### Task 10: `voice_host/turn_cycle.py`（打断处理状态机）

**Files:**
- Create: `voice_host/turn_cycle.py`
- Test: `tests/test_voice_host_turn_cycle.py`

**Interfaces:**
- Produces：`INTERRUPT_STOP_BUDGET_MS = 300`、`TurnCycleController`，方法 `start_broadcast(*, question_seq, at_ms, is_follow_up=False)`、`on_candidate_speech_started(*, at_ms) -> int | None`、`on_broadcast_finished_naturally(*, at_ms)`，属性 `was_interrupted: bool`、`truncated_at_ms: int | None`。Task 11 的 `_ask_one_turn` 直接用这个类。

- [ ] **Step 1: 写状态机测试**

创建 `tests/test_voice_host_turn_cycle.py`：

```python
"""voice_host/turn_cycle.py：播报/打断纯状态机（live-voice-interview-session
spec「打断处理」）。不做真实音频 I/O，只接收时刻事件。"""
from voice_host.turn_cycle import TurnCycleController


def test_no_interrupt_when_broadcast_finishes_naturally():
    controller = TurnCycleController()
    controller.start_broadcast(question_seq=1, at_ms=1000)
    controller.on_broadcast_finished_naturally(at_ms=1800)
    assert controller.was_interrupted is False
    assert controller.truncated_at_ms is None


def test_interrupt_records_truncation_offset():
    controller = TurnCycleController()
    controller.start_broadcast(question_seq=1, at_ms=1000)
    offset = controller.on_candidate_speech_started(at_ms=1220)
    assert offset == 220
    assert controller.was_interrupted is True
    assert controller.truncated_at_ms == 220


def test_second_speech_started_call_after_stop_is_a_noop():
    controller = TurnCycleController()
    controller.start_broadcast(question_seq=1, at_ms=1000)
    controller.on_candidate_speech_started(at_ms=1150)
    second = controller.on_candidate_speech_started(at_ms=1400)
    assert second is None
    assert controller.truncated_at_ms == 150  # 第一次记录的截断点不被覆盖


def test_speech_started_without_active_broadcast_is_a_noop():
    controller = TurnCycleController()
    offset = controller.on_candidate_speech_started(at_ms=500)
    assert offset is None
    assert controller.was_interrupted is False


def test_each_question_needs_a_fresh_controller_instance():
    # 设计决策：⛔ 不跨题复用同一个 controller（Task 10 docstring）。这个测试
    # 只是确认"新实例状态是干净的"这个前提成立，不测试 worker.py 的调用方式
    # （那部分在 Task 11 测试）。
    first = TurnCycleController()
    first.start_broadcast(question_seq=1, at_ms=0)
    first.on_candidate_speech_started(at_ms=100)

    second = TurnCycleController()
    assert second.was_interrupted is False
    assert second.truncated_at_ms is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_voice_host_turn_cycle.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'voice_host.turn_cycle'`）

- [ ] **Step 3: 实现状态机**

创建 `voice_host/turn_cycle.py`：

```python
"""语音主机播报/打断状态机（voice-structured-interview U4 tasks 5.5，
live-voice-interview-session spec「打断处理」）。

纯状态机：不做真实音频 I/O，只接收 worker.py 从 LiveKit Agents SDK 的
VAD/端点检测回调转译出的时刻事件（候选人开始说话/播报自然结束），据此判定
播报是否被打断、截断点记在哪。真实音频 I/O 的接线在 voice_host/worker.py
（Task 11），本文件在没有安装 livekit-agents/FunASR/CosyVoice 的环境下也能
被完整单测。
"""
from __future__ import annotations

from dataclasses import dataclass

INTERRUPT_STOP_BUDGET_MS = 300
"""spec 承诺的打断响应上限（300ms 内停播报）。真实的停止耗时由
voice_host/adapters.py 的 TTS 适配器真实实现负责在这个预算内完成；本状态机
只记录候选人开口发生的时刻与截断点，不测量真实停止耗时——那是集成层面的
时序，不是纯状态机能观测到的。"""


@dataclass
class _BroadcastState:
    question_seq: int
    started_at_ms: int
    is_follow_up: bool
    stopped_at_ms: int | None = None
    interrupted: bool = False


class TurnCycleController:
    """一题的播报-作答周期。每题构造一个新实例（worker.py 按题序创建），
    ⛔ 不跨题复用。"""

    def __init__(self) -> None:
        self._state: _BroadcastState | None = None

    def start_broadcast(self, *, question_seq: int, at_ms: int, is_follow_up: bool = False) -> None:
        self._state = _BroadcastState(question_seq=question_seq, started_at_ms=at_ms, is_follow_up=is_follow_up)

    def on_candidate_speech_started(self, *, at_ms: int) -> int | None:
        """候选人开口。播报中 ⇒ 记截断点、标记打断、返回截断毫秒（相对播报
        开始的偏移，供 worker.py 写 `interview_turn.interrupted_at_ms`）；
        没有活跃播报，或播报已经因为这次调用而停止过一次 ⇒ 返回 None，不是
        一次新的"打断"（同一次打断只记一次截断点）。"""
        if self._state is None or self._state.stopped_at_ms is not None:
            return None
        offset = at_ms - self._state.started_at_ms
        self._state.stopped_at_ms = at_ms
        self._state.interrupted = True
        return offset

    def on_broadcast_finished_naturally(self, *, at_ms: int) -> None:
        if self._state is not None and self._state.stopped_at_ms is None:
            self._state.stopped_at_ms = at_ms

    @property
    def was_interrupted(self) -> bool:
        return self._state is not None and self._state.interrupted

    @property
    def truncated_at_ms(self) -> int | None:
        if self._state is None or not self._state.interrupted:
            return None
        return self._state.stopped_at_ms - self._state.started_at_ms
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_voice_host_turn_cycle.py -v`
Expected: PASS（5 项）

- [ ] **Step 5: 补跑结构守卫测试**

Run: `pytest tests/test_voice_host_no_forbidden_imports.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add voice_host/turn_cycle.py tests/test_voice_host_turn_cycle.py
git commit -m "feat(voice-interview): U4 播报/打断状态机（spec「打断处理」）"
```

---

### Task 11: `voice_host/adapters.py` + `voice_host/worker.py`（播报/转写/追问接线）

**Files:**
- Create: `voice_host/adapters.py`
- Create: `voice_host/_vendor/follow_up_selector.py`（`app/agents/follow_up_selector.py` 的部署期拷贝，同 Task 9 的签名模块拷贝手法）
- Create: `voice_host/worker.py`
- Test: `tests/test_voice_host_adapters.py`
- Test: `tests/test_voice_host_worker.py`

**Interfaces:**
- Produces：`voice_host.adapters` 的 `TTSAdapter`/`ASRAdapter` Protocol、`TranscriptResult(text, confidence, endpoint_detection_ms, asr_ms, interrupted_offset_ms=None)`、`FakeTTSAdapter`/`FakeASRAdapter`。`voice_host.worker` 的 `WorkerTurnResult`、`run_session(*, conn, bundle, gateway, tts, asr) -> str`（本 Task 只交付语音模式；Task 12 加录制参数，Task 13 加文本降级分支）。

- [ ] **Step 1: 建立 `follow_up_selector.py` 的部署期种子拷贝**

创建 `voice_host/_vendor/follow_up_selector.py`：内容与 `app/agents/follow_up_selector.py`（Task 3）逐字相同，只把顶部 docstring 第一段换成：

```python
"""追问选择 L3 Agent——本文件是 app/agents/follow_up_selector.py 的部署期
拷贝（本计划「设计决策 2」）。⛔ 不要在这个文件里手改逻辑：
`sync-to-voice-host.sh`（Task 14）每次部署都会用 `.51` 仓库里的最新版本
覆盖它。"""
# ... 其余内容（FOLLOW_UP_PROMPT_VERSION / FollowUpChoice / select() 等）与
# app/agents/follow_up_selector.py 逐字相同，执行者直接 cp 该文件内容并替换
# 开头 docstring。
```

（执行者动作：`cp app/agents/follow_up_selector.py voice_host/_vendor/follow_up_selector.py`，替换开头 docstring。这个文件还依赖 `app/schemas/follow_up_result.py` 与 `app/schemas/interview_ai_input.py`——`sync-to-voice-host.sh` 的白名单也会拷贝这两个文件，见 Task 14；本 Task 先手工 `cp` 三份种子文件到 `voice_host/_vendor/`，保持与 Task 9 处理 signing.py 时相同的动作。）

同样手工执行：`cp app/schemas/follow_up_result.py voice_host/_vendor/follow_up_result.py`、`cp app/schemas/interview_ai_input.py voice_host/_vendor/interview_ai_input.py`，并把 `voice_host/_vendor/follow_up_selector.py` 里的 import 改成 `from voice_host._vendor.follow_up_result import FollowUpChoiceOut` 与 `from voice_host._vendor.interview_ai_input import FollowUpInput`（`app.llm.gateway` 的 import 保持不变——网关本身也是白名单里要拷贝的文件之一，见 Task 14，运行时语音主机会有一份 `voice_host/_vendor/gateway.py`；本 Task 的测试用 `voice_host/_vendor/follow_up_selector.py` 时直接从 `app.llm.gateway` import 即可，因为测试跑在项目主仓库检出里，两个路径实际指向同一份代码，行为一致，不影响本 Task 的可测性）。

- [ ] **Step 2: 写 `adapters.py` 测试**

创建 `tests/test_voice_host_adapters.py`：

```python
"""voice_host/adapters.py：ASR/TTS Protocol 适配器与 Fake 实现。"""
from voice_host.adapters import FakeASRAdapter, FakeTTSAdapter, TranscriptResult


def test_fake_tts_records_played_text_and_consumes_scripted_latency():
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80, 60])
    first = tts.synthesize_and_play("题目一", at_ms=0)
    second = tts.synthesize_and_play("题目二", at_ms=1000)
    assert tts.played == ["题目一", "题目二"]
    assert (first, second) == (80, 60)


def test_fake_tts_repeats_last_value_after_sequence_exhausted():
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    assert tts.synthesize_and_play("a", at_ms=0) == 80
    assert tts.synthesize_and_play("b", at_ms=0) == 80


def test_fake_asr_returns_scripted_results_in_order():
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="回答一", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="回答二", confidence=0.8, endpoint_detection_ms=280, asr_ms=140, interrupted_offset_ms=220),
    ])
    first = asr.transcribe_turn()
    second = asr.transcribe_turn()
    assert first.text == "回答一"
    assert second.interrupted_offset_ms == 220
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_voice_host_adapters.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'voice_host.adapters'`）

- [ ] **Step 4: 实现 `adapters.py`**

创建 `voice_host/adapters.py`：

```python
"""ASR/TTS 的适配器接口（voice-structured-interview U4 tasks 5.4，design
D15"ASR/TTS 只允许自托管"；本计划「设计决策 9/10/12」）。

真实实现（`FunASRStreamingAdapter`/`CosyVoiceSubprocessAdapter`）在函数体
内部才 import funasr/发起 subprocess——funasr 是重依赖，CosyVoice 走独立
子进程（设计决策 10：`grpcio` 版本冲突与 Python 3.10 要求，与主进程的
livekit-agents/FunASR 依赖树隔离）。顶层 import 会让本文件在没装这些包的
机器（含本仓库的开发/CI 环境）上直接 import 失败，连 Fake 实现都用不了。
惰性导入的写法与 scripts/probe_m3_voice.py 的既有先例一致。

`FakeTTSAdapter`/`FakeASRAdapter` 供 worker.py 的单测（Task 11）与 Task 15
的内部模拟 e2e 使用，不接触任何真实音频/网络。

`TranscriptResult.interrupted_offset_ms`：真实的 FunASR + VAD 流式识别管线
能在候选人开口的那一刻上报"打断发生在播报开始后第几毫秒"，这不是测试专用
字段，是真实语音管线的自然输出（本计划「设计决策 12」）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class TTSAdapter(Protocol):
    def synthesize_and_play(self, text: str, *, at_ms: int) -> int:
        """播报文本，返回首帧延迟 ms（`tts_first_frame_ms`）。真实实现里这
        个调用会阻塞到 TTS 首帧就绪、把音频推进 LiveKit 房间。"""
        ...


class ASRAdapter(Protocol):
    def transcribe_turn(self) -> "TranscriptResult":
        """阻塞直到端点检测判定候选人说完（或候选人打断了播报），返回这
        一轮的转写结果。"""
        ...


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    confidence: float
    endpoint_detection_ms: float
    asr_ms: float
    interrupted_offset_ms: int | None = None
    replay_requested: bool = False
    """候选人说"再说一遍"或点击重听（live-voice-interview-session spec
    Scenario「候选人请求重听」）。真实的 FunASR 管线通过关键词识别
    （"再说一遍"/"重听"）或候选人端按钮触发的 data channel 消息上报这个
    标记；`_ask_one_turn`（voice_host/worker.py）据此重新播报同一题，不计入
    追问次数、不产生新的 turn（本计划「设计决策 15」）。"""


@dataclass
class FakeTTSAdapter:
    """测试/内部模拟用：不真的合成音频，记录被要求播报过的文本，首帧延迟
    走脚本化序列（耗尽后复用最后一个值，与 FakeASRAdapter 刻意不同——TTS
    首帧延迟在真实场景里波动小，复用最后一个值是合理近似；ASR 转写内容
    每轮完全不同，耗尽就该报错提醒轮数算错，见 FakeASRAdapter 文档）。"""

    first_frame_ms_sequence: list[int] = field(default_factory=lambda: [80])
    played: list[str] = field(default_factory=list)

    def synthesize_and_play(self, text: str, *, at_ms: int) -> int:
        self.played.append(text)
        if len(self.first_frame_ms_sequence) > 1:
            return self.first_frame_ms_sequence.pop(0)
        return self.first_frame_ms_sequence[0]


@dataclass
class FakeASRAdapter:
    """脚本化转写序列：worker.py 每调用一次 `transcribe_turn` 消费一条。
    序列耗尽会让 list.pop(0) 抛 `IndexError`——刻意设计，用来在测试里及早
    暴露"轮数算错"的问题，而不是静默复用。"""

    results: list[TranscriptResult] = field(default_factory=list)

    def transcribe_turn(self) -> TranscriptResult:
        return self.results.pop(0)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_voice_host_adapters.py -v`
Expected: PASS（3 项）

- [ ] **Step 6: 写 `worker.py` 测试**

创建 `tests/test_voice_host_worker.py`（LLM 用脚本化假客户端，与 `tests/test_follow_up_selector_agent.py` 同一形状；数据库用语音主机自己的 `queue_store`，不接触 `.51` 的 `app.storage`）：

```python
"""voice_host/worker.py：语音主机 agents worker 核心编排。LLM 用脚本化假
客户端（追问选择），ASR/TTS 用 Fake 适配器，事件队列用真实 voice_host.
queue_store（tmp_path 下的独立 SQLite）。"""
import json

import pytest

from app.llm.gateway import LLMGateway
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion
from voice_host import queue_store
from voice_host.adapters import FakeASRAdapter, FakeTTSAdapter, TranscriptResult
from voice_host.worker import run_session


class ScriptedClient:
    def __init__(self, bodies: list[str]):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 10

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


def _gateway(bodies):
    return LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient(bodies),
    )


@pytest.fixture
def conn(tmp_path):
    connection = queue_store.get_connection(str(tmp_path / "queue.db"))
    queue_store.init_schema(connection)
    return connection


def _bundle():
    return SessionBundle(
        session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
        questions=[
            SessionBundleQuestion(question_id="q1", seq=1, text="讲讲你的 AUTOSAR 项目", follow_ups=["具体分层设计是怎么做的"]),
            SessionBundleQuestion(question_id="q2", seq=2, text="怎么跟团队协作", follow_ups=[]),
        ],
    )


def test_run_session_no_follow_up_two_questions(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    next_question_body = json.dumps({"decision": "next_question"}, ensure_ascii=False)
    gateway = _gateway([next_question_body])

    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年分层开发", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="每周同步进度", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr)

    assert status == "completed"
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["question_id"] == "q1"
    assert events[1]["question_id"] == "q2"
    assert queue_store.get_session_status(conn, session_id="s1") == "completed"


def test_run_session_triggers_one_follow_up(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    follow_up_body = json.dumps({"decision": "follow_up", "follow_up_index": 0}, ensure_ascii=False)
    next_question_body = json.dumps({"decision": "next_question"}, ensure_ascii=False)
    gateway = _gateway([follow_up_body])  # 第 2 题没有预埋追问，不会再调网关

    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),  # 题1
        TranscriptResult(text="分层设计是这样的", confidence=0.88, endpoint_detection_ms=290, asr_ms=145),  # 追问
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),  # 题2
    ])

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr)

    assert status == "completed"
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["question_text"] == "具体分层设计是怎么做的"
    assert events[1]["follow_up_of_seq"] == 1
    assert events[2]["question_id"] == "q2"  # 追问后正确推进到下一题


def test_run_session_records_interrupted_offset(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="被打断前的话", confidence=0.7, endpoint_detection_ms=200, asr_ms=100, interrupted_offset_ms=180),
        TranscriptResult(text="正常回答", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
    ])

    run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr)

    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert events[0]["interrupted_at_ms"] == 180


def test_run_session_falls_back_to_next_question_on_schema_extraction_failure(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    # 三次都返回非法 JSON，网关重试耗尽抛 SchemaExtractionFailed——worker 必须
    # 兜底成"进入下一题"，不能让整场面试卡死（本计划「设计决策 11」）。
    gateway = _gateway(["not json", "still not json", "nope"])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr)

    assert status == "completed"
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]  # 没有触发追问，直接进入下一题


def test_run_session_replays_question_without_counting_as_follow_up(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),  # 候选人说"再说一遍"
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),  # 重播后的真实回答
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr)

    assert status == "completed"
    # 重听不产生新 turn、不计入追问：仍然只有 2 条 turn（两道题各一条）
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["answer_text"] == "做过三年"
    # 题目被播报了两次（首次 + 重听一次）
    assert tts.played.count("讲讲你的 AUTOSAR 项目") == 2


def test_run_session_gives_up_after_max_replay_attempts(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    # 连续 3 次重听请求耗尽 MAX_REPLAY_ATTEMPTS 上限，第 4 次调用返回真实回答
    # 也会被当作最终答案采纳（防御性上限，本计划「设计决策 15」）。
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),
        TranscriptResult(text="仍然是重听请求但已达上限直接采纳", confidence=0.5, endpoint_detection_ms=100, asr_ms=50, replay_requested=True),
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr)

    assert status == "completed"
    assert tts.played.count("讲讲你的 AUTOSAR 项目") == 4  # 1 次首播 + 3 次重听
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert events[0]["answer_text"] == "仍然是重听请求但已达上限直接采纳"
```

- [ ] **Step 7: 运行测试确认失败**

Run: `pytest tests/test_voice_host_worker.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'voice_host.worker'`）

- [ ] **Step 8: 实现 `worker.py`**

创建 `voice_host/worker.py`：

```python
"""语音主机 agents worker 核心编排（voice-structured-interview U4 tasks
5.4/5.5，design D17/D19/D20）。

按题序播报 → 端点检测＋流式转写 → 追问选择（D17 纯函数，越界/异常按下一题
处理）→ 追问或下一题；每轮把 turn 写入本地事件队列（voice_host/queue_store.
py，D19"worker 的输出是 turn 事件队列"）。⛔ 不写任何 `.51` 数据库——本文件
不 import `app.storage`。

打断处理委托给 voice_host/turn_cycle.py 的纯状态机；ASR/TTS 通过
voice_host/adapters.py 的 Protocol 注入。追问选择复用
app.agents.follow_up_selector（生产部署时是 voice_host/_vendor/ 下的物理
拷贝，Task 11 Step 1；本文件的 import 语句写的是 `.51` 仓库路径，因为测试
与生产在同一个 Python 包命名空间下都能解析到——`.51` 检出时解析到
app.agents 包本身，语音主机部署检出时 sync-to-voice-host.sh 只同步了
app/agents/follow_up_selector.py 这一个文件到对应相对路径，import 路径
字面一致）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app.agents.follow_up_selector import FollowUpChoice, select
from app.llm.gateway import LLMGateway, LLMProviderUnavailable, SchemaExtractionFailed
from app.schemas.interview_ai_input import FollowUpInput
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion

from voice_host import queue_store
from voice_host.adapters import ASRAdapter, TTSAdapter
from voice_host.turn_cycle import TurnCycleController


@dataclass
class WorkerTurnResult:
    seq: int
    question_id: str
    question_text: str
    answer_text: str
    answer_mode: str
    audio_start_ms: int | None
    audio_end_ms: int | None
    asr_confidence: float | None
    follow_up_of_seq: int | None
    interrupted_at_ms: int | None
    latency: dict = field(default_factory=dict)


def _clock_ms() -> int:
    return int(time.monotonic() * 1000)


MAX_REPLAY_ATTEMPTS = 3
"""重听不计入追问次数（live-voice-interview-session spec Scenario「候选人
请求重听」），但仍设一个防御性上限——候选人反复触发不应让这一轮问答永远
无法收敛（本计划「设计决策 15」）。"""


def _ask_one_turn(
    *, question_id: str, question_text: str, tts: TTSAdapter, asr: ASRAdapter,
    seq: int, follow_up_of_seq: int | None, is_follow_up: bool,
) -> WorkerTurnResult:
    turn_cycle = TurnCycleController()
    audio_start_ms = _clock_ms()
    turn_cycle.start_broadcast(question_seq=seq, at_ms=audio_start_ms, is_follow_up=is_follow_up)
    tts_first_frame_ms = tts.synthesize_and_play(question_text, at_ms=audio_start_ms)
    transcript = asr.transcribe_turn()

    replay_attempts = 0
    while transcript.replay_requested and replay_attempts < MAX_REPLAY_ATTEMPTS:
        replay_attempts += 1
        turn_cycle = TurnCycleController()
        audio_start_ms = _clock_ms()
        turn_cycle.start_broadcast(question_seq=seq, at_ms=audio_start_ms, is_follow_up=is_follow_up)
        tts_first_frame_ms = tts.synthesize_and_play(question_text, at_ms=audio_start_ms)
        transcript = asr.transcribe_turn()

    if transcript.interrupted_offset_ms is not None:
        turn_cycle.on_candidate_speech_started(at_ms=audio_start_ms + transcript.interrupted_offset_ms)

    audio_end_ms = audio_start_ms + int(transcript.endpoint_detection_ms + transcript.asr_ms)
    turn_cycle.on_broadcast_finished_naturally(at_ms=audio_end_ms)

    end_to_end_ms = transcript.endpoint_detection_ms + transcript.asr_ms + tts_first_frame_ms

    return WorkerTurnResult(
        seq=seq, question_id=question_id, question_text=question_text,
        answer_text=transcript.text, answer_mode="voice",
        audio_start_ms=audio_start_ms, audio_end_ms=audio_end_ms,
        asr_confidence=transcript.confidence, follow_up_of_seq=follow_up_of_seq,
        interrupted_at_ms=turn_cycle.truncated_at_ms,
        latency={
            "endpoint_detection_ms": transcript.endpoint_detection_ms,
            "asr_ms": transcript.asr_ms,
            "tts_first_frame_ms": tts_first_frame_ms,
            "end_to_end_ms": end_to_end_ms,
        },
    )


def _select_follow_up(gateway: LLMGateway, question: SessionBundleQuestion, transcript: str) -> FollowUpChoice:
    """追问选择的异常兜底（本计划「设计决策 11」）：结构化输出重试耗尽
    （`SchemaExtractionFailed`）或供应商不可用（`LLMProviderUnavailable`）
    一律按"进入下一题"处理——语音回路不能因为一次 LLM 调用失败卡死。"""
    follow_up_input = FollowUpInput(
        question_text=question.text, follow_ups=question.follow_ups, transcript=transcript,
    )
    try:
        return select(gateway, follow_up_input)
    except (SchemaExtractionFailed, LLMProviderUnavailable):
        return FollowUpChoice(
            is_follow_up=False, follow_up_index=None, out_of_range=False, run_id="", response_model=None,
        )


def _persist(conn, *, session_id: str, result: WorkerTurnResult) -> None:
    queue_store.append_turn_event(
        conn, session_id=session_id, seq=result.seq, question_id=result.question_id,
        question_text=result.question_text, answer_text=result.answer_text,
        answer_mode=result.answer_mode, audio_start_ms=result.audio_start_ms,
        audio_end_ms=result.audio_end_ms, asr_confidence=result.asr_confidence,
        follow_up_of_seq=result.follow_up_of_seq, interrupted_at_ms=result.interrupted_at_ms,
        latency=result.latency,
    )


def run_session(*, conn, bundle: SessionBundle, gateway: LLMGateway, tts: TTSAdapter, asr: ASRAdapter) -> str:
    """跑完整场次（语音模式）：按 `bundle.questions` 顺序逐题问答，每题结束
    后落一条事件，返回最终状态 `'completed'`。录制接线在 Task 12（修改本
    函数加 `recorder` 参数），文本降级分支在 Task 13（修改 `_ask_one_turn`
    加 `answer_mode` 参数）。"""
    seq = 0
    for question in bundle.questions:
        seq += 1
        result = _ask_one_turn(
            question_id=question.question_id, question_text=question.text,
            tts=tts, asr=asr, seq=seq, follow_up_of_seq=None, is_follow_up=False,
        )
        _persist(conn, session_id=bundle.session_id, result=result)

        last_turn_seq_for_question = seq
        follow_up_count = 0
        while follow_up_count < bundle.follow_up_limit and question.follow_ups:
            selection_start_ms = _clock_ms()
            choice = _select_follow_up(gateway, question, result.answer_text)
            follow_up_selection_ms = _clock_ms() - selection_start_ms

            if not choice.is_follow_up:
                break

            follow_up_count += 1
            seq += 1
            follow_up_text = question.follow_ups[choice.follow_up_index]
            follow_up_result = _ask_one_turn(
                question_id=question.question_id, question_text=follow_up_text,
                tts=tts, asr=asr, seq=seq,
                follow_up_of_seq=last_turn_seq_for_question, is_follow_up=True,
            )
            follow_up_result.latency["follow_up_selection_ms"] = follow_up_selection_ms
            follow_up_result.latency["end_to_end_ms"] += follow_up_selection_ms
            _persist(conn, session_id=bundle.session_id, result=follow_up_result)

            last_turn_seq_for_question = seq
            result = follow_up_result

    queue_store.close_session(conn, session_id=bundle.session_id, status="completed")
    return "completed"
```

- [ ] **Step 9: 运行测试确认通过**

Run: `pytest tests/test_voice_host_worker.py -v`
Expected: PASS（6 项）

- [ ] **Step 10: 补跑结构守卫测试**

Run: `pytest tests/test_voice_host_no_forbidden_imports.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add voice_host/adapters.py voice_host/worker.py voice_host/_vendor/follow_up_selector.py voice_host/_vendor/follow_up_result.py voice_host/_vendor/interview_ai_input.py tests/test_voice_host_adapters.py tests/test_voice_host_worker.py
git commit -m "feat(voice-interview): U4 agents worker 播报/转写/追问接线（design D17）"
```

---

### Task 12: `voice_host/recording.py`（全程录制 + 失败降级 + 回传删除）

**Files:**
- Create: `voice_host/recording.py`
- Modify: `voice_host/worker.py`（`run_session` 增加 `recorder` 参数，包一层录制生命周期）
- Test: `tests/test_voice_host_recording.py`
- Test: `tests/test_voice_host_worker.py`（追加两个用例）

**Interfaces:**
- Produces：`RecordingAdapter` Protocol（`start`/`stop`/`abort`）、`RecordingStartFailed`、`FakeRecordingAdapter`、`finalize_recording(conn, *, session_id, path) -> str`。`run_session` 签名变为 `run_session(*, conn, bundle, gateway, tts, asr, recorder) -> str`（新增必填参数，Task 11 的调用方需要同步更新——本 Task 的测试即覆盖新签名）。

- [ ] **Step 1: 写 `recording.py` 测试**

创建 `tests/test_voice_host_recording.py`：

```python
"""voice_host/recording.py：全程录制生命周期（interview-recording-retention
spec「语音主机不留副本」；live-voice-interview-session spec「全程录制与
turn 对齐」「录制中断」）。"""
import hashlib

import pytest

from voice_host import queue_store
from voice_host.recording import FakeRecordingAdapter, RecordingStartFailed, finalize_recording


@pytest.fixture
def conn(tmp_path):
    connection = queue_store.get_connection(str(tmp_path / "queue.db"))
    queue_store.init_schema(connection)
    queue_store.open_session(connection, session_id="s1", bundle_json="{}")
    return connection


def test_recorder_stop_returns_readable_file(tmp_path):
    recorder = FakeRecordingAdapter(data_dir=tmp_path)
    recorder.start(session_id="s1")
    path = recorder.stop(session_id="s1")
    assert path.exists()
    assert recorder.started == ["s1"]


def test_recorder_fail_on_start_raises(tmp_path):
    recorder = FakeRecordingAdapter(data_dir=tmp_path, fail_on_start=True)
    with pytest.raises(RecordingStartFailed):
        recorder.start(session_id="s1")


def test_finalize_recording_registers_sha256_in_queue_store(conn, tmp_path):
    recorder = FakeRecordingAdapter(data_dir=tmp_path)
    recorder.start(session_id="s1")
    path = recorder.stop(session_id="s1")

    sha256 = finalize_recording(conn, session_id="s1", path=path)

    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha256 == expected
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info["sha256"] == expected
    assert info["path"] == str(path)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_voice_host_recording.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'voice_host.recording'`）

- [ ] **Step 3: 实现 `recording.py`**

创建 `voice_host/recording.py`：

```python
"""全程录制（voice-structured-interview U4 tasks 5.6，interview-recording-
retention spec「语音主机不留副本」，live-voice-interview-session spec「全程
录制与 turn 对齐」「录制中断」）。

真实实现用 LiveKit 的 room composite egress（服务端合成录制，不是 worker
进程自己拼音频字节）——`LiveKitEgressRecordingAdapter` 惰性 import
`livekit.api`，理由与 voice_host/adapters.py 一致。`FakeRecordingAdapter`
供测试与 Task 15 内部模拟使用，把"录制"模拟成往本地文件写一段占位字节。

中间文件 24h 过期清理见 voice_host/queue_store.py::sweep_expired_
intermediate_files（Task 8 已交付，本文件只负责录制开始/结束与"录制失败 ⇒
interrupted"这段判定逻辑，不重复实现清理）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class RecordingStartFailed(Exception):
    """录制未能开始（spec「录制失败 MUST 使场次进入中断待续」）。"""


class RecordingAdapter(Protocol):
    def start(self, *, session_id: str) -> None: ...

    def stop(self, *, session_id: str) -> Path:
        """返回本地录制文件路径；文件必须已经落盘完毕（同步等待，不是
        fire-and-forget）。"""
        ...

    def abort(self, *, session_id: str) -> None: ...


@dataclass
class FakeRecordingAdapter:
    data_dir: Path
    fail_on_start: bool = False
    started: list[str] = field(default_factory=list)

    def start(self, *, session_id: str) -> None:
        if self.fail_on_start:
            raise RecordingStartFailed(f"模拟录制启动失败: {session_id!r}")
        self.started.append(session_id)

    def stop(self, *, session_id: str) -> Path:
        path = Path(self.data_dir) / f"{session_id}.rec"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fake-recording:{session_id}".encode("utf-8"))
        return path

    def abort(self, *, session_id: str) -> None:
        return None


def finalize_recording(conn, *, session_id: str, path: Path) -> str:
    """录制文件落盘后计算哈希、登记进 queue_store（供 GET /sessions/{id}/
    recording 与 DELETE /sessions/{id}/artifacts 使用），返回 sha256
    十六进制串。"""
    from voice_host import queue_store  # 延迟导入避免循环依赖

    content = path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    queue_store.record_recording_file(conn, session_id=session_id, path=str(path), sha256=sha256)
    return sha256
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_voice_host_recording.py -v`
Expected: PASS（3 项）

- [ ] **Step 5: 写 `run_session` 接入录制的测试（追加到 `tests/test_voice_host_worker.py`）**

在 `tests/test_voice_host_worker.py` 顶部导入区追加：

```python
from voice_host.recording import FakeRecordingAdapter, RecordingStartFailed
```

在文件末尾追加：

```python


def test_run_session_finalizes_recording_on_success(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])
    recorder = FakeRecordingAdapter(data_dir=tmp_path)

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr, recorder=recorder)

    assert status == "completed"
    assert recorder.started == ["s1"]
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info is not None


def test_run_session_marks_interrupted_when_recording_fails_to_start(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([])
    tts = FakeTTSAdapter()
    asr = FakeASRAdapter(results=[])
    recorder = FakeRecordingAdapter(data_dir=tmp_path, fail_on_start=True)

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr, recorder=recorder)

    assert status == "interrupted"
    assert queue_store.get_session_status(conn, session_id="s1") == "interrupted"
    assert queue_store.events_since(conn, session_id="s1", since_seq=0) == []
```

- [ ] **Step 6: 运行完整 worker 测试套件确认新用例失败（旧用例因签名变更也会失败，属预期，Step 7 一起修）**

Run: `pytest tests/test_voice_host_worker.py -v`
Expected: FAIL（新用例 `TypeError`：`run_session()` 尚不接受 `recorder`；旧 6 个用例同样因缺少必填参数 `recorder` 报 `TypeError`）

- [ ] **Step 7: 修改 `voice_host/worker.py`：`run_session` 接入录制生命周期**

在 `voice_host/worker.py` 顶部 import 区，`from voice_host.turn_cycle import TurnCycleController` 之后追加：

```python
from voice_host.recording import RecordingAdapter, RecordingStartFailed
```

把 `run_session` 函数整体替换为：

```python
def run_session(
    *, conn, bundle: SessionBundle, gateway: LLMGateway, tts: TTSAdapter, asr: ASRAdapter,
    recorder: RecordingAdapter,
) -> str:
    """跑完整场次（语音模式）：先启动录制（失败 ⇒ 直接 interrupted，不问
    一句），再按 `bundle.questions` 顺序逐题问答，正常结束后停止录制并登记
    哈希。文本降级分支在 Task 13（修改 `_ask_one_turn` 加 `answer_mode`
    参数）。"""
    import logging

    logger = logging.getLogger(__name__)

    try:
        recorder.start(session_id=bundle.session_id)
    except RecordingStartFailed:
        logger.exception("场次 %s 录制启动失败，场次进入 interrupted", bundle.session_id)
        queue_store.close_session(conn, session_id=bundle.session_id, status="interrupted")
        return "interrupted"

    seq = 0
    for question in bundle.questions:
        seq += 1
        result = _ask_one_turn(
            question_id=question.question_id, question_text=question.text,
            tts=tts, asr=asr, seq=seq, follow_up_of_seq=None, is_follow_up=False,
        )
        _persist(conn, session_id=bundle.session_id, result=result)

        last_turn_seq_for_question = seq
        follow_up_count = 0
        while follow_up_count < bundle.follow_up_limit and question.follow_ups:
            selection_start_ms = _clock_ms()
            choice = _select_follow_up(gateway, question, result.answer_text)
            follow_up_selection_ms = _clock_ms() - selection_start_ms

            if not choice.is_follow_up:
                break

            follow_up_count += 1
            seq += 1
            follow_up_text = question.follow_ups[choice.follow_up_index]
            follow_up_result = _ask_one_turn(
                question_id=question.question_id, question_text=follow_up_text,
                tts=tts, asr=asr, seq=seq,
                follow_up_of_seq=last_turn_seq_for_question, is_follow_up=True,
            )
            follow_up_result.latency["follow_up_selection_ms"] = follow_up_selection_ms
            follow_up_result.latency["end_to_end_ms"] += follow_up_selection_ms
            _persist(conn, session_id=bundle.session_id, result=follow_up_result)

            last_turn_seq_for_question = seq
            result = follow_up_result

    path = recorder.stop(session_id=bundle.session_id)
    from voice_host.recording import finalize_recording
    finalize_recording(conn, session_id=bundle.session_id, path=path)

    queue_store.close_session(conn, session_id=bundle.session_id, status="completed")
    return "completed"
```

- [ ] **Step 8: 运行测试确认通过**

Run: `pytest tests/test_voice_host_worker.py -v`
Expected: PASS（8 项）

- [ ] **Step 9: 补跑结构守卫测试**

Run: `pytest tests/test_voice_host_no_forbidden_imports.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add voice_host/recording.py voice_host/worker.py tests/test_voice_host_recording.py tests/test_voice_host_worker.py
git commit -m "feat(voice-interview): U4 全程录制生命周期与失败降级（spec「录制中断」）"
```

---

### Task 13: 候选人答题端 Web + 文本作答降级

**Files:**
- Modify: `voice_host/queue_store.py`（追加 `pending_text_answer` 表与 `submit_text_answer`/`pop_pending_text_answer`）
- Modify: `voice_host/adapters.py`（追加 `TextAnswerAdapter`）
- Modify: `voice_host/worker.py`（`_ask_one_turn` 按 `answer_mode` 分支；`run_session` 每题按当前模式选适配器）
- Modify: `voice_host/api.py`（新增 `POST /sessions/{id}/switch-to-text`、`POST /sessions/{id}/text-answer`）
- Create: `voice_host/web/interview.css`
- Create: `voice_host/web/interview.html`
- Create: `voice_host/web/interview.js`
- Test: `tests/test_voice_host_queue_store.py`（追加）
- Test: `tests/test_voice_host_worker.py`（追加）
- Test: `tests/test_voice_host_api.py`（追加）

**Interfaces:**
- Produces：`queue_store.submit_text_answer(conn, *, session_id, text)`、`queue_store.pop_pending_text_answer(conn, *, session_id) -> str | None`；`adapters.TextAnswerAdapter(conn, session_id, poll_interval_s=0.5, timeout_s=300.0)` 满足 `ASRAdapter` Protocol；`run_session(..., text_adapter: ASRAdapter | None = None)`。

- [ ] **Step 1: 写 `pending_text_answer` 测试（追加到 `tests/test_voice_host_queue_store.py`）**

在文件末尾追加：

```python


def test_submit_and_pop_pending_text_answer(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    assert queue_store.pop_pending_text_answer(conn, session_id="s1") is None
    queue_store.submit_text_answer(conn, session_id="s1", text="我的文字回答")
    assert queue_store.pop_pending_text_answer(conn, session_id="s1") == "我的文字回答"
    assert queue_store.pop_pending_text_answer(conn, session_id="s1") is None  # 消费一次即清空


def test_set_and_get_current_answer_mode(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    assert queue_store.get_current_answer_mode(conn, session_id="s1") == "voice"
    queue_store.set_current_answer_mode(conn, session_id="s1", mode="text")
    assert queue_store.get_current_answer_mode(conn, session_id="s1") == "text"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_voice_host_queue_store.py -v`
Expected: FAIL（`AttributeError: module 'voice_host.queue_store' has no attribute 'submit_text_answer'`）

- [ ] **Step 3: 在 `voice_host/queue_store.py` 里追加 `pending_text_answer` 表与函数**

在 `SCHEMA` 字符串里 `voice_host_event` 建表块之后追加：

```sql

CREATE TABLE IF NOT EXISTS pending_text_answer (
    session_id TEXT PRIMARY KEY NOT NULL REFERENCES session(id),
    text TEXT NOT NULL,
    submitted_at REAL NOT NULL
);
```

在文件末尾追加：

```python


def submit_text_answer(conn: sqlite3.Connection, *, session_id: str, text: str) -> None:
    """候选人切到文本作答后提交一条答案（tasks 5.7）。`ON CONFLICT` 覆盖旧值
    ——同一时刻只有一条"待消费"的文本答案，worker.py 的 `TextAnswerAdapter`
    轮询消费它（`pop_pending_text_answer`）。"""
    conn.execute(
        "INSERT INTO pending_text_answer (session_id, text, submitted_at) VALUES (?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET text = excluded.text, submitted_at = excluded.submitted_at",
        (session_id, text, time.time()),
    )
    conn.commit()


def pop_pending_text_answer(conn: sqlite3.Connection, *, session_id: str) -> str | None:
    row = conn.execute(
        "SELECT text FROM pending_text_answer WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM pending_text_answer WHERE session_id = ?", (session_id,))
    conn.commit()
    return row[0]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_voice_host_queue_store.py -v`
Expected: PASS（9 项）

- [ ] **Step 5: 在 `voice_host/adapters.py` 里追加 `TextAnswerAdapter`**

在文件末尾追加：

```python


@dataclass
class TextAnswerAdapter:
    """文本作答降级的 `ASRAdapter` 实现（tasks 5.7，本计划「设计决策 13」）：
    阻塞轮询本地队列直到候选人通过 `POST /sessions/{id}/text-answer` 提交
    文字答案。`confidence=1.0`（文字没有转写置信度问题）、
    `endpoint_detection_ms=0.0`（不需要端点检测）、`asr_ms` 记的是候选人
    实际打字耗时——这样 `_ask_one_turn` 完全不需要为文本模式另写一套问答
    循环，latency_json 的字段约定（本计划「设计决策 6」）也天然兼容。"""

    conn: object
    session_id: str
    poll_interval_s: float = 0.5
    timeout_s: float = 300.0

    def transcribe_turn(self) -> "TranscriptResult":
        import time as _time

        from voice_host import queue_store

        start = _time.monotonic()
        while True:
            text = queue_store.pop_pending_text_answer(self.conn, session_id=self.session_id)
            if text is not None:
                elapsed_ms = (_time.monotonic() - start) * 1000
                return TranscriptResult(text=text, confidence=1.0, endpoint_detection_ms=0.0, asr_ms=elapsed_ms)
            if _time.monotonic() - start > self.timeout_s:
                raise TimeoutError(f"场次 {self.session_id!r} 等待文本作答超时")
            _time.sleep(self.poll_interval_s)
```

- [ ] **Step 6: 写文本模式的 `_ask_one_turn`/`run_session` 测试（追加到 `tests/test_voice_host_worker.py`）**

在文件末尾追加：

```python


def test_run_session_uses_text_adapter_when_mode_switched_before_question(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    queue_store.set_current_answer_mode(conn, session_id="s1", mode="text")
    queue_store.submit_text_answer(conn, session_id="s1", text="我用文字回答第一题")
    queue_store.submit_text_answer(conn, session_id="s1", text="我用文字回答第二题")

    from voice_host.adapters import TextAnswerAdapter
    from voice_host.recording import FakeRecordingAdapter

    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter()  # 文本模式不应被调用
    asr = FakeASRAdapter(results=[])  # 文本模式不应被调用
    text_adapter = TextAnswerAdapter(conn=conn, session_id="s1", poll_interval_s=0.01, timeout_s=2.0)
    recorder = FakeRecordingAdapter(data_dir="/tmp")

    status = run_session(
        conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
        recorder=recorder, text_adapter=text_adapter,
    )

    assert status == "completed"
    assert tts.played == []
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["answer_mode"] for e in events] == ["text", "text"]
    assert events[0]["audio_start_ms"] is None
    assert events[0]["audio_end_ms"] is None
    assert events[0]["answer_text"] == "我用文字回答第一题"


def test_run_session_supports_mixed_voice_and_text_turns(conn):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    # 第一题语音作答，回答后切到文本，第二题文本作答。
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="语音回答第一题", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
    ])

    from voice_host.adapters import TextAnswerAdapter
    from voice_host.recording import FakeRecordingAdapter

    text_adapter = TextAnswerAdapter(conn=conn, session_id="s1", poll_interval_s=0.01, timeout_s=2.0)
    recorder = FakeRecordingAdapter(data_dir="/tmp")

    def _switch_after_first_question(*args, **kwargs):
        queue_store.set_current_answer_mode(conn, session_id="s1", mode="text")
        queue_store.submit_text_answer(conn, session_id="s1", text="文字回答第二题")

    # 用 monkeypatch 风格的手动 hook：在真实场景里切换发生在候选人点击按钮
    # （Task 13 Step 8 的端点），测试里直接在第一题问答完成的时间点模拟这个
    # 外部动作——这是本单元测试能覆盖"混合模式"场景的唯一现实做法，因为
    # run_session 内部没有测试钩子。
    import voice_host.worker as worker_module

    original_persist = worker_module._persist

    def _patched_persist(conn_, *, session_id, result):
        original_persist(conn_, session_id=session_id, result=result)
        if result.seq == 1:
            _switch_after_first_question()

    worker_module._persist = _patched_persist
    try:
        status = run_session(
            conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
            recorder=recorder, text_adapter=text_adapter,
        )
    finally:
        worker_module._persist = original_persist

    assert status == "completed"
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["answer_mode"] for e in events] == ["voice", "text"]
```

- [ ] **Step 7: 运行测试确认失败**

Run: `pytest tests/test_voice_host_worker.py -v`
Expected: FAIL（`TypeError: run_session() got an unexpected keyword argument 'text_adapter'`）

- [ ] **Step 8: 修改 `voice_host/worker.py`：`_ask_one_turn` 按模式分支，`run_session` 每题查当前模式**

把 `_ask_one_turn` 函数整体替换为：

```python
def _ask_one_turn(
    *, question_id: str, question_text: str, tts: TTSAdapter, asr: ASRAdapter,
    seq: int, follow_up_of_seq: int | None, is_follow_up: bool, answer_mode: str,
) -> WorkerTurnResult:
    if answer_mode == "text":
        transcript = asr.transcribe_turn()  # 这里的 asr 实际是 TextAnswerAdapter，满足同一 Protocol
        return WorkerTurnResult(
            seq=seq, question_id=question_id, question_text=question_text,
            answer_text=transcript.text, answer_mode="text",
            audio_start_ms=None, audio_end_ms=None,
            asr_confidence=transcript.confidence, follow_up_of_seq=follow_up_of_seq,
            interrupted_at_ms=None,
            latency={"end_to_end_ms": transcript.asr_ms},
        )

    turn_cycle = TurnCycleController()
    audio_start_ms = _clock_ms()
    turn_cycle.start_broadcast(question_seq=seq, at_ms=audio_start_ms, is_follow_up=is_follow_up)
    tts_first_frame_ms = tts.synthesize_and_play(question_text, at_ms=audio_start_ms)
    transcript = asr.transcribe_turn()

    replay_attempts = 0
    while transcript.replay_requested and replay_attempts < MAX_REPLAY_ATTEMPTS:
        replay_attempts += 1
        turn_cycle = TurnCycleController()
        audio_start_ms = _clock_ms()
        turn_cycle.start_broadcast(question_seq=seq, at_ms=audio_start_ms, is_follow_up=is_follow_up)
        tts_first_frame_ms = tts.synthesize_and_play(question_text, at_ms=audio_start_ms)
        transcript = asr.transcribe_turn()

    if transcript.interrupted_offset_ms is not None:
        turn_cycle.on_candidate_speech_started(at_ms=audio_start_ms + transcript.interrupted_offset_ms)

    audio_end_ms = audio_start_ms + int(transcript.endpoint_detection_ms + transcript.asr_ms)
    turn_cycle.on_broadcast_finished_naturally(at_ms=audio_end_ms)

    end_to_end_ms = transcript.endpoint_detection_ms + transcript.asr_ms + tts_first_frame_ms

    return WorkerTurnResult(
        seq=seq, question_id=question_id, question_text=question_text,
        answer_text=transcript.text, answer_mode="voice",
        audio_start_ms=audio_start_ms, audio_end_ms=audio_end_ms,
        asr_confidence=transcript.confidence, follow_up_of_seq=follow_up_of_seq,
        interrupted_at_ms=turn_cycle.truncated_at_ms,
        latency={
            "endpoint_detection_ms": transcript.endpoint_detection_ms,
            "asr_ms": transcript.asr_ms,
            "tts_first_frame_ms": tts_first_frame_ms,
            "end_to_end_ms": end_to_end_ms,
        },
    )
```

把 `run_session` 函数整体替换为：

```python
def run_session(
    *, conn, bundle: SessionBundle, gateway: LLMGateway, tts: TTSAdapter, asr: ASRAdapter,
    recorder: RecordingAdapter, text_adapter: ASRAdapter | None = None,
) -> str:
    """跑完整场次：先启动录制（失败 ⇒ 直接 interrupted），再按
    `bundle.questions` 顺序逐题问答——每题开始前查一次
    `queue_store.get_current_answer_mode`（tasks 5.7"同一场次 MUST 允许
    混合"），语音模式用 `asr`、文本模式用 `text_adapter`（两者满足同一
    `ASRAdapter` Protocol，`_ask_one_turn` 不关心具体是哪一种）。"""
    import logging

    logger = logging.getLogger(__name__)

    try:
        recorder.start(session_id=bundle.session_id)
    except RecordingStartFailed:
        logger.exception("场次 %s 录制启动失败，场次进入 interrupted", bundle.session_id)
        queue_store.close_session(conn, session_id=bundle.session_id, status="interrupted")
        return "interrupted"

    seq = 0
    for question in bundle.questions:
        seq += 1
        mode = queue_store.get_current_answer_mode(conn, session_id=bundle.session_id)
        active_asr = text_adapter if (mode == "text" and text_adapter is not None) else asr
        result = _ask_one_turn(
            question_id=question.question_id, question_text=question.text,
            tts=tts, asr=active_asr, seq=seq, follow_up_of_seq=None, is_follow_up=False,
            answer_mode=mode if text_adapter is not None else "voice",
        )
        _persist(conn, session_id=bundle.session_id, result=result)

        last_turn_seq_for_question = seq
        follow_up_count = 0
        while follow_up_count < bundle.follow_up_limit and question.follow_ups:
            selection_start_ms = _clock_ms()
            choice = _select_follow_up(gateway, question, result.answer_text)
            follow_up_selection_ms = _clock_ms() - selection_start_ms

            if not choice.is_follow_up:
                break

            follow_up_count += 1
            seq += 1
            follow_up_text = question.follow_ups[choice.follow_up_index]
            follow_up_mode = queue_store.get_current_answer_mode(conn, session_id=bundle.session_id)
            follow_up_asr = text_adapter if (follow_up_mode == "text" and text_adapter is not None) else asr
            follow_up_result = _ask_one_turn(
                question_id=question.question_id, question_text=follow_up_text,
                tts=tts, asr=follow_up_asr, seq=seq,
                follow_up_of_seq=last_turn_seq_for_question, is_follow_up=True,
                answer_mode=follow_up_mode if text_adapter is not None else "voice",
            )
            follow_up_result.latency["follow_up_selection_ms"] = follow_up_selection_ms
            follow_up_result.latency["end_to_end_ms"] += follow_up_selection_ms
            _persist(conn, session_id=bundle.session_id, result=follow_up_result)

            last_turn_seq_for_question = seq
            result = follow_up_result

    path = recorder.stop(session_id=bundle.session_id)
    from voice_host.recording import finalize_recording
    finalize_recording(conn, session_id=bundle.session_id, path=path)

    queue_store.close_session(conn, session_id=bundle.session_id, status="completed")
    return "completed"
```

- [ ] **Step 9: 运行测试确认通过**

Run: `pytest tests/test_voice_host_worker.py -v`
Expected: PASS（10 项）

- [ ] **Step 10: 写 API 端点测试（追加到 `tests/test_voice_host_api.py`）**

在文件末尾追加：

```python


def test_switch_to_text_records_mode_and_event(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))

    switch_body = b'{"trigger": "manual"}'
    headers = _signed_headers(method="POST", path="/sessions/s1/switch-to-text", body=switch_body)
    response = client.post("/sessions/s1/switch-to-text", content=switch_body, headers=headers)
    assert response.status_code == 200


def test_submit_text_answer_requires_prior_switch(client):
    body = _bundle_body()
    client.post("/sessions", content=body, headers=_signed_headers(method="POST", path="/sessions", body=body))

    answer_body = b'{"text": "我的文字回答"}'
    headers = _signed_headers(method="POST", path="/sessions/s1/text-answer", body=answer_body)
    response = client.post("/sessions/s1/text-answer", content=answer_body, headers=headers)
    assert response.status_code == 200
```

- [ ] **Step 11: 运行测试确认失败**

Run: `pytest tests/test_voice_host_api.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 12: 在 `voice_host/api.py` 追加两个端点**

在 `create_app` 函数内、`return app` 之前追加：

```python
    @app.post("/sessions/{session_id}/switch-to-text")
    async def switch_to_text(session_id: str, request: Request):
        body = await _verify(request)
        import json
        payload = json.loads(body) if body else {}
        trigger = payload.get("trigger", "manual")
        event_type = "mode_switched_after_prompt" if trigger == "after_network_prompt" else "mode_switched_manual"
        queue_store.set_current_answer_mode(conn, session_id=session_id, mode="text")
        queue_store.record_voice_host_event(conn, session_id=session_id, event_type=event_type)
        return {"ok": True}

    @app.post("/sessions/{session_id}/text-answer")
    async def post_text_answer(session_id: str, request: Request):
        body = await _verify(request)
        import json
        payload = json.loads(body)
        queue_store.submit_text_answer(conn, session_id=session_id, text=payload["text"])
        return {"ok": True}
```

- [ ] **Step 13: 运行测试确认通过**

Run: `pytest tests/test_voice_host_api.py -v`
Expected: PASS（9 项）

- [ ] **Step 14: 候选人答题端静态页面**

创建 `voice_host/web/interview.css`：

```css
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 0; background: #f5f6f8; }
#app { max-width: 640px; margin: 0 auto; padding: 24px 16px; }
header h1 { font-size: 20px; margin-bottom: 4px; }
.ai-label { font-size: 12px; color: #888; margin-top: 0; }
#question-panel { background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
#question-text { font-size: 16px; line-height: 1.6; }
#controls { display: flex; gap: 12px; margin-top: 16px; }
button { padding: 8px 16px; border-radius: 6px; border: 1px solid #ccc; background: #fff; cursor: pointer; }
#text-answer-panel { margin-top: 16px; }
#text-answer-input { width: 100%; box-sizing: border-box; padding: 8px; }
#network-quality-prompt { color: #b45309; font-size: 13px; }
```

创建 `voice_host/web/interview.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>AI 结构化面试</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="stylesheet" href="interview.css" />
</head>
<body>
  <main id="app">
    <header>
      <h1>AI 结构化面试</h1>
      <p class="ai-label">本次面试由 AI 主持提问，内容由 AI 生成，仅供参考——人工面试官会复核最终评价。</p>
    </header>

    <section id="question-panel">
      <p id="question-text">正在连接房间…</p>
      <div id="controls">
        <button id="replay-btn" type="button">重听本题</button>
        <button id="switch-text-btn" type="button">改为文字作答</button>
      </div>
      <div id="text-answer-panel" hidden>
        <textarea id="text-answer-input" rows="4" placeholder="请输入你的回答"></textarea>
        <button id="submit-text-btn" type="button">提交</button>
      </div>
      <p id="network-quality-prompt" hidden>检测到网络不稳定，建议切换为文字作答。</p>
    </section>
  </main>
  <script src="vendor/livekit-client.min.js"></script>
  <script src="interview.js"></script>
</body>
</html>
```

创建 `voice_host/web/interview.js`：

```javascript
// 候选人答题端（voice-structured-interview U4 tasks 5.8）。全部用相对路径
// 调用当前 origin 上的语音主机接口——语音主机不挂在任何子路径前缀下（与
// `.51` 的 root_path 约束是两码事：那是 `.51` 自己的部署约束，语音主机是
// 独立域名/IP，本页面天然满足"相对路径"要求，不需要额外处理 root_path）。
// ⛔ 本页面不访问 `.51` 的任何接口（design D12/D19）。

const params = new URLSearchParams(window.location.search);
const sessionId = params.get("session_id");

const questionTextEl = document.getElementById("question-text");
const switchTextBtn = document.getElementById("switch-text-btn");
const textAnswerPanel = document.getElementById("text-answer-panel");
const textAnswerInput = document.getElementById("text-answer-input");
const submitTextBtn = document.getElementById("submit-text-btn");
const replayBtn = document.getElementById("replay-btn");
const networkPromptEl = document.getElementById("network-quality-prompt");

if (!sessionId) {
  questionTextEl.textContent = "缺少场次标识，请通过邀约链接重新进入。";
} else {
  questionTextEl.textContent = "题目将通过语音播报，同时在此同步显示文字（AI 生成内容）。";
}

async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    throw new Error(`请求失败: ${response.status}`);
  }
  return response.json();
}

switchTextBtn.addEventListener("click", async () => {
  await postJson(`/sessions/${sessionId}/switch-to-text`, { trigger: "manual" });
  textAnswerPanel.hidden = false;
});

submitTextBtn.addEventListener("click", async () => {
  const text = textAnswerInput.value.trim();
  if (!text) {
    return;
  }
  await postJson(`/sessions/${sessionId}/text-answer`, { text });
  textAnswerInput.value = "";
});

replayBtn.addEventListener("click", () => {
  // 真实实现：向房间发一条 LiveKit data channel 消息触发 agents worker 侧
  // 的重听逻辑（不计入追问次数，spec Scenario「候选人请求重听」）。本文件
  // 只交付页面骨架与文本降级通道；LiveKit 房间连接与音频播放的接线属于
  // 现场联调范围（0.4 主机到位后），不在本计划的自动化测试范围内。
  console.log("重听请求已发送（占位：真实实现走 LiveKit data channel）");
});
```

- [ ] **Step 15: 补跑结构守卫测试**

Run: `pytest tests/test_voice_host_no_forbidden_imports.py -v`
Expected: PASS

- [ ] **Step 16: Commit**

```bash
git add voice_host/queue_store.py voice_host/adapters.py voice_host/worker.py voice_host/api.py voice_host/web/interview.css voice_host/web/interview.html voice_host/web/interview.js tests/test_voice_host_queue_store.py tests/test_voice_host_worker.py tests/test_voice_host_api.py
git commit -m "feat(voice-interview): U4 候选人答题端 + 文本作答降级（spec「文本作答降级」）"
```

---

### Task 14: `scripts/provision_voice_host.sh` 幂等安装脚本 + `sync-to-voice-host.sh` 部署白名单

**Files:**
- Create: `scripts/provision_voice_host.sh`
- Create: `sync-to-voice-host.sh`
- Create: `voice_host/requirements.txt`
- Create: `voice_host/requirements-cosyvoice.txt`

**Interfaces:**
- 无 Python 接口——两个脚本是独立可执行文件。`sync-to-voice-host.sh` 的白名单是 Task 2/3/4/5 里"设计决策 2"承诺过的那份拷贝清单，本 Task 是唯一真正把它写成可执行脚本的地方（Task 9/11 的手工 `cp` 步骤是开发期种子，本脚本是生产期每次部署都要跑的路径）。

- [ ] **Step 1: 写语音主机依赖清单**

创建 `voice_host/requirements.txt`（主 venv，`livekit-agents` 优先 Python 3.14，回落 3.12，见 `docs/m3-voice-probe.md` P5）：

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.13.4
httpx==0.28.1
openai==1.59.6
# funasr==1.4.15 已在 docs/m3-voice-probe.md P2 确认可导入（dev-machine，
# 无 GPU）；livekit-agents 版本未经探针钉死，装最新版并在下一次 P2/P3 重跑
# 时把实测版本回填本文件——⛔ 不在此处伪造一个未经验证的具体版本号。
funasr==1.4.15
livekit
livekit-agents
```

创建 `voice_host/requirements-cosyvoice.txt`（独立 venv，本计划「设计决策 10」；沿用 `docs/m3-voice-probe-cosyvoice-install.md` 记录的真实安装路径，含已确认的 `pkg_resources` 修复）：

```
# CosyVoice 官方仓库无 PyPI 发行版，本清单只声明修复已知阻塞需要的前置包；
# 真正的依赖树由 CosyVoice 仓库自带的 requirements.txt 提供，见
# scripts/provision_voice_host.sh 的安装步骤。
setuptools<81
wheel
```

- [ ] **Step 2: 写 `provision_voice_host.sh`（幂等安装，无害预检）**

创建 `scripts/provision_voice_host.sh`：

```bash
#!/usr/bin/env bash
#
# 语音主机幂等安装脚本（voice-structured-interview U4 tasks 5.1，design
# D12）。目标：Linux 云主机（具体规格由 docs/m3-voice-probe.md「目标机规格
# 建议」定，采购由 0.4 完成后填入）。
#
# 幂等：已安装的组件跳过重装（用可执行文件存在/版本匹配判断），可安全重跑。
# 无害预检：--precheck-only 只跑检查、不改动任何系统状态，用于在真正安装前
# 确认目标机满足前提（端口空闲/磁盘空间/Python 版本/网络可达 GitHub 与
# PyPI 镜像）。
#
# ⚠️ P2/P3（docs/m3-voice-probe.md）当前阻塞：FunASR 缺 30s 中文样本音频、
# CosyVoice 缺 hyperpyyaml 依赖。本脚本按已确认可行的安装路径实现，
# 实际执行仍需先解决这两处阻塞（本计划前置状态段已如实记录）。
set -euo pipefail

VOICE_HOST_DIR="$(cd "$(dirname "$0")/.." && pwd)/voice_host"
LIVEKIT_VERSION="${LIVEKIT_VERSION:-}"  # 留空 = 装最新 release，见 Step 3 说明
COSYVOICE_REPO_DIR="${COSYVOICE_REPO_DIR:-$VOICE_HOST_DIR/CosyVoice}"
PRECHECK_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --precheck-only) PRECHECK_ONLY=1 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

log() { echo "[provision_voice_host] $*"; }

# ── 无害预检：任何一项不满足都在这里退出，不做任何安装动作 ──────────────
precheck() {
    local failed=0

    for port in 7880 7881 3478 8090; do
        if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :$port )" | grep -q "$port"; then
            echo "预检失败：端口 $port 已被占用" >&2
            failed=1
        fi
    done

    local free_kb
    free_kb=$(df -Pk "$VOICE_HOST_DIR/.." 2>/dev/null | tail -1 | awk '{print $4}')
    if [ -n "${free_kb:-}" ] && [ "$free_kb" -lt 20971520 ]; then  # 20GB
        echo "预检失败：可用磁盘空间不足 20GB（当前 ${free_kb}KB）" >&2
        failed=1
    fi

    if ! command -v python3.14 >/dev/null 2>&1 && ! command -v python3.12 >/dev/null 2>&1; then
        echo "预检失败：未找到 python3.14 或 python3.12（docs/m3-voice-probe.md P5 结论：优先 3.14，回落 3.12）" >&2
        failed=1
    fi

    if ! command -v git >/dev/null 2>&1; then
        echo "预检失败：未找到 git（CosyVoice 需要 git clone）" >&2
        failed=1
    fi

    if ! curl -sI --max-time 5 https://github.com >/dev/null 2>&1; then
        echo "预检失败：无法访问 https://github.com（CosyVoice 仓库克隆需要）" >&2
        failed=1
    fi

    if [ "$failed" -ne 0 ]; then
        echo "预检未通过，退出（无害——本次调用没有做任何安装动作）" >&2
        exit 1
    fi
    log "预检通过"
}

precheck
if [ "$PRECHECK_ONLY" -eq 1 ]; then
    log "--precheck-only：只跑预检，退出"
    exit 0
fi

# ── Step A: LiveKit server 二进制 ────────────────────────────────────
install_livekit_server() {
    if command -v livekit-server >/dev/null 2>&1; then
        log "livekit-server 已安装，跳过（$(livekit-server --version 2>&1 | head -1)）"
        return
    fi
    log "安装 livekit-server（官方安装脚本，LIVEKIT_VERSION=${LIVEKIT_VERSION:-latest}）"
    curl -sSL https://get.livekit.io | bash
}

# ── Step B: 主 venv（FastAPI/livekit-agents/FunASR）────────────────────
install_main_venv() {
    local venv_dir="$VOICE_HOST_DIR/.venv"
    local python_bin
    python_bin="$(command -v python3.14 || command -v python3.12)"

    if [ -x "$venv_dir/bin/python" ]; then
        log "主 venv 已存在，跳过创建（$venv_dir）"
    else
        log "创建主 venv（$python_bin）"
        "$python_bin" -m venv "$venv_dir"
    fi
    "$venv_dir/bin/pip" install --upgrade pip
    "$venv_dir/bin/pip" install -r "$VOICE_HOST_DIR/requirements.txt"
}

# ── Step C: CosyVoice 独立 venv（本计划「设计决策 10」）─────────────────
install_cosyvoice_venv() {
    local venv_dir="$VOICE_HOST_DIR/.venv-cosyvoice"
    local python_bin
    python_bin="$(command -v python3.10 || command -v python3.12)"

    if [ ! -d "$COSYVOICE_REPO_DIR" ]; then
        log "克隆 CosyVoice 仓库"
        git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "$COSYVOICE_REPO_DIR"
        (cd "$COSYVOICE_REPO_DIR" && git submodule update --init --recursive)
    else
        log "CosyVoice 仓库已存在，跳过克隆"
    fi

    if [ -x "$venv_dir/bin/python" ]; then
        log "CosyVoice venv 已存在，跳过创建（$venv_dir）"
    else
        log "创建 CosyVoice venv（$python_bin）"
        "$python_bin" -m venv "$venv_dir"
    fi

    # 已确认的真实阻塞修复（docs/m3-voice-probe-cosyvoice-install.md）：
    # grpcio==1.57.0 的 setup.py 硬依赖 pkg_resources，现代 pip 构建隔离不
    # 自动带它，必须先装旧版 setuptools 补回 pkg_resources。
    "$venv_dir/bin/pip" install -r "$VOICE_HOST_DIR/requirements-cosyvoice.txt"
    "$venv_dir/bin/pip" install -r "$COSYVOICE_REPO_DIR/requirements.txt" \
        -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
}

# ── Step D: 环境变量模板（⛔ 不覆盖已存在的 .env，保护已配置的密钥）───────
write_env_template() {
    local env_file="$VOICE_HOST_DIR/.env"
    if [ -f "$env_file" ]; then
        log ".env 已存在，跳过（不覆盖已配置的密钥）"
        return
    fi
    log "写入 .env 模板（占位密钥，部署时必须手工替换）"
    cat > "$env_file" <<'EOF'
VOICE_HOST_SHARED_SECRET=REPLACE_ME_BEFORE_FIRST_START
VOICE_HOST_DATA_DIR=voice_host_data
VOICE_HOST_PORT=8090
EOF
    chmod 600 "$env_file"
}

install_livekit_server
install_main_venv
install_cosyvoice_venv
write_env_template

log "安装完成。启动前请确认 $VOICE_HOST_DIR/.env 里的 VOICE_HOST_SHARED_SECRET 已替换为真实密钥。"
```

- [ ] **Step 3: 语法检查**

Run: `bash -n scripts/provision_voice_host.sh`
Expected: 无输出，退出码 0

- [ ] **Step 4: 本机跑一次 `--precheck-only` 确认预检逻辑可执行（不要求通过——本机不是目标 Linux 主机）**

Run: `bash scripts/provision_voice_host.sh --precheck-only; echo "exit=$?"`
Expected: 输出若干条"预检失败：…"（本机大概率缺 `python3.14`/`python3.12` 或其他条件，取决于本机实际环境），`exit=1`——这是预期行为，不是脚本 bug：本机不是目标机，预检理应不通过；关键是脚本没有崩溃、没有做任何安装动作。

- [ ] **Step 5: 写 `sync-to-voice-host.sh`**

创建 `sync-to-voice-host.sh`（与仓库根 `sync-to-server.sh` 同一形态：白名单而非黑名单，`rsync -avR` 保留相对路径结构）：

```bash
#!/usr/bin/env bash
#
# 把语音主机需要的代码同步到语音主机（voice-structured-interview U4，本计划
# 「设计决策 2」）。白名单包含两类：① voice_host/ 整个目录（语音主机自己的
# 代码）；② `.51` 仓库里被 voice_host/ 依赖、且必须与 `.51` 侧保持同源的
# 单文件契约（签名算法、schema、follow_up_selector、LLM 网关）。
#
# 用法：
#   ./sync-to-voice-host.sh                          # 用默认值
#   VOICE_HOST_SERVER=voicehost1 ./sync-to-voice-host.sh   # 用 ~/.ssh/config 里的别名
set -euo pipefail

export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

VOICE_HOST_SERVER="${VOICE_HOST_SERVER:-voicehost1}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/zhuopin-voice-host}"

cd "$(dirname "$0")"

SYNC_PATHS=(
    "voice_host"
    "app/live_voice/signing.py"
    "app/agents/follow_up_selector.py"
    "app/schemas/follow_up_result.py"
    "app/schemas/interview_ai_input.py"
    "app/schemas/session_bundle.py"
    "app/schemas/live_turn_event.py"
    "app/llm/gateway.py"
    "scripts/provision_voice_host.sh"
)

# 这些子目录即使落在白名单路径里也不推：本地 venv、CosyVoice 权重仓库、
# 运行时队列数据库、密钥文件。
EXCLUDES=(
    "--exclude=voice_host/.venv"
    "--exclude=voice_host/.venv-cosyvoice"
    "--exclude=voice_host/CosyVoice"
    "--exclude=voice_host/.env"
    "--exclude=voice_host_data"
    "--exclude=__pycache__"
)

rsync -avR "${EXCLUDES[@]}" "${SYNC_PATHS[@]}" "$VOICE_HOST_SERVER:$REMOTE_APP_DIR/"

echo "同步完成。语音主机侧需要手工重启服务使代码生效（见 provision_voice_host.sh 顶部说明，本脚本不代管进程生命周期）。"
```

- [ ] **Step 6: 语法检查**

Run: `bash -n sync-to-voice-host.sh`
Expected: 无输出，退出码 0

- [ ] **Step 7: 跑一次结构守卫测试确认白名单文件都真实存在**

Run: `pytest tests/test_voice_host_no_forbidden_imports.py -v`
Expected: PASS（本步骤间接确认 Task 2/3/4/9/11 交付的文件路径与本脚本白名单字面一致，任何路径打字错误会在人工核对阶段被发现——⚠️ 这个测试本身不校验 `sync-to-voice-host.sh` 的白名单内容，人工核对是唯一防线，登记为已知限制）

- [ ] **Step 8: Commit**

```bash
chmod +x scripts/provision_voice_host.sh sync-to-voice-host.sh
git add scripts/provision_voice_host.sh sync-to-voice-host.sh voice_host/requirements.txt voice_host/requirements-cosyvoice.txt
git commit -m "feat(voice-interview): U4 语音主机幂等安装脚本与部署同步白名单"
```

---

### Task 15: live e2e（内部模拟场次，Fake 适配器）

**⚠️ 范围声明**：本 e2e 用 Fake ASR/TTS/录制适配器在同一进程内模拟"两台机器"（`.51` 侧用真实 `app.storage.db`，语音主机侧用真实 `voice_host.queue_store`，两者之间用一个桥接类直接互调 Python 函数，不走真实 HTTP/HMAC——那部分的正确性已由 Task 4/9 的 `httpx.MockTransport`/`TestClient` 测试覆盖）。真实音频、真实网络往返、真实 LiveKit/FunASR/CosyVoice 联调是 tasks 5.11（现场）与 8.7（内部模拟批次验收）的范围，不在本计划自动化测试之内——这是「前置状态」段已如实记录的 P2/P3 阻塞的直接后果。

**Files:**
- Test: `tests/test_voice_interview_u4_live_e2e.py`

**Interfaces:**
- 无新生产代码，纯集成测试，串联 Task 3-13 已交付的全部函数。

- [ ] **Step 1: 写 e2e 测试**

创建 `tests/test_voice_interview_u4_live_e2e.py`：

```python
"""U4 live e2e（内部模拟场次，Fake 适配器）。串联：U3 邀约与同意（已交付）
→ compute_session_bundle → 语音主机 worker.run_session（含 1 次打断 1 次
追问）→ .51 侧 run_live_session_sync 轮询拉取 → 断言每维可回指、录音落盘、
语音主机无残留、`rejection_record`/`application.current_stage_id` 不变
（合规红线）。"""
import json

import pytest

from app.graph.invite_nodes import (
    compute_new_invite_token,
    compute_new_session,
    effect_create_interview_session,
    effect_issue_invite,
    effect_record_consent,
    issue_verification_code,
    open_invite,
    verify_phone_code,
)
from app.graph.live_session_nodes import compute_session_bundle, run_live_session_sync
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema

from voice_host import queue_store as vh_queue_store
from voice_host.adapters import FakeASRAdapter, FakeTTSAdapter, TranscriptResult
from voice_host.recording import FakeRecordingAdapter
from voice_host.worker import run_session as vh_run_session


class ScriptedClient:
    def __init__(self, bodies: list[str]):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 10

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


def _gateway(bodies):
    return LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient(bodies),
    )


class InProcessBridgeClient:
    """把 `.51` 侧的 `VoiceHostClient` 接口直接接到语音主机的
    `voice_host.queue_store`，跳过真实 HTTP/HMAC（本 Task 的范围声明）。
    `create_session` 在这个桥接里直接触发 `voice_host.worker.run_session`
    跑完整场次——真实部署中这一步是异步的（POST /sessions 立即返回、worker
    在后台跑），e2e 用同步调用简化时序，不影响所验证的编排逻辑正确性。
    """

    def __init__(self, *, vh_conn, gateway, tts, asr, recorder):
        self.vh_conn = vh_conn
        self.gateway = gateway
        self.tts = tts
        self.asr = asr
        self.recorder = recorder

    def create_session(self, bundle):
        vh_queue_store.open_session(self.vh_conn, session_id=bundle.session_id, bundle_json=bundle.model_dump_json())
        vh_run_session(
            conn=self.vh_conn, bundle=bundle, gateway=self.gateway,
            tts=self.tts, asr=self.asr, recorder=self.recorder,
        )
        return {"accepted": True}

    def poll_events(self, session_id, *, since):
        from app.schemas.live_turn_event import LiveEventsPollResponse, LiveTurnEvent

        events = vh_queue_store.events_since(self.vh_conn, session_id=session_id, since_seq=since)
        status = vh_queue_store.get_session_status(self.vh_conn, session_id=session_id)
        return LiveEventsPollResponse(
            events=[LiveTurnEvent(**e) for e in events], session_status=status,
        )

    def fetch_recording(self, session_id):
        info = vh_queue_store.get_recording_file(self.vh_conn, session_id=session_id)
        with open(info["path"], "rb") as fh:
            content = fh.read()
        return content, info["sha256"]

    def notify_delete_artifacts(self, session_id):
        from pathlib import Path

        info = vh_queue_store.get_recording_file(self.vh_conn, session_id=session_id)
        if info is not None:
            Path(info["path"]).unlink(missing_ok=True)
            vh_queue_store.mark_recording_transferred(self.vh_conn, session_id=session_id)


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "main.db"))
    init_schema(connection)
    return connection


@pytest.fixture
def vh_conn(tmp_path):
    connection = vh_queue_store.get_connection(str(tmp_path / "voice_host_queue.db"))
    vh_queue_store.init_schema(connection)
    return connection


def _seed_frozen_snapshot(conn, *, job_id="job-1", application_id="app-1"):
    conn.execute("INSERT INTO job (id, title) VALUES (?, '嵌入式软件工程师')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')", (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')", (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, input_hash, raw_response) "
        "VALUES ('run-prep', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', ?, 1, 1, 'run-prep', 'frozen')", (application_id,),
    )
    questions = [
        ("q1", 1, "AUTOSAR CP", "讲讲你的项目经验", '["能展开讲讲分层设计吗"]'),
        ("q2", 2, "C 语言", "讲讲内存管理", "[]"),
        ("q3", 3, "沟通表达", "怎么跟团队协作", "[]"),
        ("q4", 4, "调试能力", "讲个排查过的疑难 bug", "[]"),
        ("q5", 5, "职业规划", "未来三年打算", "[]"),
    ]
    for qid, seq, dim, text, follow_ups_json in questions:
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
            "rubric_json, follow_ups_json, rationale) VALUES (?, 'snap-1', ?, ?, 'medium', ?, '{}', ?, 'r')",
            (qid, seq, dim, text, follow_ups_json),
        )
    conn.commit()
    return application_id


def _issue_and_ready_session(conn, *, application_id, job_id="job-1"):
    session = compute_new_session(conn, application_id=application_id, prep_snapshot_version=1, sample_class="internal_sim")
    session_id = effect_create_interview_session(
        conn, thread_id=session["id"], business_key="create", session=session,
    )
    token, token_hash, expires_at = compute_new_invite_token(conn, job_id=job_id)
    effect_issue_invite(
        conn, thread_id=session_id, business_key=token_hash,
        session_id=session_id, token_hash=token_hash, expires_at=expires_at,
    )
    open_invite(conn, token)

    effect_record_consent(
        conn, thread_id=session_id, business_key="ai_interview:v1",
        session_id=session_id, kind="ai_interview", result="accepted", version="v1",
    )
    effect_record_consent(
        conn, thread_id=session_id, business_key="identity_check:v1",
        session_id=session_id, kind="identity_check", result="accepted", version="v1",
    )

    code = issue_verification_code(conn, session_id=session_id)
    verify_phone_code(conn, session_id=session_id, submitted_code=code)

    return session_id


def test_live_e2e_internal_simulation_with_interrupt_and_follow_up(conn, vh_conn, tmp_path):
    application_id = _seed_frozen_snapshot(conn)
    session_id = _issue_and_ready_session(conn, application_id=application_id)

    bundle = compute_session_bundle(conn, session_id=session_id)
    assert len(bundle.questions) == 5

    gateway = _gateway([json.dumps({"decision": "follow_up", "follow_up_index": 0}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年分层开发", confidence=0.9, endpoint_detection_ms=300, asr_ms=150, interrupted_offset_ms=180),  # 题1：含打断
        TranscriptResult(text="分层设计细节回答", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),  # 题1追问
        TranscriptResult(text="内存管理回答", confidence=0.92, endpoint_detection_ms=310, asr_ms=160),  # 题2
        TranscriptResult(text="团队协作回答", confidence=0.88, endpoint_detection_ms=290, asr_ms=145),  # 题3
        TranscriptResult(text="调试经历回答", confidence=0.91, endpoint_detection_ms=305, asr_ms=155),  # 题4
        TranscriptResult(text="职业规划回答", confidence=0.87, endpoint_detection_ms=295, asr_ms=148),  # 题5
    ])
    recorder = FakeRecordingAdapter(data_dir=tmp_path / "vh_recordings")

    client = InProcessBridgeClient(vh_conn=vh_conn, gateway=gateway, tts=tts, asr=asr, recorder=recorder)

    result = run_live_session_sync(
        conn, session_id=session_id, client=client, recording_dir=str(tmp_path / "main_recordings"),
    )

    assert result == "completed"

    turns = conn.execute(
        "SELECT seq, interrupted_at_ms, follow_up_of, answer_mode FROM interview_turn "
        "WHERE session_id = ? ORDER BY seq", (session_id,),
    ).fetchall()
    assert [t[0] for t in turns] == [1, 2, 3, 4, 5, 6]
    assert turns[0][1] == 180  # 第一题被打断，截断点被记录
    assert turns[1][2] is not None  # 第二条 turn 是追问，follow_up_of 指向第一题
    assert all(t[3] == "voice" for t in turns)

    session_row = conn.execute(
        "SELECT status, recording_uri, recording_sha256 FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    assert session_row[0] == "completed"
    assert session_row[1] is not None
    assert session_row[2] is not None

    # 语音主机无残留（interview-recording-retention spec「语音主机不留副本」）
    vh_recording_info = vh_queue_store.get_recording_file(vh_conn, session_id=session_id)
    assert vh_recording_info["transferred"] is True
    import os
    assert not os.path.exists(vh_recording_info["path"])

    # 合规红线：本单元不触发淘汰、不改动阶段流转
    stage = conn.execute("SELECT current_stage_id FROM application WHERE id = ?", (application_id,)).fetchone()[0]
    assert stage == "initial"
    rejection_count = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    assert rejection_count == 0
```

- [ ] **Step 2: 运行测试确认通过**

Run: `pytest tests/test_voice_interview_u4_live_e2e.py -v`
Expected: PASS（1 项）

- [ ] **Step 3: 跑本单元完整测试套件确认无回归**

Run: `pytest tests/test_session_bundle_schema.py tests/test_live_voice_signing.py tests/test_follow_up_result_schema.py tests/test_follow_up_selector_agent.py tests/test_live_turn_event_schema.py tests/test_live_voice_client.py tests/test_db_m3_schema.py tests/test_live_session_nodes.py tests/test_run_interview_live_sync_script.py tests/test_report_m3_latency.py tests/test_voice_host_queue_store.py tests/test_voice_host_api.py tests/test_voice_host_no_forbidden_imports.py tests/test_voice_host_turn_cycle.py tests/test_voice_host_adapters.py tests/test_voice_host_worker.py tests/test_voice_host_recording.py tests/test_voice_interview_u4_live_e2e.py -v`
Expected: PASS 全部

- [ ] **Step 4: Commit**

```bash
git add tests/test_voice_interview_u4_live_e2e.py
git commit -m "test(voice-interview): U4 live e2e（内部模拟，Fake 适配器，含打断与追问）"
```

---

## 交付后回勾

按 `03-工具链协作规则.md` 的 checkbox 判据，本计划全部 Task 完成并通过 `run-build` 的两阶段 review 后，回勾 `openspec/changes/voice-structured-interview/tasks.md` 第 5 章 5.1–5.11。5.1/5.11（真实安装、目标机联调）与 tasks.md 0.4/0.3 的依赖关系保持不变——代码与自动化测试完成不等同于"可对真实候选人开放"，8.7/8.9 的现场验收与开闸决定仍按原有不可代项流程走。

