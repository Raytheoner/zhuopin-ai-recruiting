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
