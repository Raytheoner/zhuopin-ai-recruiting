"""信号文件（`data/liaison/unpack-signal.json`）的去重追加/探测/按检查点清除。

design D12：`{"pending":[{"letter_number","msgid","archived_relpath","at"}]}`；
追加按 `msgid` 去重；文件缺失/损坏时以只含本次一项的新文件替换。
"""

from __future__ import annotations

import json

import pytest

from tools.liaison.unpack.signal import (
    append_signal,
    clear_signal_before,
    find_pending,
    probe_signal,
)


def _item(msgid: str, at: str = "2026-09-10T14:03:00.000000+08:00") -> dict:
    return {
        "letter_number": "人事部#1",
        "msgid": msgid,
        "archived_relpath": f"data/liaison/archive/x/20260910/{msgid}.md",
        "at": at,
    }


def test_append_creates_file_when_missing(tmp_path):
    path = tmp_path / "unpack-signal.json"
    replaced = append_signal(path, _item("m1"))
    assert replaced is False
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m1"]


def test_append_dedupes_by_msgid(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1", at="2026-09-10T14:00:00.000000+08:00"))
    append_signal(path, _item("m1", at="2026-09-10T15:00:00.000000+08:00"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["pending"]) == 1
    assert payload["pending"][0]["at"] == "2026-09-10T15:00:00.000000+08:00"


def test_append_replaces_corrupted_file_and_reports_it(tmp_path):
    path = tmp_path / "unpack-signal.json"
    path.write_text("{not json", encoding="utf-8")
    replaced = append_signal(path, _item("m1"))
    assert replaced is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m1"]


def test_probe_signal_empty_file_is_no_signal(tmp_path):
    path = tmp_path / "unpack-signal.json"
    assert probe_signal(path) is False
    path.write_text(json.dumps({"pending": []}), encoding="utf-8")
    assert probe_signal(path) is False


def test_probe_signal_true_when_pending_nonempty(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1"))
    assert probe_signal(path) is True


def test_clear_signal_before_keeps_items_at_or_after_checkpoint(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1", at="2026-09-10T14:00:00.000000+08:00"))
    append_signal(path, _item("m2", at="2026-09-10T15:00:00.000000+08:00"))
    clear_signal_before(path, "2026-09-10T14:30:00.000000+08:00")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [p["msgid"] for p in payload["pending"]] == ["m2"]


def test_clear_signal_before_on_missing_file_is_noop(tmp_path):
    path = tmp_path / "unpack-signal.json"
    clear_signal_before(path, "2026-09-10T14:30:00.000000+08:00")
    assert not path.exists()


def test_find_pending_returns_item_matching_msgid(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1"))
    append_signal(path, _item("m2"))
    found = find_pending(path, "m2")
    assert found is not None
    assert found["msgid"] == "m2"
    assert found["letter_number"] == "人事部#1"


def test_find_pending_returns_none_when_msgid_absent(tmp_path):
    path = tmp_path / "unpack-signal.json"
    append_signal(path, _item("m1"))
    assert find_pending(path, "not-here") is None


def test_find_pending_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "unpack-signal.json"
    assert find_pending(path, "m1") is None


def test_find_pending_returns_none_when_file_corrupted(tmp_path):
    path = tmp_path / "unpack-signal.json"
    path.write_text("{not json", encoding="utf-8")
    assert find_pending(path, "m1") is None
