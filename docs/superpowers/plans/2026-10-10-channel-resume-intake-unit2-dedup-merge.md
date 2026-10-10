# channel-resume-intake U2：去重合并 Implementation Plan

> **For agentic workers（Codex 引擎）：** 用 `run-build`（`scripts/codex_sdd_runner.py`）按 `### Task N:` 三级标题逐任务执行；本计划是 spec-to-plan 的唯一输出，⛔ 不在本会话里开始写代码。执行前先读本计划的「架构决策」与「Global Constraints」，reviewer 以它们为注意力透镜。

**Goal:** 在 U1（解包＋来源识别，已合 main）之上，把候选人归属从 M2 的「按姓名去重」升级为「按手机号哈希自动挂接 ＋ 疑似重复只提示、HR 人工合并且可撤销」。解析出手机号后系统以哈希与既有候选人比对，命中挂接、不命中新建；认不准的同名同岗候选人只标「疑似重复」，合不合由 HR 决定并留痕、可撤销。

**Architecture:** 沿用 U1 的单进程同步管线。挂接/合并/撤销是三个独占的副作用执行单元（`@idempotent_effect`），全部落在 `app/intake/merge.py`；`extract_phone_hash` 与 `detect_suspected_duplicates` 是确定性纯函数（⛔ 无模型调用）。候选人身份改造作用于**全部上传路径**（ZIP 与单文件共用 `_ingest_one_resume`，design D3 的「在 `effect_persist_parse` 之后」）。

**Tech Stack:** Python 3.14、FastAPI、SQLite（`SCHEMA`/`_ADDED_COLUMNS` 双轨迁移）、pydantic v2、标准库 `hashlib`/`re`/`unicodedata`/`json`、pytest、httpx。无新增第三方依赖。

**Spec:**

- `openspec/changes/channel-resume-intake/specs/candidate-dedup-merge/spec.md`
- `openspec/changes/channel-resume-intake/specs/resume-source-tagging/spec.md`（仅「来源列」相关；来源机制本体已由 U1 交付，本单元只消费 `candidate_source()`）
- `openspec/changes/channel-resume-intake/design.md`（决策 D3/D4/D7；Open Questions OQ4 仅标记、不阻塞）
- `openspec/changes/channel-resume-intake/tasks.md` 第 2 章（2.1–2.8，仅用于确认 U2 边界，⛔ 不作为计划输入）

## 需求覆盖表

| spec 能力 | `### Requirement:` | 覆盖 Task |
|---|---|---|
| candidate-dedup-merge | 手机号哈希自动挂接 | Task 1（`extract_phone_hash` 纯函数）、Task 2（`effect_attach_resume_to_candidate` 节点＋管线接线） |
| candidate-dedup-merge | 疑似重复只提示不自动合并 | Task 3（`detect_suspected_duplicates` 纯函数）、Task 7（候选列表「疑似重复」标记） |
| candidate-dedup-merge | HR 人工合并与撤销 | Task 4（`candidate_merge_log` 表＋`merged_into`＋`action` 列）、Task 5（合并节点）、Task 6（撤销节点）、Task 7（合并页／撤销按钮） |
| candidate-dedup-merge | 合并动作幂等 | Task 2／5／6（三个 `@idempotent_effect` 节点＋幂等测试） |
| resume-source-tagging | 来源值域（候选人来源＝最早简历来源） | Task 7（候选列表「来源列」调用 U1 已交付的 `candidate_source()`，⛔ 不重写、不加列） |

## Global Constraints

以下逐字摘自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」，与本交付单元的适用判断一并列出。**每个 Task 的验收隐含包含本节全部适用条目。**

1. **工程铁律 1**：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。✅ **适用**：`effect_attach_resume_to_candidate` / `effect_merge_candidates` / `effect_unmerge_candidates` 三个新增副作用节点全部走既有 `app/storage/idempotency.py::idempotent_effect` 装饰器，各自业务写与 `effect_log` 同事务提交（见「架构决策」第 5 条幂等键映射）。

2. **工程铁律 2**：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。✅ **适用**：`extract_phone_hash`、`hash_phone`、`normalize_name`、`detect_suspected_duplicates` 都是无副作用纯函数（不写库、不调模型、不写 storage）；写库的只有三个 `effect_*` 节点。

3. **工程铁律 3**：所有 AI 评分必须持久化：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。✅ **适用，且已由既有基础设施满足**：本单元不新增评分、⛔ 无模型调用；复用的 M2 解析路径 `compute_parse`/`effect_persist_parse` 已满足本铁律。

4. **工程铁律 4**：每条 `criterion_score` 必须有 `evidence_ref`。`evidence_ref` 为空不允许写入。✅ **适用，但不改**：本单元不写 `criterion_score`；手机号哈希与疑似重复判定不产生评分项。

5. **工程铁律 5**：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。✅ **适用，但本单元无新增模型调用**：`extract_phone_hash` 与 `detect_suspected_duplicates` 是确定性纯函数、⛔ 不调模型（spec「MUST NOT 使用模型判断是否同一人」）。

6. **工程铁律 6**：企微回调先落库再处理：只推一次、5 秒无响应即丢弃。⛔ **不适用，理由**：本单元没有企微回调路径。

7. **工程铁律 7**：`langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。✅ **适用，环境约束**：`requirements.txt` 已锁 `langgraph==1.0.10`，本单元不降级、不新增图节点。

8. **合规红线·AI 只做排序推荐，不做自动淘汰**：淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。✅ **适用**：本单元只做挂接/标记/合并/撤销，绝不淘汰、不写 `rejection_record`；合并是 HR 人工动作（`actor_type='human'`）并留痕。

9. **合规红线·禁止人脸/表情分析**：⛔ **不适用，理由**：本单元不处理任何影像/声学信号。

10. **合规红线·AI 生成的 JD、拒信、邀约须带标识**：⛔ **不适用，理由**：本单元不产出任何 AI 生成的对外文本。

11. **合规红线·模型全部走境内，简历数据不出境**：✅ **适用**：`extract_phone_hash` / `detect_suspected_duplicates` 是本地确定性计算，不把简历内容发给任何外部服务；复用的 M2 解析路径使用境内 LLM。

12. **合规红线·绝不用历史录用结果做监督信号**：⛔ **不适用，理由**：本单元无训练、无监督信号。

13. **合规红线·候选人入口一律用一次性邀请链接**：⛔ **不适用，理由**：本单元是 HR 侧管理动作，不是候选人对外入口。

14. **合规红线·主观描述不得进入硬门槛规则**：⛔ **不适用，理由**：本单元不做硬门槛判定。

15. **部署约束 1·路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用一律相对路径。✅ **适用**：Task 7 的 `candidate_list.html` / `candidate_merge.html` 沿用 `<base href>` 相对路径写法；新增路由走 `router`。

16. **部署约束 4·目标服务器是 Windows，没有 Docker，Python venv 部署，不引入容器**：✅ **适用**：本单元零新增第三方依赖，只用标准库与既有依赖。

17. **部署约束 5·M2 起处理真实简历前必须具备可识别到人的登录 + 简历访问留痕**：✅ **适用，但已由 M2 满足**：`/api/candidates*` 已在 `PROTECTED_PATH_PREFIXES` 内（未登录 401），本单元不新增真实简历处理能力。

## 架构决策（先读，避免和 `tasks.md`/`design.md` 的字面表述对不上）

**1. `candidate.source` 不做物理列（沿用 U1 架构决策 #1）。**
`tasks.md` 2.2 写「新建 `candidate`（`source`＝该简历来源）」，但 U1 已裁决 `candidate.source` 不是物理列、候选人来源恒由 `app/storage/source.py::candidate_source(conn, candidate_id)` 按「最早一份简历的来源」推导。本计划以磁盘真身为准：`effect_attach_resume_to_candidate` 只写 `candidate(name, phone_hash)`，不建 `source` 列；候选列表的来源列直接调用 U1 已交付的 `candidate_source()`。

**2. 候选人归属从「M2 姓名去重」改为「手机号哈希挂接 ＋ 疑似重复人工合并」（design D3/D4 的意图）。**
`app/graph/resume_nodes.py::effect_persist_parse` 里的 `_find_or_create_candidate`（`name = ? AND phone_hash IS NULL`）与 `_create_application` 移除，candidate/application 的创建整体搬到 `effect_attach_resume_to_candidate`。无手机号 ⇒ 恒新建候选人（spec「无手机号 → 新建候选人，并进入疑似重复检测」），不再按姓名自动合并——同名同岗的无手机号候选人由 `detect_suspected_duplicates` 标记、HR 人工合并。此改动作用于**全部上传路径**（ZIP 与单文件共用 `_ingest_one_resume`）。

**3. `application_stage_history` 新增 `action` 列（可空 TEXT，走 `_ADDED_COLUMNS`）。**
spec/design 的 `action=closed_by_merge`（本单元）与 U3 的 `action=source_corrected` 都需要它，而磁盘真身里该表只有阶段迁移列、无 `action`。`closed_by_merge` 记录为 `from_stage_id = to_stage_id = <当前阶段>`（阶段不变，`action` 列承载语义）＋ `actor_type='human'`＋`actor=<merged_by>`；application 行不删、`status` 不变（`application.status` 的 CHECK 无 `merged` 取值，不越界加状态）。

**4. resume 与 candidate 的关联只经 application（`resume` 无 `candidate_id` 列）。**
`tasks.md` 2.2/2.5 写「`resume.candidate_id` / `application.candidate_id`」，按磁盘真身落地：改的是 `application.candidate_id`。M2 的 `resume` 表注释明确「不在 resume 上留悬空外键」。

**5. 幂等键映射（design 简写 → `@idempotent_effect` 实际生成键 `{thread_id}:{node_name}:{business_key}`）。**

| 节点 | thread_id | business_key | 实际 effect_key |
|---|---|---|---|
| `effect_attach_resume_to_candidate` | `resume_id` | `"once"` | `{resume_id}:effect_attach_resume_to_candidate:once` |
| `effect_merge_candidates` | `primary_id` | `f"{secondary_id}:{request_id}"` | `{primary_id}:effect_merge_candidates:{secondary_id}:{request_id}` |
| `effect_unmerge_candidates` | `merge_log_id` | `"undo"` | `{merge_log_id}:effect_unmerge_candidates:undo` |

`request_id` 由合并页每次表单提交生成（UUID）；同一提交重试同 key 幂等，不同提交会撞 `candidate.merged_into` 校验 ⇒ 409。

**6. 手机号哈希函数。**
M2 只裁决「哈希存储、明文不落库」，磁盘上**从未实现**任何 phone 哈希函数（`candidate.phone_hash` 恒 NULL）。按全仓既有口径（`content_sha256` / `bundle_sha256` 同款）落地：`hash_phone(phone) = hashlib.sha256(phone.encode("utf-8")).hexdigest()`。`extract_phone_hash` 正则取第一个 `1[3-9]\d{9}` 后立即哈希，明文不出函数。

**7. 同岗位双投递「关闭」的谓词。**
被合并方在同岗位的非保留投递：`candidate_id` 改到主候选人＋写 `action='closed_by_merge'` 流转事实＋不删行。是否「当前关闭」由 `candidate_merge_log.unmerged_at IS NULL`（合并仍生效）派生；U2 只写事实与展示事实，不改 M2 筛选查询（筛选跳过 closed 投递属后续单元，本计划不越界）。

## File Structure

| 文件 | 责任 |
|---|---|
| `app/intake/phone_hash.py`（新建） | `hash_phone`、`extract_phone_hash` 纯函数 |
| `app/intake/duplicates.py`（新建） | `normalize_name`、`CandidateRef`、`detect_suspected_duplicates` 纯函数 |
| `app/intake/merge.py`（新建） | `effect_attach_resume_to_candidate`、`effect_merge_candidates`、`effect_unmerge_candidates`、`MergeValidationError` |
| `app/graph/resume_nodes.py`（改） | `effect_persist_parse` 移除 candidate/application 创建（挂接后移） |
| `app/storage/db.py`（改） | `candidate_merge_log` 新表；`candidate.merged_into`、`application_stage_history.action` 加列（走 `_ADDED_COLUMNS`） |
| `app/web/server.py`（改） | 挂接节点接线（上传/重解析）；候选人列表/合并/撤销 API 与页面路由 |
| `app/web/static/candidate_list.html`（新建） | 候选人列表页（来源列、疑似重复标记、合并跳转） |
| `app/web/static/candidate_merge.html`（新建） | 合并页（选主、填依据、双投递选择、撤销按钮） |
| `tests/test_phone_hash.py`（新建） | Task 1 测试 |
| `tests/test_duplicates.py`（新建） | Task 3 测试 |
| `tests/test_attach_resume.py`（新建） | Task 2 测试 |
| `tests/test_candidate_merge_schema.py`（新建） | Task 4 测试 |
| `tests/test_merge_unmerge.py`（新建） | Task 5/6 测试 |
| `tests/test_candidates_api.py`（新建） | Task 7 测试 |
| `tests/test_channel_dedup_e2e.py`（新建） | Task 8 e2e |
| `tests/test_resume_nodes.py`（改） | `effect_persist_parse` 不再建 candidate/application 的回归更新 |
| `tests/test_db_migration.py`（改） | 补 `application_stage_history` 旧表 DDL，并把 `candidate`、`application_stage_history` 纳入漂移守卫 |

---

### Task 1: `extract_phone_hash` 纯函数（手机号正则 → 立即哈希，明文不出函数）

**Files:**

- Create: `app/intake/phone_hash.py`
- Test: `tests/test_phone_hash.py`

**Interfaces:**

- Produces: `hash_phone(phone: str) -> str`、`extract_phone_hash(text_spans: list[TextSpan]) -> str | None`
- 约束：返回值永不匹配手机号正则；明文不落日志、不出函数（tasks 2.1）

- [ ] **Step 1: 创建 `app/intake/phone_hash.py`**

```python
"""手机号提取与哈希（channel-resume-intake U2 tasks 2.1）。

纯函数：只读 spans 文本，产出一个 SHA-256 哈希字符串或 None，⛔ 不写库、不调
模型。明文手机号绝不离开本模块——返回值是哈希，任何中间变量都不保留明文。
M2 只裁决「哈希存储、明文不落库」，磁盘上未实现过 phone 哈希；本模块是全仓
第一个也是唯一一个实现（与 content_sha256 / bundle_sha256 同款 hexdigest 口径）。
"""
from __future__ import annotations

