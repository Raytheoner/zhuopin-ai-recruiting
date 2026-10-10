"""手机号提取与哈希（channel-resume-intake U2 tasks 2.1）。

纯函数：只读 spans 文本，产出一个 SHA-256 哈希字符串或 None，⛔ 不写库、不调
模型。明文手机号绝不离开本模块——返回值是哈希，任何中间变量都不保留明文。
M2 只裁决「哈希存储、明文不落库」，磁盘上未实现过 phone 哈希；本模块是全仓
第一个也是唯一一个实现（与 content_sha256 / bundle_sha256 同款 hexdigest 口径）。
"""
from __future__ import annotations

import hashlib
import re

from app.parsing.spans import TextSpan

# 中国大陆手机号：1 开头、第二位 3-9、共 11 位，前后不能是数字。
_CN_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def hash_phone(phone: str) -> str:
    """M2 既定口径的落地实现：手机号只用于去重、以 SHA-256 哈希存储、明文不落库。"""
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def extract_phone_hash(text_spans: list[TextSpan]) -> str | None:
    """按 span 顺序取第一个中国大陆手机号，立即哈希后返回。明文绝不出本函数。"""
    for span in text_spans:
        match = _CN_MOBILE.search(span.text)
        if match:
            return hash_phone(match.group(0))
    return None
