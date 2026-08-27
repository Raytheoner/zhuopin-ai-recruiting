import ast
import sqlite3
from pathlib import Path

import pytest

from app.audit.events import AI_ANALYSIS, CriterionScore, DecisionEvent
from app.audit.recorder import AuditRecorder, TransactionOwnershipError
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "audit" / "decisions.jsonl"


@pytest.fixture(autouse=True)
def _clear_class_state():
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


class CountingSink:
    """记账用的假 sink：只数被调用了几次，不落任何东西。"""

    def __init__(self):
        self.writes: list[DecisionEvent] = []

    def write(self, event):
        self.writes.append(event)
        return True

    def read_all(self):
        return []


def _event(**overrides) -> DecisionEvent:
    payload = {
        "id": "thread-1:effect_record_analysis:sha256:abc",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "application_id": "app-1",
        "configured_model": "deepseek-chat",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:abc",
        "raw_response": "{}",
        "scores": (CriterionScore("autosar", 3.0, "resume-1#1-20"),),
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


# ── 两段式：本单元的头号约束 ─────────────────────────────────────────────


def test_record_writes_sqlite_only(conn, chain_path):
    """
    第一段只碰真身。碰了镜像就意味着 append 发生在事务提交之前，
    回滚会留下「JSONL 有、SQLite 无」——design D1 明令更糟的偏差方向。
    """
    mirror = CountingSink()
    recorder = AuditRecorder(SqliteSink(conn), mirror)

    assert recorder.record(conn, _event()) is True

    assert mirror.writes == []
    assert not chain_path.exists()


def test_mirror_writes_jsonl_only(conn, chain_path):
    store = CountingSink()
    store.conn = conn  # 满足事务归属断言
    recorder = AuditRecorder(store, JsonlChainSink(chain_path))

    assert recorder.mirror(_event()) is True

    assert store.writes == []
    assert len(JsonlChainSink(chain_path).read_all()) == 1


def test_recorder_exposes_no_packed_method():
    """
    结构守护：recorder.py 里不存在任何一个函数体同时触碰两个 sink 的 write。
    将来有人"顺手加一个 record_all() 方便调用"，这条立刻变红。
    """
    source = (APP_ROOT / "audit" / "recorder.py").read_text(encoding="utf-8")

    assert _functions_touching_both_sinks(source) == []


def test_packed_method_detector_actually_detects():
    """
    ⭐ 阳性对照。没有它，上一条在"检查函数根本没生效"时同样是绿的——
    "0 命中"同时兼容"约束守住了"和"检查根本没跑"两种解释，那不叫验证
    （判据形状与 tasks 6.7 相同）。
    """
    offending = (
        "class X:\n"
        "    def record_all(self, conn, event):\n"
        "        self._store.write(event)\n"
        "        self._mirror.write(event)\n"
    )

    assert _functions_touching_both_sinks(offending) == ["record_all"]


def _functions_touching_both_sinks(source: str) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        touched = set()
        for inner in ast.walk(node):
            if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)):
                continue
            if inner.func.attr != "write":
                continue
            target = inner.func.value
            if isinstance(target, ast.Attribute):
                touched.add(target.attr)
        if {"_store", "_mirror"} <= touched:
            hits.append(node.name)
    return hits


# ── ⛔ 禁止在 effect_* 函数体内 append JSONL ─────────────────────────────


