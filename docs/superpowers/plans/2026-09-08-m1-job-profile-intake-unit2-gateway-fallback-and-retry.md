# M1 交付单元 2 · LLM 网关：双供应商切换与重试转人工 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 `m1-job-profile-intake` WBS 的两条：**2.3** 主供应商失败自动切备用、切换事件记入 `analysis_run`；**2.5** schema 校验失败重试至多 2 次、仍失败把岗位转 `needs_manual` 且**不产出半成品**。

**Architecture:** 网关侧把"一家供应商"抽成 `_Provider`（client + model + supports_json_schema），`extract_structured_with_meta` 的重试循环拆成**两个互不干扰的预算**——schema 校验失败消耗 `max_retries`（在同一家上重试），供应商传输层故障触发**至多一次**切换（切换事件作为 `analysis_run` 的一行留痕，⛔ 不新建表、不加列、不改 `AuditHook` 签名）。编排侧，`compute_intake_turn` 把两类终局异常翻译成 state 上的 `needs_manual` 信号（纯函数，⛔ 不写库），`app/graph/build.py` 用一条**条件边**把这一轮导向新文件 `app/graph/manual_handoff.py` 里的两个 `effect_*` 节点（置状态 / 投递消息，各带幂等键），**完全绕开 `effect_persist_draft`**——"不产出半成品"这句话在图上的形状就是"这一轮不落 `job_profile` 行"。

**Tech Stack:** Python 3.14（`requires-python = ">=3.14,<3.15"`，与 .51 部署环境严格对齐）· openai==1.59.6（已在 `requirements.txt`，⛔ 本单元一行不加）· pydantic-settings · LangGraph 1.0.10 · SQLite（`app/storage/db.py`）· pytest（`venv/bin/python -m pytest`）

---

## Global Constraints

以下每一条对**每个** Task 都成立，reviewer 按这一段逐条看。第 1–6 条从 `CLAUDE.md` 的「工程铁律」「合规红线」「部署约束」逐字复制，第 7–15 条是本交付单元的边界。

1. **（工程铁律 1）LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。**
2. **（工程铁律 2）L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **（工程铁律 3）所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
4. **（工程铁律 5）`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   **本单元的落点：切到备用供应商之后，`analysis_run` 记的 `response_model` 必须是备用方响应返回的 `model`，`configured_model` 必须是备用方的配置名。⛔ 记主供应商的名字等于让留痕撒谎。** 备用供应商的模型名同样禁止 `latest` 类别名。
5. **（合规红线）AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
   **本单元的落点：`needs_manual` 是"请人来看"，⛔ 不是淘汰、⛔ 不改变任何候选人或岗位的实质结论；转人工是系统判定，⛔ 绝不许往 `human_review` 写行（那张表的决策人只能是人）。**
6. **（合规红线）模型全部走境内**，简历数据不出境。**备用供应商一视同仁**——这一条代码校验不了（`base_url` 是自由字符串），配置默认值必须为空，由 `.env` 评审把关。
7. **（合规红线）AI 生成的 JD、拒信、邀约须带标识。** 转人工下发的文案是**系统写死的常量字符串、不是模型产出**，⛔ 不需要也不得贴 AI 生成标识；反过来，⛔ 也不许把任何模型的部分输出塞进这条消息。
8. **（部署约束 2/4）目标服务器是 Windows，没有 Docker。** ⛔ 不引入任何新的第三方运行时依赖：`requirements.txt` **一行不改**。备用供应商复用已有的 `openai` SDK。
9. **备用供应商配置全部走 `Settings` 字段且有默认值，不配 = 无备用、行为与今天逐字一致。** ⛔ 默认值不得指向任何真实供应商。可改的部署产物只有 `.env.example`；⛔ 不碰 `sync-to-server.sh` / `deploy-server.ps1` / 计划任务 / 防火墙 / 任何其他部署产物。
10. **切换事件是 `analysis_run` 的一行（或其字段），⛔ 不另造日志表、⛔ 不给 `analysis_run` 加列、⛔ 不改 `AuditHook` 的签名**（`app/llm/gateway.py` 的 `LLMCallMeta` docstring 逐字写着签名不能动，`ai-audit-trail-and-outbound-gate` 正基于现签名设计）。
11. **切换判据写死在代码里，⛔ 不做可配置**：HTTP 5xx / 超时 / 连接错误 → 切；**4xx 与 schema 校验失败 → ⛔ 不切**（前者是请求本身的问题，换一家照样错；后者是内容问题，不是供应商问题）。
12. **2.5「不产出半成品」的判据**：重试耗尽后 `profile_patch` 不写入、`job_profile` **不落新版本**（这一轮 `SELECT COUNT(*) FROM job_profile WHERE job_id=?` 不增加），只在 state 落 `needs_manual` 信号与原因，并由 `effect_*` 节点把 `job.status` 置为 `needs_manual`。⛔ 不把部分解析结果当结果。
13. **⛔ 不碰 `app/agents/`、`app/web/`、`app/audit/`、`app/outbound/`。** `app/graph/nodes.py` 只改**两处**：import 行、以及 `compute_intake_turn` 里"提取异常 → state 信号"那一段。两个新的 `effect_*` 节点建在**新文件** `app/graph/manual_handoff.py`——既守住这条边界，也避开与并行泳道的合并冲突。
    **并行同伴：识别泳道在改 `app/agents/intake_agent.py` 与 `app/web/server.py`，幂等泳道只加测试。** `git status` 里出现它们的改动是正常的，⛔ 不要停下、不要顺手提交。
14. **⛔ 不改 `app/web/server.py`，只保证 state 里落下的信号能被它读到。** 具体两处对接（都不需要改它）：
    - `app/storage/job_queries.py:224` 的 `derive_needs_manual_reasons` 第 1 个来源 `job.status = 'needs_manual'`，原注释写着"今天恒为空，**2.5 落地当天自动生效**"——本单元就是那一天。
    - `app/web/server.py:194` 的 `_run_turn` 结尾**无条件**读 `channel.latest(job_id).type`。转人工路径**必须投递一条消息**，否则它拿到 `None` 当场 `AttributeError`，一次可恢复的转人工变成 500。
15. **⛔ 不进 run-build 的范围外改动**：不改 `requirements.txt`、不改 CI、不改 `openspec/` 下任何文件（WBS 回勾由 run-build 收尾时统一做）。

---

## File Structure

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `app/config.py` | 修改 | 备用供应商四个 `Settings` 字段 + `validate_model_version()` 对备用模型的 latest 校验 |
| `.env.example` | 修改（追加一段） | 备用供应商四项的示例与合规提示 |
| `app/llm/gateway.py` | 修改 | `LLMProviderUnavailable`、`_Provider`、`_is_switchable`、`_build_fallback`、重试/切换循环、`_record_provider_switch` |
| `app/main.py` | 修改 | `_gateway_factory()` 把四项配置接进网关（U3 注入点，⛔ 不改 `create_app` 签名） |
| `app/graph/manual_handoff.py` | **新建** | `REASON_*` 原因码、中文文案、两个 `effect_*` 节点 |
| `app/graph/state.py` | 修改（追加两键） | `needs_manual` / `needs_manual_reason_code` |
| `app/graph/nodes.py` | 修改（**两处**） | import 行；`compute_intake_turn` 的异常 → 信号分支 |
| `app/graph/build.py` | 修改 | 两个新节点 + 一条条件边 |
| `tests/test_config_fallback.py` | 新建 | Task 1 |
| `tests/test_llm_gateway_fallback.py` | 新建 | Task 2 |
| `tests/test_main_fallback_wiring.py` | 新建 | Task 3 |
| `tests/test_manual_handoff.py` | 新建 | Task 4 |
| `tests/test_intake_needs_manual.py` | 新建 | Task 5 / Task 6 |

依赖方向：Task 1 → Task 2 → Task 3（网关侧一条线）；Task 4 → Task 5 → Task 6（编排侧一条线）。Task 5 需要 Task 2 的 `LLMProviderUnavailable` 与 Task 4 的 `REASON_*` 常量都已存在。**⛔ 不要打乱顺序执行。**

## 提取验证记录（2026-09-08，出计划时做的）

本计划全部代码块**原样提取**到一份仓库副本里跑过真实的 pytest（Python 3.14 / 本仓库 `venv`），不是纸面推演：

- 六个 Task 的实现与测试全部落地后：**1176 passed, 2 skipped, 0 failed**（新增 36 个测试）
- `-m compliance`：**67 passed**
- `tests/test_boundary_guard.py`：**47 passed, 1 skipped**（新文件 `app/graph/manual_handoff.py` 不违反层次边界）
- 出计划时修掉的一处真实问题：`compute_intake_turn` 里 `try:` 包住 `run_intake_turn(...)` 之后，**参数续行必须一起缩进**——不缩进虽然 Python 合法、测试也全绿，但 diff 读起来像坏掉的代码。Task 5 的代码块给的是已经缩进好的版本。

⚠️ **执行前须知的一处既有红**：`main` 上 `tests/test_boundary_guard.py::test_real_repository_dependency_diff_is_empty` **本来就是失败的**（`bc1a0d0` 给 `requirements.txt` 加了 `tzdata==2026.3`，与守卫的基线 `e65f685` 有 diff）。**这不是本单元引入的，⛔ 不要为了让它变绿去动 `requirements.txt`**——那正好违反 Global Constraints 第 8 条。判据：本单元的改动集里没有 `requirements.txt`。

---

### Task 1: 备用供应商的配置位

**Files:**
- Modify: `app/config.py:15-22`（`Settings` 字段）、`app/config.py:49-53`（`validate_model_version`）
- Modify: `.env.example`（文件末尾追加一段）
- Test: `tests/test_config_fallback.py`

**Interfaces:**
- Produces: `Settings.llm_fallback_api_key: str = ""`、`Settings.llm_fallback_base_url: str = ""`、`Settings.llm_fallback_model: str = ""`、`Settings.llm_fallback_supports_json_schema: bool = False`；`Settings.validate_model_version()` 行为扩展（备用模型名同样禁止 latest 类别名）。Task 2 与 Task 3 依赖这四个字段名。

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_config_fallback.py`：

