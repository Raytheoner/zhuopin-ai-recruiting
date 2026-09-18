from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, NamedTuple

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from app.agents.intake_agent import derive_unspecified_fields
from app.agents.intake_question import normalize_question_payload
from app.agents.jd_grounding import verify_jd_grounding
from app.agents.resume_parser import PARSE_PROMPT_VERSION, compute_parse
from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.jd_nodes import (
    JDNotGeneratedError,
    effect_mark_jd_human_written,
    effect_update_jd_text,
    jd_edit_business_key,
)
from app.graph.nodes import (
    DECISION_REVISION_REQUESTED,
    MAX_REVISIONS,
    effect_abandon_profile,
    effect_confirm_profile,
    effect_generate_and_persist_jd,
    effect_request_revision,
    revision_count,
)
from app.graph.resume_nodes import effect_persist_parse, queue_reapplication_screening, record_resume_access
from app.graph.screening_nodes import latest_approved_profile_version, screen_and_persist
from app.middleware.auth import AuthMiddleware, UNKNOWN_REVIEWER, reviewer_of
from app.observability.logging_config import logging_status
from app.observability.middleware import (
    RequestIdMiddleware,
    unhandled_exception_handler,
)
from app.parsing.extract_text import SUPPORTED_SUFFIXES
from app.parsing.resume_ingest import ingest_resume_text
from app.parsing.spans import TextSpan
from app.schemas.job_profile import JobProfile, field_label, field_labels
from app.schemas.resume_fields import FIELD_LABELS, FIELD_NAMES
from app.storage import job_queries
from app.storage.auth_session import create_session, delete_session
from app.storage.db import get_connection, init_schema, sqlite_utc_now
from app.storage.hr_account import verify_password
from app.storage.job_discard import discard_thread_checkpoints, discard_unstarted_job
from app.storage.live_resume_gate import is_live_resume_intake_enabled

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
INDEX_TEMPLATE_PATH = STATIC_DIR / "index.html"


class LoginRequest(BaseModel):
    # ⚠️ 必须是模块级类，不能嵌进 create_app() 内部：本文件顶部有
    # `from __future__ import annotations`，函数签名注解一律延迟求值成字符串，
    # FastAPI 解析 `req: LoginRequest` 时若 LoginRequest 只存在于某次调用的
    # 函数局部命名空间里，get_type_hints() 解不出这个名字，会静默把它当成
    # 查询参数处理而不是请求体——login 路由因此对着一个不存在的 query
    # 字段返回 422，而不是按预期校验请求体。
    username: str
    password: str


class CreateJobRequest(BaseModel):
    message: str


class ReplyRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    # 缺省 false = 未知情 = 不放行。⛔ 绝不能缺省 true：那等于系统替业务经理
    # 做了"我知道有缺口"这个声明（合规红线：人工确认节点必须是真的人在确认）。
    acknowledged_gaps: bool = False


class ReviseRequest(BaseModel):
    # 业务经理"以自然语言描述要改什么"（spec Scenario：提出修改意见）。
    feedback: str


class AbandonRequest(BaseModel):
    # ⛔ 可选，不强制填：强制填理由的表单只会得到"1"和"。"。留痕的必填项是
    # 决策人 / 决策类型 / 时间 / 画像版本，理由是加分项。
    reason: str | None = None


class JDEditRequest(BaseModel):
    # HR 编辑后的**完整**文案正文。服务端会剥掉其中任何 AI 标识行再重贴一行
    # 唯一的标识——⛔ 前端不要自己拼标识，也不要指望这里原样保存。
    text: str


class FieldReviewRequest(BaseModel):
    human_value: str


class TurnOutcome(NamedTuple):
    """一轮采集的结果：给通道的消息 + L3 判定的"这是不是用人需求"。

    is_job_related 是 **L3 纯函数 run_intake_turn 的输出**，经 compute_intake_turn
    放进 state、由 graph.invoke() 的终态原样带回来。编排层只按它分流，
    ⛔ 不在 server 里再调一次模型做二次判断（tasks 5.3 的硬边界）——两个
    判定器迟早会给出不同答案，而分歧没有任何症状。
    """

    message: dict
    is_job_related: bool


def _substitute_base_href(html: str, root_path: str) -> str:
    """把 <!--BASE_HREF--> 占位符换成真实 <base href>，让前端相对路径请求
    在任意挂载前缀下都能解析到正确的地址。root_path="" 时挂域根。

    这是 5 个静态页面路由（index/login/upload/resume_list/resume_review）
    共用的唯一替换点（finding 4）：Task 2 review 时标注"第 4 个页面出现时才
    值得抽"，Task 3/4 分别新增了第 4、5 个，触发条件已满足。纯重构，行为与
    抽取前逐字一致。
    """
    base_href = f"{root_path}/" if root_path else "/"
    return html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">')


def _render_index(root_path: str) -> str:
    html = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    return _substitute_base_href(html, root_path)


def _render_static_page(filename: str, root_path: str) -> HTMLResponse:
    """`STATIC_DIR/filename` 读文件 + 替换 <!--BASE_HREF--> + 包装 HTMLResponse。"""
    html = (STATIC_DIR / filename).read_text(encoding="utf-8")
    return HTMLResponse(_substitute_base_href(html, root_path))


