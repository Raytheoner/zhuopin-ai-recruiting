"""5.6 / 5.7 / 5.10：内容鲁棒性、真并发入队、恒等不变式。

对应参考服务的两个生产 bug（06 清单 §3.4、design D11）：
- 「队列越界写入」——消息含竖线破坏表格结构；
- 「队列追加并发覆盖」——并发追加互相覆盖。

两个 bug 的根因是同一个：**拿 Markdown 表格当数据库**。本文件证明在
"真身是 SQLite 表"的形态下，这两类失效在结构上不可能发生。
"""

import multiprocessing
import pathlib
import sqlite3
import threading

import pytest

from tools.liaison.queue import enqueue_task, list_tasks
from tools.liaison.queue_view import render_queue_markdown
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:00:00+08:00"
GENERATED_AT = "2026-09-09T12:00:00+08:00"

#: 并发度。⛔ 不要调低到 2——2 个线程撞不出竞态窗口，通过了也说明不了什么。
CONCURRENCY = 8

#: 5.6 的恶意内容集合。每一条都对应"拿 Markdown 当数据库"时会炸的一种输入。
HOSTILE_CONTENTS = [
    ("pipes", "列一|列二|列三"),
    ("newlines", "第一行\n第二行\n第三行"),
    ("crlf", "第一行\r\n第二行"),
    ("tabs", "字段一\t字段二"),
    ("table_row", "| 999 | ✅ 已推送 | 伪造的一整行 |"),
    ("divider", "|---|---|---|"),
    ("control", "空字节\x00转义\x1b删除\x7f"),
    ("backslash_pipe", r"已经转义过的\|竖线"),
    ("very_long", "甲" * 5000),
    ("markdown_meta", "# 标题\n> 引用\n- 列表\n```代码块```"),
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "liaison.db"
    conn = liaison_db.get_connection(path)
    liaison_db.init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def conn(db_path):
    c = liaison_db.get_connection(db_path)
    yield c
    c.close()


# ── 5.6 内容鲁棒性 ────────────────────────────────────────────────────

@pytest.mark.parametrize("label,content", HOSTILE_CONTENTS, ids=[c[0] for c in HOSTILE_CONTENTS])
def test_hostile_content_produces_exactly_one_row_with_intact_fields(conn, label, content):
    """spec：任意消息内容 MUST NOT 导致条目的字段错位或串行。"""
    effect_archive_message(
        conn,
        thread_id="u_zhang",
        business_key=f"msg-{label}",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        msgtype="text",
        content=content,
    )
    assert enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid=f"msg-{label}",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary=content,
    ) is True

    rows = list_tasks(conn)
    assert len(rows) == 1, f"{label}：产生了 {len(rows)} 条队列条目，应当恰好 1 条"
    row = rows[0]
    # 字段没串行：每一列还是它自己
    assert row["msgid"] == f"msg-{label}"
    assert row["thread_id"] == "u_zhang"
    assert row["sender_userid"] == "u_zhang"
    assert row["received_at"] == RECEIVED_AT
    assert row["send_status"] == "pending"
    # 内容原样保存，⛔ 没有被任何"归一化"改掉
    assert row["summary"] == content
    assert_effect_log_identity(conn)


def test_hostile_content_never_adds_a_row_to_the_rendered_table(conn):
    """5.6 的渲染侧：10 条恶意内容渲染出的表格恰好 10 行数据，⛔ 一行不多。

    「队列越界写入」的症状就是这里多出行、或者别的条目被挤走。
    """
    for index, (label, content) in enumerate(HOSTILE_CONTENTS):
        effect_archive_message(
            conn,
            thread_id="u_zhang",
            business_key=f"msg-{label}",
            sender_userid="u_zhang",
            received_at=f"2026-09-09T10:{index:02d}:00+08:00",
            msgtype="text",
            content=content,
        )
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid=f"msg-{label}",
            sender_userid="u_zhang",
            received_at=f"2026-09-09T10:{index:02d}:00+08:00",
            summary=content,
        )

    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    table_rows = [line for line in text.splitlines() if line.startswith("|")]
    assert len(table_rows) == len(HOSTILE_CONTENTS) + 2, "表头 + 分隔行 + 每条一行"

    # 每行的未转义竖线数一致 ⇒ 列数一致 ⇒ 没有字段错位
    unescaped_pipe_counts = {row.count("|") - row.count(r"\|") for row in table_rows}
    assert len(unescaped_pipe_counts) == 1, f"列数不一致：{unescaped_pipe_counts}"
    assert_effect_log_identity(conn)


