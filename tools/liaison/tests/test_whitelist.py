"""准入名单（tasks.md 第 3 章）的行为契约。"""

from __future__ import annotations

import ast
import hashlib
import inspect
import logging
import os
import re
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.liaison import whitelist as whitelist_module
from tools.liaison.whitelist import admit, compute_admission, load_whitelist

PACKAGE_ROOT = Path(whitelist_module.__file__).resolve().parent
SHIPPED_CONFIG = PACKAGE_ROOT / "config" / "whitelist.yaml"

D2_ADMITTED = ["汤丽萍", "邵培申"]
D2_REJECTED = ["聂鑫", "王寒月", "陈承"]

PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_PATTERN = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 邮箱混淆写法：用 (at) / [at] / 前后带空格的 at 代替 @。
_EMAIL_OBFUSCATION = re.compile(r"\(at\)|\[at\]|\s+at\s+", re.IGNORECASE)


def _strip_phone_id_separators(text: str) -> str:
    """去掉手机号/身份证号常见的分隔符（连字符、空白）后再匹配。

    ⛔ 不要把这一步"简化掉"——不做归一化，正则只能防住"数字连在一起"的号码，
    `138-0013-8000` / `138 0013 8000` 这类分隔符变形能在肉眼审查下轻易蒙混过关
    （终审 review 实测：这三种变形原样能通过全部 5 条断言）。这里对着整份配置
    做全局归一化是安全的，不是碰运气：`whitelist.yaml` 里当前只有孤立的单个数字
    （如 "D2" 里的 "2"），两侧都不是数字，去掉分隔符不会把互不相干的数字串拼接
    成看起来像手机号/身份证号的假阳性；新增条目改动了这个前提时，靠下面
    `test_shipped_config_is_exactly_the_d2_roster` 等其它断言兜底可读性核对。
    """
    return re.sub(r"[-\s]", "", text)


def _deobfuscate_email(text: str) -> str:
    """把 (at) / [at] / 前后带空格的 at 还原成 @，让混淆邮箱也能被同一条正则捕获。

    ⛔ 不要删掉这一步——`tangliping(at)zhuopin.com` 这类写法在原始文本里根本
    不含 "@"，不做还原，上面的邮箱正则永远搜不到它。
    """
    return _EMAIL_OBFUSCATION.sub("@", text)


CONTACT_CHECKS = (
    ("手机号", PHONE_PATTERN, _strip_phone_id_separators),
    ("身份证号", ID_PATTERN, _strip_phone_id_separators),
    ("邮箱", EMAIL_PATTERN, _deobfuscate_email),
)


def test_shipped_config_exists():
    assert SHIPPED_CONFIG.is_file()


def test_shipped_config_carries_no_contact_information():
    raw = SHIPPED_CONFIG.read_text(encoding="utf-8")
    for label, pattern, normalize in CONTACT_CHECKS:
        normalized = normalize(raw)
        assert pattern.search(normalized) is None, f"准入名单配置里出现了{label}"


def test_shipped_config_entries_have_exactly_three_fields():
    document = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    for entry in document["members"]:
        assert set(entry) == {"userid", "name", "role"}


def test_shipped_config_is_exactly_the_d2_roster():
    document = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    assert [entry["name"] for entry in document["members"]] == D2_ADMITTED


def test_shipped_config_excludes_the_three_d2_rejections():
    text = SHIPPED_CONFIG.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    names = {entry["name"] for entry in document["members"]}
    for rejected in D2_REJECTED:
        assert rejected not in names


