# M1 第 0 章 · 内网 Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让业务经理在浏览器里用一句话提用人需求，经多轮追问后拿到结构化岗位画像与可复制的 JD——`job-profile-intake` + `job-description` 两个 capability 的真实实现，Web-only、SQLite、单供应商，不碰候选人个人信息；应用从第一天起就路径前缀感知，可挂载到任意反向代理子路径下，并部署到无 Docker 的 Windows 服务器。

**Architecture:** FastAPI 提供单页 Web 前端与 REST 接口，所有路由注册在一个 `APIRouter` 上，`create_app` 用可配置的 `root_path`（默认 `/hr/recruit-agent`）把整个 router 挂载到对应前缀下——不依赖任何反向代理做路径剥离，应用自己就是路径前缀感知的；前端页面通过服务端注入的 `<base href>` 与统一使用相对路径的 `fetch` 调用，实现"挂到哪个前缀下都能正常工作"。LangGraph（`SqliteSaver` checkpointer）承载「采集」与「JD 生成」两条流程，节点严格区分 `compute_*`（纯函数，LLM 调用）与 `effect_*`（写库/投递消息，独占节点 + 幂等键）；LLM 网关薄封装单供应商调用，`temperature=0`、模型版本显式锁定；Channel 抽象层今天只实现 Web，为后续接企微留好扩展点；鉴权中间件留空壳直通接入点，为后续企微 OAuth SSO 留好签名；部署形态为 Windows venv + 计划任务，不依赖 Docker。

**Tech Stack:** Python 3.11+、FastAPI、LangGraph ≥1.0.10（`langgraph-checkpoint-sqlite`）、Pydantic v2、`openai` SDK（指向境内供应商的 OpenAI 兼容端点）、SQLite（`sqlite3` 标准库）、pytest、httpx（TestClient）、PowerShell 5.1+（Windows 部署脚本）。

## Global Constraints

以下条目逐字来自项目 `CLAUDE.md`「工程铁律」「合规红线」与「部署约束」，本计划每个 Task 隐含都要遵守：

- **工程铁律 1**：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
- **工程铁律 2**：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
- **工程铁律 5（2026-08-09 现行版，本计划生成后更新，以此为准）**：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。锁不住版本时，至少要记得住版本。
  - **Task 4 专属追加要求**（派生自本条，计划原文未覆盖）：`LLMGateway` 必须把 API 响应中实际返回的 `model` 字段（OpenAI 兼容响应体标准字段 `response.model`）与构造函数传入的配置 `model`（`self._model`）分开记录，两者都要传给 `AuditHook.record()`；`AuditHook.record()` 签名须新增参数承载响应实际值（如 `response_model`），不得用配置值代替或省略。Task 4 原始 Step 1/Step 4 文本已按此更新，以更新后的版本为准。
- **工程铁律 7**：`langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。
- **部署约束 1**：路径前缀就绪。FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用一律相对路径，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
- **部署约束 2**：过渡端口 8095，登记技术债，触发条件 = 统一门户网关上线即迁移。
- **部署约束 3**：鉴权中间件留空壳接入点，签名对齐未来企微 OAuth SSO；将来只换实现不换调用方。
- **部署约束 4**：目标服务器是 Windows，没有 Docker。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
- **部署约束 5**：M2 起处理真实简历前，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。
- **合规红线**：AI 生成的 JD、拒信、邀约须带标识（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **合规红线**：模型全部走境内，数据不出境。
- **合规红线**：主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。

### Reviewer Checklist（每个 Task review 都要过一遍，非仅列出的相关 Task）

1. 每个 `effect_*` 节点是否独占、是否带幂等键 `{thread_id}:{node_name}:{business_key}`（工程铁律 1）
2. `compute_*` 节点是否真的无副作用（工程铁律 2）
3. LLM 调用是否 `temperature=0`；响应返回的实际 `model` 字段是否被记录、且与配置的 `model` 分开持久化（工程铁律 5 现行版 + Task 4 专属追加要求）
4. 前端有没有硬编码 `/static/` 或 `/api/`（挂到子路径下会全线 404，部署约束 1）
5. JD 生成是否带 AI 标识、是否有歧视性表述拦截（合规红线）
6. 写 `criterion_score` 处 `evidence_ref` 为空是否被拒绝；是否存在任何路径产生 `reason_type='ai_score'` 的淘汰记录（必须没有）——**本单元 Out of Scope 不实现候选人评分/淘汰**（见下方 Out of Scope 与 技术债 1），这两条本计划范围内应为 N/A，reviewer 确认代码库中确无相关落地即可，不必强求专门测试覆盖。

## Out of Scope（本单元明确不做，附对应 spec Requirement）

- **`job-profile-intake` /「硬门槛规则草案提取」** —— 完全不实现（含 Schema 里也不预留提取字段），属于 `tasks.md` 第 5 章（5.8/5.9）。
- **`job-profile-intake` /「采集过程审计留痕」** —— 只在 LLM 网关留一个可插拔的 `AuditHook`（默认 no-op），不落 `analysis_run` 表。完整实现是技术债。
- **`job-profile-intake` /「多轮追问补全」Scenario「业务经理中途放弃」**（3 个工作日提醒、再 3 天 `abandoned`）—— demo 是单次浏览器会话内的同步对话，没有跨天异步提醒机制。`status=abandoned` 状态位保留在 Schema 里，但没有调度器去触发它；这个 Scenario 要等企业微信通道（第 3 章）接入后才有意义。
- **`job-description` /「AI 生成内容标识」Scenario「标识不可被移除」的编辑保护部分** —— demo 的 Web 界面只做 JD 查看与复制，不提供编辑功能，因此标识没有被"常规编辑"移除的路径；"标记为人工撰写"这个显式操作本单元不做，留到编辑功能上线时一并补。
- **企业微信通道本体** —— 只做 `Channel` 抽象接口，不实现 WeChat 的具体收发。
- **Postgres checkpointer** —— 用 `SqliteSaver` 代替。
- **企微 OAuth SSO 的真实鉴权逻辑** —— `AuthMiddleware` 本单元只做无条件直通，不校验任何身份，不拒绝任何请求。
- **统一门户网关对接** —— 本单元只做到"应用自己路径前缀感知，随时可被 `proxy_pass`"，不实现网关本身，也不做网关侧配置（那是 Paul 在「企业AI转型」仓库的工作）。

## 技术债（需在进入 `tasks.md` 第 1 章前排期）

1. `analysis_run` 完整审计留痕（模型标识/版本/prompt 版本/输入哈希/原始响应/token 用量落库，现在只有 no-op 钩子）
2. Postgres checkpointer 迁移（现在是 `SqliteSaver`）
3. 企业微信通道接入（`Channel` 抽象已就位，只差 WeChat 实现）
4. 多轮修改历史（现在画像草案每轮覆盖，不留旧版本）
5. `needs_manual` 处理队列 UI（现在只落状态位，没有专门处理界面）
6. 「业务经理中途放弃」的跨天提醒调度
7. JD 编辑功能 + 标识保护 + "标记为人工撰写"显式操作
8. **迁移到统一门户网关**：过渡期占用 8095 直连，触发条件 = 统一门户网关上线即迁移。届时网关只需对 `root_path` 对应前缀加一条 `proxy_pass`，应用本身零改动（部署约束 2）
9. **可识别到人的登录 + 简历访问留痕**：M2 起处理真实简历前必须补上——「谁、什么时候、看了哪份简历」必须可查，PIPL 要求。Demo 阶段（本单元）沿用门户现有共享口令即可，共享口令不满足这条要求（部署约束 5，详见 `04-部署与门户挂载.md` §4 风险二）。鉴权中间件的空壳接入点本单元已留出（见 Task 1 `AuthMiddleware`），届时只换 `dispatch` 内部实现，不改调用方签名。这需要单独开一个 OpenSpec change（`m2-auth-and-access-log`），不并进 M1

---

### Task 1: 项目脚手架与配置（含路径前缀感知 + 鉴权中间件空壳）

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/middleware/__init__.py`
- Create: `app/middleware/auth.py`
- Create: `.env.example`
- Create: `.gitignore`（若已存在则在末尾追加，见 Step 1 说明）
- Test: `tests/test_config.py`
- Test: `tests/test_auth_middleware.py`

**Interfaces:**
- Produces: `app.config.Settings`（Pydantic `BaseSettings`），字段：`llm_provider: str`、`llm_api_key: str`、`llm_base_url: str`、`llm_model: str`、`llm_supports_json_schema: bool`、`db_path: str`（默认 `data/demo.db`）、`root_path: str`（默认 `/hr/recruit-agent`，对应环境变量 `ROOT_PATH`，部署约束 1）。`Settings.validate_model_version()` 方法：模型名等于 `"latest"` 或以 `":latest"` 结尾则抛 `ValueError`。
- Produces: `app.config.get_settings() -> Settings`（读取 `.env`，供后续所有任务 `from app.config import get_settings` 使用）。
- Produces: `app.middleware.auth.AuthContext`（`dataclass`：`user_id: str | None`、`authenticated: bool`）。
- Produces: `app.middleware.auth.AuthMiddleware`（`starlette.middleware.base.BaseHTTPMiddleware` 子类）：demo 阶段无条件放行所有请求，把 `AuthContext(user_id=None, authenticated=False)` 写入 `request.state.auth`。这是部署约束 3 的空壳接入点，被 Task 10 的 `create_app` 挂载；未来切换真实企微 OAuth SSO 时只替换 `dispatch` 内部逻辑，路由处理函数读取 `request.state.auth` 的方式不变。

- [ ] **Step 1: 检查现有 `.gitignore`，确认 `.env`、`data/`、`__pycache__/`、`.pytest_cache/` 已被忽略**

先读现有文件：

```bash
cat /Users/paulshao/Projects/HumanResource/.gitignore
```

若缺失下列任一行，追加进去（不要删除已有内容）：

```
.env
data/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 2: 写 `requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
pydantic-settings==2.7.1
langgraph==1.0.10
langgraph-checkpoint-sqlite==2.0.6
openai==1.59.6
python-dotenv==1.0.1
pytest==8.3.4
httpx==0.28.1
```

- [ ] **Step 3: 写 `pyproject.toml`**

```toml
[project]
name = "zhuopin-recruiting-agent"
version = "0.1.0-demo"
requires-python = ">=3.11"

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 4: 写 `.env.example`**

```
# 单供应商配置（0.1 模型对比实测后填入真实值，见 scripts/compare_models.py 的产出）
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-replace-me
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_SUPPORTS_JSON_SCHEMA=false
DB_PATH=data/demo.db

# 反向代理挂载路径前缀（部署约束1）。应用自己把全部路由挂到这个前缀下，
# 不依赖任何反向代理做路径剥离——网关上线时只需对准这个前缀加一条 proxy_pass。
# 若本地开发直接用 http://localhost:8095/ 访问、不经过任何前缀，改成空字符串。
ROOT_PATH=/hr/recruit-agent
```

- [ ] **Step 5: 写 `app/__init__.py`（空文件）**

```python
```

- [ ] **Step 6: 写 `app/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_supports_json_schema: bool = False
    db_path: str = "data/demo.db"
    root_path: str = "/hr/recruit-agent"

    def validate_model_version(self) -> None:
        if self.llm_model == "latest" or self.llm_model.endswith(":latest"):
            raise ValueError(
                f"禁止使用 latest 类别名锁定模型版本，收到: {self.llm_model!r}"
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_model_version()
    return settings
```

- [ ] **Step 7: 写测试 `tests/test_config.py`**

```python
import pytest

from app.config import Settings


def test_rejects_latest_model_alias():
    settings = Settings(llm_model="latest")
    with pytest.raises(ValueError, match="latest"):
        settings.validate_model_version()


def test_rejects_provider_latest_suffix():
    settings = Settings(llm_model="deepseek-chat:latest")
    with pytest.raises(ValueError, match="latest"):
        settings.validate_model_version()


def test_accepts_pinned_version():
    settings = Settings(llm_model="deepseek-chat-241226")
    settings.validate_model_version()  # 不应抛异常


def test_default_root_path_is_hr_recruit_agent():
    settings = Settings()
    assert settings.root_path == "/hr/recruit-agent"


def test_root_path_overridable_via_env(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "/foo/bar")
    settings = Settings()
    assert settings.root_path == "/foo/bar"
```

- [ ] **Step 8: 安装依赖并运行测试确认通过**

```bash
cd /Users/paulshao/Projects/HumanResource
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest tests/test_config.py -v
```

Expected: 5 个测试全部 PASS（此时代码已写好，这一步用于确认环境装好）。

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml requirements.txt app/__init__.py app/config.py .env.example .gitignore tests/test_config.py
git commit -m "chore: 项目脚手架与配置（模型版本锁定校验 + root_path 支持）"
```

- [ ] **Step 10: 写失败测试（鉴权中间件空壳）**

```python
# tests/test_auth_middleware.py
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.auth import AuthMiddleware


def _make_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/probe")
    def probe(request: Request):
        return {
            "user_id": request.state.auth.user_id,
            "authenticated": request.state.auth.authenticated,
        }

    return app


def test_auth_middleware_sets_unauthenticated_context_by_default():
    client = TestClient(_make_probe_app())
    resp = client.get("/probe")
    assert resp.status_code == 200
    assert resp.json() == {"user_id": None, "authenticated": False}


def test_auth_middleware_does_not_block_any_request():
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/anything")
    def anything():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/anything")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
```

- [ ] **Step 11: 运行确认失败**

```bash
pytest tests/test_auth_middleware.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.middleware'`

- [ ] **Step 12: 写 `app/middleware/__init__.py`（空文件）**

```python
```

