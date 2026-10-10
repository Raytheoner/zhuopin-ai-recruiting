# 面试排期（interview-scheduling）· 交付单元 U3（邀约文案生成与回填）Implementation Plan

> 本计划由 `spec-to-plan`（Codex 版）产出。输入＝`openspec/changes/interview-scheduling/`
> 下的 `specs/interview-invitation-drafting/spec.md` + `design.md` 相关 Decisions
> ＋ **上游真身**（U1 已合 main 的两张表 `interview_invitation_draft` /
> `invitation_template`；U2 已落磁盘的存储层 `app/storage/interview_scheduling.py`）
> ＋ 同包 U1/U2 两份已落地计划；⛔ 未把 `tasks.md` 当计划输入，只用于确认 U3 章节
> 边界（`tasks.md` 第 3 章「邀约文案生成与回填」，条目 3.1–3.8）。

## 0. 输入与范围

- **变更包**：`openspec/changes/interview-scheduling/`
- **本单元（U3）范围**：`tasks.md` 第 3 章——模板 v1 占位与模板维护、生成纯函数、
  草稿版本递增落库、编辑与「标记为人工撰写」、回填接口、系统外发接线（经既有门禁）、
  邀约页、U3 e2e。
- **唯一 spec 输入**：`specs/interview-invitation-drafting/spec.md`
  （5 条 Requirement / 8 个 Scenario，逐条覆盖见 §3）。
- **design.md 相关 Decisions**：D5（生成纯函数 + 草稿版本化 + 系统外发接既有门禁，
  模板存表带版本、回件未到前用占位模板）、D9（候选人拒绝邀约**不是**淘汰）、
  D10（U3 前置＝U2）。
- **上游参照（磁盘真身，本单元的硬依赖）**：
  - `app/storage/db.py` 的 `interview_invitation_draft`（`slot_id, version,
    template_version, body, ai_generated, authorship_marked_by, authorship_marked_at,
    analysis_run_id, created_at`，`(slot_id, version)` 唯一）与 `invitation_template`
    （`version` 主键、`body`、`updated_by`、`updated_at`）——U1 Task 3 已落地，
    **逐字复用、⛔ 不改这两张表**；
  - `app/storage/interview_scheduling.py::slot_for()`（场次读模型）——U2 Task 3 已落地；
  - `app/agents/letter_drafter.py` / `app/graph/letter_nodes.py` /
    `app/storage/letter_template.py`（offer-generation U2）——**同构先例**：文书生成
    纯函数、版本化落库、编辑不去标识、显式标记人工撰写才去标识。本单元按同一形状写，
    ⛔ 不发明第二套做法。
- **本单元不做的**（U1/U2 已做、U4/U5 才做）：两张草稿/模板表与 `stage.interview`（U1）、
  冲突检查与四个排期 `effect_*` 节点与排期页（U2）、联系方式加密保管 `read_contact()`
  （U4）、合规断言进 CI 与 `.51` 发版（U5）。

**⚠️ 写计划时磁盘上的已知缺口（本计划按此假设写；上游落地后本计划逐字不变）**

1. **`app/storage/contact_vault.py` 尚无**（U4 未交付）⇒ 邀约外发的收件对象解析一律走
   **惰性 import + fail-closed**（模块不存在 ⇒ `None` ⇒ 门禁按「收件对象缺失或为空」
   拦截）。与 `app/storage/contact_source.py` 的既有做法同款。
2. **`app/graph/scheduling_nodes.py` 尚无**（U2 Task 4 未落地）⇒ U3 e2e 用夹具直接
   `INSERT INTO interview_slot` 造场次；U2 落地后可把该夹具换成真实
   `effect_schedule_slot`，本计划其它部分不变。
3. **`app/middleware/auth.py` 的 `PROTECTED_PATH_PREFIXES` 尚无 `/api/interview-slots`**
   （U2 Task 7a 未落地）⇒ Task 8 一并登记，并写明「若 U2 已登记则该条为重复项，跳过」。

## Global Constraints

> 从 `CLAUDE.md` 逐字复制与本单元相关的条目。reviewer 拿它当注意力透镜；缺了会静默漏查。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。

> 本单元落点：
>
> - 铁律 1：本单元有 **5 个** `effect_*` 节点（`effect_persist_draft` / `effect_edit_draft` /
>   `effect_mark_draft_human_written` / `effect_backfill_invitation_outcome` /
>   `effect_send_invitation`），每个独占、各带幂等键，由 `@idempotent_effect` 装饰器
>   与业务写同一事务提交（节点内 ⛔ 不 `conn.commit()`）。
> - 铁律 2：`app/agents/invitation_drafter.py::compute_invitation_draft` 是纯函数
>   （不 import storage/graph，测试用 AST 扫 import 反证）；`app/graph/invitation_nodes.py::
>   compute_invitation_draft_for_slot` 只读查库做输入装配。
> - 铁律 3、5：生成走 `LLMGateway.extract_structured_with_meta`（temperature 恒 0、
>   `prompt_version='invite-v1'`、输入哈希与原始响应由 `AuditHook` 落 `analysis_run`），
>   模型标识取**响应**的 `model`（`LLMCallMeta.response_model`），草稿行落
>   `analysis_run_id` 回指那一次留痕。

### 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **模型全部走境内**，简历数据不出境。

> 本单元落点：
>
> - 第一条：回填「候选人拒绝」**只改场次邀约状态**，⛔ 不写 `rejection_record`、⛔ 不动
>   `application.current_stage_id`（design D9；测试断言 `rejection_record` 行数与
>   `current_stage_id` 双不变）。`invitation_outcome_log` 表里**没有阶段列**，
>   「阶段不动」是结构性的，不靠代码自觉。
> - 第二条：正文一律由 `app/agents/jd_agent.enforce_ai_label` 贴唯一一行标识（与 M1 JD、
>   offer 文书**同串**，门禁 `AI_LABEL_PREFIX` 判定读的也是这一串）；编辑**不去标**；
>   唯一能去标的路径是显式「标记为人工撰写」且必须留痕（谁、何时、原 AI 版本）。
> - 第三条：生成只经 `LLMGateway`（`app/main.py` 装配的境内供应商），⛔ 不新增任何
>   直连外部模型的调用点。

### 部署约束（相关条目逐字）

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。

> 落点：邀约页 `invitation_review.html` 走 `_render_static_page` 的 `<!--BASE_HREF-->`
> 替换，所有 `fetch` 用相对路径（Task 9；`tests/test_interview_invitation_api.py`
> 断言 `root_path="/hr/recruit-agent"` 下页面与接口都可用）；U3 全部接口挂在
> `/api/invitation-templates` 与 `/api/interview-slots` 两个受保护前缀下（Task 8），
> 操作人一律取 `reviewer_of(request)`（登录账号名），⛔ 不接受客户端传入的操作人。

### 本单元硬约束（`interview-invitation-drafting` spec 原文）

- 文案 MUST 带 AI 生成标识；生成 MUST 留痕（模型标识取 API 响应字段、prompt 版本、
  `temperature=0`、输入哈希、原始响应）；**同一场次重复生成 MUST 产生新版本而不覆盖旧版本**。
- 对「已取消」场次生成邀约 ⇒ 请求被拒绝。
- 回填 MUST 记录操作人与时刻；候选人拒绝 MUST NOT 产生淘汰记录（投递保持当前阶段）。
- 编辑后 MUST 继续带标识；只有显式「标记为人工撰写」才去标识，且 MUST 留痕
  （谁、何时、原 AI 版本标识）。
- 系统外发（若提供）MUST 经既有候选人外发门禁：消息类型 `interview_invitation`、
  收件对象拍平为**非空字符串**、外发总开关默认关闭且每次求值；⛔ 存在绕过门禁的外发
  路径即违规。本能力默认交付形态是「HR 复制发送」，系统外发路径的开启是**不可代项**。
- 模板由 HR 维护、带版本号；留痕 MUST 记录所用模板版本；模板 MUST NOT 含候选人评分、
  排名或淘汰理由的占位符。回件（tasks 0.4）未到前用**占位模板**，回件到后只换内容不改代码。

### 本单元存储层硬约束（U1 真身 + 既有 schema 纪律）

- `interview_invitation_draft` / `invitation_template` 两表**已由 U1 落地**（`CREATE TABLE
  IF NOT EXISTS` + `(slot_id, version)` 唯一 + `version` 主键），本单元**零改动**，
  只在其上读写。
- 本单元新增**一张**表 `invitation_outcome_log`（回填的操作人/时刻/渠道/原因留痕载体，
  见 Task 3）：新表一律 `CREATE TABLE IF NOT EXISTS`，⛔ **不进 `_ADDED_COLUMNS`**
  （加列路径只服务「老库缺列」），⛔ **不进 `_DRIFT_GUARDED_TABLES`**（该守卫只覆盖
  「既可能由 SCHEMA 建、又可能早已存在于老库」的表；本表在老库上不存在）。
  ⇒ `tests/test_db_migration.py` 的既有断言（`_ADDED_COLUMNS` 表集合 = 8 张）**逐字不变**。
- 模板 v1 占位种子走 `init_schema()` 里的幂等 `INSERT OR IGNORE`（与
  `_seed_letter_templates` 同一先例、同一位置），⛔ 不在业务路径里"顺手建种子"。
  ⚠️ `tests/test_db_interview_invitation_draft_schema.py`（U1 的 schema 测试）里那条
  「模板表刚建好时为空」的用例若与本种子冲突，改测试而不是去掉种子——种子是 tasks 3.1
  的交付物（Task 1 命令会把这个文件的运行结果一并给出）。

## 1. File Structure（本单元新增/修改）

```
app/storage/invitation_template.py         # 新增：模板版本化读写 + 占位符白名单校验
app/storage/interview_invitation.py        # 新增：草稿版本/场次事实/回填留痕读取 + 收件对象解析
app/agents/invitation_drafter.py           # 新增：生成纯函数 compute_invitation_draft
app/graph/invitation_nodes.py              # 新增：1 个 compute_* + 5 个 effect_* 节点
app/storage/db.py                          # 修改：invitation_outcome_log 建表 + 模板 v1 占位种子
app/middleware/auth.py                     # 修改：PROTECTED_PATH_PREFIXES 加两个前缀
app/web/server.py                          # 修改：Pydantic 模型 + 模板/草稿/回填/外发/页面路由
app/web/static/invitation_review.html      # 新增：邀约页（生成/复制/编辑/标记人工/回填/外发）
tests/test_invitation_template.py          # 新增：模板版本化 + 占位符反证（Task 1）
tests/test_invitation_drafter.py           # 新增：生成纯函数（Task 2）
tests/test_db_u3_schema.py                 # 新增：invitation_outcome_log schema（Task 3）
tests/test_interview_invitation_storage.py # 新增：U3 存储层行为（Task 3）
tests/test_invitation_effect.py            # 新增：5 节点 + 门禁 Scenario（Task 10）
tests/test_interview_invitation_api.py     # 新增：路由契约 + 页面 + 子路径前缀（Task 11）
tests/test_interview_scheduling_u3_e2e.py  # 新增：U3 e2e（tasks 3.8，Task 12）
tests/test_db_interview_invitation_draft_schema.py  # 修改：4 处模板版本字面量（Task 1d，D-U3-6）
```

---

### Task 1: 邀约模板存储层 `app/storage/invitation_template.py` ＋ v1 占位种子

**文件**：`app/storage/invitation_template.py`（新增，整文件）、`app/storage/db.py`（修改）、
`tests/test_invitation_template.py`（新增，整文件）、
`tests/test_db_interview_invitation_draft_schema.py`（修改，见 1d）

**1a. `app/storage/invitation_template.py`（新增，整文件）**

```python
"""邀约文案模板的版本化读写与保存校验（interview-scheduling U3 tasks 3.1）。

版本化：每次 PUT 产生新版本（'v1' → 'v2' → …），绝不覆盖旧版（interview-invitation-
drafting spec「文案模板的来源与版本」）。保存校验：⛔ 候选人评分／排名／淘汰理由类
占位符一律禁（spec 逐字：「模板 MUST NOT 含候选人评分、排名或淘汰理由的占位符」）。

⛔ 校验用**白名单**不是黑名单：只放行 ALLOWED_INVITATION_PLACEHOLDERS 里的九个，
其余（拼写错误、未登记字段、任何评分／排名词）一律拒。黑名单枚举不完（"总分"、
"被淘汰"、"match_score" 这类写法总在往外冒），白名单漏不了。

禁词表的**真源**是 app/storage/letter_template.py 的 FORBIDDEN_COMMON_KEYWORDS
（offer-generation 已为「模板不得含评分／排名／硬门槛占位符」立了同一份词表）。本模块
import **同一个常量对象**，不另抄一份——两份词表迟早分叉，而分叉的失败是静默的
（某一边放宽后没有任何错误浮出来）。tests/test_invitation_template.py 用 `is` 钉住。

版本号是字符串标签 'v<数字>'（U1 真身 `invitation_template.version` 是 TEXT 主键）。
⚠️ 取最新版⛔ 不能用 `ORDER BY version DESC`——字典序下 'v10' < 'v2'，第十版会静默
退回第二版。一律按 `_version_number()` 解析出的整数比大小。
"""
from __future__ import annotations

import re
import sqlite3

from app.storage.letter_template import FORBIDDEN_COMMON_KEYWORDS

# 占位符语法：{name}。名字可含中文（{排名} 也要能被扫到并拒掉）。
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

# 邀约模板的占位符白名单（spec「按场次生成邀约文案」的内容清单：岗位、轮次、起止时刻、
# 形式（地址或会议链接）、面试官称谓、联系人；candidate_name 是收信人姓名，属普通事实
# 字段，与评分／排名无关，一并放行——见偏离登记 D-U3-1）。
ALLOWED_INVITATION_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "candidate_name",
        "job_title",
        "round",
        "start_at",
        "end_at",
        "mode",
        "location_or_link",
        "interviewer_names",
        "contact",
    }
)


class ForbiddenPlaceholderError(ValueError):
    """模板里出现被禁或未登记的占位符。message 带出具体占位符，供接口回给 HR。"""


def _reject_reason(name: str) -> str:
    low = name.lower()
    if any(k in low or k in name for k in FORBIDDEN_COMMON_KEYWORDS):
        return "评分/排名/淘汰理由占位符"
    return "未登记的占位符"


def validate_invitation_template_body(body: str) -> None:
    """保存校验：正文非空、占位符全部落在白名单内。"""
    if not body or not body.strip():
        raise ValueError("模板正文不能为空")
    offenders = []
    for token in _PLACEHOLDER_RE.findall(body):
        name = token.strip()
        if name in ALLOWED_INVITATION_PLACEHOLDERS:
            continue
        offenders.append(f"{name}（{_reject_reason(name)}）")
    if offenders:
        raise ForbiddenPlaceholderError(
            f"模板含被禁止的占位符: {sorted(set(offenders))}"
        )


def _version_number(label: str) -> int:
    if not label.startswith("v") or not label[1:].isdigit():
        raise ValueError(f"模板版本标签非法（应为 'v<数字>'）: {label!r}")
    return int(label[1:])


def _row_to_dict(row) -> dict:
    version, body, updated_by, updated_at = row
    return {
        "version": version,
        "body": body,
        "updated_by": updated_by,
        "updated_at": updated_at,
    }


def get_invitation_template(conn: sqlite3.Connection) -> dict | None:
    """返回最新版模板；从未写入返回 None。"""
    rows = conn.execute(
        "SELECT version, body, updated_by, updated_at FROM invitation_template"
    ).fetchall()
    if not rows:
        return None
    return _row_to_dict(max(rows, key=lambda r: _version_number(r[0])))


def put_invitation_template(
    conn: sqlite3.Connection, *, body: str, updated_by: str
) -> dict:
    """新增一个版本，绝不覆盖旧版；同内容重复 PUT 是幂等 no-op。"""
    validate_invitation_template_body(body)
    if not updated_by or not updated_by.strip():
        raise ValueError("updated_by 不能为空")

    latest = get_invitation_template(conn)
    if latest is not None and latest["body"] == body:
        return {**latest, "unchanged": True}

    next_number = (_version_number(latest["version"]) + 1) if latest else 1
    conn.execute(
        "INSERT INTO invitation_template (version, body, updated_by) VALUES (?, ?, ?)",
        (f"v{next_number}", body, updated_by),
    )
    conn.commit()
    result = get_invitation_template(conn)
    assert result is not None
    return {**result, "unchanged": False}
```

