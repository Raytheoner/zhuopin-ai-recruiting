"""effect 层的三组断言：单一事务管理者、恒等不变式、异常不留幂等记录。

这三组合起来就是工程铁律 1 在本服务里的可执行形式。⛔ 任何一条红了都不要改断言——
`.51` 现网 2026-08-10 与 08-12 各丢一轮 outbox，成因正是这三条里的第三条没被守住。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sqlite3

import pytest

from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import (
    EFFECT_NODE_TO_TABLE,
    effect_archive_message,
    effect_enqueue_task,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIAISON_ROOT = REPO_ROOT / "tools" / "liaison"

#: 允许调用 conn.commit()/rollback() 的**唯一**位置：schema 初始化。
#: 键是相对 tools/liaison 的路径，值是允许的函数名集合。
TRANSACTION_OWNER_ALLOWLIST = {"storage/db.py": {"init_schema"}}


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


class SpyConnection:
    """记账用的连接包装：数 commit/rollback 各被调了几次。

    ⛔ 不要用 unittest.mock 去 patch sqlite3.Connection.commit——它是 C 实现的
    只读属性，patch 不上（实测 AttributeError）。包一层才是可行的做法。
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.commits = 0
        self.rollbacks = 0

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self) -> None:
        self.commits += 1
        self._real.commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        self._real.rollback()


# ─────────────────────────────────────────────────────────────────────────
# 2.3 单向 import + 无第二个事务管理者
# ─────────────────────────────────────────────────────────────────────────


