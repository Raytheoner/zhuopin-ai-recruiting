"""JD 编辑与「标记为人工撰写」两个 effect_* 节点（tasks 7.5）。

⚠️ 两个动作**各自独占一个节点**，⛔ 不合并成一个"处理 JD 变更"节点。
理由是工程铁律 1 的直接推论：两条路径的对外后果完全不同（改正文但保留标识 /
去掉标识并留痕），合成一个节点后"恢复时节点从头整个重跑"会带着一个分支参数
走进另一条路径，而幂等键只有一个——重跑一次就可能把标识去掉。
"""

import json

import pytest

from app.agents.jd_agent import AI_LABEL_PREFIX, AI_LABEL_TEMPLATE
from app.graph.jd_nodes import (
    JDNotGeneratedError,
    effect_mark_jd_human_written,
    effect_update_jd_text,
    jd_edit_business_key,
)
from app.storage.db import get_connection, init_schema

_TS = "2026-09-04T02:00:00+00:00"
_LABEL = AI_LABEL_TEMPLATE.format(generated_at=_TS)


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "jd.db"))
    init_schema(connection)
    connection.execute(
        "INSERT INTO job (id, title, status) VALUES ('J1', '底层软件工程师', 'approved')"
    )
    connection.execute(
        "INSERT INTO job_profile (job_id, version, status, profile_json) "
        "VALUES ('J1', 1, 'approved', ?)",
        (
            json.dumps(
                {
                    "job_title": "底层软件工程师",
                    "mcu_family": ["英飞凌 Aurix"],
                    "_jd_text": f"岗位职责：负责 ECU 底层软件开发。\n\n{_LABEL}",
                    "_jd_needs_manual": False,
                },
                ensure_ascii=False,
            ),
        ),
    )
    connection.commit()
    return connection


def _profile(conn) -> dict:
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id='J1' AND version=1"
    ).fetchone()
    return json.loads(row[0])


def _effect_rows(conn, node_name: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?", (node_name,)
    ).fetchone()[0]


# ── 常规编辑：标识删不掉 ────────────────────────────────────────────────


def test_edit_reattaches_the_label_the_user_deleted(conn):
    """合规红线的核心断言：用户把标识整行删掉再提交，落库结果照样带标识。"""
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "HR 改过的正文，标识已被我删掉。"),
        version=1,
        edited_text="HR 改过的正文，标识已被我删掉。",
    )
    stored = _profile(conn)["_jd_text"]
    assert stored.startswith("HR 改过的正文，标识已被我删掉。")
    assert stored.endswith(_LABEL)


def test_edit_preserves_the_original_generation_time(conn):
    """标识记的是"AI 什么时候生成的"，不是"HR 什么时候改的"。"""
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "改一版"),
        version=1,
        edited_text="改一版",
    )
    assert _TS in _profile(conn)["_jd_text"]


def test_edit_does_not_stack_labels(conn):
    text = f"正文\n\n{_LABEL}"
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, text),
        version=1,
        edited_text=text,
    )
    assert _profile(conn)["_jd_text"].count(AI_LABEL_PREFIX) == 1


def test_edit_never_touches_business_fields_or_status(conn):
    """决策 4：画像冻结后不可变。⛔ 只准动下划线内部键。"""
    before = _profile(conn)
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "改一版"),
        version=1,
        edited_text="改一版",
    )
    after = _profile(conn)
    assert {k: v for k, v in after.items() if not k.startswith("_")} == {
        k: v for k, v in before.items() if not k.startswith("_")
    }
    status = conn.execute(
        "SELECT status FROM job_profile WHERE job_id='J1' AND version=1"
    ).fetchone()[0]
    assert status == "approved"


def test_edit_is_idempotent_on_the_same_text(conn):
    """双击、超时重发、反向代理重试都会打过来同一份文本。"""
    key = jd_edit_business_key(1, "改一版")
    effect_update_jd_text(
        conn, thread_id="J1", business_key=key, version=1, edited_text="改一版"
    )
    assert (
        effect_update_jd_text(
            conn, thread_id="J1", business_key=key, version=1, edited_text="改一版"
        )
        is None
    )
    assert _effect_rows(conn, "effect_update_jd_text") == 1


def test_a_genuinely_different_edit_goes_through(conn):
    """幂等键含内容哈希，所以"改第二版"不会被第一版的 effect_log 短路挡住。"""
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "第一版"),
        version=1,
        edited_text="第一版",
    )
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "第二版"),
        version=1,
        edited_text="第二版",
    )
    assert _profile(conn)["_jd_text"].startswith("第二版")
    assert _effect_rows(conn, "effect_update_jd_text") == 2


def test_edit_before_any_jd_exists_raises(conn):
    conn.execute(
        "INSERT INTO job_profile (job_id, version, status, profile_json) "
        "VALUES ('J1', 2, 'drafting', ?)",
        (json.dumps({"job_title": "尚未生成 JD"}, ensure_ascii=False),),
    )
    conn.commit()
    with pytest.raises(JDNotGeneratedError):
        effect_update_jd_text(
            conn,
            thread_id="J1",
            business_key=jd_edit_business_key(2, "改一版"),
            version=2,
            edited_text="改一版",
        )


# ── 标记为人工撰写：唯一能去标识的路径，且必须留痕 ──────────────────────


def test_mark_removes_the_label_and_records_who_and_when(conn):
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    profile = _profile(conn)
    assert AI_LABEL_PREFIX not in profile["_jd_text"]
    assert profile["_jd_authorship"] == {
        "human_written": True,
        "marked_by": "zhangsan",
        "at": _TS,
    }