def write_roster(path: Path, members: list[dict[str, object]]) -> Path:
    """用 safe_dump 写名单，避免手写 YAML 的引号／缩进误差混进用例。"""
    path.write_text(
        yaml.safe_dump({"members": members}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def source_fingerprint() -> str:
    """tools/liaison 下全部 .py 的内容指纹，用于证明"名单变更没改代码"。"""
    digest = hashlib.sha256()
    for py in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        digest.update(py.relative_to(PACKAGE_ROOT).as_posix().encode("utf-8"))
        digest.update(py.read_bytes())
    return digest.hexdigest()


def called_names(func) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def impure_nodes(func) -> list[str]:
    """AST 侧写：抓非调用型副作用。

    `called_names` 只看 `ast.Call`，对不经过函数调用的副作用是瞎的——
    终审用这个反例实测过：给 `compute_admission` 加一行模块级计数器自增
    `_CALL_COUNT[0] += 1`（`AugAssign`，目标是 `Subscript`，全程没有一个
    `Call` 节点），`test_compute_admission_is_pure` 原样通过。这里把口子
    补上，但只咬"写入逃逸到函数外"的目标（`Attribute` / `Subscript`）：
    普通局部变量赋值（目标是 `Name`）必须放行——
    `candidate = sender_userid.strip()` 正是这种合法写法，把它也判违规
    会直接把现有实现钉成假阳性。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    violations: list[str] = []

    def escapes(target: ast.expr) -> bool:
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            return True
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(escapes(elt) for elt in target.elts)
        if isinstance(target, ast.Starred):
            return escapes(target.value)
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(escapes(t) for t in node.targets):
            violations.append("Assign->escaping-target")
        elif isinstance(node, ast.AugAssign) and escapes(node.target):
            violations.append("AugAssign->escaping-target")
        elif isinstance(node, ast.AnnAssign) and escapes(node.target):
            violations.append("AnnAssign->escaping-target")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            violations.append(type(node).__name__)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.append(type(node).__name__)

    return violations


def error_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR and "whitelist" in r.name]


@pytest.fixture(autouse=True)
def _reset_failure_dedup():
    """每条用例前后都清空 TD-15 的去重槽。

    ⛔ 不要删这个 fixture：去重槽是模块级全局，两条用例若恰好写出同一份坏内容，
    后跑的那条会被前一条的指纹吞掉 ERROR 而**静默变绿/变红**——那种失败极难归因。
    """
    whitelist_module._reset_failure_dedup()
    yield
    whitelist_module._reset_failure_dedup()


def test_missing_file_yields_empty_whitelist(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(tmp_path / "absent.yaml") == frozenset()
    assert error_records(caplog)


def test_unparsable_file_yields_empty_whitelist(tmp_path, caplog):
    broken = tmp_path / "whitelist.yaml"
    broken.write_text("members:\n  - userid: [unclosed\n", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(broken) == frozenset()
    assert error_records(caplog)


@pytest.mark.parametrize(
    "content", ["", "members: []\n", "members:\n", "{}\n", "[]\n", "just a string\n"]
)
def test_empty_or_shapeless_roster_yields_empty_whitelist(tmp_path, caplog, content):
    path = tmp_path / "whitelist.yaml"
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert error_records(caplog)


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: -1)() == 0, reason="root 绕过文件权限，无法构造不可读文件"
)
def test_unreadable_file_yields_empty_whitelist(tmp_path, caplog):
    blocked = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    blocked.chmod(0o000)
    try:
        with caplog.at_level(logging.ERROR):
            assert load_whitelist(blocked) == frozenset()
        assert error_records(caplog)
    finally:
        blocked.chmod(0o600)


def test_corrupting_the_file_never_reuses_the_previous_roster(tmp_path):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    assert load_whitelist(path) == frozenset({"TangLiPing"})

    path.write_text("members:\n  - userid: [unclosed\n", encoding="utf-8")
    assert load_whitelist(path) == frozenset()
    assert admit("TangLiPing", path) is False


def test_unexpected_exception_is_swallowed_into_empty_whitelist(tmp_path, monkeypatch, caplog):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )

    def explode(_raw, *args, **kwargs):
        raise RecursionError("PyYAML 在畸形输入上可能抛出非 YAMLError")

    # TD-16 ① 之后加载器换成 `yaml.load(..., Loader=_NoDuplicateKeySafeLoader)`
    # （SafeLoader 的子类，安全性不变，见 test_yaml_python_tags_are_not_constructed），
    # 所以这里改打 `yaml.load`。本测试要证的仍是同一件事：YAML 层抛出的
    # **非 YAMLError** 异常不许逃到调用方。
    monkeypatch.setattr(whitelist_module.yaml, "load", explode)
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert error_records(caplog)


@pytest.mark.parametrize(
    "path_arg",
    [Path("/nonexistent/deeply/absent.yaml"), Path("/"), Path("/dev/null"), Path("")],
)
def test_admit_never_raises_on_hostile_paths(path_arg):
    assert admit("TangLiPing", path_arg) is False


def test_compute_admission_hits_a_member():
    assert compute_admission("TangLiPing", frozenset({"TangLiPing", "ShaoPeishen"})) is True


def test_compute_admission_normalizes_surrounding_whitespace():
    """钉住 `.strip()`：去掉它 33 条用例全绿也不会发现——终审实测过。"""
    assert compute_admission(" TangLiPing ", frozenset({"TangLiPing"})) is True


def test_compute_admission_misses_a_non_member():
    assert compute_admission("NieXin", frozenset({"TangLiPing"})) is False


@pytest.mark.parametrize("sender", [None, "", "   ", 123, b"TangLiPing", ["TangLiPing"]])
def test_compute_admission_rejects_malformed_sender(sender):
    assert compute_admission(sender, frozenset({"TangLiPing"})) is False


def test_compute_admission_is_pure():
    """铁律 2：compute_* 是无副作用纯函数。新增 I/O 或日志会让这条断言失败。"""
    assert called_names(compute_admission) <= {"isinstance", "strip"}
    assert impure_nodes(compute_admission) == []


def test_compute_admission_ignores_environment(monkeypatch):
    monkeypatch.setenv("HR_LIAISON_WHITELIST", "NieXin")
    monkeypatch.setenv("HR_LIAISON_WHITELIST_PATH", "/tmp/anything.yaml")
    assert compute_admission("NieXin", frozenset({"TangLiPing"})) is False


def test_admit_hits_a_member_from_file(tmp_path):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    assert admit("TangLiPing", path) is True
    assert admit("ShaoPeishen", path) is True
    assert admit("NieXin", path) is False


def test_blank_userid_entry_is_dropped(tmp_path, caplog):
    """出厂配置的两条 userid 为空 ⇒ 谁都不准入，这是刻意的 fail-closed 出厂态。"""
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "", "name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "   ", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert error_records(caplog)


def test_shipped_config_admits_nobody_until_userids_are_filled_in():
    """⏸ 真实企微 userid 尚未取得，出厂态谁都不准入。

    userid 填进去之后这条会失败——**这是正确的信号**，届时把它改成
    断言两个 userid 均命中，那次改动本身就是"名单已生效"的证据。
    """
    assert load_whitelist(SHIPPED_CONFIG) == frozenset()


def test_entry_with_a_forbidden_field_is_dropped_entirely(tmp_path, caplog):
    """多余字段 ⇒ 整条丢弃，不是"忽略多余字段"。

    同时断言日志只写字段**名**、不写字段**值**——多余字段的值恰恰可能就是
    不该被采集的个人信息，写进日志等于把它换个地方留存。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {
                "userid": "TangLiPing",
                "name": "汤丽萍",
                "role": "HR AI 专员",
                "phone": "13800138000",
            },
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"ShaoPeishen"})
    assert "phone" in caplog.text
    assert "13800138000" not in caplog.text


