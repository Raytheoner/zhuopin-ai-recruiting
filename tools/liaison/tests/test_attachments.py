"""附件落盘（4.3）与完整性校验（4.4/4.7）。

本文件最重要的不是"能存能取"，是两条结构性断言：
- `attachments.py` 里 ⛔ 一个 `.decode(` 都不许有（生产 bug「二进制判误」）
- `attachments.py` 里 ⛔ 不许有非二进制模式的 `open()`
两条都带证伪用例——先证明扫描器真的抓得到，再拿它扫真实源码。
"""

from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import zlib

import pytest

from tools.liaison.attachments import (
    AttachmentIntegrityError,
    StoredAttachment,
    compute_digest,
    store_attachment,
    verify_archived_file,
)

MODULE_PATH = pathlib.Path(store_attachment.__globals__["__file__"]).resolve()


# ─────────────────────────────────────────────────────────────────────────
# 4.4 完整性校验只用字节长度 + SHA-256
# ─────────────────────────────────────────────────────────────────────────


def test_compute_digest_returns_byte_length_and_sha256():
    payload = b"\x89PNG\r\n\x1a\n\x00\x01\x02"
    assert compute_digest(payload) == (len(payload), hashlib.sha256(payload).hexdigest())


def test_compute_digest_rejects_str_instead_of_silently_encoding_it():
    """传 `str` 进来 ⇒ 上游某处已经把字节解码过了 ⇒ 必须响亮地失败。

    ⛔ 不许在这里 `payload.encode()` 兜底：那正是"把二进制当文本"的入口，
    生产 bug「二进制判误」就是从这类善意兜底开始的。
    """
    with pytest.raises(TypeError):
        compute_digest("这不是字节")


def test_digest_is_stable_across_calls():
    payload = os.urandom(4096)
    assert compute_digest(payload) == compute_digest(payload)


# ─────────────────────────────────────────────────────────────────────────
# 4.3 落盘：字节流 + 临时文件 + fsync + 原子 rename
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive"


def test_store_writes_bytes_verbatim(root):
    payload = os.urandom(8192)
    destination = root / "u1" / "20260909" / "m1__样本.bin"

    stored = store_attachment(payload, destination, archive_root=root)

    assert destination.read_bytes() == payload
    assert isinstance(stored, StoredAttachment)
    assert stored.byte_length == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.filename == "m1__样本.bin"
    assert stored.relative_path == "u1/20260909/m1__样本.bin"


def test_store_creates_missing_parent_directories(root):
    destination = root / "深" / "20260909" / "m1__a.txt"
    store_attachment(b"x", destination, archive_root=root)
    assert destination.is_file()


def test_store_leaves_no_temporary_files_behind(root):
    destination = root / "u1" / "20260909" / "m1__a.bin"
    store_attachment(os.urandom(1024), destination, archive_root=root)
    leftovers = [p.name for p in destination.parent.iterdir() if p.name != destination.name]
    assert leftovers == [], f"临时文件没清干净：{leftovers}"


def test_store_is_idempotent_when_destination_already_holds_the_same_bytes(root):
    """D3 的幂等策略：目标路径含 msgid，已存在即视为已完成。

    判据是 **inode 不变**——重跑必须**不重写**，而不是"重写出一样的内容"。
    重写会让一个正在被读取的文件在中途被替换掉。
    """
    payload = os.urandom(2048)
    destination = root / "u1" / "20260909" / "m1__a.bin"

    first = store_attachment(payload, destination, archive_root=root)
    inode_before = destination.stat().st_ino
    second = store_attachment(payload, destination, archive_root=root)

    assert first == second
    assert destination.stat().st_ino == inode_before


def test_store_refuses_to_overwrite_a_different_payload_at_the_same_path(root):
    """同一个 msgid + 同一个文件名却是不同的字节 ⇒ 协议层出了怪事。

    ⛔ 绝不静默覆盖（那是「归档覆盖」）、⛔ 也不静默跳过（那会让台账指向
    一份不是它描述的材料）。抛出来，由调用方记 ERROR 并**不写台账行**——
    留在"材料在、台账没有"这个可收敛、可见的中间态。
    """
    destination = root / "u1" / "20260909" / "m1__a.bin"
    store_attachment(b"first", destination, archive_root=root)

    with pytest.raises(AttachmentIntegrityError):
        store_attachment(b"second", destination, archive_root=root)

    assert destination.read_bytes() == b"first", "冲突时既有材料不许被动过"


def test_store_rejects_str_payload(root):
    with pytest.raises(TypeError):
        store_attachment("文本", root / "u1" / "20260909" / "m1__a.txt", archive_root=root)


def test_store_calls_fsync_on_the_file_and_the_directory(root, monkeypatch):
    """D3 要求 fsync。没有它，"材料已落"这句话在断电后不成立。

    目录也要 fsync——只 fsync 文件的话，rename 本身可能还没落盘。
    """
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])

    store_attachment(b"payload", root / "u1" / "20260909" / "m1__a.bin", archive_root=root)

    assert len(synced) >= 2, f"fsync 只被调了 {len(synced)} 次，文件与目录都要 fsync"


def test_store_uses_atomic_rename_not_a_direct_write(monkeypatch, root):
    """判据：目的地路径**从不**被直接 open 写入，只被 os.replace 落位。

    直接写目的地会让崩溃留下一个半截文件——而半截文件在路径上"存在"，
    下次重跑就被幂等短路当成"已完成"，材料从此永久残缺。
    """
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(
        os, "replace", lambda src, dst: (replaced.append((str(src), str(dst))), real_replace(src, dst))[1]
    )
    destination = root / "u1" / "20260909" / "m1__a.bin"

    store_attachment(b"payload", destination, archive_root=root)

    assert [dst for _, dst in replaced] == [str(destination)]


