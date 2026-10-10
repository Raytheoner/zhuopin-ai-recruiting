"""
交付单元 4.4：`effect_*` 节点的幂等专项测试。

⭐ 本文件是**工程铁律 1 的全量守护**，不是"再写几个用例"。它要做到两件事：

1. 仓库里**每一个** `effect_*` 节点都有一条「业务写之后、事务提交之前强制中断
   → 进程重启 → 按 thread_id 恢复重跑」的用例，断言副作用恰好发生一次。
2. 将来**新增一个 effect 节点却忘了加用例时，这个文件必须变红**。清单靠 AST
   从源码里现扫，与下面的硬编码清单双向比对——两边不一致就失败，谁都别想
   悄悄溜过去。铁律 1 的失败是静默的（不报错、不失败，只是少做/多做一次副
   作用），能抓住它的只有"清单过期即变红"这一条机制。

⛔ **本文件不改 `app/` 任何代码。** 若某个节点在这里被测出真的不幂等，
登记进计划的「红灯与观察项」，由另一个交付单元修——在测试单元里顺手改
被测代码，等于让测试给自己开绿灯。

⚠️ 与既有三个文件的分工（⛔ 不重复造）：
- `tests/test_idempotency.py`     —— 装饰器**本身**的语义（短路、回滚、日志）
- `tests/test_transaction_ownership.py` —— 事务**归属**（checkpointer 不得共用连接）
- `tests/test_graph_idempotency.py`     —— `effect_persist_draft` /
  `effect_deliver_message` / `effect_confirm_profile` 三个节点的既有覆盖
本文件补的是**横向全量**：把同一条崩溃-恢复协议施加到全部 10 个节点上，
并让清单无法过期。既有用例一条都不删、一条都不改。
"""

import ast
import hashlib
import json
import pathlib
import sqlite3
from dataclasses import dataclass
from typing import Callable

import pytest

from app.agents.jd_agent import AI_LABEL_TEMPLATE, enforce_ai_label
from app.agents.letter_drafter import LetterDraft
from app.audit.events import OUTBOUND_BLOCKED, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.channels.base import OutboundMessage
from app.channels.web_channel import WebChannel
from app.graph.jd_nodes import (
    JD_AUTHORSHIP_KEY,
    JD_TEXT_KEY,
    effect_mark_jd_human_written,
    effect_update_jd_text,
    jd_edit_business_key,
)
from app.graph.letter_nodes import (
    effect_edit_letter,
    effect_mark_letter_human_written,
    effect_persist_letter,
    letter_edit_business_key,
)
from app.graph.manual_handoff import (
    REASON_PROVIDER_UNAVAILABLE,
    effect_deliver_manual_handoff,
    effect_mark_needs_manual,
)
from app.graph.onboarding_nodes import effect_instantiate_checklist, effect_update_item
from app.graph.scheduling_nodes import (
    effect_cancel_slot,
    effect_complete_slot,
    effect_reschedule_slot,
    effect_schedule_slot,
)
from app.graph.nodes import (
    effect_abandon_profile,
    effect_confirm_profile,
    effect_deliver_message,
    effect_enqueue_pending_approval,
    effect_generate_and_persist_jd,
    effect_persist_draft,
    effect_record_outbound_audit,
    effect_request_revision,
)
from app.graph.interview_prep_nodes import (
    effect_delete_prep_question,
    effect_edit_prep_question,
    effect_freeze_prep,
    effect_persist_prep_draft,
    effect_regenerate_prep_question,
)
from app.agents.invitation_drafter import InvitationDraft
from app.graph.invitation_nodes import effect_persist_invitation_draft
from app.agents.interview_prep import PrepDraft, PrepQuestionDraft
from app.graph.interview_scoring_nodes import (
    AlignedTurn,
    CorrectedCriterionScore,
    TalkingPoint,
    effect_mark_scoring_failed,
    effect_persist_scorecard,
    effect_write_acoustic_refs,
)
from app.graph.invite_nodes import (
    effect_consume_resume_token,
    effect_create_interview_session,
    effect_deliver_invitation,
    effect_display_verification_code_to_hr,
    effect_issue_invite,
    effect_issue_resume_token,
    effect_issue_verification_code,
    effect_log_invite_access_denied,
    effect_open_invite,
    effect_record_consent,
    effect_send_verification_code,
    effect_verify_phone,
)
from app.graph.live_session_nodes import (
    effect_close_session,
    effect_fetch_recording,
    effect_open_session,
    effect_persist_turn,
)
from app.graph.resume_nodes import effect_persist_parse
from app.graph.screening_nodes import compute_screen, effect_persist_flags
from app.intake.bundle import FileEntry
from app.intake.ingest_bundle import effect_ingest_bundle
from app.intake.merge import (
    effect_attach_resume_to_candidate,
    effect_merge_candidates,
    effect_unmerge_candidates,
)
from app.outbound.messages import CandidateOutboundMessage
from app.schemas.job_profile import JobProfile
from app.schemas.live_turn_event import LiveTurnEvent
from app.schemas.resume_fields import (
    EducationField,
    ListField,
    NumberField,
    ResumeFields,
    TextField,
)
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion
from app.storage.db import get_connection, init_schema

# 仓库根 = tests/ 的上一级
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP_ROOT = _REPO_ROOT / "app"

# ⚠️ 硬编码清单。加了新的 effect 节点就往这里加一行，**同时**去
# build_recipes() 里加一条配方——两处都加了，测试才会绿。
# ⛔ 不要为了让测试变绿而删这里的名字：删掉等于宣布"这个节点不需要幂等
# 保护"，那是铁律 1 的例外，只有 Shao Peishen 能拍。
EFFECT_NODE_MANIFEST = frozenset(
    {
        "effect_persist_draft",
        "effect_deliver_message",
        "effect_confirm_profile",
        "effect_request_revision",
        "effect_abandon_profile",
        "effect_generate_and_persist_jd",
        "effect_enqueue_pending_approval",
        "effect_record_outbound_audit",
        "effect_update_jd_text",
        "effect_mark_jd_human_written",
        "effect_persist_letter",
        "effect_edit_letter",
        "effect_mark_letter_human_written",
        "effect_mark_needs_manual",
        "effect_deliver_manual_handoff",
        "effect_persist_parse",
        "effect_ingest_bundle",
        "effect_attach_resume_to_candidate",
        "effect_merge_candidates",
        "effect_unmerge_candidates",
        "effect_persist_flags",
        "effect_persist_prep_draft",
        "effect_freeze_prep",
        "effect_edit_prep_question",
        "effect_delete_prep_question",
        "effect_regenerate_prep_question",
        "effect_create_interview_session",
        "effect_issue_invite",
        "effect_log_invite_access_denied",
        "effect_open_invite",
        "effect_issue_resume_token",
        "effect_consume_resume_token",
        "effect_deliver_invitation",
        "effect_record_consent",
        "effect_issue_verification_code",
        "effect_verify_phone",
        "effect_display_verification_code_to_hr",
        "effect_open_session",
        "effect_persist_turn",
        "effect_close_session",
        "effect_fetch_recording",
        "effect_write_acoustic_refs",
        "effect_persist_scorecard",
        "effect_mark_scoring_failed",
        "effect_send_verification_code",
        "effect_instantiate_checklist",
        "effect_update_item",
        "effect_schedule_slot",
        "effect_reschedule_slot",
        "effect_cancel_slot",
        "effect_complete_slot",
        "effect_persist_invitation_draft",
    }
)

# ⚠️ 0919T 追加：`effect_send_verification_code`（app/graph/invite_nodes.py，
# OQ-10 未决前的占位桩）不管调用方传什么参数都立刻 raise NotImplementedError
# ——函数体在 idempotent_effect 装饰器的 `fn(conn, ...)` 调用点就抛出，
# 永远走不到 "INSERT INTO effect_log → commit()" 这一步。下面两条通用崩溃-
# 恢复协议（`test_forced_interrupt_then_recovery_applies_the_effect_exactly_once`
# / `test_effect_log_count_equals_business_rows_per_thread`）默认每个节点调用
# 一次都能真正生效（写一次 effect_log + 一次业务事实），这个前提对一个永远
# 抛异常的占位桩不成立——不是"发现它不幂等"（铁律 1 谈的是已生效的副作用是否
# 被重复应用，这个函数从未生效过），是两类节点本就不在同一个可比较的维度上。
# 该桩改成真实实现（OQ-10 落地）的那天，必须把它从这个集合里摘掉、改走通用
# 协议——`test_manifest_matches_the_source_tree` 与
# `test_every_effect_node_has_a_recovery_recipe` 仍然要求它在
# EFFECT_NODE_MANIFEST 与 build_recipes() 里各有一条，只是不喂给这两条通用
# 协议，改由 `test_effect_send_verification_code_stub_always_raises_and_writes_nothing`
# 单独覆盖："装饰器包着一个永远失败的桩，异常原样透传、不留任何痕迹"这件事本身。
_PERMANENTLY_UNIMPLEMENTED_STUBS = frozenset({"effect_send_verification_code"})


def collect_effect_node_sites() -> dict[str, list[str]]:
    """AST 扫 `app/` 下全部 .py，返回 {节点名: ["相对路径:行号", ...]}——**保留同一个
    字面量的全部出现位置**，不做去重合并。

    ⭐ **用 AST 而不是 grep**：`app/audit/assertions.py` 的注释里、
    `app/outbound/delivery.py` 的 docstring 里都出现过 `@idempotent_effect`
    这串字面量（后者原文是"本函数**不是** effect_* 节点、⛔ 不加
    @idempotent_effect"）。grep 会把这两处当成节点，清单守卫就会为了一条
    注释而误报，几次之后没人再信它——一个总在误报的守卫等于没有守卫。
    AST 只看真正的 decorator 节点，从结构上没有这个问题。

    节点名取**装饰器的字面量参数**而非函数名：幂等键里存进 effect_log 的是
    这个字面量（见 app/storage/idempotency.py），它才是数据库里的事实。

    ⚠️ **上面这句"两者一致"曾经写的是"由 `app/audit/assertions.py` 另行保证，
    本文件不重复断言"——那是假的，已订正。** `app/audit/assertions.py` 的
    `TERMINAL_STATUS_EFFECT_NODES` 只交叉核对了 10 个节点里的**两个**
    （`effect_confirm_profile`、`effect_abandon_profile`，服务于终态留痕断言）；
    其余八个节点的函数名与装饰器字面量参数是否一致，**没有任何代码断言**——
    本文件也不断言。这是一个真实存在的空白，不是"另有人管"。

    ⚠️ **本守卫抓不到什么**（reviewer 已确认、明确排除在外，⛔ 不要以为清单
    绿了就代表这些也被盖住）：
    (a) 定义在 `app/` 之外的 effect 节点——本收集器只扫 `app/` 下的 `.py`；
    (b) 动态生成的节点名，例如 `make_effect("effect_x")` 这种字面量参数不是
    直接写在 `@idempotent_effect(...)` 里的写法——下面的字面量断言会把这类
    写法钉在红灯上（`len(deco.args) == 1 and isinstance(deco.args[0], ast.Constant)`
    失败），但钉住之后它依然进不了清单，只是不会被静默漏掉、会被看见。
    """
    sites: dict[str, list[str]] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                func = deco.func
                deco_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if deco_name != "idempotent_effect":
                    continue
                assert len(deco.args) == 1 and isinstance(deco.args[0], ast.Constant), (
                    f"{path}:{node.lineno} 的 @idempotent_effect 参数不是字面量字符串。"
                    "节点名必须是字面量——变量或表达式会让 effect_log 里的 node_name "
                    "无法从源码静态推断，这条清单守卫和 app/audit/assertions.py 的"
                    "断言就同时失效了。"
                )
                where = f"{path.relative_to(_REPO_ROOT)}:{node.lineno}"
                sites.setdefault(deco.args[0].value, []).append(where)
    return sites


def collect_effect_nodes() -> dict[str, str]:
    """`collect_effect_node_sites()` 的薄封装：每个节点名只取第一个出现位置，
    向后兼容既有调用方（`build_recipes()` 的键集合比对、
    `test_collector_reports_where_each_node_lives` 等）。

    ⚠️ 重名检测由 `test_no_duplicate_effect_node_name_literals` 独立断言，
    本函数不做去重告警——它存在的目的只是保留一个简单的 dict[str, str] 接口，
    不是"这里已经确认过没有重复"。
    """
    return {name: wheres[0] for name, wheres in collect_effect_node_sites().items()}


