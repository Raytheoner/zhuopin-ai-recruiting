import hashlib
import json
import threading

import pytest

from app.audit.events import AI_ANALYSIS, OUTBOUND_BLOCKED, DecisionEvent
from app.audit.sinks import GENESIS_PREV_HASH, JsonlChainSink


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "audit" / "decisions.jsonl"


@pytest.fixture(autouse=True)
def _clear_class_state():
    """
    锁与游标是类级共享状态（按解析后的绝对路径）。tmp_path 每个用例都不同，
    理论上不会串；显式清一遍是为了让"游标缺失走磁盘重算"这条路径可被主动构造。
    """
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


def _event(index: int, **overrides) -> DecisionEvent:
    payload = {
        "id": f"run-{index}",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "configured_model": "deepseek-chat",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": f"sha256:{index}",
        "raw_response": "{}",
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


def _lines(path) -> list[bytes]:
    return [line for line in path.read_bytes().split(b"\n") if line]


def _objects(path) -> list[dict]:
    return [json.loads(line.decode("utf-8")) for line in _lines(path)]


def test_write_returns_true_and_creates_parent_directory(chain_path):
    sink = JsonlChainSink(chain_path)

    assert sink.write(_event(1)) is True
    assert chain_path.exists()


def test_first_line_carries_the_genesis_sentinel(chain_path):
    JsonlChainSink(chain_path).write(_event(1))

    assert _objects(chain_path)[0]["prev_hash"] == GENESIS_PREV_HASH


def test_second_line_prev_hash_is_sha256_of_the_first_line_bytes(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    lines = _lines(chain_path)
    assert _objects(chain_path)[1]["prev_hash"] == hashlib.sha256(lines[0]).hexdigest()


def test_file_uses_lf_only_no_crlf(chain_path):
    """
    部署约束 4：目标服务器是 Windows。文本模式会把 "\\n" 翻译成 \\r\\n 落盘，
    链在 Mac 上全绿、推到 .51 上整条报断。必须二进制 I/O。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    assert b"\r\n" not in chain_path.read_bytes()


def test_cursor_miss_recomputes_from_disk_tail(chain_path):
    """
    tasks 2.3 逐字：缓存缺失时**从磁盘末行重算**而非当 genesis。
    当成 genesis 的话，进程重启后第一行的 prev_hash 会是 64 个 0，链从那行起
    永久断裂，而且**写入时不报错**——要等某次 verify_chain() 才发现。
    """
    JsonlChainSink(chain_path).write(_event(1))
    JsonlChainSink._CURSORS.clear()  # 模拟进程重启

    JsonlChainSink(chain_path).write(_event(2))

    lines = _lines(chain_path)
    assert _objects(chain_path)[1]["prev_hash"] == hashlib.sha256(lines[0]).hexdigest()
    assert _objects(chain_path)[1]["prev_hash"] != GENESIS_PREV_HASH


def test_two_instances_alternating_keep_one_chain(chain_path):
    """design D3 配套细节：两个指向同一文件的 sink 实例交替写不断链。"""
    first, second = JsonlChainSink(chain_path), JsonlChainSink(chain_path)
    first.write(_event(1))
    second.write(_event(2))
    first.write(_event(3))

    lines = _lines(chain_path)
    objects = _objects(chain_path)
    for index in range(1, len(lines)):
        assert objects[index]["prev_hash"] == hashlib.sha256(lines[index - 1]).hexdigest()


def test_relative_and_absolute_paths_share_one_lock(tmp_path, monkeypatch):
    """
    锁与游标按**解析后的绝对路径**共享。按传进来的字符串共享的话，
    JsonlChainSink("data/x.jsonl") 与 JsonlChainSink("/abs/data/x.jsonl") 会拿到
    两把不同的锁，写同一个文件——互斥失效且不报错。
    """
    monkeypatch.chdir(tmp_path)
    relative = JsonlChainSink("audit/decisions.jsonl")
    absolute = JsonlChainSink(tmp_path / "audit" / "decisions.jsonl")

    assert relative._key == absolute._key


def test_concurrent_appends_do_not_interleave(chain_path):
    """tasks 2.7：多线程并发 append 同一文件，行不穿插。"""
    sink = JsonlChainSink(chain_path)
    errors: list[BaseException] = []

    def worker(base: int) -> None:
        try:
            for offset in range(10):
                sink.write(_event(base * 10 + offset))
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常必须带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    objects = _objects(chain_path)
    assert len(objects) == 80
    assert len({obj["id"] for obj in objects}) == 80
    lines = _lines(chain_path)
    for index in range(1, len(lines)):
        assert objects[index]["prev_hash"] == hashlib.sha256(lines[index - 1]).hexdigest()


def test_read_all_returns_every_line_including_prev_hash(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2, event_type=OUTBOUND_BLOCKED, message_type="rejection_letter"))

    records = sink.read_all()
    assert [record["id"] for record in records] == ["run-1", "run-2"]
    assert records[1]["event_type"] == OUTBOUND_BLOCKED
    assert "prev_hash" in records[0]