```python
"""WBS 2.3 的配置侧：备用供应商四个字段全部有默认值，不配 = 无备用。"""

import pytest

from app.config import Settings


def test_fallback_defaults_to_nothing_configured():
    """⛔ 默认值绝不指向任何真实供应商：没改过 .env 的机器不得在主家抖动时
    把数据发给一个谁也没批准过的端点。"""
    settings = Settings()
    assert settings.llm_fallback_api_key == ""
    assert settings.llm_fallback_base_url == ""
    assert settings.llm_fallback_model == ""
    assert settings.llm_fallback_supports_json_schema is False


@pytest.mark.parametrize("alias", ["latest", "deepseek-chat:latest", "deepseek-chat-latest"])
def test_fallback_model_rejects_latest_aliases(alias):
    """工程铁律 5 对备用家一视同仁。"""
    with pytest.raises(ValueError, match="latest"):
        Settings(llm_fallback_model=alias).validate_model_version()


def test_a_pinned_fallback_model_passes():
    Settings(llm_fallback_model="qwen-max-2025-01-25").validate_model_version()


def test_empty_fallback_model_is_not_a_version_violation():
    Settings(llm_fallback_model="").validate_model_version()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_config_fallback.py -q`
Expected: FAIL，`AttributeError: 'Settings' object has no attribute 'llm_fallback_api_key'`（以及 latest 用例不抛异常）

- [ ] **Step 3: 加字段**

在 `app/config.py` 的 `llm_supports_json_schema: bool = False` 与 `db_path: str = "data/demo.db"` **之间**插入：

```python

    # ── 备用供应商（WBS 2.3「双供应商切换与降级」）────────────────────────
    # 全部默认空 = **不配就是没有备用**，网关行为与今天逐字一致。⛔ 不给它们
    # 编一个"合理的默认供应商"：默认值一旦指向某家真实供应商，任何一台没改过
    # .env 的机器都会在主家抖动时把简历数据发给一个谁也没批准过的境外端点。
    #
    # ⛔ 三项必须同时给全（api_key / base_url / model），配不全按「无备用」运行
    # 并打 WARNING（判定在 app/llm/gateway.py 的 _build_fallback）。
    #
    # ⚠️ 合规红线：备用供应商同样**必须是境内模型**，简历数据不出境。这一条
    # 代码校验不了（base_url 是个自由字符串），由 .env 的评审把关。
    llm_fallback_api_key: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_supports_json_schema: bool = False

```

- [ ] **Step 4: 扩 `validate_model_version()`**

在 `app/config.py` 的 `validate_model_version` 方法体末尾（现有 `raise ValueError(...)` 之后）追加：

```python
        # 备用供应商一视同仁（工程铁律 5）：备用家漂了版本，历史评分照样
        # 失去解释力。空串是"没配备用"，跳过。
        #
        # ⛔ 这里刻意**不去顺手放宽上面那条 llm_model 的判定**（它漏了
        # `-latest` 这种写法，网关的 __init__ 兜得住）：改动既有字段的校验
        # 口径会让 .51 上一份今天能起来的 .env 明天起不来，而本交付单元的
        # 范围里没有这一项。登记在计划的「范围外与登记」一节。
        if self.llm_fallback_model:
            if (
                self.llm_fallback_model == "latest"
                or self.llm_fallback_model.endswith(":latest")
                or self.llm_fallback_model.endswith("-latest")
            ):
                raise ValueError(
                    "禁止使用 latest 类别名锁定备用供应商的模型版本，"
                    f"收到: {self.llm_fallback_model!r}"
                )
```

- [ ] **Step 5: 追加 `.env.example`**

在 `.env.example` **文件末尾**追加：

```
# 备用供应商（WBS 2.3）。⛔ 三项必须同时给全，配不全按「无备用」运行并打 WARNING。
# 全部留空 / 整段删掉 = 没有备用，行为与配它之前逐字一致。
# ⚠️ 合规红线：备用家同样**必须是境内模型**，简历数据不出境；⛔ 禁止 latest 类别名。
LLM_FALLBACK_API_KEY=
LLM_FALLBACK_BASE_URL=
LLM_FALLBACK_MODEL=
LLM_FALLBACK_SUPPORTS_JSON_SCHEMA=false
```

- [ ] **Step 6: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_config_fallback.py tests/test_config.py -q`
Expected: PASS（6 + 既有 config 用例全绿）

- [ ] **Step 7: 提交**

```bash
git add app/config.py .env.example tests/test_config_fallback.py
git commit -m "feat(config): 备用供应商配置位，不配即无备用（WBS 2.3）"
```

---

### Task 2: 网关双供应商切换与降级

**Files:**
- Modify: `app/llm/gateway.py`
- Test: `tests/test_llm_gateway_fallback.py`

**Interfaces:**
- Consumes: Task 1 的四个 `Settings` 字段名（此处只是同名的构造参数，网关⛔ 不 import `app.config`）
- Produces:
  - `class LLMProviderUnavailable(Exception)` —— Task 5 的 `compute_intake_turn` 捕获它
  - `PROVIDER_PRIMARY = "primary"` / `PROVIDER_FALLBACK = "fallback"` / `PROVIDER_SWITCH_EVENT = "provider_switch"`
  - `LLMGateway.__init__` 新增关键字参数 `fallback_api_key: str = ""`、`fallback_base_url: str = ""`、`fallback_model: str = ""`、`fallback_supports_json_schema: bool = False`、`fallback_client: Any = None` —— Task 3 用前四个
  - 私有属性 `LLMGateway._primary: _Provider` 与 `LLMGateway._fallback: _Provider | None` —— Task 3 的装配测试断言后者

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_llm_gateway_fallback.py`：

