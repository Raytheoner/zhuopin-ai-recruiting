import io
import json
import logging

import pytest
from pydantic import ValidationError

from app.observability.logging_config import setup_logging
from app.observability.redaction import (
    NON_CONTENT_KEYS,
    REDACTED,
    _RISKY_VALUE_RE,
    content_digest,
    loggable_summary,
)
from app.schemas.job_profile import JobProfile, SkillItem, SopProject, AutosarLayer

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


# --- final review finding 1：pydantic 模型 repr 与 ValidationError 回显 ---

_JOB_PROFILE_SECRETS = {
    "job_title": "嵌入式软件工程师（机密岗位XYZ）",
    "department": "电子电气研发部机密小组",
    "education_requirement": "985高校硕士机密要求",
    "project_experience_requirement": "某车企机密量产项目",
    "vehicle_model": "机密车型代号Q9",
}


def _build_real_job_profile() -> JobProfile:
    return JobProfile(
        job_title=_JOB_PROFILE_SECRETS["job_title"],
        department=_JOB_PROFILE_SECRETS["department"],
        headcount=2,
        education_requirement=_JOB_PROFILE_SECRETS["education_requirement"],
        experience_years="3-5年",
        core_skills=[SkillItem(name="AUTOSAR-CP机密技能", required=True)],
        project_experience_requirement=_JOB_PROFILE_SECRETS["project_experience_requirement"],
        soft_skill_keywords=["沟通-机密备注"],
        autosar_experience=[AutosarLayer.CP],
        mcu_family=["Infineon机密选型TC3xx"],
        diag_stack=["UDS机密诊断栈"],
        sop_projects=[
            SopProject(
                vehicle_model=_JOB_PROFILE_SECRETS["vehicle_model"],
                role="项目负责人机密职级",
                is_mass_production=True,
            )
        ],
        toolchain=["Vector CANoe机密授权号"],
    )


def test_pydantic_model_repr_bypass_is_redacted_and_visible(log_file):
    """finding 1：`_RISKY_VALUE_RE` 原来只认 'key': 'value' 这种 JSON/dict
    形态，pydantic 模型 repr 是 key=value（无引号键、= 号），完全漏网——
    用 %r 打一个真实 JobProfile，13 个内容字段会明文落盘且不触发兜底告警，
    是「静默泄漏」。这里验证 repr() 之后所有内容字段（含 list 型字段）都
    不再以明文出现，且兜底告警只触发一次。
    """
    profile = _build_real_job_profile()
    logging.getLogger("app.web.server").info("profile repr=%r", profile)
    text = _read(log_file)

    for secret in _JOB_PROFILE_SECRETS.values():
        assert secret not in text, f"pydantic repr 泄漏了字段内容：{secret!r}"
    assert "AUTOSAR-CP机密技能" not in text, "list 型字段（core_skills）未被脱敏"
    assert "机密备注" not in text, "list 型字段（soft_skill_keywords）未被脱敏"
    assert "Infineon机密选型TC3xx" not in text, "list 型字段（mcu_family）未被脱敏"
    assert "UDS机密诊断栈" not in text, "list 型字段（diag_stack）未被脱敏"
    assert "项目负责人机密职级" not in text, "嵌套对象字段（sop_projects.role）未被脱敏"
    assert "Vector CANoe机密授权号" not in text, "list 型字段（toolchain）未被脱敏"

    assert "<redacted>" in text
    assert text.count("脱敏兜底命中") == 1, "同一次绕过事件不应被记成多条告警"
    assert "job_title" in text, "键名应保留，否则日志失去排障价值"


def test_validation_error_echo_is_redacted(log_file):
    """finding 1：`RedactingFormatter` 的 docstring 早就点名 ValidationError
    回显是它存在的理由，但正则一直没实现这个分支。pydantic 把被拒绝的原始
    输入回显为 `input_value=<值>`，用真实 JobProfile 触发一次校验失败并经
    `logger.exception` 记录，验证回显值不再明文落盘。
    """
    secret_id = "候选人身份证机密110101199001011234"
    # 用变量而不是字面量传参，模拟真实调用点——traceback 的源码行只会显示
    # 变量名（如 **payload），不会显示运行时取值；真正的回显泄漏点是
    # ValidationError 自身 str() 里的 input_value=，不是源码行。
    payload = dict(
        job_title="正常标题",
        department="部门X",
        headcount=secret_id,  # 类型错误：headcount 需要 int
        education_requirement="本科",
        experience_years="3-5年",
    )
    try:
        JobProfile(**payload)
    except ValidationError:
        logging.getLogger("app.web.server").exception("confirm 失败")

    text = _read(log_file)
    assert secret_id not in text, "ValidationError 的 input_value= 回显泄漏了原始输入"
    assert "<redacted>" in text


def test_risky_value_regex_timing_on_pathological_input():
    """量化护栏：新增分支不能让最坏情况的耗时明显劣化于原 list 分支
    （0.31s / 340k 字符）。这里用几种典型对抗输入（未闭合引号、未闭合列表、
    裸值兜底分支的超长无分隔符输入）各测一次，全部应远低于该预算。
    """
    import time

    cases = [
        "job_title=" + "'" * 340000,  # 未闭合引号，逼引擎多次尝试收口
        "core_skills=[" + "a" * 340000,  # pydantic-repr 形态的未闭合列表
        "input_value=" + "a" * 340000,  # 裸值兜底分支的超长无分隔符输入
    ]
    for text in cases:
        start = time.time()
        _RISKY_VALUE_RE.subn(r"\1'" + REDACTED + "'", text)
        elapsed = time.time() - start
        assert elapsed < 2.0, f"正则在对抗输入上耗时 {elapsed:.3f}s，明显劣化于 0.31s 预算"


# --- final review finding 3：content_digest 拒绝低熵标量输入 ---


def test_content_digest_rejects_scalar_input():
    """finding 3：无盐 SHA-256 截断对单个低熵标量（手机号/身份证号/姓名）
    可被暴力穷举还原（已验证：11 位手机号 1 秒内还原）。原来只有文档警告，
    现在升级成运行时强制校验——单值输入必须被拒绝，而不是被"正确地"摘要
    成一个看似安全实则可逆的哈希。
    """
    with pytest.raises(TypeError):
        content_digest("13800138000")  # 不是 Mapping

    with pytest.raises(ValueError):
        content_digest({"phone": "13800138000"})  # Mapping 但只有 1 个条目

    with pytest.raises(ValueError):
        content_digest({})  # 空 Mapping 同样不足以抵抗穷举

    # >= 2 个条目仍然正常工作（不能把正常路径也堵死）。
    digest = content_digest({"a": "13800138000", "b": "13900139000"})
    assert len(digest) == 16


def test_loggable_summary_handles_low_content_field_count_without_crashing():
    """content_digest 现在会对 <2 个内容字段抛异常，但 loggable_summary 是
    日志路径的一部分——不能让一次记录动作因为业务对象恰好只有 0/1 个内容
    字段就抛异常中断日志本身。这里验证 0 个和 1 个内容字段时都不崩溃，且
    content_digest 字段被置 None 而不是伪造一个不安全的摘要。
    """
    only_non_content = {"job_id": "J-1", "version": 1}  # 0 个内容字段
    summary0 = loggable_summary(only_non_content)
    assert summary0["content_digest"] is None
    assert summary0["field_count"] == 2

    single_content = {"job_id": "J-1", "job_title": "唯一内容字段"}  # 1 个内容字段
    summary1 = loggable_summary(single_content)
    assert summary1["content_digest"] is None
    assert "唯一内容字段" not in json.dumps(summary1, ensure_ascii=False)
