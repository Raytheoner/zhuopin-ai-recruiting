# 需求识别（tasks 5.3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首轮消息不是用人需求时，系统回一句固定引导语并且**不在库里留下任何岗位记录**；已有岗位的后续轮次里出现无关消息时，回同一句引导语且**不改岗位状态、不删已采集内容**。

**Architecture:** 识别本身已经在 L3 纯函数里（`run_intake_turn` 的 `is_job_related` 分支），本单元不再造第二个判定器。缺的是编排侧的另一半：`POST /api/jobs` 在第一句话**之前**就 `INSERT INTO job`，判非用人需求之后那一行仍然留着。做法是把 L3 的 `is_job_related` 从 `graph.invoke()` 的终态里取出来交给 server 分流（server 只分流、不二次判定），首轮判否时调用一个存储层函数把这一轮写下的每一行整体抹掉（"落后即删"），并把 `job_id` 以 `null` 回给前端。引导语文案改为**确定性系统文案**，不再采用模型自由文本——这是合规红线「AI 不做淘汰」在文案上唯一可机器判据的形态。

**Tech Stack:** Python 3.14 · FastAPI 0.115.6 · LangGraph 1.0.10 + `langgraph-checkpoint-sqlite` 2.0.6（SqliteSaver）· SQLite · pytest 8.3.4 · 原生 JS（`app/web/static/index.html`）

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」逐字复制，外加本交付单元的五条边界约束。**每个 Task 的验收隐含包含本段全部内容。**

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
4. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。供应商不提供带版本号快照时，**必须从 API 响应里取回实际的 `model` 字段并持久化**。
5. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。
6. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
7. **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
8. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。

本交付单元额外的五条硬边界（逐字来自 0908E opener）：

1. 「不建岗位记录」的实现落在 server.py 的建单路径：首轮判非用人需求 → 不落 job 行（或落后即删，⛔ 不留 status 花样的僵尸行）；已有 job 的后续轮次里出现无关消息 → 回引导语，⛔ 不改 job 状态
2. 识别是 L3 纯函数的输出（is_job_related），server 只按它分流；⛔ 不在 server.py 里再调一次模型做二次判断
3. ⛔ 不碰 app/graph/nodes.py（网关泳道在改）、app/llm/、app/audit/；前端相对路径
4. 引导语文案里⛔ 不出现"AI 自动拒绝"之类措辞（合规：AI 不做淘汰）
5. 并行同伴：网关泳道在改 llm/config/main/nodes；幂等泳道只加测试

---

## 覆盖范围

**spec**：`openspec/changes/m1-job-profile-intake/specs/job-profile-intake/spec.md`
→ `### Requirement: 对话式需求发起` 的 `#### Scenario: 需求描述为空或与招聘无关`
（"系统回复引导语说明可以怎么提需求" **AND** "不创建岗位记录"）

**WBS**：`openspec/changes/m1-job-profile-intake/tasks.md` 的 `5.3`
（同 Requirement 下另两条 Scenario 由已交付的建单/追问链路承担，不在本单元范围内；`5.6` 超时放弃另属一条待办，本单元不碰）

| spec Scenario | Task |
|---|---|
| 需求描述**为空** → 引导语 | Task 1 |
| 需求描述**与招聘无关** → 引导语（文案确定性、合规） | Task 1 |
| 不创建岗位记录（存储层语义） | Task 2 |
| 不创建岗位记录（建单路径接线 + 端到端） | Task 3、Task 4 |
| 已有岗位的后续离题轮不受影响（反向保护） | Task 5 |
| 调用方对 `job_id = null` 的处理 | Task 6 |

## 现状与关键事实（实现前必读，全部已在 2026-09-08 核实）

- `app/agents/intake_agent.py:987` 起是 `is_job_related=False` 分支：已经回引导语、`profile_patch={}`、`is_productive=False`、`asked_questions=[引导语]`。**识别这一半已有。**
- `_GUIDANCE_TEXT`（`app/agents/intake_agent.py:904`）= `"没听懂是不是用人需求，可以试试：'要招一个做XX的工程师'"`。当前分支写法是 `_to_intake_questions(parsed.questions) or [_guidance_question()]`——**模型给了自由文本就用模型的**，固定文案只是兜底。
- `app/web/server.py:264` `POST /api/jobs`：先 `INSERT INTO job` + `commit()`，再跑 `_run_turn`。**"不建岗位记录"这一半没有。**
- `app/storage/db.py:16`：`job_profile.job_id TEXT NOT NULL REFERENCES job(id)`，且 `app/storage/db.py:330` 有 `PRAGMA foreign_keys = ON`。**全库只有这一条指向 `job` 的外键**（`analysis_run.job_id` 是裸 TEXT，无外键）。⇒ 删除顺序必须 `job_profile` 先于 `job`；⇒ 也**做不到**"跑完第一轮再决定要不要 INSERT job 行"，因为同一次 `graph.invoke` 里 `effect_persist_draft` 会先撞上外键。所以本单元走 opener 明确许可的**落后即删**。
- 一次首轮 invoke 在业务库里写下的行，穷举如下（无其他）：`job`（server 写）、`job_profile` v1、`conversation`、`outbox` × 1、`effect_log` × 2（`effect_persist_draft` / `effect_deliver_message`），以及 checkpointer 自己那条连接上的 `checkpoints` / `writes`。
- `app/storage/job_queries.py:103 latest_profile_rows` 是 **LEFT JOIN**，注释写明这是刻意的："有 job、没有任何 job_profile" 的行必须在列表里看得见。⇒ 只删 `job_profile` 不删 `job` 会在岗位列表里留下一条「待确定 / drafting」的僵尸，比不删更糟。
- `graph.invoke(state, config=...)` 的返回值是合并后的终态 dict，`compute_intake_turn`（`app/graph/nodes.py:169`）已经把 `is_job_related` 放进去了。`_run_turn` 现在把这个返回值丢掉了。**这就是 server 分流所需的、且唯一允许的信号来源。**
- `graph.checkpointer` 就是 `build_intake_graph` 里 `SqliteSaver(checkpointer_conn)` 那个实例（已实测 `compiled.checkpointer is saver` 为 True），带 `.lock` 与 `.conn`。
- ⚠️ **`SqliteSaver.delete_thread()` 在 `langgraph-checkpoint-sqlite==2.0.6` 里是 `raise NotImplementedError`**（2026-09-08 对 `venv` 内实际安装版本反射确认）。⛔ 不要调它。清 checkpoint 只能对该 saver 自己的两张表（`checkpoints` / `writes`）发 DELETE。
- ⛔ `app/storage/job_queries.py` 是**只读模块**，有机器判据 `tests/test_job_queries.py::test_module_contains_no_write_statements`。删除函数**不许**写进那个文件，本计划为它新开 `app/storage/job_discard.py`。
- `app/audit/` 的 `analysis_run` 行**必须保留**：那次模型调用真实发生过，铁律 3/4 要求可解释、可审计。丢弃岗位不等于丢弃"我们调过一次模型"这个事实。
- 前端 `app/web/static/index.html:461-468`：`const url = jobId ? \`api/jobs/${jobId}/reply\` : "api/jobs";` … `if (!jobId) jobId = data.job_id;`。`data.job_id` 为 `null` 时 `jobId` 仍是假值，下一条消息会重新走 `POST api/jobs`——**这正是我们要的行为**，但赋值写法看不出这是有意的。
- `scripts/replay_pilot_sessions.py:268` 直接 `response.json()["job_id"]`，拿到 `None` 会在下一行拼出 `/api/jobs/None/reply` 然后 404，错误信息与真正的原因无关。

