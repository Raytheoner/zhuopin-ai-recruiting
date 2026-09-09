"""主动节流（令牌桶）与限流退避序列。

⛔ **本模块不 import `time`。** `monotonic` 与 `sleep` 全部构造时注入——理由不是
"方便测试"，是**测试必须不 sleep 真时间**（opener 约束 2）。真时钟一旦写死在模块
里，"20 条/分钟"这条判据就只能靠真等一分钟来验，于是它会被跳过，于是它形同虚设。

D9 逐字：令牌桶容量 20、按 20/60s 补充；收到 `errcode 45009` 时退避
1s→2s→4s→8s，最多 4 次。

⛔ 本目录禁止写 `with X:`（第 2 章事务扫描器），本模块用不到。
"""

from __future__ import annotations

from collections.abc import Callable

#: D9 逐字：企微群机器人 20 条/分钟。容量与补充速率是**两个**参数，
#: ⛔ 不要合并成"每 3 秒一个"——容量决定允许多大的突发，那是另一件事。
GROUP_WEBHOOK_BUCKET_CAPACITY = 20
GROUP_WEBHOOK_REFILL_TOKENS = 20
GROUP_WEBHOOK_REFILL_SECONDS = 60.0

#: 企微在超限时返回的 errcode（D9 逐字）。**只有这一个码触发退避重试**——
#: 其余错误码一律按"非限流错误"处理：记录、告警、⛔ 不重试、⛔ 不当作成功。
RATE_LIMIT_ERRCODE = 45009

#: D9 逐字的退避序列。⛔ 不许改成"指数计算出来"的形式——写死四个数，
#: 是为了让"最多 4 次"这件事在类型上就成立（序列长度即上限）。
BACKOFF_DELAYS_SECONDS = (1.0, 2.0, 4.0, 8.0)
MAX_RETRIES = len(BACKOFF_DELAYS_SECONDS)


def compute_backoff_delay(retry_index: int) -> float:
    """第 `retry_index` 次重试之前要等多久。`retry_index` **从 1 起**。

    ⚠️ 口径钉死：**首发不算重试**。"最多 4 次"指首发之后最多再发 4 次，退避序列
    1s→2s→4s→8s 各用一次，因此一条通知最多被发 5 次。
    ⛔ 不要改成"总共 4 次"——那会让 8s 这一档永远用不上，等于悄悄把退避上限砍掉一半。
    """
    if retry_index < 1 or retry_index > MAX_RETRIES:
        raise ValueError(f"重试序号超出 1..{MAX_RETRIES} 的范围：{retry_index}")
    return BACKOFF_DELAYS_SECONDS[retry_index - 1]


def compute_refill_tokens(
    elapsed_seconds: float, *, refill_tokens: int, refill_seconds: float
) -> float:
    """经过 `elapsed_seconds` 秒应当补充多少令牌。纯函数（铁律 2）。"""
    if elapsed_seconds < 0:
        raise ValueError(f"经过的时间不可能是负数：{elapsed_seconds}")
    if refill_seconds <= 0:
        raise ValueError(f"补充周期必须为正：{refill_seconds}")
    return elapsed_seconds * refill_tokens / refill_seconds


