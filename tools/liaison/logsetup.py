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
#: 值的三种形态按顺序尝试：单引号串、双引号串（引号本身就是无歧义的取值边界，
#: 前面允许空白）、裸值（**必须紧贴分隔符**，中间不许有空白）；都不中就落到空值
#: 分支，只保护到分隔符为止。⛔ 不处理嵌套结构——日志里这两个键从来都是标量。
#:
#: Round-1 fix-round 修订（reviewer 抓出的两处出血，均只在"更多脱敏"方向纠偏，
#: 不扩大 `PROTECTED_KEYS` 的豁免面）：
#: 1) 旧版在分隔符与裸值之间也放了 `\s*`，导致 `thread_id= ` 后面隔一个空白的
#:    下一个 token（哪怕是手机号/邮箱/凭据）被当成"取值"一起原样保留、明文漏出；
#:    `\s` 含 `\n`，连换行后的下一行都会被吃进去。现在裸值分支不允许前导空白，
#:    宁可落到空值分支，也不猜一个不存在的取值。
#: 2) 旧版裸值的终止符集合 `[\s,;)\]}]` 漏了 `&`、`/`、`:`、`=`，导致
#:    `thread_id=wm001&mobile=13812345678` 这类"取值后面紧跟下一个键值对"的渲染，
#:    会把 `&mobile=13812345678` 也一起吞进保护段。现在补齐这四个终止符。
#:
#: TD-31/TD-32 还债（[Mac]0909AM）：见 `_PROTECTED_SPAN_RE` 上方的成段说明。
_PROTECTED_KEY_ALT = "|".join(PROTECTED_KEYS)

#: 键与取值之间的分隔符。半角 `=` `:` 之外补上**全角** `＝`（U+FF1D）与 `：`
#: （U+FF1A）——中文输入法下这两个是默认输出，一条人工拼接的日志里极容易混进来。
_PROTECTED_SEP = r"\s*[=:＝：]"

#: 取值的引号形态。引号本身就是无歧义的取值边界，所以前面允许空白。
_PROTECTED_QUOTED_VALUE = r"\s*'[^']*'|\s*\"[^\"]*\""

#: 裸值的终止符集合。TD-32 追加：两种引号 + 全角逗号/分号/冒号/等号/右括号——
#: 少了它们，`thread_id：wm001，mobile：138…` 这类全角渲染会把后面那一对键值
#: 一起吞进保护段，反过来变成**扩大**明文面。
_PROTECTED_BARE_VALUE = r"[^\s'\",;)\]}&/:=，；：＝）]+"

#: **TD-32 修复**（成因见 `docs/tech-debt.md` TD-32）：旧版要求分隔符紧贴**裸**键名，
#: 于是 `{"thread_id": "138…"}` / `{'msgid': '139…'}` / `thread_id：138…` 这三类渲染
#: 整条失配，取值落进脱敏区被**反过来打码**，那条日志从此和 `liaison_message.thread_id`
#: 对不上——而且**无症状**，看起来只是"脱敏很尽职"。
#:
#: 修法＝拆成两支，⛔ 不是把原来那支放松：
#: · 分支 1（键名被成对引号包住，即 JSON / dict / `%r` 渲染）：取值前额外允许
#:   **一个半角空格**。`json.dumps` 的默认分隔符就是 `": "`，数值型取值
#:   （`{"thread_id": 13812345678}`）不带引号，不放这一个空格照样落进脱敏区。
#:   ⛔ 只放一个空格、⛔ 不用 `\s*`——`\s` 含 `\n`，那正是 Round-1 修掉的出血。
#: · 分支 2（裸键名）：**逐字保持 Round-1 语义**，`thread_id= <下一个 token>` 仍按
#:   空值处理。放宽只发生在分支 1，因为「键名带引号」本身就是"这是个结构化渲染、
#:   下一个 token 就是取值"的强证据；裸键名没有这个证据，宁可多打码。
#:
#: ⛔ **`sender_userid` 不许顺手加进 `PROTECTED_KEYS`**（TD-32 逐字警告）：那是真的
#: 扩大明文面。`test_sender_userid_stays_masked_in_the_new_renderings` 把这条钉死。
_PROTECTED_SPAN_RE = re.compile(
    rf"(?P<q>['\"])\b(?:{_PROTECTED_KEY_ALT})\b(?P=q){_PROTECTED_SEP}"
    rf"(?:{_PROTECTED_QUOTED_VALUE}|[ ]?{_PROTECTED_BARE_VALUE}|)"
    r"|"
    rf"\b(?:{_PROTECTED_KEY_ALT})\b{_PROTECTED_SEP}"
    rf"(?:{_PROTECTED_QUOTED_VALUE}|{_PROTECTED_BARE_VALUE}|)"
)