- [ ] **Step 13: 写 `app/middleware/auth.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


@dataclass
class AuthContext:
    """
    当前请求的鉴权上下文。demo 阶段恒为未鉴权直通（user_id=None）。
    企微 OAuth SSO 接入时，只替换 AuthMiddleware.dispatch 内部的解析逻辑，
    调用方（路由处理函数）读取 request.state.auth 的方式不变。
    """

    user_id: str | None
    authenticated: bool


class AuthMiddleware(BaseHTTPMiddleware):
    """
    鉴权中间件空壳接入点（部署约束 3）。demo 阶段不校验、不拒绝任何请求，
    无条件放行；user_id 恒为 None。签名对齐未来企微 OAuth SSO：
    真实实现落地时只替换 dispatch 内部逻辑，路由处理函数读取
    request.state.auth 的方式保持不变。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.auth = AuthContext(user_id=None, authenticated=False)
        return await call_next(request)
```

- [ ] **Step 14: 运行确认通过**

```bash
pytest tests/test_auth_middleware.py -v
```

Expected: 2 个测试全部 PASS

- [ ] **Step 15: Commit**

```bash
git add app/middleware/ tests/test_auth_middleware.py
git commit -m "feat: 鉴权中间件空壳接入点（部署约束3，对齐未来企微OAuth SSO）"
```

---

### Task 2: 岗位画像 Pydantic Schema

**Files:**
- Create: `app/schemas/__init__.py`
- Create: `app/schemas/job_profile.py`
- Test: `tests/test_job_profile_schema.py`

**Interfaces:**
- Produces: `app.schemas.job_profile.JobProfile`（Pydantic `BaseModel`）、`SkillItem`、`SopProject`、`AutosarLayer`（Enum）、`FunctionalSafetyLevel`（Enum）、`JobStatus`（Enum：`drafting`/`needs_manual`/`approved`/`abandoned`）。这些类型被 Task 4（LLM 网关）、Task 7（需求解析 Agent）、Task 8（JD Agent）、Task 9（图节点）原样引用。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_job_profile_schema.py
import pytest
from pydantic import ValidationError

from app.schemas.job_profile import (
    AutosarLayer,
    FunctionalSafetyLevel,
    JobProfile,
    JobStatus,
    SkillItem,
    SopProject,
)


def test_minimal_valid_profile():
    profile = JobProfile(
        job_title="嵌入式软件工程师",
        department="研发部",
        headcount=1,
        education_requirement="本科及以上",
        experience_years="3-5年",
        core_skills=[SkillItem(name="AUTOSAR CP", required=True)],
        soft_skill_keywords=["沟通能力强"],
        autosar_experience=[AutosarLayer.CP],
        functional_safety=FunctionalSafetyLevel.ASIL_B,
        mcu_family=["英飞凌 Aurix TC3xx"],
        diag_stack=["UDS", "CAN-FD"],
        sop_projects=[
            SopProject(vehicle_model="X1", role="核心开发", is_mass_production=True)
        ],
        toolchain=["CANoe", "Vector"],
    )
    assert profile.headcount == 1
    assert profile.autosar_experience == [AutosarLayer.CP]


def test_headcount_must_be_positive():
    with pytest.raises(ValidationError):
        JobProfile(
            job_title="x",
            department="x",
            headcount=0,
            education_requirement="x",
            experience_years="x",
        )


def test_defaults_are_empty_not_none():
    profile = JobProfile(
        job_title="x",
        department="x",
        headcount=1,
        education_requirement="x",
        experience_years="x",
    )
    assert profile.core_skills == []
    assert profile.autosar_experience == []
    assert profile.unspecified_fields == []


def test_job_status_enum_values():
    assert {s.value for s in JobStatus} == {
        "drafting",
        "needs_manual",
        "approved",
        "abandoned",
    }
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_job_profile_schema.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.schemas'`

- [ ] **Step 3: 写 `app/schemas/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写 `app/schemas/job_profile.py`**

```python
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    DRAFTING = "drafting"
    NEEDS_MANUAL = "needs_manual"
    APPROVED = "approved"
    ABANDONED = "abandoned"


class AutosarLayer(str, Enum):
    CP = "CP"
    AP = "AP"
    NONE = "无"


class FunctionalSafetyLevel(str, Enum):
    ASIL_A = "ASIL-A"
    ASIL_B = "ASIL-B"
    ASIL_C = "ASIL-C"
    ASIL_D = "ASIL-D"
    NONE = "无"
    CERTIFIED_ENGINEER = "FuSa工程师认证"


class SkillItem(BaseModel):
    name: str
    required: bool  # True = 必会, False = 加分


class SopProject(BaseModel):
    vehicle_model: str
    sop_date: str | None = None
    role: str
    is_mass_production: bool


class JobProfile(BaseModel):
    # 通用字段
    job_title: str
    department: str
    headcount: int = Field(ge=1)
    education_requirement: str
    experience_years: str  # 保留字符串以容纳"3-5年"这类区间表达
    core_skills: list[SkillItem] = Field(default_factory=list)
    project_experience_requirement: str | None = None
    soft_skill_keywords: list[str] = Field(default_factory=list)

    # ECU 行业特化字段
    autosar_experience: list[AutosarLayer] = Field(default_factory=list)
    functional_safety: FunctionalSafetyLevel = FunctionalSafetyLevel.NONE
    mcu_family: list[str] = Field(default_factory=list)
    diag_stack: list[str] = Field(default_factory=list)
    sop_projects: list[SopProject] = Field(default_factory=list)
    toolchain: list[str] = Field(default_factory=list)

    # 追问超限降级时标记哪些字段是"未指定"填充的
    unspecified_fields: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: 运行确认通过**

```bash
pytest tests/test_job_profile_schema.py -v
```

Expected: 4 个测试全部 PASS

- [ ] **Step 6: Commit**

```bash
git add app/schemas/__init__.py app/schemas/job_profile.py tests/test_job_profile_schema.py
git commit -m "feat: 岗位画像 Pydantic Schema（通用字段 + ECU 特化字段）"
```

---

### Task 3: SQLite 存储层与幂等基础设施（`effect_log`）

**Files:**
- Create: `app/storage/__init__.py`
- Create: `app/storage/db.py`
- Create: `app/storage/idempotency.py`
- Test: `tests/test_db.py`
- Test: `tests/test_idempotency.py`

**Interfaces:**
- Produces: `app.storage.db.get_connection(db_path: str) -> sqlite3.Connection`、`app.storage.db.init_schema(conn) -> None`（建表：`job`、`job_profile`、`effect_log`、`outbox`）。
- Produces: `app.storage.idempotency.idempotent_effect(node_name: str)` —— 装饰器工厂。被装饰函数签名约定为 `fn(conn: sqlite3.Connection, thread_id: str, business_key: str, **kwargs) -> Any`。装饰后：若 `{thread_id}:{node_name}:{business_key}` 已在 `effect_log`，直接返回 `None` 并跳过原函数；否则执行原函数、写 `effect_log`、提交事务。
- Produces: `app.storage.idempotency.EffectAlreadyApplied`（可选：本任务不主动抛出，仅保留供后续需要"必须是首次执行"语义的调用方使用）。

- [ ] **Step 1: 写失败测试（建表与幂等装饰器）**

```python
# tests/test_db.py
import sqlite3

from app.storage.db import get_connection, init_schema


def test_init_schema_creates_all_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"job", "job_profile", "effect_log", "outbox"} <= tables


def test_effect_log_has_unique_index_on_effect_key(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
        "VALUES ('job1:effect_x:1', 'job1', 'effect_x', '1', datetime('now'))"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES ('job1:effect_x:1', 'job1', 'effect_x', '1', datetime('now'))"
        )
        conn.commit()


import pytest  # noqa: E402  (保持在文件顶部导入也可，这里为可读性放在使用前)
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_db.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.storage'`

- [ ] **Step 3: 写 `app/storage/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写 `app/storage/db.py`**

```python
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_profile (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    unspecified_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_log_key ON effect_log (effect_key);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI dispatches sync route handlers into a
    # worker threadpool (a different thread per request), but create_app()
    # holds one shared connection created on the startup thread. Demo scope
    # has no concurrent-write requirement (design.md 非目标: 不追求高并发);
    # M2's move to Postgres replaces this with per-request pooled connections.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
```

- [ ] **Step 5: 运行确认通过**

```bash
pytest tests/test_db.py -v
```

Expected: 2 个测试全部 PASS

- [ ] **Step 6: 写失败测试（幂等装饰器）**

```python
# tests/test_idempotency.py
from app.storage.db import get_connection, init_schema
from app.storage.idempotency import idempotent_effect


def test_effect_runs_once_on_first_call(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    calls = []

    @idempotent_effect("effect_send_something")
    def send(conn, thread_id, business_key):
        calls.append((thread_id, business_key))
        return "sent"

    result = send(conn, thread_id="job1", business_key="v1")
    assert result == "sent"
    assert calls == [("job1", "v1")]


def test_effect_skipped_on_replay_with_same_business_key(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    calls = []

    @idempotent_effect("effect_send_something")
    def send(conn, thread_id, business_key):
        calls.append((thread_id, business_key))
        return "sent"

    send(conn, thread_id="job1", business_key="v1")
    result_second = send(conn, thread_id="job1", business_key="v1")

    assert len(calls) == 1  # 副作用只发生一次
    assert result_second is None  # 第二次是跳过，不是重新执行


def test_different_business_key_runs_independently(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    calls = []

    @idempotent_effect("effect_send_something")
    def send(conn, thread_id, business_key):
        calls.append(business_key)

    send(conn, thread_id="job1", business_key="v1")
    send(conn, thread_id="job1", business_key="v2")

    assert calls == ["v1", "v2"]
```

- [ ] **Step 7: 运行确认失败**

```bash
pytest tests/test_idempotency.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.storage.idempotency'`

- [ ] **Step 8: 写 `app/storage/idempotency.py`**

```python
import functools
import sqlite3
from typing import Callable, TypeVar

T = TypeVar("T")


def idempotent_effect(node_name: str) -> Callable[[Callable[..., T]], Callable[..., T | None]]:
    """
    装饰一个 effect_* 节点函数。被装饰函数必须接受
    (conn: sqlite3.Connection, thread_id: str, business_key: str, **kwargs) 签名。

    幂等键 = f"{thread_id}:{node_name}:{business_key}"，命中 effect_log 则跳过、返回 None。
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T | None]:
        @functools.wraps(fn)
        def wrapper(
            conn: sqlite3.Connection, *, thread_id: str, business_key: str, **kwargs
        ) -> T | None:
            effect_key = f"{thread_id}:{node_name}:{business_key}"
            existing = conn.execute(
                "SELECT 1 FROM effect_log WHERE effect_key = ?", (effect_key,)
            ).fetchone()
            if existing is not None:
                return None

            result = fn(conn, thread_id=thread_id, business_key=business_key, **kwargs)

            conn.execute(
                "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (effect_key, thread_id, node_name, business_key),
            )
            conn.commit()
            return result

        return wrapper

    return decorator
```

- [ ] **Step 9: 运行确认通过**

```bash
pytest tests/test_idempotency.py tests/test_db.py -v
```

Expected: 5 个测试全部 PASS

- [ ] **Step 10: Commit**

```bash
git add app/storage/ tests/test_db.py tests/test_idempotency.py
git commit -m "feat: SQLite 存储层与 effect_log 幂等装饰器（工程铁律1）"
```

---

### Task 4: LLM 网关最小版

**Files:**
- Create: `app/llm/__init__.py`
- Create: `app/llm/gateway.py`
- Test: `tests/test_llm_gateway.py`

**Interfaces:**
- Consumes: `app.config.Settings`（Task 1）。
- Produces: `app.llm.gateway.LLMGateway`，构造签名：
  `LLMGateway(api_key: str, base_url: str, model: str, supports_json_schema: bool, max_retries: int = 2, audit_hook: AuditHook | None = None, client=None)`。
  方法：`extract_structured(self, *, system_prompt: str, user_prompt: str, schema: type[BaseModel], prompt_version: str = "v1") -> BaseModel`，失败抛 `SchemaExtractionFailed`。
- Produces: `app.llm.gateway.SchemaExtractionFailed(Exception)`。
- Produces: `app.llm.gateway.AuditHook`（`Protocol`，方法 `record(self, *, model, response_model, prompt_version, input_hash, raw_response, token_usage, latency_ms) -> None`）与默认实现 `NoopAuditHook`（只 `logging.debug`，是本单元「采集过程审计留痕」的技术债占位——见计划开头 技术债）。
- 构造函数拒绝 `model` 以 `latest` 结尾或等于 `"latest"`（复用 Task 1 的校验逻辑，与铁律 5 对应）。
- **铁律 5（2026-08-09 现行版）**：`model` 是构造函数传入的配置值（`self._model`，可能是会漂移的供应商别名，如 `deepseek-chat`）；`response_model` 是本次调用 API 响应里实际返回的 `model` 字段（OpenAI 兼容响应体标准字段 `response.model`，供应商不提供版本快照时，这是唯一能确定"这次到底是哪个模型打的分"的信息源）。两者必须分开传给 `AuditHook.record()`，不得用配置值代替响应值，也不得省略。响应对象没有 `model` 属性时，`response_model` 记 `None`，不得抛异常掩盖问题。
- 测试通过 `client=FakeOpenAIClient(...)` 注入假客户端，不发真实网络请求。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_gateway.py
import json
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from app.llm.gateway import LLMGateway, SchemaExtractionFailed


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
    model: str = "deepseek-chat-241226"
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeChatCompletions:
    def __init__(self, responses: list[str], response_model: str = "deepseek-chat-241226"):
        self._responses = list(responses)
        self._response_model = response_model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(
            choices=[FakeChoice(message=FakeMessage(content=content))],
            model=self._response_model,
        )


class FakeChat:
    def __init__(self, responses, response_model: str = "deepseek-chat-241226"):
        self.completions = FakeChatCompletions(responses, response_model=response_model)


class FakeOpenAIClient:
    def __init__(self, responses: list[str], response_model: str = "deepseek-chat-241226"):
        self.chat = FakeChat(responses, response_model=response_model)


def test_rejects_latest_model_alias():
    with pytest.raises(ValueError, match="latest"):
        LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="latest",
            supports_json_schema=False,
            client=FakeOpenAIClient([]),
        )


def test_extracts_valid_json_on_first_try():
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    result = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point
    )

    assert result == Point(x=1, y=2)
    assert client.chat.completions.calls[0]["temperature"] == 0
    assert client.chat.completions.calls[0]["model"] == "deepseek-chat-241226"


def test_retries_on_invalid_json_then_succeeds():
    client = FakeOpenAIClient(
        ["not json", json.dumps({"x": 3, "y": 4})]
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    result = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point
    )

    assert result == Point(x=3, y=4)
    assert len(client.chat.completions.calls) == 2


def test_raises_after_max_retries_exhausted():
    client = FakeOpenAIClient(["not json", "still not json", "nope"])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        max_retries=2,
        client=client,
    )

    with pytest.raises(SchemaExtractionFailed):
        gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert len(client.chat.completions.calls) == 3  # 首次 + 2 次重试


def test_audit_hook_called_with_expected_fields():
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    recorded = []

    class RecordingHook:
        def record(self, **kwargs):
            recorded.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point, prompt_version="v2"
    )

    assert len(recorded) == 1
    call = recorded[0]
    assert call["model"] == "deepseek-chat-241226"
    assert call["response_model"] == "deepseek-chat-241226"
    assert call["prompt_version"] == "v2"
    assert "raw_response" in call and "latency_ms" in call and "token_usage" in call