def test_effects_reuses_the_product_decorator_not_a_local_copy():
    """必须 import app.storage.idempotency，⛔ 不许在本目录抄一份幂等实现。

    抄一份的代价是：产品那边修了幂等的 bug（例如 2026-09-08 那次唯一键竞态短路），
    这边不会跟着修，而且没有任何症状。
    """
    source = (LIAISON_ROOT / "storage" / "effects.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.storage.idempotency" in imported


#: 依赖方向单向且**只有一条**（全局约束 4 原文）。黑名单挡不住
#: `from app.storage import db`（`module == "app.storage"`、`alias.name == "db"`，
#: 两边都不等于字面量 "app.storage.db"）这种拆分导入，所以改成白名单：
#: 除了这一条，`app.*` 的任何东西都不许进本目录。
ALLOWED_APP_IMPORTS = {"app.storage.idempotency"}


def test_liaison_does_not_import_product_db_layer():
    """只放行 `app.storage.idempotency` 这一条，`app.*` 的其余一切都不许进来。

    白名单而非黑名单：`from app.storage import db` 这种拆分导入方式，`module` 是
    `"app.storage"`、别名是 `"db"`，字面量黑名单 `!= "app.storage.db"` 两边都对不上、
    抓不到。白名单下，任何 `app.*` 引用只要拼不出 `app.storage.idempotency` 就直接
    判违规——`app.storage.db` 与 `app.graph.*` 自然都在其中，不需要再单独枚举。
    """
    for path in LIAISON_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "app" or node.module.startswith("app."):
                    if node.module in ALLOWED_APP_IMPORTS:
                        continue
                    for alias in node.names:
                        full = f"{node.module}.{alias.name}"
                        assert full in ALLOWED_APP_IMPORTS, (
                            f"{path} 引入了未放行的 {full}"
                        )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app" or alias.name.startswith("app."):
                        assert alias.name in ALLOWED_APP_IMPORTS, (
                            f"{path} 引入了未放行的 {alias.name}"
                        )


#: 视为"提交/回滚事务边界"的属性调用。`executescript` 单列进来是因为 sqlite3
#: 会在它之前隐式发一条 COMMIT，效果上它本身就是一次事务边界动作。
_TRANSACTION_BOUNDARY_ATTRS = ("commit", "rollback", "executescript")


# ─────────────────────────────────────────────────────────────────────────
# TD-18：`with <Call>:` 的判据细化
#
# 🔴 细化的**只有 `ast.Call` 这一格**。`ast.Name`（`with conn:`）与
# `ast.Attribute`（`with self._conn:`）**仍然无条件判违规**——⛔ 绝不许退回
# "只认裸局部名"，`with self._conn:` 的口子**没有症状**（effect_log 与业务表
# 静默劈叉，正是 .51 2026-08-10／08-12 丢 outbox 的失败模式）。
# `test_scanner_catches_with_self_conn_attribute` 是这条的证伪测试，必须常绿。
#
# 判定顺序（先命中先定）：
#   1. 名字命中 _CONNECTIONISH_TOKENS 或 _KNOWN_CONNECTION_CALLEES → 违规
#   2. 命中非 DB 白名单（逐条全名 or 模块族）→ 放行
#   3. 其余（名字判不出来的陌生被调用者）→ **仍判违规**
# 第 1 步排在第 2 步前面是刻意的：`contextlib.closing(conn)` 属于
# `contextlib.*` 却货真价实地管着一个连接，⛔ 不许被模块族白名单捞走。
#
# ⚠️ 第 3 步是相对 TD-18 opener 字面要求（"只在名字含 conn/... 时判违规"）
# 的一处**收紧偏离**，刻意为之：那样写会让 `with pool.acquire():`
# 这类名字里没有词根的陌生连接**静默通过**，而 tech-debt TD-18 原文
# 的落款是"宁可留误报，⛔ 不许退回窄化"。这里两头都要：
# opener 逐条点名的 open / contextlib.* / tempfile.* / suppress /
# TemporaryDirectory 全部放行（TD-18 的真实痛点已消），陌生被调用者仍然会红
# ——而那个失败是**响亮的**（一条可见的测试失败 + 一行白名单即可解决），
# 反过来漏判则**没有症状**。
# ─────────────────────────────────────────────────────────────────────────

#: 被调用者名字里出现这些词根（不分大小写）即判违规：`get_connection()`、
#: `engine.begin()`、`new_transaction()`、`db.connect()` 都会被抓。
_CONNECTIONISH_TOKENS = ("conn", "connect", "transaction", "begin")

#: 已知的连接／事务符号：名字里没有上面那些词根，但确实管着一个连接或事务。
#: ⛔ 只增不删——删一条就是打开一个无症状的口子。
_KNOWN_CONNECTION_CALLEES = frozenset(
    {
        "closing",
        "contextlib.closing",
        "atomic",
        "savepoint",
        "cursor",
        "sqlite3.Connection",
        "session",
        "Session",
    }
)

#: 已知与数据库无关的上下文管理器：逐条全名。
#: ⛔ 往这份名单里加东西之前先确认：它绝不可能是一个 sqlite3 连接。
_NON_DB_CONTEXT_CALLEES = frozenset(
    {
        "open",
        "os.fdopen",
        "io.open",
        "suppress",
        "contextlib.suppress",
        "NamedTemporaryFile",
        "TemporaryDirectory",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryDirectory",
    }
)

#: 整个模块族都与数据库无关，`<module>.<任何东西>(...)` 一律放行。
#: 例外由上面第 1 步兜住（`contextlib.closing` 先被判违规，走不到这里）。
_NON_DB_CONTEXT_MODULES = ("contextlib.", "tempfile.")


def _callee_name(func: ast.AST) -> str | None:
    # 取点号全名（`os.fdopen` 而不是只看 `fdopen`），拿不到就返回 None
    # ——⛔ None 一律按"抓"处理，不确定的时候宁可误报。
    try:
        return ast.unparse(func)
    except Exception:
        return None


def _is_connection_like_call(func: ast.AST) -> bool:
    name = _callee_name(func)
    if name is None:
        return True  # 判不出来就当成连接：宁可误报，不许静默放行
    if name in _KNOWN_CONNECTION_CALLEES:
        return True
    return any(token in name.lower() for token in _CONNECTIONISH_TOKENS)


def _is_known_non_db_context(func: ast.AST) -> bool:
    name = _callee_name(func)
    if name is None:
        return False
    if name in _NON_DB_CONTEXT_CALLEES:
        return True
    return name.startswith(_NON_DB_CONTEXT_MODULES)


def _with_call_is_a_violation(func: ast.AST) -> bool:
    """`with <callee>(...)` 是否算"第二个事务管理者"。见上方判定顺序。"""
    if _is_connection_like_call(func):
        return True
    return not _is_known_non_db_context(func)


def _scan_transaction_violations(
    source: str, label: str, allowlist: set[str]
) -> list[str]:
    """在一份源码里找"第二个事务管理者"的静态证据，归属到最近的函数作用域。

    覆盖面（Important 1 修复前的盲区）：
    - `async def`（第 3–5 章的企微 webhook 处理函数就是协程，原实现只认 `FunctionDef`
      会在最需要它的地方失明）；
    - 模块作用域的裸调用（不在任何函数体里，原实现整段扫不到，归属记为 `"<module>"`）；
    - `conn.executescript(...)`（隐式先发一条 COMMIT，是货真价实的事务边界动作，
      原实现只认字面 `.commit()` / `.rollback()`）；
    - `with X:`，`X` 是裸名字（sqlite3 连接的上下文管理器退出时会 commit，
      这是另一种"隐式提交"，原实现完全没检查 `with`）。

    `label` 只用于拼错误信息，可以是相对路径（真实扫描）也可以是伪造的文件名
    （证伪测试传入内联字符串时用）。
    """
    offenders: list[str] = []
    tree = ast.parse(source, filename=label)

    def scope_name(func_stack: list[ast.AST]) -> str:
        return func_stack[-1].name if func_stack else "<module>"

    def visit(node: ast.AST, func_stack: list[ast.AST]) -> None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TRANSACTION_BOUNDARY_ATTRS
        ):
            scope = scope_name(func_stack)
            if scope not in allowlist:
                offenders.append(f"{label}::{scope} 调了 {node.func.attr}()")
        if isinstance(node, (ast.With, ast.AsyncWith)):
            keyword = "async with" if isinstance(node, ast.AsyncWith) else "with"
            for item in node.items:
                expr = item.context_expr
                # `ast.Name`（裸局部名，如 `with conn:`）、`ast.Attribute`
                # （挂在对象上的连接，如 `with self.conn:` / `with self._conn:`——
                # 第 3–5 章的服务对象几乎必然把连接挂成属性，只认 `ast.Name`
                # 在那里是瞎的）、以及 `ast.Call`（如 `with get_connection() as c:`，
                # Important 1 原文允许不处理，但覆盖成本低就顺手覆盖了）都要抓。
                if isinstance(expr, ast.Call) and not _with_call_is_a_violation(expr.func):
                    continue
                if isinstance(expr, (ast.Name, ast.Attribute, ast.Call)):
                    scope = scope_name(func_stack)
                    if scope not in allowlist:
                        try:
                            expr_repr = ast.unparse(expr)
                        except Exception:
                            expr_repr = getattr(expr, "attr", getattr(expr, "id", "<expr>"))
                        offenders.append(
                            f"{label}::{scope} 用 `{keyword} {expr_repr}:` "
                            "隐式提交（sqlite3 连接的上下文管理器退出即 commit）"
                        )
        next_stack = func_stack
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            next_stack = func_stack + [node]
        for child in ast.iter_child_nodes(node):
            visit(child, next_stack)

    visit(tree, [])
    return offenders


