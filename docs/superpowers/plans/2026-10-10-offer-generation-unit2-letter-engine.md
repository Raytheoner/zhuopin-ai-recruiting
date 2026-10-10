# [Mac]1001X Offer 包 U2 文书引擎 实现计划

> 派发：Codex·`[Mac]1001G` ｜ 执行引擎：Codex ｜ 分支：`lane-1001x-offer-unit2-plan`
> 输出：本文件（单文件）。⛔ 只产出这一份计划，不写代码、不建分支、不自行 git 提交。

## 零、交付单元范围

本计划实现 `openspec/changes/offer-generation/tasks.md` **第 2 章「U2 文书引擎
（Offer／拒信共用）」**（tasks 2.1–2.8）：模板维护、生成纯函数、标识、编辑与
「标记为人工撰写」、docx 导出、查看留痕、文书页、`.51` 冒烟。前置＝U1（已合 main）。

**输入（spec 真源，按需列出「由」）**：

- `openspec/changes/offer-generation/specs/candidate-letter-engine/spec.md` —— 主输入。
  其 `文书模板由 HR 维护并版本化` / `按投递事实生成草稿并带 AI 标识` /
  `编辑不去标，显式标记人工撰写才去标` / `导出 docx` / `文书草稿的查看留痕`
  五条 Requirement 全部落到本单元。
- `openspec/changes/offer-generation/specs/candidate-letter-outbound/spec.md` —— 由
  `默认交付形态是 HR 自行发送` 的「导出 docx」半边（`candidate_letter.sent_status`
  落 `exported`、`letter_access_log(export)`）。⚠️ 其「复制文案 + 回填已发出」「系统
  外发一律经既有门禁」「拒信发送时机由 HR 决定」三条属 U4（tasks 4.2–4.4），不在本单元。
- `openspec/changes/offer-generation/design.md` —— 取 Decisions D1（范围＝文书生成）、
  D2（薪资不入库，docx 薪资处留空）、D3（拒信并入本包、共用引擎）、D7（AI 标识落本包
  自己的表）、D9（U2 前置＝U1）。

⛔ `tasks.md` 只用于确认第 2 章边界（2.1–2.8），**不作为计划输入**（粒度差一个数量级）。

**上游 U1 已合 main 的真实实现（接口核对参照，⚠️ 与磁盘真身一致）**：

- `app/storage/db.py`：`letter_template(kind, version)`、`candidate_letter(application_id,
  kind, version, template_version, body, ai_generated, authorship_*, analysis_run_id,
  sent_status, sent_channel, created_by/at)`、`offer(application_id UNIQUE, job_id,
  department, start_date, report_to, note, status, approval_round, created_by, updated_by)`
  三表已在 `SCHEMA` 中；`letter_access_log` 表也在。
- `app/storage/offer_approval_chain.py`：`put_approval_chain` 的「同内容重复 PUT 不产生新
  版本」幂等语义与「校验后写入 → 异常回滚 → commit」的共享单连接写法，是本单元模板接口
  的同款参照。

**前向依赖与已登记风险（⛔ 不阻塞本单元）**：

1. **`application.status` 词表不一致（登记，延续 U1）**：`design.md`/`tasks.md` 写
   `ongoing/hired/refused`，`app/storage/db.py` 实态是 `active/rejected/withdrawn`。
   本单元**不触碰 `application.status`**（那是 U3/U5 的职责）；本单元只读 `offer.status`
   与 `rejection_record` 存在性作为生成前置。
2. **OQ1/OQ3 待专员**：Offer/拒信模板真值未到，本单元用占位模板 v1（`_seed_letter_templates`），
   回件到后只换内容不改代码。
3. **`offer` 表无任何薪资类列（已由 U1 落地）**：本单元不新增薪资列；docx 导出在薪资
   位置留空段落，由 HR 手填。

## Global Constraints

以下条目逐字取自 `CLAUDE.md`，`run-build` 的两阶段 reviewer 会把本节当注意力透镜。

### 工程铁律

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须
   独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加
   唯一索引。**幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上
   不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的
   `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名
   区分 `compute_*` / `effect_*`。

3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 +
   rubric 快照 + 原始响应。

5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。供应商不提供带版本号快照时
   （如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的
   `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。

### 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：
  `rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **模型全部走境内**，简历数据不出境。
- **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。

### 本包合规红线（Offer 包专项，逐字）

- **薪资等敏感字段不入库**：`offer` 表只存岗位 / 部门 / 入职日 / 汇报对象 / 备注 / 审批状态 /
  答复，⛔ 不设薪资、股权、签字费、津贴等任何报酬列；系统 ⛔ 不在任何表、日志、留痕中持久化
  报酬信息（`offer-record-and-approval` spec「Offer 记录的字段边界」，design D2）。docx 导出在
  薪资位置留空段落，由 HR 手填；`LetterFacts` schema 层不设任何薪资键。

## 机器判据（交付前自查）

```bash
test -n "$(ls docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md
grep -q 'Global Constraints' docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md
grep -q 'compute_letter_draft' docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md
```

本计划含 `### Task 1`–`### Task 8`，共 8 个任务。

---

### Task 1: L3 纯函数 `app/agents/letter_drafter.py`（tasks 2.2）

**文件**：`app/agents/letter_drafter.py`（新建）

```python
"""Offer/拒信共用的文书草稿 L3 Agent（offer-generation U2 tasks 2.2）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库是
app/graph/letter_nodes.py 的 effect_* 节点的事，facts/模板组装是同文件
compute_* 的事——本模块 ⛔ 不 import app.storage / app.graph（模块内 grep 无
storage 写入，tests/test_letter_drafter.py 有静态断言守着）。

AI 生成标识与 M1 JD 同串：复用 app/agents/jd_agent.enforce_ai_label（其
AI_LABEL_TEMPLATE 是唯一真源）。这样 U4 门禁 gate.py 的 AI_LABEL_PREFIX in body
判定与 docx 页眉回读都用同一串，不会出现「拒信有标识但门禁读不出来」的错配。
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from app.agents.jd_agent import enforce_ai_label
from app.llm.gateway import LLMGateway

LETTER_PROMPT_VERSION_TMPL = "letter-{kind}-v1"

_LETTER_SYSTEM_PROMPT = (
    "你是招聘文书助手。基于给定的文书模板与投递事实生成一份完整的 Offer/拒信正文。\n"
    "规则：\n"
    "1. 模板里的占位符（{candidate_name}/{job_title}/{department}/{start_date}/"
    "{report_to}）MUST 用 facts 里的对应字段填充，保持模板的段落结构、措辞与礼貌语气。\n"
    "2. MUST NOT 新增 facts 里没有的事实，尤其 MUST NOT 编造或估算任何薪资/薪酬/待遇数字"
    "——薪资位置保持留空，供 HR 手工填写。\n"
    "3. MUST NOT 输出评分、排名、硬门槛命中或任何「建议录用/建议淘汰」类文本。\n"
    "输出 JSON，字段：body(string)。"
)


class LetterFacts(BaseModel):
    """生成文书所需的投递事实。⛔ schema 层不得出现薪资类键——这是「薪资不入库」
    在生成输入侧的第二道防线（第一道是 offer 表无薪资列，已由 U1 落地）。"""

    candidate_name: str
    job_title: str
    department: str | None = None
    start_date: str | None = None
    report_to: str | None = None


class _LetterBodySchema(BaseModel):
    body: str


@dataclass(frozen=True)
class LetterDraft:
    kind: str
    body: str  # 已带 AI 生成标识
    run_id: str
    response_model: str | None
    prompt_version: str


def _prompt_version(kind: str) -> str:
    if kind not in ("offer", "rejection"):
        raise ValueError(f"kind 只能是 offer/rejection，收到: {kind!r}")
    return LETTER_PROMPT_VERSION_TMPL.format(kind=kind)


def compute_letter_draft(
    gateway: LLMGateway,
    *,
    kind: str,
    template_body: str,
    template_version: int,
    facts: LetterFacts,
    audit_context: dict | None = None,
) -> LetterDraft:
    """纯函数：按模板 + 投递事实生成一版文书草稿并带 AI 生成标识。

    ⚠️ user_prompt 末尾的「请求标识」nonce 是刻意的：temperature=0 下同一投递
    连续两次生成的 prompt 逐字相同，RecorderAuditHook 的确定性 id
    （{thread_id}:{node}:{input_hash}:{attempt}）会撞主键——第二次的 analysis_run
    被 SqliteSink 短路、run_id 与第一次相同，effect_persist_letter 随之被幂等
    短路 ⇒ 版本不递增，直接违反 spec「同一投递重复生成 MUST 产生新版本」。把
    nonce 写进实际发给模型的字节里，input_hash 每次不同、run_id 随之不同。"""
    prompt_version = _prompt_version(kind)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    nonce = uuid.uuid4().hex
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=_LETTER_SYSTEM_PROMPT,
        user_prompt=(
            f"kind={kind}\n模板版本={template_version}\n模板正文：\n{template_body}\n"
            f"投递事实：{facts.model_dump_json()}\n请求标识：{nonce}"
        ),
        schema=_LetterBodySchema,
        prompt_version=prompt_version,
        audit_context=audit_context,
    )
    body = enforce_ai_label(parsed.body, generated_at=generated_at)
    return LetterDraft(
        kind=kind,
        body=body,
        run_id=meta.run_id,
        response_model=meta.response_model,
        prompt_version=prompt_version,
    )
```

