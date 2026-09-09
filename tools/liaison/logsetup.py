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
_PROTECTED_SPAN_RE = re.compile(
    r"\b(?:" + "|".join(PROTECTED_KEYS) + r")\b\s*[=:]"
    r"(?:\s*'[^']*'"
    r"|\s*\"[^\"]*\""
    r"|[^\s,;)\]}&/:=]+"
    r"|)"
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
        re.compile(r"(?<!\d)(?:\+?0?0?86[- ]?)?1[3-9]\d{9}(?!\d)"),
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

    ⚠️ `super().format(record)` 会把**未脱敏**的 traceback 文本缓存进
    `record.exc_text`。本方法只对**返回值**做替换，⛔ 不回写那个缓存。今天这是
    安全的：本模块给所有 handler 配的都是本 Formatter，没有第二个 formatter 会
    读到它。⛔ 谁要给 `tools.liaison` 挂一个裸 `logging.Formatter`，先回来读这段。
    """

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


def _resolve_log_dir(explicit: "str | os.PathLike[str] | None") -> pathlib.Path:
    if explicit is not None:
        return pathlib.Path(explicit).expanduser()
    override = os.environ.get(LOG_DIR_ENV)
    if override and override.strip():
        return pathlib.Path(override.strip()).expanduser()
    return DEFAULT_LOG_DIR


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
    """
    global _status
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    _detach_managed_handlers(logger)
    _detach_redaction_filters(logger)
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
    resolved_level = (level or os.environ.get(LOG_LEVEL_ENV) or DEFAULT_LEVEL).upper()
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
    stream_handler.setLevel(resolved_level)
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
            file_handler.setLevel(resolved_level)
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
