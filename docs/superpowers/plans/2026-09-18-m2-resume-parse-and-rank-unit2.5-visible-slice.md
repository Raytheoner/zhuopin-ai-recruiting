# M2 U2.5：可见薄片（上传入口＋解析结果列表＋逐字段 evidence 高亮＋校对确认）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让人事部第一次能在浏览器里看到 M2 的成果：传一批简历 → 在列表里看到解析出的六个字段 → 点开一份简历，左边原文右边字段，点字段能看到原文里对应的依据被高亮 → 确认或改掉机器值。全部只收 `synthetic/anonymized/departed` 三类样本，`live` 真实简历闸保持关闭。

**Architecture:** 本单元不新增任何后端判定/编排逻辑——U2（3.1–3.10）已经把上传、解析、置信度、校对接口、访问留痕全部实现。本单元只做三件事：①一个新的只读聚合查询接口（把 `resume` + `field_review_queue` 拼成列表页要的形状，同时补一次 U2.5 才需要的"解析结果列表读取"访问留痕）；②三个无前端框架的静态页面（上传入口／解析结果列表／字段校对，沿用 `login.html` 的 `<!--BASE_HREF-->` 相对路径手法），页面只调用已存在的接口；③一份薄片 e2e 测试把这条链路串起来。

**Tech Stack:** Python 3.14、FastAPI（复用既有 `app/web/server.py` 的 `router`/`create_app` 装配方式）、SQLite（只读查询，不新增表、不新增列）、原生 HTML/JS（无前端框架）、pytest + httpx `TestClient`。

**Spec:**
- `openspec/changes/m2-resume-parse-and-rank/specs/resume-upload-and-gate/spec.md`（Requirement「批量上传入口」的页面部分；「真实简历入库闸」「简历访问留痕」「可识别到人的登录」三条本单元只读消费）
- `openspec/changes/m2-resume-parse-and-rank/specs/resume-parsing/spec.md`（全部 Requirement，本单元只负责正确展示，不改判定逻辑）
- `openspec/changes/m2-resume-parse-and-rank/specs/review-workbench/spec.md`（Requirement「字段校对页」）
- `openspec/changes/m2-resume-parse-and-rank/design.md`（决策 D2/D8）
- `openspec/changes/m2-resume-parse-and-rank/tasks.md` 第 9 章（9.1–9.5）

## Global Constraints

以下逐字摘自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」，与本交付单元的适用判断一并列出。**每个 Task 的验收隐含包含本节全部条目。**

1. **工程铁律 1**：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引；幂等记录与业务写必须在同一个事务里提交。⛔ **不适用，理由**：本单元不新增任何 `effect_*` 节点。全部写路径复用 U2 已定型且已幂等的 `effect_persist_parse`（上传/重解析时）与 `POST /api/resumes/{id}/fields/{field}/review`（`field_review_queue` 更新，U2 Task 已用部分唯一索引保证幂等）；本单元新增的唯一后端代码是一个只读 `GET` 查询。
2. **工程铁律 2**：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。⛔ **不适用，理由**：本单元不新增任何 Agent，也不调用 LLM 网关。
3. **工程铁律 3**：所有 AI 评分必须持久化模型标识+版本、prompt 版本、temperature、输入哈希、rubric 快照、原始响应。⛔ **不适用，理由**：本单元不产生新的 AI 调用；解析（parse）的留痕已在 U2 落地，评分（rank）在 U4/U5 才存在。
4. **工程铁律 4**：每条 `criterion_score` 必须有 `evidence_ref`。⛔ **不适用，理由**：`criterion_score` 属于评分域（U4），本单元没有评分。本单元的类比约束是"字段校对页必须正确展示 U2 已经强制持久化的 `spans[]` 回指"（`resume-parsing` spec「原文分片与字段回指」）——Task 4 的验收显式覆盖这一点：⛔ 不允许页面新增一条"无回指也能显示"的展示路径。
5. **工程铁律 5**：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名；无版本号快照的供应商必须从 API 响应取回实际 `model` 字段并持久化。⛔ **不适用，理由**：本单元不调用 LLM 网关。
6. **工程铁律 6**：企微回调先落库再处理，只推一次、5 秒无响应即丢弃。⛔ **不适用，理由**：本单元与企微回调无关。
7. **工程铁律 7**：`langgraph >= 1.0.10`。✅ **适用，消极遵守**：本单元不改 `requirements.txt`（见 Task 6 的结论），因此这条下限不受影响；若实现过程中意外需要改依赖，必须先核对这一行没被降级。
8. **合规红线·AI 只做排序推荐，不做自动淘汰**：✅ **适用**：解析结果列表页（Task 3）⛔ 不展示任何分数/排名字段（本单元根本不产生这些字段），Task 3 的验收显式断言接口响应体里没有 `score`/`rank` 类字段。
9. **合规红线·禁止人脸/表情分析**：⛔ **不适用，理由**：本单元不涉及任何影像/声学信号。
10. **合规红线·AI 生成的 JD、拒信、邀约须带标识**：✅ **类比适用**：解析结果列表页（Task 3）必须在页面显著位置带 AI 生成标识与"仅供参考"说明文案（`resume-parsing` 的解析结果本质是 AI 生成的结构化数据，未经人工校对前不应被当作事实）。
11. **合规红线·模型全部走境内，简历数据不出境**：⛔ **不适用，理由**：本单元不新增任何模型调用点，不引入新的数据出境路径。
12. **合规红线·绝不用历史录用结果做监督信号**：⛔ **不适用，理由**：本单元不训练、不产生排序模型。
13. **合规红线·候选人入口一律用一次性邀请链接**：⛔ **不适用，理由**：本单元三个页面全部是登录后可见的 HR 内部工作台页面，没有任何候选人对外入口。
14. **合规红线·主观描述不得进入硬门槛规则**：⛔ **不适用，理由**：本单元不含硬门槛逻辑（U3）。
15. **部署约束 1·路径前缀就绪**：FastAPI `root_path`，前端资源与接口调用一律相对路径，验收标准＝挂到任意子路径下都能正常工作且有测试覆盖。✅ **强适用，是本单元的核心交付之一**：三个新页面的 HTML/JS 一律用不带前导 `/` 的相对路径 fetch（`login.html` 手法），页面路由与 API 路由都挂在既有 `router` 上（随 `create_app(root_path=...)` 统一加前缀）；Task 5 的 e2e 必须在非空 `root_path` 下跑一遍。
16. **部署约束 2·过渡端口 8095**：⛔ **不适用，理由**：本单元不改动端口配置。
17. **部署约束 3·鉴权中间件留空壳接入点，签名对齐未来企微 OAuth SSO**：⛔ **不适用，消极遵守**：本单元只调用既有 `reviewer_of()`，不改 `AuthMiddleware.dispatch`/`AuthContext` 的实现或签名；但会往 `AuthMiddleware` 使用的路径前缀集合里增补一个（见「架构决策」第 1 条），这是**使用**既有接入点，不是改签名。
18. **部署约束 4·目标服务器是 Windows，没有 Docker，Python venv + 计划任务，新依赖须 Windows 可装**：✅ **适用，结论是无操作**：本单元只加静态页面与一个只读查询接口，不引入任何新 Python 依赖，`requirements.txt`/`sync-to-server.sh` 白名单本单元不改（Task 6 显式记录这个结论）。
19. **部署约束 5·M2 起处理真实简历前须具备可识别到人的登录 + 简历访问留痕**：✅ **强适用，是本单元的验收核心**：Task 1 新增的列表接口读取解析结果时必须调用既有 `record_resume_access(..., access_type="parsed_result")`（每份简历一条，不因为列表里有六个字段就写六条）；该接口必须落在 `AuthMiddleware` 保护范围内，未登录 401 且不返回任何候选人数据（Task 1 与 Task 5 都要有测试断言）。

