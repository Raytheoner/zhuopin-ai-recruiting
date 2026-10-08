"""渠道来源识别（channel-resume-intake U1 tasks 1.2）。

纯函数：只读文件名与首页文本，产出 Source 枚举或 None，⛔ 不写库、不调模型、
不提取平台水印之外字段。规则表是占位版（文件名正则），首页关键词在人事部#3
脱敏样例到位后（U4）补齐。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from enum import Enum


class Source(str, Enum):
    BOSS = "boss"
    LIEPIN = "liepin"
    JOB_51 = "51job"
    ZHAOPIN = "zhaopin"
    REFERRAL = "referral"
    OTHER = "other"
    UNKNOWN = "unknown"


# HR 可指定的来源值域（默认来源下拉与改正接口共用）。"unknown" 是系统回退值，
# ⛔ 不在 HR 可选值里——HR 指定 "unknown" 与"不指定"语义相同。
SOURCE_VALUES = ("boss", "liepin", "51job", "zhaopin", "referral", "other")


@dataclass(frozen=True)
class SourceRule:
    source: Source
    filename_patterns: tuple[re.Pattern[str], ...]
    first_page_keywords: tuple[str, ...]


# 占位规则（design.md 风险表：各平台导出格式无样例，规则空转）。文件名正则按
# 公开可见的导出命名模式起步，U4 拿到脱敏样例后迭代并补夹具与首页关键词。
SOURCE_RULES: tuple[SourceRule, ...] = (
    SourceRule(
        Source.BOSS,
        (re.compile(r"boss", re.IGNORECASE), re.compile(r"boss直聘", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.LIEPIN,
        (re.compile(r"liepin", re.IGNORECASE), re.compile(r"猎聘", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.JOB_51,
        (re.compile(r"51job", re.IGNORECASE), re.compile(r"前程无忧", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.ZHAOPIN,
        (re.compile(r"zhaopin", re.IGNORECASE), re.compile(r"智联", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.REFERRAL,
        (re.compile(r"referral", re.IGNORECASE), re.compile(r"内推", re.IGNORECASE)),
        (),
    ),
)


def detect_source(filename: str, first_page_text: str) -> Source | None:
    """确定性来源识别。文件名规则命中返回对应 Source；否则看首页关键词（占位
    为空）；都未命中返回 None——由调用方落到 default_source 或 unknown。
    ⛔ 只返回可识别的平台/内推值，绝不返回 OTHER/UNKNOWN（那两个由人/调用方赋值）。"""
    for rule in SOURCE_RULES:
        if any(p.search(filename) for p in rule.filename_patterns):
            return rule.source
    text = first_page_text or ""
    for rule in SOURCE_RULES:
        if rule.first_page_keywords and any(k in text for k in rule.first_page_keywords):
            return rule.source
    return None


def first_page_text(data: bytes, suffix: str) -> str:
    """从内存字节取首页文本，供 detect_source 的第二参。U1 占位规则不读它，
    但签名与数据流在 U4 补首页关键词后不再改动。任何解析失败都返回空串（宁可
    识别不出，不让一个坏文件拖垮整包）。"""
    suffix = suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if not reader.pages:
                return ""
            return reader.pages[0].extract_text() or ""
        if suffix == ".docx":
            import docx
            document = docx.Document(io.BytesIO(data))
            for p in document.paragraphs:
                if p.text.strip():
                    return p.text
            return ""
    except Exception:
        return ""
    return ""
