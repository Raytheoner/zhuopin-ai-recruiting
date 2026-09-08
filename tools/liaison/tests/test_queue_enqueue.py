"""5.1 / 5.2：入队接线与「缺来源即拒绝」。

⛔ 本文件不测渲染、不测状态流转——那是 test_queue_view.py 与 test_queue_status.py。
"""

import sqlite3

import pytest

from tools.liaison.queue import (
    MissingSourceMessage,
    compute_task_summary,
    enqueue_task,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _archive(conn, *, thread_id, msgid, content="材料收到"):
    """造一条父台账行。队列条目的外键指向它，⛔ 没有它入队必被拒。"""
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        msgtype="text",
        content=content,
    )


def test_enqueue_writes_one_row_carrying_the_source_handles(conn):
    """spec：每条队列条目 SHALL 携带发送人标识、来源会话标识、来源消息唯一标识、接收时间。"""
    _archive(conn, thread_id="u_zhang", msgid="msg-1")

    assert enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary="报价单已发",
    ) is True

    rows = conn.execute(
        "SELECT msgid, thread_id, sender_userid, received_at, summary, send_status, pushed_at "
        "FROM liaison_task"
    ).fetchall()
    assert rows == [("msg-1", "u_zhang", "u_zhang", RECEIVED_AT, "报价单已发", "pending", None)]
    assert_effect_log_identity(conn)


def test_enqueue_is_idempotent_on_redelivery(conn):
    """SDK 重连后重投同一 msgid：⛔ 不许产生第二条待办。"""
    _archive(conn, thread_id="u_zhang", msgid="msg-1")
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is True
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is False

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert_effect_log_identity(conn)


def test_enqueue_rejects_when_the_source_message_does_not_exist(conn):
    """5.2 / spec「条目缺少来源信息不允许写入」：外键是这条要求的真身。"""
    with pytest.raises(MissingSourceMessage):
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid="msg-does-not-exist",
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    # 铁律 1 的方向：业务写失败 ⇒ 幂等记录也必须不存在，否则重投时会被判"已执行"而永不重试。
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_enqueue_task'"
    ).fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_enqueue_rejects_a_null_msgid(conn):
    """`msgid TEXT NOT NULL`：连来源标识都没有的条目，⛔ 一行都不许落。"""
    with pytest.raises(MissingSourceMessage):
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid=None,
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_same_msgid_from_another_thread_is_treated_as_an_idempotent_hit(conn):
    """`msgid TEXT NOT NULL UNIQUE`：一条消息最多一条队列条目。

    幂等装饰器按 `{thread_id}:...:{msgid}` 去重，thread_id 换了就命不中；
    但 UNIQUE 会拦住。这是 schema 注释写的"两道防线"里的第二道——
    ⛔ 它必须表现为幂等命中（返回 False），不是抛给调用方的崩溃。
    """
    _archive(conn, thread_id="u_zhang", msgid="msg-1")
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is True

    assert enqueue_task(
        conn, thread_id="chat_group", msgid="msg-1", sender_userid="u_li", received_at=RECEIVED_AT
    ) is False

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert_effect_log_identity(conn)


class _RollbackFailsAfterEffectLogRace:
    """代理一个真实连接：`INSERT INTO effect_log` 让它假装撞键，随后 `rollback()`
    也失败——终审 Important-1 描述的那个事故形态。

    `idempotent_effect` 的第二个 `except sqlite3.IntegrityError` 分支只有在
    真实并发（两条路径都跑过预检、都执行了 `fn`，只有一条能把 `effect_log` 那行
    真正插进去）时才会自然触发；单线程测试里没有真的第二条路径，所以用代理
    在 `effect_enqueue_task` 的业务 `INSERT INTO liaison_task` **真实成功**之后，
    伪造 `INSERT INTO effect_log` 撞键、再让 `rollback()` 抛出异常，逼装饰器走到
    "记 ERROR 然后 `raise exc`" 那一支。

    ⚠️ 只拦 `INSERT INTO effect_log`：装饰器开头的 `SELECT 1 FROM effect_log ...`
    预检、`fn` 自己那条 `INSERT INTO liaison_task`、以及 `enqueue_task` 出错后
    做的语义判据查询都必须原样打到真实连接上，否则这条测试就不是在测真实代码
    路径。
    """

    def __init__(self, real: sqlite3.Connection):
        self._real = real
        self.rollback_called = False

    def execute(self, sql, params=()):
        if sql.strip().startswith("INSERT INTO effect_log"):
            raise sqlite3.IntegrityError("UNIQUE constraint failed: effect_log.effect_key")
        return self._real.execute(sql, params)

    def rollback(self):
        self.rollback_called = True
        raise sqlite3.OperationalError("cannot rollback - no transaction is active")

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_enqueue_reraises_when_effect_log_races_and_rollback_also_fails(conn):
    """终审 Important-1：`effect_log` 撞键 + 回滚也失败 ⇒ 必须原样抛出，
    ⛔ 不许被误判成幂等命中而返回 `False`（那会让 `liaison_task` 多一行、
    `effect_log` 少一行，铁律 1 的恒等式静默破裂）。

    修复前的实现只看异常文案里有没有 `"UNIQUE"`——这个事故形态下抛出的正是
    `sqlite3.IntegrityError("UNIQUE constraint failed: effect_log.effect_key")`，
    文案里同样有 `"UNIQUE"`，会被旧逻辑当场吞掉、返回 `False`。本测试的真实性
    已经在实现旧逻辑下验证过会失败（见任务报告），不是空转。
    """
    _archive(conn, thread_id="u_zhang", msgid="msg-race")
    proxy = _RollbackFailsAfterEffectLogRace(conn)

    with pytest.raises(sqlite3.IntegrityError, match="effect_log"):
        enqueue_task(
            proxy,
            thread_id="u_zhang",
            msgid="msg-race",
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )

    assert proxy.rollback_called, "没走到 rollback 分支——这条测试没有真的触发目标代码路径"


