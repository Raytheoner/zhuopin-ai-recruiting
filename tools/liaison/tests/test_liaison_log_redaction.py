"""8.4 · 脱敏内核。正例（打码）与反例（`thread_id` / `msgid` 不许被打码）。

⚠️ 本文件里的"凭据"全是明显的假值。⛔ 不要写成行首赋值形态
（`HR_LIAISON_BOT_SECRET=xxx`）——`test_liaison_no_secrets_in_vcs.py` 会扫受版本
管理的**全部**文件，行首赋值会被判成真凭据入库。
"""

from __future__ import annotations

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
    """把包级 logger 按生产方式装配好，返回日志文件路径。"""
    logsetup.setup_logging(log_dir=tmp_path)
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