**文件**：`tests/test_letter_drafter.py`（新建）

```python
"""letter_drafter 纯函数（U2 tasks 2.2）的单元测试。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.agents.jd_agent import AI_LABEL_PREFIX
from app.agents.letter_drafter import LetterFacts, compute_letter_draft
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
        self.calls = []

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
        api_key="k",
        base_url="https://example.invalid",
        model="deepseek-chat",
        supports_json_schema=True,
        client=scripted,
    )


def test_facts_schema_has_no_salary_keys():
    props = set(LetterFacts.model_json_schema()["properties"])
    forbidden = ("salary", "pay", "compensation", "bonus", "薪")
    offenders = [p for p in props if any(k in p.lower() or k in p for k in forbidden)]
    assert offenders == []


def test_compute_uses_json_schema_prompt_version_and_temperature_zero():
    scripted = _ScriptedClient([json.dumps({"body": "张三：您好"})])
    draft = compute_letter_draft(
        _gateway(scripted),
        kind="offer",
        template_body="{candidate_name}：您好",
        template_version=1,
        facts=LetterFacts(candidate_name="张三", job_title="嵌入式工程师"),
    )
    call = scripted.chat.completions.calls[0]
    assert call["temperature"] == 0
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert draft.prompt_version == "letter-offer-v1"


def test_response_model_taken_from_api_response():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_letter_draft(
        _gateway(scripted),
        kind="rejection",
        template_body="{candidate_name}：您好",
        template_version=1,
        facts=LetterFacts(candidate_name="张三", job_title="嵌入式工程师"),
    )
    assert draft.response_model == "deepseek-chat-actual-v1"
    assert draft.prompt_version == "letter-rejection-v1"


def test_body_carries_ai_label_same_as_jd():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_letter_draft(
        _gateway(scripted),
        kind="offer",
        template_body="{candidate_name}：您好",
        template_version=1,
        facts=LetterFacts(candidate_name="张三", job_title="嵌入式工程师"),
    )
    assert AI_LABEL_PREFIX in draft.body


def test_consecutive_generations_have_distinct_run_ids():
    scripted = _ScriptedClient([
        json.dumps({"body": "您好"}),
        json.dumps({"body": "您好"}),
    ])
    gateway = _gateway(scripted)
    facts = LetterFacts(candidate_name="张三", job_title="嵌入式工程师")
    first = compute_letter_draft(
        gateway, kind="offer", template_body="{candidate_name}：您好",
        template_version=1, facts=facts,
    )
    second = compute_letter_draft(
        gateway, kind="offer", template_body="{candidate_name}：您好",
        template_version=1, facts=facts,
    )
    assert first.run_id != second.run_id


def test_module_has_no_storage_write():
    src = Path("app/agents/letter_drafter.py").read_text(encoding="utf-8")
    assert "app.storage" not in src
    assert "conn.execute" not in src
```

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1001x-offer-unit2-plan
python -m pytest tests/test_letter_drafter.py -q
```

全部通过（6 passed）。

---

### Task 2: 模板维护存储层 `app/storage/letter_template.py` + 占位模板 v1 种子（tasks 2.1）

**文件**：`app/storage/letter_template.py`（新建）

```python
"""Offer/拒信文书模板的版本化读写与保存校验（offer-generation U2 tasks 2.1）。

版本化：每次 PUT 产生新版本，绝不覆盖旧版（candidate-letter-engine spec
「文书模板由 HR 维护并版本化」）。保存校验：⛔ 评分/排名/硬门槛占位符（两类
模板一律禁）；Offer ⛔ 薪资类占位符——命中即拒并指出具体占位符（正则＋关键词表）。
"""
from __future__ import annotations

import re
import sqlite3

# 占位符语法：{name}。校验只扫花括号内的名字（名字可含中文，如 {排名} 也要命中）。
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

# 允许出现在模板里的占位符白名单（kind 决定）。超出白名单即拒——包括一切
# 评分/排名/硬门槛/薪资类占位符与拼写错误。
ALLOWED_OFFER_PLACEHOLDERS: frozenset[str] = frozenset(
    {"candidate_name", "job_title", "department", "start_date", "report_to"}
)
ALLOWED_REJECTION_PLACEHOLDERS: frozenset[str] = frozenset(
    {"candidate_name", "job_title"}
)

# ⛔ 评分/排名/硬门槛类占位符关键词（两类模板一律禁）。
FORBIDDEN_COMMON_KEYWORDS: tuple[str, ...] = (
    "score", "rank", "ranking", "total_score", "hard_requirement", "hard_rule",
    "recommend", "reject", "总分", "排名", "评分", "硬门槛", "建议",
)

# ⛔ 薪资类占位符关键词（仅 Offer 模板禁）。
FORBIDDEN_SALARY_KEYWORDS: tuple[str, ...] = (
    "salary", "pay", "compensation", "bonus", "allowance",
    "薪", "工资", "薪资", "报酬", "待遇",
)


class ForbiddenPlaceholderError(ValueError):
    """模板里出现被禁的占位符。message 带出具体占位符与类别，供接口直接回给 HR。"""


def _reject_reason(kind: str, name: str) -> str:
    low = name.lower()
    if kind == "offer" and any(
        k in low or k in name for k in FORBIDDEN_SALARY_KEYWORDS
    ):
        return "薪资类占位符"
    if any(k in low or k in name for k in FORBIDDEN_COMMON_KEYWORDS):
        return "评分/排名/硬门槛占位符"
    return "未登记的占位符"


def validate_template_body(*, kind: str, body: str) -> None:
    """保存校验：kind 合法、正文非空、占位符全部落在该 kind 的白名单内。"""
    if kind not in ("offer", "rejection"):
        raise ValueError(f"kind 只能是 offer/rejection，收到: {kind!r}")
    if not body or not body.strip():
        raise ValueError("模板正文不能为空")
    allowed = (
        ALLOWED_OFFER_PLACEHOLDERS if kind == "offer"
        else ALLOWED_REJECTION_PLACEHOLDERS
    )
    offenders = []
    for token in _PLACEHOLDER_RE.findall(body):
        name = token.strip()
        if name in allowed:
            continue
        offenders.append(f"{name}（{_reject_reason(kind, name)}）")
    if offenders:
        raise ForbiddenPlaceholderError(
            f"模板含被禁止的占位符: {sorted(set(offenders))}"
        )


def get_letter_template(conn: sqlite3.Connection, kind: str) -> dict | None:
    """返回该 kind 的最新版模板；从未写入返回 None。"""
    row = conn.execute(
        "SELECT kind, version, body, updated_by, updated_at FROM letter_template "
        "WHERE kind = ? ORDER BY version DESC LIMIT 1",
        (kind,),
    ).fetchone()
    if row is None:
        return None
    return {
        "kind": row[0],
        "version": row[1],
        "body": row[2],
        "updated_by": row[3],
        "updated_at": row[4],
    }


def put_letter_template(
    conn: sqlite3.Connection, *, kind: str, body: str, updated_by: str
) -> dict:
    """新增一个版本，绝不覆盖旧版；同内容重复 PUT 是幂等 no-op。"""
    validate_template_body(kind=kind, body=body)
    if not updated_by or not updated_by.strip():
        raise ValueError("updated_by 不能为空")

    latest = get_letter_template(conn, kind)
    if latest is not None and latest["body"] == body:
        return {**latest, "unchanged": True}

    new_version = (latest["version"] + 1) if latest else 1
    conn.execute(
        "INSERT INTO letter_template (kind, version, body, updated_by) "
        "VALUES (?, ?, ?, ?)",
        (kind, new_version, body, updated_by),
    )
    conn.commit()
    return {**get_letter_template(conn, kind), "unchanged": False}
```

**文件**：`app/storage/db.py`（修改，两处）

**2a. 在 `init_schema` 末尾追加种子调用**（现有结尾是 `_seed_onboarding_default_template(conn)` +
`conn.commit()`），改为：

```python
    _seed_onboarding_default_template(conn)
    _seed_letter_templates(conn)
    conn.commit()
