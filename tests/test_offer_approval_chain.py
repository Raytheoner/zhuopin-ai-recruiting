"""app/storage/offer_approval_chain.py：审批链岗位级配置读写
（offer-generation U1 Task 6，design D6）。

存储层行为测试：默认一级、全量替换、幂等 no-op、审批人账号校验、
level 连续性校验、共享单连接下半截写入回滚。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.offer_approval_chain import (
    UnknownApproverError,
    get_approval_chain,
    put_approval_chain,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "approval-chain.db"))
    init_schema(c)
    c.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    upsert_account(c, username="alice", password="s3cret!")
    upsert_account(c, username="bob", password="s3cret!")
    c.commit()
    return c


def _chain_rows(conn, job_id="j1"):
    return conn.execute(
        "SELECT level, approver_account_ids, updated_by, updated_at "
        "FROM offer_approval_chain WHERE job_id = ? ORDER BY level",
        (job_id,),
    ).fetchall()


class _FailingExecutemany:
    """代理真连接，只在 executemany 处炸——用于驱动"半截写入必须回滚"分支。

    sqlite3.Connection 是 C 类型，属性不可 monkeypatch，故用 __getattr__ 代理。
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def executemany(self, *args, **kwargs):
        raise sqlite3.OperationalError("boom")


# ── 读：未配置时返回默认一级 ────────────────────────────────────


def test_get_returns_default_level_one_when_unconfigured(conn):
    assert get_approval_chain(conn, "j1") == [
        {"level": 1, "approver_account_ids": [], "default": True}
    ]


def test_get_default_is_returned_even_for_unknown_job(conn):
    """读路径不校验 job 是否存在——未配置即默认链（存在性校验在 PUT 侧）。"""
    assert get_approval_chain(conn, "no-such-job") == [
        {"level": 1, "approver_account_ids": [], "default": True}
    ]


# ── 写：全量替换 + 读回 ─────────────────────────────────────────


def test_put_then_get_roundtrips(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["alice"]}],
        updated_by="hr-1",
    )
    assert get_approval_chain(conn, "j1") == [
        {
            "level": 1,
            "approver_account_ids": ["alice"],
            "updated_by": "hr-1",
            "updated_at": _chain_rows(conn)[0][3],
        }
    ]


def test_put_normalizes_approvers_sorted_and_deduplicated(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["bob", "alice", "bob"]}],
        updated_by="hr-1",
    )
    assert _chain_rows(conn)[0][1] == '["alice", "bob"]'


def test_put_stores_multiple_levels_ordered_by_level(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[
            {"level": 2, "approver_account_ids": ["bob"]},
            {"level": 1, "approver_account_ids": ["alice"]},
        ],
        updated_by="hr-1",
    )
    assert [(row[0], row[1]) for row in _chain_rows(conn)] == [
        (1, '["alice"]'),
        (2, '["bob"]'),
    ]


def test_put_replaces_previous_chain_wholesale(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[
            {"level": 1, "approver_account_ids": ["alice"]},
            {"level": 2, "approver_account_ids": ["bob"]},
        ],
        updated_by="hr-1",
    )
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["bob"]}],
        updated_by="hr-2",
    )
    assert [(row[0], row[1], row[2]) for row in _chain_rows(conn)] == [
        (1, '["bob"]', "hr-2"),
    ]


def test_put_allows_empty_approver_list(conn):
    """空列表＝"该级尚未指定审批人"（默认一级的占位语义），合法。"""
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": []}],
        updated_by="hr-1",
    )
    assert _chain_rows(conn)[0][1] == "[]"


# ── 幂等：同内容重复 PUT 不产生新版本 ───────────────────────────


def test_repeated_identical_put_is_noop(conn):
    body = [{"level": 1, "approver_account_ids": ["alice"]}]
    put_approval_chain(conn, job_id="j1", chain=body, updated_by="hr-1")
    first = _chain_rows(conn)
    assert len(first) == 1

    put_approval_chain(conn, job_id="j1", chain=body, updated_by="hr-1")
    assert _chain_rows(conn) == first  # updated_at 不动


def test_identical_content_in_different_order_is_still_noop(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["alice", "bob"]}],
        updated_by="hr-1",
    )
    before = _chain_rows(conn)
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["bob", "alice"]}],
        updated_by="hr-2",
    )
    assert _chain_rows(conn) == before


# ── 校验：审批人账号 / level / updated_by / job 存在性 ──────────


def test_put_unknown_approver_raises_and_writes_nothing(conn):
    with pytest.raises(UnknownApproverError):
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[{"level": 1, "approver_account_ids": ["nobody"]}],
            updated_by="hr-1",
        )
    assert _chain_rows(conn) == []


def test_put_unknown_approver_leaves_existing_chain_intact(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["alice"]}],
        updated_by="hr-1",
    )
    before = _chain_rows(conn)
    with pytest.raises(UnknownApproverError):
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[{"level": 1, "approver_account_ids": ["nobody"]}],
            updated_by="hr-1",
        )
    assert _chain_rows(conn) == before


def test_put_with_non_list_approvers_raises(conn):
    with pytest.raises(ValueError):
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[{"level": 1, "approver_account_ids": "alice"}],
            updated_by="hr-1",
        )


@pytest.mark.parametrize("bad_level", [0, -1, "1", None, 1.5])
def test_put_rejects_non_positive_integer_level(conn, bad_level):
    with pytest.raises(ValueError):
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[{"level": bad_level, "approver_account_ids": ["alice"]}],
            updated_by="hr-1",
        )


@pytest.mark.parametrize("levels", [[1, 3], [2, 3], [1, 1, 2]])
def test_put_rejects_non_contiguous_levels(conn, levels):
    with pytest.raises(ValueError):
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[
                {"level": level, "approver_account_ids": ["alice"]} for level in levels
            ],
            updated_by="hr-1",
        )


def test_put_empty_chain_clears_config_back_to_default(conn):
    """空链＝清空该岗位配置（读侧回落到默认一级），不报错。"""
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["alice"]}],
        updated_by="hr-1",
    )
    put_approval_chain(conn, job_id="j1", chain=[], updated_by="hr-1")
    assert _chain_rows(conn) == []
    assert get_approval_chain(conn, "j1") == [
        {"level": 1, "approver_account_ids": [], "default": True}
    ]


def test_put_unknown_job_raises(conn):
    with pytest.raises(ValueError):
        put_approval_chain(
            conn,
            job_id="no-such-job",
            chain=[{"level": 1, "approver_account_ids": ["alice"]}],
            updated_by="hr-1",
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_put_blank_updated_by_raises(conn, blank):
    with pytest.raises(ValueError):
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[{"level": 1, "approver_account_ids": ["alice"]}],
            updated_by=blank,
        )


def test_put_non_list_chain_raises(conn):
    with pytest.raises(ValueError):
        put_approval_chain(conn, job_id="j1", chain={}, updated_by="hr-1")


# ── 共享单连接：半截写入必须回滚 ────────────────────────────────


def test_put_rolls_back_when_insert_fails_midway(conn):
    put_approval_chain(
        conn,
        job_id="j1",
        chain=[{"level": 1, "approver_account_ids": ["alice"]}],
        updated_by="hr-1",
    )
    before = _chain_rows(conn)

    with pytest.raises(sqlite3.OperationalError):
        put_approval_chain(
            _FailingExecutemany(conn),
            job_id="j1",
            chain=[{"level": 1, "approver_account_ids": ["bob"]}],
            updated_by="hr-2",
        )

    # DELETE 已执行但 INSERT 失败：回滚后旧链必须原样在场（无半截状态）。
    assert _chain_rows(conn) == before