## 残留风险（实现时照抄进代码注释，⛔ 不要假装不存在）

**崩溃窗口**：`INSERT job` 与 `discard_unstarted_job()` 分属两个事务（中间 `graph.invoke` 里的 `idempotent_effect` 必然提交）。进程若恰好在两者之间崩溃，会留下一行「待确定 / drafting、零个 job_profile 版本」的 job。这与**今天已经存在**的故障模式完全一致（第一轮抛异常时同样留下这种行，见 `app/storage/job_queries.py:103` 的注释），本单元不扩大它、也不在本单元内消除它——消除它要求把建 job 行挪进 `effect_persist_draft` 的同一个事务，那要改 `app/graph/nodes.py`，超出本单元边界（Global Constraints 边界 3）。

**引导语进已问台账**：确定性引导语的 `question_id` 稳定，连续多个离题轮会在台账里累计同一个 id 的"重问"计数。这是**今天模型返回空 questions 时就已有**的行为（现分支同样落 `asked_questions=[引导语]`），本单元不改变它；引导语 `field=None`，不会被算成字段缺口。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `app/agents/intake_agent.py` | L3 纯函数：空输入短路 + 离题轮引导语确定化 | 改（Task 1） |
| `app/storage/job_discard.py` | **新模块**：丢弃一个从未成立的岗位（业务行 + checkpoint），纯 SQL，无业务判定 | 建（Task 2） |
| `app/web/server.py` | `_run_turn` 透出 `is_job_related`；`create_job` 按它分流 | 改（Task 3、4） |
| `app/web/static/index.html` | `job_id = null` 的显式处理 | 改（Task 6） |
| `scripts/replay_pilot_sessions.py` | `job_id = null` 时给出说人话的错误 | 改（Task 6） |
| `tests/test_intake_agent.py` | Task 1 的测试 | 改 |
| `tests/test_job_discard.py` | **新**：Task 2 的测试 | 建 |
| `tests/test_web_api.py` | Task 3/4/5 的测试 | 改 |
| `tests/test_static_frontend.py` | Task 6 的静态判据 | 改 |

⛔ 全程不碰：`app/graph/nodes.py`、`app/graph/build.py`、`app/llm/`、`app/audit/`、`app/storage/db.py`（**不加表、不加列、不改 schema**）、`app/storage/job_queries.py`。

---

### Task 1: L3 侧——空输入短路 + 离题轮引导语确定化

**Files:**
- Modify: `app/agents/intake_agent.py`（`run_intake_turn` 开头；`:987` 起的 `is_job_related=False` 分支）
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Consumes: 现有 `_GUIDANCE_TEXT`、`_guidance_question()`、`_last_user_text(history)`、`render_questions_text()`、`IntakeTurnResult`（全部已存在于本文件）
- Produces: `run_intake_turn(...)` 的行为契约——`is_job_related is False` 时，`questions` **恒等于** `[_guidance_question()]`，`questions_text == _GUIDANCE_TEXT`，`profile_patch == {}`，`is_productive is False`；且末轮用户文本为空白时**完全不调用 gateway**（`gateway` 的调用次数为 0）

- [ ] **Step 1: 写失败测试——离题轮忽略模型自由文本**

追加到 `tests/test_intake_agent.py`（沿用该文件既有的 scripted gateway 构造方式，与文件里现有用例保持一致）：

