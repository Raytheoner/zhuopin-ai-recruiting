# M1 采集质量修复 · 交付单元 D（未指定字段推导与确认前警示）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"这份画像还缺什么"从**模型顺口说的**改成**系统按字段表算出来的**，并在业务经理点下"确认画像"之前，用中文、显著、躲不开的方式把缺口摆到他面前，逼他在"回去补答"与"知道有缺口仍然确认"之间做一次显式选择，且这次选择被持久化、事后可查。

**Architecture:** 新增纯函数 `derive_unspecified_fields(accumulated) -> list[str]`（L3 无副作用，遍历 `JobProfile` 的 JSON Schema 属性），成为未指定字段的**唯一真源**；模型自由生成的同名列表降级为**对照数据**，只走一条经 `loggable_summary()` 脱敏的 debug 日志 + `job_profile.unspecified_fields` 那一列（DDL 注释已写明这一列此后是"对照"），永不参与判定、永不进对外 payload。中文名映射与字段定义同模块（`app/schemas/job_profile.py`），API 在返回英文标识的同时返回中文名，前端只渲染中文。确认接口新增 `acknowledged_gaps` 请求体标记，有缺口而未知情时返回 409；确认成功时把"缺口清单 + 知情标记 + 时刻"写进 `job_profile.profile_json` 的下划线前缀内部键（与 `_jd_text` 同位置，**不新建表**）。

**Tech Stack:** Python 3.14.6（`./venv`）· pydantic 2.13.4 · FastAPI 0.115.6 · LangGraph ≥ 1.0.10 + SqliteSaver · SQLite（`data/demo.db`）· pytest 8.3.4 · 原生 DOM 单文件前端（无构建、无 npm）

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-25 版）的「工程铁律」「合规红线」「部署约束」与 `openspec/changes/m1-intake-quality-fixes/delivery-units.md` §5 **逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。

> **本单元与这条的关系**：D **不新增任何 `effect_*` 节点**。6.9 的知情留痕必须写进**已经存在的** `effect_confirm_profile` 里、与它的 `UPDATE ... status='approved'` 落在同一条事务里——不得为留痕另起一个 effect、也不得在 HTTP handler 里裸 `conn.execute` + `commit`。reviewer 判据：`tests/test_graph_idempotency.py` / `tests/test_transaction_ownership.py` 全绿，且 D 的 diff 里 `@idempotent_effect` 装饰器的数量与 main 上完全相同。

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这条的关系**：`derive_unspecified_fields()` 落在 `app/agents/intake_agent.py`（L3），**必须是纯函数**——不读库、不写库、不发消息、同一输入必然同一输出（spec 的 `Scenario: 推导结果稳定` 就是这条铁律的可测形态）。那条 debug 日志是唯一的例外通道，且日志不改变返回值。

5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。

> **本单元与这条的关系**：D **不改 `SYSTEM_PROMPT` 一个字**，因此 **`prompt_version` 保持单元 B 留下的 `intake-v4` 不动**。6.2 停止的是"把模型输出当结果用"，不是"不再向模型要这个字段"——那份对照数据还要继续采集，提示词里 `unspecified_fields(string[], 可选)` 这一句原样保留。reviewer 判据：D 的 diff 里不出现 `SYSTEM_PROMPT` 与 `prompt_version` 的任何改动；若有人顺手删了提示词里那一句，`input_hash` 会变而版本没升，铁律 5 当场破。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。

> **本单元与这条的关系**：6.7 的"知道有缺口、仍然确认"就是一个**人工确认节点**，6.9 就是它的**留痕**。系统不得代替业务经理做这个选择——⛔ 前端不得给"仍然确认"预选、不得在超时后自动确认、不得在 `acknowledged_gaps` 缺省时按 `true` 处理。缺省一律是 `false`（= 未知情 = 不放行）。

- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。

> **本单元与这条的关系**：警示区块里的字段中文名与后果说明**是系统按字段表算出来的，不是 AI 生成内容**，因此**不加**"AI 建议"标识——那会把一条确定性事实伪装成建议，反而削弱它。单元 C 给档位加的 `AI_OPTIONS_HINT` 只属于 `options`，不得被复制到警示块上。

- **模型全部走境内**，简历数据不出境。

> **本单元与这条的关系**：Task 1 要把 `.51` 上两个真实会话的画像取回本仓库当测试基准。**只取 `job_profile` 表的画像字段，⛔ 不取 `conversation` 表的对话原文、⛔ 不取任何人名。** 这是业务经理写的岗位需求，不是候选人简历，不触及 M2 的"登录 + 访问留痕"门槛；但 `.gitignore` 把 `data/` 整目录列为硬红线，所以取回的数据**必须落在 `tests/fixtures/` 而不是任何名为 `data/` 的目录**（`data/` 这条 gitignore 规则匹配任意层级的同名目录，落错地方会被静默排除、commit 里根本没有这个文件）。

### 部署约束

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。

> `tests/test_static_frontend.py::test_index_html_has_no_absolute_paths` 扫描 `index.html` 里**每一个**引号/反引号字符串字面量，任何一段以 `/` 开头即失败——新增的 CSS 类名、按钮文案、DOM id 同样受这条约束。

5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。

> D 不处理简历。6.9 的留痕是**画像确认**的留痕，与这条不是同一件事，不要把它当成 M2 门槛已达成的证据。

### 跨单元接口约定（`delivery-units.md` §5，逐字）

1. **F 的 `profile_patch` 结构升级不得穿透到 `profile_json`** —— 落库前拍平成裸值，理由见 §2.F

> 反向读：**D 的 `derive_unspecified_fields` 只认裸值。** 它拿到 `{"value": null, ...}` 会判成"这个字段有值"，漏报当场回到今天的故障。D 不需要为 F 预留兼容分支（F 排在 D 之后，且 §5.1 要求 F 自己拍平），但 D 的 docstring 里必须写明这个前提，让 F 的实现者读得到。

2. **C 的点选提交不改 API 契约** —— 否则失去 B ∥ C 的并行，理由见 §2.C

> C 已合并（`f3930ff`）。D 会碰 `app/web/server.py` 与 `index.html`，**但仍然不得给 `ReplyRequest` 加字段**——D 新增的 `acknowledged_gaps` 只加在 `POST /confirm` 上，`POST /reply` 的请求体逐字不变。

5. **每个单元开工前必须 rebase 到最新 main** —— `app/agents/intake_agent.py` 与 `app/graph/nodes.py` 被 B/D/E/F 四个单元连续改动，是本批最热的两个文件

> **这是 D 的第一等约束，见下方「开工前置检查」。** D 与 B 在 `intake_agent.py` / `graph/nodes.py` / `web/server.py` / `index.html` 四处全线重叠，**B 未合并即开工必然产生一次手工合并**，而 B 恰好改了 D 要改的那几行（`_SYSTEM_MANAGED_FIELDS` 的归属、`run_intake_turn` 的返回、`effect_persist_draft` 的 INSERT 列表）。

### 明确不适用（reviewer 不必在本单元追这几条）

- 铁律 3（AI 评分持久化）、铁律 4（`evidence_ref` 非空）：本单元不写 `criterion_score`，代码库中亦无该表。
- 铁律 6（企微回调先落库）、铁律 7（`langgraph >= 1.0.10`）：本单元不接企微通道、不动依赖版本。
- 部署约束 2（8095 端口）、3（鉴权空壳）、4（Windows + venv，不引入容器）：本单元不改端口、不动鉴权中间件、不引入任何新依赖。
- 合规红线「禁止人脸/表情分析」「绝不用历史录用结果做监督信号」「候选人一次性邀请链接」：本单元不涉及。

---

## 开工前置检查（Task 1 之前，做不到就停下报告）

- [ ] `git fetch origin && git rebase origin/main`，工作区干净
- [ ] 确认**单元 B 已合并**：`grep -n "SYSTEM_MANAGED_FIELDS" app/schemas/job_profile.py`
      - 有输出 → B 已合并，按本计划执行
      - 无输出 → **B 还没合并，⛔ 停下报告，不要开工。** `delivery-units.md` §6 的执行顺序是 `B∥C → D`；提前开工的代价不是"多一次合并"，是 D 的 Task 3/5 要改的那几行在 B 的分支上正在被同时改写
- [ ] 确认 C 已合并：`grep -c "AI_OPTIONS_HINT" app/web/static/index.html` 应为 ≥ 1
- [ ] 基线全绿：`./venv/bin/python -m pytest -q`，记下用例总数（D 的每个 Task 结束时只许增不许减）

---

## 交付单元边界