**1b. `app/storage/db.py`**——在 `_seed_letter_templates()` 之后插入常量与种子函数：

```python
_INVITATION_TEMPLATE_V1 = (
    "{candidate_name} 您好：\n\n"
    "诚邀您参加我司 {job_title} 岗位第 {round} 轮面试。\n"
    "时间：{start_at} — {end_at}\n"
    "形式：{mode}（{location_or_link}）\n"
    "面试官：{interviewer_names}\n"
    "联系人：{contact}\n\n"
    "如时间不便，请直接回复本消息，我们会与您另约。\n\n"
    "卓品智能人力资源部"
)


def _seed_invitation_template(conn: sqlite3.Connection) -> None:
    """幂等种子：邀约模板 v1 占位版（interview-scheduling U3 tasks 3.1；
    tasks 0.4「人事部#3 邀约话术样例」回件到后只换内容不改代码）。
    固定 version='v1' 天然键，重复调用不产生第二行。
    ⛔ 正文 MUST NOT 含评分／排名／淘汰理由占位符——本串只有九个白名单占位符，
    tests/test_invitation_template.py 逐条反证。"""
    conn.execute(
        "INSERT OR IGNORE INTO invitation_template (version, body, updated_by) "
        "VALUES ('v1', ?, 'system')",
        (_INVITATION_TEMPLATE_V1,),
    )
```

并在 `init_schema()` 尾部加一行调用（`conn.commit()` 之前）：

```python
    _seed_onboarding_default_template(conn)
    _seed_letter_templates(conn)
    _seed_invitation_template(conn)
    conn.commit()
```

**1d. `tests/test_db_interview_invitation_draft_schema.py`（修改：四个用例、共 6 处 `'v1'` 字面量）**——
U1 那份 schema 测试里有四处用例直接 `INSERT ... VALUES ('v1', …)`，模板 v1 占位种子落地后
会撞 `invitation_template.version` 主键。按「改测试、保种子」处置（种子是 tasks 3.1 的
交付物，见偏离登记 D-U3-6）：

- `test_invitation_template_version_is_primary_key`：两次插入的 `'v1'` → `'v2'`；
- `test_invitation_template_keeps_multiple_versions`：`'v1'`/`'v2'` → `'v2'`/`'v3'`，
  断言 `rows == ["v2", "v3"]`；
- `test_invitation_template_requires_updated_by`：插入的 `'v1'` → `'v2'`；
- `test_invitation_template_updated_at_defaults_to_now`：插入的 `'v1'` → `'v2'`，
  回读的 `WHERE version='v1'` → `WHERE version='v2'`。

⛔ 不改该文件里任何 `interview_invitation_draft`（草稿表）相关的用例。

**1c. `tests/test_invitation_template.py`（新增，整文件）**

```python
"""邀约模板版本化与占位符白名单（U3 tasks 3.1）。

反证两条 spec 硬约束：⛔ 模板不得含评分／排名／淘汰理由占位符；模板带版本号、
升版不覆盖旧版。"""
from __future__ import annotations

import pytest

from app.storage import letter_template as letter_template_module
from app.storage.db import get_connection, init_schema
from app.storage.invitation_template import (
    ALLOWED_INVITATION_PLACEHOLDERS,
    ForbiddenPlaceholderError,
    get_invitation_template,
    put_invitation_template,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "invitation_template.db"))
    init_schema(c)
    return c


def test_seed_v1_exists_after_init_schema(conn):
    template = get_invitation_template(conn)
    assert template["version"] == "v1"
    assert template["updated_by"] == "system"


def test_allowed_placeholder_set_is_nine(conn):
    assert ALLOWED_INVITATION_PLACEHOLDERS == {
        "candidate_name", "job_title", "round", "start_at", "end_at",
        "mode", "location_or_link", "interviewer_names", "contact",
    }


def test_forbidden_keywords_are_the_letter_template_constant():
    """禁词表是**同一个对象**，不是抄一份。两份词表分叉是静默故障。"""
    from app.storage import invitation_template as invitation_template_module

    assert (
        invitation_template_module.FORBIDDEN_COMMON_KEYWORDS
        is letter_template_module.FORBIDDEN_COMMON_KEYWORDS
    )


def test_put_creates_next_version_and_keeps_old(conn):
    put_invitation_template(conn, body="第一版 {job_title}", updated_by="hr-1")
    result = put_invitation_template(conn, body="第二版 {job_title}", updated_by="hr-1")
    assert result["version"] == "v2"
    assert result["unchanged"] is False
    versions = [
        row[0]
        for row in conn.execute("SELECT version FROM invitation_template ORDER BY version")
    ]
    assert versions == ["v1", "v2"]


def test_put_same_body_is_unchanged_noop(conn):
    first = put_invitation_template(conn, body="同一版 {job_title}", updated_by="hr-1")
    second = put_invitation_template(conn, body="同一版 {job_title}", updated_by="hr-1")
    assert second["version"] == first["version"]
    assert second["unchanged"] is True
    count = conn.execute("SELECT COUNT(*) FROM invitation_template").fetchone()[0]
    assert count == 2  # v1 占位种子 + 这一次


@pytest.mark.parametrize(
    "placeholder",
    ["{total_score}", "{排名}", "{reject_reason}", "{score}", "{建议录用}"],
)
def test_put_rejects_forbidden_placeholders(conn, placeholder):
    with pytest.raises(ForbiddenPlaceholderError):
        put_invitation_template(conn, body=f"您好 {placeholder}", updated_by="hr-1")


def test_put_rejects_unregistered_placeholder(conn):
    with pytest.raises(ForbiddenPlaceholderError):
        put_invitation_template(conn, body="您好 {interviewer_phone}", updated_by="hr-1")


def test_put_accepts_all_whitelisted_placeholders(conn):
    body = " ".join(f"{{{name}}}" for name in sorted(ALLOWED_INVITATION_PLACEHOLDERS))
    assert put_invitation_template(conn, body=body, updated_by="hr-1")["version"] == "v2"


def test_put_rejects_empty_body(conn):
    with pytest.raises(ValueError):
        put_invitation_template(conn, body="   ", updated_by="hr-1")


def test_put_requires_updated_by(conn):
    with pytest.raises(ValueError):
        put_invitation_template(conn, body="您好 {job_title}", updated_by="  ")


def test_put_writes_no_row_when_validation_fails(conn):
    before = conn.execute("SELECT COUNT(*) FROM invitation_template").fetchone()[0]
    with pytest.raises(ForbiddenPlaceholderError):
        put_invitation_template(conn, body="{rank}", updated_by="hr-1")
    after = conn.execute("SELECT COUNT(*) FROM invitation_template").fetchone()[0]
    assert after == before
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_invitation_template.py tests/test_db_interview_invitation_draft_schema.py -q
```

预期输出（末尾一行）：`30 passed in X.XXs`（新文件 14 个：9 个普通用例 +
`test_put_rejects_forbidden_placeholders` 的 5 个参数化实例；U1 的 schema 文件 16 个：
15 个用例 + `test_draft_accepts_boolean_ai_generated` 的 2 个参数化实例）。

---

### Task 2: 生成纯函数 `app/agents/invitation_drafter.py`

**文件**：`app/agents/invitation_drafter.py`（新增，整文件）、
`tests/test_invitation_drafter.py`（新增，整文件）

**2a. `app/agents/invitation_drafter.py`（新增，整文件）**

```python
"""邀约文案 L3 Agent（interview-scheduling U3 tasks 3.2）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库是
app/graph/invitation_nodes.py 的 effect_* 节点的事——本模块 ⛔ 不 import
app.storage / app.graph（tests/test_invitation_drafter.py 有 AST 静态断言守着）。

AI 生成标识与 M1 JD **同串**：复用 app/agents/jd_agent.enforce_ai_label
（AI_LABEL_TEMPLATE 是唯一真源）。门禁 app/outbound/gate.py 的 AI_LABEL_PREFIX 判定、
邀约页回读、以及"编辑不去标"三者因此读同一串，不会出现"文案有标识但门禁读不出来"的
错配（offer-generation U2 的 letter_drafter 同一做法）。

temperature 恒 0（LLMGateway.TEMPERATURE，铁律 5）；prompt_version='invite-v1'；
模型标识取 **API 响应**的 model 字段（LLMCallMeta.response_model，铁律 5）。
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.agents.jd_agent import enforce_ai_label
from app.llm.gateway import LLMGateway

INVITATION_PROMPT_VERSION = "invite-v1"

# 「联系人」缺省串（占位）。tasks 0.4 的人事部#3 回件（现用邀约话术样例）到后随模板 v1
# 内容一起替换；在那之前 ⛔ 不编造具体人名或电话。
DEFAULT_CONTACT_HINT = "卓品智能人力资源部"

_INVITATION_SYSTEM_PROMPT = (
    "你是招聘邀约文案助手。基于给定的邀约模板与场次事实，生成一份可直接发给候选人的"
    "面试邀约文案。\n"
    "规则：\n"
    "1. 模板里的占位符（{candidate_name}/{job_title}/{round}/{start_at}/{end_at}/"
    "{mode}/{location_or_link}/{interviewer_names}/{contact}）MUST 用给定字段填充，"
    "保持模板的段落结构与礼貌语气。\n"
    "2. MUST NOT 新增 facts 里没有的事实：不得编造地址、会议链接、薪资待遇、"
    "面试官的职级或任何未给出的安排；字段为空时保持留空。\n"
    "3. MUST NOT 输出任何评分、排名、硬门槛命中、淘汰理由，或『建议录用／建议淘汰』"
    "类文本——邀约文案是给候选人本人看的。\n"
    "4. 措辞礼貌简洁，不使用内部术语与流程缩写。\n"
    "输出 JSON，字段：body(string)。"
)


class InvitationSlotFacts(BaseModel):
    """生成邀约所需的场次事实。⛔ 字段里不得出现评分／排名／淘汰理由类键——
    这是「邀约文案不含评分信息」在生成输入侧的第一道防线（另两道是模板白名单与
    effect_* 只写文案表）。"""

    candidate_name: str
    job_title: str
    round: int
    start_at: str
    end_at: str
    mode: str
    location_or_link: str | None = None
    interviewer_names: list[str] = Field(default_factory=list)


class _InvitationBodySchema(BaseModel):
    body: str


@dataclass(frozen=True)
class InvitationDraft:
    body: str  # 已带 AI 生成标识
    run_id: str
    response_model: str | None
    prompt_version: str


def compute_invitation_draft(
    gateway: LLMGateway,
    *,
    facts: InvitationSlotFacts,
    template_body: str,
    template_version: str,
    contact_hint: str | None = None,
    audit_context: dict | None = None,
) -> InvitationDraft:
    """纯函数：按模板 + 场次事实生成一版邀约文案并带 AI 生成标识。

    ⚠️ user_prompt 末尾的「请求标识」nonce 是刻意的：temperature=0 下同一场次连续
    两次生成的 prompt 逐字相同，RecorderAuditHook 的确定性 id
    （{thread_id}:{node}:{input_hash}:{attempt}）会撞主键——第二次的 analysis_run 被
    SqliteSink 短路、run_id 与第一次相同，effect_persist_draft 随之被幂等短路 ⇒ 版本
    不递增，直接违反 spec「同一场次重复生成 MUST 产生新版本而不覆盖旧版本」。把 nonce
    写进实际发给模型的字节里，input_hash 每次不同、run_id 随之不同。
    （同款先例与实证：app/agents/letter_drafter.py::compute_letter_draft。）
    """
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    nonce = uuid.uuid4().hex
    contact = (contact_hint or "").strip() or DEFAULT_CONTACT_HINT
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=_INVITATION_SYSTEM_PROMPT,
        user_prompt=(
            f"模板版本={template_version}\n模板正文：\n{template_body}\n"
            f"场次事实：{facts.model_dump_json()}\n联系人：{contact}\n"
            f"请求标识：{nonce}"
        ),
        schema=_InvitationBodySchema,
        prompt_version=INVITATION_PROMPT_VERSION,
        audit_context=audit_context,
    )
    return InvitationDraft(
        body=enforce_ai_label(parsed.body, generated_at=generated_at),
        run_id=meta.run_id,
        response_model=meta.response_model,
        prompt_version=INVITATION_PROMPT_VERSION,
    )
```

**2b. `tests/test_invitation_drafter.py`（新增，整文件）**

```python
"""invitation_drafter 纯函数（U3 tasks 3.2）的单元测试。

用与 tests/test_letter_drafter.py 同款的 scripted client 打桩 LLM 网关
（⛔ 不联网、⛔ 不真调模型）。"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from app.agents.invitation_drafter import (
    DEFAULT_CONTACT_HINT,
    INVITATION_PROMPT_VERSION,
    _INVITATION_SYSTEM_PROMPT,
    InvitationSlotFacts,
    compute_invitation_draft,
)
from app.agents.jd_agent import AI_LABEL_PREFIX
from app.llm.gateway import LLMGateway


@dataclass
class _Msg:
    content: str


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Usage:
    prompt_tokens: int = 1
    completion_tokens: int = 1


@dataclass
class _Resp:
    choices: list
    model: str
    usage: object
    system_fingerprint: object = None


class _ScriptedCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(
            choices=[_Choice(message=_Msg(content=self._responses.pop(0)))],
            model="deepseek-chat-actual-v1",
            usage=_Usage(),
        )


class _ScriptedChat:
    def __init__(self, responses):
        self.completions = _ScriptedCompletions(responses)


class _ScriptedClient:
    def __init__(self, responses):
        self.chat = _ScriptedChat(responses)


def _gateway(scripted):
    return LLMGateway(
        api_key="k", base_url="https://example.invalid", model="deepseek-chat",
        supports_json_schema=True, client=scripted,
    )


def _facts() -> InvitationSlotFacts:
    return InvitationSlotFacts(
        candidate_name="张三", job_title="嵌入式软件工程师", round=1,
        start_at="2026-10-12 14:00", end_at="2026-10-12 15:00",
        mode="onsite", location_or_link="无锡市新吴区××路 1 号",
        interviewer_names=["汤丽萍"],
    )


def test_uses_json_schema_prompt_version_and_temperature_zero():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_invitation_draft(
        _gateway(scripted), facts=_facts(),
        template_body="{candidate_name} 您好", template_version="v1",
    )
    call = scripted.chat.completions.calls[0]
    assert call["temperature"] == 0
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert draft.prompt_version == INVITATION_PROMPT_VERSION == "invite-v1"


def test_response_model_taken_from_api_response():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_invitation_draft(
        _gateway(scripted), facts=_facts(),
        template_body="{candidate_name} 您好", template_version="v1",
    )
    assert draft.response_model == "deepseek-chat-actual-v1"


def test_body_carries_ai_label_same_as_jd():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_invitation_draft(
        _gateway(scripted), facts=_facts(),
        template_body="{candidate_name} 您好", template_version="v1",
    )
    assert AI_LABEL_PREFIX in draft.body


def test_consecutive_generations_have_distinct_run_ids():
    scripted = _ScriptedClient([json.dumps({"body": "您好"}), json.dumps({"body": "您好"})])
    gateway = _gateway(scripted)
    first = compute_invitation_draft(
        gateway, facts=_facts(), template_body="模板", template_version="v1"
    )
    second = compute_invitation_draft(
        gateway, facts=_facts(), template_body="模板", template_version="v1"
    )
    assert first.run_id != second.run_id


def test_contact_hint_falls_back_to_placeholder_contact():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    compute_invitation_draft(
        _gateway(scripted), facts=_facts(), template_body="模板", template_version="v1",
        contact_hint="   ",
    )
    user_prompt = scripted.chat.completions.calls[0]["messages"][-1]["content"]
    assert DEFAULT_CONTACT_HINT in user_prompt


def test_system_prompt_forbids_scores_ranks_and_rejection_reasons():
    assert "MUST NOT 输出任何评分、排名、硬门槛命中、淘汰理由" in _INVITATION_SYSTEM_PROMPT
    assert "不得编造" in _INVITATION_SYSTEM_PROMPT


def test_slot_facts_schema_has_no_score_fields():
    props = set(InvitationSlotFacts.model_json_schema()["properties"])
    forbidden = ("score", "rank", "reject", "评分", "排名", "淘汰")
    assert [p for p in props if any(k in p.lower() or k in p for k in forbidden)] == []


def _imported_module_names(source: str) -> set[str]:
    """扫真正的 import 语句（`ast.Import` 与 `ast.ImportFrom` 都覆盖）。
    ⛔ 不用子串扫描：本模块 docstring 里就写着「不 import app.storage」，
    子串版会被自己的注释绊倒。同款先例见 tests/test_letter_drafter.py。"""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_module_has_no_storage_write():
    src = Path("app/agents/invitation_drafter.py").read_text(encoding="utf-8")
    imported = _imported_module_names(src)
    assert not [m for m in imported if m == "app.storage" or m.startswith("app.storage.")]
    assert not [m for m in imported if m == "app.graph" or m.startswith("app.graph.")]
    assert "conn.execute" not in src
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_invitation_drafter.py -q
```

