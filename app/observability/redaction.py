from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from typing import Any, Mapping

REDACTED = "<redacted>"

# 白名单：只有这些键是「非内容」字段，可以原样进日志。白名单之外一律不输出
# ——对应 spec「新增字段的默认归属」：未声明即受控。
NON_CONTENT_KEYS = frozenset({"job_id", "thread_id", "version", "round_count", "status"})

# 兜底 Filter 的高危键名。这层是探测性的、不是主防线：正则永远追不上业务
# 字段的增长速度。它的价值是「当有人绕过 loggable_summary 时留下痕迹」。
RISKY_KEYS = (
    "job_title",
    "department",
    "responsibilities",
    "requirements",
    "must_have_skills",
    "nice_to_have_skills",
    "profile_json",
    "profile_patch",
    "history_json",
    "resume_text",
    "candidate_name",
    "content",
    "message",
    "_jd_text",
)

_KEY_ALTERNATION = "|".join(re.escape(k) for k in RISKY_KEYS)
# 匹配 dict/JSON 两种渲染形态里的 "键: '值'" 或 "键": "值"，只吃掉值。
_RISKY_VALUE_RE = re.compile(
    r"(['\"](?:" + _KEY_ALTERNATION + r")['\"]\s*:\s*)"
    r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")"
)

_local = threading.local()


def content_digest(value: Any) -> str:
    """非还原性摘要：短哈希。用于「同一段内容是否变过」的排障，不可逆推原文。"""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def loggable_summary(obj: Mapping[str, Any], *, known_fields: frozenset[str] | None = None) -> dict:
    """主防线：把业务对象压成只含非内容字段的摘要。

    白名单外的键**只贡献名字与统计量，不贡献取值**；且键名本身也要过一遍
    known_fields（画像 patch 是 LLM 自由生成的裸 dict，键名理论上也可能是
    模型幻觉出来的自由文本）。
    """
    summary: dict[str, Any] = {k: obj[k] for k in NON_CONTENT_KEYS if k in obj}
    content_keys = [k for k in obj if k not in NON_CONTENT_KEYS]
    summary["field_count"] = len(obj)
    if known_fields is not None:
        summary["field_names"] = sorted(k for k in content_keys if k in known_fields)
        summary["unknown_field_count"] = sum(1 for k in content_keys if k not in known_fields)
    else:
        summary["unknown_field_count"] = len(content_keys)
    summary["content_chars"] = sum(len(str(obj[k])) for k in content_keys)
    summary["content_digest"] = content_digest({k: obj[k] for k in content_keys})
    return summary


class RedactionFilter(logging.Filter):
    """兜底层：扫描最终 record，命中高危键名时把值替换成非还原形式。

    命中即额外记一条 WARNING，使「主防线被绕过」这件事可见而不是被悄悄修正。
    _local.busy 是重入护栏：那条 WARNING 自己也会流经本 Filter。
    """

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self.hits = 0

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True

        redacted, count = _RISKY_VALUE_RE.subn(r"\1'" + REDACTED + "'", rendered)
        if count == 0:
            return True

        record.msg = redacted
        record.args = ()
        record.redacted_fields = count
        self.hits += count

        if not getattr(_local, "busy", False):
            _local.busy = True
            try:
                logging.getLogger("app.observability.redaction").warning(
                    "脱敏兜底命中 %d 处：logger=%s 位置=%s:%s。"
                    "主防线被绕过了——业务对象不应整体进日志，请改用 loggable_summary()",
                    count,
                    record.name,
                    record.pathname,
                    record.lineno,
                )
            finally:
                _local.busy = False
        return True


class RedactingFormatter(logging.Formatter):
    """异常堆栈不经过 record.getMessage()，Filter 看不到它。

    堆栈里会出现局部变量的 repr（例如 `profile_dict = {...}` 的那一帧），
    以及 `ValidationError` 把原始输入回显进 str(exc) 的情况——所以格式化
    之后再扫一遍最终文本，是 Filter 之外必须补的一刀。
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return _RISKY_VALUE_RE.sub(r"\1'" + REDACTED + "'", text)
