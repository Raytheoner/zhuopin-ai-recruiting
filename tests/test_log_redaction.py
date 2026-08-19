import json
import logging

import pytest

from app.observability.logging_config import setup_logging
from app.observability.redaction import NON_CONTENT_KEYS, loggable_summary
from app.schemas.job_profile import JobProfile

SECRET = "负责 AUTOSAR CP 底层通信栈开发，需精通 CAN FD 与 UDS 诊断"
PROFILE = {
    "job_id": "J-REDACT",
    "version": 3,
    "job_title": "嵌入式软件工程师（机密岗位名）",
    "department": "电子电气研发部",
    "responsibilities": SECRET,
    "must_have_skills": ["AUTOSAR", "CAN FD"],
}


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs"
    setup_logging(log_dir=str(path), level="DEBUG", retention_days=30)
    yield path / "app.log"
    logging.shutdown()


def _read(log_file):
    logging.shutdown()
    return log_file.read_text(encoding="utf-8")


def test_whole_object_logged_leaves_no_plaintext(log_file):
    """主防线被绕过时的报警器：有人把画像对象整体塞进日志。"""
    logging.getLogger("app.web.server").info("profile=%s", PROFILE)
    text = _read(log_file)

    assert SECRET not in text, "受控内容字段以明文进了日志"
    assert "机密岗位名" not in text
    assert "<redacted>" in text
    assert "J-REDACT" in text, "非内容字段应保留，否则日志失去排障价值"


def test_bypass_is_visible_not_silently_fixed(log_file):
    logging.getLogger("app.web.server").info("profile=%s", PROFILE)
    text = _read(log_file)
    assert "脱敏兜底命中" in text, "兜底替换必须额外留一条告警，使绕过行为可见"
    assert "loggable_summary" in text, "告警要指出正确做法"


def test_exception_stack_carrying_content_is_redacted(log_file):
    """异常信息不经过 record.getMessage()，Filter 看不到——由 Formatter 补刀。"""
    try:
        raise ValueError(f"validation failed for {json.dumps(PROFILE, ensure_ascii=False)}")
    except ValueError:
        logging.getLogger("app.web.server").exception("confirm 失败")

    text = _read(log_file)
    assert SECRET not in text, "受控内容经异常信息泄漏了"
    assert "<redacted>" in text


def test_loggable_summary_emits_no_content_values():
    summary = loggable_summary(PROFILE, known_fields=frozenset(JobProfile.model_fields))
    rendered = json.dumps(summary, ensure_ascii=False)

    assert SECRET not in rendered
    assert "机密岗位名" not in rendered
    assert summary["job_id"] == "J-REDACT"
    assert summary["version"] == 3
    assert summary["field_count"] == len(PROFILE)
    assert summary["content_chars"] > 0
    assert len(summary["content_digest"]) == 16


def test_newly_added_undeclared_field_defaults_to_controlled():
    """新增字段未显式声明是否受控时，默认必须倾向于不泄露。"""
    leaked = "候选人张某某的手机号 13800138000"
    obj = {**PROFILE, "some_brand_new_field": leaked}

    summary = loggable_summary(obj, known_fields=frozenset(JobProfile.model_fields))
    rendered = json.dumps(summary, ensure_ascii=False)

    assert leaked not in rendered, "未声明的新字段以明文进了摘要"
    assert "some_brand_new_field" not in summary.get("field_names", []), (
        "未知字段名不应进白名单"
    )
    assert summary["unknown_field_count"] >= 1, "未知字段应被计数，使新增可被察觉"


def test_non_content_whitelist_holds_no_free_text_keys():
    """白名单只能放结构性标识，任何自由文本字段混进来都是回归。"""
    assert NON_CONTENT_KEYS == {"job_id", "thread_id", "version", "round_count", "status"}