def test_no_second_transaction_manager_in_source():
    """静态判据：非测试代码里 commit/rollback/executescript/隐式 with-提交的调用点，
    只允许出现在白名单里。

    事务的主人只有一个——`idempotent_effect`。任何别的地方触发一次提交边界，都可能把
    一个只写了一半的业务事务提交下去，让 effect_log 与业务表的条数当场对不上，
    **而且不报错**。这条断言是那道闸。
    """
    offenders = []
    for path in sorted(LIAISON_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        rel = path.relative_to(LIAISON_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        offenders.extend(
            _scan_transaction_violations(
                source, rel, TRANSACTION_OWNER_ALLOWLIST.get(rel, set())
            )
        )
    assert offenders == [], "发现白名单之外的事务管理者：" + "; ".join(offenders)


# ─────────────────────────────────────────────────────────────────────────
# 证伪：证明上面的扫描器改完之后真的会红，而不是看起来更严格实际没变化。
# ⛔ 不往 app/ 或任何真实文件里写违规代码——全部在字符串里内联构造。
# ─────────────────────────────────────────────────────────────────────────


def test_scanner_catches_async_def_with_implicit_commit_via_with():
    """Important 1 的失效场景本尊：`async def` 里一个裸 `with conn:`。

    reviewer 举的例子——第 3 章加一个 `async def handle_message(...)`，结尾
    `with conn: conn.execute(...)`——旧实现（只认 `FunctionDef`、只认字面
    `.commit()`/`.rollback()`）对这段代码是瞎的。这条测试直接把这段代码喂给
    扫描器，证明新实现看得见。
    """
    source = """
async def handle_message(conn, payload):
    conn.execute("insert into liaison_message values (?)", (payload,))
    with conn:
        conn.execute("insert into liaison_task values (?)", (payload,))
"""
    offenders = _scan_transaction_violations(source, "fake_handler.py", allowlist=set())
    assert offenders, "扫描器应该报告 async def 内 `with conn:` 的隐式提交"
    assert any("handle_message" in o for o in offenders), offenders


def test_scanner_catches_module_scope_commit():
    """裸的模块作用域调用——不在任何函数体里，旧实现的 `ast.walk(func)` 结构
    上就够不到（它只在已经找到的 `FunctionDef` 内部找），这条证明新实现能。
    """
    source = """
import sqlite3

conn = sqlite3.connect(":memory:")
conn.execute("insert into x values (1)")
conn.commit()
"""
    offenders = _scan_transaction_violations(source, "fake_module_level.py", allowlist=set())
    assert offenders, "扫描器应该报告模块作用域的 commit()"
    assert any("<module>" in o for o in offenders), offenders


def test_scanner_catches_executescript_as_a_commit_boundary():
    """`executescript` 会先隐式发一条 COMMIT，效果上和显式 `.commit()` 一样是
    事务边界，旧实现的属性白名单 `("commit", "rollback")` 漏了它。
    """
    source = """
def bulk_write(conn):
    conn.executescript("INSERT INTO x VALUES (1); INSERT INTO x VALUES (2);")
"""
    offenders = _scan_transaction_violations(source, "fake_bulk.py", allowlist=set())
    assert offenders, "扫描器应该把 executescript 当成事务边界动作"
    assert any("executescript" in o for o in offenders), offenders


def test_scanner_catches_with_self_conn_attribute():
    """终审四格表第 1 行：`with self.conn:`（`ast.Attribute`）。

    这是 Important 1 要防的那个失效形态**最可能出现的一种**——第 3–5 章会把
    连接挂在服务对象上（`self.conn` / `self._conn`），比裸局部名自然得多。
    旧实现 `isinstance(item.context_expr, ast.Name)` 对 `ast.Attribute` 是瞎的，
    这条把它变成会红的测试。
    """
    source = """
class Service:
    def handle(self, payload):
        self.conn.execute("insert into liaison_message values (?)", (payload,))
        with self.conn:
            self.conn.execute("insert into liaison_task values (?)", (payload,))
"""
    offenders = _scan_transaction_violations(source, "fake_service.py", allowlist=set())
    assert offenders, "扫描器应该报告 `with self.conn:` 的隐式提交"
    assert any("handle" in o and "self.conn" in o for o in offenders), offenders


def test_scanner_catches_with_call_expression():
    """终审四格表第 2 行：`with get_connection() as c:`（`ast.Call`）。

    Important 1 原文允许这一格不处理，但覆盖成本低（`ast.Call` 与
    `ast.Name`/`ast.Attribute` 一样加进 isinstance 元组即可），顺手覆盖了。
    """
    source = """
def handle(payload):
    with get_connection() as c:
        c.execute("insert into liaison_task values (?)", (payload,))
"""
    offenders = _scan_transaction_violations(source, "fake_call.py", allowlist=set())
    assert offenders, "扫描器应该报告 `with get_connection() as c:` 的隐式提交"
    assert any("handle" in o for o in offenders), offenders


def test_scanner_catches_async_with_bare_name():
    """终审四格表第 3 行：`async with conn:`——`ast.AsyncWith` 节点本身，
    旧实现只遍历 `ast.With`，对协程里的 `async with` 结构上就够不到。
    """
    source = """
async def handle_message(conn, payload):
    async with conn:
        conn.execute("insert into liaison_task values (?)", (payload,))
"""
    offenders = _scan_transaction_violations(source, "fake_async_with.py", allowlist=set())
    assert offenders, "扫描器应该报告 `async with conn:` 的隐式提交"
    assert any("async with conn" in o for o in offenders), offenders


def test_scanner_still_catches_the_control_case_bare_with_name():
    """终审四格表第 4 行（对照组）：`with conn:`（裸局部名）。

    旧实现本就能抓到这个形态，改动扩大了覆盖面之后不能让这个对照组退化。
    """
    source = """
def handle(conn, payload):
    with conn:
        conn.execute("insert into liaison_task values (?)", (payload,))
"""
    offenders = _scan_transaction_violations(source, "fake_control.py", allowlist=set())
    assert offenders, "扫描器必须仍然能抓到裸局部名的 `with conn:`"
    assert any("with conn" in o for o in offenders), offenders


def test_scanner_still_allows_the_whitelisted_init_schema_shape():
    """新扫描器更严格了，但不能误伤真实白名单：`init_schema` 的
    `executescript` + `commit` 组合必须仍然放行。"""
    source = """
def init_schema(conn):
    conn.executescript("CREATE TABLE x(id)")
    conn.commit()
"""
    offenders = _scan_transaction_violations(
        source, "storage/db.py", allowlist={"init_schema"}
    )
    assert offenders == [], offenders


def test_scanner_matches_reality_for_the_real_db_module():
    """在真实的 `storage/db.py` 文件上跑一遍新扫描器，确认它按白名单放行
    `init_schema`、且不误报文件里的其他任何函数。这是"改完确认 db.py 不被
    误报"的可执行版本，不是口头保证。
    """
    path = LIAISON_ROOT / "storage" / "db.py"
    source = path.read_text(encoding="utf-8")
    offenders = _scan_transaction_violations(
        source, "storage/db.py", TRANSACTION_OWNER_ALLOWLIST["storage/db.py"]
    )
    assert offenders == [], offenders


def test_no_checkpointer_or_langgraph_in_liaison():
    """design D6：本服务不引入 LangGraph，因此连接上不会出现第二个事务管理者。

    checkpointer 与 effect 层共用连接正是 2026-08-13 那份 findings 的事故成因。
    这条断言让"我们没引入"从一句话变成一个会红的测试。
    """
    for path in LIAISON_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "langgraph" not in text.lower(), f"{path} 提到了 langgraph"
        assert "SqliteSaver" not in text, f"{path} 提到了 SqliteSaver"


def test_successful_effect_commits_exactly_once(conn):
    """运行期判据：一次成功的 effect = 恰好 1 次 commit、0 次 rollback。"""
    spy = SpyConnection(conn)
    effect_archive_message(
        spy,
        thread_id="u1",
        business_key="m1",
        sender_userid="u1",
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content="hello",
    )
    assert (spy.commits, spy.rollbacks) == (1, 0)


def test_duplicate_effect_neither_commits_nor_rolls_back(conn):
    """重复投递被静默跳过：不写、不提交、不回滚，返回 None。

    对应 liaison-message-archive「同一消息被投递两次」。
    """
    kwargs = dict(
        thread_id="u1",
        business_key="m1",
        sender_userid="u1",
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content="hello",
    )
    effect_archive_message(conn, **kwargs)
    spy = SpyConnection(conn)
    assert effect_archive_message(spy, **kwargs) is None
    assert (spy.commits, spy.rollbacks) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_effect_key_format_matches_the_ironclad_rule(conn):
    """幂等键 = {thread_id}:{node_name}:{business_key}，逐字符对。

    thread_id ＝私聊 userid／群聊 chatid；business_key ＝企微 msgid。
    """
    effect_archive_message(
        conn,
        thread_id="chat-42",
        business_key="msg-7",
        sender_userid="u1",
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content="x",
    )
    keys = [row[0] for row in conn.execute("SELECT effect_key FROM effect_log")]
    assert keys == ["chat-42:effect_archive_message:msg-7"]


def test_effect_node_to_table_matches_reality(conn):
    """`EFFECT_NODE_TO_TABLE` 自己的注释写着"漏登记会让那个 effect 悄悄逃过恒等
    检查"——风险点了名却一直没有守卫。Task 4 会按这个映射遍历去核对
    `effect_log` 条数与业务表行数的恒等式，映射的键一旦跟模块里真实存在的
    `effect_*` 函数漂移，Task 4 就会对漂移出去的那个 effect 悄悄不做恒等检查，
    且没有任何报错。

    这条测试把"漂移"变成两件可机器判定的事：键集合与模块里真实的 `effect_*`
    函数名集合逐一对齐；每个映射到的表名在库里真实存在。
    """
    import tools.liaison.storage.effects as effects_module

    effect_function_names = {
        name
        for name, obj in vars(effects_module).items()
        if name.startswith("effect_") and callable(obj)
    }
    assert set(EFFECT_NODE_TO_TABLE.keys()) == effect_function_names, (
        "EFFECT_NODE_TO_TABLE 的键与模块里真实的 effect_* 函数对不上："
        f"映射={sorted(EFFECT_NODE_TO_TABLE.keys())} 实际={sorted(effect_function_names)}"
    )

    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for node_name, table_name in EFFECT_NODE_TO_TABLE.items():
        assert table_name in existing_tables, (
            f"{node_name} 映射到不存在的表 {table_name}；实际表={sorted(existing_tables)}"
        )


# ─────────────────────────────────────────────────────────────────────────
# Important 2（终审必修）：EFFECT_NODE_TO_TABLE 必须核对
# "声称写的表" == "源码里真的 INSERT INTO 的表"，不止核对键集合与表存在性。
# ─────────────────────────────────────────────────────────────────────────

_INSERT_INTO_RE = re.compile(r"(?is)insert\s+into\s+([A-Za-z_][A-Za-z0-9_]*)")


def _decorator_node_name(decorator: ast.expr) -> str | None:
    """从 `@idempotent_effect("effect_xxx")` 这类装饰器里取出字符串字面量参数。

    只认『被调用的名字是 idempotent_effect』这一种形态；起别名导入
    （`from ... import idempotent_effect as ie`）不在本轮范围内，效果层
    目前也没有这么写。
    """
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name != "idempotent_effect":
        return None
    if not decorator.args:
        return None
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _find_idempotent_effects_in_tree(tree: ast.AST) -> dict:
    """扫一份已解析的 AST，收集所有被 `@idempotent_effect("...")` 装饰的函数，
    键是装饰器里的 node_name 字面量，值是函数体本身（`FunctionDef` 或
    `AsyncFunctionDef`）。

    仓库级扫描时按这个函数逐文件调用再合并结果，就把"后续章节把 effect_*
    定义在别的模块"这个前瞻缺口也堵住了——不再局限于 `storage/effects.py`。
    """
    found: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                node_name = _decorator_node_name(decorator)
                if node_name:
                    found[node_name] = node
    return found


def _insert_table_from_function(node: ast.AST):
    """在函数体内找第一个 `xxx.execute("INSERT INTO <table> ...")`，抽出
    `<table>`。找不到就返回 None（调用方判定为无法核对，同样计入违规）。
    """
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "execute"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ):
            match = _INSERT_INTO_RE.search(child.args[0].value)
            if match:
                return match.group(1)
    return None


def _validate_node_to_table_mapping(mapping: dict, effects_by_node: dict) -> list:
    """核对 mapping 里每一项，是否等于该 effect 函数体里真实 INSERT 的表。

    这是 Important 2 的核心：`EFFECT_NODE_TO_TABLE` 曾经只被『键集合对不对』
    『表存不存在』两件事守着，从未核对过『这一项对不对』。终审实测把两个
    条目对调（`effect_archive_message→liaison_task`、
    `effect_enqueue_task→liaison_message`）后，旧的两条检查依然全绿——映射
    一旦写错或复制粘贴错位，出问题时不是报错，是『悄悄不检查』。
    """
    mismatches = []
    for node_name, declared_table in mapping.items():
        func_node = effects_by_node.get(node_name)
        if func_node is None:
            mismatches.append(f"{node_name}：源码里找不到对应的 @idempotent_effect 函数")
            continue
        actual_table = _insert_table_from_function(func_node)
        if actual_table is None:
            mismatches.append(f"{node_name}：函数体里没找到 INSERT INTO，无法核对")
            continue
        if actual_table != declared_table:
            mismatches.append(
                f"{node_name}：映射声称写 {declared_table}，源码实际写的是 {actual_table}"
            )
    return mismatches


def test_effect_node_to_table_matches_the_insert_target_repo_wide():
    """仓库级版本：不止扫 `storage/effects.py`，扫 `tools/liaison` 下所有被
    `@idempotent_effect` 装饰的函数（测试目录除外）。这样第 3/4/5 章把新的
    effect 定义在别的模块时，键要跟得上（`test_effect_node_to_table_matches_reality`
    已守）、值（映射的表名对不对）也一并被这条守住，不必等第 3 章重新补一遍。
    """
    effects_by_node: dict = {}
    for path in sorted(LIAISON_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        effects_by_node.update(_find_idempotent_effects_in_tree(tree))

    mismatches = _validate_node_to_table_mapping(EFFECT_NODE_TO_TABLE, effects_by_node)
    assert mismatches == [], "EFFECT_NODE_TO_TABLE 与源码实际写入的表不一致：" + "; ".join(
        mismatches
    )


def test_validate_node_to_table_mapping_catches_a_swapped_mapping():
    """证伪：把两个条目对调后喂给同一个校验函数，必须报错。

    终审实测过这个具体的对调，在旧的两条检查（键集合、表存在性）下依然全绿。
    这条用真实的 `effects.py` 源码 + 一份人为写错的映射，证明新校验函数真的
    会抓到。⛔ 不改 `effects.py` 本体，只改喂进去的映射（monkeypatch 式构造，
    不碰生产代码）。
    """
    tree = ast.parse((LIAISON_ROOT / "storage" / "effects.py").read_text(encoding="utf-8"))
    effects_by_node = _find_idempotent_effects_in_tree(tree)

    swapped_mapping = {
        "effect_archive_message": "liaison_task",
        "effect_enqueue_task": "liaison_message",
    }
    mismatches = _validate_node_to_table_mapping(swapped_mapping, effects_by_node)
    assert mismatches, "对调后的映射必须被校验函数抓到，而不是悄悄通过"
    assert any("effect_archive_message" in m for m in mismatches), mismatches
    assert any("effect_enqueue_task" in m for m in mismatches), mismatches


def test_validate_node_to_table_mapping_on_synthetic_source():
    """再来一份完全内联构造的最小样本，跟真实 `effects.py` 解耦，防止将来
    有人改了 `effects.py` 的写法导致上面两条测试的『真实性』打折扣。
    """
    source = """
from app.storage.idempotency import idempotent_effect


@idempotent_effect("effect_write_widget")
def effect_write_widget(conn, *, thread_id, business_key):
    conn.execute("INSERT INTO widget (id) VALUES (?)", (business_key,))
    return business_key
"""
    tree = ast.parse(source, filename="fake_widget.py")
    effects_by_node = _find_idempotent_effects_in_tree(tree)

    correct_mapping = {"effect_write_widget": "widget"}
    assert _validate_node_to_table_mapping(correct_mapping, effects_by_node) == []

    wrong_mapping = {"effect_write_widget": "gadget"}
    mismatches = _validate_node_to_table_mapping(wrong_mapping, effects_by_node)
    assert mismatches, "映射写错表名必须被抓到"
    assert "gadget" in mismatches[0] and "widget" in mismatches[0]


# ─────────────────────────────────────────────────────────────────────────
# 2.4 恒等不变式脚手架
# ─────────────────────────────────────────────────────────────────────────


def assert_effect_log_identity(conn: sqlite3.Connection) -> None:
    """铁律 1 的 reviewer 判据，做成可复用的断言。

    对 EFFECT_NODE_TO_TABLE 里每个节点：
      按 thread_id 分组，该节点的 effect_log 条数 == 其业务表的行数。

    ⛔ **不要退化成"总数相等"**——总数相等可以由"A 会话多一行、B 会话少一行"凑出来，
    那恰恰是最需要被抓住的那种错。按 thread_id 逐组比才有意义。

    第 4／5／7 章的测试应当直接 import 本函数在各自的场景末尾调一次。

    ⚠️ **隐含前提（终审 Minor 4 补记，第 7 章落地前必读）**：本断言成立的前提是
    业务表**只增不删**。第 7 章的 180 天留存期清理一旦落地，清理会让业务表的
    行数低于 effect_log 里同一 thread 的记录数，这条断言会**因为一个完全正当的
    理由变红**。

    到那时正确的应对是下面两选一，⛔ 二选一之外的任何"修法"都不允许：
      1. 留存期清理与对应的 effect_log 行在同一个事务里连带删除；或
      2. 把本断言的比对范围限定在"尚未被清理"的 thread 集合内。

    真正的危险不是测试变红，而是那时候最顺手的"修法"是去削弱这条断言本身
    ——它是铁律 1 唯一的机器守卫，削弱它等于把守卫拆了。**⛔ 明确写死：
    不许把它改成总数比较、不许削弱成"约等于"。**
    """
    # 冲突 A / 方案 2（本函数 docstring 二选一的第 2 条）：留存期清理会让被清理过的
    # thread 的业务表行数低于 effect_log 行数，那是一个**正当**的差额。
    # 这里把它们排除出严格恒等的比对范围——它们由 test_retention.py 的
    # `assert_retention_accounting` 用一条**更强**的记账等式单独盯住：
    #     归档 effect 行数 == 台账行数 + 清理 effect 行数
    # ⛔ 排除的依据必须是"留下过清理记录"这个事实，不是任何形式的白名单。
    from tools.liaison.retention import RETENTION_DELETE_NODE

    cleaned_threads = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT thread_id FROM effect_log WHERE node_name = ?",
            (RETENTION_DELETE_NODE,),
        ).fetchall()
    }

    for node_name, table in EFFECT_NODE_TO_TABLE.items():
        effect_counts = dict(
            conn.execute(
                "SELECT thread_id, COUNT(*) FROM effect_log WHERE node_name = ? "
                "GROUP BY thread_id",
                (node_name,),
            ).fetchall()
        )
        business_counts = dict(
            conn.execute(f"SELECT thread_id, COUNT(*) FROM {table} GROUP BY thread_id").fetchall()
        )
        for thread_id in cleaned_threads:
            effect_counts.pop(thread_id, None)
            business_counts.pop(thread_id, None)
        assert effect_counts == business_counts, (
            f"恒等不变式破裂：节点 {node_name} 的 effect_log 分组计数 {effect_counts} "
            f"≠ 业务表 {table} 的分组计数 {business_counts}"
        )


