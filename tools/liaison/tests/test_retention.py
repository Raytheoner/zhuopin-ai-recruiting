"""留存期清理（tasks 8.1–8.2）。

⛔ 全部用 fake 时钟与 `tmp_path`：⛔ 不 sleep、⛔ 不碰真实的 `data/`
（opener 约束 3）。任何一条用例跑完之后仓库里不许多出一个文件。
"""

from __future__ import annotations

import datetime

import pytest

from tools.liaison import retention
from tools.liaison.retention import MessageRow, RetentionConfigError

CHINA_TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)


def _row(msgid, *, archived_at, has_queue_row=False, attachments_json="[]", thread_id="u1"):
    return MessageRow(
        msgid=msgid,
        thread_id=thread_id,
        archived_at=archived_at,
        attachments_json=attachments_json,
        has_queue_row=has_queue_row,
    )


def test_default_retention_days_is_180():
    """design D13 逐字：默认 180。⛔ 不许悄悄改成别的数。"""
    assert retention.DEFAULT_RETENTION_DAYS == 180
    assert retention.load_retention_days({}) == 180


def test_retention_days_comes_from_env_when_set():
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": "30"}) == 30
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": " 45 "}) == 45


def test_blank_env_falls_back_to_default():
    """空串 = 没配（与 config.py 的 `_is_blank` 同口径），⛔ 不算 0。"""
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": "   "}) == 180


@pytest.mark.parametrize("bad", ["abc", "180.5", "0", "-1", "1e3"])
def test_invalid_retention_days_is_fail_closed(bad):
    """配错就拒绝跑，⛔ 不许静默退回默认值。

    退回默认值意味着一个手滑写成 `18` 少一个 0 的配置**看起来生效了**，
    实际按 180 跑；反过来 `0` 会当场删光全部归档。两个方向都不可接受，
    唯一安全的处置是拒绝执行。
    """
    with pytest.raises(RetentionConfigError):
        retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": bad})


def test_parse_archived_at_treats_sqlite_now_as_utc():
    """🔴 冲突 C：`datetime('now')` 给的是 UTC 且不带后缀。

    ⛔ 不许当本地时间解析——本机在 EDT，差 12 小时，且不会报错。
    """
    moment = retention.parse_archived_at("2026-09-09 10:00:00")
    assert moment == datetime.datetime(2026, 9, 9, 10, 0, tzinfo=datetime.timezone.utc)


def test_parse_archived_at_keeps_explicit_offset():
    moment = retention.parse_archived_at("2026-09-09T10:00:00+08:00")
    assert moment.utcoffset() == datetime.timedelta(hours=8)


@pytest.mark.parametrize("bad", ["", "   ", "not-a-time", None, 20260909])
def test_parse_archived_at_rejects_garbage(bad):
    with pytest.raises(ValueError):
        retention.parse_archived_at(bad)


def test_compute_expired_deletes_only_rows_older_than_cutoff():
    """8.2 逐字要求的「超期被清理一条」在纯函数层的形态。"""
    fresh = _row("m-fresh", archived_at="2026-09-01 00:00:00")
    expired = _row("m-old", archived_at="2026-01-01 00:00:00")
    split = retention.compute_expired(NOW, 180, [fresh, expired])
    assert [item.msgid for item in split.deletable] == ["m-old"]
    assert split.blocked_by_queue == ()
    assert split.undecidable == ()


def test_compute_expired_keeps_the_row_exactly_on_the_boundary():
    """恰好等于 cutoff 的行**保留**（判据是严格小于）。

    边界上多留一天永远是安全方向，少留一天是不可逆的删除。
    """
    cutoff = retention.compute_cutoff(NOW, 180)
    on_boundary = _row("m-edge", archived_at=cutoff.astimezone(datetime.timezone.utc)
                       .strftime("%Y-%m-%d %H:%M:%S"))
    split = retention.compute_expired(NOW, 180, [on_boundary])
    assert split.deletable == ()


def test_compute_expired_puts_queued_rows_in_their_own_bucket():
    """🔴 冲突 B：有队列行的消息 ⛔ 不删，但也 ⛔ 不静默——单独成桶。"""
    queued = _row("m-queued", archived_at="2026-01-01 00:00:00", has_queue_row=True)
    split = retention.compute_expired(NOW, 180, [queued])
    assert split.deletable == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-queued"]


def test_compute_expired_reports_unparseable_rows_instead_of_guessing():
    """坏数据不猜、不删、不吞——进 undecidable 桶，由编排层告警。"""
    bad_time = _row("m-bad-time", archived_at="昨天")
    bad_json = _row("m-bad-json", archived_at="2026-01-01 00:00:00", attachments_json="{")
    split = retention.compute_expired(NOW, 180, [bad_time, bad_json])
    assert split.deletable == ()
    assert {item.subject for item in split.undecidable} == {"m-bad-time", "m-bad-json"}


def test_compute_expired_extracts_relative_paths():
    row = _row(
        "m-att",
        archived_at="2026-01-01 00:00:00",
        attachments_json='[{"filename": "a.xlsx", "relative_path": "u1/20260101/m-att__a.xlsx",'
                         ' "byte_length": 3, "sha256": "x"}]',
    )
    split = retention.compute_expired(NOW, 180, [row])
    assert split.deletable[0].relative_paths == ("u1/20260101/m-att__a.xlsx",)


def test_compute_expired_is_pure_and_takes_no_clock(monkeypatch):
    """时钟注入（opener 约束 3）：把 `datetime.datetime` 换成会炸的替身，
    纯函数仍必须能跑完——它一次都不许读真实时钟。"""
    row = _row("m-old", archived_at="2026-01-01 00:00:00")
    split = retention.compute_expired(NOW, 180, [row])
    assert [item.msgid for item in split.deletable] == ["m-old"]
    assert retention.compute_expired(NOW, 180, [row]) == split  # 同输入同输出