预期输出（末尾一行）：`9 passed in X.XXs`。

---

### Task 3: 邀约存储层 `app/storage/interview_invitation.py` ＋ 回填留痕表 `invitation_outcome_log`

**文件**：`app/storage/interview_invitation.py`（新增，整文件）、`app/storage/db.py`（修改）、
`tests/test_db_u3_schema.py`（新增，整文件）、
`tests/test_interview_invitation_storage.py`（新增，整文件）

**3a. `app/storage/db.py`**——在 `invitation_template` 的 `CREATE TABLE` 之后
（`candidate_contact` 建表之前）插入 DDL：

```sql
-- 邀约回填留痕（interview-invitation-drafting spec「HR 复制发送与结果回填」；
-- interview-scheduling U3 Task 3）。回填 MUST 记录操作人与时刻——本表就是这两件事
-- 的载体，同时把「同状态重复提交无第二条留痕」变成**结构性**保证：UNIQUE
-- (slot_id, status) 之下，同一场次同一状态在库里只可能有一行。
-- ⛔ 本表不写 rejection_record、不含应用阶段列：候选人拒绝邀约不是淘汰（design D9），
-- 「投递阶段不动」由"这里根本没有阶段列"保证，不靠代码自觉。
-- channel 只在 sent 行非空（微信/邮件/电话）；declined 行必须带非空 reason。
-- 新表，走 CREATE TABLE IF NOT EXISTS，⛔ 不进 _ADDED_COLUMNS、不进
-- _DRIFT_GUARDED_TABLES（老库上不存在本表）。
CREATE TABLE IF NOT EXISTS invitation_outcome_log (
    id TEXT PRIMARY KEY NOT NULL,
    slot_id TEXT NOT NULL REFERENCES interview_slot(id),
    status TEXT NOT NULL CHECK (
        status IN ('sent', 'confirmed', 'declined', 'reschedule_requested')
    ),
    channel TEXT CHECK (channel IS NULL OR channel IN ('wechat', 'email', 'phone')),
    reason TEXT,
    actor TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        (status = 'sent' AND channel IS NOT NULL)
        OR (status != 'sent' AND channel IS NULL)
    ),
    CHECK (status != 'declined' OR (reason IS NOT NULL AND trim(reason) != '')),
    UNIQUE (slot_id, status)
);

CREATE INDEX IF NOT EXISTS idx_invitation_outcome_slot
    ON invitation_outcome_log (slot_id);
```

**3b. `app/storage/interview_invitation.py`（新增，整文件）**

```python
"""邀约文案（U3）存储层：草稿版本号、场次事实装配、回填留痕读取、收件对象解析。

只做两件事：**只读**的输入装配 与 **纯计算**（下一版版本号、版本比较）。写入一律在
app/graph/invitation_nodes.py 的 effect_* 节点里——⛔ 本模块不做任何业务写，下面几个
异常类只是错误分类。

收件对象解析是**唯一**读 vault 的地方：U4（candidate-contact-vault）尚未交付，所以走
惰性 import + fail-closed（模块不存在 / 开关关闭 / 读取异常 ⇒ None ⇒ 门禁按「收件对象
缺失或为空」拦截）。与 app/storage/contact_source.py 的既有做法同款；⛔ 不在模块顶部
import vault，那会让 U3 整条 import 链在 U4 交付前直接 ImportError。
"""
from __future__ import annotations

import logging
import sqlite3

from app.agents.invitation_drafter import InvitationSlotFacts

logger = logging.getLogger(__name__)

# 已登记的回填状态（与 interview_slot.invitation_status 的 CHECK 取值对齐）。
OUTCOME_STATUSES: tuple[str, ...] = (
    "sent", "confirmed", "declined", "reschedule_requested",
)

# 人工发出渠道（spec「已发出（含渠道：微信／邮件／电话）」）。
SENT_CHANNELS: tuple[str, ...] = ("wechat", "email", "phone")


class InvitationSlotNotFoundError(ValueError):
    """slot_id 不存在。"""


class InvitationNotAllowedError(ValueError):
    """场次状态不允许当前动作（已取消的场次不可生成邀约／回填）。"""


class InvitationOutcomeAlreadyRecordedError(ValueError):
    """该 (slot_id, status) 已有回填记录（用**不同**幂等键重复提交）。

    2026-10-11 修正（Spec review F1）：回填节点命中「已有行」时必须抛本异常——
    ⛔ 不能返回成功形状：那是零业务写，而 `@idempotent_effect` 仍会写一行
    effect_log 并提交，「effect_log 条数 ↔ 业务表行数按 thread 恒等」当场被破坏。
    调用方（路由）捕获后把 `outcome` 原样返回即可（幂等成功响应）。
    """

    def __init__(self, message: str, *, outcome: dict):
        super().__init__(message)
        self.outcome = outcome


class InvitationTemplateMissingError(ValueError):
    """还没有任何邀约模板。"""


class InvitationDraftNotFoundError(ValueError):
    """draft_id 不存在。"""


def next_draft_version(conn: sqlite3.Connection, slot_id: str) -> int:
    """该场次的下一个草稿版本号（同场次内单调递增，⛔ 不覆盖旧版）。"""
    row = conn.execute(
        "SELECT MAX(version) FROM interview_invitation_draft WHERE slot_id = ?",
        (slot_id,),
    ).fetchone()
    return (row[0] or 0) + 1


_DRAFT_COLUMNS = (
    "id, slot_id, version, template_version, body, ai_generated, "
    "authorship_marked_by, authorship_marked_at, analysis_run_id, created_at"
)


def _draft_dict(row) -> dict:
    (
        draft_id, slot_id, version, template_version, body, ai_generated,
        authorship_marked_by, authorship_marked_at, analysis_run_id, created_at,
    ) = row
    return {
        "id": draft_id,
        "slot_id": slot_id,
        "version": version,
        "template_version": template_version,
        "body": body,
        "ai_generated": bool(ai_generated),
        "authorship_marked_by": authorship_marked_by,
        "authorship_marked_at": authorship_marked_at,
        "analysis_run_id": analysis_run_id,
        "created_at": created_at,
    }


def load_draft(conn: sqlite3.Connection, draft_id: str) -> dict:
    row = conn.execute(
        f"SELECT {_DRAFT_COLUMNS} FROM interview_invitation_draft WHERE id = ?",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise InvitationDraftNotFoundError(f"草稿不存在: {draft_id!r}")
    return _draft_dict(row)


def list_drafts(conn: sqlite3.Connection, slot_id: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_DRAFT_COLUMNS} FROM interview_invitation_draft "
        "WHERE slot_id = ? ORDER BY version",
        (slot_id,),
    ).fetchall()
    return [_draft_dict(r) for r in rows]


def latest_draft(conn: sqlite3.Connection, slot_id: str) -> dict | None:
    drafts = list_drafts(conn, slot_id)
    return drafts[-1] if drafts else None


def slot_facts(conn: sqlite3.Connection, slot_id: str) -> InvitationSlotFacts | None:
    """装配生成邀约所需的场次事实（候选人/岗位/轮次/时刻/形式/面试官称谓）。
    ⛔ 不读任何评分、排名、硬门槛——本函数的 SELECT 里就没有这些表。"""
    row = conn.execute(
        "SELECT c.name, j.title, s.round, s.start_at, s.end_at, s.mode, s.location_or_link "
        "FROM interview_slot s "
        "JOIN application a ON a.id = s.application_id "
        "JOIN candidate c ON c.id = a.candidate_id "
        "JOIN job j ON j.id = a.job_id "
        "WHERE s.id = ?",
        (slot_id,),
    ).fetchone()
    if row is None:
        return None
    candidate_name, job_title, round_, start_at, end_at, mode, location_or_link = row
    interviewer_names = [
        r[0]
        for r in conn.execute(
            "SELECT i.name FROM interview_slot_interviewer x "
            "JOIN interviewer i ON i.id = x.interviewer_id "
            "WHERE x.interview_slot_id = ? ORDER BY i.name COLLATE NOCASE, i.id",
            (slot_id,),
        ).fetchall()
    ]
    return InvitationSlotFacts(
        candidate_name=candidate_name,
        job_title=job_title,
        round=round_,
        start_at=start_at,
        end_at=end_at,
        mode=mode,
        location_or_link=location_or_link,
        interviewer_names=interviewer_names,
    )


def slot_application_id(conn: sqlite3.Connection, slot_id: str) -> str:
    row = conn.execute(
        "SELECT application_id FROM interview_slot WHERE id = ?", (slot_id,)
    ).fetchone()
    if row is None:
        raise InvitationSlotNotFoundError(f"场次不存在: {slot_id!r}")
    return row[0]


def slot_status(conn: sqlite3.Connection, slot_id: str) -> str:
    row = conn.execute(
        "SELECT status FROM interview_slot WHERE id = ?", (slot_id,)
    ).fetchone()
    if row is None:
        raise InvitationSlotNotFoundError(f"场次不存在: {slot_id!r}")
    return row[0]


def list_outcomes(conn: sqlite3.Connection, slot_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, slot_id, status, channel, reason, actor, at "
        "FROM invitation_outcome_log WHERE slot_id = ? ORDER BY at, id",
        (slot_id,),
    ).fetchall()
    return [
        {
            "id": r[0], "slot_id": r[1], "status": r[2], "channel": r[3],
            "reason": r[4], "actor": r[5], "at": r[6],
        }
        for r in rows
    ]


def candidate_recipient_for_invitation(
    conn: sqlite3.Connection, *, application_id: str
) -> str | None:
    """系统外发用的收件对象（拍平字符串）。vault 不可用一律 None——U4 开关默认关、
    模块可能还没交付，两种情况结论都是"收件对象未知 ⇒ 门禁拦"，⛔ 绝不回退到
    "用 application_id 拼一个假收件人"，那等于给门禁喂一个假地址。"""
    try:
        # 惰性 import：理由见模块 docstring（U4 交付前本模块必须能 import 成功）。
        from app.storage.contact_vault import (
            is_candidate_contact_vault_enabled,
            read_contact,
        )
    except ImportError:
        return None
    try:
        if not is_candidate_contact_vault_enabled():
            return None
        contact = read_contact(
            conn,
            application_id=application_id,
            accessor="system:interview-invitation-send",
            purpose="interview_invitation_outbound",
        )
    except Exception:  # noqa: BLE001 —— 未知即不可用（fail-closed）
        logger.exception(
            "application_id=%s 读取 candidate-contact-vault 失败，按不可用处理",
            application_id,
        )
        return None
    for attr in ("phone", "email"):
        value = getattr(contact, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return None
```

**3c. `tests/test_db_u3_schema.py`（新增，整文件）**

```python
"""U3 新增表 `invitation_outcome_log` 的 schema 回归（TDD 先写测试）。

反证三条结构性保证：`(slot_id, status)` 唯一；sent 必带渠道；declined 必带原因。
`rejection_record` 与阶段列**不在本表里**——候选人拒绝邀约不是淘汰（design D9）。"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "u3_schema.db"))
    init_schema(c)
    return c


def _seed_slot(conn, slot_id: str = "s1") -> str:
    conn.execute("INSERT OR IGNORE INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT OR IGNORE INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT OR IGNORE INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO application "
        "(id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES (?, 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')",
        (slot_id,),
    )
    conn.commit()
    return slot_id


def _insert(
    conn, *, outcome_id="o1", slot_id="s1", status="sent", channel="wechat",
    reason=None, actor="hr-1",
) -> None:
    conn.execute(
        "INSERT INTO invitation_outcome_log (id, slot_id, status, channel, reason, actor) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (outcome_id, slot_id, status, channel, reason, actor),
    )
    conn.commit()


def test_outcome_table_columns(conn):
    assert _columns(conn, "invitation_outcome_log") == {
        "id", "slot_id", "status", "channel", "reason", "actor", "at",
    }


def test_outcome_has_no_stage_or_rejection_columns(conn):
    """「候选人拒绝不产生淘汰记录、阶段不动」的结构保证：本表没有这些列，
    也没有指向 rejection_record 的任何引用。"""
    columns = _columns(conn, "invitation_outcome_log")
    assert "current_stage_id" not in columns
    assert "stage_id" not in columns
    assert "rejection_record_id" not in columns


def test_outcome_unique_per_slot_and_status(conn):
    _seed_slot(conn)
    _insert(conn, outcome_id="o1", status="sent", channel="wechat")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, outcome_id="o2", status="sent", channel="email")


def test_outcome_same_status_allowed_across_slots(conn):
    _seed_slot(conn, slot_id="s1")
    _seed_slot(conn, slot_id="s2")
    _insert(conn, outcome_id="o1", slot_id="s1", status="sent", channel="wechat")
    _insert(conn, outcome_id="o2", slot_id="s2", status="sent", channel="wechat")
    assert conn.execute("SELECT COUNT(*) FROM invitation_outcome_log").fetchone()[0] == 2


def test_outcome_sent_requires_channel(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="sent", channel=None)


def test_outcome_declined_requires_reason(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="declined", channel=None, reason=None)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="declined", channel=None, reason="   ")


def test_outcome_declined_with_reason_is_accepted(conn):
    _seed_slot(conn)
    _insert(conn, status="declined", channel=None, reason="已接受其他 offer")
    row = conn.execute(
        "SELECT status, channel, reason FROM invitation_outcome_log WHERE id='o1'"
    ).fetchone()
    assert row == ("declined", None, "已接受其他 offer")


def test_outcome_rejects_unknown_status(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="rejected", channel=None)


def test_outcome_rejects_unknown_channel(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="sent", channel="carrier_pigeon")


def test_outcome_rejects_channel_on_non_sent(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="confirmed", channel="wechat")


def test_outcome_slot_fk_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, slot_id="no-such-slot")


def test_outcome_actor_required(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO invitation_outcome_log (id, slot_id, status, channel, actor) "
            "VALUES ('o9', 's1', 'sent', 'wechat', NULL)"
        )


def test_outcome_at_defaults_to_now(conn):
    _seed_slot(conn)
    _insert(conn)
    at = conn.execute("SELECT at FROM invitation_outcome_log WHERE id='o1'").fetchone()[0]
    assert at and at != ""
```

**3d. `tests/test_interview_invitation_storage.py`（新增，整文件）**

