from __future__ import annotations

import hashlib
import json
import sqlite3

from app.agents.jd_agent import (
    UNKNOWN_GENERATED_AT,
    enforce_ai_label,
    extract_label_generated_at,
    strip_ai_label,
)
from app.storage.idempotency import idempotent_effect

# `profile_json` 里承载 JD 相关状态的三个内部键。都以下划线开头，
# 于是 app/agents/jd_grounding.profile_grounding_haystack 会自动把它们排除在
# 溯源证据之外（那里按前缀排除，不按键名清单）。
JD_TEXT_KEY = "_jd_text"
JD_AUTHORSHIP_KEY = "_jd_authorship"


class JDNotGeneratedError(Exception):
    """这一版画像还没有 JD，编辑与标记都无从谈起。

    ⛔ 不要在节点里把它降级成"那就当空文案处理"：那会凭空造出一份只有 AI 标识、
    没有正文的 JD 落进库里，而调用方看到的是一次成功。
    """


def jd_edit_business_key(version: int, text: str) -> str:
    """`{version}:{正文哈希}`。

    含内容哈希是刻意的：同一份文本重复提交（双击、客户端超时重发、反向代理
    重试）必须被 effect_log 短路，而"HR 真的又改了一版"必须能过去。只用
    version 会把第二次真实编辑吃掉，只用哈希会让不同版本的同名编辑串在一起。
    与 app/graph/nodes.py 的 message_business_key 同一条思路。
    """
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]
    return f"{version}:{digest}"


def _load_profile(conn: sqlite3.Connection, *, job_id: str, version: int) -> dict:
    """读出这一版画像的 profile_json。

    在节点函数体内读、用同一个 conn：读与随后的写落在同一个事务里，
    ⛔ 不许让调用方先读好再传进来——那中间隔着一次 HTTP 反序列化，
    读到的可能已经不是写回去时那一版。
    """
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
        (job_id, version),
    ).fetchone()
    if row is None:
        raise JDNotGeneratedError(f"找不到画像 {job_id} v{version}")
    profile = json.loads(row[0])
    if JD_TEXT_KEY not in profile:
        raise JDNotGeneratedError(f"画像 {job_id} v{version} 还没有生成过 JD")
    return profile


def _save_profile(
    conn: sqlite3.Connection, *, job_id: str, version: int, profile: dict
) -> None:
    """一次 UPDATE 写回整个 profile_json。

    ⛔ 不拆成多条 UPDATE：`_jd_text` 与 `_jd_authorship` 必须一起落地。拆开之后
    "标识已经去掉、留痕还没写"会成为一个真实存在的中间态，而崩在那一刻就留下
    一份没有标识也没有责任人的 JD——正是《AI 生成合成内容标识办法》要禁止的东西。

    不在这里 conn.commit()：写入必须与 effect_log 记录由 idempotent_effect
    装饰器在同一个事务里一次性提交（工程铁律 1）。
    """
    conn.execute(
        "UPDATE job_profile SET profile_json = ? WHERE job_id = ? AND version = ?",
        (json.dumps(profile, ensure_ascii=False), job_id, version),
    )


@idempotent_effect("effect_update_jd_text")
def effect_update_jd_text(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    version: int,
    edited_text: str,
) -> str:
    """effect_* 节点：把 HR 编辑过的 JD 正文写回画像，独占、幂等。

    **标识保护在这里落地**（合规红线：AI 生成的 JD 须带标识）：⛔ 不检查用户有
    没有删标识——检查就有绕过空间（改一个字、换个标点、插一行空白都能骗过检查）。
    这里无条件把提交上来的整段文本当作正文，剥干净再重贴唯一一行标识。

    唯一的例外是已经走过「标记为人工撰写」的文案：那份文案的作者已经是人，
    再贴 AI 标识就是**另一个方向上的错误标识**，所以只剥不贴。

    business_key = `{version}:{正文哈希}`（jd_edit_business_key）。

    ⛔ 只改 profile_json 里以下划线开头的内部键，不动业务字段、不动 status、
    不新建版本（design.md 决策 4：画像冻结后不可变）。

    不在这里 conn.commit() —— 理由同 app/graph/nodes.py 的 effect_persist_draft。
    """
    profile = _load_profile(conn, job_id=thread_id, version=version)

    if profile.get(JD_AUTHORSHIP_KEY):
        final_text = strip_ai_label(edited_text)
    else:
        generated_at = (
            extract_label_generated_at(profile[JD_TEXT_KEY]) or UNKNOWN_GENERATED_AT
        )
        final_text = enforce_ai_label(edited_text, generated_at=generated_at)

    profile[JD_TEXT_KEY] = final_text
    _save_profile(conn, job_id=thread_id, version=version, profile=profile)
    return final_text


@idempotent_effect("effect_mark_jd_human_written")
def effect_mark_jd_human_written(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    version: int,
    reviewer: str,
    marked_at: str,
) -> str:
    """effect_* 节点：把这一版 JD 标记为人工撰写并去掉 AI 标识，独占、幂等。

    **这是唯一一条能去掉 AI 标识的路径**（job-description spec：「若 HR 大幅
    改写文案，系统提供"标记为人工撰写"的显式操作，该操作被记录」）。去标识与
    留痕写在**同一行的同一次 UPDATE** 里，于是"标识没了但查不到谁去的"在结构上
    不可能发生——这比任何检查都可靠。

    business_key = 被标记的画像 version。重复标记命中 effect_log 直接短路，
    留痕里保留**第一个**按下按钮的人：第一个做这个决定的人才是决策人。

    ⛔ reviewer 不接受空白，⛔ 更不得写入任何自动判定的产物（合规红线：
    决策人只能是人）。与 app/graph/nodes.py 的 _record_human_review 同一条规矩。

    ⛔ 不写 human_review 表：那张表的 decision_type CHECK 只认
    approved/revision_requested/abandoned 三个值，且这三个字面量与
    app/graph/nodes.py 的 DECISION_* 常量、app/audit/assertions.py 的
    TERMINAL_STATUS_DECISIONS 逐字同源；SQLite 又改不了已有表的 CHECK，
    .51 上的老库会静默保留旧约束。留痕走 profile_json 的内部键，与
    `_gap_acknowledgement` 同一条路（design.md 决策 8：走内部键，不建新表）。

    不在这里 conn.commit() —— 理由同 effect_update_jd_text。
    """
    if not str(reviewer).strip():
        raise ValueError(
            "标记为人工撰写必须记下是谁标的（合规红线：决策人只能是人）；"
            "⛔ 不得用系统默认值或自动判定结果顶替"
        )

    profile = _load_profile(conn, job_id=thread_id, version=version)
    final_text = strip_ai_label(profile[JD_TEXT_KEY])

    profile[JD_TEXT_KEY] = final_text
    profile[JD_AUTHORSHIP_KEY] = {
        "human_written": True,
        "marked_by": reviewer,
        "at": marked_at,
    }
    _save_profile(conn, job_id=thread_id, version=version, profile=profile)
    return final_text
