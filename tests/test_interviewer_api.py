"""面试官名单维护（tasks 1.7）——存储层行为测试（TDD 先写测试）。

本文件是计划 `2026-10-08-interview-scheduling-unit1-domain-model` Task 7 的验收文件
（Task 7 的命令即 `pytest tests/test_interviewer_api.py`）。Task 7 只落存储层
`app/storage/interviewer.py`；接口契约（401/403/幂等/404 分支）在 Task 8 接线后
由 Task 10 在本文件续写，两层共用同一份名单语义。

覆盖的 spec 口径：
- 名单 MUST NOT 由 AI 生成或推荐 ⇒ 模块源码里没有任何模型调用面（下面的反证测试）；
- design D7「能登记时段的账号 ＝ 名单内账号」⇒ `account_id` 必须存在于 `hr_account`，
  不存在即拒（`InterviewerAccountMissing`），绝不隐式建号；
- 幂等口径（本单元唯一的副作用写）⇒ 同 `account_id` 重复创建返回既有记录、
  库内仍只有一行、且**不覆盖**既有字段（account_id UNIQUE 一一对应）；
- 姓名是名单的人读标识 ⇒ 空白姓名拒写（`ValueError`），不能落成一行无名单。
"""
from __future__ import annotations

import inspect
import sqlite3

import pytest

from app.storage import interviewer as interviewer_module
from app.storage.auth_session import create_session
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account
from app.storage.interviewer import (
    InterviewerAccountMissing,
    InterviewerNotFound,
    create_interviewer,
    list_interviewers,
    update_interviewer,
)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "interviewer.db"))
    init_schema(c)
    return c


def _make_account(conn: sqlite3.Connection, username: str, role: str = "hr") -> str:
    account_id = upsert_account(conn, username=username, password="testpass123")
    if role != "hr":
        conn.execute(
            "UPDATE hr_account SET role = ? WHERE id = ?", (role, account_id)
        )
        conn.commit()
    return account_id


def _row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM interviewer").fetchone()[0]


def test_list_is_empty_on_fresh_db(conn):
    assert list_interviewers(conn) == []


def test_create_returns_full_row_with_username(conn):
    hr_id = _make_account(conn, "hr-1")
    target_id = _make_account(conn, "scheduler-1", role="interviewer")

    created = create_interviewer(
        conn,
        account_id=target_id,
        name="汤丽萍",
        department="人事部",
        interviewable_jobs=["嵌入式软件工程师", "硬件工程师"],
        enabled=True,
    )

    # username 来自 hr_account 的 JOIN——接口层不需要二次查询账号名。
    assert created["account_id"] == target_id
    assert created["username"] == "scheduler-1"
    assert created["name"] == "汤丽萍"
    assert created["department"] == "人事部"
    assert created["interviewable_jobs"] == ["嵌入式软件工程师", "硬件工程师"]
    assert created["enabled"] is True
    assert created["created_at"]
    assert created["id"]
    assert created["id"] != hr_id


def test_create_requires_existing_hr_account(conn):
    """design D7 反证：名单行必须挂在真账号上，⛔ 不隐式建号、不留孤儿名单行。"""
    with pytest.raises(InterviewerAccountMissing):
        create_interviewer(
            conn,
            account_id="no-such-account",
            name="张三",
            department=None,
            interviewable_jobs=[],
            enabled=True,
        )

    assert _row_count(conn) == 0


def test_create_rejects_blank_name(conn):
    account_id = _make_account(conn, "scheduler-1", role="interviewer")

    for blank in ("", "   "):
        with pytest.raises(ValueError):
            create_interviewer(
                conn,
                account_id=account_id,
                name=blank,
                department=None,
                interviewable_jobs=[],
                enabled=True,
            )

    assert _row_count(conn) == 0


def test_create_rejects_blank_account_id(conn):
    with pytest.raises(ValueError):
        create_interviewer(
            conn,
            account_id="   ",
            name="张三",
            department=None,
            interviewable_jobs=[],
            enabled=True,
        )

    assert _row_count(conn) == 0


def test_create_is_idempotent_by_account_id(conn):
    """本单元唯一的副作用写的幂等口径：同 account_id 返回既有记录（不新增、不改写）。"""
    account_id = _make_account(conn, "scheduler-1", role="interviewer")

    first = create_interviewer(
        conn,
        account_id=account_id,
        name="汤丽萍",
        department="人事部",
        interviewable_jobs=["嵌入式软件工程师"],
        enabled=True,
    )
    second = create_interviewer(
        conn,
        account_id=f"  {account_id}  ",  # 前后空白先 strip 再判幂等
        name="另一个名字",
        department="研发部",
        interviewable_jobs=[],
        enabled=False,
    )

    assert second["id"] == first["id"]
    # 既有记录原样返回——第二次调用的入参不生效（不是 upsert）。
    assert second["name"] == "汤丽萍"
    assert second["department"] == "人事部"
    assert second["interviewable_jobs"] == ["嵌入式软件工程师"]
    assert second["enabled"] is True
    assert _row_count(conn) == 1


