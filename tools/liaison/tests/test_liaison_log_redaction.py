"""8.4 · 脱敏内核。正例（打码）与反例（`thread_id` / `msgid` 不许被打码）。

⚠️ 本文件里的"凭据"全是明显的假值。⛔ 不要写成行首赋值形态
（`HR_LIAISON_BOT_SECRET=xxx`）——`test_liaison_no_secrets_in_vcs.py` 会扫受版本
管理的**全部**文件，行首赋值会被判成真凭据入库。
"""

from __future__ import annotations

import io
import logging

import pytest

from tools.liaison import logsetup


def test_redacts_phone_email_idcard_and_credential_values():
    """正例：四种形态一个都不许留明文，但键名要留着（排障得知道是哪一项）。"""
    text = (
        "候选人邮箱 zhang.san@example.com 手机 13812345678 "
        "身份证 320102199001011234 ；启动参数 HR_LIAISON_BOT_SECRET: fake-value-1"
    )
    out = logsetup.compute_redacted_text(text)

    assert "zhang.san@example.com" not in out
    assert "13812345678" not in out
    assert "320102199001011234" not in out
    assert "fake-value-1" not in out

    assert logsetup.EMAIL_MASK in out
    assert logsetup.PHONE_MASK in out
    assert logsetup.IDCARD_MASK in out
    assert logsetup.SECRET_MASK in out
    assert "HR_LIAISON_BOT_SECRET" in out, "键名必须留着，⛔ 不许连键一起打掉"


def test_does_not_redact_msgid_and_thread_id_even_when_they_look_like_personal_data():
    """反例（opener 约束 2 逐字）：⛔ 不脱敏 msgid、thread_id。

    刻意把两个取值都写成手机号形态——这正是天真实现会踩的那颗雷：脱敏正则
    先跑一遍，`thread_id` 变成 `<redacted:phone>`，日志从此和 `liaison_message`
    对不上，而且**不报错**。
    """
    text = "thread_id=13812345678 msgid=13900000000 收到一条消息"
    assert logsetup.compute_redacted_text(text) == text


def test_protects_the_keys_but_still_redacts_the_rest_of_the_same_line():
    """保护段是**段**，不是整行豁免：同一行里键之外的手机号照样要打掉。"""
    text = "thread_id=13812345678 联系方式 13900001111"
    out = logsetup.compute_redacted_text(text)
    assert "thread_id=13812345678" in out
    assert "13900001111" not in out
    assert logsetup.PHONE_MASK in out


def test_protects_quoted_and_empty_key_values():
    """`msgid='...'` 与 `thread_id=`（空值）两种渲染形态都要认；空值情形只能保护到
    分隔符为止，⛔ 不能连着空白之后的下一个 token 一起吃掉——否则那个 token 若
    恰好是手机号/邮箱，就会连同保护段一起明文漏出（round-1 fix-round 修的洞）。
    """
    text = "msgid='13800001111' thread_id= 13900002222 邮件 a.b@example.com"
    out = logsetup.compute_redacted_text(text)
    assert "msgid='13800001111'" in out
    assert "thread_id=" in out
    assert "13900002222" not in out
    assert logsetup.PHONE_MASK in out
    assert "a.b@example.com" not in out


def test_redaction_is_idempotent():
    """跑两遍结果不变——Filter 与 Formatter 两层都会跑，不幂等就会越洗越花。"""
    text = "邮箱 a.b@example.com 手机 13812345678 LLM_API_KEY=fake-value-2"
    once = logsetup.compute_redacted_text(text)
    assert logsetup.compute_redacted_text(once) == once


def test_empty_text_is_returned_unchanged():
    assert logsetup.compute_redacted_text("") == ""


# ---------------------------------------------------------------------------
# Round-1 fix-round：reviewer 用真实调用点渲染形态证伪了三处明文漏出，规则见
# `docs/findings/`（协调者裁决：CLAUDE.md 合规红线优先于 brief 逐字正则，且只许
# 往"更多脱敏"方向纠偏）。以下用例逐条对应裁决消息里的回归输入。
# ---------------------------------------------------------------------------


def test_empty_valued_key_does_not_swallow_the_next_token_across_a_space():
    """Important 1 例 1：`thread_id= ` 后隔一个空格的手机号不许被当成取值吞掉。"""
    text = "thread_id= 13812345678"
    out = logsetup.compute_redacted_text(text)
    assert "thread_id=" in out
    assert "13812345678" not in out
    assert logsetup.PHONE_MASK in out


