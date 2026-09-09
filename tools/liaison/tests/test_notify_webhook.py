"""第 6 章·通道装配（6.1 / 6.3 / 6.5 / 6.9 / 6.10 的端到端部分）。

**不发真网络、不 sleep 真时间**：transport 与时钟都是 fake，地址一律用
`https://example.invalid/...` 占位（6.10：版本管理里 ⛔ 不出现真实 URL）。
"""

from __future__ import annotations

import pytest

from tools.liaison.errors import MissingCredentialsError
from tools.liaison.notify import guard, ratelimit, store, transport, webhook
from tools.liaison.storage import db as liaison_db

FAKE_WEBHOOK = "https://example.invalid/cgi-bin/webhook/send?key=fake-key-for-tests"


class FakeTransport:
    """按剧本回应。`scripted` 是 errcode 序列，⛔ 用完即断言"发多了"。"""

    def __init__(self, scripted=None, *, upload_ok=True):
        self.scripted = list(scripted or [])
        self.json_calls: list[dict] = []
        self.multipart_calls: list[dict] = []
        self.upload_ok = upload_ok

    def _next(self) -> int:
        if not self.scripted:
            return 0
        return self.scripted.pop(0)

    def post_json(self, url, payload, *, timeout=transport.WEBHOOK_TIMEOUT_SECONDS):
        self.json_calls.append({"url": url, "payload": payload})
        errcode = self._next()
        if errcode == "boom":
            raise transport.WebhookTransportError("连接失败")
        return transport.WebhookResponse(
            errcode=errcode, errmsg="scripted", payload={"errcode": errcode}
        )

    def post_multipart(self, url, *, filename, content, timeout=transport.WEBHOOK_TIMEOUT_SECONDS):
        self.multipart_calls.append({"url": url, "filename": filename, "content": content})
        if not self.upload_ok:
            return transport.WebhookResponse(
                errcode=40058, errmsg="bad media", payload={"errcode": 40058}
            )
        return transport.WebhookResponse(
            errcode=0, errmsg="ok", payload={"errcode": 0, "media_id": "MEDIA-1"}
        )

    def sent_msgtypes(self) -> list[str]:
        return [c["payload"]["msgtype"] for c in self.json_calls]

    def sent_markdown_bodies(self) -> list[str]:
        """实际发出去的 markdown 正文。6.9-c 就是逐字比对这几段。"""
        return [
            c["payload"]["markdown"]["content"]
            for c in self.json_calls
            if c["payload"]["msgtype"] == "markdown"
        ]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def bucket(clock):
    return ratelimit.make_group_webhook_bucket(monotonic=clock.monotonic, sleep=clock.sleep)


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def make_sender(fake, *, attachment=True):
    return webhook.GroupWebhookSender(
        webhook_url=FAKE_WEBHOOK, transport=fake, attachment_supported=attachment
    )


def count_rows(conn, sql, params=()):
    """按位置取第一列。`select_pending_resends` 只在自己的 cursor 上设 row_factory，
    不会污染这条共享连接——但数数用 COUNT(*) 更直接，也不依赖它的排序与列集。
    """
    return conn.execute(sql, params).fetchone()[0]


def count_effect_log(conn):
    return count_rows(
        conn,
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?",
        ("effect_send_group_notify",),
    )


# ── 纯函数 ──────────────────────────────────────────────────────────────


def test_upload_url_swaps_the_path_and_keeps_the_key():
    url = webhook.compute_upload_url(FAKE_WEBHOOK)
    assert "/upload_media?" in url
    assert "/send?" not in url
    assert "key=fake-key-for-tests" in url
    assert url.endswith("&type=file")


def test_upload_url_refuses_an_unexpected_path():
    """⛔ 猜不出来就报错，不硬拼——拼错的地址会把附件发去一个未知端点。"""
    with pytest.raises(ValueError):
        webhook.compute_upload_url("https://example.invalid/cgi-bin/webhook/other?key=k")


def test_payloads_carry_no_recipient_field():
    """6.7：负载里 ⛔ 不存在 touser / toparty / totag。"""
    md = webhook.compute_markdown_payload("hi")
    fl = webhook.compute_file_payload("MID")
    for payload in (md, fl):
        assert not ({"touser", "toparty", "totag"} & set(payload))
    assert md == {"msgtype": "markdown", "markdown": {"content": "hi"}}
    assert fl == {"msgtype": "file", "file": {"media_id": "MID"}}