#: 凭据取值：键名保留（排障要知道是哪一项没配好），取值一律打码。
#: 覆盖 `HR_LIAISON_*` 与 `LLM_*` 两族（opener 约束 2 逐字）。
#: ⚠️ 这会连 `HR_LIAISON_LOG_DIR=/x/y` 的路径也一起打掉——刻意如此：按前缀一刀切
#: 才不需要维护一份「哪些 HR_LIAISON_* 是秘密」的名单，而那种名单必然漏。
#:
#: Round-1 fix-round 修订：键名两侧允许一个可选、且必须成对匹配的引号
#: （`(?P<q>['"]?)…(?P=q)`）——`logger.debug("config=%r", cfg)` 这类调用点会把
#: dict/JSON 原样打印出来，键名天然带引号（`"LLM_API_KEY": …`、
#: `'HR_LIAISON_BOT_SECRET': …`），旧版要求分隔符紧跟裸键名，遇到引号整条失配、
#: 取值明文漏出。分隔符另外接受 `->`（`os.environ HR_LIAISON_BOT_SECRET -> value`
#: 这类人工排障时的口语化渲染）。
_CREDENTIAL_RE = re.compile(
    r"(?P<q>['\"]?)\b(?P<key>(?:HR_LIAISON|LLM)_[A-Z0-9_]+)\b(?P=q)"
    r"(?P<sep>\s*(?:->|[=:])\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;)\]}]+)"
)

#: 数字：半角 + 全角。全角数字是从企微客户端/Office 粘过来的号码的常见形态，
#: TD-31 实测 `１３８１２３４５６７８` 旧版整条失配、明文落盘。
_DIGIT = r"[0-9０-９]"

#: 号码内部允许的分组分隔符：连字符、点、空格，各自的半角与全角形态
#: （`－` 全角连字符、`．` 全角句点、`　` 全角空格）。
#: ⛔ 逗号/分号/斜杠**不在**其中，理由见 `_VALUE_PATTERNS` 上方的成段说明。
_PHONE_SEP = "[-.－．　 ]"

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
#:
#: Round-1 fix-round 修订：手机号规则补上可选的国际区号前缀
#: `(?:\+?0?0?86[- ]?)?`，覆盖 `+8613812345678`（区号与号码无分隔符连写）与
#: `008613812345678`。旧版 `(?<!\d)` 护栏挂在 11 位号码本身前面，遇到区号紧贴
#: （紧邻字符是数字 `6`）就直接失配；现在护栏挪到区号前面，区号作为匹配的一部分
#: 一并打码。时间戳/字节数的护栏效果不变——纯数字串里 `(?<!\d)` 只在串首成立，
#: 区号候选组不匹配时退化为空，不改变这一点。
#:
#: **TD-31 修复**（成因见 `docs/tech-debt.md` TD-31）：旧版只认 11 位**连写**，
#: `138-1234-5678` / `138 1234 5678` / `138.1234.5678` / 全角数字整条失配、明文落盘，
#: 而且**无症状**——同一行里的邮箱照样打码，看起来脱敏在正常工作。
#:
#: 放宽的边界是刻意画死的（opener：⛔ 不许放宽到会误伤正常数字串的程度）：
#: · 号段头三位 `1[3-9]\d` **必须连写**，⛔ 分隔符不进这三位。这一条把
#:   「一串被空格隔开的无关数字」挡在门外，是整条规则的主护栏。
#: · 分隔后只认 **3-4-4** 这一种分组（现实里手机号就这么写），⛔ 不认任意分组。
#: · 分隔符只收连字符/点/空格（含各自全角），⛔ **不收 `,` `;` `/`**——那些在日志里
#:   分隔的是两个不同字段，收进来就会把三段无关数字连成一个假手机号。
#:   `test_phone_separator_class_does_not_join_unrelated_fields` 钉死这条。
#: 时间戳护栏不受影响：`LOG_FORMAT` 渲染出的 `2026-09-09 13:45:01,123` 里没有任何
#: 「连写的 1[3-9]\d + 分隔 + 4 位 + 分隔 + 4 位」，见
#: `test_log_format_timestamp_is_not_mistaken_for_a_separated_phone`。
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), EMAIL_MASK),
    (
        re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dXx])"
        ),
        IDCARD_MASK,
    ),
    (
        re.compile(
            rf"(?<!{_DIGIT})"
            rf"(?:[(（]?[+＋]?0?0?86[)）]?[-－ 　]?)?"
            rf"[1１][3-9３-９]{_DIGIT}"
            rf"(?:{_DIGIT}{{8}}|{_PHONE_SEP}{_DIGIT}{{4}}{_PHONE_SEP}{_DIGIT}{{4}})"
            rf"(?!{_DIGIT})"
        ),
        PHONE_MASK,
    ),
)