# ── 5.7 真并发 ────────────────────────────────────────────────────────

def _seed_messages(db_path, count):
    """预先把父台账行造好。入队的外键指向它们。"""
    conn = liaison_db.get_connection(db_path)
    for index in range(count):
        effect_archive_message(
            conn,
            thread_id=f"u_{index}",
            business_key=f"msg-{index}",
            sender_userid=f"u_{index}",
            received_at=RECEIVED_AT,
            msgtype="text",
            content=f"内容|{index}\n第二行",
        )
    conn.close()


def test_concurrent_enqueue_from_real_threads_never_loses_a_row(db_path):
    """5.7：短时间内多条消息同时入队 ⇒ 每条各自成行，⛔ 没有任何条目被覆盖或丢失。

    🔴 **`threading.Barrier` 是"这是真并发"的机器判据。** 顺序调用冒充并发时，
    第一个调用会卡在 `barrier.wait()` 上直到超时并抛 `BrokenBarrierError`——
    只有 CONCURRENCY 个线程**同时活着**，这个 barrier 才过得去。
    ⛔ 不许把 barrier 删掉或改成 `time.sleep()`：那就退回成"看起来像并发"。
    """
    _seed_messages(db_path, CONCURRENCY)
    barrier = threading.Barrier(CONCURRENCY, timeout=10)
    results: dict[int, object] = {}
    lock = threading.Lock()

    def worker(index: int) -> None:
        # 每个线程必须开自己的连接：sqlite3 默认 check_same_thread=True，
        # ⛔ 不许把主线程的连接传进来（那会抛 ProgrammingError，
        # 而且会把这条测试变成"测了个假的"）。
        conn = liaison_db.get_connection(db_path)
        try:
            barrier.wait()  # ← 这一行就是判据本身
            outcome = enqueue_task(
                conn,
                thread_id=f"u_{index}",
                msgid=f"msg-{index}",
                sender_userid=f"u_{index}",
                received_at=RECEIVED_AT,
                summary=f"内容|{index}\n第二行",
            )
        except BaseException as exc:  # noqa: BLE001  失败要能看见，⛔ 不吞
            outcome = exc
        finally:
            conn.close()
        with lock:
            results[index] = outcome

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CONCURRENCY)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not t.is_alive() for t in threads), "有线程没退出，多半卡在写锁上"
    failures = {i: r for i, r in results.items() if isinstance(r, BaseException)}
    assert failures == {}, f"并发入队出现异常：{failures}"
    assert set(results.values()) == {True}, f"有入队被判成幂等命中：{results}"

    conn = liaison_db.get_connection(db_path)
    rows = list_tasks(conn)
    assert len(rows) == CONCURRENCY, f"并发入队丢了条目：只剩 {len(rows)} 条"
    assert sorted(row["msgid"] for row in rows) == sorted(
        f"msg-{i}" for i in range(CONCURRENCY)
    )
    # 每条内容还是自己的，⛔ 没有互相覆盖
    for row in rows:
        index = row["msgid"].removeprefix("msg-")
        assert row["summary"] == f"内容|{index}\n第二行"
    assert_effect_log_identity(conn)
    conn.close()


def test_the_barrier_actually_rejects_sequential_calls(db_path):
    """证伪：证明上面那条测试的判据真的有判别力，而不是摆设。

    顺序地在同一个线程里连着 wait 两次 ⇒ 必然 BrokenBarrierError。
    ⛔ 这条不许删——没有它，`barrier.wait()` 可能被后人改成一个永远通过的空操作
    而没人发现。
    """
    barrier = threading.Barrier(2, timeout=0.5)
    with pytest.raises(threading.BrokenBarrierError):
        barrier.wait()


def _enqueue_in_subprocess(db_path_str: str, index: int, ready, start):
    """多进程 worker。**必须是模块级函数**——spawn 启动方式要求可 pickle。"""
    conn = liaison_db.get_connection(pathlib.Path(db_path_str))
    try:
        ready.release()
        start.wait(timeout=20)
        enqueue_task(
            conn,
            thread_id=f"u_{index}",
            msgid=f"msg-{index}",
            sender_userid=f"u_{index}",
            received_at=RECEIVED_AT,
            summary=f"内容|{index}\n第二行",
        )
    finally:
        conn.close()