```python
"""WBS 2.3：双供应商切换与降级。切换事件记入 analysis_run（这里断言的是喂给
AuditHook 的那一行，落库那一段由 tests/test_audit_hook.py 覆盖）。"""

import json
from dataclasses import dataclass, field

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel

from app.llm.gateway import (
    PROVIDER_SWITCH_EVENT,
    LLMGateway,
    LLMProviderUnavailable,
    SchemaExtractionFailed,
)


class Point(BaseModel):
    x: int
    y: int


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 5


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    model: str
    usage: FakeUsage = field(default_factory=FakeUsage)
    system_fingerprint: str | None = None


class ScriptedCompletions:
    """按脚本逐次返回：字符串 = 正常响应体，异常实例 = 这次调用抛出去。"""

    def __init__(self, script, response_model):
        self._script = list(script)
        self._response_model = response_model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(
            choices=[FakeChoice(message=FakeMessage(content=item))],
            model=self._response_model,
        )


class ScriptedClient:
    def __init__(self, script, response_model):
        self.chat = type("Chat", (), {})()
        self.chat.completions = ScriptedCompletions(script, response_model)


class RecordingHook:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def _request():
    return httpx.Request("POST", "https://primary.example.com/v1/chat/completions")


def _status_error(code: int) -> APIStatusError:
    return APIStatusError(
        f"HTTP {code}", response=httpx.Response(code, request=_request()), body=None
    )


def _gateway(primary_script, fallback_script=None, hook=None, **kwargs):
    return LLMGateway(
        api_key="k",
        base_url="https://primary.example.com/v1",
        model="primary-model-241226",
        supports_json_schema=True,
        audit_hook=hook,
        client=ScriptedClient(primary_script, "primary-model-241226-actual"),
        fallback_model="fallback-model-250101" if fallback_script is not None else "",
        fallback_client=(
            ScriptedClient(fallback_script, "fallback-model-250101-actual")
            if fallback_script is not None
            else None
        ),
        fallback_supports_json_schema=True,
        **kwargs,
    )


def _extract(gateway):
    return gateway.extract_structured(
        system_prompt="s", user_prompt="u", schema=Point, prompt_version="v9"
    )


def _switch_rows(hook):
    rows = []
    for record in hook.records:
        raw = record["raw_response"]
        if raw and raw.startswith("{"):
            payload = json.loads(raw)
            if payload.get("event") == PROVIDER_SWITCH_EVENT:
                rows.append((record, payload))
    return rows


def test_primary_5xx_switches_to_fallback_and_returns_its_answer():
    hook = RecordingHook()
    gateway = _gateway([_status_error(503)], ['{"x": 1, "y": 2}'], hook=hook)

    assert _extract(gateway) == Point(x=1, y=2)

    # 两行 analysis_run：切换事件 + 备用方的真实调用。
    assert len(hook.records) == 2
    (switch_record, switch_payload), = _switch_rows(hook)
    assert switch_record["model"] == "primary-model-241226"
    assert switch_record["response_model"] is None
    assert switch_record["token_usage"] == {}
    assert switch_payload["failed_role"] == "primary"
    assert switch_payload["error_type"] == "APIStatusError"
    assert switch_payload["switched_to_model"] == "fallback-model-250101"

    # 铁律 5：切过去之后记的必须是**备用方响应返回的 model**，不是配置里的名字。
    answered = hook.records[1]
    assert answered["model"] == "fallback-model-250101"
    assert answered["response_model"] == "fallback-model-250101-actual"


def test_timeout_and_connection_errors_also_switch():
    for exc in (
        APITimeoutError(request=_request()),
        APIConnectionError(message="boom", request=_request()),
    ):
        gateway = _gateway([exc], ['{"x": 3, "y": 4}'], hook=RecordingHook())
        assert _extract(gateway) == Point(x=3, y=4)


def test_4xx_does_not_switch_and_never_touches_the_fallback():
    hook = RecordingHook()
    gateway = _gateway([_status_error(401)], ['{"x": 1, "y": 2}'], hook=hook)

    with pytest.raises(APIStatusError):
        _extract(gateway)

    assert gateway._fallback.client.chat.completions.calls == []
    assert hook.records == []  # ⛔ 4xx 不是切换事件，不记切换行


def test_schema_validation_failure_retries_on_the_same_provider():
    """2.5 的重试与 2.3 的切换是两回事：模型答得不对，换一家没有意义。"""
    hook = RecordingHook()
    gateway = _gateway(
        ["not json", '{"x": "bad"}', "still not json"], ['{"x": 1, "y": 2}'], hook=hook
    )

    with pytest.raises(SchemaExtractionFailed):
        _extract(gateway)

    assert len(gateway._primary.client.chat.completions.calls) == 3  # max_retries=2 → 3 次
    assert gateway._fallback.client.chat.completions.calls == []
    assert _switch_rows(hook) == []


def test_no_fallback_configured_raises_provider_unavailable_and_still_records_it():
    hook = RecordingHook()
    gateway = _gateway([_status_error(500)], hook=hook)

    with pytest.raises(LLMProviderUnavailable):
        _extract(gateway)

    (record, payload), = _switch_rows(hook)
    assert payload["switched_to_role"] is None  # 无处可切，事件照记
    assert record["temperature"] == 0
    assert record["prompt_version"] == "v9"


def test_both_providers_down_raises_provider_unavailable_with_two_events():
    hook = RecordingHook()
    gateway = _gateway([_status_error(502)], [_status_error(503)], hook=hook)

    with pytest.raises(LLMProviderUnavailable):
        _extract(gateway)

    rows = _switch_rows(hook)
    assert [payload["failed_role"] for _record, payload in rows] == ["primary", "fallback"]


def test_attempt_numbers_are_unique_across_the_switch():
    """app/audit/hook.py 的 _event_id 拼了 attempt。重号 = 第二行被主键静默丢掉。"""
    hook = RecordingHook()
    gateway = _gateway([_status_error(503)], ["not json", '{"x": 1, "y": 2}'], hook=hook)

    assert _extract(gateway) == Point(x=1, y=2)
    attempts = [record["attempt"] for record in hook.records]
    assert attempts == [1, 2, 3]
    assert len(set(attempts)) == len(attempts)


def test_input_hash_is_identical_across_the_switch():
    """同一次调用的所有留痕行必须能按 input_hash 串起来，否则查不出这是一次调用。"""
    hook = RecordingHook()
    gateway = _gateway([_status_error(503)], ['{"x": 1, "y": 2}'], hook=hook)
    _extract(gateway)
    assert len({record["input_hash"] for record in hook.records}) == 1


def test_fallback_model_rejects_latest_alias():
    with pytest.raises(ValueError, match="latest"):
        LLMGateway(
            api_key="k",
            base_url="https://p/v1",
            model="primary-model-241226",
            supports_json_schema=False,
            fallback_model="deepseek-chat-latest",
            fallback_client=ScriptedClient([], "x"),
        )


def test_partially_configured_fallback_runs_as_no_fallback(caplog):
    gateway = LLMGateway(
        api_key="k",
        base_url="https://p/v1",
        model="primary-model-241226",
        supports_json_schema=False,
        client=ScriptedClient([_status_error(503)], "p"),
        fallback_base_url="https://backup/v1",
        fallback_model="fallback-model-250101",
        # ⛔ 缺 fallback_api_key：⛔ 不复用主家的 key
    )
    assert gateway._fallback is None
    with pytest.raises(LLMProviderUnavailable):
        _extract(gateway)


def test_unconfigured_fallback_leaves_behaviour_byte_for_byte_unchanged():
    hook = RecordingHook()
    gateway = _gateway(['{"x": 7, "y": 8}'], hook=hook)
    assert gateway._fallback is None
    assert _extract(gateway) == Point(x=7, y=8)
    assert [record["attempt"] for record in hook.records] == [1]
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_llm_gateway_fallback.py -q`
Expected: FAIL，`ImportError: cannot import name 'PROVIDER_SWITCH_EVENT' from 'app.llm.gateway'`

- [ ] **Step 3: 加异常类型、常量与两个纯函数**

在 `app/llm/gateway.py` 里，把顶部的 import 改成（只改这一行）：

```python
from openai import APIConnectionError, APIStatusError, OpenAI
```

在 `class SchemaExtractionFailed` 定义之后、`_STRICT_UNSUPPORTED_KEYWORDS` 之前插入：

