"""channel-resume-intake U2 Task 5（合并节点）与 Task 6（撤销节点）的用例。

与计划 Task 5 的字面文本有三处不同，均已登记
（见计划「偏离登记 D-CU2-3 / D-CU2-3b / D-CU2-4」）：

① 计划文本里三个用例的形参是 `conn`，但同一份文本里只定义了 `_conn()` 辅助函数，
   `tests/conftest.py` 与仓库里也没有名为 `conn` 的 fixture——照抄的话 pytest 直接
   报 `fixture 'conn' not found`，计划自己写的「预期 3 passed」不可达（这是收集期
   的用例错误，不是待实现的功能失败）。落地：把 `_conn()` 改成同名 fixture
   `@pytest.fixture def conn()`（与 tests/test_appeal_state_machine.py 同一手法：
   内存库 + `init_schema` + 两个岗位），三个用例体逐字不动。

   ①-b 连带一处：夹具与两个 seed 辅助**显式 commit**。`idempotent_effect` 的既有语义
   是「fn 抛异常 ⇒ 回滚本连接」（app/storage/idempotency.py），而测试里的 seed 行若
   留在未提交事务里，会被夹具**后面**那次 `pytest.raises(MergeValidationError)`
   的合法回滚一并抹掉，紧接着的第二次 `_merge` 就报「候选人不存在」——那是夹具
   的假象，不是被测逻辑的问题（生产连接上，seed 行的等价物早已由各自的 effect 提交）。
   与 tests/test_appeal_state_machine.py 的 `c.commit()` 同一手法。

② 计划文本的 import 行同时引入 `effect_unmerge_candidates`，那是 **Task 6** 才落地的
   符号。照抄的话本文件 import 期即 ImportError，`-k 'not unmerge'` 拦不住
   （收集中断不是用例失败）。落地：本任务只 import Task 5 真正产出的两个名字，
   Task 6 追加撤销用例时把第三个名字补回这一行。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.intake.merge import MergeValidationError, effect_merge_candidates
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO job (id, title) VALUES ('j2', '软件工程师')")
    c.commit()
    return c


def _seed_candidate(conn, cid, name):
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, ?)", (cid, name))
    conn.commit()


def _seed_resume_application(conn, *, resume_id, candidate_id, job_id):
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', ?, ?, 'alice')",
        (resume_id, job_id, resume_id + ".pdf", "h-" + resume_id),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        ("app-" + resume_id, candidate_id, job_id, resume_id),
    )
    conn.commit()


def _merge(conn, *, primary_id, secondary_id, reason="电话确认同一人", keep=None, request_id="req-1"):
    return effect_merge_candidates(
        conn,
        thread_id=primary_id,
        business_key=f"{secondary_id}:{request_id}",
        primary_id=primary_id,
        secondary_id=secondary_id,
        reason=reason,
        keep_application_per_job=keep or {},
        merged_by="alice",
    )


def test_merge_reassigns_applications_and_sets_merged_into(conn):
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    _seed_resume_application(conn, resume_id="r2", candidate_id="s1", job_id="j2")

    result = _merge(conn, primary_id="p1", secondary_id="s1")

    assert conn.execute(
        "SELECT merged_into FROM candidate WHERE id = 's1'"
    ).fetchone()[0] == "p1"
    assert conn.execute(
        "SELECT COUNT(*) FROM application WHERE candidate_id = 'p1'"
    ).fetchone()[0] == 2
    row = conn.execute(
        "SELECT secondary_snapshot FROM candidate_merge_log WHERE id = ?",
        (result["merge_log_id"],),
    ).fetchone()
    snapshot = json.loads(row[0])
    assert {a["resume_id"] for a in snapshot["applications"]} == {"r1", "r2"}


def test_same_job_double_application_requires_keep_choice(conn):
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="p1", job_id="j1")
    _seed_resume_application(conn, resume_id="r2", candidate_id="s1", job_id="j1")

    with pytest.raises(MergeValidationError):
        _merge(conn, primary_id="p1", secondary_id="s1", keep={})

    result = _merge(conn, primary_id="p1", secondary_id="s1", keep={"j1": "app-r1"})
    closed = conn.execute(
        "SELECT action FROM application_stage_history WHERE application_id = 'app-r2' "
        "AND action = 'closed_by_merge'"
    ).fetchone()
    assert closed is not None
    assert result["merge_log_id"]


def test_secondary_multi_same_job_without_primary_open_stays_open(conn):
    """主方同岗位没有投递、被合并方同岗位两份：两份都改挂，⛔ 不产生 closed_by_merge。

    2026-10-11 修正（1001O seg2 Spec review F1）：旧实现改挂后重查主方投递会读到
    自己刚改挂的行，凭空写 closed_by_merge（HR 未被提示）。"""
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    _seed_resume_application(conn, resume_id="r2", candidate_id="s1", job_id="j1")

    result = _merge(conn, primary_id="p1", secondary_id="s1", keep={})

    rows = conn.execute(
        "SELECT id, candidate_id FROM application WHERE job_id = 'j1' ORDER BY id"
    ).fetchall()
    assert rows == [("app-r1", "p1"), ("app-r2", "p1")]
    closed = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action = 'closed_by_merge'"
    ).fetchone()[0]
    assert closed == 0
    assert result["merge_log_id"]


def test_merge_replay_is_short_circuited(conn):
    _seed_candidate(conn, "p1", "张三")
    _seed_candidate(conn, "s1", "李四")
    _seed_resume_application(conn, resume_id="r1", candidate_id="s1", job_id="j1")
    first = _merge(conn, primary_id="p1", secondary_id="s1", request_id="req-1")
    second = _merge(conn, primary_id="p1", secondary_id="s1", request_id="req-1")
    assert first is not None and second is None
    assert conn.execute("SELECT COUNT(*) FROM candidate_merge_log").fetchone()[0] == 1