```

**2b. 在 `_seed_onboarding_default_template` 函数之后新增 `_seed_letter_templates` 与两份
占位模板常量**（模板正文是数据，种子真源在 db.py；校验逻辑在 `app/storage/letter_template.py`，
避免 `db.py` 反向 import 校验模块造成依赖倒置）：

```python
_LETTER_TEMPLATE_OFFER_V1 = (
    "{candidate_name}：\n\n"
    "经我司综合评估，很高兴通知您，拟录用您担任 {job_title} 岗位，所属部门为 "
    "{department}，预计入职日期为 {start_date}，汇报对象为 {report_to}。\n\n"
    "如您对以上内容无异议，请于收到后 3 个工作日内回复确认。\n\n"
    "卓品智能人力资源部"
)

_LETTER_TEMPLATE_REJECTION_V1 = (
    "{candidate_name}：\n\n"
    "感谢您应聘我司 {job_title} 岗位。经综合评估，我们很遗憾地通知您，本次未能为"
    "您提供进一步的机会。您的简历我们将妥善保管，若未来有合适的岗位会再次与您联系。\n\n"
    "祝您求职顺利。\n\n"
    "卓品智能人力资源部"
)


def _seed_letter_templates(conn: sqlite3.Connection) -> None:
    """幂等种子：Offer/拒信各一份占位模板 v1（offer-generation U2 tasks 2.1，
    OQ1/OQ3 回件到后只换内容不改代码）。固定 (kind, version) 天然键。"""
    conn.execute(
        "INSERT OR IGNORE INTO letter_template (kind, version, body, updated_by) "
        "VALUES ('offer', 1, ?, 'system')",
        (_LETTER_TEMPLATE_OFFER_V1,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO letter_template (kind, version, body, updated_by) "
        "VALUES ('rejection', 1, ?, 'system')",
        (_LETTER_TEMPLATE_REJECTION_V1,),
    )
```

**文件**：`tests/test_letter_template.py`（新建）

```python
"""letter_template 版本化读写与占位符校验（U2 tasks 2.1）。"""
from __future__ import annotations

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.letter_template import (
    ForbiddenPlaceholderError,
    get_letter_template,
    put_letter_template,
    validate_template_body,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "t.db"))
    init_schema(c)
    return c


def test_seed_creates_placeholder_v1_for_both_kinds(conn):
    offer = get_letter_template(conn, "offer")
    rejection = get_letter_template(conn, "rejection")
    assert offer is not None and offer["version"] == 1
    assert rejection is not None and rejection["version"] == 1
    assert offer["updated_by"] == "system"


def test_put_creates_new_version_and_keeps_old(conn):
    put_letter_template(conn, kind="offer", body="{candidate_name}：您好v2", updated_by="hr")
    latest = get_letter_template(conn, "offer")
    assert latest["version"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_template WHERE kind = 'offer'"
    ).fetchone()[0] == 2


def test_put_identical_body_is_noop(conn):
    body = get_letter_template(conn, "offer")["body"]
    result = put_letter_template(conn, kind="offer", body=body, updated_by="hr")
    assert result["unchanged"] is True
    assert result["version"] == 1


def test_offer_rejects_score_rank_and_salary_placeholders():
    for bad in ("{total_score}", "{排名}", "{salary}", "{薪资}"):
        with pytest.raises(ForbiddenPlaceholderError):
            validate_template_body(kind="offer", body=f"{bad} 您好")


def test_rejection_rejects_score_rank_but_not_salary():
    with pytest.raises(ForbiddenPlaceholderError):
        validate_template_body(kind="rejection", body="{排名} 您好")
    # 拒信白名单只有 candidate_name/job_title；{department} 这类越界占位符同样被拒。
    with pytest.raises(ForbiddenPlaceholderError):
        validate_template_body(kind="rejection", body="{department} 您好")


def test_allowed_offer_placeholders_pass():
    validate_template_body(
        kind="offer",
        body="{candidate_name} 拟任 {job_title}，部门 {department}，入职 {start_date}，"
        "汇报 {report_to}",
    )
```

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1001x-offer-unit2-plan
python -m pytest tests/test_letter_template.py -q
```

全部通过（6 passed）。

---

### Task 3: L4 编排层 `app/graph/letter_nodes.py`（tasks 2.3 / 2.4 / 2.6）

**文件**：`app/graph/letter_nodes.py`（新建）

```python
"""Offer/拒信文书 L4 编排层（offer-generation U2 tasks 2.3/2.4/2.6）。

compute_letter_draft_for_application 只读查库组装 facts + 模板，调 L3 Agent
生成草稿（工程铁律 2：compute_* 可以查库做输入组装）；写库全部在
effect_persist_letter / effect_edit_letter / effect_mark_letter_human_written
三个 effect_* 节点里，每个都独占、幂等（工程铁律 1）。查看/导出留痕
record_letter_access 与 app/graph/resume_nodes.py::record_resume_access 同款：
写入失败不吞异常，让读取也失败。
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid

from app.agents.jd_agent import (
    UNKNOWN_GENERATED_AT,
    enforce_ai_label,
    extract_label_generated_at,
    strip_ai_label,
)
from app.agents.letter_drafter import LetterDraft, LetterFacts, compute_letter_draft
from app.storage.idempotency import idempotent_effect
from app.storage.letter_template import get_letter_template


class LetterNotFoundError(Exception):
    """letter_id 不存在。"""


class LetterTemplateMissingError(Exception):
    """该 kind 还没有模板。"""


class OfferNotFoundError(Exception):
    """该投递尚无 Offer 记录，不能生成 Offer 文书。"""


class OfferNotApprovedError(Exception):
    """Offer 尚未审批通过（offer.status != 'approved'），不能生成文书。"""


class RejectionRecordMissingError(Exception):
    """该投递尚无 rejection_record，不能生成拒信。"""


def _assert_offer_approved(conn: sqlite3.Connection, application_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM offer WHERE application_id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise OfferNotFoundError("该投递尚无 Offer 记录，不能生成 Offer 文书")
    if row[0] != "approved":
        raise OfferNotApprovedError("Offer 尚未审批通过，不能生成文书")


def _assert_rejection_record_exists(
    conn: sqlite3.Connection, application_id: str
) -> None:
    row = conn.execute(
        "SELECT 1 FROM rejection_record WHERE application_id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise RejectionRecordMissingError(
            "该投递尚无淘汰记录，请先在复核工作台完成批量确认"
        )


def next_letter_version(
    conn: sqlite3.Connection, application_id: str, kind: str
) -> int:
    row = conn.execute(
        "SELECT MAX(version) FROM candidate_letter WHERE application_id = ? AND kind = ?",
        (application_id, kind),
    ).fetchone()
    return (row[0] or 0) + 1


def load_letter_facts(
    conn: sqlite3.Connection, application_id: str, kind: str
) -> LetterFacts:
    row = conn.execute(
        "SELECT c.name, j.title FROM application a "
        "JOIN candidate c ON c.id = a.candidate_id "
        "JOIN job j ON j.id = a.job_id WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"application 不存在: {application_id!r}")
    candidate_name, job_title = row
    if kind == "offer":
        offer = conn.execute(
            "SELECT department, start_date, report_to FROM offer WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        if offer is None:
            raise OfferNotFoundError("该投递尚无 Offer 记录，不能生成 Offer 文书")
        return LetterFacts(
            candidate_name=candidate_name,
            job_title=job_title,
            department=offer[0],
            start_date=offer[1],
            report_to=offer[2],
        )
    return LetterFacts(candidate_name=candidate_name, job_title=job_title)


def compute_letter_draft_for_application(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    kind: str,
    gateway,
) -> tuple[LetterDraft, int]:
    """L4 compute_* 节点：先过前置校验（fail-fast，避免白烧一次 LLM 调用），
    再读模板 + facts，调 L3 纯函数。返回 (draft, 生成时绑定的模板版本)。"""
    if kind == "offer":
        _assert_offer_approved(conn, application_id)
    elif kind == "rejection":
        _assert_rejection_record_exists(conn, application_id)
    else:
        raise ValueError(f"kind 只能是 offer/rejection，收到: {kind!r}")

    template = get_letter_template(conn, kind)
    if template is None:
        raise LetterTemplateMissingError(f"缺少 {kind} 模板，请先在模板维护页创建")

    facts = load_letter_facts(conn, application_id, kind)
    job_id = conn.execute(
        "SELECT job_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    draft = compute_letter_draft(
        gateway,
        kind=kind,
        template_body=template["body"],
        template_version=template["version"],
        facts=facts,
        audit_context={
            "thread_id": f"{application_id}:letter",
            "node": "compute_letter_draft",
            "application_id": application_id,
            "job_id": job_id,
        },
    )
    return draft, template["version"]


@idempotent_effect("effect_persist_letter")
def effect_persist_letter(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    kind: str,
    version: int,
    template_version: int,
    draft: LetterDraft,
    created_by: str,
) -> str:
    """effect_* 节点：把一版草稿落成 candidate_letter（版本递增），独占、幂等。

    business_key = f"{kind}:{draft.run_id}"，幂等键 =
    {application_id}:effect_persist_letter:{kind}:{analysis_run_id}——同一次真实
    LLM 调用只落一版；再次生成（新 run_id）得到新版本。

    前置校验在事务内重做一遍（compute 与 persist 之间 offer 状态可能被改）：
    Offer 类前置 offer.status='approved'；拒信类前置存在 rejection_record。
    不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交（铁律 1）。
    """
    if kind == "offer":
        _assert_offer_approved(conn, application_id)
    else:
        _assert_rejection_record_exists(conn, application_id)
    if not created_by or not created_by.strip():
        raise ValueError("created_by 不能为空")
    letter_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO candidate_letter (id, application_id, kind, version, "
        "template_version, body, ai_generated, analysis_run_id, sent_status, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'none', ?)",
        (letter_id, application_id, kind, version, template_version, draft.body,
         draft.run_id, created_by),
    )
    return letter_id


def load_letter(conn: sqlite3.Connection, letter_id: str) -> dict:
    row = conn.execute(
        "SELECT id, application_id, kind, version, body, ai_generated FROM "
        "candidate_letter WHERE id = ?",
        (letter_id,),
    ).fetchone()
    if row is None:
        raise LetterNotFoundError(f"文书不存在: {letter_id!r}")
    return {
        "id": row[0],
        "application_id": row[1],
        "kind": row[2],
        "version": row[3],
        "body": row[4],
        "ai_generated": bool(row[5]),
    }


def letter_edit_business_key(letter_id: str, text: str) -> str:
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]
    return f"{letter_id}:{digest}"


@idempotent_effect("effect_edit_letter")
def effect_edit_letter(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    letter_id: str,
    edited_body: str,
) -> str:
    """effect_* 节点：把 HR 编辑后的正文写回，编辑不去 AI 标识（tasks 2.4）。

    标识保护与 app/graph/jd_nodes.py::effect_update_jd_text 同款：⛔ 不检查用户
    有没有删标识（检查就有绕过空间），无条件重贴。唯一例外是已「标记为人工撰写」
    的文书（ai_generated=0）：那份作者已经是人，只剥不贴。
    """
    letter = load_letter(conn, letter_id)
    if letter["ai_generated"]:
        generated_at = (
            extract_label_generated_at(letter["body"]) or UNKNOWN_GENERATED_AT
        )
        final_body = enforce_ai_label(edited_body, generated_at=generated_at)
    else:
        final_body = strip_ai_label(edited_body)
    conn.execute(
        "UPDATE candidate_letter SET body = ? WHERE id = ?", (final_body, letter_id)
    )
    return final_body


@idempotent_effect("effect_mark_letter_human_written")
def effect_mark_letter_human_written(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    letter_id: str,
    reviewer: str,
    marked_at: str,
) -> str:
    """effect_* 节点：显式「标记为人工撰写」去标识 + 留痕（tasks 2.4）。

    这是**唯一**能去掉 AI 标识的路径（candidate-letter-engine spec「编辑不去标，
    显式标记人工撰写才去标」）。去标识与留痕在同一次 UPDATE：body 剥掉标识、
    ai_generated=0、authorship_marked_by/at/from_version 一起落地，结构上不存在
    「标识没了但查不到谁去的」中间态。⛔ reviewer 不接受空白（决策人只能是人）。
    """
    if not str(reviewer).strip():
        raise ValueError(
            "标记为人工撰写必须记下是谁标的（合规红线：决策人只能是人）"
        )
    letter = load_letter(conn, letter_id)
    final_body = strip_ai_label(letter["body"])
    conn.execute(
        "UPDATE candidate_letter SET body = ?, ai_generated = 0, "
        "authorship_marked_by = ?, authorship_marked_at = ?, authorship_from_version = ? "
        "WHERE id = ?",
        (final_body, reviewer, marked_at, letter["version"], letter_id),
    )
    return final_body


def record_letter_access(
    conn: sqlite3.Connection,
    *,
    accessor: str,
    application_id: str,
    letter_id: str,
    access_type: str,
) -> None:
    """文书查看/导出留痕（candidate-letter-engine spec「导出 docx」「文书草稿的
    查看留痕」）。写入失败不吞任何异常——调用方（路由）不 catch 就是正确行为
    （FastAPI 未捕获异常 ⇒ 500，读取自然失败，不返回正文）。"""
    conn.execute(
        "INSERT INTO letter_access_log (id, accessor, application_id, letter_id, access_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), accessor, application_id, letter_id, access_type),
    )
    conn.commit()
```

