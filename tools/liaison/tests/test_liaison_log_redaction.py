"""8.4 · 脱敏内核。正例（打码）与反例（`thread_id` / `msgid` 不许被打码）。

⚠️ 本文件里的"凭据"全是明显的假值。⛔ 不要写成行首赋值形态
（`HR_LIAISON_BOT_SECRET=xxx`）——`test_liaison_no_secrets_in_vcs.py` 会扫受版本
管理的**全部**文件，行首赋值会被判成真凭据入库。
"""

from __future__ import annotations

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
    """`msgid='...'` 与 `thread_id=`（空值）两种渲染形态都要认。"""
    text = "msgid='13800001111' thread_id= 邮件 a.b@example.com"
    out = logsetup.compute_redacted_text(text)
    assert "msgid='13800001111'" in out
    assert "a.b@example.com" not in out


def test_redaction_is_idempotent():
    """跑两遍结果不变——Filter 与 Formatter 两层都会跑，不幂等就会越洗越花。"""
    text = "邮箱 a.b@example.com 手机 13812345678 LLM_API_KEY=fake-value-2"
    once = logsetup.compute_redacted_text(text)
    assert logsetup.compute_redacted_text(once) == once


def test_empty_text_is_returned_unchanged():
    assert logsetup.compute_redacted_text("") == ""