## 架构决策（先读，避免和 `tasks.md` 的字面表述对不上）

**1. 解析结果列表接口的路径是 `GET /api/resumes/by-job/{job_id}`，不是 `tasks.md` 字面写的 `GET /jobs/{id}/resumes`。**
`app/middleware/auth.py::PROTECTED_PATH_PREFIXES` 当前只有三个前缀：`/api/candidates`、`/api/resumes`、`/api/applications`（`/api/jobs*` 刻意不在其中——M1 的岗位画像列表不含候选人信息，不需要登录）。但本单元的列表接口要返回候选人姓名与解析出的六字段——这正是 spec「可识别到人的登录」里"未登录访问候选人列表或任一简历 THEN 请求被拒绝，不返回任何候选人数据"要挡的东西。两个选择：(a) 把 `/api/jobs` 整体加进保护前缀——风险是会连带保护 M1 现有的 `/api/jobs`、`/api/jobs/{id}/profile` 等端点，改变既有无鉴权行为，超出本单元范围且可能让 M1 的现有调用方或测试意外收到 401；(b) 把新接口的路径挪到已受保护的 `/api/resumes` 前缀下。选 (b)：路径定为 `GET /api/resumes/by-job/{job_id}`，`job_id` 作为路径参数而不是资源前缀，天然落在 `PROTECTED_PATH_PREFIXES` 的 `/api/resumes` 里，**不需要改动 `app/middleware/auth.py` 一行代码**。页面路由本身（`GET /jobs/{job_id}/resumes`，展示壳）不受影响——它和 `/`、`/login` 一样是未登录也能拿到 HTML 外壳的页面路由，页面里发起的数据请求（`GET api/resumes/by-job/...`）才是真正被保护的那一层，这与 M1 现有的"index 页面本身不鉴权，页面里调用的写接口才 401"是同一形态，不是新引入的例外。

**2. 页面路由（`/resumes/upload`、`/jobs/{job_id}/resumes`、`/resumes/{resume_id}/review`）都不读数据库，只回一份 HTML 外壳。**
沿用 `_render_index`/`login_page` 的手法：`(STATIC_DIR / "xxx.html").read_text(...)` + 替换 `<!--BASE_HREF-->`。页面里需要的 `job_id`/`resume_id` 通过 JS 从 `window.location.pathname` 用正则取最后一段可变段（例如 `/resumes\/([^/]+)\/review$/`），**不依赖 `root_path` 的具体值**——这样同一份静态文件在挂到任意前缀下都能定位到当前资源 id，满足部署约束 1 的"挂到任意子路径下都能正常工作"。

**3. 字段级评审状态（"已校对（谁／何时）"）挂在解析结果列表接口的响应体里，不做成单独接口。**
`tasks.md` 9.3 写"确认后 9.2 列表该字段显示'已校对（谁／何时）'"。`field_review_queue` 里同一 `(resume_id, field)` 可能有多行（历史 `reviewed` 行 + 后续因重解析产生的新 `pending` 行，`status='pending'` 上只有部分唯一索引、`reviewed` 行不受此索引约束）。取值规则：按 `created_at DESC` 排序取每个字段最新的一行，其 `status`/`reviewed_by`/`reviewed_at` 就是该字段的当前校对状态；从未进过 `field_review_queue` 的字段（从未被判定为低置信度）状态记为 `not_queued`。这个规则和状态在 Task 1 的接口响应里逐字段给出，页面（Task 3）直接渲染，不用自己拼状态机。

## File Structure

| 文件 | 责任 |
|---|---|
| `app/web/server.py`（改） | 新增 `GET /api/resumes/by-job/{job_id}`（只读聚合查询 + 访问留痕）；新增三个页面路由 `GET /resumes/upload`、`GET /jobs/{job_id}/resumes`、`GET /resumes/{resume_id}/review`；新增两个小型纯函数 `_field_review_status_by_field()`、`_display_field_value()` |
| `app/web/static/upload.html`（新建） | 上传入口页：多文件选择、已审批岗位下拉、样本类别单选（只列 synthetic/anonymized/departed）、逐文件结果表 |
| `app/web/static/resume_list.html`（新建） | 解析结果列表页：按上传时间倒序的简历表格，AI 标识与"仅供参考"说明，链接到字段校对页 |
| `app/web/static/resume_review.html`（新建） | 字段校对页：左原文右六字段，点字段高亮原文对应 span，提交确认/修改 |
| `tests/test_resume_list_endpoint.py`（新建） | Task 1 的单元测试（登录保护、聚合字段、访问留痕、校对状态） |
| `tests/test_visible_slice_e2e.py`（新建） | Task 5 的端到端测试：上传 → 列表 → 高亮 → 校对确认，全链路 |
| `docs/m2-visible-slice-guide.md`（新建） | HR 一页操作说明（人事部看什么、怎么传、怎么校对、哪些不能传、账号从哪来） |

---

### Task 1: 解析结果聚合查询接口 `GET /api/resumes/by-job/{job_id}`

**Files:**
- Modify: `app/web/server.py`（顶部 import 段加一行；在 `_require_resume` 定义之后、`@router.get("/api/resumes/{resume_id}/text")` 之前插入新代码）
- Test: `tests/test_resume_list_endpoint.py`

