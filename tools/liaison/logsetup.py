"""HR 值守通道的日志装配：轮转 + 有界容量 + 个人信息脱敏（tasks.md 8.4）。

**借用 `runtime-observability` 的做法，⛔ 不 import `app.*`**（design D7 + D10）。
`app/observability/{logging_config,redaction,handlers}.py` 是本模块的**参照读物**：
做法照抄（有界轮转文件、脱敏、不可写时降级不崩），代码自建。一旦从那边 import，
`tools/` 与产品交付物之间就出现一条编译期依赖，D10 的隔离会从「结构上不可能被
同步到 .51」退化成「记得别用」。

⛔ **本模块禁止写 `with`。** 第 2 章的
`tools/liaison/tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source`
把 `tools/liaison/` 下非测试代码里**任何** `with <名字|属性|调用>:` 判为「隐式提交
事务边界」违规——它认的是 `ast.With` 的形状，不看上下文管理器是不是数据库连接。
TD-18 之后 `open()` 等少数 callee 被放行，但 ⛔ 不要依赖那份白名单：本模块一律用
`pathlib.Path.write_text()` / `unlink()`。`test_logsetup_module_never_uses_a_with_statement`
把这条钉死。

⚠️ **脱敏为什么必须挂到 handler 上、而不只是包级 logger 上**：`Logger.handle()` 只对
**记录发起的那个 logger** 跑 filter；子 logger 的记录沿祖先链找 **handler**，⛔ 不会
再跑祖先 logger 的 filter。本服务每个模块都是 `logging.getLogger(__name__)`
（`tools.liaison.inbound` 等），所以只挂包级 logger 等于**对真正会打印个人信息的那些
行完全失明**，且这个失明不报错、无症状。见 `setup_logging` 的实现与
`test_child_logger_records_are_redacted_too` 的证伪。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field

#: 本服务全部模块的 logger 都挂在这个包名下（每个模块 `getLogger(__name__)`）。
PACKAGE_LOGGER_NAME = "tools.liaison"
LOG_FILENAME = "liaison.log"
#: ⛔ 不放 `%(request_id)s`：本服务是常驻长连接进程，没有请求边界（design D7）。
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

#: tools/liaison/logsetup.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
#: `data/` 已被 `.gitignore:11` 整目录忽略（实测 `git check-ignore -v` 命中该行），
#: 日志不会误入版本管理。⛔ 不要再往 `.gitignore` 加一条——同一件事两处真源。
DEFAULT_LOG_DIR = REPO_ROOT / "data" / "liaison" / "logs"

LOG_DIR_ENV = "HR_LIAISON_LOG_DIR"
LOG_LEVEL_ENV = "HR_LIAISON_LOG_LEVEL"
LOG_MAX_BYTES_ENV = "HR_LIAISON_LOG_MAX_BYTES"
LOG_BACKUP_COUNT_ENV = "HR_LIAISON_LOG_BACKUP_COUNT"

DEFAULT_LEVEL = "INFO"
#: 磁盘占用上界 = DEFAULT_MAX_BYTES × (DEFAULT_BACKUP_COUNT + 1) = 30 MiB。
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5

#: 打在 handler 实例上的标记，让 `setup_logging` 只摘自己挂的那些 handler。
#: ⛔ 不要改成「清空 logger.handlers」——那会连别人（如 pytest 的插件）挂上去的
#: handler 一起摘掉，而这种破坏是静默的。
MANAGED_HANDLER_ATTR = "_hr_liaison_managed"

SECRET_MASK = "<redacted:secret>"
PHONE_MASK = "<redacted:phone>"
EMAIL_MASK = "<redacted:email>"
IDCARD_MASK = "<redacted:idcard>"

#: ⛔ **不脱敏**的键（opener 约束 2 逐字：排障要用）。
#:
#: ⚠️ 代价是明写的，⛔ 不要在实现里偷偷补救：`thread_id` 私聊时取的就是 `userid`，
#: 企微后台允许把 userid 配成手机号；真出现这种取值，它会以明文留在日志里。这是
#: 被显式选定的一侧——该取值本来就作为主键明文存在 `liaison_message.thread_id`
#: 列里，日志不构成新增的泄露面，而「日志里的 thread_id 被打成 <redacted:phone>」
#: 会让归档链路彻底不可追。
#:
#: ⛔ `sender_userid` **不进**本名单：定位一条消息有 `msgid` 就够了，把它保护起来
#: 只是白白扩大明文面。⛔ 往本名单加键之前先回答「不加它，排障具体卡在哪一步」。
PROTECTED_KEYS: tuple[str, ...] = ("msgid", "thread_id")

#: 保护段：`msgid=…` / `thread_id=…`（也认 `: `）整段原样保留。
#: 值的四种形态按顺序尝试：单引号串、双引号串、裸值（吃到下一个空白/逗号/分号/
#: 右括号为止）、空值（`thread_id=` 后面什么都没有）。⛔ 不处理嵌套结构——日志里
#: 这两个键从来都是标量。
_PROTECTED_SPAN_RE = re.compile(
    r"\b(?:" + "|".join(PROTECTED_KEYS) + r")\b\s*[=:]\s*"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;)\]}]*)"
)

#: 凭据取值：键名保留（排障要知道是哪一项没配好），取值一律打码。
#: 覆盖 `HR_LIAISON_*` 与 `LLM_*` 两族（opener 约束 2 逐字）。
#: ⚠️ 这会连 `HR_LIAISON_LOG_DIR=/x/y` 的路径也一起打掉——刻意如此：按前缀一刀切
#: 才不需要维护一份「哪些 HR_LIAISON_* 是秘密」的名单，而那种名单必然漏。
_CREDENTIAL_RE = re.compile(
    r"\b((?:HR_LIAISON|LLM)_[A-Z0-9_]+)(\s*[=:]\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;)\]}]+)"
)

#: 按**值的形态**判定，⛔ 不按键名白名单——`runtime-observability`「新增字段的默认
#: 归属」要的就是「未声明即受控」：业务对象新增一个字段，只要它的取值长得像手机号
#: /邮箱/身份证，就自动被覆盖，⛔ 不需要有人记得去登记。
#:
#: **顺序有意义**：邮箱在最前（邮箱本地部分可能含 11 位数字，先整体吃掉才不会被
#: 手机号规则咬掉一半）；身份证在手机号之前（18 位号的前后有 `(?<!\d)`/`(?!\d)`
#: 护栏，本来就不会被手机号规则误伤，但顺序写死省得后人推理）。
#:
#: ⛔ **刻意不做 15 位旧版身份证**：15 位纯数字与时间戳、SDK 序号、字节数撞得太厉害，
#: 加进来会把大量无害数字打成 `<redacted:idcard>`，让日志读不懂。旧版身份证在本服务
#: 的场景（在职员工与候选人）里已基本绝迹。⛔ 不要"顺手补上"。
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), EMAIL_MASK),
    (
        re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dXx])"
        ),
        IDCARD_MASK,
    ),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), PHONE_MASK),
)


def _redact_free_span(span: str) -> str:
    """对**保护段之外**的一段文本做脱敏。⛔ 不要单独调它——保护段的切分在
    `compute_redacted_text` 里，绕过那一步就会把 `thread_id` 一起打码。"""
    if not span:
        return span
    span = _CREDENTIAL_RE.sub(r"\1\2" + SECRET_MASK, span)
    for pattern, mask in _VALUE_PATTERNS:
        span = pattern.sub(mask, span)
    return span


def compute_redacted_text(text: str) -> str:
    """脱敏的全部语义。**纯函数**（铁律 2）：⛔ 不读环境、不写文件、不打日志。

    **顺序是结构性的，⛔ 不许调换**：先把 `msgid=…` / `thread_id=…` 整段切出来
    原样保留，只对它们**之间**的文本跑脱敏正则。反过来做（先跑正则、再想办法把
    误伤的键救回来）必然在某天把一个长得像手机号的 `thread_id` 打成
    `<redacted:phone>`，那条日志就再也和库里的行对不上了。

    **幂等**：输出再跑一遍本函数结果不变（掩码串里不含邮箱/手机号/身份证形态），
    因此 Filter 与 Formatter 两层都跑一遍是安全的。
    """
    if not text:
        return text
    pieces: list[str] = []
    cursor = 0
    for match in _PROTECTED_SPAN_RE.finditer(text):
        pieces.append(_redact_free_span(text[cursor : match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_redact_free_span(text[cursor:]))
    return "".join(pieces)