def collect_effect_named_functions() -> list["_EffectFunctionSite"]:
    """AST 扫 `app/` 下全部 .py，返回**所有**名字以 `effect_` 开头的函数定义
    （`def` 与 `async def` 都算），不问它有没有被装饰。

    ⭐ **I-1 的核心**：`collect_effect_node_sites()`／`collect_effect_nodes()`
    只看装饰器的字面量参数——一个作者忘了加 `@idempotent_effect` 的
    `effect_*` 节点，对它们完全不可见，清单守卫、崩溃-恢复用例全都不会拿到
    这个节点，测试保持全绿。而"作者已经忘了"正是工程铁律 1 存在的理由：
    没有幂等键、没有 `effect_log` 行，LangGraph 从节点开头整个重跑时会静默
    重复这个副作用。这个收集器反过来按**函数名**找，不管装饰器在不在，
    再由 `test_every_effect_named_function_is_decorated_with_idempotent_effect`
    断言"找到的每一个都被装饰了"。

    ⚠️ 装饰器识别方式与 `collect_effect_node_sites()` 相同——按源码拼写认
    `idempotent_effect`。对 `from app.storage.idempotency import
    idempotent_effect as _eff` 这种别名形式，本守卫与上面那个守卫一样仍然
    失明：这是承认但本轮不修的残留限制，不在"抓不到什么"清单里重复列——
    原因是它不属于 reviewer 明确圈定的两条残留（out-of-app / 动态节点名），
    而是两个收集器共有的同一个已知盲区。
    """
    sites: list[_EffectFunctionSite] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("effect_"):
                continue
            has_decorator = any(
                isinstance(deco, ast.Call)
                and (
                    (isinstance(deco.func, ast.Name) and deco.func.id == "idempotent_effect")
                    or (
                        isinstance(deco.func, ast.Attribute)
                        and deco.func.attr == "idempotent_effect"
                    )
                )
                for deco in node.decorator_list
            )
            sites.append(
                _EffectFunctionSite(
                    qualname=node.name,
                    where=f"{path.relative_to(_REPO_ROOT)}:{node.lineno}",
                    has_idempotent_effect_decorator=has_decorator,
                )
            )
    return sites


@dataclass(frozen=True)
class _EffectFunctionSite:
    """一处以 `effect_` 开头命名的函数定义（不问是否装饰）。"""

    qualname: str
    where: str
    has_idempotent_effect_decorator: bool


def test_manifest_matches_the_source_tree():
    """⭐ 防清单过期：源码里有、清单里没有 → 失败（新节点漏测）；反之亦然（节点已删）。"""
    discovered = set(collect_effect_nodes())
    missing_from_manifest = discovered - set(EFFECT_NODE_MANIFEST)
    stale_in_manifest = set(EFFECT_NODE_MANIFEST) - discovered
    assert not missing_from_manifest, (
        f"源码里新增了 effect 节点但没进本文件的清单：{sorted(missing_from_manifest)}。"
        "把它们加进 EFFECT_NODE_MANIFEST，并在 build_recipes() 里各加一条崩溃-恢复"
        "配方——铁律 1 要求每个 effect_* 节点都被强制中断验证过。"
    )
    assert not stale_in_manifest, (
        f"清单里的节点在源码里已经不存在了：{sorted(stale_in_manifest)}。"
        "确认是被删/改名而不是被漏扫，然后同步更新 EFFECT_NODE_MANIFEST。"
    )


def test_collector_reports_where_each_node_lives():
    """收集器要给出位置，否则清单变红时没人知道该去哪个文件加配方。"""
    located = collect_effect_nodes()
    assert located["effect_persist_draft"].startswith("app/graph/nodes.py:")
    assert located["effect_update_jd_text"].startswith("app/graph/jd_nodes.py:")


def test_collector_ignores_the_literal_in_comments_and_docstrings():
    """
    回归守卫：`app/outbound/delivery.py` 的 docstring 里有一句
    "⛔ 不加 @idempotent_effect"，`app/audit/assertions.py` 的注释里也有。
    用 grep 实现收集器会把它们当成节点，这条断言把那种实现钉死在红灯上。
    """
    located = collect_effect_nodes()
    for name, where in located.items():
        assert name.startswith("effect_"), f"{name} @ {where} 不像节点名，收集器可能扫到了注释"
    assert not any(where.startswith("app/outbound/delivery.py:") for where in located.values()), (
        "app/outbound/delivery.py 里没有任何 effect_* 节点，只有一句说明它"
        "**不是**节点的 docstring——收集器扫到它说明用错了实现（应为 AST，非文本匹配）"
    )


def test_every_effect_named_function_is_decorated_with_idempotent_effect():
    """⭐⭐ I-1：按函数名找 `effect_*` 定义，揪出"忘了加装饰器"这种情形——
    `collect_effect_nodes()` 只认装饰器字面量，对这类节点完全不可见。

    一个名字以 `effect_` 开头却没有 `@idempotent_effect` 的函数，没有幂等键、
    不落 `effect_log` 行；LangGraph 从节点开头整个重跑时，这个副作用会被
    静默重复执行——这正是工程铁律 1 要防的那种失败，也是"作者已经忘了"这个
    最常见的疏漏形态。
    """
    undecorated = [
        site for site in collect_effect_named_functions() if not site.has_idempotent_effect_decorator
    ]
    assert not undecorated, "\n".join(
        [
            "以下函数名以 effect_ 开头，但没有 @idempotent_effect 装饰器——"
            "没有幂等键，没有 effect_log 行，LangGraph 从节点开头整个重跑时会"
            "静默重复这个副作用（工程铁律 1）：",
            *(f"  - {site.qualname} @ {site.where}" for site in undecorated),
        ]
    )


def test_no_duplicate_effect_node_name_literals():
    """⭐⭐ I-2：同一个 `@idempotent_effect(...)` 字面量参数不能出现在两处。

    `collect_effect_nodes()` 的 `dict[str, str]` 是 last-writer-wins：两个函数
    共用同一个字面量时，先出现的那个会被后出现的静默顶掉，清单守卫对这种
    情形完全看不见——甚至可能出现"新节点用了一个已在清单里的旧名字"这种
    最危险的形态：清单保持绿灯，但一个从未被测过的节点已经悄悄上线。

    重复本身就是一个独立的铁律 1 危害：两个节点共享 `node_name` 时，只要
    `thread_id` + `business_key` 也相同，幂等键 `f"{thread_id}:{node_name}:
    {business_key}"` 就会撞车，后一个节点会被 `idempotent_effect` 短路、
    永远不执行——`docs/findings/2026-08-13-sqlite-事务归属冲突.md` 记录的
    那类静默丢失是同一族故障。
    """
    duplicated = {
        name: wheres for name, wheres in collect_effect_node_sites().items() if len(wheres) > 1
    }
    assert not duplicated, "\n".join(
        [
            "以下 @idempotent_effect 节点名字面量出现了不止一次——两个节点共享"
            "同一个 node_name，幂等键在 thread_id + business_key 相同时会撞车，"
            "后一个节点将被 idempotent_effect 短路、永远不执行：",
            *(f"  - {name!r}: {wheres}" for name, wheres in duplicated.items()),
        ]
    )


_JOB = "job-4-4"
_RESUME = "resume-4-4"
_CANDIDATE = "candidate-4-4"
_APPLICATION = "application-4-4"
# channel-resume-intake U2 Task 5：合并配方的两个候选人。主候选人 id 同时是
# thread_id（幂等键 = {primary_id}:effect_merge_candidates:{secondary_id}:{request_id}）。
_MERGE_PRIMARY = "candidate-4-4-merge-primary"
_MERGE_SECONDARY = "candidate-4-4-merge-secondary"
# channel-resume-intake U2 Task 6：撤销配方的合并留痕 id 同时是 thread_id
# （幂等键 = {merge_log_id}:effect_unmerge_candidates:undo）。
_MERGE_LOG = "merge-log-4-4"
_ONBOARDING_CHECKLIST = "checklist-4-4"
_ONBOARDING_ITEM = "item-4-4"
_PREP_RUN = "prep-run-4-4"
_SESSION = "session-4-4"
_TURN = "turn-4-4"
_SCORE_RUN = "score-run-4-4"
_TS = "2026-09-08T02:00:00+00:00"
_FAR_FUTURE = "2099-01-01T00:00:00+00:00"
_LABEL = AI_LABEL_TEMPLATE.format(generated_at=_TS)
_AI_BODY = f"【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {_TS}。很遗憾……"
_LETTER_ID = "letter-4-4"
_LETTER_RUN = "letter-run-4-4"
_LETTER_BODY_TEXT = "张三：\n\n拟录用您担任嵌入式软件工程师。"
# 种子里那份文书的正文 = 已经由 L3 生成过、带 AI 标识的一版（生成时间 _TS）。
# ⛔ 不手写标识串：标识的唯一真源是 app/agents/jd_agent.enforce_ai_label，
# 抄一份迟早与真源漂移，而 effect_edit_letter 的「回读原生成时间」会当场摔。
_LETTER_AI_BODY = enforce_ai_label(_LETTER_BODY_TEXT, generated_at=_TS)
_EDITED_LETTER_BODY = "张三：\n\n拟录用您担任嵌入式软件工程师（HR 手改版）。"

# interview-scheduling U3 Task 4：邀约草稿持久化配方的种子 id 与正文。
# 正文走 enforce_ai_label（唯一真源），与 L3 生成物同形——节点本身不校验标识
# （那是外发门禁的职责），但配方喂进来的东西要与真实链路一致才有意义。
_INV_SLOT = "inv-slot-4-4"
_INV_APPLICATION = "inv-application-4-4"
_INV_THREAD = _INV_APPLICATION
_INV_RUN = "invitation-run-4-4"
_INV_BODY = enforce_ai_label(
    "张三您好：\n\n邀请您参加嵌入式软件工程师岗位的一面。", generated_at=_TS
)