```python
class LLMProviderUnavailable(Exception):
    """
    主备供应商都答不上来（传输层故障）。

    ⛔ 与 `SchemaExtractionFailed` **刻意分成两个类型**：那个是"模型答了、但没按
    schema 答"（内容问题，重试有意义、切供应商没意义），这个是"根本没答上"
    （供应商问题，切供应商有意义、在同一家重试没意义）。合成一个异常之后，
    编排层就再也分不出"转人工的原因是模型不听话"还是"供应商挂了"——而这两件事
    的人工处置完全不同。
    """


# 供应商角色。⛔ 两个字面量只在这里定义：analysis_run 的切换事件行、日志、
# 测试断言全部引用它们，散落成字符串就会出现"日志里写 backup、断言里写
# fallback"这种查不出来的不一致。
PROVIDER_PRIMARY = "primary"
PROVIDER_FALLBACK = "fallback"

# 切换事件写进 `analysis_run.raw_response` 的标记（2.3「切换事件记入
# analysis_run」）。⛔ 不新建日志表、不给 analysis_run 加列：那张表已经带齐
# 工程铁律 3 的全部字段，切换事件只是"这次调用没拿到响应"的一行留痕。
PROVIDER_SWITCH_EVENT = "provider_switch"

# 切换判据的分界线，**写死**。5xx = 供应商自己的问题，切；4xx = 请求本身的
# 问题（鉴权、参数、配额），换一家照样错，⛔ 不切。
_SWITCHABLE_STATUS_FLOOR = 500


def _rejects_latest_alias(model: str) -> None:
    """工程铁律 5：模型版本显式锁定，禁止 `latest` 类别名。主备一视同仁——
    备用供应商上漂了版本，历史评分照样失去解释力。"""
    if model == "latest" or model.endswith(":latest") or model.endswith("-latest"):
        raise ValueError(f"禁止使用 latest 类别名锁定模型版本，收到: {model!r}")


def _is_switchable(exc: Exception) -> bool:
    """
    这个异常该不该切备用供应商。**判据写死在这里，⛔ 不做可配置**——
    "什么算供应商挂了"是一条业务契约，配置化之后 .51 上改一行 .env 就能让
    4xx 也去打备用供应商，把一个鉴权配置错误放大成两家供应商的账单。

    切：HTTP 5xx、超时、连接错误（`APITimeoutError` 是 `APIConnectionError`
        的子类，一条 isinstance 就都盖住了）
    ⛔ 不切：4xx（鉴权/参数/配额——换一家照样错）、schema 校验失败
        （那是内容问题，由 max_retries 在**同一家**上重试）
    """
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= _SWITCHABLE_STATUS_FLOOR
    return False
```

- [ ] **Step 4: 加 `_Provider` 并改构造函数**

在 `@dataclass(frozen=True) class LLMCallMeta` 之后、`class LLMGateway` 之前插入：

```python
@dataclass(frozen=True)
class _Provider:
    """一家供应商的三件套。role 只用于留痕与日志，⛔ 不参与任何判定。"""

    role: str
    client: Any
    model: str
    supports_json_schema: bool
```

把 `LLMGateway.__init__` 的签名尾部与函数体替换成（`api_key` / `base_url` / `model` / `supports_json_schema` 三行签名不动）：

```python
        max_retries: int = 2,
        audit_hook: AuditHook | None = None,
        client: Any = None,
        fallback_api_key: str = "",
        fallback_base_url: str = "",
        fallback_model: str = "",
        fallback_supports_json_schema: bool = False,
        fallback_client: Any = None,
    ) -> None:
        _rejects_latest_alias(model)

        self._model = model
        self._supports_json_schema = supports_json_schema
        self._max_retries = max_retries
        self._audit_hook = audit_hook or NoopAuditHook()
        self._client = client or OpenAI(api_key=api_key, base_url=base_url)
        self._primary = _Provider(
            role=PROVIDER_PRIMARY,
            client=self._client,
            model=model,
            supports_json_schema=supports_json_schema,
        )
        self._fallback = self._build_fallback(
            api_key=fallback_api_key,
            base_url=fallback_base_url,
            model=fallback_model,
            supports_json_schema=fallback_supports_json_schema,
            client=fallback_client,
        )

    @staticmethod
    def _build_fallback(
        *,
        api_key: str,
        base_url: str,
        model: str,
        supports_json_schema: bool,
        client: Any,
    ) -> _Provider | None:
        """
        备用供应商是**可选**的：一个都不配就返回 None，网关行为与配它之前逐字
        一致（2.3 的默认值要求）。

        ⛔ 配不全时不猜、更不复用主供应商的 api_key：备用是**另一家**供应商，
        主家的 key 拿去打它只会得到 401，而 401 是 4xx——按 `_is_switchable`
        不切、直接抛，等于把"配置漏了一项"变成"整条采集链路挂掉"。配不全时
        一律按「无备用」运行并打 WARNING，方向是保守的那一侧。
        """
        if client is not None:
            # 测试注入路径：给了 client 就必须给 model，否则留痕里记不出这是谁答的。
            if not model:
                raise ValueError("注入了 fallback_client 就必须同时给出 fallback_model")
            _rejects_latest_alias(model)
            return _Provider(
                role=PROVIDER_FALLBACK,
                client=client,
                model=model,
                supports_json_schema=supports_json_schema,
            )

        configured = [bool(api_key), bool(base_url), bool(model)]
        if not any(configured):
            return None
        if not all(configured):
            logger.warning(
                "备用供应商配置不全（LLM_FALLBACK_API_KEY / LLM_FALLBACK_BASE_URL / "
                "LLM_FALLBACK_MODEL 必须同时给全，当前缺失: %s），本进程按「无备用」"
                "运行——主供应商故障时不会切换，会直接抛 LLMProviderUnavailable。",
                [
                    name
                    for name, present in zip(
                        ("api_key", "base_url", "model"), configured
                    )
                    if not present
                ],
            )
            return None

        _rejects_latest_alias(model)
        return _Provider(
            role=PROVIDER_FALLBACK,
            client=OpenAI(api_key=api_key, base_url=base_url),
            model=model,
            supports_json_schema=supports_json_schema,
        )
```

- [ ] **Step 5: 改重试循环为「两个预算」**

在 `extract_structured_with_meta` 里，把

```python
        for attempt_index in range(attempts):
            started = time.monotonic()
            response = self._call_model(system_prompt, user_prompt, schema)
            latency_ms = (time.monotonic() - started) * 1000
```

替换成：

```python
        provider = self._primary
        switched = False
        # 每调一次 AuditHook.record 就 +1，**跨供应商单调递增**。
        # ⛔ 不要退回"用循环下标当 attempt"：切换事件行与紧随其后的重试行会拿到
        # 同一个 attempt，而 app/audit/hook.py 的 _event_id 是
        # {thread_id}:{node}:{input_hash}:{attempt}——撞 id 的第二行会被
        # SqliteSink 当成"已写过"静默丢掉，切换事件就此消失且不报错。
        record_seq = 0
        # schema 校验失败才消耗重试预算（2.5「校验失败重试至多 2 次」）。
        # ⛔ 供应商故障不计入：它由"至多切一次"独立封顶，两个预算混用会让
        # "主家超时一次"白白吃掉一次本该留给模型的重试。
        schema_attempts_used = 0

        while schema_attempts_used < attempts:
            started = time.monotonic()
            try:
                response = self._call_model(provider, system_prompt, user_prompt, schema)
            except Exception as exc:
                latency_ms = (time.monotonic() - started) * 1000
                total_latency_ms += latency_ms
                if not _is_switchable(exc):
                    # 4xx / 其他：换一家照样错，⛔ 不切、⛔ 不重试，原样抛给调用方。
                    raise
                record_seq += 1
                self._record_provider_switch(
                    provider=provider,
                    exc=exc,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    latency_ms=latency_ms,
                    attempt=record_seq,
                    audit_context=audit_context,
                    switched_to=None if (self._fallback is None or switched) else self._fallback,
                )
                if self._fallback is None or switched:
                    raise LLMProviderUnavailable(
                        f"供应商不可用且已无可切换的备用（最后一家: {provider.role}/"
                        f"{provider.model}）: {exc!r}"
                    ) from exc
                logger.warning(
                    "主供应商 %s 调用失败（%s），切换到备用供应商 %s；"
                    "切换事件已记入 analysis_run（raw_response 含 %s 标记）。",
                    provider.model,
                    type(exc).__name__,
                    self._fallback.model,
                    PROVIDER_SWITCH_EVENT,
                )
                provider = self._fallback
                switched = True
                continue

            latency_ms = (time.monotonic() - started) * 1000
            schema_attempts_used += 1
            record_seq += 1
```

同一个函数里再改三处：

1. `self._audit_hook.record(` 的第一个参数，把 `model=self._model,` 改成：

```python
                # 配置侧记的是**这一次实际用的那家**的模型名，切到备用之后
                # 就是备用方的名字——记主供应商的名字等于让留痕撒谎。
                model=provider.model,
```

2. 同一次 `record(...)` 调用里 `attempt=attempt_index + 1,` 改成 `attempt=record_seq,`（上方那段解释 attempt 用途的既有注释保留不动）
3. 成功返回处 `attempts=attempt_index + 1,` 改成 `attempts=schema_attempts_used,`

- [ ] **Step 6: 加 `_record_provider_switch` 并让 `_call_model` 收 provider**