**文件**：`tests/test_letter_nodes.py`（新建）

```python
"""letter_nodes 的 effect_* 幂等与前置校验（U2 tasks 2.3）。"""
from __future__ import annotations

import uuid

import pytest

from app.agents.letter_drafter import LetterDraft
from app.graph.letter_nodes import (
    OfferNotApprovedError,
    RejectionRecordMissingError,
    effect_mark_letter_human_written,
    effect_persist_letter,
)
from app.storage.db import get_connection, init_schema


def _seed(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','offer')"
    )
    conn.commit()


def _draft(kind="offer", run_id="run1"):
    return LetterDraft(
        kind=kind, body="【AI 生成】正文", run_id=run_id,
        response_model="deepseek-chat", prompt_version=f"letter-{kind}-v1",
    )


def _analysis_run(conn, run_id):
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES (?, 'deepseek-chat', 'letter-offer-v1', 0.0, 'h', '{}')",
        (run_id,),
    )


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "n.db"))
    init_schema(c)
    _seed(c)
    return c


def _offer(conn, status="approved"):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, created_by) VALUES (?, 'app1','j1','研发部','2026-10-20',"
        "'李四', ?, 'hr')",
        (str(uuid.uuid4()), status),
    )


def test_effect_persist_increments_version(conn):
    _offer(conn)
    _analysis_run(conn, "run1")
    _analysis_run(conn, "run2")
    conn.commit()
    effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run2",
        application_id="app1", kind="offer", version=2, template_version=1,
        draft=_draft(run_id="run2"), created_by="hr",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate_letter WHERE application_id='app1'"
    ).fetchone()[0] == 2


def test_effect_persist_same_run_id_is_noop(conn):
    _offer(conn)
    _analysis_run(conn, "run1")
    conn.commit()
    effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    result = effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    assert result is None
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate_letter WHERE application_id='app1'"
    ).fetchone()[0] == 1


def test_offer_requires_approved_status(conn):
    _offer(conn, status="pending_approval")
    _analysis_run(conn, "run1")
    conn.commit()
    with pytest.raises(OfferNotApprovedError):
        effect_persist_letter(
            conn, thread_id="app1", business_key="offer:run1",
            application_id="app1", kind="offer", version=1, template_version=1,
            draft=_draft(run_id="run1"), created_by="hr",
        )


def test_rejection_requires_rejection_record(conn):
    _analysis_run(conn, "run1")
    conn.commit()
    with pytest.raises(RejectionRecordMissingError):
        effect_persist_letter(
            conn, thread_id="app1", business_key="rejection:run1",
            application_id="app1", kind="rejection", version=1, template_version=1,
            draft=_draft(kind="rejection", run_id="run1"), created_by="hr",
        )


def test_rejection_succeeds_with_rejection_record(conn):
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rr1','app1','human_decision','hr')"
    )
    _analysis_run(conn, "run1")
    conn.commit()
    effect_persist_letter(
        conn, thread_id="app1", business_key="rejection:run1",
        application_id="app1", kind="rejection", version=1, template_version=1,
        draft=_draft(kind="rejection", run_id="run1"), created_by="hr",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate_letter WHERE kind='rejection'"
    ).fetchone()[0] == 1


def test_mark_human_written_records_authorship(conn):
    _offer(conn)
    _analysis_run(conn, "run1")
    conn.commit()
    letter_id = effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    effect_mark_letter_human_written(
        conn, thread_id="app1", business_key=f"{letter_id}:mark-human",
        letter_id=letter_id, reviewer="alice", marked_at="2026-10-10 00:00:00",
    )
    row = conn.execute(
        "SELECT body, ai_generated, authorship_marked_by, authorship_from_version "
        "FROM candidate_letter WHERE id = ?",
        (letter_id,),
    ).fetchone()
    assert "【AI 生成】" not in row[0]
    assert row[1] == 0
    assert row[2] == "alice"
    assert row[3] == 1
```

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1001x-offer-unit2-plan
python -m pytest tests/test_letter_nodes.py -q
```

全部通过（6 passed）。

---

### Task 4: docx 渲染 `app/letter_docx.py`（tasks 2.5 渲染半边）

**文件**：`app/letter_docx.py`（新建）

```python
"""Offer/拒信文书 docx 渲染（offer-generation U2 tasks 2.5）。

字体统一做法与 scripts/letter_md_to_docx.py 一致（微软雅黑 + Consolas，东亚字体
必须显式钉死，否则中文各自回退导致参差不齐）——但本模块面向 candidate_letter.body
字符串渲染，并额外承担两件脚本不承担的事：
① 未标记人工撰写（ai_generated=1）时页眉写 AI 标识文字；
② Offer 类在正文后追加「薪资待遇（由 HR 手工填写）：」+ 一个空段落（薪资位置留空）。
⛔ 本模块不 import app.storage / scripts（app→scripts 是层次倒置）。
"""
from __future__ import annotations

