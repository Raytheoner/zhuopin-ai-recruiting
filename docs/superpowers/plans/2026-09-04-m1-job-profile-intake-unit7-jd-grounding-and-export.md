# M1 交付单元 7 · JD 溯源与导出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给已生成的 JD 补上三件缺的东西——技术要求对画像字段的确定性溯源观测（7.3）、AI 生成标识的不可删保护与「标记为人工撰写」这条唯一豁免路径及其留痕（7.5）、纯文本一键复制导出（7.7）。

**Architecture:** 溯源与标识处理全部是**纯函数**，落在 `app/agents/`（`jd_grounding.py` 新建、`jd_agent.py` 增补），复用 `app/agents/field_grounding.py` 的 `normalize_for_grounding` 做归一化与子串判定，⛔ 不另造一套。有副作用的两个动作（编辑落库、标记人工撰写）各自独占一个新的 `effect_*` 节点，放在**新文件** `app/graph/jd_nodes.py`，用现成的 `idempotent_effect` 装饰器带幂等键并在同一事务里提交。`app/web/server.py` 增三个端点接线，前端在 `app/web/static/index.html` 里加编辑框、标记按钮与复制按钮。溯源清单**不落库、按需重算**（理由见 Global Constraints 第 8 条）。

**Tech Stack:** Python 3.14（`requires-python = ">=3.14,<3.15"`，与 .51 部署环境严格对齐）· FastAPI · SQLite（`app/storage/db.py`）· LangGraph ≥1.0.10 · pytest（`venv/bin/python -m pytest`）· 单文件原生 JS 前端（无构建、无框架、无第三方库）

---

## Global Constraints

以下每一条对**每个** Task 都成立，reviewer 按这一段逐条看。前七条从 `CLAUDE.md` 的「工程铁律」「合规红线」「部署约束」逐字复制，第 8–13 条是本交付单元的边界。

1. **（工程铁律 1）LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。**
2. **（工程铁律 2）L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **（合规红线）AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
   本单元的落点：**7.5 的「标记为人工撰写」是唯一能去掉标识的路径，且必须留痕（谁、何时）；⛔ 常规编辑不得删标识。** 编辑接口对提交上来的任何文本都要重新强制贴回标识——不是"检查用户有没有删"，是"不管他删没删，服务端都重新贴"。
4. **（合规红线）AI 只做排序推荐，不做自动淘汰。** 决策人只能是人：`reviewer` 字段⛔ 不得写入任何自动判定的产物，空白一律拒绝。
5. **（部署约束 1）路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
6. **（部署约束 4）目标服务器是 Windows，没有 Docker。** ⛔ 不引入任何新的第三方运行时依赖：`requirements.txt` 一行不改。**7.7 的复制⛔ 不引第三方剪贴板库**，只用浏览器原生 API。
7. **7.3 的溯源校验是纯函数、确定性、不调模型**（同 `m1-intake-quality-fixes/design.md` 决策 11 的形状：逐字引用 + 归一化后子串判定，⛔ 不用模型自评、⛔ 不用语义/嵌入比对）；**未溯源只观测不拦截**（同决策 12：未溯源内容照常留在文案里，只把清单报出来）。⛔ 本单元不得引入任何基于未溯源结果的拦截、重试或降级分支。
8. **溯源清单⛔ 不落库，每次按需重算。** 它是确定性纯函数在两份都已持久化的输入（`profile_json` 的 `_jd_text` 与同一行的画像字段）上的取值，重算与读缓存必然同值，而落库要改 `app/graph/nodes.py` 的既有节点 `effect_generate_and_persist_jd`——那被第 12 条禁止。副作用：JD 被编辑后清单自动跟着变，这是对的行为，不是缺陷。
9. **JD 文案本身不适用工程铁律 3/4**（那两条约束的是 `analysis_run` / `criterion_score` 里的 AI 评分，JD 不是评分）。⛔ 不要在本单元里给 JD 造 `evidence_ref` 或 `criterion_score` 记录。
10. **（决策 4）画像冻结后不可变，改动走新版本。** 本单元⛔ 不新建 `job_profile` 版本、⛔ 不改 `status`、⛔ 不动画像的业务字段；只写 `profile_json` 里以下划线开头的内部键（`_jd_text` / `_jd_authorship`），沿用 `_gap_acknowledgement` 已经确立的做法（决策 8：走内部键，不建新表）。
11. **⛔ 不改 `human_review` 表的 `decision_type` CHECK。** 那三个字面量在 `app/storage/db.py`、`app/graph/nodes.py` 的 `DECISION_*` 常量、`app/audit/assertions.py` 的 `TERMINAL_STATUS_DECISIONS` 三处逐字同源；而且 SQLite 改不了已有表的 CHECK，`app/storage/db.py` 的加列机制（`_ADDED_COLUMNS`）只能加列不能改约束，.51 上的老库会静默保留旧 CHECK。「标记为人工撰写」的留痕走 `profile_json._jd_authorship`。
12. **⛔ 不碰 `app/outbound/`、`app/audit/`、`app/graph/nodes.py` 里的既有节点。** 本单元的两个新 `effect_*` 节点建在**新文件** `app/graph/jd_nodes.py` 里——既守住这条边界，也避开与并行泳道在 `nodes.py`（511 行）上的合并冲突。
13. **⛔ 不进 run-build 的范围外改动**：不改 `requirements.txt`、不改 CI、不改 `openspec/` 下任何文件（WBS 回勾由 run-build 收尾时统一做）。

---

## File Structure

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `app/agents/jd_grounding.py` | 新建 | 7.3 的全部纯函数：技术术语词表、画像 haystack 组装、`verify_jd_grounding()` |
| `app/agents/jd_agent.py` | 修改 | 7.5 的标识纯函数：`strip_ai_label` / `extract_label_generated_at` / `enforce_ai_label`；`_compose_with_label` 补空正文分支 |
| `app/graph/jd_nodes.py` | 新建 | 7.5 的两个 `effect_*` 节点与它们的 business_key 构造 |
| `app/web/server.py` | 修改 | 三个端点 + `_jd_payload()` 统一回执；`confirm()` 改用同一个回执 |
| `app/web/static/index.html` | 修改 | 7.7 复制按钮与兜底、JD 编辑框、标记人工撰写按钮、未溯源提示 |
| `tests/test_jd_grounding.py` | 新建 | Task 1 |
| `tests/test_jd_agent.py` | 修改（追加） | Task 2 |
| `tests/test_jd_nodes.py` | 新建 | Task 3 |
| `tests/test_jd_endpoints.py` | 新建 | Task 4 |
| `tests/test_static_frontend.py` | 修改（追加） | Task 5 |

依赖方向：Task 1 与 Task 2 互不依赖；Task 3 依赖 Task 2；Task 4 依赖 1/2/3；Task 5 依赖 4。⛔ 不要打乱顺序执行 3/4/5。

## 提取验证记录（2026-09-04，出计划时做的）

Task 1–4 的代码块**全部原样提取**到临时副本里跑过一遍真实的 pytest（Python 3.14 / 本仓库 venv），不是纸面推演：

| 范围 | 结果 |
|---|---|
| Task 1 `jd_grounding` + 其测试 | 54 passed |
| Task 2 标识纯函数 + 新增 12 条测试 | 12 passed |
| Task 2 改动后跑既有 `test_jd_agent.py` | 5 passed（`_compose_with_label` 的空正文分支不影响既有行为） |
| Task 3 两个 effect 节点 + 其测试 | 13 passed |
| Task 4 三端点（打进 server.py 副本）+ 其测试 | 13 passed |
| Task 4 改动后跑 `test_web_api` / `test_approval_branches` / `test_suspend_recovery` | 55 passed |

过程中被这一步**当场揪出并已在本计划里修掉**的三个真 bug，实施时不会再遇到：
1. `JD_TECHNICAL_TERMS["AUTOSAR"]` 原本 aliases 为空 → 画像里存的是枚举值 `"CP"`，"AUTOSAR" 这个词在画像里一个字都不出现，凡是文案写 AUTOSAR 的全被误判未溯源。已补 `CP`/`AP` 等别名。
2. `_FIELD_SEPARATOR` 原本写成一个**真的 NUL 字符**，会让 git/grep/编辑器把整个计划文件当二进制。已改成 `"\u0000"` 转义。
3. `AI_LABEL_PREFIX` 原本写成从模板 `split` 出来的表达式，模板措辞一变就会静默退化成"整个模板头"而守卫断言照样绿。已改成字面量 + 断言守护。

**边界**（照抄 spec-to-plan 技能的原话）：测试与被测代码出自同一份文档、同一个作者，全通只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 run-build 的两阶段 review 负责。Task 5 是前端，无 JS 测试运行器，只能靠 Step 5 的手工跑通。

---

### Task 1: JD 溯源校验纯函数（tasks 7.3）

**Files:**
- Create: `app/agents/jd_grounding.py`
- Test: `tests/test_jd_grounding.py`

**Interfaces:**
- Consumes: `app.agents.field_grounding.normalize_for_grounding(text) -> str`（已存在，NFKC + 去全部空白；本单元**唯一**允许的归一化入口）
- Produces:
  - `JD_TECHNICAL_TERMS: dict[str, tuple[str, ...]]` —— 术语 → 画像侧可接受的等价写法
  - `profile_grounding_haystack(profile: dict) -> str`
  - `verify_jd_grounding(jd_text: str, profile: dict) -> list[str]` —— 返回**未溯源**的术语名，按 `JD_TECHNICAL_TERMS` 声明序，确定性

- [ ] **Step 1: 写失败测试**

新建 `tests/test_jd_grounding.py`：