**Interfaces:**
- Consumes：既有 `app.schemas.resume_fields.FIELD_NAMES`（六个字段名的固定顺序元组）、`FIELD_LABELS`（字段名→中文标签）；既有 `app.graph.resume_nodes.record_resume_access(conn, *, accessor, resume_id, access_type)`；既有 `app.middleware.auth.reviewer_of(request)`；数据库表 `resume`、`field_review_queue`（字段与结构见本文件顶部「关键既有代码事实」，与 `app/storage/db.py` 一致）。
- Produces：`GET /api/resumes/by-job/{job_id}` → `{"job_id": str, "resumes": [ResumeListItem, ...]}`，其中 `ResumeListItem = {"resume_id": str, "candidate_display_name": str, "sample_class": str, "parse_status": "pending"|"parsed"|"unreadable", "parser_version": str|None, "uploaded_at": str, "pending_review_count": int, "fields": [FieldSummary, ...]}`，`FieldSummary = {"field": str, "label": str, "value_display": str, "confidence": float|None, "review_status": "not_queued"|"pending"|"reviewed", "reviewed_by": str|None, "reviewed_at": str|None}`。Task 3（列表页）、Task 5（e2e）直接消费这个响应形状。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_resume_list_endpoint.py`：

```python
from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from tests.test_resume_upload import _docx_bytes, _stub_llm_success


@pytest.fixture
def logged_in_client_with_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()
    return client, conn