**本单元 = `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 6 章（6.1–6.10），共 10 项。**
对应 `specs/intake-completeness-warning/spec.md` 的全部三条 Requirement。设计依据：`design.md` 决策 6、7、8。

**Task 数量说明**：`delivery-units.md` §1 预估 5-6 个 plan Task，本计划实际 **7 个**。多出来的一个是把「编排层落库」（Task 5）与「confirm 契约 + 知情留痕」（Task 6）拆成两个 review gate——它们改的是两条不同的路径（每轮都跑的采集路径 vs 只跑一次的确认路径），失败模式不同、回滚粒度不同，合在一个 gate 里 reviewer 会顾此失彼。

### 触碰面（硬边界）

| 文件 | 性质 | 谁还会碰它 |
|---|---|---|
| `app/agents/intake_agent.py` | 新增 `derive_unspecified_fields()` + 对照日志 | E（5.x）、F（7.x），**都排在 D 之后** |
| `app/schemas/job_profile.py` | 新增 `FIELD_LABELS` / `field_label()` | 无（B 已加 `SYSTEM_MANAGED_FIELDS`，D 只读不改） |
| `app/graph/state.py` | 新增一个 state 键 | E、F |
| `app/graph/nodes.py` | `compute_intake_turn` 分流 + `effect_persist_draft` 多写一列 + `effect_confirm_profile` 写留痕 | E、F |
| `app/graph/build.py` | `confirmation_prompt` payload 加中文名 | 无 |
| `app/web/server.py` | `confirm` 加 `acknowledged_gaps` / 409 / 留痕组装 | E |
| `app/web/static/index.html` | 警示区块 + 两个动作 | E（5.4 重问视觉区分） |
| `tests/fixtures/pilot-replay-profiles.json` | **新建**，真实回放基准 | 无 |
| `tests/test_intake_agent.py` `tests/test_job_profile_schema.py` `tests/test_graph_nodes.py` `tests/test_web_api.py` `tests/test_static_frontend.py` `tests/test_log_redaction.py` | 测试 | E、F |
| `docs/tech-debt.md` | 登记一条 TD | 任何人 |

### 本单元不做的事

| 不做 | 属于谁 |
|---|---|
| 判断字段值"是不是敷衍"（`experience_years="不限"`） | 谁都不做。`design.md` 决策 6「代价」段已接受：本变更的目标是不再漏报，不是判断质量 |
| 重问超限 → 目标字段计入未指定字段 | 单元 E（5.5）。D 合并后这条**自动成立**：字段没值，`derive_unspecified_fields` 自然列进去 |
| 字段溯源、`ungrounded_fields` 落库 | 单元 F（第 7 章） |
| 删掉 `job_profile.unspecified_fields` 这一列 / `JobProfile.unspecified_fields` 这个字段 | 谁都不做。Task 7 登记为技术债，删列属迁移动作 |
| 改 `SYSTEM_PROMPT` / 升 `prompt_version` | 谁都不做（铁律 5，见 Global Constraints） |

---

## File Structure

| 文件 | 职责 |
|---|---|
| `app/schemas/job_profile.py` | 字段定义 + `SYSTEM_MANAGED_FIELDS`（B 已加）+ **`FIELD_LABELS` / `field_label()`（D 新增，紧邻字段定义）**。中文名与字段定义同生共死是决策 7 的全部要点 |
| `app/agents/intake_agent.py` | L3 纯函数层：**`derive_unspecified_fields()`（真源）** + `_log_unspecified_comparison()`（对照日志，唯一的 logging 出口） |
| `app/graph/state.py` | 新增 `model_claimed_unspecified_fields`，让"推导的"与"模型自称的"在 state 里就是两个键，物理上不可能混淆 |
| `app/graph/nodes.py` | L4 编排层：分流两个列、`effect_confirm_profile` 写知情留痕 |
| `app/graph/build.py` | `confirmation_prompt` payload 同时带英文标识与中文名 |
| `app/web/server.py` | `confirm` 的 409 门禁与留痕组装（HTTP 层只组装，不写库） |
| `app/web/static/index.html` | 警示区块与两个动作，只渲染中文名 |
| `tests/fixtures/pilot-replay-profiles.json` | `.51` 真实会话画像快照 + 取数出处，6.3 的反证基准 |

---

### Task 1: 取回 `.51` 的真实回放画像，落成测试基准

**Files:**
- Create: `tests/fixtures/pilot-replay-profiles.json`
- Test: `tests/test_intake_agent.py`（新增一个 provenance 自检用例）

**Interfaces:**
- Consumes: 无（本 Task 不碰生产代码）
- Produces: `tests/fixtures/pilot-replay-profiles.json`，结构为
  `{"_provenance": {...}, "sessions": {"<job_id 前 8 位>": {"job_id": str, "version": int, "profile_json": dict, "model_unspecified_fields": list[str]}}}`。
  Task 2 的 6.3 反证测试**只**从这里取数。

**为什么这是一个独立 Task**：6.3 要求"用真实回放数据反证，不要造假数据"。真值在 `.51` 的 `data/demo.db` 里，本机 `data/demo.db` **没有**这三个会话（已核对：`a478499c` / `19b6ec6d` / `2494103e` 在本机库里查不到任何行）。手写一份"看起来像那次会话"的画像去喂测试，等于用自己编的答案验证自己写的推导——那不叫反证。

**⚠️ `.51` 不可达时的预案**：本 Task 挂起并如实报告，按 **Task 3 → 4 → 5 → 6 → 7** 的顺序继续，最后回补 Task 1 与 Task 2 的回放半边。⛔ **不得**用手写的合成画像顶替真值把 Task 2 标成完成；⛔ 不得把 `tasks.md` 的 6.3 勾上。

- [ ] **Step 1: 把 `.51` 的库拷回本机临时目录（只读取，不在服务器上改任何东西）**

```bash
mkdir -p /tmp/pilot-replay
scp zp51:'C:/apps/zhuopin-recruit-agent/data/demo.db' /tmp/pilot-replay/demo.db
# WAL/SHM 可能不存在（服务重启后已合并进主库），拷不到不算失败，继续
scp zp51:'C:/apps/zhuopin-recruit-agent/data/demo.db-wal' /tmp/pilot-replay/ || true
scp zp51:'C:/apps/zhuopin-recruit-agent/data/demo.db-shm' /tmp/pilot-replay/ || true
ls -la /tmp/pilot-replay/
```

预期：`demo.db` 存在且大于 0 字节。⚠️ **必须连 `-wal` 一起拷**——`.51` 上的 SQLite 走 WAL，只拷主库会读到一个落后于现网的快照（2026-08-13 的 findings 就是在这个坑上取的证）。

- [ ] **Step 2: 抽出两个会话的最终画像与模型当时自称的未指定字段**

```bash
./venv/bin/python - <<'PY'
import json, sqlite3
from pathlib import Path