```python
"""JD 文案对画像字段的溯源校验（tasks 7.3）。

⚠️ 与 tests/test_field_grounding.py 是**两件事**，⛔ 不要合并：
那一份校验的是「画像字段」对「用户原话」的溯源（m1-intake-quality-fixes 第 7 章），
这一份校验的是「JD 文案」对「画像字段」的溯源。对象不同，一份绿不能替另一份作证。
"""

import pytest

from app.agents.jd_grounding import (
    JD_TECHNICAL_TERMS,
    profile_grounding_haystack,
    verify_jd_grounding,
)

PROFILE = {
    "job_title": "底层软件工程师",
    "department": "电子电器研发部",
    "headcount": 2,
    "education_requirement": "本科及以上",
    "experience_years": "3-5年",
    "core_skills": [{"name": "CAN-FD 驱动开发", "required": True}],
    "autosar_experience": ["CP"],
    "functional_safety": "ASIL-B",
    "mcu_family": ["英飞凌 Aurix"],
    "diag_stack": ["UDS"],
    "toolchain": ["CANoe"],
}


def test_terms_present_in_profile_are_grounded():
    jd = "岗位职责：基于 AUTOSAR CP 开发 CAN-FD 驱动，满足 ASIL-B，使用 CANoe 验证。"
    assert verify_jd_grounding(jd, PROFILE) == []


def test_terms_absent_from_profile_are_reported():
    """v4-pro 那次编造的正是这一类：画像里一个字都没有，文案里冒出来一串。"""
    jd = "任职要求：熟悉 FlexRay 与 SOME/IP，有 Lauterbach 调试经验。"
    assert verify_jd_grounding(jd, PROFILE) == ["FlexRay", "SOME/IP", "Lauterbach"]


def test_result_is_deterministic_and_declaration_ordered():
    """同一份输入重跑必须同一个结果、同一个顺序——否则这个数字不可复算，
    也就没有决策价值（design 决策 11 否决模型判官的同一条理由）。"""
    jd = "要求熟悉 Lauterbach、FlexRay。"
    first = verify_jd_grounding(jd, PROFILE)
    second = verify_jd_grounding(jd, PROFILE)
    assert first == second == ["FlexRay", "Lauterbach"]


def test_normalization_is_shared_with_field_grounding():
    """全半角与空白差异不算未溯源（复用 normalize_for_grounding 的同一口径）。"""
    jd = "要求 ＡＳＩＬ-Ｂ 与 CAN - FD 经验。"
    assert verify_jd_grounding(jd, PROFILE) == []


def test_jd_text_inside_profile_never_grounds_itself():
    """⛔ 最重要的一条：haystack 必须排除下划线内部键。

    _jd_text 就存在同一个 profile_json 里；把它算进 haystack，文案就会拿自己
    当证据，verify 永远返回空——这条校验会变成一个**永远不会红的摆设**，
    而且没有任何症状。
    """
    profile = {**PROFILE, "_jd_text": "熟悉 FlexRay 与 SOME/IP。"}
    assert verify_jd_grounding("熟悉 FlexRay 与 SOME/IP。", profile) == [
        "FlexRay",
        "SOME/IP",
    ]


def test_haystack_excludes_underscore_keys_and_booleans():
    haystack = profile_grounding_haystack(
        {"toolchain": ["CANoe"], "_jd_text": "FlexRay", "_jd_needs_manual": True}
    )
    assert "CANoe" in haystack
    assert "FlexRay" not in haystack


def test_terms_never_span_two_fields():
    """两个字段拼在一起不得凑出第三个术语。

    画像里有 "CAN" 和 "oe" 两个不相干的值时，⛔ 不许拼成 "CANoe" 把一个真正
    未溯源的工具链判成已溯源。字段之间用 NUL 隔开，子串跨不过去。
    """
    profile = {"diag_stack": ["CAN"], "toolchain": ["oe"]}
    assert verify_jd_grounding("使用 CANoe 验证。", profile) == ["CANoe"]


def test_empty_and_malformed_profile_do_not_raise():
    """画像是 LLM 自由生成的裸 dict，任何形状都不许抛——抛了就是一次 500。"""
    assert verify_jd_grounding("熟悉 FlexRay。", {}) == ["FlexRay"]
    assert verify_jd_grounding("熟悉 FlexRay。", None) == ["FlexRay"]
    assert verify_jd_grounding("", PROFILE) == []


def test_no_term_matches_nothing():
    assert verify_jd_grounding("岗位职责：负责团队日常协作与文档撰写。", PROFILE) == []


@pytest.mark.parametrize("term", sorted(JD_TECHNICAL_TERMS))
def test_every_term_grounds_itself(term):
    """词表的自洽守卫：把术语本身放进画像，它必须判为已溯源。

    加词条时最容易犯的错是别名写错（例如把 'ASIL B' 写进 aliases 却把主键写成
    'ASIL-B '），那会让这个词条**永远判未溯源**——加一个词条就多一条恒假的噪声，
    而噪声正是这个观测指标唯一怕的东西。
    """
    assert verify_jd_grounding(term, {"core_skills": [term]}) == []
```

- [ ] **Step 2: 跑测试确认它红**

Run: `venv/bin/python -m pytest tests/test_jd_grounding.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.agents.jd_grounding'`（收集期就报错，一条都跑不起来）

- [ ] **Step 3: 写实现**

新建 `app/agents/jd_grounding.py`：

```python
from __future__ import annotations

from typing import Any

from app.agents.field_grounding import normalize_for_grounding

# 字段之间的分隔符。**必须有**：每个字段的值各自归一化之后空白已经被去光，
# 直接首尾相接会让两个不相干字段凭空拼出第三个术语（"CAN" + "oe" → "CANoe"），
# 把真正的编造判成已溯源。
# 写成 "\u0000" 转义而**不是**敲一个真的 NUL 字符：源码里的裸 NUL 会让 git、
# grep、编辑器一律把这个文件当二进制处理。needle 归一化后本来就不含空白，
# 用空格也够用，选 NUL 只是为了一眼看出"这里绝不可能是术语的一部分"。
# ⛔ 不要改成会出现在真实画像值里的字符。
_FIELD_SEPARATOR = "\u0000"

# JD 里可能出现的技术术语 → 画像侧可接受的等价写法。
#
# **为什么是闭集词表而不是"从文案里自动抽术语"**：自动抽取要么靠模型（决策 11
# 否决过：判官自己会编，且不可复算），要么靠分词（引入新依赖，且中英混排的
# ECU 术语切不准）。闭集词表是确定性的、可评审的、可复算的，代价是漏掉词表外的
# 术语——那与决策 11 声明的"本批要的是一个下界"同向，可接受。
#
# **⛔ 不要加两个字母以内的纯拉丁词条**（TI / AP / CP / IO）：归一化去掉空白后
# 它们会命中大量无关词的内部片段，噪声会淹没真正的编造。它们只能作为别名出现。
#
# **大小写敏感**：本模块⛔ 不做大小写折叠——那等于在 normalize_for_grounding
# 之外另造一套归一化口径（Global Constraints 第 7 条）。代价是文案写
# "Autosar" 而词表写 "AUTOSAR" 时该词条整条漏掉（漏检，不是误报），方向仍是下界。
# 确实高频的大小写变体单独立一个词条，见下面的 "Autosar"。
#
# **加词条时同时想清楚 aliases**：aliases 是"画像里写成这样也算数"的清单，
# 不是"文案里写成这样也算命中"的清单。方向搞反会让词条恒判未溯源，
# test_every_term_grounds_itself 会当场抓到。
JD_TECHNICAL_TERMS: dict[str, tuple[str, ...]] = {
    # ── AUTOSAR ────────────────────────────────────────────────────────
    # ⚠️ "CP" / "AP" 作为**别名**出现是刻意的：画像里 autosar_experience 存的就是
    # AutosarLayer 枚举值 "CP"/"AP"（app/schemas/job_profile.py），"AUTOSAR" 这个词
    # 在画像里一个字都不会出现。不给这两个别名，凡是文案里写 AUTOSAR 的都会被判
    # 未溯源——一条恒真的噪声。代价是画像里别处出现 "AP"（"APP 开发"、"SAP"）也会
    # 把 AUTOSAR 判成已溯源，方向是漏检（下界），与决策 11 同向。
    "AUTOSAR": ("AUTOSAR", "CP", "AP", "Classic Platform", "Adaptive Platform"),
    "Autosar": ("AUTOSAR", "Autosar", "CP", "AP"),
    "AUTOSAR CP": ("CP", "Classic Platform", "AUTOSAR"),
    "AUTOSAR AP": ("AP", "Adaptive Platform", "AUTOSAR"),
    # ── 功能安全 ───────────────────────────────────────────────────────
    "ISO 26262": ("ISO 26262", "功能安全", "ASIL"),
    "ASIL-A": ("ASIL-A", "ASIL A"),
    "ASIL-B": ("ASIL-B", "ASIL B"),
    "ASIL-C": ("ASIL-C", "ASIL C"),
    "ASIL-D": ("ASIL-D", "ASIL D"),
    "FuSa": ("FuSa", "功能安全", "ASIL"),
    # ── MCU 平台 ───────────────────────────────────────────────────────
    "TriCore": ("TriCore", "TC3", "TC2", "Aurix", "英飞凌", "Infineon"),
    "Aurix": ("Aurix", "TC3", "TC2", "英飞凌", "Infineon"),
    "Infineon": ("Infineon", "英飞凌", "Aurix"),
    "英飞凌": ("英飞凌", "Infineon", "Aurix"),
    "S32K": ("S32K", "NXP"),
    "NXP": ("NXP", "S32K"),
    "STM32": ("STM32", "ST"),
    "Renesas": ("Renesas", "瑞萨"),
    "瑞萨": ("瑞萨", "Renesas"),
    # ── 总线与诊断 ─────────────────────────────────────────────────────
    "CAN-FD": ("CAN-FD", "CANFD", "CAN FD"),
    "CAN": (),
    "LIN": (),
    "FlexRay": (),
    "车载以太网": ("车载以太网", "以太网", "Ethernet"),
    "SOME/IP": ("SOME/IP", "SOMEIP"),
    "UDS": ("UDS", "ISO 14229", "ISO14229", "诊断"),
    "ISO 14229": ("ISO 14229", "ISO14229", "UDS"),
    "OBD": (),
    "XCP": (),
    "CCP": (),
    # ── 工具链 ─────────────────────────────────────────────────────────
    "CANoe": ("CANoe", "Vector"),
    "CANape": ("CANape", "Vector"),
    "DaVinci": ("DaVinci", "Vector"),
    "Vector": (),
    "INCA": ("INCA", "ETAS"),
    "ETAS": ("ETAS", "INCA"),
    "Keil": (),
    "IAR": (),
    "Lauterbach": ("Lauterbach", "Trace32", "TRACE32"),
    "Trace32": ("Trace32", "TRACE32", "Lauterbach"),
    "Simulink": ("Simulink", "MATLAB", "Matlab"),
    "MATLAB": ("MATLAB", "Matlab", "Simulink"),
    # ── 规范与流程 ─────────────────────────────────────────────────────
    "MISRA": ("MISRA", "MISRA C", "MISRAC"),
    "A-SPICE": ("A-SPICE", "ASPICE", "Automotive SPICE"),
    "ASPICE": ("ASPICE", "A-SPICE", "Automotive SPICE"),
}


def _flatten(value: Any) -> list[str]:
    """把任意嵌套结构摊成字符串列表。

    布尔值刻意丢弃：它在画像里的语义是"是/否"（is_mass_production、required），
    摊成 "True" 只会往 haystack 里塞一个不承载任何技术术语的噪声词。
    """
    if isinstance(value, bool):
        return []
    if isinstance(value, dict):
        return [text for item in value.values() for text in _flatten(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _flatten(item)]
    if value is None:
        return []
    return [str(value)]


def profile_grounding_haystack(profile: Any) -> str:
    """画像里所有**业务字段**的值，归一化后用 NUL 串起来。

    ⛔ 以下划线开头的键一律排除，这是本模块最关键的一条不变式：`_jd_text` 就存在
    同一个 profile_json 里（app/graph/nodes.py 的 effect_generate_and_persist_jd
    写进去的），算进 haystack 就等于让文案拿自己当证据，verify 永远返回空。
    这个故障**没有任何症状**：不报错、不失败，只是校验悄悄变成摆设。
    排除规则按"下划线前缀"而不是"具体键名清单"，是为了让以后新增的内部键
    （`_gap_acknowledgement`、`_jd_authorship`、还没想到的那些）自动落在外面。

    ⚠️ 入参是 LLM 自由生成的裸 dict，**任何形状都不许抛异常**——这份画像在
    POST /confirm 之前从没撞过 JobProfile 的类型约束（app/web/server.py 的注释
    写了同一件事）。
    """
    if not isinstance(profile, dict):
        return ""
    parts: list[str] = []
    for name, value in profile.items():
        if str(name).startswith("_"):
            continue
        parts.extend(_flatten(value))
    return _FIELD_SEPARATOR.join(normalize_for_grounding(part) for part in parts)


def verify_jd_grounding(jd_text: Any, profile: Any) -> list[str]:
    """返回文案里**未溯源**的技术术语，按 JD_TECHNICAL_TERMS 的声明序。

    确定性，不调模型（design.md 决策 11 的形状）。判据与 field_grounding 逐字
    同源：两侧都过 normalize_for_grounding，然后做子串判定。

    **只观测不拦截**（决策 12）：调用方⛔ 不得据此拦下文案、重新生成或降级。
    返回空列表 = 词表内的术语全部有画像依据，⛔ 不等于"文案没有编造"——
    词表外的编造这里看不见，这个数字是下界不是精确值。
    """
    haystack = profile_grounding_haystack(profile)
    jd = normalize_for_grounding(jd_text if jd_text is not None else "")

    ungrounded: list[str] = []
    for term, aliases in JD_TECHNICAL_TERMS.items():
        needle = normalize_for_grounding(term)
        if not needle or needle not in jd:
            continue
        accepted = [needle] + [normalize_for_grounding(alias) for alias in aliases]
        if not any(candidate and candidate in haystack for candidate in accepted):
            ungrounded.append(term)
    return ungrounded
```