# ── 退避重试（6.5 / spec「限流后重试成功」「重试耗尽」「非限流错误」） ────


def test_rate_limited_then_success_delivers_exactly_one_notification(bucket, clock):
    fake = FakeTransport([45009, 0])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(
            make_sender(fake),
            guard.compute_notify_plan("短", limit_bytes=4096, attachment_supported=True),
        ),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is True
    assert outcome.attempts == 2
    assert clock.slept == [1.0]
    # spec 逐字：「不产生重复的通知」——被限流那次**没有送达**，
    # 所以真正送达的仍然只有一条。
    assert len(fake.json_calls) == 2
    assert fake.scripted == []


def test_exhausted_retries_report_the_last_errcode(bucket, clock):
    """D9 逐字：1s→2s→4s→8s，最多 4 次 ⇒ 首发 + 4 次重试 = 最多 5 次发送。"""
    fake = FakeTransport([45009] * 6)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(
            make_sender(fake),
            guard.compute_notify_plan("短", limit_bytes=4096, attachment_supported=True),
        ),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is False
    assert outcome.attempts == 5
    assert outcome.last_errcode == 45009
    assert clock.slept == [1.0, 2.0, 4.0, 8.0]
    assert len(fake.json_calls) == 5


def test_non_rate_limit_error_is_not_retried_and_not_success(bucket, clock):
    """spec 场景「非限流错误」：被记录、⛔ 不当作成功、⛔ 不静默丢弃。"""
    fake = FakeTransport([93000])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(
            make_sender(fake),
            guard.compute_notify_plan("短", limit_bytes=4096, attachment_supported=True),
        ),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is False
    assert outcome.attempts == 1
    assert outcome.last_errcode == 93000
    assert clock.slept == [], "非限流错误 ⛔ 不许退避重试"


def test_transport_failure_is_not_retried_and_not_success(bucket, clock):
    fake = FakeTransport(["boom"])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(
            make_sender(fake),
            guard.compute_notify_plan("短", limit_bytes=4096, attachment_supported=True),
        ),
        bucket=bucket,
        sleep=clock.sleep,
    )
    assert outcome.delivered is False
    assert outcome.last_errcode is None
    assert "连接失败" in outcome.last_error


def test_every_attempt_takes_a_token_from_the_bucket(clock):
    """节流在**发送前**生效（spec 逐字），因此每一次尝试都要先取令牌。"""
    taken = {"n": 0}

    class CountingBucket:
        def acquire(self) -> float:
            taken["n"] += 1
            return 0.0

    fake = FakeTransport([45009, 45009, 0])
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DirectDelivery(
            make_sender(fake),
            guard.compute_notify_plan("短", limit_bytes=4096, attachment_supported=True),
        ),
        bucket=CountingBucket(),
        sleep=clock.sleep,
    )
    assert outcome.attempts == 3
    assert taken["n"] == 3


# ── 投递形态的选取（TD-27） ────────────────────────────────────────────


def _reject_plan():
    """无附件承载 + 超限 = `MODE_REJECT`，`body` 为空。"""
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=False)
    assert plan.mode == guard.MODE_REJECT
    assert plan.body == "", "前置条件：reject 模式下没有可发的正文"
    return plan


def test_reject_mode_refuses_to_produce_a_delivery_object():
    """TD-27：拒发 ⛔ 不许静默降级成 `DirectDelivery`——那会往群里发一条空 markdown。

    「到不了这里」原先只由调用方保证。本函数是公开的，结构必须自己守住这个前提，
    否则第二个调用方一出现，"拒发"就悄悄变成"发一条看起来成功的空消息"。
    """
    fake = FakeTransport([])
    with pytest.raises(ValueError) as excinfo:
        webhook.make_group_webhook_delivery(make_sender(fake, attachment=False), _reject_plan())
    assert "调用方必须提前短路" in str(excinfo.value)
    # 抛错发生在任何 HTTP 之前：一次都没发出去。
    assert fake.json_calls == [] and fake.multipart_calls == []


