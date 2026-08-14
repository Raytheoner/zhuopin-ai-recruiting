from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.nodes import effect_confirm_profile, effect_generate_and_persist_jd
from app.middleware.auth import AuthMiddleware
from app.schemas.job_profile import JobProfile
from app.storage.db import get_connection, init_schema

STATIC_DIR = Path(__file__).parent / "static"
INDEX_TEMPLATE_PATH = STATIC_DIR / "index.html"


class CreateJobRequest(BaseModel):
    message: str


class ReplyRequest(BaseModel):
    message: str


def _render_index(root_path: str) -> str:
    """把 <!--BASE_HREF--> 占位符换成真实 <base href>，让前端相对路径请求
    在任意挂载前缀下都能解析到正确的地址。root_path="" 时挂域根。"""
    html = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    base_href = f"{root_path}/" if root_path else "/"
    return html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">')


def create_app(*, db_path: str, gateway_factory: Callable, root_path: str = "") -> FastAPI:
    conn = get_connection(db_path)
    init_schema(conn)
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
    app.add_middleware(AuthMiddleware)
    router = APIRouter()

    def _run_turn(job_id: str, message: str) -> dict:
        profile_row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        accumulated = json.loads(profile_row[0]) if profile_row else {}
        round_count = conn.execute(
            "SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()[0]

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
            "profile_patch_accumulated": accumulated,
        }
        graph.invoke(state, config={"configurable": {"thread_id": job_id}})

        latest = channel.latest(job_id)
        return {"type": latest.type, "payload": latest.payload}

    @router.post("/api/jobs")
    def create_job(req: CreateJobRequest):
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,)
        )
        conn.commit()
        message = _run_turn(job_id, req.message)
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/reply")
    def reply(job_id: str, req: ReplyRequest):
        job = conn.execute("SELECT id FROM job WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        message = _run_turn(job_id, req.message)
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/confirm")
    def confirm(job_id: str):
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
            conn, thread_id=job_id, business_key=str(version), profile_dict=profile_dict
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
        # 统一从这里读回去构造响应，两条路径读到的是同一份持久化结果。
        persisted_row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
            (job_id, version),
        ).fetchone()
        persisted = json.loads(persisted_row[0])

        return {
            "job_id": job_id,
            "jd_text": persisted["_jd_text"],
            "needs_manual": persisted.get("_jd_needs_manual", False),
        }

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
            "message": {"type": latest.type, "payload": latest.payload} if latest else None,
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