```python
"""U3 存储层 `app/storage/interview_invitation.py` 的行为测试。"""
from __future__ import annotations

import sys
import types

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.interview_invitation import (
    candidate_recipient_for_invitation,
    latest_draft,
    list_drafts,
    list_outcomes,
    next_draft_version,
    slot_application_id,
    slot_facts,
    slot_status,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "invitation_storage.db"))
    init_schema(c)
    _seed(c)
    return c


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, location_or_link) "
        "VALUES ('s1', 'app1', 2, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite', "
        "'无锡市新吴区××路 1 号')"
    )
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('ha1', 'tangliping', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('iv1', 'ha1', '汤丽萍', '人事部', '[]', 1)"
    )
    conn.execute(
        "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
        "VALUES ('s1', 'iv1')"
    )
    conn.commit()


def _insert_draft(conn, *, draft_id: str, version: int) -> None:
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "VALUES (?, 's1', ?, 'v1', '您好，邀请您参加面试。', 1)",
        (draft_id, version),
    )
    conn.commit()


def test_next_draft_version_starts_at_one(conn):
    assert next_draft_version(conn, "s1") == 1


def test_next_draft_version_after_insert(conn):
    _insert_draft(conn, draft_id="d1", version=1)
    _insert_draft(conn, draft_id="d2", version=2)
    assert next_draft_version(conn, "s1") == 3


def test_latest_draft_is_highest_version(conn):
    _insert_draft(conn, draft_id="d1", version=1)
    _insert_draft(conn, draft_id="d2", version=2)
    assert latest_draft(conn, "s1")["id"] == "d2"
    assert latest_draft(conn, "no-such-slot") is None


def test_list_drafts_is_ordered_and_keeps_old_versions(conn):
    _insert_draft(conn, draft_id="d2", version=2)
    _insert_draft(conn, draft_id="d1", version=1)
    drafts = list_drafts(conn, "s1")
    assert [d["version"] for d in drafts] == [1, 2]
    assert [d["template_version"] for d in drafts] == ["v1", "v1"]
    assert all(d["ai_generated"] is True for d in drafts)


def test_slot_facts_loads_candidate_job_and_interviewer_names(conn):
    facts = slot_facts(conn, "s1")
    assert facts.candidate_name == "张三"
    assert facts.job_title == "嵌入式软件工程师"
    assert facts.round == 2
    assert facts.mode == "onsite"
    assert facts.location_or_link == "无锡市新吴区××路 1 号"
    assert facts.interviewer_names == ["汤丽萍"]


def test_slot_facts_none_for_unknown_slot(conn):
    assert slot_facts(conn, "nope") is None


def test_slot_helpers_and_empty_outcomes(conn):
    assert slot_application_id(conn, "s1") == "app1"
    assert slot_status(conn, "s1") == "scheduled"
    assert list_outcomes(conn, "s1") == []


def test_recipient_is_none_when_vault_module_missing(conn, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", None)
    assert candidate_recipient_for_invitation(conn, application_id="app1") is None


def test_recipient_is_none_when_vault_flag_off(conn, monkeypatch):
    module = types.ModuleType("app.storage.contact_vault")
    module.is_candidate_contact_vault_enabled = lambda: False
    module.read_contact = lambda *a, **k: pytest.fail("开关关闭时不得读取 vault")
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", module)
    assert candidate_recipient_for_invitation(conn, application_id="app1") is None


def test_recipient_reads_vault_when_enabled(conn, monkeypatch):
    module = types.ModuleType("app.storage.contact_vault")
    module.is_candidate_contact_vault_enabled = lambda: True

    class _Contact:
        phone = "13800000000"
        email = None

    module.read_contact = lambda *a, **k: _Contact()
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", module)
    assert candidate_recipient_for_invitation(conn, application_id="app1") == "13800000000"


def test_recipient_is_none_when_vault_read_raises(conn, monkeypatch):
    module = types.ModuleType("app.storage.contact_vault")
    module.is_candidate_contact_vault_enabled = lambda: True

    def _boom(*a, **k):
        raise RuntimeError("解密失败")

    module.read_contact = _boom
    monkeypatch.setitem(sys.modules, "app.storage.contact_vault", module)
    assert candidate_recipient_for_invitation(conn, application_id="app1") is None
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_db_u3_schema.py tests/test_interview_invitation_storage.py -q
```

预期输出（末尾一行）：`23 passed in X.XXs`（schema 12 + 存储层 11）。
另跑一次迁移守卫确认本单元**没有**动加列路径：

```bash
python3 -m pytest tests/test_db_migration.py tests/test_db_m2_schema.py tests/test_db_m2_u2_schema.py -q
```

预期输出：全绿（`_ADDED_COLUMNS` 表集合断言逐字不变）。

---

### Task 4: `compute_invitation_draft_for_slot` ＋ `effect_persist_draft`

**文件**：`app/graph/invitation_nodes.py`（新增，本 Task 建首段）

```python
"""邀约文案（U3）的 L4 编排层（interview-scheduling U3 tasks 3.2–3.6）。

`compute_invitation_draft_for_slot` 只读查库装配场次事实 + 模板，调 L3 纯函数生成
（工程铁律 2：compute_* 可以查库做输入组装）；写库全部在下面五个 effect_* 节点里，
每个独占、各带幂等键（铁律 1），由 `@idempotent_effect` 装饰器与业务写同事务提交——
⛔ 节点内一律不 `conn.commit()`。

thread_id 约定＝该场次所属 application_id（与 U2 `app/graph/scheduling_nodes.py` 同一
口径：一个投递一条 thread）。business_key 约定：

- persist = draft.run_id（同一次真实 LLM 调用只落一版草稿；再次生成是新 run_id ⇒ 版本递增）
- edit    = f"{draft_id}:{正文 sha256 前 16 位}"（同一次编辑重放短路，改了正文算新编辑）
- mark    = draft_id（一份草稿的「标记为人工撰写」是终态，至多一次）
- backfill= f"{slot_id}:{status}"（**同一场次**同状态重复提交无第二条留痕由这条键与
  `invitation_outcome_log` 的 UNIQUE(slot_id, status) 双保险；⚠️ 键必须带 slot_id，
  否则同一投递第二轮场次的同状态回填会被静默短路——2026-10-11 修正）
- send    = f"{draft_id}:{正文 sha256 前 16 位}"（同一份文案至多尝试外发一次；
  改了正文重走门禁。⛔ 真正防重复投递的是内容哈希键
  `effect_deliver_message` / `effect_record_outbound_audit` 各自的 business_key，
  本节点的键只保证"同一次尝试不重复执行"）
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid

from app.agents.invitation_drafter import InvitationDraft, compute_invitation_draft
from app.agents.jd_agent import (
    UNKNOWN_GENERATED_AT,
    enforce_ai_label,
    extract_label_generated_at,
    strip_ai_label,
)
from app.storage.idempotency import idempotent_effect
from app.storage.interview_invitation import (
    InvitationNotAllowedError,
    InvitationSlotNotFoundError,
    InvitationTemplateMissingError,
    load_draft,
    next_draft_version,
    slot_application_id,
    slot_facts,
    slot_status,
)
from app.storage.invitation_template import get_invitation_template


def _assert_slot_invitable(conn: sqlite3.Connection, slot_id: str) -> str:
    """场次可发邀约的前置校验（spec「对已取消场次生成被拒」）。

    ⚠️ 事务内**重做**一遍：compute 与 persist 之间场次可能被改期或取消，
    只在 compute 侧校验等于没校验（重放路径根本不走 compute）。
    """
    status = slot_status(conn, slot_id)  # 场次不存在即抛 InvitationSlotNotFoundError
    if status == "cancelled":
        raise InvitationNotAllowedError("已取消的场次不可生成或回填邀约")
    return status


def compute_invitation_draft_for_slot(
    conn: sqlite3.Connection,
    *,
    slot_id: str,
    gateway,
    contact_hint: str | None = None,
) -> tuple[InvitationDraft, str]:
    """L4 compute_* 节点：先过前置校验（fail-fast，⛔ 不为一个必定被拒的请求白烧一次
    LLM 调用），再读模板 + 场次事实，调 L3 纯函数。返回 (草稿, 生成时绑定的模板版本)。"""
    facts = slot_facts(conn, slot_id)
    if facts is None:
        raise InvitationSlotNotFoundError(f"场次不存在: {slot_id!r}")
    _assert_slot_invitable(conn, slot_id)
    template = get_invitation_template(conn)
    if template is None:
        raise InvitationTemplateMissingError("还没有邀约模板，请先在模板维护页创建")

    application_id = slot_application_id(conn, slot_id)
    job_id = conn.execute(
        "SELECT job_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    draft = compute_invitation_draft(
        gateway,
        facts=facts,
        template_body=template["body"],
        template_version=template["version"],
        contact_hint=contact_hint,
        audit_context={
            "thread_id": f"{application_id}:invitation",
            "node": "compute_invitation_draft",
            "application_id": application_id,
            "job_id": job_id,
        },
    )
    return draft, template["version"]


@idempotent_effect("effect_persist_draft")
def effect_persist_draft(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    template_version: str,
    draft: InvitationDraft,
) -> str:
    """effect_* 节点：把一版草稿落成 `interview_invitation_draft`（版本递增），
    独占、幂等。business_key = draft.run_id，幂等键 =
    {application_id}:effect_persist_draft:{analysis_run_id}——同一次真实 LLM 调用只落
    一版；再次生成（新 run_id）得到新版本，旧版永久保留（spec「同一场次重复生成 MUST
    产生新版本而不覆盖旧版本」）。

    ⚠️ 版本号在**本节点内部**算（`next_draft_version`），⛔ 不由调用方传入：版本号在
    路由里算、在事务里写，两者之间有窗口——同一场次并发两次生成会算出同一个版本号，
    撞 `(slot_id, version)` 唯一索引变成 500。放进事务里算，单连接串行化把它消掉。
    `invitation_status` 只在还是 'none' 时推进到 'drafted'：重新生成一份草稿⛔ 不得把
    已经回填过的 'sent'/'confirmed' 打回去。
    前置校验在事务内重做（见 `_assert_slot_invitable`）。⛔ 不在这里 conn.commit()。
    """
    _assert_slot_invitable(conn, slot_id)
    version = next_draft_version(conn, slot_id)
    draft_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated, analysis_run_id) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (draft_id, slot_id, version, template_version, draft.body, draft.run_id),
    )
    conn.execute(
        "UPDATE interview_slot SET invitation_status = 'drafted', updated_at = datetime('now') "
        "WHERE id = ? AND invitation_status = 'none'",
        (slot_id,),
    )
    return draft_id
```

**命令与预期输出**：

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('app/graph/invitation_nodes.py').read_text(encoding='utf-8')
tree = ast.parse(src)
names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef,))]
print(sorted(names))
"
```

预期输出：

```
['_assert_slot_invitable', 'compute_invitation_draft_for_slot', 'effect_persist_draft']
```

---

### Task 5: `effect_edit_draft` ＋ `effect_mark_draft_human_written`（tasks 3.4）

**文件**：`app/graph/invitation_nodes.py`（续写 Task 4 的同一文件）

```python
def draft_edit_business_key(draft_id: str, text: str) -> str:
    """编辑动作的 business_key：同一份草稿 + 同一段正文只算一次编辑。"""
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]
    return f"{draft_id}:{digest}"


@idempotent_effect("effect_edit_draft")
def effect_edit_draft(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    draft_id: str,
    edited_body: str,
) -> str:
    """effect_* 节点：把 HR 编辑后的正文写回（spec「人工改写后的标识处置」——
    **编辑不去标**）。

    标识保护与 app/graph/jd_nodes.py::effect_update_jd_text、letter_nodes.py::
    effect_edit_letter 同款：⛔ 不检查用户有没有删标识（检查就有绕过空间——改一个字、
    换个标点、插一行空白都能骗过检查），而是无条件把提交上来的文本当正文重新贴标识。
    唯一例外是已「标记为人工撰写」的草稿（ai_generated=0）：那份作者已经是人，只剥不贴。

    ⚠️ 重新贴的是**回读出来的原生成时间**（`extract_label_generated_at`），不是"现在"：
    标识记录的是"这份文案什么时候由 AI 生成"，编辑一次就把时间往后推会让标识从事实
    退化成噪声；读不出来才落 `UNKNOWN_GENERATED_AT` 占位，⛔ 不拿"现在"冒充。
    """
    draft = load_draft(conn, draft_id)
    if draft["ai_generated"]:
        generated_at = (
            extract_label_generated_at(draft["body"]) or UNKNOWN_GENERATED_AT
        )
        final_body = enforce_ai_label(edited_body, generated_at=generated_at)
    else:
        final_body = strip_ai_label(edited_body)
    conn.execute(
        "UPDATE interview_invitation_draft SET body = ? WHERE id = ?",
        (final_body, draft_id),
    )
    return final_body


@idempotent_effect("effect_mark_draft_human_written")
def effect_mark_draft_human_written(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    draft_id: str,
    reviewer: str,
    marked_at: str,
) -> str:
    """effect_* 节点：显式「标记为人工撰写」去标识 + 留痕（spec「人工改写后的标识
    处置」第二个 Scenario）。

    这是**唯一**能去掉邀约文案 AI 标识的路径。去标识与留痕在同一次 UPDATE 里落地：
    `body` 剥掉标识、`ai_generated=0`、`authorship_marked_by` / `authorship_marked_at`
    一起写，结构上不存在「标识没了但查不到谁去的」中间态。
    「原 AI 版本标识」＝**同一行自身的 `version`**（该草稿就是被标记的那一版 AI 稿，
    `analysis_run_id` 还指向产生它的那次模型调用），因此⛔ 不需要给表加列。
    ⛔ `reviewer` 不接受空白（决策人只能是人）。
    """
    if not str(reviewer).strip():
        raise ValueError(
            "标记为人工撰写必须记下是谁标的（合规红线：决策人只能是人）"
        )
    draft = load_draft(conn, draft_id)
    final_body = strip_ai_label(draft["body"])
    conn.execute(
        "UPDATE interview_invitation_draft SET body = ?, ai_generated = 0, "
        "authorship_marked_by = ?, authorship_marked_at = ? WHERE id = ?",
        (final_body, reviewer, marked_at, draft_id),
    )
    return final_body
```

**命令与预期输出**：

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('app/graph/invitation_nodes.py').read_text(encoding='utf-8')
tree = ast.parse(src)
print([n.name for n in tree.body if isinstance(n, ast.FunctionDef)])
"
```

预期输出：

```
['_assert_slot_invitable', 'compute_invitation_draft_for_slot', 'effect_persist_draft', 'draft_edit_business_key', 'effect_edit_draft', 'effect_mark_draft_human_written']
```

---

### Task 6: `effect_backfill_invitation_outcome`（tasks 3.5）

**文件**：`app/graph/invitation_nodes.py`（续写）

```python
def validate_outcome(*, status: str, channel: str | None, reason: str | None) -> None:
    """回填入参校验（接口层与节点层共用同一份判据，⛔ 不各写一套）。
    ⛔ 不做归一化（不 strip 渠道、不大写化状态）：未知取值即拒绝，猜作者的意图会让
    "微信" 与 "wechat" 变成两种渠道、报表当场分叉。"""
    if status not in OUTCOME_STATUSES:
        raise ValueError(f"未知的邀约回填状态: {status!r}")
    if status == "sent" and channel not in SENT_CHANNELS:
        raise ValueError(f"回填「已发出」必须带渠道，渠道取值限 {SENT_CHANNELS}")
    if status != "sent" and channel is not None:
        raise ValueError("只有「已发出」可以带渠道")
    if status == "declined" and not (reason or "").strip():
        raise ValueError("回填「候选人拒绝」必须填原因")


@idempotent_effect("effect_backfill_invitation_outcome")
def effect_backfill_invitation_outcome(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    status: str,
    actor: str,
    channel: str | None = None,
    reason: str | None = None,
) -> dict:
    """effect_* 节点：HR 回填外发结果（spec「HR 复制发送与结果回填」）。

    business_key = f"{slot_id}:{status}" ⇒ 幂等键 {application_id}:effect_backfill_
    invitation_outcome:{slot_id}:{status}——**同一场次**同状态重复提交被 effect_log
    短路、一条痕都不新增；⚠️ 键里必须带 slot_id（2026-10-11 修正）：只带 {status}
    会把同一投递**第二轮场次**的同状态回填也短路掉（无痕、场次状态不更新，接口却
    返回成功形状）。再加 `invitation_outcome_log` 的 UNIQUE(slot_id, status) 与
    "插入前先查已有行"作为第二、第三道保险（键与约束分属两层，任一层失效另一层仍挡得住）。

    ⛔ 本节点**不碰** `rejection_record`、⛔ 不碰 `application.current_stage_id`、⛔ 不写
    `application_stage_history`（后者会破坏 U2 `tests/test_interview_history_invariant.py`
    的「history 条数 = 排期动作次数」不变式）。候选人拒绝邀约不是淘汰（design D9）：
    投递停在哪一阶段由 HR 另行在 M2 复核工作台决定。

    操作人与时刻落在 `invitation_outcome_log.actor/at`（回填主记录）与
    `interview_slot.updated_by/updated_at`（场次当前值）两处。
    """
    if not str(actor).strip():
        raise ValueError("回填必须记下操作人")
    validate_outcome(status=status, channel=channel, reason=reason)
    _assert_slot_invitable(conn, slot_id)

    existing = conn.execute(
        "SELECT id, slot_id, status, channel, reason, actor, at "
        "FROM invitation_outcome_log WHERE slot_id = ? AND status = ?",
        (slot_id, status),
    ).fetchone()
    if existing is not None:
        # 2026-10-11 修正（Spec review F1）：命中已有行 = 零业务写；⛔ 不能返回
        # 成功形状——`@idempotent_effect` 会照写一行 effect_log 并提交，
        # 「effect_log 条数 ↔ 业务表行数按 thread 恒等」当场被破坏。抛领域异常，
        # 路由捕获后把 outcome 原样返回（幂等成功响应，无第二条留痕）。
        raise InvitationOutcomeAlreadyRecordedError(
            f"该场次该状态已回填：slot_id={slot_id} status={status}",
            outcome={
                "id": existing[0], "slot_id": existing[1], "status": existing[2],
                "channel": existing[3], "reason": existing[4], "actor": existing[5],
                "at": existing[6],
            },
        )
    conn.execute(
        "INSERT INTO invitation_outcome_log "
        "(id, slot_id, status, channel, reason, actor) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), slot_id, status, channel, reason, actor),
    )
    conn.execute(
        "UPDATE interview_slot SET invitation_status = ?, "
        "sent_channel = COALESCE(?, sent_channel), updated_by = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (status, channel if status == "sent" else None, actor, slot_id),
    )
    existing = conn.execute(
        "SELECT id, slot_id, status, channel, reason, actor, at "
        "FROM invitation_outcome_log WHERE slot_id = ? AND status = ?",
        (slot_id, status),
    ).fetchone()
    return {
        "id": existing[0], "slot_id": existing[1], "status": existing[2],
        "channel": existing[3], "reason": existing[4], "actor": existing[5],
        "at": existing[6],
    }
```