import hashlib
import re

from app.parsing.spans import TextSpan

# 中国大陆手机号：1 开头、第二位 3-9、共 11 位，前后不能是数字。
_CN_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def hash_phone(phone: str) -> str:
    """M2 既定口径的落地实现：手机号只用于去重、以 SHA-256 哈希存储、明文不落库。"""
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def extract_phone_hash(text_spans: list[TextSpan]) -> str | None:
    """按 span 顺序取第一个中国大陆手机号，立即哈希后返回。明文绝不出本函数。"""
    for span in text_spans:
        match = _CN_MOBILE.search(span.text)
        if match:
            return hash_phone(match.group(0))
    return None
```

- [ ] **Step 2: 创建 `tests/test_phone_hash.py`**

```python
from __future__ import annotations

import re

from app.intake.phone_hash import extract_phone_hash, hash_phone
from app.parsing.spans import TextSpan


def _spans(*lines: str) -> list[TextSpan]:
    return [
        TextSpan(span_id=i, start=0, end=len(t), text=t)
        for i, t in enumerate(lines, start=1)
    ]


def test_returns_hash_of_first_phone():
    spans = _spans("张三", "手机：13800138000", "邮箱：a@b.c")
    assert extract_phone_hash(spans) == hash_phone("13800138000")


def test_result_does_not_match_phone_regex():
    value = extract_phone_hash(_spans("13800138000"))
    assert value is not None
    assert re.search(r"1[3-9]\d{9}", value) is None


def test_no_phone_returns_none():
    assert extract_phone_hash(_spans("张三", "无手机号")) is None


def test_rejects_ten_digit_number():
    # 只有 10 位，不是合法手机号
    assert extract_phone_hash(_spans("1380013800")) is None


def test_no_plaintext_in_logs(caplog):
    extract_phone_hash(_spans("13800138000"))
    assert "13800138000" not in caplog.text
```

- [ ] **Step 3: 验证**

```bash
python -m pytest tests/test_phone_hash.py -q
```

预期输出：`5 passed`，exit code 0。

---

### Task 2: `effect_attach_resume_to_candidate` 节点与 `effect_persist_parse` 重构

**Files:**

- Create: `app/intake/duplicates.py`（`normalize_name`/`CandidateRef`/`detect_suspected_duplicates`——`merge.py` 依赖它，须在本任务先建）
- Create: `app/intake/merge.py`（本任务只写挂接节点与其私有辅助；合并/撤销在 Task 5/6 追加）
- Modify: `app/graph/resume_nodes.py`（`effect_persist_parse` 移除 candidate/application 创建）
- Modify: `app/web/server.py`（上传/重解析接线挂接节点）
- Test: `tests/test_attach_resume.py`（新建）
- Modify: `tests/test_resume_nodes.py`（回归更新）

**Interfaces:**

- Produces: `effect_attach_resume_to_candidate(conn, *, thread_id, business_key, resume_id, job_id, name, phone_hash) -> dict | None`
- 幂等键：`{resume_id}:effect_attach_resume_to_candidate:once`

- [ ] **Step 1: 创建 `app/intake/duplicates.py`**

```python
"""疑似重复判定（channel-resume-intake U2 tasks 2.3）。

纯函数：只做姓名规范化比较，⛔ 不写库、不调模型。姓名规范化＝NFKC 统一全半角 +
去掉全部空白（与 app/agents/field_grounding.py::normalize_for_grounding 同口径）。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def normalize_name(text: str | None) -> str:
    if not text:
        return ""
    return "".join(unicodedata.normalize("NFKC", str(text)).split())


@dataclass(frozen=True)
class CandidateRef:
    id: str
    name: str


def detect_suspected_duplicates(
    candidate: CandidateRef,
    same_job_candidates: list[CandidateRef],
) -> list[CandidateRef]:
    """同岗位 + 姓名规范化后相同 ⇒ 疑似重复。只返回其它候选人（不含 candidate 自己）。
    纯函数，⛔ 无任何模型调用。"""
    target = normalize_name(candidate.name)
    return [
        c for c in same_job_candidates
        if c.id != candidate.id and normalize_name(c.name) == target
    ]
```

- [ ] **Step 2: 创建 `app/intake/merge.py`（挂接部分）**

```python
"""候选人挂接与合并（channel-resume-intake U2 tasks 2.2/2.5/2.6）。

三个独占的副作用执行单元，各自带幂等键、经 @idempotent_effect 与 effect_log
同事务提交（工程铁律 1）。关联变更只动 application.candidate_id——resume 上
没有 candidate_id 列（M2 注释明确），candidate 与 resume 的关联唯一经 application。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.intake.duplicates import normalize_name
from app.storage.idempotency import idempotent_effect


class MergeValidationError(ValueError):
    """合并/撤销的前置校验失败（候选人不存在、已合并、缺保留选择等）。"""


def _find_candidate(conn: sqlite3.Connection, name_norm: str, phone_hash: str | None) -> str | None:
    """按 (规范化姓名, 手机号哈希) 查未合并候选人。无手机号 ⇒ 恒不命中（spec
    「无手机号 → 新建候选人」：NULL 不参与匹配，宁留重复也不错误合并）。"""
    if phone_hash is None:
        return None
    row = conn.execute(
        "SELECT id FROM candidate WHERE name = ? AND phone_hash = ? AND merged_into IS NULL",
        (name_norm, phone_hash),
    ).fetchone()
    return row[0] if row else None


def _create_application(
    conn: sqlite3.Connection, *, candidate_id: str, job_id: str, resume_id: str
) -> str:
    application_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (application_id, candidate_id, job_id, resume_id),
    )
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type) "
        "VALUES (?, ?, NULL, 'initial', 'agent')",
        (str(uuid.uuid4()), application_id),
    )
    return application_id