把 `def _call_model(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]):` 及其下一行替换成：

```python
    def _record_provider_switch(
        self,
        *,
        provider: _Provider,
        exc: Exception,
        prompt_version: str,
        input_hash: str,
        latency_ms: float,
        attempt: int,
        audit_context: dict[str, Any] | None,
        switched_to: _Provider | None,
    ) -> None:
        """
        把一次供应商故障（以及随之发生的切换）记成 `analysis_run` 的**一行**。

        ⛔ 不新建表、不加列（2.3「切换事件记入 analysis_run」；AuditHook 的签名
        也不能动，见 LLMCallMeta 的说明）。这一行的判据是自洽的：
        `raw_response` 是一段 JSON，`event` 字段恒为 `provider_switch`；
        `response_model` 为 None（根本没拿到响应）；`token_usage` 为空。
        紧随其后那一行的 `configured_model` 就是切过去的那家。

        ⛔ raw_response 里只放异常类型名与角色/模型名，**不放异常文本**：
        供应商的错误体可能回显请求内容，而 spec 禁止在留痕里存原文。
        """
        self._audit_hook.record(
            model=provider.model,
            response_model=None,
            system_fingerprint=None,
            prompt_version=prompt_version,
            temperature=self.TEMPERATURE,
            input_hash=input_hash,
            raw_response=json.dumps(
                {
                    "event": PROVIDER_SWITCH_EVENT,
                    "failed_role": provider.role,
                    "failed_model": provider.model,
                    "error_type": type(exc).__name__,
                    "switched_to_role": None if switched_to is None else switched_to.role,
                    "switched_to_model": None if switched_to is None else switched_to.model,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            token_usage={},
            latency_ms=latency_ms,
            attempt=attempt,
            audit_context=audit_context,
        )

    def _call_model(
        self,
        provider: _Provider,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ):
        strict_schema = _to_strict_json_schema(schema) if provider.supports_json_schema else None
```

同一个方法末尾，把发请求那两行改成：

```python
        return provider.client.chat.completions.create(
            model=provider.model,
```

- [ ] **Step 7: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_llm_gateway_fallback.py tests/test_llm_gateway.py -q`
Expected: PASS，`32 passed`（新增 11 + 既有 21 全绿——**既有 21 条一条都不许改**，它们就是"不配备用时行为不变"的回归证据）

- [ ] **Step 8: 提交**

```bash
git add app/llm/gateway.py tests/test_llm_gateway_fallback.py
git commit -m "feat(gateway): 主供应商 5xx/超时/连接错误切备用，切换事件记入 analysis_run（WBS 2.3）"
```

---

### Task 3: 把备用供应商接进生产装配

**Files:**
- Modify: `app/main.py:44-53`（`_gateway_factory()`）
- Test: `tests/test_main_fallback_wiring.py`

**Interfaces:**
- Consumes: Task 1 的 `Settings` 四个字段；Task 2 的 `LLMGateway` 四个 `fallback_*` 关键字参数与 `_fallback` 属性
- Produces: 无新符号。⛔ 不改 `create_app` 签名、⛔ 不改模块级的留痕装配（`_audit_conn` / `_audit_recorder` / `_audit_hook` 三行一个字都不动）

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_main_fallback_wiring.py`：

```python
"""WBS 2.3 的装配守护：备用供应商四项必须从 Settings 接到 _gateway_factory()。

AST 扫源码 + 一个真实子进程装配，两条路互补（与 tests/test_main_wiring.py 同一
形状）：AST 便宜、钉住"写在哪儿"；子进程贵、能证明"真的跑得起来"。
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")

FALLBACK_KEYWORDS = {
    "fallback_api_key": "llm_fallback_api_key",
    "fallback_base_url": "llm_fallback_base_url",
    "fallback_model": "llm_fallback_model",
    "fallback_supports_json_schema": "llm_fallback_supports_json_schema",
}


def _gateway_call() -> ast.Call:
    for node in ast.walk(ast.parse(MAIN_SOURCE)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "LLMGateway":
            return node
    raise AssertionError("app/main.py 里找不到 LLMGateway(...) 的构造调用")


def test_all_four_fallback_keywords_come_from_settings():
    """⛔ 不许在这里写死任何供应商常量：备用供应商只能来自 Settings。"""
    keywords = {kw.arg: kw.value for kw in _gateway_call().keywords}
    for arg, settings_field in FALLBACK_KEYWORDS.items():
        assert arg in keywords, f"_gateway_factory() 漏传了 {arg}"
        value = keywords[arg]
        assert isinstance(value, ast.Attribute) and value.attr == settings_field, (
            f"{arg} 必须取 settings.{settings_field}，⛔ 不许写死字面量"
        )


def test_importing_app_main_without_fallback_env_yields_no_fallback(tmp_path):
    """.51 上不改 .env 就是「无备用」——行为与今天逐字一致（2.3 的默认值要求）。"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LLM_FALLBACK_")
    }
    env["DB_PATH"] = str(tmp_path / "wiring.db")
    env["AUDIT_JSONL_PATH"] = str(tmp_path / "audit.jsonl")
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.main as m; print(m._gateway_factory()._fallback)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None"
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_main_fallback_wiring.py -q`
Expected: FAIL，`AssertionError: _gateway_factory() 漏传了 fallback_api_key`

- [ ] **Step 3: 接线**

在 `app/main.py` 的 `_gateway_factory()` 里，`audit_hook=_audit_hook,` 之后追加：

```python
        # WBS 2.3：备用供应商。四项全部来自 Settings 且都有默认值——.51 上
        # 不改 .env 就是"无备用"，行为与今天逐字一致，⛔ 不需要配套改任何
        # 部署产物。
        fallback_api_key=settings.llm_fallback_api_key,
        fallback_base_url=settings.llm_fallback_base_url,
        fallback_model=settings.llm_fallback_model,
        fallback_supports_json_schema=settings.llm_fallback_supports_json_schema,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_main_fallback_wiring.py tests/test_main_wiring.py -q`
Expected: PASS（新增 2 条 + 既有留痕装配守护全绿——后者证明本改动没有动 U3 的注入点）

- [ ] **Step 5: 提交**

```bash
git add app/main.py tests/test_main_fallback_wiring.py
git commit -m "feat(main): _gateway_factory 接入备用供应商四项配置（WBS 2.3）"
```

---

### Task 4: 转人工的两个 effect 节点

**Files:**
- Create: `app/graph/manual_handoff.py`
- Test: `tests/test_manual_handoff.py`

**Interfaces:**
- Consumes: `app.storage.idempotency.idempotent_effect`、`app.channels.base.Channel/OutboundMessage`
- Produces（Task 5 全部要用）:
  - `REASON_SCHEMA_EXHAUSTED = "schema_retry_exhausted"`、`REASON_PROVIDER_UNAVAILABLE = "provider_unavailable"`
  - `manual_handoff_text(reason_code: str) -> str`
  - `effect_mark_needs_manual(conn, *, thread_id: str, business_key: str, reason_code: str) -> None`
  - `effect_deliver_manual_handoff(conn, *, thread_id: str, business_key: str, channel: Channel, reason_code: str, round_count: int) -> None`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_manual_handoff.py`：

```python
"""WBS 2.5 的两个 effect_* 节点（app/graph/manual_handoff.py），单独调用。
图上的接线由 tests/test_intake_needs_manual.py 覆盖。"""

import json

import pytest

from app.channels.web_channel import WebChannel
from app.graph.manual_handoff import (
    REASON_PROVIDER_UNAVAILABLE,
    REASON_SCHEMA_EXHAUSTED,
    effect_deliver_manual_handoff,
    effect_mark_needs_manual,
    manual_handoff_text,
)
from app.storage.db import get_connection, init_schema


def _seeded_conn(tmp_path, status="drafting"):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('job1', '待确定', ?)", (status,)
    )
    conn.commit()
    return conn


def test_marking_writes_the_status_and_its_effect_log_together(tmp_path):
    """工程铁律 1：业务写与幂等记录由装饰器在同一个事务里提交一次。"""
    conn = _seeded_conn(tmp_path)

    effect_mark_needs_manual(
        conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
    )

    status = conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0]
    logged = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key='job1:effect_mark_needs_manual:0'"
    ).fetchone()[0]
    assert (status, logged) == ("needs_manual", 1)