def test_empty_valued_msgid_does_not_swallow_a_following_email():
    """Important 1 例 2：`msgid= ` 后隔一个空格的邮箱同理。"""
    text = "msgid= zhang.san@example.com"
    out = logsetup.compute_redacted_text(text)
    assert "msgid=" in out
    assert "zhang.san@example.com" not in out
    assert logsetup.EMAIL_MASK in out


def test_empty_valued_msgid_with_colon_does_not_swallow_a_credential():
    """Important 1 例 3：`msgid: ` 后隔一个空格的凭据同理，且凭据本身要按凭据规则打码。"""
    text = "msgid: HR_LIAISON_BOT_SECRET=sekrit"
    out = logsetup.compute_redacted_text(text)
    assert "msgid:" in out
    assert "sekrit" not in out
    assert logsetup.SECRET_MASK in out
    assert "HR_LIAISON_BOT_SECRET" in out


def test_protected_bare_value_stops_at_ampersand_not_swallowing_next_pair():
    """Important 1 例 4：`thread_id=wm001&mobile=...` 里 `&` 之后是另一个键值对，
    ⛔ 不许被当成 thread_id 取值的一部分一起保护起来。"""
    text = "thread_id=wm001&mobile=13812345678"
    out = logsetup.compute_redacted_text(text)
    assert "thread_id=wm001" in out
    assert "13812345678" not in out
    assert logsetup.PHONE_MASK in out


def test_empty_valued_key_followed_by_newline_does_not_protect_next_line():
    """Important 1 附加例：`\\s` 含 `\\n`，`thread_id=` 后换行的下一行同样不许被吞。"""
    text = "thread_id=\n13812345678"
    out = logsetup.compute_redacted_text(text)
    assert "13812345678" not in out
    assert logsetup.PHONE_MASK in out


def test_credential_value_redacted_when_key_is_double_quoted_json_style():
    """Important 2 例 1：`logger.debug("config=%r", cfg)` 打印出来的 JSON 渲染。"""
    text = '{"LLM_API_KEY": "sk-abc123"}'
    out = logsetup.compute_redacted_text(text)
    assert "sk-abc123" not in out
    assert logsetup.SECRET_MASK in out
    assert "LLM_API_KEY" in out


def test_credential_value_redacted_when_key_is_single_quoted_dict_repr():
    """Important 2 例 2：Python dict repr 渲染，键值都带单引号。"""
    text = "config={'HR_LIAISON_BOT_SECRET': 'super-sekrit'}"
    out = logsetup.compute_redacted_text(text)
    assert "super-sekrit" not in out
    assert logsetup.SECRET_MASK in out
    assert "HR_LIAISON_BOT_SECRET" in out


def test_credential_value_redacted_with_arrow_rendering():
    """Important 2 例 3：人工排障口语化的 `->` 渲染，不是 `=`/`:`。"""
    text = "os.environ HR_LIAISON_BOT_SECRET -> super-sekrit"
    out = logsetup.compute_redacted_text(text)
    assert "super-sekrit" not in out
    assert logsetup.SECRET_MASK in out
    assert "HR_LIAISON_BOT_SECRET" in out


def test_mobile_with_plus86_prefix_is_masked_with_and_without_separator():
    """Important 4 例 1：`+86 ` 带空格与 `+86` 无分隔符连写两种渲染都要打码。"""
    text = "请联系 +86 13812345678 或 +8613812345678"
    out = logsetup.compute_redacted_text(text)
    assert "13812345678" not in out
    assert out.count(logsetup.PHONE_MASK) == 2


def test_mobile_with_0086_prefix_is_masked():
    """Important 4 例 2：`0086` 前缀连写（`tel:008613812345678`）。"""
    text = "tel:008613812345678"
    out = logsetup.compute_redacted_text(text)
    assert "13812345678" not in out
    assert logsetup.PHONE_MASK in out


def test_epoch_millis_and_long_digit_runs_still_not_masked():
    """Important 4 护栏回归：修 +86 前缀不能连带误伤时间戳/字节数——13 位毫秒级
    时间戳与 15 位连续数字串，两者都不含手机号/身份证形态，必须原样保留。"""
    text = "ts=1694209999123 size=123456789012345 bytes"
    assert logsetup.compute_redacted_text(text) == text