import re
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from app.agents.jd_agent import (
    AI_LABEL_TEMPLATE,
    UNKNOWN_GENERATED_AT,
    extract_label_generated_at,
)

DEFAULT_BODY_FONT = "微软雅黑"
DEFAULT_MONO_FONT = "Consolas"
SALARY_PARAGRAPH_LABEL = "薪资待遇（由 HR 手工填写）："


def _pin_font(
    style,
    *,
    ascii_font: str,
    east_asia_font: str,
    size_pt: float,
    bold: bool | None = None,
    color: tuple[int, int, int] | None = None,
    space_after_pt: float | None = None,
) -> None:
    style.font.name = ascii_font
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)
    rfonts.set(qn("w:eastAsia"), east_asia_font)
    style.font.size = Pt(size_pt)
    if bold is not None:
        style.font.bold = bold
    if color is not None:
        style.font.color.rgb = RGBColor(*color)
    if space_after_pt is not None:
        style.paragraph_format.space_after = Pt(space_after_pt)


def _add_runs(paragraph, text: str) -> None:
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
            run.font.name = DEFAULT_BODY_FONT
            run._element.rPr.rFonts.set(qn("w:eastAsia"), DEFAULT_BODY_FONT)
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = DEFAULT_MONO_FONT
            run._element.rPr.rFonts.set(qn("w:eastAsia"), DEFAULT_MONO_FONT)
        else:
            run = paragraph.add_run(part)
            run.font.name = DEFAULT_BODY_FONT
            run._element.rPr.rFonts.set(qn("w:eastAsia"), DEFAULT_BODY_FONT)


def _pin_default_styles(document: "docx.Document") -> None:
    _pin_font(
        document.styles["Normal"], ascii_font=DEFAULT_BODY_FONT,
        east_asia_font=DEFAULT_BODY_FONT, size_pt=11, space_after_pt=6,
    )
    for name, size in (("Heading 1", 16), ("Heading 2", 13.5), ("Heading 3", 12)):
        _pin_font(
            document.styles[name], ascii_font=DEFAULT_BODY_FONT,
            east_asia_font=DEFAULT_BODY_FONT, size_pt=size, bold=True,
            color=(0x1F, 0x1F, 0x1F), space_after_pt=6,
        )
    for list_style in ("List Bullet", "List Number"):
        _pin_font(
            document.styles[list_style], ascii_font=DEFAULT_BODY_FONT,
            east_asia_font=DEFAULT_BODY_FONT, size_pt=11, space_after_pt=4,
        )


def _set_ai_header(document: "docx.Document", body: str) -> None:
    generated_at = extract_label_generated_at(body) or UNKNOWN_GENERATED_AT
    paragraph = document.sections[0].header.paragraphs[0]
    paragraph.text = AI_LABEL_TEMPLATE.format(generated_at=generated_at)
    for run in paragraph.runs:
        run.font.name = DEFAULT_BODY_FONT
        run._element.rPr.rFonts.set(qn("w:eastAsia"), DEFAULT_BODY_FONT)


def _append_body_lines(document: "docx.Document", body: str) -> None:
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            document.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            document.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            document.add_heading(line[2:].strip(), level=1)
        elif re.match(r"^\s*[-*] ", line):
            _add_runs(
                document.add_paragraph(style="List Bullet"),
                re.sub(r"^\s*[-*] ", "", line),
            )
        elif re.match(r"^\s*\d+\. ", line):
            _add_runs(
                document.add_paragraph(style="List Number"),
                re.sub(r"^\s*\d+\. ", "", line),
            )
        else:
            _add_runs(document.add_paragraph(), line)


def render_letter_to_docx(
    *,
    body: str,
    kind: str,
    ai_generated: bool,
    out_path: str | Path,
) -> Path:
    """把一版文书草稿渲染成 docx。导出内容与草稿一致（body 原样渲染，含 AI 标识行），
    页眉按 ai_generated 决定是否写 AI 标识；Offer 末尾追加留空的薪资段落。"""
    document = docx.Document()
    _pin_default_styles(document)
    if ai_generated:
        _set_ai_header(document, body)
    _append_body_lines(document, body)
    if kind == "offer":
        _add_runs(document.add_paragraph(), SALARY_PARAGRAPH_LABEL)
        document.add_paragraph()  # 薪资位置：空段落，由 HR 手填
    out = Path(out_path)
    document.save(str(out))
    return out
```

**文件**：`tests/test_letter_docx.py`（新建）

```python
"""letter_docx 渲染（U2 tasks 2.5）。"""
from __future__ import annotations

import docx
from docx.oxml.ns import qn

from app.agents.jd_agent import AI_LABEL_PREFIX, AI_LABEL_TEMPLATE
from app.letter_docx import (
    DEFAULT_BODY_FONT,
    SALARY_PARAGRAPH_LABEL,
    render_letter_to_docx,
)

BODY = (
    "张三：\n\n经评估，拟录用您担任嵌入式工程师。\n\n"
    + AI_LABEL_TEMPLATE.format(generated_at="2026-10-10 00:00:00")
)


def test_offer_docx_header_contains_ai_label_when_ai_generated(tmp_path):
    out = render_letter_to_docx(
        body=BODY, kind="offer", ai_generated=True, out_path=tmp_path / "a.docx"
    )
    document = docx.Document(str(out))
    header = "\n".join(p.text for p in document.sections[0].header.paragraphs)
    assert AI_LABEL_PREFIX in header


def test_human_written_docx_header_has_no_ai_label(tmp_path):
    out = render_letter_to_docx(
        body="张三：您好", kind="rejection", ai_generated=False,
        out_path=tmp_path / "a.docx",
    )
    document = docx.Document(str(out))
    header = "\n".join(p.text for p in document.sections[0].header.paragraphs)
    assert AI_LABEL_PREFIX not in header


def test_offer_salary_paragraph_is_blank(tmp_path):
    out = render_letter_to_docx(
        body=BODY, kind="offer", ai_generated=True, out_path=tmp_path / "a.docx"
    )
    document = docx.Document(str(out))
    texts = [p.text for p in document.paragraphs]
    assert SALARY_PARAGRAPH_LABEL in texts
    assert texts[texts.index(SALARY_PARAGRAPH_LABEL) + 1] == ""


def test_rejection_has_no_salary_paragraph(tmp_path):
    out = render_letter_to_docx(
        body=BODY, kind="rejection", ai_generated=False, out_path=tmp_path / "a.docx"
    )
    document = docx.Document(str(out))
    assert all(SALARY_PARAGRAPH_LABEL not in p.text for p in document.paragraphs)


def test_body_text_preserved_and_runs_pin_east_asia_font(tmp_path):
    out = render_letter_to_docx(
        body=BODY, kind="offer", ai_generated=False, out_path=tmp_path / "a.docx"
    )
    document = docx.Document(str(out))
    texts = [p.text for p in document.paragraphs]
    assert any("嵌入式工程师" in t for t in texts)
    fonts = set()
    for p in document.paragraphs:
        for run in p.runs:
            rpr = run._element.rPr
            if rpr is not None and rpr.rFonts is not None:
                fonts.add(rpr.rFonts.get(qn("w:eastAsia")))
    assert fonts <= {DEFAULT_BODY_FONT, "Consolas"}
```

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1001x-offer-unit2-plan
python -m pytest tests/test_letter_docx.py -q
```

全部通过（5 passed）。

---

### Task 5: server.py 接线——模板/生成/查看/编辑/标记人工/导出端点（tasks 2.1/2.4/2.5/2.6）

**文件**：`app/web/server.py`（修改，三处）

**5a. 顶部 import 追加**（在现有 `from app.storage.offer_approval_chain import (...)` 之后）：

```python
from app.graph.letter_nodes import (
    LetterTemplateMissingError,
    OfferNotFoundError,
    OfferNotApprovedError,
    RejectionRecordMissingError,
    compute_letter_draft_for_application,
    effect_edit_letter,
    effect_mark_letter_human_written,
    effect_persist_letter,
    letter_edit_business_key,
    next_letter_version,
    record_letter_access,
)
from app.letter_docx import render_letter_to_docx
from app.storage.letter_template import (
    ForbiddenPlaceholderError,
    get_letter_template,
    put_letter_template,
)
```

并在 `import hashlib` / `import logging` / `import uuid` 一行附近追加：

```python
import os
import tempfile
```

在 `from fastapi.responses import FileResponse, HTMLResponse` 之后追加 `BackgroundTask`
（来源 `starlette.background`）：

```python
from starlette.background import BackgroundTask
```