def _effect_functions_touching_the_mirror(source: str) -> list[str]:
    forbidden_calls = {"mirror", "backfill"}
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("effect_"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                if inner.func.attr in forbidden_calls:
                    hits.append(f"{node.name}:{inner.func.attr}")
            if isinstance(inner, ast.Name) and inner.id == "JsonlChainSink":
                hits.append(f"{node.name}:JsonlChainSink")
    return hits


def test_no_effect_function_appends_jsonl():
    """
    OP-0826-E §三 第 2 条：⛔ 禁止在 effect_* 函数体内 append JSONL。
    允许的偏差只有单向——「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据）。

    这条今天在 app/ 下是"恒真"的（还没有任何 effect_* 引用 audit）。它存在的
    意义是**在 U5 接线写错时立刻变红**，而不是今天证明了什么。
    """
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        offenders += [
            f"{path.name}:{hit}"
            for hit in _effect_functions_touching_the_mirror(path.read_text(encoding="utf-8"))
        ]

    assert offenders == []


@pytest.mark.parametrize(
    "offending,expected",
    [
        pytest.param(
            (
                "@idempotent_effect('effect_x')\n"
                "def effect_x(conn, *, thread_id, business_key, event):\n"
                "    recorder.record(conn, event)\n"
                "    recorder.mirror(event)\n"
            ),
            ["effect_x:mirror"],
            id="mirror_call",
        ),
        pytest.param(
            (
                "@idempotent_effect('effect_x')\n"
                "def effect_x(conn, *, thread_id, business_key, event):\n"
                "    recorder.record(conn, event)\n"
                "    recorder.backfill(event)\n"
            ),
            ["effect_x:backfill"],
            id="backfill_call",
        ),
        pytest.param(
            (
                "def effect_x(conn, *, thread_id, business_key, event):\n"
                "    sink = JsonlChainSink(path)\n"
                "    sink.write(event)\n"
            ),
            ["effect_x:JsonlChainSink"],
            id="jsonl_chain_sink_name",
        ),
    ],
)
def test_effect_mirror_detector_actually_detects(offending, expected):
    """
    ⭐ 阳性对照，理由同上。三个分支各一条用例——`.mirror(`、`.backfill(`、裸引用
    `JsonlChainSink` 这个名字——因为它们是检查函数里三条独立的命中路径，任何一条
    单独回归（比如漏掉 backfill 分支）都不会被另外两条盖住。
    """
    assert _effect_functions_touching_the_mirror(offending) == expected


# ── 事务归属（铁律 1）────────────────────────────────────────────────────


def test_record_rejects_a_foreign_connection(conn, tmp_path, chain_path):
    """
    传进来的 conn 与 sink 绑定的不是同一个对象 → 两个事务管理者。
    2026-08-13 那次丢 outbox 的事故就是这个形状（findings §8.5）。
    """
    other = get_connection(str(tmp_path / "other.db"))
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))

    with pytest.raises(TransactionOwnershipError):
        recorder.record(other, _event())


def test_record_does_not_commit(conn, tmp_path, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.record(conn, _event())

    onlooker = get_connection(conn.execute("PRAGMA database_list").fetchone()[2])
    assert onlooker.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


def test_record_propagates_storage_failure(conn, chain_path):
    """
    spec「留痕写入失败」：该次评分结果 MUST NOT 进入下游排序，失败可被观测、
    不静默丢弃。实现上 record() 抛异常，调用方不吞（design D1 末条）。
    """
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))

    with pytest.raises(sqlite3.IntegrityError):
        recorder.record(conn, _event(scores=(CriterionScore("autosar", 3.0, ""),)))


# ── 转发与边界 ───────────────────────────────────────────────────────────


def test_query_by_delegates_to_the_store(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.record(conn, _event(id="run-a", application_id="app-1"))
    recorder.record(conn, _event(id="run-b", application_id="app-2"))
    conn.commit()

    assert [record["id"] for record in recorder.query_by(application_id="app-2")] == ["run-b"]


def test_verify_integrity_delegates_to_the_mirror(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.mirror(_event(id="run-a"))
    recorder.mirror(_event(id="run-b"))

    result = recorder.verify_integrity()
    assert result.ok is True
    assert result.total == 2


def _modules_importing_config_or_graph(source: str) -> list[str]:
    """
    扫真正的 import 语句（`ast.Import` 与 `ast.ImportFrom` 两种节点都要覆盖），
    返回命中 `app.config` / `app.graph` 前缀的模块名列表。

    ⚠️ **不要**用 `"app.config" not in source` 这种子串扫描——写明这条规则的
    docstring 里就含 "app.config" 四个字，子串版会被自己的注释绊倒
    （2026-08-26 提取验证实测）。
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    return [name for name in imported if name.startswith(("app.config", "app.graph"))]


@pytest.mark.parametrize("module", ["events", "sinks", "recorder"])
def test_audit_module_imports_no_config_or_graph(module):
    """
    铁律 2 的落点：app/audit 是被 L4 调用的存储适配层，自己不决定何时被调用。
    import app.config 会让审计路径在启动时绑死配置、并让 U3 的注入点不再是唯一
    一处；import app.graph 是反向依赖。路径与连接一律由调用方传入。
    """
    source = (APP_ROOT / "audit" / f"{module}.py").read_text(encoding="utf-8")

    assert _modules_importing_config_or_graph(source) == []


@pytest.mark.parametrize(
    "offending,expected",
    [
        pytest.param("import app.config\n", ["app.config"], id="plain_import"),
        pytest.param(
            "from app.graph import build_graph\n", ["app.graph"], id="from_import"
        ),
    ],
)
def test_import_detector_actually_detects(offending, expected):
    """
    ⭐ 阳性对照。没有它，上一条在"检查函数漏掉 ast.ImportFrom / 把 startswith
    退化成精确匹配"时同样是绿的——那不是验证，是巧合。两条用例分别覆盖
    `ast.Import`（`import app.config`）与 `ast.ImportFrom`（`from app.graph
    import x`），因为它们是检查函数里两条独立的分支，一条回归不会被另一条盖住。
    """
    assert _modules_importing_config_or_graph(offending) == expected