def test_marking_twice_is_a_no_op(tmp_path):
    """LangGraph 恢复时节点从头整个重跑。"""
    conn = _seeded_conn(tmp_path)
    for _ in range(2):
        effect_mark_needs_manual(
            conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
        )
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1


@pytest.mark.parametrize("terminal", ["approved", "abandoned"])
def test_terminal_statuses_are_never_overwritten(tmp_path, terminal):
    """一次模型故障⛔ 不得把已冻结/已放弃的岗位拽回「待人工处理」。"""
    conn = _seeded_conn(tmp_path, status=terminal)
    effect_mark_needs_manual(
        conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
    )
    assert conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0] == terminal


def test_delivery_puts_exactly_one_needs_manual_message_in_the_outbox(tmp_path):
    conn = _seeded_conn(tmp_path)
    channel = WebChannel(conn)

    for _ in range(2):  # 重放
        effect_deliver_manual_handoff(
            conn,
            thread_id="job1",
            business_key="0",
            channel=channel,
            reason_code=REASON_PROVIDER_UNAVAILABLE,
            round_count=0,
        )

    rows = conn.execute(
        "SELECT message_type, payload_json FROM outbox WHERE thread_id='job1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "needs_manual"
    payload = json.loads(rows[0][1])
    assert payload["reason_code"] == REASON_PROVIDER_UNAVAILABLE
    assert payload["message"]


@pytest.mark.parametrize(
    "code", [REASON_SCHEMA_EXHAUSTED, REASON_PROVIDER_UNAVAILABLE, "something-new"]
)
def test_every_reason_code_renders_non_empty_chinese_text(code):
    """⛔ 未登记的原因码不许抛：这条路径本来就是"出事之后"的路径。
    ⚠️ _run_turn 会把这段文案当响应体返回给业务经理，空串等于一个空白页面。"""
    text = manual_handoff_text(code)
    assert text and "转交 HR 人工处理" in text


@pytest.mark.compliance
def test_automatic_handoff_never_writes_a_human_review_row(tmp_path):
    """转人工是**系统判定**。决策人只能是人——⛔ 不许往 human_review 里塞机器决策。"""
    conn = _seeded_conn(tmp_path)
    effect_mark_needs_manual(
        conn, thread_id="job1", business_key="0", reason_code=REASON_SCHEMA_EXHAUSTED
    )
    assert conn.execute("SELECT COUNT(*) FROM human_review").fetchone()[0] == 0
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_manual_handoff.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.graph.manual_handoff'`

- [ ] **Step 3: 新建 `app/graph/manual_handoff.py`**

```python
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
        "已经采集到的内容都保留着，不用重新说一遍。"
    ),
    REASON_PROVIDER_UNAVAILABLE: (
        "模型服务暂时不可用（主备两家都没能应答），本轮已转交 HR 人工处理。"
        "已经采集到的内容都保留着，不用重新说一遍。"
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
        "本轮采集没能完成，已转交 HR 人工处理。已经采集到的内容都保留着。",
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_manual_handoff.py -q`
Expected: PASS，`9 passed`

- [ ] **Step 5: 边界守卫复查**

Run: `venv/bin/python -m pytest tests/test_boundary_guard.py -q`
Expected: 除既有那条 `test_real_repository_dependency_diff_is_empty`（`tzdata` 遗留红，见「提取验证记录」）之外全绿。**⛔ 不要为它改 `requirements.txt`。**

- [ ] **Step 6: 提交**

```bash
git add app/graph/manual_handoff.py tests/test_manual_handoff.py
git commit -m "feat(graph): 转人工的两个幂等 effect 节点，置状态与投递各自独占（WBS 2.5）"
```

---

### Task 5: compute 侧信号与图上的转人工旁路

**Files:**
- Modify: `app/graph/state.py`（末尾追加两键）
- Modify: `app/graph/nodes.py`（**两处**：import 行；`compute_intake_turn` 里 `run_intake_turn(...)` 那一段）
- Modify: `app/graph/build.py`
- Test: `tests/test_intake_needs_manual.py`

**Interfaces:**
- Consumes: Task 2 的 `LLMProviderUnavailable`、既有的 `SchemaExtractionFailed`；Task 4 的 `REASON_SCHEMA_EXHAUSTED` / `REASON_PROVIDER_UNAVAILABLE` 与两个 `effect_*` 节点
- Produces: `IntakeState` 新增 `needs_manual: bool` 与 `needs_manual_reason_code: str`；图上新增节点名 `effect_mark_needs_manual` / `effect_deliver_manual_handoff`，幂等键的 `business_key` 一律是 `str(state["round_count"])`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_intake_needs_manual.py`：

```python
"""WBS 2.5：校验失败重试至多 2 次，仍失败转 needs_manual，⛔ 不产出半成品。"""

import pytest

from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.manual_handoff import REASON_PROVIDER_UNAVAILABLE, REASON_SCHEMA_EXHAUSTED
from app.graph.nodes import compute_intake_turn
from app.llm.gateway import LLMProviderUnavailable, SchemaExtractionFailed
from app.storage.db import get_connection, init_schema
from app.storage.job_queries import derive_needs_manual_reasons


class ExplodingGateway:
    """extract_structured* 一律抛。网关内部的重试已经耗尽，抛出来的就是终局。"""

    def __init__(self, exc):
        self._exc = exc

    def extract_structured(self, **kwargs):
        raise self._exc

    def extract_structured_with_meta(self, **kwargs):
        raise self._exc


def _state():
    return {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 2,
        "profile_patch_accumulated": {"job_title": "嵌入式软件工程师"},
    }


def _seeded_conn(tmp_path, status="drafting"):
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('job1', '待确定', ?)", (status,)
    )
    conn.commit()
    return conn


# ── compute 侧：纯函数，只置信号 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "exc, expected_code",
    [
        (SchemaExtractionFailed("3 次尝试后仍未通过 Schema 校验"), REASON_SCHEMA_EXHAUSTED),
        (LLMProviderUnavailable("主备都不可用"), REASON_PROVIDER_UNAVAILABLE),
    ],
)
def test_compute_turns_extraction_failure_into_a_needs_manual_signal(exc, expected_code):
    result = compute_intake_turn(_state(), gateway=ExplodingGateway(exc))

    assert result["needs_manual"] is True
    assert result["needs_manual_reason_code"] == expected_code
    assert result["is_complete"] is False
    assert result["is_productive"] is False
    assert result["pending_questions"] == []


def test_compute_returns_the_accumulated_profile_untouched():
    """「不产出半成品」：已采集的照旧，本轮**一个字段都不加**。"""
    state = _state()
    result = compute_intake_turn(
        state, gateway=ExplodingGateway(SchemaExtractionFailed("x"))
    )

    assert result["profile_patch_accumulated"] == {"job_title": "嵌入式软件工程师"}
    # 这一轮系统什么都没说，⛔ 不许往 history 里塞一句模型从未说过的话。
    assert result["history"] == state["history"]
    # round_count 的真源是 job_profile 行数，本轮不落行 ⇒ ⛔ 不自增。
    assert result["round_count"] == 2


# ── 图侧：两个 effect 节点 ───────────────────────────────────────────────


def _run_failing_turn(tmp_path, status="drafting", exc=None):
    conn = _seeded_conn(tmp_path, status=status)
    channel = WebChannel(conn)
    graph = build_intake_graph(
        str(tmp_path / "t.db"),
        gateway=ExplodingGateway(exc or SchemaExtractionFailed("x")),
        conn=conn,
        channel=channel,
    )
    graph.invoke(_state(), config={"configurable": {"thread_id": "job1"}})
    return conn, channel, graph


def test_failing_turn_marks_the_job_needs_manual_and_writes_no_profile_row(tmp_path):
    conn, channel, _graph = _run_failing_turn(tmp_path)

    assert conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0] == "needs_manual"
    # 「不产出半成品」的落库判据：一行都没有。
    assert conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id='job1'").fetchone()[0] == 0

    message = channel.latest("job1")
    assert message.type == "needs_manual"
    assert message.payload["reason_code"] == REASON_SCHEMA_EXHAUSTED
    assert message.payload["message"]  # ⛔ 不能是空串：_run_turn 会把它当响应体返回