def test_identity_assertion_still_catches_a_break_in_an_uncleaned_thread(conn):
    """冲突 A 的证伪：收窄比对范围之后，**没被清理过**的 thread 仍必须被抓住。

    ⛔ 不要删这条。方案 2 的风险正是"排除范围"悄悄扩大到把所有 thread 都排掉，
    那时断言永远为真——比没有断言更糟。
    """
    _process(conn, thread_id="u1", msgid="m1")
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('sneaky', 'u1', 'u1', 't', 'text')"
    )
    conn.commit()
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def test_identity_assertion_excludes_only_threads_that_were_actually_cleaned(conn):
    """被排除的必须**恰好**是留下过清理记录的那些 thread，一个不多。"""
    _process(conn, thread_id="u1", msgid="m1")
    _process(conn, thread_id="u2", msgid="m2")
    # u1 上伪造一条"清理过"的痕迹，并把它的台账行删掉——u1 应被排除。
    conn.execute("DELETE FROM liaison_task WHERE thread_id = 'u1'")
    conn.execute("DELETE FROM liaison_message WHERE thread_id = 'u1'")
    conn.execute(
        "INSERT INTO effect_log VALUES ('u1:effect_delete_expired_message:m1', 'u1', "
        "'effect_delete_expired_message', 'm1', datetime('now'))"
    )
    conn.commit()
    assert_effect_log_identity(conn)  # u1 被排除，u2 仍严格恒等 ⇒ 通过
    # u2 上制造一处破裂，必须仍然被抓住
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('sneaky2', 'u2', 'u2', 't', 'text')"
    )
    conn.commit()
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def _process(conn, *, thread_id, msgid, content="hello"):
    """把一条消息走完本章范围内的两个 effect：归档 + 入队。

    顺序是钉死的：先台账后队列。队列条目的 FK 指向台账，反过来会撞 FK。
    """
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content=content,
    )
    effect_enqueue_task(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at="2026-09-08T10:00:00+08:00",
        summary=content[:40],
    )


