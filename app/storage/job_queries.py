from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from app.schemas.job_profile import field_labels, summarize_profile

# ─────────────────────────────────────────────────────────────────────────────
# 会话之外三个只读视图的查询层（m1-job-profile-intake tasks 8.1 / 8.2 / 8.4）。
#
# ⛔ 本模块只读：一条 INSERT / UPDATE / DELETE / ALTER / DROP 都不许有。三个
#    视图都是把已经落库的事实读出来摆好，加一条写入就越过了本交付单元的边界，
#    也会让"这几个页面可以放心给业务经理点"这个前提不再成立。
#    机器判据见 tests/test_job_queries.py::test_module_contains_no_write_statements。
#
# ⛔ 本模块不 import app.graph / app.agents：app/graph/nodes.py 已经 import 了
#    app/storage/idempotency.py，反向再导一次就是层次倒置。需要 graph 层的常量
#    （MAX_REVISIONS、DECISION_REVISION_REQUESTED）时由调用方 app/web/server.py
#    当参数传进来——它本来就 import 着这两个名字。⛔ 也不在这里重抄字面量：
#    重抄就多一个会漂移的真源，而漂移没有任何症状，只是队列的上限判定悄悄和
#    revise() 的判定对不上。
# ─────────────────────────────────────────────────────────────────────────────


# 转人工的三个理由码。与前端 index.html 无耦合（前端只渲染 label，不认 code），
# code 存在是为了让测试与将来的过滤按稳定标识来写，而不是按会改的中文文案。
REASON_JOB_STATUS = "job_status"
REASON_JD_DISCRIMINATION = "jd_discrimination"
REASON_REVISION_LIMIT = "revision_limit"

# 英文 status → 中文标签。⛔ 界面上不得出现英文 snake_case 或英文 status
# （与 index.html:211-213 既有约束同源，那正是第 6 章修过一遍的故障现象）。
# 兜底返回原值而不是抛异常：出现未登记的取值时，界面上会显示一个刺眼的英文串，
# 这是**可见**的故障；抛异常则会让整个详情页白屏，把一个显示问题升级成不可用。
_PROFILE_STATUS_LABELS = {
    "drafting": "草案",
    "approved": "已确认",
    "abandoned": "已放弃",
}

# 与 app/graph/nodes.py 的 DECISION_* 常量、app/storage/db.py 的
# human_review.decision_type CHECK 逐字同源（那三处已经互为同源，这里是第四处
# **只读**的消费方）。⛔ 不要在这里新增取值——新增取值的正确做法是先改那三处。
_DECISION_LABELS = {
    "approved": "确认",
    "revision_requested": "要求修改",
    "abandoned": "放弃",
}

# app/middleware/auth.py::UNKNOWN_REVIEWER 的中文展示文案（决策人未知时的
# 诚实标记，鉴权空壳阶段每条留痕都会命中）。⛔ 不在这里重抄 "unknown:web-
# session" 这个字面量——调用方（app/web/server.py）本来就 import 着
# UNKNOWN_REVIEWER，按 unknown_reviewer 参数传进来，与 MAX_REVISIONS /
# DECISION_REVISION_REQUESTED 同一条纪律（真源只有一处，重抄会漂移）。
_UNKNOWN_REVIEWER_LABEL = "未登录（演示环境）"

# 落库时刻的真源一律是 SQLite datetime('now')（app/storage/db.py:276
# sqlite_utc_now()）——UTC、秒级、无时区后缀。⛔ 前端不做时区加减：
# to_shanghai_label() 是唯一的转换点。
_UTC_ZONE = ZoneInfo("UTC")
_SHANGHAI_ZONE = ZoneInfo("Asia/Shanghai")
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def to_shanghai_label(utc_timestamp: str | None) -> str | None:
    """把裸 UTC 时间戳转成东八区可读文案（"裸值给逻辑、label 给显示"）。

    无锡的人打开列表/详情/队列看到的每一个时间戳都源自这里——不转换的话
    显示的是早 8 小时的时间，且不报错、不失败，只是每一个时间都错
    （final review I-1）。

    NULL / 空串原样返回：调用方各自决定空值文案，这里只负责"有值就转好"。
    格式解析失败（脏数据、未来格式变更）时同样原样返回裸值——⛔ 不让一条
    格式异常的历史行把整页拖成 500，返回一个看得出没转换的裸值好过白屏。
    """
    if not utc_timestamp:
        return utc_timestamp
    try:
        naive = datetime.strptime(utc_timestamp, _TIMESTAMP_FORMAT)
    except (TypeError, ValueError):
        return utc_timestamp
    localized = naive.replace(tzinfo=_UTC_ZONE).astimezone(_SHANGHAI_ZONE)
    return localized.strftime(_TIMESTAMP_FORMAT)


