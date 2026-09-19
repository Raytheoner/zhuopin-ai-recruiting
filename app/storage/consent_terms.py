"""同意条款文件的加载与版本号解析（voice-structured-interview U3 tasks 4.5，
design D5）。条款文本本身不进代码仓库的"正式内容"——`config/consent/*.md`
是运营可编辑的配置文件，本模块只负责发现版本号与读取文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CONSENT_DIR = Path("config/consent")

KNOWN_CONSENT_KINDS: tuple[str, ...] = ("ai_interview", "identity_check")

_FILENAME_PATTERN = re.compile(r"^(?P<kind>[a-z_]+)-v(?P<version>\d+)\.md$")


class ConsentTermNotFoundError(Exception):
    """请求的 kind/version 组合没有对应的条款文件。"""


@dataclass(frozen=True)
class ConsentTerm:
    kind: str
    version: str
    text: str


def _available_versions(kind: str) -> list[int]:
    if kind not in KNOWN_CONSENT_KINDS:
        return []
    versions: list[int] = []
    for path in CONSENT_DIR.glob(f"{kind}-v*.md"):
        match = _FILENAME_PATTERN.match(path.name)
        if match and match.group("kind") == kind:
            versions.append(int(match.group("version")))
    return versions


def latest_consent_version(kind: str) -> str:
    """该 kind 目前文件名里版本号数值最大的那个，格式化回 'v<N>'。
    ⛔ 不按字符串排序——'v10' 字符串序小于 'v2'。"""
    versions = _available_versions(kind)
    if not versions:
        raise ConsentTermNotFoundError(f"未找到 kind={kind!r} 的任何条款文件")
    return f"v{max(versions)}"


def load_consent_term(kind: str, version: str) -> ConsentTerm:
    if kind not in KNOWN_CONSENT_KINDS:
        raise ConsentTermNotFoundError(f"未知的条款类型: {kind!r}")
    path = CONSENT_DIR / f"{kind}-{version}.md"
    if not path.is_file():
        raise ConsentTermNotFoundError(f"未找到条款文件: {path}")
    return ConsentTerm(kind=kind, version=version, text=path.read_text(encoding="utf-8"))