def test_enqueue_does_not_leave_a_half_written_row_after_the_race(tmp_path):
    """把 `test_enqueue_reraises_when_effect_log_races_and_rollback_also_fails`
    的事故重放一遍，但用真实文件库：关闭代理连接后重新打开，断言两张表都没有
    留下任何半截痕迹——`rollback()` 失败不代表数据真的提交了（sqlite3 的隐式
    事务在连接关闭时会被丢弃），只是这条连接自己在事后已经看不清真相，
    所以 `enqueue_task` 才必须把异常原样抛出而不是自己拍板"当作幂等命中"。
    """
    db_path = tmp_path / "liaison.db"
    setup_conn = liaison_db.get_connection(db_path)
    liaison_db.init_schema(setup_conn)
    _archive(setup_conn, thread_id="u_zhang", msgid="msg-race")
    setup_conn.close()

    conn = liaison_db.get_connection(db_path)
    proxy = _RollbackFailsAfterEffectLogRace(conn)
    with pytest.raises(sqlite3.IntegrityError):
        enqueue_task(
            proxy,
            thread_id="u_zhang",
            msgid="msg-race",
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )
    conn.close()

    verify_conn = liaison_db.get_connection(db_path)
    assert verify_conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert verify_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_enqueue_task'"
    ).fetchone()[0] == 0
    verify_conn.close()


def test_enqueue_rejects_a_null_thread_id_without_mislabeling_it_as_missing_source(conn):
    """终审 Minor-4：`thread_id=None` 撞的是 `liaison_task.thread_id NOT NULL`，
    跟"缺来源消息"是两件事——msgid 是好的、也已经归档过，运维照着
    `MissingSourceMessage` 的提示去补归档不会有任何效果。

    ⛔ 不靠文案匹配猜是哪个字段：语义判据先确认 msgid 本身没问题
    （`liaison_message` 里确实有这条），再确认不是 `effect_log` 记录的
    真幂等命中，剩下的完整性冲突原样抛出——SQLite 自己的报文已经点名了
    是 `thread_id`。
    """
    _archive(conn, thread_id="u_zhang", msgid="msg-1")

    with pytest.raises(sqlite3.IntegrityError, match="thread_id") as excinfo:
        enqueue_task(
            conn,
            thread_id=None,
            msgid="msg-1",
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )
    assert not isinstance(excinfo.value, MissingSourceMessage)

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_summary_is_a_mechanical_truncation_never_a_generated_one():
    """⛔ 摘要只能是机械截断。本服务不调用任何 LLM（design D7 / 合规红线）。"""
    assert compute_task_summary("报价单已发", msgtype="text") == "报价单已发"

    long_text = "甲" * 500
    truncated = compute_task_summary(long_text, msgtype="text", max_chars=120)
    assert len(truncated) == 120
    assert truncated.endswith("…")
    assert truncated[:119] == "甲" * 119


def test_summary_keeps_the_raw_characters_including_pipes_and_newlines():
    """⛔ 入队前不许做任何"竖线归一化"。转义只发生在渲染层（判断 1）。"""
    raw = "第一行|第二列\n第二行"
    assert compute_task_summary(raw, msgtype="text") == raw


def test_summary_for_a_message_without_text_names_the_type():
    """图片/文件消息没有正文。摘要写成`[file]`比空字符串可用——但仍然不是生成的内容。"""
    assert compute_task_summary("", msgtype="file") == "[file]"
    assert compute_task_summary("   ", msgtype="image") == "[image]"


def test_queue_module_never_commits_by_itself():
    """提交由 idempotent_effect 独占（第 2 章的单一事务管理者约束）。"""
    import ast
    import pathlib

    from tools.liaison import queue as queue_module

    tree = ast.parse(pathlib.Path(queue_module.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback", "executescript")
    ]
    assert offenders == [], f"queue.py 里出现了事务边界调用，行号 {offenders}"