@pytest.mark.compliance
def test_label_removal_and_the_record_are_inseparable(conn):
    """合规红线的结构性保证：标识去掉了但没有留痕，这个状态**不可能存在**。

    两者写在同一行的同一次 UPDATE 里，由 idempotent_effect 在同一个事务里
    连同 effect_log 一起提交（工程铁律 1）。⛔ 不许拆成两条 UPDATE，
    更不许拆成两次 commit——那正是 .51 现网 2026-08-10/08-12 丢 outbox 的形状。
    """
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    rows = conn.execute("SELECT profile_json FROM job_profile").fetchall()
    for (raw,) in rows:
        profile = json.loads(raw)
        text = profile.get("_jd_text")
        if text is None:
            continue
        if AI_LABEL_PREFIX not in text:
            assert profile.get("_jd_authorship"), (
                "存在一份没有 AI 标识、也没有人工撰写留痕的 JD——"
                "这正是《AI 生成合成内容标识办法》要禁止的状态"
            )


@pytest.mark.compliance
def test_effect_log_count_equals_marked_profile_count(conn):
    """工程铁律 1 的 reviewer 判据：effect_log 条数与业务行数按 thread 恒等。"""
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="lisi",
        marked_at="2026-09-05T00:00:00+00:00",
    )
    logged = _effect_rows(conn, "effect_mark_jd_human_written")
    marked = sum(
        1
        for (raw,) in conn.execute("SELECT profile_json FROM job_profile").fetchall()
        if json.loads(raw).get("_jd_authorship")
    )
    assert logged == marked == 1


@pytest.mark.compliance
def test_crash_between_label_removal_and_authorship_write_leaves_no_orphan_state(
    conn, monkeypatch
):
    """补测（Task 3 变异验证 ③ 发现的缺口）：现有测试只看"函数正常跑完之后"的
    最终状态，抓不住"去标识"与"写留痕"被拆成两条 UPDATE、两次 commit 时，
    两次写之间进程崩溃会留下的中间态——一份标识已经没了、但也没有留痕的 JD。

    做法：让 `_save_profile` 的第二次调用抛异常。在正确实现（一次 UPDATE、
    交给 idempotent_effect 统一提交）下，`_save_profile` 只会被调用一次，
    这次 mock 完全不介入，函数正常提交、断言照样成立。只有被拆成两条
    UPDATE（先提交去标识，再写留痕）的实现，才会在第二次调用时踩中这个
    mock，从而在"标识已被上一次 commit 真正落盘、留痕还没来得及写"的
    中间态上被抓个正着。
    """
    import app.graph.jd_nodes as jd_nodes

    original_save_profile = jd_nodes._save_profile
    call_count = {"n": 0}

    def flaky_save_profile(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("simulated crash before the second UPDATE completes")
        return original_save_profile(*args, **kwargs)

    monkeypatch.setattr(jd_nodes, "_save_profile", flaky_save_profile)

    try:
        effect_mark_jd_human_written(
            conn,
            thread_id="J1",
            business_key="1",
            version=1,
            reviewer="zhangsan",
            marked_at=_TS,
        )
    except RuntimeError:
        pass

    profile = _profile(conn)
    text = profile.get("_jd_text")
    label_present = text is not None and AI_LABEL_PREFIX in text
    record_present = bool(profile.get("_jd_authorship"))
    assert label_present or record_present, (
        "崩溃后出现了既没有 AI 标识、也没有人工撰写留痕的状态——"
        "这正是把「去标识」与「写留痕」拆成两条 UPDATE、两次 commit 会产生的中间态"
    )


def test_mark_is_idempotent_and_keeps_the_first_reviewer(conn):
    """重复标记只留第一个人。第一个按下这个按钮的人才是决策人。"""
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    assert (
        effect_mark_jd_human_written(
            conn,
            thread_id="J1",
            business_key="1",
            version=1,
            reviewer="lisi",
            marked_at="2026-09-05T00:00:00+00:00",
        )
        is None
    )
    assert _profile(conn)["_jd_authorship"]["marked_by"] == "zhangsan"


@pytest.mark.compliance
def test_mark_rejects_a_blank_reviewer(conn):
    """合规红线：决策人只能是人。空白 reviewer 等于"没人负责却去掉了标识"。"""
    with pytest.raises(ValueError):
        effect_mark_jd_human_written(
            conn,
            thread_id="J1",
            business_key="1",
            version=1,
            reviewer="   ",
            marked_at=_TS,
        )
    assert AI_LABEL_PREFIX in _profile(conn)["_jd_text"]
    assert _effect_rows(conn, "effect_mark_jd_human_written") == 0


def test_edit_after_marking_does_not_bring_the_label_back(conn):
    """已经声明是人工撰写的文案，再编辑⛔ 不许把 AI 标识贴回去——
    那会把一份人写的文案标成 AI 生成的，方向反了同样是错的标识。"""
    effect_mark_jd_human_written(
        conn,
        thread_id="J1",
        business_key="1",
        version=1,
        reviewer="zhangsan",
        marked_at=_TS,
    )
    effect_update_jd_text(
        conn,
        thread_id="J1",
        business_key=jd_edit_business_key(1, "人工重写的正文"),
        version=1,
        edited_text="人工重写的正文",
    )
    profile = _profile(conn)
    assert profile["_jd_text"] == "人工重写的正文"
    assert profile["_jd_authorship"]["marked_by"] == "zhangsan"