```python
def test_off_topic_turn_always_returns_the_deterministic_guidance(tmp_path):
    """离题轮的文案是系统的，不是模型的。

    合规红线「AI 只做排序推荐，不做自动淘汰」在文案上唯一可机器判据的形态，
    就是这句话由系统固定给出。模型自由文本随时可能写出「已自动拒绝」这类
    暗示淘汰的说法，而那是一条**没有任何症状**的红线破口：接口照样 200，
    测试照样绿，只有业务经理在屏幕上看见。
    """
    from app.agents.intake_agent import _GUIDANCE_TEXT, run_intake_turn

    gateway = _scripted_gateway(
        [
            json.dumps(
                {
                    "is_job_related": False,
                    "questions": [{"text": "不符合要求，AI 已自动拒绝该请求"}],
                    "profile_patch": {"job_title": "不该被写进来"},
                }
            )
        ]
    )

    result = run_intake_turn(gateway, history=[{"role": "user", "content": "今天中午吃什么"}], round_count=0)

    assert result.is_job_related is False
    assert [q.text for q in result.questions] == [_GUIDANCE_TEXT]
    assert result.questions_text == _GUIDANCE_TEXT
    assert "自动拒绝" not in result.questions_text
    assert "淘汰" not in result.questions_text
    # 离题轮不许有任何画像产出，也不许消耗追问预算。
    assert result.profile_patch == {}
    assert result.is_productive is False
    # 引导语确实下发了，已问台账要如实记它。
    assert [q.text for q in result.asked_questions] == [_GUIDANCE_TEXT]
```

`_scripted_gateway` 若在 `tests/test_intake_agent.py` 里不存在同名 helper，就照该文件现有用例（例如围绕 `is_job_related` 的既有用例，`tests/test_intake_agent.py:75`）的构造方式原样复用，⛔ 不要新造一套 fake。

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_intake_agent.py::test_off_topic_turn_always_returns_the_deterministic_guidance -v`
Expected: FAIL，`assert ['不符合要求，AI 已自动拒绝该请求'] == ['没听懂是不是用人需求，可以试试：\'要招一个做XX的工程师\'']`

- [ ] **Step 3: 改实现——离题分支不再采用模型文本**

`app/agents/intake_agent.py`，把 `if not parsed.is_job_related:` 块里的第一行

```python
        questions = _to_intake_questions(parsed.questions) or [_guidance_question()]
```

改成：

```python
        # 引导语**恒为系统文案**，⛔ 不采用模型这一轮的自由文本。
        #
        # 原写法是「模型给了就用模型的，没给才兜底」。问题不在于模型说得
        # 好不好，而在于这条路径上模型说的话没有任何机器判据：它随时可能
        # 写出「已自动拒绝」「不符合条件」这类暗示淘汰的措辞，而合规红线
        # 「AI 只做排序推荐，不做自动淘汰」被破时**没有任何症状**——接口
        # 照样 200，测试照样绿，只有业务经理在屏幕上看见。固定文案之后，
        # 这条红线才第一次有了可以断言的对象（tasks 5.3）。
        questions = [_guidance_question()]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_intake_agent.py -v`
Expected: 全部 PASS（含新用例）

- [ ] **Step 5: 写失败测试——空白输入不调模型**

```python
def test_blank_message_is_rejected_without_calling_the_model():
    """spec 的 Scenario 标题是"需求描述**为空**或与招聘无关"——空是其中一半。

    空输入送进模型只有两个结果：多花一次钱，或者模型自己脑补出一个岗位。
    判定放在 L3 而不是 server：识别是 L3 的职责，server 只按结论分流
    （Global Constraints 边界 2）。
    """
    from app.agents.intake_agent import _GUIDANCE_TEXT, run_intake_turn

    gateway = _scripted_gateway([])  # 队列为空：真调了模型就会 IndexError

    result = run_intake_turn(gateway, history=[{"role": "user", "content": "   \n  "}], round_count=0)

    assert result.is_job_related is False
    assert [q.text for q in result.questions] == [_GUIDANCE_TEXT]
    assert result.profile_patch == {}
    assert result.is_productive is False
    assert result.llm_latency_ms == 0.0
    assert result.llm_response_model is None
```

- [ ] **Step 6: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_intake_agent.py::test_blank_message_is_rejected_without_calling_the_model -v`
Expected: FAIL with `IndexError: pop from empty list`（证明当前实现确实把空输入送进了模型）

- [ ] **Step 7: 实现空输入短路**

在 `run_intake_turn` 里、**`gateway.extract_structured_with_meta(...)` 之前**（`user_prompt = _build_user_prompt(...)` 那一行的前面）插入：

```python
    # 空白输入直接判非用人需求，⛔ 不调模型。
    #
    # spec「需求描述为空或与招聘无关」的前半句。放在这里而不是 server：
    # 识别是 L3 的职责，server 只按 is_job_related 分流（边界 2）。
    # 空串送进模型只有两种下场——白花一次调用，或者模型凭空脑补一个岗位
    # 出来，而后者会真的建出一条岗位记录。
    if not _last_user_text(history).strip():
        blank_questions = [_guidance_question()]
        return IntakeTurnResult(
            is_job_related=False,
            questions=blank_questions,
            profile_patch={},
            is_complete=False,
            questions_text=render_questions_text(blank_questions),
            # 没调模型，时序与模型标识都留默认值——⛔ 不要编一个 0 之外的
            # 数字或配置里的模型名冒充（铁律 4：响应返回的才算）。
            is_productive=False,
            asked_questions=blank_questions,
        )
```

- [ ] **Step 8: 跑全套 L3 测试**

Run: `venv/bin/python -m pytest tests/test_intake_agent.py tests/test_graph_nodes.py -v`
Expected: 全部 PASS。`tests/test_graph_nodes.py:699` 那条既有的离题用例（模型返回 `questions: []`）行为不变——它本来就走兜底引导语。