def test_audit_hook_records_actual_response_model_separately_from_configured():
    """
    工程铁律 5（2026-08-09 现行版）：DeepSeek 这类供应商只给会漂移的别名
    （如 deepseek-chat），配置里写的名字不算数，必须记住 API 响应实际
    返回的 model 字段——且要和配置值分开存，不能互相代替。
    """
    client = FakeOpenAIClient(
        [json.dumps({"x": 1, "y": 2})],
        response_model="deepseek-chat-241226",
    )
    recorded = []

    class RecordingHook:
        def record(self, **kwargs):
            recorded.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",  # 配置里写的是会漂移的别名
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert len(recorded) == 1
    call = recorded[0]
    assert call["model"] == "deepseek-chat"                   # 配置值
    assert call["response_model"] == "deepseek-chat-241226"   # API 实际返回值
    assert call["model"] != call["response_model"]


def test_json_schema_mode_sets_response_format():
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="doubao-seed-2.1-turbo-241215",
        supports_json_schema=True,
        client=client,
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    response_format = client.chat.completions.calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Point"
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_llm_gateway.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.llm'`

- [ ] **Step 3: 写 `app/llm/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写 `app/llm/gateway.py`**

```python
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SchemaExtractionFailed(Exception):
    """重试耗尽后仍未拿到符合 Schema 的结构化输出。"""


class AuditHook(Protocol):
    def record(
        self,
        *,
        model: str,
        response_model: str | None,
        prompt_version: str,
        input_hash: str,
        raw_response: str,
        token_usage: dict[str, Any],
        latency_ms: float,
    ) -> None: ...


class NoopAuditHook:
    """
    默认审计钩子：只打日志，不落库。
    完整的 analysis_run 持久化是技术债（见计划开头 技术债），
    这里保留可插拔的调用点，接线时只需替换这一个实现。
    """

    def record(self, **kwargs: Any) -> None:
        logger.debug("audit_hook(noop): %s", kwargs)


class LLMGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        supports_json_schema: bool,
        max_retries: int = 2,
        audit_hook: AuditHook | None = None,
        client: Any = None,
    ) -> None:
        if model == "latest" or model.endswith(":latest") or model.endswith("-latest"):
            raise ValueError(f"禁止使用 latest 类别名锁定模型版本，收到: {model!r}")

        self._model = model
        self._supports_json_schema = supports_json_schema
        self._max_retries = max_retries
        self._audit_hook = audit_hook or NoopAuditHook()
        self._client = client or OpenAI(api_key=api_key, base_url=base_url)

    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        prompt_version: str = "v1",
    ) -> T:
        input_hash = hashlib.sha256(
            f"{system_prompt}\n{user_prompt}".encode("utf-8")
        ).hexdigest()

        last_error: Exception | None = None
        attempts = self._max_retries + 1

        for _ in range(attempts):
            started = time.monotonic()
            response = self._call_model(system_prompt, user_prompt, schema)
            latency_ms = (time.monotonic() - started) * 1000
            raw_content = response.choices[0].message.content

            # 铁律 5（2026-08-09 现行版）：response.model 是 API 实际返回的模型标识，
            # 与构造函数传入的配置值 self._model 分开记录——配置里写的名字不算数，
            # 供应商静默升级 deepseek-chat 这类别名时，只有响应里的值可信。
            response_model = getattr(response, "model", None)

            usage = getattr(response, "usage", None)
            token_usage = (
                {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                }
                if usage is not None
                else {}
            )

            self._audit_hook.record(
                model=self._model,
                response_model=response_model,
                prompt_version=prompt_version,
                input_hash=input_hash,
                raw_response=raw_content,
                token_usage=token_usage,
                latency_ms=latency_ms,
            )

            try:
                data = json.loads(raw_content)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                continue

        raise SchemaExtractionFailed(
            f"{attempts} 次尝试后仍未通过 Schema 校验（{schema.__name__}）: {last_error}"
        ) from last_error

    def _call_model(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]):
        if self._supports_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        else:
            response_format = {"type": "json_object"}
            messages = [
                {
                    "role": "system",
                    "content": system_prompt + "\n只输出合法 JSON，不要输出任何其他文字。",
                },
                {"role": "user", "content": user_prompt},
            ]

        return self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=messages,
            response_format=response_format,
        )
```

- [ ] **Step 5: 运行确认通过**

```bash
pytest tests/test_llm_gateway.py -v
```

Expected: 7 个测试全部 PASS（含 2026-08-09 新增的 `test_audit_hook_records_actual_response_model_separately_from_configured`）

- [ ] **Step 6: Commit**

```bash
git add app/llm/ tests/test_llm_gateway.py
git commit -m "feat: LLM 网关最小版（temperature=0、版本锁定、schema校验+重试、可插拔审计钩子）"
```

---

### Task 5: 模型对比实测脚本与决策记录

**Files:**
- Create: `scripts/compare_models.py`
- Create: `docs/m1-model-comparison.md`
- Test: `tests/test_compare_models.py`

**Interfaces:**
- Consumes: `app.llm.gateway.LLMGateway`（Task 4）、`app.schemas.job_profile.JobProfile`（Task 2）。
- Produces: `scripts.compare_models.PROVIDER_CANDIDATES: list[ProviderConfig]`（`ProviderConfig` 是本文件内定义的 `dataclass`，字段 `name, api_key_env, base_url, model, supports_json_schema`）。
- Produces: `scripts.compare_models.run_comparison(sample_text: str, providers: list[ProviderConfig]) -> list[ComparisonResult]`，`ComparisonResult` 字段：`provider_name, schema_valid: bool, latency_ms: float, raw_output: str, error: str | None`。

这一步是实测（0.1），不是纯代码任务：脚本与测试用假 LLM 客户端验证「聚合与报告生成」的逻辑正确；**真实的供应商对比数据必须由工程师用真实 API Key 跑一遍 `scripts/compare_models.py` 后填进 `docs/m1-model-comparison.md`**，不要在这里编造评测数字。跑完之后把选中的供应商填回 `.env`，供 Task 4 起的网关及后续所有任务使用。

- [ ] **Step 1: 写失败测试（聚合逻辑，用假网关，不发真实请求）**

```python
# tests/test_compare_models.py
from scripts.compare_models import ComparisonResult, ProviderConfig, summarize


def test_summarize_picks_provider_with_all_schema_valid_and_lowest_latency():
    results = [
        ComparisonResult(
            provider_name="a", schema_valid=True, latency_ms=800, raw_output="{}", error=None
        ),
        ComparisonResult(
            provider_name="b", schema_valid=True, latency_ms=300, raw_output="{}", error=None
        ),
        ComparisonResult(
            provider_name="c", schema_valid=False, latency_ms=100, raw_output="bad", error="invalid json"
        ),
    ]

    summary = summarize(results)

    assert summary.recommended_provider == "b"
    assert summary.disqualified == ["c"]


def test_summarize_raises_when_no_provider_passes_schema():
    results = [
        ComparisonResult(
            provider_name="a", schema_valid=False, latency_ms=100, raw_output="x", error="bad"
        ),
    ]

    import pytest

    with pytest.raises(ValueError, match="没有供应商通过"):
        summarize(results)
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_compare_models.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'scripts'`

- [ ] **Step 3: 写 `scripts/__init__.py`（空文件，使其可被当作包导入）**

```python
```

- [ ] **Step 4: 写 `scripts/compare_models.py`**

```python
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from app.llm.gateway import LLMGateway, SchemaExtractionFailed
from app.schemas.job_profile import JobProfile

SAMPLE_REQUIREMENT = "要个做嵌入式开发的，能写驱动，最好懂 AUTOSAR"

EXTRACTION_SYSTEM_PROMPT = (
    "你是招聘助手，把业务经理的口语化用人需求转成结构化岗位画像 JSON，"
    "字段需符合给定 Schema，缺失信息用合理默认值填充，不要编造具体项目经验。"
)


@dataclass
class ProviderConfig:
    name: str
    api_key_env: str
    base_url: str
    model: str
    supports_json_schema: bool


@dataclass
class ComparisonResult:
    provider_name: str
    schema_valid: bool
    latency_ms: float
    raw_output: str
    error: str | None


@dataclass
class ComparisonSummary:
    recommended_provider: str
    disqualified: list[str]
    results: list[ComparisonResult]


# 候选供应商，来自 01-开源调研与技术选型.md 的候选名单，实测前只是候选，不是结论。
PROVIDER_CANDIDATES: list[ProviderConfig] = [
    ProviderConfig(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat-241226",  # 实测前请去 DeepSeek 控制台确认当前可用的锁定版本号
        supports_json_schema=False,
    ),
    ProviderConfig(
        name="doubao",
        api_key_env="ARK_API_KEY",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seed-2-1-turbo-241215",  # 实测前请去火山方舟控制台确认 Endpoint ID / 版本号
        supports_json_schema=True,
    ),
    ProviderConfig(
        name="qwen",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-plus-241226",  # 实测前请去 DashScope 控制台确认当前可用的锁定版本号
        supports_json_schema=False,
    ),
]


def run_comparison(
    sample_text: str, providers: list[ProviderConfig]
) -> list[ComparisonResult]:
    results = []
    for provider in providers:
        api_key = os.environ.get(provider.api_key_env, "")
        gateway = LLMGateway(
            api_key=api_key,
            base_url=provider.base_url,
            model=provider.model,
            supports_json_schema=provider.supports_json_schema,
            max_retries=0,  # 对比测试只看首次是否达标，不吃重试红利
        )
        started = time.monotonic()
        try:
            profile = gateway.extract_structured(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_prompt=sample_text,
                schema=JobProfile,
            )
            latency_ms = (time.monotonic() - started) * 1000
            results.append(
                ComparisonResult(
                    provider_name=provider.name,
                    schema_valid=True,
                    latency_ms=latency_ms,
                    raw_output=profile.model_dump_json(),
                    error=None,
                )
            )
        except SchemaExtractionFailed as exc:
            latency_ms = (time.monotonic() - started) * 1000
            results.append(
                ComparisonResult(
                    provider_name=provider.name,
                    schema_valid=False,
                    latency_ms=latency_ms,
                    raw_output="",
                    error=str(exc),
                )
            )
    return results


def summarize(results: list[ComparisonResult]) -> ComparisonSummary:
    passing = [r for r in results if r.schema_valid]
    disqualified = [r.provider_name for r in results if not r.schema_valid]

    if not passing:
        raise ValueError("没有供应商通过 Schema 校验，需要人工排查或换供应商")

    best = min(passing, key=lambda r: r.latency_ms)
    return ComparisonSummary(
        recommended_provider=best.provider_name,
        disqualified=disqualified,
        results=results,
    )


if __name__ == "__main__":
    results = run_comparison(SAMPLE_REQUIREMENT, PROVIDER_CANDIDATES)
    summary = summarize(results)
    print(f"推荐供应商: {summary.recommended_provider}")
    print(f"未通过 Schema 校验: {summary.disqualified}")
    for r in results:
        print(f"- {r.provider_name}: schema_valid={r.schema_valid} latency={r.latency_ms:.0f}ms")
```

- [ ] **Step 5: 运行确认通过（假数据测试，不需要真实 API Key）**

```bash
pytest tests/test_compare_models.py -v
```

Expected: 2 个测试全部 PASS

- [ ] **Step 6: 写决策文档模板 `docs/m1-model-comparison.md`**

```markdown
# M1 模型对比实测结论

> 状态：**待实测**。跑完 `scripts/compare_models.py` 后由工程师填写本文件，不得编造数字。

## 如何跑

```bash
export DEEPSEEK_API_KEY=...
export ARK_API_KEY=...
export DASHSCOPE_API_KEY=...
python -m scripts.compare_models
```

## 对比结果（跑完后填）

| 供应商 | schema_valid | 延迟(ms) | json_schema 支持 | 备注 |
|---|---|---|---|---|
| deepseek | | | 否（json_object 降级） | |
| doubao | | | 是 | |
| qwen | | | 否（json_object 降级） | |

