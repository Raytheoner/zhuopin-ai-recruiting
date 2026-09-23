"""G3 泳道结果私信本人（0917Y）：发件箱表 ＋ 入队 CLI ＋ 值守线程消费者。

裁决（Shao Peishen 2026-09-17 答 1a）：泳道批次收敛后经值守通道**私信本人**一条摘要，
⛔ 不进群、⛔ 不发给任何其他人。本文件把这条裁决钉成机器判据：

- 收件人**只能**是名单里 `name == OWNER_NAME` 的那一条的 userid；
- 消费者与 CLI 都**没有**"收件人"这个参数——非本人收件人在类型上就不可能出现；
- ⛔ 本文件不 import aibot、不真实建连：SDK 发送口一律用替身。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import queue
import subprocess
import sys
import threading

import pytest

from tools.liaison import __main__ as liaison_main
from tools.liaison import alerts, owner_notify, session
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import EFFECT_NODE_TO_TABLE, effect_enqueue_owner_notify
from tools.liaison.tests.conftest import RecordingSink
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity
from tools.liaison.tests.test_main_wiring import StoppingEvent, T0

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIAISON_ROOT = REPO_ROOT / "tools" / "liaison"

OWNER_USERID = "ShaoPeiShen"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def roster(tmp_path):
    """与生产名单同形：本人 + 汤丽萍。⛔ 不引用真实 config/whitelist.yaml。"""
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        '  - userid: "TangLiPing"\n'
        "    name: 汤丽萍\n"
        "    role: HR AI 专员\n"
        f'  - userid: "{OWNER_USERID}"\n'
        f"    name: {owner_notify.OWNER_NAME}\n"
        "    role: 工具主人\n",
        encoding="utf-8",
    )
    return path


class FakeSendPort:
    """替身发送口：记录 (chatid, body)，可按脚本抛错。⛔ 不碰 aibot。"""

    def __init__(self, *, ready: bool = True, failures: int = 0) -> None:
        self._ready = ready
        self.failures_left = failures
        self.calls: list[tuple[str, str]] = []

    def ready(self) -> bool:
        return self._ready

    def send_markdown(self, chatid: str, content: str) -> None:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise RuntimeError("Reply ack error: errcode=45009, errmsg=freq limit")
        self.calls.append((chatid, content))


def enqueue(conn, key: str = "lanes-20260917-101500", body: str = "泳道汇总 body") -> None:
    effect_enqueue_owner_notify(
        conn,
        thread_id=owner_notify.OWNER_NOTIFY_THREAD_ID,
        business_key=key,
        body=body,
    )


def row(conn, key: str = "lanes-20260917-101500"):
    return conn.execute(
        "SELECT sent_at, attempts, last_error FROM owner_notify_outbox WHERE dedupe_key = ?",
        (key,),
    ).fetchone()


# ─────────────────────────────────────────────────────────────────────────
# 一、发件箱表 ＋ 入队幂等
# ─────────────────────────────────────────────────────────────────────────


def test_outbox_table_has_the_contracted_columns(conn):
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(owner_notify_outbox)").fetchall()
    }
    assert {
        "id", "dedupe_key", "thread_id", "body", "created_at", "sent_at", "attempts", "last_error"
    } <= cols


def test_enqueue_is_idempotent_on_dedupe_key(conn):
    """同一个 dedupe_key 入队两次 ⇒ 一行；幂等命中返回 None、不报错。"""
    first = effect_enqueue_owner_notify(
        conn, thread_id=owner_notify.OWNER_NOTIFY_THREAD_ID, business_key="k1", body="a"
    )
    second = effect_enqueue_owner_notify(
        conn, thread_id=owner_notify.OWNER_NOTIFY_THREAD_ID, business_key="k1", body="b（应被忽略）"
    )
    assert first == "k1" and second is None
    rows = conn.execute("SELECT body FROM owner_notify_outbox").fetchall()
    assert rows == [("a",)]
    assert_effect_log_identity(conn)


def test_outbox_dedupe_key_is_unique_at_the_storage_layer(conn):
    """结构防线：绕过装饰器直插同键必须被表本身拒绝。"""
    import sqlite3

    conn.execute(
        "INSERT INTO owner_notify_outbox (dedupe_key, thread_id, body) VALUES ('k', 't', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO owner_notify_outbox (dedupe_key, thread_id, body) VALUES ('k', 't', 'y')"
        )


def test_enqueue_effect_is_registered_for_the_identity_check():
    assert EFFECT_NODE_TO_TABLE["effect_enqueue_owner_notify"] == "owner_notify_outbox"


# ─────────────────────────────────────────────────────────────────────────
# 二、收件人只能是名单里的本人
# ─────────────────────────────────────────────────────────────────────────


def test_owner_userid_is_resolved_from_the_roster_by_name():
    names = {"TangLiPing": "汤丽萍", OWNER_USERID: owner_notify.OWNER_NAME}
    assert owner_notify.compute_owner_userid(names) == OWNER_USERID


@pytest.mark.parametrize(
    "names",
    [
        {},
        {"TangLiPing": "汤丽萍"},
        {"A": owner_notify.OWNER_NAME, "B": owner_notify.OWNER_NAME},  # 两条同名 ⇒ 不猜
    ],
)
def test_owner_userid_is_none_when_not_exactly_one_match(names):
    assert owner_notify.compute_owner_userid(names) is None


def test_compute_owner_userid_is_pure():
    """铁律 2：纯函数，不读文件、不记日志。"""
    tree = ast.parse(inspect.getsource(owner_notify.compute_owner_userid))
    calls = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "?"))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not calls & {"open", "read_text", "load_whitelist_names", "info", "error", "warning"}


def test_consumer_sends_only_to_the_owner_userid_from_the_roster(conn, roster):
    port = FakeSendPort()
    sink = RecordingSink()
    enqueue(conn, body="批次 lanes-x：OK 2 条")
    report = owner_notify.drain_owner_notify_outbox(
        conn, send_port=port, whitelist_path=roster, alert_sink=sink
    )
    assert report.sent == 1
    assert port.calls == [(OWNER_USERID, "批次 lanes-x：OK 2 条")]
    sent_at, attempts, last_error = row(conn)
    assert sent_at is not None and attempts == 0 and last_error is None
    assert_effect_log_identity(conn)


def test_consumer_never_sends_to_tang_even_if_she_is_the_only_member(conn, tmp_path):
    """名单里没有本人 ⇒ fail-closed：不发、记告警、attempts 不动。"""
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        'members:\n  - userid: "TangLiPing"\n    name: 汤丽萍\n    role: HR AI 专员\n',
        encoding="utf-8",
    )
    port = FakeSendPort()
    sink = RecordingSink()
    enqueue(conn)
    report = owner_notify.drain_owner_notify_outbox(
        conn, send_port=port, whitelist_path=path, alert_sink=sink
    )
    assert port.calls == []
    assert report.sent == 0 and report.owner_unresolved is True
    assert row(conn)[0] is None and row(conn)[1] == 0
    assert any("本人" in text for text in sink.texts)


def test_consumer_has_no_recipient_parameter():
    """非本人收件人在类型上就不可能出现：消费者签名里没有任何收件人形参。"""
    params = set(inspect.signature(owner_notify.drain_owner_notify_outbox).parameters)
    forbidden = {"chatid", "userid", "recipient", "to", "receiver", "user_id"}
    assert not params & forbidden, params


def test_cli_rejects_any_recipient_flag(tmp_path):
    """CLI 没有收件人参数：`--to`/`--userid`/`--chatid` 一律 argparse 拒绝（exit 2），
    在打开任何数据库之前。子进程跑，顺带证明 `python -m tools.liaison owner-notify`
    这条子命令真的接上了。"""
    body = tmp_path / "body.md"
    body.write_text("x", encoding="utf-8")
    for flag in ("--to", "--userid", "--chatid"):
        result = subprocess.run(
            [sys.executable, "-m", "tools.liaison", "owner-notify",
             "--dedupe-key", "k", "--body-file", str(body), flag, "Someone"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 2, (flag, result.stderr)
        assert "unrecognized" in result.stderr


# ─────────────────────────────────────────────────────────────────────────
# 三、发送失败重试与停发
# ─────────────────────────────────────────────────────────────────────────


def test_failed_send_increments_attempts_and_records_error(conn, roster):
    port = FakeSendPort(failures=1)
    sink = RecordingSink()
    enqueue(conn)
    owner_notify.drain_owner_notify_outbox(
        conn, send_port=port, whitelist_path=roster, alert_sink=sink
    )
    sent_at, attempts, last_error = row(conn)
    assert sent_at is None and attempts == 1 and "45009" in last_error
    # 失败不留发送幂等记录：下一轮必须还能再试
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_send_owner_notify'"
    ).fetchone()[0] == 0
    # 第二轮成功
    owner_notify.drain_owner_notify_outbox(
        conn, send_port=port, whitelist_path=roster, alert_sink=sink
    )
    assert row(conn)[0] is not None and row(conn)[1] == 1
    assert len(port.calls) == 1


def test_after_max_attempts_the_row_is_parked_and_an_alert_is_raised(conn, roster):
    port = FakeSendPort(failures=99)
    sink = RecordingSink()
    enqueue(conn)
    for _ in range(owner_notify.MAX_ATTEMPTS + 2):
        owner_notify.drain_owner_notify_outbox(
            conn, send_port=port, whitelist_path=roster, alert_sink=sink
        )
    sent_at, attempts, _ = row(conn)
    assert sent_at is None
    assert attempts == owner_notify.MAX_ATTEMPTS, "≥ MAX 次后停发，⛔ 不许继续累加"
    assert port.calls == []
    assert any("停发" in text for text in sink.texts)
    assert sum("停发" in text for text in sink.texts) == 1, "停发告警只记一次"


def test_sent_rows_are_never_resent(conn, roster):
    port = FakeSendPort()
    sink = RecordingSink()
    enqueue(conn)
    for _ in range(3):
        owner_notify.drain_owner_notify_outbox(
            conn, send_port=port, whitelist_path=roster, alert_sink=sink
        )
    assert len(port.calls) == 1


def test_port_not_ready_does_not_burn_attempts(conn, roster):
    """连接没就绪（断线期间）⛔ 不算一次失败——否则一次 45 秒的断线就把三次机会烧光。"""
    port = FakeSendPort(ready=False)
    enqueue(conn)
    report = owner_notify.drain_owner_notify_outbox(
        conn, send_port=port, whitelist_path=roster, alert_sink=RecordingSink()
    )
    assert report.skipped_not_ready is True
    assert row(conn)[1] == 0 and port.calls == []


def test_send_happens_before_the_row_is_marked(conn, roster):
    """先发送、后落 sent_at（与群通知同一顺序）：发送抛错时 sent_at 必须仍为空。"""
    src = inspect.getsource(owner_notify.effect_send_owner_notify)
    assert src.index("send_port.send_markdown(") < src.index("UPDATE owner_notify_outbox")


# ─────────────────────────────────────────────────────────────────────────
# 四、SDK 发送口：协程跨线程投递、单聊 chatid = userid、fail-closed
# ─────────────────────────────────────────────────────────────────────────


class FakeClient:
    """形状对齐 aibot 1.0.2 `WSClient.send_message(chatid, body)`（协程）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, chatid: str, body: dict):
        self.calls.append((chatid, body))
        return {"errcode": 0}