- [ ] **Step 9: Commit**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py
git commit -m "feat(intake): 离题轮引导语确定化 + 空白输入不调模型（tasks 5.3）"
```

---

### Task 2: 存储层——`discard_unstarted_job()`

**Files:**
- Create: `app/storage/job_discard.py`
- Test: `tests/test_job_discard.py`

**Interfaces:**
- Consumes: 无（只依赖 `sqlite3` 与库里现有表）
- Produces:
  - `discard_unstarted_job(conn: sqlite3.Connection, job_id: str) -> None` —— 一个事务里删掉 `job_profile` / `conversation` / `outbox` / `effect_log` / `job` 五张表里属于该 `job_id` 的行，然后 `commit()`。**不删 `analysis_run`。**
  - `discard_thread_checkpoints(saver, thread_id: str) -> None` —— 删掉 SqliteSaver 自己那两张表（`checkpoints` / `writes`）里属于该 thread 的行。`saver` 鸭子类型，只用它的 `.lock` 与 `.conn`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_job_discard.py`：

```python
import json
import sqlite3
import threading

import pytest

from app.storage.db import init_schema
from app.storage.job_discard import discard_thread_checkpoints, discard_unstarted_job


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "discard.db"))
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


def _seed_one_turn(conn, job_id):
    """照抄一次首轮 invoke 在库里真实写下的每一行，一行不多一行不少。"""
    conn.execute("INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,))
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) VALUES (?, ?, 1, 'drafting', '{}')",
        (f"{job_id}-v1", job_id),
    )
    conn.execute(
        "INSERT INTO conversation (thread_id, history_json) VALUES (?, ?)",
        (job_id, json.dumps([{"role": "user", "content": "今天中午吃什么"}])),
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, 'question', '{}')",
        (job_id,),
    )
    for node in ("effect_persist_draft", "effect_deliver_message"):
        conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, '0', datetime('now'))",
            (f"{job_id}:{node}:0", job_id, node),
        )
    conn.execute(
        "INSERT INTO analysis_run (id, job_id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES (?, ?, 'deepseek-chat-241226', 'intake-v5', 0.0, 'h', '{}')",
        (f"{job_id}-run1", job_id),
    )
    conn.commit()


def _counts(conn, job_id):
    return {
        "job": conn.execute("SELECT COUNT(*) FROM job WHERE id=?", (job_id,)).fetchone()[0],
        "job_profile": conn.execute("SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)).fetchone()[0],
        "conversation": conn.execute("SELECT COUNT(*) FROM conversation WHERE thread_id=?", (job_id,)).fetchone()[0],
        "outbox": conn.execute("SELECT COUNT(*) FROM outbox WHERE thread_id=?", (job_id,)).fetchone()[0],
        "effect_log": conn.execute("SELECT COUNT(*) FROM effect_log WHERE thread_id=?", (job_id,)).fetchone()[0],
        "analysis_run": conn.execute("SELECT COUNT(*) FROM analysis_run WHERE job_id=?", (job_id,)).fetchone()[0],
    }


def test_discard_removes_every_business_row_of_that_job(conn):
    _seed_one_turn(conn, "job-a")
    assert _counts(conn, "job-a") == {
        "job": 1, "job_profile": 1, "conversation": 1, "outbox": 1, "effect_log": 2, "analysis_run": 1
    }

    discard_unstarted_job(conn, "job-a")

    counts = _counts(conn, "job-a")
    assert counts["job"] == 0
    assert counts["job_profile"] == 0
    assert counts["conversation"] == 0
    assert counts["outbox"] == 0
    # effect_log 必须一起删：铁律1 的 reviewer 判据是"每个 effect_* 节点的
    # effect_log 条数与其业务表行数按 thread 恒等"。业务行删了、幂等记录
    # 留着，这条不变式当场破，而且破得没有症状。
    assert counts["effect_log"] == 0


def test_discard_keeps_the_audit_record_of_the_model_call(conn):
    """那次模型调用真的发生过。

    岗位可以当作从未成立，"我们调过一次模型"这个事实不可以——铁律3/4 要求
    每一次调用可解释、可审计，PIPL 第 24 条的说明权也建立在它上面。
    """
    _seed_one_turn(conn, "job-b")

    discard_unstarted_job(conn, "job-b")

    assert _counts(conn, "job-b")["analysis_run"] == 1


def test_discard_touches_no_other_job(conn):
    _seed_one_turn(conn, "job-c")
    _seed_one_turn(conn, "job-d")

    discard_unstarted_job(conn, "job-c")

    assert _counts(conn, "job-d") == {
        "job": 1, "job_profile": 1, "conversation": 1, "outbox": 1, "effect_log": 2, "analysis_run": 1
    }


def test_discard_is_committed(conn, tmp_path):
    """另开一条连接看得见结果 —— 没 commit 的话下一个请求还会看见这行。"""
    _seed_one_turn(conn, "job-e")
    discard_unstarted_job(conn, "job-e")

    other = sqlite3.connect(str(tmp_path / "discard.db"))
    try:
        assert other.execute("SELECT COUNT(*) FROM job WHERE id='job-e'").fetchone()[0] == 0
    finally:
        other.close()


class _FakeSaver:
    """鸭子类型的 checkpointer：只需要 .lock 与 .conn。"""

    def __init__(self, connection):
        self.lock = threading.Lock()
        self.conn = connection


def test_discard_checkpoints_removes_only_that_thread(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "cp.db"))
    try:
        # 表名与列名取自 langgraph-checkpoint-sqlite 2.0.6 的建表语句。
        connection.execute(
            "CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, "
            "checkpoint_id TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, "
            "checkpoint_id TEXT NOT NULL)"
        )
        for thread in ("keep", "drop"):
            connection.execute("INSERT INTO checkpoints VALUES (?, '', 'c1')", (thread,))
            connection.execute("INSERT INTO writes VALUES (?, '', 'c1')", (thread,))
        connection.commit()

        discard_thread_checkpoints(_FakeSaver(connection), "drop")

        assert connection.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='drop'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM writes WHERE thread_id='drop'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='keep'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM writes WHERE thread_id='keep'").fetchone()[0] == 1
    finally:
        connection.close()


def test_discard_checkpoints_fails_loudly_when_the_tables_are_gone(tmp_path):
    """升级 langgraph 把两张表改名时，这里必须红，⛔ 不许静默空转。

    静默空转的症状是：checkpoint 残留悄悄回来，而所有测试仍然是绿的。
    """
    connection = sqlite3.connect(str(tmp_path / "empty.db"))
    try:
        with pytest.raises(sqlite3.OperationalError):
            discard_thread_checkpoints(_FakeSaver(connection), "whatever")
    finally:
        connection.close()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_job_discard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.storage.job_discard'`