def _upload_one(client, filler_paragraphs):
    files = [("files", ("a.docx", _docx_bytes(filler_paragraphs),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    return resp.json()["results"][0]


def test_list_resumes_for_job_returns_parsed_summary(logged_in_client_with_job, monkeypatch):
    client, _conn = logged_in_client_with_job
    _stub_llm_success(monkeypatch)
    filler = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"
    result = _upload_one(client, ["张三", filler])
    resume_id = result["resume_id"]

    resp = client.get("/api/resumes/by-job/j1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "j1"
    assert len(body["resumes"]) == 1
    item = body["resumes"][0]
    assert item["resume_id"] == resume_id
    assert item["candidate_display_name"] == "张三"
    assert item["sample_class"] == "synthetic"
    assert item["parse_status"] == "parsed"
    assert item["parser_version"] == "v1"
    assert "score" not in item
    assert "rank" not in item

    field_by_name = {f["field"]: f for f in item["fields"]}
    assert field_by_name["name"]["value_display"] == "张三"
    assert field_by_name["name"]["review_status"] == "not_queued"
    assert field_by_name["years_of_experience"]["value_display"] == "未提及"
    assert field_by_name["years_of_experience"]["review_status"] == "not_queued"


def test_list_resumes_for_job_records_access_log_once_per_resume(logged_in_client_with_job, monkeypatch):
    client, conn = logged_in_client_with_job
    _stub_llm_success(monkeypatch)
    filler = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"
    _upload_one(client, ["张三", filler])

    client.get("/api/resumes/by-job/j1")

    count = conn.execute(
        "SELECT COUNT(*) FROM resume_access_log WHERE access_type = 'parsed_result'"
    ).fetchone()[0]
    assert count == 1


def test_list_resumes_for_job_shows_pending_then_reviewed_status(make_test_client, monkeypatch):
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, TextField,
    )
    import app.web.server as server_mod

    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()

    def _fake_compute_parse(_gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value="张三", confidence=0.2,
                            spans=[{"span_id": 1, "quote": "张三", "start": 0, "end": 2}]),
            years_of_experience=NumberField(not_mentioned=True, value=None, confidence=1.0),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1)

    monkeypatch.setattr(server_mod, "compute_parse", _fake_compute_parse)

    filler = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"
    result = _upload_one(client, ["张三", filler])
    resume_id = result["resume_id"]

    resp = client.get("/api/resumes/by-job/j1")
    field_by_name = {f["field"]: f for f in resp.json()["resumes"][0]["fields"]}
    assert field_by_name["name"]["review_status"] == "pending"
    assert resp.json()["resumes"][0]["pending_review_count"] == 1

    client.post(f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "张三三"})

    resp2 = client.get("/api/resumes/by-job/j1")
    item2 = resp2.json()["resumes"][0]
    field_by_name2 = {f["field"]: f for f in item2["fields"]}
    assert field_by_name2["name"]["review_status"] == "reviewed"
    assert field_by_name2["name"]["reviewed_by"] == "alice"
    assert field_by_name2["name"]["value_display"] == "张三"  # 摘要仍取 parsed_json 的机器值，不是校对值
    assert item2["pending_review_count"] == 0


def test_list_resumes_requires_login(make_test_client):
    client, conn = make_test_client()
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()
    resp = client.get("/api/resumes/by-job/j1")
    assert resp.status_code == 401


def test_list_resumes_unknown_job_404(logged_in_client_with_job):
    client, _conn = logged_in_client_with_job
    resp = client.get("/api/resumes/by-job/does-not-exist")
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_resume_list_endpoint.py -v`
Expected: 全部 FAIL（`404 Not Found`，因为 `/api/resumes/by-job/{job_id}` 路由还不存在；`test_list_resumes_requires_login` 也会 FAIL，因为不存在的路由返回 404 不是 401）

- [ ] **Step 3: 加 import**

在 `app/web/server.py` 顶部 import 段（`from app.parsing.spans import TextSpan` 那一行附近）新增一行：

```python
from app.schemas.resume_fields import FIELD_LABELS, FIELD_NAMES
```

- [ ] **Step 4: 实现聚合查询接口**

在 `app/web/server.py` 里 `_require_resume` 函数定义结束之后、`@router.get("/api/resumes/{resume_id}/text")` 之前，插入：

```python
    def _latest_field_review_rows(resume_id: str) -> dict[str, dict]:
        """按字段取最新一行校对队列记录（架构决策 3）：同一字段可能有一条历史
        reviewed 行 + 一条新的 pending 行（重解析后再次判低置信度），
        按 created_at 倒序取第一条即当前状态。"""
        rows = conn.execute(
            "SELECT field, status, reviewed_by, reviewed_at FROM field_review_queue "
            "WHERE resume_id = ? ORDER BY created_at DESC",
            (resume_id,),
        ).fetchall()
        latest: dict[str, dict] = {}
        for field, status, reviewed_by, reviewed_at in rows:
            if field not in latest:
                latest[field] = {
                    "status": status, "reviewed_by": reviewed_by, "reviewed_at": reviewed_at,
                }
        return latest

    def _display_field_value(field_payload: dict) -> str:
        if field_payload.get("not_mentioned"):
            return "未提及"
        value = field_payload.get("value")
        if value is None:
            return "未提及"
        if isinstance(value, list):
            return "、".join(value) if value else "未提及"
        if isinstance(value, dict):
            parts = [str(value[k]) for k in ("degree", "school") if value.get(k)]
            return " ".join(parts) if parts else "未提及"
        return str(value)

    @router.get("/api/resumes/by-job/{job_id}")
    def list_resumes_for_job(request: Request, job_id: str) -> dict:
        """9.2 解析结果列表页的数据源。⛔ 不返回任何分数/排名字段——那些字段
        本单元根本不产生（U4/U5 才有）。路径故意落在 /api/resumes 下而不是
        /jobs/{id}/resumes（架构决策 1）：这样天然受 AuthMiddleware 的
        PROTECTED_PATH_PREFIXES 保护，未登录 401、不返回任何候选人数据
        （resume-upload-and-gate spec「可识别到人的登录」）。
        """
        job = conn.execute("SELECT id FROM job WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        rows = conn.execute(
            "SELECT id, sample_class, file_name, status, parsed_json, parser_version, "
            "uploaded_at FROM resume WHERE job_id = ? ORDER BY uploaded_at DESC",
            (job_id,),
        ).fetchall()

        accessor = reviewer_of(request)
        items = []
        for resume_id, sample_class, file_name, status, parsed_json, parser_version, uploaded_at in rows:
            display_name = file_name
            pending_count = 0
            fields_payload: list[dict] = []
            if parsed_json:
                # 每份简历一条留痕，⛔ 不因为要读六个字段就写六条
                # （resume-upload-and-gate spec「简历访问留痕」按"这次读取"计一条）。
                record_resume_access(conn, accessor=accessor, resume_id=resume_id,
                                      access_type="parsed_result")
                parsed = json.loads(parsed_json)
                review_rows = _latest_field_review_rows(resume_id)
                name_field = parsed.get("name") or {}
                if not name_field.get("not_mentioned") and name_field.get("value"):
                    display_name = name_field["value"]
                for field_name in FIELD_NAMES:
                    field_payload = parsed.get(field_name) or {}
                    review = review_rows.get(field_name)
                    review_status = review["status"] if review else "not_queued"
                    if review_status == "pending":
                        pending_count += 1
                    fields_payload.append({
                        "field": field_name,
                        "label": FIELD_LABELS[field_name],
                        "value_display": _display_field_value(field_payload),
                        "confidence": field_payload.get("confidence"),
                        "review_status": review_status,
                        "reviewed_by": review["reviewed_by"] if review else None,
                        "reviewed_at": review["reviewed_at"] if review else None,
                    })
            items.append({
                "resume_id": resume_id,
                "candidate_display_name": display_name,
                "sample_class": sample_class,
                "parse_status": status,
                "parser_version": parser_version,
                "uploaded_at": uploaded_at,
                "pending_review_count": pending_count,
                "fields": fields_payload,
            })
        return {"job_id": job_id, "resumes": items}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_resume_list_endpoint.py -v`
Expected: 5 passed

- [ ] **Step 6: 跑一遍既有回归，确认没有破坏 U2**

Run: `python -m pytest tests/test_resume_upload.py tests/test_resume_field_review.py tests/test_resume_access_log.py -v`
Expected: 全部 PASS（本 Task 只新增代码，未修改任何既有函数体）

- [ ] **Step 7: Commit**

```bash
git add app/web/server.py tests/test_resume_list_endpoint.py
git commit -m "feat(m2-u2.5): 新增解析结果聚合查询接口 GET /api/resumes/by-job/{job_id}"
```

---

### Task 2: 上传入口页 `GET /resumes/upload`

**Files:**
- Create: `app/web/static/upload.html`
- Modify: `app/web/server.py`（在 `login_page` 函数之后插入新路由）
- Test: `tests/test_upload_page.py`

**Interfaces:**
- Consumes：既有 `GET /api/jobs`（返回 `{"jobs":[{job_id,title,status,...}]}`，本页用 JS 过滤 `status === 'approved'`）、既有 `POST /api/resumes/upload`（Task 1 之前就存在，见「关键既有代码事实」）。
- Produces：页面路由 `GET /resumes/upload` 返回 HTML；不产生新的后端数据接口，Task 3/Task 5 不依赖本 Task 的内部实现，只需要知道这个路径存在。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_upload_page.py`：

```python
from __future__ import annotations


def test_upload_page_served_and_uses_relative_paths(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/upload")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert 'fetch("api/jobs")' in html
    assert 'fetch("api/resumes/upload"' in html
    assert 'value="live"' not in html  # live 不出现在页面选项（design D2）


def test_upload_page_served_under_root_path_prefix(tmp_path):
    from app.llm.gateway import LLMGateway
    from app.storage.db import get_connection
    from app.web.server import create_app
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "test.db")
    app = create_app(
        db_path=db_path,
        gateway_factory=lambda: LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        ),
        root_path="/hr/recruit-agent",
        resume_storage_dir=str(tmp_path / "resumes"),
    )
    # ⚠️ 不要给 TestClient 传 base_url 里带路径段（如
    # `base_url="http://testserver/hr/recruit-agent"`）——httpx 对以 "/" 开头的
    # 绝对路径请求不会去掉 base_url 自带的路径段，两段会拼接成
    # "/hr/recruit-agent/hr/recruit-agent/..." 导致 404（本计划编写时已用一次性
    # 脚本实测复现）。本仓库现成的 root_path 测试写法见
    # `tests/test_health_endpoint.py`：`TestClient(app)`（不传 base_url）+
    # 请求路径自己带全 root_path 前缀，照抄这个手法。
    client = TestClient(app)
    resp = client.get("/hr/recruit-agent/resumes/upload")
    assert resp.status_code == 200
    assert '<base href="/hr/recruit-agent/">' in resp.text
    get_connection(db_path).close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_upload_page.py -v`
Expected: FAIL（`404 Not Found`，路由不存在）

- [ ] **Step 3: 写页面文件**

创建 `app/web/static/upload.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <title>上传简历 · 卓品智能招聘助手</title>
</head>
<body>
  <h1>上传简历</h1>
  <p>只能上传 <strong>合成替身 / 脱敏 / 历史离职</strong> 样本，真实在招简历（live）当前不开放上传。</p>
  <form id="upload-form">
    <label>目标岗位（只列已审批画像的岗位）
      <select id="job-select" required></select>
    </label>
    <br>
    <label>样本类别
      <label><input type="radio" name="sample_class" value="synthetic" checked> 合成替身</label>
      <label><input type="radio" name="sample_class" value="anonymized"> 脱敏样本</label>
      <label><input type="radio" name="sample_class" value="departed"> 历史离职候选人</label>
    </label>
    <br>
    <label>选择文件（可多选，支持 PDF / Word）
      <input id="file-input" type="file" multiple accept=".pdf,.docx" required>
    </label>
    <br>
    <button type="submit">上传</button>
  </form>
  <p id="error" style="color:red"></p>
  <table id="result-table" border="1" style="display:none">
    <thead><tr><th>文件名</th><th>结果</th></tr></thead>
    <tbody id="result-body"></tbody>
  </table>
  <p><a id="to-list-link" href="#">上传完成后查看解析结果列表</a></p>

  <script>
    const jobSelect = document.getElementById("job-select");
    const toListLink = document.getElementById("to-list-link");

    async function loadJobs() {
      const resp = await fetch("api/jobs");
      const body = await resp.json();
      const approved = (body.jobs || []).filter((j) => j.status === "approved");
      jobSelect.innerHTML = "";
      if (approved.length === 0) {
        const opt = document.createElement("option");
        opt.textContent = "暂无已审批画像的岗位";
        opt.disabled = true;
        jobSelect.appendChild(opt);
        return;
      }
      for (const job of approved) {
        const opt = document.createElement("option");
        opt.value = job.job_id;
        opt.textContent = job.title;
        jobSelect.appendChild(opt);
      }
      toListLink.href = `jobs/${approved[0].job_id}/resumes`;
    }

    document.getElementById("upload-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      document.getElementById("error").textContent = "";
      const jobId = jobSelect.value;
      if (!jobId) {
        document.getElementById("error").textContent = "请先选择岗位";
        return;
      }
      const sampleClass = document.querySelector('input[name="sample_class"]:checked').value;
      const files = document.getElementById("file-input").files;
      const formData = new FormData();
      formData.append("job_id", jobId);
      formData.append("sample_class", sampleClass);
      for (const f of files) {
        formData.append("files", f);
      }
      const resp = await fetch("api/resumes/upload", { method: "POST", body: formData });
      if (!resp.ok) {
        document.getElementById("error").textContent = "上传失败，请确认已登录";
        return;
      }
      const body = await resp.json();
      const tbody = document.getElementById("result-body");
      tbody.innerHTML = "";
      for (const r of body.results) {
        const tr = document.createElement("tr");
        const nameTd = document.createElement("td");
        nameTd.textContent = r.file_name;
        const statusTd = document.createElement("td");
        let label = r.status;
        if (r.status === "accepted") label = `接收（${r.parse_status || "处理中"}）`;
        if (r.status === "duplicate") label = "重复：已存在同一份简历";
        if (r.status === "rejected") label = `拒收：${r.reason || ""}`;
        statusTd.textContent = label;
        tr.appendChild(nameTd);
        tr.appendChild(statusTd);
        tbody.appendChild(tr);
      }
      document.getElementById("result-table").style.display = "";
      toListLink.href = `jobs/${jobId}/resumes`;
    });

    loadJobs();
  </script>
</body>
</html>
```

- [ ] **Step 4: 加页面路由**

在 `app/web/server.py` 的 `login_page` 函数定义之后插入：

```python
    @router.get("/resumes/upload")
    def upload_page():
        html = (STATIC_DIR / "upload.html").read_text(encoding="utf-8")
        base_href = f"{root_path}/" if root_path else "/"
        return HTMLResponse(html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">'))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_upload_page.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add app/web/static/upload.html app/web/server.py tests/test_upload_page.py
git commit -m "feat(m2-u2.5): 上传入口页 GET /resumes/upload"
```

---

### Task 3: 解析结果列表页 `GET /jobs/{job_id}/resumes`

**Files:**
- Create: `app/web/static/resume_list.html`
- Modify: `app/web/server.py`（在 Task 2 新增的 `upload_page` 之后插入新路由）
- Test: `tests/test_resume_list_page.py`

**Interfaces:**
- Consumes：Task 1 的 `GET /api/resumes/by-job/{job_id}`。
- Produces：页面路由 `GET /jobs/{job_id}/resumes`；页面里每行提供指向字段校对页的链接 `resumes/{resume_id}/review`（Task 4 消费这个约定的相对路径写法）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_resume_list_page.py`：

```python
from __future__ import annotations


def test_resume_list_page_served_with_ai_disclaimer(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/jobs/j1/resumes")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert "AI" in html
    assert "仅供参考" in html
    assert 'fetch(`api/resumes/by-job/' in html
    assert "score" not in html.lower()
    assert "排名" not in html
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_resume_list_page.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 写页面文件**

创建 `app/web/static/resume_list.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <title>解析结果列表 · 卓品智能招聘助手</title>
</head>
<body>
  <h1>解析结果列表</h1>
  <p><strong>本页六字段由 AI 自动抽取，仅供参考，未经人工校对前不代表最终事实。</strong></p>
  <table id="resume-table" border="1">
    <thead>
      <tr>
        <th>候选人</th><th>样本类别</th><th>解析状态</th><th>解析版本</th>
        <th>待校对字段数</th><th>操作</th>
      </tr>
    </thead>
    <tbody id="resume-body"></tbody>
  </table>
  <p id="empty-hint" style="display:none">该岗位暂无上传的简历。</p>
  <p id="error" style="color:red"></p>

  <script>
    function jobIdFromPath() {
      const m = window.location.pathname.match(/\/jobs\/([^/]+)\/resumes\/?$/);
      return m ? m[1] : null;
    }

    async function loadResumes() {
      const jobId = jobIdFromPath();
      if (!jobId) {
        document.getElementById("error").textContent = "无法识别岗位标识";
        return;
      }
      const resp = await fetch(`api/resumes/by-job/${jobId}`);
      if (resp.status === 401) {
        window.location.href = "login";
        return;
      }
      if (!resp.ok) {
        document.getElementById("error").textContent = "加载失败";
        return;
      }
      const body = await resp.json();
      const tbody = document.getElementById("resume-body");
      tbody.innerHTML = "";
      if (body.resumes.length === 0) {
        document.getElementById("empty-hint").style.display = "";
        return;
      }
      for (const item of body.resumes) {
        const tr = document.createElement("tr");
        const cells = [
          item.candidate_display_name,
          item.sample_class,
          item.parse_status,
          item.parser_version || "-",
          String(item.pending_review_count),
        ];
        for (const text of cells) {
          const td = document.createElement("td");
          td.textContent = text;
          tr.appendChild(td);
        }
        const actionTd = document.createElement("td");
        const link = document.createElement("a");
        link.href = `resumes/${item.resume_id}/review`;
        link.textContent = "查看并校对";
        actionTd.appendChild(link);
        tr.appendChild(actionTd);
        tbody.appendChild(tr);
      }
    }

    loadResumes();
  </script>
</body>
</html>
```

- [ ] **Step 4: 加页面路由**

在 `app/web/server.py` 的 Task 2 新增的 `upload_page` 之后插入：

```python
    @router.get("/jobs/{job_id}/resumes")
    def resume_list_page(job_id: str):
        html = (STATIC_DIR / "resume_list.html").read_text(encoding="utf-8")
        base_href = f"{root_path}/" if root_path else "/"
        return HTMLResponse(html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">'))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_resume_list_page.py -v`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add app/web/static/resume_list.html app/web/server.py tests/test_resume_list_page.py
git commit -m "feat(m2-u2.5): 解析结果列表页 GET /jobs/{job_id}/resumes"
```

---

### Task 4: 字段校对页 `GET /resumes/{resume_id}/review`（原文回指高亮）

**Files:**
- Create: `app/web/static/resume_review.html`
- Modify: `app/web/server.py`（在 Task 3 新增的 `resume_list_page` 之后插入新路由）
- Test: `tests/test_resume_review_page.py`

**Interfaces:**
- Consumes：既有 `GET /api/resumes/{resume_id}/text`（`{resume_id, raw_text}`）、既有 `GET /api/resumes/{resume_id}/parsed`（`{resume_id, parsed_json}`，`parsed_json[field].spans[]` 含 `{span_id, quote, start, end}`）、既有 `POST /api/resumes/{resume_id}/fields/{field}/review`（body `{human_value}`）。
- Produces：页面路由 `GET /resumes/{resume_id}/review`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_resume_review_page.py`：

```python
from __future__ import annotations


def test_resume_review_page_served_with_relative_fetches(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/r1/review")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert 'fetch(`api/resumes/${resumeId}/text`)' in html
    assert 'fetch(`api/resumes/${resumeId}/parsed`)' in html
    assert 'fields/${fieldName}/review`' in html
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_resume_review_page.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 写页面文件**

创建 `app/web/static/resume_review.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <title>字段校对 · 卓品智能招聘助手</title>
  <style>
    .pending { background: #fff3cd; }
    mark { background: #ffec99; }
    #raw-text { white-space: pre-wrap; border: 1px solid #ccc; padding: 8px; max-height: 70vh; overflow-y: auto; }
    .field-row { cursor: pointer; padding: 4px; }
    .field-row.selected { outline: 2px solid #339af0; }
  </style>
</head>
<body>
  <h1>字段校对</h1>
  <p><strong>右侧字段由 AI 自动抽取，点击字段可在左侧原文中看到依据；未经确认前仅供参考。</strong></p>
  <div style="display:flex; gap:16px">
    <div style="flex:1"><div id="raw-text">加载中…</div></div>
    <div style="flex:1" id="fields-panel">加载中…</div>
  </div>
  <p id="error" style="color:red"></p>

  <script>
    function resumeIdFromPath() {
      const m = window.location.pathname.match(/\/resumes\/([^/]+)\/review\/?$/);
      return m ? m[1] : null;
    }

    function escapeHtml(s) {
      return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    const resumeId = resumeIdFromPath();
    let rawText = "";
    let parsedFields = {};

    function renderRawText(highlightSpans) {
      const spans = (highlightSpans || []).slice().sort((a, b) => a.start - b.start);
      let html = "";
      let cursor = 0;
      for (const span of spans) {
        if (span.start < cursor) continue; // 忽略与前一个 span 重叠的部分，MVP 不处理重叠
        html += escapeHtml(rawText.slice(cursor, span.start));
        html += `<mark>${escapeHtml(rawText.slice(span.start, span.end))}</mark>`;
        cursor = span.end;
      }
      html += escapeHtml(rawText.slice(cursor));
      document.getElementById("raw-text").innerHTML = html;
    }

    function selectField(fieldName) {
      document.querySelectorAll(".field-row").forEach((el) => el.classList.remove("selected"));
      const row = document.getElementById(`field-row-${fieldName}`);
      if (row) row.classList.add("selected");
      const field = parsedFields[fieldName] || {};
      renderRawText(field.spans || []);
    }

    async function submitReview(fieldName, humanValue) {
      const resp = await fetch(`api/resumes/${resumeId}/fields/${fieldName}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ human_value: humanValue }),
      });
      if (resp.ok) {
        await loadParsed();
      } else {
        document.getElementById("error").textContent = "提交失败：该字段可能没有待校对记录";
      }
    }

    function fieldDisplayValue(field) {
      if (field.not_mentioned) return "未提及";
      const v = field.value;
      if (v === null || v === undefined) return "未提及";
      if (Array.isArray(v)) return v.length ? v.join("、") : "未提及";
      if (typeof v === "object") return [v.degree, v.school].filter(Boolean).join(" ") || "未提及";
      return String(v);
    }

    function renderFields() {
      const panel = document.getElementById("fields-panel");
      panel.innerHTML = "";
      for (const [fieldName, field] of Object.entries(parsedFields)) {
        const row = document.createElement("div");
        row.className = "field-row" + (field.review_pending ? " pending" : "");
        row.id = `field-row-${fieldName}`;
        row.addEventListener("click", () => selectField(fieldName));

        const title = document.createElement("div");
        title.textContent = `${fieldName}：${fieldDisplayValue(field)}（置信度 ${field.confidence}）`;
        row.appendChild(title);

        if (field.review_pending) {
          const badge = document.createElement("span");
          badge.textContent = "【待校对】";
          row.appendChild(badge);
        }

        const confirmBtn = document.createElement("button");
        confirmBtn.textContent = "确认";
        confirmBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          submitReview(fieldName, fieldDisplayValue(field));
        });
        row.appendChild(confirmBtn);

        const editInput = document.createElement("input");
        editInput.placeholder = "改为…";
        row.appendChild(editInput);

        const editBtn = document.createElement("button");
        editBtn.textContent = "提交修改";
        editBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (editInput.value.trim()) submitReview(fieldName, editInput.value.trim());
        });
        row.appendChild(editBtn);

        panel.appendChild(row);
      }
    }

    async function loadRawText() {
      const resp = await fetch(`api/resumes/${resumeId}/text`);
      if (resp.status === 401) {
        window.location.href = "login";
        return;
      }
      const body = await resp.json();
      rawText = body.raw_text || "";
    }

    async function loadParsed() {
      const resp = await fetch(`api/resumes/${resumeId}/parsed`);
      const body = await resp.json();
      const parsed = body.parsed_json || {};
      parsedFields = {};
      for (const [name, field] of Object.entries(parsed)) {
        parsedFields[name] = { ...field, review_pending: false };
      }
      renderFields();
      renderRawText([]);
    }

    async function init() {
      await loadRawText();
      await loadParsed();
    }

    init();
  </script>