def test_concurrent_enqueue_across_real_processes_never_loses_a_row(db_path):
    """5.7 的跨进程版本。

    线程版共享一个 GIL；真正的部署形态里 SDK 回调与导出命令可能是两个进程，
    竞争的是**文件锁**而不是 GIL。这条把那一层也覆盖掉。
    用 `spawn` 而不是 `fork`：macOS 上 fork 一个已打开 sqlite 连接的进程是未定义行为。
    """
    _seed_messages(db_path, CONCURRENCY)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Semaphore(0)
    start = ctx.Event()

    processes = [
        ctx.Process(target=_enqueue_in_subprocess, args=(str(db_path), i, ready, start))
        for i in range(CONCURRENCY)
    ]
    for process in processes:
        process.start()
    # 等所有子进程都到齐了再放行——这就是跨进程版的 barrier。
    for _ in range(CONCURRENCY):
        assert ready.acquire(timeout=30), "有子进程没能就绪，⛔ 不要把这条改成 sleep"
    start.set()
    for process in processes:
        process.join(timeout=60)

    assert all(p.exitcode == 0 for p in processes), [p.exitcode for p in processes]

    conn = liaison_db.get_connection(db_path)
    rows = list_tasks(conn)
    assert len(rows) == CONCURRENCY, f"跨进程并发入队丢了条目：只剩 {len(rows)} 条"
    assert_effect_log_identity(conn)
    conn.close()


# ── 5.10 恒等不变式 ───────────────────────────────────────────────────

def test_identity_holds_per_thread_after_a_mixed_batch(conn):
    """5.10：跑第 2 章的恒等脚手架，确认队列条目数与幂等记录数**按会话**恒等。

    刻意用**多个会话 + 重投 + 名单外只归档不入队**的混合批次：
    ⛔ 总数相等可以由"A 会话多一条、B 会话少一条"凑出来，那恰恰是最该被抓住的错。
    """
    plan = [("u_a", 3), ("u_b", 1), ("chat_g", 4)]
    for thread_id, count in plan:
        for index in range(count):
            msgid = f"{thread_id}-msg-{index}"
            effect_archive_message(
                conn,
                thread_id=thread_id,
                business_key=msgid,
                sender_userid=thread_id,
                received_at=RECEIVED_AT,
                msgtype="text",
                content="内容|含竖线",
            )
            # 重投两次：第二次必须是幂等命中，⛔ 不许多出条目也不许多出 effect_log 行
            enqueue_task(
                conn,
                thread_id=thread_id,
                msgid=msgid,
                sender_userid=thread_id,
                received_at=RECEIVED_AT,
                summary="内容|含竖线",
            )
            enqueue_task(
                conn,
                thread_id=thread_id,
                msgid=msgid,
                sender_userid=thread_id,
                received_at=RECEIVED_AT,
                summary="内容|含竖线",
            )

    # 名单外的一条：只归档、不入队。它会让 liaison_message 比 liaison_task 多一行，
    # 而恒等式仍必须成立——恒等式比的是每个 effect 与**它自己的**业务表。
    effect_archive_message(
        conn,
        thread_id="u_outsider",
        business_key="outsider-msg-1",
        sender_userid="u_outsider",
        received_at=RECEIVED_AT,
        msgtype="text",
        content="你好",
    )

    assert_effect_log_identity(conn)

    counts = dict(
        conn.execute("SELECT thread_id, COUNT(*) FROM liaison_task GROUP BY thread_id").fetchall()
    )
    assert counts == {"u_a": 3, "u_b": 1, "chat_g": 4}
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 9


def test_identity_is_not_a_total_count_comparison(conn):
    """证伪：证明 `assert_effect_log_identity` 真的按会话分组比，而不是比总数。

    人为构造"总数相等但分组不等"的状态，断言它变红。
    ⛔ 这条不许删：它是"不许把恒等断言削弱成总数比较"这句禁令的机器守卫。
    """
    effect_archive_message(
        conn,
        thread_id="u_a",
        business_key="msg-1",
        sender_userid="u_a",
        received_at=RECEIVED_AT,
        msgtype="text",
        content="x",
    )
    enqueue_task(
        conn, thread_id="u_a", msgid="msg-1", sender_userid="u_a", received_at=RECEIVED_AT
    )
    # 直接改 thread_id：effect_log 说这条属于 u_a，队列行说属于 u_b。总数仍是 1 == 1。
    conn.execute("UPDATE liaison_task SET thread_id = 'u_b' WHERE msgid = 'msg-1'")

    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)