# 打开的是拷贝，不是现网库；不加 mode=ro，让 SQLite 正常回放 -wal。
conn = sqlite3.connect("/tmp/pilot-replay/demo.db")
sessions = {}
for prefix in ("a478499c", "19b6ec6d"):
    row = conn.execute(
        "SELECT id, job_id, version, profile_json, unspecified_fields "
        "FROM job_profile WHERE job_id LIKE ?||'%' ORDER BY version DESC LIMIT 1",
        (prefix,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"{prefix} 在 .51 的库里查不到任何 job_profile 行——停下报告，不要编数据")
    _id, job_id, version, profile_json, unspecified = row
    sessions[prefix] = {
        "job_id": job_id,
        "version": version,
        "profile_json": json.loads(profile_json),
        "model_unspecified_fields": json.loads(unspecified or "[]"),
    }
    print(prefix, "version=", version, "模型自称=", sessions[prefix]["model_unspecified_fields"])

payload = {
    "_provenance": {
        "source": "192.168.100.51:C:/apps/zhuopin-recruit-agent/data/demo.db（含 -wal），scp 拷回后本地读取",
        "table": "job_profile，每个 job 取 version 最大的一行",
        "captured_at": "2026-08-25",
        "why": "openspec/changes/m1-intake-quality-fixes/tasks.md 6.3 要求用真实回放数据反证推导结果",
        "scope": "只含 job_profile 的画像字段与模型自称的未指定字段；⛔ 不含 conversation 表的对话原文、不含任何人名",
    },
    "sessions": sessions,
}
out = Path("tests/fixtures/pilot-replay-profiles.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("written:", out)
PY
```

预期输出里 `a478499c` 的 `模型自称=` 是 `[]`（这就是 `design.md` 决策 6 说的"漏报"），`19b6ec6d` 的 `模型自称=` 里含 `functional_safety` 与 `sop_projects`（"虚报"）。

**⚠️ 如果实际输出与上面不符**：不要改脚本、不要挑别的行去凑。停下来把真实输出如实报告——那说明 `design.md` 决策 6 的举证与真值对不上，是一次需要决策人参与的契约问题，不是一个可以就地绕过的测试障碍。

- [ ] **Step 3: 人工过一遍脱敏边界，然后写 provenance 自检测试**

```bash
grep -c "姚祖怡\|history_json\|role\": \"user" tests/fixtures/pilot-replay-profiles.json
```

预期：`0`。非 0 就说明抽多了，回到 Step 2 收窄字段。

```python
# tests/test_intake_agent.py 末尾追加
import json
from pathlib import Path

_REPLAY_PATH = Path(__file__).parent / "fixtures" / "pilot-replay-profiles.json"


def _replay(prefix: str) -> dict:
    """读取 .51 真实会话画像快照。取数出处见文件里的 _provenance 段。"""
    return json.loads(_REPLAY_PATH.read_text(encoding="utf-8"))["sessions"][prefix]


def test_replay_fixture_carries_provenance_and_no_dialogue_text():
    """
    这份基准的价值全部来自"它是真的"。没有出处的快照与手写的假数据无法区分，
    半年后没人说得清它是从哪来的——那时 6.3 就退化成"用自己编的答案验证自己
    写的推导"。同时守住脱敏边界：只允许画像字段进仓库，对话原文与人名不进。
    """
    raw = _REPLAY_PATH.read_text(encoding="utf-8")
    payload = json.loads(raw)

    provenance = payload["_provenance"]
    assert "192.168.100.51" in provenance["source"]
    assert provenance["captured_at"]
    assert "job_profile" in provenance["table"]

    assert set(payload["sessions"]) == {"a478499c", "19b6ec6d"}
    for prefix, session in payload["sessions"].items():
        assert session["job_id"].startswith(prefix)
        assert isinstance(session["profile_json"], dict)
        assert isinstance(session["model_unspecified_fields"], list)

    # 对话原文与人名一律不得进仓库
    assert "history_json" not in raw
    assert "姚祖怡" not in raw
```

- [ ] **Step 4: 运行自检测试**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py::test_replay_fixture_carries_provenance_and_no_dialogue_text -v`
Expected: PASS

- [ ] **Step 5: 清理临时目录并提交**

```bash
rm -rf /tmp/pilot-replay
git add tests/fixtures/pilot-replay-profiles.json tests/test_intake_agent.py
git commit -m "test(intake): 取回 .51 两个 pilot 会话的真实画像作为 6.3 反证基准"
```

⚠️ 只 `git add` 上面这两个路径。`git status` 里出现别人的改动是正常的，不要停、不要问、不要顺手提交。

---

### Task 2: `derive_unspecified_fields()` —— 未指定字段的唯一真源（6.1 + 6.3）

**Files:**
- Modify: `app/agents/intake_agent.py`（在 `_render_profile_field_guide()` 之后、`SYSTEM_PROMPT` 之前插入）
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Consumes: `app.schemas.job_profile.JobProfile`、`app.schemas.job_profile.SYSTEM_MANAGED_FIELDS`（单元 B 已加）、Task 1 的 `_replay()`
- Produces: `derive_unspecified_fields(accumulated: dict) -> list[str]` —— Task 3/5/6 全部调用它，**不许有第二份实现**

- [ ] **Step 1: 写失败的测试（spec 三条 Scenario + 真实回放反证）**

```python
# tests/test_intake_agent.py 顶部 import 处追加
from app.agents.intake_agent import derive_unspecified_fields
from app.schemas.job_profile import JobProfile


def test_derive_lists_every_business_field_for_empty_profile():
    """空画像 = 所有业务字段都未指定；系统管理字段不算业务字段。"""
    derived = derive_unspecified_fields({})

    assert "unspecified_fields" not in derived
    assert set(derived) == set(JobProfile.model_fields) - {"unspecified_fields"}


def test_answered_field_does_not_enter_unspecified():
    """spec Scenario: 已答字段不进未指定。"""
    derived = derive_unspecified_fields({"functional_safety": "ASIL-B"})

    assert "functional_safety" not in derived


def test_unanswered_field_is_never_missed():
    """spec Scenario: 未答字段不被遗漏。空容器、None、空白串、占位符都算未答。"""
    derived = derive_unspecified_fields(
        {
            "toolchain": [],
            "mcu_family": None,
            "project_experience_requirement": "   ",
            "department": "未指定",
            "job_title": "底层软件开发工程师",
        }
    )

    assert "toolchain" in derived
    assert "mcu_family" in derived
    assert "project_experience_requirement" in derived
    assert "department" in derived
    assert "job_title" not in derived


def test_derivation_is_stable_across_repeated_calls():
    """spec Scenario: 推导结果稳定。顺序也必须稳定——下游要直接渲染这个列表。"""
    accumulated = {"job_title": "嵌入式软件工程师", "toolchain": ["CANoe"], "core_skills": []}

    first = derive_unspecified_fields(accumulated)
    second = derive_unspecified_fields(dict(accumulated))

    assert first == second
    assert first == sorted(first, key=list(JobProfile.model_fields).index)


def test_internal_underscore_keys_are_ignored():
    """profile_json 里混着 _jd_text / _gap_acknowledgement 这类内部键，
    它们不在字段表里，既不该被当成已答字段，也不该被列进未指定。"""
    derived = derive_unspecified_fields({"_jd_text": "岗位职责……", "_gap_acknowledgement": {}})

    assert "_jd_text" not in derived
    assert "_gap_acknowledgement" not in derived
    assert "job_title" in derived


def test_derive_catches_what_the_model_underreported_in_a478499c():
    """
    真实回放反证（tasks 6.3）：`a478499c` 强制收尾时，模型给的 unspecified_fields
    是**空数组**——它宣称这份画像什么都不缺。系统推导必须给出非空结果，否则本章
    等于什么都没修。
    """
    session = _replay("a478499c")

    assert session["model_unspecified_fields"] == [], (
        "前置事实变了：这个会话模型当时给的不再是空数组。停下报告，不要改这条断言去迁就"
    )

    derived = derive_unspecified_fields(session["profile_json"])

    assert derived, "模型说没缺口，系统推导也说没缺口——漏报没有被修掉"


def test_derive_does_not_repeat_the_models_overreport_in_19b6ec6d():
    """
    真实回放反证（tasks 6.3）：`19b6ec6d` 里模型把用户**已经答过**的
    functional_safety / sop_projects 列进了未指定（虚报）。系统推导必须不重复这个错。
    """
    session = _replay("19b6ec6d")
    profile = session["profile_json"]

    # 前置事实：这两个字段在最终画像里确实有值（用户答过了）
    assert profile.get("functional_safety"), "前置事实变了，停下报告"
    assert profile.get("sop_projects"), "前置事实变了，停下报告"
    # 前置事实：模型当时确实把它们列进了未指定
    assert "functional_safety" in session["model_unspecified_fields"]

    derived = derive_unspecified_fields(profile)

    assert "functional_safety" not in derived
    assert "sop_projects" not in derived
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -k "derive or unspecified" -v`
Expected: FAIL — `ImportError: cannot import name 'derive_unspecified_fields' from 'app.agents.intake_agent'`

- [ ] **Step 3: 写最小实现**

在 `app/agents/intake_agent.py` 里，`PROFILE_FIELD_GUIDE = _render_profile_field_guide()` 这一行**之后**插入：

```python
# 画像里表示"这个字段系统填过、但不是用户定的"的占位符。app/web/server.py 的
# confirm 在必填字段缺失时就是拿它兜底的，所以推导必须认得它。
# 刻意只认这一个字面量，不去猜"未确定""待定""不限"之类的近义词——那已经是在
# 判断值的质量，而 design.md 决策 6 的「代价」段明确把质量判断排除在本章之外。
_UNSPECIFIED_PLACEHOLDER = "未指定"


def _is_unspecified_value(value) -> bool:
    """一个字段的取值是否等于"用户从未确定过"。"""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return stripped == "" or stripped == _UNSPECIFIED_PLACEHOLDER
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    # 数字/布尔等标量：有值就算已指定。headcount=0 进不来（JobProfile 有 ge=1），
    # is_mass_production=False 是一个真实的答案，不是"没答"。
    return False


def derive_unspecified_fields(accumulated: dict) -> list[str]:
    """
    未指定字段的**唯一真源**（tasks 6.1、design.md 决策 6）。

    遍历 JobProfile 的 JSON Schema 属性（排除系统管理字段），值缺失 / 为 None /
    为空容器 / 为空白串 / 等于占位符的，就是未指定。返回顺序 = 字段定义顺序，
    因此同一输入必然得到逐位相同的结果（spec 的「推导结果稳定」）。

    为什么不用模型给的那份：真实数据两个方向都错过——`a478499c` 强制收尾时模型
    给的是空数组（漏报），`19b6ec6d` 却把用户已经答过的 functional_safety /
    sop_projects 列了进去（虚报）。一个既会漏报又会虚报的列表比没有更糟：它让人
    以为"系统说没问题"。

    **入参必须是拍平后的裸值画像**（`{"headcount": 3}`，不是
    `{"headcount": {"value": 3, "source_quote": ...}}`）。第 7 章会把 profile_patch
    的字段升级成带来源的结构，`delivery-units.md` §5 约定 1 要求它在落库前拍平——
    没拍平的话本函数会把 `{"value": null, ...}` 当成"这个字段有值"，漏报当场回到
    今天的故障。

    profile_json 里混着的下划线内部键（`_jd_text`、`_gap_acknowledgement`）天然被
    忽略：本函数只看字段表里有的名字，不看入参里多出来的名字。
    """
    return [
        name
        for name in JobProfile.model_json_schema()["properties"]
        if name not in SYSTEM_MANAGED_FIELDS
        and (name not in accumulated or _is_unspecified_value(accumulated[name]))
    ]
```

同时把文件顶部的 import 补上（B 已经引入了 `SYSTEM_MANAGED_FIELDS`，确认这一行在）：

```python
from app.schemas.job_profile import JobProfile, SYSTEM_MANAGED_FIELDS
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -v`
Expected: PASS（含两条真实回放反证）

- [ ] **Step 5: 跑全量，确认没碰坏别人**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS，用例总数 = 前置检查记下的基线 + 本 Task 新增数

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py
git commit -m "feat(intake): derive_unspecified_fields 成为未指定字段真源（tasks 6.1/6.3）"
```

---

### Task 3: 模型输出降级为对照，且这条日志必须走 `loggable_summary()`（6.2）

**Files:**
- Modify: `app/agents/intake_agent.py`（`IntakeTurnResult`、`run_intake_turn` 的返回、新增模块 logger）
- Test: `tests/test_intake_agent.py`、`tests/test_log_redaction.py`

**Interfaces:**
- Consumes: Task 2 的 `derive_unspecified_fields()`；`app.observability.redaction.loggable_summary()`
- Produces:
  - `IntakeTurnResult.unspecified_fields: list[str]` —— 语义换人：**从此是系统推导值**
  - `IntakeTurnResult.model_claimed_unspecified_fields: list[str]` —— 模型自称值，只作对照
  - `_log_unspecified_comparison(accumulated, model_claimed, derived) -> None`

**⚠️ 这是本变更包里第一次把业务对象内容送进 logging**（`delivery-units.md` §3.3）。`loggable_summary()` 至今在生产代码里**一个调用点都没有**——它有单元测试，但从没真正上过岗。本 Task 就是它的上岗点。

**验收要求（逐字来自 §3.3）**：要有一条测试断言这条日志路径**确实调用了** `loggable_summary()`。不能只测"日志里没泄漏"——依据 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.3.1 的更正段：在没有调用点的情况下"0 命中"同时兼容"脱敏有效"和"脱敏根本没上岗"两种解释，那不叫验证。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_intake_agent.py
import logging

from app.agents import intake_agent


def test_model_claimed_unspecified_never_becomes_the_result():
    """
    tasks 6.2：模型自称的未指定字段不再进结果。这里模型虚报 functional_safety
    （用户本轮刚答了 ASIL-B），推导结果必须不含它；模型那份原样保留在对照字段里。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"functional_safety": "ASIL-B"},
                    "unspecified_fields": ["functional_safety", "sop_projects"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要 ASIL-B"}],
        round_count=1,
        profile_patch_accumulated={"job_title": "底层软件开发工程师"},
    )

    assert "functional_safety" not in result.unspecified_fields
    assert "toolchain" in result.unspecified_fields  # 真的没答的字段照样列出来
    assert result.model_claimed_unspecified_fields == ["functional_safety", "sop_projects"]


def test_unspecified_comparison_log_goes_through_loggable_summary(monkeypatch, caplog):
    """
    delivery-units.md §3.3 的验收要求：断言这条日志路径**确实调用了**
    loggable_summary()。只断言"日志里没泄漏"是不够的——没有调用点时"0 命中"
    同时兼容"脱敏有效"和"脱敏根本没上岗"两种解释（findings §8.3.1 更正段）。
    """
    calls = []
    real = intake_agent.loggable_summary

    def spy(obj, **kwargs):
        calls.append((dict(obj), kwargs))
        return real(obj, **kwargs)

    monkeypatch.setattr(intake_agent, "loggable_summary", spy)

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"job_title": "底层软件开发工程师"},
                    "unspecified_fields": ["toolchain", "根本不存在的字段"],
                }
            )
        ]
    )

    with caplog.at_level(logging.DEBUG, logger="app.agents.intake_agent"):
        run_intake_turn(gateway, history=[{"role": "user", "content": "招人"}], round_count=1)

    # 1) 确实调用了，而且是带 known_fields 的那种调用（键名本身也要过滤，
    #    因为模型可能幻觉出一个不存在的字段名）
    assert len(calls) == 2, "推导结果与模型自称各要过一次脱敏，一次都不能省"
    assert all("known_fields" in kwargs for _obj, kwargs in calls)

    # 2) 落到日志里的是摘要形态，不是业务对象本体
    text = caplog.text
    assert "field_count" in text and "unknown_field_count" in text
    assert "底层软件开发工程师" not in text
    # 3) 模型幻觉出的字段名只贡献计数，不贡献名字
    assert "根本不存在的字段" not in text
```

```python
# tests/test_log_redaction.py 末尾追加
def test_unspecified_comparison_log_does_not_trip_the_bypass_filter():
    """
    主防线上岗的另一半证据：这条日志经过 RedactionFilter 时必须**零命中**。
    命中意味着主防线被绕过（RedactionFilter 只是探测性的兜底），那时它会额外
    打一条 WARNING —— 那条 WARNING 存在本身就是故障信号，不是"脱敏起作用了"。
    """
    from app.observability.redaction import RedactionFilter, loggable_summary
    from app.schemas.job_profile import JobProfile

    known = frozenset(JobProfile.model_fields)
    accumulated = {"job_title": "底层软件开发工程师", "functional_safety": "ASIL-B", "toolchain": []}
    rendered = "未指定字段对照（tasks 6.2）：系统推导 %s；模型自称 %s" % (
        loggable_summary({"toolchain": accumulated["toolchain"]}, known_fields=known),
        loggable_summary(
            {k: accumulated.get(k) for k in ("functional_safety", "sop_projects")},
            known_fields=known,
        ),
    )

    record = logging.LogRecord("app.agents.intake_agent", logging.DEBUG, __file__, 1, rendered, (), None)
    assert RedactionFilter().filter(record) is True
    assert getattr(record, "redacted_fields", 0) == 0
    assert "底层软件开发工程师" not in record.getMessage()
    assert "ASIL-B" not in record.getMessage()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -k "model_claimed or loggable_summary" tests/test_log_redaction.py -k "bypass_filter" -v`
Expected: FAIL — `AttributeError: module 'app.agents.intake_agent' has no attribute 'loggable_summary'`

- [ ] **Step 3: 写实现**

`app/agents/intake_agent.py` 顶部 import 区补两行：

```python
import logging

from app.observability.redaction import loggable_summary
```

紧接 `derive_unspecified_fields` 之后插入：

```python
logger = logging.getLogger(__name__)

# 键名本身也要过一遍白名单：profile_patch 是 LLM 自由生成的裸 dict，
# 模型自称的"未指定字段"里可能出现一个它幻觉出来的字段名，那本身就是自由文本。
_JOB_PROFILE_FIELD_NAMES = frozenset(JobProfile.model_fields)


def _log_unspecified_comparison(
    accumulated: dict, model_claimed: list[str], derived: list[str]
) -> None:
    """
    把"模型自称的未指定字段"与"系统推导结果"的对照打进 debug 日志（tasks 6.2）。

    这是本变更包里第一次把业务对象内容送进 logging，**必须走 loggable_summary()**
    （delivery-units.md §3.3）。⛔ 不得写成
    `logger.debug("...%s", parsed.unspecified_fields)` 直接打——那绕过主防线，
    只会被 RedactionFilter 事后探测到并告警。

    刻意**不加** `logger.isEnabledFor(DEBUG)` 护栏：加了之后"脱敏是否真的上岗"
    就取决于运行时日志级别，而 §3.3 的验收要求正是要一条能无条件断言到的调用。
    代价是每轮多几次 dict 操作——相对一次 7~26 秒的 LLM 调用，可以忽略。

    传给 loggable_summary 的是「字段名 → 该字段当前取值」的映射，不是字段名列表：
    这样摘要里的 field_names 只会出现字段表里真实存在的名字，模型幻觉出来的名字
    落进 unknown_field_count 这个计数里，既留下了信号又不把那段自由文本写进日志。
    """
    logger.debug(
        "未指定字段对照（tasks 6.2）：系统推导 %s；模型自称 %s",
        loggable_summary(
            {name: accumulated.get(name) for name in derived},
            known_fields=_JOB_PROFILE_FIELD_NAMES,
        ),
        loggable_summary(
            {name: accumulated.get(name) for name in model_claimed},
            known_fields=_JOB_PROFILE_FIELD_NAMES,
        ),
    )
```

`IntakeTurnResult` 里，把原来那一行 `unspecified_fields` 的注释改掉并新增一个字段：

```python
    # 系统按画像字段表推导出的未指定字段（tasks 6.1）。**这是真源。**
    unspecified_fields: list[str] = field(default_factory=list)
    # 模型自称的未指定字段（tasks 6.2）。只作对照：落 job_profile.unspecified_fields
    # 那一列 + 一条 debug 日志。⛔ 不参与任何判定、不进任何对外 payload。
    model_claimed_unspecified_fields: list[str] = field(default_factory=list)
```

`run_intake_turn` 的**最后一个** `return IntakeTurnResult(...)`（`is_job_related=True` 那个分支）：在它之前插入三行，并改掉一个参数。

⚠️ 单元 B 在这个 return 里加过参数，**按下面的"改这一行"做定点修改，不要整块替换函数尾部**：

```python
    # 改前：
    #     unspecified_fields=parsed.unspecified_fields if give_up else [],
    # 改后（新增两行局部变量 + 换掉这一个参数 + 追加一个参数）：
    accumulated_after = {**(profile_patch_accumulated or {}), **parsed.profile_patch}
    derived = derive_unspecified_fields(accumulated_after)
    _log_unspecified_comparison(accumulated_after, parsed.unspecified_fields, derived)

    return IntakeTurnResult(
        ...                                       # B 留下的其余参数原样不动
        unspecified_fields=derived,
        model_claimed_unspecified_fields=list(parsed.unspecified_fields),
        ...
    )
```

**`is_job_related=False` 那个早退分支不动**：那一轮压根没有画像，两个字段都保持默认空列表。

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py tests/test_log_redaction.py -v`
Expected: PASS

⚠️ 这一步大概率会**打断几条既有用例**：`test_round_limit_forces_completion_with_unspecified_fields` 这类断言 `"mcu_family" in result.unspecified_fields` 的用例，语义已经从"模型说的"变成"系统推导的"。逐条判断：
- 断言的字段本来就没值 → 断言**依然成立**，只是理由变了，在用例 docstring 里补一句说明
- 断言依赖"只有 give_up 时才非空" → **改断言**，因为推导现在每轮都给结果
- ⛔ 不许为了让老用例过而给 `derive_unspecified_fields` 加 `if give_up` 分支

- [ ] **Step 5: 跑全量**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py tests/test_log_redaction.py
git commit -m "feat(intake): 模型自称的未指定字段降级为对照，日志走 loggable_summary（tasks 6.2）"
```

---

### Task 4: `field → 中文名` 映射与完整性测试（6.4）

**Files:**
- Modify: `app/schemas/job_profile.py`（紧跟 `JobProfile` 类定义之后）
- Test: `tests/test_job_profile_schema.py`

**Interfaces:**
- Consumes: `JobProfile.model_fields`、`SYSTEM_MANAGED_FIELDS`
- Produces: `FIELD_LABELS: dict[str, str]`、`field_label(name: str) -> str`、`field_labels(names) -> list[str]` —— Task 5/6/7 全部调用它

**为什么放后端**（`design.md` 决策 7）：前端硬编码一份中文映射表的话，`JobProfile` 加字段时前端不会同步更新，用户会看到漏网的英文 snake_case——**这正是今天的故障现象**。映射与字段定义放在一起，加字段时漏改会被完整性测试直接抓到。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_job_profile_schema.py 追加
from app.schemas.job_profile import (
    FIELD_LABELS,
    SYSTEM_MANAGED_FIELDS,
    field_label,
    field_labels,
)


def test_every_profile_field_has_a_chinese_label():
    """
    design.md 决策 7 的机械保障：加字段忘了补中文名，这条当场失败。
    没有这条测试，漏改的表现是业务经理在警示块里看到一个英文 snake_case——
    而那正是本章要修的故障现象本身。
    """
    business_fields = set(JobProfile.model_fields) - set(SYSTEM_MANAGED_FIELDS)

    missing = sorted(business_fields - set(FIELD_LABELS))
    assert not missing, f"这些字段没有中文名：{missing}"

    extra = sorted(set(FIELD_LABELS) - business_fields)
    assert not extra, f"中文名映射里有字段表中不存在的键（字段被删了没跟）：{extra}"


def test_labels_are_chinese_and_never_leak_the_english_identifier():
    """spec：界面上不出现内部英文字段标识。中文名本身不许原样带英文标识。"""
    for name, label in FIELD_LABELS.items():
        assert label.strip(), f"{name} 的中文名是空的"
        assert name not in label, f"{name} 的中文名里混进了英文标识：{label}"


def test_field_label_never_returns_english_for_unknown_name():
    """降级也不许泄漏英文：未知字段名返回中性文案，不返回原名。"""
    assert field_label("toolchain") == FIELD_LABELS["toolchain"]
    assert "some_hallucinated_field" not in field_label("some_hallucinated_field")


def test_field_labels_preserves_order():
    """下游把英文列表与中文列表按下标配对，顺序错位会张冠李戴。"""
    assert field_labels(["toolchain", "headcount"]) == [
        FIELD_LABELS["toolchain"],
        FIELD_LABELS["headcount"],
    ]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_job_profile_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'FIELD_LABELS'`

- [ ] **Step 3: 写实现**

在 `app/schemas/job_profile.py` 的 `JobProfile` 类定义**之后**追加：

```python
# 字段 → 给业务经理看的中文名（design.md 决策 7）。
#
# ⛔ 不要把这份表搬到前端。它必须和上面的字段定义待在同一个文件里：JobProfile
# 加字段时，前端不会跟着改，用户就会在缺口警示里看到一个英文 snake_case——那正是
# 本章要修的故障现象。放在这里，漏改会被
# tests/test_job_profile_schema.py::test_every_profile_field_has_a_chinese_label
# 当场抓到。
#
# **加字段时必须同时加一行。** 这不是文档义务，是会让测试变红的硬约束。
FIELD_LABELS: dict[str, str] = {
    "job_title": "岗位名称",
    "department": "所属部门",
    "headcount": "招聘人数",
    "education_requirement": "学历要求",
    "experience_years": "工作年限",
    "core_skills": "核心技能",
    "project_experience_requirement": "项目经验要求",
    "soft_skill_keywords": "软技能关键词",
    "autosar_experience": "AUTOSAR 经验",
    "functional_safety": "功能安全等级",
    "mcu_family": "MCU 平台",
    "diag_stack": "诊断与总线协议栈",
    "sop_projects": "量产项目经历",
    "toolchain": "开发工具链",
}

_UNKNOWN_FIELD_LABEL = "未命名字段"


def field_label(name: str) -> str:
    """字段名 → 中文名。

    未知字段名返回中性文案而**不是**原样返回英文标识：spec 明确要求"界面上不出现
    内部英文字段标识"，降级路径也不例外。上面那条完整性测试保证这个降级在真实
    字段上不可能发生——留着它是为了不让一次映射缺失变成 payload 组装时的 KeyError
    （那会在业务经理点确认的那一刻炸成 500）。
    """
    return FIELD_LABELS.get(name, _UNKNOWN_FIELD_LABEL)


def field_labels(names) -> list[str]:
    """按原顺序批量转中文名。下游按下标与英文列表配对，顺序必须保持。"""
    return [field_label(name) for name in names]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_job_profile_schema.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/schemas/job_profile.py tests/test_job_profile_schema.py
git commit -m "feat(schema): 字段中文名映射与完整性测试（tasks 6.4）"
```

---

### Task 5: 编排层分流落库与 `confirmation_prompt` 带中文名（6.5 后端）

**Files:**
- Modify: `app/graph/state.py`、`app/graph/nodes.py`（`compute_intake_turn` / `effect_persist_draft`）、`app/graph/build.py`（`_deliver_node`）
- Test: `tests/test_graph_nodes.py`、`tests/test_web_api.py`

**Interfaces:**
- Consumes: `IntakeTurnResult.unspecified_fields`（推导值）、`IntakeTurnResult.model_claimed_unspecified_fields`（对照值）、`field_labels()`
- Produces:
  - `IntakeState["model_claimed_unspecified_fields"]: list[str]`
  - `job_profile.derived_unspecified_fields` 列 = 推导值；`job_profile.unspecified_fields` 列 = 模型自称值
  - `confirmation_prompt` payload 新增键 `unspecified_field_labels: list[str]`，与既有的 `unspecified_fields` **同序等长**

**两列的分工是 DDL 里已经写死的**（`app/storage/db.py` 的建表注释逐字）："系统按画像字段表推导出的未指定字段（第 6 章写）。与上面那列 LLM 自由生成的 `unspecified_fields` 并存，前者是真源、后者降级为对照。" ⛔ 不许把同一个值写进两列——那会让 8.1 的"修复前 vs 修复后"对比失去对照组。

**payload 键为什么是"加一个"而不是"改类型"**：`.51` 上已有历史 `outbox` 行，`confirmation_prompt` 的 `unspecified_fields` 存的是字符串数组；`GET /api/jobs/{id}` 会把这些历史行原样读回前端。把这个键改成对象数组会让历史行在新前端里崩——与单元 A 处理裸字符串问题时踩的是同一个坑。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_graph_nodes.py 追加
def test_persist_draft_splits_derived_and_model_claimed_into_two_columns(tmp_path):
    """
    tasks 6.2/6.5：推导值进 derived_unspecified_fields（真源），模型自称值留在
    unspecified_fields（对照）。⛔ 两列同值等于毁掉 8.1 回放对比的对照组。
    """
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 't', 'drafting')")

    effect_persist_draft(
        conn,
        thread_id="j1",
        business_key="0",
        state={
            "profile_patch_accumulated": {"job_title": "嵌入式软件工程师"},
            "unspecified_fields": ["toolchain", "mcu_family"],
            "model_claimed_unspecified_fields": ["functional_safety"],
            "history": [],
        },
    )

    row = conn.execute(
        "SELECT derived_unspecified_fields, unspecified_fields FROM job_profile WHERE job_id='j1'"
    ).fetchone()

    assert json.loads(row[0]) == ["toolchain", "mcu_family"]
    assert json.loads(row[1]) == ["functional_safety"]


def test_persist_draft_tolerates_state_without_model_claimed_key(tmp_path):
    """重放/老 checkpoint 里没有这个新键时按空列表处理，不能 KeyError。"""
    conn = get_connection(str(tmp_path / "t.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j2', 't', 'drafting')")

    effect_persist_draft(
        conn,
        thread_id="j2",
        business_key="0",
        state={"profile_patch_accumulated": {}, "unspecified_fields": [], "history": []},
    )

    row = conn.execute(
        "SELECT unspecified_fields FROM job_profile WHERE job_id='j2'"
    ).fetchone()
    assert json.loads(row[0]) == []
```

```python
# tests/test_web_api.py 追加
def test_confirmation_prompt_payload_carries_chinese_labels(tmp_path):
    """
    tasks 6.5：API 返回未指定字段时同时返回中文名，两个列表同序等长。
    前端只渲染中文名（spec：界面上不出现内部英文字段标识）。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
                "unspecified_fields": [],
            }
        )
    ]
    client = make_app(tmp_path, responses)

    payload = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["message"]

    assert payload["type"] == "confirmation_prompt"
    fields = payload["payload"]["unspecified_fields"]
    labels = payload["payload"]["unspecified_field_labels"]
    assert fields, "画像只填了 job_title，不该一个缺口都没有"
    assert len(labels) == len(fields)
    assert labels == field_labels(fields)
    assert all(not label.isascii() for label in labels), "中文名里混进了英文标识"
```

（`tests/test_web_api.py` 顶部补 `from app.schemas.job_profile import field_labels`；`tests/test_graph_nodes.py` 顶部若还没有 `import json` / `get_connection` / `init_schema` / `effect_persist_draft` 的 import，按该文件既有写法补上。）

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py -k "derived or model_claimed" tests/test_web_api.py -k "chinese_labels" -v`
Expected: FAIL — `derived_unspecified_fields` 仍是默认 `'[]'`，`unspecified_field_labels` 键不存在

- [ ] **Step 3: 写实现**

`app/graph/state.py`，在 `unspecified_fields` 那一项旁边加：

```python
    # 系统按字段表推导的未指定字段（tasks 6.1，真源）。
    unspecified_fields: list[str]

    # 模型自称的未指定字段（tasks 6.2，对照）。与上面那个键刻意分成两个键：
    # 混用一个键名就迟早会有人把对照值当真源用，而那个 bug 的表现是"警示块里
    # 少列了一个字段"——没人会注意到。
    model_claimed_unspecified_fields: list[str]
```

`app/graph/nodes.py` 的 `compute_intake_turn` 返回字典里，`"unspecified_fields": result.unspecified_fields,` 之后加一行：

```python
        "model_claimed_unspecified_fields": result.model_claimed_unspecified_fields,
```

`effect_persist_draft`：把 `unspecified_json` 的取值换掉，并加一个新变量；然后在 INSERT 的列清单与占位符里各加一项。

⚠️ 单元 B 已经往这条 INSERT 里加过列（`is_productive`、`asked_questions`）。**在 rebase 后的那条语句上做增量修改**，不要用下面的片段整体覆盖它。改完必须满足：列名个数 == `?` 个数 == 元组长度，且列清单里同时出现 `unspecified_fields` 与 `derived_unspecified_fields`。

```python
    # 两列分工见 app/storage/db.py 的建表注释：derived_* 是系统推导的真源，
    # 裸 unspecified_fields 是模型自称的对照。⛔ 不许两列写同一个值——那会让
    # 8.1 的"修复前 vs 修复后"对比失去对照组。
    derived_json = json.dumps(state.get("unspecified_fields", []), ensure_ascii=False)
    model_claimed_json = json.dumps(
        state.get("model_claimed_unspecified_fields", []), ensure_ascii=False
    )
```

INSERT 里 `unspecified_fields` 绑定 `model_claimed_json`，新增的 `derived_unspecified_fields` 绑定 `derived_json`。

`app/graph/build.py` 的 `_deliver_node`，`is_complete` 分支：

```python
        if state.get("is_complete"):
            # tasks 6.5：英文标识与中文名同时下发，同序等长。前端只渲染中文名。
            # 加一个新键而不是改 unspecified_fields 的类型：.51 上的历史
            # confirmation_prompt 行存的是字符串数组，GET /api/jobs/{id} 会把它们
            # 原样读回新前端，改类型会让历史行当场崩。
            unspecified = state.get("unspecified_fields", [])
            payload = {
                "type": "confirmation_prompt",
                "profile_patch_accumulated": state.get("profile_patch_accumulated", {}),
                "unspecified_fields": unspecified,
                "unspecified_field_labels": field_labels(unspecified),
            }
```

文件顶部 import 补 `from app.schemas.job_profile import field_labels`。

> **`business_key` 说明**：`effect_deliver_message` 的 `business_key` 是 payload 的内容哈希，加了新键后同一轮的哈希会变。这不破坏幂等——它带着 `round_count` 前缀，同一轮内的真实重放仍然算出同一个 key；只是本次改动之前写下的历史行不会被新代码复用，而那些行本来就已经投递完毕。

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_web_api.py tests/test_graph_idempotency.py tests/test_transaction_ownership.py -v`
Expected: PASS（幂等与事务归属两套用例必须原样全绿——本 Task 没有新增 effect 节点，它们红了就是碰坏了铁律 1）

- [ ] **Step 5: 跑全量并提交**

```bash
./venv/bin/python -m pytest -q
git add app/graph/state.py app/graph/nodes.py app/graph/build.py tests/test_graph_nodes.py tests/test_web_api.py
git commit -m "feat(graph): 推导值与模型自称值分两列落库，确认提示带中文名（tasks 6.5）"
```

---

### Task 6: 带缺口确认必须显式知情 —— 409 门禁与留痕（6.7 后端 / 6.9 / 6.10）

**Files:**
- Modify: `app/web/server.py`（`ConfirmRequest`、`confirm`）、`app/graph/nodes.py`（`effect_confirm_profile`）
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `derive_unspecified_fields()`、`field_label()`、`sqlite_utc_now()`
- Produces:
  - `POST /api/jobs/{job_id}/confirm` 请求体 `{"acknowledged_gaps": bool}`（**整个请求体可省略**，省略等价于 `false`）
  - 409 响应 `detail = {"message": str, "gaps": [{"field": str, "label": str}]}`
  - `job_profile.profile_json["_gap_acknowledgement"] = {"acknowledged": bool, "had_gaps": bool, "fields": [...], "labels": [...], "at": "..."}`

**三条设计约束，reviewer 请重点看：**

1. **缺口在确认这一刻现算**，不读 state、不读上一轮的列：`derive_unspecified_fields(profile_dict)` 拿最新 `profile_json` 重算。确认是一次独立的、可重试的 HTTP 动作，依赖某一轮 state 的残留会让"重试一次结论就变了"。
2. **请求体可省略**（`req: ConfirmRequest | None = None`）。6.10 要求"无缺口时确认流程与今天完全一致（不多一步点击）"，前端今天发的是 `fetch(url, {method: "POST"})`、不带 body；改成必填 body 会让所有既有调用与既有测试一起 422。
3. **留痕必须与 `status='approved'` 同事务**：写进 `effect_confirm_profile`（铁律 1）。而且 `effect_generate_and_persist_jd` 随后会用 `{**profile_dict, "_jd_text": ...}` 覆盖 `profile_json`——**必须把带留痕的那份 dict 传给它**，否则 JD 一生成，知情记录就被覆盖没了。这是本 Task 最容易静默丢数据的一处，专门有一条测试盯着。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_web_api.py 追加

def _make_client_at_confirmation(tmp_path):
    """跑到"可确认"状态，且画像里故意留着缺口（只填了 job_title）。"""
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
                "unspecified_fields": [],
            }
        ),
        json.dumps({"jd_text": "岗位职责：负责 ECU 底层驱动开发。", "needs_manual": False}),
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["job_id"]
    return client, job_id