class TokenBucket:
    """发送前生效的主动节流。

    spec 逐字：节流 SHALL 在发送前生效，MUST NOT 依赖"先发出去、被拒了再说"。
    因此 `acquire()` 是**阻塞**语义（拿不到就等），⛔ 不是"拿不到就返回 False 让
    调用方决定"——把决定权交出去，第一个图省事的调用方就会直接发。
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_tokens: int,
        refill_seconds: float,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"桶容量必须为正：{capacity}")
        self._capacity = float(capacity)
        self._refill_tokens = refill_tokens
        self._refill_seconds = refill_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._tokens = float(capacity)
        self._updated_at = monotonic()

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = now - self._updated_at
        if elapsed <= 0:
            # 单调钟不倒流；相等时无事可做。⛔ 不要在这里 raise——
            # 某些平台上连续两次 monotonic() 返回同一个值是合法的。
            return
        gained = compute_refill_tokens(
            elapsed,
            # gitleaks:allow —— `<含 token 的关键字>=<值>` 命中 gitleaks 的
            # generic-api-key 规则（熵 3.52，阈值 3.5），这里是个关键字实参，
            # ⛔ 不是凭据。写这条豁免而不是把参数改名，是因为 `refill_tokens`
            # 是 D9 里"按 20/60s 补充"的直译，为了绕扫描器改名会让代码与 design 对不上。
            refill_tokens=self._refill_tokens,
            refill_seconds=self._refill_seconds,
        )
        self._tokens = min(self._capacity, self._tokens + gained)
        self._updated_at = now

    @property
    def available_tokens(self) -> float:
        """当前还剩多少令牌（先补一次再报）。**只读观测面**，⛔ 不许拿它做判断。

        它的存在是为了让 TD-26 ② 的判据钉在"服务端那边被打了几下"上，而不是
        `acquire()` 被调了几次——后者是实现细节，前者才是 D9 那条 20 条/分钟守的东西。
        ⛔ 不要在生产代码里写 `if bucket.available_tokens >= n:` 之类的先看后取：
        节流的语义是**阻塞取**，看一眼再自己决定发不发，等于把决定权交回给调用方。
        """
        self._refill()
        return self._tokens

    def acquire(self) -> float:
        """取一个令牌，返回**实际等待的秒数**（不需要等就是 0.0）。"""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        deficit = 1.0 - self._tokens
        wait = deficit * self._refill_seconds / self._refill_tokens
        self._sleep(wait)
        self._refill()
        # 睡够了就一定扣得动。⛔ 不要写成 `while self._tokens < 1: sleep(...)`：
        # 注入的假时钟如果不前进，那种写法会死循环，而死循环在无人值守的
        # 泳道里表现为"这一批永远不结束"，比一次断言失败难查得多。
        self._tokens = max(0.0, self._tokens - 1.0)
        return wait


def make_group_webhook_bucket(
    *, monotonic: Callable[[], float], sleep: Callable[[float], None]
) -> TokenBucket:
    """按 D9 的群 webhook 参数造一个桶。⛔ 参数写死在这里，不做成可配。"""
    return TokenBucket(
        capacity=GROUP_WEBHOOK_BUCKET_CAPACITY,
        refill_tokens=GROUP_WEBHOOK_REFILL_TOKENS,
        refill_seconds=GROUP_WEBHOOK_REFILL_SECONDS,
        monotonic=monotonic,
        sleep=sleep,
    )


# ---------------------------------------------------------------------------
# 进程级单例（TD-26 ①）
# ---------------------------------------------------------------------------

#: 群 webhook 的**唯一**令牌桶。⛔ 不许在模块外直接读写它，走 `get_group_webhook_bucket`。
_GROUP_WEBHOOK_BUCKET: TokenBucket | None = None
_GROUP_WEBHOOK_CLOCK: tuple[Callable[[], float], Callable[[float], None]] | None = None


def get_group_webhook_bucket(
    *, monotonic: Callable[[], float], sleep: Callable[[float], None]
) -> TokenBucket:
    """取群 webhook 的**进程级单例**令牌桶。生产代码取桶只走这一条路。

    🔴 **为什么必须是单例**：D9 的「20 条/分钟」是**服务端**的额度。两个调用方
    各 `make_group_webhook_bucket()` 一次就是两份配额，进程内看着都没超，服务端
    那头挨了 40 下——于是又被 `45009` 打回、退回"被平台拒了才知道"的老路，
    而主动限流的全部意义就是不走那条路。

    ⛔ **第二个调用方带着自己的时钟来一律 `raise`，不静默返回既有单例。**
    带自己的时钟＝它以为自己在造一个新桶；静默返回会让它的注入悄悄失效
    （测试里表现为假时钟推不动真桶，生产里表现为绕过单例这件事没人知道）。
    报出来的代价是一次明确的失败，吞掉的代价是配额翻倍且无症状。
    """
    global _GROUP_WEBHOOK_BUCKET, _GROUP_WEBHOOK_CLOCK
    if _GROUP_WEBHOOK_BUCKET is None:
        _GROUP_WEBHOOK_BUCKET = make_group_webhook_bucket(monotonic=monotonic, sleep=sleep)
        _GROUP_WEBHOOK_CLOCK = (monotonic, sleep)
        return _GROUP_WEBHOOK_BUCKET
    if _GROUP_WEBHOOK_CLOCK != (monotonic, sleep):
        raise RuntimeError(
            "群 webhook 令牌桶已经初始化过了，⛔ 不许用另一套时钟再取一次——"
            "那意味着有第二个调用方在造自己的桶，配额会翻倍。"
            "需要换时钟请先显式 reset_group_webhook_bucket()（仅限测试）。"
        )
    return _GROUP_WEBHOOK_BUCKET


def reset_group_webhook_bucket() -> None:
    """清掉单例。⛔ **只给测试用**，生产代码里出现即是缺陷。

    单例一旦被重置，之前发出去的量就不再计入新桶——在生产里那就是"手动给自己
    续了一份配额"。留这个口子是因为测试必须能换假时钟，⛔ 不是因为它有别的用途。
    """
    global _GROUP_WEBHOOK_BUCKET, _GROUP_WEBHOOK_CLOCK
    _GROUP_WEBHOOK_BUCKET = None
    _GROUP_WEBHOOK_CLOCK = None