## 决策

- **选定供应商**：（待填）
- **理由**：（待填，至少覆盖 schema 遵循度、延迟、单价三项）
- **写回配置**：把结果填进 `.env` 的 `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_SUPPORTS_JSON_SCHEMA`
```

- [ ] **Step 7: Commit**

```bash
git add scripts/ docs/m1-model-comparison.md tests/test_compare_models.py
git commit -m "feat: 模型对比实测脚本（0.1），聚合逻辑测试覆盖，真实数据待工程师实测填入"
```

---

### Task 6: 通道抽象层 + Web 通道

**Files:**
- Create: `app/channels/__init__.py`
- Create: `app/channels/base.py`
- Create: `app/channels/web_channel.py`
- Test: `tests/test_web_channel.py`

**Interfaces:**
- Produces: `app.channels.base.OutboundMessage`（`dataclass`，字段 `type: str`（`"question" | "confirmation_prompt" | "jd_result" | "needs_manual"`）、`payload: dict`）。
- Produces: `app.channels.base.Channel`（`Protocol`）：`deliver(self, thread_id: str, message: OutboundMessage) -> None`、`latest(self, thread_id: str) -> OutboundMessage | None`。
- Produces: `app.channels.web_channel.WebChannel(conn: sqlite3.Connection)`，实现上述 Protocol，落 `outbox` 表（Task 3 已建表）。这是「发起/追问/确认」的通道无关落地实现，第一个通道；后续接企微时新增 `WeComChannel` 实现同一接口即可。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_channel.py
from app.channels.base import OutboundMessage
from app.channels.web_channel import WebChannel
from app.storage.db import get_connection, init_schema


def test_deliver_then_latest_returns_same_message(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)

    message = OutboundMessage(type="question", payload={"questions": ["MCU 平台族是？"]})
    channel.deliver("job1", message)

    latest = channel.latest("job1")
    assert latest.type == "question"
    assert latest.payload == {"questions": ["MCU 平台族是？"]}


def test_latest_returns_none_when_no_message(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)

    assert channel.latest("unknown-job") is None


def test_latest_returns_most_recent_message(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)

    channel.deliver("job1", OutboundMessage(type="question", payload={"n": 1}))
    channel.deliver("job1", OutboundMessage(type="question", payload={"n": 2}))

    latest = channel.latest("job1")
    assert latest.payload == {"n": 2}
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_web_channel.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.channels'`

- [ ] **Step 3: 写 `app/channels/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写 `app/channels/base.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class OutboundMessage:
    type: str  # "question" | "confirmation_prompt" | "jd_result" | "needs_manual"
    payload: dict


class Channel(Protocol):
    def deliver(self, thread_id: str, message: OutboundMessage) -> None: ...

    def latest(self, thread_id: str) -> OutboundMessage | None: ...
```

- [ ] **Step 5: 写 `app/channels/web_channel.py`**

```python
from __future__ import annotations

import json
import sqlite3

from app.channels.base import OutboundMessage