def test_each_effect_gets_its_own_idempotency_key(tmp_path):
    """工程铁律 1：每个有副作用的动作独占一个节点，各带幂等键。"""
    conn, _channel, _graph = _run_failing_turn(tmp_path)

    keys = [
        row[0]
        for row in conn.execute(
            "SELECT effect_key FROM effect_log WHERE thread_id='job1' ORDER BY effect_key"
        )
    ]
    assert keys == [
        "job1:effect_deliver_manual_handoff:2",
        "job1:effect_mark_needs_manual:2",
    ]
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_intake_needs_manual.py -q`
Expected: FAIL，`SchemaExtractionFailed` 直接从 `compute_intake_turn` 里穿出来（还没有 try/except）

- [ ] **Step 3: 给 `IntakeState` 加两个键**

在 `app/graph/state.py` **文件末尾**追加：

```python

    # ── 2.5 转人工信号 ──────────────────────────────────────────────
    # compute_intake_turn 在"重试耗尽 / 主备都不可用"时置位。这是**唯一**
    # 决定图走 effect_mark_needs_manual 分支的键（见 app/graph/build.py 的
    # 条件边），⛔ 不要再引入第二个判定源。
    needs_manual: bool
    # 原因码，取值见 app/graph/manual_handoff.py 的 REASON_* 常量。
    needs_manual_reason_code: str
```

- [ ] **Step 4: 改 `nodes.py`（改动处一：import）**

把 `from app.llm.gateway import LLMGateway` 这**一行**替换成**两行**：

```python
from app.graph.manual_handoff import REASON_PROVIDER_UNAVAILABLE, REASON_SCHEMA_EXHAUSTED
from app.llm.gateway import LLMGateway, LLMProviderUnavailable, SchemaExtractionFailed
```

⚠️ 放在 `from app.graph.state import IntakeState` 之后、`from app.llm.gateway ...` 原位——保持 import 块的字母序。⛔ 不会形成循环：`manual_handoff` 不 import `nodes`。

- [ ] **Step 5: 改 `nodes.py`（改动处二：异常 → 信号）**

在 `compute_intake_turn` 里，把从 `    result = run_intake_turn(` 到它的收尾 `    )` 这**一整段**替换成：

```python
    # ── 2.5「重试耗尽 → 转 needs_manual，不产出半成品」──────────────────
    # 这是本交付单元在 nodes.py 里的**唯一**逻辑改动处。捕获的两类异常语义不同、
    # 原因码也不同：SchemaExtractionFailed = 模型答了但没按 schema 答；
    # LLMProviderUnavailable = 主备两家都没答上。
    #
    # ⛔ 不在这里写库、不在这里发消息——compute_* 是纯函数（工程铁律 2）。
    # 置位 needs_manual 之后，由 app/graph/build.py 的条件边把流程导向
    # effect_mark_needs_manual / effect_deliver_manual_handoff 两个 effect 节点。
    try:
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
    except (SchemaExtractionFailed, LLMProviderUnavailable) as exc:
        reason_code = (
            REASON_SCHEMA_EXHAUSTED
            if isinstance(exc, SchemaExtractionFailed)
            else REASON_PROVIDER_UNAVAILABLE
        )
        # ⛔ 这里不打日志：nodes.py 没有模块级 logger，为一条 warning 再加两行
        # 导入等于把"只改一处"变成"改三处"。转人工这件事的日志打在
        # app/graph/manual_handoff.py 的 effect_mark_needs_manual 里——那才是
        # 状态真正落库的地方，而 compute_* 的返回值可能因重放被丢弃。
        return {
            **state,
            # ⛔ 原样退回，**不并入任何本轮的部分解析结果**（2.5「不产出
            # 半成品」）：半成品比没有更糟——业务经理会以为系统听懂了。
            "profile_patch_accumulated": accumulated_before,
            # ⛔ history 不追加 assistant 轮：这一轮系统什么也没说。追加一句
            # 空话会让下一轮的 prompt 里多出一段模型从未说过的话。
            "history": history,
            "needs_manual": True,
            "needs_manual_reason_code": reason_code,
            "is_complete": False,
            "is_job_related": state.get("is_job_related", True),
            "pending_questions": [],
            # ⛔ round_count 不自增：这一轮不落 job_profile 行，而 round_count
            # 的真源就是 job_profile 的行数（_run_turn 每轮重查）。在这里 +1
            # 会让 state 与库对不上。
            "is_productive": False,
        }