def _redact_free_span(span: str) -> str:
    """对**保护段之外**的一段文本做脱敏。⛔ 不要单独调它——保护段的切分在
    `compute_redacted_text` 里，绕过那一步就会把 `thread_id` 一起打码。"""
    if not span:
        return span
    span = _CREDENTIAL_RE.sub(r"\g<q>\g<key>\g<q>\g<sep>" + SECRET_MASK, span)
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


class RedactionFilter(logging.Filter):
    """在 record 层做脱敏。**必须挂到每个 handler 上**，见模块 docstring。

    同一条 record 会流经多个 handler（stderr + file）各 filter 一遍。用
    `record.hr_liaison_redacted` 做已处理标记，避免第二个 handler 把已经替换成
    掩码的文本重新扫一遍——不是为了正确性（`compute_redacted_text` 是幂等的），
    而是为了别把一次记录算成两次命中，也省掉一次全文正则。
    """

    RECORD_MARKER = "hr_liaison_redacted"

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self.hits = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, self.RECORD_MARKER, False):
            return True
        try:
            rendered = record.getMessage()
        except Exception as exc:
            # `getMessage()` 会因为 %-占位符与 args 数量对不上而抛异常。⛔ 绝不能
            # 把未脱敏的 record 原样放行——它会在 emit() 里再抛一次，落进 stdlib 的
            # `Handler.handleError()`，后者把 `record.msg` / `record.args` **原文**
            # 写进 sys.stderr，绕开 Filter 与 Formatter 两层防线。就地中和：丢掉
            # 原始负载，换一句不带内容的诊断信息。
            record.msg = (
                "[liaison-redaction] 日志格式化失败，原始 msg/args 已丢弃以避免明文外泄："
                f"logger={record.name} 位置={record.pathname}:{record.lineno} "
                f"错误类型={type(exc).__name__}"
            )
            record.args = ()
            setattr(record, self.RECORD_MARKER, True)
            return True

        redacted = compute_redacted_text(rendered)
        if redacted != rendered:
            # 替换后 args 必须清空：否则 `getMessage()` 会拿掩码文本再做一次
            # %-格式化，把 `<redacted:phone>` 里不存在的占位符和 args 对不上。
            record.msg = redacted
            record.args = ()
            self.hits += 1
        setattr(record, self.RECORD_MARKER, True)
        return True


class RedactingFormatter(logging.Formatter):
    """异常堆栈 ⛔ 不经过 `record.getMessage()`，Filter 看不到它。

    堆栈里会出现局部变量的 repr、以及抛异常那一行的**源码原文**（
    `raise ValueError("联系 someone@example.com")` 这种）。所以格式化之后再扫
    一遍最终文本，是 Filter 之外必须补的一刀。

    ⚠️ `super().format(record)` 内部会调 `formatException()` 把 traceback 渲染
    成文本并**缓存进 `record.exc_text`**，供同一条 record 之后被别的 handler /
    formatter 复用（stdlib 的省算设计）。`propagate=True` 是 `setup_logging` 刻意
    保留的（见其 docstring），意味着这条 record 之后会继续往 root 走——root 上
    任何调用方或第三方库挂的裸 `logging.Formatter`（今天仓库里还没有，但
    `logging.basicConfig()` 或任意一次调试用的 `StreamHandler` 都会造出一个）都
    会读到这份缓存。⛔ 因此"没有第二个 formatter 会读到它"是假设、不是事实，
    真正的安全靠的是**在源头脱敏这份缓存本身**：重写 `formatException()`，让
    `record.exc_text` 从被写入的那一刻起就已经是脱敏文本，而不是指望没人会用
    第二个 formatter 去读原始缓存。
    """

    def formatException(self, ei) -> str:
        return compute_redacted_text(super().formatException(ei))

    def format(self, record: logging.LogRecord) -> str:
        return compute_redacted_text(super().format(record))


