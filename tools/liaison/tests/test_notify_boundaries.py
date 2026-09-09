"""第 6 章·结构性约束（6.7 + D9 的「阈值不共用」+「不静默截断」+ 无 `with`）。

这些断言守的是**写不出来**，不是「跑起来对」。它们变红时的正确修法是删掉违规
代码，⛔ 不是给断言加豁免。

⚠️ 每一组扫描器都配了一条**证伪用例**（喂一段故意违规的假源码，断言它确实报得
出来）。没有证伪的结构性断言与「永远为真」无法区分，而永远为真的断言比没有断言
更糟——它会让人以为这条线有人守着。
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re

import pytest

from tools.liaison.notify import guard

NOTIFY_ROOT = pathlib.Path(guard.__file__).resolve().parent

#: 企微「发给指定人/部门/标签」的三个参数名，以及候选人相关的收件对象叫法。
#: ⚠️ 刻意用 `search` 而不是全匹配：`candidate_name` 这种也要抓住。
_RECIPIENT_PARAM = re.compile(
    r"(touser|to_user|toparty|to_party|totag|to_tag|candidate|"
    r"recipient|openid|open_id|external_?user_?id)",
    re.IGNORECASE,
)
_RECIPIENT_KEY = {"touser", "toparty", "totag", "external_userid", "openid"}

#: 「被发送的正文」在本包里的叫法。对它们做切片 = 疑似静默截断。
_BODY_NAMES = {"text", "body", "content", "full_text", "summary"}

_LIMIT_CONSTANTS = ("AIBOT_CHANNEL_LIMIT_BYTES", "GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES")


def _notify_sources() -> list[pathlib.Path]:
    """`notify/` 下的**非测试** Python 源码。"""
    return sorted(p for p in NOTIFY_ROOT.rglob("*.py") if not p.name.startswith("test_"))


def _parse(source: str, *, filename: str = "<memory>") -> ast.Module:
    return ast.parse(source, filename=filename)


# --------------------------------------------------------------------------
# 扫描器：全部接收**源码字符串**，因此既能扫真文件，也能被证伪用例喂假源码。
# --------------------------------------------------------------------------


def find_recipient_params(source: str, *, filename: str = "<memory>") -> list[str]:
    """找出任何「发给谁」形参。⛔ 只看形参，不看注释与 docstring。"""
    offenders: list[str] = []
    for node in ast.walk(_parse(source, filename=filename)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        names = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                names.append(extra.arg)
        for name in names:
            if _RECIPIENT_PARAM.search(name):
                offenders.append(f"{filename}::{node.name}({name})")
    return offenders


def find_recipient_dict_keys(source: str, *, filename: str = "<memory>") -> list[str]:
    """找出任何以收件对象为键的字典字面量。⛔ 只看字典键，不看注释与 docstring。"""
    offenders: list[str] = []
    for node in ast.walk(_parse(source, filename=filename)):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if key.value.lower() in _RECIPIENT_KEY:
                    offenders.append(f"{filename}:{key.lineno} 键 {key.value!r}")
    return offenders


def find_body_slices(source: str, *, filename: str = "<memory>") -> list[str]:
    """找出对「被发送正文」的切片——静默截断在语法上的样子。"""
    offenders: list[str] = []
    for node in ast.walk(_parse(source, filename=filename)):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            source_name = getattr(node.value, "id", "") or getattr(node.value, "attr", "")
            if source_name in _BODY_NAMES:
                offenders.append(f"{filename}:{node.lineno} 对 {source_name} 做了切片")
    return offenders


def find_with_statements(source: str, *, filename: str = "<memory>") -> list[str]:
    """找出任何 `with` / `async with`（呼应第 2 章事务扫描器，本包自带一条）。

    ⛔ 用 AST，不用字符串 grep——docstring 里提到 `with` 这个词是允许的。
    """
    offenders: list[str] = []
    for node in ast.walk(_parse(source, filename=filename)):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            offenders.append(f"{filename}:{node.lineno} 出现 with 语句")
    return offenders


def _module_level_limit_assignments() -> dict[str, ast.expr]:
    """`guard.py` 里模块级 `*_LIMIT_BYTES` 常量的**赋值表达式**。"""
    tree = _parse((NOTIFY_ROOT / "guard.py").read_text(encoding="utf-8"), filename="guard.py")
    found: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_LIMIT_BYTES"):
                    found[target.id] = node.value
    return found


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------


def test_there_are_sources_to_scan():
    """自检：扫描面为空的断言永远绿，那比没有断言更糟。"""
    assert len(_notify_sources()) >= 5


# --------------------------------------------------------------------------
# 第一组：6.7 收件对象在结构上不存在
# --------------------------------------------------------------------------


@pytest.mark.compliance
def test_no_function_takes_a_recipient_parameter():
    """6.7：⛔ 不存在以候选人标识为收件对象的参数。

    收件对象由 webhook 地址决定，而地址只能来自 HR_LIAISON_GROUP_WEBHOOK
    （内部值守群）。任何「发给谁」的形参都是在这条通道上开一个对外的口子。
    """
    offenders: list[str] = []
    for path in _notify_sources():
        offenders += find_recipient_params(
            path.read_text(encoding="utf-8"), filename=path.name
        )
    assert offenders == [], f"群通知通道出现了收件对象参数：{offenders}"


@pytest.mark.compliance
def test_no_payload_dict_carries_a_recipient_key():
    """6.7：⛔ 不存在以候选人标识为收件对象的调用路径。

    只看字典字面量的键，⛔ 不看注释与 docstring——注释里写「⛔ 不发候选人」是应该的，
    写成一个字段才是问题。
    """
    offenders: list[str] = []
    for path in _notify_sources():
        offenders += find_recipient_dict_keys(
            path.read_text(encoding="utf-8"), filename=path.name
        )
    assert offenders == [], f"群通知负载里出现了收件对象字段：{offenders}"


def test_recipient_scanners_actually_catch_a_violation():
    """证伪：断言本身得真的能抓到东西，否则它只是装饰。"""
    fake = (
        "def send_markdown(body, touser):\n"
        "    payload = {'msgtype': 'text', 'touser': touser}\n"
        "    return payload\n"
    )
    param_offenders = find_recipient_params(fake, filename="fake.py")
    key_offenders = find_recipient_dict_keys(fake, filename="fake.py")
    assert param_offenders == ["fake.py::send_markdown(touser)"], param_offenders
    assert key_offenders == ["fake.py:2 键 'touser'"], key_offenders

    # 注释与 docstring 里出现这些词是**应该的**，⛔ 不许被扫成违规。
    innocent = (
        'def send_markdown(body):\n'
        '    """⛔ 不发候选人（candidate）、不带 touser/toparty/totag。"""\n'
        '    return {"msgtype": "markdown"}  # touser 绝不出现\n'
    )
    assert find_recipient_params(innocent, filename="innocent.py") == []
    assert find_recipient_dict_keys(innocent, filename="innocent.py") == []


# --------------------------------------------------------------------------
# 第二组：两条通道的阈值结构上不可共用
# --------------------------------------------------------------------------


def test_the_two_channel_limits_are_never_read_from_one_shared_name():
    """D9：⛔ 两条通道不共用同一个阈值常量。

    判据不是「两个数不相等」（那太容易靠改数糊弄），而是：`guard.py` 里 ⛔ 不存在
    一个既不叫 AIBOT_* 也不叫 GROUP_WEBHOOK_* 的「通用上限」常量。
    """
    limit_names = sorted(_module_level_limit_assignments())
    assert limit_names == sorted(_LIMIT_CONSTANTS), f"guard.py 里的阈值常量集合变了：{limit_names}"


def test_the_two_channel_limits_have_different_values():
    """D9 逐字：aibot 20480 字节、群 webhook 4096 字节，两个数、两个来源。"""
    assert guard.AIBOT_CHANNEL_LIMIT_BYTES == 20480
    assert guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES == 4096
    assert guard.AIBOT_CHANNEL_LIMIT_BYTES != guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    # 每条通道各包一份，⛔ 不许两个 ChannelLimit 指向同一个数。
    assert guard.AIBOT_CHANNEL.limit_bytes == guard.AIBOT_CHANNEL_LIMIT_BYTES
    assert guard.GROUP_WEBHOOK_CHANNEL.limit_bytes == guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
    assert guard.AIBOT_CHANNEL.limit_bytes != guard.GROUP_WEBHOOK_CHANNEL.limit_bytes


def test_neither_channel_limit_is_derived_from_the_other():
    """⛔ 两者不是同一个名字的别名：各自是独立的整数字面量赋值。

    `X = Y` / `X = Y * n` / `X = Y // n` 这类推导一律不许——推导出来的两个数看着
    是两个，改一个动两个，静默截断就是这么埋进去的。
    """
    assigns = _module_level_limit_assignments()
    for name in _LIMIT_CONSTANTS:
        value = assigns[name]
        assert isinstance(value, ast.Constant) and isinstance(value.value, int), (
            f"{name} 不是独立的整数字面量赋值：{ast.dump(value)}"
        )
        referenced = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
        assert referenced == set(), f"{name} 的取值引用了别的名字：{referenced}"


def test_compute_length_guard_requires_an_explicit_keyword_limit():
    """`limit_bytes` 是**必传关键字参数、⛔ 无默认值**。

    「忘了传、用了别的通道的默认值」这个错误因此在语法层就写不出来——调用点会
    当场 TypeError，⛔ 不会静默套用另一条通道的数。
    """
    param = inspect.signature(guard.compute_length_guard).parameters["limit_bytes"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty

    plan_param = inspect.signature(guard.compute_notify_plan).parameters["limit_bytes"]
    assert plan_param.kind is inspect.Parameter.KEYWORD_ONLY
    assert plan_param.default is inspect.Parameter.empty

    with pytest.raises(TypeError):
        guard.compute_length_guard("忘了传阈值")  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# 第三组：⛔ 无静默截断
# --------------------------------------------------------------------------


@pytest.mark.compliance
def test_no_silent_truncation_anywhere_in_the_notify_package():
    """D9 逐字：⛔ 任何形式的静默截断。

    判据：`notify/` 下 ⛔ 不存在对被发送文本的切片（`text[:n]`）。唯一被允许的
    「裁剪」是 `compute_text_prefix_within_bytes`，而它的产物永远和一句显式的降级
    声明拼在一起再发出去。
    """
    offenders: list[str] = []
    for path in _notify_sources():
        offenders += find_body_slices(path.read_text(encoding="utf-8"), filename=path.name)
    assert offenders == [], f"群通知包里出现了对正文的切片（疑似静默截断）：{offenders}"


def test_truncation_scanner_actually_catches_a_violation():
    """证伪：切片扫描器真的抓得到 `text[:n]`。"""
    fake = "def send(text):\n    return text[:100]\n"
    assert find_body_slices(fake, filename="fake.py") == ["fake.py:2 对 text 做了切片"]
    # 非正文的切片（路径改写）不算违规，⛔ 不许误报把人逼去关掉这条断言。
    innocent = "def upload_url(parsed):\n    return parsed.path[:-5]\n"
    assert find_body_slices(innocent, filename="innocent.py") == []


@pytest.mark.compliance
def test_degraded_body_always_declares_itself():
    """降级发出去的每一条提要，都必须自带「我被降级过」的声明。"""
    for size in (2000, 5000, 20000):
        plan = guard.compute_notify_plan(
            "中" * size,
            limit_bytes=guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES,
            attachment_supported=True,
        )
        assert plan.mode == guard.MODE_DEGRADED
        assert "降级" in plan.body
        assert plan.attachment_filename in plan.body
        assert plan.attachment_content == "中" * size, "附件必须是完整原文，⛔ 不是提要"
        assert guard.compute_byte_length(plan.body) <= guard.GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES


# --------------------------------------------------------------------------
# 第四组：⛔ 非测试代码里没有 with 语句
# --------------------------------------------------------------------------


def test_no_with_statement_in_notify_non_test_sources():
    """呼应第 2 章事务扫描器：本包自带一条，出事时定位更快。

    第 2 章把 `with X:` 判为「隐式提交事务边界」违规，`with <Call>:` 只放行一份
    正面白名单（TD-18 细化，`8bae001`），`urlopen` 属陌生被调用者、照旧会红。
    本包一律不写 `with`：响应用 `resp = ...` + `try/finally: resp.close()`。
    """
    offenders: list[str] = []
    for path in _notify_sources():
        offenders += find_with_statements(path.read_text(encoding="utf-8"), filename=path.name)
    assert offenders == [], f"notify/ 的非测试代码里出现了 with 语句：{offenders}"


def test_with_scanner_actually_catches_a_violation():
    """证伪：with 扫描器抓得到真的 `with`，且 ⛔ 不被 docstring 里的词误伤。"""
    fake = "def f(conn):\n    with conn:\n        conn.execute('INSERT INTO t VALUES (1)')\n"
    assert find_with_statements(fake, filename="fake.py") == ["fake.py:2 出现 with 语句"]
    innocent = 'def f():\n    """⛔ 不许写 with 语句。"""\n    return "with"\n'
    assert find_with_statements(innocent, filename="innocent.py") == []


def test_notify_package_never_imports_time_or_requests():
    """结构性：时钟与传输都必须注入；⛔ 不引入第三方 HTTP 库（design D10）。"""
    banned = {"time", "requests", "httpx", "urllib3", "aiohttp"}
    offenders: list[str] = []
    for path in _notify_sources():
        tree = _parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in banned:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    offenders.append(f"{path.name}: from {node.module}")
    assert offenders == [], offenders