class NoSendClient:
    def run(self) -> None: ...


@pytest.fixture
def loop_thread():
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)
    loop.close()


def test_sdk_send_port_posts_a_markdown_body_to_the_client_loop(loop_thread):
    from tools.liaison import session_client

    stopper = session_client.LoopStopper()
    stopper.remember(loop_thread)
    holder = session_client.ClientHolder()
    client = FakeClient()
    holder.remember(client)
    port = owner_notify.SdkSendPort(holder, stopper, timeout=5.0)
    assert port.ready() is True
    port.send_markdown(OWNER_USERID, "**hi**")
    assert client.calls == [
        (OWNER_USERID, {"msgtype": "markdown", "markdown": {"content": "**hi**"}})
    ]


def test_sdk_send_port_is_not_ready_without_client_or_loop(loop_thread):
    from tools.liaison import session_client

    stopper = session_client.LoopStopper()
    holder = session_client.ClientHolder()
    port = owner_notify.SdkSendPort(holder, stopper, timeout=1.0)
    assert port.ready() is False
    holder.remember(FakeClient())
    assert port.ready() is False, "有 client 没 loop 也不算就绪"
    stopper.remember(loop_thread)
    assert port.ready() is True
    holder.forget()
    assert port.ready() is False


def test_sdk_send_port_fails_closed_when_the_sdk_has_no_send_message(loop_thread):
    from tools.liaison import session_client

    stopper = session_client.LoopStopper()
    stopper.remember(loop_thread)
    holder = session_client.ClientHolder()
    holder.remember(NoSendClient())
    port = owner_notify.SdkSendPort(holder, stopper, timeout=1.0)
    with pytest.raises(owner_notify.OwnerNotifySendError):
        port.send_markdown(OWNER_USERID, "x")


