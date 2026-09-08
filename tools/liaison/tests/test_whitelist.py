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

    def explode(_raw):
        raise RecursionError("PyYAML 在畸形输入上可能抛出非 YAMLError")

    monkeypatch.setattr(whitelist_module.yaml, "safe_load", explode)
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