- [ ] **Step 4: 跑测试确认它绿**

Run: `venv/bin/python -m pytest tests/test_jd_grounding.py -q`
Expected: PASS，**54 passed**（9 条常规用例 + `test_every_term_grounds_itself` 对 45 个词条的参数化）。数字是出计划时实跑测出来的，不是估的；对不上说明词表被改过——**⛔ 不要直接改断言迁就它**，先确认新词条的 aliases 写对了没有。

- [ ] **Step 5: 跑一遍既有溯源测试，确认没碰坏 field_grounding**

Run: `venv/bin/python -m pytest tests/test_field_grounding.py tests/test_jd_agent.py -q`
Expected: PASS，0 failed

- [ ] **Step 6: 提交**

```bash
git add app/agents/jd_grounding.py tests/test_jd_grounding.py
git commit -m "feat(jd): JD 文案对画像字段的确定性溯源校验（tasks 7.3）"
```

---

### Task 2: AI 标识的剥离、回读与强制重贴（tasks 7.5 纯函数层）

**Files:**
- Modify: `app/agents/jd_agent.py`（在 `_compose_with_label` 附近增补，⛔ 不动 `generate_jd` 与 `DISCRIMINATORY_PATTERNS`）
- Test: `tests/test_jd_agent.py`（追加，⛔ 不改既有用例）

**Interfaces:**
- Consumes: 本文件已有的 `AI_LABEL_TEMPLATE`、`_compose_with_label(body, generated_at)`
- Produces:
  - `AI_LABEL_PREFIX: str = "【AI 生成】"`
  - `UNKNOWN_GENERATED_AT: str`
  - `strip_ai_label(text: str) -> str`
  - `extract_label_generated_at(text: str) -> str | None`
  - `enforce_ai_label(text: str, *, generated_at: str) -> str`

- [ ] **Step 1: 写失败测试**

先把 `tests/test_jd_agent.py` **顶部已有的**那个 import 扩成下面这样（⛔ 不要在文件末尾另起一个 import 块加 `# noqa: E402`——那是在给 lint 让路，不是在写测试）：

```python
from app.agents.jd_agent import (
    AI_LABEL_PREFIX,
    AI_LABEL_TEMPLATE,
    UNKNOWN_GENERATED_AT,
    contains_discriminatory_language,
    enforce_ai_label,
    extract_label_generated_at,
    generate_jd,
    strip_ai_label,
)
```

然后在 `tests/test_jd_agent.py` **末尾追加**：

```python
# ── AI 标识的保护（tasks 7.5 纯函数层）──────────────────────────────────
#
# 合规红线：AI 生成的 JD 须带标识（《AI 生成合成内容标识办法》）。
# 下面这几个函数是那条红线在代码里的落点，⛔ 常规编辑不得删标识。

_TS = "2026-09-04T02:00:00+00:00"
_LABELLED = f"岗位职责：负责 ECU 底层软件开发。\n\n{AI_LABEL_TEMPLATE.format(generated_at=_TS)}"


def test_prefix_is_the_actual_head_of_the_template():
    """前缀与模板必须同源。写死一个和模板对不上的前缀，strip 会一行都剥不掉，
    而"标识删不掉"这个保护就悄悄失效了——不报错、不失败。"""
    assert AI_LABEL_TEMPLATE.startswith(AI_LABEL_PREFIX)


def test_strip_removes_the_label_line_only():
    assert strip_ai_label(_LABELLED) == "岗位职责：负责 ECU 底层软件开发。"


def test_strip_removes_every_label_line_even_if_duplicated():
    """反复编辑保存过的老文本里可能积了不止一行标识，必须全剥干净，
    否则重贴之后会越堆越多。"""
    label = AI_LABEL_TEMPLATE.format(generated_at=_TS)
    text = f"正文\n\n{label}\n{label}"
    assert strip_ai_label(text) == "正文"


def test_strip_tolerates_leading_whitespace_before_the_label():
    label = AI_LABEL_TEMPLATE.format(generated_at=_TS)
    assert strip_ai_label(f"正文\n\n   {label}") == "正文"


def test_strip_keeps_text_that_merely_mentions_ai():
    """只有以标识前缀开头的**整行**才是标识。正文里提到"AI"不能被吃掉。"""
    text = "岗位职责：开发 AI 相关的嵌入式模块。"
    assert strip_ai_label(text) == text


def test_extract_reads_back_the_generation_time():
    assert extract_label_generated_at(_LABELLED) == _TS


def test_extract_returns_none_when_there_is_no_label():
    assert extract_label_generated_at("岗位职责：负责 ECU 底层软件开发。") is None


def test_extract_round_trips_whatever_compose_produced():
    """回读的正则由模板拆出来，不是手抄的。这条用例守的是"模板改了、正则没跟着改"
    ——那会让回读静默失效，编辑一次就把真实生成时间换成"未知"。"""
    composed = f"正文\n\n{AI_LABEL_TEMPLATE.format(generated_at=_TS)}"
    assert extract_label_generated_at(composed) == _TS


def test_enforce_reattaches_the_label_after_a_user_deleted_it():
    """7.5 的核心：不管用户提交上来的文本里有没有标识，服务端一律重新贴。
    ⛔ 不是"检查他删没删"，是"重新贴"。"""
    edited = "岗位职责：负责 ECU 底层软件开发（HR 改过一版）。"
    result = enforce_ai_label(edited, generated_at=_TS)
    assert result.startswith(edited)
    assert result.endswith(AI_LABEL_TEMPLATE.format(generated_at=_TS))


def test_enforce_does_not_stack_labels():
    once = enforce_ai_label(_LABELLED, generated_at=_TS)
    twice = enforce_ai_label(once, generated_at=_TS)
    assert once == twice
    assert once.count(AI_LABEL_PREFIX) == 1


def test_enforce_on_empty_body_yields_the_label_alone():
    """正文被清空也必须留下标识，⛔ 不留下两个空行加一行标识那种脏输出。"""
    assert enforce_ai_label("", generated_at=_TS) == AI_LABEL_TEMPLATE.format(
        generated_at=_TS
    )


def test_unknown_generated_at_is_honest_not_a_fake_timestamp():
    """回读不出生成时间时用一个明说"没留存"的占位串。⛔ 不许拿"现在"冒充
    生成时间——一个错的时间比一个诚实的空缺更难被发现，也更难被审计接受。"""
    assert "未知" in UNKNOWN_GENERATED_AT
```

- [ ] **Step 2: 跑测试确认它红**

Run: `venv/bin/python -m pytest tests/test_jd_agent.py -q`
Expected: FAIL —— `ImportError: cannot import name 'AI_LABEL_PREFIX' from 'app.agents.jd_agent'`

- [ ] **Step 3: 写实现**

在 `app/agents/jd_agent.py` 顶部的 import 段加 `import re`（放在 `import datetime as dt` 之后，保持字母序）：

```python
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
```

在 `AI_LABEL_TEMPLATE` 定义之后**紧接着**插入：

```python
# 标识行的识别前缀。它**必须**是 AI_LABEL_TEMPLATE 真正的开头：对不上的话
# strip_ai_label 一行都剥不掉，而"标识不可删"这条保护就静默失效了——不报错、
# 不失败，只是编辑一次标识就没了。改模板时**必须**同步改这里，
# tests/test_jd_agent.py::test_prefix_is_the_actual_head_of_the_template 会当场抓到。
# ⛔ 不要写成从模板里 split 出来的表达式：那种写法在模板措辞变化时会静默退化成
# "整个模板头"，而守卫断言照样是绿的。
AI_LABEL_PREFIX = "【AI 生成】"

# 回读不出生成时间时的占位串。⛔ 绝不拿"现在"冒充生成时间：一个错的时间戳
# 比一个诚实的空缺更难被发现，审计那天也解释不过去（合规红线：AI 生成内容
# 标识办法要的是让人看见真实情况）。
UNKNOWN_GENERATED_AT = "未知（该文案生成时间未留存）"

# 回读用的正则从模板**拆**出来，不是另抄一份。模板改了正则自动跟着变，
# 不会出现"模板换了措辞、回读静默失效、编辑一次就把真实生成时间换成未知"
# 这种无症状故障。
_LABEL_HEAD, _LABEL_TAIL = AI_LABEL_TEMPLATE.split("{generated_at}")
_LABEL_PATTERN = re.compile(
    re.escape(_LABEL_HEAD) + r"(?P<generated_at>.*?)" + re.escape(_LABEL_TAIL)
)
```