def create_app(
    *,
    db_path: str,
    gateway_factory: Callable,
    root_path: str = "",
    resume_storage_dir: str | None = None,
) -> FastAPI:
    conn = get_connection(db_path)
    init_schema(conn)

    # 提前拉入的最小 constructor 装配（本任务只做到"目录存在"，上传路由与解析
    # 摄取逻辑属于后续任务范围）：不传时落回 Settings.resume_storage_dir 的
    # 默认值，测试可用 tmp_path 显式覆盖，避免把上传文件真的写进仓库工作区。
    from app.config import get_settings

    _resume_storage_dir = Path(resume_storage_dir or get_settings().resume_storage_dir)
    _resume_storage_dir.mkdir(parents=True, exist_ok=True)

    channel = WebChannel(conn)

    # gateway 与 graph 的构造从"每次请求一次"上提到"应用启动一次"，与 conn/
    # channel 的现有生命周期对齐。方向 A 让 build_intake_graph() 内部为
    # checkpointer 开一个独立连接（app/graph/build.py）后，如果每次请求都
    # 重新调用 build_intake_graph()，这个独立连接会每请求泄漏一个——LLMGateway
    # 本身是无状态的配置+client 包装（app/llm/gateway.py），复用是安全的；
    # 图对象也是无状态可重入的，不同 job_id（LangGraph 的 thread_id）之间由
    # checkpointer 按 thread_id 分区，复用同一个编译好的图不会造成跨 job 串扰。
    gateway = gateway_factory()
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        yield
        # 应用正常关闭时显式释放 checkpointer 的独立连接（设计要求：进程
        # 正常退出与异常退出都不遗留未关闭连接）。conn 本身继续沿用现有代码
        # 一直以来的做法——不显式关闭，随进程退出释放（Windows 计划任务场景
        # 下与部署约束4一致，SYSTEM 账户进程退出即释放所有句柄）。
        graph.checkpointer.conn.close()

    app = FastAPI(title="卓品智能招聘助手 · Demo", lifespan=_lifespan)
    app.add_middleware(AuthMiddleware, conn=conn, root_path=root_path)
    # 后 add 的更靠外：RequestIdMiddleware 必须包住 AuthMiddleware，
    # 否则鉴权层自己产生的日志与异常拿不到请求标识。
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    router = APIRouter()

    @router.post("/api/auth/login")
    def login(req: LoginRequest, response: Response):
        row = conn.execute(
            "SELECT id, password_hash, password_salt FROM hr_account WHERE username = ?",
            (req.username.strip(),),
        ).fetchone()
        if row is None or not verify_password(req.password, row[1], row[2]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = create_session(conn, hr_account_id=row[0])
        response.set_cookie(
            "hr_session",
            token,
            httponly=True,
            samesite="lax",
            path=root_path or "/",
        )
        return {"ok": True}

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get("hr_session")
        if token:
            delete_session(conn, token)
        response.delete_cookie("hr_session", path=root_path or "/")
        return {"ok": True}

    @router.get("/login")
    def login_page():
        return _render_static_page("login.html", root_path)

    @router.get("/resumes/upload")
    def upload_page():
        return _render_static_page("upload.html", root_path)

    @router.get("/jobs/{job_id}/resumes")
    def resume_list_page(job_id: str):
        return _render_static_page("resume_list.html", root_path)

    @router.get("/resumes/{resume_id}/review")
    def resume_review_page(resume_id: str):
        return _render_static_page("resume_review.html", root_path)

    def _response_payload(message) -> dict:
        """
        对外响应统一过一遍归一化。为什么必须在读的这一侧做：outbox 里存着
        2026-08-18 及之前写下的 {"questions": ["裸字符串"]}（.51 现网 15 个 job
        的历史行），新前端按对象访问会直接崩。归一化是幂等的，新行过一遍不变。
        """
        if message.type != "question":
            return message.payload
        return normalize_question_payload(message.payload)

    def _run_turn(job_id: str, message: str) -> TurnOutcome:
        # 轮次起始时刻在这里打，不在 compute 节点里打：那才是"用户开始等"
        # 的时刻，节点里打会漏掉下面几次取数的时间。格式与 job_profile
        # .created_at 的 datetime('now') 完全一致（见 sqlite_utc_now）。
        turn_started_at = sqlite_utc_now()
        profile_row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        accumulated = json.loads(profile_row[0]) if profile_row else {}
        round_count = conn.execute(
            "SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()[0]

        # 预算的第二个口径：只数有产出的轮次（design.md 决策 5）。空转轮不
        # 消耗 MAX_ROUNDS，但仍然占 round_count，因此仍受 MAX_TOTAL_ROUNDS 约束。
        productive_round_count = conn.execute(
            "SELECT COUNT(*) FROM job_profile WHERE job_id=? AND is_productive=1", (job_id,)
        ).fetchone()[0]

        # 已问台账：全部轮次的 question_id 并集 + 上一轮问出的问题原样读回。
        # 一次查询取两样东西，按 version 升序，最后一行就是上一轮。
        asked_rows = conn.execute(
            "SELECT asked_questions FROM job_profile WHERE job_id=? ORDER BY version ASC",
            (job_id,),
        ).fetchall()
        asked_question_ids_before: list[str] = []
        previous_questions: list[dict] = []
        asked_question_rounds: list[list[dict]] = []
        for (raw,) in asked_rows:
            # 历史行（.51 上 2026-08-19 之前写的）这一列是默认值 '[]'；老库补列
            # 时也拿到 '[]'。两条路径都不需要回填。
            payloads = json.loads(raw or "[]")
            # 按轮保留一份：第 5 章的重问台账要知道"这个子问题出现在几轮里"，
            # 拍平后的并集算不出次数。空轮也要占一项，轮次下标才对得上。
            asked_question_rounds.append(payloads)
            previous_questions = payloads
            for payload in payloads:
                question_id = payload.get("question_id")
                if question_id and question_id not in asked_question_ids_before:
                    asked_question_ids_before.append(question_id)

        # 对话历史和画像、轮次一样从库里读回完整的一份，再追加本轮新消息。
        # 修复前这里只塞了本轮消息（history=[{本轮}]），而 IntakeState.history
        # 没有 reducer、LangGraph 按 LastValue 覆盖 checkpoint 里的旧值——第二轮起
        # 模型只看得到最新一句话，既不知道最初的用人需求，也不知道上一轮问过什么，
        # 每轮都是冷启动（review Critical 发现1）。
        conversation_row = conn.execute(
            "SELECT history_json FROM conversation WHERE thread_id=?", (job_id,)
        ).fetchone()
        prior_history = json.loads(conversation_row[0]) if conversation_row else []

        state = {
            "job_id": job_id,
            "history": [*prior_history, {"role": "user", "content": message}],
            "round_count": round_count,
            "productive_round_count": productive_round_count,
            "profile_patch_accumulated": accumulated,
            "asked_question_ids_before": asked_question_ids_before,
            "previous_questions": previous_questions,
            "asked_question_rounds": asked_question_rounds,
            "turn_started_at": turn_started_at,
        }
        # 终态要接住，⛔ 不要再丢掉：is_job_related 只有这一个合法来源。
        final_state = graph.invoke(state, config={"configurable": {"thread_id": job_id}})

        latest = channel.latest(job_id)
        return TurnOutcome(
            message={"type": latest.type, "payload": _response_payload(latest)},
            # 默认 True：判定没接上时按"是用人需求"算，与 compute 节点里
            # is_productive 的默认口径一致——保守方向是**保留**记录，
            # 不是悄悄删掉一个真实岗位。
            is_job_related=bool(final_state.get("is_job_related", True)),
        )

    # 终态说明文案。⛔ 不要在这里写"请联系管理员"这类无动作的话——业务经理
    # 需要知道**下一步能做什么**，而不是知道自己撞墙了。
    _ABANDONED_DETAIL = "这个岗位已经放弃，内容保留但不再流转；如需重开请新建一个岗位。"
    _APPROVED_DETAIL = (
        "这个岗位的画像已经确认冻结，不能再修改；"
        "如需变更请新建一个岗位（画像冻结后不可原地修改，改动一律走新版本）。"
    )

    def _job_status(job_id: str) -> str:
        row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        return row[0]

    def _reject_if_abandoned(job_id: str) -> None:
        """放弃是终态：⛔ 不允许继续作答、也不允许再确认。

        少了这道守卫，一个已放弃的岗位可以被 POST /reply 复活、再被确认——
        "放弃"就变成了一个只影响显示的标签，而 human_review 里那条 abandoned
        留痕会与最终 approved 的状态直接矛盾。
        """
        if _job_status(job_id) == "abandoned":
            raise HTTPException(status_code=409, detail=_ABANDONED_DETAIL)

    def _latest_version_or_404(job_id: str) -> int:
        row = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise HTTPException(status_code=404, detail="job not found")
        return int(row[0])

    def _jd_payload(job_id: str, version: int) -> dict:
        """JD 的统一回执。confirm / GET / 编辑 / 标记四个出口共用同一个形状。

        **溯源清单在这里现算，⛔ 不落库。** verify_jd_grounding 是确定性纯函数，
        两个入参（文案与画像字段）都在同一行 profile_json 里，重算与读缓存必然
        同值；而落库要改 app/graph/nodes.py 的既有节点 effect_generate_and_persist_jd，
        那超出本交付单元的边界。附带的好处是 JD 被编辑之后清单自动跟着变——
        缓存反而会在这里过期。

        ⛔ 清单只是观测（design.md 决策 12），本函数与它的调用方都不得据此拦截、
        重生成或降级。
        """
        row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
            (job_id, version),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        persisted = json.loads(row[0])

        jd_text = persisted.get("_jd_text")
        if jd_text is None:
            raise HTTPException(
                status_code=409, detail="这个岗位还没有生成 JD，请先确认画像"
            )

        authorship = persisted.get("_jd_authorship")
        return {
            "job_id": job_id,
            "version": version,
            "jd_text": jd_text,
            "needs_manual": persisted.get("_jd_needs_manual", False),
            "human_written": bool(authorship),
            "authorship": authorship,
            "ungrounded_terms": verify_jd_grounding(jd_text, persisted),
        }

    @router.post("/api/jobs")
    def create_job(req: CreateJobRequest):
        job_id = str(uuid.uuid4())
        # 先建行、后判定，是外键逼出来的顺序而不是选择：job_profile.job_id
        # 指向 job 且 PRAGMA foreign_keys=ON，而 is_job_related 要跑完 compute
        # 才知道——那时同一次 invoke 里的 effect_persist_draft 已经在写
        # job_profile 了。所以这里走"落后即删"（tasks 5.3 许可的形态之一）。
        conn.execute(
            "INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,)
        )
        conn.commit()
        outcome = _run_turn(job_id, req.message)

        if not outcome.is_job_related:
            # spec「需求描述为空或与招聘无关」：回引导语 **且不创建岗位记录**。
            # 消息在 _run_turn 里已经从 outbox 读出来了，删在后面不影响回执。
            #
            # ⚠️ 已知残留风险：INSERT 与这次删除分属两个事务（中间
            # graph.invoke 里的 idempotent_effect 必然提交），进程恰好崩在
            # 两者之间会留下一行零版本的 drafting job。这与今天"第一轮抛
            # 异常"留下的行是同一种，见 app/storage/job_queries.py 的
            # latest_profile_rows 注释。消除它要把建 job 行挪进
            # effect_persist_draft 的同一个事务，那要改 app/graph/nodes.py，
            # 超出本交付单元边界。⛔ 不要在这里加"定期清理僵尸行"的兜底
            # 逻辑掩盖它——那会把一个已登记的窗口变成一个隐形的窗口。
            discard_unstarted_job(conn, job_id)
            try:
                discard_thread_checkpoints(graph.checkpointer, job_id)
            except Exception:  # noqa: BLE001 —— 清理动作不能拖垮用户可见响应，方案 C（TD-13）
                logger.error(
                    "discard_thread_checkpoints 失败，job_id=%s；checkpoint 行留存，"
                    "不影响本次响应（清理是维护性动作，不是用户可见路径）",
                    job_id,
                    exc_info=True,
                )
            # 没有岗位就没有 id 可给。⛔ 不要回那个已删的 uuid：前端会拿它
            # 去 POST /reply，撞上 404，错误信息与真正的原因毫无关系。
            return {"job_id": None, "message": outcome.message}

        return {"job_id": job_id, "message": outcome.message}

    @router.post("/api/jobs/{job_id}/reply")
    def reply(job_id: str, req: ReplyRequest):
        job = conn.execute("SELECT id FROM job WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        _reject_if_abandoned(job_id)
        # 范围追加（控制器裁决，2026-09-04）：approved 也是终态，⛔ 不能再靠
        # /reply 悄悄产出一版新草案——那与 spec 6.4「确认后冻结」直接矛盾。
        # 用 confirm/revise 已有的同一个状态码与同一段文案（_APPROVED_DETAIL）。
        if _job_status(job_id) == "approved":
            raise HTTPException(status_code=409, detail=_APPROVED_DETAIL)
        message = _run_turn(job_id, req.message).message
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/confirm")
    def confirm(job_id: str, request: Request, req: ConfirmRequest | None = None):
        _reject_if_abandoned(job_id)
        row = conn.execute(
            "SELECT profile_json, status FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no profile draft yet")

        latest_message = channel.latest(job_id)
        if latest_message is None or latest_message.type != "confirmation_prompt":
            raise HTTPException(status_code=409, detail="画像还在追问中，未到可确认状态")

        profile_dict = json.loads(row[0])

        # tasks 6.7：缺口在确认这一刻现算，不读 state、不读上一轮写下的列。
        # 确认是一次独立、可重试的 HTTP 动作；依赖某一轮 state 的残留会让
        # "重试一次结论就变了"。
        gaps = derive_unspecified_fields(profile_dict)
        acknowledged = bool(req and req.acknowledged_gaps)
        if gaps and not acknowledged:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "这份画像还有未指定的内容，确认前请先选择：回去补答，或知道有缺口仍然确认",
                    "gaps": [{"field": name, "label": field_label(name)} for name in gaps],
                },
            )

        # tasks 6.9：知情留痕（design.md 决策 8，写 profile_json 的下划线内部键，
        # 不新建表）。无论有没有缺口都写一条——"确认时没有缺口"本身也是事后要能
        # 查到的事实，缺了它就分不清"当时没缺口"和"这条记录漏写了"。
        profile_dict = {
            **profile_dict,
            "_gap_acknowledgement": {
                "acknowledged": acknowledged,
                "had_gaps": bool(gaps),
                "fields": gaps,
                "labels": field_labels(gaps),
                "at": sqlite_utc_now(),
            },
        }

        version = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()[0]

        # 先校验、后落 approved：profile_patch 是 LLM 自由生成的裸 dict，到这一步
        # 才第一次撞上 JobProfile 的类型约束（例如 headcount 被写成 "两个人"、
        # functional_safety 被写成 "ASIL B"）。校验失败时如果画像已经被标成
        # approved，用人部门既拿不到 JD 又回不到追问状态，只能弃单重来。
        try:
            profile = JobProfile.model_validate(
                {
                    "job_title": profile_dict.get("job_title", "未命名岗位"),
                    "department": profile_dict.get("department", "未指定"),
                    "headcount": profile_dict.get("headcount", 1),
                    "education_requirement": profile_dict.get("education_requirement", "未指定"),
                    "experience_years": profile_dict.get("experience_years", "未指定"),
                    **{
                        k: v
                        for k, v in profile_dict.items()
                        if k
                        not in {
                            "job_title",
                            "department",
                            "headcount",
                            "education_requirement",
                            "experience_years",
                        }
                    },
                }
            )
        except ValidationError as exc:
            # 不让 ValidationError 裸奔成 500：这一刻正是业务经理点"确认"的时候，
            # 整条 demo 流程的高潮。返回 422 + 说清是哪个字段、期望什么，让人能
            # 补一句话重新确认，而不是看到一个白屏 500。
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "岗位画像字段不符合规范，无法确认；请补充或修正后重试",
                    "errors": [
                        {
                            "field": ".".join(str(part) for part in err["loc"]),
                            "reason": err["msg"],
                            "got": str(err.get("input")),
                        }
                        for err in exc.errors()
                    ],
                },
            ) from exc

        effect_confirm_profile(
            conn,
            thread_id=job_id,
            business_key=str(version),
            profile_dict=profile_dict,
            # 决策人由鉴权层给（部署约束 3 的空壳接入点）。SSO 落地后这里
            # 一行不改，reviewer_of 自动返回真实的企微 userid。
            reviewer=reviewer_of(request),
        )

        gateway = gateway_factory()
        # generate_jd() 是一次真实、有成本的 LLM 调用，必须像其他有副作用的节点
        # 一样独占一个幂等 effect（工程铁律1）——否则 POST .../confirm 被重试
        # （双击、客户端超时重发、反向代理重试）会重复触发生成，并且第二次的
        # （可能不同的）结果会静默覆盖第一次。business_key 复用 effect_confirm_profile
        # 的 version：同一个已确认版本的第二次调用在 idempotent_effect 内部直接
        # 短路，generate_jd() 根本不会被再次调用。
        effect_generate_and_persist_jd(
            conn,
            thread_id=job_id,
            business_key=str(version),
            gateway=gateway,
            profile=profile,
            profile_dict=profile_dict,
            version=version,
        )

        # 不能直接用 effect_generate_and_persist_jd() 的返回值：重放命中
        # effect_log 时 idempotent_effect 会短路返回 None（没有真的执行函数体）。
        # 无论是本次真跑了还是被短路了，profile_json 里此刻都已经是最终状态，
        # 统一从 _jd_payload() 读回去构造响应，两条路径读到的是同一份持久化结果。
        # 回执形状与 GET/编辑/标记三个端点完全一致，前端只写一套渲染逻辑。
        return _jd_payload(job_id, version)

    @router.post("/api/jobs/{job_id}/revise")
    def revise(job_id: str, request: Request, req: ReviseRequest):
        """修改分支（tasks 6.5 / 6.6）：记一笔留痕，然后把修改意见当作用户
        这一轮的原话重跑一次采集。

        ⛔ 留痕先于重跑，且顺序不可换：先跑再记的话，_run_turn 抛异常时这次
        修改就查不到了；先记再跑，_run_turn 失败后重试会命中同一个幂等键，
        留痕不重复、采集照常补上（自愈）。
        """
        status = _job_status(job_id)
        if status == "abandoned":
            raise HTTPException(status_code=409, detail=_ABANDONED_DETAIL)
        if status == "approved":
            raise HTTPException(status_code=409, detail=_APPROVED_DETAIL)

        row = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise HTTPException(status_code=404, detail="no profile draft yet")
        version = row[0]

        latest_message = channel.latest(job_id)
        if latest_message is None or latest_message.type != "confirmation_prompt":
            raise HTTPException(status_code=409, detail="画像还在追问中，直接回复即可，不必走修改")

        feedback = req.feedback.strip()
        if not feedback:
            raise HTTPException(
                status_code=422, detail="请写明要改什么，修改意见不能为空"
            )

        already = revision_count(conn, job_id)
        if already >= MAX_REVISIONS:
            # tasks 6.6：超限**不是失败**，是换一条路。⛔ 不在这里改任何状态：
            # needs_manual 队列是第 8 章的事，本单元不铺那条线。确认这条路
            # 仍然开着（spec：由 HR 直接编辑画像后提交确认）。
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"这个岗位的画像已经改过 {already} 次，达到上限 {MAX_REVISIONS} 次。"
                        "请由 HR 直接编辑画像后再提交确认。"
                    ),
                    "revision_count": already,
                    "max_revisions": MAX_REVISIONS,
                },
            )

        effect_request_revision(
            conn,
            thread_id=job_id,
            business_key=str(version),
            reviewer=reviewer_of(request),
            feedback=feedback,
        )
        # 重跑一轮采集。retry 语义与 POST /reply 完全一致（同一个 _run_turn）：
        # 重复提交会各自产生一版草案，这是既有行为，本单元不改。留痕那一半
        # 不受影响——它有幂等键，重复提交只记一条。
        message = _run_turn(job_id, feedback).message
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/abandon")
    def abandon(job_id: str, request: Request, req: AbandonRequest | None = None):
        """放弃分支（tasks 6.7）：置 abandoned，内容一字不改。

        ⛔ 这里刻意**没有**终态守卫：重复 POST 应当幂等地返回 200（双击、
        客户端超时重发都会打到这里），由 effect_abandon_profile 的幂等键短路。
        返回 409 会让一次无害的重试在业务经理眼里变成一个错误。
        """
        row = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise HTTPException(status_code=404, detail="no profile draft yet")

        reason = (req.reason or "").strip() if req else ""
        effect_abandon_profile(
            conn,
            thread_id=job_id,
            business_key=str(row[0]),
            reviewer=reviewer_of(request),
            feedback=reason or None,
        )
        return {"job_id": job_id, "status": "abandoned"}

    @router.get("/api/jobs/{job_id}/jd")
    def get_jd(job_id: str):
        return _jd_payload(job_id, _latest_version_or_404(job_id))

    @router.post("/api/jobs/{job_id}/jd")
    def edit_jd(job_id: str, req: JDEditRequest):
        """常规编辑（tasks 7.5）。

        ⛔ 这条路径**去不掉** AI 标识：effect_update_jd_text 无条件重贴。
        要去标识只有 POST /jd/human-written 一条路，且必须留痕。
        """
        _reject_if_abandoned(job_id)
        version = _latest_version_or_404(job_id)

        text = req.text.strip()
        if not text:
            # 空正文不是一次编辑，是一次误操作（多半是前端把 textarea 清空了）。
            # 放过去会得到一份只剩 AI 标识、没有正文的 JD，而 HR 看到的是"保存成功"。
            raise HTTPException(status_code=422, detail="文案正文不能为空")

        try:
            effect_update_jd_text(
                conn,
                thread_id=job_id,
                business_key=jd_edit_business_key(version, text),
                version=version,
                edited_text=text,
            )
        except JDNotGeneratedError as exc:
            raise HTTPException(
                status_code=409, detail="这个岗位还没有生成 JD，请先确认画像"
            ) from exc
        return _jd_payload(job_id, version)

    @router.post("/api/jobs/{job_id}/jd/human-written")
    def mark_jd_human_written(job_id: str, request: Request):
        """「标记为人工撰写」（tasks 7.5）——**唯一**能去掉 AI 标识的路径，且留痕。

        ⛔ 这里刻意**没有**终态守卫：重复 POST 应当幂等地返回 200（双击、
        客户端超时重发都会打到这里），由 effect_mark_jd_human_written 的幂等键
        短路，留痕里保留第一个按下按钮的人。理由同 abandon()。
        """
        _reject_if_abandoned(job_id)
        version = _latest_version_or_404(job_id)
        try:
            effect_mark_jd_human_written(
                conn,
                thread_id=job_id,
                business_key=str(version),
                version=version,
                # 决策人由鉴权层给（部署约束 3 的空壳接入点）。SSO 落地后这里
                # 一行不改，reviewer_of 自动返回真实的企微 userid。
                reviewer=reviewer_of(request),
                marked_at=sqlite_utc_now(),
            )
        except JDNotGeneratedError as exc:
            raise HTTPException(
                status_code=409, detail="这个岗位还没有生成 JD，请先确认画像"
            ) from exc
        return _jd_payload(job_id, version)

    # ── 会话之外的只读视图（tasks 8.1 / 8.2 / 8.4）────────────────────────
    #
    # ⛔ 这一段里三个端点全是 GET，且不许有别的：列表、详情、队列都只是把已经
    # 落库的事实读出来摆好。加写入就越过了本交付单元的边界，也会让"这几个页面
    # 可以放心给业务经理点"这个前提不再成立。
    #
    # ⚠️ MAX_REVISIONS 与 DECISION_REVISION_REQUESTED 由这里**传进**查询层，
    # 不在 app/storage/job_queries.py 里重抄：那两个名字的真源是
    # app/graph/nodes.py，而 storage 层 import graph 层是层次倒置
    # （graph 已经在 import storage/idempotency.py）。

    def _job_row_payload(row: dict, counts: dict, message_types: dict) -> dict:
        """列表与队列共用同一个行形状。两处各拼一份的话，将来加一个字段必然
        只加在其中一处，而两个页面显示不一致这件事没有测试会自己发现。"""
        profile = row["profile"]
        jd = job_queries.jd_state(profile)
        revisions = counts.get(row["job_id"], 0)
        reasons = job_queries.derive_needs_manual_reasons(
            job_status=row["status"],
            profile=profile,
            revision_count=revisions,
            max_revisions=MAX_REVISIONS,
        )
        return {
            "job_id": row["job_id"],
            "title": job_queries.display_title(row),
            "status": row["status"],
            "stage_label": job_queries.stage_label(
                job_status=row["status"],
                latest_version=row["latest_version"],
                latest_message_type=message_types.get(row["job_id"]),
                jd=jd,
            ),
            "created_at": row["created_at"],
            "created_at_label": row["created_at_label"],
            "updated_at": row["updated_at"],
            "updated_at_label": row["updated_at_label"],
            "latest_version": row["latest_version"],
            "revision_count": revisions,
            # ⛔ 只回 JD 的三个布尔状态，不回正文：正文有专门的、合规上已过审的
            # 展示位（GET /api/jobs/{job_id}/jd）。多一个渲染正文的地方就多一个
            # 会漏掉 AI 生成标识的地方。
            "jd": jd,
            "needs_manual": bool(reasons),
            "needs_manual_reasons": reasons,
        }

    def _job_rows_with_context() -> tuple[list[dict], dict, dict]:
        """列表与队列都要的三次查询。⛔ 不做逐 job 的 N+1 查询。"""
        return (
            job_queries.latest_profile_rows(conn),
            job_queries.revision_counts(
                conn, revision_decision_type=DECISION_REVISION_REQUESTED
            ),
            job_queries.latest_message_types(conn),
        )

    @router.get("/api/jobs")
    def list_jobs() -> dict:
        """8.1 岗位列表与状态视图。只读。

        ⛔ 不分页：M1 的量级是"日均新增岗位个位数"（design.md 非目标：不追求
        高并发），加分页只会多一套前后端要对齐的状态。量级变了再说。
        """
        rows, counts, message_types = _job_rows_with_context()
        return {"jobs": [_job_row_payload(row, counts, message_types) for row in rows]}

    # 8.2「生成快照」的诚实边界。
    #
    # analysis_run 表里有工程铁律 3 要求的全套字段（模型标识/版本/prompt 版本/
    # temperature/输入哈希/原始响应/token 用量），但**当前没有任何调用点给网关传
    # audit_context**——app/llm/gateway.py 的 audit_context 参数在 app/graph/ 与
    # app/agents/ 下无调用方，于是 app/audit/hook.py 里 context.get("job_id") 恒为
    # None，那些行的 job_id 全是 NULL，按岗位根本查不出来。
    #
    # ⛔ 不在这里瞎猜关联（比如按时间就近匹配 analysis_run 行）：猜出来的留痕比
    # 没有留痕更糟——审计那天答不出"这条是怎么对上的"，而 PIPL 第 24 条说明权
    # 要的正是这个答案。本页展示的快照来自 job_profile 逐轮落的列，其中
    # llm_response_model 是工程铁律 5 的落点（API 响应里实际返回的模型标识）。
    #
    # 补齐的做法是在网关调用点传 audit_context={"job_id": ...}，那要碰 app/graph/
    # 与 app/agents/，超出本交付单元边界。
    #
    # ⛔ 不为此新开一条技术债：这件事**已经登记在 docs/tech-debt.md 的 TD-1** 里
    # ——TD-1「怎么还」第 ① 步逐字写着"先有一个单元把 audit_context（至少含
    # thread_id / job_id / node）接到 intake 的 LLM 调用上"，「现状」段又逐字写着
    # "intake 路径尚未传 audit_context，那些行的 job_id / application_id 全为 NULL"。
    # 再开一条就是给同一个事实开第二个真源，两边迟早写得不一样而没有任何症状。
    _SNAPSHOT_NOTE = (
        "本页快照来自逐轮落库的画像行，模型标识取自 API 响应实际返回值。"
        "完整的模型调用留痕（analysis_run 表）当前未与岗位关联、按岗位查不到，"
        "见技术债 TD-1 的第 ① 步（audit_context 尚未接到 intake 路径）。"
    )

    @router.get("/api/jobs/{job_id}/profile")
    def get_job_profile(job_id: str) -> dict:
        """8.2 画像详情：版本历史 + 每版生成快照 + 人工决策留痕。只读。

        标题与 JD 状态复用 latest_profile_rows() 的同一条推导路径，⛔ 不另写
        一份"取最新版画像"的逻辑：两份推导迟早会在某个边界上不一致（比如
        "只有 job、没有 job_profile"那种行），而不一致时两边都不报错。
        """
        rows = [row for row in job_queries.latest_profile_rows(conn) if row["job_id"] == job_id]
        if not rows:
            raise HTTPException(status_code=404, detail="job not found")
        row = rows[0]

        versions = job_queries.profile_versions(conn, job_id)
        jd = job_queries.jd_state(row["profile"])

        return {
            "job_id": row["job_id"],
            "title": job_queries.display_title(row),
            "status": row["status"],
            "stage_label": job_queries.stage_label(
                job_status=row["status"],
                latest_version=row["latest_version"],
                latest_message_type=job_queries.latest_message_types(conn).get(job_id),
                jd=jd,
            ),
            "created_at": row["created_at"],
            "created_at_label": row["created_at_label"],
            "latest_version": row["latest_version"],
            "snapshot_note": _SNAPSHOT_NOTE,
            "versions": versions,
            "decisions": job_queries.decision_records(
                conn, job_id, unknown_reviewer=UNKNOWN_REVIEWER
            ),
        }

    @router.get("/api/queues/needs-manual")
    def needs_manual_queue() -> dict:
        """8.4 转人工队列。只读、只展示。

        ⛔ 只展示不处置：本端点与本队列页面 ⛔ 不提供批量确认、批量放弃、批量
        重生成（合规红线「AI 只做排序推荐，不做自动淘汰」；批量处置是 M2 的事，
        且必须有人工确认节点与留痕）。

        ⛔ 放弃（abandoned）的岗位不进队列：放弃是终态、不再流转，把它摆进 HR
        的待办里只会制造清不掉的积压。过滤放在这里而不是
        derive_needs_manual_reasons 里——那个函数只回答"有哪些理由"，详情页
        恰恰应该看得到"这个岗位当初为什么被转人工"，哪怕它后来被放弃了。
        """
        rows, counts, message_types = _job_rows_with_context()
        items = [
            payload
            for payload in (
                _job_row_payload(row, counts, message_types)
                for row in rows
                if row["status"] != "abandoned"
            )
            if payload["needs_manual"]
        ]
        # 队列按"等得最久的排前面"，与列表页的倒序刻意相反：列表回答"最近发生了
        # 什么"，队列回答"该先办哪一个"。job_id 作为第二排序键，保证同一时刻的
        # 两条有稳定顺序（否则每次刷新顺序会跳，看的人会以为队列变了）。
        items.sort(key=lambda item: (item["updated_at"], item["job_id"]))
        return {"jobs": items, "total": len(items)}

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = conn.execute(
            "SELECT id, title, status FROM job WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        latest = channel.latest(job_id)
        return {
            "job_id": job[0],
            "status": job[2],
            "message": {"type": latest.type, "payload": _response_payload(latest)}
            if latest
            else None,
        }

    _VALID_SAMPLE_CLASSES = {"synthetic", "anonymized", "departed", "live"}

    @router.post("/api/resumes/upload")
    def upload_resumes(
        request: Request,
        job_id: str = Form(...),
        sample_class: str = Form(...),
        files: list[UploadFile] = File(...),
    ):
        if sample_class not in _VALID_SAMPLE_CLASSES:
            raise HTTPException(status_code=422, detail="sample_class 取值非法")
        job = conn.execute("SELECT id FROM job WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        uploader = reviewer_of(request)
        results = []

        if sample_class == "live":
            gate_open = is_live_resume_intake_enabled(auth=request.state.auth, conn=conn)
            if not gate_open:
                for f in files:
                    results.append({
                        "file_name": f.filename,
                        "status": "rejected",
                        "reason": "真实简历入库闸未开启",
                    })
                logger.warning(
                    "闸关闭时的 live 上传尝试：uploader=%s job_id=%s file_count=%d",
                    uploader, job_id, len(files),
                )
                return {"results": results}

        for f in files:
            results.append(_ingest_one_resume(job_id=job_id, sample_class=sample_class,
                                               uploaded_by=uploader, upload=f))
        return {"results": results}

    def _ingest_one_resume(*, job_id: str, sample_class: str, uploaded_by: str,
                            upload: UploadFile) -> dict:
        suffix = Path(upload.filename or "").suffix.lower()
        content = upload.file.read()
        # 提前拒收，不读 ingest_resume_text 的 UnsupportedFileType 路径——避免为被拒文件建 DB 行/落盘再回滚
        if suffix not in SUPPORTED_SUFFIXES:
            return {"file_name": upload.filename, "status": "rejected",
                    "reason": "不支持的类型"}

        content_hash = hashlib.sha256(content).hexdigest()
        dup = conn.execute(
            "SELECT id FROM resume WHERE job_id = ? AND content_sha256 = ?",
            (job_id, content_hash),
        ).fetchone()
        if dup is not None:
            return {"file_name": upload.filename, "status": "duplicate",
                    "resume_id": dup[0]}

        resume_id = str(uuid.uuid4())
        stored_path = _resume_storage_dir / f"{resume_id}{suffix}"
        stored_path.write_bytes(content)

        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
            "uploaded_by) VALUES (?, ?, ?, ?, ?, ?)",
            (resume_id, job_id, sample_class, upload.filename, content_hash, uploaded_by),
        )
        conn.commit()

        ingest_result = ingest_resume_text(stored_path)
        if not ingest_result.readable:
            conn.execute(
                "UPDATE resume SET status = 'unreadable' WHERE id = ?", (resume_id,)
            )
            conn.commit()
            return {"file_name": upload.filename, "status": "accepted",
                    "resume_id": resume_id, "parse_status": "unreadable"}

        conn.execute(
            "UPDATE resume SET raw_text = ? WHERE id = ?",
            (ingest_result.raw_text, resume_id),
        )
        for span in ingest_result.spans:
            conn.execute(
                "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
                "VALUES (?, ?, ?, ?, ?)",
                (resume_id, span.span_id, span.start, span.end, span.text),
            )
        conn.commit()

        threshold_row = conn.execute(
            "SELECT parse_confidence_threshold FROM job WHERE id = ?", (job_id,)
        ).fetchone()
        confidence_threshold = threshold_row[0] if threshold_row else 0.7

        try:
            fields, meta = compute_parse(
                gateway,
                spans=ingest_result.spans,
                audit_context={"thread_id": resume_id, "node": "compute_parse", "job_id": job_id},
            )
        except Exception:
            logger.exception("resume_id=%s 抽取失败，简历留在 pending，可稍后重解析", resume_id)
            return {"file_name": upload.filename, "status": "accepted",
                    "resume_id": resume_id, "parse_status": "parse_failed"}

        parser_version = "v1"
        application_id = effect_persist_parse(
            conn,
            thread_id=resume_id,
            business_key=parser_version,
            resume_id=resume_id,
            job_id=job_id,
            fields=fields,
            parser_version=parser_version,
            model_configured=gateway.model,
            model_response=meta.response_model,
            # ⛔ 不写 "parse-v1" 字面量：compute_parse 默认用的是
            # PARSE_PROMPT_VERSION，字面量与常量一旦漂移，审计链（记的是
            # compute_parse 的真实行为）与 resume_parse_version.prompt_version
            # 会对同一次解析给出两个版本号，而且不报错。
            prompt_version=PARSE_PROMPT_VERSION,
            confidence_threshold=confidence_threshold,
        )
        resolved_application_id = application_id
        if resolved_application_id is None:
            existing_app = conn.execute(
                "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
            ).fetchone()
            resolved_application_id = existing_app[0] if existing_app else None
        if resolved_application_id is not None:
            profile_version = latest_approved_profile_version(conn, job_id)
            if profile_version is not None:
                screen_and_persist(
                    conn,
                    application_id=resolved_application_id,
                    resume_id=resume_id,
                    job_id=job_id,
                    profile_version=profile_version,
                    parse_version=parser_version,
                )
        return {"file_name": upload.filename, "status": "accepted",
                "resume_id": resume_id, "application_id": application_id,
                "parse_status": "parsed"}

    @router.post("/api/resumes/{resume_id}/reparse")
    def reparse_resume(resume_id: str):
        row = conn.execute(
            "SELECT job_id, status FROM resume WHERE id = ?", (resume_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="resume not found")
        job_id, resume_status = row[0], row[1]

        span_count = conn.execute(
            "SELECT COUNT(*) FROM resume_text_span WHERE resume_id = ?", (resume_id,)
        ).fetchone()[0]
        # 不可读（TD-52 的扫描件退路）或一条分片都没有的简历，⛔ 不许重解析：
        # compute_parse 拿空 span 列表会把六个字段全判 not_mentioned，
        # _lowest_field_confidence 对空列表返回 1.0（"满分置信"），于是
        # effect_persist_parse 会把它标成 parsed、一条人工校对都不建、还给它
        # 建出一条字段全空的 candidate/application——把隔离区里的简历静默放进
        # 后续筛选（resume-parsing spec「MUST NOT 以空字段进入后续判定与排序」）。
        if resume_status == "unreadable" or span_count == 0:
            raise HTTPException(
                status_code=409,
                detail="该简历不可读（无原文分片），无法重新解析，请走人工补录",
            )

        spans = [
            TextSpan(span_id=r[0], start=r[1], end=r[2], text=r[3])
            for r in conn.execute(
                "SELECT span_id, start, end, text FROM resume_text_span "
                "WHERE resume_id = ? ORDER BY span_id",
                (resume_id,),
            ).fetchall()
        ]
        threshold_row = conn.execute(
            "SELECT parse_confidence_threshold FROM job WHERE id = ?", (job_id,)
        ).fetchone()
        confidence_threshold = threshold_row[0] if threshold_row else 0.7

        existing_versions = conn.execute(
            "SELECT COUNT(*) FROM resume_parse_version WHERE resume_id = ?", (resume_id,)
        ).fetchone()[0]
        parser_version = f"v{existing_versions + 1}"

        fields, meta = compute_parse(
            gateway,
            spans=spans,
            audit_context={"thread_id": resume_id, "node": "compute_parse", "job_id": job_id},
        )
        application_id = effect_persist_parse(
            conn,
            thread_id=resume_id,
            business_key=parser_version,
            resume_id=resume_id,
            job_id=job_id,
            fields=fields,
            parser_version=parser_version,
            model_configured=gateway.model,
            model_response=meta.response_model,
            # 同上传路由：版本号取常量，⛔ 不写字面量（审计链与
            # resume_parse_version 必须说同一个 prompt 版本）。
            prompt_version=PARSE_PROMPT_VERSION,
            confidence_threshold=confidence_threshold,
        )
        resolved_application_id = application_id
        if resolved_application_id is None:
            existing_app = conn.execute(
                "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
            ).fetchone()
            resolved_application_id = existing_app[0] if existing_app else None
        if resolved_application_id is not None:
            profile_version = latest_approved_profile_version(conn, job_id)
            if profile_version is not None:
                screen_and_persist(
                    conn,
                    application_id=resolved_application_id,
                    resume_id=resume_id,
                    job_id=job_id,
                    profile_version=profile_version,
                    parse_version=parser_version,
                )
        return {"resume_id": resume_id, "application_id": application_id,
                "parser_version": parser_version}

    def _require_resume(resume_id: str) -> tuple:
        row = conn.execute(
            "SELECT id, file_name, raw_text, parsed_json, sample_class FROM resume WHERE id = ?",
            (resume_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="resume not found")
        return row

    def _latest_field_review_rows(resume_id: str) -> dict[str, dict]:
        """按字段取最新一行校对队列记录（架构决策 3）：同一字段可能有一条历史
        reviewed 行 + 一条新的 pending 行（重解析后再次判低置信度），
        按 created_at 倒序取第一条即当前状态。"""
        rows = conn.execute(
            "SELECT field, status, reviewed_by, reviewed_at FROM field_review_queue "
            "WHERE resume_id = ? ORDER BY created_at DESC",
            (resume_id,),
        ).fetchall()
        latest: dict[str, dict] = {}
        for field, status, reviewed_by, reviewed_at in rows:
            if field not in latest:
                latest[field] = {
                    "status": status, "reviewed_by": reviewed_by, "reviewed_at": reviewed_at,
                }
        return latest

    def _display_field_value(field_payload: dict) -> str:
        if field_payload.get("not_mentioned"):
            return "未提及"
        value = field_payload.get("value")
        if value is None:
            return "未提及"
        if isinstance(value, list):
            return "、".join(value) if value else "未提及"
        if isinstance(value, dict):
            parts = [str(value[k]) for k in ("degree", "school") if value.get(k)]
            return " ".join(parts) if parts else "未提及"
        return str(value)

    @router.get("/api/applications/{application_id}/screening-flags")
    def get_screening_flags(application_id: str) -> dict:
        """硬门槛判定标记的最小只读接口（task-5-brief.md 附带范围）：只用于
        黑盒验证「上传/重解析后立即判定」这个触发点是否生效，⛔ 不做任何
        排序/淘汰相关的展示或聚合。"""
        rows = conn.execute(
            "SELECT profile_version, rule_ref, verdict, reason, evidence_ref, created_at "
            "FROM screening_flag WHERE application_id = ? ORDER BY created_at",
            (application_id,),
        ).fetchall()
        return {
            "flags": [
                {
                    "profile_version": r[0], "rule_ref": r[1], "verdict": r[2],
                    "reason": r[3], "evidence_ref": r[4], "created_at": r[5],
                }
                for r in rows
            ]
        }

    @router.get("/api/resumes/by-job/{job_id}")
    def list_resumes_for_job(request: Request, job_id: str) -> dict:
        """9.2 解析结果列表页的数据源。⛔ 不返回任何分数/排名字段——那些字段
        本单元根本不产生（U4/U5 才有）。路径故意落在 /api/resumes 下而不是
        /jobs/{id}/resumes（架构决策 1）：这样天然受 AuthMiddleware 的
        PROTECTED_PATH_PREFIXES 保护，未登录 401、不返回任何候选人数据
        （resume-upload-and-gate spec「可识别到人的登录」）。
        """
        job = conn.execute("SELECT id FROM job WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        rows = conn.execute(
            "SELECT id, sample_class, file_name, status, parsed_json, parser_version, "
            "uploaded_at FROM resume WHERE job_id = ? ORDER BY uploaded_at DESC",
            (job_id,),
        ).fetchall()

        accessor = reviewer_of(request)
        items = []
        for resume_id, sample_class, file_name, status, parsed_json, parser_version, uploaded_at in rows:
            display_name = file_name
            pending_count = 0
            fields_payload: list[dict] = []
            if parsed_json:
                # 每份简历一条留痕，⛔ 不因为要读六个字段就写六条
                # （resume-upload-and-gate spec「简历访问留痕」按"这次读取"计一条）。
                record_resume_access(conn, accessor=accessor, resume_id=resume_id,
                                      access_type="parsed_result")
                parsed = json.loads(parsed_json)
                review_rows = _latest_field_review_rows(resume_id)
                name_field = parsed.get("name") or {}
                if not name_field.get("not_mentioned") and name_field.get("value"):
                    display_name = name_field["value"]
                for field_name in FIELD_NAMES:
                    field_payload = parsed.get(field_name) or {}
                    review = review_rows.get(field_name)
                    review_status = review["status"] if review else "not_queued"
                    if review_status == "pending":
                        pending_count += 1
                    fields_payload.append({
                        "field": field_name,
                        "label": FIELD_LABELS[field_name],
                        "value_display": _display_field_value(field_payload),
                        "confidence": field_payload.get("confidence"),
                        "review_status": review_status,
                        "reviewed_by": review["reviewed_by"] if review else None,
                        "reviewed_at": review["reviewed_at"] if review else None,
                    })
            items.append({
                "resume_id": resume_id,
                "candidate_display_name": display_name,
                "sample_class": sample_class,
                "parse_status": status,
                "parser_version": parser_version,
                "uploaded_at": uploaded_at,
                "pending_review_count": pending_count,
                "fields": fields_payload,
            })
        return {"job_id": job_id, "resumes": items}

    @router.get("/api/resumes/{resume_id}/text")
    def get_resume_text(request: Request, resume_id: str):
        row = _require_resume(resume_id)
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="raw_text")
        return {"resume_id": resume_id, "raw_text": row[2] or ""}

    @router.get("/api/resumes/{resume_id}/spans")
    def get_resume_spans(request: Request, resume_id: str):
        _require_resume(resume_id)
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="spans")
        rows = conn.execute(
            "SELECT span_id, start, end, text FROM resume_text_span "
            "WHERE resume_id = ? ORDER BY span_id",
            (resume_id,),
        ).fetchall()
        return {"spans": [
            {"span_id": r[0], "start": r[1], "end": r[2], "text": r[3]} for r in rows
        ]}

    @router.get("/api/resumes/{resume_id}/parsed")
    def get_resume_parsed(request: Request, resume_id: str):
        row = _require_resume(resume_id)
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="parsed_result")
        return {"resume_id": resume_id, "parsed_json": json.loads(row[3]) if row[3] else None}

    @router.get("/api/resumes/{resume_id}/download")
    def download_resume(request: Request, resume_id: str):
        row = _require_resume(resume_id)
        file_name = row[1]
        suffix = Path(file_name).suffix.lower()
        stored_path = _resume_storage_dir / f"{resume_id}{suffix}"
        if not stored_path.exists():
            raise HTTPException(status_code=404, detail="文件已不在存储中")
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="download")
        return FileResponse(str(stored_path), filename=file_name)

    @router.post("/api/resumes/{resume_id}/fields/{field}/review")
    def review_field(request: Request, resume_id: str, field: str, req: FieldReviewRequest):
        row = conn.execute(
            "SELECT id, human_value FROM field_review_queue "
            "WHERE resume_id = ? AND field = ? AND status = 'pending'",
            (resume_id, field),
        ).fetchone()
        if row is None:
            already_reviewed = conn.execute(
                "SELECT human_value FROM field_review_queue "
                "WHERE resume_id = ? AND field = ? AND status = 'reviewed' "
                "ORDER BY created_at DESC LIMIT 1",
                (resume_id, field),
            ).fetchone()
            if already_reviewed is not None and already_reviewed[0] == req.human_value:
                return {"ok": True, "already_reviewed": True}
            raise HTTPException(status_code=404, detail="该字段没有待校对记录")

        reviewer = reviewer_of(request)
        conn.execute(
            "UPDATE field_review_queue SET status = 'reviewed', human_value = ?, "
            "reviewed_by = ?, reviewed_at = datetime('now') WHERE id = ?",
            (req.human_value, reviewer, row[0]),
        )
        conn.commit()
        queue_reapplication_screening(conn, resume_id)
        return {"ok": True, "already_reviewed": False}

    @router.get("/health")
    def health() -> dict:
        """日志子系统坏掉时唯一还能对外说话的通道（design 决策 5）。

        降级时仍返回 200：服务照常提供业务功能，用 503 会诱导监控去重启一个
        其实健康的进程——拿更大的故障换更小的故障。降级事实放在 body 里，
        运维检查看 status 字段而不是 HTTP 码。
        """
        status = logging_status()
        return {
            "status": "degraded" if status.degraded else "ok",
            "logging": status.as_dict(),
        }

    @router.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(_render_index(root_path))

    app.include_router(router, prefix=root_path)
    app.mount(
        f"{root_path}/static" if root_path else "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

    return app