def test_partial_write_failure_leaves_no_file_at_the_destination(root, monkeypatch):
    """写到一半炸了 ⇒ 目的地必须仍然不存在（原子性的可观测形式）。"""
    destination = root / "u1" / "20260909" / "m1__a.bin"

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        store_attachment(b"payload", destination, archive_root=root)

    assert not destination.exists()


# ─────────────────────────────────────────────────────────────────────────
# 4.7 二进制附件不被误判损坏（生产 bug「二进制判误」）
# ─────────────────────────────────────────────────────────────────────────


def _minimal_xlsx_bytes() -> bytes:
    """一份真实的 zip 容器（xlsx 就是 zip）。⛔ 不用 `b"fake xlsx"` 冒充——
    那种"样本"恰好是 UTF-8 可解码的，会让"二进制判误"这条测试假绿。
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    return buffer.getvalue()


def _minimal_pdf_bytes() -> bytes:
    """最小 PDF：头是 ASCII，但流内容是压缩后的字节，UTF-8 解不开。

    ⛔ 这里刻意**不用** `b"..." % len(body)` 拼接——PDF 的文件头就是 `%PDF`，
    而 `%P` 不是合法的字节格式化占位符，`%` 运算符会当场 `ValueError`
    （计划编写期的提取验证实测踩到）。用拼接，不用格式化。
    """
    body = zlib.compress(os.urandom(512))
    header = b"%PDF-1.7\n1 0 obj\n<< /Length " + str(len(body)).encode("ascii") + b" >>\nstream\n"
    return header + body + b"\nendstream\nendobj\n%%EOF"


@pytest.mark.parametrize(
    "sample,name",
    [(_minimal_xlsx_bytes(), "反馈表.xlsx"), (_minimal_pdf_bytes(), "说明.pdf")],
)
def test_real_binary_samples_round_trip_byte_for_byte(root, sample, name):
    """spec Scenario「二进制附件不被误判损坏」：取回的字节与原文件逐字节相同。"""
    with pytest.raises(UnicodeDecodeError):
        sample.decode("utf-8")  # 前提：这份样本确实不是 UTF-8 可解码的

    destination = root / "u1" / "20260909" / f"m1__{name}"
    stored = store_attachment(sample, destination, archive_root=root)

    assert destination.read_bytes() == sample
    assert verify_archived_file(destination, byte_length=stored.byte_length, sha256=stored.sha256)


def test_verify_detects_truncation_and_corruption(root):
    destination = root / "u1" / "20260909" / "m1__a.bin"
    payload = os.urandom(4096)
    stored = store_attachment(payload, destination, archive_root=root)

    destination.write_bytes(payload[:-1])  # 截断
    assert not verify_archived_file(destination, byte_length=stored.byte_length, sha256=stored.sha256)

    flipped = bytearray(payload)
    flipped[0] ^= 0xFF
    destination.write_bytes(bytes(flipped))  # 等长但内容变了
    assert not verify_archived_file(destination, byte_length=stored.byte_length, sha256=stored.sha256)


def test_verify_returns_false_for_a_missing_file(root):
    assert not verify_archived_file(root / "nope.bin", byte_length=0, sha256="0" * 64)


# ─────────────────────────────────────────────────────────────────────────
# 结构性断言：碰附件字节的模块里 ⛔ 不许有解码，也不许有文本模式的 open
# ─────────────────────────────────────────────────────────────────────────


def scan_text_handling_violations(source: str, filename: str) -> list[str]:
    """扫一份源码，找出两类违规：`.decode(` 调用、非二进制模式的 `open()`。

    做成独立函数是为了能证伪——先喂一份**故意写坏**的源码证明它抓得到，
    再拿它扫真实模块。⛔ 不要把它内联进测试里，那样就没法证伪了。
    """
    violations: list[str] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "decode":
            violations.append(f"{filename}:{node.lineno} 出现 .decode()——⛔ 附件字节不许解码")
        if isinstance(func, ast.Name) and func.id == "open":
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if not isinstance(mode, str) or "b" not in mode:
                violations.append(f"{filename}:{node.lineno} open() 不是二进制模式（mode={mode!r}）")
    return violations


def test_scanner_catches_a_deliberately_broken_module():
    """证伪：扫描器必须抓到这两种写法，否则下面那条对真实源码的断言不成立。"""
    bad = (
        "def load(path):\n"
        "    with open(path) as handle:\n"
        "        return handle.read().decode('utf-8')\n"
    )
    violations = scan_text_handling_violations(bad, "bad.py")
    assert len(violations) == 2, violations
    assert any("decode" in v for v in violations)
    assert any("二进制模式" in v for v in violations)


def test_scanner_allows_binary_mode_open():
    good = "def load(path):\n    with open(path, 'rb') as handle:\n        return handle.read()\n"
    assert scan_text_handling_violations(good, "good.py") == []


def test_attachments_module_never_decodes_and_never_opens_in_text_mode():
    """真实源码上的断言（4.4 逐字：⛔ 任何 UTF-8/文本解码）。

    ⛔ 变红时不许给违规行加豁免——生产 bug「二进制判误」的修法是把解码删掉，
    不是把检查删掉。文件名的字节截断需要 decode，那段代码在 `archive.py`，
    ⛔ 不许搬进本模块。
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert scan_text_handling_violations(source, str(MODULE_PATH)) == []
