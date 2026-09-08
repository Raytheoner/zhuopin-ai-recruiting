"""归档路径的计算与消息归档的编排。

**本模块分两层，⛔ 不许混在一起：**

- `compute_safe_filename` / `compute_archive_path` 是 `compute_*` 纯函数
  （工程铁律 2 的形状）：不读文件、不读时钟、不读环境变量、不记日志。
  测试用 AST 把这条钉成断言。
- `archive_message` 是编排点：它按 design D3 钉死的顺序，**先**调
  `attachments.store_attachment` 落材料，**后**调第 2 章的
  `effect_archive_message` 写台账。⛔ 顺序不许反。

⛔ **本模块里的 `decode` 只作用于文件名，绝不作用于附件内容。**
4.4 禁止的是"对附件内容做文本解码后据此判定损坏"（生产 bug「二进制判误」
的成因）；文件名本来就是 `str`，而按**字节**截断（文件系统的 255 是字节不是
字符）必须切完再拼回 `str`，这一步绕不开 decode。碰附件字节的模块是
`attachments.py`，那里 ⛔ 一个 `decode` 都不许出现，由扫描器守着。
"""

from __future__ import annotations

import datetime
import pathlib
import unicodedata
from typing import Any, Final

#: 文件名的默认字节预算。留出余量给 `<msgid>__` 前缀——最终路径组件的上限
#: 是 255 字节（APFS/ext4 的单个 name component 限制），200 给文件名、
#: 剩下的给前缀，`compute_archive_path` 会按实际 msgid 长度再算一次精确预算。
DEFAULT_MAX_FILENAME_BYTES: Final[int] = 200

#: 名字被清空后的占位名。⛔ 不许返回 ""/"."/".."——它们拼进路径会指向目录本身，
#: 最终 `os.replace` 会去覆盖一个目录，报出与根因无关的 IsADirectoryError。
FALLBACK_FILENAME: Final[str] = "unnamed"

#: 路径分隔符。只收 `/` 与 `\`。
#: ⛔ 刻意**不**把 `:` 算进来：POSIX 上它不是分隔符，删掉它是对文件名的
#: 改写而不是路径安全，违反 4.2「⛔ 不因……改写」。
_PATH_SEPARATORS: Final[frozenset[str]] = frozenset({"/", "\\"})


class ArchivePathError(ValueError):
    """归档路径无法安全地算出来。

    只在 `thread_id`／`msgid` 这类**键**不可用时抛（见 `compute_archive_path`）。
    文件名不抛——它走归一化，任何输入都能折成一个安全的名字。
    """