def test_make_sdk_connect_remembers_the_client_for_the_send_port():
    """建连时把 client 交给 holder，`run()` 返回后丢掉——与 loop 把手同一纪律。"""
    from tools.liaison import session_client

    seen: list[str] = []

    class Client:
        def on(self, *_a, **_k): ...

        def run(self) -> None:
            seen.append("run:" + ("held" if holder.current() is not None else "none"))

    holder = session_client.ClientHolder()
    connect = session_client.make_sdk_connect(
        Client, on_connected=lambda: None, on_disconnected=lambda: None, client_holder=holder
    )
    connect()
    assert seen == ["run:held"]
    assert holder.current() is None


# ─────────────────────────────────────────────────────────────────────────
# 五、值守线程接线：空闲 tick 时消费，只在 connected 状态
# ─────────────────────────────────────────────────────────────────────────


def _svc(tmp_path):
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    return session.LiaisonSession(conn, RecordingSink(), liveness_path=tmp_path / "liveness.json")


def test_worker_drains_the_outbox_on_idle_ticks_when_connected(tmp_path, roster):
    svc = _svc(tmp_path)
    svc.on_connected(T0)
    enqueue(svc.conn)
    port = FakeSendPort()
    consumer = owner_notify.OwnerNotifyConsumer(send_port=port, whitelist_path=roster)
    ports = liaison_main.InboundPorts(whitelist_path=roster, owner_notify=consumer)
    liaison_main.run_session_worker(
        svc, queue.Queue(), StoppingEvent(after=2), tick_interval=0.01, ports=ports,
        clock=lambda: T0,
    )
    assert port.calls == [(OWNER_USERID, "泳道汇总 body")]