def test_confirm_without_acknowledgement_is_rejected_with_409(tmp_path):
    """spec Scenario: 未做选择不放行。"""
    client, job_id = _make_client_at_confirmation(tmp_path)

    resp = client.post(f"/api/jobs/{job_id}/confirm")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["gaps"], "409 必须附上未指定字段"
    assert all(gap["label"] and not gap["label"].isascii() for gap in detail["gaps"])
    assert {gap["field"] for gap in detail["gaps"]} >= {"toolchain", "mcu_family"}


def test_confirm_with_explicit_acknowledgement_succeeds_and_is_recorded(tmp_path):
    """spec Scenario: 知情确认被记录 —— 确认完成，且事后可从库里查回。"""
    client, job_id = _make_client_at_confirmation(tmp_path)

    resp = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})

    assert resp.status_code == 200
    assert resp.json()["jd_text"]

    conn = get_connection(str(tmp_path / "web.db"))
    profile_json = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
        (job_id,),
    ).fetchone()[0]
    record = json.loads(profile_json)["_gap_acknowledgement"]

    assert record["acknowledged"] is True
    assert record["had_gaps"] is True
    assert "toolchain" in record["fields"]
    assert record["labels"] == field_labels(record["fields"])
    assert record["at"]


def test_gap_acknowledgement_survives_jd_generation(tmp_path):
    """
    effect_generate_and_persist_jd 会用 {**profile_dict, "_jd_text": ...} 整体覆盖
    profile_json。传给它的如果是确认前那份 dict，知情留痕会在 JD 生成的那一刻被
    静默抹掉——事后查不到、也没有任何报错。这条测试就是盯这个。
    """
    client, job_id = _make_client_at_confirmation(tmp_path)
    client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})

    conn = get_connection(str(tmp_path / "web.db"))
    persisted = json.loads(
        conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()[0]
    )

    assert persisted["_jd_text"], "JD 没落库，前置条件不成立"
    assert persisted["_gap_acknowledgement"]["acknowledged"] is True