- [ ] **Step 3: 写实现**

新建 `app/storage/job_discard.py`：

```python
from __future__ import annotations

import sqlite3

# ─────────────────────────────────────────────────────────────────────────────
# 丢弃一个"从未成立"的岗位（m1-job-profile-intake tasks 5.3）。
#
# 为什么是"落后即删"而不是"先判再建"：job_profile.job_id 有外键指向 job，
# 且连接上 PRAGMA foreign_keys = ON。而 is_job_related 要跑完 compute 才知道，
# 那时同一次 graph.invoke 里的 effect_persist_draft 已经要写 job_profile 了——
# 先判后建在结构上做不到，除非把建 job 行挪进 effect 节点（那要改
# app/graph/nodes.py，超出本交付单元边界）。
#
# ⛔ 不要改成"标一个 status 了事"。业务经理的岗位列表走 LEFT JOIN
# （app/storage/job_queries.py 的 latest_profile_rows，注释写明是刻意的），
# 任何留下来的 job 行都会真的出现在列表里；再加一个状态去过滤，就等于
# 把"没建岗位"实现成了"建了个看不见的岗位"——两者在 PIPL 的数据最小化
# 与业务经理的直觉上都不是一回事。
# ─────────────────────────────────────────────────────────────────────────────

# 删除顺序：子表先于父表。全库只有 job_profile 一条外键指向 job
# （app/storage/db.py:16），顺序错了会直接 IntegrityError。
_BUSINESS_DELETES: tuple[tuple[str, str], ...] = (
    ("job_profile", "job_id"),
    ("conversation", "thread_id"),
    ("outbox", "thread_id"),
    # effect_log 必须一起删。铁律1 的 reviewer 判据是"每个 effect_* 节点的
    # effect_log 条数与其业务表行数按 thread 恒等"；业务行没了、幂等记录还在，
    # 这条不变式当场破，且破得完全没有症状。
    ("effect_log", "thread_id"),
    ("job", "id"),
)

# ⛔ analysis_run **不在**上面这张表里，这是刻意的。那次模型调用真实发生过：
# 铁律3/4 要求每一次调用的模型标识、版本、prompt 版本、输入哈希、原始响应
# 可解释可审计，PIPL 第 24 条的说明权也建立在它上面。岗位可以当作从未成立，
# "我们调过一次模型"这个事实不可以。analysis_run.job_id 是裸 TEXT、无外键，
# 留一个指不到 job 的 job_id 不会破坏任何约束。


def discard_unstarted_job(conn: sqlite3.Connection, job_id: str) -> None:
    """把这一轮为 ``job_id`` 写下的业务行整体抹掉，一个事务提交一次。

    只在"首轮就判定不是用人需求"这一种情形下调用。⛔ 不要用它删已经开始
    追问的岗位——那是"放弃"（effect_abandon_profile），语义完全不同：放弃
    要保留已采集内容并留痕，这里是让记录从未存在过。
    """
    for table, column in _BUSINESS_DELETES:
        conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (job_id,))
    conn.commit()


def discard_thread_checkpoints(saver, thread_id: str) -> None:
    """删掉 SqliteSaver 为该 thread 留下的 checkpoint 与 writes 行。

    ``saver`` 按鸭子类型使用：只碰它的 ``.lock`` 与 ``.conn``，⛔ 不 import
    langgraph——storage 层不该依赖编排框架。

    ⚠️ **⛔ 不要改成 saver.delete_thread()**：那个方法在
    ``langgraph-checkpoint-sqlite==2.0.6``（requirements.txt 钉死的版本）里
    是 ``raise NotImplementedError``，2026-09-08 对 venv 内实际安装版本反射
    确认过。这里直接对它自己那两张表发 DELETE，用的是库本身的加锁方式
    （``with saver.lock, saver.conn``）。

    表名改了就让它抛 sqlite3.OperationalError，⛔ 不要 try/except 兜住：
    静默空转的症状是 checkpoint 残留悄悄回来，而所有测试仍然是绿的。
    """
    with saver.lock, saver.conn:
        saver.conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        saver.conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_job_discard.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add app/storage/job_discard.py tests/test_job_discard.py
git commit -m "feat(storage): discard_unstarted_job/discard_thread_checkpoints（tasks 5.3）"
```

---

### Task 3: `_run_turn` 把 L3 的 `is_job_related` 透给编排层

**Files:**
- Modify: `app/web/server.py`（`:129` `_run_turn`；调用点 `:273` `create_job`、`:287` `reply`、`:476` `revise`）
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `graph.invoke(state, config=...)` 的返回值（合并终态，含 `compute_intake_turn` 写入的 `is_job_related`）
- Produces: 模块内 `class TurnOutcome(NamedTuple): message: dict; is_job_related: bool`；`_run_turn(job_id, message) -> TurnOutcome`。三个调用点改用 `.message`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_web_api.py`：

```python
def test_off_topic_turn_calls_the_model_exactly_once(tmp_path):
    """server 只按 L3 的结论分流，⛔ 不再调一次模型做二次判断。

    call_count > 1 就说明编排层自己又问了一遍"这算不算用人需求"——那是
    第二个判定器，两个判定器迟早会给出不同答案，而分歧没有任何症状。
    """
    responses = [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})]
    client, scripted = make_app_with_scripted_client(tmp_path, responses)

    resp = client.post("/api/jobs", json={"message": "今天中午吃什么"})

    assert resp.status_code == 200
    assert scripted.chat.completions.call_count == 1