@pytest.mark.parametrize(
    ("attachment_supported", "text", "expected"),
    [
        (True, "短通知", webhook.DirectDelivery),
        (True, "中" * 2000, webhook.DegradedDelivery),
    ],
    ids=["direct", "degraded"],
)
def test_the_other_two_modes_still_pick_their_delivery_form(
    attachment_supported, text, expected
):
    """TD-27 只收紧 reject 分支，⛔ 不动 direct / degraded 的正路。"""
    plan = guard.compute_notify_plan(
        text, limit_bytes=4096, attachment_supported=attachment_supported
    )
    delivery = webhook.make_group_webhook_delivery(
        make_sender(FakeTransport([]), attachment=attachment_supported), plan
    )
    assert isinstance(delivery, expected)


# ── 降级投递的断点续发（6.3 / 6.9） ─────────────────────────────────────


def test_degraded_delivery_sends_the_file_then_the_summary(bucket, clock):
    fake = FakeTransport([0, 0])
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=True)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DegradedDelivery(make_sender(fake), plan), bucket=bucket, sleep=clock.sleep
    )
    assert outcome.delivered is True
    # 顺序钉死：先附件、后提要。反过来会在群里留下一条声称"见附件"却没有附件的提要。
    assert fake.sent_msgtypes() == ["file", "markdown"]
    assert fake.multipart_calls[0]["content"] == "中" * 2000
    assert fake.multipart_calls[0]["filename"] == plan.attachment_filename


def test_retry_after_a_partial_degraded_delivery_never_resends_the_file(bucket, clock):
    """断点续发：提要被限流后重试，⛔ 不许把已经送达的附件再发一遍。"""
    fake = FakeTransport([0, 45009, 0])
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=True)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DegradedDelivery(make_sender(fake), plan), bucket=bucket, sleep=clock.sleep
    )
    assert outcome.delivered is True
    assert fake.sent_msgtypes() == ["file", "markdown", "markdown"]
    assert len(fake.multipart_calls) == 1, "附件 ⛔ 只上传一次"


def test_failed_upload_is_a_delivery_failure_not_a_silent_send(bucket, clock):
    fake = FakeTransport([0], upload_ok=False)
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=True)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DegradedDelivery(make_sender(fake), plan), bucket=bucket, sleep=clock.sleep
    )
    assert outcome.delivered is False
    assert fake.json_calls == [], "附件没传上去 ⇒ ⛔ 一条群消息都不许发"


class NoMediaIdTransport(FakeTransport):
    """`errcode=0` 但**响应里没有 media_id**——企微侧"成功了但没给东西"的形状。

    ⛔ 不要合并进 `upload_ok=False`：那条走的是 `not response.ok`，是**另一道**
    守卫。这条专打 `webhook.py` 里 `if not media_id: raise` 那一句。
    """

    def post_multipart(self, url, *, filename, content, timeout=None):
        self.multipart_calls.append({"url": url, "filename": filename, "content": content})
        return transport.WebhookResponse(errcode=0, errmsg="ok", payload={"errcode": 0})


def test_upload_without_media_id_is_refused_not_treated_as_success():
    """errcode=0 却没有 media_id ⇒ ⛔ 不当作成功。

    删掉 `webhook.py` 的 `if not media_id: raise` 本条必红——`str(None)` 会得到
    `"None"` 这个看起来挺像 media_id 的字符串，后面 `send_file("None")` 照发不误，
    群里就出现一条声称"完整正文见附件"却根本没有附件的通知，也就是一句谎。
    """
    fake = NoMediaIdTransport()
    with pytest.raises(transport.WebhookTransportError) as exc:
        make_sender(fake).publish_attachment(filename="a.md", content="x")
    assert "media_id" in str(exc.value)


def test_missing_media_id_stops_the_whole_degraded_delivery(bucket, clock):
    """守卫真的挡在投递路径上：没有 media_id ⇒ ⛔ 一条群消息都不许发。"""
    fake = NoMediaIdTransport()
    plan = guard.compute_notify_plan("中" * 2000, limit_bytes=4096, attachment_supported=True)
    outcome = webhook.effect_deliver_with_backoff(
        webhook.DegradedDelivery(make_sender(fake), plan), bucket=bucket, sleep=clock.sleep
    )
    assert outcome.delivered is False
    assert fake.json_calls == []