def test_list_orders_by_name_then_id(conn):
    first = create_interviewer(
        conn,
        account_id=_make_account(conn, "scheduler-1", role="interviewer"),
        name="bob",
        department=None,
        interviewable_jobs=[],
        enabled=True,
    )
    second = create_interviewer(
        conn,
        account_id=_make_account(conn, "scheduler-2", role="interviewer"),
        name="Alice",
        department=None,
        interviewable_jobs=[],
        enabled=True,
    )
    third = create_interviewer(
        conn,
        account_id=_make_account(conn, "scheduler-3", role="interviewer"),
        name="bob",
        department=None,
        interviewable_jobs=[],
        enabled=True,
    )

    listing = list_interviewers(conn)

    # 大小写不敏感排序（COLLATE NOCASE）：Alice 在 bob 之前。
    assert [row["name"] for row in listing] == ["Alice", "bob", "bob"]
    # 同名者按 id 定序（顺序稳定，页面不会因为排序不稳定而跳动）。
    bob_ids = [row["id"] for row in listing if row["name"] == "bob"]
    assert bob_ids == sorted([first["id"], third["id"]])
    assert second["id"] in [row["id"] for row in listing]


def test_update_patches_only_given_fields(conn):
    account_id = _make_account(conn, "scheduler-1", role="interviewer")
    created = create_interviewer(
        conn,
        account_id=account_id,
        name="汤丽萍",
        department="人事部",
        interviewable_jobs=["嵌入式软件工程师"],
        enabled=True,
    )

    updated = update_interviewer(
        conn,
        interviewer_id=created["id"],
        updates={"enabled": False, "interviewable_jobs": ["硬件工程师"]},
    )

    assert updated["enabled"] is False
    assert updated["interviewable_jobs"] == ["硬件工程师"]
    # 未出现在 updates 里的列保持原值（PATCH 语义）。
    assert updated["name"] == "汤丽萍"
    assert updated["department"] == "人事部"
    assert updated["account_id"] == account_id

    # 落库值真的是 0/1 而非常量真值。
    assert conn.execute(
        "SELECT enabled FROM interviewer WHERE id = ?", (created["id"],)
    ).fetchone()[0] == 0


def test_update_can_clear_department_and_name(conn):
    created = create_interviewer(
        conn,
        account_id=_make_account(conn, "scheduler-1", role="interviewer"),
        name="汤丽萍",
        department="人事部",
        interviewable_jobs=[],
        enabled=True,
    )

    updated = update_interviewer(
        conn,
        interviewer_id=created["id"],
        updates={"department": None, "name": " 汤丽萍 "},
    )

    assert updated["department"] is None
    assert updated["name"] == "汤丽萍"  # strip 后写回


def test_update_rejects_unknown_id(conn):
    with pytest.raises(InterviewerNotFound):
        update_interviewer(conn, interviewer_id="no-such-id", updates={"enabled": False})


def test_update_rejects_blank_name(conn):
    created = create_interviewer(
        conn,
        account_id=_make_account(conn, "scheduler-1", role="interviewer"),
        name="汤丽萍",
        department=None,
        interviewable_jobs=[],
        enabled=True,
    )

    with pytest.raises(ValueError):
        update_interviewer(conn, interviewer_id=created["id"], updates={"name": "  "})

    # 拒写后原值不动。
    assert list_interviewers(conn)[0]["name"] == "汤丽萍"


def test_update_with_empty_updates_is_noop(conn):
    created = create_interviewer(
        conn,
        account_id=_make_account(conn, "scheduler-1", role="interviewer"),
        name="汤丽萍",
        department="人事部",
        interviewable_jobs=["嵌入式软件工程师"],
        enabled=True,
    )

    assert update_interviewer(conn, interviewer_id=created["id"], updates={}) == created


def test_update_ignores_columns_outside_whitelist(conn):
    """列名白名单是写路径的唯一来源，⛔ 不入参即 SQL——越界键被静默忽略而不是拼进 SQL。"""
    account_id = _make_account(conn, "scheduler-1", role="interviewer")
    other_id = _make_account(conn, "scheduler-2", role="interviewer")
    created = create_interviewer(
        conn,
        account_id=account_id,
        name="汤丽萍",
        department=None,
        interviewable_jobs=[],
        enabled=True,
    )

    updated = update_interviewer(
        conn,
        interviewer_id=created["id"],
        updates={"account_id": other_id, "id": "hacked", "unknown": 1},
    )

    assert updated["account_id"] == account_id
    assert updated["id"] == created["id"]