def test_worker_does_not_drain_while_disconnected(tmp_path, roster):
    svc = _svc(tmp_path)
    enqueue(svc.conn)
    port = FakeSendPort()
    consumer = owner_notify.OwnerNotifyConsumer(send_port=port, whitelist_path=roster)
    ports = liaison_main.InboundPorts(whitelist_path=roster, owner_notify=consumer)
    liaison_main.run_session_worker(
        svc, queue.Queue(), StoppingEvent(after=2), tick_interval=0.01, ports=ports,
        clock=lambda: T0,
    )
    assert port.calls == [] and row(svc.conn)[1] == 0


def test_consumer_exceptions_never_kill_the_worker(tmp_path, roster, caplog):
    class Exploding:
        def ready(self):
            raise RuntimeError("boom")

        def send_markdown(self, *_a):
            raise AssertionError("不该到这里")

    svc = _svc(tmp_path)
    svc.on_connected(T0)
    enqueue(svc.conn)
    consumer = owner_notify.OwnerNotifyConsumer(send_port=Exploding(), whitelist_path=roster)
    ports = liaison_main.InboundPorts(whitelist_path=roster, owner_notify=consumer)
    with caplog.at_level("ERROR"):
        liaison_main.run_session_worker(
            svc, queue.Queue(), StoppingEvent(after=2), tick_interval=0.01, ports=ports,
            clock=lambda: T0,
        )
    assert "本人通知" in caplog.text


def test_main_wires_a_consumer_into_the_worker_ports():
    """`main()` 必须真的把 SdkSendPort → OwnerNotifyConsumer → InboundPorts 接上，
    ⛔ 不许只在测试里接。AST 扫 main() 函数体。"""
    src = (LIAISON_ROOT / "__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    names = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "?"))
        for n in ast.walk(main_fn)
        if isinstance(n, ast.Call)
    }
    assert {
        "SdkSendPort",
        "OwnerNotifyConsumer",
        "InboundPorts",
        "ClientHolder",
        "SdkDownloadPort",
    } <= names


# ─────────────────────────────────────────────────────────────────────────
# 六、入队 CLI（进程内）
# ─────────────────────────────────────────────────────────────────────────


def test_owner_notify_cli_enqueues_and_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    body = tmp_path / "digest.md"
    body.write_text("批次 lanes-1", encoding="utf-8")
    argv = ["--dedupe-key", "lanes-1", "--body-file", str(body)]
    assert owner_notify.owner_notify_main(argv) == 0
    assert "已入队" in capsys.readouterr().out
    assert owner_notify.owner_notify_main(argv) == 0
    assert "已存在" in capsys.readouterr().out
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    assert conn.execute("SELECT COUNT(*) FROM owner_notify_outbox").fetchone()[0] == 1
    conn.close()


def test_owner_notify_cli_refuses_empty_body_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")
    assert owner_notify.owner_notify_main(["--dedupe-key", "k", "--body-file", str(empty)]) == 2
    assert owner_notify.owner_notify_main(
        ["--dedupe-key", "k", "--body-file", str(tmp_path / "nope.md")]
    ) == 2
    assert not (tmp_path / "liaison.db").exists() or (
        liaison_db.get_connection(tmp_path / "liaison.db")
        .execute("SELECT COUNT(*) FROM owner_notify_outbox").fetchone()[0] == 0
    )