```

- [ ] **Step 2: 跑测试确认它当前的状态**

Run: `venv/bin/python -m pytest tests/test_web_api.py::test_off_topic_turn_calls_the_model_exactly_once -v`
Expected: PASS（今天就没有二次调用）。**这是一条防回归的护栏**，先落地它，Task 4 改建单路径时它才有意义。若此步意外 FAIL，停下来先查清楚，⛔ 不要绕过。

- [ ] **Step 3: 改 `_run_turn` 的返回类型**

在 `app/web/server.py` 顶部 import 区加 `from typing import NamedTuple`（若已有 `typing` 导入则合并），并在 `create_app` **之外**、模块级定义：

```python
class TurnOutcome(NamedTuple):
    """一轮采集的结果：给通道的消息 + L3 判定的"这是不是用人需求"。

    is_job_related 是 **L3 纯函数 run_intake_turn 的输出**，经 compute_intake_turn
    放进 state、由 graph.invoke() 的终态原样带回来。编排层只按它分流，
    ⛔ 不在 server 里再调一次模型做二次判断（tasks 5.3 的硬边界）——两个
    判定器迟早会给出不同答案，而分歧没有任何症状。
    """

    message: dict
    is_job_related: bool
```

把 `_run_turn` 的签名与结尾改成：

```python
    def _run_turn(job_id: str, message: str) -> TurnOutcome:
```

```python
        # 终态要接住，⛔ 不要再丢掉：is_job_related 只有这一个合法来源。
        final_state = graph.invoke(state, config={"configurable": {"thread_id": job_id}})

        latest = channel.latest(job_id)
        return TurnOutcome(
            message={"type": latest.type, "payload": _response_payload(latest)},
            # 默认 True：判定没接上时按"是用人需求"算，与 compute 节点里
            # is_productive 的默认口径一致——保守方向是**保留**记录，
            # 不是悄悄删掉一个真实岗位。
            is_job_related=bool(final_state.get("is_job_related", True)),
        )
```

- [ ] **Step 4: 更新三个调用点**

`create_job`（`:273` 附近）、`reply`（`:287` 附近）、`revise`（`:476` 附近）三处的

```python
        message = _run_turn(job_id, ...)
```

一律改为

```python
        message = _run_turn(job_id, ...).message
```

本 Task 只做这一步的机械替换，**行为逐字不变**；`create_job` 的分流在 Task 4 做。

- [ ] **Step 5: 跑全套 web 测试**

Run: `venv/bin/python -m pytest tests/test_web_api.py tests/test_jd_endpoints.py tests/test_job_views_api.py tests/test_approval_branches.py -v`
Expected: 全部 PASS，一条不少（本 Task 不改变任何外部可观察行为）

- [ ] **Step 6: Commit**

```bash
git add app/web/server.py tests/test_web_api.py
git commit -m "refactor(server): _run_turn 返回 TurnOutcome，透出 L3 的 is_job_related（tasks 5.3）"
```

---

### Task 4: `POST /api/jobs` 首轮非用人需求 → 不留岗位记录

**Files:**
- Modify: `app/web/server.py`（`create_job`，`:264` 附近）
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 2 的 `discard_unstarted_job` / `discard_thread_checkpoints`；Task 3 的 `TurnOutcome.is_job_related`
- Produces: `POST /api/jobs` 的响应契约——判非用人需求时 `{"job_id": None, "message": {...引导语...}}`；判是用人需求时**与今天逐字一致**

- [ ] **Step 1: 写失败测试**

```python
def _table_counts(db_path):
    check = sqlite3.connect(db_path)
    try:
        return {
            "job": check.execute("SELECT COUNT(*) FROM job").fetchone()[0],
            "job_profile": check.execute("SELECT COUNT(*) FROM job_profile").fetchone()[0],
            "conversation": check.execute("SELECT COUNT(*) FROM conversation").fetchone()[0],
            "outbox": check.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],
            "effect_log": check.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0],
            "checkpoints": check.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0],
            "writes": check.execute("SELECT COUNT(*) FROM writes").fetchone()[0],
        }
    finally:
        check.close()


def test_off_topic_first_message_creates_no_job_record(tmp_path):
    """spec「需求描述为空或与招聘无关」：回引导语 **AND** 不创建岗位记录。

    只断言 job 表是不够的：库里还留着 job_profile / conversation / outbox /
    effect_log / checkpoint 就等于"岗位记录还在，只是列表里看不见"。
    """
    from app.agents.intake_agent import _GUIDANCE_TEXT

    db_path = str(tmp_path / "web.db")
    responses = [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})]
    client, _ = make_app_with_scripted_client(tmp_path, responses)

    resp = client.post("/api/jobs", json={"message": "今天中午吃什么"})

    assert resp.status_code == 200
    body = resp.json()
    # 引导语照常回给用户 —— "不建记录"不等于"不回话"。
    assert body["message"]["type"] == "question"
    assert [q["text"] for q in body["message"]["payload"]["questions"]] == [_GUIDANCE_TEXT]
    # 没有岗位，就没有 job_id 可给。⛔ 不许回一个指向已删行的 id。
    assert body["job_id"] is None

    assert _table_counts(db_path) == {
        "job": 0, "job_profile": 0, "conversation": 0,
        "outbox": 0, "effect_log": 0, "checkpoints": 0, "writes": 0,
    }


def test_off_topic_first_message_leaves_the_job_list_empty(tmp_path):
    """岗位列表走 LEFT JOIN（job_queries.latest_profile_rows 的注释写明是刻意的）：
    只删 job_profile 不删 job，列表里会留一条「待确定 / drafting」的僵尸。"""
    responses = [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})]
    client = make_app(tmp_path, responses)

    client.post("/api/jobs", json={"message": "今天中午吃什么"})

    listing = client.get("/api/jobs")
    assert listing.status_code == 200
    assert listing.json()["jobs"] == []