def test_confirm_without_gaps_needs_no_body_and_no_extra_click(tmp_path):
    """
    6.10：无缺口时确认流程与今天完全一致。请求体可以整个省略，不多一步点击。
    """
    full_profile = {
        "job_title": "嵌入式软件工程师",
        "department": "研发部",
        "headcount": 2,
        "education_requirement": "本科及以上",
        "experience_years": "3-5年",
        "core_skills": [{"name": "C", "required": True}],
        "project_experience_requirement": "有量产项目",
        "soft_skill_keywords": ["沟通"],
        "autosar_experience": ["CP"],
        "functional_safety": "ASIL-B",
        "mcu_family": ["TC3xx"],
        "diag_stack": ["UDS"],
        "sop_projects": [
            {"vehicle_model": "X1", "role": "开发", "is_mass_production": True}
        ],
        "toolchain": ["CANoe"],
    }
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": full_profile,
                "unspecified_fields": [],
            }
        ),
        json.dumps({"jd_text": "岗位职责：……", "needs_manual": False}),
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["job_id"]

    resp = client.post(f"/api/jobs/{job_id}/confirm")

    assert resp.status_code == 200

    conn = get_connection(str(tmp_path / "web.db"))
    record = json.loads(
        conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()[0]
    )["_gap_acknowledgement"]
    assert record["had_gaps"] is False
    assert record["fields"] == []