**5b. 模块级请求模型**（在 `class LoginRequest(BaseModel)` 等既有请求模型附近追加）：

```python
class LetterTemplateUpdateRequest(BaseModel):
    body: str


class LetterGenerateRequest(BaseModel):
    kind: str


class LetterEditRequest(BaseModel):
    body: str
```

**5c. 路由**（在 `create_app` 内、`_require_role` 定义之后、`_canonical_items` 之前追加整段）：

```python
    # ── offer-generation U2：文书模板与文书引擎（tasks 2.1/2.4/2.5/2.6/2.7）──

    def _letter_row(letter_id: str):
        return conn.execute(
            "SELECT id, application_id, kind, version, template_version, body, "
            "ai_generated, authorship_marked_by, authorship_marked_at, "
            "authorship_from_version, sent_status, sent_channel, created_by, created_at "
            "FROM candidate_letter WHERE id = ?",
            (letter_id,),
        ).fetchone()

    def _letter_payload(letter_id: str):
        row = _letter_row(letter_id)
        if row is None:
            return None
        return {
            "id": row[0], "application_id": row[1], "kind": row[2], "version": row[3],
            "template_version": row[4], "body": row[5], "ai_generated": bool(row[6]),
            "authorship_marked_by": row[7], "authorship_marked_at": row[8],
            "authorship_from_version": row[9], "sent_status": row[10],
            "sent_channel": row[11], "created_by": row[12], "created_at": row[13],
        }

    def _letter_meta_payload(letter_id: str):
        payload = _letter_payload(letter_id)
        if payload is None:
            return None
        # 列表不返回正文：查看正文唯一入口是 GET /api/letters/{id}，它先写 view 留痕。
        payload.pop("body")
        return payload

    def _load_letter_or_404(letter_id: str) -> dict:
        payload = _letter_payload(letter_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="文书不存在")
        return payload

    @router.get("/api/letter-templates/{kind}")
    def get_letter_template_endpoint(kind: str, request: Request):
        _require_role(request, "hr")
        template = get_letter_template(conn, kind)
        if template is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        return template

    @router.put("/api/letter-templates/{kind}")
    def put_letter_template_endpoint(
        kind: str, req: LetterTemplateUpdateRequest, request: Request
    ):
        username = _require_role(request, "hr")
        try:
            return put_letter_template(conn, kind=kind, body=req.body, updated_by=username)
        except ForbiddenPlaceholderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/applications/{application_id}/letters")
    def list_letters(application_id: str, request: Request):
        _require_role(request, "hr")
        rows = conn.execute(
            "SELECT id FROM candidate_letter WHERE application_id = ? "
            "ORDER BY kind, version DESC",
            (application_id,),
        ).fetchall()
        return {"letters": [_letter_meta_payload(r[0]) for r in rows]}

    @router.post("/api/applications/{application_id}/letters", status_code=201)
    def generate_letter(
        application_id: str, req: LetterGenerateRequest, request: Request
    ):
        username = _require_role(request, "hr")
        if req.kind not in ("offer", "rejection"):
            raise HTTPException(status_code=422, detail="kind 只能是 offer 或 rejection")
        try:
            draft, template_version = compute_letter_draft_for_application(
                conn, application_id=application_id, kind=req.kind, gateway=gateway
            )
        except (OfferNotFoundError, OfferNotApprovedError, RejectionRecordMissingError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LetterTemplateMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        version = next_letter_version(conn, application_id, req.kind)
        effect_persist_letter(
            conn,
            thread_id=application_id,
            business_key=f"{req.kind}:{draft.run_id}",
            application_id=application_id,
            kind=req.kind,
            version=version,
            template_version=template_version,
            draft=draft,
            created_by=username,
        )
        # 重复调用（同一 run_id）被 idempotent_effect 短路，version 预算号不会真落库——
        # 用 analysis_run_id 反查实际落库的版本，不信任调用前算出来的那个数（同 prep 的 fix 1a）。
        actual = conn.execute(
            "SELECT id FROM candidate_letter WHERE application_id = ? AND kind = ? "
            "AND analysis_run_id = ?",
            (application_id, req.kind, draft.run_id),
        ).fetchone()[0]
        return _letter_payload(actual)

    @router.get("/api/letters/{letter_id}")
    def view_letter(letter_id: str, request: Request):
        username = _require_role(request, "hr")
        letter = _load_letter_or_404(letter_id)
        # 先留痕再返回正文；留痕失败 ⇒ 未捕获异常 ⇒ 500，不返回正文（spec「留痕失败 MUST NOT 返回正文」）。
        record_letter_access(
            conn, accessor=username, application_id=letter["application_id"],
            letter_id=letter_id, access_type="view",
        )
        return letter

    @router.patch("/api/letters/{letter_id}")
    def edit_letter(letter_id: str, req: LetterEditRequest, request: Request):
        _require_role(request, "hr")
        letter = _load_letter_or_404(letter_id)
        if not req.body or not req.body.strip():
            raise HTTPException(status_code=422, detail="文书正文不能为空")
        effect_edit_letter(
            conn,
            thread_id=letter["application_id"],
            business_key=letter_edit_business_key(letter_id, req.body),
            letter_id=letter_id,
            edited_body=req.body,
        )
        return _letter_payload(letter_id)

    @router.post("/api/letters/{letter_id}/mark-human")
    def mark_letter_human(letter_id: str, request: Request):
        username = _require_role(request, "hr")
        letter = _load_letter_or_404(letter_id)
        effect_mark_letter_human_written(
            conn,
            thread_id=letter["application_id"],
            business_key=f"{letter_id}:mark-human",
            letter_id=letter_id,
            reviewer=username,
            marked_at=sqlite_utc_now(),
        )
        return _letter_payload(letter_id)

    @router.get("/api/letters/{letter_id}/export.docx")
    def export_letter(letter_id: str, request: Request):
        username = _require_role(request, "hr")
        letter = _load_letter_or_404(letter_id)
        # 导出也写留痕（access_type=export），先留痕再产文件（spec「导出 MUST 写访问留痕」）。
        record_letter_access(
            conn, accessor=username, application_id=letter["application_id"],
            letter_id=letter_id, access_type="export",
        )
        fd, tmp_path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        try:
            render_letter_to_docx(
                body=letter["body"],
                kind=letter["kind"],
                ai_generated=letter["ai_generated"],
                out_path=tmp_path,
            )
        except Exception:
            os.unlink(tmp_path)
            raise
        # 首次导出把 sent_status 推进到 exported；已 copied/sent/system_queued 的不降级。
        conn.execute(
            "UPDATE candidate_letter SET sent_status = 'exported' "
            "WHERE id = ? AND sent_status = 'none'",
            (letter_id,),
        )
        conn.commit()
        return FileResponse(
            tmp_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"letter-{letter_id}.docx",
            background=BackgroundTask(os.unlink, tmp_path),
        )
```

**文件**：`tests/test_letter_endpoints.py`（新建）