def test_identity_holds_on_empty_database(conn):
    """空库也必须恒等（两边都是空 dict）。边界条件，别跳过。"""
    assert_effect_log_identity(conn)


def test_identity_holds_across_a_batch_of_messages(conn):
    """处理任意一批消息后核对（liaison-task-queue「幂等记录与条目数恒等」）。"""
    for thread_id, msgids in (("u1", ["m1", "m2", "m3"]), ("chat-9", ["m4", "m5"])):
        for msgid in msgids:
            _process(conn, thread_id=thread_id, msgid=msgid)
    assert_effect_log_identity(conn)
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 10


def test_identity_holds_after_replaying_the_whole_batch(conn):
    """重连后通道重投一批历史消息：归档与队列均无新增，恒等仍成立。

    对应 liaison-message-archive「重连后重投历史消息」。
    """
    batch = [("u1", "m1"), ("u1", "m2"), ("chat-9", "m3")]
    for thread_id, msgid in batch:
        _process(conn, thread_id=thread_id, msgid=msgid)
    before = (
        conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0],
    )
    for _ in range(3):  # 重投三遍
        for thread_id, msgid in batch:
            _process(conn, thread_id=thread_id, msgid=msgid)
    after = (
        conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0],
    )
    assert after == before == (3, 3, 6)
    assert_effect_log_identity(conn)