**命令与预期输出**：

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('app/graph/invitation_nodes.py').read_text(encoding='utf-8')
print([n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)][-2:])
"
```

预期输出：

```
['validate_outcome', 'effect_backfill_invitation_outcome']
```

---

### Task 7: `effect_send_invitation`（系统外发接线，经既有门禁；tasks 3.6）

**文件**：`app/graph/invitation_nodes.py`（续写）

在文件顶部 import 区补两行（放在 `from app.storage...` 之前，按模块分组）：

```python
from app.outbound.delivery import deliver_candidate_message
from app.outbound.gate import GateDecision
from app.outbound.messages import CandidateOutboundMessage
```

文件末尾追加：

```python
def draft_body_digest(body: str) -> str:
    return hashlib.sha256(str(body).encode("utf-8")).hexdigest()[:16]


@idempotent_effect("effect_send_invitation")
def effect_send_invitation(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    draft_id: str,
    recipient: str | None,
    channel,
    recorder,
    outbound_enabled,
    confirmed_by: str | None,
) -> GateDecision:
    """effect_* 节点：把一版草稿送进**既有候选人外发门禁**（spec「系统外发一律经既有
    门禁」；design D5）。

    ⛔ 唯一入口是 `app.outbound.delivery.deliver_candidate_message()`——本文件不出现
    任何 `channel.deliver(...)` 直连（tests/test_invitation_effect.py 有源码级反证），
    也不新增第二个外发路径。门禁的六条 fail-closed 判定原样生效：

    - 缺 AI 生成标识 ⇒ `REASON_MISSING_AI_LABEL` 拦截（spec 第二个 Scenario）；
    - 收件对象不是非空字符串 ⇒ `REASON_RECIPIENT_UNKNOWN` 拦截（U4 vault 关着时就是
      这条路：`candidate_recipient_for_invitation()` 返回 None，这里喂空串）；
    - 外发总开关关闭 ⇒ `REASON_OUTBOUND_DISABLED` 拦截并留痕（spec 第一个 Scenario）；
    - HR 签名（`confirmed_by`）与总开关**串联**：签了字开关关着照样不发。

    ⚠️ `recipient` 用 `recipient or ""` 收尾：`CandidateOutboundMessage.recipient` 声明
    是 `str`，把 None 塞进去是类型谎言；空串在门禁里与 None 同判（第 ⑤ 条要求
    "isinstance(str) and strip() 非空"），⛔ 不在这里替门禁做判断。

    ⚠️ 放行时把场次邀约状态推进到 `'sent'` + `sent_channel='system'`——只在当前状态属于
    `'none'/'drafted'/'sent'` 时推进，⛔ 不把已回填的 `confirmed`/`declined`/
    `reschedule_requested` 打回去。
    """
    draft = load_draft(conn, draft_id)
    _assert_slot_invitable(conn, slot_id)
    message = CandidateOutboundMessage(
        message_type="interview_invitation",
        recipient=recipient or "",
        body=draft["body"],
        confirmed_by=confirmed_by,
    )
    decision = deliver_candidate_message(
        conn,
        thread_id=thread_id,
        message=message,
        channel=channel,
        recorder=recorder,
        outbound_enabled=outbound_enabled,
    )
    if decision.allowed:
        conn.execute(
            "UPDATE interview_slot SET invitation_status = 'sent', "
            "sent_channel = 'system', updated_by = ?, updated_at = datetime('now') "
            "WHERE id = ? AND invitation_status IN ('none', 'drafted', 'sent')",
            (confirmed_by, slot_id),
        )
    return decision
```

并把 Task 3 存储层的三个常量与三个异常类补进 Task 4 的 import 区（它们已在
`app/storage/interview_invitation.py` 里定义，直接 import 即可）：

```python
from app.storage.interview_invitation import (
    OUTCOME_STATUSES,
    SENT_CHANNELS,
    InvitationDraftNotFoundError,
    InvitationNotAllowedError,
    InvitationSlotNotFoundError,
    InvitationTemplateMissingError,
    load_draft,
    slot_application_id,
    slot_facts,
    slot_status,
)
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_invitation_effect.py -q
```

预期输出（Task 10 落地后）：`26 passed in X.XXs`。
Task 7 单步先用「模块可导入 + 无直连通道调用」两条最小检查：

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('app/graph/invitation_nodes.py').read_text(encoding='utf-8')
print('channel.deliver' in src)
print([n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)])
"
```

预期输出：

```
False
['_assert_slot_invitable', 'compute_invitation_draft_for_slot', 'effect_persist_draft', 'draft_edit_business_key', 'effect_edit_draft', 'effect_mark_draft_human_written', 'validate_outcome', 'effect_backfill_invitation_outcome', 'draft_body_digest', 'effect_send_invitation']
```

---

### Task 8: 模板/草稿/回填/外发接口 ＋ 鉴权前缀（tasks 3.1、3.4–3.6）

**文件**：`app/middleware/auth.py`（修改）、`app/web/server.py`（修改）

**8a. `app/middleware/auth.py`**——`PROTECTED_PATH_PREFIXES` 加两个前缀：

```python
PROTECTED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/candidates",
    "/api/resumes",
    "/api/applications",
    "/api/rejections",
    "/api/interviewers",
    # interview-scheduling U2（其 Task 7a）与本单元 U3 共用：场次读模型与邀约动作的
    # 前缀。⚠️ 若 U2 已把它登记进来，这一行是重复项，**跳过即可**（元组里重复条目
    # 不影响判定，但没必要留）；`/api/invitation-templates` 是本单元新增的。
    "/api/interview-slots",
    "/api/invitation-templates",
)
```

**8b. `app/web/server.py`**——顶部 import 区追加：

```python
from app.graph.invitation_nodes import (
    compute_invitation_draft_for_slot,
    draft_body_digest,
    draft_edit_business_key,
    effect_backfill_invitation_outcome,
    effect_edit_draft,
    effect_mark_draft_human_written,
    effect_persist_draft,
    effect_send_invitation,
    validate_outcome,
)
from app.storage.interview_invitation import (
    InvitationDraftNotFoundError,
    InvitationNotAllowedError,
    InvitationSlotNotFoundError,
    InvitationTemplateMissingError,
    candidate_recipient_for_invitation,
    list_drafts,
    list_outcomes,
    slot_application_id,
)
from app.storage.interview_scheduling import SlotNotFoundError, slot_for
from app.storage.invitation_template import (
    ForbiddenPlaceholderError,
    get_invitation_template,
    put_invitation_template,
)
```

⚠️ `slot_for` 与 `SlotNotFoundError` 来自 U2 已落地的
`app/storage/interview_scheduling.py`（磁盘真身已核）；若该文件的导出名与此不符，
按磁盘真身调整 import 名，⛔ 不改本单元其它逻辑。

**8c. 模块级 Pydantic 模型**（放在既有请求模型区）：

```python
class InvitationTemplatePutRequest(BaseModel):
    body: str


class InvitationGenerateRequest(BaseModel):
    contact_hint: str | None = None


class InvitationEditRequest(BaseModel):
    body: str


class InvitationOutcomeRequest(BaseModel):
    status: str
    channel: str | None = None
    reason: str | None = None
```

**8d. 路由**（放在 `/api/interviewers` 那组路由之后）：

```python
    # ── interview-scheduling U3：邀约文案生成与回填 ──────────────────────
    # 全部端点要求 HR 角色（`_require_hr_role`）：邀约是 HR 的动作，面试官账号 403。
    # 操作人一律取 reviewer_of(request)（登录账号名）——⛔ 不接受请求体里传"操作人"，
    # 那等于让调用方自己声明自己是谁。

    def _latest_draft_or_none(slot_id: str) -> dict | None:
        drafts = list_drafts(conn, slot_id)
        return drafts[-1] if drafts else None

    def _invitation_payload(slot_id: str) -> dict:
        slot = slot_for(conn, slot_id)
        if slot is None:
            raise HTTPException(status_code=404, detail="场次不存在")
        return {
            "slot": slot,
            "template": get_invitation_template(conn),
            "drafts": list_drafts(conn, slot_id),
            "outcomes": list_outcomes(conn, slot_id),
        }

    @router.get("/api/invitation-templates")
    def invitation_template_detail(request: Request):
        _require_hr_role(request)
        return {"template": get_invitation_template(conn)}

    @router.put("/api/invitation-templates")
    def invitation_template_put(req: InvitationTemplatePutRequest, request: Request):
        _require_hr_role(request)
        try:
            return put_invitation_template(
                conn, body=req.body, updated_by=reviewer_of(request)
            )
        except (ForbiddenPlaceholderError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/interview-slots/{slot_id}/invitation")
    def invitation_page(slot_id: str):
        return _render_static_page("invitation_review.html", root_path)

    @router.get("/api/interview-slots/{slot_id}/invitation")
    def invitation_detail(slot_id: str, request: Request):
        _require_hr_role(request)
        return _invitation_payload(slot_id)

    @router.post("/api/interview-slots/{slot_id}/invitation/generate", status_code=201)
    def invitation_generate(
        slot_id: str, req: InvitationGenerateRequest, request: Request
    ):
        _require_hr_role(request)
        try:
            application_id = slot_application_id(conn, slot_id)
            draft, template_version = compute_invitation_draft_for_slot(
                conn, slot_id=slot_id, gateway=gateway, contact_hint=req.contact_hint
            )
            draft_id = effect_persist_draft(
                conn, thread_id=application_id, business_key=draft.run_id,
                slot_id=slot_id, template_version=template_version, draft=draft,
            )
        except InvitationSlotNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (InvitationNotAllowedError, InvitationTemplateMissingError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        payload = _invitation_payload(slot_id)
        payload["created_draft_id"] = draft_id
        return payload

    @router.post("/api/interview-slots/{slot_id}/invitation/drafts/{draft_id}/edit")
    def invitation_edit(
        slot_id: str, draft_id: str, req: InvitationEditRequest, request: Request
    ):
        _require_hr_role(request)
        try:
            application_id = slot_application_id(conn, slot_id)
            body = effect_edit_draft(
                conn, thread_id=application_id,
                business_key=draft_edit_business_key(draft_id, req.body),
                draft_id=draft_id, edited_body=req.body,
            )
        except (InvitationDraftNotFoundError, InvitationSlotNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"draft_id": draft_id, "body": body}

    @router.post(
        "/api/interview-slots/{slot_id}/invitation/drafts/{draft_id}/mark-human-written"
    )
    def invitation_mark_human_written(slot_id: str, draft_id: str, request: Request):
        _require_hr_role(request)
        try:
            application_id = slot_application_id(conn, slot_id)
            body = effect_mark_draft_human_written(
                conn, thread_id=application_id, business_key=draft_id,
                draft_id=draft_id, reviewer=reviewer_of(request),
                marked_at=sqlite_utc_now(),
            )
        except (InvitationDraftNotFoundError, InvitationSlotNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"draft_id": draft_id, "body": body}

    @router.post("/api/interview-slots/{slot_id}/invitation/outcome")
    def invitation_outcome(
        slot_id: str, req: InvitationOutcomeRequest, request: Request
    ):
        _require_hr_role(request)
        try:
            validate_outcome(status=req.status, channel=req.channel, reason=req.reason)
            application_id = slot_application_id(conn, slot_id)
            # 2026-10-11 修正（Spec review）：键必须带 slot_id——只带 status 会把
            # 同一投递第二轮场次的同状态回填静默短路（与 U2 既有键约定相抵）。
            outcome = effect_backfill_invitation_outcome(
                conn, thread_id=application_id, business_key=f"{slot_id}:{req.status}",
                slot_id=slot_id, status=req.status, actor=reviewer_of(request),
                channel=req.channel, reason=req.reason,
            )
            if outcome is None:
                # 幂等短路：同一状态重复提交时节点直接返回 None（不重复写留痕）。
                # 响应体仍要给页面一份可渲染的现状，⛔ 不把 None 直接塞给前端。
                outcome = next(
                    (
                        o for o in list_outcomes(conn, slot_id)
                        if o["status"] == req.status
                    ),
                    None,
                )
        except InvitationOutcomeAlreadyRecordedError as exc:
            # 2026-10-11 修正（Spec review F1 配套）：不同幂等键的重复回填 ⇒
            # 幂等成功（既有行原样返回）；⛔ 不能落进下面的宽 ValueError ⇒ 422。
            outcome = exc.outcome
        except InvitationSlotNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvitationNotAllowedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"outcome": outcome, **_invitation_payload(slot_id)}

    @router.post("/api/interview-slots/{slot_id}/invitation/send")
    def invitation_send(slot_id: str, request: Request):
        _require_hr_role(request)
        try:
            application_id = slot_application_id(conn, slot_id)
        except InvitationSlotNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        draft = _latest_draft_or_none(slot_id)
        if draft is None:
            raise HTTPException(status_code=409, detail="该场次还没有邀约草稿")
        decision = effect_send_invitation(
            conn, thread_id=application_id,
            business_key=f"{draft['id']}:{draft_body_digest(draft['body'])}",
            slot_id=slot_id, draft_id=draft["id"],
            recipient=candidate_recipient_for_invitation(
                conn, application_id=application_id
            ),
            channel=channel, recorder=recorder, outbound_enabled=outbound_enabled,
            confirmed_by=reviewer_of(request),
        )
        return {
            # ⚠️ None ⇒ 这一次 (草稿, 正文) 已尝试过外发、本节点被幂等短路：既不重复
            # 投递也不重复留痕（真正防重的是内容哈希键，见节点 docstring）。
            "delivery_mode": (
                "replay" if decision is None
                else ("delivered" if decision.allowed else "blocked")
            ),
            "blocked_reason": None if decision is None else decision.reason,
        }
```

**命令与预期输出**：

```bash
python3 -c "import app.web.server; print('app import ok')"
```

预期输出：`app import ok`

```bash
python3 -m pytest tests/test_auth_middleware.py tests/test_auth_routes.py -q
```

预期输出：全绿（新增两个受保护前缀不破坏既有鉴权用例）。

---

### Task 9: 邀约页 `app/web/static/invitation_review.html`（tasks 3.7）

**文件**：`app/web/static/invitation_review.html`（新增，整文件）

页面路由（`GET /interview-slots/{slot_id}/invitation`）已在 Task 8 落地；本 Task 只写
页面本体。页面 MUST NOT 展示评分／排名／硬门槛（与 U2 视图同一口径），全部请求走
`<base>` 下的**相对路径**（部署约束 1），AI 标识在正文里可见（就是正文自带的标识行）。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <title>面试邀约 · 卓品智能招聘</title>
  <link rel="stylesheet" href="static/app.css">