@idempotent_effect("effect_attach_resume_to_candidate")
def effect_attach_resume_to_candidate(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    resume_id: str,
    job_id: str,
    name: str,
    phone_hash: str | None,
) -> dict:
    """把一份已解析简历挂到候选人：按 (规范化姓名, 哈希) 查未合并候选人，命中复用、
    不命中新建；创建 application + 初始流转事实。幂等键 {resume_id}:effect_attach_resume_to_candidate:once。"""
    existing = conn.execute(
        "SELECT id, candidate_id FROM application WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    if existing is not None:
        return {"application_id": existing[0], "candidate_id": existing[1], "candidate_created": False}

    name_norm = normalize_name(name) or "姓名待校对"
    candidate_id = _find_candidate(conn, name_norm, phone_hash)
    candidate_created = candidate_id is None
    if candidate_created:
        candidate_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO candidate (id, name, phone_hash) VALUES (?, ?, ?)",
            (candidate_id, name_norm, phone_hash),
        )
    application_id = _create_application(
        conn, candidate_id=candidate_id, job_id=job_id, resume_id=resume_id
    )
    return {"application_id": application_id, "candidate_id": candidate_id, "candidate_created": candidate_created}
```

- [ ] **Step 3: 重构 `app/graph/resume_nodes.py::effect_persist_parse`**

删除 `_find_or_create_candidate` 与 `_create_application` 两个函数；把 `effect_persist_parse` 改为只写解析结果与校对队列、不再创建 candidate/application、返回 `None`：

```python
@idempotent_effect("effect_persist_parse")
def effect_persist_parse(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    resume_id: str,
    job_id: str,
    fields: ResumeFields,
    parser_version: str,
    model_configured: str,
    model_response: str | None,
    prompt_version: str,
    confidence_threshold: float,
) -> None:
    """写 resume_parse_version（历史）+ resume 三列（最新版缓存）+
    field_review_queue（低置信度字段）。

    ⛔ 不再创建 candidate/application：候选人归属由
    app/intake/merge.py::effect_attach_resume_to_candidate 在解析之后承担
    （channel-resume-intake U2 design D3）。
    ⛔ 不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交（工程铁律 1）。
    """
    fields_json = fields.model_dump_json()
    confidence = _lowest_field_confidence(fields)

    conn.execute(
        "INSERT INTO resume_parse_version "
        "(resume_id, parser_version, parsed_json, confidence, model_configured, "
        "model_response, prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (resume_id, parser_version, fields_json, confidence, model_configured,
         model_response, prompt_version),
    )
    conn.execute(
        "UPDATE resume SET status = 'parsed', parsed_json = ?, parse_confidence = ?, "
        "parser_version = ? WHERE id = ?",
        (fields_json, confidence, parser_version, resume_id),
    )

    _upsert_review_queue_rows(
        conn, resume_id=resume_id, fields=fields, confidence_threshold=confidence_threshold
    )
```

保留模块内 `_lowest_field_confidence`、`_upsert_review_queue_rows`、`record_resume_access`、`queue_reapplication_screening` 原样不动；删掉 `_find_or_create_candidate`、`_create_application`。

- [ ] **Step 4: 接线 `app/web/server.py` 的上传与重解析**

在 `app/web/server.py` 顶部 import 区追加：

```python
from app.intake.merge import effect_attach_resume_to_candidate
from app.intake.phone_hash import extract_phone_hash
```

把 `_ingest_one_resume` 里 `parser_version = "v1"` 之后到 `return {"file_name": ...}` 之前的那段，整体替换为：

```python
        parser_version = "v1"
        effect_persist_parse(
            conn,
            thread_id=resume_id,
            business_key=parser_version,
            resume_id=resume_id,
            job_id=job_id,
            fields=fields,
            parser_version=parser_version,
            model_configured=gateway.model,
            model_response=meta.response_model,
            prompt_version=PARSE_PROMPT_VERSION,
            confidence_threshold=confidence_threshold,
        )
        candidate_name = (
            fields.name.value if (not fields.name.not_mentioned and fields.name.value)
            else "姓名待校对"
        )
        phone_hash = extract_phone_hash(ingest_result.spans)
        attached = effect_attach_resume_to_candidate(
            conn,
            thread_id=resume_id,
            business_key="once",
            resume_id=resume_id,
            job_id=job_id,
            name=candidate_name,
            phone_hash=phone_hash,
        )
        resolved_application_id = attached["application_id"] if attached else None
        if resolved_application_id is None:
            existing_app = conn.execute(
                "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
            ).fetchone()
            resolved_application_id = existing_app[0] if existing_app else None
        screening_status = "deferred"
        if resolved_application_id is not None:
            profile_version = latest_approved_profile_version(conn, job_id)
            if profile_version is not None:
                try:
                    screen_and_persist(
                        conn,
                        application_id=resolved_application_id,
                        resume_id=resume_id,
                        job_id=job_id,
                        profile_version=profile_version,
                        parse_version=parser_version,
                    )
                    screening_status = "ok"
                except Exception:
                    logger.exception(
                        "job_id=%s resume_id=%s 硬门槛判定失败，简历已入库，"
                        "留待人工/后续触发重判",
                        job_id, resume_id,
                    )
        return {"file_name": upload.filename, "status": "accepted",
                "resume_id": resume_id, "application_id": resolved_application_id,
                "parse_status": "parsed", "screening_status": screening_status}
```

把 `reparse_resume` 里 `fields, meta = compute_parse(...)` 之后到 `return {...}` 之前的那段，整体替换为：

```python
        effect_persist_parse(
            conn,
            thread_id=resume_id,
            business_key=parser_version,
            resume_id=resume_id,
            job_id=job_id,
            fields=fields,
            parser_version=parser_version,
            model_configured=gateway.model,
            model_response=meta.response_model,
            prompt_version=PARSE_PROMPT_VERSION,
            confidence_threshold=confidence_threshold,
        )
        candidate_name = (
            fields.name.value if (not fields.name.not_mentioned and fields.name.value)
            else "姓名待校对"
        )
        phone_hash = extract_phone_hash(spans)
        attached = effect_attach_resume_to_candidate(
            conn,
            thread_id=resume_id,
            business_key="once",
            resume_id=resume_id,
            job_id=job_id,
            name=candidate_name,
            phone_hash=phone_hash,
        )
        resolved_application_id = attached["application_id"] if attached else None
        if resolved_application_id is None:
            existing_app = conn.execute(
                "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
            ).fetchone()
            resolved_application_id = existing_app[0] if existing_app else None
        screening_status = "deferred"
        if resolved_application_id is not None:
            profile_version = latest_approved_profile_version(conn, job_id)
            if profile_version is not None:
                try:
                    screen_and_persist(
                        conn,
                        application_id=resolved_application_id,
                        resume_id=resume_id,
                        job_id=job_id,
                        profile_version=profile_version,
                        parse_version=parser_version,
                    )
                    screening_status = "ok"
                except Exception:
                    logger.exception(
                        "job_id=%s resume_id=%s 重解析后硬门槛判定失败，简历已"
                        "重新解析入库，留待人工/后续触发重判",
                        job_id, resume_id,
                    )
        return {"resume_id": resume_id, "application_id": resolved_application_id,
                "parser_version": parser_version, "screening_status": screening_status}
```

- [ ] **Step 5: 更新 `tests/test_resume_nodes.py`**

把 `test_first_parse_creates_candidate_and_application`、`test_first_parse_writes_stage_history_and_resume_columns`、`test_rerun_same_effect_key_does_not_duplicate`、`test_reparse_new_version_updates_resume_and_keeps_old_version_row` 四个用例替换为（其余两个 review queue 用例不动）：

```python
def test_parse_writes_resume_columns_and_version_but_no_application(conn):
    assert _persist(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM application").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 0
    resume_row = conn.execute(
        "SELECT status, parser_version, parse_confidence FROM resume WHERE id = 'r1'"
    ).fetchone()
    assert resume_row[0] == "parsed"
    assert resume_row[1] == "v1"


def test_rerun_same_effect_key_does_not_duplicate(conn):
    first = _persist(conn)
    second = _persist(conn)
    assert first is None and second is None
    count = conn.execute("SELECT COUNT(*) FROM resume_parse_version WHERE resume_id = 'r1'").fetchone()[0]
    assert count == 1


def test_reparse_new_version_updates_resume_and_keeps_old_version_row(conn):
    _persist(conn, parser_version="v1", business_key="v1")
    _persist(conn, parser_version="v2", business_key="v2", fields=_fields(name_confidence=0.95))
    versions = conn.execute(
        "SELECT parser_version FROM resume_parse_version WHERE resume_id = 'r1' "
        "ORDER BY parser_version"
    ).fetchall()
    assert [v[0] for v in versions] == ["v1", "v2"]
    resume_row = conn.execute("SELECT parser_version FROM resume WHERE id = 'r1'").fetchone()
    assert resume_row[0] == "v2"
    assert conn.execute("SELECT COUNT(*) FROM application").fetchone()[0] == 0
```

- [ ] **Step 6: 创建 `tests/test_attach_resume.py`**

```python
from __future__ import annotations

import sqlite3

from app.intake.merge import effect_attach_resume_to_candidate
from app.intake.phone_hash import hash_phone
from app.storage.db import init_schema
from app.storage.source import candidate_source


def _conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    return c


def _resume(conn, rid):
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, 'j1', 'synthetic', ?, ?, 'alice')",
        (rid, rid + ".pdf", "h-" + rid),
    )


def _attach(conn, *, resume_id, name, phone_hash=None, business_key="once"):
    return effect_attach_resume_to_candidate(
        conn,
        thread_id=resume_id,
        business_key=business_key,
        resume_id=resume_id,
        job_id="j1",
        name=name,
        phone_hash=phone_hash,
    )


def test_phone_hash_hit_attaches_to_existing_candidate(conn):
    conn = _conn()
    conn.execute("INSERT INTO candidate (id, name, phone_hash) VALUES ('c1', '张三', ?)",
                 (hash_phone("13800138000"),))
    _resume(conn, "r1")
    result = _attach(conn, resume_id="r1", name="张三", phone_hash=hash_phone("13800138000"))
    assert result["candidate_id"] == "c1"
    assert result["candidate_created"] is False