⚠️ `AI_LABEL_PREFIX` 那一行的两次 `split` 是为了从模板里切出 `"【AI 生成】"` 这一段而不写死它。实现时**直接照抄上面这一行**；它对当前模板 `"【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {generated_at}。"` 求值为 `"【AI 生成】"`，并由 Step 1 的断言守住。

把已有的 `_compose_with_label` 替换为（只加一个空正文分支，其余逐字不变）：

```python
def _compose_with_label(body: str, generated_at: str) -> str:
    label = AI_LABEL_TEMPLATE.format(generated_at=generated_at)
    # 正文被清空时只留标识：拼成 "\n\n【AI 生成】…" 会在文案顶上留两个空行，
    # 复制出去贴到招聘平台上就是两行空白。标识本身一个字都不能少。
    if not body:
        return label
    return f"{body}\n\n{label}"
```

在文件**末尾**追加三个纯函数：

```python
def strip_ai_label(text: str) -> str:
    """去掉文本里所有的 AI 标识行。

    ⛔ **这不是"给用户删标识"的功能。** 它只有两个合法调用点：
    ① enforce_ai_label 内部——剥掉再重贴，保证不管用户提交什么都只有一行标识；
    ② effect_mark_jd_human_written——「标记为人工撰写」是唯一能真的去掉标识的
       路径，且必须留痕（合规红线：AI 生成内容标识办法）。
    ⛔ 任何 HTTP handler 都不得直接调用它。

    判据是"整行以标识前缀开头"，不是"文本里含标识前缀"：正文里提到 AI 的句子
    不能被吃掉。行首空白先 strip 掉再判——历史文本里的标识行可能带缩进。
    """
    kept = [
        line
        for line in str(text).splitlines()
        if not line.strip().startswith(AI_LABEL_PREFIX)
    ]
    return "\n".join(kept).strip()


def extract_label_generated_at(text: str) -> str | None:
    """从已带标识的文本里回读**原始生成时间**。读不出返回 None。

    为什么要回读而不是重新取"现在"：标识记录的是"这份文案是什么时候由 AI
    生成的"，不是"HR 什么时候编辑的"。编辑一次就把时间往后推，这条标识就
    从事实退化成噪声。

    为什么不落一个 `_jd_generated_at` 键：那要改 app/graph/nodes.py 的既有节点
    effect_generate_and_persist_jd，被本交付单元的边界禁止（Global Constraints
    第 12 条）。时间戳本来就完整地印在标识行里，回读是无损的。
    """
    match = _LABEL_PATTERN.search(str(text))
    if match is None:
        return None
    return match.group("generated_at").strip() or None


def enforce_ai_label(text: str, *, generated_at: str) -> str:
    """先剥干净、再重贴唯一一行标识。

    这是 7.5「常规编辑不可删标识」的**唯一**实现方式：服务端不去检查用户有没有
    删标识（检查就有绕过空间——改一个字、换个标点、插一行空白都能骗过检查），
    而是无条件把提交上来的文本当作正文重新贴标识。用户删不删都一样。
    """
    return _compose_with_label(strip_ai_label(text), generated_at)
```

- [ ] **Step 4: 跑测试确认它绿**

Run: `venv/bin/python -m pytest tests/test_jd_agent.py -q`
Expected: PASS，**17 passed**（既有 5 条 `generate_jd` / 歧视词用例 + 新追加 12 条），0 failed。
出计划时已实跑确认：把 `_compose_with_label` 改成带空正文分支之后，既有那 5 条**一条都没受影响**。

- [ ] **Step 5: 提交**

```bash
git add app/agents/jd_agent.py tests/test_jd_agent.py
git commit -m "feat(jd): AI 标识的剥离、回读与强制重贴纯函数（tasks 7.5）"
```

---

### Task 3: 编辑与「标记为人工撰写」两个 effect 节点（tasks 7.5 副作用层）

**Files:**
- Create: `app/graph/jd_nodes.py`
- Test: `tests/test_jd_nodes.py`

**Interfaces:**
- Consumes:
  - `app.agents.jd_agent.strip_ai_label(text) -> str` / `enforce_ai_label(text, *, generated_at) -> str` / `extract_label_generated_at(text) -> str | None` / `UNKNOWN_GENERATED_AT: str`（Task 2 产出）
  - `app.storage.idempotency.idempotent_effect(node_name)`（已存在；被装饰函数签名固定为 `(conn, *, thread_id, business_key, **kwargs)`，装饰器负责查 `effect_log`、写 `effect_log`、`conn.commit()`）
- Produces:
  - `class JDNotGeneratedError(Exception)`
  - `jd_edit_business_key(version: int, text: str) -> str`
  - `effect_update_jd_text(conn, *, thread_id, business_key, version: int, edited_text: str) -> str | None`
  - `effect_mark_jd_human_written(conn, *, thread_id, business_key, version: int, reviewer: str, marked_at: str) -> str | None`
  - 内部键约定：`_jd_authorship = {"human_written": True, "marked_by": <reviewer>, "at": <marked_at>}`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_jd_nodes.py`：

```python
"""JD 编辑与「标记为人工撰写」两个 effect_* 节点（tasks 7.5）。

⚠️ 两个动作**各自独占一个节点**，⛔ 不合并成一个"处理 JD 变更"节点。
理由是工程铁律 1 的直接推论：两条路径的对外后果完全不同（改正文但保留标识 /
去掉标识并留痕），合成一个节点后"恢复时节点从头整个重跑"会带着一个分支参数
走进另一条路径，而幂等键只有一个——重跑一次就可能把标识去掉。
"""

import json

import pytest

from app.agents.jd_agent import AI_LABEL_PREFIX, AI_LABEL_TEMPLATE
from app.graph.jd_nodes import (
    JDNotGeneratedError,
    effect_mark_jd_human_written,
    effect_update_jd_text,
    jd_edit_business_key,
)
from app.storage.db import get_connection, init_schema

_TS = "2026-09-04T02:00:00+00:00"
_LABEL = AI_LABEL_TEMPLATE.format(generated_at=_TS)


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "jd.db"))
    init_schema(connection)
    connection.execute(
        "INSERT INTO job (id, title, status) VALUES ('J1', '底层软件工程师', 'approved')"
    )
    connection.execute(
        "INSERT INTO job_profile (job_id, version, status, profile_json) "
        "VALUES ('J1', 1, 'approved', ?)",
        (
            json.dumps(
                {
                    "job_title": "底层软件工程师",
                    "mcu_family": ["英飞凌 Aurix"],
                    "_jd_text": f"岗位职责：负责 ECU 底层软件开发。\n\n{_LABEL}",
                    "_jd_needs_manual": False,
                },
                ensure_ascii=False,
            ),
        ),
    )
    connection.commit()
    return connection


def _profile(conn) -> dict:
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id='J1' AND version=1"
    ).fetchone()
    return json.loads(row[0])


def _effect_rows(conn, node_name: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?", (node_name,)
    ).fetchone()[0]


# ── 常规编辑：标识删不掉 ────────────────────────────────────────────────


def test_edit_reattaches_the_label_the_user_deleted(conn):
    """合规红线的核心断言：用户把标识整行删掉再提交，落库结果照样带标识。"""
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "HR 改过的正文，标识已被我删掉。"),
        version=1,
        edited_text="HR 改过的正文，标识已被我删掉。",
    )
    stored = _profile(conn)["_jd_text"]
    assert stored.startswith("HR 改过的正文，标识已被我删掉。")
    assert stored.endswith(_LABEL)


def test_edit_preserves_the_original_generation_time(conn):
    """标识记的是"AI 什么时候生成的"，不是"HR 什么时候改的"。"""
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "改一版"),
        version=1,
        edited_text="改一版",
    )
    assert _TS in _profile(conn)["_jd_text"]


def test_edit_does_not_stack_labels(conn):
    text = f"正文\n\n{_LABEL}"
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, text),
        version=1,
        edited_text=text,
    )
    assert _profile(conn)["_jd_text"].count(AI_LABEL_PREFIX) == 1


def test_edit_never_touches_business_fields_or_status(conn):
    """决策 4：画像冻结后不可变。⛔ 只准动下划线内部键。"""
    before = _profile(conn)
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "改一版"),
        version=1,
        edited_text="改一版",
    )
    after = _profile(conn)
    assert {k: v for k, v in after.items() if not k.startswith("_")} == {
        k: v for k, v in before.items() if not k.startswith("_")
    }
    status = conn.execute(
        "SELECT status FROM job_profile WHERE job_id='J1' AND version=1"
    ).fetchone()[0]
    assert status == "approved"


def test_edit_is_idempotent_on_the_same_text(conn):
    """双击、超时重发、反向代理重试都会打过来同一份文本。"""
    key = jd_edit_business_key(1, "改一版")
    effect_update_jd_text(
        conn, thread_id="J1", business_key=key, version=1, edited_text="改一版"
    )
    assert (
        effect_update_jd_text(
            conn, thread_id="J1", business_key=key, version=1, edited_text="改一版"
        )
        is None
    )
    assert _effect_rows(conn, "effect_update_jd_text") == 1


def test_a_genuinely_different_edit_goes_through(conn):
    """幂等键含内容哈希，所以"改第二版"不会被第一版的 effect_log 短路挡住。"""
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "第一版"),
        version=1,
        edited_text="第一版",
    )
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "第二版"),
        version=1,
        edited_text="第二版",
    )
    assert _profile(conn)["_jd_text"].startswith("第二版")
    assert _effect_rows(conn, "effect_update_jd_text") == 2


def test_edit_before_any_jd_exists_raises(conn):
    conn.execute(
        "INSERT INTO job_profile (job_id, version, status, profile_json) "
        "VALUES ('J1', 2, 'drafting', ?)",
        (json.dumps({"job_title": "尚未生成 JD"}, ensure_ascii=False),),
    )
    conn.commit()
    with pytest.raises(JDNotGeneratedError):
        effect_update_jd_text(
            conn,
            thread_id="J1",
            business_key=jd_edit_business_key(2, "改一版"),
            version=2,
            edited_text="改一版",
        )


# ── 标记为人工撰写：唯一能去标识的路径，且必须留痕 ──────────────────────


def test_mark_removes_the_label_and_records_who_and_when(conn):
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    profile = _profile(conn)
    assert AI_LABEL_PREFIX not in profile["_jd_text"]
    assert profile["_jd_authorship"] == {
        "human_written": True,
        "marked_by": "zhangsan",
        "at": _TS,
    }