</head>
<body>
  <main>
    <h1>面试邀约</h1>
    <p id="slot-summary">加载中…</p>
    <p id="status-line"></p>

    <section>
      <h2>模板</h2>
      <p id="template-version"></p>
      <textarea id="template-body" rows="8" cols="80"></textarea>
      <button id="save-template">保存模板（升版）</button>
    </section>

    <section>
      <h2>文案</h2>
      <button id="generate">生成邀约文案</button>
      <ul id="draft-list"></ul>
      <pre id="draft-body"></pre>
      <button id="copy-draft">复制文案</button>
      <textarea id="edit-body" rows="8" cols="80"></textarea>
      <button id="save-edit">保存编辑</button>
      <button id="mark-human">标记为人工撰写</button>
    </section>

    <section>
      <h2>回填外发结果</h2>
      <select id="outcome-status">
        <option value="sent">已发出</option>
        <option value="confirmed">候选人已确认</option>
        <option value="declined">候选人拒绝</option>
        <option value="reschedule_requested">候选人请求改期</option>
      </select>
      <select id="outcome-channel">
        <option value="wechat">微信</option>
        <option value="email">邮件</option>
        <option value="phone">电话</option>
      </select>
      <input id="outcome-reason" placeholder="候选人拒绝时必填的原因">
      <button id="submit-outcome">回填</button>
      <button id="send-by-system">由系统发送（经外发门禁）</button>
      <ul id="outcome-list"></ul>
    </section>
  </main>

  <script>
    const slotId = location.pathname.split("/").filter(Boolean).pop();
    const api = `api/interview-slots/${slotId}/invitation`;
    let currentDraftId = null;

    function say(text) {
      document.getElementById("status-line").textContent = text || "";
    }

    async function load() {
      const res = await fetch(api);
      if (!res.ok) { say("加载失败"); return; }
      const data = await res.json();
      const slot = data.slot;
      document.getElementById("slot-summary").textContent =
        `投递 ${slot.application_id} · 第 ${slot.round} 轮 · ${slot.start_at} — ${slot.end_at}` +
        ` · 场次 ${slot.status} · 邀约 ${slot.invitation_status}`;
      if (data.template) {
        document.getElementById("template-version").textContent =
          `当前版本 ${data.template.version}（${data.template.updated_by}）`;
        document.getElementById("template-body").value = data.template.body;
      }
      const list = document.getElementById("draft-list");
      list.innerHTML = "";
      data.drafts.forEach((draft) => {
        const li = document.createElement("li");
        li.textContent =
          `v${draft.version} · 模板 ${draft.template_version}` +
          `${draft.ai_generated ? " · AI 生成" : " · 人工撰写"}` +
          `${draft.authorship_marked_by ? " · 去标人 " + draft.authorship_marked_by : ""}`;
        li.onclick = () => {
          currentDraftId = draft.id;
          document.getElementById("draft-body").textContent = draft.body;
          document.getElementById("edit-body").value = draft.body;
        };
        list.appendChild(li);
      });
      const outcomes = document.getElementById("outcome-list");
      outcomes.innerHTML = "";
      data.outcomes.forEach((outcome) => {
        const li = document.createElement("li");
        li.textContent =
          `${outcome.status}${outcome.channel ? "·" + outcome.channel : ""}` +
          `${outcome.reason ? "·" + outcome.reason : ""} · ${outcome.actor} · ${outcome.at}`;
        outcomes.appendChild(li);
      });
      const last = data.drafts[data.drafts.length - 1];
      if (last) {
        currentDraftId = last.id;
        document.getElementById("draft-body").textContent = last.body;
        document.getElementById("edit-body").value = last.body;
      }
    }

    async function post(url, body) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const payload = await res.json();
      if (!res.ok) { say(payload.detail || "操作失败"); return null; }
      return payload;
    }

    document.getElementById("save-template").onclick = async () => {
      const res = await fetch("api/invitation-templates", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: document.getElementById("template-body").value }),
      });
      const payload = await res.json();
      say(res.ok ? `模板已保存为 ${payload.version}` : payload.detail);
      if (res.ok) { await load(); }
    };

    document.getElementById("generate").onclick = async () => {
      const payload = await post(`${api}/generate`, {});
      if (payload) { say("已生成新版本"); await load(); }
    };

    document.getElementById("copy-draft").onclick = async () => {
      const text = document.getElementById("draft-body").textContent;
      try {
        await navigator.clipboard.writeText(text);
        say("已复制到剪贴板，请到微信／邮件里粘贴发送");
      } catch (err) {
        say("复制失败，请手动选中正文复制");
      }
    };

    document.getElementById("save-edit").onclick = async () => {
      if (!currentDraftId) { say("请先选择一版草稿"); return; }
      const payload = await post(`${api}/drafts/${currentDraftId}/edit`, {
        body: document.getElementById("edit-body").value,
      });
      if (payload) { say("已保存（AI 标识保留）"); await load(); }
    };

    document.getElementById("mark-human").onclick = async () => {
      if (!currentDraftId) { say("请先选择一版草稿"); return; }
      const payload = await post(
        `${api}/drafts/${currentDraftId}/mark-human-written`, {}
      );
      if (payload) { say("已标记为人工撰写（已留痕）"); await load(); }
    };

    document.getElementById("submit-outcome").onclick = async () => {
      const payload = await post(`${api}/outcome`, {
        status: document.getElementById("outcome-status").value,
        channel: document.getElementById("outcome-channel").value,
        reason: document.getElementById("outcome-reason").value || null,
      });
      if (payload) { say("已回填"); await load(); }
    };

    document.getElementById("send-by-system").onclick = async () => {
      const payload = await post(`${api}/send`, {});
      if (payload) {
        const text = payload.delivery_mode === "blocked"
          ? `外发被门禁拦截：${payload.blocked_reason}`
          : `外发结果：${payload.delivery_mode}`;
        say(text);
        await load();
      }
    };

    load();
  </script>
</body>
</html>
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_invitation_api.py -q
```

预期输出（Task 11 落地后）：`15 passed in X.XXs`。

---

### Task 10: `tests/test_invitation_effect.py`（五个节点 + 门禁两个 Scenario）

**文件**：`tests/test_invitation_effect.py`（新增，整文件）

```python
"""U3 五个 effect_* 节点 + 系统外发门禁的节点级测试。

覆盖三条最容易静默失效的约束：
- 版本递增不覆盖（spec「同一场次重复生成 MUST 产生新版本」）；
- 候选人拒绝**不产生淘汰记录、阶段不动**（design D9）；
- 外发一律经门禁：总开关关 / 缺 AI 标识 / 收件对象未知 三条拦截路径各留一条痕。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label, extract_label_generated_at
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.invitation_nodes import (
    draft_body_digest,
    draft_edit_business_key,
    effect_backfill_invitation_outcome,
    effect_edit_draft,
    effect_mark_draft_human_written,
    effect_persist_draft,
    effect_send_invitation,
)
from app.storage.db import get_connection, init_schema

GENERATED_AT = "2026-10-10T00:00:00+00:00"


class SpyChannel:
    def __init__(self):
        self.delivered = []

    def deliver(self, thread_id, message):
        self.delivered.append((thread_id, message))

    def latest(self, thread_id):
        return None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "invitation_effect.db"))
    init_schema(c)
    _seed(c)
    return c


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s1', 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')"
    )
    conn.commit()


def _draft(*, run_id: str = "run-u3-1", body: str | None = None) -> InvitationDraft:
    return InvitationDraft(
        body=body or enforce_ai_label("您好，邀请您参加面试。", generated_at=GENERATED_AT),
        run_id=run_id,
        response_model="deepseek-chat-actual-v1",
        prompt_version="invite-v1",
    )


def _persist(conn, *, draft: InvitationDraft | None = None, slot_id: str = "s1") -> str | None:
    d = draft or _draft()
    return effect_persist_draft(
        conn, thread_id="app1", business_key=d.run_id, slot_id=slot_id,
        template_version="v1", draft=d,
    )


def _draft_count(conn, slot_id: str = "s1") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM interview_invitation_draft WHERE slot_id = ?", (slot_id,)
    ).fetchone()[0]


def _effect_count(conn, node_name: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?", (node_name,)
    ).fetchone()[0]


def _slot(conn, slot_id: str = "s1") -> tuple:
    return conn.execute(
        "SELECT invitation_status, sent_channel, updated_by FROM interview_slot WHERE id = ?",
        (slot_id,),
    ).fetchone()


def _mirror_lines(chain_path: Path) -> list[dict]:
    text = chain_path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines()] if text else []


# ── effect_persist_draft ──────────────────────────────────────────────────


def test_persist_draft_inserts_version_one_and_sets_drafted(conn):
    draft_id = _persist(conn)
    row = conn.execute(
        "SELECT version, template_version, ai_generated, analysis_run_id "
        "FROM interview_invitation_draft WHERE id = ?",
        (draft_id,),
    ).fetchone()
    assert row == (1, "v1", 1, "run-u3-1")
    assert _slot(conn)[0] == "drafted"
    assert _effect_count(conn, "effect_persist_draft") == 1


def test_persist_draft_replay_returns_none_and_writes_no_second_row(conn):
    _persist(conn)
    assert _persist(conn) is None
    assert _draft_count(conn) == 1
    assert _effect_count(conn, "effect_persist_draft") == 1


def test_persist_draft_versions_increment_on_new_run_id(conn):
    _persist(conn, draft=_draft(run_id="run-1"))
    _persist(conn, draft=_draft(run_id="run-2"))
    versions = [
        r[0]
        for r in conn.execute(
            "SELECT version FROM interview_invitation_draft WHERE slot_id = 's1' "
            "ORDER BY version"
        )
    ]
    assert versions == [1, 2]


def test_persist_draft_rejects_cancelled_slot(conn):
    conn.execute("UPDATE interview_slot SET status = 'cancelled' WHERE id = 's1'")
    conn.commit()
    from app.storage.interview_invitation import InvitationNotAllowedError

    with pytest.raises(InvitationNotAllowedError):
        _persist(conn)
    assert _draft_count(conn) == 0


def test_persist_draft_does_not_downgrade_confirmed_status(conn):
    conn.execute(
        "UPDATE interview_slot SET invitation_status = 'confirmed' WHERE id = 's1'"
    )
    conn.commit()
    _persist(conn, draft=_draft(run_id="run-9"))
    assert _slot(conn)[0] == "confirmed"


# ── effect_edit_draft / effect_mark_draft_human_written ───────────────────


def test_edit_keeps_ai_label_and_original_generated_at(conn):
    draft_id = _persist(conn)
    body = effect_edit_draft(
        conn, thread_id="app1",
        business_key=draft_edit_business_key(draft_id, "改了两句话"),
        draft_id=draft_id, edited_body="改了两句话",
    )
    assert AI_LABEL_PREFIX in body
    assert extract_label_generated_at(body) == GENERATED_AT
    stored = conn.execute(
        "SELECT body FROM interview_invitation_draft WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    assert stored == body


def test_edit_reattaches_label_even_if_hr_stripped_it(conn):
    draft_id = _persist(conn)
    body = effect_edit_draft(
        conn, thread_id="app1",
        business_key=draft_edit_business_key(draft_id, "无标识正文"),
        draft_id=draft_id, edited_body="无标识正文",
    )
    assert body.count(AI_LABEL_PREFIX) == 1


def test_edit_business_key_is_stable_per_text(conn):
    draft_id = _persist(conn)
    key = draft_edit_business_key(draft_id, "同一段正文")
    effect_edit_draft(
        conn, thread_id="app1", business_key=key, draft_id=draft_id,
        edited_body="同一段正文",
    )
    assert (
        effect_edit_draft(
            conn, thread_id="app1", business_key=key, draft_id=draft_id,
            edited_body="同一段正文",
        )
        is None
    )
    assert _effect_count(conn, "effect_edit_draft") == 1


def test_mark_human_written_strips_label_and_records_who_and_when(conn):
    draft_id = _persist(conn)
    body = effect_mark_draft_human_written(
        conn, thread_id="app1", business_key=draft_id, draft_id=draft_id,
        reviewer="hr-1", marked_at="2026-10-10 09:00:00",
    )
    assert AI_LABEL_PREFIX not in body
    row = conn.execute(
        "SELECT version, ai_generated, authorship_marked_by, authorship_marked_at "
        "FROM interview_invitation_draft WHERE id = ?",
        (draft_id,),
    ).fetchone()
    assert row == (1, 0, "hr-1", "2026-10-10 09:00:00")


def test_mark_requires_reviewer(conn):
    draft_id = _persist(conn)
    with pytest.raises(ValueError):
        effect_mark_draft_human_written(
            conn, thread_id="app1", business_key=draft_id, draft_id=draft_id,
            reviewer="   ", marked_at="2026-10-10 09:00:00",
        )


def test_edit_after_mark_does_not_reattach_label(conn):
    draft_id = _persist(conn)
    effect_mark_draft_human_written(
        conn, thread_id="app1", business_key=draft_id, draft_id=draft_id,
        reviewer="hr-1", marked_at="2026-10-10 09:00:00",
    )
    body = effect_edit_draft(
        conn, thread_id="app1",
        business_key=draft_edit_business_key(draft_id, "人手写的内容"),
        draft_id=draft_id, edited_body="人手写的内容",
    )
    assert body == "人手写的内容"


# ── effect_backfill_invitation_outcome ────────────────────────────────────


def test_backfill_sent_sets_status_channel_actor_time(conn):
    outcome = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="sent", slot_id="s1", status="sent",
        actor="hr-1", channel="wechat",
    )
    assert outcome["status"] == "sent"
    assert outcome["channel"] == "wechat"
    assert outcome["actor"] == "hr-1"
    assert outcome["at"]
    assert _slot(conn) == ("sent", "wechat", "hr-1")


def test_backfill_declined_keeps_stage_and_writes_no_rejection_record(conn):
    before_stage = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0]
    effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="declined", slot_id="s1",
        status="declined", actor="hr-1", reason="已接受其他 offer",
    )
    after_stage = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0]
    rejections = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    assert before_stage == after_stage == "interview"
    assert rejections == 0
    assert _slot(conn)[0] == "declined"
    history = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id = 'app1'"
    ).fetchone()[0]
    assert history == 0  # ⛔ 回填不写流转事实（U2 的条数守恒不变式不能被破坏）


def test_backfill_same_status_twice_writes_one_row_and_one_effect_log(conn):
    first = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:sent", slot_id="s1", status="sent",
        actor="hr-1", channel="wechat",
    )
    second = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:sent", slot_id="s1", status="sent",
        actor="hr-1", channel="wechat",
    )
    assert first["id"]
    assert second is None
    count = conn.execute(
        "SELECT COUNT(*) FROM invitation_outcome_log WHERE slot_id = 's1'"
    ).fetchone()[0]
    assert count == 1
    assert _effect_count(conn, "effect_backfill_invitation_outcome") == 1


def test_backfill_duplicate_with_new_request_key_raises_without_orphan_effect_log(conn):
    """2026-10-11 修正（Spec review F1）：用不同幂等键重复回填同一 (slot,status)
    ⇒ 抛领域异常（⛔ 不是成功形状），且**不写孤儿 effect_log**（恒等式守恒）。"""
    first = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:sent", slot_id="s1", status="sent",
        actor="hr-1", channel="wechat",
    )
    assert first["id"]
    with pytest.raises(InvitationOutcomeAlreadyRecordedError) as excinfo:
        effect_backfill_invitation_outcome(
            conn, thread_id="app1", business_key="s1:sent:req-2", slot_id="s1",
            status="sent", actor="hr-1", channel="wechat",
        )
    assert excinfo.value.outcome["id"] == first["id"]
    assert _effect_count(conn, "effect_backfill_invitation_outcome") == 1


def test_backfill_same_status_across_slots_is_not_short_circuited(conn):
    """2026-10-11 修正（Spec review）：幂等键必须带 slot_id——同一投递第二轮
    场次的同状态回填不能被 {status} 相同的 effect_log 键静默短路。"""
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s2', 'app1', 2, '2026-10-20 06:00', '2026-10-20 07:00', 'onsite')"
    )
    conn.commit()
    first = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:confirmed", slot_id="s1",
        status="confirmed", actor="hr-1",
    )
    second = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s2:confirmed", slot_id="s2",
        status="confirmed", actor="hr-1",
    )
    assert first is not None and second is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM invitation_outcome_log WHERE slot_id = 's2'"
    ).fetchone()[0] == 1
    assert _effect_count(conn, "effect_backfill_invitation_outcome") == 2


def test_backfill_confirmed_after_sent_keeps_channel(conn):
    effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="sent", slot_id="s1", status="sent",
        actor="hr-1", channel="email",
    )
    effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="confirmed", slot_id="s1",
        status="confirmed", actor="hr-1",
    )
    assert _slot(conn)[:2] == ("confirmed", "email")
    count = conn.execute(
        "SELECT COUNT(*) FROM invitation_outcome_log WHERE slot_id = 's1'"
    ).fetchone()[0]
    assert count == 2


def test_backfill_sent_requires_channel(conn):
    with pytest.raises(ValueError):
        effect_backfill_invitation_outcome(
            conn, thread_id="app1", business_key="sent", slot_id="s1",
            status="sent", actor="hr-1",
        )


def test_backfill_declined_requires_reason(conn):
    with pytest.raises(ValueError):
        effect_backfill_invitation_outcome(
            conn, thread_id="app1", business_key="declined", slot_id="s1",
            status="declined", actor="hr-1",
        )


def test_backfill_rejects_unknown_status(conn):
    with pytest.raises(ValueError):
        effect_backfill_invitation_outcome(
            conn, thread_id="app1", business_key="rejected", slot_id="s1",
            status="rejected", actor="hr-1",
        )


def test_backfill_rejects_cancelled_slot(conn):
    from app.storage.interview_invitation import InvitationNotAllowedError

    conn.execute("UPDATE interview_slot SET status = 'cancelled' WHERE id = 's1'")
    conn.commit()
    with pytest.raises(InvitationNotAllowedError):
        effect_backfill_invitation_outcome(
            conn, thread_id="app1", business_key="sent", slot_id="s1",
            status="sent", actor="hr-1", channel="wechat",
        )


# ── effect_send_invitation（门禁接线） ───────────────────────────────────


@pytest.fixture
def wired(conn, tmp_path):
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return SpyChannel(), recorder, chain_path


def _send(conn, wired, *, recipient="13800000000", enabled=False, confirmed_by="hr-1"):
    channel, recorder, chain_path = wired
    draft = conn.execute(
        "SELECT id, body FROM interview_invitation_draft WHERE slot_id = 's1' "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    draft_id, body = draft
    decision = effect_send_invitation(
        conn, thread_id="app1",
        business_key=f"{draft_id}:{draft_body_digest(body)}",
        slot_id="s1", draft_id=draft_id, recipient=recipient, channel=channel,
        recorder=recorder, outbound_enabled=lambda: enabled, confirmed_by=confirmed_by,
    )
    return decision


def test_send_blocked_when_switch_off_and_audit_recorded(conn, wired):
    _persist(conn)
    decision = _send(conn, wired, enabled=False)
    channel, _recorder, chain_path = wired
    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert channel.delivered == []
    lines = _mirror_lines(chain_path)
    assert [line["event_type"] for line in lines] == ["outbound_blocked"]
    assert lines[0]["blocked_reason"] == "外发总开关关闭"
    assert _slot(conn)[0] == "drafted"  # ⛔ 拦截不动邀约状态


def test_send_blocked_when_ai_label_missing(conn, wired):
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "VALUES ('d-nolabel', 's1', 1, 'v1', '您好，邀请您参加面试。', 1)"
    )
    conn.commit()
    decision = _send(conn, wired, enabled=True)
    channel, _recorder, chain_path = wired
    assert decision.allowed is False
    assert decision.reason == "缺少 AI 生成标识"
    assert channel.delivered == []
    assert _mirror_lines(chain_path)[0]["blocked_reason"] == "缺少 AI 生成标识"


def test_send_blocked_when_recipient_unknown(conn, wired):
    _persist(conn)
    decision = _send(conn, wired, recipient=None, enabled=True)
    assert decision.allowed is False
    assert decision.reason == "收件对象缺失或为空"


def test_send_allowed_delivers_and_marks_sent(conn, wired):
    _persist(conn)
    decision = _send(conn, wired, enabled=True)
    channel, _recorder, chain_path = wired
    assert decision.allowed is True
    assert len(channel.delivered) == 1
    assert channel.delivered[0][1].type == "interview_invitation"
    assert _slot(conn) == ("sent", "system", "hr-1")
    assert _mirror_lines(chain_path)[0]["event_type"] == "outbound_delivered"


def test_send_replay_same_content_is_noop(conn, wired):
    _persist(conn)
    _send(conn, wired, enabled=True)
    assert _send(conn, wired, enabled=True) is None
    channel, _recorder, _chain_path = wired
    assert len(channel.delivered) == 1


def test_send_does_not_downgrade_confirmed_status(conn, wired):
    _persist(conn)
    effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="confirmed", slot_id="s1",
        status="confirmed", actor="hr-1",
    )
    _send(conn, wired, enabled=True)
    assert _slot(conn)[0] == "confirmed"


def test_send_module_has_no_direct_channel_deliver():
    src = Path("app/graph/invitation_nodes.py").read_text(encoding="utf-8")
    assert "channel.deliver" not in src
    assert "deliver_candidate_message(" in src
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_invitation_effect.py -q
```

预期输出（末尾一行）：`26 passed in X.XXs`。

---

### Task 11: `tests/test_interview_invitation_api.py`（路由契约 + 邀约页 + 子路径前缀）

**文件**：`tests/test_interview_invitation_api.py`（新增，整文件）

```python
"""U3 路由契约（tasks 3.1、3.4–3.7）。