def test_no_phone_creates_new_candidate(conn):
    conn = _conn()
    _resume(conn, "r1")
    result = _attach(conn, resume_id="r1", name="张三", phone_hash=None)
    assert result["candidate_created"] is True
    row = conn.execute("SELECT name, phone_hash FROM candidate WHERE id = ?",
                       (result["candidate_id"],)).fetchone()
    assert row == ("张三", None)


def test_same_phone_different_sources_attach_same_candidate(conn):
    conn = _conn()
    _resume(conn, "r-old")
    _resume(conn, "r-new")
    _attach(conn, resume_id="r-old", name="张三", phone_hash=hash_phone("13800138000"))
    conn.execute("UPDATE resume SET source = 'boss', uploaded_at = '2026-01-01 00:00:00' WHERE id = 'r-old'")
    result = _attach(conn, resume_id="r-new", name="张三", phone_hash=hash_phone("13800138000"))
    assert result["candidate_created"] is False
    conn.execute("UPDATE resume SET source = 'liepin', uploaded_at = '2026-01-02 00:00:00' WHERE id = 'r-new'")
    assert candidate_source(conn, result["candidate_id"]) == "boss"


def test_replay_is_short_circuited(conn):
    conn = _conn()
    _resume(conn, "r1")
    first = _attach(conn, resume_id="r1", name="张三", phone_hash=None)
    second = _attach(conn, resume_id="r1", name="张三", phone_hash=None)
    assert first is not None and second is None
    assert conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM application").fetchone()[0] == 1
```

- [ ] **Step 7: 验证**

```bash
python -m pytest tests/test_attach_resume.py tests/test_resume_nodes.py tests/test_resume_reparse.py tests/test_screening_trigger_on_upload.py tests/test_effect_idempotency_suite.py -q
```

预期输出：全部 passed，exit code 0。`test_effect_idempotency_suite.py` 中 `effect_persist_parse` 配方按 `resume_parse_version` 计数，不受本次重构影响，无需改动。

---

### Task 3: `detect_suspected_duplicates` 纯函数测试（函数本体已在 Task 2 建）

**Files:**

- Test: `tests/test_duplicates.py`

**Interfaces:**

- Produces: `normalize_name(text: str | None) -> str`、`CandidateRef`、`detect_suspected_duplicates(candidate, same_job_candidates) -> list[CandidateRef]`
- 约束：同岗位＋姓名规范化后相同 ⇒ 疑似；⛔ 无模型调用（tasks 2.3）

- [ ] **Step 1: 创建 `tests/test_duplicates.py`**

```python
from __future__ import annotations

from app.intake.duplicates import CandidateRef, detect_suspected_duplicates, normalize_name


def test_normalize_name_folds_width_and_whitespace():
    assert normalize_name("张 三") == normalize_name("张三")
    assert normalize_name("张　三") == normalize_name("张三")
    assert normalize_name("ＡＢＣ") == normalize_name("ABC")


def test_detects_same_job_same_normalized_name():
    cand = CandidateRef("c1", "张 三")
    refs = [CandidateRef("c1", "张三"), CandidateRef("c2", "张　三"), CandidateRef("c3", "李四")]
    result = detect_suspected_duplicates(cand, refs)
    assert [c.id for c in result] == ["c2"]


def test_does_not_match_different_name():
    cand = CandidateRef("c1", "张三")
    assert detect_suspected_duplicates(cand, [CandidateRef("c2", "张伟")]) == []


def test_never_returns_candidate_itself():
    cand = CandidateRef("c1", "张三")
    assert detect_suspected_duplicates(cand, [CandidateRef("c1", "张三")]) == []