def test_interviewable_jobs_round_trip_chinese_without_escapes(conn):
    """JSON 用 ensure_ascii=False 落库——名单要能被人直接读懂（.51 运维查库场景）。"""
    created = create_interviewer(
        conn,
        account_id=_make_account(conn, "scheduler-1", role="interviewer"),
        name="汤丽萍",
        department=None,
        interviewable_jobs=["嵌入式软件工程师"],
        enabled=True,
    )

    raw = conn.execute(
        "SELECT interviewable_jobs FROM interviewer WHERE id = ?", (created["id"],)
    ).fetchone()[0]
    assert raw == '["嵌入式软件工程师"]'
    assert created["interviewable_jobs"] == ["嵌入式软件工程师"]


def test_module_has_no_ai_generation_path():
    """名单 MUST NOT 由 AI 生成或推荐——模块源码里没有任何模型调用面（反证）。"""
    source = inspect.getsource(interviewer_module)
    lowered = source.lower()

    assert "app.llm" not in lowered
    assert "llm" not in lowered
    assert "gateway" not in lowered
    assert "prompt" not in lowered
    assert "temperature" not in lowered


# ── Task 8 接线后的接口契约（计划 Task 10 的正文，先落入本文件让 Task 8 的 TDD 能跑）──
# 鉴权三层：未登录 → 401；已登录但 role != 'hr' → 403；role = 'hr' → 放行。
# ⚠️ Task 10 若整文件重写本文件，必须同时保留上面 Task 7 的存储层用例与本节。


def _account(conn, username: str, role: str = "hr") -> str:
    account_id = upsert_account(conn, username=username, password="testpass123")
    if role != "hr":
        conn.execute(
            "UPDATE hr_account SET role = ? WHERE username = ?", (role, username)
        )
        conn.commit()
    return account_id


def _login(client, conn, account_id: str) -> None:
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def test_interviewers_requires_login(make_test_client):
    client, conn = make_test_client()
    assert client.get("/api/interviewers").status_code == 401


def test_interviewers_rejects_non_hr_role(make_test_client):
    client, conn = make_test_client()
    interviewer_id = _account(conn, "interviewer-1", role="interviewer")
    _login(client, conn, interviewer_id)

    assert client.get("/api/interviewers").status_code == 403
    assert client.post(
        "/api/interviewers", json={"account_id": "any", "name": "张三"}
    ).status_code == 403


def test_hr_can_create_and_list_interviewer(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1")
    target_id = _account(conn, "interviewee-1", role="interviewer")
    _login(client, conn, hr_id)

    created = client.post("/api/interviewers", json={
        "account_id": target_id,
        "name": "汤丽萍",
        "department": "人事部",
        "interviewable_jobs": ["嵌入式软件工程师"],
        "enabled": True,
    })
    assert created.status_code == 201
    body = created.json()
    assert body["account_id"] == target_id
    assert body["name"] == "汤丽萍"
    assert body["department"] == "人事部"
    assert body["interviewable_jobs"] == ["嵌入式软件工程师"]
    assert body["enabled"] is True

    listing = client.get("/api/interviewers")
    assert listing.status_code == 200
    assert [row["account_id"] for row in listing.json()] == [target_id]


def test_create_interviewer_is_idempotent_by_account_id(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1")
    target_id = _account(conn, "interviewee-1", role="interviewer")
    _login(client, conn, hr_id)

    payload = {"account_id": target_id, "name": "汤丽萍", "interviewable_jobs": ["嵌入式"]}
    first = client.post("/api/interviewers", json=payload)
    second = client.post("/api/interviewers", json={**payload, "name": "改名不应生效"})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["name"] == "汤丽萍"


def test_create_interviewer_missing_account_returns_404(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, _account(conn, "hr-1"))
    resp = client.post("/api/interviewers", json={
        "account_id": "no-such-account", "name": "张三"
    })
    assert resp.status_code == 404


def test_patch_updates_only_provided_fields(make_test_client):
    client, conn = make_test_client()
    hr_id = _account(conn, "hr-1")
    target_id = _account(conn, "interviewee-1", role="interviewer")
    _login(client, conn, hr_id)

    created = client.post("/api/interviewers", json={
        "account_id": target_id,
        "name": "汤丽萍",
        "department": "人事部",
        "interviewable_jobs": ["嵌入式"],
        "enabled": True,
    }).json()

    resp = client.patch(
        f"/api/interviewers/{created['id']}", json={"department": "行政部"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["department"] == "行政部"
    assert body["name"] == "汤丽萍"
    assert body["interviewable_jobs"] == ["嵌入式"]


def test_patch_missing_interviewer_returns_404(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, _account(conn, "hr-1"))
    resp = client.patch("/api/interviewers/no-such-id", json={"name": "张三"})
    assert resp.status_code == 404