def test_a_real_request_after_an_off_topic_one_starts_clean(tmp_path):
    """业务经理先随口说了句无关的，再正经提需求——第二条要能正常建岗位。"""
    db_path = str(tmp_path / "web.db")
    responses = [
        json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}}),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "是否涉及 AUTOSAR？"}],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
    ]
    client = make_app(tmp_path, responses)

    client.post("/api/jobs", json={"message": "今天中午吃什么"})
    second = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})

    assert second.status_code == 200
    job_id = second.json()["job_id"]
    assert job_id is not None
    counts = _table_counts(db_path)
    # 第一次那一轮的行一个都没剩下，第二次的行一个不少。
    assert counts["job"] == 1
    assert counts["job_profile"] == 1
    assert counts["conversation"] == 1
```

`_table_counts` 里的 `checkpoints` / `writes` 两张表由 SqliteSaver 在首次 invoke 时建出；若某个用例路径下它们尚不存在，用 `sqlite_master` 判存在后再计数，⛔ 不要 try/except 吞掉。

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_web_api.py -k off_topic_first_message -v`
Expected: FAIL —— `assert body["job_id"] is None` 收到一个真 uuid；表计数 `job == 1`

- [ ] **Step 3: 实现分流**

`app/web/server.py` 顶部 import 区加：

```python
from app.storage.job_discard import discard_thread_checkpoints, discard_unstarted_job
```

`create_job` 改为：

```python
    @router.post("/api/jobs")
    def create_job(req: CreateJobRequest):
        job_id = str(uuid.uuid4())
        # 先建行、后判定，是外键逼出来的顺序而不是选择：job_profile.job_id
        # 指向 job 且 PRAGMA foreign_keys=ON，而 is_job_related 要跑完 compute
        # 才知道——那时同一次 invoke 里的 effect_persist_draft 已经在写
        # job_profile 了。所以这里走"落后即删"（tasks 5.3 许可的形态之一）。
        conn.execute(
            "INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,)
        )
        conn.commit()
        outcome = _run_turn(job_id, req.message)

        if not outcome.is_job_related:
            # spec「需求描述为空或与招聘无关」：回引导语 **且不创建岗位记录**。
            # 消息在 _run_turn 里已经从 outbox 读出来了，删在后面不影响回执。
            #
            # ⚠️ 已知残留风险：INSERT 与这次删除分属两个事务（中间
            # graph.invoke 里的 idempotent_effect 必然提交），进程恰好崩在
            # 两者之间会留下一行零版本的 drafting job。这与今天"第一轮抛
            # 异常"留下的行是同一种，见 app/storage/job_queries.py 的
            # latest_profile_rows 注释。消除它要把建 job 行挪进
            # effect_persist_draft 的同一个事务，那要改 app/graph/nodes.py，
            # 超出本交付单元边界。⛔ 不要在这里加"定期清理僵尸行"的兜底
            # 逻辑掩盖它——那会把一个已登记的窗口变成一个隐形的窗口。
            discard_unstarted_job(conn, job_id)
            discard_thread_checkpoints(graph.checkpointer, job_id)
            # 没有岗位就没有 id 可给。⛔ 不要回那个已删的 uuid：前端会拿它
            # 去 POST /reply，撞上 404，错误信息与真正的原因毫无关系。
            return {"job_id": None, "message": outcome.message}

        return {"job_id": job_id, "message": outcome.message}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_web_api.py -v`
Expected: 全部 PASS（新增 3 条 + 既有全绿）

- [ ] **Step 5: 跑全量回归**

Run: `venv/bin/python -m pytest -q`
Expected: 全绿。特别留意 `tests/test_graph_idempotency.py`、`tests/test_transaction_ownership.py`、`tests/test_audit_assertions.py`、`tests/test_job_queries.py` —— 它们守着铁律 1 与审计断言。

- [ ] **Step 6: Commit**

```bash
git add app/web/server.py tests/test_web_api.py
git commit -m "feat(server): 首轮非用人需求不留岗位记录，job_id 回 null（tasks 5.3）"
```

---

### Task 5: 已有岗位的后续离题轮——回引导语、不改状态、不删记录

