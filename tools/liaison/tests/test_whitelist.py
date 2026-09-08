"""准入名单（tasks.md 第 3 章）的行为契约。"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
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
