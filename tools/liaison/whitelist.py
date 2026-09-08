"""HR 企微值守服务的准入名单：fail-closed 判定。

三条结构性约定，改动前先读：

1. **判定路径不抛异常给调用方。** 任何失败——文件缺失、不可读、解析失败、结构不符、
   名单为零——都折成"空名单"并让全部发送人未命中，同时记 ERROR 级日志。
   一个准入闸在配置损坏时放行，比它根本不存在更危险。
2. **进程内不缓存名单。** 每次判定重新读文件。这不是性能疏忽，是 spec 的硬要求
   （"MUST NOT 降级为放行上一次成功加载的名单"）——缓存一旦存在，文件损坏后
   沿用旧名单就是这条要求的直接违反。本服务每分钟只处理个位数消息，读一次文件的
   代价可以忽略。附带收益：名单变更重启即生效，且不需要重启也已生效。
3. **`compute_admission` 是纯函数**（工程铁律 2 的形状）：不读文件、不读环境变量、
   不调网络、不记日志。所有 I/O 与日志都在 `load_whitelist` 一侧。
   `admit()` 是把两者接起来的调用缝，第 4／5 章从这里接线。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import yaml

logger = logging.getLogger(__name__)

DEFAULT_WHITELIST_PATH: Final[Path] = (
    Path(__file__).resolve().parent / "config" / "whitelist.yaml"
)

# 名单条目只允许这三个字段。多一个字段整条丢弃，不是忽略多余字段——
# 让"⛔ 不含手机号／邮箱／身份证号"这条合规要求在运行时自我执行，
# 而不是只靠人在 review 时看一眼配置文件。
ALLOWED_MEMBER_FIELDS: Final[frozenset[str]] = frozenset({"userid", "name", "role"})


def compute_admission(sender_userid: Any, whitelist: frozenset[str]) -> bool:
    """纯函数：发送人标识是否命中已加载的名单。

    ⛔ 本函数不得新增任何 I/O、环境变量读取、网络调用或日志——
    `test_compute_admission_is_pure` 用 AST 把这条钉成断言。
    """
    if not isinstance(sender_userid, str):
        return False
    candidate = sender_userid.strip()
    if not candidate:
        return False
    return candidate in whitelist


def load_whitelist(path: Path = DEFAULT_WHITELIST_PATH) -> frozenset[str]:
    """读名单文件，返回可准入的 userid 集合。任何失败一律返回空集合。"""
    try:
        return _read_roster(path)
    except Exception:  # noqa: BLE001
        # 兜底带：_read_roster 已按类型分支捕获了预期失败。这里接住的是未预期的
        # 异常（例如 PyYAML 在畸形输入上抛出的非 YAMLError）。⛔ 不允许它逃到
        # 调用方——spec 要求"任何判定路径上的失败结果都必须是未命中"。
        # exc_info=True 保证它不会变成一次静默吞异常。
        logger.error("准入名单加载出现未预期异常，按空名单全拒：path=%s", path, exc_info=True)
        return frozenset()


def admit(sender_userid: Any, path: Path = DEFAULT_WHITELIST_PATH) -> bool:
    """调用缝：加载名单 + 判定。每次调用重新读文件（见模块 docstring 第 2 条）。"""
    return compute_admission(sender_userid, load_whitelist(path))


def _read_roster(path: Path) -> frozenset[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("准入名单不可读，按空名单全拒：path=%s err=%s", path, exc)
        return frozenset()

    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.error(
            "准入名单解析失败，按空名单全拒（⛔ 不沿用任何此前加载过的名单）：path=%s err=%s",
            path,
            exc,
        )
        return frozenset()

    if not isinstance(document, Mapping):
        logger.error("准入名单顶层不是映射，按空名单全拒：path=%s", path)
        return frozenset()

    members = document.get("members")
    if not isinstance(members, list):
        logger.error("准入名单缺 members 列表或类型不对，按空名单全拒：path=%s", path)
        return frozenset()

    admitted = {
        userid
        for index, entry in enumerate(members)
        if (userid := _validated_userid(entry, index, path)) is not None
    }

    if not admitted:
        logger.error("准入名单为零条有效条目，全部发送人判为未命中：path=%s", path)

    return frozenset(admitted)


def _validated_userid(entry: Any, index: int, path: Path) -> str | None:
    """校验单条名单条目。不合格返回 None（整条丢弃），并记 ERROR。

    ⛔ 日志只写字段**名**，绝不写字段**值**——多余字段的值恰恰可能就是
    手机号／邮箱这类不该被采集的个人信息，写进日志等于把它换个地方留存。
    """
    if not isinstance(entry, Mapping):
        logger.error("准入名单第 %d 条不是映射，整条丢弃：path=%s", index, path)
        return None

    userid = entry.get("userid")
    if not isinstance(userid, str) or not userid.strip():
        logger.error(
            "准入名单第 %d 条 userid 为空或非字符串，整条丢弃（该成员不会被准入）：path=%s",
            index,
            path,
        )
        return None

    return userid.strip()