def compute_safe_filename(
    raw_name: Any, *, max_bytes: int = DEFAULT_MAX_FILENAME_BYTES
) -> str:
    """把原始文件名压成一个安全的**单个**路径组件。

    只做四件事（4.2 逐字）：移除路径分隔符与控制字符、去除首尾空白、
    超长时按字节截断且保留扩展名。

    ⛔ **不因为非 ASCII 或"看着像乱码"改写或拒收。** 中文名原样保留。
    ⛔ 不做大小写归一、不做拼音转写、不按内容猜扩展名。
    """
    if not isinstance(raw_name, str):
        # 协议字段可能是 None 或数字。判定路径不抛异常给调用方——
        # 一个坏文件名不该让整条消息归档不了。
        return FALLBACK_FILENAME

    cleaned = "".join(
        char
        for char in raw_name
        if char not in _PATH_SEPARATORS and unicodedata.category(char) != "Cc"
    ).strip()

    # 只剩点号（"" / "." / ".." / "..."）的名字指向目录本身，折成占位名。
    if not cleaned.strip("."):
        return FALLBACK_FILENAME

    return _truncate_preserving_extension(cleaned, max_bytes)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """按字节截断，丢掉被切碎的那个字符。

    `errors="ignore"` 在这里是**正确**的用法而不是掩盖问题：被丢掉的只可能是
    我们自己刚切碎的那一个多字节字符的残段。⛔ 不要把这个模式搬去处理附件字节。
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _truncate_whole_name(name: str, max_bytes: int) -> str:
    """扩展名保不住时的整串截断兜底。

    ⛔ 结果绝不能是 ``""``/``"."``/``".."``——它们拼进路径会指向目录本身，
    最终 `os.replace` 会去覆盖一个目录，报出与根因无关的 `IsADirectoryError`。
    `_truncate_utf8` 编码后为空串时是假值，但原名以多个 `.` 开头时，截断可能
    恰好只剩下纯点号前缀（非空、真值）——`or FALLBACK_FILENAME` 挡不住这种，
    必须显式再判一次。
    """
    truncated = _truncate_utf8(name, max_bytes)
    if truncated in ("", ".", ".."):
        return FALLBACK_FILENAME
    return truncated


def _truncate_preserving_extension(name: str, max_bytes: int) -> str:
    if len(name.encode("utf-8")) <= max_bytes:
        return name

    stem, dot, extension = name.rpartition(".")
    # `stem` 为空 ⇒ 形如 ".gitignore"，整个名字就是名字，没有扩展名可保。
    # `extension` 为空 ⇒ 名字以字面 "." 收尾（如 "a."）——rpartition 会切出
    # 一个空扩展名，`suffix` 会退化成裸 "."，没有值得保留的后缀；不挡在这里
    # 会让下面的 `truncated_stem + suffix` 在 stem 预算精确为 0 时退化成单独
    # 的 "."，违反上面的硬不变式（review round 2 finding）。
    if not dot or not stem or not extension:
        return _truncate_whole_name(name, max_bytes)

    suffix = "." + extension
    suffix_bytes = len(suffix.encode("utf-8"))
    if suffix_bytes > max_bytes:
        # 病态输入：扩展名本身就吃掉了全部预算。保不住扩展名，
        # 但**绝不能溢出**——溢出会在 open() 时报 ENAMETOOLONG，
        # 而那时候材料已经收到了却落不了盘。
        return _truncate_whole_name(name, max_bytes)

    # `suffix_bytes <= max_bytes` 已在上面确认，所以 `truncated_stem`（预算
    # 为 `max_bytes - suffix_bytes >= 0`）加上 `suffix` 必然不超预算——
    # 即便 `truncated_stem` 恰好截成空串（含 4.9 边界：suffix 精确吃满预算，
    # stem 预算为 0），单独返回 `suffix` 仍在预算内且比丢弃扩展名更贴合
    # 「保留扩展名」的意图，⛔ 不要在这里回退到对整串做无差别截断。
    # 此时 `extension` 已确认非空，`suffix` 至少 2 字节（"." + 非空扩展名），
    # 因此 `truncated_stem + suffix` 不可能退化成 "" / "." / ".."。
    truncated_stem = _truncate_utf8(stem, max_bytes - suffix_bytes)
    return truncated_stem + suffix


#: 单个路径组件的字节上限（APFS / ext4 的 name component 限制）。
#: `<msgid>__<文件名>` 合起来受这一条约束，⛔ 不是只约束文件名。
MAX_PATH_COMPONENT_BYTES: Final[int] = 255

#: `<msgid>` 与文件名之间的分隔符（design D4 逐字）。
_MSGID_SEPARATOR: Final[str] = "__"

# tools/liaison/archive.py → parents[0]=liaison, [1]=tools, [2]=仓库根
_REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]

#: 归档根目录（design D4）。`data/` 已被 .gitignore:11 覆盖，归档不会误入版本管理。
DEFAULT_ARCHIVE_ROOT: Final[pathlib.Path] = _REPO_ROOT / "data" / "liaison" / "archive"


def _validated_key(value: Any, field_name: str) -> str:
    """校验一个要进路径的**键**（`thread_id` / `msgid`）。

    ⛔ **只校验、不改写。** 归一化会把两个不同的键磨成同一个字符串，
    两条消息就落到同一个路径上——「归档覆盖」那个生产 bug 换了个成因又回来了。
    键来自企微协议、本该是安全的标识符；不安全就说明上游给的东西有问题，
    这时候正确的方向是**响亮地失败**，不是安静地清洗。
    """
    if not isinstance(value, str):
        raise ArchivePathError(f"{field_name} 必须是字符串，实际是 {type(value).__name__}")
    if value != value.strip() or not value:
        raise ArchivePathError(f"{field_name} 为空或含首尾空白，⛔ 不接受也不清洗")
    if value in (".", ".."):
        raise ArchivePathError(f"{field_name} 是 '.' 或 '..'，会指向目录本身")
    for char in value:
        if char in _PATH_SEPARATORS or unicodedata.category(char) == "Cc":
            raise ArchivePathError(f"{field_name} 含路径分隔符或控制字符，⛔ 不清洗，直接拒绝")
    return value


def _yyyymmdd(received_at: Any) -> str:
    """从 `received_at` 取出 `yyyymmdd`，**只用于分目录**。

    ⛔ 不做时区换算——协议给的偏移就是发送人看到的那个时刻。
    ⛔ 不兜底成"今天"——那会把一个坏时间戳变成一条落错目录的记录，
    而且不报错、事后无从发现。
    """
    if not isinstance(received_at, str):
        raise ArchivePathError(
            f"received_at 必须是 ISO-8601 字符串，实际是 {type(received_at).__name__}"
        )
    try:
        moment = datetime.datetime.fromisoformat(received_at)
    except ValueError as exc:
        raise ArchivePathError(f"received_at 不是可解析的 ISO-8601 时间戳：{received_at!r}") from exc
    return f"{moment.year:04d}{moment.month:02d}{moment.day:02d}"


def compute_archive_path(
    *,
    thread_id: Any,
    msgid: Any,
    received_at: Any,
    filename: Any,
    archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT,
) -> pathlib.Path:
    """算出一份附件的归档落点（design D4 逐字）：

        <archive_root>/<thread_id>/<yyyymmdd>/<msgid>__<归一化文件名>

    **键的最细一级是 `msgid`。** ⛔ 禁止任何"按天一个文件"或"按人一个文件"
    的粗粒度落点——那正是参考服务生产 bug「归档覆盖」的成因。日期在这条路径里
    只是分目录。

    纯函数：不读文件、不读时钟、不读环境变量。同一条消息在任何时刻算出的路径
    必须逐字相同——否则重投时会落出第二份，而幂等装饰器仍认为只处理了一次。
    """
    safe_thread_id = _validated_key(thread_id, "thread_id")
    safe_msgid = _validated_key(msgid, "msgid")
    day = _yyyymmdd(received_at)

    prefix = safe_msgid + _MSGID_SEPARATOR
    budget = MAX_PATH_COMPONENT_BYTES - len(prefix.encode("utf-8"))
    if budget < len(FALLBACK_FILENAME.encode("utf-8")):
        # msgid 长到连占位名都放不下。⛔ 不许截断 msgid——那是改写键。
        raise ArchivePathError(
            f"msgid 过长（{len(prefix.encode('utf-8'))} 字节），"
            f"路径组件放不下文件名；⛔ 不截断 msgid"
        )

    safe_name = compute_safe_filename(filename, max_bytes=budget)
    return archive_root / safe_thread_id / day / (prefix + safe_name)