# ── 端到端（守卫 → 降级 → 落库 → 告警） ────────────────────────────────


def test_short_notify_goes_out_directly_and_is_recorded(conn, bucket, clock):
    fake = FakeTransport([0])
    state = webhook.send_group_notify(
        conn,
        text="值守通知",
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=RecordingSink(),
        sleep=clock.sleep,
    )
    assert state == store.STATE_SENT
    assert fake.sent_msgtypes() == ["markdown"]
    row = conn.execute("SELECT state, mode, channel FROM liaison_group_notify").fetchone()
    assert row == (store.STATE_SENT, guard.MODE_DIRECT, guard.GROUP_WEBHOOK_CHANNEL.name)


def test_long_notify_is_degraded_and_the_full_text_is_retrievable(conn, bucket, clock):
    """spec 场景「超长内容降级成功」：群里收到提要与附件，完整内容可从附件取回。"""
    text = "中" * 2000
    fake = FakeTransport([0, 0])
    state = webhook.send_group_notify(
        conn,
        text=text,
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=RecordingSink(),
        sleep=clock.sleep,
    )
    assert state == store.STATE_SENT
    assert fake.multipart_calls[0]["content"] == text
    summary = fake.json_calls[1]["payload"]["markdown"]["content"]
    assert guard.compute_byte_length(summary) <= guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    assert "降级" in summary
    # 台账里存的是完整原文，⛔ 不是提要
    assert conn.execute("SELECT body FROM liaison_group_notify").fetchone()[0] == text


def test_undegradable_notify_is_rejected_alerted_and_never_sent(conn, bucket, clock):
    """spec 场景「无法降级时拒发」：不发送 + 告警说明拒发原因。"""
    fake = FakeTransport([0])
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn,
        text="中" * 2000,
        sender=make_sender(fake, attachment=False),
        bucket=bucket,
        alert_sink=sink,
        sleep=clock.sleep,
    )
    assert state == store.STATE_REJECTED
    assert fake.json_calls == [] and fake.multipart_calls == []
    assert len(sink.texts) == 1
    assert guard.REJECT_NO_ATTACHMENT_CHANNEL in sink.texts[0]


def test_exhausted_notify_lands_as_pending_resend_with_an_alert(conn, bucket, clock):
    fake = FakeTransport([45009] * 6)
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn,
        text="值守通知",
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=sink,
        sleep=clock.sleep,
    )
    assert state == store.STATE_PENDING_RESEND
    assert len(store.select_pending_resends(conn)) == 1
    assert len(sink.texts) == 1


def test_missing_webhook_env_refuses_to_build_a_sender():
    """6.10 端到端：环境变量未配置 → 拒发并告知缺失项，⛔ 不静默跳过后报成功。"""
    fake = FakeTransport([0])
    with pytest.raises(MissingCredentialsError) as excinfo:
        webhook.build_group_webhook_sender(env={}, transport=fake)
    assert "HR_LIAISON_GROUP_WEBHOOK" in str(excinfo.value)
    assert fake.json_calls == [], "⛔ 没有地址时一条消息都不许发出去"


def test_building_a_sender_from_the_environment_uses_the_configured_url():
    fake = FakeTransport([0])
    sender = webhook.build_group_webhook_sender(
        env={"HR_LIAISON_GROUP_WEBHOOK": FAKE_WEBHOOK}, transport=fake
    )
    sender.send_markdown("hi")
    assert fake.json_calls[0]["url"] == FAKE_WEBHOOK


# ── opener 点名要的七条（数值全部打印出来，供收工报告逐条核对） ──────────


