"""6.4 —— 跨介质对账，以及它与 verify_chain() 的**不可互相替代性**。

verify_chain() 回答的是"链自身有没有被改"；reconcile() 回答的是"该留的痕都
留了没有"。两个问题，两条断言。本文件的核心是那两条**交叉反例**：

    链完好 + 镜像缺行  →  chain_assertion 绿、reconciliation_assertion 红
    镜像齐全 + 被篡改  →  chain_assertion 红、reconciliation_assertion 绿

任何一个方向缺了用例，"不可互相替代"就只是一句注释。
"""

import json

import pytest

from app.audit.assertions import chain_assertion, reconciliation_assertion
from app.audit.events import AI_ANALYSIS, CriterionScore, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema

pytestmark = pytest.mark.compliance


@pytest.fixture(autouse=True)
def _clear_chain_class_state():
    """JsonlChainSink 的锁与游标是**类级、按绝对路径共享**的
    （app/audit/sinks.py:271-273）。不清掉，上一条用例的游标会跟着进下一条，
    新文件的第一行拿到一个来自别的文件的 prev_hash，链从那行起永久断裂。
    tests/test_audit_recorder.py 已有同形状的 fixture，此处照同一做法。"""
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "audit" / "decisions.jsonl"


@pytest.fixture
def recorder(conn, chain_path):
    return AuditRecorder(store=SqliteSink(conn), mirror_sink=JsonlChainSink(chain_path))


def make_event(run_id: str) -> DecisionEvent:
    return DecisionEvent(
        id=run_id,
        event_type=AI_ANALYSIS,
        thread_id="thread-1",
        application_id="app-1",
        configured_model="deepseek-chat",
        response_model="deepseek-chat",
        prompt_version="score-v1",
        temperature=0.0,
        input_hash=f"sha256:{run_id}",
        raw_response="{}",
        scores=(CriterionScore("skill_match", 3.0, "resume-1#120-180"),),
    )


def record_both(recorder: AuditRecorder, conn, run_id: str) -> DecisionEvent:
    """正常路径：先真身（进事务、提交），再镜像（提交之后）。

    顺序不能反——design D1：允许的偏差只有单向「SQLite 有、JSONL 缺行」。
    """
    event = make_event(run_id)
    recorder.record(conn, event)
    conn.commit()
    recorder.mirror(event)
    return event


# ── 正常态：两条断言都绿 ────────────────────────────────────────────────

def test_both_assertions_pass_on_a_consistent_pair(recorder, conn):
    for run_id in ("run-1", "run-2", "run-3"):
        record_both(recorder, conn, run_id)

    reconciled = reconciliation_assertion(recorder)
    chained = chain_assertion(recorder)

    assert reconciled.ok is True
    assert reconciled.violations == ()
    assert chained.ok is True
    assert chained.violations == ()


def test_both_assertions_pass_on_an_empty_pair(recorder):
    """空库空文件：两条都通过。

    ⚠️ 这个绿色**不代表系统在正常留痕**——它和"什么都没发生过"是同一个
    颜色。真正的效力证据在下面那两条交叉反例，这条只是基线。
    """
    assert reconciliation_assertion(recorder).ok is True
    assert chain_assertion(recorder).ok is True


# ── 交叉反例一：链完好、镜像缺行 → 对账红，链绿 ────────────────────────

def test_missing_mirror_row_is_caught_by_reconcile_but_not_by_chain(recorder, conn):
    """崩溃窗口的真实形状：SQLite 写了、进程死在 append 之前。

    这时候链一点问题都没有（少写的那一行从来没进过链），verify_chain()
    永远是绿的。只有对账能发现"该留的痕少了一条"。
    这条用例就是 delivery-units.md §3.4「两条不可互相替代」的机器证据。
    """
    record_both(recorder, conn, "run-1")
    # run-2 只写真身，不写镜像——模拟两段之间崩溃
    orphan = make_event("run-2")
    recorder.record(conn, orphan)
    conn.commit()

    chained = chain_assertion(recorder)
    reconciled = reconciliation_assertion(recorder)

    assert chained.ok is True, "链本身没被改，verify_chain 不该红——它看不见这类问题"
    assert reconciled.ok is False, "镜像缺了一行，对账必须红"
    assert any("run-2" in str(v) for v in reconciled.violations)


def test_backfilled_missing_row_stops_being_reported(recorder, conn):
    """链尾补录之后，那条缺行不再算违例（Reconciliation.unexplained_missing）。

    已知且已登记的缺行一直算成违例，这条断言就会长期红着——红久了就没人
    看了，等于没有断言。补录走链尾 type=backfill，⛔ 不插回原位（插回必然断链）。
    """
    orphan = make_event("run-2")
    recorder.record(conn, orphan)
    conn.commit()
    assert reconciliation_assertion(recorder).ok is False

    recorder.backfill("run-2", reason="两段之间进程崩溃，镜像缺行")

    assert reconciliation_assertion(recorder).ok is True
    # 补录本身没有破坏链
    assert chain_assertion(recorder).ok is True


# ── 交叉反例二：镜像齐全但被篡改 → 链红，对账绿 ────────────────────────

def test_tampered_mirror_is_caught_by_chain_but_not_by_reconcile(
    recorder, conn, chain_path
):
    """改的是记录**内容**，id 集合一点没变——对账比的是 id 差集，看不见。

    只有哈希链能发现"这一行的字节被动过"。这是上一条反例的镜像方向：
    两条断言各自守着对方守不到的那一半。
    """
    for run_id in ("run-1", "run-2", "run-3"):
        record_both(recorder, conn, run_id)

    lines = chain_path.read_bytes().split(b"\n")
    record = json.loads(lines[0].decode("utf-8"))
    record["raw_response"] = "被人改过的响应"
    lines[0] = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    chain_path.write_bytes(b"\n".join(lines))

    chained = chain_assertion(recorder)
    reconciled = reconciliation_assertion(recorder)

    assert chained.ok is False, "中间一行被改，链必须红"
    assert chained.violations, "链断了必须指出断在哪一行"
    assert any("broken_at" in v for v in chained.violations)
    assert reconciled.ok is True, "id 集合没变，对账看不见内容篡改——这正是它的盲区"


def test_deleted_mirror_line_breaks_the_chain(recorder, conn, chain_path):
    """整行删除：链红（后继的 prev_hash 对不上），对账也红（id 少了一个）。

    两条同时红是正常的——"不可互相替代"说的是各有盲区，不是互斥。
    """
    for run_id in ("run-1", "run-2", "run-3"):
        record_both(recorder, conn, run_id)

    lines = [line for line in chain_path.read_bytes().split(b"\n") if line.strip()]
    chain_path.write_bytes(b"\n".join(lines[:1] + lines[2:]) + b"\n")

    assert chain_assertion(recorder).ok is False
    assert reconciliation_assertion(recorder).ok is False


# ── 结构 ───────────────────────────────────────────────────────────────

def test_two_assertions_have_distinct_names(recorder):
    """名字不同不是洁癖：CI 报告里靠 name 区分"链断了"和"痕少了"，
    两者的处置完全不同——前者要查谁改了文件，后者要查哪次写入没落地。"""
    assert reconciliation_assertion(recorder).name != chain_assertion(recorder).name
