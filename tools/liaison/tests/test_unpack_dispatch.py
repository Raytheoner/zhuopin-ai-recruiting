"""`tools/liaison/unpack/dispatch.py` 的并发守卫与起活。

🔴 本文件里**真实 `subprocess.Popen`／真实 `os.kill` 一律不调用**——`popen`/
`is_alive` 全部用 fake 注入。真实 `os.kill(pid, 0)` 只在 `compute_is_alive`
自身的单测里对**本进程自己的 pid**（`os.getpid()`）调一次，用来验证"确实活着"
这一条正向路径，不构造/依赖任何外部进程。
"""

from __future__ import annotations

import os

import pytest

from tools.liaison.unpack.dispatch import compute_is_alive, compute_is_busy


def test_compute_is_alive_true_for_self_pid():
    assert compute_is_alive(os.getpid()) is True


def test_compute_is_alive_false_for_process_lookup_error():
    def _raiser(pid, sig):
        raise ProcessLookupError()

    assert compute_is_alive(-1, _kill=_raiser) is False


def test_compute_is_alive_false_for_permission_error():
    def _raiser(pid, sig):
        raise PermissionError()

    assert compute_is_alive(1, _kill=_raiser) is False


def test_compute_is_alive_false_for_any_other_exception():
    def _raiser(pid, sig):
        raise OSError("boom")

    assert compute_is_alive(1, _kill=_raiser) is False


def test_compute_is_busy_true_when_lock_valid_and_alive():
    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, lambda pid: True) is True


def test_compute_is_busy_false_when_alive_returns_false():
    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, lambda pid: False) is False


def test_compute_is_busy_false_when_lock_missing():
    assert compute_is_busy(None, lambda pid: True) is False


def test_compute_is_busy_false_when_lock_corrupted_json():
    assert compute_is_busy("{not json", lambda pid: True) is False


def test_compute_is_busy_false_when_lock_missing_pid_field():
    assert compute_is_busy('{"started_at": "x"}', lambda pid: True) is False


def test_compute_is_busy_false_when_is_alive_raises():
    def _raiser(pid):
        raise RuntimeError("判活查询本身抛异常")

    lock_text = '{"pid": 123, "started_at": "x", "log": "y"}'
    assert compute_is_busy(lock_text, _raiser) is False