```

⚠️ **`run_intake_turn(...)` 的全部参数续行必须一起多缩进 4 格**（上面的代码块已经是缩进好的版本）。不缩进虽然 Python 合法、测试也会全绿，但 diff 读起来像坏掉的代码。
⚠️ 这一段之后的 `accumulated = {**accumulated_before, **result.profile_patch}` 及其后的全部代码**原样不动**。

- [ ] **Step 6: 改 `build.py`（import + 两个节点 + 条件边）**

在 `from langgraph.graph import END, StateGraph` 之后的 import 块里，`from app.graph.nodes import ...` **之前**插入一行：

```python
from app.graph.manual_handoff import effect_deliver_manual_handoff, effect_mark_needs_manual
```

在 `build_intake_graph` 的 docstring 末尾（`持久化恢复。` 之后、`"""` 之前）追加：

```

    2.5 的转人工是**同一张图上的一条旁路**：compute_intake_turn 置位
    `needs_manual` 时改走 effect_mark_needs_manual → effect_deliver_manual_handoff
    → END，⛔ 完全绕开 effect_persist_draft——那正是"不产出半成品"这句话在图上
    的形状（不落新版本画像）。
```

在既有的 `graph.add_node("compute_intake_turn", _compute_node)` **之前**插入三个函数：

```python
    def _mark_needs_manual_node(state: IntakeState) -> IntakeState:
        effect_mark_needs_manual(
            conn,
            thread_id=state["job_id"],
            # business_key 用 round_count：这一轮**没有**落 job_profile 行，
            # 所以重放/用户重试时 round_count 仍然是同一个值，幂等键命中、
            # 这两个 effect 被正确跳过（job.status 已经是 needs_manual、消息
            # 已经投递过一次）。⛔ 不要在这里用时间戳之类每次都变的值。
            business_key=str(state["round_count"]),
            reason_code=state["needs_manual_reason_code"],
        )
        return state

    def _deliver_manual_handoff_node(state: IntakeState) -> IntakeState:
        effect_deliver_manual_handoff(
            conn,
            thread_id=state["job_id"],
            business_key=str(state["round_count"]),
            channel=channel,
            reason_code=state["needs_manual_reason_code"],
            round_count=state["round_count"],
        )
        return state

    def _route_after_compute(state: IntakeState) -> str:
        """本轮走正常落库还是走转人工旁路。**唯一判定源是 needs_manual**。"""
        return "handoff" if state.get("needs_manual") else "draft"

```

在 `graph.add_node("effect_deliver_message", _deliver_node)` 之后追加：

```python
    graph.add_node("effect_mark_needs_manual", _mark_needs_manual_node)
    graph.add_node("effect_deliver_manual_handoff", _deliver_manual_handoff_node)
```

把

```python
    graph.set_entry_point("compute_intake_turn")
    graph.add_edge("compute_intake_turn", "effect_persist_draft")
    graph.add_edge("effect_persist_draft", "effect_deliver_message")
    graph.add_edge("effect_deliver_message", END)
```

替换成：

```python
    graph.set_entry_point("compute_intake_turn")
    graph.add_conditional_edges(
        "compute_intake_turn",
        _route_after_compute,
        {"draft": "effect_persist_draft", "handoff": "effect_mark_needs_manual"},
    )
    graph.add_edge("effect_persist_draft", "effect_deliver_message")
    graph.add_edge("effect_deliver_message", END)
    graph.add_edge("effect_mark_needs_manual", "effect_deliver_manual_handoff")
    graph.add_edge("effect_deliver_manual_handoff", END)
```

- [ ] **Step 7: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_intake_needs_manual.py tests/test_graph_idempotency.py -q`
Expected: PASS（新增 5 条 + 既有图幂等用例全绿——后者证明正常路径一步没变）

- [ ] **Step 8: 提交**

```bash
git add app/graph/state.py app/graph/nodes.py app/graph/build.py tests/test_intake_needs_manual.py
git commit -m "feat(graph): 提取失败置 needs_manual 信号，条件边绕开画像落库（WBS 2.5）"
```

---

### Task 6: 端到端不变式与队列打通

**Files:**
- Test: `tests/test_intake_needs_manual.py`（追加四个用例，⛔ 不改 Task 5 已写的用例）

**Interfaces:**
- Consumes: Task 5 的 `_run_failing_turn` 辅助函数与 `ExplodingGateway`（同一文件内，⛔ 不要重复定义）
- Produces: 无新符号

本 Task **只加断言、⛔ 不改任何生产代码**。如果任何一条不通过，说明前面某个 Task 做错了——回去改那个 Task，⛔ 不要放宽这里的断言。

- [ ] **Step 1: 追加四个用例**

在 `tests/test_intake_needs_manual.py` **文件末尾**追加：

```python


def test_replaying_the_same_failing_turn_delivers_exactly_one_message(tmp_path):
    """LangGraph 恢复时节点从头整个重跑——重跑不得再投递一次。"""
    conn, _channel, graph = _run_failing_turn(tmp_path)
    graph.invoke(_state(), config={"configurable": {"thread_id": "job1"}})

    assert conn.execute("SELECT COUNT(*) FROM outbox WHERE thread_id='job1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id='job1'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id='job1'").fetchone()[0] == 0


def test_effect_log_count_matches_the_business_write_per_thread(tmp_path):
    """reviewer 判据：effect_log 条数与业务表行数按 thread 恒等。
    本单元的业务写是 job.status 一次 + outbox 一行，各对应一条 effect_log。"""
    conn, _channel, _graph = _run_failing_turn(tmp_path)

    outbox_rows = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    deliver_effects = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id='job1' "
        "AND node_name='effect_deliver_manual_handoff'"
    ).fetchone()[0]
    assert outbox_rows == deliver_effects == 1


def test_provider_outage_produces_its_own_reason_code(tmp_path):
    """两类失败的人工处置完全不同，队列里必须分得开。"""
    _conn, channel, _graph = _run_failing_turn(
        tmp_path, exc=LLMProviderUnavailable("主备都不可用")
    )
    assert channel.latest("job1").payload["reason_code"] == REASON_PROVIDER_UNAVAILABLE


def test_the_needs_manual_queue_can_finally_see_it(tmp_path):
    """job_queries.py:224「今天恒为空，2.5 落地当天自动生效」——就是这一刻。
    ⚠️ 本用例⛔ 不改 app/web/server.py，只证明它读得到。"""
    conn, _channel, _graph = _run_failing_turn(tmp_path)
    status = conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0]

    reasons = derive_needs_manual_reasons(
        job_status=status, profile={}, revision_count=0, max_revisions=3
    )
    assert [reason["code"] for reason in reasons] == ["job_status"]
```

- [ ] **Step 2: 跑本文件**

Run: `venv/bin/python -m pytest tests/test_intake_needs_manual.py -q`
Expected: PASS，`9 passed`

- [ ] **Step 3: 跑全量 + 合规**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: `1176 passed, 1 failed, 1 skipped` —— 唯一的 failed 必须是既有的 `test_real_repository_dependency_diff_is_empty`（`tzdata` 遗留红）。**出现任何其他 failed 都是本单元的问题。**

Run: `venv/bin/python -m pytest tests/ -q -m compliance`
Expected: `67 passed`（既有 66 + 本单元的 `test_automatic_handoff_never_writes_a_human_review_row`）

- [ ] **Step 4: 提交**

```bash
git add tests/test_intake_needs_manual.py
git commit -m "test(intake): 转人工的重放幂等、effect_log 恒等与队列打通断言（WBS 2.5）"
```

---

## 范围外与登记（run-build 收尾时逐条写进报告，⛔ 不要顺手做掉）

1. **⏸ 前端不渲染 `needs_manual` 消息。** `app/web/static/index.html` 只认 `question` / `confirmation_prompt` / `jd_result`，转人工消息会被返回但不显示。⛔ 本单元不碰 `app/web/`（Global Constraints 13）。业务上的兜底是转人工队列（8.4，已打通，见 Task 6 的最后一个用例）。**需另开一个前端单元。**
2. **⏸ `analysis_run` 的切换事件行仍然查不到 `job_id`。** 采集路径至今不给网关传 `audit_context`（`docs/tech-debt.md` TD-1），切换事件行的 `job_id` / `thread_id` 同样是 NULL。⛔ 本单元不解决——那要碰 `app/agents/`。切换事件靠 `input_hash` + `attempt` 自成一组，**在本单元的范围内是自洽的**。
3. **⏸ `Settings.validate_model_version()` 对 `llm_model` 仍漏 `-latest` 写法。** 网关 `__init__` 兜得住（`_rejects_latest_alias` 三种都查），所以不是活的漏洞；放宽既有字段的校验口径会让 .51 上一份今天能起来的 `.env` 明天起不来，⛔ 不在本单元做。
4. **⏸ 无法在 .51 上验证真实的双供应商切换**：本项目只有 DeepSeek 一家账号（`docs/m1-model-comparison.md`；2026-08-11 Shao Peishen 显式拍板不等 doubao/qwen 补测账号）。**备用供应商选谁、要不要采购第二家账号 = 预算与外部采购，属不可代项，⛔ 代理人不得代拍。** 代码与单测照做，`.env` 的 `LLM_FALLBACK_*` 保持全空 ⇒ .51 上行为与今天逐字一致，**本单元不需要发版**。
5. **⚠️ WBS 2.3 在 `tasks.md` 里被标注为「已移出到『多供应商接入』」。** 本单元按 opener 的显式指定实现了它。run-build 收尾回勾时**须同时处理这条账目**：或把 2.3 划回本包并勾上，或在原位注明"已由 2026-09-08 的 unit2 交付"。⛔ 不要一边交付了行为、一边让 WBS 上仍写着"已移出"——那正是「代码完成但账目不对」的中间态。
6. **⚠️ 既有红：`tests/test_boundary_guard.py::test_real_repository_dependency_diff_is_empty`**（`bc1a0d0` 加 `tzdata==2026.3`，与守卫基线 `e65f685` 有 diff）。不是本单元引入，⛔ 不得为它改 `requirements.txt`。

---

## Self-Review

**1. spec 覆盖** —— `openspec/changes/m1-job-profile-intake/specs/job-profile-intake/spec.md`：

| Requirement / Scenario | 落在哪个 Task |
|---|---|
| 「采集过程审计留痕」· 记录生成快照（模型标识/版本/prompt 版本/temperature/输入哈希/原始响应/token 用量/生成时间） | Task 2（切换事件与备用方调用各记一行，字段一个不少；`configured_model` / `response_model` 分别取配置侧与响应侧） |
| 同上 · `temperature=0` 且模型版本显式锁定、不使用 latest 类别名 | Task 1（`Settings` 校验备用模型名）+ Task 2（`_rejects_latest_alias` 主备一视同仁；`temperature` 仍恒取 `LLMGateway.TEMPERATURE` 单一来源） |
| 「多轮追问补全」· 追问过程的失败兜底（本单元只覆盖"系统侧失败"这一支，用户不回复的 `abandoned` 支不在范围内） | Task 4 + Task 5（转 `needs_manual`，已采集内容保留） |
| 「结构化岗位画像产出」· 画像符合预定义 JSON Schema、校验通过 | Task 2（校验不过则重试；重试耗尽⛔ 不产出）+ Task 5（不落 `job_profile` 行） |

WBS 对应：**2.3 全部** + **2.5 全部**（`tasks.md` 原注写着 2.5 的重试与"不产出半成品"两半已实现、"转 `needs_manual`"完全没有实现——本单元补的就是缺的那一半，并顺带把它接上 8.4 的队列）。

**2. 占位符扫描** —— 全文无 TBD / TODO / "适当处理错误" / "类似 Task N"。每个代码步骤都给了完整可粘贴的代码块与确切的插入位置。

**3. 类型一致性** —— 跨 Task 的名字逐个对过：`LLMProviderUnavailable`、`PROVIDER_SWITCH_EVENT`、`_Provider(role/client/model/supports_json_schema)`、`fallback_api_key` / `fallback_base_url` / `fallback_model` / `fallback_supports_json_schema` / `fallback_client`、`llm_fallback_*` 四个 `Settings` 字段、`REASON_SCHEMA_EXHAUSTED` / `REASON_PROVIDER_UNAVAILABLE`、`effect_mark_needs_manual` / `effect_deliver_manual_handoff` 的关键字签名、`needs_manual` / `needs_manual_reason_code` 两个 state 键、`business_key = str(round_count)`。Task 2→3、Task 4→5、Task 5→6 的 Interfaces 块两两对齐。

**下一步：** 用 `run-build` 执行本计划。⛔ 本次响应不开始实现。