生成阶段的 LLM 调用在**路由边界**打桩（monkeypatch `app.web.server.
compute_invitation_draft_for_slot`）——节点级行为由 tests/test_invitation_effect.py
与 tests/test_invitation_drafter.py 覆盖，本文件只验接口契约、权限、幂等与页面。
"""
from __future__ import annotations

import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label
from app.middleware.auth import SESSION_COOKIE_NAME
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.web import server as server_module
from app.web.server import create_app

GENERATED_AT = "2026-10-10T00:00:00+00:00"


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s1', 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode, status) "
        "VALUES ('s2', 'app1', 1, '2026-10-13 14:00', '2026-10-13 15:00', 'onsite', 'cancelled')"
    )
    conn.commit()


def _login(conn, client, *, role: str = "hr") -> None:
    account_id = upsert_account(conn, username="tester", password="testpass123")
    conn.execute("UPDATE hr_account SET role = ? WHERE id = ?", (role, account_id))
    conn.commit()
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)


@pytest.fixture
def client(make_test_client, monkeypatch):
    c, conn = make_test_client()
    _seed(conn)
    _login(conn, c)
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot", _fake_compute()
    )
    return c, conn


def _fake_compute(run_ids=("run-u3-1",)):
    """路由边界的生成打桩：按顺序返回不同的 run_id（版本递增用例需要两个）。"""
    remaining = list(run_ids)

    def _compute(conn, *, slot_id, gateway, contact_hint=None):
        run_id = remaining.pop(0) if remaining else run_ids[-1]
        return (
            InvitationDraft(
                body=enforce_ai_label("您好，邀请您参加面试。", generated_at=GENERATED_AT),
                run_id=run_id,
                response_model="deepseek-chat-actual-v1",
                prompt_version="invite-v1",
            ),
            "v1",
        )

    return _compute


def _generate(client, slot_id: str = "s1"):
    return client.post(f"/api/interview-slots/{slot_id}/invitation/generate", json={})


def _draft_id(client, slot_id: str = "s1") -> str:
    return client.get(f"/api/interview-slots/{slot_id}/invitation").json()["drafts"][-1]["id"]


# ── 模板接口 ──────────────────────────────────────────────────────────────


def test_put_template_requires_login(make_test_client):
    c, conn = make_test_client()
    _seed(conn)
    res = c.put("/api/invitation-templates", json={"body": "您好 {job_title}"})
    assert res.status_code == 401


def test_put_template_requires_hr_role(make_test_client):
    c, conn = make_test_client()
    _seed(conn)
    _login(conn, c, role="interviewer")
    res = c.put("/api/invitation-templates", json={"body": "您好 {job_title}"})
    assert res.status_code == 403


def test_put_template_bumps_version(client):
    c, _conn = client
    first = c.put("/api/invitation-templates", json={"body": "第一版 {job_title}"})
    second = c.put("/api/invitation-templates", json={"body": "第二版 {job_title}"})
    assert first.status_code == 200 and first.json()["version"] == "v2"
    assert second.status_code == 200 and second.json()["version"] == "v3"
    assert second.json()["updated_by"] == "tester"


def test_put_template_rejects_forbidden_placeholder(client):
    c, _conn = client
    res = c.put("/api/invitation-templates", json={"body": "您好 {total_score}"})
    assert res.status_code == 422
    assert "评分" in res.json()["detail"] or "未登记" in res.json()["detail"]


# ── 详情与生成 ────────────────────────────────────────────────────────────


def test_detail_returns_seeded_template_and_empty_drafts(client):
    c, _conn = client
    body = c.get("/api/interview-slots/s1/invitation").json()
    assert body["slot"]["slot_id"] == "s1"
    assert body["slot"]["application_id"] == "app1"
    assert body["template"]["version"] == "v1"
    assert body["drafts"] == []
    assert body["outcomes"] == []


def test_generate_creates_version_one_with_ai_label(client):
    c, conn = client
    res = _generate(c)
    assert res.status_code == 201
    payload = res.json()
    assert len(payload["drafts"]) == 1
    assert payload["drafts"][0]["version"] == 1
    assert payload["drafts"][0]["ai_generated"] is True
    assert AI_LABEL_PREFIX in payload["drafts"][0]["body"]
    assert payload["slot"]["invitation_status"] == "drafted"


def test_generate_twice_appends_new_version_and_keeps_old(client, monkeypatch):
    c, _conn = client
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot", _fake_compute(("run-1", "run-2"))
    )
    _generate(c)
    res = _generate(c)
    versions = [d["version"] for d in res.json()["drafts"]]
    assert versions == [1, 2]


def test_generate_rejects_cancelled_slot(client):
    c, _conn = client
    res = _generate(c, slot_id="s2")
    assert res.status_code == 409


# ── 编辑 / 标记人工撰写 ───────────────────────────────────────────────────


def test_edit_endpoint_keeps_ai_label(client):
    c, _conn = client
    _generate(c)
    draft_id = _draft_id(c)
    res = c.post(
        f"/api/interview-slots/s1/invitation/drafts/{draft_id}/edit",
        json={"body": "改了两句话"},
    )
    assert res.status_code == 200
    assert AI_LABEL_PREFIX in res.json()["body"]


def test_mark_human_written_endpoint_strips_label_and_records_actor(client):
    c, _conn = client
    _generate(c)
    draft_id = _draft_id(c)
    res = c.post(
        f"/api/interview-slots/s1/invitation/drafts/{draft_id}/mark-human-written"
    )
    assert res.status_code == 200
    assert AI_LABEL_PREFIX not in res.json()["body"]
    stored = c.get("/api/interview-slots/s1/invitation").json()["drafts"][0]
    assert stored["ai_generated"] is False
    assert stored["authorship_marked_by"] == "tester"
    assert stored["authorship_marked_at"]


# ── 回填 ──────────────────────────────────────────────────────────────────


def test_outcome_sent_sets_status_and_channel(client):
    c, _conn = client
    res = c.post(
        "/api/interview-slots/s1/invitation/outcome",
        json={"status": "sent", "channel": "wechat", "reason": None},
    )
    assert res.status_code == 200
    assert res.json()["slot"]["invitation_status"] == "sent"
    assert res.json()["slot"]["sent_channel"] == "wechat"
    assert res.json()["outcome"]["actor"] == "tester"


def test_outcome_declined_keeps_stage_and_creates_no_rejection_record(client):
    c, conn = client
    res = c.post(
        "/api/interview-slots/s1/invitation/outcome",
        json={"status": "declined", "channel": None, "reason": "已接受其他 offer"},
    )
    assert res.status_code == 200
    assert res.json()["slot"]["invitation_status"] == "declined"
    stage = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0]
    assert stage == "interview"
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == 0


def test_outcome_sent_without_channel_is_422(client):
    c, _conn = client
    res = c.post(
        "/api/interview-slots/s1/invitation/outcome",
        json={"status": "sent", "channel": None, "reason": None},
    )
    assert res.status_code == 422


# ── 系统外发（默认不可用） ────────────────────────────────────────────────


def test_send_endpoint_blocked_and_reports_reason(client):
    """默认环境下：外发总开关关 + vault 未开启（收件对象未知）⇒ 一律拦截，
    响应体把门禁给的原因原样带回页面。"""
    c, _conn = client
    _generate(c)
    res = c.post("/api/interview-slots/s1/invitation/send")
    assert res.status_code == 200
    assert res.json()["delivery_mode"] == "blocked"
    assert res.json()["blocked_reason"]


# ── 页面与子路径前缀 ──────────────────────────────────────────────────────


def test_invitation_page_works_under_subpath(tmp_path):
    db_path = str(tmp_path / "subpath.db")
    resume_dir = str(tmp_path / "resumes")

    def _gateway_factory():
        from app.llm.gateway import LLMGateway

        return LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        )

    app = create_app(
        db_path=db_path, gateway_factory=_gateway_factory,
        root_path="/hr/recruit-agent", resume_storage_dir=resume_dir,
    )
    from app.storage.db import get_connection
    from fastapi.testclient import TestClient

    _seed(get_connection(db_path))
    with TestClient(app) as c:
        res = c.get("/hr/recruit-agent/interview-slots/s1/invitation")
        assert res.status_code == 200
        assert '<base href="/hr/recruit-agent/">' in res.text
        assert "api/interview-slots/" in res.text  # 相对路径，⛔ 无硬编码 /api/…
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_invitation_api.py -q
```

预期输出（末尾一行）：`15 passed in X.XXs`。

---

### Task 12: `tests/test_interview_scheduling_u3_e2e.py`（U3 e2e，tasks 3.8）

**文件**：`tests/test_interview_scheduling_u3_e2e.py`（新增，整文件）

```python
"""U3 e2e（tasks 3.8）：安排 → 生成 → 复制 → 回填已发出 → 回填确认；
另跑一条「候选人拒绝」路径，断言投递阶段不变、淘汰记录不新增。

⚠️ 场次由夹具直接 `INSERT INTO interview_slot` 造（U1 建表、U2 的
`effect_schedule_slot` 尚未落地）。U2 落地后可把 `_seed_slot` 换成对排期接口的调用，
本文件其余部分逐字不变。