def test_sender_userid_is_not_a_protected_key_and_still_gets_masked():
    """`sender_userid` 明确不在 `PROTECTED_KEYS` 里，取值该打码照样打码。"""
    text = "sender_userid=13812345678"
    out = logsetup.compute_redacted_text(text)
    assert "13812345678" not in out
    assert logsetup.PHONE_MASK in out


def test_parent_thread_id_does_not_match_the_protected_thread_id_key():
    """`parent_thread_id` 里的 `thread_id` 前面紧贴着 `_`（词字符），`\\b` 不成立，
    不会被误当成受保护的 `thread_id` 键，取值该打码照样打码。"""
    text = "parent_thread_id=13812345678"
    out = logsetup.compute_redacted_text(text)
    assert "13812345678" not in out
    assert logsetup.PHONE_MASK in out


@pytest.fixture
def wired_logger(tmp_path):
    """把包级 logger 按生产方式装配好，返回日志文件路径。

    ⚠️ 显式钉死 `level="DEBUG"`：不钉的话这几条用例的通过与否会跟着宿主环境的
    `HR_LIAISON_LOG_LEVEL` 漂——比如本机 CI 若导出了 `HR_LIAISON_LOG_LEVEL=ERROR`，
    这里用的 WARNING/INFO 记录会被过滤掉，测试红得毫无线索。
    """
    logsetup.setup_logging(log_dir=tmp_path, level="DEBUG")
    yield tmp_path / logsetup.LOG_FILENAME
    logsetup.teardown_logging()


def test_child_logger_records_are_redacted_too(wired_logger):
    """🔴 证伪「Filter 只挂包级 logger 就够了」——本服务真正打日志的全是子 logger。

    `Logger.handle()` 只对**发起记录的那个 logger** 跑 filter；子 logger 的记录
    沿祖先链找 **handler**，⛔ 不会再跑祖先 logger 的 filter。本服务每个模块都是
    `logging.getLogger(__name__)`，所以只挂包级 logger 等于对真正会打印个人信息
    的那些行完全失明，而且**不报错、无症状**。
    把 handler 上那层 filter 摘掉，这条必红。
    """
    logging.getLogger("tools.liaison.inbound").warning("候选人手机 13812345678")
    text = wired_logger.read_text(encoding="utf-8")
    assert "13812345678" not in text
    assert logsetup.PHONE_MASK in text


def test_exception_tracebacks_are_redacted(wired_logger):
    """Filter 看不到 traceback：抛异常那一行的**源码原文**也会被写进日志。"""
    logger = logging.getLogger("tools.liaison.archive")
    try:
        raise ValueError("联系 zhang.san@example.com 核对")
    except ValueError:
        logger.error("归档失败", exc_info=True)
    text = wired_logger.read_text(encoding="utf-8")
    assert "zhang.san@example.com" not in text
    assert logsetup.EMAIL_MASK in text


def test_thread_id_survives_the_whole_handler_chain(wired_logger):
    """端到端反例：走完 Filter + Formatter 两层，`thread_id` 仍是明文。"""
    logging.getLogger("tools.liaison.inbound").info(
        "已归档 thread_id=%s msgid=%s", "13812345678", "MSG-0001"
    )
    text = wired_logger.read_text(encoding="utf-8")
    assert "thread_id=13812345678" in text
    assert "msgid=MSG-0001" in text


def test_filter_neutralises_a_broken_format_string_instead_of_leaking_it(wired_logger):
    """占位符与 args 对不上时 ⛔ 不许把原文交给 stdlib 的 handleError()。"""
    logging.getLogger("tools.liaison.inbound").warning(
        "手机 %s 邮箱 %s", "13812345678"
    )
    text = wired_logger.read_text(encoding="utf-8")
    assert "13812345678" not in text
    assert "[liaison-redaction] 日志格式化失败" in text


# ---------------------------------------------------------------------------
# Fix round 1（review 三条 Important，均落在计划逐字转写的代码里，非实现者错误；
# 裁决：CLAUDE.md 合规红线与函数自身承诺的契约优先于计划逐字文本，且只许往
# 更安全的方向纠偏——降级不崩溃、脱敏更多不更少）。
# ---------------------------------------------------------------------------


