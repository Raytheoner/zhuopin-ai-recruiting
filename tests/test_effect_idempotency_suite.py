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
import json
import pathlib
import sqlite3
from dataclasses import dataclass
from typing import Callable

import pytest

from app.agents.jd_agent import AI_LABEL_TEMPLATE
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
from app.outbound.messages import CandidateOutboundMessage
from app.schemas.job_profile import JobProfile
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
    }
)


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
_TS = "2026-09-08T02:00:00+00:00"
_LABEL = AI_LABEL_TEMPLATE.format(generated_at=_TS)
_AI_BODY = f"【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {_TS}。很遗憾……"


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
    }


def test_every_effect_node_has_a_recovery_recipe(tmp_path):
    """⭐ 防配方过期：清单里有、配方里没有 → 失败。与 Task 1 的守卫合起来，
    "新增节点却没写崩溃-恢复用例"在两个方向上都会变红。"""
    recipes = set(build_recipes(tmp_path))
    assert recipes == set(EFFECT_NODE_MANIFEST), (
        f"清单与配方不一致。缺配方：{sorted(set(EFFECT_NODE_MANIFEST) - recipes)}；"
        f"多配方：{sorted(recipes - set(EFFECT_NODE_MANIFEST))}"
    )


@pytest.mark.parametrize("node_name", sorted(EFFECT_NODE_MANIFEST))
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


@pytest.mark.parametrize("node_name", sorted(EFFECT_NODE_MANIFEST))
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