def _lane_logdir(tmp_path, results_text: str) -> pathlib.Path:
    logdir = tmp_path / "lanes-20260917-101500"
    logdir.mkdir()
    (logdir / "results.tsv").write_text(results_text, encoding="utf-8")
    (logdir / "summary.txt").write_text("泳道 编号 状态 分钟\n", encoding="utf-8")
    return logdir


def _outbox_bodies(tmp_path) -> list[str]:
    db = tmp_path / "liaison.db"
    if not db.exists():
        return []
    conn = liaison_db.get_connection(db)
    try:
        return [r[0] for r in conn.execute("SELECT body FROM owner_notify_outbox").fetchall()]
    finally:
        conn.close()


def test_owner_notify_cli_can_build_the_body_from_a_lane_logdir(tmp_path, monkeypatch):
    """run-lanes.sh 收敛后直接指 LOGDIR：正文由 compute_lane_digest 生成。

    夹具故意含一条 PARTIAL——2026-09-17 口径起「全部 OK 不私信」，全 OK 的批次
    走不到入队这一步（见下面三条口径用例）。
    """
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    logdir = _lane_logdir(
        tmp_path,
        "G3\t0917Y\tOK\t12\t/x/0917Y.log\tsonnet\n"
        "G4\t0917Z\tPARTIAL\t8\t/x/0917Z.log\tsonnet\n",
    )
    assert owner_notify.owner_notify_main(
        ["--dedupe-key", "lanes-20260917-101500", "--lane-logdir", str(logdir)]
    ) == 0
    (body,) = _outbox_bodies(tmp_path)
    assert "0917Y" in body and "OK" in body and "/x/0917Y.log" not in body
    assert "0917Z" in body and "PARTIAL" in body


# ── 私信口径（2026-09-17 Shao Peishen 15:4x 定：全部 OK 不私信，有 PARTIAL/FAIL 才提醒）──
# 这三条钉的是 `_read_body` 里那道「全 OK ⇒ None」的闸。它是 Cowork 直改进来的，
# 删掉那五行，第一条当场红（入队了一条不该发的私信）。


def test_all_ok_batch_is_not_enqueued(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    logdir = _lane_logdir(
        tmp_path,
        "G1\t0917A\tOK\t12\t/x/a.log\tsonnet\n"
        "G2\t0917B\tOK\t9\t/x/b.log\tsonnet\n",
    )
    assert owner_notify.owner_notify_main(
        ["--dedupe-key", "lanes-all-ok", "--lane-logdir", str(logdir)]
    ) == owner_notify.EXIT_BAD_ARGS
    assert "全部 OK" in capsys.readouterr().err
    assert _outbox_bodies(tmp_path) == []


@pytest.mark.parametrize(
    "bad_status",
    ["PARTIAL", "FAIL(1)", "NO-SENTINEL", "NO-BODY", "WORKTREE-FAIL"],
)
def test_batch_with_any_abnormal_row_is_enqueued(tmp_path, monkeypatch, bad_status):
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    logdir = _lane_logdir(
        tmp_path,
        "G1\t0917A\tOK\t12\t/x/a.log\tsonnet\n"
        f"G2\t0917B\t{bad_status}\t9\t/x/b.log\tsonnet\n",
    )
    assert owner_notify.owner_notify_main(
        ["--dedupe-key", f"lanes-{bad_status}", "--lane-logdir", str(logdir)]
    ) == 0
    (body,) = _outbox_bodies(tmp_path)
    assert "0917B" in body


def test_empty_results_tsv_behaviour_is_unchanged(tmp_path, monkeypatch):
    """results.tsv 存在但一行都没有 ⇒ 仍入队一条「共 0 条」的摘要（口径改动前就是这样）。

    这是「results.tsv 被建了、泳道一条都没跑起来」的形态，值得提醒本人；
    ⛔ 不要把它归到「全 OK」里静默掉——`all([])` 为真正是这里要防的坑。
    """
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    logdir = _lane_logdir(tmp_path, "")
    assert owner_notify.owner_notify_main(
        ["--dedupe-key", "lanes-empty", "--lane-logdir", str(logdir)]
    ) == 0
    (body,) = _outbox_bodies(tmp_path)
    assert "共 0 条" in body


def test_main_dispatches_owner_notify_before_credentials_are_loaded():
    """子命令必须短路在 main() 的 load_credentials() 之前（与 cleanup 同一纪律）。"""
    src = (LIAISON_ROOT / "__main__.py").read_text(encoding="utf-8")
    assert 'sys.argv[1] == "owner-notify"' in src
    assert src.index('sys.argv[1] == "owner-notify"') < src.index('sys.argv[1] == SELF_CHECK_ARG')