def test_bad_log_level_falls_back_instead_of_crashing(tmp_path):
    """Important 1：手抖/数字/非法级别一律不许在装配日志之前把进程打死。

    `resolved_level` 曾经只 `.upper()` 不校验就直接扔给 `logger.setLevel()`——
    跟 `_env_int`「取不到、非数字、越界一律回落默认值，⛔ 不抛异常」的承诺不对称。
    `setup_logging()` 是 `main()` 的第一句，此时还没有任何 handler，一条未捕获
    异常就是 launchd 崩溃循环，连 stderr 都没有落地机会——直接违反本函数自己的
    docstring 契约（不可写时 ⛔ 不崩溃、⛔ 不阻断业务功能）。
    """
    logger = logging.getLogger(logsetup.PACKAGE_LOGGER_NAME)
    default_value = logging.getLevelNamesMapping()[logsetup.DEFAULT_LEVEL]
    try:
        for raw in ("INFO ", "INF0", "verbose"):
            status = logsetup.setup_logging(log_dir=tmp_path, level=raw)
            assert status.configured is True
            assert (
                logger.level == default_value
            ), f"level={raw!r} 未回落到 DEFAULT_LEVEL：实际 {logger.level}"

        # `'20'` 与数字级别的其它合法取值：能落到一个真实存在的级别就该被接受，
        # ⛔ 不是见到数字就一律回落默认值（DEFAULT_LEVEL 恰好也是 INFO=20，
        # 用 ERROR=40 才能真的证明是"数字被解析"而不是"数字被拒绝后走了默认值"）。
        status = logsetup.setup_logging(log_dir=tmp_path, level="20")
        assert logger.level == logging.INFO
        status = logsetup.setup_logging(log_dir=tmp_path, level="40")
        assert logger.level == logging.ERROR
    finally:
        logsetup.teardown_logging()


def test_teardown_resets_the_logger_level_to_notset(tmp_path):
    """Important 2：`teardown_logging` 不摘 `logger.level` 就是留了一个进程范围的
    定时器——下一条用例的记录会在 `Logger.isEnabledFor()` 这一步被静默过滤掉。
    `caplog.at_level(...)` 只抬高 root 的级别，抬不动这个包级 logger 自己钉死的
    级别（控制组见 `test_session_liveness.py::test_reading_a_corrupt_stamp_returns_none_and_logs`
    在 `HR_LIAISON_LOG_LEVEL=ERROR` 环境下先跑一遍脱敏套件的失败）。
    """
    logger = logging.getLogger(logsetup.PACKAGE_LOGGER_NAME)
    logsetup.setup_logging(log_dir=tmp_path, level="ERROR")
    assert logger.level == logging.ERROR
    logsetup.teardown_logging()
    assert logger.level == logging.NOTSET


def test_traceback_redacted_before_propagating_to_a_plain_root_formatter(wired_logger):
    """Important 3：`RedactingFormatter` 原先只对 `format()` 的**返回值**做替换，
    不回写 `record.exc_text` 缓存；`setup_logging` 的 `propagate=True` 是刻意保留
    的（见其 docstring），意味着这条 record 还会继续往 root 走。root 上任何裸
    `logging.Formatter`（今天仓库里没有，但 `logging.basicConfig()` 或一次调试
    用的 `StreamHandler` 都会造出一个）读到的是**未脱敏**的原始缓存——这正是
    "结构上不可能明文外泄"这条合规红线的失效点。修复后 `formatException()` 在
    源头就把缓存写成脱敏文本，root 收到的也是安全的。
    """
    root = logging.getLogger()
    leak_stream = io.StringIO()
    leak_handler = logging.StreamHandler(leak_stream)
    leak_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(leak_handler)
    try:
        logger = logging.getLogger("tools.liaison.archive")
        try:
            raise ValueError("联系 zhang.san@example.com 手机 13812345678")
        except ValueError:
            logger.error("归档失败", exc_info=True)
    finally:
        root.removeHandler(leak_handler)

    leaked = leak_stream.getvalue()
    assert "zhang.san@example.com" not in leaked, "邮箱明文泄露到了 root 的裸 formatter"
    assert "13812345678" not in leaked, "手机号明文泄露到了 root 的裸 formatter"

    own_text = wired_logger.read_text(encoding="utf-8")
    assert "zhang.san@example.com" not in own_text
    assert "13812345678" not in own_text