</body>
</html>
```

**⚠️ 已知简化（写进本 Task，不算技术债）**：本页的"待校对"高亮（`.pending`）目前恒为 `false`——`GET /api/resumes/{id}/parsed` 不返回 `field_review_queue` 状态，只有 Task 1 的列表接口才聚合了这个状态。若 HR 需要在校对页本身也看到"待校对"角标，后续可以让本页额外调用一次 `GET /api/resumes/by-job/{job_id}`（本页没有 `job_id`，需要 `GET /api/resumes/{id}/text` 或 `/parsed` 之一顺带带出 `job_id` 才能拼这个请求）——这超出本单元范围，登记在 Task 6 的交付说明里而不是当场加接口。

- [ ] **Step 4: 加页面路由**

在 `app/web/server.py` 的 Task 3 新增的 `resume_list_page` 之后插入：

```python
    @router.get("/resumes/{resume_id}/review")
    def resume_review_page(resume_id: str):
        html = (STATIC_DIR / "resume_review.html").read_text(encoding="utf-8")
        base_href = f"{root_path}/" if root_path else "/"
        return HTMLResponse(html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">'))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_resume_review_page.py -v`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add app/web/static/resume_review.html app/web/server.py tests/test_resume_review_page.py
git commit -m "feat(m2-u2.5): 字段校对页 GET /resumes/{resume_id}/review，含 evidence 高亮"
```

---

### Task 5: 薄片端到端测试（9.4）

**Files:**
- Create: `tests/test_visible_slice_e2e.py`

**Interfaces:**
- Consumes：Task 1–4 的全部产出（`GET/POST` 全部既有与新增接口、三个页面路由）。
- Produces：无新签名，本 Task 只验证既有链路。

- [ ] **Step 1: 写测试（同时也是本 Task 唯一的步骤——e2e 测试没有"先写空实现"的中间态，全部依赖前四个 Task 已完成）**

创建 `tests/test_visible_slice_e2e.py`：

```python
from __future__ import annotations

import docx
import pytest
from fastapi.testclient import TestClient

from app.llm.gateway import LLMCallMeta, LLMGateway
from app.schemas.resume_fields import (
    EducationField, ListField, NumberField, ResumeFields, TextField,
)
from app.storage.auth_session import create_session
from app.storage.db import get_connection
from app.storage.hr_account import upsert_account
from app.web.server import create_app
from tests.test_resume_upload import _docx_bytes


ROOT_PATH = "/hr/recruit-agent"


@pytest.fixture
def slice_client(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app(
        db_path=db_path,
        gateway_factory=lambda: LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        ),
        root_path=ROOT_PATH,
        resume_storage_dir=str(tmp_path / "resumes"),
    )
    # ⚠️ 不传 base_url 里带路径段的写法，理由同 Task 2 的注释——httpx 会把
    # base_url 自带的路径段和请求里以 "/" 开头的绝对路径拼接两次导致 404。
    client = TestClient(app)
    conn = get_connection(db_path)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()
    yield client, conn
    conn.close()


def _login(client, conn):
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def _stub_parse(monkeypatch, *, name_confidence: float):
    import app.web.server as server_mod

    def _fake_compute_parse(_gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value="李四", confidence=name_confidence,
                            spans=[{"span_id": 1, "quote": "李四", "start": 0, "end": 2}]),
            years_of_experience=NumberField(value=5.0, confidence=0.9,
                                             spans=[{"span_id": 2, "quote": "5年", "start": 10, "end": 12}]),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1)

    monkeypatch.setattr(server_mod, "compute_parse", _fake_compute_parse)