def test_identity_scaffold_actually_catches_a_break(conn):
    """脚手架本身要有牙——绕过 effect 直接写业务表，断言必须红。

    ⛔ 不要删这条。一个永远为真的断言比没有断言更糟：它会让 reviewer 以为
    这条不变式被守住了。这条是对断言本身的证伪测试。
    """
    _process(conn, thread_id="u1", msgid="m1")
    assert_effect_log_identity(conn)
    # 绕过 effect 层偷偷插一行业务数据
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('sneaky', 'u1', 'u1', 't', 'text')"
    )
    conn.commit()
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def test_identity_is_per_thread_not_global(conn):
    """跨会话的数量互相抵消也必须被抓住。

    构造：u1 的业务表多一行、u2 的 effect_log 多一行，总数相等但分组不等。
    如果断言写成"总数相等"，这条会绿——那就是它存在的意义。
    """
    _process(conn, thread_id="u1", msgid="m1")
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('extra', 'u1', 'u1', 't', 'text')"
    )
    conn.execute(
        "INSERT INTO effect_log VALUES ('u2:effect_archive_message:ghost', 'u2', "
        "'effect_archive_message', 'ghost', datetime('now'))"
    )
    conn.commit()
    total_effect = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_archive_message'"
    ).fetchone()[0]
    total_business = conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0]
    assert total_effect == total_business == 2  # 总数相等，但……
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def test_nasty_content_does_not_break_the_queue_structure(conn):
    """含竖线/换行/控制字符的内容入队后，条目字段不错位、不串行。

    对应 liaison-task-queue「内容含竖线」「内容含换行」两个场景在队列真身上的形态。
    """
    nasty = "标题|列二|列三\n---|---|---\n值\t制表\x00空字节"
    _process(conn, thread_id="u1", msgid="m1", content=nasty)
    _process(conn, thread_id="u1", msgid="m2", content="normal")
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 2
    assert (
        conn.execute("SELECT content FROM liaison_message WHERE msgid = 'm1'").fetchone()[0]
        == nasty
    )
    assert_effect_log_identity(conn)