def test_entry_missing_a_required_field_is_dropped(tmp_path, caplog):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "TangLiPing", "name": "汤丽萍"},
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"ShaoPeishen"})


def test_non_mapping_entry_is_dropped(tmp_path, caplog):
    path = write_roster(
        tmp_path / "whitelist.yaml",
        ["TangLiPing", {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"}],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"ShaoPeishen"})


def test_yaml_python_tags_are_not_constructed(tmp_path):
    """⛔ 绝不用 yaml.load。名单文件受版本管理，但它是"配置"这一类的输入，
    用能构造任意 Python 对象的加载器是无谓的暴露面。"""
    path = tmp_path / "whitelist.yaml"
    path.write_text("members: !!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
    assert load_whitelist(path) == frozenset()


def test_entry_missing_userid_is_dropped(tmp_path, caplog):
    """缺失字段测试此前只覆盖了缺 `role`；缺 `userid` 是另一条独立路径
    （终审 Minor：移动 `entry["userid"]` 到字段校验之前，此前全部 38 条用例仍绿）。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"ShaoPeishen"})


def test_yaml_parse_error_log_does_not_leak_source_text(tmp_path, caplog):
    """解析失败必须报错，但不能把源文本（可能含真实字段值）带进日志。

    PyYAML 的 YAMLError 消息里内嵌了出错行的源码片段（`Mark.get_snippet()`）——
    如果运维往名单里填了手机号，恰好又把文件写坏了，这个片段就会把号码原样
    转印进 ERROR 日志，绕开 `_validated_userid` 里做的全部字段名/值区分。
    """
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n  - userid: 'unterminated string 13800138000\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert "13800138000" not in caplog.text


def test_appending_a_member_takes_effect_without_touching_any_py_file(tmp_path):
    """spec Scenario「追加一名成员」：新成员开始命中，且未修改任何源代码文件。

    ⚠️ 这里用的是虚构的测试标识，⛔ 不要改成聂鑫／王寒月／陈承——
    那三位不入名单是 design.md D2 的结论，测试里出现会误导 reviewer。
    """
    base = [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}]
    path = write_roster(tmp_path / "whitelist.yaml", base)

    fingerprint_before = source_fingerprint()
    assert admit("TestOnlyAppendedMember", path) is False

    write_roster(
        path,
        base
        + [
            {
                "userid": "TestOnlyAppendedMember",
                "name": "测试用追加条目",
                "role": "仅本用例使用，⛔ 不是 D2 名单成员",
            }
        ],
    )

    assert admit("TestOnlyAppendedMember", path) is True
    assert source_fingerprint() == fingerprint_before


def test_admit_rereads_the_file_on_every_call(tmp_path):
    """无缓存不变式：名单文件消失后，上一次命中的人立刻不再命中。

    ⛔ 不要为了性能给 load_whitelist 加缓存——spec 明文禁止
    "放行上一次成功加载的名单"，缓存与 fail-closed 直接冲突。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    assert admit("TangLiPing", path) is True
    path.unlink()
    assert admit("TangLiPing", path) is False


def test_pyyaml_constructor_error_log_does_not_leak_scalar_text(tmp_path, caplog):
    """`yaml.safe_load` 对畸形 `!!int` / `!!bool` 标签抛的是 ValueError / KeyError，
    根本不是 `yaml.YAMLError`——它们绕过 `_read_roster` 里专门加固的
    YAMLError 分支，直接落进 `load_whitelist` 的通用兜底。兜底此前用
    `exc_info=True`，而这些异常的 `str()` 里原样嵌着触发解析的标量文本，
    如果那段文本恰好是手机号，就会被整个转印进 ERROR 日志。
    """
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        'members:\n  - userid: !!int "13800138000abc"\n    name: 汤丽萍\n    role: HR AI 专员\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert "13800138000abc" not in caplog.text


def test_pyyaml_bool_constructor_error_log_does_not_leak_scalar_text(tmp_path, caplog):
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        'members:\n  - userid: !!bool "tangliping@zhuopin.com"\n'
        "    name: 汤丽萍\n    role: HR AI 专员\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert "tangliping@zhuopin.com" not in caplog.text


def test_top_level_key_outside_members_is_rejected(tmp_path, caplog):
    """顶层字段的白名单必须和条目级一样严格——多一个键就整份名单全拒，
    不是"忽略陌生顶层键、照常吃 members"。日志只写键名，不写值。
    """
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        'hr_contact_phone: "138-0013-8000"\n'
        "notes: 汤丽萍 tangliping(at)zhuopin.com\n"
        "members:\n"
        "  - userid: TangLiPing\n"
        "    name: 汤丽萍\n"
        "    role: HR AI 专员\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert error_records(caplog)
    assert "hr_contact_phone" in caplog.text
    assert "138-0013-8000" not in caplog.text
    assert "tangliping(at)zhuopin.com" not in caplog.text


def test_rebinding_default_whitelist_path_takes_effect_through_admit(tmp_path, monkeypatch):
    """`DEFAULT_WHITELIST_PATH` 若被冻在 `def` 时的默认参数里，运行时重新赋值
    这个模块常量对 `admit()`/`load_whitelist()` 就是死信——第 4／5 章接线、
    以及测试用 monkeypatch 换配置文件，全都会悄悄读回出厂文件。
    """
    override_path = write_roster(
        tmp_path / "override.yaml",
        [{"userid": "TestOnlyOverrideMember", "name": "测试用覆盖条目", "role": "仅本用例使用"}],
    )
    monkeypatch.setattr(whitelist_module, "DEFAULT_WHITELIST_PATH", override_path)
    assert admit("TestOnlyOverrideMember") is True


# ─────────────────────────────────────────────────────────────────────────
# TD-15：失败 ERROR 按名单文件内容去重（⛔ 不降级、⛔ 不缓存名单本身）
# ─────────────────────────────────────────────────────────────────────────


def test_same_broken_file_logs_only_one_error_group_across_three_admits(tmp_path, caplog):
    """出厂态本尊：同一份坏名单连调三次 `admit()`，只出**一组** ERROR。

    这是 TD-15 的正题。第 4／5 章每条入站消息调一次 `admit()`，出厂态
    （两条 `userid` 留空）每次必产 3 条 ERROR——不去重就是每条消息刷 3 条，
    运维会学会忽略这个 logger，而模块里真正的合规漏洞恰恰只靠它暴露。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "", "name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "", "name": "邵培申", "role": "工具主人"},
        ],
    )

    with caplog.at_level(logging.ERROR):
        assert admit("TangLiPing", path) is False
        first = len(error_records(caplog))
        assert first, "第一次必须照记 ERROR——去重的是重复副本，不是失败本身"

        assert admit("TangLiPing", path) is False
        assert admit("TangLiPing", path) is False

    assert len(error_records(caplog)) == first, (
        "同一份内容的第 2／3 次失败不应再记 ERROR：" + caplog.text
    )


def test_error_group_covers_every_distinct_failure_before_dedup_kicks_in(tmp_path, caplog):
    """契约「任何失败都记 ERROR」仍成立：**每一个不同的失败态**都被记过。

    出厂态那一组里三条失败（两条 userid 为空 + 一条零条有效条目）
    必须都在第一组里出现过——⛔ 去重不许把同一轮里的兄弟 ERROR 也吞掉。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "", "name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    messages = [r.getMessage() for r in error_records(caplog)]
    assert sum("userid 为空或非字符串" in m for m in messages) == 2, messages
    assert sum("零条有效条目" in m for m in messages) == 1, messages


def test_changed_content_logs_a_second_error_group(tmp_path, caplog):
    """内容一变就重新记一组——去重的键是内容，不是"这个文件报过了"。"""
    path = tmp_path / "whitelist.yaml"
    path.write_text("members:\n  - userid: ''\n    name: 汤丽萍\n    role: HR\n", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
        first = len(error_records(caplog))
        assert load_whitelist(path) == frozenset()
        assert len(error_records(caplog)) == first, "同内容不该重复记"

        # 依然是坏的，但内容变了（多了一条同样空 userid 的成员）
        path.write_text(
            "members:\n  - userid: ''\n    name: 汤丽萍\n    role: HR\n"
            "  - userid: ''\n    name: 邵培申\n    role: 工具主人\n",
            encoding="utf-8",
        )
        assert load_whitelist(path) == frozenset()
        second = len(error_records(caplog))

    assert second > first, "内容变了必须重新记一组 ERROR：" + caplog.text


def test_dedup_does_not_downgrade_the_level(tmp_path, caplog):
    """⛔ 裁决是"去重不降级"：记出来的仍然是 ERROR，不是 WARNING。"""
    path = write_roster(tmp_path / "whitelist.yaml", [{"userid": "", "name": "汤", "role": "HR"}])
    with caplog.at_level(logging.DEBUG):
        assert load_whitelist(path) == frozenset()
    levels = {r.levelno for r in caplog.records if "whitelist" in r.name}
    assert levels == {logging.ERROR}, levels


def test_dedup_caches_only_a_fingerprint_never_the_roster(tmp_path):
    """⛔ 缓存的只能是指纹字符串。名单本身**绝不**缓存（spec 硬要求）。

    两条判据：① 模块级去重状态是 `str | None`，不是集合/名单；
    ② 无缓存不变式仍然成立——先成功加载一次让某人命中，删掉文件后立刻不命中。
    """
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    assert admit("TangLiPing", path) is True
    assert whitelist_module._LAST_LOGGED_FAILURE_FINGERPRINT is None

    path.unlink()
    assert admit("TangLiPing", path) is False
    slot = whitelist_module._LAST_LOGGED_FAILURE_FINGERPRINT
    assert isinstance(slot, str) and len(slot) == 64, slot
    assert "TangLiPing" not in slot


def test_a_clean_load_clears_the_dedup_slot(tmp_path, caplog):
    """修好之后又改坏回同一份内容，必须重新报——否则真故障会被上一轮的指纹吞掉。"""
    path = tmp_path / "whitelist.yaml"
    broken = "members:\n  - userid: ''\n    name: 汤丽萍\n    role: HR\n"
    good = "members:\n  - userid: TangLiPing\n    name: 汤丽萍\n    role: HR\n"

    with caplog.at_level(logging.ERROR):
        path.write_text(broken, encoding="utf-8")
        assert load_whitelist(path) == frozenset()
        first = len(error_records(caplog))
        assert first

        path.write_text(good, encoding="utf-8")
        assert load_whitelist(path) == frozenset({"TangLiPing"})
        assert whitelist_module._LAST_LOGGED_FAILURE_FINGERPRINT is None

        path.write_text(broken, encoding="utf-8")
        assert load_whitelist(path) == frozenset()

    assert len(error_records(caplog)) > first, caplog.text


def test_two_different_files_with_identical_bad_content_both_report(tmp_path, caplog):
    """两个文件恰好写坏成同一份内容，是两个独立现场，⛔ 不许互相吞 ERROR。"""
    content = "members:\n  - userid: ''\n    name: 汤丽萍\n    role: HR\n"
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text(content, encoding="utf-8")
    b.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        assert load_whitelist(a) == frozenset()
        first = len(error_records(caplog))
        assert load_whitelist(b) == frozenset()

    assert len(error_records(caplog)) > first, caplog.text


# ─────────────────────────────────────────────────────────────────────────
# TD-16 ①：YAML 重复键 fail-closed 且点名键名
# ─────────────────────────────────────────────────────────────────────────


def test_duplicate_top_level_members_key_is_rejected_not_last_wins(tmp_path, caplog):
    """运维"再追加一段 `members:`"的现场：PyYAML 原生静默取后者，名单被整份替换。

    ⛔ 不许 last-wins——那个失败**没有症状**（闸门看起来健康，人却换了一批）。
    """
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n  - userid: TangLiPing\n    name: 汤丽萍\n    role: HR AI 专员\n"
        "members:\n  - userid: ShaoPeishen\n    name: 邵培申\n    role: 工具主人\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()

    messages = [r.getMessage() for r in error_records(caplog)]
    assert any("重复键" in m and "members" in m for m in messages), messages


def test_duplicate_userid_key_inside_an_entry_is_rejected(tmp_path, caplog):
    """条目内两个 `userid`：同样 fail-closed，并点名是哪个键。"""
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n  - userid: TangLiPing\n    userid: ShaoPeishen\n"
        "    name: 汤丽萍\n    role: HR AI 专员\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()

    messages = [r.getMessage() for r in error_records(caplog)]
    assert any("重复键" in m and "userid" in m for m in messages), messages


def test_duplicate_key_error_does_not_leak_field_values(tmp_path, caplog):
    """点名的是**键名**，⛔ 不是键值——与 `extra` / `top_level_extra` 两处同口径。"""
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n  - userid: '13800138000'\n    userid: 'tangliping@zhuopin.com'\n"
        "    name: 汤丽萍\n    role: HR AI 专员\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert "13800138000" not in caplog.text
    assert "tangliping@zhuopin.com" not in caplog.text


def test_a_roster_without_duplicate_keys_still_loads(tmp_path, caplog):
    """重复键守卫不许误伤正常名单（两条成员各有自己的 `userid` 不是重复键）。"""
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [
            {"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"},
            {"userid": "ShaoPeishen", "name": "邵培申", "role": "工具主人"},
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset({"TangLiPing", "ShaoPeishen"})
    assert error_records(caplog) == []


# ─────────────────────────────────────────────────────────────────────────
# TD-16 ②：非 UTF-8 单列成一类，不再落"未预期异常"
# ─────────────────────────────────────────────────────────────────────────


def test_non_utf8_file_is_reported_as_an_encoding_problem(tmp_path, caplog):
    """GBK 存盘是运维在 Windows 上最容易犯的错，不该被报成内部异常。"""
    path = tmp_path / "whitelist.yaml"
    path.write_bytes(
        "members:\n  - userid: TangLiPing\n    name: 汤丽萍\n    role: HR AI 专员\n".encode("gbk")
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()

    messages = [r.getMessage() for r in error_records(caplog)]
    assert any("不是 UTF-8 编码" in m for m in messages), messages
    assert not any("未预期异常" in m for m in messages), messages


def test_non_utf8_error_does_not_leak_file_content(tmp_path, caplog):
    """⛔ 只记 encoding／偏移量／reason，不记 `str(exc)`，更不记 `exc.object`。"""
    path = tmp_path / "whitelist.yaml"
    path.write_bytes(
        "members:\n  - userid: '13800138000'\n    name: 汤丽萍\n    role: HR\n".encode("gbk")
    )
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(path) == frozenset()
    assert "13800138000" not in caplog.text


# ─────────────────────────────────────────────────────────────────────────
# TD-16 ③：`path` 传 str 也要走正常分支，不落"未预期异常"
# ─────────────────────────────────────────────────────────────────────────


def test_str_path_loads_like_a_path_object(tmp_path):
    """第 4／5 章调用方传字符串是完全可能的——不该因此永久拒绝所有人。"""
    path = write_roster(
        tmp_path / "whitelist.yaml",
        [{"userid": "TangLiPing", "name": "汤丽萍", "role": "HR AI 专员"}],
    )
    assert load_whitelist(str(path)) == frozenset({"TangLiPing"})
    assert admit("TangLiPing", str(path)) is True


def test_str_path_that_is_missing_reports_unreadable_not_unexpected(tmp_path, caplog):
    """诊断必须落到"文件不可读"这一类，⛔ 不是"未预期异常"。"""
    with caplog.at_level(logging.ERROR):
        assert load_whitelist(str(tmp_path / "absent.yaml")) == frozenset()
    messages = [r.getMessage() for r in error_records(caplog)]
    assert any("不可读" in m for m in messages), messages
    assert not any("未预期异常" in m for m in messages), messages