@pytest.mark.compliance
def test_label_removal_and_the_record_are_inseparable(conn):
    """合规红线的结构性保证：标识去掉了但没有留痕，这个状态**不可能存在**。

    两者写在同一行的同一次 UPDATE 里，由 idempotent_effect 在同一个事务里
    连同 effect_log 一起提交（工程铁律 1）。⛔ 不许拆成两条 UPDATE，
    更不许拆成两次 commit——那正是 .51 现网 2026-08-10/08-12 丢 outbox 的形状。
    """
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    rows = conn.execute("SELECT profile_json FROM job_profile").fetchall()
    for (raw,) in rows:
        profile = json.loads(raw)
        text = profile.get("_jd_text")
        if text is None:
            continue
        if AI_LABEL_PREFIX not in text:
            assert profile.get("_jd_authorship"), (
                "存在一份没有 AI 标识、也没有人工撰写留痕的 JD——"
                "这正是《AI 生成合成内容标识办法》要禁止的状态"
            )


@pytest.mark.compliance
def test_effect_log_count_equals_marked_profile_count(conn):
    """工程铁律 1 的 reviewer 判据：effect_log 条数与业务行数按 thread 恒等。"""
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="lisi",
        marked_at="2026-09-05T00:00:00+00:00",
    )
    logged = _effect_rows(conn, "effect_mark_jd_human_written")
    marked = sum(
        1
        for (raw,) in conn.execute("SELECT profile_json FROM job_profile").fetchall()
        if json.loads(raw).get("_jd_authorship")
    )
    assert logged == marked == 1


def test_mark_is_idempotent_and_keeps_the_first_reviewer(conn):
    """重复标记只留第一个人。第一个按下这个按钮的人才是决策人。"""
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    assert (
        effect_mark_jd_human_written(
            conn,
            thread_id="J1",
            business_key="1",
            version=1,
            reviewer="lisi",
            marked_at="2026-09-05T00:00:00+00:00",
        )
        is None
    )
    assert _profile(conn)["_jd_authorship"]["marked_by"] == "zhangsan"


@pytest.mark.compliance
def test_mark_rejects_a_blank_reviewer(conn):
    """合规红线：决策人只能是人。空白 reviewer 等于"没人负责却去掉了标识"。"""
    with pytest.raises(ValueError):
        effect_mark_jd_human_written(
            conn,
            thread_id="J1",
            business_key="1",
            version=1,
            reviewer="   ",
            marked_at=_TS,
        )
    assert AI_LABEL_PREFIX in _profile(conn)["_jd_text"]
    assert _effect_rows(conn, "effect_mark_jd_human_written") == 0


def test_edit_after_marking_does_not_bring_the_label_back(conn):
    """已经声明是人工撰写的文案，再编辑⛔ 不许把 AI 标识贴回去——
    那会把一份人写的文案标成 AI 生成的，方向反了同样是错的标识。"""
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "人工重写的正文"),
        version=1,
        edited_text="人工重写的正文",
    )
    profile = _profile(conn)
    assert profile["_jd_text"] == "人工重写的正文"
    assert profile["_jd_authorship"]["marked_by"] == "zhangsan"
```

- [ ] **Step 2: 跑测试确认它红**

Run: `venv/bin/python -m pytest tests/test_jd_nodes.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.graph.jd_nodes'`

- [ ] **Step 3: 写实现**

新建 `app/graph/jd_nodes.py`：

```python
from __future__ import annotations

import hashlib
import json
import sqlite3

from app.agents.jd_agent import (
    AI_LABEL_PREFIX,
    UNKNOWN_GENERATED_AT,
    enforce_ai_label,
    extract_label_generated_at,
    strip_ai_label,
)
from app.storage.idempotency import idempotent_effect

# `profile_json` 里承载 JD 相关状态的三个内部键。都以下划线开头，
# 于是 app/agents/jd_grounding.profile_grounding_haystack 会自动把它们排除在
# 溯源证据之外（那里按前缀排除，不按键名清单）。
JD_TEXT_KEY = "_jd_text"
JD_AUTHORSHIP_KEY = "_jd_authorship"


class JDNotGeneratedError(Exception):
    """这一版画像还没有 JD，编辑与标记都无从谈起。

    ⛔ 不要在节点里把它降级成"那就当空文案处理"：那会凭空造出一份只有 AI 标识、
    没有正文的 JD 落进库里，而调用方看到的是一次成功。
    """


def jd_edit_business_key(version: int, text: str) -> str:
    """`{version}:{正文哈希}`。

    含内容哈希是刻意的：同一份文本重复提交（双击、客户端超时重发、反向代理
    重试）必须被 effect_log 短路，而"HR 真的又改了一版"必须能过去。只用
    version 会把第二次真实编辑吃掉，只用哈希会让不同版本的同名编辑串在一起。
    与 app/graph/nodes.py 的 message_business_key 同一条思路。
    """
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]
    return f"{version}:{digest}"


def _load_profile(conn: sqlite3.Connection, *, job_id: str, version: int) -> dict:
    """读出这一版画像的 profile_json。

    在节点函数体内读、用同一个 conn：读与随后的写落在同一个事务里，
    ⛔ 不许让调用方先读好再传进来——那中间隔着一次 HTTP 反序列化，
    读到的可能已经不是写回去时那一版。
    """
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
        (job_id, version),
    ).fetchone()
    if row is None:
        raise JDNotGeneratedError(f"找不到画像 {job_id} v{version}")
    profile = json.loads(row[0])
    if JD_TEXT_KEY not in profile:
        raise JDNotGeneratedError(f"画像 {job_id} v{version} 还没有生成过 JD")
    return profile


def _save_profile(
    conn: sqlite3.Connection, *, job_id: str, version: int, profile: dict
) -> None:
    """一次 UPDATE 写回整个 profile_json。

    ⛔ 不拆成多条 UPDATE：`_jd_text` 与 `_jd_authorship` 必须一起落地。拆开之后
    "标识已经去掉、留痕还没写"会成为一个真实存在的中间态，而崩在那一刻就留下
    一份没有标识也没有责任人的 JD——正是《AI 生成合成内容标识办法》要禁止的东西。

    不在这里 conn.commit()：写入必须与 effect_log 记录由 idempotent_effect
    装饰器在同一个事务里一次性提交（工程铁律 1）。
    """
    conn.execute(
        "UPDATE job_profile SET profile_json = ? WHERE job_id = ? AND version = ?",
        (json.dumps(profile, ensure_ascii=False), job_id, version),
    )


@idempotent_effect("effect_update_jd_text")
def effect_update_jd_text(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    version: int,
    edited_text: str,
) -> str:
    """effect_* 节点：把 HR 编辑过的 JD 正文写回画像，独占、幂等。

    **标识保护在这里落地**（合规红线：AI 生成的 JD 须带标识）：⛔ 不检查用户有
    没有删标识——检查就有绕过空间（改一个字、换个标点、插一行空白都能骗过检查）。
    这里无条件把提交上来的整段文本当作正文，剥干净再重贴唯一一行标识。

    唯一的例外是已经走过「标记为人工撰写」的文案：那份文案的作者已经是人，
    再贴 AI 标识就是**另一个方向上的错误标识**，所以只剥不贴。

    business_key = `{version}:{正文哈希}`（jd_edit_business_key）。

    ⛔ 只改 profile_json 里以下划线开头的内部键，不动业务字段、不动 status、
    不新建版本（design.md 决策 4：画像冻结后不可变）。

    不在这里 conn.commit() —— 理由同 app/graph/nodes.py 的 effect_persist_draft。
    """
    profile = _load_profile(conn, job_id=thread_id, version=version)

    if profile.get(JD_AUTHORSHIP_KEY):
        final_text = strip_ai_label(edited_text)
    else:
        generated_at = (
            extract_label_generated_at(profile[JD_TEXT_KEY]) or UNKNOWN_GENERATED_AT
        )
        final_text = enforce_ai_label(edited_text, generated_at=generated_at)

    profile[JD_TEXT_KEY] = final_text
    _save_profile(conn, job_id=thread_id, version=version, profile=profile)
    return final_text


@idempotent_effect("effect_mark_jd_human_written")
def effect_mark_jd_human_written(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    version: int,
    reviewer: str,
    marked_at: str,
) -> str:
    """effect_* 节点：把这一版 JD 标记为人工撰写并去掉 AI 标识，独占、幂等。

    **这是唯一一条能去掉 AI 标识的路径**（job-description spec：「若 HR 大幅
    改写文案，系统提供"标记为人工撰写"的显式操作，该操作被记录」）。去标识与
    留痕写在**同一行的同一次 UPDATE** 里，于是"标识没了但查不到谁去的"在结构上
    不可能发生——这比任何检查都可靠。

    business_key = 被标记的画像 version。重复标记命中 effect_log 直接短路，
    留痕里保留**第一个**按下按钮的人：第一个做这个决定的人才是决策人。

    ⛔ reviewer 不接受空白，⛔ 更不得写入任何自动判定的产物（合规红线：
    决策人只能是人）。与 app/graph/nodes.py 的 _record_human_review 同一条规矩。

    ⛔ 不写 human_review 表：那张表的 decision_type CHECK 只认
    approved/revision_requested/abandoned 三个值，且这三个字面量与
    app/graph/nodes.py 的 DECISION_* 常量、app/audit/assertions.py 的
    TERMINAL_STATUS_DECISIONS 逐字同源；SQLite 又改不了已有表的 CHECK，
    .51 上的老库会静默保留旧约束。留痕走 profile_json 的内部键，与
    `_gap_acknowledgement` 同一条路（design.md 决策 8：走内部键，不建新表）。

    不在这里 conn.commit() —— 理由同 effect_update_jd_text。
    """
    if not str(reviewer).strip():
        raise ValueError(
            "标记为人工撰写必须记下是谁标的（合规红线：决策人只能是人）；"
            "⛔ 不得用系统默认值或自动判定结果顶替"
        )

    profile = _load_profile(conn, job_id=thread_id, version=version)
    final_text = strip_ai_label(profile[JD_TEXT_KEY])

    profile[JD_TEXT_KEY] = final_text
    profile[JD_AUTHORSHIP_KEY] = {
        "human_written": True,
        "marked_by": reviewer,
        "at": marked_at,
    }
    _save_profile(conn, job_id=thread_id, version=version, profile=profile)
    return final_text
```

⚠️ 实现时注意：`AI_LABEL_PREFIX` 在本模块里没有被直接用到（只在测试里断言），
所以**⛔ 不要 import 它**——把上面 import 段里的 `AI_LABEL_PREFIX,` 一行删掉，
否则 ruff/flake8 会报 F401。保留的四个是 `UNKNOWN_GENERATED_AT`、
`enforce_ai_label`、`extract_label_generated_at`、`strip_ai_label`。