def test_concurrent_style_interleaved_writes_do_not_overwrite(conn):
    """多条消息交错入队，每条各自产生一条条目，无覆盖无丢失。

    对应 liaison-task-queue「并发写入不互相覆盖」。本服务是单进程单连接
    （design D6/D12），所以这里模拟的是**交错的调用顺序**而非真并发线程；
    真并发不在本服务的模型内，⛔ 不要为了"更真"而引入线程池——那会引入一个
    本服务不存在的假设。结构上的防护是 liaison_task.msgid 的 UNIQUE 约束。
    """
    msgids = [f"m{i}" for i in range(10)]
    for msgid in msgids:
        effect_archive_message(
            conn,
            thread_id="u1",
            business_key=msgid,
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
            msgtype="text",
            content=f"body-{msgid}",
        )
    for msgid in reversed(msgids):  # 入队顺序与归档顺序刻意相反
        effect_enqueue_task(
            conn,
            thread_id="u1",
            business_key=msgid,
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
        )
    stored = [row[0] for row in conn.execute("SELECT msgid FROM liaison_task ORDER BY msgid")]
    assert stored == sorted(msgids)
    assert_effect_log_identity(conn)


# ─────────────────────────────────────────────────────────────────────────
# 2.5 业务写抛异常 ⇒ effect_log 不留记录 ⇒ 重跑会重新尝试
# ─────────────────────────────────────────────────────────────────────────


def test_no_effect_log_when_business_write_raises(conn):
    """业务写失败时不留下幂等记录（liaison-task-queue 同名场景）。

    ⛔ 这条红了**绝不能**靠"先写 effect_log 再写业务"来"修"——那正是事故本身。
    """
    from app.storage.idempotency import idempotent_effect

    @idempotent_effect("effect_archive_message")
    def failing_archive(c, *, thread_id, business_key):
        c.execute(
            "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
            "VALUES (?, ?, 'u1', 't', 'text')",
            (business_key, thread_id),
        )
        raise RuntimeError("模拟业务写之后、提交之前的失败")

    with pytest.raises(RuntimeError, match="模拟业务写"):
        failing_archive(conn, thread_id="u1", business_key="m1")

    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_failed_effect_is_retried_on_next_run(conn):
    """重新处理同一条消息时该动作会被重新尝试，并且这次成功。

    这是上一条的另一半：不留幂等记录**的目的**就是让重试可能发生。
    只断言"没留记录"而不断言"重试真的成功了"，等于只测了一半。
    """
    from app.storage.idempotency import idempotent_effect

    attempts = {"n": 0}

    @idempotent_effect("effect_archive_message")
    def flaky_archive(c, *, thread_id, business_key):
        attempts["n"] += 1
        c.execute(
            "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
            "VALUES (?, ?, 'u1', 't', 'text')",
            (business_key, thread_id),
        )
        if attempts["n"] == 1:
            raise RuntimeError("第一次失败")
        return business_key

    with pytest.raises(RuntimeError):
        flaky_archive(conn, thread_id="u1", business_key="m1")
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0

    assert flaky_archive(conn, thread_id="u1", business_key="m1") == "m1"
    assert attempts["n"] == 2
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1
    assert_effect_log_identity(conn)


def test_failed_effect_rolls_back_exactly_once_and_never_commits(conn):
    """运行期判据：失败路径 = 0 次 commit、1 次 rollback。

    0 次 commit 是关键——只要失败路径上有过一次 commit，那半截业务写就落盘了，
    而 effect_log 是空的，恒等式当场破且无症状。
    """
    from app.storage.idempotency import idempotent_effect

    @idempotent_effect("effect_enqueue_task")
    def failing_enqueue(c, *, thread_id, business_key):
        raise RuntimeError("boom")

    spy = SpyConnection(conn)
    with pytest.raises(RuntimeError):
        failing_enqueue(spy, thread_id="u1", business_key="m1")
    assert (spy.commits, spy.rollbacks) == (0, 1)