@dataclass
class LoggingStatus:
    """日志子系统的当前状态。**降级不许是静默的**——`runtime-observability`
    逐字：「MUST NOT 静默降级为"什么都不记录"」。降级事实同时走两条路暴露：
    一条 ERROR 日志（走 stderr，launchd 收得到）+ 这个可读的状态对象。"""

    configured: bool = False
    degraded: bool = False
    reason: str | None = None
    log_file: str | None = None
    handlers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "configured": self.configured,
            "degraded": self.degraded,
            "reason": self.reason,
            "log_file": self.log_file,
            "handlers": list(self.handlers),
        }


_status = LoggingStatus()


def logging_status() -> LoggingStatus:
    return _status


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """环境变量取整。**取不到、非数字、越界一律回落到默认值，⛔ 不抛异常**——
    日志装配是进程的第一个动作，让它因为一个手抖的环境变量把服务打死，
    代价与收益差着数量级。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= minimum else default


def _resolve_level(raw: str | None) -> str:
    """把环境变量/参数里的日志级别取值规整成合法级别名。⛔ 不抛异常——本函数
    的唯一调用点 `setup_logging` 自己的 docstring 承诺「不可写时 ⛔ 不崩溃、
    ⛔ 不阻断业务功能」；一个带尾随空格的级别名（plist 的 `EnvironmentVariables`
    里很容易手抖多打一个空格）或数字级别（`20` 是很常见的约定）没有理由把整个
    进程在装配日志之前——`setup_logging()` 是 `main()` 的第一句，此时还没有任何
    handler——就用一条未捕获异常打死。取不到、非法一律回落到 `DEFAULT_LEVEL`。

    ⚠️ **`NOTSET` 被排除在外**（final review Minor 6）：`logging.NOTSET`（值 0）
    技术上是一个合法级别名，但语义是「没有设置，跟随父级 effective level」——
    包级 logger 的父级是 root，root 默认 `WARNING`。原样接受的话，运维传一个
    望文生义的 `HR_LIAISON_LOG_LEVEL=NOTSET`（期望"什么都记录"）会静默变成
    "只记 WARNING 以上"，比压根不设这个变量更容易让人误解。宁可回落
    `DEFAULT_LEVEL`，也不放这个反直觉的行为过去。
    """
    candidate = (raw or DEFAULT_LEVEL).strip().upper()
    names = logging.getLevelNamesMapping()
    if candidate in names and candidate != "NOTSET":
        return candidate
    try:
        numeric = int(candidate)
    except ValueError:
        return DEFAULT_LEVEL
    if numeric == logging.NOTSET:
        return DEFAULT_LEVEL
    for name, value in names.items():
        if value == numeric and name != "NOTSET":
            return name
    return DEFAULT_LEVEL


def _resolve_log_dir(explicit: "str | os.PathLike[str] | None") -> pathlib.Path:
    """⚠️ **两个 `expanduser()` 调用都必须被兜住**（final review Important 1）：
    对形如 `~nosuchuser/…` 的路径，`expanduser()` 抛的是 `RuntimeError`
    （"Could not determine home directory"），⛔ 不是 `OSError`——下游
    `_probe_writable` 只兜 `OSError`，这个异常会直接穿透 `setup_logging()`
    （`main()` 的第一句，此时还没有任何 handler），变成一次无日志、无
    `LoggingStatus` 的 launchd 崩溃循环。取不到就回落 `DEFAULT_LOG_DIR`，让
    `_probe_writable` 按老路径继续走「不可写就降级」，而不是让路径解析本身
    成为第二条会崩溃的路。"""
    if explicit is not None:
        try:
            return pathlib.Path(explicit).expanduser()
        except (RuntimeError, OSError, ValueError):
            return DEFAULT_LOG_DIR
    override = os.environ.get(LOG_DIR_ENV)
    if override and override.strip():
        try:
            return pathlib.Path(override.strip()).expanduser()
        except (RuntimeError, OSError, ValueError):
            return DEFAULT_LOG_DIR
    return DEFAULT_LOG_DIR


def resolve_log_dir(explicit: "str | os.PathLike[str] | None" = None) -> pathlib.Path:
    """日志目录的**公开**解析入口（`HR_LIAISON_LOG_DIR` ⇒ 回落 `DEFAULT_LOG_DIR`）。

    TD-30 的留存期清理要清的是**这个**目录下的轮转产物，而它必须与
    `setup_logging` 真正写日志的那个目录是同一个。⛔ 不许在清理侧另写一遍
    "读 `HR_LIAISON_LOG_DIR`、读不到用默认"——同一件事两处真源，改一处漏一处
    的后果是"清理跑得很成功，清的却是一个没人往里写的空目录"，且毫无症状。
    """
    return _resolve_log_dir(explicit)


def _probe_writable(directory: pathlib.Path) -> str | None:
    """返回 None 表示可写，否则返回不可写的原因（人类可读）。

    ⛔ 不用 `with open(...)`（模块 docstring 第 2 段）：`Path.write_text()` +
    `unlink()` 同样能探到权限/磁盘/父路径是文件这三类故障。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _detach_managed_handlers(logger: logging.Logger) -> None:
    """只摘本模块自己挂的 handler。⛔ 不许写成 `logger.handlers.clear()`——
    那会把 pytest 插件、调试器之类挂上去的 handler 一起摘掉，静默破坏。"""
    for handler in list(logger.handlers):
        if getattr(handler, MANAGED_HANDLER_ATTR, False):
            logger.removeHandler(handler)
            handler.close()