class WebChannel:
    """
    第一个 Channel 实现。Web 是同步请求/响应，"投递"等价于写 outbox，
    HTTP handler 处理完请求后读 latest() 拿去当响应体。
    未来接企微时新增 WeComChannel，实现同样的 deliver/latest，
    graph 节点侧代码不需要改。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def deliver(self, thread_id: str, message: OutboundMessage) -> None:
        self._conn.execute(
            "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, ?, ?)",
            (thread_id, message.type, json.dumps(message.payload, ensure_ascii=False)),
        )
        self._conn.commit()

    def latest(self, thread_id: str) -> OutboundMessage | None:
        row = self._conn.execute(
            "SELECT message_type, payload_json FROM outbox "
            "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        return OutboundMessage(type=row[0], payload=json.loads(row[1]))
```

- [ ] **Step 6: 运行确认通过**

```bash
pytest tests/test_web_channel.py -v
```

Expected: 3 个测试全部 PASS

- [ ] **Step 7: Commit**

```bash
git add app/channels/ tests/test_web_channel.py
git commit -m "feat: 通道抽象层 + Web 通道实现（0.5）"
```

---

### Task 7: ECU 领域知识库 + 需求解析 Agent

**Files:**
- Create: `app/agents/__init__.py`
- Create: `app/agents/ecu_knowledge.py`
- Create: `app/agents/intake_agent.py`
- Test: `tests/test_ecu_knowledge.py`
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Produces: `app.agents.ecu_knowledge.FOLLOWUP_RULES: dict[str, list[str]]`（模糊术语 → 追问问题列表，如 `"嵌入式开发"` → `["是否涉及 AUTOSAR？", "MCU 平台族是？（如英飞凌/NXP/TI）", "是否有功能安全等级（ASIL）要求？"]`）。
- Produces: `app.agents.ecu_knowledge.match_ambiguous_terms(text: str) -> list[str]`：返回命中的模糊术语列表（用于决定要不要追问）。
- Produces: `app.agents.intake_agent.IntakeTurnResult`（`dataclass`：`is_job_related: bool`、`questions: list[str]`（至多 3 个）、`profile_patch: dict`、`is_complete: bool`、`unspecified_fields: list[str]`）。
- Produces: `app.agents.intake_agent.run_intake_turn(gateway: LLMGateway, *, history: list[dict], round_count: int) -> IntakeTurnResult`（纯函数，`compute_*`，无副作用，被 Task 9 的 `compute_intake_turn` 节点直接调用）。
  - `round_count >= 5` 时强制 `is_complete=True`，`questions=[]`，未覆盖字段进 `unspecified_fields`。
  - `is_job_related=False` 时直接返回引导语（`questions` 里放一句引导语，`is_complete=False`，`profile_patch={}`）。

- [ ] **Step 1: 写失败测试（ECU 知识库）**

```python
# tests/test_ecu_knowledge.py
from app.agents.ecu_knowledge import FOLLOWUP_RULES, match_ambiguous_terms


def test_matches_known_ambiguous_term():
    matches = match_ambiguous_terms("要个做嵌入式开发的，能写驱动")
    assert "嵌入式开发" in matches


def test_no_match_for_unrelated_text():
    assert match_ambiguous_terms("今天天气不错") == []


def test_every_rule_has_at_most_three_questions():
    for term, questions in FOLLOWUP_RULES.items():
        assert 1 <= len(questions) <= 3, f"{term} 的追问数超过每轮上限"
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_ecu_knowledge.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.agents'`

- [ ] **Step 3: 写 `app/agents/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写 `app/agents/ecu_knowledge.py`**

```python
from __future__ import annotations

# 术语 → 追问问题（每条不超过 3 个，满足"每轮追问不超过 3 个问题"约束）
FOLLOWUP_RULES: dict[str, list[str]] = {
    "嵌入式开发": [
        "是否涉及 AUTOSAR（CP/AP）？",
        "MCU 平台族是？（如英飞凌 Aurix / NXP S32K / TI）",
        "是否有功能安全等级（ASIL）要求？",
    ],
    "驱动开发": [
        "驱动对接的总线类型是？（CAN-FD / LIN / 以太网）",
        "是否要求 UDS 诊断栈经验？",
    ],
    "功能安全": [
        "具体到 ASIL 哪个等级？",
        "是否要求 FuSa 工程师认证？",
    ],
    "算法开发": [
        "是感知/控制/诊断算法中的哪一类？",
        "是否要求量产项目（SOP）经验？",
    ],
}


def match_ambiguous_terms(text: str) -> list[str]:
    return [term for term in FOLLOWUP_RULES if term in text]
```

- [ ] **Step 5: 运行确认通过**

```bash
pytest tests/test_ecu_knowledge.py -v
```

Expected: 3 个测试全部 PASS

- [ ] **Step 6: 写失败测试（需求解析 Agent）**

```python
# tests/test_intake_agent.py
import json
from dataclasses import dataclass, field

from app.agents.intake_agent import run_intake_turn
from app.llm.gateway import LLMGateway


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    usage: object = None


class FakeChatCompletions:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeChatCompletions(responses)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


def make_gateway(responses: list[str]) -> LLMGateway:
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses),
    )


def test_unrelated_message_returns_guidance_and_not_complete():
    gateway = make_gateway(
        [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})]
    )

    result = run_intake_turn(gateway, history=[{"role": "user", "content": "今天天气不错"}], round_count=0)

    assert result.is_job_related is False
    assert result.is_complete is False
    assert result.questions  # 引导语非空


def test_job_related_message_returns_followup_questions():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {"job_title": "嵌入式软件工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个做嵌入式开发的"}],
        round_count=0,
    )

    assert result.is_job_related is True
    assert result.questions == ["是否涉及 AUTOSAR？"]
    assert result.profile_patch == {"job_title": "嵌入式软件工程师"}
    assert result.is_complete is False


def test_round_limit_forces_completion_with_unspecified_fields():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["还差 mcu_family"],
                    "profile_patch": {},
                    "unspecified_fields": ["mcu_family"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个嵌入式的"}],
        round_count=5,
    )

    assert result.is_complete is True
    assert result.questions == []
    assert "mcu_family" in result.unspecified_fields


def test_questions_capped_at_three_even_if_model_returns_more():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["Q1", "Q2", "Q3", "Q4", "Q5"],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个嵌入式的"}], round_count=1
    )

    assert len(result.questions) == 3
```

- [ ] **Step 7: 运行确认失败**

```bash
pytest tests/test_intake_agent.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.agents.intake_agent'`

- [ ] **Step 8: 写 `app/agents/intake_agent.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.gateway import LLMGateway
from pydantic import BaseModel

MAX_ROUNDS = 5
MAX_QUESTIONS_PER_ROUND = 3

SYSTEM_PROMPT = (
    "你是招聘助手。判断用户消息是否是用人需求；如果是，基于 ECU 行业知识"
    "生成至多 3 个追问问题，并把能确定的字段整理进 profile_patch（只放本轮新确定的字段，"
    "不要重复历史已有字段）。如果不是用人需求，questions 里放一句引导语，"
    "is_job_related=false，profile_patch 为空对象。"
    "输出 JSON，字段：is_job_related(bool), questions(string[]), profile_patch(object), "
    "unspecified_fields(string[], 可选)。"
)


class _IntakeTurnSchema(BaseModel):
    is_job_related: bool
    questions: list[str] = []
    profile_patch: dict = {}
    unspecified_fields: list[str] = []


@dataclass
class IntakeTurnResult:
    is_job_related: bool
    questions: list[str]
    profile_patch: dict
    is_complete: bool
    unspecified_fields: list[str] = field(default_factory=list)


def run_intake_turn(
    gateway: LLMGateway, *, history: list[dict], round_count: int
) -> IntakeTurnResult:
    user_prompt = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history)

    parsed = gateway.extract_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=_IntakeTurnSchema,
        prompt_version="intake-v1",
    )

    if not parsed.is_job_related:
        return IntakeTurnResult(
            is_job_related=False,
            questions=parsed.questions or ["没听懂是不是用人需求，可以试试：'要招一个做XX的工程师'"],
            profile_patch={},
            is_complete=False,
        )

    at_round_limit = round_count >= MAX_ROUNDS
    questions = [] if at_round_limit else parsed.questions[:MAX_QUESTIONS_PER_ROUND]

    return IntakeTurnResult(
        is_job_related=True,
        questions=questions,
        profile_patch=parsed.profile_patch,
        is_complete=at_round_limit or not questions,
        unspecified_fields=parsed.unspecified_fields if at_round_limit else [],
    )
```

注意：`is_complete=at_round_limit or not questions` —— 当模型认为已经问完（`questions` 为空）时也视为完成，不强制凑够 5 轮，这与 spec「直至画像必填字段齐备或达到追问上限」一致。

- [ ] **Step 9: 运行确认通过**

```bash
pytest tests/test_intake_agent.py -v
```

Expected: 4 个测试全部 PASS

- [ ] **Step 10: Commit**

```bash
git add app/agents/__init__.py app/agents/ecu_knowledge.py app/agents/intake_agent.py tests/test_ecu_knowledge.py tests/test_intake_agent.py
git commit -m "feat: ECU 知识库 + 需求解析 Agent（多轮追问、上限降级、需求识别）"
```

---

### Task 8: JD 生成 Agent（含 AI 标识与歧视性表述拦截）

**Files:**
- Create: `app/agents/jd_agent.py`
- Test: `tests/test_jd_agent.py`

**Interfaces:**
- Consumes: `app.llm.gateway.LLMGateway`（Task 4）、`app.schemas.job_profile.JobProfile`（Task 2）。
- Produces: `app.agents.jd_agent.DISCRIMINATORY_PATTERNS: dict[str, list[str]]`（分类 → 关键词列表：`性别`/`年龄`/`婚育`/`地域`/`民族`/`健康状况`）。
- Produces: `app.agents.jd_agent.contains_discriminatory_language(text: str) -> list[str]`：返回命中的分类列表。
- Produces: `app.agents.jd_agent.JDGenerationResult`（`dataclass`：`text: str`、`needs_manual: bool`、`blocked_categories: list[str]`）。
- Produces: `app.agents.jd_agent.generate_jd(gateway: LLMGateway, profile: JobProfile, *, max_retries: int = 2) -> JDGenerationResult`（纯函数）：生成文案、注入 AI 标识行、检测歧视性表述，命中则重新生成，连续 2 次仍命中则 `needs_manual=True` 并返回最后一次生成内容供人工处理（不是半成品，是完整但被标记的文本）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_jd_agent.py
import json
from dataclasses import dataclass

from app.agents.jd_agent import (
    contains_discriminatory_language,
    generate_jd,
)
from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list
    usage: object = None


class FakeChatCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeChatCompletions(responses)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


def make_gateway(responses):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses),
    )


def make_profile():
    return JobProfile(
        job_title="嵌入式软件工程师",
        department="研发部",
        headcount=1,
        education_requirement="本科及以上",
        experience_years="3-5年",
    )


def test_detects_gender_keyword():
    assert "性别" in contains_discriminatory_language("仅限男性应聘")


def test_no_false_positive_on_clean_text():
    assert contains_discriminatory_language("负责嵌入式软件开发与调试") == []


def test_generate_jd_injects_ai_label_and_returns_clean_text():
    gateway = make_gateway([json.dumps({"body": "负责嵌入式软件开发与调试"})])

    result = generate_jd(gateway, make_profile())

    assert "AI 生成" in result.text
    assert "负责嵌入式软件开发与调试" in result.text
    assert result.needs_manual is False
    assert result.blocked_categories == []


def test_regenerates_once_on_discriminatory_hit_then_succeeds():
    gateway = make_gateway(
        [
            json.dumps({"body": "仅限男性应聘"}),
            json.dumps({"body": "负责嵌入式软件开发与调试"}),
        ]
    )

    result = generate_jd(gateway, make_profile())

    assert result.needs_manual is False
    assert "仅限男性" not in result.text
    assert len(gateway._client.chat.completions.calls) == 2  # type: ignore[attr-defined]


def test_needs_manual_after_two_consecutive_hits():
    gateway = make_gateway(
        [
            json.dumps({"body": "仅限男性应聘"}),
            json.dumps({"body": "限男性，35岁以下"}),
        ]
    )

    result = generate_jd(gateway, make_profile(), max_retries=2)

    assert result.needs_manual is True
    assert "性别" in result.blocked_categories
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_jd_agent.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.agents.jd_agent'`

- [ ] **Step 3: 写 `app/agents/jd_agent.py`**

```python
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pydantic import BaseModel

from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile

AI_LABEL_TEMPLATE = (
    "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {generated_at}。"
)

DISCRIMINATORY_PATTERNS: dict[str, list[str]] = {
    "性别": ["仅限男性", "仅限女性", "限男性", "限女性", "男性优先", "女性优先"],
    "年龄": ["35岁以下", "30岁以下", "限35周岁", "年轻化团队"],
    "婚育": ["已婚已育", "未婚未育", "限已婚"],
    "地域": ["仅限本地户口", "限本地生源"],
    "民族": ["仅限汉族"],
    "健康状况": ["无乙肝", "限健康人士"],
}

JD_SYSTEM_PROMPT = (
    "你是招聘文案助手。基于给定的岗位画像 JSON 生成招聘文案正文（不含 AI 标识，"
    "标识由系统另行拼接），包含岗位职责、任职要求（必备/加分分列）、简短团队介绍。"
    "文案中出现的技术要求必须能追溯到画像字段，不得凭空新增。"
    "禁止出现任何性别/年龄/婚育/地域/民族/健康状况相关的限制性表述。"
    "输出 JSON，字段：body(string)。"
)


class _JDBodySchema(BaseModel):
    body: str


@dataclass
class JDGenerationResult:
    text: str
    needs_manual: bool
    blocked_categories: list[str]


def contains_discriminatory_language(text: str) -> list[str]:
    hits = []
    for category, keywords in DISCRIMINATORY_PATTERNS.items():
        if any(keyword in text for keyword in keywords):
            hits.append(category)
    return hits


def _compose_with_label(body: str, generated_at: str) -> str:
    label = AI_LABEL_TEMPLATE.format(generated_at=generated_at)
    return f"{body}\n\n{label}"


def generate_jd(
    gateway: LLMGateway, profile: JobProfile, *, max_retries: int = 2
) -> JDGenerationResult:
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    last_body = ""
    last_hits: list[str] = []

    # max_retries 是总生成尝试次数（不是"首次+N次重试"），对齐 spec「连续 N 次仍
    # 出现则转人工处理」的字面语义——默认 2 次，与 job-description spec 的
    # 「拦截歧视性表述」Scenario 保持一致（不同于 LLMGateway.max_retries 的
    # "首次+N次重试"约定，两者是不同函数，各自的语义以各自测试为准）。
    for _ in range(max_retries):
        parsed = gateway.extract_structured(
            system_prompt=JD_SYSTEM_PROMPT,
            user_prompt=profile.model_dump_json(),
            schema=_JDBodySchema,
            prompt_version="jd-v1",
        )
        last_body = parsed.body
        last_hits = contains_discriminatory_language(parsed.body)

        if not last_hits:
            return JDGenerationResult(
                text=_compose_with_label(parsed.body, generated_at),
                needs_manual=False,
                blocked_categories=[],
            )

    return JDGenerationResult(
        text=_compose_with_label(last_body, generated_at),
        needs_manual=True,
        blocked_categories=last_hits,
    )
```

注意测试里 `gateway._client` 是访问私有属性用于断言调用次数——这是测试内部实现细节，允许；生产代码不要依赖这个属性名。原始版本的循环写成 `range(max_retries + 1)`（复用了 `LLMGateway.max_retries` 的"首次+N次重试"约定）会导致 `max_retries=2` 时最多尝试 3 次而不是 2 次，既让 `test_needs_manual_after_two_consecutive_hits`（只给 2 条脚本响应）在第 3 次调用时因响应耗尽而报错，也悄悄违反了 spec「连续 2 次仍出现则转人工处理」的字面约束（多给了一次机会）。已在写这份计划时用真实 pytest 跑出这个失败并改成 `range(max_retries)`，不是凭经验判断。

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_jd_agent.py -v
```

Expected: 6 个测试全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/agents/jd_agent.py tests/test_jd_agent.py
git commit -m "feat: JD 生成 Agent（AI 标识注入 + 歧视性表述拦截，连续2次转人工）"
```

---

### Task 9: LangGraph 图骨架接线 + 画像确认 + 幂等验证

**Files:**
- Create: `app/graph/__init__.py`
- Create: `app/graph/state.py`
- Create: `app/graph/nodes.py`
- Create: `app/graph/build.py`
- Test: `tests/test_graph_nodes.py`
- Test: `tests/test_graph_idempotency.py`

**Interfaces:**
- Produces: `app.graph.state.IntakeState`（`TypedDict, total=False`）：`job_id: str`、`history: list[dict]`、`round_count: int`、`profile_patch_accumulated: dict`、`pending_questions: list[str]`、`is_complete: bool`、`unspecified_fields: list[str]`。
- Produces: `app.graph.nodes.compute_intake_turn(state, *, gateway) -> IntakeState`（纯函数，调 `run_intake_turn`，返回更新后的 state 字段）。
- Produces: `app.graph.nodes.effect_persist_draft(conn, *, thread_id, business_key, state) -> None`（`@idempotent_effect("effect_persist_draft")`，写/更新 `job_profile` 草案行，`business_key = str(round_count)`）。
- Produces: `app.graph.nodes.effect_deliver_message(conn, *, thread_id, business_key, channel, message) -> None`（`@idempotent_effect("effect_deliver_message")`，调用 `channel.deliver`，`business_key` 由调用方传入内容哈希）。
- Produces: `app.graph.nodes.effect_confirm_profile(conn, *, thread_id, business_key, profile_dict) -> None`（`@idempotent_effect("effect_confirm_profile")`，把最新 `job_profile` 行状态改为 `approved`，同时更新 `job.status`；`business_key = str(version)`）。
- Produces: `app.graph.build.build_intake_graph(db_path: str)`：返回编译好的 LangGraph 图（`SqliteSaver` checkpointer），`invoke(state, config={"configurable": {"thread_id": job_id}})` 执行一次「计算 → 持久化草稿 → 投递消息」。

- [ ] **Step 1: 写失败测试（节点函数单测，不经过完整图）**

```python
# tests/test_graph_nodes.py
import json
from dataclasses import dataclass

from app.channels.base import OutboundMessage
from app.channels.web_channel import WebChannel
from app.graph.nodes import (
    compute_intake_turn,
    effect_confirm_profile,
    effect_deliver_message,
    effect_persist_draft,
)
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list
    usage: object = None


class FakeChatCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeChatCompletions(responses)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


def make_gateway(responses):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses),
    )


def test_compute_intake_turn_updates_state():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {"job_title": "嵌入式软件工程师"},
                }
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    assert new_state["pending_questions"] == ["是否涉及 AUTOSAR？"]
    assert new_state["profile_patch_accumulated"]["job_title"] == "嵌入式软件工程师"
    assert new_state["round_count"] == 1
    assert new_state["is_complete"] is False


def test_effect_persist_draft_writes_job_profile_row(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    effect_persist_draft(
        conn,
        thread_id="job1",
        business_key="1",
        state={"profile_patch_accumulated": {"job_title": "x"}, "unspecified_fields": []},
    )

    row = conn.execute(
        "SELECT status, profile_json FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == "drafting"
    assert json.loads(row[1])["job_title"] == "x"


def test_effect_deliver_message_calls_channel(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    channel = WebChannel(conn)
    message = OutboundMessage(type="question", payload={"questions": ["Q1"]})

    effect_deliver_message(
        conn, thread_id="job1", business_key="hash1", channel=channel, message=message
    )

    assert channel.latest("job1").payload == {"questions": ["Q1"]}


def test_effect_confirm_profile_marks_approved(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'job1', 1, 'drafting', '{}')"
    )
    conn.commit()

    effect_confirm_profile(
        conn, thread_id="job1", business_key="1", profile_dict={"job_title": "x"}
    )

    job_status = conn.execute("SELECT status FROM job WHERE id='job1'").fetchone()[0]
    profile_status = conn.execute(
        "SELECT status FROM job_profile WHERE job_id='job1' ORDER BY version DESC LIMIT 1"
    ).fetchone()[0]
    assert job_status == "approved"
    assert profile_status == "approved"
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_graph_nodes.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.graph'`

- [ ] **Step 3: 写 `app/graph/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写 `app/graph/state.py`**

```python
from __future__ import annotations

from typing import TypedDict


class IntakeState(TypedDict, total=False):
    job_id: str
    history: list[dict]
    round_count: int
    profile_patch_accumulated: dict
    pending_questions: list[str]
    is_complete: bool
    is_job_related: bool
    unspecified_fields: list[str]
```

- [ ] **Step 5: 写 `app/graph/nodes.py`**

```python
from __future__ import annotations

import hashlib
import json
import sqlite3

from app.agents.intake_agent import run_intake_turn
from app.channels.base import Channel, OutboundMessage
from app.graph.state import IntakeState
from app.llm.gateway import LLMGateway
from app.storage.idempotency import idempotent_effect


def compute_intake_turn(state: IntakeState, *, gateway: LLMGateway) -> IntakeState:
    """compute_* 节点：纯函数，只调用 LLM 与做数据转换，不写库、不发消息。"""
    result = run_intake_turn(
        gateway, history=state["history"], round_count=state.get("round_count", 0)
    )

    accumulated = dict(state.get("profile_patch_accumulated", {}))
    accumulated.update(result.profile_patch)

    return {
        **state,
        "is_job_related": result.is_job_related,
        "pending_questions": result.questions,
        "profile_patch_accumulated": accumulated,
        "is_complete": result.is_complete,
        "round_count": state.get("round_count", 0) + 1,
        "unspecified_fields": result.unspecified_fields,
    }


@idempotent_effect("effect_persist_draft")
def effect_persist_draft(conn: sqlite3.Connection, *, thread_id: str, business_key: str, state: dict) -> None:
    """effect_* 节点：写 job_profile 草案行，独占、幂等。business_key = round_count。"""
    profile_json = json.dumps(state.get("profile_patch_accumulated", {}), ensure_ascii=False)
    unspecified_json = json.dumps(state.get("unspecified_fields", []), ensure_ascii=False)
    version = int(business_key) + 1

    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, unspecified_fields) "
        "VALUES (?, ?, ?, 'drafting', ?, ?)",
        (f"{thread_id}-v{version}", thread_id, version, profile_json, unspecified_json),
    )
    conn.commit()


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
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, profile_dict: dict
) -> None:
    """
    effect_* 节点：把最新画像草案冻结为 approved，同步更新 job.status。
    business_key = 冻结的 version 号，防止同一版本被重复确认两次。
    """
    conn.execute(
        "UPDATE job_profile SET status = 'approved' "
        "WHERE job_id = ? AND version = (SELECT MAX(version) FROM job_profile WHERE job_id = ?)",
        (thread_id, thread_id),
    )
    conn.execute("UPDATE job SET status = 'approved' WHERE id = ?", (thread_id,))
    conn.commit()


def message_business_key(payload: dict) -> str:
    """给 effect_deliver_message 生成稳定的 business_key，同一轮同一内容只投递一次。"""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 6: 运行确认通过**

```bash
pytest tests/test_graph_nodes.py -v
```

Expected: 4 个测试全部 PASS

- [ ] **Step 7: 写幂等专项测试（强制重复调用，断言副作用只发生一次）**

```python
# tests/test_graph_idempotency.py
from app.graph.nodes import effect_confirm_profile, effect_persist_draft
from app.storage.db import get_connection, init_schema


def test_effect_persist_draft_replay_does_not_duplicate_rows(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    state = {"profile_patch_accumulated": {"job_title": "x"}, "unspecified_fields": []}

    effect_persist_draft(conn, thread_id="job1", business_key="1", state=state)
    effect_persist_draft(conn, thread_id="job1", business_key="1", state=state)  # 模拟节点重跑

    count = conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    assert count == 1


def test_effect_confirm_profile_replay_is_noop_second_time(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'job1', 1, 'drafting', '{}')"
    )
    conn.commit()

    effect_confirm_profile(conn, thread_id="job1", business_key="1", profile_dict={})
    # 第二次调用命中 effect_log，函数体不应再执行（即使这里执行了也是同样结果，
    # 但关键断言是 effect_log 只有一条记录 —— 这是幂等键生效的证据）
    effect_confirm_profile(conn, thread_id="job1", business_key="1", profile_dict={})

    effect_log_count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_confirm_profile'"
    ).fetchone()[0]
    assert effect_log_count == 1
```

- [ ] **Step 8: 运行确认通过**

```bash
pytest tests/test_graph_idempotency.py -v
```

Expected: 2 个测试全部 PASS

- [ ] **Step 9: 写 `app/graph/build.py`（图骨架，`SqliteSaver` checkpointer）**

```python
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
```

注意：早期草稿曾写成 `SqliteSaver.from_conn_string(db_path)` 直接赋值——这是 `langgraph-checkpoint-sqlite==2.0.6` 的一个常见误用陷阱，`from_conn_string` 返回的是一个 `@contextmanager` 生成器，不 `with` 就直接传给 `graph.compile(checkpointer=...)` 会在编译时抛 `TypeError: Invalid checkpointer provided`。写这份计划时用真实的 `langgraph==1.0.10` + `langgraph-checkpoint-sqlite==2.0.6`（`requirements.txt` 锁定的确切版本）跑通 `tests/test_web_api.py` 才发现这个问题，已改成 `SqliteSaver(conn)`。

- [ ] **Step 10: 写图编译/调用的最小集成测试**

```python
# 追加到 tests/test_graph_nodes.py 末尾
def test_build_intake_graph_runs_end_to_end(tmp_path):
    from app.channels.web_channel import WebChannel
    from app.graph.build import build_intake_graph
    from app.storage.db import get_connection, init_schema

    db_path = str(tmp_path / "graph.db")
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {"job_title": "嵌入式软件工程师"},
                }
            )
        ]
    )
    channel = WebChannel(conn)
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

    initial_state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }
    graph.invoke(initial_state, config={"configurable": {"thread_id": "job1"}})

    latest = channel.latest("job1")
    assert latest.type == "question"
    assert latest.payload["questions"] == ["是否涉及 AUTOSAR？"]
```

- [ ] **Step 11: 运行全部图相关测试确认通过**

```bash
pytest tests/test_graph_nodes.py tests/test_graph_idempotency.py -v
```

Expected: 全部 PASS

- [ ] **Step 12: Commit**

```bash
git add app/graph/ tests/test_graph_nodes.py tests/test_graph_idempotency.py
git commit -m "feat: LangGraph 图骨架接线（compute_/effect_ 节点、SqliteSaver、幂等专项测试）"
```

---

### Task 10: FastAPI Web 服务 + 单页前端（路径前缀自挂载 + 相对路径前端）

**Files:**
- Create: `app/web/__init__.py`
- Create: `app/web/server.py`
- Create: `app/web/static/index.html`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `app.graph.build.build_intake_graph`、`app.agents.jd_agent.generate_jd`、`app.graph.nodes.effect_confirm_profile`（Task 9）、`app.channels.web_channel.WebChannel`（Task 6）、`app.middleware.auth.AuthMiddleware`（Task 1）、`app.config.get_settings`（Task 1）。
- Produces: `app.web.server.create_app(*, db_path: str, gateway_factory: Callable, root_path: str = "") -> FastAPI`。

  **`root_path` 的处理方式（部署约束 1）**：所有业务路由先注册到一个 `APIRouter()` 上，最后用 `app.include_router(router, prefix=root_path)` 把整个 router 挂到 `root_path` 前缀下。这是应用**自己**把路由挂到前缀下（自挂载），不依赖任何反向代理做路径剥离——`root_path=""` 时行为等价于挂在域根（本地开发/单元测试默认值）；`root_path="/hr/recruit-agent"` 时全部路由变成 `/hr/recruit-agent/`、`/hr/recruit-agent/api/jobs` 等。将来统一网关上线，只要 nginx `proxy_pass` 把请求原样转发到这个前缀（不剥离），应用不需要改一行代码。首页 HTML 通过服务端注入的 `<base href="{root_path}/">` + 前端相对路径 `fetch` 调用，让浏览器在任意挂载前缀下都能正确拼出 API 地址。

  路由（均相对 `root_path` 前缀）：
  - `GET /` → 返回单页前端，动态注入 `<base href>`
  - `POST /api/jobs` body `{"message": str}` → 创建 job（`drafting`），跑一轮 intake graph，返回 outbox 最新消息
  - `POST /api/jobs/{job_id}/reply` body `{"message": str}` → 追加一轮
  - `POST /api/jobs/{job_id}/confirm` → 若最新画像未处于可确认状态则拒绝（409）；否则冻结画像、触发 JD 生成，返回 JD 文本
  - `GET /api/jobs/{job_id}` → 返回当前 job/profile/outbox 状态，供前端轮询渲染

  `app.add_middleware(AuthMiddleware)`（Task 1）在 `create_app` 内挂载，demo 阶段无条件放行。

- [ ] **Step 1: 写失败测试（用 FastAPI TestClient，注入假 gateway；含 root_path 子路径挂载测试）**

```python
# tests/test_web_api.py
import json
from dataclasses import dataclass

from fastapi.testclient import TestClient

from app.web.server import create_app


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list
    usage: object = None


class ScriptedChatCompletions:
    """按顺序吐出预先写好的响应，模拟"追问两轮后完成，再生成 JD"整条链路。"""

    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class ScriptedChat:
    def __init__(self, responses):
        self.completions = ScriptedChatCompletions(responses)


class ScriptedOpenAIClient:
    def __init__(self, responses):
        self.chat = ScriptedChat(responses)


def make_app(tmp_path, responses, root_path: str = ""):
    from app.llm.gateway import LLMGateway

    db_path = str(tmp_path / "web.db")
    client = ScriptedOpenAIClient(responses)

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=client,
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path=root_path)
    return TestClient(app)


def test_create_job_returns_first_question(tmp_path):
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["是否涉及 AUTOSAR？"],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["type"] == "question"
    assert body["message"]["payload"]["questions"] == ["是否涉及 AUTOSAR？"]


def test_reply_and_confirm_then_generate_jd(tmp_path):
    responses = [
        # 第一轮：追问
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["MCU 平台族是？"],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
        # 第二轮：完成
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"mcu_family": ["英飞凌 Aurix"]},
            }
        ),
        # confirm 后触发 JD 生成
        json.dumps({"body": "负责嵌入式软件开发与调试"}),
    ]
    client = make_app(tmp_path, responses)

    create_resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = create_resp.json()["job_id"]

    reply_resp = client.post(f"/api/jobs/{job_id}/reply", json={"message": "AUTOSAR CP"})
    assert reply_resp.json()["message"]["type"] == "confirmation_prompt"

    confirm_resp = client.post(f"/api/jobs/{job_id}/confirm")
    assert confirm_resp.status_code == 200
    jd_text = confirm_resp.json()["jd_text"]
    assert "AI 生成" in jd_text
    assert "负责嵌入式软件开发与调试" in jd_text


def test_confirm_rejected_when_still_drafting(tmp_path):
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["MCU 平台族是？"],
                "profile_patch": {},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    create_resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = create_resp.json()["job_id"]

    confirm_resp = client.post(f"/api/jobs/{job_id}/confirm")
    assert confirm_resp.status_code == 409


def test_index_defaults_base_href_to_root_when_no_root_path(tmp_path):
    client = make_app(tmp_path, [], root_path="")

    resp = client.get("/")

    assert resp.status_code == 200
    assert '<base href="/">' in resp.text


def test_index_base_href_matches_configured_root_path(tmp_path):
    client = make_app(tmp_path, [], root_path="/hr/recruit-agent")

    resp = client.get("/hr/recruit-agent/")

    assert resp.status_code == 200
    assert '<base href="/hr/recruit-agent/">' in resp.text


def test_app_works_when_mounted_at_arbitrary_subpath(tmp_path):
    """
    路径前缀就绪的硬验收标准（部署约束1）：把服务挂到任意子路径下
    （这里用 /foo/bar 举例，不是 /hr/recruit-agent 也要正常工作）都不 404。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": ["是否涉及 AUTOSAR？"],
                "profile_patch": {},
            }
        )
    ]
    client = make_app(tmp_path, responses, root_path="/foo/bar")

    index_resp = client.get("/foo/bar/")
    assert index_resp.status_code == 200

    api_resp = client.post("/foo/bar/api/jobs", json={"message": "要个做嵌入式开发的"})
    assert api_resp.status_code == 200
    assert api_resp.json()["message"]["type"] == "question"