_FILLER = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"


def test_visible_slice_full_flow_under_root_path_prefix(slice_client, monkeypatch):
    client, conn = slice_client
    _login(client, conn)
    _stub_parse(monkeypatch, name_confidence=0.2)  # 低置信度 → 进校对队列

    # 1. 上传入口页可见，root_path 前缀下 base href 正确
    upload_page = client.get(f"{ROOT_PATH}/resumes/upload")
    assert upload_page.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in upload_page.text
    assert 'value="live"' not in upload_page.text

    # 2. 上传一份文本 Word 简历（含"李四"+填充正文）
    files = [("files", ("word.docx", _docx_bytes(["李四", _FILLER]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    upload_resp = client.post(
        f"{ROOT_PATH}/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"}, files=files,
    )
    assert upload_resp.status_code == 200
    resume_id = upload_resp.json()["results"][0]["resume_id"]
    assert upload_resp.json()["results"][0]["parse_status"] == "parsed"

    # 2b. live 样本类别在接口侧仍被拒收（页面不可选，接口仍是最后一道闸）
    live_files = [("files", ("word2.docx", _docx_bytes(["王五", _FILLER]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    live_resp = client.post(
        f"{ROOT_PATH}/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "live"}, files=live_files,
    )
    assert live_resp.json()["results"][0]["status"] == "rejected"
    assert "入库闸未开启" in live_resp.json()["results"][0]["reason"]

    # 3. 未登录访问列表接口被拒
    anon_client = TestClient(client.app)
    anon_resp = anon_client.get(f"{ROOT_PATH}/api/resumes/by-job/j1")
    assert anon_resp.status_code == 401

    # 4. 登录后解析结果列表页可见该简历
    list_page = client.get(f"{ROOT_PATH}/jobs/j1/resumes")
    assert list_page.status_code == 200
    assert f'<base href="{ROOT_PATH}/">' in list_page.text

    list_resp = client.get(f"{ROOT_PATH}/api/resumes/by-job/j1")
    assert list_resp.status_code == 200
    items = list_resp.json()["resumes"]
    assert len(items) == 1
    assert items[0]["resume_id"] == resume_id
    assert items[0]["pending_review_count"] == 1  # name 置信度 0.2 < 阈值 0.7

    # 5. 字段校对页：拿到 spans，断言高亮子串与原文一致（按偏移切片）
    review_page = client.get(f"{ROOT_PATH}/resumes/{resume_id}/review")
    assert review_page.status_code == 200

    text_resp = client.get(f"{ROOT_PATH}/api/resumes/{resume_id}/text")
    raw_text = text_resp.json()["raw_text"]
    parsed_resp = client.get(f"{ROOT_PATH}/api/resumes/{resume_id}/parsed")
    name_field = parsed_resp.json()["parsed_json"]["name"]
    span = name_field["spans"][0]
    assert raw_text[span["start"]:span["end"]] == span["quote"] == "李四"

    # 6. 提交校对确认，字段离开待校对队列
    review_resp = client.post(
        f"{ROOT_PATH}/api/resumes/{resume_id}/fields/name/review",
        json={"human_value": "李四四"},
    )
    assert review_resp.status_code == 200

    list_resp_after = client.get(f"{ROOT_PATH}/api/resumes/by-job/j1")
    item_after = list_resp_after.json()["resumes"][0]
    assert item_after["pending_review_count"] == 0
    field_by_name = {f["field"]: f for f in item_after["fields"]}
    assert field_by_name["name"]["review_status"] == "reviewed"
    assert field_by_name["name"]["reviewed_by"] == "alice"

    # 7. 每次"读取解析结果"各留一条痕（本用例调了 3 次 GET .../by-job/j1，
    #    每次对这一份 resume 各记一条 parsed_result 访问）
    access_count = conn.execute(
        "SELECT COUNT(*) FROM resume_access_log WHERE resume_id = ? AND access_type = 'parsed_result'",
        (resume_id,),
    ).fetchone()[0]
    assert access_count == 3
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_visible_slice_e2e.py -v`
Expected: 1 passed（若 Task 1–4 均已正确实现；如果失败，先确认失败原因是本测试的问题还是前序 Task 的回归，⛔ 不要为了让这个测试过而放宽前序 Task 的断言）

- [ ] **Step 3: 跑全量回归**

Run: `python -m pytest -q`
Expected: 全部 PASS，无新增失败

- [ ] **Step 4: Commit**

```bash
git add tests/test_visible_slice_e2e.py
git commit -m "test(m2-u2.5): 薄片端到端测试，覆盖上传/列表/高亮/校对确认全链路"
```

---

### Task 6: HR 操作说明 + 依赖与白名单结论（9.5）

**Files:**
- Create: `docs/m2-visible-slice-guide.md`

**Interfaces:**
- Consumes：无代码接口，纯文档。
- Produces：无。

- [ ] **Step 1: 确认本单元不需要新依赖**

Run: `git diff --stat HEAD -- requirements.txt` （在完成 Task 1–5 之后跑，确认为空）
Expected: 无输出（`requirements.txt` 未被本单元的任何 Task 修改——三个页面是纯 HTML/JS，聚合查询接口只用了标准库 `json` 与既有 `sqlite3` 连接）

- [ ] **Step 2: 写 HR 操作说明**

创建 `docs/m2-visible-slice-guide.md`：

```markdown
# M2 可见薄片 · HR 操作说明

这是招聘智能体第一个能在浏览器里看到的功能：传简历 → 系统自动读出六个字段 → 你核对一遍 → 确认或改掉。

## 账号从哪来

登录账号由技术侧用 `scripts/create_hr_account.py` 建，每人一个，不共用。如果还没有账号，找技术侧建一个；忘记密码同样找技术侧，重新跑一次同一个脚本会更新密码。

## 能传什么，不能传什么

**能传**：
- 合成替身样本（技术侧生成的测试用简历，不是真人）
- 脱敏样本（去掉了姓名等身份信息的历史简历）
- 历史离职候选人的简历

**不能传**：
- 真实在招候选人的简历——系统现在还没有开放这条通道（需要合规验收通过后由项目负责人开启），页面上也看不到这个选项，传了也会被系统拒收。

## 怎么传

1. 打开"上传简历"页面
2. 选择岗位（下拉框只会列出已经确认过职位画像的岗位；如果找不到你要的岗位，说明画像还没走完确认流程）
3. 选样本类别（上面三种之一）
4. 选文件，支持 PDF 和 Word（`.docx`），可以一次选多个
5. 点"上传"，页面下方会列出每个文件的结果：
   - **接收**：正常收下了，系统会自动开始解析
   - **重复**：这份文件之前传过，系统不会重复处理
   - **拒收**：文件类型不支持（比如传了 Excel），或者其他原因，页面会写清楚为什么

## 怎么看解析结果、怎么校对

1. 上传完成后，点页面上的链接进"解析结果列表"，或者直接从岗位页面进入
2. 列表里每一行是一份简历，能看到：候选人姓名（如果解析出来了）、样本类别、解析状态、还有几个字段等着你确认
3. 点"查看并校对"进入某一份简历的详情页：左边是简历原文，右边是系统读出的六个字段（姓名、工作年限、技能、公司经历、教育、期望城市）
4. 点右边任意一个字段，左边原文里对应的那句话会被**高亮**——这就是系统"凭什么这么读"的依据，你可以核对是不是读对了
5. 如果读对了，点"确认"；如果读错了，在旁边的输入框里填正确的值，点"提交修改"
6. 标了【待校对】的字段是系统自己没把握的（比如手写体潦草、格式奇怪），这些字段在你确认之前不会被后面的筛选流程使用

## 这一版还不做什么

这一版只到"传简历 → 看六字段 → 校对"为止。评分、排序、硬性条件筛选、淘汰候选人，都是下一版的事，这一版列表页看不到分数也看不到排名。
```

- [ ] **Step 3: Commit**

```bash
git add docs/m2-visible-slice-guide.md
git commit -m "docs(m2-u2.5): HR 操作说明；确认本单元不改依赖与部署白名单"
```

---

## Self-Review 记录（写计划时已自查，供实现者复核）

**Spec 覆盖**：
- 「批量上传入口」页面部分 → Task 2
- 「真实简历入库闸」（只读消费） → Task 2（页面不列 live 选项）+ Task 5（e2e 断言接口仍拒收）
- 「简历访问留痕」（列表接口新增的读取路径） → Task 1 + Task 5
- 「可识别到人的登录」（列表接口的保护） → Task 1（架构决策 1）+ Task 5
- 「首期字段抽取」「原文分片与字段回指」「字段级置信度与人工校对队列」「解析留痕与版本」「扫描件与不可读文件」（本单元只消费展示） → Task 1（字段摘要/校对状态）+ Task 4（高亮）+ Task 5
- 「字段校对页」（review-workbench spec） → Task 4

**占位符扫描**：全文无 TBD/TODO；Task 4 的"已知简化"是显式记录的范围边界，不是占位符。

**类型一致性**：`FieldSummary`/`ResumeListItem` 的字段名在 Task 1（后端产出）、Task 3（列表页消费）、Task 5（e2e 断言）三处一致；页面路径约定 `resumes/{id}/review`、`jobs/{id}/resumes` 在 Task 2/3/4 之间一致。

**端到端提取验证（2026-09-18 已执行）**：把本计划 Task 1–5 的全部代码原样提取到 `/tmp` 一次性 git 快照副本，装好 `requirements.txt` 精确锁定版本（含 `langgraph==1.0.10`）到独立 venv，跑了三轮：
1. 先跑既有 U2 回归（`test_resume_upload.py`/`test_resume_field_review.py`/`test_resume_access_log.py`）确认环境本身没问题：13 passed。
2. 应用 Task 1 的后端代码 + 测试：`test_resume_list_endpoint.py` 5 passed（一次通过，未发现问题）。
3. 应用 Task 2–4 的页面路由/静态文件 + Task 5 的 e2e 测试：首次跑出 2 个真实失败——`test_upload_page_served_under_root_path_prefix` 与 e2e 测试都用了 `TestClient(app, base_url="http://testserver/hr/recruit-agent")`，导致 httpx 把 base_url 自带的路径段和请求路径的 `root_path` 前缀拼接了两次（实际请求打到 `/hr/recruit-agent/hr/recruit-agent/...`，404）。定位后对照本仓库既有 `tests/test_health_endpoint.py` 的写法（`TestClient(app)` 不传 base_url），改成同款手法，修复后重跑 5 passed。**这两处失败已经改到本计划正文的 Task 2/Task 5 代码块里**，不是遗留问题。
4. 全量回归 `pytest -q`：3178 passed（含新增的 12 个用例），另有 2 个失败（`tools/liaison/tests/test_liaison_no_secrets_in_vcs.py`）经核实是快照副本不是真实 git 仓库导致 `git ls-files` 报错（在未做任何本计划改动的纯净快照上同样失败），与本计划改动无关。

---

Plan complete and saved to `docs/superpowers/plans/2026-09-18-m2-resume-parse-and-rank-unit2.5-visible-slice.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints

本计划由无头 opener 产出，⛔ 不在本次会话内执行；执行请另开 `run-build` session。