def _detach_redaction_filters(logger: logging.Logger) -> None:
    for log_filter in list(logger.filters):
        if isinstance(log_filter, RedactionFilter):
            logger.removeFilter(log_filter)


def teardown_logging() -> None:
    """摘掉本模块挂的一切并复位状态。**只给测试收尾用**，⛔ 生产路径不调。

    没有它，上一条用例挂的 file handler 会一直攥着一个已被删除的 tmp 目录，
    下一条用例的日志就写进了一个看不见的地方。

    ⚠️ **level 必须复位回 `NOTSET`**：`setup_logging` 会 `logger.setLevel(...)`，
    而 level 是挂在 logger 对象本身、不是挂在某个 handler 上的状态——只摘 handler
    不摘 level，上一条用例设的级别会在进程里永久生效，把下一条用例的记录在
    `Logger.isEnabledFor()` 这一步就悄悄过滤掉（比如 `caplog.at_level("WARNING")`
    只抬高了 root 的级别，抬不动这个包级 logger）。`NOTSET` 让它退回"跟随
    effective level"的默认状态，不是"跟随生产环境残留的上一次配置"。
    """
    global _status
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    _detach_managed_handlers(logger)
    _detach_redaction_filters(logger)
    logger.setLevel(logging.NOTSET)
    _status = LoggingStatus()