def test_unprefixed_paths_404_when_root_path_is_configured(tmp_path):
    """
    反向证明：设了 /foo/bar 前缀后，不带前缀的路径必须 404——
    否则前缀就只是摆设，没有真的生效。
    """
    client = make_app(tmp_path, [], root_path="/foo/bar")

    assert client.get("/").status_code == 404
    assert client.post("/api/jobs", json={"message": "x"}).status_code == 404


def test_frontend_html_has_no_hardcoded_absolute_api_or_static_paths(tmp_path):
    """
    验证「前端资源与接口调用一律相对路径，禁止硬编码 /static/... /api/...」
    这条约束在实际产出的 HTML 里成立，不是文字承诺。
    """
    client = make_app(tmp_path, [], root_path="/hr/recruit-agent")

    html = client.get("/hr/recruit-agent/").text

    assert '"/api/jobs' not in html
    assert "`/api/jobs" not in html
    assert "fetch(\"api/jobs\")" in html or "url = jobId" in html
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_web_api.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.web'`

- [ ] **Step 3: 写 `app/web/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写 `app/web/server.py`**

```python
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agents.jd_agent import generate_jd
from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.nodes import effect_confirm_profile
from app.middleware.auth import AuthMiddleware
from app.schemas.job_profile import JobProfile
from app.storage.db import get_connection, init_schema

STATIC_DIR = Path(__file__).parent / "static"
INDEX_TEMPLATE_PATH = STATIC_DIR / "index.html"


class CreateJobRequest(BaseModel):
    message: str


class ReplyRequest(BaseModel):
    message: str


def _render_index(root_path: str) -> str:
    """把 <!--BASE_HREF--> 占位符换成真实 <base href>，让前端相对路径请求
    在任意挂载前缀下都能解析到正确的地址。root_path="" 时挂域根。"""
    html = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    base_href = f"{root_path}/" if root_path else "/"
    return html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">')


