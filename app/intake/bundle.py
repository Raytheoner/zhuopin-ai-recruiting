"""ZIP 导出包解包（channel-resume-intake U1 tasks 1.1）。

纯函数：只读输入字节、只产出 FileEntry 列表，⛔ 不写任何 storage、不调模型、
不碰数据库。标准库 zipfile；允许一层子目录；白名单 pdf/docx；超限抛
BundleTooLarge；损坏条目标 unreadable；两层以上目录标 too_deep。
"""
from __future__ import annotations

import io
import zipfile
import zlib
from dataclasses import dataclass
from typing import Literal

SUPPORTED_BUNDLE_SUFFIXES = (".pdf", ".docx")


@dataclass(frozen=True)
class BundleLimits:
    max_files: int = 200
    max_total_bytes: int = 200 * 1024 * 1024
    max_ratio: float = 100.0


class BundleTooLarge(ValueError):
    """文件数 / 总大小 / 压缩比任一超限（design D1 上限可配）。"""


@dataclass(frozen=True)
class FileEntry:
    name: str  # 包内路径，如 "子目录/张三.pdf"
    filename: str  # 包内文件名（展示用）
    kind: Literal["ok", "unsupported", "unreadable", "too_deep"]
    data: bytes = b""

    @property
    def reason(self) -> str:
        if self.kind == "unsupported":
            return "不支持的类型"
        if self.kind == "unreadable":
            return "无法读取"
        if self.kind == "too_deep":
            return "目录层级过深"
        return ""


def _dir_depth(name: str) -> int:
    # 文件名本身不算目录：dir/file.pdf → 1，a/b/file.pdf → 2。
    return len(name.split("/")) - 1


def unpack_bundle(data: bytes, limits: BundleLimits | None = None) -> list[FileEntry]:
    limits = limits or BundleLimits()
    if not data:
        raise BundleTooLarge("空压缩包")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleTooLarge(f"压缩包损坏: {exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > limits.max_files:
            raise BundleTooLarge(f"文件数超限：{len(infos)} > {limits.max_files}")

        total_uncompressed = sum(i.file_size for i in infos)
        if total_uncompressed > limits.max_total_bytes:
            raise BundleTooLarge("总大小超限")

        compressed_size = len(data)
        if compressed_size > 0:
            ratio = total_uncompressed / compressed_size
            if ratio > limits.max_ratio:
                raise BundleTooLarge("压缩比超限")

        entries: list[FileEntry] = []
        for info in infos:
            filename = info.filename.rsplit("/", 1)[-1]
            depth = _dir_depth(info.filename)
            if depth >= 2:
                entries.append(FileEntry(name=info.filename, filename=filename, kind="too_deep"))
                continue

            suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if suffix not in SUPPORTED_BUNDLE_SUFFIXES:
                entries.append(FileEntry(name=info.filename, filename=filename, kind="unsupported"))
                continue

            try:
                content = zf.read(info)
            except (zipfile.BadZipFile, RuntimeError, zlib.error, EOFError):
                entries.append(FileEntry(name=info.filename, filename=filename, kind="unreadable"))
                continue

            entries.append(FileEntry(name=info.filename, filename=filename, kind="ok", data=content))
        return entries
