"""candidate-contact-vault（姊妹变更包 interview-scheduling U4 交付）的可用性
探测与读取适配（voice-structured-interview design D13/D14，tasks 4.10）。

⚠️ 本模块不建 vault 本身——vault 属于 interview-scheduling 变更包
（`app/storage/contact_vault.py::is_candidate_contact_vault_enabled()` /
`read_contact()`），本包只是它的第一个调用方之一。写这份计划时该模块尚未
交付（`openspec/changes/interview-scheduling/tasks.md` 4.1-4.4 未勾选）。

⛔ 不在模块顶部 `from app.storage.contact_vault import ...`——那会在 vault
未交付期间让本包的任何 import 链路直接 ImportError。改用运行时惰性 import，
"模块不存在"与"模块存在但开关关闭"两种情况统一折成 False/None
（fail-closed：未知即当作不可用，与 tasks 4.10「vault 开关关 ⇒ 签发被拒」
同一口径）。
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def is_contact_vault_available() -> bool:
    """vault 模块已交付且开关开启才算可用。模块不存在、开关关闭、或求值过程
    出任何异常，一律返回 False。"""
    try:
        from app.storage.contact_vault import is_candidate_contact_vault_enabled
    except ImportError:
        return False
    try:
        return is_candidate_contact_vault_enabled()
    except Exception:
        logger.exception("candidate-contact-vault 开关求值异常，按不可用处理")
        return False


def resolve_live_candidate_phone(conn: sqlite3.Connection, *, application_id: str) -> str | None:
    """live 场次专用：从 vault 读取该投递的候选人手机号（拍平字符串）。
    vault 不可用（未交付/未开启/读取异常）一律返回 None——调用方据此拒绝
    签发 live 场次邀请（tasks 4.10）。internal_sim 场次不应调用本函数
    （spec「internal_sim 场次不经 vault」）。"""
    if not is_contact_vault_available():
        return None
    try:
        from app.storage.contact_vault import read_contact
    except ImportError:
        return None
    try:
        contact = read_contact(
            conn,
            application_id=application_id,
            accessor="system:voice-interview-invite",
            purpose="live_interview_invite",
        )
    except Exception:
        logger.exception(
            "application_id=%s 读取 candidate-contact-vault 失败，按不可用处理",
            application_id,
        )
        return None
    return getattr(contact, "phone", None)
