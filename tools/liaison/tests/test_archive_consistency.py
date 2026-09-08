"""spec「台账中存在的消息其材料必然可取回」的可反复执行版本。

这个核对器只回答一个方向的问题：**台账说有的，磁盘上是不是真的有且没变。**
⛔ 反方向（磁盘上有、台账没有）不是不一致——那正是 design D3 允许的中间态。
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from tools.liaison.archive import InboundAttachment, archive_message
from tools.liaison.consistency import (
    LedgerInconsistency,
    verify_ledger_against_archive,
)
from tools.liaison.storage import db as liaison_db

RECEIVED_AT = "2026-09-09T10:30:00+08:00"

MODULE_PATH = pathlib.Path(verify_ledger_against_archive.__globals__["__file__"]).resolve()

#: 模块 docstring 逐字："本模块不删任何东西"——本核对器是第 8 章留存清理的
#: **前置**，不是清理本身。这几个名字出现在 `consistency.py` 里，就说明
#: 有人把删除/改写动作接了进来。
_FORBIDDEN_CALLEES: frozenset[str] = frozenset(
    {"unlink", "rmtree", "remove", "write_bytes", "write_text"}
)


@pytest.fixture
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


def _archive(conn, root, msgid, payload=None, filename="表.xlsx"):
    return archive_message(
        conn,
        thread_id="tanglp",
        msgid=msgid,
        sender_userid="tanglp",
        received_at=RECEIVED_AT,
        msgtype="file" if payload is not None else "text",
        content="",
        attachment=InboundAttachment(filename=filename, payload=payload) if payload is not None else None,
        archive_root=root,
    )


def test_a_healthy_archive_reports_no_inconsistency(conn, root):
    """spec Scenario「台账与材料的一致性可核对」的正路。"""
    for n in range(5):
        _archive(conn, root, f"m{n}", payload=f"payload-{n}".encode())

    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_empty_ledger_is_consistent(conn, root):
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_text_only_messages_are_skipped(conn, root):
    """没有附件的消息不参与核对——`attachments_json` 是 `[]`，没有材料可查。"""
    _archive(conn, root, "m1")
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_a_missing_file_behind_the_ledger_is_reported(conn, root):
    """"台账已记但材料缺失"——这正是 spec 禁止出现的那种状态，核对器必须看得见。"""
    outcome = _archive(conn, root, "m1", payload=b"payload")
    (root / outcome.attachments[0].relative_path).unlink()

    problems = verify_ledger_against_archive(conn, archive_root=root)

    assert len(problems) == 1
    assert isinstance(problems[0], LedgerInconsistency)
    assert problems[0].msgid == "m1"
    assert problems[0].relative_path == outcome.attachments[0].relative_path


def test_a_corrupted_file_is_reported(conn, root):
    """长度相同但内容变了 ⇒ SHA-256 抓得到。"""
    outcome = _archive(conn, root, "m1", payload=b"AAAA")
    (root / outcome.attachments[0].relative_path).write_bytes(b"BBBB")

    problems = verify_ledger_against_archive(conn, archive_root=root)
    assert [p.msgid for p in problems] == ["m1"]


def test_a_truncated_file_is_reported(conn, root):
    outcome = _archive(conn, root, "m1", payload=b"AAAABBBB")
    (root / outcome.attachments[0].relative_path).write_bytes(b"AAAA")

    assert [p.msgid for p in verify_ledger_against_archive(conn, archive_root=root)] == ["m1"]


def test_extra_files_on_disk_are_not_inconsistencies(conn, root):
    """磁盘上有、台账没有 ⇒ design D3 明确允许的中间态，⛔ 不许报成不一致。

    报了会让崩溃恢复期的每一次核对都刷出一堆假警报，真问题就淹了。
    """
    _archive(conn, root, "m1", payload=b"payload")
    stray = root / "tanglp" / "20260909" / "m9__孤儿.bin"
    stray.write_bytes(b"orphan")

    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_malformed_attachments_json_is_reported_not_raised(conn, root):
    """台账里存了坏 JSON ⇒ 报一条不一致，⛔ 不抛——核对器要能跑完整张表。"""
    _archive(conn, root, "m1", payload=b"payload")
    conn.execute("UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?", ("{坏", "m1"))
    conn.commit()

    problems = verify_ledger_against_archive(conn, archive_root=root)
    assert len(problems) == 1
    assert "attachments_json" in problems[0].problem


def test_all_rows_are_visited_even_after_the_first_failure(conn, root):
    """遇到第一条坏的不能停——spec 要求的是"遍历每条记录"。"""
    outcomes = {m: _archive(conn, root, m, payload=m.encode()) for m in ("m1", "m2", "m3")}
    for msgid in ("m1", "m3"):
        (root / outcomes[msgid].attachments[0].relative_path).unlink()

    assert sorted(p.msgid for p in verify_ledger_against_archive(conn, archive_root=root)) == ["m1", "m3"]


def test_checker_reads_bytes_never_text(conn, root):
    """核对走的是 `verify_archived_file`（`rb` + SHA-256），二进制样本必须过。"""
    payload = bytes(range(256)) * 8
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")

    _archive(conn, root, "m1", payload=payload)
    assert verify_ledger_against_archive(conn, archive_root=root) == []


def test_all_rows_are_visited_even_when_a_malformed_row_sorts_between_two_failures(conn, root):
    """`continue`（坏 JSON 行）换成 `break` 后，扫描会在坏行处提前终止——
    `test_all_rows_are_visited_even_after_the_first_failure` 只走 `_verify_entry`
    那条失败路径，从来没经过第 55 行的 `continue`，所以那个 mutation 能在
    11/11 全绿的情况下潜伏。这里把 msgid 排序钉死成 m1 < m2(坏) < m3——
    坏行落在两条"材料缺失"记录中间，`break` 会吞掉 m2 自己的问题，
    还会让排在它后面的 m3 整条从没被摸到。"""
    outcomes = {m: _archive(conn, root, m, payload=m.encode()) for m in ("m1", "m2", "m3")}
    for msgid in ("m1", "m3"):
        (root / outcomes[msgid].attachments[0].relative_path).unlink()
    conn.execute("UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?", ("{坏", "m2"))
    conn.commit()

    problems = verify_ledger_against_archive(conn, archive_root=root)
    assert sorted(p.msgid for p in problems) == ["m1", "m2", "m3"]


def test_ledger_records_enough_to_re_verify_without_the_original(conn, root):
    """台账里存的元数据必须自足：路径 + 字节长度 + SHA-256，三样齐了才核对得了。"""
    _archive(conn, root, "m1", payload=b"payload")
    recorded = json.loads(
        conn.execute("SELECT attachments_json FROM liaison_message WHERE msgid = 'm1'").fetchone()[0]
    )
    assert set(recorded[0]) == {"filename", "relative_path", "byte_length", "sha256"}


# ─────────────────────────────────────────────────────────────────────────
# 结构性断言：本模块 ⛔ 不删任何东西（模块 docstring 逐字）
# ─────────────────────────────────────────────────────────────────────────


def scan_deletion_and_write_violations(source: str, filename: str) -> list[str]:
    """扫一份源码，找出「本模块不删任何东西」的破线：`unlink` / `rmtree` /
    `os.remove` / `write_bytes` / `write_text` 调用。

    做成独立函数是为了能证伪——先喂一份**故意写坏**的源码证明它抓得到，
    再拿它扫真实模块。⛔ 不要把它内联进测试里，那样就没法证伪了。
    """
    violations: list[str] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name in _FORBIDDEN_CALLEES:
            violations.append(f"{filename}:{node.lineno} 出现 {name}(...)——⛔ 本模块不删/不写任何东西")
    return violations


def test_scanner_catches_a_deliberately_broken_consistency_module():
    """证伪：扫描器必须抓到全部五种写法，否则下面对真实源码的断言不成立。"""
    bad = (
        "import os\n"
        "import shutil\n"
        "def wipe(path):\n"
        "    path.unlink()\n"
        "    shutil.rmtree(path)\n"
        "    os.remove(path)\n"
        "    path.write_bytes(b'x')\n"
        "    path.write_text('x')\n"
    )
    violations = scan_deletion_and_write_violations(bad, "bad.py")
    assert len(violations) == 5, violations
    for name in ("unlink", "rmtree", "remove", "write_bytes", "write_text"):
        assert any(name in v for v in violations), violations


def test_scanner_allows_a_read_only_module():
    good = "def check(path):\n    return path.exists()\n"
    assert scan_deletion_and_write_violations(good, "good.py") == []


def test_consistency_module_never_deletes_or_writes():
    """真实源码上的断言（模块 docstring 逐字：⛔ 本模块不删任何东西）。

    ⛔ 变红时不许给违规行加豁免——第 8 章的留存清理是独立模块，
    删除/改写动作不许混进这个只读核对器。
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert scan_deletion_and_write_violations(source, str(MODULE_PATH)) == []
