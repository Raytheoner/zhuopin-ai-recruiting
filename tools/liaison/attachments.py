"""附件的字节流落盘与完整性校验（design D4、tasks 4.3/4.4）。

🔴 **本模块是本服务里唯一碰附件字节的地方，它有两条 ⛔ 由扫描器守着的禁令：**

1. ⛔ **一个 `.decode(` 都不许出现。** 完整性校验只用字节长度 + SHA-256。
   参考服务的生产 bug「二进制判误」正是把合法的 xlsx/pdf 拿去做 UTF-8 解码后
   误判为损坏——那不是一次手滑，是"文件内容可以当文本看"这个假设的必然结果。
2. ⛔ **不许有非二进制模式的 `open()`。** 文本模式会做换行转换，
   在 Windows 上把 `\\n` 写成 `\\r\\n`，附件当场变成另一份文件。

守卫在 tests/test_attachments.py::test_attachments_module_never_decodes_and_never_opens_in_text_mode，
带证伪用例。⛔ 变红时的正确修法是删掉违规代码，不是给它加豁免。

**本模块不碰数据库。** 它是 `effect_archive_message` 的**前置**而不是它的一部分
——文件系统不参与 SQL 事务，硬凑只会让一次 I/O 失败把幂等装饰器的事务处理
路径也拖进来，而 `rollback()` 回滚不了已经落盘的文件（design D3）。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile
from dataclasses import dataclass

#: 临时文件的前缀。以 `.` 开头 ⇒ 不会被普通的目录浏览看到；
#: 第 8 章的留存清理按这个前缀顺手扫掉崩溃残留的孤儿临时文件。
_TEMP_PREFIX = ".tmp-"
_TEMP_SUFFIX = ".part"


class AttachmentIntegrityError(RuntimeError):
    """同一个归档路径上已经有一份**不同**的材料。

    路径里含 `msgid`，所以这意味着同一条协议消息带来了两份不同的字节——
    协议层出了怪事。⛔ 不静默覆盖（那是「归档覆盖」），⛔ 也不静默跳过
    （那会让台账指向一份不是它描述的材料）。
    """


@dataclass(frozen=True)
class StoredAttachment:
    """一份已落盘附件的可核对元数据。会被序列化进 `liaison_message.attachments_json`。

    `relative_path` 存的是**相对归档根**的路径：归档根将来可能整体搬家
    （换盘、换机器），存绝对路径会让台账里所有的行一起失效。
    """

    filename: str
    relative_path: str
    byte_length: int
    sha256: str

    def as_dict(self) -> dict:
        return {
            "filename": self.filename,
            "relative_path": self.relative_path,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
        }


def compute_digest(payload: bytes) -> tuple[int, str]:
    """完整性凭据 = （字节长度, SHA-256 十六进制摘要）。

    ⛔ 只有这两样。任何"顺便看看是不是文本""按内容猜类型"的分支都不许加。
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        # ⛔ 不在这里 `payload.encode()` 兜底：那是"把二进制当文本"的入口。
        # 传进来一个 str 说明上游某处已经解码过了，那才是要修的地方。
        raise TypeError(f"附件必须是字节，实际是 {type(payload).__name__}；⛔ 本函数不做隐式编码")
    data = bytes(payload)
    return len(data), hashlib.sha256(data).hexdigest()


def verify_archived_file(path: pathlib.Path, *, byte_length: int, sha256: str) -> bool:
    """核对一份已归档材料。文件不在、长度不符、摘要不符 ⇒ False。

    ⛔ 全程 `rb`，⛔ 不解码。文件不存在返回 False 而不是抛——调用方
    （Task 5 的核对器）要把"哪些不一致"收集成一张清单，不是遇到第一条就停。
    """
    try:
        with open(path, "rb") as handle:
            actual = handle.read()
    except OSError:
        return False
    return compute_digest(actual) == (byte_length, sha256)


def store_attachment(
    payload: bytes,
    destination: pathlib.Path,
    *,
    archive_root: pathlib.Path,
) -> StoredAttachment:
    """把一份附件原样落到 `destination`，返回可核对的元数据。

    顺序（design D3 逐字）：**临时文件 → `fsync` → 原子 `rename`**。
    ⛔ 不许直接写目的地——崩溃会留下一个半截文件，而半截文件在路径上"存在"，
    下次重跑就被下面的幂等短路当成"已完成"，材料从此永久残缺。

    幂等：目标路径含 `msgid`，已存在**且字节一致**即视为已完成，直接返回，
    ⛔ 不重写（重写会把一个正被读取的文件在中途换掉）。已存在但字节不一致
    ⇒ `AttachmentIntegrityError`。
    """
    # 先转一次 bytes，把它同时喂给 compute_digest 和后面的写盘——
    # 避免 bytearray/memoryview 输入被隐式拷贝两遍。
    data = bytes(payload)
    byte_length, digest = compute_digest(data)

    if destination.exists():
        if verify_archived_file(destination, byte_length=byte_length, sha256=digest):
            return _stored(destination, archive_root, byte_length, digest)
        raise AttachmentIntegrityError(
            f"归档路径 {destination} 已存在一份不同的材料（同一 msgid 带来了两份不同字节）；"
            f"⛔ 不覆盖、⛔ 不跳过"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        dir=destination.parent, prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX
    )
    temp_path = pathlib.Path(temp_name)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    except BaseException:
        # 清临时文件是尽力而为：清不掉也不能盖住原始异常。留下的孤儿临时文件
        # 以 `.tmp-` 开头，第 8 章的留存清理会扫掉。
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # 目录也要 fsync：只 fsync 文件的话，`rename` 这个目录项的变更本身
    # 可能还没落盘——断电后文件内容在、名字不在，等于材料没落。
    dir_fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    return _stored(destination, archive_root, byte_length, digest)


def _stored(
    destination: pathlib.Path, archive_root: pathlib.Path, byte_length: int, digest: str
) -> StoredAttachment:
    return StoredAttachment(
        filename=destination.name,
        relative_path=destination.relative_to(archive_root).as_posix(),
        byte_length=byte_length,
        sha256=digest,
    )