def _loads_list(raw: str | None) -> list:
    """JSON 列列的容错读取。

    .51 上 2026-08-19 之前写的历史行这些列可能是 NULL（加列时给的是 '[]'，
    但更早的行经由别的路径写入过 NULL）。列表页不能因为一行历史数据整页 500。
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def latest_profile_rows(conn: sqlite3.Connection) -> list[dict]:
    """每个 job 一行：job 表字段 + 该 job 版本号最大的那一版 job_profile。

    LEFT JOIN 而不是 INNER JOIN：POST /api/jobs 先 INSERT job 再跑第一轮
    （app/web/server.py 的 create_job），第一轮如果失败，库里就留下一个
    "有 job、没有任何 job_profile"的行。INNER JOIN 会让这种 job 在列表里彻底
    消失——而它恰恰是最需要被人看见的那一种（"我提过一个需求，怎么找不到了"）。

    排序按"最近有动静"倒序：列表回答的是"最近发生了什么"。队列的排序刻意相反，
    见 app/web/server.py 的 needs_manual_queue()。
    """
    rows = conn.execute(
        "SELECT j.id, j.title, j.status, j.created_at, "
        "       p.version, p.status, p.created_at, p.profile_json "
        "FROM job j "
        "LEFT JOIN job_profile p "
        "       ON p.job_id = j.id "
        "      AND p.version = (SELECT MAX(v.version) FROM job_profile v WHERE v.job_id = j.id) "
        "ORDER BY COALESCE(p.created_at, j.created_at) DESC, j.id DESC"
    ).fetchall()

    result: list[dict] = []
    for row in rows:
        profile = {}
        if row[7]:
            try:
                loaded = json.loads(row[7])
                profile = loaded if isinstance(loaded, dict) else {}
            except (TypeError, ValueError):
                profile = {}
        result.append(
            {
                "job_id": row[0],
                "title": row[1],
                "status": row[2],
                "created_at": row[3],
                "created_at_label": to_shanghai_label(row[3]),
                "latest_version": row[4],
                "latest_profile_status": row[5],
                "updated_at": row[6] or row[3],
                "updated_at_label": to_shanghai_label(row[6] or row[3]),
                "profile": profile,
            }
        )
    return result


def revision_counts(conn: sqlite3.Connection, *, revision_decision_type: str) -> dict[str, int]:
    """每个 job 的修改次数。真源是 human_review 行数，与
    app/graph/nodes.py::revision_count 同一口径——⛔ 不另存计数列。

    一次聚合查询覆盖全部 job，⛔ 不在列表里逐个 job 调 revision_count()：
    那是 N+1，列表页一打开就是几十次查询。
    """
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT job_id, COUNT(*) FROM human_review WHERE decision_type = ? GROUP BY job_id",
            (revision_decision_type,),
        )
    }


def latest_message_types(conn: sqlite3.Connection) -> dict[str, str]:
    """每个 thread 最后一条 outbox 消息的类型（用来区分"追问中"与"等你确认"）。

    同样是一次聚合，⛔ 不逐个 job 调 WebChannel.latest()。
    """
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT o.thread_id, o.message_type FROM outbox o "
            "WHERE o.id = (SELECT MAX(x.id) FROM outbox x WHERE x.thread_id = o.thread_id)"
        )
    }


def display_title(row: dict) -> str:
    """列表里显示的岗位名。

    真源是**最新一版画像里的 job_title**，⛔ 不是 job.title：job.title 在
    create_job 里被写死成 '待确定'（app/web/server.py:268），此后没有任何代码
    更新它（全仓库只有两条 UPDATE job SET，都只改 status）。拿 job.title 当
    标题，整个列表会是一列一模一样的「待确定」。

    ⛔ 也不要顺手把画像标题回写进 job.title —— 本单元只读。
    """
    title = row.get("profile", {}).get("job_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return row["title"]


def jd_state(profile: dict) -> dict:
    """这一版画像的 JD 状态。三个键都读 profile_json 里的下划线内部键
    （交付单元 7 确立的做法：走内部键，不建新表）。

    ⛔ 只回布尔，不回 JD 正文：详情页不渲染正文（Global Constraints 第 5 条），
    正文有专门的、合规上已过审的展示位 GET /api/jobs/{id}/jd。
    """
    return {
        "generated": bool(profile.get("_jd_text")),
        "needs_manual": bool(profile.get("_jd_needs_manual")),
        "human_written": bool(profile.get("_jd_authorship")),
    }


def derive_needs_manual_reasons(
    *,
    job_status: str,
    profile: dict,
    revision_count: int,
    max_revisions: int,
) -> list[dict]:
    """这个岗位为什么需要 HR 人工介入。空列表 = 不需要。

    ⚠️ 队列是**推导出来的视图**，不是一个状态列。JobStatus.NEEDS_MANUAL
    （app/schemas/job_profile.py:16）至今没有任何写入方（WBS 2.5 未做），
    只认它的话队列永远是空的——而"空队列"和"没人需要处理"在界面上长得一模
    一样，这是一个没有任何症状的故障。三个来源都查：

      1) job.status = 'needs_manual'：今天恒为空，2.5 落地当天自动生效。
         ⛔ 不要因为"现在查不到"就省掉这一条
      2) profile_json._jd_needs_manual：JD 连续 2 次触发歧视性表述检测后由
         app/graph/nodes.py 的 effect_generate_and_persist_jd 落库
         （今天唯一真实存在的写入方）
      3) 修改次数达上限：spec「修改次数上限」要求"提示转人工"，而
         app/web/server.py 的 revise() 只在那一次 409 响应里说了一句话，
         页面一关就没了。这里把它变成一条查得到的事实

    ⛔ "放弃（abandoned）的岗位不进队列"这条过滤**不在本函数里**做：本函数只
    回答"有哪些理由"，"要不要进队列"是队列端点的事——理由的产出与"要不要
    据此进队列"是两件事，混在一起的话，将来任何一处只想单独调整过滤策略
    （比如只在队列页收紧、别处仍然展示）都会牵连到这个函数本身。

    ⚠️ 上限理由（revision_limit）例外：这一条**不对 approved 状态的岗位生成**。
    修改上限只在"还能继续修改"这件事上有意义——revise() 对 approved 直接
    409，此时 revision_counts 早已定格。理由的文案是"请由 HR 直接编辑画像
    后提交确认"，而画像一旦 approved 就是**一件已经做完的事**：继续产出
    这条理由会让这类岗位永久钉在转人工队列里、没有任何路径能清掉
    （final review I-2）。⛔ 这条例外只挡 revision_limit 一条理由，不整体
    排除 approved——`_jd_needs_manual` 恰恰只出现在 approved 岗位上，那是
    今天队列里唯一真实的写入方。
    """
    reasons: list[dict] = []
    if job_status == "needs_manual":
        reasons.append({"code": REASON_JOB_STATUS, "label": "岗位状态已被置为「转人工」"})
    if profile.get("_jd_needs_manual"):
        reasons.append(
            {
                "code": REASON_JD_DISCRIMINATION,
                "label": "JD 连续 2 次触发歧视性表述检测，已转人工；请核对文案后再发布",
            }
        )
    if revision_count >= max_revisions and job_status != "approved":
        reasons.append(
            {
                "code": REASON_REVISION_LIMIT,
                # 上限数字从入参渲染，⛔ 不在文案里写死：写死之后改 MAX_REVISIONS
                # 界面上不会跟着变，而且不报错——业务经理看到的上限和系统实际
                # 执行的上限会悄悄不一致。
                "label": f"画像修改已达上限 {max_revisions} 次，请由 HR 直接编辑画像后提交确认",
            }
        )
    return reasons


def stage_label(
    *,
    job_status: str,
    latest_version: int | None,
    latest_message_type: str | None,
    jd: dict,
) -> str:
    """列表里那一列中文状态。⛔ 不把英文 status 直接摆给业务经理看。

    判定顺序即优先级：终态压过过程态。一个已放弃的岗位即使最后一条消息是
    confirmation_prompt，显示的也必须是「已放弃」——否则界面会在邀请人去点
    一个服务端已经用 409 挡死的按钮。
    """
    if job_status == "abandoned":
        return "已放弃"
    if job_status == "needs_manual":
        return "待人工处理"
    if job_status == "approved":
        return "已确认 · JD 已生成" if jd["generated"] else "已确认"
    if latest_version is None:
        return "刚发起，还没有画像"
    if latest_message_type == "confirmation_prompt":
        return "等你确认"
    return "追问中"


def profile_versions(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """一个岗位的完整版本历史，按 version 升序，每版带一份生成快照。

    ⚠️ 缺口读的是 derived_unspecified_fields 这一列，**不是** unspecified_fields：
    后者存的是"模型自称的"，已降级为对照（app/storage/db.py:31-33 的注释与
    docs/tech-debt.md TD-2）。读错列会让详情页显示一份和确认页不一致的缺口清单，
    而两边都不报错。

    ⚠️ 快照里的模型标识取 llm_response_model（API 响应实际返回的 model 字段，
    工程铁律 5 的落点），⛔ 不取配置里写的 settings.llm_model——那正是这条铁律
    要区分开的两个东西。
    """
    rows = conn.execute(
        "SELECT version, status, created_at, is_productive, derived_unspecified_fields, "
        "       ungrounded_fields, written_fields, llm_response_model, llm_latency_ms, "
        "       turn_started_at, asked_questions, profile_json "
        "FROM job_profile WHERE job_id = ? ORDER BY version ASC",
        (job_id,),
    ).fetchall()

    versions: list[dict] = []
    for row in rows:
        try:
            loaded = json.loads(row[11])
            profile = loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            profile = {}
        unspecified = [name for name in _loads_list(row[4]) if isinstance(name, str)]
        ungrounded = [name for name in _loads_list(row[5]) if isinstance(name, str)]
        versions.append(
            {
                "version": row[0],
                "status": row[1],
                "status_label": _PROFILE_STATUS_LABELS.get(row[1], row[1]),
                "created_at": row[2],
                "created_at_label": to_shanghai_label(row[2]),
                "is_productive": bool(row[3]),
                "unspecified_fields": unspecified,
                "unspecified_field_labels": field_labels(unspecified),
                "asked_question_count": len(_loads_list(row[10])),
                # summarize_profile 按 FIELD_LABELS 声明序只输出有值的业务字段，
                # profile_json 里以下划线开头的内部键（_jd_text /
                # _gap_acknowledgement / _jd_authorship）天然不在其中。
                "summary": summarize_profile(profile),
                "snapshot": {
                    "llm_response_model": row[7],
                    "llm_latency_ms": row[8],
                    "turn_started_at": row[9],
                    "completed_at": row[2],
                    "ungrounded_fields": _loads_list(row[5]),
                    # ⛔ ungrounded_fields 本身是英文 snake_case 业务字段名
                    # （app/graph/state.py:83-85），只给逻辑用；界面渲染走这个
                    # 中文对应字段，与上面 unspecified_field_labels 同一条纪律
                    # （两处都是 field_labels() 翻的同一份 FIELD_LABELS）。
                    "ungrounded_field_labels": field_labels(ungrounded),
                    "written_fields": _loads_list(row[6]),
                },
                "jd": jd_state(profile),
            }
        )
    return versions


def decision_records(
    conn: sqlite3.Connection, job_id: str, *, unknown_reviewer: str
) -> list[dict]:
    """一个岗位的人工决策留痕（spec「决策留痕」：谁、什么时候、决定了哪一版）。

    ⛔ 只读 human_review，不做任何补写。查不到记录时返回空列表——"这个岗位
    还没有人做过决策"和"留痕漏了"由 app/audit/assertions.py 的断言四去区分，
    ⛔ 不在展示层替它下结论。

    unknown_reviewer 由调用方（app/web/server.py）传入
    app.middleware.auth.UNKNOWN_REVIEWER：鉴权是空壳，今天每条留痕的
    reviewer 恒为这个标识，直接展示给业务经理看是一串没有意义的英文
    （"决策人 unknown:web-session"）。reviewer_label 把它映射成中文；
    真实值不是这个标识时原样展示（SSO 落地后就是企微 userid）。
    """
    rows = conn.execute(
        "SELECT profile_version, decision_type, reviewer, feedback, decided_at "
        "FROM human_review WHERE job_id = ? ORDER BY decided_at ASC, profile_version ASC",
        (job_id,),
    ).fetchall()
    return [
        {
            "profile_version": row[0],
            "decision_type": row[1],
            "decision_label": _DECISION_LABELS.get(row[1], row[1]),
            "reviewer": row[2],
            "reviewer_label": (
                _UNKNOWN_REVIEWER_LABEL if row[2] == unknown_reviewer else row[2]
            ),
            "feedback": row[3],
            "decided_at": row[4],
            "decided_at_label": to_shanghai_label(row[4]),
        }
        for row in rows
    ]
