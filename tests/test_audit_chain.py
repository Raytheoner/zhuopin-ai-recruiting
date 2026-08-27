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


# ── verify_chain()：四个攻击场景 ─────────────────────────────────────────


def _rewrite(path, objects: list[dict]) -> None:
    """按给定对象重写整个文件（模拟攻击者持有写权限）。"""
    payload = b"\n".join(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for obj in objects
    )
    path.write_bytes(payload + b"\n")


def test_intact_chain_passes_and_returns_total(chain_path):
    sink = JsonlChainSink(chain_path)
    for index in range(5):
        sink.write(_event(index))

    result = sink.verify_chain()
    assert result.ok is True
    assert result.total == 5
    assert result.broken_at is None


@pytest.mark.parametrize("prepare", ["missing", "empty"])
def test_missing_or_empty_file_passes_with_zero_total(chain_path, prepare):
    if prepare == "empty":
        chain_path.parent.mkdir(parents=True, exist_ok=True)
        chain_path.write_bytes(b"")

    result = JsonlChainSink(chain_path).verify_chain()
    assert result.ok is True
    assert result.total == 0


def test_modified_middle_line_breaks_at_the_next_line(chain_path):
    """
    spec「中间一行被修改」：校验失败并指出首个断链位置。
    改第 2 行 → 第 3 行的 prev_hash 对不上 → 首个断链位置是 3。
    """
    sink = JsonlChainSink(chain_path)
    for index in range(4):
        sink.write(_event(index))

    objects = _objects(chain_path)
    objects[1]["raw_response"] = '{"score": 5}'  # 篡改
    _rewrite(chain_path, objects)

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 3


def test_deleted_middle_line_breaks_at_that_position(chain_path):
    """spec「中间一行被删除」。删掉第 2 行后，原第 3 行落到第 2 位且 prev_hash 对不上。"""
    sink = JsonlChainSink(chain_path)
    for index in range(4):
        sink.write(_event(index))

    objects = _objects(chain_path)
    _rewrite(chain_path, objects[:1] + objects[2:])

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2


def test_all_prev_hash_fields_stripped_breaks_at_line_two(chain_path):
    """
    ⭐ 这条是这道防线的分水岭，不是"多写一个用例"（OP-0826-E §三 第 3 条）。

    攻击者删光镜像中所有记录的 prev_hash 字段，试图让整链因"字段缺失即豁免"
    而通过校验。平台侧踩过这个绕过，本仓库一次做对。

    ⚠️ 断言的是 broken_at == 2，**不是** ok is False：只断言 ok is False 的话，
    一个"任何 prev_hash 缺失都算断链（含第 1 行）"的实现也会绿，而那个实现违反
    spec「仅第 1 条记录可豁免（向前兼容既有文件）」。位置断言同时锁住了豁免的
    存在与豁免的边界。
    """
    sink = JsonlChainSink(chain_path)
    for index in range(4):
        sink.write(_event(index))

    objects = _objects(chain_path)
    for obj in objects:
        obj.pop("prev_hash")
    _rewrite(chain_path, objects)

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2
    assert "prev_hash" in (result.error or "")


def test_line_one_may_omit_prev_hash(chain_path):
    """spec：仅第 1 条记录可豁免（向前兼容既有文件）。单行文件缺字段应通过。"""
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))

    objects = _objects(chain_path)
    objects[0].pop("prev_hash")
    _rewrite(chain_path, objects)

    assert sink.verify_chain().ok is True


def test_non_json_line_is_reported_as_a_break(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    with open(chain_path, "ab") as handle:
        handle.write(b"not json at all\n")

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2


# ── 序列化鲁棒性（tasks 2.6）────────────────────────────────────────────


def test_chinese_and_escaped_newlines_do_not_false_alarm(chain_path):
    """
    spec「记录内容含中文与特殊字符」：链校验仍能正确通过，不因序列化差异误报。
    design D3 第 2 条：校验对磁盘原始字节重算，不做 JSON 解析后重新 dumps 的
    规范化——重排序、ensure_ascii 差异、空格差异都会让哈希对不上。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1, blocked_reason="缺少『AI 生成』标识\n第二行\t制表符"))
    sink.write(_event(2, blocked_reason="严重度未知——按拦截处理"))
    sink.write(_event(3, raw_response='{"评语": "熟悉 AUTOSAR，CAN 通信经验 3 年"}'))

    result = sink.verify_chain()
    assert result.ok is True
    assert result.total == 3
    # 一条含真实换行的记录仍然只占一行——json.dumps 把它转义成两个字符。
    assert len(_lines(chain_path)) == 3


def test_verification_is_byte_based_not_content_based(chain_path):
    """
    把第 1 行按不同的键顺序重新序列化：**内容完全一样、字节不同**。
    一个"解析后重新 dumps 再比"的实现会放过它；按字节算的实现必须在第 2 行报断。
    这条是 design D3 第 2 条的反向证明。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    objects = _objects(chain_path)
    reordered = json.dumps(objects[0], ensure_ascii=False, sort_keys=False, indent=None)
    rest = _lines(chain_path)[1:]
    chain_path.write_bytes(b"\n".join([reordered.encode("utf-8"), *rest]) + b"\n")

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2


def test_broken_at_reports_only_the_first_break(chain_path):
    sink = JsonlChainSink(chain_path)
    for index in range(6):
        sink.write(_event(index))

    objects = _objects(chain_path)
    objects[1]["raw_response"] = "tampered-a"
    objects[4]["raw_response"] = "tampered-b"
    _rewrite(chain_path, objects)

    # 改了第 2 行与第 5 行 → 第 3 行与第 6 行都对不上，只报第一处。
    assert sink.verify_chain().broken_at == 3


def test_tail_hash_matches_the_last_line_digest(chain_path):
    """
    已知边界：哈希链检不出**最后一行**被改（没有后继来暴露它）。返回 tail_hash
    让将来需要时可以把链尾锚定到外部。spec 未要求，U2 不做锚定本身。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    assert sink.verify_chain().tail_hash == hashlib.sha256(_lines(chain_path)[-1]).hexdigest()


def test_chain_stays_verifiable_after_more_appends(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    assert sink.verify_chain().ok is True

    sink.write(_event(2))
    result = sink.verify_chain()
    assert result.ok is True
    assert result.total == 2
