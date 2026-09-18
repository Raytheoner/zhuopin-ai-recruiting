"""app/storage/contact_source.py：candidate-contact-vault（姊妹变更包
interview-scheduling 交付）的可用性探测与读取适配（tasks 4.10）。
本包不建 vault 模块本身，vault 不存在时一律按"未开启"处理（fail-closed）。
"""

import sqlite3

from app.storage.contact_source import is_contact_vault_available, resolve_live_candidate_phone


def test_vault_module_absent_reports_unavailable():
    # 开发环境此时 app.storage.contact_vault 尚不存在（interview-scheduling
    # 未交付）——is_contact_vault_available 必须优雅返回 False，不抛 ImportError。
    assert is_contact_vault_available() is False


def test_resolve_live_candidate_phone_returns_none_when_vault_unavailable(tmp_path):
    conn = sqlite3.connect(":memory:")
    assert resolve_live_candidate_phone(conn, application_id="a1") is None
