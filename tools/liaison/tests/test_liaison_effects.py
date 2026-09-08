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


def test_liaison_does_not_import_product_db_layer():
    """只 import 幂等装饰器这一个模块，⛔ 不 import app.storage.db。

    import 了 app.storage.db 就等于把产品库的 schema、迁移、连接语义一起拖进来，
    D5 的"独立库"就名存实亡了。
    """
    for path in LIAISON_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "app.storage.db", f"{path} 不该 import app.storage.db"
                assert not node.module.startswith("app.graph"), f"{path} 不该 import {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "app.storage.db", f"{path} 不该 import app.storage.db"


def test_no_second_transaction_manager_in_source():
    """静态判据：非测试代码里 commit/rollback 的调用点只允许出现在白名单里。

    事务的主人只有一个——`idempotent_effect`。任何别的地方调 commit()，都可能把
    一个只写了一半的业务事务提交下去，让 effect_log 与业务表的条数当场对不上，
    **而且不报错**。这条断言是那道闸。
    """
    offenders = []
    for path in sorted(LIAISON_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        rel = path.relative_to(LIAISON_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("commit", "rollback")
                ):
                    if func.name not in TRANSACTION_OWNER_ALLOWLIST.get(rel, set()):
                        offenders.append(f"{rel}::{func.name} 调了 {node.func.attr}()")
    assert offenders == [], "发现白名单之外的事务管理者：" + "; ".join(offenders)


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