class _CrashBeforeDurableCommit(sqlite3.Connection):
    """
    在"紧跟 `INSERT INTO effect_log` 之后的那一次 `commit()`"上抛异常。

    ⭐ **这就是"真中断"的落点**，不是随便找个地方抛。`idempotent_effect`
    的执行序是：查 effect_log → 跑函数体（业务写）→ INSERT effect_log →
    commit()。在这一次、也是唯一一次 commit() 上抛，命中的正是
    "业务写已经在事务里、还没落盘"的那一瞬间。异常从 commit() 里抛出，
    落在装饰器 try/except **之外**（它只包着函数体），所以不会触发 rollback，
    事务保持打开——随后 conn.close() 丢弃它，等价于进程崩溃。

    ⛔ 不用"连着调两次函数"冒充中断：那走的是装饰器的短路分支，
    证明的是"命中 effect_log 会跳过"，完全没有触碰"半截写入会不会落盘"。
    第二次调用时数据库里已经有第一次的完整提交，测不出任何原子性问题。

    只崩一次（crashed_once），恢复重跑走的是同一个类的正常路径。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._armed = False
        self.crashed_once = False

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("INSERT INTO EFFECT_LOG"):
            self._armed = True
        return super().execute(sql, *args, **kwargs)

    def commit(self):
        if self._armed and not self.crashed_once:
            self._armed = False
            self.crashed_once = True
            raise RuntimeError("simulated crash exactly before durable commit")
        return super().commit()


def _open_crashing_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path, check_same_thread=False, factory=_CrashBeforeDurableCommit
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@dataclass(frozen=True)
class Recipe:
    """一个 effect 节点的崩溃-恢复配方。

    `count_business_rows` 数的是**这个节点自己的那条业务事实**，不是"某张表
    的全部行"。有些节点只做 UPDATE（`effect_confirm_profile` 改 status），
    行数不变，所以口径要选"处于目标状态的行数"而不是"新增行数"。
    `rows_per_effect` 是"一次生效应当留下几条业务事实"，正常是 1。
    """

    thread_id: str
    seed: Callable[[sqlite3.Connection], None]
    invoke: Callable[[sqlite3.Connection], None]
    count_business_rows: Callable[[sqlite3.Connection], int]
    rows_per_effect: int = 1
    note: str = ""


def _seed_nothing(conn: sqlite3.Connection) -> None:
    conn.commit()


def _seed_job(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'drafting')",
        (_JOB,),
    )
    conn.commit()


def _seed_resume(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '嵌入式工程师', 'drafting')",
        (_JOB,),
    )
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'hash1', 'alice')",
        (_RESUME, _JOB),
    )
    conn.commit()


def _seed_merge_pair(conn: sqlite3.Connection) -> None:
    """`effect_merge_candidates` 的种子：两个未合并候选人 + 被合并方名下一条投递。

    ⛔ 刻意不给**主**候选人在同一岗位建投递：那就成了 spec 的「同岗位双投递」，
    节点会要求 HR 指定保留哪份并抛 `MergeValidationError`——本配方走的是合并的
    常规路径，双投递分支由 `tests/test_merge_unmerge.py` 单独覆盖。
    """
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '嵌入式工程师', 'drafting')",
        (_JOB,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (_MERGE_PRIMARY,))
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '李四')", (_MERGE_SECONDARY,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'hash1', 'alice')",
        (_RESUME, _JOB),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (_APPLICATION, _MERGE_SECONDARY, _JOB, _RESUME),
    )
    conn.commit()


def _seed_merge_log_for_unmerge(conn: sqlite3.Connection) -> None:
    """`effect_unmerge_candidates` 的种子：一份**已完成**的合并留痕。

    ⛔ 刻意**不**调 `effect_merge_candidates` 造这行留痕：那会给 effect_log 写一行，
    而配方连接的 `_CrashBeforeDurableCommit` 正是在"INSERT INTO effect_log 之后的
    那一次 commit"上抛——种子的 commit 就成了崩溃点，`recipe.seed()` 自己先炸，
    协议还没开始就结束了。这里按合并**之后**的磁盘状态手插：
    application 挂在 primary 名下、secondary.merged_into 已指向 primary、
    candidate_merge_log 带一份包含该 application 的快照（撤销就是按它还原）。
    """
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '嵌入式工程师', 'drafting')",
        (_JOB,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (_MERGE_PRIMARY,))
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '李四')", (_MERGE_SECONDARY,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'hash1', 'alice')",
        (_RESUME, _JOB),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (_APPLICATION, _MERGE_PRIMARY, _JOB, _RESUME),
    )
    conn.execute(
        "UPDATE candidate SET merged_into = ? WHERE id = ?", (_MERGE_PRIMARY, _MERGE_SECONDARY)
    )
    conn.execute(
        "INSERT INTO candidate_merge_log "
        "(id, primary_id, secondary_id, reason, secondary_snapshot, merged_by) "
        "VALUES (?, ?, ?, '电话确认同一人', ?, 'alice')",
        (
            _MERGE_LOG,
            _MERGE_PRIMARY,
            _MERGE_SECONDARY,
            json.dumps(
                {
                    "secondary_id": _MERGE_SECONDARY,
                    "applications": [
                        {
                            "id": _APPLICATION,
                            "job_id": _JOB,
                            "resume_id": _RESUME,
                            "current_stage_id": "initial",
                            "status": "active",
                            "kanban_state": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()


def _seed_screening(conn: sqlite3.Connection) -> None:
    """`effect_persist_flags` 的种子：job/candidate/resume/application 四张表
    最小闭环 + 一条不会触发 `verdict='fail'` 的 blocking 规则（fail 需要
    非空 evidence_ref，pass 不需要，seed 越简单越好）。"""
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '嵌入式工程师', 'drafting')",
        (_JOB,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (_CANDIDATE,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "uploaded_by, status, parsed_json, parser_version) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', 'hash1', 'alice', 'parsed', ?, 'v1')",
        (_RESUME, _JOB, _resume_fields().model_dump_json()),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (_APPLICATION, _CANDIDATE, _JOB, _RESUME),
    )
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES (?, 1, 'experience_years', 'gte', '3', 1, '经验≥3年')",
        (_JOB,),
    )
    conn.commit()


def _resume_fields() -> ResumeFields:
    """与 tests/test_resume_nodes.py::_fields() 逐字同源的六字段构造——
    崩溃-恢复配方不关心字段内容本身，只借用一份已知合法的 ResumeFields。"""
    return ResumeFields(
        name=TextField(value="张三", confidence=0.9),
        years_of_experience=NumberField(value=5, confidence=0.9),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )


def _seed_job_with_profile_v1(conn: sqlite3.Connection, profile: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'drafting')",
        (_JOB,),
    )
    conn.execute(
        "INSERT INTO job_profile (job_id, version, status, profile_json) "
        "VALUES (?, 1, 'drafting', ?)",
        (_JOB, json.dumps(profile or {"job_title": "底层软件工程师"}, ensure_ascii=False)),
    )
    conn.commit()


def _human_review_count(conn: sqlite3.Connection, decision_type: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM human_review WHERE job_id = ? AND decision_type = ?",
        (_JOB, decision_type),
    ).fetchone()[0]


def _profile_v1(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = 1", (_JOB,)
    ).fetchone()
    return json.loads(row[0])


class _CountingGateway:
    """给 effect_generate_and_persist_jd 用的 LLM 替身。

    只实现 generate_jd() 真正调到的那个方法。**按类计数**（不是按实例），
    因为崩溃与重放各用一次全新的 invoke()，实例计数会各自归零，就看不见
    "同一份 JD 被生成了两次"这个事实——而那正是 Task 3 要如实记录的观察项。
    """

    calls = 0

    def extract_structured(self, *, system_prompt, user_prompt, schema, prompt_version):
        type(self).calls += 1
        return schema(body="岗位职责：负责 ECU 底层软件开发，参与量产项目交付。")


def _jd_profile() -> JobProfile:
    return JobProfile(
        job_title="底层软件工程师",
        department="研发部",
        headcount=1,
        education_requirement="本科及以上",
        experience_years="3-5年",
    )


_EDITED_JD = "岗位职责：负责 ECU 底层软件开发（HR 手改版）。"


class _FakeVoiceHostClient:
    """给 U4 live 子图三个需要 client 的 effect_* 节点用的替身，只实现
    `effect_open_session`/`effect_fetch_recording` 真正调到的两个方法——本
    文件不测 `run_live_session_sync` 本身（那是另一份既有覆盖
    tests/test_live_session_nodes.py 的范围），不需要 `poll_events`。"""

    def __init__(self) -> None:
        self.created_sessions: list[str] = []
        self.deleted_artifacts: list[str] = []

    def create_session(self, bundle: SessionBundle) -> None:
        self.created_sessions.append(bundle.session_id)

    def notify_delete_artifacts(self, session_id: str) -> None:
        self.deleted_artifacts.append(session_id)


_RECORDING_CONTENT = b"hello-recording-4-4"
_RECORDING_SHA256 = hashlib.sha256(_RECORDING_CONTENT).hexdigest()


def _bundle_ingest_stub(*, job_id, sample_class, uploaded_by, upload,
                        source=None, source_origin=None) -> dict:
    """`effect_ingest_bundle` 崩溃-恢复配方注入的单文件接收替身：不写任何库。

    真实调用方注入的是 `app/web/server.py` 的 `_ingest_one_resume`（M2 既有单
    文件接收，内部自管事务），它的业务表行数不属于本节点的原子边界。配方换成
    不落库的桩，恒等式里数的就只剩本节点自己那一行 `bundle_ingest_result`。
    """
    return {"file_name": upload.filename, "status": "accepted", "resume_id": "stub-4-4"}


def _seed_hired_application_for_checklist(conn: sqlite3.Connection) -> None:
    """`effect_instantiate_checklist` 的种子：一份 `hired` 投递 + 一条已接受的
    Offer。入职清单模板 ⛔ 不必手插——`init_schema` 的
    `_seed_onboarding_default_template` 已落好部门级 `default` 兜底模板（六条，
    `app/storage/db.py`），本节点的模板解析兜底正好走到它。"""
    conn.execute(
        "INSERT INTO job (id, title, department, status) "
        "VALUES (?, '嵌入式工程师', '研发部', 'approved')",
        (_JOB,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (_CANDIDATE,))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.docx', 'hash-onboard-4-4', 'alice')",
        (_RESUME, _JOB),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, ?, ?, ?, 'hired', 'hired')",
        (_APPLICATION, _CANDIDATE, _JOB, _RESUME),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, ?, '研发部', '2026-10-20', 'manager-1', 'accepted', 1, 'alice')",
        (f"offer-{_APPLICATION}", _APPLICATION, _JOB),
    )
    conn.commit()


def _seed_pending_hr_item_for_update(conn: sqlite3.Connection) -> None:
    """`effect_update_item` 的种子：一份 `hired` 投递（复用清单种子的
    job/candidate/resume/application/offer 五件套，让 `application → job`
    的部门联结真实可查）＋ 一份清单 ＋ 一条 `hr` 名下 `pending` 条目
    ＋ 一个 `role='hr'` 的操作人账号。

    清单与条目都按固定 id 手插（⛔ 不调 `effect_instantiate_checklist`）：
    本节点的 `thread_id` 就是 item_id，配方要求它在建配方时已知，而实例化
    节点生成的是 uuid。手插换来确定性的 thread_id——正是幂等协议要认的键。"""
    _seed_hired_application_for_checklist(conn)
    conn.execute(
        "INSERT INTO onboarding_checklist "
        "(id, application_id, template_version, start_date, created_by) "
        "VALUES (?, ?, 1, '2026-10-20', 'alice')",
        (_ONBOARDING_CHECKLIST, _APPLICATION),
    )
    conn.execute(
        "INSERT INTO onboarding_item "
        "(id, checklist_id, name, owner_party, due_offset_days, required, status) "
        "VALUES (?, ?, '劳动合同签署', 'hr', 0, 1, 'pending')",
        (_ONBOARDING_ITEM, _ONBOARDING_CHECKLIST),
    )
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt, role) "
        "VALUES ('account-4-4', 'alice', 'pbkdf2_sha256$1$deadbeef', 'aabbccdd', 'hr')"
    )
    conn.commit()


def _seed_interview_ready(conn: sqlite3.Connection) -> None:
    """排期四节点共用种子（2026-10-11 修正）：一个 HR 账号＋一个面试官（名下
    可用时段覆盖 2026-10-14 06:00–08:00）＋一个处于 `interview` 阶段的投递。

    可用时段是必须的：`effect_schedule_slot` 过冲突检查，没时段会以
    「面试官无可用时段」拒绝，配方就永远撞不到崩溃-恢复协议要测的那条路径。"""
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('sched-hr', 'sched-hr', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('sched-iv-acc', 'sched-iv', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('sched-iv1', 'sched-iv-acc', '面试官甲', '研发部', '[]', 1)"
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('sched-job', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('sched-c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('sched-r1', 'sched-job', 'synthetic', 'a.pdf', 'sha-sched', 'sched-hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('sched-app1', 'sched-c1', 'sched-job', 'sched-r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interviewer_availability "
        "(id, interviewer_id, start_at, end_at, registered_by) "
        "VALUES ('sched-av1', 'sched-iv1', '2026-10-14 06:00', '2026-10-14 08:00', 'sched-hr')"
    )
    conn.commit()


def _seed_scheduled_slot(
    conn: sqlite3.Connection,
    *,
    start_at: str = "2026-10-14 06:30",
    end_at: str = "2026-10-14 07:30",
) -> None:
    """改期/取消/完成三个节点的种子：面试就绪数据＋一条 `scheduled` 场次
    （按固定 id 手插，⛔ 不调 `effect_schedule_slot`——thread_id 要点名确定性 id）。"""
    _seed_interview_ready(conn)
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, created_by) "
        "VALUES ('sched-slot1', 'sched-app1', 1, ?, ?, 'onsite', 'sched-hr')",
        (start_at, end_at),
    )
    conn.execute(
        "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
        "VALUES ('sched-slot1', 'sched-iv1')"
    )
    conn.commit()


def _seed_past_scheduled_slot(conn: sqlite3.Connection) -> None:
    """`effect_complete_slot` 种子：开始时刻已过的场次（节点要求 now ≥ start）。"""
    _seed_scheduled_slot(conn, start_at="2026-01-01 06:00", end_at="2026-01-01 07:00")


def build_recipes(tmp_path: pathlib.Path) -> dict[str, Recipe]:
    """节点名 → 崩溃-恢复配方。键集合必须与 EFFECT_NODE_MANIFEST 逐字相等。"""

    def _deliver(conn):
        effect_deliver_message(
            conn,
            thread_id=_JOB,
            business_key="hash-1",
            channel=WebChannel(conn),
            message=OutboundMessage(type="question", payload={"questions": ["Q1"]}),
        )

    def _audit(conn):
        recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
        message = CandidateOutboundMessage(
            message_type="rejection_letter", recipient="cand-9@example.com", body=_AI_BODY
        )
        effect_record_outbound_audit(
            conn,
            thread_id=_JOB,
            business_key=f"{message.content_hash()}:False:等待人工确认",
            recorder=recorder,
            event=DecisionEvent(
                id=f"{_JOB}:effect_record_outbound_audit:{message.content_hash()}:False",
                event_type=OUTBOUND_BLOCKED,
                thread_id=_JOB,
                message_type=message.message_type,
                recipient=message.recipient,
                content_hash=message.content_hash(),
                blocked_reason="等待人工确认",
                evidence={"severity": "high"},
            ),
        )

    def _enqueue(conn):
        effect_enqueue_pending_approval(
            conn,
            thread_id=_JOB,
            business_key="draft-hash-1",
            message=CandidateOutboundMessage(
                message_type="rejection_letter", recipient="cand-9@example.com", body=_AI_BODY
            ),
            blocked_reason="等待人工确认",
        )

    def _seed_jd(conn):
        _seed_job_with_profile_v1(
            conn,
            {
                "job_title": "底层软件工程师",
                JD_TEXT_KEY: f"岗位职责：负责 ECU 底层软件开发。\n\n{_LABEL}",
                "_jd_needs_manual": False,
            },
        )

    def _seed_application_base(conn):
        """prep 五个节点共用的前置：已确认画像 + 一条投递 + 一条可引用的
        analysis_run（充当 gen_run_id 的外键目标——真实链路里这行由 LLM
        网关的 AuditHook 写，这里手工造一行等价物，配方不关心它的内容）。"""
        conn.execute(
            "INSERT INTO job (id, title, status) VALUES (?, '嵌入式软件工程师', 'approved')",
            (_JOB,),
        )
        conn.execute(
            "INSERT INTO job_profile (job_id, version, status, profile_json) "
            "VALUES (?, 1, 'approved', ?)",
            (_JOB, json.dumps({"job_title": "嵌入式软件工程师"}, ensure_ascii=False)),
        )
        conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (_CANDIDATE,))
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES (?, ?, 'synthetic', 'a.pdf', 'hash-4-4', 'tester')",
            (_RESUME, _JOB),
        )
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES (?, ?, ?, ?, 'initial')",
            (_APPLICATION, _CANDIDATE, _JOB, _RESUME),
        )
        conn.execute(
            "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
            "input_hash, raw_response) VALUES (?, 'deepseek-chat', 'interview-prep-v1', 0, "
            "'hash', '{}')",
            (_PREP_RUN,),
        )
        conn.commit()

    def _seed_prep_snapshot_draft(conn):
        """在 _seed_application_base 基础上再加一份 draft 快照 + 一道题，供
        edit/delete/regenerate/freeze 四个节点复用。"""
        _seed_application_base(conn)
        conn.execute(
            "INSERT INTO prep_snapshot (id, application_id, version, profile_version, "
            "gen_run_id, status) VALUES ('prep-snap-4-4', ?, 1, 1, ?, 'draft')",
            (_APPLICATION, _PREP_RUN),
        )
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
            "rubric_json, follow_ups_json, rationale, origin) VALUES "
            "('prep-q-4-4', 'prep-snap-4-4', 1, 'AUTOSAR CP', 'easy', '原题面', "
            "'原 rubric', '[\"追问\"]', '依据', 'ai')"
        )
        conn.commit()

    def _prep_snapshot_count(conn, *, status=None):
        if status is None:
            return conn.execute(
                "SELECT COUNT(*) FROM prep_snapshot WHERE application_id = ? AND version = 1",
                (_APPLICATION,),
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM prep_snapshot WHERE application_id = ? AND version = 1 "
            "AND status = ?",
            (_APPLICATION, status),
        ).fetchone()[0]

    def _prep_question_row(conn):
        return conn.execute(
            "SELECT text, rubric_json, origin FROM prep_question WHERE snapshot_id = 'prep-snap-4-4' "
            "AND seq = 1"
        ).fetchone()

    _regen_replacement = PrepQuestionDraft(
        dimension="AUTOSAR CP", difficulty="medium", text="重生成后的题面",
        rubric="重生成后的 rubric", follow_ups=["新追问"], rationale="重生成依据",
    )

    # ── U3 邀约与同意（app/graph/invite_nodes.py）11 个节点的共用种子 ──────
    def _seed_invite_session(conn, *, status="pending"):
        """`_seed_application_base` 之上再放一行 `interview_session`——U3 的
        11 个节点里除 `effect_create_interview_session` 本身外，全部要求场次
        已存在（`interview_invite_event`/`interview_consent` 都外键指向它）。
        `prep_snapshot_version` 是裸整数，不建到 `prep_snapshot` 的复合外键
        （见 app/storage/db.py SCHEMA 注释），不需要真的有一份 prep_snapshot。"""
        _seed_application_base(conn)
        conn.execute(
            "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
            "retention_until, retention_policy_version, sample_class, status) "
            "VALUES (?, ?, 1, ?, 'v1-90d', 'internal_sim', ?)",
            (_SESSION, _APPLICATION, _FAR_FUTURE, status),
        )
        conn.commit()

    def _seed_resume_token_session(conn):
        """`effect_consume_resume_token` 专用：场次已签发一枚续入令牌
        （`resume_token_hash` 非空）、状态 'interrupted'，用来观察消费后
        `resume_token_hash` 清空 + 状态回到 'in_progress' 这个转移。"""
        _seed_invite_session(conn, status="interrupted")
        conn.execute(
            "UPDATE interview_session SET resume_token_hash = 'rt-hash-4-4' WHERE id = ?",
            (_SESSION,),
        )
        conn.commit()

    # ── U4 live 子图（app/graph/live_session_nodes.py）4 个节点的共用种子 ──
    def _seed_live_session(conn):
        """复用既有的 `_seed_prep_snapshot_draft`（job/profile/candidate/
        resume/application/analysis_run/prep_snapshot/prep_question 一条
        完整闭环）再加一行 'in_progress' 的 `interview_session`——
        `effect_persist_turn` 的 `interview_turn.question_id` 外键指向
        `prep_question`，没有这条闭环会在种子阶段就炸 FK。"""
        _seed_prep_snapshot_draft(conn)
        conn.execute(
            "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
            "retention_until, retention_policy_version, sample_class, status) "
            "VALUES (?, ?, 1, ?, 'v1-90d', 'internal_sim', 'in_progress')",
            (_SESSION, _APPLICATION, _FAR_FUTURE),
        )
        conn.commit()

    def _live_bundle() -> SessionBundle:
        return SessionBundle(
            session_id=_SESSION, prep_curve="easy_to_hard", follow_up_limit=2,
            questions=[SessionBundleQuestion(question_id="prep-q-4-4", seq=1, text="原题面", follow_ups=["追问"])],
        )

    # ── U5 post 评分（app/graph/interview_scoring_nodes.py）3 个节点的种子 ──
    def _seed_scoring_session(conn):
        """在 `_seed_live_session` 之上再加一条已完成的 `interview_turn`
        （`effect_write_acoustic_refs`/`effect_persist_scorecard` 的证据回指都
        指向具体某条 turn）与一条 `analysis_run`（`criterion_score`/
        `interview_scorecard` 外键指向它，见 app/storage/db.py SCHEMA）。"""
        _seed_prep_snapshot_draft(conn)
        conn.execute(
            "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
            "retention_until, retention_policy_version, sample_class, status) "
            "VALUES (?, ?, 1, ?, 'v1-90d', 'internal_sim', 'completed')",
            (_SESSION, _APPLICATION, _FAR_FUTURE),
        )
        conn.execute(
            "INSERT INTO interview_turn (id, session_id, seq, question_id, question_text, "
            "answer_text, answer_mode, audio_start_ms, audio_end_ms) VALUES "
            "(?, ?, 1, 'prep-q-4-4', '讲讲你的项目', '做过三年 AUTOSAR CP 分层开发', "
            "'voice', 0, 8000)",
            (_TURN, _SESSION),
        )
        conn.execute(
            "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
            "prompt_version, temperature, input_hash, raw_response) VALUES "
            "(?, ?, ?, 'deepseek-chat', 'interview-score-v1', 0, 'h', '{}')",
            (_SCORE_RUN, _APPLICATION, _JOB),
        )
        conn.commit()

    # ── U2 文书引擎（app/graph/letter_nodes.py）3 个节点的种子 ────────────
    def _seed_letter_application(conn):
        """`effect_persist_letter` 的前置：投递闭环（复用
        `_seed_application_base`）+ 一条 'approved' 的 offer（节点内
        `_assert_offer_approved` 的前置）+ 一条 analysis_run（草稿的 run_id
        会落进 candidate_letter.analysis_run_id，外键指向它）。"""
        _seed_application_base(conn)
        conn.execute(
            "INSERT INTO offer (id, application_id, job_id, department, start_date, "
            "report_to, status, created_by) VALUES ('offer-4-4', ?, ?, '研发部', "
            "'2026-10-20', '李四', 'approved', 'hr:tester')",
            (_APPLICATION, _JOB),
        )
        conn.execute(
            "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
            "input_hash, raw_response) VALUES (?, 'deepseek-chat', 'letter-offer-v1', 0, "
            "'letter-hash-4-4', '{}')",
            (_LETTER_RUN,),
        )
        conn.commit()

    def _seed_letter_row(conn):
        """`effect_edit_letter` / `effect_mark_letter_human_written` 的前置：
        一条已生成的 Offer 文书（带 AI 标识，生成时间 _TS——编辑节点要按它
        回读原生成时间）。"""
        _seed_application_base(conn)
        conn.execute(
            "INSERT INTO candidate_letter (id, application_id, kind, version, "
            "template_version, body, ai_generated, sent_status, created_by) "
            "VALUES (?, ?, 'offer', 1, 1, ?, 1, 'none', 'hr:tester')",
            (_LETTER_ID, _APPLICATION, _LETTER_AI_BODY),
        )
        conn.commit()

    def _letter_body(conn):
        return conn.execute(
            "SELECT body FROM candidate_letter WHERE id = ?", (_LETTER_ID,)
        ).fetchone()[0]

    def _letter_still_ai_generated(conn):
        return conn.execute(
            "SELECT COUNT(*) FROM candidate_letter WHERE id = ? AND ai_generated = 1",
            (_LETTER_ID,),
        ).fetchone()[0]

    _letter_draft = LetterDraft(
        kind="offer",
        body=_LETTER_AI_BODY,
        run_id=_LETTER_RUN,
        response_model="deepseek-chat",
        prompt_version="letter-offer-v1",
    )

    # ── interview-scheduling U3（app/graph/invitation_nodes.py）持久化节点的种子 ──
    def _seed_invitation_slot(conn):
        """`effect_persist_invitation_draft` 的前置：job→candidate→resume→
        application→interview_slot 最小闭环，`invitation_status` 取默认 'none'
        （节点生效后会推到 'drafted'，崩溃点之前必须仍是 'none'）。

        ⛔ 不需要 analysis_run：`interview_invitation_draft.analysis_run_id` **无外键**
        （U3 计划 §5 已在磁盘上核对过），草稿的 run_id 用打桩值即可——这与
        `effect_persist_letter` 的配方不同，后者的 analysis_run_id 是有外键的。"""
        conn.execute(
            "INSERT INTO job (id, title, status) "
            "VALUES ('inv-job-4-4', '嵌入式软件工程师', 'approved')"
        )
        conn.execute("INSERT INTO candidate (id, name) VALUES ('inv-cand-4-4', '张三')")
        conn.execute(
            "INSERT INTO resume "
            "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES ('inv-resume-4-4', 'inv-job-4-4', 'synthetic', 'a.pdf', "
            "'inv-hash-4-4', 'tester')"
        )
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES (?, 'inv-cand-4-4', 'inv-job-4-4', 'inv-resume-4-4', 'interview')",
            (_INV_APPLICATION,),
        )
        conn.execute(
            "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
            "VALUES (?, ?, 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')",
            (_INV_SLOT, _INV_APPLICATION),
        )
        conn.commit()

    _invitation_draft = InvitationDraft(
        body=_INV_BODY,
        run_id=_INV_RUN,
        response_model="deepseek-chat-actual-v1",
        prompt_version="invite-v1",
    )

    return {
        "effect_persist_draft": Recipe(
            thread_id=_JOB,
            seed=_seed_job,
            invoke=lambda conn: effect_persist_draft(
                conn,
                thread_id=_JOB,
                business_key="0",
                state={"profile_patch_accumulated": {"job_title": "底层软件工程师"}, "history": []},
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM job_profile WHERE job_id = ?", (_JOB,)
            ).fetchone()[0],
        ),
        "effect_deliver_message": Recipe(
            thread_id=_JOB,
            seed=_seed_nothing,
            invoke=_deliver,
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE thread_id = ?", (_JOB,)
            ).fetchone()[0],
        ),
        "effect_confirm_profile": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_confirm_profile(
                conn,
                thread_id=_JOB,
                business_key="1",
                profile_dict={"job_title": "底层软件工程师"},
                reviewer="业务经理甲",
            ),
            count_business_rows=lambda conn: _human_review_count(conn, "approved"),
        ),
        "effect_request_revision": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_request_revision(
                conn,
                thread_id=_JOB,
                business_key="1",
                reviewer="业务经理甲",
                feedback="学历要求写高了",
            ),
            count_business_rows=lambda conn: _human_review_count(conn, "revision_requested"),
        ),
        "effect_abandon_profile": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_abandon_profile(
                conn,
                thread_id=_JOB,
                business_key="1",
                reviewer="业务经理甲",
                feedback="岗位取消",
            ),
            count_business_rows=lambda conn: _human_review_count(conn, "abandoned"),
        ),
        "effect_generate_and_persist_jd": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_generate_and_persist_jd(
                conn,
                thread_id=_JOB,
                business_key="1",
                gateway=_CountingGateway(),
                profile=_jd_profile(),
                profile_dict={"job_title": "底层软件工程师"},
                version=1,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM job_profile WHERE job_id = ? "
                "AND json_extract(profile_json, '$._jd_text') IS NOT NULL",
                (_JOB,),
            ).fetchone()[0],
            note=(
                "唯一一个重放会重复触发付费 LLM 调用的节点，见「红灯与观察项」O-1。"
                "同时它也是 value-idempotent 的：这里用 IS NOT NULL 做存在性判断，"
                "只要该字段已被写过一次，哪怕重放让 LLM 又生成了一遍新文本覆盖旧值，"
                "在这一列上仍然是「存在」——行数分不出写了一次还是两次，双发保护"
                "同样完全靠 effect_log 的 COUNT(*) == 1 断言。"
            ),
        ),
        "effect_enqueue_pending_approval": Recipe(
            thread_id=_JOB,
            seed=_seed_nothing,
            invoke=_enqueue,
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM pending_approval WHERE thread_id = ?", (_JOB,)
            ).fetchone()[0],
        ),
        "effect_record_outbound_audit": Recipe(
            thread_id=_JOB,
            seed=_seed_nothing,
            invoke=_audit,
            # ⚠️ 偏离登记（Task 2 落地）：brief 原文按 `WHERE thread_id = ?` 数
            # analysis_run，但 analysis_run 表（app/storage/db.py SCHEMA）根本
            # 没有 thread_id 列，只有 application_id / job_id——那条查询在真实
            # schema 上会直接抛 sqlite3.OperationalError: no such column，是
            # 配方本身写错，不是节点不幂等。这里改成不加过滤地数全表行数：
            # rows_per_effect=0 的语义是"这个节点在 SQLite 侧恒不产生业务行"
            # （SqliteSink.SUPPORTED_EVENT_TYPES 只收 ai_analysis，OUTBOUND_BLOCKED
            # 事件的 write() 恒返回 False、不 INSERT），每个节点各用一个独立
            # tmp_path 数据库文件，全表计数与"这个节点的 analysis_run 行数"
            # 结果等价。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM analysis_run"
            ).fetchone()[0],
            rows_per_effect=0,
            note=(
                "**显式声明的例外**：外发事件在 analysis_run 里没有真身"
                "（SqliteSink.SUPPORTED_EVENT_TYPES 只收 ai_analysis），它的载体是"
                "JSONL 镜像，而镜像 append 由调用方在装饰器 commit **之后**触发，"
                "本就不在事务里。所以本节点的 SQLite 业务行数恒为 0——这是设计如此，"
                "⛔ 不是漏测。见「红灯与观察项」O-2。"
                "同时它是四个 value-idempotent 配方之一，且是最极端的一个："
                "rows_per_effect=0 时业务行断言退化成 0 == 0 的恒等式，永远成立、"
                "什么也证明不了，双发保护 100% 靠 effect_log 的 COUNT(*) == 1。"
            ),
        ),
        "effect_update_jd_text": Recipe(
            thread_id=_JOB,
            seed=_seed_jd,
            invoke=lambda conn: effect_update_jd_text(
                conn,
                thread_id=_JOB,
                business_key=jd_edit_business_key(1, _EDITED_JD),
                version=1,
                edited_text=_EDITED_JD,
            ),
            count_business_rows=lambda conn: int(
                bool(_EDITED_JD in _profile_v1(conn).get(JD_TEXT_KEY, ""))
            ),
            note=(
                "**value-idempotent**：这条业务写是把 JD 文案整段替换成同一段"
                "编辑后的文本，无论 idempotent_effect 放行执行了一次还是被短路"
                "跳过零次，profile_json 里这段文案落地后的取值完全相同——单看"
                "这一列的取值/行数分不出「生效了一次」与「生效了两次」，双发"
                "保护完全靠 effect_log 的 COUNT(*) == 1 断言，这里的检查只是"
                "附带确认。"
            ),
        ),
        "effect_mark_jd_human_written": Recipe(
            thread_id=_JOB,
            seed=_seed_jd,
            invoke=lambda conn: effect_mark_jd_human_written(
                conn,
                thread_id=_JOB,
                business_key="1",
                version=1,
                reviewer="HR 乙",
                marked_at=_TS,
            ),
            count_business_rows=lambda conn: int(bool(_profile_v1(conn).get(JD_AUTHORSHIP_KEY))),
            note=(
                "**value-idempotent**：标记「人工撰写」是把 profile_json 里的一个"
                "作者标记位设成同一个值，重复标记与只标记一次在这一列上的取值"
                "完全相同——双发保护同样完全靠 effect_log 的 COUNT(*) == 1 断言。"
            ),
        ),
        "effect_persist_letter": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_letter_application,
            invoke=lambda conn: effect_persist_letter(
                conn,
                thread_id=_APPLICATION,
                business_key=f"offer:{_LETTER_RUN}",
                application_id=_APPLICATION,
                kind="offer",
                version=1,
                template_version=1,
                draft=_letter_draft,
                created_by="hr:tester",
            ),
            # 业务事实 = 这版草稿在 candidate_letter 里的那一行（一次真实 LLM
            # 调用落一版）。行数选「该投递的文书行数」而非全表：本节点按
            # application_id 写，口径与 thread_id 对齐。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM candidate_letter WHERE application_id = ?",
                (_APPLICATION,),
            ).fetchone()[0],
            note=(
                "业务事实是 INSERT 的一行 candidate_letter；同一 business_key"
                "（kind:run_id）重放会先命中 effect_log 短路，撞不到"
                "UNIQUE(application_id, kind, version)。"
            ),
        ),
        "effect_edit_letter": Recipe(
            thread_id=_LETTER_ID,
            seed=_seed_letter_row,
            invoke=lambda conn: effect_edit_letter(
                conn,
                thread_id=_LETTER_ID,
                business_key=letter_edit_business_key(_LETTER_ID, _EDITED_LETTER_BODY),
                letter_id=_LETTER_ID,
                edited_body=_EDITED_LETTER_BODY,
            ),
            # ⚠️ value-idempotent：编辑是 UPDATE 同一行（body 整段替换），
            # 行数不变，口径改用「该行正文是否等于编辑后的目标值」这个 0/1
            # 谓词，与 effect_update_jd_text 同一手法。目标值由
            # enforce_ai_label 以**原标识里的生成时间**（_TS）重贴——正是节点
            # 内部走的那条路（「编辑不去标识」）。
            count_business_rows=lambda conn: int(
                _letter_body(conn)
                == enforce_ai_label(_EDITED_LETTER_BODY, generated_at=_TS)
            ),
            note=(
                "**value-idempotent**：编辑把正文整段替换成同一段目标文本，"
                "无论放行一次还是被短路，这一列的取值完全相同——双发保护完全"
                "靠 effect_log 的 COUNT(*) == 1 断言。"
            ),
        ),
        "effect_mark_letter_human_written": Recipe(
            thread_id=_LETTER_ID,
            seed=_seed_letter_row,
            invoke=lambda conn: effect_mark_letter_human_written(
                conn,
                thread_id=_LETTER_ID,
                business_key="mark-human-4-4",
                letter_id=_LETTER_ID,
                reviewer="HR 乙",
                marked_at=_TS,
            ),
            # ⚠️ value-idempotent：去标识 + 留痕是同一次 UPDATE，行数不变，
            # 口径改用「该行是否已处于目标状态（ai_generated=0）」的 0/1 谓词。
            count_business_rows=_letter_still_ai_generated,
            rows_per_effect=-1,
            note=(
                "**value-idempotent + 负 rows_per_effect**：种子先放一行"
                " ai_generated=1（rows_before=1），生效一次后这一行变成 0"
                "（0 行），`rows_before + rows_per_effect == 0` 要求 -1。"
                "双发保护同样完全靠 effect_log 的 COUNT(*) == 1：第二次调用"
                "命中短路，不会在已经是 0 的谓词上再改一次。"
            ),
        ),
        "effect_mark_needs_manual": Recipe(
            thread_id=_JOB,
            seed=_seed_job,
            invoke=lambda conn: effect_mark_needs_manual(
                conn,
                thread_id=_JOB,
                business_key="0",
                reason_code=REASON_PROVIDER_UNAVAILABLE,
            ),
            # ⚠️ value-idempotent：业务写是 UPDATE job SET status='needs_manual'
            # （见 app/graph/manual_handoff.py），不是新增行——job 表本身的行数
            # 恒为 1（种子已插入）。这里改用"处于 needs_manual 状态的 job 行数"
            # 这个等价口径：生效一次前是 0、生效一次后是 1，与其余配方
            # "rows_per_effect 份新增业务事实"的语义对齐，能分辨"没生效/生效
            # 一次"，但和 effect_update_jd_text 等 value-idempotent 配方一样，
            # 分不出「生效一次」与「生效两次」——双发保护同样完全靠 effect_log
            # 的 COUNT(*) == 1 断言。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM job WHERE id = ? AND status = 'needs_manual'",
                (_JOB,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：把 job.status 置为同一个值 'needs_manual'，"
                "行数口径改用「处于该状态的行数」而非「新增行数」，因为这是"
                "UPDATE 不是 INSERT。双发保护完全靠 effect_log 的 COUNT(*) == 1。"
            ),
        ),
        "effect_deliver_manual_handoff": Recipe(
            thread_id=_JOB,
            seed=_seed_job,
            invoke=lambda conn: effect_deliver_manual_handoff(
                conn,
                thread_id=_JOB,
                business_key="0",
                channel=WebChannel(conn),
                reason_code=REASON_PROVIDER_UNAVAILABLE,
                round_count=0,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE thread_id = ?", (_JOB,)
            ).fetchone()[0],
        ),
        "effect_persist_parse": Recipe(
            thread_id=_RESUME,
            seed=_seed_resume,
            invoke=lambda conn: effect_persist_parse(
                conn,
                thread_id=_RESUME,
                business_key="v1",
                resume_id=_RESUME,
                job_id=_JOB,
                fields=_resume_fields(),
                parser_version="v1",
                model_configured="deepseek-chat",
                model_response="deepseek-chat",
                prompt_version="parse-v1",
                confidence_threshold=0.7,
            ),
            # 数 resume_parse_version 而不是 application：application 被
            # idx_application_resume（app/storage/db.py，UNIQUE(resume_id)）钉死
            # 在"每份简历至多 1 行"，无论这个节点被重放几次、写没写成，计数都不
            # 会超过 1——用它当业务事实等于用一个恒真断言冒充守卫。本节点真正
            # 1:1 对应一次生效的是 resume_parse_version 的那一行（主键
            # (resume_id, parser_version) = 一个 business_key 一行），重解析加
            # 版本时它会真的增长，重复生效也会真的撞主键。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM resume_parse_version WHERE resume_id = ?", (_RESUME,)
            ).fetchone()[0],
        ),
        "effect_attach_resume_to_candidate": Recipe(
            thread_id=_RESUME,
            seed=_seed_resume,
            invoke=lambda conn: effect_attach_resume_to_candidate(
                conn,
                thread_id=_RESUME,
                business_key="once",
                resume_id=_RESUME,
                job_id=_JOB,
                name="张三",
                # ⚠️ 故意给 None（无手机号 ⇒ 恒新建候选人）：这条配方不依赖种子里的
                # phone_hash 取值，与 Task 1 的哈希口径解耦。
                phone_hash=None,
            ),
            # 业务事实 = 这个 resume 名下的 application 一行（idx_application_resume
            # 把"每份简历至多一条投递"钉死）。candidate 行数不能用：命中复用分支
            # 下它不增长，分不出生效与否。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM application WHERE resume_id = ?", (_RESUME,)
            ).fetchone()[0],
        ),
        "effect_merge_candidates": Recipe(
            thread_id=_MERGE_PRIMARY,
            seed=_seed_merge_pair,
            invoke=lambda conn: effect_merge_candidates(
                conn,
                thread_id=_MERGE_PRIMARY,
                # 幂等键 = {primary_id}:effect_merge_candidates:{secondary_id}:{request_id}
                business_key=f"{_MERGE_SECONDARY}:req-4-4",
                primary_id=_MERGE_PRIMARY,
                secondary_id=_MERGE_SECONDARY,
                reason="电话确认同一人",
                keep_application_per_job={},
                merged_by="alice",
            ),
            # 业务事实 = 一次合并留痕一行（idx_candidate_merge_log_active_secondary
            # 把"同一个被合并方撤销前只能有一行"钉死）。⛔ 不数 application：
            # 合并只把 candidate_id 改到 primary，**行数不变**，分不出生效与否；
            # 也不数 candidate：primary 行本来就存在（同理）。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM candidate_merge_log WHERE secondary_id = ?",
                (_MERGE_SECONDARY,),
            ).fetchone()[0],
            note=(
                "本节点自己的业务事实 = candidate_merge_log 一行。崩溃点落在 "
                "effect_log INSERT 之后的那次 commit 上，此时留痕、application 改挂、"
                "secondary.merged_into 三处业务写都还在同一个事务里，随连接一起丢弃；"
                "重放时 effect_log 先短路，撞不到 idx_candidate_merge_log_active_secondary。"
            ),
        ),
        "effect_unmerge_candidates": Recipe(
            thread_id=_MERGE_LOG,
            seed=_seed_merge_log_for_unmerge,
            invoke=lambda conn: effect_unmerge_candidates(
                conn,
                thread_id=_MERGE_LOG,
                # 幂等键 = {merge_log_id}:effect_unmerge_candidates:undo
                business_key="undo",
                merge_log_id=_MERGE_LOG,
                unmerged_by="alice",
            ),
            # 业务事实 = 按快照**归还**给被合并方的投递份数（种子那份挂在 primary
            # 名下 ⇒ 生效前 0、生效后 1）。⚠️ 与合并配方口径相反不是笔误：合并只
            # 改挂、行数不变，只有"归属"能分辨；撤销的归属变化本身就是可数的——它
            # 恰恰是 spec「撤销合并 ⇒ 简历与投递归还」那句话的可执行形式。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM application WHERE candidate_id = ?", (_MERGE_SECONDARY,)
            ).fetchone()[0],
            note=(
                "**value-idempotent**：撤销是 UPDATE 族（恢复投递归属 + 清 "
                "merged_into + 写 unmerged_by/at），行数口径改用「已归还到被合并方名下的"
                "投递数」，与 effect_mark_needs_manual / effect_update_item 同一手法。"
                "崩溃点落在 effect_log INSERT 之后的那次 commit 上，三处业务写都还在"
                "同一个事务里、随连接一起丢弃（这正是最危险的那个窗口：业务写落了盘而"
                "effect_log 没落，重放会撞「该合并已被撤销」永久失败）；重放时 effect_log "
                "先短路，撤销恰好发生一次。"
            ),
        ),
        "effect_ingest_bundle": Recipe(
            thread_id=_JOB,
            seed=_seed_job,
            invoke=lambda conn: effect_ingest_bundle(
                conn,
                thread_id=_JOB,
                business_key="bundle-sha-4-4",
                job_id=_JOB,
                sample_class="synthetic",
                uploaded_by="alice",
                entries=[
                    FileEntry(name="a.pdf", filename="a.pdf", kind="ok", data=b"%PDF-4-4"),
                ],
                default_source=None,
                # ⚠️ ingest_one 是注入的桩：本节点自己的原子业务写只有
                # bundle_ingest_result 一行（逐文件简历写入委托给 M2 既有单文件
                # 接收，其幂等由文件哈希去重 + effect_persist_parse 承担，见计划
                # 「架构决策」第 2 条）。崩溃-恢复协议要隔离的正是这一行，注入一个
                # 不写库的桩才不会把 M2 的业务表行数混进本节点的恒等式。
                ingest_one=_bundle_ingest_stub,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM bundle_ingest_result WHERE job_id = ?", (_JOB,)
            ).fetchone()[0],
            note=(
                "本节点自己的业务事实 = bundle_ingest_result 一行（唯一键 job_id + "
                "bundle_sha256）。逐文件简历写入委托给注入的 ingest_one，不在本节点"
                "的原子边界内，故配方用不写库的桩把它隔离掉——一行 = 一次生效；"
                "重放时 effect_log 先短路，撞不到 idx_bundle_ingest_result_job_hash。"
            ),
        ),
        "effect_persist_flags": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_screening,
            invoke=lambda conn: effect_persist_flags(
                conn,
                thread_id=_APPLICATION,
                business_key="1:v1:0",
                application_id=_APPLICATION,
                profile_version=1,
                verdicts=compute_screen(
                    conn, resume_id=_RESUME, job_id=_JOB, profile_version=1
                ),
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM screening_flag WHERE application_id = ?", (_APPLICATION,)
            ).fetchone()[0],
        ),
        "effect_persist_prep_draft": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_application_base,
            invoke=lambda conn: effect_persist_prep_draft(
                conn,
                thread_id=_APPLICATION,
                business_key=_PREP_RUN,
                application_id=_APPLICATION,
                version=1,
                profile_version=1,
                resume_run_id=None,
                draft=PrepDraft(
                    questions=[
                        PrepQuestionDraft(
                            dimension="AUTOSAR CP", difficulty="easy", text="题面",
                            rubric="rubric", follow_ups=["追问"], rationale="依据",
                        )
                    ],
                    dropped_count=0,
                    run_id=_PREP_RUN,
                    response_model="deepseek-chat-241226",
                ),
            ),
            count_business_rows=lambda conn: _prep_snapshot_count(conn),
        ),
        "effect_freeze_prep": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_freeze_prep(
                conn,
                thread_id=_APPLICATION,
                business_key="1",
                application_id=_APPLICATION,
                version=1,
                confirmed_by="hr-1",
            ),
            count_business_rows=lambda conn: _prep_snapshot_count(conn, status="frozen"),
            note=(
                "**value-idempotent**：draft → frozen 是 UPDATE，行数口径改用"
                "「处于 frozen 状态的行数」而非「新增行数」，与 "
                "effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_edit_prep_question": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_edit_prep_question(
                conn,
                thread_id=_APPLICATION,
                business_key="1:1:edit1",
                snapshot_id="prep-snap-4-4",
                seq=1,
                text="改过的题面",
                rubric="改过的 rubric",
            ),
            count_business_rows=lambda conn: int(
                _prep_question_row(conn) == ("改过的题面", "改过的 rubric", "ai_edited")
            ),
            note=(
                "**value-idempotent**：改题面是 UPDATE 同一行，行数口径改用"
                "「该行取值是否等于编辑后的目标值」这个 0/1 谓词，与 "
                "effect_update_jd_text 同一手法。"
            ),
        ),
        "effect_regenerate_prep_question": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_regenerate_prep_question(
                conn,
                thread_id=_APPLICATION,
                business_key="1:1:regen1",
                snapshot_id="prep-snap-4-4",
                seq=1,
                question=_regen_replacement,
            ),
            count_business_rows=lambda conn: int(
                (_prep_question_row(conn) or (None,))[0] == "重生成后的题面"
            ),
            note=(
                "**value-idempotent**：重生成是 UPDATE 同一行，行数口径改用"
                "「该行题面是否等于重生成后的目标值」这个 0/1 谓词，与 "
                "effect_update_jd_text 同一手法。"
            ),
        ),
        "effect_delete_prep_question": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_delete_prep_question(
                conn,
                thread_id=_APPLICATION,
                business_key="1:1:delete",
                snapshot_id="prep-snap-4-4",
                seq=1,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM prep_question WHERE snapshot_id = 'prep-snap-4-4' "
                "AND seq = 1"
            ).fetchone()[0],
            rows_per_effect=-1,
            note=(
                "**全表唯一的负值**：这是 DELETE，不是 INSERT/UPDATE——种子先放一行"
                "（rows_before=1），生效一次后这行消失（0 行），"
                "`rows_before + rows_per_effect == 0` 要求 rows_per_effect=-1。"
                "双发保护同样完全靠 effect_log 的 COUNT(*) == 1：第二次调用命中"
                "幂等短路，不会在已经是 0 行的表上再次尝试 DELETE。"
            ),
        ),
        "effect_create_interview_session": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_application_base,
            invoke=lambda conn: effect_create_interview_session(
                conn,
                thread_id=_APPLICATION,
                business_key="req-4-4",
                session={
                    "id": _SESSION,
                    "application_id": _APPLICATION,
                    "prep_snapshot_version": 1,
                    "sample_class": "internal_sim",
                    "retention_until": _FAR_FUTURE,
                    "retention_policy_version": "v1-90d",
                },
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE application_id = ?", (_APPLICATION,)
            ).fetchone()[0],
        ),
        "effect_issue_invite": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_issue_invite(
                conn,
                thread_id=_SESSION,
                business_key="issue-hash-4-4",
                session_id=_SESSION,
                token_hash="issue-hash-4-4",
                expires_at=_FAR_FUTURE,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_invite_event WHERE session_id = ? "
                "AND event_type = 'issued'",
                (_SESSION,),
            ).fetchone()[0],
        ),
        "effect_log_invite_access_denied": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_log_invite_access_denied(
                conn,
                thread_id=_SESSION,
                business_key="denied-4-4",
                session_id=_SESSION,
                reason="expired_access",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_invite_event WHERE session_id = ? "
                "AND event_type = 'expired_access'",
                (_SESSION,),
            ).fetchone()[0],
        ),
        "effect_open_invite": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_open_invite(
                conn, thread_id=_SESSION, business_key="open", session_id=_SESSION,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE id = ? AND status = 'in_progress'",
                (_SESSION,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：pending → in_progress 是 UPDATE，行数口径"
                "改用「处于该状态的行数」，与 effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_issue_resume_token": Recipe(
            thread_id=_SESSION,
            seed=lambda conn: _seed_invite_session(conn, status="interrupted"),
            invoke=lambda conn: effect_issue_resume_token(
                conn,
                thread_id=_SESSION,
                business_key="resume-hash-4-4",
                session_id=_SESSION,
                token_hash="resume-hash-4-4",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_invite_event WHERE session_id = ? "
                "AND event_type = 'resume_issued'",
                (_SESSION,),
            ).fetchone()[0],
        ),
        "effect_consume_resume_token": Recipe(
            thread_id=_SESSION,
            seed=_seed_resume_token_session,
            invoke=lambda conn: effect_consume_resume_token(
                conn, thread_id=_SESSION, business_key="consume-4-4", session_id=_SESSION,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE id = ? "
                "AND status = 'in_progress' AND resume_token_hash IS NULL",
                (_SESSION,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：消费续入令牌是 UPDATE（清空哈希 + 状态回到"
                "in_progress），种子先放一个非空哈希 + 'interrupted' 状态，行数口径"
                "改用「已消费状态的行数」，与 effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_deliver_invitation": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_deliver_invitation(
                conn,
                thread_id=_SESSION,
                business_key="draft-4-4",
                session_id=_SESSION,
                recipient="candidate:application-4-4",
                body="您好，请点击链接开始面试。",
                channel=WebChannel(conn),
                recorder=AuditRecorder(SqliteSink(conn), JsonlChainSink(tmp_path / "decisions-invite.jsonl")),
                outbound_enabled=lambda: False,
                confirmed_by="hr:tester",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_invite_event WHERE session_id = ? "
                "AND event_type IN ('manual_handoff', 'delivered')",
                (_SESSION,),
            ).fetchone()[0],
        ),
        "effect_record_consent": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_record_consent(
                conn,
                thread_id=_SESSION,
                business_key="ai_interview:v1",
                session_id=_SESSION,
                kind="ai_interview",
                result="accepted",
                version="v1",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_consent WHERE session_id = ? AND kind = 'ai_interview'",
                (_SESSION,),
            ).fetchone()[0],
        ),
        "effect_issue_verification_code": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_issue_verification_code(
                conn,
                thread_id=_SESSION,
                business_key="code-4-4",
                session_id=_SESSION,
                code_hash="code-hash-4-4",
                expires_at=_FAR_FUTURE,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE id = ? AND phone_code_hash = 'code-hash-4-4'",
                (_SESSION,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：签发验证码是 UPDATE 同一行三列，行数口径改用"
                "「该行哈希是否等于签发值」这个 0/1 谓词，与 effect_update_jd_text "
                "同一手法。"
            ),
        ),
        "effect_verify_phone": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_verify_phone(
                conn, thread_id=_SESSION, business_key="1", session_id=_SESSION, correct=True,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE id = ? AND phone_verified_at IS NOT NULL",
                (_SESSION,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：验证通过是 UPDATE phone_verified_at，行数口径"
                "改用「该列非空的行数」，与 effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_display_verification_code_to_hr": Recipe(
            thread_id=_SESSION,
            seed=_seed_invite_session,
            invoke=lambda conn: effect_display_verification_code_to_hr(
                conn,
                thread_id=_SESSION,
                business_key="display-4-4",
                session_id=_SESSION,
                accessor="hr:tester",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_invite_event WHERE session_id = ? "
                "AND event_type = 'code_displayed_to_hr'",
                (_SESSION,),
            ).fetchone()[0],
        ),
        "effect_open_session": Recipe(
            thread_id=_SESSION,
            seed=_seed_live_session,
            invoke=lambda conn: effect_open_session(
                conn,
                thread_id=_SESSION,
                business_key="1",
                session_id=_SESSION,
                bundle=_live_bundle(),
                client=_FakeVoiceHostClient(),
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_live_event WHERE session_id = ? AND event_type = 'opened'",
                (_SESSION,),
            ).fetchone()[0],
        ),
        "effect_persist_turn": Recipe(
            thread_id=_SESSION,
            seed=_seed_live_session,
            invoke=lambda conn: effect_persist_turn(
                conn,
                thread_id=_SESSION,
                business_key="1",
                session_id=_SESSION,
                event=LiveTurnEvent(
                    seq=1, question_id="prep-q-4-4", question_text="讲讲你的项目",
                    answer_text="回答内容", answer_mode="text",
                ),
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_turn WHERE session_id = ? AND seq = 1", (_SESSION,)
            ).fetchone()[0],
        ),
        "effect_close_session": Recipe(
            thread_id=_SESSION,
            seed=_seed_live_session,
            invoke=lambda conn: effect_close_session(
                conn, thread_id=_SESSION, business_key="close", session_id=_SESSION,
                final_status="completed",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE id = ? AND status = 'completed'",
                (_SESSION,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：in_progress → completed 是 UPDATE，行数口径"
                "改用「处于该状态的行数」，与 effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_fetch_recording": Recipe(
            thread_id=_SESSION,
            seed=_seed_live_session,
            invoke=lambda conn: effect_fetch_recording(
                conn,
                thread_id=_SESSION,
                business_key=_RECORDING_SHA256,
                session_id=_SESSION,
                content=_RECORDING_CONTENT,
                recording_sha256=_RECORDING_SHA256,
                recording_dir=str(tmp_path / "recordings"),
                client=_FakeVoiceHostClient(),
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE id = ? AND recording_sha256 = ?",
                (_SESSION, _RECORDING_SHA256),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：落录音哈希是 UPDATE，行数口径改用「该列"
                "等于目标哈希的行数」，与 effect_update_jd_text 同一手法。"
            ),
        ),
        "effect_write_acoustic_refs": Recipe(
            thread_id=f"{_SESSION}:post",
            seed=_seed_scoring_session,
            invoke=lambda conn: effect_write_acoustic_refs(
                conn,
                thread_id=f"{_SESSION}:post",
                business_key=_SESSION,
                session_id=_SESSION,
                aligned_turns=[
                    AlignedTurn(
                        turn_id=_TURN, seq=1, question_id="prep-q-4-4", question_text="讲讲你的项目",
                        answer_text="做过三年 AUTOSAR CP 分层开发", answer_mode="voice",
                        asr_confidence=None, audio_start_ms=0, audio_end_ms=8000, low_confidence=False,
                    )
                ],
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_turn WHERE id = ? AND acoustic_ref IS NOT NULL",
                (_TURN,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：写声学参考是 UPDATE，行数口径改用「该列"
                "非空的行数」，与 effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_persist_scorecard": Recipe(
            thread_id=f"{_SESSION}:post",
            seed=_seed_scoring_session,
            invoke=lambda conn: effect_persist_scorecard(
                conn,
                thread_id=f"{_SESSION}:post",
                business_key=_SCORE_RUN,
                session_id=_SESSION,
                corrected_scores=[
                    CorrectedCriterionScore(
                        dimension="AUTOSAR CP", score=4.0, turn_id=_TURN, start=0, end=5,
                        quote="做过三年",
                    )
                ],
                overall_summary="整体表现良好",
                talking_points=[
                    TalkingPoint(dimension="AUTOSAR CP", turn_id=_TURN, tip_text="建议追问")
                ],
                analysis_run_id=_SCORE_RUN,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_scorecard WHERE session_id = ?", (_SESSION,)
            ).fetchone()[0],
        ),
        "effect_mark_scoring_failed": Recipe(
            thread_id=f"{_SESSION}:post",
            seed=_seed_scoring_session,
            invoke=lambda conn: effect_mark_scoring_failed(
                conn,
                thread_id=f"{_SESSION}:post",
                business_key="fail-4-4",
                session_id=_SESSION,
                reason="维度证据反查失败",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_session WHERE id = ? AND post_scoring_status = 'failed_retry'",
                (_SESSION,),
            ).fetchone()[0],
            note=(
                "**value-idempotent**：标记失败是 UPDATE post_scoring_status，行数"
                "口径改用「处于该状态的行数」，与 effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_send_verification_code": Recipe(
            thread_id=_SESSION,
            seed=_seed_nothing,
            invoke=lambda conn: effect_send_verification_code(
                conn, thread_id=_SESSION, business_key="1",
            ),
            count_business_rows=lambda conn: 0,
            rows_per_effect=0,
            note=(
                "**永久占位桩（OQ-10 未决）**：不进入下面两条通用崩溃-恢复协议的"
                "parametrize 范围（见 _PERMANENTLY_UNIMPLEMENTED_STUBS），因为函数体"
                "永远在 idempotent_effect 的 fn(conn, ...) 调用点抛 NotImplementedError，"
                "走不到 effect_log INSERT/commit 这一步，两条通用协议的"
                "'crashed_once'/'恰好一份' 断言对它无意义。这条配方只服务"
                "test_every_effect_node_has_a_recovery_recipe 的清单-配方对齐检查，"
                "真正的行为断言在"
                "test_effect_send_verification_code_stub_always_raises_and_writes_nothing。"
            ),
        ),
        "effect_instantiate_checklist": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_hired_application_for_checklist,
            invoke=lambda conn: effect_instantiate_checklist(
                conn,
                thread_id=_APPLICATION,
                business_key="instantiate",
                created_by="hr:tester",
            ),
            # 业务事实＝这份投递唯一那一行 onboarding_checklist。同一事务里还
            # 展开出六行 onboarding_item，但"一次生效"的可分辨口径取清单行数：
            # 条目条数由模板决定（本配方走 default 模板＝6 条），
            # `tests/test_onboarding_nodes.py` 已单独锁死条目条数与内容，这里数
            # 它只会把"模板有几条"混进幂等判据里。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM onboarding_checklist WHERE application_id = ?",
                (_APPLICATION,),
            ).fetchone()[0],
            note=(
                "业务事实是 INSERT 的一行 onboarding_checklist；"
                "onboarding_checklist.application_id 带 UNIQUE 约束，是幂等键（第一道）"
                "之外的结构性第二道防线——重放若真漏过了 effect_log 短路，会撞 UNIQUE "
                "而不是静默写下第二份清单（同一投递只有一份清单是 spec「按投递实例化"
                "清单」的硬要求）。"
            ),
        ),
        "effect_update_item": Recipe(
            thread_id=_ONBOARDING_ITEM,
            seed=_seed_pending_hr_item_for_update,
            invoke=lambda conn: effect_update_item(
                conn,
                thread_id=_ONBOARDING_ITEM,
                business_key="done:request-4-4",
                to_status="done",
                reason=None,
                operator_username="alice",
            ),
            # 业务事实＝这条条目唯一那一行留痕。同一事务里还有一次
            # `UPDATE onboarding_item SET status='done'`，但那条 UPDATE **重复
            # 执行是幂等的**（done→done 行数不变），拿它当口径测不出"副作用被
            # 应用了两次"；留痕是 INSERT，重放若真漏过 effect_log 短路，必然多出
            # 第二行——这才是会变红的口径（铁律 1 的 reviewer 判据按 thread 数
            # 行数，thread_id 就是 item_id）。
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM onboarding_item_history WHERE item_id = ?",
                (_ONBOARDING_ITEM,),
            ).fetchone()[0],
            note=(
                "业务事实是 INSERT 的一行 onboarding_item_history（条目状态变更留痕）；"
                "同事务里的 onboarding_item.status UPDATE 与它同生共死，"
                "`tests/test_onboarding_nodes.py::test_update_item_rerun_no_second_history` "
                "另有一条用例锁死「重跑不产生第二条留痕」。"
            ),
        ),
        "effect_schedule_slot": Recipe(
            thread_id="sched-app1",
            seed=_seed_interview_ready,
            invoke=lambda conn: effect_schedule_slot(
                conn,
                thread_id="sched-app1",
                business_key="sched-slot1",
                slot_id="sched-slot1",
                application_id="sched-app1",
                interviewer_ids=["sched-iv1"],
                round_=1,
                start_at="2026-10-14 06:30",
                end_at="2026-10-14 07:30",
                mode="onsite",
                location_or_link=None,
                actor="sched-hr",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_slot WHERE application_id = 'sched-app1'"
            ).fetchone()[0],
            note=(
                "业务事实是 INSERT 的一行 interview_slot（同事务还有 roster 行与 "
                "scheduled 留痕）；场次主键与 effect_log 幂等键共同兜底唯一性。"
            ),
        ),
        "effect_reschedule_slot": Recipe(
            thread_id="sched-app1",
            seed=_seed_scheduled_slot,
            invoke=lambda conn: effect_reschedule_slot(
                conn,
                thread_id="sched-app1",
                business_key="sched-slot1:req-1",
                slot_id="sched-slot1",
                start_at="2026-10-14 06:45",
                end_at="2026-10-14 07:45",
                actor="sched-hr",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM application_stage_history "
                "WHERE application_id = 'sched-app1' AND action = 'rescheduled'"
            ).fetchone()[0],
            note=(
                "业务事实是 INSERT 的一行 rescheduled 留痕；场次行本身是 UPDATE，"
                "重复执行状态幂等，拿它当口径测不出双写。"
            ),
        ),
        "effect_cancel_slot": Recipe(
            thread_id="sched-app1",
            seed=_seed_scheduled_slot,
            invoke=lambda conn: effect_cancel_slot(
                conn,
                thread_id="sched-app1",
                business_key="sched-slot1",
                slot_id="sched-slot1",
                cancel_reason="候选人改约",
                actor="sched-hr",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM application_stage_history "
                "WHERE application_id = 'sched-app1' AND action = 'cancelled'"
            ).fetchone()[0],
            note="业务事实是 INSERT 的一行 cancelled 留痕（场次行是 UPDATE）。",
        ),
        "effect_complete_slot": Recipe(
            thread_id="sched-app1",
            seed=_seed_past_scheduled_slot,
            invoke=lambda conn: effect_complete_slot(
                conn,
                thread_id="sched-app1",
                business_key="sched-slot1:completed",
                slot_id="sched-slot1",
                target_status="completed",
                actor="sched-hr",
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM application_stage_history "
                "WHERE application_id = 'sched-app1' AND action = 'completed'"
            ).fetchone()[0],
            note="业务事实是 INSERT 的一行 completed 留痕；种子场次开始时刻已过。",
        ),
        # ⚠️ 节点名字面量与 interview-scheduling U3 计划原文不同：计划写的是
        # `effect_persist_draft`，那个字面量已被 M1 画像泳道占用（app/graph/nodes.py），
        # 重名会被本文件的 `test_no_duplicate_effect_node_name_literals` 判红，更隐蔽的是
        # `test_manifest_matches_the_source_tree` 会因重名误判"清单已覆盖"、让新节点
        # 躲过全部崩溃-恢复用例。⇒ 加域前缀，与 `effect_persist_letter` /
        # `effect_persist_prep_draft` 同一先例（见偏离登记 D-U3-8）。
        "effect_persist_invitation_draft": Recipe(
            thread_id=_INV_THREAD,
            seed=_seed_invitation_slot,
            invoke=lambda conn: effect_persist_invitation_draft(
                conn,
                thread_id=_INV_THREAD,
                business_key=_INV_RUN,
                slot_id=_INV_SLOT,
                template_version="v1",
                draft=_invitation_draft,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interview_invitation_draft WHERE slot_id = ?",
                (_INV_SLOT,),
            ).fetchone()[0],
            note=(
                "业务事实是 INSERT 的一行 interview_invitation_draft；同一事务里还有"
                "把 invitation_status 从 'none' UPDATE 成 'drafted' 的一步，但那一步"
                "是状态幂等、拿它当口径测不出双写。"
            ),
        ),
    }


def test_every_effect_node_has_a_recovery_recipe(tmp_path):
    """⭐ 防配方过期：清单里有、配方里没有 → 失败。与 Task 1 的守卫合起来，
    "新增节点却没写崩溃-恢复用例"在两个方向上都会变红。"""
    recipes = set(build_recipes(tmp_path))
    assert recipes == set(EFFECT_NODE_MANIFEST), (
        f"清单与配方不一致。缺配方：{sorted(set(EFFECT_NODE_MANIFEST) - recipes)}；"
        f"多配方：{sorted(recipes - set(EFFECT_NODE_MANIFEST))}"
    )


@pytest.mark.parametrize(
    "node_name", sorted(EFFECT_NODE_MANIFEST - _PERMANENTLY_UNIMPLEMENTED_STUBS)
)
def test_forced_interrupt_then_recovery_applies_the_effect_exactly_once(node_name, tmp_path):
    """
    ⭐⭐⭐ 本单元的主用例。对**每一个** effect_* 节点走同一条协议：

      种子数据 → 调用节点 → 在"业务写已入事务、effect_log 已 INSERT、
      commit 尚未落盘"的那一刻强制中断 → close()（未提交事务随连接丢弃，
      等价进程崩溃）→ 换全新连接（等价进程重启）确认**什么都没落盘** →
      用同一个 thread_id / business_key 重跑（等价 LangGraph 从节点开头
      整个重跑）→ 断言 effect_log 与业务事实**各恰好一份**。

    中间那一步"确认什么都没落盘"是关键：只断言最终一份，测不出"业务写落了
    盘、effect_log 没落"这个最坏情形——那种情形下重放要么撞唯一约束永久
    失败，要么静默做第二次副作用。
    """
    recipe = build_recipes(tmp_path)[node_name]
    db_path = str(tmp_path / f"{node_name}.db")

    conn = _open_crashing_connection(db_path)
    init_schema(conn)
    conn.commit()
    recipe.seed(conn)

    rows_before = recipe.count_business_rows(conn)

    with pytest.raises(RuntimeError, match="simulated crash"):
        recipe.invoke(conn)
    assert conn.crashed_once, (
        f"{node_name} 没有触发中断——说明它的 effect_log INSERT 之后没有 commit()，"
        "或者根本没走 idempotent_effect。这本身就是铁律 1 的红灯，⛔ 不要靠放宽"
        "断言绕过去"
    )
    conn.close()

    fresh = get_connection(db_path)
    assert (
        fresh.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
            (node_name, recipe.thread_id),
        ).fetchone()[0]
        == 0
    ), f"{node_name}：崩溃点之前 effect_log 不应有任何行落盘"
    assert recipe.count_business_rows(fresh) == rows_before, (
        f"{node_name}：崩溃点之前业务写不应落盘。落了就说明业务写与 effect_log "
        "不在同一个事务里——铁律 1 的直接违反，重放会撞唯一约束或静默重复副作用"
    )

    recipe.invoke(fresh)

    effect_rows = fresh.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
        (node_name, recipe.thread_id),
    ).fetchone()[0]
    assert effect_rows == 1, f"{node_name}：恢复重跑后 effect_log 应恰好 1 条，实得 {effect_rows}"
    assert recipe.count_business_rows(fresh) == rows_before + recipe.rows_per_effect, (
        f"{node_name}：恢复重跑后业务事实应恰好 {recipe.rows_per_effect} 份。{recipe.note}"
    )


@pytest.mark.parametrize(
    "node_name", sorted(EFFECT_NODE_MANIFEST - _PERMANENTLY_UNIMPLEMENTED_STUBS)
)
def test_effect_log_count_equals_business_rows_per_thread(node_name, tmp_path):
    """
    ⭐ 工程铁律 1 的 reviewer 判据逐字落成断言：
    「每个 effect_* 节点的 effect_log 条数与其业务表行数按 thread 恒等」。

    做法：连续调用三次（同一 thread_id、同一 business_key），第二三次会命中
    effect_log 短路。三次之后 effect_log 恒为 1，业务事实恒为 rows_per_effect。

    ⚠️ 这条**不是** Task 2 主用例的重复。主用例证明的是"崩溃点两侧的原子性"，
    这一条证明的是"稳态下两个计数不会漂移"。前者防丢失，后者防重复——
    铁律 1 的两个方向各需要一条。
    """
    recipe = build_recipes(tmp_path)[node_name]
    conn = get_connection(str(tmp_path / f"{node_name}-steady.db"))
    init_schema(conn)
    conn.commit()
    recipe.seed(conn)
    rows_before = recipe.count_business_rows(conn)

    for _ in range(3):
        recipe.invoke(conn)

    effect_rows = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
        (node_name, recipe.thread_id),
    ).fetchone()[0]
    assert effect_rows == 1, f"{node_name}：同一幂等键调 3 次，effect_log 应恒为 1"
    assert recipe.count_business_rows(conn) == rows_before + recipe.rows_per_effect, (
        f"{node_name}：同一幂等键调 3 次，业务事实应恒为 {recipe.rows_per_effect} 份。{recipe.note}"
    )


def test_llm_call_is_replayed_when_the_crash_lands_before_commit(tmp_path):
    """
    ⚠️ **观察项 O-1 的固化，不是红灯。**

    `effect_generate_and_persist_jd` 的副作用有两半：一半在事务里（写
    job_profile.profile_json），一半在事务外（一次真实、有成本的 LLM 调用）。
    崩溃落在提交之前时，事务那一半被正确丢弃，**LLM 那一半已经发生过了**，
    重放会再调一次——数据库状态依旧精确一次（这正是铁律 1 要保的），
    但**账单是两次**。

    这条用例把这个事实钉住，让它成为一个**已知且被度量**的性质，而不是
    某天有人看账单时才发现的意外。⛔ 本单元不修它（只新增 tests/）；
    要修得让 LLM 调用与写库拆成两个节点（先 compute 出文本并落草稿，
    再 effect 写正式列），那是另一个交付单元的事。
    """
    _CountingGateway.calls = 0
    recipe = build_recipes(tmp_path)["effect_generate_and_persist_jd"]
    db_path = str(tmp_path / "jd-cost.db")

    conn = _open_crashing_connection(db_path)
    init_schema(conn)
    conn.commit()
    recipe.seed(conn)

    with pytest.raises(RuntimeError, match="simulated crash"):
        recipe.invoke(conn)
    conn.close()

    fresh = get_connection(db_path)
    recipe.invoke(fresh)

    assert recipe.count_business_rows(fresh) == 1, "数据库状态必须精确一次"
    assert _CountingGateway.calls == 2, (
        "观察项 O-1：崩溃在提交之前时 LLM 会被调用两次（第一次的结果随事务丢弃）。"
        "若这里变成 1，说明有人把 LLM 调用挪到了事务之外或加了缓存——那是好事，"
        "请更新本用例并从计划的「红灯与观察项」里摘掉 O-1"
    )


def test_outbound_audit_has_no_sqlite_business_row_by_design(tmp_path):
    """
    ⚠️ **观察项 O-2 的固化。** `effect_record_outbound_audit` 的
    `rows_per_effect = 0` 是全表唯一的 0，必须有一条用例说明它是设计如此，
    否则下一个读这份注册表的人只会当成"这条配方没写完"。
    """
    recipe = build_recipes(tmp_path)["effect_record_outbound_audit"]
    assert recipe.rows_per_effect == 0
    assert "显式声明的例外" in recipe.note

    conn = get_connection(str(tmp_path / "audit.db"))
    init_schema(conn)
    conn.commit()
    recipe.invoke(conn)
    assert recipe.count_business_rows(conn) == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_record_outbound_audit'"
        ).fetchone()[0]
        == 1
    ), "SQLite 里没有业务行，但幂等保护必须照常生效——重复留痕由 effect_log 挡住"


def test_effect_send_verification_code_stub_always_raises_and_writes_nothing(tmp_path):
    """
    ⚠️ **`_PERMANENTLY_UNIMPLEMENTED_STUBS` 的固化，不是红灯。**

    `effect_send_verification_code`（OQ-10 未决前的占位桩）被排除在两条通用
    崩溃-恢复协议之外，理由见 `_PERMANENTLY_UNIMPLEMENTED_STUBS` 的注释。这条
    用例补上真正的行为断言：即便函数体永远 raise，装饰器本身仍要正确工作——
    异常原样透传给调用方（⛔ 不能被 idempotent_effect 吞掉、也不能被错误分类
    成"幂等命中"返回 None），且不留下任何 effect_log 行或业务行（⛔ 不能有
    部分写入残留）。
    """
    conn = get_connection(str(tmp_path / "stub.db"))
    init_schema(conn)
    conn.commit()

    with pytest.raises(NotImplementedError):
        effect_send_verification_code(conn, thread_id=_SESSION, business_key="1")

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_send_verification_code'"
        ).fetchone()[0]
        == 0
    ), "占位桩从未生效，不应留下任何 effect_log 行"