```

- [ ] **Step 2: 验证**

```bash
python -m pytest tests/test_duplicates.py -q
```

预期输出：`4 passed`，exit code 0。

---

### Task 4: 数据模型——`candidate_merge_log`、`candidate.merged_into`、`application_stage_history.action`

**Files:**

- Modify: `app/storage/db.py`
- Test: `tests/test_candidate_merge_schema.py`（新建）
- Modify: `tests/test_db_migration.py`（补旧表 DDL 与漂移守卫名单）

**Interfaces:**

- Schema: `candidate.merged_into TEXT REFERENCES candidate(id)`（可空，走 `_ADDED_COLUMNS`）；`application_stage_history.action TEXT`（可空，走 `_ADDED_COLUMNS`）；新表 `candidate_merge_log`

> **偏离登记 D-CU2-1 / D-CU2-2（执行期实测，2026-10-10 Task 4 落地时登记）**
>
> **D-CU2-1（技术方案决策，可代）——`action` 不是本包首建，改为「放宽 CHECK」而非「裸 TEXT」。**
> 本计划写 Task 4 时（1001W/1001V 计划批）磁盘真身里该表还没有 `action`；但
> **interview-scheduling U2（1001R，2026-10-10 合并）已先落地**`action` + `detail_json`，
> 且 `action` 带**五值 CHECK**（`scheduled/rescheduled/cancelled/completed/no_show`）。
> Step 2 字面照抄会连那个 CHECK 与 `detail_json` 一起删掉——那是别的单元的已交付产物。
> 落地形态：`action` 的取值域以 `app/storage/db.py::STAGE_HISTORY_ACTIONS` 为真源，
> **追加** `closed_by_merge`（本包 Task 5 写它），SCHEMA / `_ADDED_COLUMNS` /
> `_rebuild_application_stage_history_action_check` 三处同源。⚠️ Step 4 要求的
> `("application_stage_history", "action", "TEXT")` 同样按此落地——⛔ 不是裸 `TEXT`。
> 老库两条路径：没跑过 1001R 的走加列一步到位；跑过 1001R 的（列已在、CHECK 五值）
> 由 `_rebuild_application_stage_history_action_check` 整表重建放宽（SQLite 改不了
> CHECK，同 `_rebuild_hr_account_role_check` 先例）。不重建的话 Task 5 的
> `closed_by_merge` 在服务器上当场 `IntegrityError`。U3 的 `source_corrected` 落地时
> 按同法在 `STAGE_HISTORY_ACTIONS` 加值（本次不加：不留无写入方的取值）。
>
> **D-CU2-2（实现细节）——Step 6 的测试代码有一处不可执行。**
> 计划文本里的 `with sqlite3.IntegrityError():` 在 Python 3 不是断言——异常类不支持
> 上下文管理器协议，会直接 `TypeError`。落地改写为 `pytest.raises(sqlite3.IntegrityError)`，
> 断言语义与计划意图一致。
>
> 另：Step 1（`candidate.merged_into`）与 Step 5 的漂移守卫登记已由 Task 2 随
> `candidate` 建表一并落地（见 `db.py` 的 `_ADDED_COLUMNS` 注释：⛔ 不重复追一行），
> 本任务不再重复改动。

- [ ] **Step 1: `candidate` 表加 `merged_into` 列**

在 `app/storage/db.py` 的 `candidate` 表定义里，把 `phone_hash TEXT,` 之后加一行：

```sql
CREATE TABLE IF NOT EXISTS candidate (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    phone_hash TEXT,
    merged_into TEXT REFERENCES candidate(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 2: `application_stage_history` 表加 `action` 列**

在 `application_stage_history` 表定义里，把 `actor TEXT,` 之后加一行：

```sql
CREATE TABLE IF NOT EXISTS application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    action TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 3: SCHEMA 新增 `candidate_merge_log` 表**

在 `candidate` 表定义与其唯一索引之后、`resume` 表之前插入：

```sql
-- 合并留痕（channel-resume-intake U2 tasks 2.4）。一行 = 一次合并：primary 保留、
-- secondary 被并入。secondary_snapshot 存被合并方合并前全部 application 的 JSON
-- 快照（撤销按它恢复）。merged_by / reason 的 CHECK 与 source_correction_log 同
-- 一手法：空操作人 / 空依据等于没留痕。unmerged_by/at 可空——未撤销为 NULL。
CREATE TABLE IF NOT EXISTS candidate_merge_log (
    id TEXT PRIMARY KEY NOT NULL,
    primary_id TEXT NOT NULL REFERENCES candidate(id),
    secondary_id TEXT NOT NULL REFERENCES candidate(id),
    reason TEXT NOT NULL CHECK (
        reason IS NOT NULL
        AND trim(reason, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    secondary_snapshot TEXT NOT NULL,
    merged_by TEXT NOT NULL CHECK (
        merged_by IS NOT NULL
        AND trim(merged_by, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    merged_at TEXT NOT NULL DEFAULT (datetime('now')),
    unmerged_by TEXT,
    unmerged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_merge_log_secondary
    ON candidate_merge_log (secondary_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_merge_log_active_secondary
    ON candidate_merge_log (secondary_id) WHERE unmerged_at IS NULL;
```

- [ ] **Step 4: `_ADDED_COLUMNS` 登记两列**

在 `app/storage/db.py` 的 `_ADDED_COLUMNS` 元组末尾追加：

```python
    # channel-resume-intake U2 tasks 2.4：合并标记（可空；未合并为 NULL）。
    ("candidate", "merged_into", "TEXT REFERENCES candidate(id)"),
    # channel-resume-intake U2 tasks 2.5：流转事实表的动作标签（closed_by_merge；
    # U3 将复用同一列写 source_corrected）。可空，普通阶段流转为 NULL。
    ("application_stage_history", "action", "TEXT"),
)
```

- [ ] **Step 5: 更新 `tests/test_db_migration.py` 的漂移守卫**

在 `_LEGACY_HR_ACCOUNT_DDL` 之后新增：

```python
# M2 U1 建表的 application_stage_history，不含 action——该列由
# channel-resume-intake U2 task 2.5 通过 _ADDED_COLUMNS 加入老库。
_LEGACY_APPLICATION_STAGE_HISTORY_DDL = """
CREATE TABLE application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
```

在 `_legacy_db` 里，`conn.executescript(_LEGACY_INTERVIEW_SESSION_DDL)` 之前加一行 `conn.executescript(_LEGACY_APPLICATION_STAGE_HISTORY_DDL)`。

把 `_DRIFT_GUARDED_TABLES` 改为：

```python
_DRIFT_GUARDED_TABLES = (
    "job_profile", "resume", "job_prep_config", "interview_session", "hr_account",
    "candidate", "application_stage_history",
)
```

- [ ] **Step 6: 创建 `tests/test_candidate_merge_schema.py`**

```python
from __future__ import annotations

import sqlite3

from app.storage.db import _ADDED_COLUMNS, _existing_columns, init_schema


def _conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def test_candidate_has_merged_into_and_history_has_action():
    conn = _conn()
    assert "merged_into" in _existing_columns(conn, "candidate")
    assert "action" in _existing_columns(conn, "application_stage_history")


def test_candidate_merge_log_table_exists():
    conn = _conn()
    cols = _existing_columns(conn, "candidate_merge_log")
    assert cols >= {
        "id", "primary_id", "secondary_id", "reason", "secondary_snapshot",
        "merged_by", "merged_at", "unmerged_by", "unmerged_at",
    }


def test_added_columns_registered():
    cols = {(t, c) for t, c, _ in _ADDED_COLUMNS}
    assert ("candidate", "merged_into") in cols
    assert ("application_stage_history", "action") in cols


def test_merge_log_rejects_empty_reason_and_actor():
    conn = _conn()
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
    with sqlite3.IntegrityError():
        conn.execute(
            "INSERT INTO candidate_merge_log (id, primary_id, secondary_id, reason, "
            "secondary_snapshot, merged_by) VALUES ('m1', 'c1', 'c2', '  ', '{}', 'alice')"
        )
```

- [ ] **Step 7: 验证**

```bash
python -m pytest tests/test_candidate_merge_schema.py tests/test_db_migration.py -q
```

预期输出：全部 passed（新增 4 条 + 既有迁移守卫），exit code 0。

---

### Task 5: `effect_merge_candidates` 节点（快照、双投递保留、closed_by_merge、幂等）

**Files:**

- Modify: `app/intake/merge.py`（追加合并节点与其私有辅助）
- Test: `tests/test_merge_unmerge.py`（合并用例）

**Interfaces:**

- Produces: `effect_merge_candidates(conn, *, thread_id, business_key, primary_id, secondary_id, reason, keep_application_per_job, merged_by) -> dict | None`
- 幂等键：`{primary_id}:effect_merge_candidates:{secondary_id}:{request_id}`

> **偏离登记 D-CU2-3 / D-CU2-3b / D-CU2-4 / D-CU2-5 / D-CU2-6（执行期实测，
> 2026-10-10～11 Task 5 落地时登记）**
>
> **D-CU2-3（测试夹具，不可代）——Step 2 的用例形参 `conn` 没有对应的夹具。**
> 同一份文本只定义了辅助函数 `_conn()`，`tests/conftest.py` 与全仓都没有名为 `conn`
> 的夹具；逐字照抄时 pytest 在**收集期**报 `fixture 'conn' not found`，计划自己写的
> 「预期 3 passed」不可达。落地：把 `_conn()` 原样改成同名夹具
> `@pytest.fixture def conn()`（内存库 + `init_schema` + 两个岗位），**四个用例体逐字
> 不动**。手法与 `tests/test_appeal_state_machine.py` 一致。
>
> **D-CU2-3b（连带，不可代）——夹具与 seed 辅助必须显式 `commit()`。**
> `app/storage/idempotency.py::idempotent_effect` 的既有语义是「被装饰函数抛异常 ⇒
> 回滚**本连接**」。夹具 seed 的行若留在未提交事务里，会被同一用例里**合法**的那次
> `pytest.raises(MergeValidationError)` 的回滚一并抹掉，紧接着的第二次 `_merge()`
> 报「候选人不存在」——那是夹具造成的假象，不是被测逻辑的问题（生产连接上，seed 行
> 的等价物早已由各自的 effect 提交）。落地：夹具与两个 seed 辅助各加 `conn.commit()`
> （同 `tests/test_appeal_state_machine.py` 的 `c.commit()`）。
>
> **D-CU2-4（顺序依赖，不可代）——Step 2 的 import 行引入了 Task 6 的符号。**
> 计划文本在文件头一行同时 import `effect_merge_candidates` 与
> `effect_unmerge_candidates`，但后者是**Task 6** 才落地的节点。照抄时本文件在
> import 期即 `ImportError`，而 `-k 'not unmerge'` 拦不住——那是收集中断，不是用例
> 失败，Step 3 拿不到「3 passed」。落地：Task 5 只 import 本任务真正产出的两个名字
> （`MergeValidationError`、`effect_merge_candidates`），文件头留待办注释；Task 6
> 追加撤销用例时把第三个名字补回同一行。
>
> **D-CU2-5（闸命令空转，不可代）——`-k 'not unmerge'` 会把本文件三个用例全部反选
> 掉。** pytest 的 `-k` 匹配的是**完整 node id（含文件名）**，而本文件名为
> `test_merge_unmerge.py`，含 `unmerge` 字样 ⇒ 实测输出 `3 deselected`、exit 0。
> 那是**空转的绿**：一条用例都没跑也会通过，比失败更危险。
> 落地：Task 5 的闸改用 `"$SDD_PYTHON" -m pytest tests/test_merge_unmerge.py -q`
> （此时文件里只有合并用例 ⇒ `4 passed`）；Task 6 落地后同一文件自然变成
> `6 passed`，无需再挑选用例。
>
> **D-CU2-6（漏列文件，不可代）——Task 5 的 Files 段漏了
> `tests/test_effect_idempotency_suite.py`，漏掉它仓级守卫当场变红。**
> 该文件用 AST 扫 `app/` 下全部 `@idempotent_effect` 字面量，要求**每个**新节点
> 同时进 `EFFECT_NODE_MANIFEST` 与 `build_recipes()`（各一条崩溃-恢复配方）。
> 只加节点不改这份清单时，实测
> `test_manifest_matches_the_source_tree` 直接失败：
> 「源码里新增了 effect 节点但没进本文件的清单：['effect_merge_candidates']」。
> 落地：在清单加 `effect_merge_candidates`，并加配方
> `_seed_merge_pair`（主候选人无同岗位投递 ⇒ 走合并常规路径，双投递分支留给
> Task 5 自己的用例）＋ `Recipe(thread_id=主候选人, business_key=f"{secondary}:req-4-4",
> count_business_rows=candidate_merge_log 行数)`。
> ⚠️ 业务事实口径为什么是 `candidate_merge_log` 而不是 `application`：合并只把
> `application.candidate_id` 改挂，**行数不变**，用它分不出"生效 / 未生效"。
> 这条配方顺带把 Global Constraints 第 1 条要求的「`effect_log` 条数与业务表行数
> 按 thread 恒等」变成了对**本节点**的可执行断言（两条通用协议 × 新节点 = 2 条新用例，
> 实测该文件 97 → 99 passed）。Task 6 的撤销节点落地时同样要在此处补一条。
>
> 注：Step 1 的节点代码逐字落地，⛔ 无偏离（含 seg2 Spec review F1 的「改挂前冻结主方投递
> 快照」——该修正已由 main `56c9395` 写入计划文本，并补第 4 条回归用例，故 D-CU2-5 的闸
> 实测 `4 passed`）。

- [ ] **Step 1: 在 `app/intake/merge.py` 追加合并逻辑**

```python
def _snapshot_secondary(conn: sqlite3.Connection, secondary_id: str) -> dict:
    apps = conn.execute(
        "SELECT id, job_id, resume_id, current_stage_id, status, kanban_state "
        "FROM application WHERE candidate_id = ? ORDER BY created_at, id",
        (secondary_id,),
    ).fetchall()
    return {
        "secondary_id": secondary_id,
        "applications": [
            {
                "id": a[0], "job_id": a[1], "resume_id": a[2],
                "current_stage_id": a[3], "status": a[4], "kanban_state": a[5],
            }
            for a in apps
        ],
    }


def _write_closed_by_merge(conn: sqlite3.Connection, application_id: str, merged_by: str) -> None:
    row = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    stage_id = row[0]
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action) "
        "VALUES (?, ?, ?, ?, 'human', ?, 'closed_by_merge')",
        (str(uuid.uuid4()), application_id, stage_id, stage_id, merged_by),
    )


@idempotent_effect("effect_merge_candidates")
def effect_merge_candidates(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    primary_id: str,
    secondary_id: str,
    reason: str,
    keep_application_per_job: dict[str, str],
    merged_by: str,
) -> dict:
    """把 secondary 并入 primary：写 secondary 快照 → secondary 的 application 全部
    改 candidate_id 到 primary → 同岗位双投递按 HR 选择保留一份、另一份写
    action='closed_by_merge' 流转事实不删 → secondary.merged_into = primary。
    幂等键 {primary_id}:effect_merge_candidates:{secondary_id}:{request_id}。"""
    if primary_id == secondary_id:
        raise MergeValidationError("主候选人不能等于被合并候选人")
    primary = conn.execute(
        "SELECT id, merged_into FROM candidate WHERE id = ?", (primary_id,)
    ).fetchone()
    secondary = conn.execute(
        "SELECT id, merged_into FROM candidate WHERE id = ?", (secondary_id,)
    ).fetchone()
    if primary is None or secondary is None:
        raise MergeValidationError("候选人不存在")
    if primary[1] is not None:
        raise MergeValidationError("主候选人已被合并")
    if secondary[1] is not None:
        raise MergeValidationError("被合并候选人已被合并")

    secondary_apps = conn.execute(
        "SELECT id, job_id FROM application WHERE candidate_id = ? ORDER BY created_at, id",
        (secondary_id,),
    ).fetchall()

    # 2026-10-11 修正（1001O seg2 Spec review F1）：**改挂前**冻结主方既有投递快照，
    # 校验与写关闭共用同一份——⛔ 不在改挂循环里现查：那会读到自己刚改挂的行，被合并方
    # 同岗位多份、主方没有时会凭空写 closed_by_merge（HR 未被提示、产生错误审计事实）。
    primary_apps_by_job: dict[str, str] = {}
    for row in conn.execute(
        "SELECT id, job_id FROM application WHERE candidate_id = ? ORDER BY created_at, id",
        (primary_id,),
    ).fetchall():
        primary_apps_by_job.setdefault(row[1], row[0])

    # 同岗位双投递：合并前必须由 HR 选保留哪份（spec「同岗位双投递」）。
    for application_id, job_id in secondary_apps:
        primary_open = primary_apps_by_job.get(job_id)
        if primary_open is None:
            continue
        keep_id = keep_application_per_job.get(job_id)
        if keep_id not in (application_id, primary_open):
            raise MergeValidationError(f"岗位 {job_id} 存在双投递，必须指定保留哪份投递")

    snapshot = _snapshot_secondary(conn, secondary_id)
    merge_log_id = str(uuid.uuid4())

    for application_id, job_id in secondary_apps:
        primary_open = primary_apps_by_job.get(job_id)
        conn.execute(
            "UPDATE application SET candidate_id = ? WHERE id = ?", (primary_id, application_id)
        )
        if primary_open is not None:
            keep_id = keep_application_per_job.get(job_id)
            loser_id = primary_open if keep_id == application_id else application_id
            _write_closed_by_merge(conn, loser_id, merged_by)

    conn.execute(
        "INSERT INTO candidate_merge_log "
        "(id, primary_id, secondary_id, reason, secondary_snapshot, merged_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (merge_log_id, primary_id, secondary_id, reason,
         json.dumps(snapshot, ensure_ascii=False), merged_by),
    )
    conn.execute(
        "UPDATE candidate SET merged_into = ? WHERE id = ?", (primary_id, secondary_id)
    )
    return {"merge_log_id": merge_log_id, "primary_id": primary_id, "secondary_id": secondary_id}
```

- [ ] **Step 2: 创建 `tests/test_merge_unmerge.py`（合并用例）**

```python
from __future__ import annotations

import json
import sqlite3

import pytest

from app.intake.merge import MergeValidationError, effect_merge_candidates, effect_unmerge_candidates
from app.storage.db import init_schema


def _conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO job (id, title) VALUES ('j2', '软件工程师')")
    return c


def _seed_candidate(conn, cid, name):
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, ?)", (cid, name))


def _seed_resume_application(conn, *, resume_id, candidate_id, job_id):
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', ?, ?, 'alice')",
        (resume_id, job_id, resume_id + ".pdf", "h-" + resume_id),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        ("app-" + resume_id, candidate_id, job_id, resume_id),
    )


def _merge(conn, *, primary_id, secondary_id, reason="电话确认同一人", keep=None, request_id="req-1"):
    return effect_merge_candidates(
        conn,
        thread_id=primary_id,
        business_key=f"{secondary_id}:{request_id}",
        primary_id=primary_id,
        secondary_id=secondary_id,
        reason=reason,
        keep_application_per_job=keep or {},
        merged_by="alice",
    )


def test_merge_reassigns_applications_and_sets_merged_into(conn):
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    _seed_resume_application(conn, resume_id="r2", candidate_id="s1", job_id="j2")

    result = _merge(conn, primary_id="p1", secondary_id="s1")

    assert conn.execute(
        "SELECT merged_into FROM candidate WHERE id = 's1'"
    ).fetchone()[0] == "p1"
    assert conn.execute(
        "SELECT COUNT(*) FROM application WHERE candidate_id = 'p1'"
    ).fetchone()[0] == 2
    row = conn.execute(
        "SELECT secondary_snapshot FROM candidate_merge_log WHERE id = ?",
        (result["merge_log_id"],),
    ).fetchone()
    snapshot = json.loads(row[0])
    assert {a["resume_id"] for a in snapshot["applications"]} == {"r1", "r2"}


def test_same_job_double_application_requires_keep_choice(conn):
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="p1", job_id="j1")
    _seed_resume_application(conn, resume_id="r2", candidate_id="s1", job_id="j1")

    with pytest.raises(MergeValidationError):
        _merge(conn, primary_id="p1", secondary_id="s1", keep={})

    result = _merge(conn, primary_id="p1", secondary_id="s1", keep={"j1": "app-r1"})
    closed = conn.execute(
        "SELECT action FROM application_stage_history WHERE application_id = 'app-r2' "
        "AND action = 'closed_by_merge'"
    ).fetchone()
    assert closed is not None
    assert result["merge_log_id"]


def test_secondary_multi_same_job_without_primary_open_stays_open(conn):
    """主方同岗位没有投递、被合并方同岗位两份：两份都改挂，⛔ 不产生 closed_by_merge。

    2026-10-11 修正（1001O seg2 Spec review F1）：旧实现改挂后重查主方投递会读到
    自己刚改挂的行，凭空写 closed_by_merge（HR 未被提示）。"""
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    _seed_resume_application(conn, resume_id="r2", candidate_id="s1", job_id="j1")

    result = _merge(conn, primary_id="p1", secondary_id="s1", keep={})

    rows = conn.execute(
        "SELECT id, candidate_id FROM application WHERE job_id = 'j1' ORDER BY id"
    ).fetchall()
    assert rows == [("app-r1", "p1"), ("app-r2", "p1")]
    closed = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action = 'closed_by_merge'"
    ).fetchone()[0]
    assert closed == 0
    assert result["merge_log_id"]


def test_merge_replay_is_short_circuited(conn):
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    first = _merge(conn, primary_id="p1", secondary_id="s1", request_id="req-1")
    second = _merge(conn, primary_id="p1", secondary_id="s1", request_id="req-1")
    assert first is not None and second is None
    assert conn.execute("SELECT COUNT(*) FROM candidate_merge_log").fetchone()[0] == 1
```

- [ ] **Step 3: 验证**

```bash
python -m pytest tests/test_merge_unmerge.py -q -k 'not unmerge'
```

预期输出：`3 passed`，exit code 0。

---

### Task 6: `effect_unmerge_candidates` 节点（按快照恢复、三段测试、幂等）

**Files:**

- Modify: `app/intake/merge.py`（追加撤销节点）
- Test: `tests/test_merge_unmerge.py`（撤销用例）

**Interfaces:**

- Produces: `effect_unmerge_candidates(conn, *, thread_id, business_key, merge_log_id, unmerged_by) -> dict | None`
- 幂等键：`{merge_log_id}:effect_unmerge_candidates:undo`

- [ ] **Step 1: 在 `app/intake/merge.py` 追加撤销逻辑**

```python
@idempotent_effect("effect_unmerge_candidates")
def effect_unmerge_candidates(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    merge_log_id: str,
    unmerged_by: str,
) -> dict:
    """按快照恢复 secondary 原有 application 归属；合并期间 primary 新增记录保留在
    primary；写 unmerged_by/at 并清 secondary.merged_into。
    幂等键 {merge_log_id}:effect_unmerge_candidates:undo。"""
    row = conn.execute(
        "SELECT primary_id, secondary_id, secondary_snapshot, unmerged_at "
        "FROM candidate_merge_log WHERE id = ?",
        (merge_log_id,),
    ).fetchone()
    if row is None:
        raise MergeValidationError("合并留痕不存在")
    if row[3] is not None:
        raise MergeValidationError("该合并已被撤销")

    primary_id, secondary_id, snapshot_json = row[0], row[1], row[2]
    snapshot = json.loads(snapshot_json)
    for app in snapshot["applications"]:
        conn.execute(
            "UPDATE application SET candidate_id = ?, current_stage_id = ?, "
            "status = ?, kanban_state = ? WHERE id = ?",
            (secondary_id, app["current_stage_id"], app["status"], app["kanban_state"], app["id"]),
        )

    conn.execute("UPDATE candidate SET merged_into = NULL WHERE id = ?", (secondary_id,))
    conn.execute(
        "UPDATE candidate_merge_log SET unmerged_by = ?, unmerged_at = datetime('now') "
        "WHERE id = ?",
        (unmerged_by, merge_log_id),
    )
    return {"merge_log_id": merge_log_id, "primary_id": primary_id, "secondary_id": secondary_id}
```

- [ ] **Step 2: 追加撤销用例到 `tests/test_merge_unmerge.py`**

```python
def test_unmerge_restores_secondary_and_keeps_new_primary_records(conn):
    """合并→新增→撤销三段：撤销只恢复 secondary 原有归属，primary 新增记录保留。"""
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    result = _merge(conn, primary_id="p1", secondary_id="s1")

    # 合并期间 primary 新增一份投递（不在快照里）
    _seed_resume_application(conn, resume_id="r-new", candidate_id="p1", job_id="j2")

    effect_unmerge_candidates(
        conn,
        thread_id=result["merge_log_id"],
        business_key="undo",
        merge_log_id=result["merge_log_id"],
        unmerged_by="alice",
    )

    assert conn.execute(
        "SELECT merged_into FROM candidate WHERE id = 's1'"
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT candidate_id FROM application WHERE resume_id = 'r1'"
    ).fetchone()[0] == "s1"
    assert conn.execute(
        "SELECT candidate_id FROM application WHERE resume_id = 'r-new'"
    ).fetchone()[0] == "p1"
    log = conn.execute(
        "SELECT unmerged_by, unmerged_at FROM candidate_merge_log WHERE id = ?",
        (result["merge_log_id"],),
    ).fetchone()
    assert log[0] == "alice"
    assert log[1] is not None


def test_unmerge_replay_is_short_circuited_and_second_is_rejected(conn):
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    result = _merge(conn, primary_id="p1", secondary_id="s1")

    first = effect_unmerge_candidates(
        conn, thread_id=result["merge_log_id"], business_key="undo",
        merge_log_id=result["merge_log_id"], unmerged_by="alice",
    )
    second = effect_unmerge_candidates(
        conn, thread_id=result["merge_log_id"], business_key="undo",
        merge_log_id=result["merge_log_id"], unmerged_by="alice",
    )
    assert first is not None and second is None
```

- [ ] **Step 3: 验证**

```bash
python -m pytest tests/test_merge_unmerge.py -q
```

预期输出：`5 passed`，exit code 0。

---

### Task 7: 候选人列表页、合并页与 API（来源列、疑似重复、撤销按钮）

**Files:**

- Modify: `app/web/server.py`（路由与请求模型）
- Create: `app/web/static/candidate_list.html`
- Create: `app/web/static/candidate_merge.html`
- Test: `tests/test_candidates_api.py`

**Interfaces:**

- Produces: `GET /candidates`、`GET /api/candidates`、`GET /candidates/{candidate_id}/merge`、`GET /api/candidates/{candidate_id}/merge`、`POST /api/candidates/merge`、`POST /api/candidates/merge/{merge_log_id}/unmerge`

- [ ] **Step 1: 在 `app/web/server.py` 顶部 import 区追加本任务所需导入**

```python
from app.intake.duplicates import CandidateRef, detect_suspected_duplicates
from app.intake.merge import (
    MergeValidationError,
    effect_merge_candidates,
    effect_unmerge_candidates,
)
from app.storage.source import candidate_source
```

- [ ] **Step 2: 在 `app/web/server.py` 加请求模型**

在 `class LoginRequest(BaseModel):` 附近新增：

```python
class MergeCandidatesRequest(BaseModel):
    primary_id: str
    secondary_id: str
    reason: str
    request_id: str
    keep_application_per_job: dict[str, str] = {}
```

- [ ] **Step 3: 在 `create_app` 内加候选路由（放在 `correct_resume_source` 路由之后）**

```python
    def _require_candidate(candidate_id: str) -> tuple:
        row = conn.execute(
            "SELECT id, name, phone_hash, merged_into FROM candidate WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        return row

    def _same_job_candidate_refs(candidate_id: str) -> list[CandidateRef]:
        rows = conn.execute(
            "SELECT DISTINCT c.id, c.name FROM application a "
            "JOIN application b ON b.job_id = a.job_id "
            "JOIN candidate c ON c.id = b.candidate_id "
            "WHERE a.candidate_id = ? AND b.candidate_id != ? AND c.merged_into IS NULL",
            (candidate_id, candidate_id),
        ).fetchall()
        return [CandidateRef(id=r[0], name=r[1]) for r in rows]

    def _conflict_jobs(candidate_id: str, other_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT a.job_id, a.id, b.id FROM application a "
            "JOIN application b ON b.job_id = a.job_id "
            "WHERE a.candidate_id = ? AND b.candidate_id = ?",
            (candidate_id, other_id),
        ).fetchall()
        return [{"job_id": r[0], "applications": [r[1], r[2]]} for r in rows]

    @router.get("/candidates")
    def candidate_list_page():
        return _render_static_page("candidate_list.html", root_path)

    @router.get("/api/candidates")
    def list_candidates(request: Request) -> dict:
        rows = conn.execute(
            "SELECT id, name, merged_into FROM candidate ORDER BY created_at DESC, id DESC"
        ).fetchall()
        items = []
        for cid, name, merged_into in rows:
            suspected = detect_suspected_duplicates(
                CandidateRef(cid, name), _same_job_candidate_refs(cid)
            )
            resume_count = conn.execute(
                "SELECT COUNT(*) FROM application WHERE candidate_id = ?", (cid,)
            ).fetchone()[0]
            items.append({
                "candidate_id": cid,
                "name": name,
                "source": candidate_source(conn, cid),
                "resume_count": resume_count,
                "merged_into": merged_into,
                "suspected_duplicate_ids": [s.id for s in suspected],
            })
        return {"candidates": items}

    @router.get("/candidates/{candidate_id}/merge")
    def candidate_merge_page(candidate_id: str):
        return _render_static_page("candidate_merge.html", root_path)

    @router.get("/api/candidates/{candidate_id}/merge")
    def candidate_merge_data(request: Request, candidate_id: str) -> dict:
        cand = _require_candidate(candidate_id)
        suspected = detect_suspected_duplicates(
            CandidateRef(candidate_id, cand[1]), _same_job_candidate_refs(candidate_id)
        )
        suspects = []
        for s in suspected:
            suspects.append({
                "candidate_id": s.id,
                "name": s.name,
                "source": candidate_source(conn, s.id),
                "conflict_jobs": _conflict_jobs(candidate_id, s.id),
            })
        history = conn.execute(
            "SELECT id, primary_id, secondary_id, reason, merged_at, unmerged_at "
            "FROM candidate_merge_log WHERE primary_id = ? OR secondary_id = ? "
            "ORDER BY merged_at DESC",
            (candidate_id, candidate_id),
        ).fetchall()
        return {
            "candidate": {
                "candidate_id": cand[0], "name": cand[1],
                "source": candidate_source(conn, cand[0]), "merged_into": cand[3],
            },
            "suspected_duplicates": suspects,
            "merge_history": [
                {"merge_log_id": h[0], "primary_id": h[1], "secondary_id": h[2],
                 "reason": h[3], "merged_at": h[4], "unmerged_at": h[5]}
                for h in history
            ],
        }

    @router.post("/api/candidates/merge")
    def merge_candidates(request: Request, req: MergeCandidatesRequest):
        if not req.reason or not req.reason.strip():
            raise HTTPException(status_code=422, detail="合并依据不能为空")
        merged_by = reviewer_of(request)
        try:
            result = effect_merge_candidates(
                conn,
                thread_id=req.primary_id,
                business_key=f"{req.secondary_id}:{req.request_id}",
                primary_id=req.primary_id,
                secondary_id=req.secondary_id,
                reason=req.reason,
                keep_application_per_job=req.keep_application_per_job,
                merged_by=merged_by,
            )
        except MergeValidationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result

    @router.post("/api/candidates/merge/{merge_log_id}/unmerge")
    def unmerge_candidates(request: Request, merge_log_id: str):
        try:
            result = effect_unmerge_candidates(
                conn,
                thread_id=merge_log_id,
                business_key="undo",
                merge_log_id=merge_log_id,
                unmerged_by=reviewer_of(request),
            )
        except MergeValidationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result
```

- [ ] **Step 4: 创建 `app/web/static/candidate_list.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261010">
  <title>候选人列表 · 卓品智能招聘助手</title>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">HR 工作台 · 候选人列表</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">候选人列表</h1>
    <div class="card">
      <table id="candidate-table" class="data-table">
        <thead>
          <tr><th>候选人</th><th>来源</th><th>简历数</th><th>疑似重复</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody id="candidate-body"></tbody>
      </table>
      <p id="empty-hint" class="empty-hint" style="display:none">暂无候选人。</p>
    </div>
    <p id="error" style="color:red"></p>
  </main>

  <script>
    const SOURCE_LABELS = {
      boss: "Boss直聘", liepin: "猎聘", "51job": "前程无忧",
      zhaopin: "智联", referral: "内推", other: "其他", unknown: "未知",
    };

    async function loadCandidates() {
      const resp = await fetch("api/candidates");
      if (resp.status === 401) { window.location.href = "login"; return; }
      if (!resp.ok) { document.getElementById("error").textContent = "加载失败"; return; }
      const body = await resp.json();
      const tbody = document.getElementById("candidate-body");
      tbody.innerHTML = "";
      if (body.candidates.length === 0) {
        document.getElementById("empty-hint").style.display = "";
        return;
      }
      for (const item of body.candidates) {
        const tr = document.createElement("tr");
        const suspected = item.suspected_duplicate_ids.length > 0 ? "疑似重复" : "-";
        const merged = item.merged_into ? "已合并" : "正常";
        [item.name, SOURCE_LABELS[item.source] || item.source, String(item.resume_count),
         suspected, merged].forEach((text) => {
          const td = document.createElement("td");
          td.textContent = text;
          tr.appendChild(td);
        });
        const actionTd = document.createElement("td");
        const link = document.createElement("a");
        link.href = `candidates/${item.candidate_id}/merge`;
        link.textContent = "合并";
        link.className = "link";
        actionTd.appendChild(link);
        tr.appendChild(actionTd);
        tbody.appendChild(tr);
      }
    }

    loadCandidates();
  </script>
</body>
</html>
```

- [ ] **Step 5: 创建 `app/web/static/candidate_merge.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261010">
  <title>合并候选人 · 卓品智能招聘助手</title>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">HR 工作台 · 合并候选人</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">合并候选人</h1>
    <div class="card"><h2 id="candidate-title"></h2></div>
    <div class="card">
      <h2>疑似重复</h2>
      <div id="suspects"></div>
    </div>
    <div class="card">
      <h2>合并历史</h2>
      <div id="history"></div>
    </div>
    <p id="error" style="color:red"></p>
  </main>

  <script>
    const SOURCE_LABELS = {
      boss: "Boss直聘", liepin: "猎聘", "51job": "前程无忧",
      zhaopin: "智联", referral: "内推", other: "其他", unknown: "未知",
    };

    function candidateIdFromPath() {
      const m = window.location.pathname.match(/\/candidates\/([^/]+)\/merge\/?$/);
      return m ? m[1] : null;
    }

    function renderSuspects(candidate, suspects) {
      const box = document.getElementById("suspects");
      box.innerHTML = "";
      if (suspects.length === 0) {
        box.textContent = "该候选人没有疑似重复。";
        return;
      }
      for (const s of suspects) {
        const row = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = `${s.name}（${SOURCE_LABELS[s.source] || s.source}）`;
        row.appendChild(title);

        const reason = document.createElement("input");
        reason.placeholder = "合并依据（必填）";
        row.appendChild(reason);

        const keepSelects = document.createElement("div");
        for (const conflict of s.conflict_jobs) {
          const label = document.createElement("label");
          label.textContent = `岗位 ${conflict.job_id} 双投递，保留哪份：`;
          const select = document.createElement("select");
          for (const appId of conflict.applications) {
            const opt = document.createElement("option");
            opt.value = appId;
            opt.textContent = appId;
            select.appendChild(opt);
          }
          select.dataset.jobId = conflict.job_id;
          label.appendChild(select);
          keepSelects.appendChild(label);
          keepSelects.appendChild(document.createElement("br"));
        }
        row.appendChild(keepSelects);

        const submit = document.createElement("button");
        submit.textContent = `并入当前候选人（${candidate.name} 为主）`;
        submit.onclick = () => {
          const keep = {};
          for (const select of keepSelects.querySelectorAll("select")) {
            keep[select.dataset.jobId] = select.value;
          }
          doMerge(candidate.candidate_id, s.candidate_id, reason.value, keep);
        };
        row.appendChild(submit);
        box.appendChild(row);
        box.appendChild(document.createElement("hr"));
      }
    }

    function renderHistory(history) {
      const box = document.getElementById("history");
      box.innerHTML = "";
      if (history.length === 0) { box.textContent = "暂无合并记录。"; return; }
      for (const h of history) {
        const row = document.createElement("div");
        row.textContent = `${h.merged_at}：${h.secondary_id} 并入 ${h.primary_id}（${h.reason}）`;
        if (!h.unmerged_at) {
          const undo = document.createElement("button");
          undo.textContent = "撤销";
          undo.onclick = () => doUnmerge(h.merge_log_id);
          row.appendChild(undo);
        } else {
          row.textContent += `（已于 ${h.unmerged_at} 撤销）`;
        }
        box.appendChild(row);
      }
    }

    async function doMerge(primaryId, secondaryId, reason, keep) {
      const resp = await fetch("api/candidates/merge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          primary_id: primaryId, secondary_id: secondaryId, reason,
          request_id: crypto.randomUUID(), keep_application_per_job: keep,
        }),
      });
      if (!resp.ok) {
        document.getElementById("error").textContent = "合并失败：" + (await resp.json()).detail;
        return;
      }
      load();
    }

    async function doUnmerge(mergeLogId) {
      const resp = await fetch(`api/candidates/merge/${mergeLogId}/unmerge`, { method: "POST" });
      if (!resp.ok) {
        document.getElementById("error").textContent = "撤销失败：" + (await resp.json()).detail;
        return;
      }
      load();
    }

    async function load() {
      const candidateId = candidateIdFromPath();
      if (!candidateId) { document.getElementById("error").textContent = "无法识别候选人标识"; return; }
      const resp = await fetch(`api/candidates/${candidateId}/merge`);
      if (resp.status === 401) { window.location.href = "login"; return; }
      if (!resp.ok) { document.getElementById("error").textContent = "加载失败"; return; }
      const body = await resp.json();
      document.getElementById("candidate-title").textContent =
        `${body.candidate.name}（${SOURCE_LABELS[body.candidate.source] || body.candidate.source}）`;
      renderSuspects(body.candidate, body.suspected_duplicates);
      renderHistory(body.merge_history);
    }

    load();
  </script>
</body>
</html>
```

- [ ] **Step 6: 创建 `tests/test_candidates_api.py`**

```python
from __future__ import annotations

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.source import candidate_source


def _client_and_candidates(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '张 三')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
                 "uploaded_by, source) VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h1', 'alice', 'boss')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
                 "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
                 "uploaded_by, source) VALUES ('r2', 'j1', 'synthetic', 'b.pdf', 'h2', 'alice', 'liepin')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
                 "VALUES ('a2', 'c2', 'j1', 'r2', 'initial')")
    conn.commit()
    return client, conn


def test_candidate_list_shows_source_and_suspected_flag(make_test_client):
    client, conn = _client_and_candidates(make_test_client)
    body = client.get("/api/candidates").json()
    by_id = {c["candidate_id"]: c for c in body["candidates"]}
    assert by_id["c1"]["source"] == "boss"
    assert by_id["c1"]["suspected_duplicate_ids"] == ["c2"]


def test_merge_endpoint_reassigns_and_can_be_unmerged(make_test_client):
    client, conn = _client_and_candidates(make_test_client)
    resp = client.post("/api/candidates/merge", json={
        "primary_id": "c1", "secondary_id": "c2", "reason": "电话确认同一人",
        "request_id": "req-1", "keep_application_per_job": {"j1": "a1"},
    })
    assert resp.status_code == 200
    assert candidate_source(conn, "c1") == "boss"
    assert conn.execute(
        "SELECT merged_into FROM candidate WHERE id = 'c2'"
    ).fetchone()[0] == "c1"

    merge_log_id = resp.json()["merge_log_id"]
    undo = client.post(f"/api/candidates/merge/{merge_log_id}/unmerge")
    assert undo.status_code == 200
    assert conn.execute(
        "SELECT candidate_id FROM application WHERE resume_id = 'r2'"
    ).fetchone()[0] == "c2"


def test_merge_rejects_empty_reason(make_test_client):
    client, _conn = _client_and_candidates(make_test_client)
    resp = client.post("/api/candidates/merge", json={
        "primary_id": "c1", "secondary_id": "c2", "reason": "  ", "request_id": "req-2",
    })
    assert resp.status_code == 422


def test_candidate_pages_render(make_test_client):
    client, _conn = _client_and_candidates(make_test_client)
    assert client.get("/candidates").status_code == 200
    assert client.get("/candidates/c1/merge").status_code == 200
```

- [ ] **Step 7: 验证**

```bash
python -m pytest tests/test_candidates_api.py tests/test_static_frontend.py -q
```

预期输出：全部 passed，exit code 0。

---

### Task 8: U2 端到端（两份同手机号不同来源自动挂接 → 无手机号同名同岗疑似重复 → 合并 → 撤销）

**Files:**

- Test: `tests/test_channel_dedup_e2e.py`

**Interfaces:**

- 覆盖 tasks 2.8 全链路：同手机号不同来源自动挂同一候选人 → 无手机号同名同岗疑似重复 → HR 合并 → 撤销 → 归属恢复；`candidate_merge_log` 条数守恒

- [ ] **Step 1: 创建 `tests/test_channel_dedup_e2e.py`**

```python
from __future__ import annotations

import io
import zipfile

import docx

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _stub_compute_parse(monkeypatch, name: str):
    import app.web.server as server_mod
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, TextField,
    )

    def _fake(gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value=name, confidence=0.95),
            years_of_experience=NumberField(not_mentioned=True, value=None, confidence=1.0),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1, run_id="run-id")

    monkeypatch.setattr(server_mod, "compute_parse", _fake)


def _client_and_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def _upload_zip(client, entries: dict[str, list[str]]) -> list[dict]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, paragraphs in entries.items():
            zf.writestr(name, _docx_bytes(paragraphs))
    files = [("files", ("bundle.zip", buf.getvalue(), "application/zip"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    assert resp.status_code == 200
    return resp.json()["results"]


def _upload_single(client, file_name: str, paragraphs: list[str]) -> dict:
    files = [("files", (file_name, _docx_bytes(paragraphs),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    assert resp.status_code == 200
    return resp.json()["results"][0]


def _paragraphs(phone: bool, tag: str) -> list[str]:
    head = "张三 13800138000" if phone else "张三"
    return [head, f"工作经历：{tag}，某某公司嵌入式工程师，负责车身控制器固件开发，" * 3]


def test_u2_e2e_attach_merge_unmerge(make_test_client, monkeypatch):
    client, conn = _client_and_job(make_test_client)
    _stub_compute_parse(monkeypatch, name="张三")

    # 两份不同来源、同手机号 ⇒ 自动挂接同一候选人
    results = _upload_zip(client, {
        "boss-张三.docx": _paragraphs(phone=True, tag="boss直聘导出"),
        "liepin-张三.docx": _paragraphs(phone=True, tag="猎聘导出"),
    })
    assert sum(1 for r in results if r["status"] == "accepted") == 2
    assert conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1

    # 无手机号、同名、同岗位 → 新建候选人并被标疑似重复
    nophone = _upload_single(client, "boss-张三-无手机号.docx", _paragraphs(phone=False, tag="无手机号"))
    assert nophone["parse_status"] == "parsed"
    assert conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 2

    body = client.get("/api/candidates").json()
    by_id = {c["candidate_id"]: c for c in body["candidates"]}
    # 手机号候选人有 2 份投递；无手机号候选人有 1 份
    primary_id = next(c["candidate_id"] for c in body["candidates"] if c["resume_count"] == 2)
    secondary_id = next(c["candidate_id"] for c in body["candidates"] if c["resume_count"] == 1)
    assert primary_id in by_id[secondary_id]["suspected_duplicate_ids"]

    # 同岗位双投递：HR 选择保留无手机号候选人的那份投递
    secondary_app = conn.execute(
        "SELECT id FROM application WHERE candidate_id = ? AND job_id = 'j1'", (secondary_id,)
    ).fetchone()[0]
    merge_resp = client.post("/api/candidates/merge", json={
        "primary_id": primary_id, "secondary_id": secondary_id,
        "reason": "电话确认同一人", "request_id": "e2e-req-1",
        "keep_application_per_job": {"j1": secondary_app},
    })
    assert merge_resp.status_code == 200
    merge_log_id = merge_resp.json()["merge_log_id"]
    log_count_after_merge = conn.execute("SELECT COUNT(*) FROM candidate_merge_log").fetchone()[0]

    undo = client.post(f"/api/candidates/merge/{merge_log_id}/unmerge")
    assert undo.status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM candidate_merge_log").fetchone()[0] == log_count_after_merge
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate WHERE id = ? AND merged_into IS NULL", (secondary_id,)
    ).fetchone()[0] == 1
    # 撤销后无手机号候选人的投递归还
    assert conn.execute(
        "SELECT COUNT(*) FROM application WHERE candidate_id = ?", (secondary_id,)
    ).fetchone()[0] == 1
```

- [ ] **Step 2: 验证**

```bash
python -m pytest tests/test_channel_dedup_e2e.py -q
```

预期输出：`1 passed`，exit code 0。

---

## 收口自检（run-build 前）

```bash
python -m pytest \
  tests/test_phone_hash.py \
  tests/test_duplicates.py \
  tests/test_attach_resume.py \
  tests/test_candidate_merge_schema.py \
  tests/test_merge_unmerge.py \
  tests/test_candidates_api.py \
  tests/test_channel_dedup_e2e.py \
  tests/test_resume_nodes.py \
  tests/test_resume_reparse.py \
  tests/test_screening_trigger_on_upload.py \
  tests/test_effect_idempotency_suite.py \
  tests/test_db_migration.py \
  tests/test_static_frontend.py -q
```

预期：全部 passed，exit code 0。

**下一步：** 用 `run-build` 执行本计划（`scripts/codex_sdd_runner.py` 按 `### Task N:` 抽取任务、两阶段 review）。本计划含全部实现与测试代码，run-build 会先提取到临时目录做端到端提取验证（spec-to-plan 第 6 节的动作后移到执行期，因为本会话边界禁止写 `app/**`/`tests/**`）。