```python
"""文书引擎端到端端点（U2 tasks 2.1/2.4/2.5/2.6）。"""
from __future__ import annotations

import io
import json
import sqlite3

import docx
from fastapi.testclient import TestClient

from app.agents.jd_agent import AI_LABEL_PREFIX
from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.llm.gateway import LLMGateway
from app.storage.auth_session import create_session
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.web.server import create_app
from tests.test_web_api import ScriptedOpenAIClient


def _app(tmp_path, responses):
    db_path = str(tmp_path / "web.db")
    conn = get_connection(db_path)
    init_schema(conn)
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
    hook = RecorderAuditHook(recorder, conn)
    scripted = ScriptedOpenAIClient(responses)

    def gateway_factory():
        return LLMGateway(
            api_key="k", base_url="https://example.com", model="deepseek-chat",
            supports_json_schema=False, client=scripted, audit_hook=hook,
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path="")
    return TestClient(app), scripted


def _login(tmp_path, client, username="tester"):
    conn = sqlite3.connect(str(tmp_path / "web.db"))
    account_id = upsert_account(conn, username=username, password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    conn.close()
    client.cookies.set("hr_session", token)


def _seed_app(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "web.db"))
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','offer')"
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, created_by) VALUES ('o1','app1','j1','研发部','2026-10-20',"
        "'李四','approved','hr')"
    )
    conn.commit()
    conn.close()


def test_template_put_rejects_salary_placeholder(tmp_path):
    client, _ = _app(tmp_path, [])
    _login(tmp_path, client)
    resp = client.put("/api/letter-templates/offer", json={"body": "{salary} 您好"})
    assert resp.status_code == 422
    assert "salary" in resp.json()["detail"]


def test_generate_twice_creates_new_version(tmp_path):
    client, _ = _app(tmp_path, [
        json.dumps({"body": "拟录用您担任嵌入式工程师"}),
        json.dumps({"body": "拟录用您担任嵌入式工程师"}),
    ])
    _login(tmp_path, client)
    _seed_app(tmp_path)

    resp = client.post("/api/applications/app1/letters", json={"kind": "offer"})
    assert resp.status_code == 201
    letter = resp.json()
    assert letter["version"] == 1
    assert letter["ai_generated"] is True
    assert AI_LABEL_PREFIX in letter["body"]
    # 同一投递重复生成 MUST 产生新版本（spec Scenario「生成 Offer 草稿」的
    # 「同一投递重复生成」）。两次生成的请求标识 nonce 不同 ⇒ input_hash 不同 ⇒
    # 两个不同的 run_id ⇒ 两行 candidate_letter。
    resp2 = client.post("/api/applications/app1/letters", json={"kind": "offer"})
    assert resp2.status_code == 201
    assert resp2.json()["version"] == 2


def test_generate_rejection_requires_rejection_record(tmp_path):
    client, _ = _app(tmp_path, [])
    _login(tmp_path, client)
    _seed_app(tmp_path)
    resp = client.post("/api/applications/app1/letters", json={"kind": "rejection"})
    assert resp.status_code == 409


def test_edit_preserves_ai_label_and_mark_human_strips_it(tmp_path):
    client, _ = _app(tmp_path, [json.dumps({"body": "拟录用您担任嵌入式工程师"})])
    _login(tmp_path, client)
    _seed_app(tmp_path)
    letter = client.post("/api/applications/app1/letters", json={"kind": "offer"}).json()

    edited = client.patch(f"/api/letters/{letter['id']}", json={"body": "改过的正文"}).json()
    assert edited["ai_generated"] is True
    assert AI_LABEL_PREFIX in edited["body"]

    marked = client.post(f"/api/letters/{letter['id']}/mark-human").json()
    assert marked["ai_generated"] is False
    assert AI_LABEL_PREFIX not in marked["body"]
    assert marked["authorship_marked_by"] == "tester"
    assert marked["authorship_from_version"] == 1


def test_view_writes_access_log_then_returns_body(tmp_path):
    client, _ = _app(tmp_path, [json.dumps({"body": "拟录用您担任嵌入式工程师"})])
    _login(tmp_path, client)
    _seed_app(tmp_path)
    letter = client.post("/api/applications/app1/letters", json={"kind": "offer"}).json()
    resp = client.get(f"/api/letters/{letter['id']}")
    assert resp.status_code == 200
    assert resp.json()["body"] == letter["body"]
    conn = sqlite3.connect(str(tmp_path / "web.db"))
    row = conn.execute(
        "SELECT access_type, accessor FROM letter_access_log WHERE letter_id = ?",
        (letter["id"],),
    ).fetchone()
    assert row == ("view", "tester")


def test_view_log_failure_blocks_body(tmp_path, monkeypatch):
    client, _ = _app(tmp_path, [json.dumps({"body": "拟录用您担任嵌入式工程师"})])
    _login(tmp_path, client)
    _seed_app(tmp_path)
    letter = client.post("/api/applications/app1/letters", json={"kind": "offer"}).json()

    def boom(*args, **kwargs):
        raise RuntimeError("留痕表不可写")

    import app.web.server as server_mod
    monkeypatch.setattr(server_mod, "record_letter_access", boom)
    resp = client.get(f"/api/letters/{letter['id']}")
    assert resp.status_code == 500


def test_export_writes_log_sets_sent_status_and_renders(tmp_path):
    client, _ = _app(tmp_path, [json.dumps({"body": "拟录用您担任嵌入式工程师"})])
    _login(tmp_path, client)
    _seed_app(tmp_path)
    letter = client.post("/api/applications/app1/letters", json={"kind": "offer"}).json()

    resp = client.get(f"/api/letters/{letter['id']}/export.docx")
    assert resp.status_code == 200
    document = docx.Document(io.BytesIO(resp.content))
    header = "\n".join(p.text for p in document.sections[0].header.paragraphs)
    assert AI_LABEL_PREFIX in header
    texts = [p.text for p in document.paragraphs]
    assert "薪资待遇（由 HR 手工填写）：" in texts

    conn = sqlite3.connect(str(tmp_path / "web.db"))
    assert conn.execute(
        "SELECT sent_status FROM candidate_letter WHERE id = ?", (letter["id"],)
    ).fetchone()[0] == "exported"
    assert conn.execute(
        "SELECT COUNT(*) FROM letter_access_log WHERE letter_id = ? AND access_type = 'export'",
        (letter["id"],),
    ).fetchone()[0] == 1
```

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1001x-offer-unit2-plan
python -m pytest tests/test_letter_endpoints.py -q
```

全部通过（7 passed）。

---

### Task 6: 文书页 `app/web/static/letters.html` + 页面路由（tasks 2.7）

**文件**：`app/web/static/letters.html`（新建）

> 单文件前端、无构建，照 `app/web/static/resume_list.html` 的既有形态：`<base href>` 由
> `_render_static_page` 替换为 root_path 下的绝对 base；JS 里一切接口调用用**相对路径**
> （`api/applications/…`、`api/letters/…`），⛔ 不得出现 `/api/…`、`/static/…` 这类硬编码
> 绝对路径（部署约束 1，`tests/test_letters_page.py` 会逐字扫字符串字面量）。

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!--BASE_HREF-->
  <title>文书页 · 卓品智能招聘助手</title>
  <link rel="stylesheet" href="static/app.css">
</head>
<body>
  <main class="container">
    <h1>Offer / 拒信文书</h1>
    <p id="app-id" hidden></p>
    <section>
      <h2>生成</h2>
      <button id="gen-offer">生成 Offer 文书</button>
      <button id="gen-rejection">生成拒信</button>
      <p id="gen-error" class="error" hidden></p>
    </section>
    <section>
      <h2>各版本</h2>
      <ul id="letter-list"></ul>
    </section>
    <section id="editor" hidden>
      <h2 id="editor-title">草稿</h2>
      <p id="ai-badge">【AI 生成】本版本由系统生成，带 AI 生成标识。</p>
      <textarea id="letter-body" rows="16"></textarea>
      <button id="save-edit">保存编辑</button>
      <button id="mark-human">标记为人工撰写</button>
      <button id="copy-text">复制文案</button>
      <a id="export-link" href="#">导出 docx</a>
    </section>
  </main>
  <script>
    const appId = location.pathname.split("/").filter(Boolean).pop();
    document.getElementById("app-id").textContent = appId;
    const list = document.getElementById("letter-list");
    const editor = document.getElementById("editor");
    const body = document.getElementById("letter-body");
    const aiBadge = document.getElementById("ai-badge");
    const exportLink = document.getElementById("export-link");
    let currentId = null;

    function refreshList() {
      fetch(`api/applications/${appId}/letters`)
        .then(r => r.json())
        .then(data => {
          list.innerHTML = "";
          (data.letters || []).forEach(l => {
            const li = document.createElement("li");
            const a = document.createElement("a");
            a.textContent = `${l.kind} v${l.version} · ${l.ai_generated ? "AI 生成" : "人工撰写"}`;
            a.href = "#";
            a.onclick = () => openLetter(l.id);
            li.appendChild(a);
            list.appendChild(li);
          });
        });
    }

    function openLetter(id) {
      fetch(`api/letters/${id}`)
        .then(r => { if (!r.ok) return r.json().then(x => { throw new Error(x.detail); }); return r.json(); })
        .then(l => {
          currentId = l.id;
          editor.hidden = false;
          body.value = l.body;
          aiBadge.hidden = !l.ai_generated;
          exportLink.href = `api/letters/${l.id}/export.docx`;
        })
        .catch(e => alert(e.message));
    }

    function generate(kind) {
      fetch(`api/applications/${appId}/letters`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind })
      })
        .then(r => { if (!r.ok) return r.json().then(x => { throw new Error(x.detail); }); return r.json(); })
        .then(() => refreshList())
        .catch(e => {
          const el = document.getElementById("gen-error");
          el.hidden = false;
          el.textContent = e.message;
        });
    }

    document.getElementById("gen-offer").onclick = () => generate("offer");
    document.getElementById("gen-rejection").onclick = () => generate("rejection");
    document.getElementById("save-edit").onclick = () => {
      fetch(`api/letters/${currentId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: body.value })
      // 2026-10-11 修正（Spec review 实测）：⛔ 不能把响应体当 letter_id 传给
      // openLetter（那会请求 api/letters/[object Object]，成功路径必报假错）；
      // 保存后按当前 letter_id 重开即可。
      }).then(r => { if (!r.ok) return r.json().then(x => { throw new Error(x.detail); }); })
        .then(() => openLetter(currentId));
    };
    document.getElementById("mark-human").onclick = () => {
      fetch(`api/letters/${currentId}/mark-human`, { method: "POST" })
        .then(r => { if (!r.ok) return r.json().then(x => { throw new Error(x.detail); }); })
        .then(() => openLetter(currentId));
    };
    document.getElementById("copy-text").onclick = () => {
      navigator.clipboard.writeText(body.value).then(() => alert("已复制"));
    };

    refreshList();
  </script>