def test_6_9_a_degraded_success_keeps_the_full_text_retrievable_from_the_ledger(
    conn, bucket, clock
):
    """🔴 6.9-a：超长正文降级成"提要 + 附件"并送达，**完整原文可从台账 `body` 取回**。"""
    text = "".join(f"第{i}行值守明细内容\n" for i in range(400))
    assert guard.compute_byte_length(text) > guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES

    fake = FakeTransport([0, 0])
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn,
        text=text,
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=sink,
        sleep=clock.sleep,
    )

    ledger_body = conn.execute("SELECT body FROM liaison_group_notify").fetchone()[0]
    print(
        "[6.9-a] state=", state,
        " mode=", conn.execute("SELECT mode FROM liaison_group_notify").fetchone()[0],
        " msgtypes=", fake.sent_msgtypes(),
        " 原文字节=", guard.compute_byte_length(text),
        " 台账body==原文 →", ledger_body == text,
        " 告警条数=", len(sink.texts),
        sep="",
    )
    assert state == store.STATE_SENT
    assert fake.sent_msgtypes() == ["file", "markdown"]
    assert fake.multipart_calls[0]["content"] == text
    assert ledger_body == text, "台账 body 必须是完整原文，⛔ 不是提要"
    assert sink.texts == [], "成功送达 ⛔ 不该告警"


def test_6_9_b_undegradable_is_rejected_with_exactly_one_alert_and_zero_transport_calls(
    conn, bucket, clock
):
    """🔴 6.9-b：提要无附件承载 → `rejected` + 告警恰 1 条 + transport 一次都没被调用。"""
    fake = FakeTransport([0])
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn,
        text="中" * 3000,
        sender=make_sender(fake, attachment=False),
        bucket=bucket,
        alert_sink=sink,
        sleep=clock.sleep,
    )
    print(
        "[6.9-b] state=", state,
        " 告警条数=", len(sink.texts),
        " json_calls=", fake.json_calls,
        " multipart_calls=", fake.multipart_calls,
        " 台账行数=", count_rows(conn, "SELECT COUNT(*) FROM liaison_group_notify"),
        sep="",
    )
    assert state == store.STATE_REJECTED
    assert len(sink.texts) == 1, "拒发必须恰好告警一条"
    assert fake.json_calls == [] and fake.multipart_calls == [], "拒发时 transport ⛔ 零调用"
    assert count_rows(conn, "SELECT COUNT(*) FROM liaison_group_notify WHERE state = ?",
                      (store.STATE_REJECTED,)) == 1


@pytest.mark.parametrize("text", ["短短的值守通知", "中" * 2000])
def test_6_9_c_what_goes_out_is_either_the_original_or_the_declared_summary(
    conn, bucket, clock, text
):
    """🔴 6.9-c：发出去的 markdown 正文只可能是**原文**或**带降级声明的提要**。

    ⛔ 不存在第三种。特别地，"截断后的原文前 N 字节"必须让这条测试红——
    下面把那个候选算出来，逐字确认它**不是**实际发出去的内容。
    """
    plan = guard.compute_notify_plan(
        text,
        limit_bytes=guard.GROUP_WEBHOOK_CHANNEL.limit_bytes,
        attachment_supported=True,
    )
    fake = FakeTransport([0, 0])
    webhook.send_group_notify(
        conn,
        text=text,
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=RecordingSink(),
        sleep=clock.sleep,
    )
    bodies = fake.sent_markdown_bodies()
    assert len(bodies) == 1
    actual = bodies[0]

    # 静默截断的样子：原文按字节裁到通道上限，**不带任何声明**。
    silent_truncation = guard.compute_text_prefix_within_bytes(
        text, guard.GROUP_WEBHOOK_CHANNEL.limit_bytes
    )
    print(
        "[6.9-c] mode=", plan.mode,
        " 实发==原文 →", actual == text,
        " 实发==提要(plan.body) →", actual == plan.body,
        " 实发==静默截断候选 →", actual == silent_truncation,
        " 实发字节=", guard.compute_byte_length(actual),
        " 原文字节=", guard.compute_byte_length(text),
        sep="",
    )
    assert actual in (text, plan.body), "⛔ 只可能是原文或提要，不存在第三种"
    if plan.mode == guard.MODE_DEGRADED:
        assert actual == plan.body
        assert actual != text
        assert actual != silent_truncation, "⛔ 绝不静默截断"
        assert actual.startswith("【内容超长已降级】"), "提要必须带显式的裁剪声明"
        assert fake.multipart_calls[0]["content"] == text
    else:
        assert actual == text


