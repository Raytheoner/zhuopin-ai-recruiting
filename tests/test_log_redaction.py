import io
import json
import logging

import pytest

from app.observability.logging_config import setup_logging
from app.observability.redaction import (
    NON_CONTENT_KEYS,
    REDACTED,
    _RISKY_VALUE_RE,
    loggable_summary,
)
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
    # must_have_skills 是 list 类型的受控字段——review finding 4：旧正则只吃
    # 引号包起来的标量值，list 形态的 repr（['AUTOSAR', 'CAN FD']）完全漏网。
    assert "AUTOSAR" not in text, "list 类型的受控字段（must_have_skills）未被脱敏"
    assert "CAN FD" not in text
    assert "must_have_skills" in text, "键名应保留，否则日志失去排障价值"


def test_bypass_is_visible_not_silently_fixed(log_file):
    logging.getLogger("app.web.server").info("profile=%s", PROFILE)
    text = _read(log_file)
    assert "脱敏兜底命中" in text, "兜底替换必须额外留一条告警，使绕过行为可见"
    assert "loggable_summary" in text, "告警要指出正确做法"


def test_bypass_warning_fires_once_per_event(log_file):
    """review finding 2：同一条 record 会被 console/file 两个 handler 各 filter
    一遍，第二遍如果把第一遍留下的 '<redacted>' 又当成新命中，会把一次绕过
    误记成两次事故，还多写一倍的告警行。这里 setup_logging 已经同时挂了
    console 与 file 两个 handler（tmp_path 可写），是这个 bug 的真实触发条件。
    """
    logging.getLogger("app.web.server").info("profile=%s", PROFILE)
    text = _read(log_file)
    assert text.count("脱敏兜底命中") == 1, "同一次绕过事件不应该被记成多条告警"


def test_malformed_format_call_leaves_no_plaintext_anywhere(log_file, monkeypatch):
    """review finding 1：record.getMessage() 因 %-占位符与 args 数量不匹配而
    抛异常时，绝不能把未脱敏的 record 原样放行——那会在 emit() 里再次抛出
    同一个异常，被 stdlib Handler.handleError() 接住，把 record.msg /
    record.args 原文写进 sys.stderr，绕开 Filter 和 Formatter 两层防线。
    """
    fake_stderr = io.StringIO()
    monkeypatch.setattr("sys.stderr", fake_stderr)

    payload = {"job_title": "绝密岗位ZZZ", "responsibilities": "绝密职责YYY"}
    # 只给了一个位置参数，但 msg 里有两个占位符 %s / %d —— record.getMessage()
    # 会抛 TypeError。
    logging.getLogger("app.web.server").info("profile=%s round=%d", payload)

    text = _read(log_file)
    stderr_text = fake_stderr.getvalue()

    assert "绝密岗位ZZZ" not in text
    assert "绝密职责YYY" not in text
    assert "绝密岗位ZZZ" not in stderr_text, "格式化失败时，原始负载经 stderr 泄漏了"
    assert "绝密职责YYY" not in stderr_text


def test_exception_stack_carrying_content_is_redacted(log_file):
    """异常信息不经过 record.getMessage()，Filter 看不到——由 Formatter 补刀。"""
    try:
        raise ValueError(f"validation failed for {json.dumps(PROFILE, ensure_ascii=False)}")
    except ValueError:
        logging.getLogger("app.web.server").exception("confirm 失败")

    text = _read(log_file)
    assert SECRET not in text, "受控内容经异常信息泄漏了"
    assert "<redacted>" in text
    # 同 review finding 4 的盲区：异常栈里 json.dumps 出来的 must_have_skills
    # 也是 list（JSON array）形态，同样必须被吃掉。
    assert "AUTOSAR" not in text, "异常堆栈里 list 类型的受控字段未被脱敏"
    assert "CAN FD" not in text


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


def test_risky_keys_cover_real_job_profile_schema_fields():
    """review finding 5：RISKY_KEYS 曾经是按臆想的业务对象猜的键名，跟
    app/schemas/job_profile.py::JobProfile 的真实字段对不上，等于形同虚设。
    这里直接对着真实字段名验证正则命中。
    """
    text = (
        "{'education_requirement': '清华硕士', "
        "'project_experience_requirement': '某车企机密项目'}"
    )
    redacted, count = _RISKY_VALUE_RE.subn(r"\1'" + REDACTED + "'", text)

    assert count == 2
    assert "清华硕士" not in redacted
    assert "某车企机密项目" not in redacted


def test_risky_keys_cover_profile_patch_accumulated_graph_state_key():
    """真实的 graph state 键（app/graph/state.py）是 profile_patch_accumulated，
    而不是 profile_patch——旧的键名要求精确闭合引号，一个字符都不能多，
    所以完全命中不到。这里用它在代码里实际出现的序列化形态（json.dumps 出的
    带引号字符串）验证。
    """
    text = '{"profile_patch_accumulated": "{\\"job_title\\": \\"\\u7edd\\u5bc6\\u5c97\\u4f4d\\"}"}'
    redacted, count = _RISKY_VALUE_RE.subn(r"\1'" + REDACTED + "'", text)

    assert count == 1
    assert "job_title" not in redacted