def test_constraint_violation_in_business_write_leaves_no_trace(conn):
    """真实失败形态而非人造异常：FK 违约（队列条目指向不存在的消息）。

    比 `raise RuntimeError` 更接近现网会发生的事——顺序写反了就是这个报错。
    """
    with pytest.raises(sqlite3.IntegrityError):
        effect_enqueue_task(
            conn,
            thread_id="u1",
            business_key="never-archived",
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
        )
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_partial_batch_failure_keeps_identity(conn):
    """一批消息中间有一条失败，其余照常，恒等式对每个会话仍成立。"""
    _process(conn, thread_id="u1", msgid="m1")
    with pytest.raises(sqlite3.IntegrityError):
        effect_enqueue_task(
            conn,
            thread_id="u1",
            business_key="orphan",
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
        )
    _process(conn, thread_id="u1", msgid="m2")
    _process(conn, thread_id="chat-9", msgid="m3")
    assert_effect_log_identity(conn)
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 3


def test_constraint_violation_in_archive_write_leaves_no_trace(conn):
    """真实失败形态：直接驱动生产函数 `effect_archive_message` 本体失败。

    reviewer 指出前 5 条新测试里有 3 条（`test_no_effect_log_when_business_write_raises`、
    `test_failed_effect_is_retried_on_next_run`、
    `test_failed_effect_rolls_back_exactly_once_and_never_commits`）装饰的是测试内的
    局部闭包，从未真正调用过生产的 `effect_archive_message`——证伪 A 改坏
    `effects.py` 后 `test_no_effect_log_when_business_write_raises` 仍然 PASSED，
    正是这个覆盖盲区的可见症状。本条直接调生产函数，用违反 `liaison_message.msgtype`
    的 NOT NULL 约束触发真实 `sqlite3.IntegrityError`，断言不留幂等记录、不留半截
    业务行，然后用合法入参重跑一次确认能正常成功写入。
    """
    with pytest.raises(sqlite3.IntegrityError):
        effect_archive_message(
            conn,
            thread_id="u1",
            business_key="m-bad",
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
            msgtype=None,  # NOT NULL 违约：liaison_message.msgtype 无默认值
        )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE business_key = 'm-bad'"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM liaison_message WHERE msgid = 'm-bad'"
        ).fetchone()[0]
        == 0
    )
    assert_effect_log_identity(conn)

    # 重跑：同一个 business_key，这次用合法入参，应当正常成功写入。
    result = effect_archive_message(
        conn,
        thread_id="u1",
        business_key="m-bad",
        sender_userid="u1",
        received_at="2026-09-08T10:05:00+08:00",
        msgtype="text",
    )
    assert result == "m-bad"
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1
    assert_effect_log_identity(conn)


# ─────────────────────────────────────────────────────────────────────────
# TD-18：`with <Call>:` 判据细化后的两侧证据
#   放行侧——与数据库无关的上下文管理器不再误报
#   证伪侧——`with self._conn:` 与连接样调用必须仍然红
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stmt",
    [
        'with open("x.txt", encoding="utf-8") as f:',
        'with io.open("x.txt") as f:',
        "with os.fdopen(fd) as f:",
        "with contextlib.suppress(OSError):",
        "with suppress(OSError):",
        "with tempfile.TemporaryDirectory() as d:",
        "with TemporaryDirectory() as d:",
        "with tempfile.NamedTemporaryFile() as f:",
        "with NamedTemporaryFile() as f:",
        "with contextlib.ExitStack() as stack:",
    ],
)
def test_scanner_allows_non_db_context_managers(stmt):
    """TD-18 的还债正题：第 3–5 章写这些与数据库无关的 `with` 不该变红。"""
    source = f"""
def handle(payload):
    {stmt}
        pass
"""
    offenders = _scan_transaction_violations(source, "fake_non_db.py", allowlist=set())
    assert offenders == [], offenders


@pytest.mark.parametrize(
    "stmt",
    [
        "with self._conn:",
        "with self.conn:",
        "with conn:",
        "async with conn:",
        "with get_connection() as c:",
        "with db.connect() as c:",
        "with engine.begin() as c:",
        "with new_transaction() as t:",
        "with GetConnection() as c:",
        "with contextlib.closing(conn) as c:",
        "with closing(conn) as c:",
    ],
)
def test_scanner_still_catches_connection_shaped_context_managers(stmt):
    """🔴 证伪：细化判据之后，连接／事务形态**一条都不许漏**。

    ⛔ 这条参数表只增不删。`with self._conn:` 那一格尤其不许动——它是 TD-18
    原文点名"⛔ 不许退回窄化"要守的那个口子，漏了它**没有任何症状**
    （effect_log 与业务表静默劈叉，正是 .51 2026-08-10／08-12 丢 outbox 的形态）。
    `contextlib.closing(conn)` 一格证明"连接样"判据压在模块族白名单**之前**。
    """
    prefix = "async def" if stmt.startswith("async with") else "def"
    source = f"""
{prefix} handle(conn, payload):
    {stmt}
        pass
"""
    offenders = _scan_transaction_violations(source, "fake_conn.py", allowlist=set())
    assert offenders, f"扫描器必须仍然抓到 `{stmt}`"


def test_scanner_still_catches_unknown_callees_by_default():
    """陌生被调用者仍然判违规——⛔ 不许"名字里没有 conn 就放行"。

    `with pool.acquire():` 名字里一个词根都没有，却完全可能是一个连接。
    漏判没有症状；误判是一条响亮的测试失败，加一行白名单即可。
    """
    source = """
def handle(pool):
    with pool.acquire() as c:
        c.execute("insert into liaison_task values (1)")
"""
    offenders = _scan_transaction_violations(source, "fake_unknown.py", allowlist=set())
    assert offenders, "陌生被调用者应当保持默认判违规"
