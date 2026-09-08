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
    try:
        return _read_roster(path)
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
        logger.error(
            "准入名单加载出现未预期异常，按空名单全拒（⛔ 不记录异常消息本身——"
            "第三方库抛出的异常可能把源码/字段值嵌进消息里）："
            "path=%s error_type=%s frames=%s",
            path,
            type(exc).__name__,
            frames,
        )
        return frozenset()


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


def _read_roster(path: Path) -> frozenset[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        # OSError 的消息只含 errno/strerror/文件名（如"[Errno 2] No such file or
        # directory: '...'"），不会携带文件*内容*——这里 err=%s 是安全的，
        # 不需要像下面 YAMLError 分支那样做行列号改写。
        logger.error("准入名单不可读，按空名单全拒：path=%s err=%s", path, exc)
        return frozenset()

    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # ⛔ 故意不记 str(exc) / exc.problem / problem_mark.get_snippet()。
        # PyYAML 的报错信息内嵌出错行的源码片段（Mark.__str__ 会触发
        # get_snippet()），如果运维往名单里填了手机号又恰好写坏了文件，
        # 号码会原样被这个片段转印进日志——绕开 `_validated_userid` 里
        # 做的全部字段名/值区分。这里只取行列号定位问题，不取内容，
        # 不要为了"调试方便"把异常文本加回来。
        logger.error(
            "准入名单解析失败，按空名单全拒（⛔ 不沿用任何此前加载过的名单；"
            "错误信息不回显源文本，避免配置里的字段值出现在日志里）："
            "path=%s error_type=%s%s",
            path,
            type(exc).__name__,
            _yaml_error_location(exc),
        )
        return frozenset()

    if not isinstance(document, Mapping):
        logger.error("准入名单顶层不是映射，按空名单全拒：path=%s", path)
        return frozenset()

    top_level_extra = set(document.keys()) - ALLOWED_TOP_LEVEL_KEYS
    if top_level_extra:
        # 顶层字段必须和条目级同等严格：多一个键（哪怕只是运维顺手加的
        # 联系方式字段）就整份名单全拒，不是"忽略陌生顶层键、照常吃
        # members"——否则 `config/README.md` 承诺的"白名单式校验"在顶层
        # 就是一句空话。⛔ 日志只写键名，不写值：多余顶层键的值同样可能
        # 是不该采集的个人信息。
        logger.error(
            "准入名单顶层含不允许的字段 %s，按空名单全拒（只允许 %s）：path=%s",
            sorted(str(key) for key in top_level_extra),
            sorted(ALLOWED_TOP_LEVEL_KEYS),
            path,
        )
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

    keys = set(entry.keys())
    extra = keys - ALLOWED_MEMBER_FIELDS
    if extra:
        logger.error(
            "准入名单第 %d 条含不允许的字段 %s，整条丢弃（只允许 %s；⛔ 不采集手机号／邮箱／身份证号）：path=%s",
            index,
            sorted(str(key) for key in extra),
            sorted(ALLOWED_MEMBER_FIELDS),
            path,
        )
        return None

    missing = ALLOWED_MEMBER_FIELDS - keys
    if missing:
        logger.error(
            "准入名单第 %d 条缺字段 %s，整条丢弃：path=%s", index, sorted(missing), path
        )
        return None

    userid = entry["userid"]
    if not isinstance(userid, str) or not userid.strip():
        logger.error(
            "准入名单第 %d 条 userid 为空或非字符串，整条丢弃（该成员不会被准入）：path=%s",
            index,
            path,
        )
        return None

    return userid.strip()