- [ ] **Step 4: 跑测试确认它绿**

Run: `venv/bin/python -m pytest tests/test_jd_nodes.py -q`
Expected: PASS，13 passed

- [ ] **Step 5: 跑一遍幂等与事务归属的既有测试，确认没影响别人**

Run: `venv/bin/python -m pytest tests/test_idempotency.py tests/test_graph_idempotency.py tests/test_transaction_ownership.py -q`
Expected: PASS，0 failed

- [ ] **Step 6: 提交**

```bash
git add app/graph/jd_nodes.py tests/test_jd_nodes.py
git commit -m "feat(jd): JD 编辑与标记人工撰写两个幂等 effect 节点（tasks 7.5）"
```

---

### Task 4: JD 的读取 / 编辑 / 标记三个端点与统一回执（tasks 7.3 观测面 + 7.5 接线）

**Files:**
- Modify: `app/web/server.py`（新增 `JDEditRequest`、`_latest_version_or_404()`、`_jd_payload()`、三个路由；`confirm()` 的 return 改用 `_jd_payload()`）
- Test: `tests/test_jd_endpoints.py`

**Interfaces:**
- Consumes:
  - `app.agents.jd_grounding.verify_jd_grounding(jd_text, profile) -> list[str]`（Task 1）
  - `app.graph.jd_nodes.effect_update_jd_text` / `effect_mark_jd_human_written` / `jd_edit_business_key` / `JDNotGeneratedError`（Task 3）
  - 本文件已有的 `reviewer_of(request)`、`_reject_if_abandoned(job_id)`、`sqlite_utc_now()`
- Produces: JD 统一回执 —— `{job_id, version, jd_text, needs_manual, human_written, authorship, ungrounded_terms}`。`confirm()`、`GET /jd`、`POST /jd`、`POST /jd/human-written` 四个出口**同一个形状**。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_jd_endpoints.py`：

```python
"""JD 的读取 / 编辑 / 标记人工撰写三个端点（tasks 7.3 / 7.5）。"""

import json

import pytest

from app.agents.jd_agent import AI_LABEL_PREFIX
from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE
from tests.test_web_api import make_app


def _confirmed_job(tmp_path, jd_response: str = JD_RESPONSE, root_path: str = ""):
    """跑到"画像已确认、JD 已生成"这一步，返回 (client, job_id, confirm 回执)。"""
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE, jd_response], root_path=root_path)
    prefix = root_path
    job_id = client.post(f"{prefix}/api/jobs", json={"message": "要个做 ECU 底层的"}).json()[
        "job_id"
    ]
    resp = client.post(
        f"{prefix}/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True}
    )
    assert resp.status_code == 200, resp.text
    return client, job_id, resp.json()


def test_confirm_returns_the_unified_jd_payload(tmp_path):
    _, _, body = _confirmed_job(tmp_path)
    assert set(body) >= {
        "job_id",
        "version",
        "jd_text",
        "needs_manual",
        "human_written",
        "authorship",
        "ungrounded_terms",
    }
    assert body["human_written"] is False
    assert body["authorship"] is None


def test_get_jd_returns_the_same_payload_as_confirm(tmp_path):
    client, job_id, confirmed = _confirmed_job(tmp_path)
    fetched = client.get(f"/api/jobs/{job_id}/jd").json()
    assert fetched == confirmed


def test_ungrounded_terms_are_reported_not_blocked(tmp_path):
    """决策 12：只观测不拦截。编造了术语的 JD 照样 200、照样落库、照样能拿到。"""
    fabricated = json.dumps(
        {"body": "任职要求：熟悉 FlexRay 与 Lauterbach 调试。"}, ensure_ascii=False
    )
    _, _, body = _confirmed_job(tmp_path, jd_response=fabricated)
    assert body["ungrounded_terms"] == ["FlexRay", "Lauterbach"]
    assert "FlexRay" in body["jd_text"]  # ⛔ 没被拦下、没被删掉


def test_ungrounded_terms_empty_when_everything_traces_back(tmp_path):
    grounded = json.dumps(
        {"body": "岗位职责：基于 AUTOSAR CP 开发 CAN 驱动，满足 ASIL-B。"},
        ensure_ascii=False,
    )
    _, _, body = _confirmed_job(tmp_path, jd_response=grounded)
    assert body["ungrounded_terms"] == []


@pytest.mark.compliance
def test_editing_cannot_delete_the_ai_label(tmp_path):
    """7.5 的红线断言：前端把标识删光提交上来，回执与落库结果照样带标识。"""
    client, job_id, _ = _confirmed_job(tmp_path)
    resp = client.post(
        f"/api/jobs/{job_id}/jd", json={"text": "我把标识删了，只留正文。"}
    )
    assert resp.status_code == 200
    assert AI_LABEL_PREFIX in resp.json()["jd_text"]
    assert AI_LABEL_PREFIX in client.get(f"/api/jobs/{job_id}/jd").json()["jd_text"]