def create_app(*, db_path: str, gateway_factory: Callable, root_path: str = "") -> FastAPI:
    app = FastAPI(title="卓品智能招聘助手 · Demo")
    app.add_middleware(AuthMiddleware)

    conn = get_connection(db_path)
    init_schema(conn)
    channel = WebChannel(conn)
    router = APIRouter()

    def _run_turn(job_id: str, message: str) -> dict:
        gateway = gateway_factory()
        graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

        history_row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        accumulated = json.loads(history_row[0]) if history_row else {}
        round_count = conn.execute(
            "SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()[0]

        state = {
            "job_id": job_id,
            "history": [{"role": "user", "content": message}],
            "round_count": round_count,
            "profile_patch_accumulated": accumulated,
        }
        graph.invoke(state, config={"configurable": {"thread_id": job_id}})

        latest = channel.latest(job_id)
        return {"type": latest.type, "payload": latest.payload}

    @router.post("/api/jobs")
    def create_job(req: CreateJobRequest):
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,)
        )
        conn.commit()
        message = _run_turn(job_id, req.message)
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/reply")
    def reply(job_id: str, req: ReplyRequest):
        job = conn.execute("SELECT id FROM job WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        message = _run_turn(job_id, req.message)
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/confirm")
    def confirm(job_id: str):
        row = conn.execute(
            "SELECT profile_json, status FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no profile draft yet")

        latest_message = channel.latest(job_id)
        if latest_message is None or latest_message.type != "confirmation_prompt":
            raise HTTPException(status_code=409, detail="画像还在追问中，未到可确认状态")

        profile_dict = json.loads(row[0])
        version = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()[0]

        effect_confirm_profile(
            conn, thread_id=job_id, business_key=str(version), profile_dict=profile_dict
        )

        gateway = gateway_factory()
        profile = JobProfile.model_validate(
            {
                "job_title": profile_dict.get("job_title", "未命名岗位"),
                "department": profile_dict.get("department", "未指定"),
                "headcount": profile_dict.get("headcount", 1),
                "education_requirement": profile_dict.get("education_requirement", "未指定"),
                "experience_years": profile_dict.get("experience_years", "未指定"),
                **{
                    k: v
                    for k, v in profile_dict.items()
                    if k
                    not in {
                        "job_title",
                        "department",
                        "headcount",
                        "education_requirement",
                        "experience_years",
                    }
                },
            }
        )
        jd_result = generate_jd(gateway, profile)

        conn.execute(
            "UPDATE job_profile SET profile_json = ? "
            "WHERE job_id = ? AND version = ?",
            (json.dumps({**profile_dict, "_jd_text": jd_result.text}, ensure_ascii=False), job_id, version),
        )
        conn.commit()

        return {
            "job_id": job_id,
            "jd_text": jd_result.text,
            "needs_manual": jd_result.needs_manual,
        }

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = conn.execute(
            "SELECT id, title, status FROM job WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        latest = channel.latest(job_id)
        return {
            "job_id": job[0],
            "status": job[2],
            "message": {"type": latest.type, "payload": latest.payload} if latest else None,
        }

    @router.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(_render_index(root_path))

    app.include_router(router, prefix=root_path)
    app.mount(
        f"{root_path}/static" if root_path else "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

    return app
```

注意：`app.include_router(router, prefix=root_path)` 是本任务的核心机制——路由全部先注册成不带前缀的裸路径，最后统一挂到 `root_path` 下。`root_path=""` 时 `prefix=""` 与老版本行为完全一致（域根挂载）；`root_path="/foo/bar"` 时不带前缀的 `/`、`/api/jobs` 会真实 404（Step 1 的 `test_unprefixed_paths_404_when_root_path_is_configured` 断言了这一点），证明前缀是真的生效了，不是摆设。当前 `index.html` 没有引用任何 `/static/...` 下的资源（样式与脚本全部内联），`static` 挂载点是为将来独立 CSS/JS 文件预留的，同样带前缀，保持一致。

- [ ] **Step 5: 运行确认通过**

```bash
pytest tests/test_web_api.py -v
```

Expected: 9 个测试全部 PASS

- [ ] **Step 6: 写单页前端 `app/web/static/index.html`（含「演示环境」显著标注、`<!--BASE_HREF-->` 占位符、相对路径 fetch）**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<!--BASE_HREF-->
<title>卓品智能招聘助手 · 内网 Demo</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; }
  .banner { background: #fff3cd; border: 1px solid #ffe69c; color: #664d03; padding: 12px 16px; border-radius: 8px; margin-bottom: 24px; font-weight: 600; }
  .chat { border: 1px solid #ddd; border-radius: 8px; padding: 16px; min-height: 200px; margin-bottom: 16px; }
  .turn { margin-bottom: 12px; }
  .turn.user { text-align: right; color: #0d6efd; }
  .turn.assistant { color: #333; }
  textarea { width: 100%; box-sizing: border-box; padding: 8px; }
  button { margin-top: 8px; padding: 8px 16px; cursor: pointer; }
  #jd-output { white-space: pre-wrap; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-top: 16px; display: none; }
</style>
</head>
<body>
  <div class="banner">⚠️ 演示环境，不进入正式招聘流程</div>

  <h1>一句话提用人需求</h1>
  <div class="chat" id="chat"></div>
  <textarea id="input" rows="3" placeholder="例如：要个做嵌入式开发的，能写驱动"></textarea>
  <button id="send-btn">发送</button>
  <button id="confirm-btn" style="display:none;">确认画像，生成 JD</button>

  <div id="jd-output"></div>

  <script>
    // 全部请求用相对路径（不带开头的 "/"），配合 <head> 里的 <base href> 解析，
    // 挂在任意前缀下都不需要改这段代码（部署约束1）。
    let jobId = null;

    function appendTurn(role, text) {
      const chat = document.getElementById("chat");
      const div = document.createElement("div");
      div.className = "turn " + role;
      div.textContent = text;
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    }

    function renderMessage(message) {
      if (message.type === "question") {
        appendTurn("assistant", message.payload.questions.join("\n"));
        document.getElementById("confirm-btn").style.display = "none";
      } else if (message.type === "confirmation_prompt") {
        const unspecified = message.payload.unspecified_fields || [];
        let text = "画像已收集完整，请确认。";
        if (unspecified.length > 0) {
          text += "\n以下字段未指定：" + unspecified.join("、");
        }
        appendTurn("assistant", text);
        document.getElementById("confirm-btn").style.display = "inline-block";
      }
    }

    document.getElementById("send-btn").addEventListener("click", async () => {
      const input = document.getElementById("input");
      const text = input.value.trim();
      if (!text) return;
      appendTurn("user", text);
      input.value = "";

      const url = jobId ? `api/jobs/${jobId}/reply` : "api/jobs";
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const data = await resp.json();
      if (!jobId) jobId = data.job_id;
      renderMessage(data.message);
    });

    document.getElementById("confirm-btn").addEventListener("click", async () => {
      const resp = await fetch(`api/jobs/${jobId}/confirm`, { method: "POST" });
      const data = await resp.json();
      const output = document.getElementById("jd-output");
      output.style.display = "block";
      output.textContent = data.jd_text;
      if (data.needs_manual) {
        appendTurn("assistant", "⚠️ JD 多次触发歧视性表述检测，已转人工处理，请核对下方内容。");
      }
    });
  </script>
</body>
</html>
```

- [ ] **Step 7: Commit**

```bash
git add app/web/ tests/test_web_api.py
git commit -m "feat: FastAPI Web 服务 + 单页前端（root_path 自挂载 + 相对路径前端，0.8/0.9）"
```

---

### Task 11: Windows 服务器部署（venv + 计划任务，无 Docker）

> 原「Docker 化与部署」整体作废（04-部署与门户挂载.md §7）。目标服务器 `192.168.100.51` 是 Windows，没有 Docker，也不引入容器运行时。部署形态改为 Python venv + Windows 计划任务保活 + 防火墙规则，过渡期监听 **8095** 绑 `0.0.0.0`。

**Files:**
- Create: `app/main.py`
- Create: `deploy-server.ps1`
- Create: `sync-to-server.ps1`
- Create: `docs/deploy-51-server.md`

**Interfaces:**
- Produces: `app.main.app`（`FastAPI` 实例，`create_app` 用 `get_settings()` 组装真实 `gateway_factory` 与 `root_path`，供 `uvicorn app.main:app` 启动）。

- [ ] **Step 1: 写 `app/main.py`（生产入口，串起 Task 1 的配置与 Task 10 的 app 工厂）**

```python
from app.config import get_settings
from app.llm.gateway import LLMGateway
from app.web.server import create_app

settings = get_settings()


def _gateway_factory() -> LLMGateway:
    return LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
    )


app = create_app(
    db_path=settings.db_path,
    gateway_factory=_gateway_factory,
    root_path=settings.root_path,
)
```

- [ ] **Step 2: 本地跑一次确认能启动（手工验证，非 pytest）**

```bash
cd /Users/paulshao/Projects/HumanResource
source .venv/bin/activate
cp .env.example .env  # 先用假 key 验证能不能启动，不需要真实调用
uvicorn app.main:app --reload --port 8095
```

Expected: 终端打印 `Uvicorn running on http://127.0.0.1:8095`。**注意**：`.env.example` 里 `ROOT_PATH` 默认是 `/hr/recruit-agent`，所以浏览器要打开 `http://127.0.0.1:8095/hr/recruit-agent/` 才能看到「演示环境」横幅与输入框——直接开 `http://127.0.0.1:8095/` 会 404，这是 Task 10 路径前缀自挂载机制的预期行为，不是 bug。此时点发送会因为假 API Key 报错，属预期，先确认页面能起来。Ctrl+C 停止。

- [ ] **Step 3: 写 `deploy-server.ps1`（首次部署，在服务器上通过 RDP 以管理员身份运行一次）**

```powershell
<#
.SYNOPSIS
  卓品智能招聘助手 Demo 首次部署脚本 —— Windows venv + 计划任务，无 Docker（部署约束4）。
.DESCRIPTION
  在目标服务器 192.168.100.51 上、以管理员身份运行（RDP 登录后手工执行一次）：
    1. 若不存在则创建 Python venv
    2. 安装 requirements.txt 依赖
    3. 注册 Windows 计划任务：SYSTEM 账户 + AtStartup 触发 + 失败重启 3 次
    4. 开放防火墙入站规则，放行 8095
  代码需先用 sync-to-server.ps1（或手工方式）放到 $AppDir 下，再运行本脚本。
  自包含实现，不依赖「企业AI转型」仓库的 ZhuopinDeploy.psm1。
#>

param(
    [string]$AppDir = "C:\apps\zhuopin-recruit-agent",
    [string]$PythonExe = "python",
    [int]$Port = 8095,
    [string]$TaskName = "ZhuopinRecruitAgent",
    [string]$FirewallRuleName = "ZhuopinRecruitAgent-Inbound-8095"
)

$ErrorActionPreference = "Stop"

Write-Host "==> 部署目录: $AppDir"
if (-not (Test-Path $AppDir)) {
    throw "部署目录 $AppDir 不存在。请先用 sync-to-server.ps1 把代码放到这里，再运行本脚本。"
}

Set-Location $AppDir

$venvPath = Join-Path $AppDir ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvUvicorn = Join-Path $venvPath "Scripts\uvicorn.exe"

Write-Host "==> 检查 venv: $venvPath"
if (-not (Test-Path $venvPython)) {
    Write-Host "    venv 不存在，创建中..."
    & $PythonExe -m venv $venvPath
} else {
    Write-Host "    venv 已存在，跳过创建"
}

Write-Host "==> 安装依赖"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $AppDir "requirements.txt")

Write-Host "==> 检查 .env"
$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning ".env 不存在！请在 $envFile 手工创建（参考 .env.example），填入真实 LLM_API_KEY 后再启动服务。"
}

Write-Host "==> 注册 Windows 计划任务: $TaskName"
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "    任务已存在，先移除旧定义"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute $venvUvicorn `
    -Argument "app.main:app --host 0.0.0.0 --port $Port" `
    -WorkingDirectory $AppDir

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "卓品智能招聘助手 Demo（M1 第0章），FastAPI+uvicorn，监听 $Port" | Out-Null

Write-Host "==> 开放防火墙规则: $FirewallRuleName (TCP $Port)"
$existingRule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Write-Host "    规则已存在，跳过"
} else {
    New-NetFirewallRule `
        -DisplayName $FirewallRuleName `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $Port `
        -Action Allow | Out-Null
}

Write-Host "==> 启动计划任务"
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 3
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "==> 计划任务状态: LastTaskResult=$($taskInfo.LastTaskResult)（0 = 成功启动）"
Write-Host "==> 部署完成。验证: curl.exe http://localhost:$Port/hr/recruit-agent/"
```

- [ ] **Step 4: 写 `sync-to-server.ps1`（日常发版，从开发机运行）**

```powershell
<#
.SYNOPSIS
  日常发版脚本 —— 从开发机把代码同步到 51 服务器并重启计划任务。
.DESCRIPTION
  不重建 venv、不重装依赖；requirements.txt 变更时需另外在服务器上手工
  重跑 deploy-server.ps1 的 venv/依赖安装部分（或直接重跑整个 deploy-server.ps1，
  它对已存在的 venv 是幂等的，只会跳过创建步骤）。
  依赖本机到目标服务器的 SSH 访问（scp/ssh 命令行工具，Windows 10/11 自带 OpenSSH 客户端）。
  自包含实现，不依赖「企业AI转型」仓库的 ZhuopinDeploy.psm1。
#>

param(
    [string]$ServerHost = "192.168.100.51",
    [string]$ServerUser = "Administrator",
    [string]$RemoteAppDir = "C:/apps/zhuopin-recruit-agent",
    [string]$LocalAppDir = ".",
    [string]$TaskName = "ZhuopinRecruitAgent"
)

$ErrorActionPreference = "Stop"

$remote = "$ServerUser@$ServerHost"

Write-Host "==> 推送代码到 $remote`:$RemoteAppDir"

# 不推送这些目录：.venv（服务器自己建）、data（运行时数据）、缓存、.git
$excludeNames = @(".venv", "data", "__pycache__", ".pytest_cache", ".git")

$itemsToCopy = Get-ChildItem -Path $LocalAppDir -Force |
    Where-Object { $excludeNames -notcontains $_.Name }

foreach ($item in $itemsToCopy) {
    Write-Host "    scp: $($item.Name)"
    if ($item.PSIsContainer) {
        scp -r $item.FullName "${remote}:${RemoteAppDir}/"
    } else {
        scp $item.FullName "${remote}:${RemoteAppDir}/"
    }
}

Write-Host "==> 远程重启计划任务: $TaskName"
# /end 在任务未运行时会报非零退出码，用 & 而不是 && 让 /run 无论如何都执行
ssh $remote "schtasks /end /tn `"$TaskName`" & schtasks /run /tn `"$TaskName`""

Write-Host "==> 等待服务重新监听"
Start-Sleep -Seconds 5

Write-Host "==> 远程健康检查"
ssh $remote "curl.exe -sS -o NUL -w `"HTTP %{http_code}`n`" --max-time 10 http://localhost:8095/hr/recruit-agent/"

Write-Host "==> 发版完成"
```

- [ ] **Step 5: 写部署说明 `docs/deploy-51-server.md`**

```markdown
# 部署到 51 服务器（Windows venv + 计划任务，无 Docker）

依据 `04-部署与门户挂载.md` §6 决策记录：目标服务器 `192.168.100.51` 是 Windows，
没有 Docker，沿用现有 4 个服务同款的部署模式——venv + 计划任务，不引入容器运行时。

## 前置条件

- 服务器已装 Python 3.11+（与其他现有服务共用的解释器版本对齐，若不确定找 IT 确认）
- 本机（开发机）到服务器的 SSH 访问已配置（`sync-to-server.ps1` 用 scp/ssh）
- 服务器能出公网访问 LLM 供应商 API（已实测：DeepSeek / 火山方舟 / 阿里百炼三家域名连通，见
  `04-部署与门户挂载.md` §4）

## 首次部署

1. 在服务器上创建部署目录（默认 `C:\apps\zhuopin-recruit-agent`）
2. 从开发机运行 `sync-to-server.ps1` 把代码推过去（首次也可以手工 scp，效果一样）
3. RDP 登录服务器，以管理员身份打开 PowerShell，进入部署目录，创建 `.env`
   （**不要把 `.env` 提交进 git，也不要放在门户可访问的路径下**）：

```
LLM_PROVIDER=<按 docs/m1-model-comparison.md 的决策填>
LLM_API_KEY=<真实 key>
LLM_BASE_URL=<对应供应商 base_url>
LLM_MODEL=<锁定版本号，禁止 latest>
LLM_SUPPORTS_JSON_SCHEMA=<true|false>
DB_PATH=data/demo.db
ROOT_PATH=/hr/recruit-agent
```

4. 运行首次部署脚本：

```powershell
.\deploy-server.ps1
```

5. 验证：`curl.exe http://localhost:8095/hr/recruit-agent/` 应返回带「演示环境」横幅的页面；
   `Get-ScheduledTask -TaskName ZhuopinRecruitAgent` 应显示 `Ready`/`Running`
6. 请 Paul 在门户导航加一行外链，指向 `http://192.168.100.51:8095/hr/recruit-agent/`
   （板块名「HR·招聘智能体」，见 `04-部署与门户挂载.md` §2「门户导航挂法」）

## 日常发版

代码有更新后，从开发机运行：

```powershell
.\sync-to-server.ps1
```

它会把代码 scp 推过去并重启计划任务。**依赖变更**（`requirements.txt` 改了）时，
额外 RDP 登录服务器重跑一次 `deploy-server.ps1`（对已存在的 venv 是幂等的，
只会重新 `pip install`，不会重建 venv）。

## 保活验证

- 重启服务器后，计划任务应在开机时自动拉起服务（`AtStartup` 触发器）：
  `Get-ScheduledTaskInfo -TaskName ZhuopinRecruitAgent` 查看 `LastRunTime`
- 手工 `Stop-Process` 掉 uvicorn 进程后，计划任务应在 1 分钟内自动重启
  （`-RestartCount 3 -RestartInterval 1分钟`，验证时最多等 3 次、共 3 分钟）

## 安全红线（照抄 `04-部署与门户挂载.md` §5，执行时逐条核对）

- [ ] `.env` 没有出现在门户可访问的任何路径下，也没有提交进 git
- [ ] LLM 凭据不在门户可访问的任何路径下
- [ ] 页面「演示环境，不进入正式招聘流程」标注清晰可见
- [ ] 访问日志沿用现有四服务同款做法（JSONL，不采集个人身份信息）

## 技术债提醒

- **过渡端口 8095 是临时的**：统一门户网关上线即迁移，届时只需网关加一条
  `proxy_pass` 指向 `root_path=/hr/recruit-agent`，本应用零改动（见计划开头 技术债 #8）
- **鉴权仍是门户共享口令**：M2 起处理真实简历前必须换成可识别到人的登录 + 访问留痕
  （见计划开头 技术债 #9，`04-部署与门户挂载.md` §4 风险二）
```

- [ ] **Step 6: Commit**

```bash
git add app/main.py deploy-server.ps1 sync-to-server.ps1 docs/deploy-51-server.md
git commit -m "feat: Windows 服务器部署（venv + 计划任务，无 Docker，部署约束4）"
```

---

### Task 12: 试运行反馈收集

**Files:**
- Create: `docs/m1-demo-pilot-feedback.md`

这是操作性任务（0.11），不是代码任务：交付一份可直接使用的反馈收集表 + 执行清单，实际邀请 3 位业务经理试跑、收集反馈是人工操作，不能由这个计划代劳，只能把执行框架准备好。

- [ ] **Step 1: 写反馈收集表与执行清单**

```markdown
# M1 Demo 试运行反馈收集

> 目标：请 3 位业务经理各跑一个真实岗位需求，收集反馈，为进入 `tasks.md` 第 1 章前的技术债排期提供依据。

## 执行清单

- [ ] 确认 Demo 已部署到 51 服务器且门户导航可访问（见 `docs/deploy-51-server.md`）
- [ ] 选定 3 位业务经理，每人准备 1 个真实、正在招或近期招过的岗位
- [ ] 逐一陪同或远程跑一遍：一句话需求 → 多轮追问 → 确认画像 → 查看 JD
- [ ] 每人跑完后立即用下表记录反馈（趁热记，不要事后回忆）

## 反馈记录表（每位业务经理一份）

| 项目 | 内容 |
|---|---|
| 业务经理 | |
| 岗位 | |
| 追问轮数 | |
| 是否触发"未指定"降级 | 是/否，具体字段： |
| 画像技术栈字段是否准确（业务经理自评） | 准确 / 部分准确 / 不准确，具体： |
| JD 是否可直接用（业务经理自评） | 可直接用 / 需小改 / 需大改 |
| 对话是否顺畅（有没有追问跑偏、重复问同一件事） | |
| 其他吐槽/建议 | |

## 汇总与下一步

- 3 份反馈汇总后，若技术栈字段准确率明显低于 `02-系统架构与MVP范围.md` 定的 ≥80% 目标（demo 阶段样本量小，仅作预警不是正式验收——正式验收是 tasks.md 9.1 的 10 个历史岗位重跑），记录下来在 1.x 之前排查
- 把「技术债」小节（见本计划开头）列的 9 项拿出来，结合反馈里暴露的问题排优先级，再进 `tasks.md` 第 1 章
```

- [ ] **Step 2: Commit**

```bash
git add docs/m1-demo-pilot-feedback.md
git commit -m "docs: M1 Demo 试运行反馈收集表与执行清单（0.11）"
```

---

## Self-Review（写完计划后的自查）

**Spec 覆盖检查**

`job-profile-intake/spec.md`：
- 对话式需求发起（Web scenario）→ Task 7 + Task 9 + Task 10；企微 scenario 依赖 Channel 抽象但不实现（技术债 3）
- 需求描述为空或无关 → Task 7 `is_job_related=False` 分支
- 多轮追问补全（识别模糊项、追问上限）→ Task 7
- 业务经理中途放弃 → **Out of Scope**（已列出，需企微通道支撑）
- 结构化岗位画像产出（含 Schema 校验/重试） → Task 2 + Task 4 + Task 7
- 硬门槛规则草案提取 → **Out of Scope**（tasks.md 第 5 章）
- 采集过程审计留痕 → Task 4 `AuditHook`（no-op 占位，技术债 1）

`job-description/spec.md`：
- JD 生成 / 画像未冻结拒绝生成 → Task 8 + Task 10（`confirm` 409 分支）
- AI 生成内容标识 / 标识不可被移除 → Task 8（标识注入）；编辑保护部分 **Out of Scope**（demo 无编辑功能）
- 歧视性表述拦截 → Task 8
- 文案导出（复制）→ Task 10 前端；生成留痕复用 Task 4 的 `AuditHook`

`job-profile-approval/spec.md`：
- 画像确认断点（Web scenario，通道无关）→ Task 9 `effect_confirm_profile` + Task 10 `confirm` 路由；企微 scenario 依赖 Channel 抽象但不实现
- 副作用幂等 → Task 3 `idempotent_effect` + Task 9 幂等专项测试（`test_graph_idempotency.py`）
- 回调可靠接收 → **Out of Scope**（企微通道本体不实现，无回调可接收）
- 决策留痕 → `human_review` 完整落库是技术债，本单元只在 `job_profile.status` 记录确认结果

`CLAUDE.md` 部署约束（本次重新生成的核心）：
- 部署约束 1（路径前缀就绪）→ Task 1 `Settings.root_path` + Task 10 `create_app(root_path=...)` 自挂载机制 + `test_app_works_when_mounted_at_arbitrary_subpath` / `test_unprefixed_paths_404_when_root_path_is_configured` 两个测试，真实覆盖"挂到任意子路径都能正常工作"这条硬验收标准，不是文字承诺
- 部署约束 2（过渡端口 8095）→ Task 11 `deploy-server.ps1`/`sync-to-server.ps1` 硬编码默认值 8095；迁移到网关登记为技术债 8
- 部署约束 3（鉴权中间件空壳）→ Task 1 `AuthMiddleware` + `AuthContext`，Task 10 `create_app` 挂载
- 部署约束 4（Windows venv + 计划任务，无 Docker）→ Task 11 完整实现，两个 PowerShell 脚本均为可直接执行内容，不引用「企业AI转型」仓库的 `ZhuopinDeploy.psm1`
- 部署约束 5（M2 起需登录+留痕）→ 不在本单元范围，登记为技术债 9，`AuthMiddleware` 的空壳接入点已为此留位

**占位符检查**：全文搜索过，没有 TBD/TODO；唯一"留待人工填写"的是 `docs/m1-model-comparison.md` 的实测数字表格和 `docs/m1-demo-pilot-feedback.md` 的反馈表——这两处本质是"数据收集模板"，不是代码或逻辑占位，已在对应 Task 里明确说明原因。`deploy-server.ps1` / `sync-to-server.ps1` 是完整可执行的 PowerShell 脚本，不是伪代码；服务器上是否已装 Python 3.11+ 作为前置条件在 `docs/deploy-51-server.md` 里注明，不是脚本里的占位符。

**类型一致性检查**：`JobProfile`（Task 2）在 Task 4/5/7/8/10 里签名一致；`IntakeState`（Task 9）字段名在 `nodes.py`/`build.py`/`server.py` 里一致；`OutboundMessage.type` 取值集合（`question`/`confirmation_prompt`/`jd_result`/`needs_manual`）在 Task 6/9/10 前端里保持一致；`create_app(*, db_path, gateway_factory, root_path="")` 签名在 Task 10（定义）与 Task 11 `app/main.py`（调用，传入 `settings.root_path`）之间一致；`AuthMiddleware`/`AuthContext`（Task 1 定义）与 Task 10 `create_app` 里的 `app.add_middleware(AuthMiddleware)` 调用一致。

**幂等覆盖检查**：`effect_persist_draft`、`effect_deliver_message`、`effect_confirm_profile` 三个写库/投递动作全部用 `idempotent_effect` 装饰，Task 9 有专项重放测试。

**路径前缀验收专项自查**（对应用户本次重新生成的核心诉求，不能只是文字承诺）：
- `test_app_works_when_mounted_at_arbitrary_subpath`（`tests/test_web_api.py`，Task 10 Step 1）用 `root_path="/foo/bar"`（不是默认值 `/hr/recruit-agent`）验证首页与 API 路由都返回 200，不 404
- `test_unprefixed_paths_404_when_root_path_is_configured` 反向验证：配置了前缀后，不带前缀的路径必须 404，证明前缀真的生效而非摆设
- `test_frontend_html_has_no_hardcoded_absolute_api_or_static_paths` 验证产出的 HTML 里没有硬编码的 `/api/...` 绝对路径引用
- 这套 router-prefix 自挂载 + `<base href>` 注入的机制在写入计划前已用真实 FastAPI 0.115.6 + httpx 0.28.1（与 `requirements.txt` 锁定版本一致）跑通验证，不是凭经验猜测的设计

**全量端到端验证（非常规自查，专为这次重新生成做的）**：写完计划后，把全部 12 个 Task 的代码从这份文档里原样抽取落盘、按 `requirements.txt` 精确锁定版本（含 `langgraph==1.0.10`、`langgraph-checkpoint-sqlite==2.0.6`）装进 Python 3.12（满足 `pyproject.toml` 的 `>=3.11`）venv，跑了一遍完整 `pytest`。**53 个测试全部 PASS**（2026-08-09 因铁律 5 更新给 Task 4 补了 1 个测试，现应为 54 个），过程中发现并修复了 3 个真实 bug（均已同步进上面对应 Task 的代码块，不是残留问题）：

1. **Task 8 `generate_jd` 重试次数差一**：`range(max_retries + 1)` 复用了 `LLMGateway.max_retries` 的"首次+N次重试"约定，导致 `max_retries=2` 时最多尝试 3 次，既让 `test_needs_manual_after_two_consecutive_hits` 报错（脚本只给 2 条响应），也悄悄突破了 spec「连续 2 次仍出现则转人工处理」的字面约束——已改成 `range(max_retries)`
2. **Task 3 `get_connection` 跨线程报错**：FastAPI 把同步路由处理函数派发到线程池，而 `create_app` 只建一个共享 `sqlite3.Connection`，默认 `check_same_thread=True` 导致 `sqlite3.ProgrammingError`——已加 `check_same_thread=False`（demo 规模不追求高并发，风险可接受；M2 迁移到 Postgres 后用连接池，这个问题自然消失）
3. **Task 9 `build_intake_graph` checkpointer 用法错误**：`SqliteSaver.from_conn_string(db_path)` 返回的是 `@contextmanager` 生成器，不 `with` 直接传给 `graph.compile()` 会抛 `TypeError: Invalid checkpointer provided`——已改成 `SqliteSaver(conn)`，复用函数本就持有的连接

这三个 bug 都不是本次"两处必须变"范围内引入的新代码——它们藏在从旧计划原样搬运的 Task 3/8/9 里，此前从未被真正执行过（`run-build` 尚未开始）。如果不做这次全量重试，会在 `run-build` 阶段才被 TDD 的"运行确认通过"步骤捕获，届时才第一次发现，现在提前堵上。

---

## 交付要求核对表（回应 spec-to-plan 的自查清单）

- [x] Global Constraints 段与 CLAUDE.md 逐字一致（含新增的部署约束 5 条）——**2026-08-09 补记**：CLAUDE.md 工程铁律 5 在本计划生成后更新，新增"响应实际 model 字段须与配置值分开持久化"一条；Global Constraints 段与 Task 4 已同步更新为现行版本，详见 Global Constraints 段工程铁律 5 与其下的 Task 4 专属追加要求
- [x] 本单元覆盖的 Requirement 均能指到 Task；未覆盖的在 Out of Scope 列出
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [x] 无 TBD/TODO/"适当处理"类占位符
- [x] 前后 Task 类型名、函数签名、字段名一致
- [x] 每个有副作用的动作独占 Task 步骤且带幂等键
- [x] AI 评分相关：本单元不做评分（M2 范围），故不适用 `evidence_ref` 断言；JD 生成的可追溯性由「文案不得凭空出现画像外技术要求」的 prompt 约束 + 人工试跑反馈（Task 12）间接验证
- [x] **路径前缀子路径挂载测试真实存在于测试代码里**（`tests/test_web_api.py` 的 `test_app_works_when_mounted_at_arbitrary_subpath` 等三个测试），不是文字承诺——已用真实 FastAPI 依赖版本验证通过
- [x] **Windows 部署两个 PowerShell 脚本是完整可执行内容**（`deploy-server.ps1` / `sync-to-server.ps1`），不是伪代码或占位注释；均不引用「企业AI转型」仓库的 `ZhuopinDeploy.psm1`