**Files:**
- Test only: `tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 1/3/4 的全部行为
- Produces: 无生产代码。本 Task 的产物是**反向保护**：证明 Task 4 的删除只作用于建单路径。

> **给实现者的提示**：这一半**预期本来就是对的**（`discard_*` 只在 `create_job` 里调用，`/reply` 走 `_run_turn(...).message`）。如果下面任何一条红了，说明 Task 4 的删除溢出到了 `/reply`——那是本单元最危险的一种失败：**它会删掉业务经理已经答了好几轮的真实内容**。⛔ 不许靠改测试让它绿。

- [ ] **Step 1: 写测试**

```python
def test_off_topic_reply_on_an_existing_job_keeps_everything(tmp_path):
    """已有岗位的后续轮次里冒出一句无关的话：回引导语，⛔ 不改 job 状态、
    ⛔ 不删已采集内容。这是 Task 4 那把"删除"的反向护栏——它一旦溢出到
    /reply，删掉的就是业务经理已经答了好几轮的真实内容。"""
    from app.agents.intake_agent import _GUIDANCE_TEXT

    db_path = str(tmp_path / "web.db")
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "是否涉及 AUTOSAR？"}],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
        json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}}),
    ]
    client = make_app(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"}).json()["job_id"]
    assert job_id is not None

    second = client.post(f"/api/jobs/{job_id}/reply", json={"message": "对了，食堂几点开饭"})

    assert second.status_code == 200
    assert [q["text"] for q in second.json()["message"]["payload"]["questions"]] == [_GUIDANCE_TEXT]

    check = sqlite3.connect(db_path)
    try:
        # 岗位还在，状态没被这句无关的话改掉。
        assert check.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()[0] == "drafting"
        # 已采集内容原样保留 —— 离题轮的 profile_patch 是 {}，累积画像不变。
        rows = check.execute(
            "SELECT profile_json, is_productive FROM job_profile WHERE job_id=? ORDER BY version",
            (job_id,),
        ).fetchall()
        assert len(rows) == 2
        assert json.loads(rows[0][0])["job_title"] == "嵌入式软件工程师"
        assert json.loads(rows[1][0])["job_title"] == "嵌入式软件工程师"
        # 离题轮不消耗追问预算（is_productive=0），这是第 3 章既有口径。
        assert rows[1][1] == 0
    finally:
        check.close()


def test_blank_reply_on_an_existing_job_does_not_call_the_model(tmp_path):
    """空白回复走 L3 的短路，同样不建/不删任何东西。"""
    from app.agents.intake_agent import _GUIDANCE_TEXT

    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "是否涉及 AUTOSAR？"}],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        )
        # 只有一条：第二轮真调了模型就会 IndexError
    ]
    client, scripted = make_app_with_scripted_client(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"}).json()["job_id"]
    second = client.post(f"/api/jobs/{job_id}/reply", json={"message": "   "})

    assert second.status_code == 200
    assert [q["text"] for q in second.json()["message"]["payload"]["questions"]] == [_GUIDANCE_TEXT]
    assert scripted.chat.completions.call_count == 1
```

- [ ] **Step 2: 跑测试**

Run: `venv/bin/python -m pytest tests/test_web_api.py -k "off_topic_reply or blank_reply" -v`
Expected: PASS。红了就回头查 Task 4，⛔ 不要改这里的断言。

- [ ] **Step 3: Commit**

```bash
git add tests/test_web_api.py
git commit -m "test(server): 已有岗位的后续离题轮不改状态、不删内容（tasks 5.3）"
```

---

### Task 6: 调用方对 `job_id = null` 的显式处理

**Files:**
- Modify: `app/web/static/index.html`（`:461-468` 的 send 处理器）
- Modify: `scripts/replay_pilot_sessions.py`（`:268`）
- Test: `tests/test_static_frontend.py`

**Interfaces:**
- Consumes: Task 4 的响应契约（`job_id` 可能是 `null`）
- Produces: 无新接口。前端在 `job_id === null` 时保持"还没有岗位"的状态，下一条消息重新走 `POST api/jobs`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_static_frontend.py`：

```python
def test_frontend_keeps_no_job_when_the_server_returns_null_job_id():
    """离题的第一句话不建岗位，服务端回 job_id: null。

    前端必须**保持**"还没有岗位"的状态，下一条消息重新走 POST api/jobs。
    原写法 `if (!jobId) jobId = data.job_id;` 碰巧也是对的（null 仍是假值），
    但看不出这是有意的——下一个人把它改成无条件赋值，行为一样对；再改成
    `jobId = data.job_id ?? jobId` 之类就开始出错，而且不会有任何东西变红。
    """
    assert "if (!jobId && data.job_id)" in INDEX_HTML
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_static_frontend.py::test_frontend_keeps_no_job_when_the_server_returns_null_job_id -v`
Expected: FAIL

- [ ] **Step 3: 改前端**

`app/web/static/index.html`，把

```javascript
      if (!jobId) jobId = data.job_id;
```

改为

```javascript
      // job_id 可能是 null —— 服务端判定这句话不是用人需求，没有建岗位记录
      // （tasks 5.3）。这时**保持** jobId 为空，下一条消息重新走 POST api/jobs。
      // ⛔ 不要无条件赋值：那会把 jobId 写成 null 之外的语义假象，也让这条
      // 分支看起来像是"没考虑过"。
      if (!jobId && data.job_id) jobId = data.job_id;
```

⛔ 这一段不动任何 URL 字面量——`api/jobs` 等相对路径写法逐字保留（部署约束 1，判据在 `tests/test_static_frontend.py` 既有用例）。

- [ ] **Step 4: 改回放脚本**

`scripts/replay_pilot_sessions.py:268`：

```python
        job_id = response.json()["job_id"]
```

改为：

```python
        job_id = response.json()["job_id"]
        if job_id is None:
            # 服务端判定第一句话不是用人需求，没建岗位（tasks 5.3）。
            # ⛔ 不要带着 None 往下走：拼出来的 /api/jobs/None/reply 会 404，
            # 错误信息与真正的原因毫无关系。
            raise RuntimeError(
                f"回放的第一句话被判定为非用人需求，没有建出岗位，无法继续回放：{first!r}"
            )
```

- [ ] **Step 5: 跑测试**

Run: `venv/bin/python -m pytest tests/test_static_frontend.py -v && venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add app/web/static/index.html scripts/replay_pilot_sessions.py tests/test_static_frontend.py
git commit -m "feat(web): 前端与回放脚本显式处理 job_id=null（tasks 5.3）"
```

---

## 收尾：回勾 WBS

全部 Task 的两阶段 review 通过后，把 `openspec/changes/m1-job-profile-intake/tasks.md` 的

```
- [ ] 5.3 需求识别：区分"是用人需求"与"无关消息"，后者回引导语且不建岗位记录
```

勾成 `- [x]`，并在条目后追加落地说明（指向 `app/agents/intake_agent.py` 的确定性引导语与空输入短路、`app/storage/job_discard.py`、`app/web/server.py` 的 `create_job` 分流，以及本文件路径）。

⛔ 由 `run-build` 在 final review 之后回勾，本计划不代勾。