def test_edit_is_idempotent_across_retries(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    first = client.post(f"/api/jobs/{job_id}/jd", json={"text": "改一版"}).json()
    second = client.post(f"/api/jobs/{job_id}/jd", json={"text": "改一版"}).json()
    assert first == second


def test_edit_rejects_empty_text(tmp_path):
    """空正文不是一次编辑，是一次误操作。⛔ 不许把一份 JD 清成只剩标识。"""
    client, job_id, _ = _confirmed_job(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/jd", json={"text": "   "})
    assert resp.status_code == 422


def test_mark_human_written_drops_the_label_and_records_the_reviewer(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    body = client.post(f"/api/jobs/{job_id}/jd/human-written").json()
    assert AI_LABEL_PREFIX not in body["jd_text"]
    assert body["human_written"] is True
    assert body["authorship"]["marked_by"]  # 鉴权空壳返回 UNKNOWN_REVIEWER，非空
    assert body["authorship"]["at"]


def test_mark_human_written_is_idempotent(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    first = client.post(f"/api/jobs/{job_id}/jd/human-written").json()
    second = client.post(f"/api/jobs/{job_id}/jd/human-written").json()
    assert first == second


def test_jd_endpoints_404_on_unknown_job(tmp_path):
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    assert client.get("/api/jobs/nope/jd").status_code == 404
    assert client.post("/api/jobs/nope/jd", json={"text": "x"}).status_code == 404
    assert client.post("/api/jobs/nope/jd/human-written").status_code == 404


def test_jd_endpoints_409_before_the_profile_is_confirmed(tmp_path):
    """还没确认画像就来编辑 JD：说清楚"先确认画像"，⛔ 不要 500。"""
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    job_id = client.post("/api/jobs", json={"message": "要个做 ECU 底层的"}).json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}/jd").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/jd", json={"text": "x"}).status_code == 409
    assert client.post(f"/api/jobs/{job_id}/jd/human-written").status_code == 409


def test_jd_endpoints_reject_an_abandoned_job(tmp_path):
    client, job_id, _ = _confirmed_job(tmp_path)
    client.post(f"/api/jobs/{job_id}/abandon", json={"reason": ""})
    assert client.post(f"/api/jobs/{job_id}/jd", json={"text": "x"}).status_code == 409
    assert client.post(f"/api/jobs/{job_id}/jd/human-written").status_code == 409


def test_jd_endpoints_work_under_a_mount_prefix(tmp_path):
    """部署约束 1：挂到 /hr/recruit-agent 下必须照常工作。"""
    client, job_id, _ = _confirmed_job(tmp_path, root_path="/hr/recruit-agent")
    resp = client.get(f"/hr/recruit-agent/api/jobs/{job_id}/jd")
    assert resp.status_code == 200
    assert resp.json()["jd_text"]
```

- [ ] **Step 2: 跑测试确认它红**

Run: `venv/bin/python -m pytest tests/test_jd_endpoints.py -q`
Expected: FAIL —— 第一条就挂在 `KeyError` / `assert set(body) >= {...}`，随后的路由用例全部 404（`/api/jobs/{id}/jd` 还不存在）

- [ ] **Step 3: 写实现**

3a. 在 `app/web/server.py` 的 import 段补三处（保持既有分组与字母序）：

```python
from app.agents.intake_agent import derive_unspecified_fields
from app.agents.intake_question import normalize_question_payload
from app.agents.jd_grounding import verify_jd_grounding
from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.jd_nodes import (
    JDNotGeneratedError,
    effect_mark_jd_human_written,
    effect_update_jd_text,
    jd_edit_business_key,
)
from app.graph.nodes import (
```

3b. 在 `class AbandonRequest` 之后加请求模型：

```python
class JDEditRequest(BaseModel):
    # HR 编辑后的**完整**文案正文。服务端会剥掉其中任何 AI 标识行再重贴一行
    # 唯一的标识——⛔ 前端不要自己拼标识，也不要指望这里原样保存。
    text: str
```

3c. 在 `create_app()` 内部、`_reject_if_abandoned()` 定义之后，加两个 helper：

```python
    def _latest_version_or_404(job_id: str) -> int:
        row = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise HTTPException(status_code=404, detail="job not found")
        return int(row[0])

    def _jd_payload(job_id: str, version: int) -> dict:
        """JD 的统一回执。confirm / GET / 编辑 / 标记四个出口共用同一个形状。

        **溯源清单在这里现算，⛔ 不落库。** verify_jd_grounding 是确定性纯函数，
        两个入参（文案与画像字段）都在同一行 profile_json 里，重算与读缓存必然
        同值；而落库要改 app/graph/nodes.py 的既有节点 effect_generate_and_persist_jd，
        那超出本交付单元的边界。附带的好处是 JD 被编辑之后清单自动跟着变——
        缓存反而会在这里过期。

        ⛔ 清单只是观测（design.md 决策 12），本函数与它的调用方都不得据此拦截、
        重生成或降级。
        """
        row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
            (job_id, version),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        persisted = json.loads(row[0])

        jd_text = persisted.get("_jd_text")
        if jd_text is None:
            raise HTTPException(
                status_code=409, detail="这个岗位还没有生成 JD，请先确认画像"
            )

        authorship = persisted.get("_jd_authorship")
        return {
            "job_id": job_id,
            "version": version,
            "jd_text": jd_text,
            "needs_manual": persisted.get("_jd_needs_manual", False),
            "human_written": bool(authorship),
            "authorship": authorship,
            "ungrounded_terms": verify_jd_grounding(jd_text, persisted),
        }
```

3d. 把 `confirm()` 结尾那段（从 `# 不能直接用 effect_generate_and_persist_jd() 的返回值` 注释开始，到 `return {...}` 结束）整段替换为：

```python
        # 不能直接用 effect_generate_and_persist_jd() 的返回值：重放命中
        # effect_log 时 idempotent_effect 会短路返回 None（没有真的执行函数体）。
        # 无论是本次真跑了还是被短路了，profile_json 里此刻都已经是最终状态，
        # 统一从 _jd_payload() 读回去构造响应，两条路径读到的是同一份持久化结果。
        # 回执形状与 GET/编辑/标记三个端点完全一致，前端只写一套渲染逻辑。
        return _jd_payload(job_id, version)
```

3e. 在 `abandon()` 之后、`get_job()` 之前，加三个路由：

```python
    @router.get("/api/jobs/{job_id}/jd")
    def get_jd(job_id: str):
        return _jd_payload(job_id, _latest_version_or_404(job_id))

    @router.post("/api/jobs/{job_id}/jd")
    def edit_jd(job_id: str, req: JDEditRequest):
        """常规编辑（tasks 7.5）。

        ⛔ 这条路径**去不掉** AI 标识：effect_update_jd_text 无条件重贴。
        要去标识只有 POST /jd/human-written 一条路，且必须留痕。
        """
        _reject_if_abandoned(job_id)
        version = _latest_version_or_404(job_id)

        text = req.text.strip()
        if not text:
            # 空正文不是一次编辑，是一次误操作（多半是前端把 textarea 清空了）。
            # 放过去会得到一份只剩 AI 标识、没有正文的 JD，而 HR 看到的是"保存成功"。
            raise HTTPException(status_code=422, detail="文案正文不能为空")

        try:
            effect_update_jd_text(
                conn,
                thread_id=job_id,
                business_key=jd_edit_business_key(version, text),
                version=version,
                edited_text=text,
            )
        except JDNotGeneratedError as exc:
            raise HTTPException(
                status_code=409, detail="这个岗位还没有生成 JD，请先确认画像"
            ) from exc
        return _jd_payload(job_id, version)

    @router.post("/api/jobs/{job_id}/jd/human-written")
    def mark_jd_human_written(job_id: str, request: Request):
        """「标记为人工撰写」（tasks 7.5）——**唯一**能去掉 AI 标识的路径，且留痕。

        ⛔ 这里刻意**没有**终态守卫：重复 POST 应当幂等地返回 200（双击、
        客户端超时重发都会打到这里），由 effect_mark_jd_human_written 的幂等键
        短路，留痕里保留第一个按下按钮的人。理由同 abandon()。
        """
        _reject_if_abandoned(job_id)
        version = _latest_version_or_404(job_id)
        try:
            effect_mark_jd_human_written(
                conn,
                thread_id=job_id,
                business_key=str(version),
                version=version,
                # 决策人由鉴权层给（部署约束 3 的空壳接入点）。SSO 落地后这里
                # 一行不改，reviewer_of 自动返回真实的企微 userid。
                reviewer=reviewer_of(request),
                marked_at=sqlite_utc_now(),
            )
        except JDNotGeneratedError as exc:
            raise HTTPException(
                status_code=409, detail="这个岗位还没有生成 JD，请先确认画像"
            ) from exc
        return _jd_payload(job_id, version)
```

- [ ] **Step 4: 跑测试确认它绿**

Run: `venv/bin/python -m pytest tests/test_jd_endpoints.py -q`
Expected: PASS，13 passed

- [ ] **Step 5: 跑既有的 Web 与确认分支测试，确认 confirm 回执改形状没打破谁**

Run: `venv/bin/python -m pytest tests/test_web_api.py tests/test_approval_branches.py tests/test_suspend_recovery.py -q`
Expected: PASS，55 passed, 0 failed

✅ **这一步在出计划时已经端到端跑过**（把 3a–3e 五处改动打进一份 server.py 副本，用这三个测试文件跑，55 条全绿）：既有用例读 `confirm` 回执都用的是按键取值，没有人断言"回执恰好等于这三个键"，所以回执增量扩充不打破任何一条。**⛔ 如果这里真的红了，不要为了迁就旧断言把新键去掉**——把该断言改成包含关系 `set(body) >= {...}`，并在提交信息里写明改了哪一条。

- [ ] **Step 6: 提交**

```bash
git add app/web/server.py tests/test_jd_endpoints.py
git commit -m "feat(web): JD 读取/编辑/标记人工撰写三端点与统一回执（tasks 7.3/7.5）"
```

---

### Task 5: 前端 —— 一键复制导出、JD 编辑框、标记人工撰写（tasks 7.7 + 7.5 前端）

**Files:**
- Modify: `app/web/static/index.html`
- Test: `tests/test_static_frontend.py`（追加，⛔ 不改既有用例）

**Interfaces:**
- Consumes: Task 4 的统一回执 `{job_id, version, jd_text, needs_manual, human_written, authorship, ungrounded_terms}`，以及三个端点 `api/jobs/{id}/jd`（GET/POST）与 `api/jobs/{id}/jd/human-written`（POST）
- Produces: 前端元素 id —— `#jd-panel` `#jd-output` `#jd-copy-btn` `#jd-copy-hint` `#jd-edit-btn` `#jd-editor` `#jd-editor-input` `#jd-save-btn` `#jd-cancel-btn` `#jd-human-btn` `#jd-grounding`；JS 函数 `renderJd(data)` `copyJdText()`

- [ ] **Step 1: 写失败测试**

在 `tests/test_static_frontend.py` **末尾追加**：

```python
# ── JD 面板：复制导出与标识保护（tasks 7.7 / 7.5）──────────────────────
#
# 弱断言（本仓库没有 JS 测试运行器，单文件前端无构建）：只保证这几条不可退回的
# 约束没被改掉。真正的验证是 Step 5 的手工跑通。


def test_copy_uses_native_apis_only_and_has_a_plain_http_fallback():
    """部署约束 4 / 7.7：⛔ 不引第三方剪贴板库。

    execCommand 兜底不是装饰：demo 走的是明文 http://…:8095，
    **navigator.clipboard 在非安全上下文里根本不存在**（不是抛异常，是 undefined），
    只写现代 API 的话复制按钮在服务器上会一声不响地什么都不做。
    """
    assert "navigator.clipboard" in INDEX_HTML
    assert "isSecureContext" in INDEX_HTML
    assert 'document.execCommand("copy")' in INDEX_HTML
    assert "<script src" not in INDEX_HTML  # 单文件原生 JS，无外链脚本


@pytest.mark.compliance
def test_frontend_never_constructs_or_strips_the_ai_label():
    """合规红线：AI 标识由服务端贴、由服务端去。

    前端一旦自己拼或自己剥，标识就有了一条不经过 effect_mark_jd_human_written、
    因而**不留痕**的去除路径——那正是《AI 生成合成内容标识办法》要禁止的。
    判据是标识前缀这个字符串在 index.html 里一次都不出现。
    """
    assert "【AI 生成】" not in INDEX_HTML


def test_copy_copies_the_persisted_text_including_the_label():
    """复制的内容必须是服务端回执里的 jd_text 原文（含标识），
    ⛔ 不许从 DOM 里另取一份可能被样式或截断改过的文本。"""
    assert "currentJdText" in INDEX_HTML


def test_jd_panel_wires_all_three_endpoints_with_relative_paths():
    assert "api/jobs/${jobId}/jd`" in INDEX_HTML
    assert "api/jobs/${jobId}/jd/human-written`" in INDEX_HTML


def test_marking_human_written_asks_for_confirmation_first():
    """去掉 AI 标识是一次不可撤销的合规声明，⛔ 不做成"点一下就去掉"。"""
    body = INDEX_HTML.split("jd-human-btn")[-1]
    assert "window.confirm" in body


def test_ungrounded_terms_are_shown_as_advice_not_a_blocker():
    """决策 12：只观测不拦截。⛔ 前端不得因为有未溯源术语就禁用复制或保存。"""
    assert "jd-grounding" in INDEX_HTML
    assert "ungrounded_terms" in INDEX_HTML
```

⚠️ 该文件顶部当前只 `import re` 与 `from pathlib import Path`，新用例用到了 `pytest.mark.compliance`，实现时在文件顶部补一行 `import pytest`（放在 `import re` 之后）。

- [ ] **Step 2: 跑测试确认它红**

Run: `venv/bin/python -m pytest tests/test_static_frontend.py -q`
Expected: FAIL —— 至少 5 条新用例红（`navigator.clipboard`、`currentJdText`、`jd-grounding` 等字符串在 index.html 里都还不存在）

- [ ] **Step 3: 写实现**

3a. 在 `<style>` 段里 `#jd-output { … }` 那一行**之后**插入：

```css
  #jd-panel { display: none; margin-top: 16px; }
  #jd-panel #jd-output { display: block; margin-top: 0; }
  .jd-actions { margin-top: 8px; }
  .jd-actions button { margin: 0 8px 0 0; }
  .jd-hint { font-size: 13px; color: #6c757d; margin-left: 4px; }
  #jd-editor { display: none; margin-top: 8px; }
  /* 未溯源提示是**提醒**不是拦截（design 决策 12）：用中性的黄，⛔ 不用
     gap-warning 那套红色——红色会让人以为文案被系统拦下了。 */
  .jd-grounding { background: #fff3cd; border: 1px solid #ffe69c; color: #664d03; padding: 10px 14px; border-radius: 8px; margin-top: 10px; font-size: 14px; }
  /* 人工撰写徽标：AI 标识没了之后，界面上必须还看得出这份文案的作者是谁。 */
  .jd-authorship { font-size: 13px; color: #0f5132; background: #d1e7dd; border: 1px solid #badbcc; border-radius: 6px; padding: 6px 10px; margin-top: 10px; }
```

3b. 把 `<div id="jd-output"></div>` 那一行整体替换为：

```html
  <div id="jd-panel">
    <div id="jd-output"></div>
    <div id="jd-grounding" class="jd-grounding" style="display:none;"></div>
    <div id="jd-authorship" class="jd-authorship" style="display:none;"></div>
    <div class="jd-actions">
      <button id="jd-copy-btn">复制全文</button>
      <button id="jd-edit-btn">编辑文案</button>
      <button id="jd-human-btn">标记为人工撰写</button>
      <span id="jd-copy-hint" class="jd-hint"></span>
    </div>
    <div id="jd-editor">
      <textarea id="jd-editor-input" rows="12"></textarea>
      <button id="jd-save-btn">保存文案</button>
      <button id="jd-cancel-btn">取消</button>
    </div>
  </div>
```

3c. 在 `<script>` 段里 `let activeQuestions = null;` 之后加一个模块级变量：

```js
    // 服务端回执里的 jd_text 原文（含 AI 标识）。复制与编辑都从这里取，
    // ⛔ 不从 DOM 的 textContent 反取——那是渲染结果，可能被样式或将来的
    // 截断逻辑改过，而复制出去的东西必须与落库的一字不差（合规红线：
    // AI 生成的 JD 须带标识）。
    let currentJdText = "";
```

3d. 在 `doConfirm()` **之前**插入渲染与三个动作的实现：

```js
    // ── JD 面板（tasks 7.3 / 7.5 / 7.7）────────────────────────────────
    //
    // ⛔ 这一段里**没有**任何拼接或剥离 AI 标识的代码，而且不许有：标识由服务端
    // 贴、由服务端在「标记为人工撰写」时去掉并留痕。前端自己动手就等于开了一条
    // 不留痕的去标识路径（合规红线：《AI 生成合成内容标识办法》）。

    function renderJd(data) {
      currentJdText = data.jd_text || "";
      document.getElementById("jd-panel").style.display = "block";
      document.getElementById("jd-output").textContent = currentJdText;
      document.getElementById("jd-copy-hint").textContent = "";

      // 未溯源术语只提示，⛔ 不禁用任何按钮、不挡任何操作（design 决策 12：
      // 本批只观测不拦截）。这是给 HR 的一句"这几个词画像里没有，核对一下"。
      const grounding = document.getElementById("jd-grounding");
      const terms = data.ungrounded_terms || [];
      if (terms.length) {
        grounding.textContent =
          "以下技术要求在岗位画像里找不到出处，发布前请核对：" + terms.join("、");
        grounding.style.display = "block";
      } else {
        grounding.style.display = "none";
      }

      // 标识去掉之后，界面上仍然要看得出这份文案是谁声明为人工撰写的。
      const authorship = document.getElementById("jd-authorship");
      const humanBtn = document.getElementById("jd-human-btn");
      if (data.human_written && data.authorship) {
        authorship.textContent =
          "已标记为人工撰写：" + data.authorship.marked_by + "，" + data.authorship.at;
        authorship.style.display = "block";
        humanBtn.style.display = "none";
      } else {
        authorship.style.display = "none";
        humanBtn.style.display = "inline-block";
      }
    }

    async function copyJdText() {
      const hint = document.getElementById("jd-copy-hint");
      const text = currentJdText;
      if (!text) {
        hint.textContent = "还没有可复制的文案。";
        return;
      }

      let ok = false;
      // 现代 API 只在安全上下文（https 或 localhost）里存在。
      if (window.isSecureContext && navigator.clipboard) {
        try {
          await navigator.clipboard.writeText(text);
          ok = true;
        } catch (err) {
          ok = false;
        }
      }
      if (!ok) {
        // ⚠️ 这条兜底不是装饰：demo 挂在明文 http://…:8095 上，那里
        // navigator.clipboard **根本不存在**（undefined，不抛异常），
        // 只写上面那半段的话，复制按钮在服务器上会一声不响地什么都不做。
        const scratch = document.createElement("textarea");
        scratch.value = text;
        scratch.setAttribute("readonly", "");
        scratch.style.position = "fixed";
        scratch.style.top = "-1000px";
        document.body.appendChild(scratch);
        scratch.select();
        try {
          ok = document.execCommand("copy");
        } catch (err) {
          ok = false;
        }
        document.body.removeChild(scratch);
      }
      hint.textContent = ok
        ? "已复制纯文本全文（含 AI 生成标识）"
        : "复制失败，请手动选中下方文案复制";
    }

    async function submitJd(url, init) {
      const resp = await fetch(url, init);
      const data = await resp.json();
      if (!resp.ok) {
        const detail = data && data.detail;
        appendTurn(
          "assistant",
          "⚠️ " + (typeof detail === "string" ? detail : "操作失败，请稍后重试。")
        );
        return null;
      }
      renderJd(data);
      return data;
    }

    document.getElementById("jd-copy-btn").addEventListener("click", copyJdText);

    document.getElementById("jd-edit-btn").addEventListener("click", () => {
      document.getElementById("jd-editor-input").value = currentJdText;
      document.getElementById("jd-editor").style.display = "block";
      document.getElementById("jd-editor-input").focus();
    });

    document.getElementById("jd-cancel-btn").addEventListener("click", () => {
      document.getElementById("jd-editor").style.display = "none";
    });

    document.getElementById("jd-save-btn").addEventListener("click", async () => {
      const text = document.getElementById("jd-editor-input").value.trim();
      if (!text) return;
      const data = await submitJd(`api/jobs/${jobId}/jd`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text }),
      });
      if (data) {
        document.getElementById("jd-editor").style.display = "none";
      }
    });

    document.getElementById("jd-human-btn").addEventListener("click", async () => {
      // 去掉 AI 生成标识是一次不可撤销的合规声明，二次确认不是多余的一步。
      // ⛔ 不做成"点一下就去掉"。
      if (
        !window.confirm(
          "标记为人工撰写会去掉文案里的 AI 生成标识，并记下是谁、什么时候标记的。" +
            "只有在你已经大幅改写过这份文案时才这样做。确定吗？"
        )
      ) {
        return;
      }
      await submitJd(`api/jobs/${jobId}/jd/human-written`, { method: "POST" });
    });
```

3e. 把 `doConfirm()` 里从 `document.getElementById("gap-warning").style.display = "none";` 到函数结尾的那几行替换为：

```js
      document.getElementById("gap-warning").style.display = "none";
      renderJd(data);
      if (data.needs_manual) {
        appendTurn("assistant", "⚠️ JD 多次触发歧视性表述检测，已转人工处理，请核对下方内容。");
      }
```

3f. 在 `hideApprovalUi()` 函数体里补一行，让放弃/重新追问时把 JD 面板也收起来（它当前只收确认区）：

```js
      document.getElementById("jd-panel").style.display = "none";
```

- [ ] **Step 4: 跑测试确认它绿**

Run: `venv/bin/python -m pytest tests/test_static_frontend.py -q`
Expected: PASS，0 failed（含既有的相对路径与结构化问题渲染用例）

- [ ] **Step 5: 手工跑通一遍真实界面**

```bash
venv/bin/python -m uvicorn app.web.server:create_app --factory --port 8095
```

⚠️ 若 `create_app` 需要参数而无法直接 `--factory` 起，改用仓库既有的启动入口（`app/main.py` 或 `05-发布运行手册.md` 里记的那条命令），**⛔ 不要为了跑起来改 server.py 的签名**。

浏览器打开 `http://127.0.0.1:8095/`，走一遍：提需求 → 回答追问 → 确认画像 → JD 出现。逐条核对：
1. 点「复制全文」→ 提示"已复制…"，粘到文本编辑器里，**末尾那行 `【AI 生成】…` 在**
2. 点「编辑文案」→ 把末尾标识行整行删掉 → 点「保存文案」→ **标识又回来了**
3. 点「标记为人工撰写」→ 二次确认 → 标识消失，下面出现绿色的"已标记为人工撰写：… ，…"
4. 再点一次「编辑文案」并保存 → 标识**没有**被贴回去
5. 刷新页面前先 `curl -s http://127.0.0.1:8095/api/jobs/<job_id>/jd` 核对 `human_written` 与 `authorship` 与界面一致

结果**逐条**记进 run-build 的执行记录（哪条通过、哪条有偏差）。⛔ 不许只写一句"手工验证通过"。

- [ ] **Step 6: 跑全量测试**

Run: `venv/bin/python -m pytest -q`
Expected: PASS，0 failed

Run: `venv/bin/python -m pytest -m compliance -q`
Expected: PASS，0 failed（合规断言单独再跑一遍，让"红线被破坏"是一个可归因的失败步骤）

- [ ] **Step 7: 提交**

```bash
git add app/web/static/index.html tests/test_static_frontend.py
git commit -m "feat(web): JD 一键复制导出、编辑框与标记人工撰写入口（tasks 7.7/7.5）"
```

---

## Spec 覆盖对照

| spec / tasks 条目 | 覆盖它的 Task |
|---|---|
| `job-description` spec「JD 生成」→ Scenario「生成 JD」的 AND：**文案内容 MUST 可追溯到画像中的具体字段，不得出现画像中不存在的技术要求** | Task 1（纯函数）+ Task 4（回执里报出）+ Task 5（界面提示） |
| `job-description` spec「AI 生成内容标识」→ Scenario「标识不可被移除」第一条：**HR 在系统内编辑文案时标识不可通过常规编辑操作删除** | Task 2（`enforce_ai_label`）+ Task 3（`effect_update_jd_text`）+ Task 4（`POST /jd`）+ Task 5（编辑框） |
| 同上 Scenario 第二条：**系统提供"标记为人工撰写"的显式操作，该操作被记录** | Task 3（`effect_mark_jd_human_written`，留痕含 marked_by / at）+ Task 4（`POST /jd/human-written`）+ Task 5（按钮与二次确认） |
| `job-description` spec「文案导出」→ Scenario「复制文案」：**可一键复制纯文本格式的完整文案** | Task 5（`copyJdText`，含非安全上下文兜底） |
| tasks 7.3 | Task 1 / 4 / 5 |
| tasks 7.5 | Task 2 / 3 / 4 / 5 |
| tasks 7.7 | Task 5 |

**本单元刻意不覆盖**（已在 tasks.md 各条注明或已移出，⛔ 不要顺手做）：
- 7.1 / 7.2 / 7.4 / 7.6 —— 已实现并已勾选
- `job-description` spec「生成留痕」Scenario（模型标识、模型版本、prompt 版本、原始响应落库）—— 已移出到变更包 `ai-audit-trail-and-outbound-gate`，由 `analysis_run` 承载，⛔ 本单元不另起一套
- 未溯源率的**拦截阈值**—— 按 `m1-intake-quality-fixes/design.md` 决策 12，等 ≥ 20 场真实会话的分布出来再单独开变更

## 收尾时需要登记的技术债

run-build 完成后，把下面两条登记进 `openspec/changes/m1-job-profile-intake/tasks.md` 或技术债清单（⛔ 不要在实现里顺手"修掉"它们）：

- **TD-JD-1｜JD 溯源用的是闭集术语词表**（`app/agents/jd_grounding.JD_TECHNICAL_TERMS`）。词表外的编造看不见，这个数字是**下界**不是精确值——与决策 11 声明的口径一致。触发扩表的条件：真实使用中出现词表没覆盖到的编造。
- **TD-JD-2｜「标记为人工撰写」的留痕落在 `profile_json._jd_authorship`，不在 `human_review` 表里**。理由是 SQLite 改不了已有表的 CHECK（Global Constraints 第 11 条）。后果：`app/audit/assertions.py` 的断言四查不到这类决策。要并进审计口径的话，是一个独立变更（改表 + 改三处同源字面量 + 改断言），⛔ 不在本单元里做。
