"""第 6 章·主动节流与退避序列（6.4 / 6.5 的时序部分）。

**本文件不 sleep 真时间。** 假时钟自己往前走——这不是为了跑得快，是为了让
"20 条/分钟"这条判据**能被验**：靠真等一分钟的断言最终都会被人跳过。
"""

from __future__ import annotations

import pytest

from tools.liaison.notify import ratelimit


class FakeClock:
    """单调钟 + sleep 的假体。`sleep` 直接把钟推到未来，⛔ 不真等。"""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, f"⛔ 不许睡负数：{seconds}"
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def bucket(clock):
    return ratelimit.make_group_webhook_bucket(monotonic=clock.monotonic, sleep=clock.sleep)


def test_backoff_sequence_is_exactly_1_2_4_8(clock):
    """D9 逐字：1s→2s→4s→8s，最多 4 次。"""
    assert [ratelimit.compute_backoff_delay(i) for i in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]
    assert ratelimit.MAX_RETRIES == 4


@pytest.mark.parametrize("bad", [0, -1, 5])
def test_backoff_index_outside_the_sequence_is_an_error(bad):
    """⛔ 不许"越界就返回最后一档"——那会把"最多 4 次"悄悄变成无限次。"""
    with pytest.raises(ValueError):
        ratelimit.compute_backoff_delay(bad)


def test_only_45009_is_the_rate_limit_signal():
    """限流码是**一个**确定的常量（D9 逐字）。

    ⚠️ "非 45009 的 errcode ⛔ 不重试、⛔ 不当成功"这条**判定**落在 Task 5 的
    `effect_deliver_with_backoff`（`webhook.py`），本模块只提供这个常量。
    这里钉住取值，⛔ 不要在本文件里补一个自造的判定函数——那会和 Task 5 的实现打架。
    """
    assert ratelimit.RATE_LIMIT_ERRCODE == 45009


def test_normal_rate_is_not_delayed(bucket, clock):
    """spec 场景「正常速率不受影响」：远低于上限时立即发送，无额外延迟。"""
    waits = [bucket.acquire() for _ in range(5)]
    assert waits == [0.0] * 5
    assert clock.slept == []


def test_a_full_bucket_lets_the_capacity_through_without_waiting(bucket):
    """容量 20（D9 逐字）：桶满时前 20 条不等待。"""
    waits = [bucket.acquire() for _ in range(ratelimit.GROUP_WEBHOOK_BUCKET_CAPACITY)]
    assert waits == [0.0] * 20


def test_the_twenty_first_send_waits_for_a_refilled_token(bucket, clock):
    """spec 场景「短时间内大量通知」：超出部分被**延后**，⛔ 不是被拒。"""
    for _ in range(ratelimit.GROUP_WEBHOOK_BUCKET_CAPACITY):
        bucket.acquire()
    waited = bucket.acquire()
    assert waited == pytest.approx(3.0)  # 60s / 20 个令牌
    assert clock.slept == [pytest.approx(3.0)]


def test_sustained_rate_never_exceeds_twenty_per_minute(bucket, clock):
    """稳态速率判据：突发窗口之后，相邻两条的间隔恒 ≥ 3 秒（= 20 条/分钟）。

    ⚠️ 口径说明：令牌桶**容量 20** 是 D9 逐字要求的，因此第一个窗口允许一次
    20 条的突发。这条断言因此从第 21 条开始验——⛔ 不要把它改成"任意 60 秒窗口
    内不超过 20 条"，那条更严的性质本实现按设计就不成立，写上去只会让人为了
    让它变绿去改容量，而容量是 design 定的。
    """
    sent_at: list[float] = []
    for _ in range(40):
        bucket.acquire()
        sent_at.append(clock.now)
    tail = sent_at[ratelimit.GROUP_WEBHOOK_BUCKET_CAPACITY :]
    gaps = [b - a for a, b in zip(tail, tail[1:])]
    assert gaps, "样本不足，断言没验到东西"
    assert all(gap >= 3.0 - 1e-9 for gap in gaps), gaps


def test_tokens_refill_over_time(clock):
    """闲一会儿就把令牌补回来，⛔ 不会补过容量上限。"""
    b = ratelimit.make_group_webhook_bucket(monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(20):
        b.acquire()
    clock.now += 30.0  # 30 秒补 10 个
    assert [b.acquire() for _ in range(10)] == [0.0] * 10
    assert b.acquire() > 0.0

    clock.now += 3600.0  # 闲很久
    assert [b.acquire() for _ in range(20)] == [0.0] * 20  # 最多补满 20 个
    assert b.acquire() > 0.0


def test_refill_math_is_a_pure_function():
    assert ratelimit.compute_refill_tokens(60.0, refill_tokens=20, refill_seconds=60.0) == 20.0
    assert ratelimit.compute_refill_tokens(3.0, refill_tokens=20, refill_seconds=60.0) == 1.0
    with pytest.raises(ValueError):
        ratelimit.compute_refill_tokens(-1.0, refill_tokens=20, refill_seconds=60.0)


def test_ratelimit_module_does_not_import_time():
    """结构性：时钟必须注入。模块自己 import time ⇒ "不 sleep 真时间"守不住。"""
    import ast
    import pathlib

    source = pathlib.Path(ratelimit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "time" not in imported, "⛔ ratelimit.py 不许 import time，时钟只能注入"