def test_6_5_retry_then_success_writes_exactly_one_effect_log_and_one_ledger_row(
    conn, bucket, clock
):
    """🔴 6.5：脚本 `[45009, 0]` → 送达，且 `effect_log` 与台账**各恰 1 行**。"""
    fake = FakeTransport([45009, 0])
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn,
        text="值守通知·限流后重试成功",
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=sink,
        sleep=clock.sleep,
    )
    effect_rows = count_effect_log(conn)
    ledger_rows = count_rows(conn, "SELECT COUNT(*) FROM liaison_group_notify")
    print(
        "[6.5-成功] state=", state,
        " effect_log行数=", effect_rows,
        " liaison_group_notify行数=", ledger_rows,
        " sleep时长序列=", clock.slept,
        " json_calls=", len(fake.json_calls),
        " attempts=", conn.execute("SELECT attempts FROM liaison_group_notify").fetchone()[0],
        " 告警条数=", len(sink.texts),
        sep="",
    )
    assert state == store.STATE_SENT
    assert effect_rows == 1
    assert ledger_rows == 1
    assert effect_rows == ledger_rows, "铁律 1 恒等式：effect_log 行数 == 业务表行数"
    assert clock.slept == [1.0]
    assert len(fake.json_calls) == 2
    assert sink.texts == []


def test_6_5_exhausted_retries_land_one_pending_resend_row_with_exactly_one_alert(
    conn, bucket, clock
):
    """🔴 6.5：脚本 `[45009]*5` → `pending_resend` 恰 1 行 + 告警恰 1 条，⛔ 不静默丢弃。"""
    fake = FakeTransport([45009] * 5)
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn,
        text="值守通知·重试耗尽",
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=sink,
        sleep=clock.sleep,
    )
    pending_rows = count_rows(
        conn, "SELECT COUNT(*) FROM liaison_group_notify WHERE state = ?",
        (store.STATE_PENDING_RESEND,),
    )
    effect_rows = count_effect_log(conn)
    print(
        "[6.5-耗尽] state=", state,
        " pending_resend行数=", pending_rows,
        " effect_log行数=", effect_rows,
        " sleep时长序列=", clock.slept,
        " json_calls=", len(fake.json_calls),
        " 告警条数=", len(sink.texts),
        " 告警原文=", sink.texts[0] if sink.texts else None,
        sep="",
    )
    assert state == store.STATE_PENDING_RESEND
    assert pending_rows == 1
    assert effect_rows == 1
    assert clock.slept == [1.0, 2.0, 4.0, 8.0]
    assert len(fake.json_calls) == 5
    assert len(sink.texts) == 1
    assert len(store.select_pending_resends(conn)) == 1


def test_6_5_non_rate_limit_errcode_is_never_retried_and_never_reported_as_sent(
    conn, bucket, clock
):
    """🔴 非限流错误：脚本 `[93000]` → ⛔ 不重试、⛔ 不判成功、`last_errcode == 93000`。"""
    fake = FakeTransport([93000])
    sink = RecordingSink()
    state = webhook.send_group_notify(
        conn,
        text="值守通知·非限流错误",
        sender=make_sender(fake),
        bucket=bucket,
        alert_sink=sink,
        sleep=clock.sleep,
    )
    row = conn.execute(
        "SELECT state, attempts, last_errcode, sent_at FROM liaison_group_notify"
    ).fetchone()
    print(
        "[非限流] state=", state,
        " 台账行=", row,
        " sleep时长序列=", clock.slept,
        " json_calls=", len(fake.json_calls),
        " 告警条数=", len(sink.texts),
        sep="",
    )
    assert state != store.STATE_SENT, "⛔ 非限流错误不许被当成成功"
    assert state == store.STATE_PENDING_RESEND
    assert clock.slept == [], "⛔ 非限流错误一次退避都不许有"
    assert len(fake.json_calls) == 1, "⛔ 只发一次，不重试"
    assert row[1] == 1
    assert row[2] == 93000
    assert row[3] is None, "未送达 ⇒ ⛔ 不许有 sent_at"
    assert len(sink.texts) == 1


