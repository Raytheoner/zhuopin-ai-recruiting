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
4. **失败 ERROR 按「名单文件内容的 SHA-256」去重**（TD-15）：同一份内容连续失败
   只记一组 ERROR，内容一变就重新记一组。⛔ 这不是降级——每一个**不同的**失败态
   仍然都被 ERROR 记录过，第 1 条的契约原样成立；去掉的只是**同一失败态的重复副本**。
   ⛔ 缓存的是"上次已记录的失败指纹"这一个字符串，**绝不缓存名单本身**
   （第 2 条是 spec 硬要求）：判定路径每次仍然重新读文件、重新解析、重新校验，
   去重只作用在 `logger.error` 这一步。为什么需要它：出厂态（两条 `userid` 留空）
   下每次判定必产 3 条 ERROR，而第 4／5 章**每条入站消息**调一次 `admit()`——
   不去重就是持续误报，运维会学会忽略这个 logger，而模块里真正的合规漏洞
   （值泄漏、顶层字段静默忽略）恰恰只靠 ERROR 日志暴露。
"""

from __future__ import annotations

import hashlib
import logging
import traceback
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

# 文档顶层只允许这一个键。多一个顶层键（例如运维顺手加的联系方式字段）
# 必须和条目级同等严格地整份名单全拒——不能只在 _validated_userid 里
# 做白名单，让顶层留一个"合规扫描照不到"的口子。
ALLOWED_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset({"members"})


#: TD-15 的全部状态：上一次**已经记录过**的失败指纹（名单文件内容的 SHA-256）。
#: ⛔ 这里存的是一个 64 字符的十六进制串，**不是名单、不是文件内容、不是解析结果**——
#: 它进不了任何判定路径，`load_whitelist` 每次仍然完整重读重解析。
#: 单槽而不是集合：内存有界，且"坏 A → 坏 B → 又回到坏 A"会重新记一次（合理，
#: 那确实是一次新的状态变化）。
_LAST_LOGGED_FAILURE_FINGERPRINT: str | None = None


def _content_fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _roster_fingerprint(path: Path, raw: bytes) -> str:
    """名单**内容**的 SHA-256，前面拼上路径。

    拼路径的理由：两个不同文件恰好写坏成同一份内容，是两个各自独立的失败现场，
    ⛔ 不该互相把对方的 ERROR 吞掉。生产上只有一份 `DEFAULT_WHITELIST_PATH`，
    拼不拼路径对去重效果没有任何差别，纯粹是把"跨文件误吞"这条堵死。
    """
    return _content_fingerprint(f"{path!r}|".encode("utf-8", "replace") + raw)


def _reset_failure_dedup() -> None:
    """清空去重槽。给测试用——⛔ 不要在生产路径上调它（那等于关掉去重）。"""
    global _LAST_LOGGED_FAILURE_FINGERPRINT
    _LAST_LOGGED_FAILURE_FINGERPRINT = None


class _FailureLog:
    """一次 `load_whitelist` 调用期间的 ERROR 记录器，按内容指纹去重。

    去重判据在**构造时**就把上一轮的指纹快照下来（`_previous`），⛔ 不在
    `error()` 里现读全局——否则本轮第一条 ERROR 一提交就会更新全局，
    同一轮的第 2／3 条会被自己刚写的指纹误判成"重复"而吞掉，
    出厂态那 3 条就只剩 1 条了。全局只在 `finish()`（本轮走完）时更新一次。
    """

    def __init__(self) -> None:
        self._previous = _LAST_LOGGED_FAILURE_FINGERPRINT
        self.fingerprint: str | None = None
        self.attempted = False
        self.emitted = False

    def _suppressed(self) -> bool:
        return self.fingerprint is not None and self.fingerprint == self._previous

    def error(self, message: str, *args: Any) -> None:
        self.attempted = True
        if self._suppressed():
            return
        self.emitted = True
        logger.error(message, *args)

    def finish(self) -> None:
        """本轮走完后更新全局指纹槽。

        ⛔ 不叫 `commit()`：`test_no_second_transaction_manager_in_source` 的静态
        扫描器把 `tools/liaison/` 非测试代码里任何 `.commit()` 调用都判成
        「第二个事务管理者」。那条断言是刻意宁可误报的（漏判没有症状），
        所以这里换个名字，⛔ 不要为了这个名字去放宽那道闸。

        - 本轮**有**失败 → 记住这份内容的指纹，下一轮同内容同失败就闭嘴；
        - 本轮**无**失败 → 清空，让"修好之后又改坏成同一份内容"能重新报。
        """
        global _LAST_LOGGED_FAILURE_FINGERPRINT
        if self.emitted:
            # 只在真的输出过 ERROR 时，才在组尾补一句可发现性提示：
            # 运维看见 3 条 ERROR 之后突然安静，不能让他误以为问题自己好了。
            logger.error(
                "以上准入名单失败按文件内容去重：内容不变时不再重复记录，"
                "内容一变会重新记一组（TD-15）：fingerprint=%s",
                self.fingerprint,
            )
        _LAST_LOGGED_FAILURE_FINGERPRINT = self.fingerprint if self.attempted else None


class _DuplicateKeyError(yaml.YAMLError):
    """YAML 映射里出现重复键。

    ⛔ 不复用 PyYAML 的静默 last-wins：运维若"再追加一段 `members:`"而不是
    扩写原有列表，名单会被**静默替换**成后一段，闸门看起来健康。
    带上键名（键名是字段名，与 `top_level_extra` / `extra` 两处日志同口径；
    ⛔ 仍不带字段**值**）与行列号，让运维能直接定位。
    """

    def __init__(self, key: str, line: int | None, column: int | None) -> None:
        super().__init__("准入名单出现重复键")
        self.duplicate_key = key
        self.line = line
        self.column = column


class _NoDuplicateKeySafeLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` + 重复键 fail-closed。

    PyYAML 的 `construct_mapping` 对重复键取后者且零日志。这里在构造映射前
    先自己扫一遍键，撞到重复就抛 `_DuplicateKeyError`。
    """

    def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicated = key in seen
            except TypeError:
                # 不可哈希的键（如 `[a, b]: x`）——按其文本形态比较即可，
                # 反正后面 ALLOWED_TOP_LEVEL_KEYS / ALLOWED_MEMBER_FIELDS 会全拒。
                key = repr(key)
                duplicated = key in seen
            if duplicated:
                mark = getattr(key_node, "start_mark", None)
                raise _DuplicateKeyError(
                    str(key),
                    None if mark is None else mark.line + 1,
                    None if mark is None else mark.column + 1,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


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


def load_whitelist(path: Path | None = None) -> frozenset[str]:
    """读名单文件，返回可准入的 userid 集合。任何失败一律返回空集合。

    ⛔ 默认值不写成 `path: Path = DEFAULT_WHITELIST_PATH`——那样默认值在
    `def` 执行时就绑死了，运行时重新赋值 `whitelist_module.DEFAULT_WHITELIST_PATH`
    （启动期覆盖、测试 monkeypatch）对调用方是死信，永远读回当初绑的那份。
    这里在函数体内每次调用时重新取模块变量，rebinding 才生效。
    """
    if path is None:
        path = DEFAULT_WHITELIST_PATH
    failures = _FailureLog()
    try:
        return _read_roster(path, failures)
    except Exception as exc:  # noqa: BLE001
        # 兜底带：_read_roster 已按类型分支捕获了预期失败。这里接住的是未预期的
        # 异常——⛔ 不允许它逃到调用方——spec 要求"任何判定路径上的失败结果
        # 都必须是未命中"。
        #
        # 真实原因（终审 Critical 实测纠正过一版过于乐观的评估）：这里接住的
        # 异常不是本模块写的，是 `yaml.safe_load` 内部——PyYAML 对 `!!int`
        # `!!bool` 这类标签在标量构造失败时，抛的是 `ValueError`/`KeyError`
        # 而不是 `yaml.YAMLError`，绕过了上面 `_read_roster` 里专门加固的
        # YAMLError 分支。这些异常的 **消息本身**（`str(exc)`）原样嵌着触发
        # 解析失败的标量文本——例如 `invalid literal for int() with base 10:
        # '13800138000abc'`——如果那段文本是配置里的手机号，`exc_info=True`
        # 连同 traceback 里的异常消息一起会把它转印进日志。
        # 所以：⛔ 绝不记录 `str(exc)` / `repr(exc)` / `exc.args`（消息可能
        # 内嵌第三方库正在处理的源文本），只记异常*类型*与调用帧的
        # 文件名/行号/函数名（`traceback.extract_tb` 给的 `FrameSummary`
        # 不含被处理的数据，只含我们自己和 PyYAML 的源码位置）。
        # ⛔ 不要因为"这个分支本来就该安全"就把这段评估再乐观化——
        # 上一版评估只审计了本模块自己构造异常的代码，没考虑异常可能
        # 源自调用的第三方库，就是这次被抓到的疏漏。
        frames = "; ".join(
            f"{frame.filename}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)
        )
        if failures.fingerprint is None:
            # 连文件内容都没读到就炸了（例如 `Path(path)` 本身抛）——没有内容可
            # 指纹化。退化成"输入 + 异常类型"的指纹：对同一坏输入依然稳定可去重，
            # ⛔ 不含任何文件内容。
            failures.fingerprint = _content_fingerprint(
                f"{type(exc).__name__}|{path!r}".encode("utf-8", "replace")
            )
        failures.error(
            "准入名单加载出现未预期异常，按空名单全拒（⛔ 不记录异常消息本身——"
            "第三方库抛出的异常可能把源码/字段值嵌进消息里）："
            "path=%s error_type=%s frames=%s",
            path,
            type(exc).__name__,
            frames,
        )
        return frozenset()
    finally:
        failures.finish()


def admit(sender_userid: Any, path: Path | None = None) -> bool:
    """调用缝：加载名单 + 判定。每次调用重新读文件（见模块 docstring 第 2 条）。

    🔴 `path` 默认 `None`，在 `load_whitelist` 里才解析成
    `DEFAULT_WHITELIST_PATH`——⛔ 不要在这里写 `path or DEFAULT_WHITELIST_PATH`：
    `Path("")` 是 falsy，`or` 会把它悄悄换成出厂配置路径，
    这正是 `test_admit_never_raises_on_hostile_paths` 要防的行为改写。
    这里索性把 `path` 原样透传给 `load_whitelist`，判定逻辑只在一处。
    """
    return compute_admission(sender_userid, load_whitelist(path))


def _yaml_error_location(exc: yaml.YAMLError) -> str:
    """从 YAMLError 里只取行列号，供运维定位问题行；绝不取内容。

    `yaml.MarkedYAMLError`（覆盖 ScannerError/ParserError/ConstructorError 等
    实践中会遇到的全部子类）带 `problem_mark`，其 `.line`/`.column` 是纯整数，
    不会像 `str(mark)` 那样触发 `get_snippet()` 回显源码。同一异常常常还带
    `context_mark`——它标的是"问题从哪里开始"（例如一个没闭合的引号从哪个
    字符起算），往往比 `problem_mark`（"扫描器在哪里放弃"，对未闭合引号这类
    错误常常落在文件末尾）更贴近真正出错的那一行；两者都只是行列整数，同样
    不含内容，一并给出。`.line`/`.column` 是 0-based，这里转成 1-based 以
    对齐编辑器里看到的行号。普通 `YAMLError`（两个 mark 都没有）返回空字符串
    ——没有位置信息可给。
    """
    parts: list[str] = []
    context_mark = getattr(exc, "context_mark", None)
    if context_mark is not None:
        parts.append(f"context_line={context_mark.line + 1} context_column={context_mark.column + 1}")
    problem_mark = getattr(exc, "problem_mark", None)
    if problem_mark is not None:
        parts.append(f"line={problem_mark.line + 1} column={problem_mark.column + 1}")
    if not parts:
        return ""
    return " " + " ".join(parts)


def _read_roster(path: Path, failures: _FailureLog) -> frozenset[str]:
    # TD-16 ③：类型标注写的是 `Path`，但第 4／5 章的调用方完全可能传字符串。
    # 不归一化的话 `str` 会一路走到 `.read_text` 才炸成"未预期异常"——
    # fail-closed 正确但诊断错位（运维看到的是内部异常，不是"文件读不到"）。
    path = Path(path)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        # 没有内容可指纹化（文件根本读不到）。用"路径 + errno"作指纹：
        # 同一个"文件缺失"状态稳定去重，换一个路径或换一种 errno 会重新记。
        failures.fingerprint = _content_fingerprint(
            f"OSError|{path!r}|{getattr(exc, 'errno', None)}".encode("utf-8", "replace")
        )
        # OSError 的消息只含 errno/strerror/文件名（如"[Errno 2] No such file or
        # directory: '...'"），不会携带文件*内容*——这里 err=%s 是安全的，
        # 不需要像下面 YAMLError 分支那样做行列号改写。
        failures.error("准入名单不可读，按空名单全拒：path=%s err=%s", path, exc)
        return frozenset()

    failures.fingerprint = _roster_fingerprint(path, raw_bytes)

    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        # TD-16 ②：`UnicodeDecodeError` 是 `ValueError` 不是 `OSError`，此前会掉进
        # `load_whitelist` 的兜底带被报成"未预期异常"——而"另存时编码选错"恰恰是
        # 运维在 Windows 记事本上最容易犯的那个错。单列成一类，用运维能看懂的话说。
        # ⛔ 只记 encoding／偏移量／reason（都是定长元信息），不记 `str(exc)`，
        # 更不记 `exc.object`——那是文件内容本身。
        failures.error(
            "准入名单不是 UTF-8 编码，按空名单全拒（另存为 UTF-8 无 BOM 即可；"
            "⛔ 错误信息不回显文件内容）：path=%s encoding=%s byte_offset=%d reason=%s",
            path,
            exc.encoding,
            exc.start,
            exc.reason,
        )
        return frozenset()

    try:
        document = yaml.load(raw, Loader=_NoDuplicateKeySafeLoader)  # noqa: S506
    except _DuplicateKeyError as exc:
        # TD-16 ①：PyYAML 原生行为是静默 last-wins。运维「再追加一段 `members:`」
        # 时名单会被后一段整份替换，⛔ 零日志——这里改成 fail-closed 并点名键名。
        failures.error(
            "准入名单出现重复键 %r，按空名单全拒（PyYAML 原生行为是静默取后者，"
            "会让「追加一段」变成「替换整份名单」；请把新成员并进同一个 members 列表）："
            "path=%s line=%s column=%s",
            exc.duplicate_key,
            path,
            exc.line,
            exc.column,
        )
        return frozenset()
    except yaml.YAMLError as exc:
        # ⛔ 故意不记 str(exc) / exc.problem / problem_mark.get_snippet()。
        # PyYAML 的报错信息内嵌出错行的源码片段（Mark.__str__ 会触发
        # get_snippet()），如果运维往名单里填了手机号又恰好写坏了文件，
        # 号码会原样被这个片段转印进日志——绕开 `_validated_userid` 里
        # 做的全部字段名/值区分。这里只取行列号定位问题，不取内容，
        # 不要为了"调试方便"把异常文本加回来。
        failures.error(
            "准入名单解析失败，按空名单全拒（⛔ 不沿用任何此前加载过的名单；"
            "错误信息不回显源文本，避免配置里的字段值出现在日志里）："
            "path=%s error_type=%s%s",
            path,
            type(exc).__name__,
            _yaml_error_location(exc),
        )
        return frozenset()

    if not isinstance(document, Mapping):
        failures.error("准入名单顶层不是映射，按空名单全拒：path=%s", path)
        return frozenset()

    top_level_extra = set(document.keys()) - ALLOWED_TOP_LEVEL_KEYS
    if top_level_extra:
        # 顶层字段必须和条目级同等严格：多一个键（哪怕只是运维顺手加的
        # 联系方式字段）就整份名单全拒，不是"忽略陌生顶层键、照常吃
        # members"——否则 `config/README.md` 承诺的"白名单式校验"在顶层
        # 就是一句空话。⛔ 日志只写键名，不写值：多余顶层键的值同样可能
        # 是不该采集的个人信息。
        failures.error(
            "准入名单顶层含不允许的字段 %s，按空名单全拒（只允许 %s）：path=%s",
            sorted(str(key) for key in top_level_extra),
            sorted(ALLOWED_TOP_LEVEL_KEYS),
            path,
        )
        return frozenset()

    members = document.get("members")
    if not isinstance(members, list):
        failures.error("准入名单缺 members 列表或类型不对，按空名单全拒：path=%s", path)
        return frozenset()

    admitted = {
        userid
        for index, entry in enumerate(members)
        if (userid := _validated_userid(entry, index, path, failures)) is not None
    }

    if not admitted:
        failures.error("准入名单为零条有效条目，全部发送人判为未命中：path=%s", path)

    return frozenset(admitted)


def _validated_userid(
    entry: Any, index: int, path: Path, failures: _FailureLog
) -> str | None:
    """校验单条名单条目。不合格返回 None（整条丢弃），并记 ERROR。

    ⛔ 日志只写字段**名**，绝不写字段**值**——多余字段的值恰恰可能就是
    手机号／邮箱这类不该被采集的个人信息，写进日志等于把它换个地方留存。
    """
    if not isinstance(entry, Mapping):
        failures.error("准入名单第 %d 条不是映射，整条丢弃：path=%s", index, path)
        return None

    keys = set(entry.keys())
    extra = keys - ALLOWED_MEMBER_FIELDS
    if extra:
        failures.error(
            "准入名单第 %d 条含不允许的字段 %s，整条丢弃（只允许 %s；⛔ 不采集手机号／邮箱／身份证号）：path=%s",
            index,
            sorted(str(key) for key in extra),
            sorted(ALLOWED_MEMBER_FIELDS),
            path,
        )
        return None

    missing = ALLOWED_MEMBER_FIELDS - keys
    if missing:
        failures.error(
            "准入名单第 %d 条缺字段 %s，整条丢弃：path=%s", index, sorted(missing), path
        )
        return None

    userid = entry["userid"]
    if not isinstance(userid, str) or not userid.strip():
        failures.error(
            "准入名单第 %d 条 userid 为空或非字符串，整条丢弃（该成员不会被准入）：path=%s",
            index,
            path,
        )
        return None

    return userid.strip()