</body>
</html>
```

**文件**：`app/web/server.py`（修改，路由追加在 Task 5 的 `/api/letters/{letter_id}/export.docx`
之后、`@router.get("/api/applications/{application_id}/interview-sessions")` 之前）：

```python
    @router.get("/applications/{application_id}/letters")
    def letters_page(application_id: str):
        return _render_static_page("letters.html", root_path)
```

**文件**：`tests/test_letters_page.py`（新建）

```python
"""文书页静态路由与子路径前缀（U2 tasks 2.7）。"""
from __future__ import annotations

import re
from pathlib import Path

from app.llm.gateway import LLMGateway
from app.web.server import create_app
from fastapi.testclient import TestClient


def _make(root_path: str, tmp_path):
    db_path = str(tmp_path / "p.db")

    def gateway_factory():
        return LLMGateway(
            api_key="k", base_url="https://example.invalid", model="deepseek-chat",
            supports_json_schema=False, client=object(),
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path=root_path)
    return TestClient(app)


def test_letters_page_served_with_relative_paths(make_test_client):
    client, _ = make_test_client()
    resp = client.get("/applications/app1/letters")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/">' in html
    assert "AI" in html
    assert "api/applications/" in html


def test_letters_page_works_under_subpath_prefix(tmp_path):
    client = _make("/hr/recruit-agent", tmp_path)
    resp = client.get("/hr/recruit-agent/applications/app1/letters")
    assert resp.status_code == 200
    html = resp.text
    assert '<base href="/hr/recruit-agent/">' in html


def test_letters_page_has_no_absolute_path_strings():
    html = Path("app/web/static/letters.html").read_text(encoding="utf-8")
    without_comments = "\n".join(line.split("//", 1)[0] for line in html.splitlines())
    literals = [
        content
        for _, content in re.findall(r"""(["'`])((?:\\.|(?!\1).)*)\1""", without_comments)
    ]
    absolute = [lit for lit in literals if lit.split("${", 1)[0].startswith("/")]
    assert not absolute, f"发现硬编码的绝对路径字符串字面量: {absolute!r}"


def test_letters_page_edit_and_mark_human_reopen_current_letter():
    """2026-10-11 修正（Spec review 实测）：⛔ 不能把响应体当 letter_id 传给
    openLetter（`api/letters/[object Object]`，成功路径必报假错）。"""
    html = Path("app/web/static/letters.html").read_text(encoding="utf-8")
    assert ".then(openLetter)" not in html
    assert html.count("openLetter(currentId)") >= 2
```

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1001x-offer-unit2-plan
python -m pytest tests/test_letters_page.py -q
```

全部通过（3 passed）。

---

### Task 7: 全量回归（tasks 2.8 代码侧）

**命令与预期输出**：

```bash
cd /Users/paulshao/Projects/HumanResource/.claude/worktrees/lane-1001x-offer-unit2-plan
python -m pytest \
  tests/test_letter_drafter.py \
  tests/test_letter_template.py \
  tests/test_letter_nodes.py \
  tests/test_letter_docx.py \
  tests/test_letter_endpoints.py \
  tests/test_letters_page.py \
  tests/test_db_offer_schema.py \
  tests/test_offer_approval_chain.py \
  tests/test_offer_approval_chain_endpoint.py \
  tests/test_db_m3_schema.py -q
```

预期：全部通过（0 failed）。`tests/test_db_offer_schema.py`（U1 建的 schema 测试）与
`tests/test_db_m3_schema.py`（老库升级既有表不变）必须原样通过——U2 只加种子行、不碰
既有表结构。

---

### Task 8: `.51` 同款 Windows 环境导出冒烟（tasks 2.8，⛔ 需服务器访问）

**前置**：`.51` 服务器可达、本泳道代码已部署或可用 `scp` 把 `app/letter_docx.py` +
一份样例 body 推上去执行。⛔ 本步只验证「docx 在 Windows 上字体/结构正常、Word 能打开」，
不做发版（发版是 Shao Peishen 的不可代项）。

**在 `.51` 上跑**：

```bash
# 在 .51 上（Windows + 项目 venv）执行：
python - <<'PY'
from app.letter_docx import render_letter_to_docx
from app.agents.jd_agent import AI_LABEL_TEMPLATE
body = "张三：\n\n拟录用您担任嵌入式工程师。\n\n" + AI_LABEL_TEMPLATE.format(generated_at="2026-10-10 00:00:00")
out = render_letter_to_docx(body=body, kind="offer", ai_generated=True, out_path=r"data\smoke-offer.docx")
print("saved:", out)
PY
```

**判据**：用 Word 打开 `data\smoke-offer.docx`，确认：① 页眉含「【AI 生成】…」标识；
② 正文中文不出现「标题一种、正文一种」的字体参差；③ 末尾有「薪资待遇（由 HR 手工填写）：」
标签且下一段为空。三项都过 ⇒ 在 `tasks.md` 第 2.8 条勾选并记录。

**⏸ 留步预案**：若 `.51` 不可达或低峰窗口未到，本步登记「⏸ 留步：`.51` 不可达，docx
导出冒烟未执行」后照常收工——2.1–2.7 的代码与单测已全部落地，不因本步判整件失败。

---

## Requirement → Task 覆盖表

| spec 文件 · Requirement | 本单元落点 | Task |
|---|---|---|
| candidate-letter-engine · 文书模板由 HR 维护并版本化 | `letter_template` 版本化 PUT + 占位符校验 + v1 种子 + `GET/PUT /api/letter-templates/{kind}` | 2, 5 |
| candidate-letter-engine · 按投递事实生成草稿并带 AI 标识 | `compute_letter_draft` 纯函数 + AI 标识同 M1 JD 串 + `effect_persist_letter` + `POST /api/applications/{id}/letters` | 1, 3, 5 |
| candidate-letter-engine · 编辑不去标，显式标记人工撰写才去标 | `effect_edit_letter` / `effect_mark_letter_human_written` + `PATCH` / `POST mark-human` | 3, 5 |
| candidate-letter-engine · 导出 docx | `render_letter_to_docx`（页眉 AI 标识 + Offer 薪资留空段）+ `GET export.docx`（写 `letter_access_log(export)` + `sent_status=exported`） | 4, 5 |
| candidate-letter-engine · 文书草稿的查看留痕 | `record_letter_access` + `GET /api/letters/{id}`（先留痕再返回正文） | 3, 5 |
| candidate-letter-outbound · 默认交付形态是 HR 自行发送（导出半边） | `export.docx` 作为默认交付 + `sent_status=exported`；「复制文案 + 回填已发出」属 U4 tasks 4.4，不在本单元 | 4, 5 |
| candidate-letter-outbound · 系统外发一律经既有门禁 | ⛔ U4 tasks 4.1/4.2（`offer_letter` 登记 + `effect_enqueue_letter`），不在本单元 | — |
| candidate-letter-outbound · 拒信发送时机由 HR 决定 | ⛔ U4 tasks 4.3（拒信页显式生成），不在本单元 | — |

> U4 的三条行为级 Requirement（门禁登记、`effect_enqueue_letter`、拒信页、复制/发送回填）
> 不在 U2 范围，本单元只提供它们的 schema 与 `sent_status` 落点（`exported` 已落地）。

## 端到端提取验证（已做）

本计划的三处高风险代码已脱离计划文档、在 `/private/tmp` 用真实 venv + 真实仓库代码独立验证：

1. **占位符校验**（正则 `\{([^{}]+)\}` + 关键词表 + kind 白名单）：`{candidate_name}` 等
   合法占位符通过；`{total_score}/{排名}`（两类模板）、`{salary}/{薪资}`（Offer）命中即拒；
   拒信模板的越界占位符 `{department}` 同样被拒。**通过。**
2. **docx 渲染**（页眉 AI 标识 + Offer 薪资空段 + 东亚字体钉死）：未标记人工时页眉含
   `AI_LABEL_PREFIX`；`薪资待遇（由 HR 手工填写）：` 之后紧跟空段落；拒信无薪资段、无页眉
   标识；全部 run 的 `w:eastAsia` 只落在 `{微软雅黑, Consolas}`。**通过。**
3. **`effect_persist_letter` + `idempotent_effect` + 前置校验 + FK**：对真实
   `app/storage/db.py` 建表，版本递增、同 run_id 重跑短路（返回 None）、未审批 Offer 与
   缺 `rejection_record` 均拒绝、`candidate_letter.analysis_run_id` 外键校验通过。**通过。**

（本验证只证明「代码可执行且内部自洽」，spec 合规由 `run-build` 两阶段 review 负责。）

## 下一步

用 `run-build` 执行本计划。U2 完成后 `offer-generation` 的第 3 章（U3 内部审批流）才可发车
（design D9：U3 前置＝U1；U4 前置＝U2 + M2 `rejection_record`）。
