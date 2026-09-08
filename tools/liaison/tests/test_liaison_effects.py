"""effect 层的三组断言：单一事务管理者、恒等不变式、异常不留幂等记录。

这三组合起来就是工程铁律 1 在本服务里的可执行形式。⛔ 任何一条红了都不要改断言——
`.51` 现网 2026-08-10 与 08-12 各丢一轮 outbox，成因正是这三条里的第三条没被守住。
"""

from __future__ import annotations

import ast
import pathlib
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
    return c


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
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.context_expr, ast.Name):
                    scope = scope_name(func_stack)
                    if scope not in allowlist:
                        offenders.append(
                            f"{label}::{scope} 用 `with {item.context_expr.id}:` "
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