def setup_logging(
    *,
    log_dir: "str | os.PathLike[str] | None" = None,
    level: str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> LoggingStatus:
    """进程启动时调一次，装配 `tools.liaison` 包级 logger。**幂等**。

    ⚠️ **读的是进程环境，读不到 `.env`。** 本函数被接在 `__main__.main()` 的
    **第一句**，排在 `load_dotenv_into_environ(...)` **前面**（opener 约束 3 逐字：
    ⛔ 不动既有行）。这是刻意的：本服务由 launchd 守护，环境变量来自 plist 的
    `EnvironmentVariables`，进程环境本来就是权威来源；而排在最前面换来的是
    **凭据校验失败那条路径也已经有日志**——那是启动期最常见的一类失败。
    ⛔ 不许为了让 `.env` 生效把本调用挪到 dotenv 之后。

    ⚠️ **⛔ 不动 `logger.propagate`（保持 True）。** 置 False 会让 `caplog` 抓不到
    本包的记录，把第 1–7 章一批现存用例静默变哑（它们断言的是 `caplog.text`）。
    生产环境下 root 没有 handler，不会重复输出。

    命名：⛔ 不叫 `effect_setup_logging`。铁律 2 的 `compute_*`/`effect_*` 是给
    **L4 编排层的图节点**定的命名法（用来让「需要幂等键」一眼可见）；本函数是
    进程 bootstrap，不在任何图里、不落 `effect_log`，幂等由「先摘旧 handler 再挂
    新的」这个覆写语义保证。opener 约束 3 逐字写的就是 `setup_logging()`。

    不可写时**⛔ 不崩溃、⛔ 不阻断业务功能**：退回只有 stderr 的配置，把降级
    事实记进 ERROR 日志与 `LoggingStatus`。
    """
    global _status

    directory = _resolve_log_dir(log_dir)
    resolved_level = _resolve_level(level or os.environ.get(LOG_LEVEL_ENV))
    resolved_max_bytes = (
        max_bytes
        if max_bytes is not None
        else _env_int(LOG_MAX_BYTES_ENV, DEFAULT_MAX_BYTES, minimum=1)
    )
    resolved_backup_count = (
        backup_count
        if backup_count is not None
        else _env_int(LOG_BACKUP_COUNT_ENV, DEFAULT_BACKUP_COUNT, minimum=0)
    )

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    # 幂等的全部实现：先把上一次挂的摘干净，再挂新的。⛔ 不要改成「已配置就直接
    # 返回」——那会让改了环境变量之后的第二次调用**静默无效**。
    _detach_managed_handlers(logger)
    _detach_redaction_filters(logger)

    logger.setLevel(resolved_level)
    logger.addFilter(RedactionFilter())

    formatter = RedactingFormatter(LOG_FORMAT)

    # stderr 一份：launchd 的 `StandardErrorPath` 会收（部署约束 4 + design D12）。
    # 它同时是「文件通道自己坏掉时」唯一还能说话的通道，所以**先挂它**——降级那条
    # ERROR 日志要靠它出去。
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RedactionFilter())
    # ⛔ 不设 stream_handler.setLevel(...)（final review Important 2）：包级
    # logger 自己的 setLevel(resolved_level) 已经在 callHandlers 派发前把低于
    # 级别的 record 挡掉了，handler 级别的 setLevel 只是重复同一道闸门——而
    # `Logger.callHandlers` 是先比较 `record.levelno` 和 `hdlr.level`、比较不过
    # 才连 `hdlr.handle()`（也就是本 handler 的 `RedactionFilter`）都不会跑，
    # 直接把这条 record 原样甩给下一个 handler。`propagate=True` 是本模块刻意
    # 保留的，于是任何比 handler level 低的记录会在完全跳过脱敏的情况下明文
    # 传到 root 上的其它 handler——这道多余的闸门不省事，只白白开一扇泄露窗。
    setattr(stream_handler, MANAGED_HANDLER_ATTR, True)
    logger.addHandler(stream_handler)

    handler_names = ["stderr"]
    log_path = directory / LOG_FILENAME
    reason = _probe_writable(directory)
    if reason is None:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=resolved_max_bytes,
                backupCount=resolved_backup_count,
                encoding="utf-8",
            )
        except OSError as exc:
            # 探测通过之后仍可能失败（竞态、句柄耗尽）。同样不许崩。
            reason = f"{type(exc).__name__}: {exc}"
        else:
            file_handler.setFormatter(formatter)
            file_handler.addFilter(RedactionFilter())
            # ⛔ 同上不设 file_handler.setLevel(...)——理由见 stream_handler 那条
            # 注释，两处是同一个漏洞的两个实例。
            setattr(file_handler, MANAGED_HANDLER_ATTR, True)
            logger.addHandler(file_handler)
            handler_names.append("file")

    _status = LoggingStatus(
        configured=True,
        degraded=reason is not None,
        reason=reason,
        log_file=str(log_path) if reason is None else None,
        handlers=handler_names,
    )

    if _status.degraded:
        logger.error(
            "日志文件通道不可用，已降级为仅 stderr：目录=%s 原因=%s。"
            "业务功能不受影响，但排障证据不会落盘——请检查该目录的存在性与写权限",
            directory,
            reason,
        )
    return _status