def test_6_10_missing_env_raises_and_names_the_variable_without_reporting_success():
    """🔴 6.10 端到端：`HR_LIAISON_GROUP_WEBHOOK` 缺失 → 抛错并点名变量，⛔ 不返回成功。"""
    fake = FakeTransport([0])
    sender = None
    with pytest.raises(MissingCredentialsError) as excinfo:
        sender = webhook.build_group_webhook_sender(env={}, transport=fake)
    message = str(excinfo.value)
    print(
        "[6.10] 异常类型=", type(excinfo.value).__name__,
        " 含变量名 →", "HR_LIAISON_GROUP_WEBHOOK" in message,
        " missing_names=", excinfo.value.missing_names,
        " sender=", sender,
        " json_calls=", fake.json_calls,
        " multipart_calls=", fake.multipart_calls,
        sep="",
    )
    assert "HR_LIAISON_GROUP_WEBHOOK" in message
    assert excinfo.value.missing_names == ("HR_LIAISON_GROUP_WEBHOOK",)
    assert sender is None, "⛔ 没有地址时不许返回一个能用的 sender"
    assert fake.json_calls == [] and fake.multipart_calls == []
    # 空白字符串同样算"没配"——⛔ 不许被当成一个合法地址。
    with pytest.raises(MissingCredentialsError):
        webhook.build_group_webhook_sender(
            env={"HR_LIAISON_GROUP_WEBHOOK": "   "}, transport=fake
        )


# ── 8.7·二进制附件与 20MB 上限（docx 群发的前置） ────────────────────────


def test_publish_attachment_passes_bytes_through_untouched():
    """docx 的字节流一路不失真地到达 transport。

    判据里那个 0x89 不是合法 UTF-8：实现里只要有一次 decode 往返，本条当场炸。
    """
    blob = b"PK\x03\x04\x89\xff\x00docx"
    fake = FakeTransport()
    media_id = make_sender(fake).publish_attachment(filename="附件.docx", content=blob)
    assert media_id == "MEDIA-1"
    assert fake.multipart_calls[0]["content"] == blob
    assert fake.multipart_calls[0]["filename"] == "附件.docx"


def test_oversized_attachment_is_refused_before_any_network_call():
    """超过企微 20MB 上限 ⇒ **提前拒绝**，⛔ 不发出去再看错误码。

    「发出去再看错误码」在这条通道上尤其糟：附件是降级投递的**第一步**，它失败
    之后提要那条不会发，于是一次 20MB 的误传换来的是一整条通知彻底没发出去，
    而现场只留下一个企微的数字错误码——没人能从那个码看出"文件太大"。
    """
    fake = FakeTransport()
    with pytest.raises(transport.WebhookTransportError) as exc:
        make_sender(fake).publish_attachment(
            filename="巨大.docx", content=b"x" * (webhook.MAX_ATTACHMENT_BYTES + 1)
        )
    message = str(exc.value)
    assert "20" in message and str(webhook.MAX_ATTACHMENT_BYTES + 1) in message, (
        f"报错必须写明上限与实际大小，否则看的人不知道该砍到多少：{message}"
    )
    assert fake.multipart_calls == [], "超限 ⇒ ⛔ 一个字节都不许发出去"
    assert fake.json_calls == []


def test_attachment_exactly_at_the_limit_is_allowed():
    """边界另一侧：正好等于上限**放行**。

    ⛔ 不能写成 `>=`——那会把一个企微本来收得下的附件挡在门外，而症状是
    "偶尔发不出去"，比超限本身更难查。
    """
    fake = FakeTransport()
    media_id = make_sender(fake).publish_attachment(
        filename="刚好.docx", content=b"x" * webhook.MAX_ATTACHMENT_BYTES
    )
    assert media_id == "MEDIA-1"


def test_size_guard_measures_bytes_not_characters():
    """`str` 正文按 **UTF-8 编码后的字节数**算，⛔ 不按字符数。

    中文一个字 3 字节：按字符数算会让一份 60MB 的中文正文一路溜到企微那头。
    """
    fake = FakeTransport()
    oversized = "中" * (webhook.MAX_ATTACHMENT_BYTES // 3 + 1)
    assert len(oversized) < webhook.MAX_ATTACHMENT_BYTES, "本用例的前提：字符数没超"
    with pytest.raises(transport.WebhookTransportError):
        make_sender(fake).publish_attachment(filename="中文.md", content=oversized)
    assert fake.multipart_calls == []