def test_going_back_to_answer_keeps_collected_content(tmp_path):
    """
    spec Scenario: 选择"回去补答" —— 会话回到可继续作答的状态，已采集内容保留。
    "回去补答"在后端就是"不确认、继续 POST /reply"，因此这里验证的是：确认提示
    之后再回一轮，之前采集的字段一个都没丢。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
                "unspecified_fields": [],
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [],
                "profile_patch": {"toolchain": ["CANoe"]},
                "unspecified_fields": [],
            }
        ),
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "招一个做驱动的"}).json()["job_id"]

    client.post(f"/api/jobs/{job_id}/reply", json={"message": "工具链用 CANoe"})

    conn = get_connection(str(tmp_path / "web.db"))
    accumulated = json.loads(
        conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()[0]
    )
    assert accumulated["job_title"] == "嵌入式软件工程师"  # 补答没有把已采集内容冲掉
    assert accumulated["toolchain"] == ["CANoe"]
```

（`tests/test_web_api.py` 顶部补 `from app.storage.db import get_connection`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_web_api.py -k "acknowledg or gap or going_back" -v`
Expected: FAIL — 确认接口今天不认 `acknowledged_gaps`，缺口存在时直接 200

- [ ] **Step 3: 写实现**

`app/web/server.py`：`ReplyRequest` 之后加请求体模型（⛔ **不要动 `ReplyRequest`**，§5 约定 2）：

```python
class ConfirmRequest(BaseModel):
    # 缺省 false = 未知情 = 不放行。⛔ 绝不能缺省 true：那等于系统替业务经理
    # 做了"我知道有缺口"这个声明（合规红线：人工确认节点必须是真的人在确认）。
    acknowledged_gaps: bool = False
```

`confirm` 的签名与 `profile_dict = json.loads(row[0])` 之后：

```python
    @router.post("/api/jobs/{job_id}/confirm")
    def confirm(job_id: str, req: ConfirmRequest | None = None):
        ...
        profile_dict = json.loads(row[0])

        # tasks 6.7：缺口在确认这一刻现算，不读 state、不读上一轮写下的列。
        # 确认是一次独立、可重试的 HTTP 动作；依赖某一轮 state 的残留会让
        # "重试一次结论就变了"。
        gaps = derive_unspecified_fields(profile_dict)
        acknowledged = bool(req and req.acknowledged_gaps)
        if gaps and not acknowledged:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "这份画像还有未指定的内容，确认前请先选择：回去补答，或知道有缺口仍然确认",
                    "gaps": [{"field": name, "label": field_label(name)} for name in gaps],
                },
            )

        # tasks 6.9：知情留痕（design.md 决策 8，写 profile_json 的下划线内部键，
        # 不新建表）。无论有没有缺口都写一条——"确认时没有缺口"本身也是事后要能
        # 查到的事实，缺了它就分不清"当时没缺口"和"这条记录漏写了"。
        profile_dict = {
            **profile_dict,
            "_gap_acknowledgement": {
                "acknowledged": acknowledged,
                "had_gaps": bool(gaps),
                "fields": gaps,
                "labels": field_labels(gaps),
                "at": sqlite_utc_now(),
            },
        }
```

后面 `JobProfile.model_validate(...)` 与两个 effect 调用**都用这份新的 `profile_dict`**（`model_validate` 会忽略 `_` 开头的额外键，pydantic 默认 `extra='ignore'`，与今天 `_jd_text` 的处理方式一致）。

文件顶部 import 补：

```python
from app.agents.intake_agent import derive_unspecified_fields
from app.schemas.job_profile import JobProfile, field_label, field_labels
```

`app/graph/nodes.py` 的 `effect_confirm_profile`：`profile_dict` 这个入参今天收下了却没用，现在让它真正落库——

```python
    conn.execute(
        "UPDATE job_profile SET status = 'approved', profile_json = ? "
        "WHERE job_id = ? AND version = (SELECT MAX(version) FROM job_profile WHERE job_id = ?)",
        (json.dumps(profile_dict, ensure_ascii=False), thread_id, thread_id),
    )
```

并在 docstring 里补一段：

```
    2026-08-25（tasks 6.9）：profile_dict 这个入参此前只是收下不用，现在承载知情
    确认留痕（`_gap_acknowledgement`）。留痕与 status='approved' 必须落在同一条
    事务里（铁律 1）——分开写会出现"画像已确认但查不到确认时是否知情"，而这正是
    spec「使事后可以查明确认时业务经理是否知情」要杜绝的状态。
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_web_api.py tests/test_graph_idempotency.py tests/test_transaction_ownership.py -v`
Expected: PASS

- [ ] **Step 5: 跑全量并提交**

```bash
./venv/bin/python -m pytest -q
git add app/web/server.py app/graph/nodes.py tests/test_web_api.py
git commit -m "feat(web): 带缺口确认必须显式知情，确认留痕随画像持久化（tasks 6.7/6.9/6.10）"
```

---

### Task 7: 确认入口上方的缺口警示区块与两个动作（6.6 / 6.7 前端 / 6.8）+ 收尾

**Files:**
- Modify: `app/web/static/index.html`
- Modify: `docs/tech-debt.md`、`openspec/changes/m1-intake-quality-fixes/tasks.md`
- Test: `tests/test_static_frontend.py`

**Interfaces:**
- Consumes: `payload.unspecified_fields`（英文标识，只用来判空与计数）、`payload.unspecified_field_labels`（中文名，**唯一渲染源**）、409 的 `detail.gaps`
- Produces: 无（前端是链路末端）

**这个单元的弱点，提前说清**：本仓库没有 JS 测试运行器，`tests/test_static_frontend.py` 只能做字符串弱断言。**真正的验收是 Step 6 的手工跑通**（对应 tasks 8.4）。这不是拆分方式的问题，是前端无构建这个既有形态的代价。

**三条硬规则：**
1. **只渲染中文名。** 历史 `confirmation_prompt` 行（`.51` 上 2026-08-25 之前写的）没有 `unspecified_field_labels` 这个键——这时**渲染条数而不是字段名**（"还有 N 项未指定"），⛔ 绝不回退去渲染英文标识。spec：界面上不出现内部英文字段标识。
2. **警示块在确认入口的上方**，不是对话流里的一行小字（spec 原文：`MUST NOT` 只作为对话流中的一条普通消息）。这条正是 pilot 三场里两位经理"点了确认、事后才在 JD 里发现缺口"的直接成因。
3. **有缺口时主确认按钮不出现**，由警示块里的两个动作取代——UI 层就没有"不做选择直接确认"这条路径可走；后端 409 是第二道闸，不是唯一一道。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_static_frontend.py 追加
def test_gap_warning_block_sits_above_confirm_and_renders_chinese_only():
    """
    tasks 6.6（弱断言，本仓库无 JS 测试运行器，真实验收在手工路径）。
    锁的是三件"被人顺手改回去就没有任何其它信号"的事：
      1) 警示块的 DOM 位置在确认按钮之前
      2) 渲染源是中文名那个键，不是英文标识那个键
      3) 后果说明这句话还在
    """
    assert 'id="gap-warning"' in INDEX_HTML
    assert INDEX_HTML.index('id="gap-warning"') < INDEX_HTML.index('id="confirm-btn"')

    assert "unspecified_field_labels" in INDEX_HTML
    # 英文标识只允许用于判空/计数，不允许出现在任何写进 DOM 的位置
    assert "不会出现在 JD" in INDEX_HTML


def test_gap_warning_offers_both_choices_and_never_preselects():
    """
    tasks 6.7 + 合规红线（AI 不做决定）：两个动作都在，且都不是默认动作。
    """
    assert "回去补答" in INDEX_HTML
    assert "仍然确认" in INDEX_HTML
    # 只有"仍然确认"这条路径才带 acknowledged_gaps: true
    assert "acknowledged_gaps" in INDEX_HTML
    assert "acknowledged_gaps: false" not in INDEX_HTML


def test_confirm_without_gaps_still_posts_without_body():
    """
    6.10「无缺口时确认流程与今天完全一致（不多一步点击）」的前端一半：
    没有缺口时发出去的仍然是不带 body 的 POST。
    """
    assert 'method: "POST" }' in INDEX_HTML
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_static_frontend.py -v`
Expected: FAIL — `id="gap-warning"` 不存在

- [ ] **Step 3: 写实现**

样式（`<style>` 块内追加）——刻意做得显眼，spec 要求"视觉显著"：

```css
  .gap-warning { background: #f8d7da; border: 2px solid #dc3545; color: #58151c; padding: 14px 16px; border-radius: 8px; margin: 16px 0 8px; }
  .gap-warning h3 { margin: 0 0 8px; font-size: 15px; }
  .gap-warning ul { margin: 0 0 10px; padding-left: 20px; }
  .gap-warning .consequence { font-weight: 600; margin-bottom: 12px; }
  .gap-warning button { margin: 0 8px 0 0; }
```

DOM（确认按钮**之前**）：

```html
  <div id="gap-warning" class="gap-warning" style="display:none;"></div>
  <button id="confirm-btn" style="display:none;">确认画像，生成 JD</button>
```

脚本：

```js
    // 确认入口上方的缺口警示（tasks 6.6/6.7/6.8）。
    // ⛔ 只渲染中文名。历史 confirmation_prompt 行（.51 上 2026-08-25 之前写的）
    // 没有 unspecified_field_labels 这个键，这时渲染条数而不是字段名——绝不回退
    // 去渲染英文 snake_case，那正是本章要修的故障现象。
    const GAP_CONSEQUENCE = "留空的话，这些要求不会出现在生成的 JD 里。";

    function renderGapWarning(fields, labels) {
      const box = document.getElementById("gap-warning");
      const confirmBtn = document.getElementById("confirm-btn");
      box.textContent = "";

      if (!fields || fields.length === 0) {
        // 无缺口不打扰（spec Scenario：无缺口时不出现缺口警示区块）。
        box.style.display = "none";
        confirmBtn.style.display = "inline-block";
        return;
      }

      const title = document.createElement("h3");
      title.textContent = "确认之前请先看一下：还有 " + fields.length + " 项没有确定";
      box.appendChild(title);

      if (labels && labels.length === fields.length) {
        const list = document.createElement("ul");
        labels.forEach((label) => {
          const item = document.createElement("li");
          item.textContent = label;
          list.appendChild(item);
        });
        box.appendChild(list);
      }

      const consequence = document.createElement("div");
      consequence.className = "consequence";
      consequence.textContent = GAP_CONSEQUENCE;
      box.appendChild(consequence);

      // 两个动作都要显式点，没有默认动作、没有预选（合规红线：AI 不代替人做决定）。
      const backBtn = document.createElement("button");
      backBtn.textContent = "回去补答";
      backBtn.addEventListener("click", () => {
        // 6.8：回到可继续作答的状态。已采集内容留在服务端，前端只需把入口交还
        // 给输入框——用户下一条回复照常走 POST /reply。
        box.style.display = "none";
        confirmBtn.style.display = "none";
        appendTurn("assistant", "好的，请直接补充上面这几项，我接着记。");
        document.getElementById("input").focus();
      });
      box.appendChild(backBtn);

      const anywayBtn = document.createElement("button");
      anywayBtn.textContent = "知道有缺口，仍然确认";
      anywayBtn.addEventListener("click", () => doConfirm(true));
      box.appendChild(anywayBtn);

      box.style.display = "block";
      // 有缺口时主确认按钮不出现：UI 层就没有"不做选择直接确认"这条路可走。
      confirmBtn.style.display = "none";
    }
```

`renderMessage` 的两个分支：`question` 分支里在隐藏确认按钮的同时把警示块也隐藏（`document.getElementById("gap-warning").style.display = "none";`）；`confirmation_prompt` 分支改成：

```js
      } else if (message.type === "confirmation_prompt") {
        appendTurn("assistant", "画像已收集完整，请确认。");
        renderGapWarning(
          message.payload.unspecified_fields || [],
          message.payload.unspecified_field_labels || []
        );
      }
```

把原来 `confirm-btn` 的点击处理抽成 `doConfirm(acknowledged)`：

```js
    async function doConfirm(acknowledged) {
      // 无缺口时发的仍然是不带 body 的 POST，与改动前逐字一致——6.10 要求
      // "无缺口时确认流程与今天完全一致，不多一步点击"。
      const init = acknowledged
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ acknowledged_gaps: true }),
          }
        : { method: "POST" };
      const resp = await fetch(`api/jobs/${jobId}/confirm`, init);
      const data = await resp.json();
      if (!resp.ok) {
        const detail = data && data.detail;
        let reason = "确认失败，请稍后重试。";
        if (typeof detail === "string") {
          reason = detail;
        } else if (detail && detail.gaps) {
          // 409 的缺口回执同样只显示中文名。
          reason = [detail.message]
            .concat(detail.gaps.map((gap) => gap.label))
            .join("；");
        } else if (detail) {
          const fields = (detail.errors || []).map(
            (e) => `${e.field}: ${e.reason}（当前值 ${e.got}）`
          );
          reason = [detail.message].concat(fields).join("；");
        }
        appendTurn("assistant", "⚠️ " + reason);
        return;
      }
      document.getElementById("gap-warning").style.display = "none";
      const output = document.getElementById("jd-output");
      output.style.display = "block";
      output.textContent = data.jd_text;
      if (data.needs_manual) {
        appendTurn("assistant", "⚠️ JD 多次触发歧视性表述检测，已转人工处理，请核对下方内容。");
      }
    }

    document.getElementById("confirm-btn").addEventListener("click", () => doConfirm(false));
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_static_frontend.py -v`
Expected: PASS（含 `test_index_html_has_no_absolute_paths`——新增的类名、文案、DOM id 里不许有任何以 `/` 开头的字符串字面量）

- [ ] **Step 5: 跑全量**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 手工跑通（真实验收，对应 tasks 8.4）**

```bash
DB_PATH=data/dev-unitD.db ROOT_PATH=/hr/recruit-agent ./venv/bin/python -m uvicorn app.main:app --port 8099
```

在 `http://127.0.0.1:8099/hr/recruit-agent/` 上逐条确认：

| # | 操作 | 期望 |
|---|---|---|
| 1 | 一句话提需求，走到"画像已收集完整" | 确认按钮**不出现**；上方出现红框警示块，列的是中文名 |
| 2 | 读警示块文案 | 出现"留空的话，这些要求不会出现在生成的 JD 里"；**整块里看不到任何英文 snake_case** |
| 3 | 点"回去补答" | 警示块消失，光标回到输入框；再发一条补充，之前采集的内容没丢 |
| 4 | 补答后再次到达确认状态 | 已补上的字段从警示块里消失 |
| 5 | 点"知道有缺口，仍然确认" | 正常出 JD |
| 6 | 查库 `sqlite3 data/dev-unitD.db "select profile_json from job_profile order by version desc limit 1"` | 能查到 `_gap_acknowledgement`，`acknowledged` 为 true，`fields` 与界面上列的那几项一致 |
| 7 | 把所有字段答全后确认 | **不出现**警示块，点一次确认按钮直接出 JD（不多一步点击） |
| 8 | 全程注意地址栏 | 一直带着 `/hr/recruit-agent` 前缀，没有 404（部署约束 1） |

跑完删掉临时库：`rm -f data/dev-unitD.db*`

- [ ] **Step 7: 登记技术债**

`docs/tech-debt.md` 追加（编号接现有最大号）：

```markdown
## TD-N · `job_profile.unspecified_fields` 与 `JobProfile.unspecified_fields` 已降级为对照

**欠的是什么**：`job_profile.unspecified_fields` 这一列与 `JobProfile.unspecified_fields`
这个 pydantic 字段。2026-08-25（`m1-intake-quality-fixes` 第 6 章）起，真源是
`derived_unspecified_fields` 列，这两处只保留"模型自称了什么"的对照价值。

**触发条件**：第 8 章 8.7 的编造率/漏报率数字算完并写进 `docs/` 之后，对照数据的
使命就结束了，届时删列 + 删字段。

**不还的后果**：两个同名不同义的载体长期并存，下一个改这块代码的人有一半概率
读错真源——而读错的表现是"警示块少列了一个字段"，没有任何报错。
```

- [ ] **Step 8: 回勾 WBS 并提交**

把 `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 6 章 6.1–6.10 全部勾上。

```bash
git add app/web/static/index.html tests/test_static_frontend.py docs/tech-debt.md openspec/changes/m1-intake-quality-fixes/tasks.md
git commit -m "feat(frontend): 确认入口上方的缺口警示与知情选择（tasks 6.6/6.7/6.8），回勾第 6 章"
```

---

## Self-Review：spec 覆盖矩阵

| spec Requirement / Scenario | 落在哪个 Task |
|---|---|
| 未指定字段由系统确定性推导 | Task 2（`derive_unspecified_fields`）、Task 3（模型输出不再进结果） |
| └ Scenario: 已答字段不进未指定 | Task 2 `test_answered_field_does_not_enter_unspecified` + `test_derive_does_not_repeat_the_models_overreport_in_19b6ec6d` |
| └ Scenario: 未答字段不被遗漏 | Task 2 `test_unanswered_field_is_never_missed` + `test_derive_catches_what_the_model_underreported_in_a478499c` |
| └ Scenario: 推导结果稳定 | Task 2 `test_derivation_is_stable_across_repeated_calls` |
| 确认前的显著缺口警示 | Task 4（中文名）、Task 5（payload 带中文名）、Task 7（警示块） |
| └ Scenario: 有缺口时的呈现 | Task 7 `test_gap_warning_block_sits_above_confirm_and_renders_chinese_only` + 手工验收 1/2 |
| └ Scenario: 无缺口时不打扰 | Task 7 `renderGapWarning` 早退分支 + 手工验收 7 |
| └ Scenario: 中文名称覆盖 | Task 4 `test_every_profile_field_has_a_chinese_label`、Task 5 `test_confirmation_prompt_payload_carries_chinese_labels` |
| 带缺口确认必须显式知情 | Task 6（409 门禁 + 留痕）、Task 7（两个动作） |
| └ Scenario: 未做选择不放行 | Task 6 `test_confirm_without_acknowledgement_is_rejected_with_409` |
| └ Scenario: 选择回去补答 | Task 6 `test_going_back_to_answer_keeps_collected_content`、Task 7 手工验收 3/4 |
| └ Scenario: 知情确认被记录 | Task 6 `test_confirm_with_explicit_acknowledgement_succeeds_and_is_recorded` + `test_gap_acknowledgement_survives_jd_generation` |

## Self-Review：`tasks.md` 第 6 章逐项映射

| tasks | Task | 备注 |
|---|---|---|
| 6.1 纯函数 `derive_unspecified_fields` | Task 2 | |
| 6.2 停止透传 `parsed.unspecified_fields`，降级为 debug 日志 | Task 3 | **`loggable_summary()` 的首个生产上岗点**，含"确实调用了"的断言 |
| 6.3 真实回放反证测试 | Task 1（取真值）+ Task 2（断言） | ⛔ 不得用合成数据顶替 |
| 6.4 中文名映射 + 完整性测试 | Task 4 | 放 `app/schemas/job_profile.py`，不放前端 |
| 6.5 API 返回中文名 | Task 5 | 加键不改类型，兼容 `.51` 历史行 |
| 6.6 确认按钮上方的警示块 | Task 7 | |
| 6.7 `acknowledged_gaps` + 409 + 两个动作 | Task 6（后端）+ Task 7（前端） | |
| 6.8 回去补答保留已采集内容 | Task 6（后端验证）+ Task 7（前端动作） | |
| 6.9 知情留痕写 `profile_json` 内部键 | Task 6 | 不新建表；与 `status='approved'` 同事务 |
| 6.10 三条验收测试 | Task 6 | 含"JD 生成不覆盖留痕"这条额外的静默丢数据守卫 |

## Self-Review：三处请 reviewer 特别看的取舍

1. **`IntakeTurnResult.unspecified_fields` 的语义被换掉了，键名没换。** 换名字（比如改成 `derived_unspecified_fields`）会更醒目，代价是 `graph/nodes.py`、`graph/build.py`、`index.html`、`.51` 上已有的 `outbox` payload 四处跟着改，且历史 payload 无法回填。取舍是：**对外键名保持不变（兼容历史行），对内用一个新键 `model_claimed_unspecified_fields` 把对照值物理隔离**，让"混用"这件事在类型层面就干不成。
2. **确认时重算缺口，而不是读上一轮写下的 `derived_unspecified_fields` 列。** 多一次纯函数计算，换来"确认这个动作自身是幂等且自洽的"——重试不会因为读到不同轮次的残留而给出不同结论。代价是：若将来推导规则变了，历史行里存的旧结果与确认时算的新结果会不一致；这是可接受的，因为留痕记的是**确认那一刻算出来的**那份（写进 `_gap_acknowledgement.fields`）。
3. **无缺口时确认请求不带 body。** 更"规整"的写法是让前端永远发 `{"acknowledged_gaps": false}`，但 6.10 明确要求"无缺口时确认流程与今天完全一致（不多一步点击）"，而且既有的 `tests/test_web_api.py` 里有一批 `client.post(.../confirm)` 不带 body 的调用——让请求体保持可选，是让这条 6.10 的验收可以被机械检查，而不是靠人眼比对交互步数。