全程走 HTTP（相对路径与登录 cookie 与生产一致），⛔ 不直接调节点函数。
"""
from __future__ import annotations

import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label
from app.middleware.auth import SESSION_COOKIE_NAME
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.web import server as server_module


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    # 「安排」这一步：U2 的 effect_schedule_slot 落地前由夹具直插（见模块 docstring）。
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, location_or_link, created_by) "
        "VALUES ('slot-1', 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite', "
        "'无锡市新吴区××路 1 号', 'tester')"
    )
    conn.commit()


def _login(conn, client) -> None:
    account_id = upsert_account(conn, username="tester", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)


def _fake_compute(run_ids=("run-e2e-1",)):
    remaining = list(run_ids)

    def _compute(conn, *, slot_id, gateway, contact_hint=None):
        run_id = remaining.pop(0) if remaining else run_ids[-1]
        return (
            InvitationDraft(
                body=enforce_ai_label(
                    "张三您好：诚邀您参加面试。",
                    generated_at="2026-10-10T00:00:00+00:00",
                ),
                run_id=run_id,
                response_model="deepseek-chat-actual-v1",
                prompt_version="invite-v1",
            ),
            "v1",
        )

    return _compute


@pytest.fixture
def client(make_test_client, monkeypatch):
    c, conn = make_test_client()
    _seed(conn)
    _login(conn, c)
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot", _fake_compute()
    )
    return c, conn


def test_e2e_generate_copy_backfill_sent_then_confirmed(client):
    c, _conn = client
    generated = c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    assert generated.status_code == 201
    draft = generated.json()["drafts"][-1]
    assert draft["version"] == 1

    # 「复制」＝页面把正文交给剪贴板；后端能给出的就是这份正文本身，
    # 断言它带 AI 标识（合规红线：AI 生成的邀约须带标识）。
    detail = c.get("/api/interview-slots/slot-1/invitation").json()
    copied_body = detail["drafts"][-1]["body"]
    assert AI_LABEL_PREFIX in copied_body

    sent = c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "sent", "channel": "wechat", "reason": None},
    )
    assert sent.status_code == 200
    assert sent.json()["slot"]["invitation_status"] == "sent"
    assert sent.json()["slot"]["sent_channel"] == "wechat"

    confirmed = c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "confirmed", "channel": None, "reason": None},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["slot"]["invitation_status"] == "confirmed"
    # 渠道在 confirmed 之后仍然记得（COALESCE 不把 sent_channel 抹掉）。
    assert confirmed.json()["slot"]["sent_channel"] == "wechat"
    assert [o["status"] for o in confirmed.json()["outcomes"]] == ["sent", "confirmed"]


def test_e2e_declined_keeps_stage_and_no_rejection_record(client):
    c, conn = client
    c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "sent", "channel": "email", "reason": None},
    )
    res = c.post(
        "/api/interview-slots/slot-1/invitation/outcome",
        json={"status": "declined", "channel": None, "reason": "已接受其他 offer"},
    )
    assert res.status_code == 200
    assert res.json()["slot"]["invitation_status"] == "declined"
    assert (
        conn.execute("SELECT current_stage_id FROM application WHERE id = 'app1'").fetchone()[0]
        == "interview"
    )
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM application_stage_history WHERE application_id = 'app1'"
        ).fetchone()[0]
        == 0
    )


def test_e2e_regenerate_appends_version_and_old_draft_still_readable(client, monkeypatch):
    c, _conn = client
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot",
        _fake_compute(("run-e2e-1", "run-e2e-2", "run-e2e-3")),
    )
    c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    c.post("/api/interview-slots/slot-1/invitation/generate", json={})
    detail = c.get("/api/interview-slots/slot-1/invitation").json()
    assert [d["version"] for d in detail["drafts"]] == [1, 2]
    assert all(d["analysis_run_id"] for d in detail["drafts"])
```

**命令与预期输出**：

```bash
python3 -m pytest tests/test_interview_scheduling_u3_e2e.py -q
```

预期输出（末尾一行）：`3 passed in X.XXs`。

---

## 2. 交付前自查

- [x] 任务标题全部三级 `### Task N: `（`grep -c '^### Task '` 应等于 12）。
- [x] 有 **Global Constraints** 段，内容与 CLAUDE.md 一致（工程铁律 1/2/3/5 逐字、
      合规红线三条逐字、部署约束 1/5 逐字，并逐条标注本单元落点）。
- [x] spec 每条 `### Requirement:` 都能指到至少一个 Task（见 §3）。
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出。
- [x] 无 TBD / TODO /「适当处理错误」占位符；唯一标注为「⚠️ 若上游已落地则跳过」的
      两处（auth 前缀重复项、`slot_for` import 名）都给了**确定动作**，不是待定项。
- [x] 前后 Task 的表名、列名、函数签名一致（模板版本是 TEXT `'v1'`、草稿
      `version` 是 INTEGER、`template_version` 列存标签）。
- [x] 每个有副作用的动作独占一个 Task 步骤且带幂等键：`effect_persist_draft`（run_id）、
      `effect_edit_draft`（draft_id+正文指纹）、`effect_mark_draft_human_written`（draft_id）、
      `effect_backfill_invitation_outcome`（target_status）、`effect_send_invitation`
      （draft_id+正文指纹）。
- [x] 本单元不产出任何 AI 评分 ⇒ 无 `evidence_ref` 断言需求（spec 不含评分条目）。

## 3. spec 覆盖对照

| Requirement（`interview-invitation-drafting/spec.md`） | 落点（Task） | 关键判据 |
|---|---|---|
| 按场次生成邀约文案 | Task 1（模板）、Task 2（纯函数）、Task 4（persist）、Task 8（接口） | 标识与留痕：`draft.body` 含 `AI_LABEL_PREFIX`；`analysis_run_id` 回指那一次调用；版本递增不覆盖（Task 10/11） |
| 　Scenario 生成草稿 | Task 8 + Task 11 | 201 + 草稿带标识 + 留痕（模型标识取响应字段：Task 2） |
| 　Scenario 对已取消场次生成被拒 | Task 4（事务内 `_assert_slot_invitable`）+ Task 8（409） | Task 10/11 各一条反证 |
| HR 复制发送与结果回填 | Task 3（表）、Task 6（节点）、Task 8（接口）、Task 9（页面复制按钮） | 回填记录 `actor/at`；渠道枚举；`declined` 必填原因 |
| 　Scenario 回填已发出 | Task 6 + Task 11 | `invitation_status='sent'` + `sent_channel='wechat'` + 操作人时刻 |
| 　Scenario 回填候选人拒绝 | Task 6 + Task 10/11/12 | ⛔ 不写 `rejection_record`、⛔ 阶段不动、⛔ 不写 history |
| 人工改写后的标识处置 | Task 5（两个节点）+ Task 8 + Task 9 | 编辑后仍带标识（无条件重贴）；标记人工撰写去标识 + `authorship_marked_by/at` |
| 系统外发一律经既有门禁 | Task 7 + Task 8（`/send`） | 唯一入口 `deliver_candidate_message`；三条拦截路径各一条痕（Task 10） |
| 文案模板的来源与版本 | Task 1（表 + 白名单校验 + v1 占位种子）、Task 8（PUT） | 升版不覆盖；留痕记模板版本；⛔ 无评分/排名/淘汰理由占位符（参数化反证） |

## 4. 本计划相对 `tasks.md` / `design.md` / spec 的偏离登记

1. **D-U3-1（实现细节）**：模板占位符白名单在 tasks 3.1 列的六项之外多放
   `candidate_name`（收信人姓名），共九个。理由：邀约文案要称呼候选人，而它是普通
   事实字段，与评分／排名无关；tasks 3.1 的清单是「至少含」而不是封闭枚举。
2. **D-U3-2（schema 增量）**：新增 `invitation_outcome_log` 表（U1 未建）。理由：
   tasks 3.5 要求「记录操作人与时刻」且「同状态重复提交无第二条留痕」，需要一个
   `(slot_id, status)` 唯一的结构性载体；把状态与操作人塞进 `interview_slot` 的
   `updated_by/updated_at` 只能记最后一次，且 UNIQUE 无处落地。
   ⛔ 表里**没有**阶段列与 `rejection_record` 引用——「候选人拒绝不淘汰」是结构保证。
3. **D-U3-3（与 spec Scenario 的差异，须 reviewer 知悉）**：spec 的
   「总开关关闭时系统外发 ⇒ 消息被拦截进待审批队列」这一句，按既有门禁
   （`app/outbound/delivery.py`）**无法在一次带签名的调用里同时成立**：
   `confirmed_by` 非空时被拦**不入队**（`deliver_candidate_message` 的 `elif not
   (message.confirmed_by or "").strip()` 分支），
   而不带签名的调用会被更早的第 ⑥ 条判成「风险等级为最高级/等待人工确认」。
   本单元取 tasks 3.6 的字面写法（`confirmed_by=<HR>`，一次点击＝一次人工签名，
   与 voice-structured-interview U3 的 `effect_deliver_invitation` 接线同一形态），
   因此**拦截 + 留痕「外发总开关关闭」成立、『进待审批队列』不成立**；
   后者要成立必须改门禁的入队判据，属跨单元、且触碰候选人外发通道
   （CLAUDE.md 决策代理表**不可代**项），本单元⛔ 不动。
   既有测试 `tests/test_outbound_delivery.py::test_a_signed_draft_with_the_switch_off_is_blocked_and_queued`
   对同一 Scenario 的既有读法同样是「拦截 + 原因记『外发总开关关闭』」。
4. **D-U3-4（实现细节）**：`compute_invitation_draft` 的签名在 tasks 3.2 的
   `(slot, template, contact_hint)` 基础上补了 `gateway`（首参）与把 `template` 拆成
   `template_body`/`template_version`，与 offer-generation U2 的
   `compute_letter_draft(gateway, *, kind, template_body, template_version, facts, ...)`
   逐字同构（纯函数必须自己拿网关；模板版本要单独落库留痕）。
5. **D-U3-5（依赖缺口，已写进 §0）**：U4 的 `app/storage/contact_vault.py` 未交付 ⇒
   收件对象解析用惰性 import + fail-closed；U2 的 `effect_schedule_slot` 未落地 ⇒
   e2e 夹具直插场次；U2 的 `/api/interview-slots` 前缀可能尚未登记 ⇒ auth.py 一行可能重复。
6. **D-U3-6（测试基线）**：模板 v1 占位种子（tasks 3.1 的交付物）会让 U1 的 schema
   测试 `tests/test_db_interview_invitation_draft_schema.py` 里四处写死 `'v1'` 的
   `INSERT` 撞主键。本计划按「改测试、保种子」处置：把那四处字面量改成 `'v2'`/`'v3'`
   （`test_invitation_template_version_is_primary_key` 两次插入都用 `'v2'`；
   `test_invitation_template_keeps_multiple_versions` 用 `'v2'`/`'v3'` 并断言
   `["v2", "v3"]`；`test_invitation_template_requires_updated_by` 与
   `test_invitation_template_updated_at_defaults_to_now` 的插入改 `'v2'`）。
   ⛔ 不去掉种子——种子是 tasks 3.1 的交付物。

> **落地说明 D-U3-7（Task 1 实现时发现的测试基线偏差，两处；以磁盘真身为准，⛔ 无需返工）。**
> 计划 1c / 1d 里有两处断言漏算了**本 Task 自己引入的 v1 占位种子**（种子是同一份
> 交付物，两条断言互斥——`test_seed_v1_exists_after_init_schema` 要求种子在，
> 而这两条断言按字面要求种子的那一行不在）。按「保种子、改断言」处置：
>
> ① `tests/test_invitation_template.py::test_put_creates_next_version_and_keeps_old`：
> 计划字面 `result["version"] == "v2"` / `versions == ["v1", "v2"]`。种子 v1 在场时，
> 两次 PUT 依次产出 v2、v3——改为断言第二次 PUT 为 `"v3"`、`versions == ["v1","v2","v3"]`
> （测试意图「升版不覆盖旧版」不变，且 v1 仍在）。同文件另两条
> （`test_put_same_body_is_unchanged_noop` 的 `count == 2`、
> `test_put_accepts_all_whitelisted_placeholders` 的 `"v2"`）本就按种子在场写，
> 未改。
> ② `tests/test_db_interview_invitation_draft_schema.py::test_invitation_template_keeps_multiple_versions`：
> 计划字面把插入改 `'v2'`/`'v3'` 并断言 `rows == ["v2","v3"]`，但 `SELECT version` 是全表——
> 种子 v1 也在。改为断言 `["v1","v2","v3"]`（「一版一行、不覆盖」在带种子的真身上同样成立）。
> 计划 1d 其余三处（`..._is_primary_key` / `..._requires_updated_by` / `..._updated_at_
> defaults_to_now`）按字面落地，逐字未改。
>
> **验证证据**：`tests/test_invitation_template.py tests/test_db_interview_invitation_draft_schema.py -q`
> ⇒ **31 passed**（计划预期「30 passed」是条数笔误：新增文件实为 15 条用例——10 条普通
> ＋ `test_put_rejects_forbidden_placeholders` 的 5 个参数化实例；U1 schema 文件 16 条＝15＋1）。

## 5. 提取验证记录（`spec-to-plan` 第 6 步，本计划写作时已做的最小核验）

- 已在仓库磁盘上核对：U1 的 `interview_invitation_draft` / `invitation_template`
  两表 DDL 与列名（`app/storage/db.py:930-957`）、其 schema 测试
  （`tests/test_db_interview_invitation_draft_schema.py`）与 `analysis_run_id`
  **无外键**（本计划的测试可用打桩 run_id）。
- 已核对同构先例的真实签名：`compute_letter_draft`、`effect_persist_letter`、
  `effect_edit_letter`、`effect_mark_letter_human_written`（`app/agents/letter_drafter.py`、
  `app/graph/letter_nodes.py`）、`effect_deliver_invitation`（`app/graph/invite_nodes.py`）、
  `deliver_candidate_message` 的入队判据（`app/outbound/delivery.py`）、
  `compute_outbound_gate` 的七条判定顺序（`app/outbound/gate.py`）与
  `AI_LABEL_PREFIX` 的来源（`app/storage/letter_template.py`、
  `app/agents/jd_agent.py`）。
- 已核对门禁判定**顺序**对测试设计的影响：缺标识（第 ④ 条）先于收件对象（第 ⑤ 条）
  先于总开关（第 ⑦ 条），所以「总开关关闭」用例必须同时给足标识与收件人，
  否则拿到的是别的原因（Task 10 的 `_send` 因此显式传 `recipient`）。
- 已核对 `tests/conftest.py::make_test_client` 的返回形状（`(client, conn)` 二元组）、
  `SESSION_COOKIE_NAME == "hr_session"`、`upsert_account` / `create_session` 的签名，
  以及 `_render_static_page` 的 `<!--BASE_HREF-->` 替换点。
- **本会话实测（SQLite，内存库）**：本计划的 `invitation_outcome_log` DDL 可建表；
  七个反例全部被拒——`sent` 缺渠道、`declined` 空原因、未知 status、未知 channel、
  非 `sent` 带渠道、`slot_id` 外键不存在、`(slot_id, status)` 重复；
  两条正例（`sent`+渠道、`declined`+原因）通过。
- **本会话实测（正则模拟白名单校验）**：模板 v1 占位种子里的九个占位符全部落在
  `ALLOWED_INVITATION_PLACEHOLDERS` 内，命中禁词的占位符为 0，且覆盖 spec 要求的
  岗位/轮次/起止时刻/形式/面试官称谓/联系人六项。
- **本会话实测（AST）**：计划内 22 个 `python` 代码块全部可 `ast.parse`
  （其中路由片段按「函数体内缩进」形态解析）——⛔ 不保证语义正确，只保证没有搬运级
  语法破损。

> 完整「把计划里全部代码块原样提取、独立 venv 跑全量测试」的第 6 步**不在本
> plan-writing 泳道执行**（本泳道只产出 plan，⛔ 不改 `app/`/`tests/`）。该步由
> `run-build` 在执行本计划时完成（U1/U2 两份计划同一处置）。

## 6. 完成判据（`tasks.md` 第 3 章 checkbox 在这些全部成立后才勾）

- `python3 -m pytest tests/test_invitation_template.py tests/test_invitation_drafter.py -q` 全绿；
- `python3 -m pytest tests/test_db_u3_schema.py tests/test_interview_invitation_storage.py -q` 全绿；
- `python3 -m pytest tests/test_invitation_effect.py tests/test_interview_invitation_api.py tests/test_interview_scheduling_u3_e2e.py -q` 全绿；
- `python3 -m pytest tests/test_db_interview_invitation_draft_schema.py tests/test_db_migration.py tests/test_db_m2_schema.py tests/test_db_m2_u2_schema.py tests/test_auth_middleware.py -q` 全绿；
- `python3 -m pytest -q` 全量无回归；
- `grep -c '^### Task ' docs/superpowers/plans/2026-10-10-interview-scheduling-unit3-invitation-drafting.md` == 12。

**仍未闭环、留给后续单元/回件的（⛔ 不在本单元自行闭合）**：

- 系统外发的**开启**（`CANDIDATE_OUTBOUND_ENABLED` 与邀请外发通道）是 🔴 不可代项，
  U3 只交付"默认关闭 + 经门禁拦截并留痕"的接线；
- 联系方式保管（U4）未交付 ⇒ 系统外发的收件对象在当下恒为空、必被门禁拦（预期行为）；
- 模板 v1 正文与「联系人」缺省串都是**占位**，待 tasks 0.4 人事部#3 回件到后只换内容
  不改代码（design D5 / Risk 表）。




